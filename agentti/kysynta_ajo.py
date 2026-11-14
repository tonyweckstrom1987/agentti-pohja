"""Kysyntaagentti: viikkoluvut historiaan, maanantain aamurivi ja halytys ulkopuolisen issuesta tai PR:sta.

Rakentuu agentti.kysynta-moduulin paalle (sama laskenta, omat tunnukset ja botit suodatetaan pois). Ei LLM-kutsuja,
ei tunnuksia GitHubiin eika npm:aan: vain julkiset rajapinnat. Telegram-tunnukset tulevat ilmoita-moduulin tunnustiedostosta.

Kaytto:
    python3 -m agentti.kysynta_ajo viikko  [--historia HAKEMISTO]
    python3 -m agentti.kysynta_ajo halytys [--nahdyt TIEDOSTO]

viikko:  hakee kaikkien julkisten repojen luvut (+ npm ja PyPI), kirjoittaa HAKEMISTO/vvvv-vv.json (ISO-viikko) ja
         HAKEMISTO/aamurivi.json (yksi rivi aamukoosteeseen, aamupaketti nayttaa sen maanantaisin).
halytys: lukee kaikkien repojen uusimmat ulkopuoliset issuet ja PR:t yhdella hakupyynnolla ja ilmoittaa Telegramiin kunkin uuden kerran.
         Ensimmainen ajo vain kirjaa nykytilanteen (ei tulvaa vanhoista). Sisaltoa (otsikko, teksti) ei lueta eika lahteta:
         viestissa on vain repo, numero, laji, GitHub-tunnus ja osoite, joten ulkopuolinen ei paase ohjeistamaan agenttia.
Raja: kirjautumaton GitHub sallii 60 pyyntoa tunnissa. Halytys kuluttaa 1 pyynnon. Viikkoajo kuluttaa 2 pyyntoa + 1 niille
repoille, joilla on forkkeja (ks. agentti.kysynta); repolista haetaan vain kerran. Kun raja on lahes taynna, haut jatetaan tekematta."""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import urllib.error
from pathlib import Path

from . import ilmoita, kysynta
from .kysynta import OMISTAJA, TILA_OLETUS, hae_json, ulkoinen
from .pr_kutsu import REPOT

# Samassa hakemistossa kuin TILA_OLETUS: claudejono saa kirjoittaa tanne, /var/lib/claude-jono ja /var/lib/yojono ei
_TILA_HAKEMISTO = os.path.dirname(TILA_OLETUS)
HISTORIA_OLETUS = os.path.join(_TILA_HAKEMISTO, "historia")
NAHDYT_OLETUS = os.path.join(_TILA_HAKEMISTO, "kysynta-nahdyt.json")
PYPI_PAKETIT = {"llm-stopcheck": "llm-stopcheck"}
NAHDYT_MAX = 2000
VIESTEJA_AJOSSA = 5          # tulva-esto: loput tiivistetaan yhteen viestiin
TUNNUS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def viikko_avain(pvm):
    """ISO-viikko muodossa vvvv-vv (esim. 2026-45)."""
    v = datetime.date.fromisoformat(pvm).isocalendar()
    return "%04d-%02d" % (v[0], v[1])


def julkiset_repot(hae=hae_json):
    """Tonyn omat julkiset repot (ei forkkeja, ei arkistoituja). Haun epaonnistuessa tunnettu REPOT-lista."""
    try:
        rivit = hae(kysynta.REPOLISTA_URL)
        nimet = sorted({r["name"] for r in rivit if isinstance(r, dict) and not r.get("fork") and not r.get("archived")
                        and isinstance(r.get("name"), str) and REPO_RE.match(r["name"])})
        if nimet:
            return nimet, None
    except Exception as e:
        return list(REPOT), "repolista: " + kysynta._virhe(e)
    return list(REPOT), "repolista: tyhja"


def pypi_viikko(paketti, hae=hae_json):
    """Viimeisen viikon lataukset (pypistats). Palauttaa (luku tai "?", virhe tai None)."""
    try:
        luku = hae("https://pypistats.org/api/packages/%s/recent" % paketti)["data"]["last_week"]
        if isinstance(luku, bool) or not isinstance(luku, int):
            return "?", "pypi: ei lukua"
        return luku, None
    except Exception as e:
        return "?", "pypi: " + kysynta._virhe(e)


def kirjoita_json(polku, data):
    """Atominen kirjoitus: kaatuminen ei jata puolikasta tiedostoa."""
    kohde = Path(polku)
    kohde.parent.mkdir(parents=True, exist_ok=True)
    kahva, tmp = tempfile.mkstemp(dir=str(kohde.parent), prefix=".kysynta-")
    try:
        with os.fdopen(kahva, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, kohde)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _summa(luvut, avain):
    """Tarkkojen lukujen summa; None jos yksikaan ei ole tunnettu. Alarajat ("5+") lasketaan mukaan arvona ja merkitaan +:lla."""
    summa, alaraja, tunnettu = 0, False, False
    for rivi in luvut.values():
        v = rivi.get(avain)
        if type(v) is int:
            summa, tunnettu = summa + v, True
        elif isinstance(v, str) and re.fullmatch(r"\d+\+", v):
            summa, alaraja, tunnettu = summa + int(v[:-1]), True, True
    return None if not tunnettu else ("%d+" % summa if alaraja else summa)


def aamurivi(viikko, luvut, edellinen=None):
    """Yksi rivi aamukoosteeseen. Tuntematon on "?", ei 0. Muutos edelliseen viikkoon vain kahden tarkan luvun valilta."""
    osat = []
    for nimi, avain in (("npm", "npm_viikko"), ("PyPI", "pypi_viikko"), ("t\u00e4hdet", "tahdet"), ("forkit", "forkit"),
                        ("issuet", "issuet"), ("PR:t", "prt")):
        nyt = _summa(luvut, avain)
        vanha = _summa(edellinen, avain) if edellinen else None
        osat.append("%s %s%s" % (nimi, "?" if nyt is None else nyt, kysynta._muutos(nyt, vanha)))
    return "Kysynt\u00e4, viikko %s: %s. Ulkoiset luvut, omat ja botit pois." % (viikko.split("-")[1], ", ".join(osat))


def mittaa_viikko(hae=hae_json):
    """Palauttaa (luvut repoittain, virheet). PyPI-luku on mukana pypi_viikko-kenttana."""
    hae = kysynta.muistiin(hae)    # repolista haetaan kerran, vaikka sita tarvitaan kahdessa paikassa
    repot, repovirhe = julkiset_repot(hae)
    luvut, virheet = kysynta.mittaa(repot, hae)
    if repovirhe:
        virheet.insert(0, repovirhe)
    for repo, paketti in PYPI_PAKETIT.items():
        if repo in luvut:
            luvut[repo]["pypi_viikko"], virhe = pypi_viikko(paketti, hae)
            if virhe:
                virheet.append("%s %s" % (repo, virhe))
    return luvut, virheet


def lue_json(polku):
    try:
        data = json.loads(Path(polku).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def aja_viikko(historia, pvm, hae=hae_json):
    """Kirjoittaa viikkotiedoston ja aamurivin. Palauttaa (exit-koodi, rivi)."""
    luvut, virheet = mittaa_viikko(hae)
    viikko = viikko_avain(pvm)
    edellinen = None
    vanhat = sorted(n for n in os.listdir(historia) if re.fullmatch(r"\d{4}-\d{2}\.json", n) and n[:7] < viikko) if os.path.isdir(historia) else []
    if vanhat:
        edellinen = (lue_json(os.path.join(historia, vanhat[-1])) or {}).get("luvut")
    tunnetut = any(v not in ("?", "-") for rivi in luvut.values() for v in rivi.values())
    if not tunnetut:    # kaikki haut kaatuivat: ei kirjoiteta nollia eika "?"-historiaa
        return 1, "Kysynt\u00e4mittaus ep\u00e4onnistui kokonaan (verkko tai raja)."
    rivi = aamurivi(viikko, luvut, edellinen)
    kirjoita_json(os.path.join(historia, viikko + ".json"), {"viikko": viikko, "pvm": pvm, "luvut": luvut, "virheet": virheet})
    kirjoita_json(os.path.join(historia, "aamurivi.json"), {"viikko": viikko, "rivi": rivi})
    return 0, rivi


def aamurivi_nyt(historia, pvm):
    """Aamukoosteen rivi: vain maanantaisin ja vain kuluvan ISO-viikon mittauksesta. Muuten None."""
    try:
        if datetime.date.fromisoformat(pvm).weekday() != 0:
            return None
        data = lue_json(os.path.join(historia, "aamurivi.json"))
        if data and data.get("viikko") == viikko_avain(pvm) and isinstance(data.get("rivi"), str):
            return data["rivi"][:300]
    except (ValueError, OSError):
        pass
    return None


# --- halytys -----------------------------------------------------------------------------------------------------

def viesti(repo, laji, tunnus, numero):
    polku = "pull" if laji == "PR" else "issues"
    return ("Ulkopuolinen avasi %s: %s#%d" % (laji, repo, numero),
            "Tekij\u00e4: %s\nhttps://github.com/%s/%s/%s/%d" % (tunnus, OMISTAJA, repo, polku, numero))


def aja_halytys(nahdyt_polku, hae=hae_json, laheta=ilmoita.ilmoita):
    """Palauttaa (exit-koodi, yhteenvetorivi). Avain merkitaan nahdyksi vasta kun viesti lahti (tai ensimmaisella ajolla)."""
    tila = lue_json(nahdyt_polku)
    ensimmainen = tila is None
    nahdyt = list((tila or {}).get("nahdyt", [])) if not ensimmainen else []
    nahtuja = set(nahdyt)
    uudet, virheita = [], 0
    try:
        for k in kysynta.ulkoiset_kohteet(hae)[0]:
            avain = "%s#%d" % (k["repo"], k["numero"])
            tunnus = k["tunnus"] if TUNNUS_RE.match(k["tunnus"]) else "(tuntematon)"
            if avain not in nahtuja and REPO_RE.match(k["repo"]):
                uudet.append((avain, k["repo"], "PR" if k["laji"] == "pr" else "issue", tunnus, k["numero"]))
    except Exception as e:     # myos RajaLoppu: ei merkita mitaan nahdyksi, yritys toistuu seuraavalla ajolla
        virheita = 1
        print("halytys: %s" % kysynta._virhe(e), file=sys.stderr)
    if ensimmainen and virheita:
        return 1, "Halytys: haku ep\u00e4onnistui, tilaa ei luotu."
    lahetetty = 0
    for i, (avain, repo, laji, tunnus, numero) in enumerate(uudet):
        if ensimmainen:
            nahdyt.append(avain)
            continue
        if i >= VIESTEJA_AJOSSA:
            if i == VIESTEJA_AJOSSA:
                tulos = laheta("paatos", "Lis\u00e4\u00e4 ulkopuolisia issueita ja PR:i\u00e4: %d kpl" % (len(uudet) - VIESTEJA_AJOSSA), "Katso GitHubista.")
                if tulos == "lahetetty":
                    nahdyt += [u[0] for u in uudet[VIESTEJA_AJOSSA:]]
            break
        otsikko, teksti = viesti(repo, laji, tunnus, numero)
        if laheta("paatos", otsikko, teksti) == "lahetetty":
            lahetetty += 1
            nahdyt.append(avain)
    kirjoita_json(nahdyt_polku, {"nahdyt": nahdyt[-NAHDYT_MAX:]})
    if ensimmainen:
        return 0, "Halytys: ensimm\u00e4inen ajo, kirjattu %d olemassa olevaa ulkopuolista (ei viestej\u00e4)." % len(uudet)
    return 0, "Halytys: uusia %d, viestej\u00e4 l\u00e4hti %d, hakuvirheit\u00e4 %d." % (len(uudet), lahetetty, virheita)


def main(argv=None, hae=hae_json, tanaan=None, laheta=ilmoita.ilmoita):
    ap = argparse.ArgumentParser(prog="agentti.kysynta_ajo")
    ap.add_argument("toiminto", choices=("viikko", "halytys"))
    ap.add_argument("--historia", default=os.environ.get("KYSYNTA_HISTORIA", HISTORIA_OLETUS))
    ap.add_argument("--nahdyt", default=os.environ.get("KYSYNTA_NAHDYT", NAHDYT_OLETUS))
    a = ap.parse_args(argv)
    try:
        if a.toiminto == "viikko":
            koodi, teksti = aja_viikko(a.historia, tanaan or datetime.date.today().isoformat(), hae)
        else:
            koodi, teksti = aja_halytys(a.nahdyt, hae, laheta)
    except OSError as e:     # esim. kirjoitusoikeus puuttuu: kerro tyyppi, ei polkua tai sisaltoa
        print("kysynta_ajo: %s" % type(e).__name__, file=sys.stderr)
        return 1
    print(teksti)
    return koodi


if __name__ == "__main__":
    sys.exit(main())
