"""Nykytila-rivi (virheloki 5.-6.11.2026: varmuuskopio "puuttui", vaikka toimi; juurisyy "lahteesta, ei muistista").

Saanto siirretty ohjeesta koodiin:
  1. Tehtava, joka luo tai korvaa infran osan (palvelu, ajastus, varmuuskopio, valvonta, yhteys), vaatii rivin
     `Nykytila tarkistettu: <lahde> -> <mita loytyi>`. Ajuri hylkaa tehtavan ilman rivia (tila HYLATTY) ennen mallin kutsua.
  2. agentti-pohjan PR, joka lisaa tiedostoja hakemistoon isanta/ tai yojono/-ajastuksia, vaatii saman rivin PR-kuvaukseen.
     Riippumaton tarkastaja hylkaa ilman rivia ilman mallin kutsua.

Tama moduuli ei muuta katto-, budjetti- eika oikeusarvoja. Se vain kiristaa sita, mika paasee ajoon.
Ei-ASCII-merkit kirjoitetaan \\u-koodeilla (kooditiedoston ASCII-saanto).
"""
import re

# Tehtavat, joiden nimen paivays on tama tai myohempi, tarkistetaan (aiemmat jonossa olevat tehtavat eivat hylkaannu jalkikateen).
ALKAEN = "2026-11-07"

_A = "[a\u00e4]"
_O = "[o\u00f6]"
# Infra-avainsanat (Tonyn tehtava 6.11.2026): taivutukset sallitaan perustaipumisella (palvelun, ajastimen, kontin).
INFRA_RE = re.compile(
    r"\bvarmuuskopio\w*|\bcron\w*|\bajastin\w*|\bajastu\w*|\bpalvelu\w*|\bpalveli\w*|\bvalvonta\w*|\bvalvonna\w*|\brclone\b"
    r"|\bsystemd\b|\bkontti\w*|\bkontei\w*|\byhteys\b|\byhteyde\w*|\bsnapshot\w*", re.IGNORECASE)
# Luonti- tai korvaustoiminto samassa lauseessa: pelkka maininta (esim. "lue palvelun loki") ei vaadi rivia.
TOIMINTO_RE = re.compile(
    r"\b(?:luo|luoda|luodaan|lis" + _A + r"|lis" + _A + _A + r"|asenna\w*|ota k" + _A + r"ytt" + _O + _O + r"n|korvaa\w*|korvata"
    r"|uusi|uuden|uusia|rakenna\w*|perusta\w*|pystyt" + _A + r"\w*|toteuta\w*|vaihda\w*"
    r"|siirr" + _A + r"\w*|kytke\w*|konfiguroi\w*|m" + _A + _A + r"rit" + _A + r"\w*)\b", re.IGNORECASE)
KIELTO_RE = re.compile(r"\b(?:ei|eik" + _A + r"|" + _A + r"l" + _A + r"|ilman|kielletty|vain luku)\b", re.IGNORECASE)

RIVI_RE = re.compile(r"^[ \t>*_-]*Nykytila tarkistettu[ \t]*:(?:\*\*|__)?[ \t]*(\S.*)$", re.IGNORECASE | re.MULTILINE)
EROTIN_RE = re.compile(r"\s*(?:\u2192|->|=>)\s*")
TYHJA_RE = re.compile(r"^[\s\-\u2013\u2014.:*_<>]*(?:tbd|todo|n/?a|ei mit" + _A + _A + r"n|ei ole|l" + _A + r"hde|mit" + _A + r" l" + _O + r"ytyi)?[\s\-\u2013\u2014.:*_<>]*$",
                      re.IGNORECASE)
PAIVAYS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
KOODILOHKO_RE = re.compile(r"```.*?```", re.S)
SYY = "puuttuu rivi `Nykytila tarkistettu: <l\u00e4hde> \u2192 <mit\u00e4 l\u00f6ytyi>` (l\u00e4hde: 01-infra.md, palvelimen komento tai tiedosto)"


def on_nykytila(teksti):
    """True, jos tekstissa on rivi `Nykytila tarkistettu: <lahde> -> <mita loytyi>`, jossa molemmat puolet ovat sisaltoa
    (ei tyhja, viiva eika paikkamerkki). Rivi koodilohkon sisalla ei lasketa (se olisi esimerkki, ei tarkistus)."""
    teksti = KOODILOHKO_RE.sub("", teksti or "")
    for m in RIVI_RE.finditer(teksti):
        osat = EROTIN_RE.split(m.group(1).strip(), maxsplit=1)
        if len(osat) == 2 and not TYHJA_RE.match(osat[0]) and not TYHJA_RE.match(osat[1]):
            return True
    return False


def vaatii_nykytilan(teksti):
    """True, jos jokin lause luo tai korvaa infran osan: infra-avainsana ja luonti-/korvaustoiminto samassa lauseessa.
    Kieltolauseet ("ei saa asentaa palvelua") ja nykytilarivi itse eivat laukaise. Koodilohkot ohitetaan."""
    teksti = RIVI_RE.sub("", KOODILOHKO_RE.sub("", teksti or ""))
    for rivi in teksti.splitlines():
        for lause in re.split(r"(?<=[.!?;])\s+", rivi):
            if INFRA_RE.search(lause) and TOIMINTO_RE.search(lause) and not KIELTO_RE.search(lause):
                return True
    return False


def tehtavan_virhe(nimi, teksti, alkaen=ALKAEN):
    """None (ok) tai hylkaysperuste (str). Tarkistus koskee vain tehtavia, joiden nimen paivays >= alkaen."""
    m = PAIVAYS_RE.match(nimi or "")
    if not m or m.group(1) < alkaen:
        return None
    if vaatii_nykytilan(teksti) and not on_nykytila(teksti):
        return SYY
    return None


# --- PR-tarkistus (agentti-pohja) ---
PR_REPO = "agentti-pohja"
YOJONO_AJASTUS_RE = re.compile(r"^yojono/(?:systemd/|.*\.(?:timer|service)(?:\.mall)?$)")


def pr_vaatii_nykytilan(repo, tiedostot):
    """True, jos agentti-pohjan PR lisaa tiedoston hakemistoon isanta/ tai yojono/-ajastuksen (systemd-malli, .timer, .service).
    tiedostot: GitHubin /pulls/N/files -lista (filename, status)."""
    if repo != PR_REPO:
        return False
    for t in tiedostot or []:
        if not isinstance(t, dict) or t.get("status") != "added":
            continue
        nimi = str(t.get("filename") or "")
        if nimi.startswith("isanta/") or YOJONO_AJASTUS_RE.match(nimi):
            return True
    return False


def pr_virhe(repo, tiedostot, kuvaus):
    """None (ok) tai hylkaysperuste (str) PR:lle."""
    if pr_vaatii_nykytilan(repo, tiedostot) and not on_nykytila(kuvaus):
        return "PR lis\u00e4\u00e4 tiedostoja hakemistoon isanta/ tai yojono/-ajastuksia, mutta kuvauksesta " + SYY[len("puuttuu "):] + " puuttuu"
    return None
