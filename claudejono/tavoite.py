"""Tehtavien tavoitesidonta, kaistaosuudet ja jalkimittari (Tonyn paatokset A839-A840, 7.11.2026).

Jokainen uusi tehtava nimeaa kompassin tavoitteen riviilla `Tavoite: <portfolio | ansainta | Aivo | yllapito>`
(hyvaksytaan myos `TO-paikka/portfolio`). Ilman rivia tehtavaa ei aloiteta; ajuri kirjaa hylkayksen (ei kaatumista).
Rivi on dataa: vain tama suljettu lista kelpaa.

Kaistaosuudet (koodissa):
- kaista 3 (kun kaistoja on 3): vain Aivo-tehtavat; jos Aivo-tehtavia ei ole, vain yllapito.
- kaista 1: portfolio, sitten yllapito, sitten (ellei kumpaakaan ole) ansainta.  kaista 2: ansainta, sitten yllapito, sitten portfolio.
  Kaistat 1-2 eivat ota Aivo-tehtavia, kun kolmas kaista on kaytossa. Jos kaistoja on alle 3, Aivo-rajaus ei koske ketaan.
Tama ei muuta kulukattoja, oikeuksia eika polkurajoja.
"""
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

PORTFOLIO, ANSAINTA, AIVO, YLLAPITO = "portfolio", "ansainta", "aivo", "yllapito"
TAVOITTEET = (PORTFOLIO, ANSAINTA, AIVO, YLLAPITO)
NAYTTONIMI = {PORTFOLIO: "TO-paikka/portfolio", ANSAINTA: "ansainta", AIVO: "Aivo", YLLAPITO: "yll\u00e4pito"}

# Pakollisuus alkaa tasta paivasta (tiedostonimen paivays). Vanhat jonossa odottavat tehtavat ajetaan ilman rivia
# vapaina yllapitotehtavina, jotta jono ei jumiudu muutoksen yhdistamiseen.
TAVOITE_ALKAEN = "2026-11-08"
TAVOITERIVI_RE = re.compile(r"^Tavoite:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
LUKU_MAX = 20000
HYLKAYSLOKI = "tavoitehylkaykset.jsonl"
PAIVAYS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
PR_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")


def _ascii(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def normalisoi(arvo):
    """Rivin arvo -> yksi TAVOITTEET-arvoista tai None."""
    a = _ascii(arvo).rstrip(".")
    if a in ("portfolio", "to-paikka", "to-paikka/portfolio", "to paikka/portfolio"):
        return PORTFOLIO
    return a if a in (ANSAINTA, AIVO, YLLAPITO) else None


def tavoite_tekstista(teksti):
    """Ensimmainen `Tavoite:`-rivi, jonka arvo on sallittu. None = puuttuu tai ei kelpaa."""
    m = TAVOITERIVI_RE.search((teksti or "")[:LUKU_MAX])
    return normalisoi(m.group(1)) if m else None


def tehtavan_tavoite(polku):
    try:
        with open(polku, "rb") as f:
            return tavoite_tekstista(f.read(LUKU_MAX).decode("utf-8", "replace"))
    except OSError:
        return None


def pakollinen(nimi):
    """Tavoiterivi vaaditaan tehtavalta, jonka nimen paivays on TAVOITE_ALKAEN tai myohempi (tuntematon paivays -> vaaditaan)."""
    m = PAIVAYS_RE.match(nimi)
    return not m or m.group(1) >= TAVOITE_ALKAEN


def luokittele(polku):
    """(tavoite, hylkaysperuste). Hylatty: tavoite None ja peruste tekstina. Vanha tehtava ilman rivia -> (yllapito, '')."""
    t = tehtavan_tavoite(polku)
    if t:
        return t, ""
    if pakollinen(Path(polku).name):
        return None, "Tavoite-rivi puuttuu tai ei kelpaa (sallitut: portfolio, ansainta, Aivo, yllapito)"
    return YLLAPITO, ""


def kaistan_jarjestys(kaista, kaistoja, aivo_odottaa):
    """Tavoitteet etusijajarjestyksessa kaistalle; tavoite, jota listassa ei ole, jaa tekematta talla kaistalla."""
    if kaistoja >= 3:
        if kaista == 3:
            return (AIVO,) if aivo_odottaa else (YLLAPITO,)
        if kaista == 2:
            return (ANSAINTA, YLLAPITO, PORTFOLIO)
        return (PORTFOLIO, YLLAPITO, ANSAINTA)
    # Alle 3 kaistaa: ei Aivo-rajausta. Kaista 2 suosii ansaintaa, muut portfoliota.
    muut = (ANSAINTA, YLLAPITO, PORTFOLIO, AIVO) if kaista == 2 else (PORTFOLIO, YLLAPITO, ANSAINTA, AIVO)
    return muut


def valitse(odottavat, kaista, kaistoja, ohitettu=None):
    """odottavat = [(polku, kiinnitetty_kaista)] vanhin ensin. Palauttaa jarjestetyn listan polkuja, jotka kaista saa ottaa:
    ensin kaistalle kiinnitetyt (`Kaista: N` ylittaa osuudet, ja on Tonyn nimenomainen paatos), sitten tavoiteosuuden mukaan.
    `ohitettu(polku, peruste)` kutsutaan tehtavalle, jota ei aloiteta (puuttuva tavoite)."""
    luokat = []
    for p, pin in odottavat:
        tav, peruste = luokittele(p)
        if tav is None:
            if ohitettu:
                ohitettu(p, peruste)
            continue
        luokat.append((p, pin, tav))
    aivo_odottaa = any(t == AIVO for _, _, t in luokat)
    jarj = kaistan_jarjestys(kaista, kaistoja, aivo_odottaa)
    ulos = [p for p, pin, _ in luokat if pin == kaista]
    for tav in jarj:
        ulos += [p for p, pin, t in luokat if pin is None and t == tav]
    return ulos


def kirjaa_hylkays(tila, nimi, peruste, nyt=None):
    """Yksi rivi per tehtava ja peruste (ei toistoa jokaisella ajolla). Virhe ei kaada ajoa."""
    try:
        p = Path(tila) / HYLKAYSLOKI
        if p.exists() and any(json.loads(r).get("tehtava") == nimi for r in p.read_text(encoding="utf-8").splitlines() if r.strip()):
            return False
        nyt = nyt or datetime.now().astimezone()
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"aika": nyt.isoformat(timespec="seconds"), "tehtava": nimi, "peruste": peruste}, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError):
        return False


# ---- jalkimittari -------------------------------------------------------------------------------------------------

def jalkimittari(repo, yhdistetyt_pr=None, alku=None):
    """Tavoitteittain: montako tehtavaa, joista montako johti PR:aan (tuloksessa PR-linkki) ja yhdistettyyn PR:aan.
    yhdistetyt_pr = joukko muotoa 'omistaja/repo#numero' (GitHubista); None = tietoa ei ole, jolloin yhdistettyja ei lasketa vaan ne merkitaan tuntemattomiksi.
    alku = date; vain taman paivan jalkeen tehdyt (tehtavan nimen paivays)."""
    teht = Path(repo) / "claude-jono" / "tehtavat"
    tul = Path(repo) / "claude-jono" / "tulokset"
    rivit = {t: Counter() for t in TAVOITTEET}
    rivit["ei_tavoitetta"] = Counter()
    if not teht.is_dir():
        return rivit
    for p in sorted(teht.glob("*.md")):
        m = PAIVAYS_RE.match(p.name)
        if alku and m and m.group(1) < alku.isoformat():
            continue
        r = tul / p.name
        if not r.exists():
            continue
        tav = tehtavan_tavoite(p) or "ei_tavoitetta"
        try:
            teksti = r.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        c = rivit[tav]
        c["tehtavia"] += 1
        numerot = {f"{r}#{n}" for r, n in PR_RE.findall(teksti)}
        if numerot:
            c["pr"] += 1
            if yhdistetyt_pr is not None and numerot & set(yhdistetyt_pr):
                c["yhdistetty"] += 1
    return rivit


def jalkimittari_teksti(rivit, yhdistetyt_tiedossa):
    ulos = ["## J\u00e4lkimittari (tavoitteittain)", "",
            "| Tavoite | Teht\u00e4vi\u00e4 | Johti PR:\u00e4\u00e4n | Yhdistetty |", "|---|---|---|---|"]
    for avain in (*TAVOITTEET, "ei_tavoitetta"):
        c = rivit.get(avain, Counter())
        if not c and avain == "ei_tavoitetta":
            continue
        yh = str(c["yhdistetty"]) if yhdistetyt_tiedossa else "ei tietoa"
        ulos.append(f"| {NAYTTONIMI.get(avain, 'ei tavoitetta (vanha)')} | {c['tehtavia']} | {c['pr']} | {yh} |")
    if not yhdistetyt_tiedossa:
        ulos += ["", "Yhdistettyjen PR:ien tilaa ei saatu (GitHub-haku ei onnistunut tai ei tehty); PR-sarake on silti tulosten linkeist\u00e4."]
    return "\n".join(ulos) + "\n"


def yhdistetyt_haku(rivit_pr, hae=None):
    """rivit_pr = joukko 'omistaja/repo#nro'. Palauttaa yhdistettyjen joukon tai None, jos haku ei onnistunut kokonaan
    (julkinen GitHub-API ilman tunnuksia; virheesta vain tyyppi). hae(osoite) -> dict on testattava korvike."""
    import urllib.request

    def oletus(osoite):
        with urllib.request.urlopen(urllib.request.Request(osoite, headers={"Accept": "application/vnd.github+json"}), timeout=10) as r:
            return json.load(r)
    hae = hae or oletus
    ulos = set()
    for avain in sorted(rivit_pr):
        repo, _, nro = avain.partition("#")
        try:
            if hae(f"https://api.github.com/repos/{repo}/pulls/{nro}").get("merged_at"):
                ulos.add(avain)
        except Exception:
            return None
    return ulos


def pr_avaimet(repo, alku=None):
    """Kaikki tulosten PR-avaimet (jalkimittarin GitHub-hakua varten)."""
    tul = Path(repo) / "claude-jono" / "tulokset"
    ulos = set()
    for p in tul.glob("*.md") if tul.is_dir() else []:
        m = PAIVAYS_RE.match(p.name)
        if alku and m and m.group(1) < alku.isoformat():
            continue
        try:
            ulos |= {f"{r}#{n}" for r, n in PR_RE.findall(p.read_text(encoding="utf-8", errors="replace"))}
        except OSError:
            pass
    return ulos
