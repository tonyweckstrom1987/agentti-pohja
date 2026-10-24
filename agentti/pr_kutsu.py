"""PR-kutsu: pushaa agentin `yo/`-haaran botti-tilin forkkiin ja avaa siita PR alkuperaiseen julkiseen repoon.

Miksi fork: botilla ei ole mitaan oikeuksia Tonyn repoihin. Se kirjoittaa vain omiin forkkeihinsa, ja PR tulee forkista,
joten automerge (vain saman repon haarat) ei koske siihen: Tony yhdistaa aina itse.

Turva ("koodi on laki"):
- repo vain REPOT-listasta (julkiset repot; uuden lisays vaatii koodimuutoksen), haara vain `yo/<nimi>`
- tunnus luetaan tiedostosta (oletus /etc/yojono/github-bot.token), ei koskaan argumenttina, URL:ssa tai tulosteessa;
  git saa sen vain tilapaiselta askpass-skriptilta (0700), joten tunnus ei nay prosessilistassa
- ei force-pushia (refspec ilman `+`), ei paikallisia koukkuja (core.hooksPath=/dev/null)
- ei suojattuja polkuja (.github/, tools/, systemd/, isanta/, rajat.py, config.py, salaisuuden nakoiset tiedostot)
- enintaan RAJA_PAIVASSA PR-yritysta paivassa; vain stdlib

Kaytto:
    python3 -m agentti.pr_kutsu tarkista REPO HAKEMISTO HAARA          # vain paikalliset tarkistukset, ei verkkoa
    python3 -m agentti.pr_kutsu pr REPO HAKEMISTO HAARA OTSIKKO KUVAUSTIEDOSTO
Ymparisto: YOJONO_BOT_KAYTTAJA (oletus agenttifirma-bot), YOJONO_BOT_TUNNUS, YOJONO_PR_TILA.
"""
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date

OMISTAJA = "tonyweckstrom1987"
OLETUS_BOTTI = "agenttifirma-bot"
REPOT = ("agentti-pohja", "mcp-model-router", "ai-pr-reviewer-action", "llm-stopcheck", "ideapankki")
HAARA_RE = re.compile(r"^yo/[a-z0-9][a-z0-9._-]{0,59}$")
BOTTI_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
TUNNUS_RE = re.compile(r"^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$")
OLETUS_TUNNUS = "/etc/yojono/github-bot.token"
OLETUS_TILA = "/var/lib/yojono/pr-tila.json"
RAJA_PAIVASSA = 100
MAX_TIEDOSTOJA = 40
OTSIKKO_MAX = 120
KUVAUS_MAX = 4000
SUOJATUT_ALKUOSAT = (".github/", "tools/", "systemd/", "isanta/")
SUOJATUT_NIMET = ("rajat.py", "config.py")
SALAINEN_RE = re.compile(r"(\.env|token|secret|api_?key|\.pem|id_ed25519|id_rsa)", re.IGNORECASE)
ALAKUULO = "\n\n---\nAgentin tekema ehdotus (yojono). Tony katselmoi ja yhdistaa; mitaan ei oteta kayttoon automaattisesti."


class PrVirhe(Exception):
    """Odotettu virhe: viesti on turvallinen tulostaa (ei sisalla tunnusta)."""


def suojattu(polku):
    """Onko tiedostopolku sellainen, jota agentti ei saa muuttaa PR:ssa."""
    p = posixpath.normpath(polku.strip()).lower()
    while p.startswith("./"):
        p = p[2:]
    nimi = p.rsplit("/", 1)[-1]
    osat = p.split("/")
    alut = tuple(a.rstrip("/") for a in SUOJATUT_ALKUOSAT)
    return (p.startswith("/") or ".." in osat or osat[0] in alut or ".github" in osat
            or nimi in SUOJATUT_NIMET or bool(SALAINEN_RE.search(nimi)))


def lue_tunnus(polku):
    """Lukee tunnuksen tiedostosta ja tarkistaa muodon. Virheilmoitus ei sisalla tunnusta."""
    try:
        with open(polku, encoding="ascii") as f:
            tunnus = f.read().strip()
    except (OSError, UnicodeDecodeError):
        raise PrVirhe("tunnustiedostoa ei voi lukea: " + polku)
    if not TUNNUS_RE.match(tunnus):
        raise PrVirhe("tunnustiedoston muoto ei kelpaa")
    return tunnus


def botti_kayttaja(env=None):
    nimi = (os.environ if env is None else env).get("YOJONO_BOT_KAYTTAJA", OLETUS_BOTTI)
    if not BOTTI_RE.match(nimi):
        raise PrVirhe("YOJONO_BOT_KAYTTAJA ei kelpaa")
    return nimi


def _git(hakemisto, *args, suorita=subprocess.run):
    r = suorita(["git", "-C", hakemisto, *args], capture_output=True, text=True, timeout=60, check=False)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


# Repot, joiden oletushaara ei ole main (kovakoodattu: ei API-hakua, ei arvausta). Havainto 24.10.: ai-pr-reviewer-action kayttaa
# haaraa master, ja kovakoodattu main kaatoi sen kaikki PR:t.
OLETUSHAARAT = {"ai-pr-reviewer-action": "master"}


def pohja_haara(repo):
    return OLETUSHAARAT.get(repo, "main")


def tarkista(repo, hakemisto, haara, pohja=None, suorita=subprocess.run):
    """Paikalliset tarkistukset ilman verkkoa. Palauttaa muutetut tiedostot tai nostaa PrVirhen."""
    if repo not in REPOT:
        raise PrVirhe("repo ei ole sallittujen listalla: " + repo)
    pohja = pohja or pohja_haara(repo)
    if not isinstance(haara, str) or not HAARA_RE.match(haara):
        raise PrVirhe("haaran pitaa olla muotoa yo/<nimi> (pienet kirjaimet, numerot, . _ -)")
    if not os.path.isdir(os.path.join(hakemisto, ".git")):
        raise PrVirhe("hakemisto ei ole git-repo")
    if _git(hakemisto, "rev-parse", "--verify", "refs/heads/" + haara, suorita=suorita)[0] != 0:
        raise PrVirhe("haaraa ei ole paikallisesti: " + haara)
    koodi, ulos, _ = _git(hakemisto, "diff", "--name-only", "--no-renames", f"{pohja}...{haara}", suorita=suorita)
    if koodi != 0:
        raise PrVirhe("erotuksen laskenta epaonnistui (onko pohjahaara %s olemassa?)" % pohja)
    tiedostot = [r for r in ulos.splitlines() if r.strip()]
    if not tiedostot:
        raise PrVirhe("haarassa ei ole muutoksia pohjaan nahden")
    if len(tiedostot) > MAX_TIEDOSTOJA:
        raise PrVirhe("liikaa tiedostoja (%d > %d)" % (len(tiedostot), MAX_TIEDOSTOJA))
    huonot = [t for t in tiedostot if suojattu(t)]
    if huonot:
        raise PrVirhe("suojattuja polkuja muutettu, tee kasin: " + ", ".join(huonot[:5]))
    return tiedostot


def paikkoja_jaljella(tilapolku=None, pvm=None):
    """Montako PR-yritysta on viela jaljella tanaan (vain luku). Ajuri tarkistaa taman ennen kalliin mallikutsun alkua."""
    pvm = pvm or date.today().isoformat()
    try:
        with open(tilapolku or os.environ.get("YOJONO_PR_TILA", OLETUS_TILA), encoding="utf-8") as f:
            tila = json.load(f)
    except (OSError, ValueError):
        return RAJA_PAIVASSA
    n = tila.get("n", 0) if tila.get("pvm") == pvm and isinstance(tila.get("n"), int) else 0
    return max(RAJA_PAIVASSA - n, 0)


def varaa_paikka(tilapolku, pvm=None):
    """Kirjaa PR-yrityksen paivalaskuriin. Palauttaa False, jos paivaraja on taynna."""
    pvm = pvm or date.today().isoformat()
    try:
        with open(tilapolku, encoding="utf-8") as f:
            tila = json.load(f)
    except (OSError, ValueError):
        tila = {}
    n = tila.get("n", 0) if tila.get("pvm") == pvm and isinstance(tila.get("n"), int) else 0
    if n >= RAJA_PAIVASSA:
        return False
    tmp = tilapolku + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pvm": pvm, "n": n + 1}, f)
    os.replace(tmp, tilapolku)
    return True


def pushaa(hakemisto, repo, haara, botti, tunnuspolku, suorita=subprocess.run, sync=True):
    """Pushaa haaran botin forkkiin (ei force). Tunnus kulkee vain tilapaisen askpass-skriptin kautta."""
    with tempfile.TemporaryDirectory() as tmp:
        skripti = os.path.join(tmp, "askpass.sh")
        with open(os.open(skripti, os.O_CREAT | os.O_WRONLY, 0o700), "w", encoding="ascii") as f:
            f.write("#!/bin/sh\ncase \"$1\" in\n  Username*) echo %s ;;\n  *) cat %s ;;\nesac\n"
                    % (shlex.quote(botti), shlex.quote(tunnuspolku)))
        ymp = {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_", "GIT_ASKPASS"))}
        ymp.update({"GIT_ASKPASS": skripti, "GIT_TERMINAL_PROMPT": "0"})
        kohde = f"https://github.com/{botti}/{repo}.git"
        refspec = f"refs/heads/{haara}:refs/heads/{haara}"
        r = suorita(["git", "-C", hakemisto, "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null",
                     "push", kohde, refspec], capture_output=True, text=True, timeout=180, check=False, env=ymp)
    if r.returncode != 0:
        loppu = ((r.stderr or "") + (r.stdout or "")).strip()[-300:]
        if "workflow" in loppu and "scope" in loppu:
            raise PrVirhe("fork on jaljessa ylavirrasta (ylavirran tyonkulkutiedostot muuttuneet; tokenilla ei ole eika pida olla "
                          "workflow-oikeutta). Synkkaa fork kasin botin tililla: github.com/%s/%s -> Sync fork -> Update branch. "
                          "API-synkronointi: %s. Push: %s" % (botti, repo, "ok" if sync is True else sync, loppu))
        raise PrVirhe("push epaonnistui (onko fork olemassa ja token voimassa?): " + loppu)


def paivita_fork(repo, botti, tunnus, avaa=urllib.request.urlopen):
    """Paivittaa botin forkin main-haaran alkuperaisen perusteella (GitHubin merge-upstream, vain fast-forward).

    Ilman tata uusi haara vie forkkiin myos ylavirran tyonkulkutiedostomuutokset, ja GitHub hylkaa pushin,
    koska tunnuksella ei ole `workflow`-oikeutta (eika sen kuulu olla). Virheet eivat kaada ketjua: jos paivitys
    ei onnistu, itse push nostaa selkean virheen.
    """
    pyynto = urllib.request.Request(
        f"https://api.github.com/repos/{botti}/{repo}/merge-upstream", data=json.dumps({"branch": pohja_haara(repo)}).encode(),
        method="POST", headers={"Authorization": "Bearer " + tunnus, "Accept": "application/vnd.github+json",
                                "Content-Type": "application/json", "User-Agent": "yojono-pr-kutsu"})
    try:
        with avaa(pyynto, timeout=30) as vastaus:
            vastaus.read()
        return True
    except urllib.error.HTTPError as e:
        return "HTTP %s" % e.code
    except (urllib.error.URLError, ValueError, OSError) as e:
        return type(e).__name__


def fork_jaljessa(repo, botti, avaa=urllib.request.urlopen):
    """Montako commitia botin fork on jaljessa ylavirrasta (julkinen compare-haku, ei tunnusta) tai None jos ei saada selville."""
    pohja = pohja_haara(repo)
    pyynto = urllib.request.Request(
        f"https://api.github.com/repos/{OMISTAJA}/{repo}/compare/{pohja}...{botti}:{pohja}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "yojono-pr-kutsu"})
    try:
        with avaa(pyynto, timeout=20) as vastaus:
            luku = json.loads(vastaus.read().decode("utf-8")).get("behind_by")
        return luku if isinstance(luku, int) and not isinstance(luku, bool) else None
    except (urllib.error.URLError, ValueError, OSError):
        return None


def fork_valmis(repo, env=None, avaa=urllib.request.urlopen):
    """Esitarkistus ennen mallikutsua: onko fork ajan tasalla tai saadaanko se synkattua. Palauttaa (ok, selitys).
    Epavarmuus (ei verkkoa, ei tunnusta) ei esta ajoa: silloin ok=True, ja itse push nostaa selkean virheen."""
    env = os.environ if env is None else env
    try:
        botti = botti_kayttaja(env)
        jalessa = fork_jaljessa(repo, botti, avaa=avaa)
        if not jalessa:
            return True, ""
        tunnus = lue_tunnus(env.get("YOJONO_BOT_TUNNUS", OLETUS_TUNNUS))
        paivita_fork(repo, botti, tunnus, avaa=avaa)
        if not fork_jaljessa(repo, botti, avaa=avaa):
            return True, ""
        return False, "fork %s/%s on %s commitia jaljessa eika API-synkka onnistu: Sync fork kasin" % (botti, repo, jalessa)
    except PrVirhe:
        return True, ""


def avaa_pr(repo, haara, botti, otsikko, kuvaus, tunnus, avaa=urllib.request.urlopen):
    """Avaa PR forkista alkuperaiseen repoon. Palauttaa PR:n osoitteen."""
    runko = {
        "title": "[yojono] " + "".join(c for c in str(otsikko) if c >= " " and c != "\x7f").strip()[:OTSIKKO_MAX],
        "head": f"{botti}:{haara}",
        "base": pohja_haara(repo),
        "body": str(kuvaus)[:KUVAUS_MAX] + ALAKUULO,
        "maintainer_can_modify": False,
    }
    pyynto = urllib.request.Request(
        f"https://api.github.com/repos/{OMISTAJA}/{repo}/pulls", data=json.dumps(runko).encode(), method="POST",
        headers={"Authorization": "Bearer " + tunnus, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "yojono-pr-kutsu"})
    try:
        with avaa(pyynto, timeout=30) as vastaus:
            osoite = json.loads(vastaus.read().decode("utf-8")).get("html_url")
    except urllib.error.HTTPError as e:
        raise PrVirhe("GitHub hylkasi PR:n (HTTP %s)" % e.code)
    except (urllib.error.URLError, ValueError, OSError) as e:
        raise PrVirhe("PR-pyynto epaonnistui: " + type(e).__name__)
    if not isinstance(osoite, str) or not osoite.startswith("https://github.com/"):
        raise PrVirhe("GitHubin vastauksesta puuttuu PR:n osoite")
    return osoite


def luo_pr(repo, hakemisto, haara, otsikko, kuvaus, env=None, suorita=subprocess.run, avaa=urllib.request.urlopen):
    """Koko ketju: tarkistukset, paivaraja, push, PR. Palauttaa PR:n osoitteen."""
    env = os.environ if env is None else env
    botti = botti_kayttaja(env)
    tarkista(repo, hakemisto, haara, suorita=suorita)
    tunnuspolku = env.get("YOJONO_BOT_TUNNUS", OLETUS_TUNNUS)
    tunnus = lue_tunnus(tunnuspolku)
    if not varaa_paikka(env.get("YOJONO_PR_TILA", OLETUS_TILA)):
        raise PrVirhe("paivaraja %d PR-yritysta taynna" % RAJA_PAIVASSA)
    sync = paivita_fork(repo, botti, tunnus, avaa=avaa)
    pushaa(hakemisto, repo, haara, botti, tunnuspolku, suorita=suorita, sync=sync)
    return avaa_pr(repo, haara, botti, otsikko, kuvaus, tunnus, avaa=avaa)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) == 4 and argv[0] == "tarkista":
            tiedostot = tarkista(argv[1], argv[2], argv[3])
            print("ok: %d tiedostoa" % len(tiedostot))
            return 0
        if len(argv) == 6 and argv[0] == "pr":
            with open(argv[5], encoding="utf-8") as f:
                kuvaus = f.read()
            print(luo_pr(argv[1], argv[2], argv[3], argv[4], kuvaus))
            return 0
    except PrVirhe as e:
        print("pr_kutsu: %s" % e, file=sys.stderr)
        return 1
    except OSError as e:
        print("pr_kutsu: tiedostovirhe: %s" % type(e).__name__, file=sys.stderr)
        return 1
    print("kaytto: pr_kutsu tarkista REPO HAKEMISTO HAARA | pr REPO HAKEMISTO HAARA OTSIKKO KUVAUSTIEDOSTO", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
