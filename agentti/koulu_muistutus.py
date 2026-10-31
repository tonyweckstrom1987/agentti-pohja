"""Koulun muistutukset: lukee koulu.md:n ja tulostaa tekstin (ei verkkoa, ei mallikutsuja).

Muoto (yksi tapahtuma per rivi, erotin |):
    PVM | kurssi | tapahtuma | tyyppi [| tehty]
PVM on VVVV-KK-PP, tyyppi on tentti, palautus tai kertaus. Viides sarake on
valinnainen: `x` merkitsee palautuksen tehdyksi. Tyhjat rivit ja #-alkuiset rivit ohitetaan.

Ajo: python3 -m agentti.koulu_muistutus --tiedosto koulu.md [--pvm VVVV-KK-PP]
"""
import argparse
import datetime
import sys

TYYPIT = ("tentti", "palautus", "kertaus")


class KouluVirhe(ValueError):
    """Virheellinen koulu.md-rivi. Viesti kertoo rivinumeron ja syyn."""


def jasenna(teksti):
    """Palauttaa listan (pvm, kurssi, tapahtuma, tyyppi, tehty). Nostaa KouluVirhe virheesta."""
    tapahtumat = []
    for nro, rivi in enumerate(teksti.splitlines(), 1):
        rivi = rivi.strip()
        if not rivi or rivi.startswith("#"):
            continue
        osat = [o.strip() for o in rivi.split("|")]
        if len(osat) not in (4, 5):
            raise KouluVirhe("rivi %d: odotettiin 4-5 saraketta, loytyi %d" % (nro, len(osat)))
        pvm_teksti, kurssi, tapahtuma, tyyppi = osat[:4]
        try:
            pvm = datetime.date.fromisoformat(pvm_teksti)
        except ValueError:
            raise KouluVirhe("rivi %d: virheellinen paivamaara (odotettu VVVV-KK-PP)" % nro)
        if not kurssi or not tapahtuma:
            raise KouluVirhe("rivi %d: kurssi ja tapahtuma eivat voi olla tyhjia" % nro)
        if tyyppi not in TYYPIT:
            raise KouluVirhe("rivi %d: tuntematon tyyppi (sallitut: %s)" % (nro, ", ".join(TYYPIT)))
        tehty = len(osat) == 5 and osat[4].lower() == "x"
        tapahtumat.append((pvm, kurssi, tapahtuma, tyyppi, tehty))
    return sorted(tapahtumat, key=lambda t: (t[0], t[1], t[2]))


def _rivi(t, nayta_pvm=True):
    pvm, kurssi, tapahtuma, tyyppi, _ = t
    alku = "%s " % pvm.strftime("%d.%m.") if nayta_pvm else ""
    return "- %s%s: %s (%s)" % (alku, kurssi, tapahtuma, tyyppi)


def muistutus(tapahtumat, tanaan):
    """Rakentaa muistutustekstin. Puhdas funktio: sama syote antaa saman tekstin."""
    huomenna = tanaan + datetime.timedelta(days=1)
    viikon_loppu = tanaan + datetime.timedelta(days=7)
    osiot = [
        ("Myohassa", [t for t in tapahtumat if t[0] < tanaan and t[3] == "palautus" and not t[4]], True),
        ("Tanaan", [t for t in tapahtumat if t[0] == tanaan], False),
        ("Huomenna", [t for t in tapahtumat if t[0] == huomenna], False),
        ("Seuraavat 7 paivaa", [t for t in tapahtumat if huomenna < t[0] <= viikon_loppu], True),
    ]
    rivit = ["Koulu %s" % tanaan.strftime("%d.%m.%Y")]
    nayta = [(otsikko, ts, p) for otsikko, ts, p in osiot if ts]
    if not nayta:
        rivit.append("Ei muistutettavaa.")
    for otsikko, ts, pvm_nakyviin in nayta:
        rivit.append("")
        rivit.append(otsikko + ":")
        rivit.extend(_rivi(t, pvm_nakyviin) for t in ts)
    return "\n".join(rivit)


def main(argv=None):
    p = argparse.ArgumentParser(description="Koulun muistutukset koulu.md:sta")
    p.add_argument("--tiedosto", default="koulu.md")
    p.add_argument("--pvm", help="VVVV-KK-PP, oletus tanaan")
    a = p.parse_args(argv)
    try:
        tanaan = datetime.date.fromisoformat(a.pvm) if a.pvm else datetime.date.today()
    except ValueError:
        print("Virhe: --pvm pitaa olla muodossa VVVV-KK-PP", file=sys.stderr)
        return 2
    try:
        with open(a.tiedosto, encoding="utf-8") as f:
            tapahtumat = jasenna(f.read())
    except OSError as e:
        print("Virhe: tiedostoa ei voi lukea (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    except KouluVirhe as e:
        print("Virhe tiedostossa %s, %s" % (a.tiedosto, e), file=sys.stderr)
        return 1
    print(muistutus(tapahtumat, tanaan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
