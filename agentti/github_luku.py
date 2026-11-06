"""GitHubin vain luku -tunnus julkisten repojen hauille (A744).

Tunnus on fine-grained PAT (Public repositories, read-only), jonka Tony tallentaa vain isannalle
tiedostoon (oletus /etc/agentscope/github-luku.token, 600). Tunnusta ei lueta ymparistomuuttujista,
ei kirjata lokiin eika tulosteta. Moduuli tekee vain GET-pyyntoja eika sisalla kirjoitusoperaatioita.
Jos tiedostoa ei ole tai se on viallinen, haut jatkuvat ilman tunnusta (60 hakua/h)."""
import os
import re
import urllib.parse
import urllib.request

OLETUSPOLKU = "/etc/agentscope/github-luku.token"
POLKU_ENV = "GITHUB_LUKU_TUNNUS_POLKU"   # vain polku, ei tunnus
SALLITTU_ISANTA = "api.github.com"
TUNNUS_RE = re.compile(r"^[A-Za-z0-9_\-]{20,255}$")
MAX_KOKO = 1024


def lue_tunnus(polku=None):
    """Palauttaa tunnuksen tai None (puuttuu, ei luettavissa, ylisuuri tai muoto outo). Ei nosta poikkeusta."""
    polku = polku or os.environ.get(POLKU_ENV) or OLETUSPOLKU
    try:
        with open(polku, "rb") as f:
            raaka = f.read(MAX_KOKO + 1)
        if len(raaka) > MAX_KOKO:
            return None
        teksti = raaka.decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return teksti if TUNNUS_RE.match(teksti) else None


def _sallittu_osoite(url):
    osat = urllib.parse.urlsplit(url)
    return osat.scheme == "https" and osat.hostname == SALLITTU_ISANTA


def pyynto(url, user_agent, accept="application/vnd.github+json", polku=None):
    """GET-pyynto GitHubin rajapintaan. Authorization-otsake lisataan vain https://api.github.com-osoitteisiin."""
    otsakkeet = {"Accept": accept, "User-Agent": user_agent}
    if _sallittu_osoite(url):
        tunnus = lue_tunnus(polku)
        if tunnus:
            otsakkeet["Authorization"] = "Bearer " + tunnus
    return urllib.request.Request(url, headers=otsakkeet, method="GET")


def rate_limit(user_agent="agenttifirma-github-luku", avaa=urllib.request.urlopen, polku=None):
    """Palauttaa (limit, remaining) osoitteesta /rate_limit tai None. /rate_limit ei kuluta kiintiota.
    Todennus: limit 5000 tarkoittaa, etta tunnus on kaytossa (ilman tunnusta 60)."""
    import json
    try:
        with avaa(pyynto("https://api.github.com/rate_limit", user_agent, polku=polku), timeout=15) as v:
            core = json.loads(v.read().decode("utf-8"))["resources"]["core"]
        return int(core["limit"]), int(core["remaining"])
    except Exception:
        return None


def main():
    tulos = rate_limit()
    print("tunnus: " + ("luettu" if lue_tunnus() else "ei kaytossa"))
    print("rate_limit: " + ("haku epaonnistui" if tulos is None else "limit %d, jaljella %d" % tulos))
    return 0 if tulos else 1


if __name__ == "__main__":
    raise SystemExit(main())
