"""Valmis-portti (A515-A516): tila VALMIS vaatii riippumattoman tarkastajan merkinn\u00e4n.

Merkint\u00e4 on tiedosto tulokset/<tulos>.tarkastus.md (esim. tulokset/2026-11-05-17.md -> tulokset/2026-11-05-17.tarkastus.md):
  - Tarkastaja: <malli tai istunto>     (eri kuin tuloksen "- Malli:"-rivi; sama arvo ei ole riippumaton)
  - Toimii k\u00e4yt\u00f6ss\u00e4: kyll\u00e4 | ei
  - Todiste: <lokirivi, tuloste tai URL>  (tyhj\u00e4 tai pelkk\u00e4 viiva ei kelpaa)
VALMIS ilman kelvollista merkint\u00e4\u00e4 n\u00e4ytet\u00e4\u00e4n tilassa KESKEN. Merkint\u00e4 "Toimii k\u00e4yt\u00f6ss\u00e4: ei" VALMIS-tulokselle
on virhe, jonka virhemittari laskee. Vain stdlib, vain luku."""
import re
from pathlib import Path

PAATE = ".tarkastus.md"
TARKASTAJA_RE = re.compile(r"^[ \t>*_-]*Tarkastaja[ \t]*:(?:\*\*|__)?[ \t]*(\S.*?)[ \t]*$", re.I | re.M)
TOIMII_RE = re.compile(r"^[ \t>*_-]*Toimii k(?:\u00e4|a)yt(?:\u00f6|o)ss(?:\u00e4|a)[ \t]*:(?:\*\*|__)?[ \t]*(\S.*?)[ \t]*$", re.I | re.M)
TODISTE_RE = re.compile(r"^[ \t>*_-]*Todiste[ \t]*:(?:\*\*|__)?[ \t]*(\S.*?)[ \t]*$", re.I | re.M)
MALLI_RE = re.compile(r"^- Malli:[ \t]*(\S.*?)[ \t]*$", re.M)
TYHJA_RE = re.compile(r"^[\s\-\u2013\u2014.:*_]*(?:tbd|todo|n/?a|ei mit(?:\u00e4|a)(?:\u00e4|a)n|ei ole|ei todistetta)?[\s\-\u2013\u2014.:*_]*$", re.I)
KYLLA_RE = re.compile(r"^(?:kyll(?:\u00e4|a)|on|ok)\b", re.I)
EI_RE = re.compile(r"^(?:ei|false)\b", re.I)


def tarkastuspolku(hakemisto, tulosnimi):
    """tulokset-hakemiston tarkastustiedosto tulosnimelle (esim. 'a.md' -> 'a.tarkastus.md')."""
    return Path(hakemisto) / (Path(tulosnimi).stem + PAATE)


def lue(tarkastusteksti):
    """J\u00e4sent\u00e4\u00e4 merkinn\u00e4n: {tarkastaja, toimii (True/False/None), todiste}. Puuttuva kentt\u00e4 on None."""
    teksti = tarkastusteksti or ""
    haku = lambda re_: (lambda m: m.group(1) if m else None)(re_.search(teksti))
    toimii = haku(TOIMII_RE)
    return {"tarkastaja": haku(TARKASTAJA_RE),
            "toimii": True if toimii and KYLLA_RE.match(toimii) else (False if toimii and EI_RE.match(toimii) else None),
            "todiste": haku(TODISTE_RE)}


def _todiste_ok(todiste):
    return bool(todiste) and not TYHJA_RE.match(todiste)


def _riippumaton(tulosteksti, tarkastaja):
    if not tarkastaja or TYHJA_RE.match(tarkastaja):
        return False
    malli = MALLI_RE.search(tulosteksti or "")
    return not (malli and malli.group(1).strip().lower() == tarkastaja.strip().lower())


def kelvollinen(tulosteksti, tarkastusteksti):
    """True, jos merkint\u00e4ss\u00e4 on riippumaton tarkastaja, 'toimii k\u00e4yt\u00f6ss\u00e4: kyll\u00e4' ja todisteviite."""
    if not tarkastusteksti:
        return False
    t = lue(tarkastusteksti)
    return t["toimii"] is True and _todiste_ok(t["todiste"]) and _riippumaton(tulosteksti, t["tarkastaja"])


def toimimaton(tarkastusteksti):
    """True, jos tarkastaja totesi tuloksen toimimattomaksi ('Toimii k\u00e4yt\u00f6ss\u00e4: ei') ja liitti todisteen."""
    t = lue(tarkastusteksti)
    return t["toimii"] is False and _todiste_ok(t["todiste"])


def lue_tarkastus(hakemisto, tulosnimi):
    try:
        return tarkastuspolku(hakemisto, tulosnimi).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def tehokas_tila(tila, tulosteksti, tarkastusteksti):
    """VALMIS ilman kelvollista tarkastusta -> KESKEN; muut tilat sellaisenaan."""
    if tila == "VALMIS" and not kelvollinen(tulosteksti, tarkastusteksti):
        return "KESKEN"
    return tila
