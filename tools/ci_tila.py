"""Lukee PR:n CI-tilan (check-runs) ilman gh:ta ja ilman tokenia (vain julkiset repot).

Kaytto: python3 tools/ci_tila.py <omistaja/repo> <pr-numero>
Tulostaa head-sha:n, jokaisen check-runin (nimi, tila, tulos) ja yhteenvedon.
Paluukoodit: 0 vihrea, 1 punainen, 2 kesken tai ei ajoja, 3 verkko- tai HTTP-virhe.
Luokittelu on sama kuin agentti/autonomia.py:n ci_tulos(); sita ei kopioida. Virheista tulostetaan vain tyyppi ja HTTP-tila."""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
if str(JUURI) not in sys.path:
    sys.path.insert(0, str(JUURI))

from agentti.autonomia import ci_tulos  # noqa: E402

API = "https://api.github.com/repos/%s/"
REPO_RE = re.compile(r"^(?!\.)[A-Za-z0-9_.-]+/(?!\.)[A-Za-z0-9_.-]+$")
KOODIT = {"vihrea": 0, "punainen": 1, "kesken": 2, "ei ajoja": 2}


class HakuVirhe(Exception):
    """Verkko- tai HTTP-virhe; viesti sisaltaa vain tyypin ja HTTP-tilan."""


def hae(url):
    pyynto = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "agenttifirma-ci-tila"})
    try:
        with urllib.request.urlopen(pyynto, timeout=20) as v:
            return json.loads(v.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HakuVirhe("HTTPError %d" % e.code) from None
    except Exception as e:
        raise HakuVirhe(type(e).__name__) from None


def yhteenveto(ajot):
    """'vihrea', 'punainen', 'kesken' tai 'ei ajoja' (luokittelu autonomia.ci_tulos:sta)."""
    if not ajot:
        return "ei ajoja"
    tulos = ci_tulos({"check_runs": ajot})
    return {"ok": "vihrea", "virhe": "punainen"}.get(tulos, "kesken")


def ci_tila(repo, numero, hae=hae):
    """(sha, check-runit, yhteenveto). Nostaa HakuVirhe jos haku epaonnistuu."""
    pr = hae(API % repo + "pulls/%d" % numero)
    sha = ((pr or {}).get("head") or {}).get("sha")
    if not sha:
        raise HakuVirhe("PuuttuvaSha")
    ajot = (hae(API % repo + "commits/%s/check-runs?per_page=100" % sha) or {}).get("check_runs") or []
    return sha, ajot, yhteenveto(ajot)


def main(argv=None, hae=hae, ulos=print):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or not REPO_RE.match(argv[0]) or not argv[1].isdigit():
        ulos("Kaytto: python3 tools/ci_tila.py <omistaja/repo> <pr-numero>")
        return 3
    try:
        sha, ajot, tulos = ci_tila(argv[0], int(argv[1]), hae)
    except HakuVirhe as e:
        ulos("Virhe: %s" % e)
        return 3
    ulos("PR %s#%s head %s" % (argv[0], argv[1], sha))
    for a in ajot:
        ulos("  %s: %s / %s" % (a.get("name"), a.get("status"), a.get("conclusion") or "-"))
    ulos("Yhteenveto: %s" % tulos)
    return KOODIT[tulos]


if __name__ == "__main__":
    sys.exit(main())
