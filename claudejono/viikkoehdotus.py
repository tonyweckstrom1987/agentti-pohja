"""Viikkosuunnittelun pohja sunnuntaiksi: pisteytetty ehdotuslista (hyoty x varmuus / vaiva) tiedostoon <vvvv-vv>-ehdotus.md.

Pisteytys on Tonyn 2.11.2026 paattama: hyoty tavoitteelle 1-5, varmuus 1-5, vaiva tunteina. Tama moduuli EI keksi pisteita:
ehdokkaat tulevat (a) jonon tehtavista, joilla ei ole tulosta, ja joiden `Pisteet:`-rivi on luettavissa, (b) viimeisen 7 vrk:n
epaonnistuneista tai hylatyista tehtavista (uusintayritys), (c) valinnaisesta tiedostosta claude-jono/ehdokkaat.md
(rivi: `- nimi | hyoty | varmuus | vaiva h | tavoite`). Valitavoitteet.md:n Q4-tavoitteet, joille ei ole yhtaan pisteytettya ehdokasta,
listataan erikseen "pisteytettavina aukkoina" (ei pisteita). Pisteet ovat ehdotuksia: Tony ja Claude tarkistavat ne suunnittelussa.

Kaytto: python3 -m claudejono.viikkoehdotus [--laheta] [--nyt ISO-AIKA] [--repo POLKU] [--hakemisto POLKU]
Oletushakemisto ei ole docs-kloonin sisalla (ajuri ei saa committaa sinne; untracked-tiedosto voisi myos estaa git pullin)."""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import ajuri
from .aamukooste import OLETUS_REPO, TZ, lue_tulokset

OLETUS_HAKEMISTO = "/var/lib/claude-jono/viikko"
MAX_EHDOTUKSIA = 20
MIN_EHDOTUKSIA = 10
UUSINTA_PAIVAT = 7

PISTEET_RIVI_RE = re.compile(r"(?im)^[\s>*#_-]*pisteet\b[*_]*\s*:(.*)$")
PISTEET_RE = re.compile(r"hy[o\u00f6]ty\s*(\d(?:[.,]\d)?).*?varmuus\s*(\d(?:[.,]\d)?).*?vaiva\s*(\d+(?:[.,]\d+)?)", re.I)
EHDOKAS_RIVI_RE = re.compile(r"^\s*-\s*(.+?)\s*\|\s*(\d(?:[.,]\d)?)\s*\|\s*(\d(?:[.,]\d)?)\s*\|\s*(\d+(?:[.,]\d+)?)\s*h?\s*(?:\|\s*(.*?))?\s*$")
VALITAVOITE_RE = re.compile(r"(?i)v[a\u00e4]litavoite\s*(\d)")
TAVOITE_AVAIMET = (
    (5, r"kysyn|npm|marketplace|dataraportti|terveystark|mcp-doctor|ulkoinen|portfolio-n[a\u00e4]kyvyys"),
    (1, r"ty[o\u00f6]paik|hakemus|linkedin|portfolio|iceye|yhteydenot"),
    (2, r"pseudonym|pilotti|hiekkalaatikko"),
    (3, r"aamu|muistutus|h[a\u00e4]lytys|kulu|varmuuskopi|kooste|automerge|ajastin|valvonta"),
    (4, r"oppitunti|haastattelu|palaute|opiskelu|opetus"),
)


def _luku(s):
    return float(s.replace(",", "."))


def pisteet(teksti):
    """(hyoty, varmuus, vaiva_h) tehtavatekstin Pisteet-rivilt\u00e4 tai None. Vaiva > 0, hyoty ja varmuus 1-5."""
    for m in PISTEET_RIVI_RE.finditer(teksti):
        p = PISTEET_RE.search(m.group(1))
        if p:
            h, v, e = _luku(p.group(1)), _luku(p.group(2)), _luku(p.group(3))
            if 1 <= h <= 5 and 1 <= v <= 5 and e > 0:
                return h, v, e
    return None


def pisteytys(h, v, e):
    return round(h * v / e, 2)


def tavoite(teksti):
    """Valitavoitteen numero: tehtavassa nimetty `valitavoite N` tai avainsanoista; None jos ei tunnistu."""
    m = VALITAVOITE_RE.search(teksti)
    if m and 1 <= int(m.group(1)) <= 5:
        return int(m.group(1))
    for nro, avain in TAVOITE_AVAIMET:
        if re.search(avain, teksti, re.I):
            return nro
    return None


def _otsikko(nimi, teksti):
    m = re.search(r"(?m)^#\s+(.+)$", teksti)
    if m:
        return " ".join(m.group(1).split())[:90]
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{2}-(auto-)?", "", nimi[:-3]).replace("-", " ")
    return slug[:90]


def ehdokkaat(repo, nyt):
    """Pisteytetyt ehdokkaat: lista dicteja {nimi, h, v, e, pisteet, tavoite, lahde}. Samannimiset yhdistetaan (ensimmainen voittaa)."""
    jono = Path(repo) / ajuri.JONO
    ulos, nahty = [], set()

    def lisaa(nimi, p, teksti, lahde):
        if p is None or nimi in nahty:
            return
        nahty.add(nimi)
        ulos.append({"nimi": nimi, "h": p[0], "v": p[1], "e": p[2], "pisteet": pisteytys(*p), "tavoite": tavoite(nimi + " " + teksti), "lahde": lahde})

    tehtavat, tulokset = jono / "tehtavat", jono / "tulokset"
    if tehtavat.is_dir():
        for p in sorted(tehtavat.iterdir()):
            if p.is_file() and ajuri.kelvollinen_nimi(p.name) and not (tulokset / p.name).exists():
                teksti = p.read_text(encoding="utf-8", errors="replace")
                lisaa(_otsikko(p.name, teksti), pisteet(teksti), teksti, "jonossa")
    for r in lue_tulokset(repo, nyt - timedelta(days=UUSINTA_PAIVAT), nyt):
        if r["tila"] in ("EPAONNISTUI", "HYLATTY"):
            try:
                teksti = (tehtavat / r["nimi"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lisaa("Uusinta: " + _otsikko(r["nimi"], teksti), pisteet(teksti), teksti, r["tila"].lower() + " " + r["aika"].astimezone(TZ).strftime("%-d.%-m."))
    try:
        for rivi in (jono / "ehdokkaat.md").read_text(encoding="utf-8", errors="replace").splitlines():
            m = EHDOKAS_RIVI_RE.match(rivi)
            if m:
                p = (_luku(m.group(2)), _luku(m.group(3)), _luku(m.group(4)))
                if 1 <= p[0] <= 5 and 1 <= p[1] <= 5 and p[2] > 0:
                    lisaa(m.group(1), p, (m.group(5) or "") + " " + m.group(1), "ehdokkaat.md")
    except OSError:
        pass
    ulos.sort(key=lambda x: (-x["pisteet"], -x["h"], x["nimi"]))
    return ulos


def painopiste(repo, viikko_nro):
    """Kompassin `Viikko NN (...): SANA` -rivin painopiste tai ''."""
    try:
        teksti = (Path(repo) / ajuri.JONO / "kompassi.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r"(?m)^\s*-\s*Viikko\s+%d\b[^:]*:\s*([^(\n]+)" % viikko_nro, teksti)
    return m.group(1).strip() if m else ""


def valitavoitteet(repo):
    """Q4-tavoitteet {nro: teksti} valitavoitteet.md:sta (rivit `- Tavoite N: ...`)."""
    try:
        teksti = (Path(repo) / ajuri.JONO / "valitavoitteet.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {int(m.group(1)): " ".join(m.group(2).split()) for m in re.finditer(r"(?m)^-\s*Tavoite\s+(\d)\s*:\s*(.+)$", teksti)}


def viikon_nimi(nyt):
    """Suunniteltava viikko: huomisen ISO-viikko (sunnuntaina ajettaessa tuleva maanantai alkaa uuden viikon)."""
    y, w, _ = (nyt.astimezone(TZ).date() + timedelta(days=1)).isocalendar()
    return y, w, f"{y}-{w:02d}"


def rakenna(repo, nyt):
    """(viikon_nimi, markdown, ehdokaslista)."""
    y, w, nimi = viikon_nimi(nyt)
    lista = ehdokkaat(repo, nyt)[:MAX_EHDOTUKSIA]
    paino = painopiste(repo, w)
    tavoitteet = valitavoitteet(repo)
    katetut = {e["tavoite"] for e in lista}
    aukot = {n: t for n, t in tavoitteet.items() if n not in katetut}
    rivit = [f"# Viikkoehdotus {nimi} (suunnittelun pohja)", "",
             f"Koottu automaattisesti {nyt.astimezone(TZ).strftime('%-d.%-m.%Y klo %H.%M')}. "
             "Pisteytys: hy\u00f6ty \u00d7 varmuus / vaiva (h). Pisteet ovat ehdotuksia, jotka Tony ja Claude tarkistavat suunnittelussa (noin 20 min).", ""]
    rivit.append(f"Viikon painopiste (kompassi): {paino}." if paino else "Viikon painopistett\u00e4 ei l\u00f6ydy kompassista: Tony asettaa sen suunnittelussa.")
    rivit += ["", "## Ehdotukset pistej\u00e4rjestyksess\u00e4", ""]
    if lista:
        rivit += ["| # | Pisteet | Ehdotus | Hy\u00f6ty | Varmuus | Vaiva h | Tavoite | L\u00e4hde |", "|---|---|---|---|---|---|---|---|"]
        for i, e in enumerate(lista, 1):
            merkki = " [painopiste]" if paino and paino.lower()[:6] in (e["nimi"] + " ").lower() else ""
            rivit.append(f"| {i} | {e['pisteet']:.2f} | {e['nimi'].replace('|', '/')}{merkki} | {e['h']:g} | {e['v']:g} | {e['e']:g} | "
                         f"{e['tavoite'] or '?'} | {e['lahde']} |")
    else:
        rivit.append("Pisteytettyj\u00e4 ehdokkaita ei l\u00f6ytynyt.")
    if len(lista) < MIN_EHDOTUKSIA:
        rivit += ["", f"Huomio: pisteytettyj\u00e4 ehdotuksia on vain {len(lista)} (tavoite {MIN_EHDOTUKSIA}\u2013{MAX_EHDOTUKSIA}). "
                      "Lis\u00e4\u00e4 ehdokkaita tiedostoon claude-jono/ehdokkaat.md (rivi: `- nimi | hy\u00f6ty | varmuus | vaiva h | tavoite`) "
                      "tai anna jonoteht\u00e4ville `Pisteet:`-rivi. Pisteit\u00e4 ei keksit\u00e4 puuttuviin."]
    if aukot:
        rivit += ["", "## Pisteytett\u00e4v\u00e4t aukot (v\u00e4litavoite ilman pisteytetty\u00e4 ehdotusta)", ""]
        rivit += [f"- Tavoite {n}: {t[:200]}" for n, t in sorted(aukot.items())]
    rivit += ["", "Pisteytetyn listan j\u00e4lkeen: valitaan ensin suurin pistem\u00e4\u00e4r\u00e4, mutta v\u00e4litavoitteen alle kuulumaton ty\u00f6 j\u00e4\u00e4 pois (kompassi: ei uusia suuntia ilman, ett\u00e4 vanha tuottaa)."]
    return nimi, "\n".join(rivit) + "\n", lista


def main(argv=None):
    p = argparse.ArgumentParser(description="Viikkosuunnittelun ehdotuspohja")
    p.add_argument("--laheta", action="store_true", help="vanha valitsin; ei laheta enaa Telegramiin (A427)")
    p.add_argument("--nyt")
    p.add_argument("--repo", default=os.environ.get("CLAUDEJONO_REPO", OLETUS_REPO))
    p.add_argument("--hakemisto", default=os.environ.get("CLAUDEJONO_VIIKKO_HAK", OLETUS_HAKEMISTO))
    a = p.parse_args(argv)
    nyt = datetime.fromisoformat(a.nyt) if a.nyt else datetime.now(TZ)
    if nyt.tzinfo is None:
        nyt = nyt.replace(tzinfo=TZ)
    nimi, teksti, lista = rakenna(a.repo, nyt)
    hak = Path(a.hakemisto)
    hak.mkdir(parents=True, exist_ok=True)
    polku = hak / f"{nimi}-ehdotus.md"
    polku.write_text(teksti, encoding="utf-8")
    print(f"kirjoitettu {polku} ({len(lista)} ehdotusta)")
    # A427: viikkoehdotus ei ole halytys; tiedosto kirjoitetaan hakemistoon ja aamukooste/docs-repo nayttaa sen. --laheta jaa vanhojen kutsujen vuoksi sallituksi mutta ei laheta.
    return 0


if __name__ == "__main__":
    sys.exit(main())
