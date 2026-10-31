"""Automaattinen korjaaja: valvotun yksikon kaatuminen kaynnistaa diagnoosin ilman Tonya (Tonyn toive 31.10.2026).

systemd `OnFailure=korjaaja@%n.service` -> `python3 -m claudejono.korjaaja <yksikko>` (rootina). Ajuri:
  1. tarkistaa yksikon nimen, sallitun listan ja silmukan eston (korjaaja ei korjaa itseaan),
  2. tarkistaa toistorajan (sama yksikko enintaan 1 diagnoosi / 6 h) ja paivakaton (kustannuskatto),
  3. keraa `journalctl -u <yksikko> -n 80` ITSE (agentti ei saa journal-oikeuksia) ja antaa sen agentille tekstina, datana,
  4. ajaa Clauden kayttajana claudejono aikarajalla (10 min) ja kierrosrajalla,
  5. kirjoittaa diagnoosin docs-repoon claude-jono/korjaukset/<pvm>-<yksikko>.md ja lahettaa Telegram-tiivistelman.
Korjaaja ei kaynnista palveluita uudelleen, ei muuta asetuksia eika mergea. Se ei koske ajurin omaan polkusaantoon
(ajuri.sallittu_polku): korjausten committaus kay tassa tiukasti omalla polkutarkistuksella.
"""
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from claudejono import ajuri

KEHOTE = Path(__file__).resolve().parent / "korjaaja-kehote.md"
YKSIKKO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,80}\.service$")
# Valvotut yksikot (CT115:n nakymasta). Etuliite "kauppatiimi-" kattaa kauppatiimin palvelut. Muut CT:t: suunnitelma (README).
VALVOTUT = ("claude-jono.service", "yojono.service", "trendit-paiva.service", "trendit-viikko.service")
VALVOTUT_ETULIITTEET = ("kauppatiimi-",)
TOISTORAJA_S = 6 * 3600            # sama yksikko enintaan 1 diagnoosi / 6 h
PAIVAKATTO = 8                     # kaikki diagnoosit yhteensa enintaan 8 / vrk (kustannuskatto)
AIKARAJA_S = 10 * 60               # Clauden ajoaika
MAX_KIERROKSET = 20
LOKI_RIVEJA = 80
LOKI_RIVI_MAX = 400
LOKI_MAX_TAVUA = 16000
TIIVISTELMA_MAX = 1200
OLETUS_TILA = "/var/lib/korjaaja/tila.json"
KORJAUKSET = "korjaukset"


class KorjaajaVirhe(RuntimeError):
    pass


def silmukka_esto(yksikko):
    """Korjaajan oma kaatuminen ei saa laukaista korjaajaa."""
    return yksikko.startswith("korjaaja") or "korjaaja@" in yksikko


def valvottu(yksikko):
    return yksikko in VALVOTUT or any(yksikko.startswith(e) for e in VALVOTUT_ETULIITTEET)


def tarkista_yksikko(yksikko):
    """None jos ok, muuten syy tekstina (ei poikkeusta: OnFailure-ajo paattyy siististi)."""
    if not isinstance(yksikko, str) or not YKSIKKO_RE.match(yksikko):
        return "yksikon nimi ei kelpaa"
    if silmukka_esto(yksikko):
        return "silmukan esto: korjaajaa ei korjata korjaajalla"
    if not valvottu(yksikko):
        return "yksikko ei ole valvottujen listalla"
    return None


def lataa_tila(polku):
    try:
        d = json.loads(Path(polku).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def saa_ajaa(tila, yksikko, nyt):
    """(ok, syy). tila = {"yksikot": {nimi: [aikaleimat]}}; nyt = epoch-sekunnit."""
    ajat = tila.get("yksikot", {}).get(yksikko, [])
    viimeisin = max((a for a in ajat if isinstance(a, (int, float))), default=None)
    if viimeisin is not None and 0 <= nyt - viimeisin < TOISTORAJA_S:
        return False, f"toistoraja: {yksikko} diagnosoitu {int((nyt - viimeisin) // 60)} min sitten (raja 6 h)"
    paiva = sum(1 for l in tila.get("yksikot", {}).values() for a in l
                if isinstance(a, (int, float)) and 0 <= nyt - a < 24 * 3600)
    if paiva >= PAIVAKATTO:
        return False, f"paivakatto: {PAIVAKATTO} diagnoosia / vrk tayttyi"
    return True, ""


def kirjaa(tila, yksikko, nyt):
    """Palauttaa uuden tilan, josta yli 24 h vanhat leimat on karsittu (toistoraja 6 h < 24 h)."""
    uusi = {"yksikot": {}}
    for nimi, ajat in tila.get("yksikot", {}).items():
        pidetyt = [a for a in ajat if isinstance(a, (int, float)) and 0 <= nyt - a < 24 * 3600]
        if pidetyt:
            uusi["yksikot"][nimi] = pidetyt
    uusi["yksikot"].setdefault(yksikko, []).append(nyt)
    return uusi


def puhdista_loki(teksti):
    """Loki on dataa: poista ohjausmerkit, rajaa rivit ja koko, neutraloi kehyksen sulkevat tagit."""
    rivit = []
    for rivi in teksti.splitlines()[-LOKI_RIVEJA:]:
        rivi = "".join(c for c in rivi if c == "\t" or (c.isprintable()))
        rivi = re.sub(r"</?\s*(loki|tehtava|kehote|diagnoosi)\b[^>]*>?", "[tagi poistettu]", rivi, flags=re.I)
        rivit.append(rivi[:LOKI_RIVI_MAX])
    ulos = "\n".join(rivit)
    return ulos.encode("utf-8")[-LOKI_MAX_TAVUA:].decode("utf-8", "ignore")


def keraa_loki(yksikko, suorita=subprocess.run):
    """journalctl rootina (ajuri). Palauttaa puhdistetun tekstin; virheesta vain tyyppi/tila."""
    try:
        r = suorita(["journalctl", "-u", yksikko, "-n", str(LOKI_RIVEJA), "--no-pager", "-o", "short-iso"],
                    capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as virhe:
        return f"(lokin keruu epaonnistui: {type(virhe).__name__})"
    if r.returncode != 0:
        return f"(journalctl palautti koodin {r.returncode})"
    return puhdista_loki(r.stdout)


def rakenna_syote(yksikko, loki, kehote_polku=KEHOTE):
    kehote = Path(kehote_polku).read_text(encoding="utf-8")
    return (f"{kehote}\n\nKaatunut yksikko: {yksikko}\n\n"
            "<loki>\n" + loki + "\n</loki>\n")


def turvallinen_nimi(yksikko):
    return re.sub(r"[^a-z0-9-]", "-", yksikko.removesuffix(".service").lower()).strip("-")[:60] or "yksikko"


def korjaus_polku(repo, yksikko, pvm):
    return Path(repo) / ajuri.JONO / KORJAUKSET / f"{pvm}-{turvallinen_nimi(yksikko)}.md"


KORJAUS_POLKU_RE = re.compile(rf"^{ajuri.JONO}/{KORJAUKSET}/\d{{4}}-\d{{2}}-\d{{2}}-[a-z0-9-]{{1,60}}\.md$")


def kirjoita_korjaus(repo, yksikko, ok, teksti, nyt):
    polku = korjaus_polku(repo, yksikko, nyt.strftime("%Y-%m-%d"))
    polku.parent.mkdir(parents=True, exist_ok=True)
    runko = teksti.encode("utf-8")[:ajuri.TULOS_MAX_TAVUA].decode("utf-8", "ignore")
    uusi = (f"## {nyt.isoformat(timespec='minutes')} - {'VALMIS' if ok else 'EPAONNISTUI'}\n\n{runko}\n")
    if polku.exists():
        vanha = polku.read_text(encoding="utf-8")
        polku.write_text(vanha + "\n" + uusi, encoding="utf-8")
    else:
        polku.write_text(f"# Korjaaja: {yksikko}\n\nDiagnoosi on agentin ehdotus, ei hyvaksytty korjaus. Loki oli syotteena dataa.\n\n" + uusi,
                         encoding="utf-8")
    return polku


def julkaise_korjaus(repo, polku):
    """Committaa ja pushaa VAIN claude-jono/korjaukset/<pvm>-<nimi>.md. Ajurin sallittu_polku-saantoon ei koskea."""
    suht = str(Path(polku).relative_to(repo))
    if not KORJAUS_POLKU_RE.match(suht):
        raise KorjaajaVirhe("vain korjaustiedoston saa committaa")
    ajuri.git(repo, "add", "--", suht)
    staged = sorted(r for r in ajuri.git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if r)
    if staged != [suht]:
        ajuri.git(repo, "reset", "-q", check=False)
        raise KorjaajaVirhe(f"indeksissa muuta kuin korjaus: {staged[:5]}")
    ajuri.git(repo, "commit", "-q", "-m", f"korjaaja: diagnoosi {Path(polku).name}")
    for _ in range(3):
        if ajuri.git(repo, "push", "-q", "origin", "HEAD", check=False).returncode == 0:
            return True
        ajuri.git(repo, "pull", "-q", "--rebase", "origin", "main", check=False)
    return False


def aja_claude(syote, env, tyohak, aikaraja=AIKARAJA_S):
    """(ok, teksti). Oma prosessiryhma: aikarajalla tapetaan koko ryhma (runuser + claude + lapset)."""
    env = dict(env)
    env["CLAUDEJONO_MAX_KIERROKSET"] = str(MAX_KIERROKSET)
    kom, alaenv, cwd = ajuri.lapsen_ajo(syote, env, tyohak)
    p = subprocess.Popen(kom, cwd=cwd, env=alaenv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=aikaraja)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        return False, "aikakatkaisu (10 min)"
    return ajuri.tulkitse(out, p.returncode)


def tiivistelma(yksikko, ok, teksti):
    return f"korjaaja: {yksikko} {'diagnoosi valmis' if ok else 'diagnoosi epaonnistui'}\n" + teksti[:TIIVISTELMA_MAX]


def ota_lukko(polku, odotus_s=120):
    """Sama lukko kuin claude-jono-ajurilla (repo.parent/lukko): git-operaatiot eivat mene paallekkain."""
    f = open(polku, "w")
    loppu = time.monotonic() + odotus_s
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            if time.monotonic() > loppu:
                f.close()
                return None
            time.sleep(2)


def kasittele(yksikko, repo, tyohak, tila_polku, env=None, nyt_epoch=None, keraa=keraa_loki, aja=aja_claude, laheta=None):
    """Koko ketju. Palauttaa (koodi, viesti). Ei koskaan nosta poikkeusta rajoista: OnFailure-ajo paattyy aina siististi."""
    env = dict(os.environ if env is None else env)
    nyt_e = time.time() if nyt_epoch is None else nyt_epoch
    syy = tarkista_yksikko(yksikko)
    if syy:
        return 0, f"ohitettu: {syy}"
    Path(tila_polku).parent.mkdir(parents=True, exist_ok=True)
    with open(str(tila_polku) + ".lukko", "w") as tl:
        fcntl.flock(tl, fcntl.LOCK_EX)
        tila = lataa_tila(tila_polku)
        ok, syy = saa_ajaa(tila, yksikko, nyt_e)
        if not ok:
            return 0, f"ohitettu: {syy}"
        # Kirjataan ENNEN ajoa: kaatuva tai aikakatkaistu diagnoosi kuluttaa myos rajan (ei kierrettavissa kaatamalla).
        uusi = kirjaa(tila, yksikko, nyt_e)
        tmp = str(tila_polku) + ".tmp"
        Path(tmp).write_text(json.dumps(uusi), encoding="utf-8")
        os.replace(tmp, tila_polku)
    syote = rakenna_syote(yksikko, keraa(yksikko))
    try:
        ok, teksti = aja(syote, env, tyohak)
    except Exception as virhe:  # tyyppi vain: viesti voi sisaltaa salaisuuksia
        ok, teksti = False, f"ajon virhe: {type(virhe).__name__}"
    nyt = datetime.fromtimestamp(nyt_e).astimezone()
    julkaistu = False
    try:
        lukko = ota_lukko(Path(repo).parent / "lukko")
        if lukko is None:
            raise KorjaajaVirhe("repon lukko varattu")
        with lukko:
            ajuri.git(repo, "pull", "-q", "--rebase", "origin", "main")
            polku = kirjoita_korjaus(repo, yksikko, ok, teksti, nyt)
            julkaistu = julkaise_korjaus(repo, polku)
    except (ajuri.JonoVirhe, KorjaajaVirhe) as virhe:
        teksti += f"\n(diagnoosin tallennus docs-repoon epaonnistui: {type(virhe).__name__})"
    if laheta:
        laheta(tiivistelma(yksikko, ok, teksti) + ("" if julkaistu else "\n(ei tallennettu docs-repoon)"))
    return 0, f"{yksikko}: {'VALMIS' if ok else 'EPAONNISTUI'}" + ("" if julkaistu else " (ei julkaistu)")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("kaytto: python3 -m claudejono.korjaaja YKSIKKO", file=sys.stderr)
        return 2
    repo = Path(os.environ.get("CLAUDEJONO_REPO", "/var/lib/claude-jono/docs"))
    tyohak = Path(os.environ.get("CLAUDEJONO_TYOHAK", "/opt/agentscope"))
    tila = os.environ.get("KORJAAJA_TILA", OLETUS_TILA)
    try:
        from yojono.ajuri import laheta_telegram
    except Exception:
        laheta_telegram = None
    try:
        koodi, viesti = kasittele(argv[0], repo, tyohak, tila, laheta=laheta_telegram)
    except Exception as virhe:
        print(f"korjaaja virhe: {type(virhe).__name__}", file=sys.stderr)
        return 1
    print(viesti)
    return koodi


if __name__ == "__main__":
    sys.exit(main())
