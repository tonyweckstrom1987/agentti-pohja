"""Virhemittari: virheet viikoittain juurisyittain (Tonyn ehdotus 5.11.2026, tehtava 2026-11-05-17).

Lukee claude-jono/virheloki.md:n ja claude-jono/tulokset/*.md:n (tilat EPAONNISTUI ja KESKEN) ja korvaa
claude-jono/tulostaulu.md:n osion "Virhemittari" viikkotaulukolla: viikko, virheita, juurisyyluokat
(1 tehty != toimii, 2 muistista ei lahteesta, 3 vaara oletus ymparistosta, 4 muu, luokittelematon)
ja toistuiko sama juurisyy. Sama juurisyy kahtena perakkaisena viikkona -> korjausehdotus koodiin;
kolmantena -> Tonylle (A8).

Lisamittarit (Tonyn paatos 11.11.2026, A941), jotka luetaan virhelokin vakiokentista; puuttuva kentta = "tuntematon":
  Tapahtui: VVVV-KK-PP HH:MM ja Havaittu: VVVV-KK-PP HH:MM  -> havaitsemisaika tunteina (viikon mediaani)
  Havaitsija: Tony / tarkastaja / testi/CI / agentti itse
  Ehkaisy: koodi / ohje / ei viela  -> lasketaan vain toistuneista juurisyista (luokat 1-3, ks. toisto)
Virheet per 10 tehtavaa: viikon virheet / viikon tehtavat (tehtavat/PVM-*.md) * 10.

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

OTSIKKO_ALKU = "## Virhemittari"
LUOKAT = ("1", "2", "3", "4", "?")
# KESKEN-syyt (ajuri.KESKEN_SYYT): rivi-puuttuu ja ei-todennettu ovat luokan 1 virheita; odottavat eivat ole virheita.
SYY_VIRHE = ("rivi-puuttuu", "ei-todennettu")
SYY_ODOTTAA = ("odottaa-yhdistamista", "odottaa-kayttoonottoa")
KESKEN_SYY_RE = re.compile(r"^- KESKEN-syy:[ \t]*(\S+)[ \t]*$", re.M)
# Virhelokin vakavuus: vain avoimesti kirjattu rivi "Vakavuus: vakava" merkinnan rungossa. Ei arvata (lokissa ei ollut kenttaa 6.11.).
VAKAVUUS_RE = re.compile(r"^[ \t>*_-]*Vakavuus(?:\*\*|__)?[ \t]*:(?:\*\*|__)?[ \t]*(vakava)\b", re.I | re.M)
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
TUNTEMATON = "tuntematon"
HAVAITSIJAT = ("Tony", "tarkastaja", "testi/CI", "agentti itse", TUNTEMATON)
EHKAISYT = ("koodi", "ohje", "ei viel\u00e4", TUNTEMATON)
AIKA_RE = (r"(?:(\d{4})-(\d{2})-(\d{2})|(\d{1,2})\.(\d{1,2})\.(\d{4}))"
           r"(?:[ T]|,? klo )(\d{1,2})[:.](\d{2})")


def parse_paiva(teksti):
    """'2026-11-02' tai '4.11.2026' -> date; muuten None."""
    try:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", teksti)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", teksti)
        if m:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:   # mahdoton paiva (31.2.) -> ei paivaa
        return None
    return None


def osio_paiva(otsikko):
    """Virhelokin osio-otsikon alun paiva: '5.11.2026', '5.-6.11.2026' (en-viiva tai yhdysmerkki) tai '30.10.-2.11.2026' -> date.
    Valin alkupaiva ratkaisee viikon. Muuten None."""
    m = re.match(r"(\d{1,2})\.(?:(\d{1,2})\.)?[ \t]*(?:[\u2013\u2014-][ \t]*\d{1,2}\.(\d{1,2})\.|)(\d{4})\b", otsikko) \
        if re.match(r"\d{1,2}\.(?:\d{1,2}\.)?[ \t]*[\u2013\u2014-]", otsikko) else None
    try:
        if m:
            vuosi = int(m.group(4))
            if m.group(2) and m.group(3) and int(m.group(2)) > int(m.group(3)):
                vuosi -= 1   # vuodenvaihteen yli: '30.12.-2.1.2027' alkaa vuonna 2026
            return datetime.date(vuosi, int(m.group(2) or m.group(3)), int(m.group(1)))
    except ValueError:
        return None
    return parse_paiva(otsikko)


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
        osio = re.match(r"## (\d{1,2}\.(?:\d{1,2}\.)?[ \t]*(?:[\u2013\u2014-][ \t]*\d{1,2}\.\d{1,2}\.)?\d{0,4})", rivi)
        if alku or osio:
            if nykyinen:
                merkinnat.append(nykyinen)
            nykyinen = [parse_paiva(alku.group(1)) if alku else osio_paiva(rivi[3:]), rivi]
        elif rivi.startswith("#"):
            if nykyinen:
                merkinnat.append(nykyinen)
            nykyinen = None
        elif nykyinen:
            nykyinen[1] += "\n" + rivi
    if nykyinen:
        merkinnat.append(nykyinen)
    return [(p, t) for p, t in merkinnat if p]


def _kentta(teksti, nimi):
    """Merkinnan vakiokentan arvo ('Nimi: arvo' rivin tai lauseen sisalla, lihavointi sallittu) tai None."""
    m = re.search(r"(?im)\b" + nimi + r":\**\s*([^\n]+)", teksti)
    return m.group(1).strip() if m else None


def parse_aika(teksti):
    """'2026-11-09 12:30', '2026-11-09T12:30' tai '9.11.2026 klo 12.30' -> datetime; muuten None (pelkka paiva ei riita)."""
    m = re.match(AIKA_RE, (teksti or "").strip())
    if not m:
        return None
    try:
        if m.group(1):
            paiva = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:
            paiva = datetime.date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
        return datetime.datetime.combine(paiva, datetime.time(int(m.group(7)), int(m.group(8))))
    except ValueError:
        return None


def havaitsemisaika_h(teksti):
    """Tunnit Tapahtui-kentasta Havaittu-kenttaan; None jos jompikumpi puuttuu tai jarjestys on virheellinen."""
    a = parse_aika(_kentta(teksti, "Tapahtui"))
    b = parse_aika(_kentta(teksti, "Havaittu"))
    if not a or not b or b < a:
        return None
    return (b - a).total_seconds() / 3600.0


def havaitsija(teksti):
    arvo = (_kentta(teksti, "Havaitsija") or "").lower()
    if arvo.startswith("tony"):
        return "Tony"
    if arvo.startswith("tarkastaja"):
        return "tarkastaja"
    if arvo.startswith(("testi", "ci")):
        return "testi/CI"
    if arvo.startswith(("agentti", "itse", "claude", "pilvi")):
        return "agentti itse"
    return TUNTEMATON


def ehkaisy(teksti):
    arvo = (_kentta(teksti, "Ehk[a\u00e4]isy") or "").lower()
    for nimi in EHKAISYT[:3]:
        if re.match(re.escape(nimi).replace("\u00e4", "[a\u00e4]") + r"\b", arvo):
            return nimi
    return TUNTEMATON


def mediaani(luvut):
    luvut = sorted(luvut)
    if not luvut:
        return None
    k = len(luvut) // 2
    return luvut[k] if len(luvut) % 2 else (luvut[k - 1] + luvut[k]) / 2.0


def lue_tehtavat(hakemisto):
    """Tehtavatiedostojen paivat: tehtavat/VVVV-KK-PP-*.md (README ja muut ohitetaan)."""
    paivat = []
    for p in sorted(Path(hakemisto).glob("*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-", p.name)
        paiva = parse_paiva(m.group(1)) if m else None
        if paiva:
            paivat.append(paiva)
    return paivat


def lue_tulos(teksti):
    """(tila, aika-date, runko) tulostiedoston otsikkoriveilta; None jos tilaa tai aikaa ei loydy."""
    tila = re.search(r"^- Tila: (\S+)", teksti, re.M)
    aika = re.search(r"^- Aika: (\S+)", teksti, re.M)
    if not tila or not aika:
        return None
    paiva = parse_paiva(aika.group(1))
    return (tila.group(1), paiva, teksti) if paiva else None


def lue_kesken_syy(teksti):
    """Tulostiedoston otsikon '- KESKEN-syy: koodi' -rivi; None jos rivia ei ole (vanha tulos)."""
    # Vain otsikkolohko (yhtenainen '- ' -rivien jakso '- Tila:' -rivista); raportin rungon rivia ei lueta.
    otsikko = []
    for rivi in teksti.splitlines():
        if otsikko or rivi.startswith("- Tila:"):
            if not rivi.startswith("- "):
                break
            otsikko.append(rivi)
    m = KESKEN_SYY_RE.search("\n".join(otsikko))
    return m.group(1).lower() if m else None


def luokittele_ajo(tila, teksti):
    """'1'..'4'/'?' = virhe; 'O' = odottaa (ei virhe, oma sarake); None = ei virhe."""
    if tila == "KESKEN":
        # Ajuri (claudejono/ajuri.py, on_todennettu) asettaa KESKEN, kun raportissa ei ole kelvollista todennusta.
        # Syykoodi: odottaa-* = tyo on valmis ja odottaa ihmista, ei virhe. rivi-puuttuu, ei-todennettu, tuntematon
        # koodi ja vanha tulos ilman syykoodia = tehty != toimii, luokka 1.
        if lue_kesken_syy(teksti) in SYY_ODOTTAA:
            return "O"
        return "1"
    if tila == "EPAONNISTUI":
        return "4" if RAJA_RE.search(teksti) else "?"
    return None


def laske(loki_teksti, tulokset, nyt, tehtavat=None):
    """tulokset: [teksti]; tehtavat: [date] (None = ei tietoa). Palauttaa (rivit, tilastot);
    rivit = viikkojarjestyksessa dictit."""
    viikot = {}

    def rivi(v):
        return viikot.setdefault(v, {
            "loki": 0, "ajot": 0, "odottaa": 0, "vakavia": 0, "luokat": dict.fromkeys(LUOKAT, 0), "merkinnat": [],
            "tunnit": [], "kuka": dict.fromkeys(HAVAITSIJAT, 0), "ehkaisy": dict.fromkeys(EHKAISYT, 0),
            "tehtavia": 0 if tehtavat is not None else None})

    n_loki = 0
    for paiva, teksti in lue_loki(loki_teksti):
        r = rivi(viikko(paiva))
        r["loki"] += 1
        luokka = luokittele_loki(teksti)
        r["luokat"][luokka] += 1
        r["vakavia"] += 1 if VAKAVUUS_RE.search(teksti) else 0
        r["merkinnat"].append((luokka, ehkaisy(teksti)))
        r["kuka"][havaitsija(teksti)] += 1
        h = havaitsemisaika_h(teksti)
        if h is not None:
            r["tunnit"].append(h)
        n_loki += 1
    n_ajot = n_luettu = 0
    for teksti in tulokset:
        t = lue_tulos(teksti)
        if not t:
            continue
        n_luettu += 1
        luokka = luokittele_ajo(t[0], t[2])
        if luokka is None:
            continue
        r = rivi(viikko(t[1]))
        if luokka == "O":
            r["odottaa"] += 1
            continue
        r["ajot"] += 1
        r["luokat"][luokka] += 1
        r["kuka"][TUNTEMATON] += 1  # ajurin tilasta ei nay, kuka virheen havaitsi
        n_ajot += 1
    for paiva in tehtavat or []:
        rivi(viikko(paiva))["tehtavia"] += 1
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
        viikot[v]["mediaani_h"] = mediaani(viikot[v]["tunnit"])
        viikot[v]["per10"] = (viikot[v]["yht"] * 10.0 / viikot[v]["tehtavia"]
                              if viikot[v]["tehtavia"] else None)
        for luokka in toistuneet(viikot, jarj, i):
            for l, e in viikot[v]["merkinnat"]:
                if l == luokka:
                    viikot[v]["ehkaisy"][e] += 1
    return [(v, viikot[v]) for v in jarj], {"loki": n_loki, "ajot": n_ajot, "tulostiedostoa": n_luettu}


def putki(viikot, jarj, i, luokka):
    """Montako perakkaista viikkoa (viikkoon i asti) luokassa on virheita."""
    n = 0
    j = i
    while j >= 0 and viikot[jarj[j]]["luokat"][luokka] > 0:
        n += 1
        j -= 1
    return n


def toistuneet(viikot, jarj, i):
    """Luokat 1-3, joiden juurisyy toistui viikolla i (vahintaan kaksi perakkaista viikkoa)."""
    return [k for k in ("1", "2", "3") if putki(viikot, jarj, i, k) >= 2]


def toisto(viikot, jarj, i):
    """Toistuiko juurisyy 1-3 edellisella viikolla (kaksi perakkaista) tai jo kolmannen kerran (A8)."""
    osat = []
    for luokka in ("1", "2", "3"):
        n = putki(viikot, jarj, i, luokka)
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


def luku(x):
    """Desimaaliluku suomalaisittain (pilkku, yksi desimaali); None -> tuntematon."""
    return TUNTEMATON if x is None else ("%.1f" % x).replace(".", ",")


def muodosta_osio(rivit, tilastot, nyt):
    ot = ["Viikko", "Virheit\u00e4", "Virhelokissa", "Ajoissa (EP\u00c4ONNISTUI/KESKEN)", "Vakavia (lokissa)", "Odottaa (ei virhe)"]
    ot += [LUOKKA_OTSIKOT[k] for k in LUOKAT]
    ot.append("Toistuiko sama juurisyy")
    ot += ["Toistuneet: ehk\u00e4isy " + e for e in EHKAISYT]
    ot.append("Havaitsemisaika, mediaani (h)")
    ot += ["Havaitsija: " + k for k in HAVAITSIJAT]
    ot += ["Teht\u00e4vi\u00e4", "Virheit\u00e4 / 10 teht\u00e4v\u00e4\u00e4"]
    out = ["Luvut tuottaa `tools/virhemittari.py` (ajo sunnuntaisin ennen viikkokatsausta). Viikko = ISO-viikko; "
           "tulokset viikotetaan tulostiedoston ajan mukaan.", "",
           "| " + " | ".join(ot) + " |", "|" + "---|" * len(ot)]
    for v, r in rivit:
        solut = [viikko_nimi(v, nyt), str(r["yht"]), str(r["loki"]), str(r["ajot"]), str(r["vakavia"]), str(r["odottaa"])]
        solut += [str(r["luokat"][k]) for k in LUOKAT]
        solut.append(r["toisto"])
        solut += [str(r["ehkaisy"][e]) for e in EHKAISYT]
        solut.append(luku(r["mediaani_h"]))
        solut += [str(r["kuka"][k]) for k in HAVAITSIJAT]
        solut += ["?" if r["tehtavia"] is None else str(r["tehtavia"]), luku(r["per10"])]
        out.append("| " + " | ".join(solut) + " |")
    out += ["",
            "Luokitus: virhelokin merkinn\u00e4t `virheiden-ehkaisy.md`:n kolmen juurisyyn mukaan; jos luokkaa ei n\u00e4e, "
            "merkint\u00e4 on luokittelematon (ei arvata). Ajoista KESKEN on luokka 1, kun syy on `rivi-puuttuu` tai `ei-todennettu` tai syyt\u00e4 ei ole kirjattu (vanha tulos); "
            "KESKEN-syyt `odottaa-yhdistamista` ja `odottaa-kayttoonottoa` n\u00e4kyv\u00e4t omassa sarakkeessaan eiv\u00e4tk\u00e4 ole virheit\u00e4. "
            "Vakavia = virhelokin merkinn\u00e4t, joissa on rivi `Vakavuus: vakava` (ei arvata). EP\u00c4ONNISTUI aikakatkaisun tai kierrosrajan takia on luokka 4. Sama juurisyy "
            "kahtena viikkona: korjausehdotus koodiin; kolmantena: Tonylle (A8).",
            "",
            "Lis\u00e4mittarit (Tonyn p\u00e4\u00e4t\u00f6s 11.11.2026): ehk\u00e4isy (koodi/ohje/ei viel\u00e4) lasketaan vain toistuneiden "
            "juurisyiden (luokat 1\u20133) virhelokimerkinn\u00f6ist\u00e4, havaitsemisaika on viikon mediaani virhelokin kentist\u00e4 Tapahtui ja Havaittu, "
            "havaitsija tulee kent\u00e4st\u00e4 Havaitsija ja teht\u00e4v\u00e4m\u00e4\u00e4r\u00e4 `tehtavat/`-hakemiston tiedostoista (p\u00e4iv\u00e4 nimen alussa). "
            "Puuttuva kentt\u00e4 on \"tuntematon\"; vanhoja merkint\u00f6j\u00e4 ei ole muutettu. Ajojen (EP\u00c4ONNISTUI/KESKEN) havaitsijaa ei tiedet\u00e4, "
            "joten ne lasketaan tuntemattomiin.",
            "",
            "Todennettu k\u00e4yt\u00f6ss\u00e4: skripti ajettu %s oikeaa dataa vastaan, luettu %d virhelokimerkint\u00e4\u00e4 ja "
            "%d tulostiedostoa (joista %d virheit\u00e4: EP\u00c4ONNISTUI tai KESKEN ilman odottava-syyt\u00e4)."
            % (nyt.isoformat(), tilastot["loki"], tilastot["tulostiedostoa"], tilastot["ajot"])]
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
        tehtavat = lue_tehtavat(d / "tehtavat") if (d / "tehtavat").is_dir() else None
        rivit, tilastot = laske(loki, tulokset, nyt, tehtavat)
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
