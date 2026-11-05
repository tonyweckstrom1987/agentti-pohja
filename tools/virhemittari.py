"""Virhemittari: virheet viikoittain juurisyittain (Tonyn ehdotus 5.11.2026, tehtava 2026-11-05-17).

Lukee claude-jono/virheloki.md:n ja claude-jono/tulokset/*.md:n (tilat EPAONNISTUI ja KESKEN) ja korvaa
claude-jono/tulostaulu.md:n osion "Virhemittari" viikkotaulukolla: viikko, virheita, juurisyyluokat
(1 tehty != toimii, 2 muistista ei lahteesta, 3 vaara oletus ymparistosta, 4 muu, luokittelematon)
ja toistuiko sama juurisyy. Sama juurisyy kahtena perakkaisena viikkona -> korjausehdotus koodiin;
kolmantena -> Tonylle (A8).

Luokittelu ei arvaa: merkinta, jonka luokkaa ei nae saantotaulusta (LOKI_SAANNOT), merkinnan omasta
tunnisteesta "(juurisyy N)" tai ajon tekstista, on "luokittelematon".

Kaytto (ajetaan sunnuntaisin ennen viikkokatsausta):
    python3 tools/virhemittari.py --docs /polku/docs/claude-jono            # tulostaa osion
    python3 tools/virhemittari.py --docs /polku/docs/claude-jono --kirjoita # paivittaa tulostaulu.md:n
    python3 tools/virhemittari.py --docs ... --ulos /polku/uusi-tulostaulu.md
Vain stdlib. Virheista tulostetaan vain tyyppi."""
import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from claudejono import valmis  # noqa: E402

OTSIKKO_ALKU = "## Virhemittari"
LUOKAT = ("1", "2", "3", "4", "?")
LUOKKA_OTSIKOT = {
    "1": "1 tehty \u2260 toimii",
    "2": "2 muistista, ei l\u00e4hteest\u00e4",
    "3": "3 v\u00e4\u00e4r\u00e4 oletus ymp\u00e4rist\u00f6st\u00e4",
    "4": "4 muu",
    "?": "luokittelematon",
}
# Epaonnistumisen syy ajon tekstissa: resurssiraja tai ajuri, ei kolmea juurisyyta -> 4 muu.
RAJA_RE = re.compile(r"aikakatkaisu|error_max_turns|kierrosraja|lukukelvoton", re.I)

# Vanhat virhelokin merkinnat: (tunnistava ASCII-osa merkinnan tekstista, luokka). Perusteet:
# virheiden-ehkaisy.md luettelee tapaukset luokkiin 1-3; muut luokitellaan vain jos merkinnan oma Syy-teksti
# nimeaa syyn. Merkinnat, joita taulukko ei kata, jaavat luokittelemattomiksi.
LOKI_SAANNOT = (
    ("tekstitykset", "1"),            # ehkaisy: juurisyy 1
    ("--auth antoi", "1"),            # ehkaisy: juurisyy 1
    ("README lupasi", "1"),           # ehkaisy: juurisyy 1
    ("CRLF", "1"),                    # ehkaisy: juurisyy 1
    ("kauppa-vienti.sh", "1"),        # ehkaisy: juurisyy 1 (cron PATH)
    ("Kaista-PR #171", "1"),          # Syy: ajettiin vain osa testeista, ei CI:n komentoa
    ("assertNotIn", "1"),             # tarkastaja ei ajanut testia (vain nayttava tarkistus)
    ("aamuhaastattelu", "1"),         # mekanismia ei todennettu ennen lupausta
    ("agentti-pohja #207", "1"),      # putki peitti testin paluuarvon (nayttava tarkistus)
    ("Koulumatkoissa M01", "2"),      # ehkaisy: juurisyy 2
    ("eBay", "2"),                    # ehkaisy: juurisyy 2
    ("ylistiv", "2"),                 # ehkaisy: juurisyy 2
    ("Notebookin listaa", "2"),       # Syy: muistikuva, ei listaa
    ("SERTI HTML", "2"),              # Syy: luettu muistista
    ("englanniksi", "2"),             # kirjattu luokkaan 2 tulostaulun virhemittarissa 5.11.
    ("70 minuuttiin", "3"),           # ehkaisy: juurisyy 3
    ("git stash", "3"),               # Syy: oletus, etta taustatyo on erillinen
    ("test_pr_opit", "4"),            # kellosta riippuva testi (aikapommi)
    ("Toinen aikapommi", "4"),        # sama
)
TUNNISTE_RE = re.compile(r"\(juurisyy ([1-4])\)")


def parse_paiva(teksti):
    """'2026-11-02' tai '4.11.2026' -> date; muuten None."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", teksti)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", teksti)
    if m:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def viikko(paiva):
    """(ISO-vuosi, ISO-viikko)."""
    iso = paiva.isocalendar()
    return (iso[0], iso[1])


def luokittele_loki(teksti):
    m = TUNNISTE_RE.search(teksti)
    if m:
        return m.group(1)
    for osa, luokka in LOKI_SAANNOT:
        if osa in teksti:
            return luokka
    return "?"


def lue_loki(teksti):
    """[(date, merkinnan_teksti)]: rivit '- PVM ...' ja osiot '## P.K.VVVV: otsikko' runkoineen."""
    merkinnat = []
    nykyinen = None
    for rivi in teksti.splitlines():
        alku = re.match(r"- (\d{4}-\d{2}-\d{2})\b", rivi)
        osio = re.match(r"## (\d{1,2}\.\d{1,2}\.\d{4})\b", rivi)
        if alku or osio:
            if nykyinen:
                merkinnat.append(nykyinen)
            nykyinen = [parse_paiva((alku or osio).group(1)), rivi]
        elif rivi.startswith("#"):
            if nykyinen:
                merkinnat.append(nykyinen)
            nykyinen = None
        elif nykyinen:
            nykyinen[1] += "\n" + rivi
    if nykyinen:
        merkinnat.append(nykyinen)
    return [(p, t) for p, t in merkinnat if p]


def lue_tulos(teksti):
    """(tila, aika-date, runko) tulostiedoston otsikkoriveilta; None jos tilaa tai aikaa ei loydy."""
    tila = re.search(r"^- Tila: (\S+)", teksti, re.M)
    aika = re.search(r"^- Aika: (\S+)", teksti, re.M)
    if not tila or not aika:
        return None
    paiva = parse_paiva(aika.group(1))
    return (tila.group(1), paiva, teksti) if paiva else None


def luokittele_ajo(tila, teksti):
    if tila == "KESKEN":
        # Ajuri (claudejono/ajuri.py, on_todennettu) asettaa KESKEN vain, kun raportissa ei ole kelvollista
        # "Todennettu kaytossa: miten" -rivia: tehty != toimii. Siksi KESKEN on aina luokka 1.
        return "1"
    if tila == "EPAONNISTUI":
        return "4" if RAJA_RE.search(teksti) else "?"
    return None


TULOSNIMI_RE = re.compile(r"^# Tulos:\s*(\S+)", re.M)


def laske(loki_teksti, tulokset, nyt, tarkastukset=None):
    """tulokset: [teksti]. tarkastukset: {tulosnimi: tarkastusteksti} (valmis.py). Palauttaa (rivit, tilastot);
    rivit = viikkojarjestyksessa dictit. VALMIS-tulos, jonka tarkastaja totesi toimimattomaksi, on virhe (luokka 1, A516)."""
    tarkastukset = tarkastukset or {}
    viikot = {}

    def rivi(v):
        return viikot.setdefault(v, {"loki": 0, "ajot": 0, "valmis_rikki": 0, "luokat": dict.fromkeys(LUOKAT, 0)})

    n_loki = 0
    for paiva, teksti in lue_loki(loki_teksti):
        r = rivi(viikko(paiva))
        r["loki"] += 1
        r["luokat"][luokittele_loki(teksti)] += 1
        n_loki += 1
    n_ajot = n_luettu = n_rikki = 0
    for teksti in tulokset:
        t = lue_tulos(teksti)
        if not t:
            continue
        n_luettu += 1
        luokka = luokittele_ajo(t[0], t[2])
        rikki = False
        if luokka is None and t[0] == "VALMIS":
            nimi = TULOSNIMI_RE.search(t[2])
            rikki = bool(nimi) and valmis.toimimaton(tarkastukset.get(nimi.group(1)))
            if rikki:
                luokka = "1"   # tehty != toimii
        if luokka is None:
            continue
        r = rivi(viikko(t[1]))
        if rikki:
            r["valmis_rikki"] += 1
            n_rikki += 1
        r["ajot"] += 1
        r["luokat"][luokka] += 1
        n_ajot += 1
    # Kaikki viikot ensimmaisesta nykyiseen, myos tyhjat.
    if viikot:
        ensin = min(viikot)
        v = ensin
        paiva = datetime.date.fromisocalendar(v[0], v[1], 1)
        loppu = viikko(nyt)
        while viikko(paiva) <= loppu:
            rivi(viikko(paiva))
            paiva += datetime.timedelta(days=7)
    jarj = sorted(viikot)
    for i, v in enumerate(jarj):
        viikot[v]["yht"] = viikot[v]["loki"] + viikot[v]["ajot"]
        viikot[v]["toisto"] = toisto(viikot, jarj, i)
    return [(v, viikot[v]) for v in jarj], {"loki": n_loki, "ajot": n_ajot, "tulostiedostoa": n_luettu, "valmis_rikki": n_rikki}


def toisto(viikot, jarj, i):
    """Toistuiko juurisyy 1-3 edellisella viikolla (kaksi perakkaista) tai jo kolmannen kerran (A8)."""
    osat = []
    for luokka in ("1", "2", "3"):
        n = 0
        j = i
        while j >= 0 and viikot[jarj[j]]["luokat"][luokka] > 0:
            n += 1
            j -= 1
        if n == 2:
            osat.append("kyll\u00e4: luokka %s kahtena viikkona, tee korjausehdotus koodiin" % luokka)
        elif n >= 3:
            osat.append("kolmas kerta: luokka %s, Tonylle (A8)" % luokka)
    return "; ".join(osat) if osat else "ei"


def viikko_nimi(v, nyt):
    ma = datetime.date.fromisocalendar(v[0], v[1], 1)
    su = ma + datetime.timedelta(days=6)
    nimi = "%d (%d.%d.\u2013%d.%d.)" % (v[1], ma.day, ma.month, su.day, su.month)
    if nyt <= su:
        nimi += ", kesken"
    return nimi


def muodosta_osio(rivit, tilastot, nyt):
    ot = ["Viikko", "Virheit\u00e4", "Virhelokissa", "Ajoissa (EP\u00c4ONNISTUI/KESKEN/VALMIS ja toimimaton)", "VALMIS, my\u00f6hemmin toimimaton"]
    ot += [LUOKKA_OTSIKOT[k] for k in LUOKAT]
    ot.append("Toistuiko sama juurisyy")
    out = ["Luvut tuottaa `tools/virhemittari.py` (ajo sunnuntaisin ennen viikkokatsausta). Viikko = ISO-viikko; "
           "tulokset viikotetaan tulostiedoston ajan mukaan.", "",
           "| " + " | ".join(ot) + " |", "|" + "---|" * len(ot)]
    for v, r in rivit:
        solut = [viikko_nimi(v, nyt), str(r["yht"]), str(r["loki"]), str(r["ajot"]), str(r["valmis_rikki"])]
        solut += [str(r["luokat"][k]) for k in LUOKAT]
        solut.append(r["toisto"])
        out.append("| " + " | ".join(solut) + " |")
    out += ["",
            "Luokitus: virhelokin merkinn\u00e4t `virheiden-ehkaisy.md`:n kolmen juurisyyn mukaan; jos luokkaa ei n\u00e4e, "
            "merkint\u00e4 on luokittelematon (ei arvata). Ajoista KESKEN (ei kelvollista \"Todennettu k\u00e4yt\u00f6ss\u00e4\" -rivi\u00e4) on luokka 1 "
            "(4.11. l\u00f6ydetty juurisyy), VALMIS-tulos, jonka riippumaton tarkastaja totesi toimimattomaksi "
            "(`tulokset/<tulos>.tarkastus.md`: \"Toimii k\u00e4yt\u00f6ss\u00e4: ei\" + todiste), on luokka 1 ja n\u00e4kyy omassa sarakkeessaan, EP\u00c4ONNISTUI aikakatkaisun tai kierrosrajan takia on luokka 4. Sama juurisyy "
            "kahtena viikkona: korjausehdotus koodiin; kolmantena: Tonylle (A8).",
            "",
            "Todennettu k\u00e4yt\u00f6ss\u00e4: skripti ajettu %s oikeaa dataa vastaan, luettu %d virhelokimerkint\u00e4\u00e4 ja "
            "%d tulostiedostoa (joista %d EP\u00c4ONNISTUI, KESKEN tai toimimattomaksi todettu VALMIS; n\u00e4ist\u00e4 VALMIS: %d)."
            % (nyt.isoformat(), tilastot["loki"], tilastot["tulostiedostoa"], tilastot["ajot"], tilastot["valmis_rikki"])]
    return out


def korvaa_osio(tulostaulu, osio_rivit):
    """Korvaa osion '## Virhemittari...' rungon; otsikkorivi sailyy. ValueError jos osiota ei ole."""
    rivit = tulostaulu.split("\n")
    alku = next((i for i, r in enumerate(rivit) if r.startswith(OTSIKKO_ALKU)), None)
    if alku is None:
        raise ValueError("tulostaulusta puuttuu osio: " + OTSIKKO_ALKU)
    loppu = next((i for i in range(alku + 1, len(rivit)) if rivit[i].startswith("## ")), len(rivit))
    return "\n".join(rivit[:alku + 1] + [""] + osio_rivit + [""] + rivit[loppu:])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--docs", required=True, help="hakemisto claude-jono (sisaltaa virheloki.md, tulostaulu.md, tulokset/)")
    ap.add_argument("--nyt", help="paiva VVVV-KK-PP (oletus: tanaan)")
    ap.add_argument("--kirjoita", action="store_true", help="paivita tulostaulu.md paikallaan")
    ap.add_argument("--ulos", help="kirjoita paivitetty tulostaulu tahan tiedostoon")
    a = ap.parse_args(argv)
    try:
        nyt = parse_paiva(a.nyt) if a.nyt else datetime.date.today()
        d = Path(a.docs)
        loki = (d / "virheloki.md").read_text(encoding="utf-8")
        tulokset = [p.read_text(encoding="utf-8", errors="replace") for p in sorted((d / "tulokset").glob("*.md"))]
        tarkastukset = {p.name[:-len(valmis.PAATE)] + ".md": p.read_text(encoding="utf-8", errors="replace")
                        for p in (d / "tulokset").glob("*" + valmis.PAATE)}
        rivit, tilastot = laske(loki, tulokset, nyt, tarkastukset)
        osio = muodosta_osio(rivit, tilastot, nyt)
        if a.kirjoita or a.ulos:
            uusi = korvaa_osio((d / "tulostaulu.md").read_text(encoding="utf-8"), osio)
            kohde = Path(a.ulos) if a.ulos else d / "tulostaulu.md"
            kohde.parent.mkdir(parents=True, exist_ok=True)
            kohde.write_text(uusi, encoding="utf-8")
        else:
            print("\n".join(osio))
    except (OSError, ValueError, AttributeError) as e:
        print("virhe: " + type(e).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
