"""Clauden tehtavajono GitHubin yli (doc 71, Tonyn hyvaksynta 30.10.2026).

Pilvi-Claude kirjoittaa tehtavat docs-repoon `claude-jono/tehtavat/<id>.md`. Tama ajuri (CT115, systemd-ajastin) hakee repon,
ottaa vanhimman tehtavan jolla ei ole tulosta, ajaa sen `claude -p`:lla ja pushaa tuloksen `claude-jono/tulokset/<id>.md`.
Yksi tehtava per ajo. Ajuri ei koskaan committaa mitaan muuta kuin oman tulostiedostonsa.

Raja (koodissa, ei vain kehotteessa): tehtava saa avata PR:n, mutta ei mergea, ei muuta katto- tai oikeusarvoja eika kasittele salaisuuksia.
Kehote kertoo rajan agentille; CT115:n .claude/settings.json estaa salaisuustiedostot; ajuri rajaa commitin polun.
"""
import argparse
import contextlib
import fcntl
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from claudejono import silmukka
from . import kulutus, litellm_ajo, varaus, virheopit
from . import malli as mallit

TEHTAVA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-[a-z0-9-]{1,60}\.md$")
TEHTAVA_MAX_TAVUA = 20000
TULOS_MAX_TAVUA = 60000
# Tyhja suunnittelukierros (0 tehtavaa) toistui 1.11. noin 10 min valein ja lisasi tonylle.md:hen lahes saman kohdan
# kymmenia kertoja. Tauko: tyhjan kierroksen jalkeen uutta suunnittelua ei ajeta ennen kuin tauko on kulunut tai uusi tulos on tullut.
SUUNNITTELU_TAUKO_S = 3 * 3600
SUUNNITTELU_TILA = "suunnittelu-tila.json"
OLETUS_AIKARAJA_S = 45 * 60
OLETUS_MAX_KIERROKSET = 60
# Tonyn paatos 4.11.2026: Opus-tehtaville pidempi raja ja enemman kierroksia (suunnittelija pilkkoo yli 2 h tyot).
OPUS_AIKARAJA_S = 90 * 60
OPUS_MAX_KIERROKSET = 120


def aikaraja_s(env, malli=None):
    """Lapsiprosessin aikaraja. Opus: CLAUDEJONO_AIKARAJA_OPUS_S > CLAUDEJONO_AIKARAJA_S > 90 min; muut: CLAUDEJONO_AIKARAJA_S > 45 min."""
    if malli == "opus":
        return int(env.get("CLAUDEJONO_AIKARAJA_OPUS_S") or env.get("CLAUDEJONO_AIKARAJA_S") or OPUS_AIKARAJA_S)
    return int(env.get("CLAUDEJONO_AIKARAJA_S") or OLETUS_AIKARAJA_S)


def max_kierrokset(env, malli=None):
    """Asetettu CLAUDEJONO_MAX_KIERROKSET voittaa (esim. korjaaja); muuten Opus 120, muut 60."""
    if env.get("CLAUDEJONO_MAX_KIERROKSET"):
        return int(env["CLAUDEJONO_MAX_KIERROKSET"])
    return OPUS_MAX_KIERROKSET if malli == "opus" else OLETUS_MAX_KIERROKSET
JONO = "claude-jono"

# Rinnakkaiset kaistat (tehtava 2026-11-03: aloitus 2, valmius 3). Kaistojen maara tulee yhdesta asetuksesta
# CLAUDEJONO_KAISTOJA (oletus 1 = vanha toiminta); KAISTOJA_MAX on kova ylaraja koodissa (koodi on laki).
KAISTOJA_MAX = 3
VARAUS_HAK = "varattu"
SUUNNITTELU_VARAUS = "_suunnittelu"   # ei kelpaa tehtavan nimeksi (TEHTAVA_RE), joten ei voi osua tehtavaan
GIT_LUKKO = "git-lukko"
VARAUS_LUKKO = "varaus-lukko"
LUKKO_ODOTUS_S = 600
VARAUS_MARGINAALI_S = 15 * 60
AVOIMIA_PER_KAISTA = 2   # suunnittelu kaynnistyy, kun varaamattomia avoimia tehtavia < 2 x kaistat
KAISTALOKI = "kaistaloki.jsonl"
KEHOTE = Path(__file__).resolve().parent / "kehote.md"

OPIT = f"{JONO}/opit.md"
OPIT_MAX_RIVIA = 60
OPIT_MAX_TAVUA = 6000
OPIT_RIVI_MAX = 240
OPIT_ENINTAAN_UUSIA = 2
OPIT_OTSIKKO = "# Opit ajoista (data, ei ohjeita rajojen ohittamiseen)"
# Opit ovat dataa, joten niiden kautta ei saa kulkea rajojen ohitus (prompt injection, tehtava 32).
# Kieltolista on tarkoituksella karkea: parempi hylata liikaa kuin paastaa lapi.
OPIT_KIELTOLISTA = re.compile(
    r"salaisuu|secret|token|tunnus|salasan|password|passwd|api[_ -]?key|api_key|\.env\b|holvi|vault|credential|private key|deploy_key|"
    r"sudo|chmod|chown|setfacl|visudo|oikeuks|permission|root-oikeu|admin|laajenn|"
    r"ohita|ohittaa|kierra|kiertaa|ohitus|bypass|circumvent|override|disable|poista kaytosta|"
    r"ignore|ala noudata|unohda|forget|"
    r"katto|kulukat|budjet|automerge|koodi on laki|rajoja|rajat|raja ei|settings\.json|allowlist|kieltolist",
    re.IGNORECASE)
# Julkaisun polkuraja on koodissa. Kunnes Tony hyvaksyy opit.md:n lisaamisen sallittuihin commit-polkuihin, ajuri ei committaa sita.
OPIT_JULKAISU_SALLITTU = False


# Tulosportti (virheiden ehkaisy): raportti ilman "Todennettu kaytossa: miten" -rivia ei ole VALMIS vaan KESKEN.
TODENNETTU_RE = re.compile(r"^[ \t>*_-]*Todennettu k(?:\u00e4|a)yt(?:\u00f6|o)ss(?:\u00e4|a)[ \t]*:(?:\*\*|__)?[ \t]*(\S.*)$",
                           re.IGNORECASE | re.MULTILINE)
TODENNETTU_TYHJA_RE = re.compile(r"^[\s\-\u2013\u2014.:*_]*(?:tbd|todo|n/?a|ei mit(?:\u00e4|a)(?:\u00e4|a)n|ei ole)?[\s\-\u2013\u2014.:*_]*$", re.IGNORECASE)


def on_todennettu(teksti):
    """True, jos raportissa on rivi 'Todennettu kaytossa:' jolla on muu arvo kuin tyhja tai viiva."""
    return any(not TODENNETTU_TYHJA_RE.match(m.group(1)) for m in TODENNETTU_RE.finditer(teksti or ""))


class JonoVirhe(RuntimeError):
    pass


def kelvollinen_nimi(nimi):
    return bool(TEHTAVA_RE.match(nimi))


def seuraava_tehtava(repo):
    """Vanhin (nimen mukaan) kelvollinen tehtava, jolla ei ole tulosta. None jos jono on tyhja."""
    teht = Path(repo) / JONO / "tehtavat"
    tul = Path(repo) / JONO / "tulokset"
    if not teht.is_dir():
        return None
    for p in sorted(teht.iterdir()):
        if p.is_file() and kelvollinen_nimi(p.name) and not (tul / p.name).exists():
            return p
    return None


def kaistojen_maara(env):
    """Kaistojen lukumaara yhdesta asetuksesta CLAUDEJONO_KAISTOJA, rajattuna valille 1..KAISTOJA_MAX. Virheellinen -> 1."""
    try:
        n = int(env.get("CLAUDEJONO_KAISTOJA", "1"))
    except (TypeError, ValueError):
        return 1
    return max(1, min(KAISTOJA_MAX, n))


def tarkista_kaista(kaista, env):
    if not isinstance(kaista, int) or not 1 <= kaista <= kaistojen_maara(env):
        raise JonoVirhe(f"kaista {kaista} ei kelpaa (sallittu 1..{kaistojen_maara(env)}, asetus CLAUDEJONO_KAISTOJA)")
    return kaista


_PAIKALLINEN = threading.local()


@contextlib.contextmanager
def lukko(polku, odotus_s=LUKKO_ODOTUS_S):
    """Odottava flock-lukko. Uudelleenkaytettava saman saikeen sisalla (sisakkainen kutsu ei jumita). Antaa odotusajan sekunteina."""
    avoimet = _PAIKALLINEN.__dict__.setdefault("avoimet", {})
    avain = str(polku)
    if avain in avoimet:
        avoimet[avain] += 1
        try:
            yield 0.0
        finally:
            avoimet[avain] -= 1
        return
    Path(polku).parent.mkdir(parents=True, exist_ok=True)
    alku = time.monotonic()
    with open(polku, "a") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - alku > odotus_s:
                    raise JonoVirhe(f"lukkoa ei saatu {odotus_s} s kuluessa: {Path(polku).name}")
                time.sleep(0.05)
        avoimet[avain] = 1
        try:
            yield time.monotonic() - alku
        finally:
            del avoimet[avain]


def git_lukko(tila):
    """Yhteinen lyhyt lukko kaikille docs-kloonin git-toimille (pull, add, commit, push). Claude-ajon ajaksi sita EI pideta."""
    return lukko(Path(tila) / GIT_LUKKO)


def varaus_vanhenee_s(env):
    """Varaus vanhenee vasta, kun Clauden aikaraja + marginaali on ylitetty: eheasti kaynnissa oleva ajo ei meneta varaustaan."""
    try:
        return int(env.get("CLAUDEJONO_VARAUS_S") or max(aikaraja_s(env), aikaraja_s(env, "opus")) + VARAUS_MARGINAALI_S)
    except (TypeError, ValueError):
        return OLETUS_AIKARAJA_S + VARAUS_MARGINAALI_S


def _pid_elossa(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:     # PermissionError: prosessi on olemassa
        return True
    return True


def _varaus_elossa(polku, nyt, vanhenee_s):
    try:
        mtime = polku.stat().st_mtime
    except FileNotFoundError:
        return False
    if nyt - mtime > vanhenee_s:
        return False
    try:
        pid = json.loads(polku.read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        return True     # juuri luotu, sisaltoa ei viela kirjoitettu
    return not (isinstance(pid, int) and not _pid_elossa(pid))


def varaa(tila, nimi, kaista, nyt=None, vanhenee_s=OLETUS_AIKARAJA_S + VARAUS_MARGINAALI_S):
    """Atominen varaus: tiedosto varattu/<nimi> luodaan O_EXCL:lla. True = varaus saatiin, False = toinen kaista omistaa sen.
    Vanhentunut (aikaraja tai kuollut prosessi) varaus otetaan yli. Koko toimi on oman lukkonsa sisalla, jotta kaksi ylitystyontoa ei poista toistensa varauksia."""
    nyt = time.time() if nyt is None else nyt
    polku = Path(tila) / VARAUS_HAK / nimi
    with lukko(Path(tila) / VARAUS_LUKKO):
        polku.parent.mkdir(parents=True, exist_ok=True)
        if polku.exists():
            if _varaus_elossa(polku, nyt, vanhenee_s):
                return False
            polku.unlink(missing_ok=True)
        try:
            fd = os.open(polku, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            json.dump({"kaista": kaista, "pid": os.getpid(), "aika": nyt, "avain": uuid.uuid4().hex}, f)
        return True


def vapauta(tila, nimi, kaista):
    """Vapauttaa vain oman varauksen (kaista + pid tasmaa). Jos varaus ehti vanhentua ja siirtyi toiselle, sita ei kosketa."""
    polku = Path(tila) / VARAUS_HAK / nimi
    with lukko(Path(tila) / VARAUS_LUKKO):
        try:
            d = json.loads(polku.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if d.get("kaista") != kaista or d.get("pid") != os.getpid():
            return False
        polku.unlink(missing_ok=True)
        return True


def avoimet_tehtavat(repo, tila, nyt=None, vanhenee_s=OLETUS_AIKARAJA_S + VARAUS_MARGINAALI_S):
    """Avointen tehtavien maara: kelvollinen, ei tulosta eika toisen kaistan varausta."""
    teht = Path(repo) / JONO / "tehtavat"
    tul = Path(repo) / JONO / "tulokset"
    if not teht.is_dir():
        return 0
    varatut = set(aktiiviset_varaukset(tila, nyt, vanhenee_s))
    return sum(1 for p in teht.iterdir()
               if p.is_file() and kelvollinen_nimi(p.name) and not (tul / p.name).exists() and p.name not in varatut)


def suunnittelu_tarpeen(repo, tila, env, nyt=None, vanhenee_s=OLETUS_AIKARAJA_S + VARAUS_MARGINAALI_S):
    """Ennakoiva suunnittelu: sallittu, kun varaamattomia avoimia tehtavia on alle AVOIMIA_PER_KAISTA x kaistat.
    Muiden kaistojen tyo ei esta suunnittelua; yksi suunnittelija kerrallaan varmistetaan varauksella SUUNNITTELU_VARAUS."""
    return avoimet_tehtavat(repo, tila, nyt, vanhenee_s) < AVOIMIA_PER_KAISTA * kaistojen_maara(env)


def aktiiviset_varaukset(tila, nyt=None, vanhenee_s=OLETUS_AIKARAJA_S + VARAUS_MARGINAALI_S):
    nyt = time.time() if nyt is None else nyt
    hak = Path(tila) / VARAUS_HAK
    if not hak.is_dir():
        return []
    return sorted(p.name for p in hak.iterdir() if p.is_file() and _varaus_elossa(p, nyt, vanhenee_s))


KAISTARIVI_RE = re.compile(r"^Kaista:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
KAISTARIVI_LUKU_MAX = 20000   # tavua; rivi luetaan vain tiedoston alusta


def tehtavan_kaista(polku, kaistoja=KAISTOJA_MAX):
    """Tehtavan `Kaista: N` -rivi. Palauttaa kaistanumeron tai None (vapaa tehtava).
    Rivi on dataa: puuttuva, lukukelvoton tai kaistojen maaran ulkopuolinen arvo (esim. Kaista: 3 kun kaistoja on 2)
    tulkitaan vapaaksi, jotta kiinnitys ei jaa ikuisesti ottajaa vaille. Ensimmainen rivi ratkaisee."""
    try:
        with open(polku, "rb") as f:
            teksti = f.read(KAISTARIVI_LUKU_MAX).decode("utf-8", "replace")
    except OSError:
        return None
    m = KAISTARIVI_RE.search(teksti)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= min(kaistoja, KAISTOJA_MAX) else None


def odottavat_tehtavat(repo):
    """Kaikki kelvolliset tehtavat ilman tulosta nimen mukaisessa jarjestyksessa."""
    teht = Path(repo) / JONO / "tehtavat"
    tul = Path(repo) / JONO / "tulokset"
    if not teht.is_dir():
        return []
    return [p for p in sorted(teht.iterdir()) if p.is_file() and kelvollinen_nimi(p.name) and not (tul / p.name).exists()]


def varaa_seuraava(repo, tila, kaista, nyt=None, vanhenee_s=OLETUS_AIKARAJA_S + VARAUS_MARGINAALI_S, kaistoja=KAISTOJA_MAX, ohita=None):
    """Valinta: 1) kaistalle kiinnitetyt (`Kaista: N`) vanhin ensin, 2) vapaat vanhin ensin. Toisen kaistan kiinnittamiin ei
    kosketa eika toisen kaistan varaamiin. Varaa valitun. None jos ei ole.
    ohita(polku) -> True ohittaa tehtavan (esim. Claude-reitti, kun Tonyn varaus tai kayttoraja estaa sen)."""
    odottavat = [(p, tehtavan_kaista(p, kaistoja)) for p in odottavat_tehtavat(repo)]
    for omat in (True, False):
        for p, k in odottavat:
            if ((k == kaista) if omat else (k is None)) and not (ohita and ohita(p)):
                if varaa(tila, p.name, kaista, nyt, vanhenee_s):
                    return p
    return None


def lue_opit(repo):
    """opit.md:n sisalto kehotteeseen, rajattuna kokoon. Puuttuva tiedosto = ei opittua."""
    p = Path(repo) / OPIT
    if not p.is_file():
        return ""
    return p.read_bytes()[:OPIT_MAX_TAVUA].decode("utf-8", "ignore")


def kehotteen_opit(repo, tehtava_nimi="", tehtava_teksti=""):
    """<opit>-lohkon sisalto: opit.md ja sen perassa enintaan virheopit.ENINTAAN tehtavaan liittyvaa virhelokin merkintaa
    (saanto ja ehkaisy). Sama lohko ja sama kieltolista kuin opeilla: rivit ovat dataa eivatka anna lupaa ohittaa rajoja."""
    osat = [lue_opit(repo).strip()]
    try:
        osat.append(virheopit.kehoteosa(repo, tehtava_nimi, tehtava_teksti, sallittu=lambda rivi: not OPIT_KIELTOLISTA.search(rivi)))
    except (OSError, ValueError):
        pass   # virheloki ei saa kaataa ajoa; opit.md kulkee silti
    return "\n\n".join(o for o in osat if o)


def kaistahuomautus(kaista):
    """Kaistalla 2..3 agentti tyoskentelee omassa alihakemistossaan, jotta kaksi ajoa ei kayta samaa kloonia.
    Polku pysyy /opt/agentscope/tyo:n alla, joten PR-tyokalun polkuraja ei muutu."""
    if kaista <= 1:
        return ""
    h = f"/opt/agentscope/tyo/k{kaista}"
    return (f"Rinnakkaiskaista {kaista}: toinen ajo voi olla kaynnissa samaan aikaan. Tee kloonit hakemistoon {h}/<repo> "
            f"(tama korvaa ylla olevan /opt/agentscope/tyo/<repo>), PR-kutsun hakemisto on {h}/<repo> ja kuvaustiedosto {h}/<kuvaus>.md. "
            "Ala koske muiden kaistojen hakemistoihin (/opt/agentscope/tyo/k*/ ja suoraan /opt/agentscope/tyo/<repo>).")


def rakenna_kehote(tehtava_polku, kehote_polku=KEHOTE, opit="", kaista=1):
    data = Path(tehtava_polku).read_bytes()
    if len(data) > TEHTAVA_MAX_TAVUA:
        raise JonoVirhe(f"tehtava yli {TEHTAVA_MAX_TAVUA} tavua")
    osat = [Path(kehote_polku).read_text(encoding="utf-8")]
    if kaistahuomautus(kaista):
        osat.append(kaistahuomautus(kaista))
    if opit.strip():
        osat.append("<opit>\n" + opit.strip().replace("</opit>", "") + "\n</opit>")
    osat.append("<tehtava>\n" + data.decode("utf-8") + "\n</tehtava>\n")
    return "\n\n".join(osat)


def poimi_uusi_oppi(teksti):
    """Rivit viimeisesta '## Uusi oppi' -osiosta (enintaan OPIT_ENINTAAN_UUSIA). Tyhja lista jos osiota ei ole."""
    kohdat = [m for m in re.finditer(r"^##[ \t]+Uusi oppi[ \t]*$", teksti or "", re.MULTILINE)]
    if not kohdat:
        return []
    loppu = teksti[kohdat[-1].end():]
    rivit = []
    for rivi in loppu.splitlines():
        if rivi.startswith("#"):
            break
        rivi = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", rivi).strip()
        if rivi:
            rivit.append(rivi)
    return rivit[:OPIT_ENINTAAN_UUSIA]


def suodata_opit(rivit):
    """(hyvaksytyt, hylatyt). Hylataan kieltolistan osumat, liian pitkat, monirivisyyteen viittaavat ja ei-ASCII-ohjausmerkit."""
    hyva, huono = [], []
    for r in rivit:
        r = r.strip()
        if (not r or len(r) > OPIT_RIVI_MAX or OPIT_KIELTOLISTA.search(r) or "<" in r or "`" in r
                or any(ord(c) < 32 for c in r)):
            huono.append(r)
        else:
            hyva.append(r)
    return hyva, huono


def paivita_opit(vanha, uudet, nyt=None):
    """Lisaa paivatyt rivit ja pitaa tiedoston enintaan OPIT_MAX_RIVIA rivin mittaisena.
    Ylimaarat: vanhimmat opit tiivistetaan yhdeksi riviksi (alut ';'-erotettuna)."""
    pvm = (nyt or datetime.now()).strftime("%Y-%m-%d")
    rivit = [r for r in (vanha or "").splitlines() if r.startswith("- ")]
    rivit += [f"- {pvm}: {u}" for u in uudet]
    if len(rivit) > OPIT_MAX_RIVIA - 2:
        ylim = len(rivit) - (OPIT_MAX_RIVIA - 2) + 1
        vanhimmat, rivit = rivit[:ylim], rivit[ylim:]
        alut = "; ".join(re.sub(r"^- \d{4}-\d{2}-\d{2}: ", "", r)[:40] for r in vanhimmat)
        rivit.insert(0, f"- tiivistetty ({len(vanhimmat)} vanhaa): {alut}"[:OPIT_RIVI_MAX])
    return OPIT_OTSIKKO + "\n\n" + "\n".join(rivit) + "\n"


def julkaise_opit(repo, teksti):
    """Kirjoittaa ja committaa VAIN opit.md:n. Pois kaytosta kunnes OPIT_JULKAISU_SALLITTU on hyvaksytty."""
    if not OPIT_JULKAISU_SALLITTU:
        return False
    with git_lukko(Path(repo).parent):
        (Path(repo) / OPIT).write_text(teksti, encoding="utf-8")
        git(repo, "add", "--", OPIT)
        if [r for r in git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if r] != [OPIT]:
            git(repo, "reset", "-q", check=False)
            raise JonoVirhe("indeksissa muuta kuin opit.md")
        git(repo, "commit", "-q", "-m", "claude-jono: opit")
        return _tyonna(repo)


def claude_komento(kehote, env, malli=None):
    """malli = opus | sonnet (claudejono.malli); None = Clauden oma oletus (vanha toiminta)."""
    kom = [env.get("CLAUDEJONO_CLAUDE", "claude"), "-p", kehote, "--output-format", "json",
           "--max-turns", str(max_kierrokset(env, malli))]
    if malli:
        if malli not in mallit.CLAUDE_MALLIT:
            raise JonoVirhe("claude-malli ei kelpaa")
        kom += ["--model", malli]
    if env.get("CLAUDEJONO_LUPATILA"):
        kom += ["--permission-mode", env["CLAUDEJONO_LUPATILA"]]
    return kom


RUNUSER = "/usr/sbin/runuser"


def kaistan_tyohak(tyohak, kaista=1):
    """Kaista 1: <tyohak>/tyo (vanha). Kaista 2..3: <tyohak>/tyo/k<N>, oma klooni-alue; sama /opt/agentscope/tyo -juuri kuin PR-tyokalulla."""
    return Path(tyohak) / "tyo" / (f"k{kaista}" if kaista > 1 else "")


def lapsen_ajo(kehote, env, tyohak, kaista=1, malli=None, lisaliput=()):
    """(komento, ymparisto, cwd) Claude-lapsiprosessille.

    Jos CLAUDEJONO_KAYTTAJA on asetettu (Tonyn paatos 30.10.2026), ajuri jaa rootiksi (git, deploy key, Telegram), mutta agentti
    ajetaan tavallisena kayttajana runuserilla: se ei nae deploy keyta, botin tunnusta, yojonon kattoja eika salaisuuksia, eika
    saa rootin ymparistoa. Ilman muuttujaa toiminta on ennallaan."""
    kom = claude_komento(kehote, env, malli) + list(lisaliput)
    kayttaja = env.get("CLAUDEJONO_KAYTTAJA", "")
    if not kayttaja:
        alaenv = {k: v for k, v in env.items() if not k.startswith(("YOJONO_TELEGRAM_", "CLAUDEJONO_GIT", "CLAUDEJONO_LITELLM", "GIT_SSH"))}
        return kom, alaenv, str(tyohak if kaista <= 1 else kaistan_tyohak(tyohak, kaista))
    if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", kayttaja) or kayttaja == "root":
        raise JonoVirhe("CLAUDEJONO_KAYTTAJA ei kelpaa")
    koti = env.get("CLAUDEJONO_KOTI", f"/var/lib/{kayttaja}")
    alaenv = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": koti, "USER": kayttaja, "LOGNAME": kayttaja,
              "LANG": env.get("LANG", "C.UTF-8")}
    # Absoluuttinen polku: Popen etsii komennon lapsen PATHista, jossa ei ole /usr/sbin:ia (vika 30.10.2026).
    return [RUNUSER, "-u", kayttaja, "--", *kom], alaenv, str(kaistan_tyohak(tyohak, kaista))


def luo_kaistan_tyohak(tyohak, kaista, env):
    """Luo tyohakemiston (kaista 1: <tyohak>/tyo). Kaistojen 2..3 alihakemisto annetaan agenttikayttajalle, jotta se voi kirjoittaa siihen."""
    h = kaistan_tyohak(tyohak, kaista)
    h.mkdir(parents=True, exist_ok=True)
    kayttaja = env.get("CLAUDEJONO_KAYTTAJA", "")
    if kaista > 1 and kayttaja:
        try:
            shutil.chown(h, user=kayttaja)
        except (OSError, LookupError):
            pass
    return h


def tulkitse(stdout, koodi):
    """(ok, teksti) claude -p --output-format json -tulosteesta. Lukukelvoton tuloste = epaonnistui."""
    try:
        d = json.loads(stdout)
    except (ValueError, TypeError):
        return False, f"lukukelvoton tuloste (koodi {koodi})"
    teksti = d.get("result") if isinstance(d.get("result"), str) else ""
    ok = koodi == 0 and d.get("is_error") is not True and d.get("subtype", "success") == "success"
    if not ok and not teksti:
        teksti = f"claude epaonnistui (subtype: {d.get('subtype', '?')})"
    return ok, teksti


def kirjoita_tulos(repo, nimi, ok, teksti, nyt=None, tila=None, malli=None):
    nyt = nyt or datetime.now().astimezone()
    polku = Path(repo) / JONO / "tulokset" / nimi
    polku.parent.mkdir(parents=True, exist_ok=True)
    tavut = teksti.encode("utf-8")
    runko = tavut[:TULOS_MAX_TAVUA].decode("utf-8", "ignore")
    if len(tavut) > TULOS_MAX_TAVUA:   # 1.11.: M02-lahde katkesi kesken sanan ilman merkintaa
        runko += (f"\n\n[AJURI: raportti katkaistu {TULOS_MAX_TAVUA} tavuun, alkuperainen {len(tavut)} tavua. "
                  "Loppu puuttuu tasta tiedostosta.]")
    polku.write_text(f"# Tulos: {nimi}\n\n- Tila: {tila or ('VALMIS' if ok else 'EPAONNISTUI')}\n- Aika: {nyt.isoformat(timespec='minutes')}\n{('- Malli: ' + malli + chr(10)) if malli else ''}\n{runko}\n",
                     encoding="utf-8")
    return polku


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False, timeout=300)
    if check and r.returncode != 0:
        raise JonoVirhe(f"git {args[0]} epaonnistui ({r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}")
    return r


AUTO_MAX_PAIVA = 30     # Tonyn paatos 30.10.2026: itse keksittyja tehtavia enintaan 30 / vrk (nostettu 6 -> 30; koodi on laki)
AUTO_PER_AJO = 2
AUTO_NIMI_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
TONYLLE = "tonylle.md"
SUUNNITTELU = Path(__file__).resolve().parent / "suunnittelu.md"


def sallittu_polku(suht):
    """Ajuri saa committaa vain: tulokset/<nimi>.md, tehtavat/<pvm>-NN-auto-*.md, tonylle.md ja vapaat docs-polut (vapaa_docs_polku)."""
    osat = suht.split("/")
    if len(osat) == 3 and osat[0] == JONO and osat[1] == "tulokset" and kelvollinen_nimi(osat[2]):
        return True
    if len(osat) == 3 and osat[0] == JONO and osat[1] == "tehtavat" and kelvollinen_nimi(osat[2]) and "-auto-" in osat[2]:
        return True
    return suht == f"{JONO}/{TONYLLE}" or vapaa_docs_polku(suht)


# Docs-repon vapaa kirjoitus (Tonyn paatos 4.11.2026). Agentti kirjoittaa tiedostot kaistan tyohakemiston kansioon docs-ulos/
# docs-repon polkuineen; ajuri lukee ne agenttikayttajana tar-virtana (root ei seuraa agentin symlinkkeja) ja committaa
# tuloksen kanssa. Ei: tehtavat/, tulokset/, isanta-kasin/ (Tony ajaa kasin), kompassi.md (Tonyn paatokset), opit.md,
# juuren tiedostot, piilotiedostot; vain tekstipaatteet ja kokoraja.
DOCS_ULOS = "docs-ulos"
DOCS_MAX_TIEDOSTOJA = 20
DOCS_MAX_TAVUA = 200_000
DOCS_PAATTEET = (".md", ".txt", ".csv", ".json")
DOCS_NIMI_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


def vapaa_docs_polku(suht):
    osat = suht.split("/")
    if len(osat) < 2 or any(o in ("", ".", "..") or o.startswith(".") for o in osat) or not DOCS_NIMI_RE.match(suht):
        return False
    if not suht.endswith(DOCS_PAATTEET):
        return False
    kielletyt = (f"{JONO}/tehtavat/", f"{JONO}/tulokset/", f"{JONO}/isanta-kasin/", f"{JONO}/korjaukset/", f"{JONO}/kompassi.md",
                 OPIT, f"{JONO}/sunnuntaiehdotukset.md")
    return not any(suht == k or suht.startswith(k) for k in kielletyt)


def valmistele_docs_ulos(kaista_hak, env):
    """Tyhja docs-ulos/ ennen ajoa (vanha sisalto pois), agenttikayttajan omistukseen."""
    ulos = Path(kaista_hak) / DOCS_ULOS
    shutil.rmtree(ulos, ignore_errors=True)
    ulos.mkdir(parents=True, exist_ok=True)
    kayttaja = env.get("CLAUDEJONO_KAYTTAJA", "")
    if kayttaja:
        try:
            shutil.chown(ulos, user=kayttaja)
        except (OSError, LookupError):
            pass
    return ulos


def poimi_docs_ulos(kaista_hak, repo, env, suorita=subprocess.run, hylatyt=None):
    """Kopioi docs-ulos/:n sallitut tiedostot docs-repoon. Palauttaa kopioidut polut (repossa). Virheet: tyhja lista.
    Virhelokin uusi merkinta ilman pakollisia kenttia hylataan: tiedostoa ei kopioida ja syy lisataan listaan `hylatyt`."""
    ulos = Path(kaista_hak) / DOCS_ULOS
    if not ulos.is_dir():
        return []
    kom = ["tar", "-C", str(ulos), "-cf", "-", "."]
    kayttaja = env.get("CLAUDEJONO_KAYTTAJA", "")
    if kayttaja:
        kom = [RUNUSER, "-u", kayttaja, "--"] + kom
    try:
        r = suorita(kom, capture_output=True, timeout=60, check=False)
        if r.returncode != 0 or len(r.stdout) > DOCS_MAX_TIEDOSTOJA * DOCS_MAX_TAVUA * 2:
            return []
        kopioidut = []
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
            for m in tf.getmembers():
                suht = posixpath.normpath(m.name)
                if not m.isreg() or m.size > DOCS_MAX_TAVUA or not vapaa_docs_polku(suht):
                    continue
                data = tf.extractfile(m).read()
                try:
                    teksti = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if suht == virheopit.LOKI:
                    vialliset = virheopit.tarkista(teksti)
                    if vialliset:
                        if hylatyt is not None:
                            hylatyt.append((suht, virheopit.selite(vialliset)))
                        continue
                kohde = Path(repo) / suht
                kohde.parent.mkdir(parents=True, exist_ok=True)
                if kohde.is_symlink():
                    continue
                kohde.write_bytes(data)
                kopioidut.append(kohde)
                if len(kopioidut) >= DOCS_MAX_TIEDOSTOJA:
                    break
        return kopioidut
    except (OSError, subprocess.SubprocessError, tarfile.TarError):
        return []
    finally:
        shutil.rmtree(ulos, ignore_errors=True)


def _tyonna(repo):
    """Push, ja torman (toinen kirjoittaja ehti ensin) jalkeen pull --rebase + uusinta. Rebase-konflikti perutaan, ettei klooni jaa kesken."""
    for _ in range(3):
        if git(repo, "push", "-q", "origin", "HEAD", check=False).returncode == 0:
            return True
        if git(repo, "pull", "-q", "--rebase", "origin", "main", check=False).returncode != 0:
            git(repo, "rebase", "--abort", check=False)
    return False


def julkaise(repo, polut, viesti=None):
    """Committaa ja pushaa VAIN annetut sallitut polut. Jos indeksissa on jotain muuta, ei tehda mitaan.
    Koko vaihe tehdaan yhteisen git-lukon sisalla, jotta rinnakkaiset kaistat eivat kayta indeksia yhta aikaa."""
    if not isinstance(polut, (list, tuple)):
        polut = [polut]
    suhteet = [str(Path(p).relative_to(repo)) for p in polut]
    for suht in suhteet:
        if not sallittu_polku(suht):
            raise JonoVirhe(f"polkua ei saa committaa: {suht}")
    with git_lukko(Path(repo).parent):
        git(repo, "add", "--", *suhteet)
        staged = sorted(r for r in git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if r)
        if not staged or not set(staged) <= set(suhteet):
            git(repo, "reset", "-q", check=False)
            raise JonoVirhe(f"indeksissa muuta kuin sallitut polut: {staged[:5]}")
        git(repo, "commit", "-q", "-m", viesti or f"claude-jono: tulos {Path(polut[0]).name}")
        return _tyonna(repo)


def auto_tanaan(repo, pvm):
    teht = Path(repo) / JONO / "tehtavat"
    return sum(1 for p in teht.glob(f"{pvm}-*-auto-*.md")) if teht.is_dir() else 0


def seuraava_numero(repo, pvm):
    teht = Path(repo) / JONO / "tehtavat"
    nrot = [int(p.name[11:13]) for p in teht.glob(f"{pvm}-[0-9][0-9]-*.md")] if teht.is_dir() else []
    return max(nrot, default=0) + 1


def jasenna_suunnitelma(teksti):
    """Mallin loppuvastauksesta JSON-olio {"tehtavat": [{"nimi", "sisalto"}], "tonylle": "..."}. Virheellinen -> None."""
    try:
        alku, loppu = teksti.index("{"), teksti.rindex("}")
        d = json.loads(teksti[alku:loppu + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("tehtavat", []), list):
        return None
    ok = []
    for t in d.get("tehtavat", []):
        if (isinstance(t, dict) and isinstance(t.get("nimi"), str) and AUTO_NIMI_RE.match(t["nimi"])
                and isinstance(t.get("sisalto"), str) and 0 < len(t["sisalto"].encode("utf-8")) <= TEHTAVA_MAX_TAVUA):
            ok.append(t)
    tonylle = d.get("tonylle") if isinstance(d.get("tonylle"), str) else ""
    return {"tehtavat": ok, "tonylle": tonylle[:3000]}


def tulosten_maara(repo):
    hak = Path(repo) / JONO / "tulokset"
    return sum(1 for _ in hak.glob("*.md")) if hak.is_dir() else 0


def suunnittelu_tauolla(tyohak, repo, nyt, tauko_s):
    """True, jos edellinen suunnittelu ei tuottanut tehtavia, tauko ei ole kulunut eika uusia tuloksia ole tullut."""
    try:
        d = json.loads((Path(tyohak) / SUUNNITTELU_TILA).read_text(encoding="utf-8"))
        aika, tehtavia, tuloksia = float(d["aika"]), int(d["tehtavia"]), int(d["tuloksia"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return tehtavia == 0 and 0 <= nyt.timestamp() - aika < tauko_s and tuloksia == tulosten_maara(repo)


def kirjaa_suunnittelu(tyohak, repo, nyt, tehtavia):
    try:
        (Path(tyohak) / SUUNNITTELU_TILA).write_text(
            json.dumps({"aika": nyt.timestamp(), "tehtavia": tehtavia, "tuloksia": tulosten_maara(repo)}), encoding="utf-8")
    except OSError:
        pass


RAJA_RE = re.compile(r"usage limit|limit reached|rate.?limit|too many requests|\b429\b|overloaded|quota|hit your limit", re.IGNORECASE)


def mittarit(stdout):
    """Claude-ajon luvut tulosteesta (arvio tilauksen kulutuksesta): kierrokset ja API-kustannuksen arvo. Lukukelvoton -> tyhja."""
    try:
        d = json.loads(stdout)
    except (ValueError, TypeError):
        return {}
    if not isinstance(d, dict):
        return {}
    ulos = {}
    for avain, nimi in (("num_turns", "kierrokset"), ("total_cost_usd", "arvo_usd"), ("duration_api_ms", "api_ms")):
        if isinstance(d.get(avain), (int, float)) and not isinstance(d.get(avain), bool):
            ulos[nimi] = d[avain]
    return ulos


def kirjaa_kaistaloki(tila, kaista, laji, nimi, ok, kesto_s, teksti="", stdout="", pushattu=None, lukko_odotus_s=0.0, nyt=None, malli=""):
    """Yksi JSON-rivi per ajo (kaistakoe, doc 71 / 2026-11-03). Rajaan osuminen on arvaus: tekstista haetaan tunnetut ilmaukset.
    Kirjausvirhe ei saa kaataa ajoa."""
    nyt = nyt or datetime.now().astimezone()
    rivi = {"aika": nyt.isoformat(timespec="seconds"), "kaista": kaista, "laji": laji, "tehtava": nimi, "ok": bool(ok),
            "kesto_s": round(kesto_s, 1), "raja": bool(not ok and RAJA_RE.search(teksti or "")),
            "lukko_odotus_s": round(lukko_odotus_s, 2), **mittarit(stdout)}
    if malli:
        rivi["malli"] = malli
    if pushattu is not None:
        rivi["push"] = bool(pushattu)
    try:
        fd = os.open(Path(tila) / KAISTALOKI, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(rivi) + "\n")
    except OSError:
        pass


PORTTI = None   # valinnainen porttitarkistin: callable(sisalto) -> None (ok) tai kieltoperuste (str). Asetetaan kun tarkistin on paaosassa.


def _normalisoi(teksti):
    return " ".join(re.sub(r"<!--.*?-->", "", teksti, flags=re.S).lower().split())


def tehtavan_slug(tiedostonimi):
    """'2026-11-03-02-auto-nimi.md' -> 'nimi' (paivays, numero ja auto-etuliite pois)."""
    nimi = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{2}-", "", tiedostonimi[:-3] if tiedostonimi.endswith(".md") else tiedostonimi)
    return nimi[5:] if nimi.startswith("auto-") else nimi


def suodata_uudet(repo, ehdotukset, portti=None):
    """Hylkaa ehdotukset, jotka ovat jo jonossa tai tehty (sama nimi tai sama sisalto) tai joita porttitarkistin kieltaa.
    Palauttaa (hyvaksytyt, hylatyt: [(nimi, syy)]). Vertailu kattaa kaikki tehtavat/-tiedostot (myos tehdyt) seka saman erran ehdotukset."""
    teht = Path(repo) / JONO / "tehtavat"
    nimet, sisallot = set(), set()
    if teht.is_dir():
        for p in teht.iterdir():
            if p.is_file() and kelvollinen_nimi(p.name):
                nimet.add(tehtavan_slug(p.name))
                try:
                    sisallot.add(_normalisoi(p.read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    pass
    ok, hylatyt = [], []
    for t in ehdotukset:
        slug, sis = t["nimi"], _normalisoi(t["sisalto"])
        if slug in nimet:
            hylatyt.append((slug, "nimi on jo jonossa tai tehty"))
        elif sis in sisallot:
            hylatyt.append((slug, "sama sisalto on jo jonossa tai tehty"))
        else:
            syy = portti(t["sisalto"]) if portti else None
            if syy:
                hylatyt.append((slug, f"porttitarkistin: {syy}"))
                continue
            ok.append(t)
            nimet.add(slug)
            sisallot.add(sis)
    return ok, hylatyt


def suunnittele(repo, tyohak, env, suorita, laheta=None, nyt=None, kaista=1):
    """Jono tyhja: Claude ehdottaa jatkotehtavat. Ajuri nimeaa, rajaa (AUTO_MAX_PAIVA, AUTO_PER_AJO) ja committaa ne."""
    nyt = nyt or datetime.now().astimezone()
    pvm = nyt.date().isoformat()
    pysaytys = silmukka.pysaytetty(tyohak)
    if pysaytys:
        return f"itsesuunnittelu pysaytetty (laatuotos): {pysaytys}"
    tilaa = min(AUTO_PER_AJO, AUTO_MAX_PAIVA - auto_tanaan(repo, pvm))
    if tilaa <= 0:
        return "jono tyhja (itsesuunnittelun paivaraja taynna)"
    if suunnittelu_tauolla(tyohak, repo, nyt, int(env.get("CLAUDEJONO_SUUNNITTELU_TAUKO_S", SUUNNITTELU_TAUKO_S))):
        return "jono tyhja (suunnittelu tauolla: edellinen kierros ei tuottanut tehtavia eika uusia tuloksia ole)"
    kehote = (KEHOTE.read_text(encoding="utf-8") + "\n\n" + SUUNNITTELU.read_text(encoding="utf-8")
              + f"\n\nEhdota enintaan {tilaa} tehtavaa.\n")
    luo_kaistan_tyohak(tyohak, kaista, env)
    sm = mallit.suunnittelun_malli()   # suunnittelu = Opus (mallien tyonjako 3.11.2026)
    kom, alaenv, cwd = lapsen_ajo(kehote, env, tyohak, kaista, sm.nimi)
    alku_s = time.monotonic()
    try:
        r = suorita(kom, cwd=cwd, capture_output=True, text=True,
                    timeout=aikaraja_s(env, sm.nimi), env=alaenv, check=False)
        ok, teksti = tulkitse(r.stdout, r.returncode)
        kulutus.kirjaa(Path(repo).parent, sm.tunniste, "", kaista, "suunnittelu", ok, time.monotonic() - alku_s,
                       kulutus.claude_luvut(getattr(r, "stdout", "") or ""), nyt)
        if not ok and RAJA_RE.search(teksti or ""):
            varaus.kirjaa_raja(Path(repo).parent, kaista, "_suunnittelu", env)
            return "jono tyhja (suunnittelu: kayttorajavirhe, Claude-kaistat tauolla)"
    except subprocess.TimeoutExpired:
        kirjaa_suunnittelu(tyohak, repo, nyt, 0)
        return "jono tyhja (suunnittelu: aikakatkaisu)"
    suunn = jasenna_suunnitelma(teksti) if ok else None
    if suunn is None:
        kirjaa_suunnittelu(tyohak, repo, nyt, 0)
        return "jono tyhja (suunnittelu ei tuottanut kelvollista JSONia)"
    with git_lukko(Path(repo).parent):   # kirjoitukset ja julkaisu yhden lukon sisalla: toisen kaistan pull --rebase ei nae puolivalmista tyopuuta
        polut = []
        git(repo, "pull", "-q", "--rebase", "origin", "main", check=False)   # nae muiden samaan aikaan lisaamat tehtavat ennen vertailua
        nro = seuraava_numero(repo, pvm)
        # Porttitarkistin (Tonyn linjaus 2.11.): julkaisu, raha, oikeudet, katot, ajastukset ja uusi suunta Tonylle, ei jonoon.
        hyvaksytyt, sunnuntai, _hylatyt_portti = silmukka.suodata(suunn["tehtavat"])
        if sunnuntai:
            suunn["tonylle"] = (suunn["tonylle"].strip() + "\n" + silmukka.sunnuntairivit(sunnuntai)).strip()
        uudet, _hylatyt = suodata_uudet(repo, hyvaksytyt, PORTTI)
        for t in uudet[:tilaa]:
            p = Path(repo) / JONO / "tehtavat" / f"{pvm}-{nro:02d}-auto-{t['nimi']}.md"
            if not kelvollinen_nimi(p.name):
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("<!-- CT115:n Claude kirjoitti itse (itsesuunnittelu) -->\n" + t["sisalto"].rstrip() + "\n", encoding="utf-8")
            polut.append(p)
            nro += 1
        if suunn["tonylle"].strip():
            tp = Path(repo) / JONO / TONYLLE
            tp.parent.mkdir(parents=True, exist_ok=True)
            vanha = tp.read_text(encoding="utf-8") if tp.exists() else "# Tonyn paatettavat\n"
            tp.write_text(vanha.rstrip() + f"\n\n## {nyt.isoformat(timespec='minutes')} (CT115)\n{suunn['tonylle'].strip()}\n", encoding="utf-8")
            polut.append(tp)
        kirjaa_suunnittelu(tyohak, repo, nyt, len([p for p in polut if p.name != TONYLLE]))
        if not polut:
            return "jono tyhja (ei uusia tehtavia)"
        julkaise(repo, polut, f"claude-jono: itsesuunnittelu {len([p for p in polut if p.name != TONYLLE])} tehtavaa")
    viesti = "claude-jono: itsesuunnittelu -> " + ", ".join(p.name for p in polut)
    if laheta:
        laheta(viesti)
    return viesti


def luo_toistuvat(repo, tyohak, nyt=None):
    """Toistuvat tehtavat (esim. viikoittainen optimointikierros, toisto 7 pv) pohjista claudejono/toistuvat/.
    Ajuri nimeaa ne `<pvm>-NN-auto-<nimi>.md` (sallittu polku) ja laskee ne itse keksittyjen paivarajaan (AUTO_MAX_PAIVA).
    Ei luoda, jos laatuotos on pysayttanyt silmukan, edellinen kierros on ajamatta tai paivaraja on taynna."""
    nyt = nyt or datetime.now().astimezone()
    if silmukka.pysaytetty(tyohak):
        return []
    pvm = nyt.date().isoformat()
    polut = []
    for nimi in silmukka.toistuvat_erapaivalla(repo, nyt):
        if auto_tanaan(repo, pvm) + len(polut) >= AUTO_MAX_PAIVA:
            break
        p = Path(repo) / JONO / "tehtavat" / f"{pvm}-{seuraava_numero(repo, pvm) + len(polut):02d}-auto-{nimi}.md"
        if not kelvollinen_nimi(p.name):
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<!-- Toistuva tehtava (claudejono/toistuvat/" + nimi + ".md), toisto "
                     + str(silmukka.TOISTUVAT[nimi]) + " pv -->\n" + silmukka.toistuvan_sisalto(nimi).rstrip() + "\n",
                     encoding="utf-8")
        polut.append(p)
    if polut:
        julkaise(repo, polut, "claude-jono: toistuva tehtava " + ", ".join(p.name for p in polut))
    return polut


# Oletuksena pois: ajuri luo nama vain kun palvelu asettaa CLAUDEJONO_EHKAISY=1 (Tony paattaa).
EHKAISY_AVOIMIA_MAX = 2   # enintaan nain monta ehkaisytehtavaa jonossa yhta aikaa
EHKAISY_PER_AJO = 1       # ja enintaan yksi uusi per ajo; AUTO_MAX_PAIVA ja AUTO_PER_AJO pysyvat voimassa
EHKAISY_NIMI = "ehkaisy-koodiin-juurisyy-"
EHKAISY_MALLI = Path(__file__).resolve().parent / "ehkaisy-tehtava.md"
EHKAISY_LUOKAT = {"1": "tehty \u2260 toimii", "2": "muistista, ei l\u00e4hteest\u00e4", "3": "v\u00e4\u00e4r\u00e4 oletus omasta ymp\u00e4rist\u00f6st\u00e4"}


def luo_ehkaisytehtavat(repo, tyohak, nyt=None):
    """Toistuva juurisyy (luokka 1-3), jonka uusin ehkaisy on 'ohje', tuottaa tehtavan `<pvm>-NN-auto-ehkaisy-koodiin-juurisyy-N-<pvm>.md`:
    siirra ehkaisy testiksi tai koodiportiksi. Sama toisto (luokka + uusimman merkinnan paiva) tuottaa tehtavan vain kerran, myos
    tehdyista. Jonon koko: enintaan EHKAISY_AVOIMIA_MAX avointa, EHKAISY_PER_AJO per ajo, AUTO_MAX_PAIVA paivassa."""
    nyt = nyt or datetime.now().astimezone()
    if silmukka.pysaytetty(tyohak):
        return []
    pvm = nyt.date().isoformat()
    teht = Path(repo) / JONO / "tehtavat"
    avoimet = sum(1 for p in odottavat_tehtavat(repo) if tehtavan_slug(p.name).startswith(EHKAISY_NIMI))
    olemassa = {tehtavan_slug(p.name) for p in teht.iterdir() if p.is_file() and kelvollinen_nimi(p.name)} if teht.is_dir() else set()
    polut = []
    for luokka, ms in virheopit.toistuvat_ohjeet(virheopit.jasenna(virheopit.lue_loki(repo)), nyt.date()):
        slug = f"{EHKAISY_NIMI}{luokka}-{ms[-1].pvm.strftime('%Y%m%d')}"
        if (slug in olemassa or avoimet + len(polut) >= EHKAISY_AVOIMIA_MAX or len(polut) >= min(EHKAISY_PER_AJO, AUTO_PER_AJO)
                or auto_tanaan(repo, pvm) + len(polut) >= AUTO_MAX_PAIVA):
            continue
        p = teht / f"{pvm}-{seuraava_numero(repo, pvm) + len(polut):02d}-auto-{slug}.md"
        if not kelvollinen_nimi(p.name):
            continue
        # Virhelokin rivit ovat dataa: sama kieltolista kuin opeilla myos tehtavatiedoston kautta (rivi pois, ei muokata).
        rivit = [r for r in (virheopit.rivi_kehotteeseen(m) for m in ms[-5:]) if not OPIT_KIELTOLISTA.search(r)]
        if not rivit:
            continue
        sisalto = (EHKAISY_MALLI.read_text(encoding="utf-8")
                   .replace("{luokka}", luokka).replace("{luokan_nimi}", EHKAISY_LUOKAT[luokka])
                   .replace("{lukumaara}", str(len(ms))).replace("{ikkuna}", str(virheopit.TOISTO_IKKUNA_PV))
                   .replace("{merkinnat}", "\n".join(rivit)))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<!-- Ajuri loi: sama juurisyy toistui virhelokissa, ehkaisy vain ohje (claudejono/virheopit.py) -->\n"
                     + sisalto.rstrip() + "\n", encoding="utf-8")
        polut.append(p)
    if polut:
        julkaise(repo, polut, "claude-jono: ehkaisy koodiin " + ", ".join(p.name for p in polut))
    return polut


def valvo_virhelokia(repo, tila, laheta=None):
    """Hakemisen jalkeen: uusi vaillinainen virhelokimerkinta (jonka kirjoittaja ei kulkenut docs-ulos-portin kautta) halytetaan
    kerran per merkinta. Ajuri ei muokkaa lokia. Palauttaa vialliset (tunniste, puuttuvat)."""
    vialliset = virheopit.tarkista(virheopit.lue_loki(repo))
    polku = Path(tila) / "virheloki-halytetty.json"
    try:
        nahty = set(json.loads(polku.read_text()))
    except (OSError, ValueError, TypeError):
        nahty = set()
    uudet = [(t, p) for t, p in vialliset if t not in nahty]
    if uudet and laheta:
        laheta("claude-jono: virhelokin uusi merkint\u00e4 vaillinainen, lis\u00e4\u00e4 kent\u00e4t: " + virheopit.selite(uudet)[:600])
    if uudet:
        try:
            polku.write_text(json.dumps(sorted(nahty | {t for t, _ in uudet})))
        except OSError:
            pass
    return vialliset


def _lue_teksti(polku):
    try:
        return Path(polku).read_bytes()[:TEHTAVA_MAX_TAVUA].decode("utf-8", "replace")
    except OSError:
        return ""


def _ilmoita_varaus(tila, env, laheta, nyt):
    """Telegram-ilmoitus, kun Tonyn varauksen tila vaihtuu. Ilmoitusvirhe ei saa kaataa ajoa."""
    try:
        varaus.ilmoita_muutos(tila, varaus.arvio(tila, env, nyt), laheta)
    except Exception:
        pass


def aja(repo, tyohak, env=None, suorita=subprocess.run, laheta=None, kaista=1, litellm=None):
    """litellm = testattava korvike litellm_ajo.aja:lle (alias, teksti, env) -> (ok, teksti, luvut)."""
    env = dict(os.environ if env is None else env)
    kaista = tarkista_kaista(kaista, env) if kaista != 1 else 1
    tila = Path(repo).parent
    vanhenee = varaus_vanhenee_s(env)
    leima = f" [kaista {kaista}]" if kaista > 1 else ""
    nyt_k = datetime.now(kulutus.TZ)
    _ilmoita_varaus(tila, env, laheta, nyt_k)
    estetty = varaus.claude_estetty(tila, env, kaista, nyt_k)   # Tonyn varaus tai kayttorajan tauko; ei koske OpenRouter-reittia
    litellm_kunnossa = litellm_ajo.avain_asetettu(env) or litellm is not None
    ohitettu = []

    def ohita(p):
        m = mallit.valitse(p.name, _lue_teksti(p))
        if (m.reitti == "claude" and estetty) or (m.reitti == "openrouter" and not litellm_kunnossa):
            ohitettu.append(p.name)
            return True
        return False

    # Valinta + varaus yhden lyhyen git-lukon sisalla: pull, tulosten tarkistus ja atominen varaus eivat lomitu toisen kaistan kanssa.
    with git_lukko(tila) as odotus:
        git(repo, "pull", "-q", "--rebase", "origin", "main")
        if env.get("CLAUDEJONO_ITSESUUNNITTELU", "1") == "1" and env.get("CLAUDEJONO_TOISTUVAT") == "1":
            luo_toistuvat(repo, tyohak)   # toistuvat (esim. viikon optimointikierros) vain kun palvelu kytkee ne paalle
        if env.get("CLAUDEJONO_ITSESUUNNITTELU", "1") == "1":
            try:
                valvo_virhelokia(repo, tila, laheta)
                if env.get("CLAUDEJONO_EHKAISY") == "1":   # uusien tehtavien luonti oletuksena pois, kuten toistuvat (CLAUDEJONO_TOISTUVAT)
                    luo_ehkaisytehtavat(repo, tyohak)
            except (OSError, ValueError, JonoVirhe) as virhe:
                print(f"claude-jono: virheloki-ehkaisy ohitettu: {type(virhe).__name__}", file=sys.stderr)
        t = varaa_seuraava(repo, tila, kaista, vanhenee_s=vanhenee, kaistoja=kaistojen_maara(env), ohita=ohita)
        suunnittelu = False
        if t is None:
            if ohitettu:
                return f"jono odottaa, ei ajettavaa talla kaistalla: {estetty or 'litellm-avainta ei ole asetettu'}"
            if env.get("CLAUDEJONO_ITSESUUNNITTELU", "1") != "1":
                return "jono tyhja"
            if estetty:
                return f"jono tyhja (suunnittelu estetty: {estetty})"
            # Vapaa kaista suunnittelee, vaikka muut kaistat ovat toissa (muuten 2. ja 3. kaista jaavat tyhjaksi).
            # Paivakatto, ajokohtainen raja ja tauko pysyvat suunnittele()-funktiossa; yksi suunnittelija varauksella.
            if not suunnittelu_tarpeen(repo, tila, env, vanhenee_s=vanhenee):
                return "jono riittaa (avoimia tehtavia yli 2 x kaistat)"
            suunnittelu = varaa(tila, SUUNNITTELU_VARAUS, kaista, vanhenee_s=vanhenee)
            if not suunnittelu:
                return "jono tyhja (toinen kaista suunnittelee)"
    alku = time.monotonic()
    if suunnittelu:
        try:
            viesti = suunnittele(repo, tyohak, env, suorita, laheta, kaista=kaista)
        finally:
            vapauta(tila, SUUNNITTELU_VARAUS, kaista)
        kirjaa_kaistaloki(tila, kaista, "suunnittelu", "", True, time.monotonic() - alku, lukko_odotus_s=odotus,
                          malli=mallit.suunnittelun_malli().tunniste)
        return viesti
    taydennys = ""   # taydennys hoidetaan ennakoivalla suunnittelulla (#175): vapaa kaista suunnittelee, kun avoimia < 2 x kaistat
    try:
        luo_kaistan_tyohak(tyohak, kaista, env)
        tehtava_teksti = _lue_teksti(t)
        m = mallit.valitse(t.name, tehtava_teksti)
        r = None
        luvut = {}
        if m.reitti == "openrouter":
            if len(t.read_bytes()) > TEHTAVA_MAX_TAVUA:
                raise JonoVirhe(f"tehtava yli {TEHTAVA_MAX_TAVUA} tavua")
            ok, teksti, luvut = (litellm or litellm_ajo.aja)(m.nimi, tehtava_teksti, env)
        else:
            kehote = rakenna_kehote(t, opit=kehotteen_opit(repo, t.name, tehtava_teksti), kaista=kaista)
            kom, alaenv, cwd = lapsen_ajo(kehote, env, tyohak, kaista, m.nimi)
            valmistele_docs_ulos(cwd, env)
            try:
                r = suorita(kom, cwd=cwd, capture_output=True, text=True,
                            timeout=aikaraja_s(env, m.nimi), env=alaenv, check=False)
                ok, teksti = tulkitse(r.stdout, r.returncode)
                luvut = kulutus.claude_luvut(getattr(r, "stdout", "") or "")
            except subprocess.TimeoutExpired:
                ok, teksti = False, "aikakatkaisu"
        kesto = time.monotonic() - alku
        kulutus.kirjaa(tila, m.tunniste, t.name, kaista, "tehtava", ok, kesto, luvut)
        if m.reitti == "claude" and not ok and RAJA_RE.search(teksti or ""):
            # Kayttorajavirhe: kaikki Claude-kaistat tauolle, tehtava jaa jonoon (ei tulosta) enintaan RAJA_YRITYKSIA kertaa. Ei uusintasilmukkaa.
            tauko, yritys = varaus.kirjaa_raja(tila, kaista, t.name, env)
            if laheta:
                laheta(f"Claude-kayttoraja: kaistat tauolla {tauko // 60} min ({t.name}, yritys {yritys}/{varaus.RAJA_YRITYKSIA})")
            if yritys < varaus.RAJA_YRITYKSIA:
                kirjaa_kaistaloki(tila, kaista, "tehtava", t.name, False, kesto, teksti, getattr(r, "stdout", "") or "", None, odotus, malli=m.tunniste)
                return f"claude-jono: {t.name} KAYTTORAJA, kaistat tauolla {tauko // 60} min{leima}"
        with git_lukko(tila) as odotus2:
            # Todennusportti koskee Claude-reitin tyota (koodi, PR:t); OpenRouter-rutiini (tiivistelmat, luokittelu) ei tuota todennusrivia.
            tila_nimi = "KESKEN" if ok and m.reitti == "claude" and not on_todennettu(teksti) else ("VALMIS" if ok else "EPAONNISTUI")
            polku = kirjoita_tulos(repo, t.name, ok, teksti, tila=tila_nimi, malli=m.tunniste)
            hylatyt_docs = []
            docs = poimi_docs_ulos(cwd, repo, env, hylatyt=hylatyt_docs) if m.reitti == "claude" else []
            if hylatyt_docs:
                taydennys += " (virheloki hyl\u00e4tty: " + "; ".join(syy for _, syy in hylatyt_docs)[:300] + ")"
            pushattu = julkaise(repo, [polku] + docs)
            if tila_nimi == "VALMIS" and m.reitti == "claude":
                uudet, _ = suodata_opit(poimi_uusi_oppi(teksti))
                if uudet:
                    julkaise_opit(repo, paivita_opit(lue_opit(repo), uudet))
        kirjaa_kaistaloki(tila, kaista, "tehtava", t.name, ok, kesto, teksti, getattr(r, "stdout", "") or "", pushattu, odotus + odotus2,
                          malli=m.tunniste)
    finally:
        vapauta(tila, t.name, kaista)   # varaus vapautuu tuloksen kirjauksen jalkeen tai virheessa; muuten se vanhenee aikarajalla
    viesti = f"claude-jono: {t.name} {tila_nimi}{leima}" + ("" if pushattu else " (push epaonnistui)") + taydennys
    if laheta:
        laheta(viesti + f" [{m.tunniste}]\n" + teksti[:1500])
    return viesti


def main(argv=None):
    p = argparse.ArgumentParser(description="Clauden tehtavajono (GitHub)")
    p.add_argument("komento", choices=["ajo", "seuraava"])
    p.add_argument("--kaista", type=int, default=1, help="rinnakkaiskaista 1..CLAUDEJONO_KAISTOJA (oletus 1, enintaan %d)" % KAISTOJA_MAX)
    a = p.parse_args(argv)
    repo = Path(os.environ.get("CLAUDEJONO_REPO", "/var/lib/claude-jono/docs"))
    tyohak = Path(os.environ.get("CLAUDEJONO_TYOHAK", "/opt/agentscope"))
    if a.komento == "seuraava":
        try:
            git(repo, "pull", "-q", "--rebase", "origin", "main")
        except JonoVirhe as virhe:
            print(f"claude-jono virhe: {virhe}", file=sys.stderr)
            return 1
        t = seuraava_tehtava(repo)
        print(t.name if t else "jono tyhja")
        return 0
    try:
        tarkista_kaista(a.kaista, os.environ)
    except JonoVirhe as virhe:
        print(f"claude-jono virhe: {virhe}", file=sys.stderr)
        return 2
    with open(repo.parent / f"lukko-{a.kaista}", "w") as kaistalukko:   # yksi ajo per kaista; kaistojen valinta on varauksilla
        try:
            fcntl.flock(kaistalukko, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"toinen ajo kaynnissa (kaista {a.kaista})")
            return 0
        try:
            from yojono.ajuri import laheta_telegram
        except Exception:
            laheta_telegram = None
        try:
            print(aja(repo, tyohak, laheta=laheta_telegram, kaista=a.kaista))
        except Exception as virhe:  # kaikki kaatumiset halytetaan (vika 30.10.: FileNotFoundError jai hiljaiseksi 20 h)
            kuvaus = f"{type(virhe).__name__}: {str(virhe)[:200]}"
            print(f"claude-jono virhe: {kuvaus}", file=sys.stderr)
            halyta(repo.parent / "viimeisin-halytys.json", kuvaus, laheta_telegram)
            return 1
    return 0


HALYTYS_TOISTO_S = 6 * 3600


def halyta(tila, kuvaus, laheta, nyt=None):
    """Lahettaa Telegram-halytyksen kaatumisesta. Sama virhe toistetaan enintaan 6 h valein, jottei 30 min ajastin
    tayta kanavaa. Palauttaa True, jos viesti lahetettiin."""
    nyt = time.time() if nyt is None else nyt
    try:
        edellinen = json.loads(Path(tila).read_text())
    except (OSError, ValueError):
        edellinen = {}
    if edellinen.get("kuvaus") == kuvaus and nyt - edellinen.get("aika", 0) < HALYTYS_TOISTO_S:
        return False
    if not laheta:
        return False
    laheta(f"HALYTYS claude-jono kaatui: {kuvaus}\nTarkista: journalctl -u claude-jono.service -n 40 --no-pager")
    try:
        Path(tila).write_text(json.dumps({"kuvaus": kuvaus, "aika": nyt}))
    except OSError:
        pass
    return True


if __name__ == "__main__":
    sys.exit(main())
