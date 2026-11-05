#!/usr/bin/env python3
"""Kertoo, paljonko kayttoonotettu kopio on jaljessa GitHubin oletushaaraa (yhdistetty ei ole sama kuin kaytossa).

Peruste: tulos 2026-11-05-34. PR #214 oli mainissa, mutta /opt/agentti-pohja ei ollut paivittynyt, joten
PR-avaajan virheen syy ei tullut lokiin. Tyokalu vain lukee: ei fetchia, ei pullia, ei kirjoituksia.

Kaytto: tools/kayttoonotto_ero.py [--hakemisto /opt/agentti-pohja] [--repo agentti-pohja] [--haara main]
Poistumiskoodi: 0 ajan tasalla, 1 jaljessa, 2 tilaa ei saatu selville.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
OMISTAJA = "tonyweckstrom1987"
PR_RE = re.compile(r"#(\d+)")
AIKARAJA_S = 20


class EroVirhe(Exception):
    pass


def paikallinen_commit(hakemisto, suorita=subprocess.run):
    try:
        r = suorita(["git", "-C", hakemisto, "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    except OSError as e:
        raise EroVirhe("gitia ei voitu ajaa: " + type(e).__name__)
    if r.returncode != 0:
        raise EroVirhe("%s ei ole luettava git-kopio (safe.directory?): %s" % (hakemisto, r.stderr.strip()[:200]))
    return r.stdout.strip()


def hae_vertailu(repo, commit, haara, avaa=urllib.request.urlopen):
    url = "%s/repos/%s/%s/compare/%s...%s" % (API, OMISTAJA, repo, commit, haara)
    pyynto = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "kayttoonotto-ero"})
    try:
        with avaa(pyynto, timeout=AIKARAJA_S) as v:
            return json.loads(v.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise EroVirhe("commit %s ei loydy GitHubista: kopiossa voi olla omia committeja" % commit[:12])
        raise EroVirhe("GitHub HTTP %s" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise EroVirhe("GitHub-haku epaonnistui: " + type(e).__name__)


def tiivista(vertailu):
    """Palauttaa (jaljessa, omia_committeja, PR-numerot vanhimmasta uusimpaan). PR:t vain vertailun palauttamista commiteista."""
    pr = []
    for c in vertailu.get("commits") or []:
        otsikko = ((c.get("commit") or {}).get("message") or "").split("\n", 1)[0]
        for nro in PR_RE.findall(otsikko):
            if int(nro) not in pr:
                pr.append(int(nro))
    return int(vertailu.get("ahead_by") or 0), int(vertailu.get("behind_by") or 0), pr


def raportti(hakemisto, repo, haara, commit, jaljessa, omia, pr):
    if jaljessa == 0 and omia == 0:
        return "%s: ajan tasalla %s:n kanssa (%s)" % (hakemisto, haara, commit[:7])
    osat = ["%s: %d committia jaljessa %s/%s:sta (kaytossa %s)" % (hakemisto, jaljessa, repo, haara, commit[:7])]
    if omia:
        osat.append("%d committia, joita GitHubissa ei ole" % omia)
    if pr:
        osat.append("PR:t: " + ", ".join("#%d" % n for n in pr))
    return "; ".join(osat)


def main(argv=None, avaa=urllib.request.urlopen, suorita=subprocess.run, tuloste=sys.stdout):
    p = argparse.ArgumentParser(description="Paljonko kayttoonotto on jaljessa GitHubia")
    p.add_argument("--hakemisto", default="/opt/agentti-pohja")
    p.add_argument("--repo", default="agentti-pohja")
    p.add_argument("--haara", default="main")
    a = p.parse_args(argv)
    try:
        commit = paikallinen_commit(a.hakemisto, suorita=suorita)
        jaljessa, omia, pr = tiivista(hae_vertailu(a.repo, commit, a.haara, avaa=avaa))
    except EroVirhe as e:
        print("%s: tila ei selvinnyt: %s" % (a.hakemisto, e), file=tuloste)
        return 2
    print(raportti(a.hakemisto, a.repo, a.haara, commit, jaljessa, omia, pr), file=tuloste)
    return 0 if jaljessa == 0 and omia == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
