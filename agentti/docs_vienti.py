"""Docs-viennin siivottu kopio: agenttien tulokset, tehtavat ja paatokset Gitean repoon tony/agenttidata.

Suunnitelma: claude-jono/suunnitelmat/aivo-tietolahde-1-tulokset.md (vaihtoehto a, Tonyn paatos A1003).
Vain nimetyt polut (allowlist), siivous ennen kopiota, push vain sallittuun osoitteeseen. Ei muuta mitaan muualle.

Lahde (docs-repon kopio, vain luku):  claude-jono/tulokset/*.md, claude-jono/tehtavat/*.md, claude-jono/pohdinta-vastaukset.md
                                     (perusajo, 15 min valein) seka claude-jono/kompassi.md ja claude-jono/faktat.md
                                     (--aivo, kerran tunnissa; Tonyn paatos 19.11.2026, tehtava "Aivo: agenttidatan vientiin kompassi...").
Kohde (tyoklooni): tulokset/, tehtavat/, pohdinta-vastaukset.md, kompassi.md, faktat.md.
Kumpikin ajo hallitsee vain omia polkujaan: perusajo ei koske kompassiin eika faktoihin, --aivo ei koske tuloksiin.
Samaan tyoklooniin ajetaan yksi vienti kerrallaan (lukko <tyo>.lukko).

Siivous: tiedosto, jossa on avaimen muotoinen merkkijono (sk-, ghp_, github_pat_, Telegram-token, yksityisen avaimen otsake),
jatetaan kokonaan pois (kirjataan vain nimi). Muuten poistetaan avain=arvo-parit sekka IPv4-osoitteet ja *.ts.net-nimet.
Tiedostot yli 200 kt jatetaan pois. Symbolisia linkkeja ei seurata. Jos mikaan ei muuttunut, committia ei tehda.

Kaytto: python3 -m agentti.docs_vienti [--aivo] [--kuiva]
Ymparisto: DOCS_VIENTI_LAHDE (/opt/agenttifirma-docs), DOCS_VIENTI_TYO (/var/lib/docs-vienti/agenttidata),
DOCS_VIENTI_OSOITE (PAKOLLINEN: ssh://git@<Gitean IP>[:portti]/tony/agenttidata.git), DOCS_VIENTI_ISANTA (PAKOLLINEN: tasmalleen Gitean IPv4-osoite;
osoitteen isannan pitaa olla sama), DOCS_VIENTI_AVAIN (/etc/docs-vienti/avain).
Virheista tulostetaan vain tyypin nimi tai lyhyt syy, ei gitin tulostetta (voi sisaltaa salaisuuksia).
"""
import argparse
import contextlib
import fcntl
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .tilannekuva import SALAISUUDET

OLETUS_LAHDE = "/opt/agenttifirma-docs"
OLETUS_TYO = "/var/lib/docs-vienti/agenttidata"
OLETUS_AVAIN = "/etc/docs-vienti/avain"

# Sallittu isanta: vain yksi, asetuksella DOCS_VIENTI_ISANTA annettu kirjaimellinen IPv4-osoite (ei verkkoalueita, ei nimia eli ei DNS-kikkoja).
# Asetus ei saa olla julkinen, loopback-, linkki- tai monilahetysosoite; asetukset kirjoitetaan /etc/docs-vienti/asetukset.env, ei repoon.
SALLITTU_POLKU = "/tony/agenttidata.git"
HAARA = "main"
MAX_TAVUA = 200_000
GIT_AIKARAJA_S = 120
NIMI_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.md$")

LAHDE_KANSIO = "claude-jono"
KANSIOT = ("tulokset", "tehtavat")
YKSITTAISET = ("pohdinta-vastaukset.md",)
# Aivon kirjaston tiedostot (--aivo, kerran tunnissa). Viedaan sellaisinaan siivouksen jalkeen.
AIVO = ("kompassi.md", "faktat.md")
LUKKO_ODOTUS_S = 60

# Avaimen muotoiset merkkijonot: tiedosto jatetaan pois kokonaan. sk-: ei sanan keskelta (risk-assessment-framework).
AVAIN_MUOTOISET = [
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
IPV4 = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9])")
TAILNET = re.compile(r"(?i)\b(?:[a-z0-9-]+\.)+ts\.net\b")


class ViennitVirhe(Exception):
    """Vienti pysaytetty (esim. kielletty osoite); viesti on lyhyt eika sisalla salaisuuksia."""


def isanta_kelvollinen(isanta):
    """Tosi vain, jos asetus on yksi IPv4-osoite, joka ei ole julkinen eika loopback-, linkki-, monilahetys- tai maarittamaton osoite."""
    try:
        ip = ipaddress.IPv4Address(str(isanta))
    except ValueError:
        return False
    return not (ip.is_global or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified)


def osoite_sallittu(osoite, isanta):
    """Tosi vain, jos osoite on ssh://git@<isanta>[:portti]/tony/agenttidata.git ja <isanta> on asetuksen yksi tarkka IPv4-osoite."""
    if not isanta_kelvollinen(isanta):
        return False
    try:
        u = urlparse(str(osoite))
        port = u.port
        host = ipaddress.IPv4Address(u.hostname or "")
    except ValueError:
        return False
    return (u.scheme == "ssh" and u.username == "git" and u.password is None and host == ipaddress.IPv4Address(str(isanta))
            and (port is None or port > 0) and u.path == SALLITTU_POLKU and not u.query and not u.fragment)


def avainmuotoinen(teksti):
    return any(p.search(teksti) for p in AVAIN_MUOTOISET)


def siivoa(teksti):
    """Poistaa avain=arvo-parit, IPv4-osoitteet ja tailnet-nimet. Ei katkaise pituutta."""
    for s in SALAISUUDET:
        teksti = s.sub("[poistettu]", teksti)
    teksti = TAILNET.sub("[nimi-poistettu]", teksti)
    return IPV4.sub("[ip-poistettu]", teksti)


def _lahteet(lahde, aivo):
    """Palauttaa (suhteellinen kohdepolku, lahdepolku) vain allowlistasta; symlinkit ja epakelvot nimet ohitetaan."""
    perus = Path(lahde) / LAHDE_KANSIO
    tulos = []
    for kansio in (() if aivo else KANSIOT):
        k = perus / kansio
        if not k.is_dir() or k.is_symlink():
            continue
        for p in sorted(k.iterdir()):
            if NIMI_RE.match(p.name) and p.is_file() and not p.is_symlink():
                tulos.append((kansio + "/" + p.name, p))
    for nimi in (AIVO if aivo else YKSITTAISET):
        p = perus / nimi
        if p.is_file() and not p.is_symlink():
            tulos.append((nimi, p))
    return tulos


def _sallittu_kohde(polku, aivo):
    """Ajo saa muuttaa vain omia polkujaan: --aivo vain AIVO-tiedostoja, perusajo vain tuloksia, tehtavia ja YKSITTAISET."""
    if aivo:
        return polku in AIVO
    if polku in YKSITTAISET:
        return True
    osat = polku.split("/")
    return len(osat) == 2 and osat[0] in KANSIOT and bool(NIMI_RE.match(osat[1]))


def valmista(lahde, kohde, aivo=False):
    """Kirjoittaa siivotun kopion tyoklooniin (poistaa ensin taman ajon hallitut polut). Palauttaa (kopioitu, ohitetut: [(nimi, syy)])."""
    kohde = Path(kohde)
    for kansio in (() if aivo else KANSIOT):
        shutil.rmtree(kohde / kansio, ignore_errors=True)
    for nimi in (AIVO if aivo else YKSITTAISET):
        (kohde / nimi).unlink(missing_ok=True)
    kopioitu, ohitetut = 0, []
    for suht, p in _lahteet(lahde, aivo):
        try:
            if p.stat().st_size > MAX_TAVUA:
                ohitetut.append((suht, "yli %d tavua" % MAX_TAVUA))
                continue
            teksti = p.read_text(encoding="utf-8", errors="replace")
        except OSError as virhe:
            ohitetut.append((suht, type(virhe).__name__))
            continue
        if avainmuotoinen(teksti):
            ohitetut.append((suht, "avaimen muotoinen merkkijono"))
            continue
        ulos = kohde / suht
        ulos.parent.mkdir(parents=True, exist_ok=True)
        ulos.write_text(siivoa(teksti), encoding="utf-8")
        kopioitu += 1
    return kopioitu, ohitetut


def _git(tyo, *args, env=None):
    """Ajaa gitin ilman tulosteen vuotoa; palauttaa (paluukoodi, stdout)."""
    e = dict(os.environ)
    e.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    e.update(env or {})
    r = subprocess.run(["git", "-C", str(tyo)] + list(args), capture_output=True, text=True, timeout=GIT_AIKARAJA_S, env=e)
    return r.returncode, r.stdout


def _ssh_env(avain, tyo):
    tiedot = str(Path(tyo).parent / "known_hosts")
    return {"GIT_SSH_COMMAND": "ssh -i %s -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
                               "-o UserKnownHostsFile=%s" % (avain, tiedot)}


@contextlib.contextmanager
def _lukko(tyo, odotus_s=LUKKO_ODOTUS_S):
    """Yksi vienti kerrallaan samaan tyoklooniin (perusajo ja --aivo voivat osua samaan aikaan). Lukko vapautuu prosessin paattyessa."""
    polku = Path(str(tyo) + ".lukko")
    polku.parent.mkdir(parents=True, exist_ok=True)
    with open(polku, "a") as f:
        loppu = time.monotonic() + odotus_s
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= loppu:
                    raise ViennitVirhe("toinen vienti on kaynnissa; yritetaan seuraavalla ajolla")
                time.sleep(1)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def vie(lahde, tyo, osoite, avain=OLETUS_AVAIN, aivo=False, kuiva=False, ssh=True, isanta="", lukko_odotus_s=LUKKO_ODOTUS_S):
    """Koko vienti. Palauttaa yhteenvetorivin. Nostaa ViennitVirhe, jos osoite on kielletty tai git epaonnistuu.

    Pushaus tehdaan suoraan tarkistettuun osoitteeseen (ei etakohteen nimen kautta), joten tyoklooniin
    asetettu `origin` ei voi ohjata dataa muualle.
    """
    if not isanta_kelvollinen(isanta):
        raise ViennitVirhe("DOCS_VIENTI_ISANTA puuttuu tai ei ole kelvollinen sisaverkon IPv4-osoite; ei pushata")
    if not osoite_sallittu(osoite, isanta):
        raise ViennitVirhe("osoite ei ole sallittu (isanta, kayttaja tai polku); ei pushata")
    tyo = Path(tyo)
    with _lukko(tyo, lukko_odotus_s):
        return _vie_lukittuna(lahde, tyo, osoite, avain, aivo, kuiva, ssh)


def _vie_lukittuna(lahde, tyo, osoite, avain, aivo, kuiva, ssh):
    """vie() lukon sisalla; osoite ja isanta on jo tarkistettu."""
    env = _ssh_env(avain, tyo) if ssh else {}
    if not (tyo / ".git").is_dir():
        tyo.mkdir(exist_ok=True)
        rc, _ = _git(tyo, "init", "-q", "-b", HAARA)
        if rc != 0:
            raise ViennitVirhe("git init epaonnistui")
    # Haetaan nykytila (tyhja repo: haara puuttuu, se on sallittu ensimmaisella kerralla).
    rc, nakyy = _git(tyo, "ls-remote", osoite, "refs/heads/" + HAARA, env=env)
    if rc != 0:
        raise ViennitVirhe("etarepon luku epaonnistui (git ls-remote)")
    if nakyy.strip():
        rc, _ = _git(tyo, "fetch", "-q", osoite, HAARA, env=env)
        if rc != 0:
            raise ViennitVirhe("git fetch epaonnistui")
        rc, _ = _git(tyo, "reset", "-q", "--hard", "FETCH_HEAD")
        if rc != 0:
            raise ViennitVirhe("git reset epaonnistui")
    kopioitu, ohitetut = valmista(lahde, tyo, aivo)
    _git(tyo, "add", "-A")
    rc, nimet = _git(tyo, "diff", "--cached", "--name-only")
    if rc != 0:
        raise ViennitVirhe("git diff epaonnistui")
    muuttuneet = [n for n in nimet.splitlines() if n]
    vaarat = [n for n in muuttuneet if not _sallittu_kohde(n, aivo)]
    if vaarat:
        _git(tyo, "reset", "-q")
        raise ViennitVirhe("allowlistin ulkopuolisia polkuja (%d); ei committia" % len(vaarat))
    ohit = "; ohitettu %d" % len(ohitetut) if ohitetut else ""
    if not muuttuneet:
        return "ei muutoksia (kopioitu %d)%s; ei committia" % (kopioitu, ohit)
    if kuiva:
        _git(tyo, "reset", "-q")
        return "kuiva-ajo: %d tiedostoa muuttuisi (kopioitu %d)%s; ei committia" % (len(muuttuneet), kopioitu, ohit)
    rc, _ = _git(tyo, "-c", "user.name=docs-vienti", "-c", "user.email=docs-vienti@localhost",
                 "commit", "-q", "-m", "docs-vienti%s: %d tiedostoa muuttui" % (" (aivo)" if aivo else "", len(muuttuneet)))
    if rc != 0:
        raise ViennitVirhe("git commit epaonnistui")
    rc, _ = _git(tyo, "push", "-q", osoite, "HEAD:refs/heads/" + HAARA, env=env)
    if rc != 0:
        raise ViennitVirhe("git push epaonnistui")
    return "pushattu: %d tiedostoa muuttui (kopioitu %d)%s" % (len(muuttuneet), kopioitu, ohit)


def main(argv=None):
    p = argparse.ArgumentParser(prog="agentti.docs_vienti", description="Siivottu kopio agenttien tuloksista Gitean repoon tony/agenttidata")
    p.add_argument("--aivo", action="store_true", help="vie vain Aivon tiedostot: claude-jono/kompassi.md ja faktat.md (ajastin kerran tunnissa)")
    p.add_argument("--kuiva", action="store_true", help="ala committaa eika pushaa")
    a = p.parse_args(argv)
    env = os.environ.get
    try:
        print("docs-vienti: " + vie(env("DOCS_VIENTI_LAHDE", OLETUS_LAHDE), env("DOCS_VIENTI_TYO", OLETUS_TYO),
                                    env("DOCS_VIENTI_OSOITE", ""), env("DOCS_VIENTI_AVAIN", OLETUS_AVAIN),
                                    a.aivo, a.kuiva, isanta=env("DOCS_VIENTI_ISANTA", "")))
    except ViennitVirhe as virhe:
        print("docs-vienti: VIRHE: %s" % virhe, file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as virhe:
        print("docs-vienti: VIRHE: %s" % type(virhe).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
