"""Itsekehittyva silmukka (Tonyn linjaus 2.11.2026 klo 16.10): jono ei tyhjene, optimointi toistuu, laatua tarkkaillaan.

Tama moduuli sisaltaa kolme asiaa, jotka ajuri kutsuu:
1. tarkista_tehtava / suodata: itsesuunnittelun tehtavien porttitarkistin. Julkaisu, raha, oikeudet, katot, ajastukset ja
   uusi suunta eivat mene jonoon vaan Tonyn sunnuntaiehdotuksiin.
2. luo_toistuvat: viikoittainen optimointikierros (toisto 7 pv) pohjasta toistuvat/optimointi.md.
3. pysaytetty: laatuotoksen pysaytysehto (laatu.py kirjaa tilan) estaa itsesuunnittelun ja toistuvat tehtavat.

Tama moduuli EI muuta katto-, oikeus- tai polkurajoja. Se vain kiristaa sita, mika paasee jonoon.
"""
import json
import re
from datetime import date, datetime
from pathlib import Path

JONO = "claude-jono"
AVOIMIA_ALARAJA = 5          # alle taman: itsesuunnittelu taydentaa jonoa (kompassi, Itsekehittyva infra)
TOISTUVAT_HAKEMISTO = Path(__file__).resolve().parent / "toistuvat"
TOISTUVAT = {"optimointi": 7}   # nimi -> toistovali vuorokausina
LAATU_TILA = "laatu-tila.json"
TOISTUVA_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d{2}-auto-([a-z0-9-]+)\.md$")

PISTEET_RE = re.compile(
    r"^[ \t]*Pisteet:[ \t]*hy.ty[ \t]*([1-5])\b.*?varmuus[ \t]*([1-5])\b.*?vaiva[ \t]*(\d+(?:[.,]\d+)?)[ \t]*h",
    re.IGNORECASE | re.MULTILINE)
SUUNTA_RE = re.compile(r"^[ \t]*Suunta:[ \t]*(jatkoa|uusi)\b", re.IGNORECASE | re.MULTILINE)

# Karkea toimintosanalista: parempi ohjata Tonylle liikaa kuin paastaa lapi, mutta ei niin tiukka, etta jono tyhjenee.
# Takautuva koe 59 aiemmalla itse keksityilla tehtavalla (2.11.2026) nayttaa, mita pelkkien aihesanojen kaytto laukaisi
# (76 % tehtavista, mm. "julkaisulista", "systemd timer" diagnoosissa). Siksi luokat tunnistavat TOIMINNAN (muuta, lisaa, osta,
# julkaise), eivat pelkkaa aihetta. Ei-ASCII-merkit kirjoitetaan \u-koodeilla.
_A = "[a\u00e4]"
_O = "[o\u00f6]"
MUUTA = r"(?:lis" + _A + r"|muuta|muokkaa|vaihda|aseta|nosta|laske|kasvata|poista|laajenna|my" + _O + r"nn" + _A + r"|anna|asenna|luo|ota k" + _A + r"ytt" + _O + _O + r"n|s" + _A + _A + r"d" + _A + r")\w*"
LUOKAT = {
    "julkaisu": re.compile(r"\bjulkaise\b|\bjulkaisen\b|\bjulkaisemme\b|\bjulkaistaan\b|npm publish|gh release|\bpostaa\b|merkitse julkaistuksi",
                           re.IGNORECASE),
    "raha": re.compile(r"\b(?:osta(?! ja pid)|ostaa|ostetaan|maksa|maksaa|maksetaan|tilaa|tilataan|hanki|hankitaan|sijoita|sijoitetaan)\b",
                       re.IGNORECASE),
    "oikeudet": re.compile(r"\bsudoers|\bvisudo|\bchmod|\bchown|\bsetfacl|\bdeploy.?key"
                           r"|" + MUUTA + r"\s+(?:\w+\s+){0,3}(?:oikeu\w*|permission\w*)", re.IGNORECASE),
    "kulukatto": re.compile(r"\bauto_max|\bauto_per|" + MUUTA + r"\s+(?:\w+\s+){0,3}(?:kulukat\w*|\w*katto\w*|\w*katot|\w*katon|budjet\w*"
                            r"|p" + _A + r"iv" + _A + r"raja\w*|automerge\w*)", re.IGNORECASE),
    "ajastus": re.compile(r"\boncalendar|" + MUUTA + r"\s+(?:\w+\s+){0,3}(?:ajastin\w*|ajastu\w*|timer\w*|cron\w*)", re.IGNORECASE),
    "uusi-suunta": re.compile(r"uusi suunta|uusi tuote|uusi projekti|uusi agentti|uusi palvelu|uusi repo|aloita uusi|pivot",
                              re.IGNORECASE),
}
# Kielto koskee vain sen jalkeista toimintaa samassa lauseenosassa ("ala nosta kattoa"). Sana lauseessa ei riita vapauttamaan:
# "Nosta katto, jos budjetti ei riita" ja "Muuta budjettia ilman Tonyn lupaa" ovat ehdotuksia.
KIELTO_RE = re.compile(r"\b(ei|eik[a\u00e4]|eiv[a\u00e4]t|ettei|[a\u00e4]l[a\u00e4]|[a\u00e4]l[a\u00e4]k[a\u00e4]|ilman|kielletty|vain luku)\b", re.IGNORECASE)
LAUSEENOSA_RE = re.compile(r"[,:]|\s(?:ja|mutta|vaan|sek[a\u00e4])\s", re.IGNORECASE)


def ehdottaa(lause, re_):
    """True, jos lauseessa on osuma, jota ei edella kielto samassa lauseenosassa."""
    for m in re_.finditer(lause):   # koko lauseesta, jotta "osta ja pida" -poikkeus toimii
        alku = 0
        for erotin in LAUSEENOSA_RE.finditer(lause, 0, m.start()):
            alku = erotin.end()
        if not KIELTO_RE.search(lause[alku:m.start()]):
            return True
    return False


def lauseet(teksti):
    """Pilkkoo tekstin riveiksi ja lauseiksi. Tyhjat pudotetaan."""
    out = []
    for rivi in (teksti or "").splitlines():
        for l in re.split(r"(?<=[.!?;])\s+", rivi):
            if l.strip():
                out.append(l.strip())
    return out


def tarkista_tehtava(sisalto):
    """(luokka, syy). luokka: 'ok', 'sunnuntai' (Tonyn paatettava) tai 'hylatty' (muotovirhe).

    Hylatty: pisteytys tai suunta puuttuu. Sunnuntai: Suunta on uusi tai lause ehdottaa julkaisua, rahaa, oikeuksia,
    kulukattoja, ajastuksia tai uutta suuntaa. Kielto ennen toimintaa samassa lauseenosassa ("ei saa muuttaa kattoja") ei laukaise tarkistinta."""
    if not PISTEET_RE.search(sisalto or ""):
        return "hylatty", "Pisteet-rivi puuttuu tai on virheellinen (hyoty, varmuus, vaiva h)"
    m = SUUNTA_RE.search(sisalto)
    if not m:
        return "hylatty", "Suunta-rivi puuttuu (jatkoa tai uusi)"
    if m.group(1).lower() == "uusi":
        return "sunnuntai", "uusi-suunta"
    for l in lauseet(sisalto):
        for nimi, re_ in LUOKAT.items():
            if ehdottaa(l, re_):
                return "sunnuntai", nimi
    return "ok", ""


def otsikko(sisalto):
    for r in (sisalto or "").splitlines():
        if r.strip():
            return r.strip().lstrip("# ").strip()[:100]
    return ""


def suodata(tehtavat):
    """(hyvaksytyt, sunnuntaiehdotukset, hylatyt) tehtavalistasta. Jarjestys sailyy."""
    hyva, sunn, huono = [], [], []
    for t in tehtavat:
        luokka, syy = tarkista_tehtava(t.get("sisalto", ""))
        if luokka == "ok":
            hyva.append(t)
        elif luokka == "sunnuntai":
            sunn.append((t, syy))
        else:
            huono.append((t, syy))
    return hyva, sunn, huono


def sunnuntairivit(sunn):
    """Tonylle.md:hen kirjoitettavat rivit (sallittu polku). Tiedosto sunnuntaiehdotukset.md vaatisi sallittu_polku-muutoksen."""
    return "\n".join(f"- Sunnuntaiehdotus ({syy}): {t['nimi']} - {otsikko(t['sisalto'])}" for t, syy in sunn)[:900]


def avoimia(repo):
    """Tehtavat, joilla ei ole tulosta."""
    teht = Path(repo) / JONO / "tehtavat"
    tul = Path(repo) / JONO / "tulokset"
    if not teht.is_dir():
        return 0
    return sum(1 for p in teht.glob("*.md") if not (tul / p.name).exists())


def erapaiva(repo, nimi):
    """Toistuvan tehtavan seuraava luontipaiva: edellisen -auto-<nimi>.md:n paiva + toistovali. None = ei viela tehty (heti)."""
    teht = Path(repo) / JONO / "tehtavat"
    paivat = []
    for p in teht.glob(f"*-auto-{nimi}.md") if teht.is_dir() else []:
        m = TOISTUVA_RE.match(p.name)
        if m and m.group(2) == nimi:
            paivat.append(date.fromisoformat(m.group(1)))
    if not paivat:
        return None
    return max(paivat).toordinal() + TOISTUVAT[nimi]


def erapaiva_tullut(repo, nimi, nyt):
    e = erapaiva(repo, nimi)
    return e is None or nyt.date().toordinal() >= e


def toistuvan_sisalto(nimi):
    p = TOISTUVAT_HAKEMISTO / f"{nimi}.md"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def toistuvat_erapaivalla(repo, nyt):
    """Toistuvat tehtavat, jotka pitaa luoda nyt: erapaiva tullut eika edellinen ole viela ajamatta."""
    tul = Path(repo) / JONO / "tulokset"
    teht = Path(repo) / JONO / "tehtavat"
    out = []
    for nimi in TOISTUVAT:
        odottaa = any(not (tul / p.name).exists() for p in teht.glob(f"*-auto-{nimi}.md")) if teht.is_dir() else False
        if not odottaa and erapaiva_tullut(repo, nimi, nyt) and toistuvan_sisalto(nimi) is not None:
            out.append(nimi)
    return out


def tila_polku(tyohak):
    return Path(tyohak) / LAATU_TILA


def lue_laatu_tila(tyohak):
    try:
        d = json.loads(tila_polku(tyohak).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def pysaytetty(tyohak):
    """Pysaytysehdon syy (merkkijono) tai None. Pysaytys poistuu vain kun Tony ajaa: python3 -m claudejono vapauta."""
    p = lue_laatu_tila(tyohak).get("pysaytetty")
    if isinstance(p, dict) and p.get("syy"):
        return str(p["syy"])[:200]
    return None
