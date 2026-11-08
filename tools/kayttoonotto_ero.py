#!/usr/bin/env python3
"""Kertoo, paljonko kayttoonotettu kopio on jaljessa GitHubin oletushaaraa (yhdistetty ei ole sama kuin kaytossa).

Peruste: tulos 2026-11-05-34. PR #214 oli mainissa, mutta /opt/agentti-pohja ei ollut paivittynyt, joten
PR-avaajan virheen syy ei tullut lokiin.

Verkko: tyokalu tekee GitHubin julkiseen rajapintaan (api.github.com) tunnistautumattoman lukupyynnon (compare) ja
tarvittaessa toisen (repon olemassaolo, vain 404:n syyn erottamiseen). Muuta se ei tee: ei fetchia, ei pullia,
ei kirjoituksia, ei tunnuksia. Yksityinen repo nakyy tunnistautumattomalle 404:na.

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
# Vain GitHubin yhdistamisotsikot: "Merge pull request #N from ..." ja squash-yhdistamisen paate "Otsikko (#N)".
# Muut #-numerot otsikossa tai rungossa (esim. "viittaa #1") eivat ole PR-numeroita.
PR_RE = re.compile(r"^Merge pull request #(\d+) from |\s\(#(\d+)\)$")
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
            raise EroVirhe(selita_404(repo, commit, avaa))
        raise EroVirhe("GitHub HTTP %s" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise EroVirhe("GitHub-haku epaonnistui: " + type(e).__name__)


def repo_nakyy(repo, avaa=urllib.request.urlopen):
    """True/False: repo nakyy tunnistautumattomalle; None: ei voitu selvittaa."""
    pyynto = urllib.request.Request("%s/repos/%s/%s" % (API, OMISTAJA, repo),
                                    headers={"Accept": "application/vnd.github+json", "User-Agent": "kayttoonotto-ero"})
    try:
        with avaa(pyynto, timeout=AIKARAJA_S):
            return True
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def selita_404(repo, commit, avaa=urllib.request.urlopen):
    """Pyynto on tunnistautumaton, joten 404 voi tarkoittaa kolmea asiaa: erotellaan ne."""
    nakyy = repo_nakyy(repo, avaa=avaa)
    if nakyy is True:
        return "commit %s ei loydy GitHubista: kopiossa voi olla omia committeja tai haara on eri" % commit[:12]
    if nakyy is False:
        return ("GitHub vastasi 404 myos repolle %s/%s: repo on yksityinen (tunnistautumaton pyynto ei nae sita) "
                "tai --repo on vaarin" % (OMISTAJA, repo))
    return "GitHub vastasi 404, eika repon nakyvyytta saatu selville: syy voi olla commit, yksityinen repo tai vaara --repo"


def tiivista(vertailu):
    """Palauttaa (jaljessa, omia_committeja, PR-numerot vanhimmasta uusimpaan). PR:t vain vertailun palauttamista commiteista."""
    pr = []
    for c in vertailu.get("commits") or []:
        otsikko = ((c.get("commit") or {}).get("message") or "").split("\n", 1)[0]
        for m in PR_RE.finditer(otsikko):
            nro = int(m.group(1) or m.group(2))
            if nro not in pr:
                pr.append(nro)
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
