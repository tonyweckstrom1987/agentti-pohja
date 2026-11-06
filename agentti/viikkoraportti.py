"""Viikkoraportti optimoinnista (doc 34, kohta 6): kulut, virheet, ehdotusten hyvaksymis-% ja resurssit.

Lukee vain tapahtumalokin (ei kirjoita) ja palvelimen perusluvut; ei verkkoa, ei mallikutsua, vain stdlib.
Ehdotukset ovat saantopohjaisia vihjeita (esim. "harkitse halvempaa mallia"); raportti ei toteuta mitaan.

Kaytto:
    TAPAHTUMALOKI=/var/lib/yojono/tapahtumaloki.db python3 -m agentti.viikkoraportti [PAIVAA]
Tulostaa tekstin; lahetys Telegramiin: `python3 -m agentti.ilmoita valmis "Viikkoraportti" "<teksti>"`.
"""
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

from . import oppimittari, tapahtumaloki

TEKSTI_MAX = 1200
KULUOSUUS_RAJA = 50.0      # %: yksi agentti syo yli puolet kuluista
VIRHEOSUUS_RAJA = 10.0     # %: virheiden osuus tapahtumista
HYVAKSYMIS_RAJA = 30.0     # %: hylkaysten maara kasvaa
MIN_PAATOKSIA = 5
LEVY_RAJA = 85.0           # % kaytetty
MUISTI_RAJA = 15.0         # % vapaana
# Viikkoraportti ajetaan CT115:ssa (viikkoraportti.service). Siella elava klooni on ensin, dokumenttikopio varalla.
# Ymparistomuuttuja VIRHELOKI ohittaa listan.
VIRHELOKI_OLETUKSET = ("/var/lib/claude-jono/docs/claude-jono/virheloki.md", "/opt/agenttifirma-docs/claude-jono/virheloki.md")


def _koodit(yht, alku, tulos):
    """Tapahtumien `virhekoodi` (tiedot_json) tuloksittain; puuttuva koodi (vanhat rivit) = 'tuntematon'."""
    koodit = {}
    for (teksti,) in yht.execute("SELECT tiedot_json FROM tapahtuma WHERE aika>=? AND tulos=?", (alku, tulos)):
        try:
            koodi = json.loads(teksti or "{}").get("virhekoodi") or "tuntematon"
        except (ValueError, AttributeError):
            koodi = "tuntematon"
        koodit[koodi] = koodit.get(koodi, 0) + 1
    return koodit


def keraa(yht, paivaa=7, nyt=None):
    """Palauttaa raportin raakaluvut tapahtumalokista. Ei muuta tietokantaa."""
    nyt = time.time() if nyt is None else nyt
    alku = nyt - paivaa * 86400
    kulu_agentti = {a: round(k, 6) for a, k in yht.execute(
        "SELECT agentti, COALESCE(SUM(kulu_usd),0) FROM tapahtuma WHERE aika>=? GROUP BY agentti", (alku,))}
    yhteensa, virheet_n = yht.execute(
        "SELECT COUNT(*), COALESCE(SUM(tulos='virhe'),0) FROM tapahtuma WHERE aika>=?", (alku,)).fetchone()
    koodit = _koodit(yht, alku, "virhe")
    ohituskoodit = _koodit(yht, alku, "ohitettu")      # ei ajettu eika kulua (nyt: forkin synkkaus), mutta syy halutaan nakyviin
    perus = tapahtumaloki.yhteenveto(yht, paivaa, nyt=nyt)
    tilat = perus["ehdotukset"]
    return {"paivaa": paivaa, "tapahtumia": yhteensa, "virheita": int(virheet_n), "virhekoodit": koodit,
            "ohitettuja": sum(ohituskoodit.values()), "ohituskoodit": ohituskoodit,
            "kulu_usd": perus["kulu_usd"], "kulu_agentti": kulu_agentti,
            "hyvaksymis_pros": perus["hyvaksymis_pros"], "paatettyja": tilat.get("hyvaksytty", 0) + tilat.get("hylatty", 0)}


def resurssit(levypolku="/"):
    """Levyn kaytto %, vapaa muisti % ja 1 min kuorma; puuttuva arvo on None."""
    levy = muisti = kuorma = None
    try:
        k = shutil.disk_usage(levypolku)
        levy = round(100.0 * k.used / k.total, 1)
    except (OSError, ZeroDivisionError):
        pass
    try:
        luvut = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for rivi in f:
                nimi, _, loppu = rivi.partition(":")
                if nimi in ("MemTotal", "MemAvailable"):
                    luvut[nimi] = int(loppu.split()[0])
        muisti = round(100.0 * luvut["MemAvailable"] / luvut["MemTotal"], 1)
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        pass
    try:
        kuorma = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    return {"levy_kaytetty_pros": levy, "muisti_vapaa_pros": muisti, "kuorma_1min": kuorma}


def vihjeet(tiedot, res):
    """Saantopohjaiset optimointivihjeet. Tyhja lista = ei huomautettavaa."""
    v = []
    kulu = tiedot["kulu_usd"]
    kuluttajia = [a for a, k in tiedot["kulu_agentti"].items() if k > 0]
    if kulu > 0 and len(kuluttajia) >= 2:  # yhden kuluttajan 100 % osuus ei kerro mitaan
        agentti, summa = max(tiedot["kulu_agentti"].items(), key=lambda p: p[1])
        osuus = 100.0 * summa / kulu
        if osuus > KULUOSUUS_RAJA:
            v.append("Agentti %s kuluttaa %.0f %% kuluista: harkitse halvempaa mallia tai tehtavan rajausta." % (agentti, osuus))
    if tiedot["tapahtumia"]:
        osuus = 100.0 * tiedot["virheita"] / tiedot["tapahtumia"]
        if osuus > VIRHEOSUUS_RAJA and tiedot["virhekoodit"]:
            koodi = max(tiedot["virhekoodit"].items(), key=lambda p: p[1])[0]
            v.append("Virheita %.0f %% tapahtumista, yleisin koodi %s: selvita syy." % (osuus, koodi))
    forkit = sum(n for k, n in tiedot.get("ohituskoodit", {}).items() if k.startswith("fork_")) + sum(n for k, n in tiedot["virhekoodit"].items() if k.startswith("fork_"))
    if forkit:
        v.append("Forkin synkkaus esti %d tehtavaa: katso koodit (fork_ei_ff = fork erkaantunut, fork_workflow_oikeus = botin tunnukselta puuttuu workflow-oikeus); synkkaa kasin." % forkit)
    pros = tiedot["hyvaksymis_pros"]
    if pros is not None and tiedot["paatettyja"] >= MIN_PAATOKSIA and pros < HYVAKSYMIS_RAJA:
        v.append("Vain %.0f %% ehdotuksista hyvaksytty: tarkenna ehdotusten kriteereita." % pros)
    if res.get("levy_kaytetty_pros") is not None and res["levy_kaytetty_pros"] > LEVY_RAJA:
        v.append("Levy %.0f %% taynna: siivoa tai laajenna." % res["levy_kaytetty_pros"])
    if res.get("muisti_vapaa_pros") is not None and res["muisti_vapaa_pros"] < MUISTI_RAJA:
        v.append("Vapaata muistia vain %.0f %%: harkitse lisamuistia." % res["muisti_vapaa_pros"])
    return v


def muodosta(tiedot, res, oppiminen=None):
    """Raportin teksti (pelkka teksti, enintaan TEKSTI_MAX merkkia)."""
    r = ["Viikkoraportti (%d pv)" % tiedot["paivaa"]]
    if not tiedot["tapahtumia"]:
        r.append("Ei tapahtumia lokissa.")
    else:
        r.append("Tapahtumia %d, virheita %d, kulu %.2f USD." % (tiedot["tapahtumia"], tiedot["virheita"], tiedot["kulu_usd"]))
        if tiedot["kulu_agentti"]:
            r.append("Kulu agenteittain: " + ", ".join(
                "%s %.2f" % (a, k) for a, k in sorted(tiedot["kulu_agentti"].items(), key=lambda p: -p[1])[:5]))
        if tiedot["virhekoodit"]:
            r.append("Virhekoodit: " + ", ".join("%s x%d" % (k, n) for k, n in sorted(tiedot["virhekoodit"].items(), key=lambda p: -p[1])[:5]))
        if tiedot.get("ohituskoodit"):
            r.append("Ohitettu %d (ei kulua): " % tiedot["ohitettuja"] + ", ".join("%s x%d" % (k, n) for k, n in sorted(tiedot["ohituskoodit"].items(), key=lambda p: -p[1])[:5]))
    pros = tiedot["hyvaksymis_pros"]
    r.append("Ehdotusten hyvaksymis-%%: %s (paatettyja %d)." % ("-" if pros is None else "%.0f" % pros, tiedot["paatettyja"]))
    osat = []
    if res.get("levy_kaytetty_pros") is not None:
        osat.append("levy %.0f %%" % res["levy_kaytetty_pros"])
    if res.get("muisti_vapaa_pros") is not None:
        osat.append("muisti vapaa %.0f %%" % res["muisti_vapaa_pros"])
    if res.get("kuorma_1min") is not None:
        osat.append("kuorma %.2f" % res["kuorma_1min"])
    if osat:
        r.append("Resurssit: " + ", ".join(osat) + ".")
    ehdotukset = vihjeet(tiedot, res)
    r.append("Vihjeet: " + (" ".join(ehdotukset) if ehdotukset else "ei huomautettavaa."))
    if oppiminen:      # oppimisen mittari (agentti.oppimittari); viimeisena, jotta TEKSTI_MAX-katkaisu leikkaa ensin sen eika halytyksia
        r.append(oppiminen)
    teksti = "\n".join(r)
    return teksti[:TEKSTI_MAX]


def oppimisrivi(nyt=None):
    """Oppimisen mittarin rivi virhelokista. Lokin puuttuminen kerrotaan rivilla, ettei mittari katoa raportista hiljaa."""
    ymp = os.environ.get("VIRHELOKI")
    polut = (ymp,) if ymp else VIRHELOKI_OLETUKSET
    for polku in polut:
        try:
            with open(polku, encoding="utf-8") as f:
                teksti = f.read()
        except (OSError, ValueError):     # ValueError: ei UTF-8
            continue
        return oppimittari.rivi(teksti, nyt or datetime.now(timezone.utc).date())
    return "Oppiminen: virhelokia ei l\u00f6ytynyt (%d polkua kokeiltu)." % len(polut)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    polku = os.environ.get("TAPAHTUMALOKI")
    if not polku or len(argv) > 1:
        print("kaytto: TAPAHTUMALOKI=polku python3 -m agentti.viikkoraportti [PAIVAA]", file=sys.stderr)
        return 2
    try:
        paivaa = int(argv[0]) if argv else 7
        if not 1 <= paivaa <= 366:
            raise ValueError("paivaa 1-366")
    except ValueError as e:
        print("viikkoraportti: %s" % e, file=sys.stderr)
        return 2
    if polku != ":memory:" and not os.path.exists(polku):
        print("viikkoraportti: lokia ei ole", file=sys.stderr)
        return 2
    yht = tapahtumaloki.avaa(polku)
    try:
        print(muodosta(keraa(yht, paivaa), resurssit(), oppimisrivi()))
    finally:
        yht.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
