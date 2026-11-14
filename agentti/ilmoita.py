"""Ilmoitus Tonylle Telegramiin: lyhyt, kiinteamuotoinen viesti tyon tuloksesta.

Kaytto (terminal-Claude tai skripti):
    python3 -m agentti.ilmoita valmis "Deploy valmis" "testit ok, kuiva-ajo ok"   # ei Telegramiin: docs-repoon
    python3 -m agentti.ilmoita virhe "Deploy epaonnistui" "testi X kaatui, palautettu"
    python3 -m agentti.ilmoita paatos "Hyvaksy PR #7?" "yhteenveto ja suositus"

Turva: vastaanottaja on aina tunnustiedostossa (ei argumenttina), tila on vain valmis|virhe|paatos,
teksti puhdistetaan ohjausmerkeista ja rajataan, tunnuksia ei koskaan tulosteta, ja lahetyksia on
enintaan RAJA_TUNNISSA tunnissa (juokseva silmukka ei voi tulvia chattia). Vain stdlib.
"""
import json
import os
import sys
import time

from . import halytysportti, telegram

TILAT = {"valmis": "VALMIS", "virhe": "VIRHE", "paatos": "PAATOS"}
OTSIKKO_MAX = 100
TEKSTI_MAX = 1200
RAJA_TUNNISSA = 30
OLETUS_TUNNUKSET = "/etc/yojono/telegram.env"
OLETUS_TILA = "/var/lib/yojono/ilmoita-tila.json"


def puhdista(teksti, raja):
    """Poistaa ohjausmerkit (rivinvaihto sallittu) ja rajaa pituuden."""
    siisti = "".join(c for c in str(teksti) if c == "\n" or (c >= " " and c != "\x7f"))
    return siisti.strip()[:raja]


def muodosta(tila, otsikko, teksti=""):
    """Palauttaa viestin tai nostaa ValueErrorin, jos tila on tuntematon tai otsikko tyhja."""
    if tila not in TILAT:
        raise ValueError("tila: valmis | virhe | paatos")
    otsikko = puhdista(otsikko, OTSIKKO_MAX).replace("\n", " ")
    if not otsikko:
        raise ValueError("otsikko puuttuu")
    viesti = "[%s] %s" % (TILAT[tila], otsikko)
    teksti = puhdista(teksti, TEKSTI_MAX)
    return viesti + ("\n" + teksti if teksti else "")


def lue_tunnukset(polku):
    """KEY=VALUE-rivit tiedostosta; puuttuva tai lukukelvoton tiedosto antaa tyhjan."""
    tulos = {}
    try:
        with open(polku, encoding="utf-8") as f:
            for rivi in f:
                rivi = rivi.strip()
                if rivi and not rivi.startswith("#") and "=" in rivi:
                    avain, _, arvo = rivi.partition("=")
                    tulos[avain.strip()] = arvo.strip().strip("'\"")
    except OSError:
        pass
    return tulos


def raja_sallii(tila_polku, nyt=None):
    """Kirjaa lahetyksen ja palauttaa False, jos tunnin sisalla on jo RAJA_TUNNISSA lahetysta."""
    nyt = time.time() if nyt is None else nyt
    ajat = []
    try:
        with open(tila_polku, encoding="utf-8") as f:
            ajat = [a for a in json.load(f) if isinstance(a, (int, float)) and nyt - a < 3600]
    except (OSError, ValueError):
        pass
    if len(ajat) >= RAJA_TUNNISSA:
        return False
    ajat.append(nyt)
    try:
        with open(tila_polku, "w", encoding="utf-8") as f:
            json.dump(ajat, f)
    except OSError:
        pass  # tilan kirjaus ei saa estaa ilmoitusta
    return True


def ilmoita(tila, otsikko, teksti="", env_polku=None, tila_polku=None, hae=None, nyt=None, portti_tila=None):
    """Palauttaa 'lahetetty' | 'raja' | 'ei_tunnuksia' | 'epaonnistui' | 'ohjattu' | 'jonoon'.
    Halytysportti (A62, A427): vain tila 'virhe' on halytys ja paasee Telegramiin, enintaan 5 kpl vuorokaudessa.
    'valmis' ja 'paatos' palauttavat 'ohjattu' (kuuluvat docs-repoon); 'jonoon' = paivakatto taynna, koottu huomiseksi."""
    viesti = muodosta(tila, otsikko, teksti)
    if tila != "virhe":
        return halytysportti.OHJATTU
    viesti = "HALYTYS " + viesti
    tunnukset = lue_tunnukset(env_polku or os.environ.get("ILMOITA_TUNNUKSET", OLETUS_TUNNUKSET))
    env = {"TELEGRAM_BOT_TOKEN": tunnukset.get("YOJONO_TELEGRAM_TOKEN"),
           "TELEGRAM_CHAT_ID": tunnukset.get("YOJONO_TELEGRAM_CHAT")}
    if not env["TELEGRAM_BOT_TOKEN"] or not env["TELEGRAM_CHAT_ID"]:
        return "ei_tunnuksia"
    if not raja_sallii(tila_polku or os.environ.get("ILMOITA_TILA", OLETUS_TILA), nyt):
        return "raja"
    portti_tila = portti_tila or (tila_polku + ".portti.json" if tila_polku else None)
    tulos = halytysportti.laheta(viesti, lambda t: telegram.laheta(t, env, hae=hae), tila_polku=portti_tila, nyt=nyt)
    return tulos if tulos != halytysportti.OHJATTU else "epaonnistui"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or len(argv) > 3:
        print("kaytto: python3 -m agentti.ilmoita valmis|virhe|paatos OTSIKKO [TEKSTI]", file=sys.stderr)
        return 2
    try:
        tulos = ilmoita(argv[0], argv[1], argv[2] if len(argv) == 3 else "")
    except ValueError as e:
        print("ilmoita: %s" % e, file=sys.stderr)
        return 2
    print("ilmoitus: %s" % tulos)
    return 0 if tulos in ("lahetetty", "ohjattu", "jonoon") else 1


if __name__ == "__main__":
    sys.exit(main())
