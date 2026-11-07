"""PR-kutsu: pushaa agentin `yo/`-haaran botti-tilin forkkiin ja avaa siita PR alkuperaiseen julkiseen repoon.

Miksi fork: botilla ei ole mitaan oikeuksia Tonyn repoihin. Se kirjoittaa vain omiin forkkeihinsa, ja PR tulee forkista,
joten automerge (vain saman repon haarat) ei koske siihen: Tony yhdistaa aina itse.

Turva ("koodi on laki"):
- repo mika tahansa Tonyn oma repo paitsi KIELLETYT_REPOT (Tonyn paatos 4.11.2026: kaikki omat repot; REPOT on mittareiden
  seurantalista), haara vain `yo/<nimi>`
- tunnus luetaan tiedostosta (oletus /etc/yojono/github-bot.token), ei koskaan argumenttina, URL:ssa tai tulosteessa;
  git saa sen vain tilapaiselta askpass-skriptilta (0700), joten tunnus ei nay prosessilistassa
- ei force-pushia (refspec ilman `+`), ei paikallisia koukkuja (core.hooksPath=/dev/null)
- agentti ei muuta omia suojakaiteitaan (AGENTILTA_KIELLETYT: automerge, PR-tyokalu, oikeudet, salaisuudet); muut polut,
  myos isanta/, tools/ ja .github/, saa ehdottaa PR:lla (Tonyn paatos 4.11.2026). Rajatut (suojattu()) yhdistaa Tony,
  muut automerge riippumattoman tarkastajan hyvaksynnalla (tools/automerge.py)
- enintaan RAJA_PAIVASSA PR-yritysta paivassa; vain stdlib
- forkin synkkaus (synkkaa_fork): vain kyseisen repon pohjahaara (main; ai-pr-reviewer-action: master), vain fast-forward, ei force-pushia
  (refspec ilman `+`), ei muita haaroja; jos fork-main eroaa ylavirrasta, ei tehda mitaan (fork_ei_ff); sama tunnus kuin PR:ssa, ei uusia oikeuksia

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
import urllib.parse
import urllib.request
from datetime import date

from . import github_app

OMISTAJA = "tonyweckstrom1987"
OLETUS_BOTTI = "agenttifirma-bot"
KIELLETYT_REPOT = ("tonyweckstrom1987.github.io", "agenttifirma-docs")   # julkaisu Tonyn nimissa / Tonyn paatokset
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
REPOT = ("agentti-pohja", "mcp-model-router", "ai-pr-reviewer-action", "llm-stopcheck", "ideapankki", "evalkit", "kauppatiimi", "mcp-doctor", "llm-cassette")
HAARA_RE = re.compile(r"^yo/[a-z0-9][a-z0-9._-]{0,59}$")
BOTTI_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
TUNNUS_RE = re.compile(r"^(github_pat_|ghp_|ghs_)[A-Za-z0-9_]{20,}$")      # ghs_ = GitHub Appin asennustunnus (1 h)
OLETUS_TUNNUS = "/etc/yojono/github-bot.token"
OLETUS_TILA = "/var/lib/yojono/pr-tila.json"
RAJA_PAIVASSA = 100
MAX_TIEDOSTOJA = 40
OTSIKKO_MAX = 120
KUVAUS_MAX = 4000
# Tonyn yhdistettavat polut (sama luokka kuin tools/automerge.py:n RAJATUT; aamupaketti ja autonomia luokittelevat taman mukaan).
SUOJATUT_ALKUOSAT = (".github/", "yojono/")
SUOJATUT_NIMET = ("rajat.py", "config.py", "pr_kutsu.py", "github_app.py", "claudejono-pr", "asenna-kayttaja.sh", "automerge.py",
                  "varaus.py")
SUOJATUT_RE = re.compile(r"(^|/)sudoers|nftables|iptables|ufw|palomuuri|firewall")
# Naita agentti ei saa edes ehdottaa: omat suojakaiteet (agentit eivat muuta omia rajojaan; Claude kirjoittaa, Tony yhdistaa).
AGENTILTA_KIELLETYT_RE = re.compile(
    r"^(tools/automerge\.py|\.github/workflows/automerge\.ya?ml|agentti/(pr_kutsu|github_app)\.py|claudejono/claudejono-pr)$|(^|/)sudoers")
# Repokohtaiset lisasuojaukset (yleisten suojattujen lisaksi). kauppatiimi (Tonyn paatos 31.10.2026, "vapaat kadet paperilla"):
# agentti saa muuttaa strategiaa, analyysia, testeja ja paperiasetuksia, mutta oikean rahan lukot pysyvat Tonyn kasissa:
# riskivahti, salkku, kirjanpito, asetusten valitsin (fail closed), palvelu-/ajastintiedostot, data/ ja tietokannat.
REPON_SUOJATUT = {"kauppatiimi": re.compile(
    r"(^|/)(riskivahti\.py|salkku\.py|kirjanpito\.py|asetukset\.py|rajat\.py|config\.py)$|\.(service|timer|db|sqlite3?)$|^data/")}
SALAINEN_RE = re.compile(r"(\.env|token|secret|api_?key|\.pem|id_ed25519|id_rsa)", re.IGNORECASE)
# Koodiportti (virheiden ehkaisy, tehtava 2026-11-03-04): PR:n kuvauksessa pitaa olla rivi "Todennettu kaytossa: miten".
# Tyhja arvo, viiva tai pelkka paikkamerkki ei kelpaa. Tarkistus koskee vain sita osaa, joka oikeasti paatyy PR:n runkoon.
TODENNETTU_RE = re.compile(r"^[ \t>*_-]*Todennettu k(?:\u00e4|a)yt(?:\u00f6|o)ss(?:\u00e4|a)[ \t]*:(?:\*\*|__)?[ \t]*(.*)$",
                           re.IGNORECASE | re.MULTILINE)
TODENNETTU_TYHJA_RE = re.compile(r"^[\s\-\u2013\u2014.:*_]*(?:tbd|todo|n/?a|ei mit(?:\u00e4|a)(?:\u00e4|a)n|ei ole)?[\s\-\u2013\u2014.:*_]*$", re.IGNORECASE)
ALAKUULO = ("\n\n---\nAgentin tekema ehdotus. Riippumaton tarkastaja katselmoi; automerge yhdistaa rajattujen ulkopuoliset, "
            "rajatut (oikeudet, kulukatot, salaisuudet, raha, tyonkulut) yhdistaa Tony.")


class PrVirhe(Exception):
    """Odotettu virhe: viesti on turvallinen tulostaa (ei sisalla tunnusta)."""


class ForkVirhe(PrVirhe):
    """Forkin synkkaus ei onnistunut tai sita ei voi tehda. `koodi` on vakaa (tapahtumaloki ja viikkoraportti), `viesti` selitys ilman koodia;
    str(virhe) on `koodi: viesti`, joten koodi nakyy myos kehitys.sh:n kautta kulkevassa viestissa.
    Koodit: fork_jaljessa (fork jaa jaljelle push-yrityksen jalkeen), fork_ei_ff (fork-main eroaa ylavirrasta: ei tehda mitaan),
    fork_push_epaonnistui (push hylatty tai ei onnistunut), fork_workflow_oikeus (push hylatty, koska botin tunnuksella ei ole
    workflow-oikeutta ja ylavirran commitit muuttavat .github/workflows-tiedostoja), fork_tarkistus_epaonnistui (tilaa ei saatu luettua)."""

    def __init__(self, koodi, viesti):
        super().__init__("%s: %s" % (koodi, viesti))
        self.koodi, self.viesti = koodi, viesti


def _normalisoi(polku):
    p = posixpath.normpath(polku.strip()).lower()
    while p.startswith("./"):
        p = p[2:]
    return p


def suojattu(polku):
    """Onko polku rajattu: Tony yhdistaa (oikeudet, kulukatot, palomuuri, salaisuudet, tyonkulut, automerge)."""
    p = _normalisoi(polku)
    nimi = p.rsplit("/", 1)[-1]
    osat = p.split("/")
    alut = tuple(a.rstrip("/") for a in SUOJATUT_ALKUOSAT)
    if osat[0] == "yojono" and re.match(r"^test_[a-z0-9_]+\.py$", nimi) and len(osat) == 2:
        return False                                       # yojonon testit eivat ole kulukattoja (sama kuin automerge)
    return (p.startswith("/") or ".." in osat or osat[0] in alut or ".github" in osat
            or nimi in SUOJATUT_NIMET or bool(SUOJATUT_RE.search(p)) or bool(SALAINEN_RE.search(nimi)))


def agentilta_kielletty(polku):
    """Polku, jota agentti ei saa muuttaa PR:ssa lainkaan (omat suojakaiteet, salaisuudet, polkukikat)."""
    p = _normalisoi(polku)
    osat = p.split("/")
    return (p.startswith("/") or ".." in osat or bool(AGENTILTA_KIELLETYT_RE.search(p))
            or bool(SALAINEN_RE.search(p.rsplit("/", 1)[-1])))


def sallittu_repo(repo):
    """Kaikki Tonyn omat repot paitsi KIELLETYT_REPOT (fork botin tilille pitaa olla olemassa)."""
    return isinstance(repo, str) and bool(REPO_RE.match(repo)) and repo not in KIELLETYT_REPOT and ".." not in repo


def sallittu_polku(repo, polku):
    """Saako polkua muuttaa repon lisasuojausten puolesta (repot ilman lisasuojausta: aina True)."""
    saanto = REPON_SUOJATUT.get(repo)
    if saanto is None:
        return True
    p = posixpath.normpath(polku.strip()).lower()
    while p.startswith("./"):
        p = p[2:]
    if p.startswith("/") or ".." in p.split("/"):
        return False
    return not saanto.search(p)


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


def tunnus_tiedosto(env, repo, rooli, tmp):
    """Polku tiedostoon, jossa on kaytettava tunnus. rooli: 'fork' (push, forkin synkkaus) tai 'pr' (PR:n avaus).

    Jos YOJONO_APP_ASETUKSET on asetettu, haetaan lyhytikainen GitHub App -asennustunnus (vain tama repo, vain rooliin tarvittava oikeus)
    ja kirjoitetaan se tmp-hakemistoon (600). Jos haku epaonnistuu, kaytetaan vanhaa PAT-tiedostoa varalla (virheesta tulostetaan vain tyyppi/tila).
    Ilman asetusta kaytetaan PAT:ia kuten ennen."""
    asetukset = env.get("YOJONO_APP_ASETUKSET")
    if asetukset:
        try:
            tila = os.path.dirname(env.get("YOJONO_PR_TILA", OLETUS_TILA))
            tunnus = github_app.hae_tunnus(asetukset, rooli, repo, tila)
            polku = os.path.join(tmp, "app-" + rooli + ".tok")
            with open(os.open(polku, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w", encoding="ascii") as f:
                f.write(tunnus)
            return polku
        except (github_app.AppVirhe, OSError, UnicodeEncodeError) as e:
            print("pr_kutsu: App-tunnus ei saatavilla (%s), kaytetaan PAT-varaa" % (e if isinstance(e, github_app.AppVirhe) else type(e).__name__),
                  file=sys.stderr)
    return env.get("YOJONO_BOT_TUNNUS", OLETUS_TUNNUS)


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
    if not sallittu_repo(repo):
        raise PrVirhe("repo ei ole sallittu: " + str(repo))
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
    huonot = [t for t in tiedostot if agentilta_kielletty(t)]
    if huonot:
        raise PrVirhe("agentin omia suojakaiteita tai salaisuuksia muutettu, kirjaa tarve Tonylle: " + ", ".join(huonot[:5]))
    rajatut = [t for t in tiedostot if not sallittu_polku(repo, t)]
    if rajatut:
        raise PrVirhe("repon %s suojattuja polkuja muutettu (oikean rahan lukot), tee kasin: %s" % (repo, ", ".join(rajatut[:5])))
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


def _git_ymparisto(tmp, botti, tunnuspolku):
    """Gitin ymparisto pushia varten: tunnus kulkee vain tilapaisen askpass-skriptin (0700, hakemistossa tmp) kautta, GH_/GITHUB_-muuttujat poistetaan."""
    skripti = os.path.join(tmp, "askpass.sh")
    with open(os.open(skripti, os.O_CREAT | os.O_WRONLY, 0o700), "w", encoding="ascii") as f:
        f.write("#!/bin/sh\ncase \"$1\" in\n  Username*) echo %s ;;\n  *) cat %s ;;\nesac\n"
                % (shlex.quote(botti), shlex.quote(tunnuspolku)))
    ymp = {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_", "GIT_ASKPASS"))}
    ymp.update({"GIT_ASKPASS": skripti, "GIT_TERMINAL_PROMPT": "0"})
    return ymp


def pushaa(hakemisto, repo, haara, botti, tunnuspolku, suorita=subprocess.run, sync=True):
    """Pushaa haaran botin forkkiin (ei force). Tunnus kulkee vain tilapaisen askpass-skriptin kautta."""
    with tempfile.TemporaryDirectory() as tmp:
        ymp = _git_ymparisto(tmp, botti, tunnuspolku)
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


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SYNKKA_REFS = ("refs/synkka/ylavirta", "refs/synkka/fork")      # vain valiaikaisen bare-repon sisalla


def _github_url(omistaja, repo):
    return "https://github.com/%s/%s.git" % (omistaja, repo)


def _luku_ymparisto():
    """Julkisten repojen luku (ls-remote, fetch): ei tunnuksia, ei kyselyja."""
    ymp = {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_", "GIT_ASKPASS"))}
    ymp["GIT_TERMINAL_PROMPT"] = "0"
    return ymp


def _git_ajo(suorita, argv, koodi, aika, ymp):
    try:
        return suorita(argv, capture_output=True, text=True, timeout=aika, check=False, env=ymp)
    except subprocess.TimeoutExpired:
        raise ForkVirhe(koodi, "git aikakatkaisu (%d s)" % aika) from None
    except OSError as e:
        raise ForkVirhe(koodi, "git ei kaynnisty: " + type(e).__name__) from None


def _paa_sha(suorita, osoite, haara, ymp):
    """Haaran karki ls-remote:lla (julkinen luku) tai None, jos sita ei saada luettua."""
    r = _git_ajo(suorita, ["git", "-c", "credential.helper=", "ls-remote", "--heads", osoite, "refs/heads/" + haara], "fork_tarkistus_epaonnistui", 60, ymp)
    rivit = [x.split() for x in (r.stdout or "").splitlines() if x.strip()]
    if r.returncode != 0 or len(rivit) != 1 or len(rivit[0]) != 2 or rivit[0][1] != "refs/heads/" + haara or not SHA_RE.match(rivit[0][0]):
        return None
    return rivit[0][0]


def _on_esivanhempi(suorita, bare, a, b, ymp):
    """0 = a on b:n esivanhempi, 1 = ei ole; muu = virhe (ForkVirhe)."""
    r = _git_ajo(suorita, ["git", "-C", bare, "merge-base", "--is-ancestor", a, b], "fork_tarkistus_epaonnistui", 60, ymp)
    if r.returncode not in (0, 1):
        raise ForkVirhe("fork_tarkistus_epaonnistui", "historian vertailu epaonnistui")
    return r.returncode


def synkkaa_fork(repo, botti, tunnuspolku, suorita=subprocess.run, url=_github_url):
    """Kelaa botin forkin pohjahaaran (main; ai-pr-reviewer-action: master) ylavirran tasalle, VAIN fast-forwardina.

    Vaiheet: ls-remote (ylavirta ja fork) -> samat = valmis; muuten fetch molemmat valiaikaiseen bare-repoon -> fork on ylavirran esivanhempi = push
    (refspec ilman `+`, ei force-pushia, vain tama yksi haara, botin tunnuksella) -> tarkistus ls-remote:lla. Jos fork-main eroaa ylavirrasta
    (ei fast-forward), ei tehda mitaan (fork_ei_ff). Palauttaa "ajan_tasalla", "kelattu" tai "edella" (fork on vain edella: ei tarvitse tehda mitaan);
    muuten ForkVirhe(koodi). Tunnus luetaan vain pushia varten, eika se nay prosessilistassa, URL:ssa tai viestissa."""
    if not sallittu_repo(repo):
        raise ForkVirhe("fork_tarkistus_epaonnistui", "repo ei ole sallittu: " + str(repo))
    haara = pohja_haara(repo)
    ylavirta, fork = url(OMISTAJA, repo), url(botti, repo)
    luku = _luku_ymparisto()
    u, f = _paa_sha(suorita, ylavirta, haara, luku), _paa_sha(suorita, fork, haara, luku)
    if u is None or f is None:
        raise ForkVirhe("fork_tarkistus_epaonnistui", "%s ei ole luettavissa" % ("ylavirran " + haara if u is None else "forkin " + haara))
    if u == f:
        return "ajan_tasalla"
    with tempfile.TemporaryDirectory() as tmp:
        bare = os.path.join(tmp, "synkka.git")
        if _git_ajo(suorita, ["git", "init", "-q", "--bare", bare], "fork_tarkistus_epaonnistui", 60, luku).returncode != 0:
            raise ForkVirhe("fork_tarkistus_epaonnistui", "valiaikaisen repon luonti epaonnistui")
        for osoite, ref in ((ylavirta, SYNKKA_REFS[0]), (fork, SYNKKA_REFS[1])):
            r = _git_ajo(suorita, ["git", "-C", bare, "-c", "credential.helper=", "fetch", "-q", "--no-tags", osoite, "refs/heads/%s:%s" % (haara, ref)],
                         "fork_tarkistus_epaonnistui", 180, luku)
            if r.returncode != 0:
                raise ForkVirhe("fork_tarkistus_epaonnistui", "fetch epaonnistui: " + ((r.stderr or "") + (r.stdout or "")).strip()[-200:])
        if _on_esivanhempi(suorita, bare, SYNKKA_REFS[1], SYNKKA_REFS[0], luku) != 0:      # fork ei ole ylavirran esivanhempi
            if _on_esivanhempi(suorita, bare, SYNKKA_REFS[0], SYNKKA_REFS[1], luku) == 0:
                return "edella"
            raise ForkVirhe("fork_ei_ff", "fork-%s on erkaantunut ylavirrasta (ei fast-forward): mitaan ei tehty, synkkaa kasin" % haara)
        try:
            lue_tunnus(tunnuspolku)                                                        # muoto tarkistetaan ennen pushia; itse tunnus ei jaa muistiin
        except PrVirhe as e:
            raise ForkVirhe("fork_push_epaonnistui", "tunnus: " + str(e)) from None
        ymp = _git_ymparisto(tmp, botti, tunnuspolku)
        r = _git_ajo(suorita, ["git", "-C", bare, "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", "push", fork, "%s:refs/heads/%s" % (SYNKKA_REFS[0], haara)],
                     "fork_push_epaonnistui", 180, ymp)
    if r.returncode != 0:
        koko = ((r.stderr or "") + (r.stdout or "")).strip()          # luokittelu koko tulosteesta (hint-rivit voivat tyontaa [rejected]-rivin pois lopusta)
        loppu = koko[-300:]
        if "workflow" in koko and "scope" in koko:
            raise ForkVirhe("fork_workflow_oikeus", "ylavirran commitit muuttavat .github/workflows-tiedostoja, eika botin tunnuksella ole workflow-oikeutta "
                            "(tunnukselle ei anneta sita itse): synkkaa fork kasin. Push: " + loppu)
        if "non-fast-forward" in koko or "fetch first" in koko or "contains work that you do not" in koko:
            raise ForkVirhe("fork_ei_ff", "fork-%s muuttui synkkauksen aikana (push ei ollut fast-forward): mitaan ei tehty. Push: %s" % (haara, loppu))
        raise ForkVirhe("fork_push_epaonnistui", "push forkin %s-haaraan epaonnistui: %s" % (haara, loppu))
    if _paa_sha(suorita, fork, haara, luku) != u:
        raise ForkVirhe("fork_jaljessa", "push onnistui, mutta fork-%s ei vastaa ylavirtaa" % haara)
    return "kelattu"


def fork_valmis(repo, env=None, suorita=subprocess.run, url=_github_url):
    """Esitarkistus ennen mallikutsua: synkkaa botin fork ylavirran tasalle (vain fast-forward). Palauttaa (ok, koodi, selitys); ok=False -> tehtava ohitetaan
    ilman kulua ja yritysta, ja koodi (fork_*) kirjataan tapahtumalokiin ja raporttiin. Ei lukukelpoista tilaa tai tunnusta = fail closed (mallikutsu menisi hukkaan)."""
    env = os.environ if env is None else env
    try:
        botti = botti_kayttaja(env)
        with tempfile.TemporaryDirectory() as tmp:
            tulos = synkkaa_fork(repo, botti, tunnus_tiedosto(env, repo, "fork", tmp), suorita=suorita, url=url)
    except ForkVirhe as e:
        return False, e.koodi, e.viesti
    except PrVirhe as e:
        return False, "fork_tarkistus_epaonnistui", str(e)
    return True, "", tulos


def olemassa_oleva_pr(repo, haara, botti, tunnus, avaa=urllib.request.urlopen):
    """Avoimen PR:n osoite haaralle botti:haara, tai None (myos virheissa)."""
    kysely = urllib.parse.urlencode({"head": f"{botti}:{haara}", "state": "open"})
    pyynto = urllib.request.Request(
        f"https://api.github.com/repos/{OMISTAJA}/{repo}/pulls?{kysely}",
        headers={"Authorization": "Bearer " + tunnus, "Accept": "application/vnd.github+json", "User-Agent": "yojono-pr-kutsu"})
    try:
        with avaa(pyynto, timeout=30) as vastaus:
            lista = json.loads(vastaus.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return None
    if isinstance(lista, list) and lista and isinstance(lista[0], dict):
        osoite = lista[0].get("html_url")
        if isinstance(osoite, str) and osoite.startswith("https://github.com/"):
            return osoite
    return None


AIHE_RE = re.compile(r"^[a-z]{1,30}$")
TUNNISTE_RE = re.compile(r"^[a-z0-9_-]{0,60}$")
HAARA_MAX = 63                                             # kehitys.sh: cut -c1-63


def avoin_aihe_pr(repo, aihe, tunniste, botti, tunnus, avaa=urllib.request.urlopen, sivuja=3):
    """Botin avoin yojono-PR samalle aiheelle (ja tunnisteelle) mina tahansa paivana: osoite tai None.

    Miksi (tulos 07-40, 7.11.2026): kehitys.sh:n haaran nimessa on paiva (yo/<aihe>-<pvm>[-tunniste]), joten joka ajo avasi uuden PR:n
    edellisen, yha avoimen PR:n viereen (esim. llm-stopcheck #18 ja #20, ai-pr-reviewer-action #24 ja #26), ja avoimien PR:ien kasa kasvoi.
    Vertailu tehdaan tasmalleen samalla nimen muodostuksella kuin kehitys.sh (myos 63 merkin katkaisu), joten eri tunnisteen PR ei esta.
    Nostaa PrVirhe, jos listaa ei saada luettua (kutsuja paattaa, estaako virhe ajon)."""
    if not AIHE_RE.match(str(aihe)) or not TUNNISTE_RE.match(str(tunniste)):
        raise PrVirhe("virheellinen aihe tai tunniste")
    malli = re.compile(r"^yo/" + re.escape(aihe) + r"-(\d{4}-\d{2}-\d{2})")
    for sivu in range(1, sivuja + 1):
        kysely = urllib.parse.urlencode({"state": "open", "per_page": 100, "page": sivu})
        pyynto = urllib.request.Request(
            f"https://api.github.com/repos/{OMISTAJA}/{repo}/pulls?{kysely}",
            headers={"Authorization": "Bearer " + tunnus, "Accept": "application/vnd.github+json", "User-Agent": "yojono-pr-kutsu"})
        try:
            with avaa(pyynto, timeout=30) as vastaus:
                lista = json.loads(vastaus.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise PrVirhe("avoimien PR:ien haku epaonnistui (HTTP %s)" % e.code)
        except (urllib.error.URLError, ValueError, OSError) as e:
            raise PrVirhe("avoimien PR:ien haku epaonnistui: " + type(e).__name__)
        if not isinstance(lista, list):
            raise PrVirhe("avoimien PR:ien vastaus ei ole lista")
        for pr in lista:
            head = pr.get("head") if isinstance(pr, dict) else None
            if not isinstance(head, dict):
                continue
            ref = head.get("ref")
            kayttaja = (head.get("user") or {}).get("login") if isinstance(head.get("user"), dict) else None
            if not isinstance(ref, str) or not isinstance(kayttaja, str) or kayttaja.lower() != botti.lower():
                continue
            m = malli.match(ref)
            if not m:
                continue
            odotettu = ("yo/%s-%s" % (aihe, m.group(1)) + ("-" + tunniste if tunniste else ""))[:HAARA_MAX]
            osoite = pr.get("html_url")
            if ref == odotettu and isinstance(osoite, str) and osoite.startswith("https://github.com/"):
                return osoite
        if len(lista) < 100:
            return None
    return None


def avoin(repo, aihe, tunniste="", env=None, avaa=urllib.request.urlopen):
    """CLI-kaare: tarkistaa repon ja hakee tunnuksen 'pr'-roolilla (sama kuin PR:n avauksessa, vain luku)."""
    env = os.environ if env is None else env
    if not sallittu_repo(repo):
        raise PrVirhe("repo ei ole sallittu: " + str(repo))
    botti = botti_kayttaja(env)
    with tempfile.TemporaryDirectory() as tmp:
        tunnus = lue_tunnus(tunnus_tiedosto(env, repo, "pr", tmp))
    return avoin_aihe_pr(repo, aihe, tunniste, botti, tunnus, avaa=avaa)


def tarkista_todennus(kuvaus):
    """Nostaa PrVirhe, jos kuvauksesta puuttuu rivi 'Todennettu kaytossa: <miten>' tai arvo on tyhja tai '-'."""
    runko = str(kuvaus)[:KUVAUS_MAX]
    arvot = [m.group(1) for m in TODENNETTU_RE.finditer(runko)]
    if not arvot:
        raise PrVirhe("kuvauksesta puuttuu rivi 'Todennettu kaytossa: miten' (koodiportti)")
    if not any(not TODENNETTU_TYHJA_RE.match(a) for a in arvot):
        raise PrVirhe("rivin 'Todennettu kaytossa:' arvo on tyhja tai '-' (koodiportti)")


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
        if e.code == 422:
            # Yleisin syy: haaralle on jo avoin PR. Push on silloin jo paivittanyt sen (vika 31.10.: raportit luulivat epaonnistuneen).
            olemassa = olemassa_oleva_pr(repo, haara, botti, tunnus, avaa=avaa)
            if olemassa:
                return olemassa + " (paivitetty)"
        raise PrVirhe("GitHub hylkasi PR:n (HTTP %s)" % e.code)
    except (urllib.error.URLError, ValueError, OSError) as e:
        raise PrVirhe("PR-pyynto epaonnistui: " + type(e).__name__)
    if not isinstance(osoite, str) or not osoite.startswith("https://github.com/"):
        raise PrVirhe("GitHubin vastauksesta puuttuu PR:n osoite")
    return osoite


def luo_pr(repo, hakemisto, haara, otsikko, kuvaus, env=None, suorita=subprocess.run, avaa=urllib.request.urlopen):
    """Koko ketju: tarkistukset, paivaraja, push, PR. Palauttaa PR:n osoitteen."""
    env = os.environ if env is None else env
    tarkista_todennus(kuvaus)                              # ensin: hylkays ei kuluta paivarajaa eika pushaa mitaan
    botti = botti_kayttaja(env)
    tarkista(repo, hakemisto, haara, suorita=suorita)
    with tempfile.TemporaryDirectory() as tmp:
        forkpolku = tunnus_tiedosto(env, repo, "fork", tmp)
        forktunnus = lue_tunnus(forkpolku)                 # puuttuva/virheellinen tunnus ei kuluta paivarajaa
        if not varaa_paikka(env.get("YOJONO_PR_TILA", OLETUS_TILA)):
            raise PrVirhe("paivaraja %d PR-yritysta taynna" % RAJA_PAIVASSA)
        sync = paivita_fork(repo, botti, forktunnus, avaa=avaa)
        pushaa(hakemisto, repo, haara, botti, forkpolku, suorita=suorita, sync=sync)
        return avaa_pr(repo, haara, botti, otsikko, kuvaus, lue_tunnus(tunnus_tiedosto(env, repo, "pr", tmp)), avaa=avaa)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) == 4 and argv[0] == "tarkista":
            tiedostot = tarkista(argv[1], argv[2], argv[3])
            print("ok: %d tiedostoa" % len(tiedostot))
            return 0
        if len(argv) in (3, 4) and argv[0] == "avoin":
            # Paluuarvot: 0 = avoin PR loytyi (osoite tulostetaan), 3 = ei avointa, 1 = tarkistus epaonnistui
            osoite = avoin(argv[1], argv[2], argv[3] if len(argv) == 4 else "")
            if osoite:
                print(osoite)
                return 0
            return 3
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
    print("kaytto: pr_kutsu tarkista REPO HAKEMISTO HAARA | pr REPO HAKEMISTO HAARA OTSIKKO KUVAUSTIEDOSTO | avoin REPO AIHE [TUNNISTE]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
