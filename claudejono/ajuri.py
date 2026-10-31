"""Clauden tehtavajono GitHubin yli (doc 71, Tonyn hyvaksynta 30.10.2026).

Pilvi-Claude kirjoittaa tehtavat docs-repoon `claude-jono/tehtavat/<id>.md`. Tama ajuri (CT115, systemd-ajastin) hakee repon,
ottaa vanhimman tehtavan jolla ei ole tulosta, ajaa sen `claude -p`:lla ja pushaa tuloksen `claude-jono/tulokset/<id>.md`.
Yksi tehtava per ajo. Ajuri ei koskaan committaa mitaan muuta kuin oman tulostiedostonsa.

Raja (koodissa, ei vain kehotteessa): tehtava saa avata PR:n, mutta ei mergea, ei muuta katto- tai oikeusarvoja eika kasittele salaisuuksia.
Kehote kertoo rajan agentille; CT115:n .claude/settings.json estaa salaisuustiedostot; ajuri rajaa commitin polun.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

TEHTAVA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-[a-z0-9-]{1,60}\.md$")
TEHTAVA_MAX_TAVUA = 20000
TULOS_MAX_TAVUA = 60000
OLETUS_AIKARAJA_S = 45 * 60
OLETUS_MAX_KIERROKSET = 60
JONO = "claude-jono"
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


def lue_opit(repo):
    """opit.md:n sisalto kehotteeseen, rajattuna kokoon. Puuttuva tiedosto = ei opittua."""
    p = Path(repo) / OPIT
    if not p.is_file():
        return ""
    return p.read_bytes()[:OPIT_MAX_TAVUA].decode("utf-8", "ignore")


def rakenna_kehote(tehtava_polku, kehote_polku=KEHOTE, opit=""):
    data = Path(tehtava_polku).read_bytes()
    if len(data) > TEHTAVA_MAX_TAVUA:
        raise JonoVirhe(f"tehtava yli {TEHTAVA_MAX_TAVUA} tavua")
    osat = [Path(kehote_polku).read_text(encoding="utf-8")]
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
    (Path(repo) / OPIT).write_text(teksti, encoding="utf-8")
    git(repo, "add", "--", OPIT)
    if [r for r in git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if r] != [OPIT]:
        git(repo, "reset", "-q", check=False)
        raise JonoVirhe("indeksissa muuta kuin opit.md")
    git(repo, "commit", "-q", "-m", "claude-jono: opit")
    for _ in range(3):
        if git(repo, "push", "-q", "origin", "HEAD", check=False).returncode == 0:
            return True
        git(repo, "pull", "-q", "--rebase", "origin", "main", check=False)
    return False


def claude_komento(kehote, env):
    kom = [env.get("CLAUDEJONO_CLAUDE", "claude"), "-p", kehote, "--output-format", "json",
           "--max-turns", str(int(env.get("CLAUDEJONO_MAX_KIERROKSET", OLETUS_MAX_KIERROKSET)))]
    if env.get("CLAUDEJONO_LUPATILA"):
        kom += ["--permission-mode", env["CLAUDEJONO_LUPATILA"]]
    return kom


RUNUSER = "/usr/sbin/runuser"


def lapsen_ajo(kehote, env, tyohak):
    """(komento, ymparisto, cwd) Claude-lapsiprosessille.

    Jos CLAUDEJONO_KAYTTAJA on asetettu (Tonyn paatos 30.10.2026), ajuri jaa rootiksi (git, deploy key, Telegram), mutta agentti
    ajetaan tavallisena kayttajana runuserilla: se ei nae deploy keyta, botin tunnusta, yojonon kattoja eika salaisuuksia, eika
    saa rootin ymparistoa. Ilman muuttujaa toiminta on ennallaan."""
    kom = claude_komento(kehote, env)
    kayttaja = env.get("CLAUDEJONO_KAYTTAJA", "")
    if not kayttaja:
        alaenv = {k: v for k, v in env.items() if not k.startswith(("YOJONO_TELEGRAM_", "CLAUDEJONO_GIT", "GIT_SSH"))}
        return kom, alaenv, str(tyohak)
    if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", kayttaja) or kayttaja == "root":
        raise JonoVirhe("CLAUDEJONO_KAYTTAJA ei kelpaa")
    koti = env.get("CLAUDEJONO_KOTI", f"/var/lib/{kayttaja}")
    alaenv = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": koti, "USER": kayttaja, "LOGNAME": kayttaja,
              "LANG": env.get("LANG", "C.UTF-8")}
    # Absoluuttinen polku: Popen etsii komennon lapsen PATHista, jossa ei ole /usr/sbin:ia (vika 30.10.2026).
    return [RUNUSER, "-u", kayttaja, "--", *kom], alaenv, str(Path(tyohak) / "tyo")


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


def kirjoita_tulos(repo, nimi, ok, teksti, nyt=None):
    nyt = nyt or datetime.now().astimezone()
    polku = Path(repo) / JONO / "tulokset" / nimi
    polku.parent.mkdir(parents=True, exist_ok=True)
    runko = teksti.encode("utf-8")[:TULOS_MAX_TAVUA].decode("utf-8", "ignore")
    polku.write_text(f"# Tulos: {nimi}\n\n- Tila: {'VALMIS' if ok else 'EPAONNISTUI'}\n- Aika: {nyt.isoformat(timespec='minutes')}\n\n{runko}\n",
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
    """Ajuri saa committaa vain: tulokset/<nimi>.md, tehtavat/<pvm>-NN-auto-*.md ja tonylle.md."""
    osat = suht.split("/")
    if len(osat) == 3 and osat[0] == JONO and osat[1] == "tulokset" and kelvollinen_nimi(osat[2]):
        return True
    if len(osat) == 3 and osat[0] == JONO and osat[1] == "tehtavat" and kelvollinen_nimi(osat[2]) and "-auto-" in osat[2]:
        return True
    return suht == f"{JONO}/{TONYLLE}"


def julkaise(repo, polut, viesti=None):
    """Committaa ja pushaa VAIN annetut sallitut polut. Jos indeksissa on jotain muuta, ei tehda mitaan."""
    if not isinstance(polut, (list, tuple)):
        polut = [polut]
    suhteet = [str(Path(p).relative_to(repo)) for p in polut]
    for suht in suhteet:
        if not sallittu_polku(suht):
            raise JonoVirhe(f"polkua ei saa committaa: {suht}")
    git(repo, "add", "--", *suhteet)
    staged = sorted(r for r in git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if r)
    if not staged or not set(staged) <= set(suhteet):
        git(repo, "reset", "-q", check=False)
        raise JonoVirhe(f"indeksissa muuta kuin sallitut polut: {staged[:5]}")
    git(repo, "commit", "-q", "-m", viesti or f"claude-jono: tulos {Path(polut[0]).name}")
    for _ in range(3):
        if git(repo, "push", "-q", "origin", "HEAD", check=False).returncode == 0:
            return True
        git(repo, "pull", "-q", "--rebase", "origin", "main", check=False)
    return False


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


def suunnittele(repo, tyohak, env, suorita, laheta=None, nyt=None):
    """Jono tyhja: Claude ehdottaa jatkotehtavat. Ajuri nimeaa, rajaa (AUTO_MAX_PAIVA, AUTO_PER_AJO) ja committaa ne."""
    nyt = nyt or datetime.now().astimezone()
    pvm = nyt.date().isoformat()
    tilaa = min(AUTO_PER_AJO, AUTO_MAX_PAIVA - auto_tanaan(repo, pvm))
    if tilaa <= 0:
        return "jono tyhja (itsesuunnittelun paivaraja taynna)"
    kehote = (KEHOTE.read_text(encoding="utf-8") + "\n\n" + SUUNNITTELU.read_text(encoding="utf-8")
              + f"\n\nEhdota enintaan {tilaa} tehtavaa.\n")
    kom, alaenv, cwd = lapsen_ajo(kehote, env, tyohak)
    try:
        r = suorita(kom, cwd=cwd, capture_output=True, text=True,
                    timeout=int(env.get("CLAUDEJONO_AIKARAJA_S", OLETUS_AIKARAJA_S)), env=alaenv, check=False)
        ok, teksti = tulkitse(r.stdout, r.returncode)
    except subprocess.TimeoutExpired:
        return "jono tyhja (suunnittelu: aikakatkaisu)"
    suunn = jasenna_suunnitelma(teksti) if ok else None
    if suunn is None:
        return "jono tyhja (suunnittelu ei tuottanut kelvollista JSONia)"
    polut = []
    nro = seuraava_numero(repo, pvm)
    for t in suunn["tehtavat"][:tilaa]:
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
    if not polut:
        return "jono tyhja (ei uusia tehtavia)"
    julkaise(repo, polut, f"claude-jono: itsesuunnittelu {len([p for p in polut if p.name != TONYLLE])} tehtavaa")
    viesti = "claude-jono: itsesuunnittelu -> " + ", ".join(p.name for p in polut)
    if laheta:
        laheta(viesti)
    return viesti


def aja(repo, tyohak, env=None, suorita=subprocess.run, laheta=None):
    env = dict(os.environ if env is None else env)
    git(repo, "pull", "-q", "--rebase", "origin", "main")
    t = seuraava_tehtava(repo)
    if t is None:
        if env.get("CLAUDEJONO_ITSESUUNNITTELU", "1") == "1":
            return suunnittele(repo, tyohak, env, suorita, laheta)
        return "jono tyhja"
    (Path(tyohak) / "tyo").mkdir(parents=True, exist_ok=True)
    kehote = rakenna_kehote(t, opit=lue_opit(repo))
    kom, alaenv, cwd = lapsen_ajo(kehote, env, tyohak)
    try:
        r = suorita(kom, cwd=cwd, capture_output=True, text=True,
                    timeout=int(env.get("CLAUDEJONO_AIKARAJA_S", OLETUS_AIKARAJA_S)), env=alaenv, check=False)
        ok, teksti = tulkitse(r.stdout, r.returncode)
    except subprocess.TimeoutExpired:
        ok, teksti = False, "aikakatkaisu"
    polku = kirjoita_tulos(repo, t.name, ok, teksti)
    pushattu = julkaise(repo, polku)
    if ok:
        uudet, _ = suodata_opit(poimi_uusi_oppi(teksti))
        if uudet:
            julkaise_opit(repo, paivita_opit(lue_opit(repo), uudet))
    viesti = f"claude-jono: {t.name} {'VALMIS' if ok else 'EPAONNISTUI'}" + ("" if pushattu else " (push epaonnistui)")
    if laheta:
        laheta(viesti + "\n" + teksti[:1500])
    return viesti


def main(argv=None):
    p = argparse.ArgumentParser(description="Clauden tehtavajono (GitHub)")
    p.add_argument("komento", choices=["ajo", "seuraava"])
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
    with open(repo.parent / "lukko", "w") as lukko:
        try:
            fcntl.flock(lukko, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("toinen ajo kaynnissa")
            return 0
        try:
            from yojono.ajuri import laheta_telegram
        except Exception:
            laheta_telegram = None
        try:
            print(aja(repo, tyohak, laheta=laheta_telegram))
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
