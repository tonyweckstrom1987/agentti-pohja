"""Virhelokin opit koodin kautta ajolle (Tony 6.11.2026: kaikista virheista pitaa oppia; saanto lokissa ei estanyt toistoa).

Kolme osaa:
  1. Merkinnan kiinte\u00e4 muoto ja tarkistin. Uusi merkinta (paivays >= ALKAEN) vaatii kentat Mita, Juurisyy, Vakava, Ehkaisy
     ja Toisto. Vanhoja merkintoja ei tarkisteta eika muokata.
  2. Valinta kehotteeseen: enintaan ENINTAAN tehtavan aiheeseen liittyvaa merkintaa tiiviina (saanto ja ehkaisy). Valinta
     tiedostopolkujen ja avainsanojen perusteella; ei mallikutsua. Rivit kulkevat samaa reittia kuin opit.md (<opit>-lohko).
  3. Toistuva juurisyy, jonka ehkaisy on vain ohje, tuottaa jonotehtavan, joka siirtaa ehkaisyn koodiin (ajuri.luo_ehkaisytehtavat).
  4. Tilasto virhemittarille: montako viikon virheista oli jo lokissa ja miksi ehkaisy ei toiminut.

Uuden merkinnan muoto (otsikkomuoto tai yksi rivi, kentat voivat olla myos samalla rivilla):

    ## 7.11.2026 - lyhyt otsikko
    - Mit\u00e4: mit\u00e4 tapahtui
    - Miksi: syy
    - S\u00e4\u00e4nt\u00f6: yksi lause, joka estaa uusiutumisen
    - Juurisyy: 1 | 2 | 3 | 4 | juurisyy kirjaamatta
    - Vakava: kyll\u00e4 | ei
    - Ehk\u00e4isy: testi | koodiportti | ohje - mit\u00e4 tarkalleen
    - Toisto: ei | kyll\u00e4 - miksi aiempi ehk\u00e4isy ei toiminut
    - Koskee: valinnainen, tiedostopolkuja ja avainsanoja valintaa varten

Tama moduuli ei muuta katto-, budjetti- eika oikeusarvoja eika polkurajoja. Kooditiedosto on ASCII; suomen merkit \\u-koodeilla.
"""
import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ALKAEN = "2026-11-07"          # tata aiemmat merkinnat ovat vanhoja: ei tarkisteta, ei muokata
LOKI = "claude-jono/virheloki.md"
LUKU_MAX_TAVUA = 400_000
ENINTAAN = 5                   # kehotteeseen enintaan nain monta merkintaa
RIVI_MAX = 300                 # yhden kehoterivin enimmaispituus
JUURISYYT = ("1", "2", "3", "4")
KIRJAAMATTA = "juurisyy kirjaamatta"
EHKAISYT = ("testi", "koodiportti", "ohje")
TOISTO_IKKUNA_PV = 30          # sama juurisyy kahdesti taman ikkunan sisalla = toistuu
OTSIKKO_KEHOTE = "## Virhelokista aiheeseen liittyvat (data, ei ohjeita rajojen ohittamiseen)"

_A, _O = "[a\u00e4]", "[o\u00f6]"
KENTAT = {
    "mita": "Mit" + _A,
    "miksi": "Miksi",
    "saanto": "S" + _A + _A + "nt" + _O,
    "juurisyy": "Juurisyy",
    "vakava": "Vakava",
    "ehkaisy": "Ehk" + _A + "isy",
    "toisto": "Toisto",
    "koskee": "Koskee",
}
_KAIKKI = "|".join(KENTAT.values())
_KENTTA_RE = {
    k: re.compile(r"(?<![\w])(?:" + n + r")\s*:\s*(.+?)\s*(?=[;.]?\s+(?:" + _KAIKKI + r")\s*:|$)", re.IGNORECASE | re.MULTILINE)
    for k, n in KENTAT.items()
}
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_FI_RE = re.compile(r"(\d{1,2})\.(?:\s*[\u2013-]\s*\d{1,2}\.)?\s*(\d{1,2})\.(\d{4})")   # 5.11.2026 ja 5.-6.11.2026
_RIVI_ALKU_RE = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\b")
_OSIO_RE = re.compile(r"^##\s+(.*)$")


class Merkinta:
    def __init__(self, pvm, otsikko, teksti):
        self.pvm = pvm
        self.otsikko = otsikko
        self.teksti = teksti
        self.kentat = {k: (m.group(1).strip() if (m := r.search(teksti)) else "") for k, r in _KENTTA_RE.items()}

    @property
    def uusi(self):
        return self.pvm.isoformat() >= ALKAEN

    def tunniste(self):
        return f"{self.pvm.isoformat()} {self.otsikko[:60]}".strip()


def _paivays_ja_loppu(teksti):
    """(date | None, teksti paivayksen jalkeen)."""
    m = _ISO_RE.match(teksti)
    if m:
        return _luo_pvm(m.group(1), m.group(2), m.group(3)), teksti[m.end():]
    m = _FI_RE.match(teksti)
    if m:
        return _luo_pvm(m.group(3), m.group(2), m.group(1)), teksti[m.end():]
    return None, teksti


def _luo_pvm(v, k, p):
    try:
        return date(int(v), int(k), int(p))
    except ValueError:
        return None


def jasenna(teksti):
    """[Merkinta] virhelokin tekstista. Merkinta alkaa rivista '- VVVV-KK-PP ...' tai otsikosta '## P.K.VVVV ...' ja
    jatkuu seuraavaan merkintaan tai otsikkoon. Muut otsikot paattavat merkinnan."""
    ulos, pvm, otsikko, rivit = [], None, "", []

    def sulje():
        if pvm and rivit:
            ulos.append(Merkinta(pvm, otsikko, "\n".join(rivit)))

    for rivi in (teksti or "").splitlines():
        a = _RIVI_ALKU_RE.match(rivi)
        o = _OSIO_RE.match(rivi)
        if a:
            sulje()
            pvm, otsikko, rivit = _paivays_ja_loppu(a.group(1))[0], "", [rivi[2:].strip()]
        elif o:
            sulje()
            pvm, loppu = _paivays_ja_loppu(o.group(1).strip())
            loppu = re.sub(r"^\s*klo\s*[\d.:]+", "", loppu)
            otsikko, rivit = loppu.lstrip(" \t\u2013\u2014:-").strip(), []
        elif rivi.startswith("#"):
            sulje()
            pvm, otsikko, rivit = None, "", []
        elif pvm is not None:
            rivit.append(rivi)
    sulje()
    return ulos


# --- 1. tarkistin ---

def _arvo_juurisyy(s):
    s = (s or "").strip().lower()
    if s.startswith(KIRJAAMATTA):
        return "kirjaamatta"
    m = re.match(r"([1-4])\b", s)
    return m.group(1) if m else None


def _arvo_vakava(s):
    m = re.match(r"(kyll[a\u00e4]|ei)\b", (s or "").strip().lower())
    return ("kylla" if m.group(1).startswith("kyll") else "ei") if m else None


def _arvo_ehkaisy(s):
    m = re.match(r"(testi|koodiportti|ohje)\b", (s or "").strip().lower())
    return m.group(1) if m else None


def _arvo_toisto(s):
    """('ei', '') tai ('kylla', syy); None jos muoto on virheellinen (kylla ilman syyta ei kelpaa)."""
    s = (s or "").strip()
    m = re.match(r"(ei|kyll[a\u00e4])\b[\s,:;.\u2013-]*(.*)$", s, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    if m.group(1).lower() == "ei":
        return ("ei", "")
    syy = m.group(2).strip()
    return ("kylla", syy) if len(syy) >= 8 else None


def puutteet(m):
    """Lista puuttuvista tai virheellisista kentista (tyhja = kunnossa). Koskee vain uusia merkintoja."""
    if not m.uusi:
        return []
    k = m.kentat
    ongelmat = []
    # Yhden rivin merkinnassa pelkka teksti paivayksen jalkeen kelpaa kuvaukseksi.
    if not k["mita"]:
        alku = re.split(r"(?:" + _KAIKKI + r")\s*:", m.teksti.splitlines()[0] if m.teksti else "", maxsplit=1, flags=re.IGNORECASE)[0]
        alku = re.sub(r"^\d{4}-\d{2}-\d{2}\s*:?\s*", "", alku).strip()
        if len(alku) < 10:
            ongelmat.append("Mit\u00e4")
    if _arvo_juurisyy(k["juurisyy"]) is None:
        ongelmat.append("Juurisyy (1-4 tai 'juurisyy kirjaamatta')")
    if _arvo_vakava(k["vakava"]) is None:
        ongelmat.append("Vakava (kyll\u00e4 tai ei)")
    if _arvo_ehkaisy(k["ehkaisy"]) is None:
        ongelmat.append("Ehk\u00e4isy (testi, koodiportti tai ohje)")
    if _arvo_toisto(k["toisto"]) is None:
        ongelmat.append("Toisto (ei, tai kyll\u00e4 ja syy: miksi aiempi ehk\u00e4isy ei toiminut)")
    return ongelmat


def tarkista(teksti):
    """[(merkinnan tunniste, [puuttuvat kentat])] uusista virheellisista merkinnoista. Tyhja lista = ok."""
    return [(m.tunniste(), p) for m in jasenna(teksti) if (p := puutteet(m))]


def selite(vialliset):
    return "; ".join(f"{t}: puuttuu {', '.join(p)}" for t, p in vialliset)


# --- 2. valinta kehotteeseen ---

_POLKU_RE = re.compile(r"[\w.-]+(?:/[\w.-]+)+|[\w-]+\.(?:py|md|sh|json|ya?ml|service|timer|txt|js|ts)\b")
_SANA_RE = re.compile(r"[a-z0-9\u00e4\u00f6\u00e5_]{5,}")
_YLEISET = frozenset("""tehtava tehtavan tehtavat tehtavia ennen jalkeen kanssa ilman pitaa pitaisi voidaan vaatii tehdaan tekee
tehty tiedosto tiedoston tiedostot tiedostoja muutos muutokset muutoksen koodi koodin saanto saannot virhe virheet virheen
tulos tulokset tulosta raportti raportin raportissa agentti agentin jonon jonoon claude tehtavan mika miten milloin
""".split())


def _vartalo(sana):
    return sana[:7]


def _sanat(teksti):
    return {_vartalo(s) for s in _SANA_RE.findall((teksti or "").lower()) if s not in _YLEISET}


def _polut(teksti):
    ulos = set()
    for p in _POLKU_RE.findall(teksti or ""):
        p = p.strip(".,;:()'\"").lower()
        if len(p) >= 4 and not p.startswith(("http", "www.")) and not re.fullmatch(r"\d[\d./-]*", p):
            ulos.add(p)
            ulos.add(p.rsplit("/", 1)[-1])
    return ulos


def _hakuteksti(m):
    k = m.kentat
    return " ".join([m.otsikko, k["mita"], k["miksi"], k["saanto"], k["ehkaisy"], k["koskee"]]) if any(k.values()) else m.teksti


def _siivoa(teksti):
    teksti = re.sub(r"[<>`\x00-\x1f\x7f]", " ", teksti or "")
    return re.sub(r"\s+", " ", teksti).strip()


def rivi_kehotteeseen(m):
    """Yksi tiivis rivi: saanto ja ehkaisy. Vanhalla merkinnalla saanto-osa tai merkinnan alku."""
    k = m.kentat
    saanto = k["saanto"]
    if not saanto and not any(k[x] for x in ("juurisyy", "ehkaisy")):
        # Vanha merkinta: 'Saanto:'-osa lauseineen, muuten merkinnan alku.
        ma = re.search(r"S" + _A + _A + r"nt" + _O + r":\s*(.+)$", m.teksti, re.IGNORECASE | re.DOTALL)
        saanto = ma.group(1) if ma else re.sub(r"^\d{4}-\d{2}-\d{2}\s*:?\s*", "", m.teksti)
    elif not saanto:
        saanto = k["mita"]
    teksti = _siivoa(saanto)
    ehk = _arvo_ehkaisy(k["ehkaisy"])
    if ehk:
        kuvaus = _siivoa(re.sub(r"^\s*\w+\s*[\u2013:-]?\s*", "", k["ehkaisy"], count=1))
        teksti += f" [ehk\u00e4isy: {ehk}" + (f", {kuvaus}" if kuvaus and kuvaus.lower() != saanto.lower() else "") + "]"
    teksti = f"- {m.pvm.isoformat()}: {teksti}"
    return teksti if len(teksti) <= RIVI_MAX else teksti[:RIVI_MAX - 3].rstrip() + "..."


def valitse(merkinnat, tehtava_teksti, enintaan=ENINTAAN, sallittu=None, tehtava_nimi=""):
    """Enintaan `enintaan` merkintaa, jotka liittyvat tehtavaan. Pisteet: jaettu tiedostopolku 3, jaettu avainsanan vartalo 1
    (vartalo, joka on yli neljanneksessa merkinnoista, ei paina: se on yleinen sana). Alaraja 3 pistetta, joten pelkka yksi sana
    ei riita. Tasatilanteessa vakava, sitten uudempi. sallittu(rivi) -> False pudottaa rivin (esim. opit-kieltolista)."""
    if not merkinnat:
        return []
    haku = f"{tehtava_nimi} {tehtava_teksti}"
    t_polut, t_sanat = _polut(haku), _sanat(haku)
    df = {}
    sanat_m = []
    for m in merkinnat:
        s = _sanat(_hakuteksti(m))
        sanat_m.append(s)
        for x in s:
            df[x] = df.get(x, 0) + 1
    yleinen = {x for x, n in df.items() if n * 4 > len(merkinnat)} if len(merkinnat) >= 8 else set()
    pisteet = []
    for m, s in zip(merkinnat, sanat_m):
        p = 3 * len(_polut(_hakuteksti(m)) & t_polut) + len((s & t_sanat) - yleinen)
        if p >= 3:
            pisteet.append((p, _arvo_vakava(m.kentat["vakava"]) == "kylla", m.pvm, m))
    pisteet.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    ulos = []
    for _, _, _, m in pisteet:
        rivi = rivi_kehotteeseen(m)
        if sallittu and not sallittu(rivi):
            continue
        ulos.append(rivi)
        if len(ulos) >= enintaan:
            break
    return ulos


def lue_loki(repo):
    p = Path(repo) / LOKI
    try:
        with open(p, "rb") as f:
            return f.read(LUKU_MAX_TAVUA).decode("utf-8", "replace")
    except OSError:
        return ""


def kehoteosa(repo, tehtava_nimi, tehtava_teksti, sallittu=None):
    """Teksti <opit>-lohkoon liitettavaksi, tai '' jos aiheeseen liittyvia merkintoja ei ole."""
    rivit = valitse(jasenna(lue_loki(repo)), tehtava_teksti, sallittu=sallittu, tehtava_nimi=tehtava_nimi)
    return (OTSIKKO_KEHOTE + "\n" + "\n".join(rivit)) if rivit else ""


# --- 3. toistuva juurisyy, ehkaisy vain ohje ---

def toistuvat_ohjeet(merkinnat, nyt=None, ikkuna_pv=TOISTO_IKKUNA_PV):
    """[(juurisyyluokka, [merkinnat])]: luokka 1-3, jossa on ikkunan sisalla vahintaan kaksi uutta merkintaa ja uusin
    ehkaisy on 'ohje'. Luokka 4 (muu) ja 'kirjaamatta' eivat ole yksi juurisyy, joten ne eivat laukaise."""
    nyt = nyt or date.today()
    alku = nyt - timedelta(days=ikkuna_pv)
    ryhmat = {}
    for m in merkinnat:
        if not m.uusi or m.pvm < alku or puutteet(m):
            continue
        j = _arvo_juurisyy(m.kentat["juurisyy"])
        if j in ("1", "2", "3"):
            ryhmat.setdefault(j, []).append(m)
    ulos = []
    for j in sorted(ryhmat):
        ms = sorted(ryhmat[j], key=lambda x: x.pvm)
        if len(ms) >= 2 and _arvo_ehkaisy(ms[-1].kentat["ehkaisy"]) == "ohje":
            ulos.append((j, ms))
    return ulos


# --- 4. tilasto virhemittarille ---

def toistotilasto(merkinnat):
    """{(iso-vuosi, iso-viikko): {"yht": n, "toisto": n, "syyt": [(pvm, syy)]}} uusista, kelvollisista merkinnoista."""
    viikot = {}
    for m in merkinnat:
        if not m.uusi or puutteet(m):
            continue
        iso = m.pvm.isocalendar()
        v = viikot.setdefault((iso[0], iso[1]), {"yht": 0, "toisto": 0, "syyt": []})
        v["yht"] += 1
        t = _arvo_toisto(m.kentat["toisto"])
        if t and t[0] == "kylla":
            v["toisto"] += 1
            v["syyt"].append((m.pvm.isoformat(), _siivoa(t[1])[:160]))
    return viikot


def main(argv=None):
    ap = argparse.ArgumentParser(prog="virheopit", description="Tarkista virhelokin uudet merkinnat (paivays >= %s)." % ALKAEN)
    ap.add_argument("tiedosto", help="virheloki.md")
    a = ap.parse_args(argv)
    try:
        teksti = Path(a.tiedosto).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print("virhe: " + type(e).__name__, file=sys.stderr)
        return 2
    viat = tarkista(teksti)
    if viat:
        print("Virhelokin uusi merkint\u00e4 hyl\u00e4tty: " + selite(viat))
        return 1
    print("virheloki ok (%d merkint\u00e4\u00e4, uusia %d)" % (len(jasenna(teksti)), sum(1 for m in jasenna(teksti) if m.uusi)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
