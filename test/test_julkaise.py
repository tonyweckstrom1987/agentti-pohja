"""isanta/julkaise.sh (yleistetty julkaisukomento), paketoi.sh ja asenna-julkaise.sh.

Hiekkalaatikko: vaarennetty pct ja systemctl, paikalliset hakemistot konttina, oikea git archive ja oikea bash. Ei verkkoa, ei oikeaa
pct:ta, ei kosketa mihinkaan sandboxin ulkopuolella (paitsi kontin /tmp:n paketti, jonka skripti siivoaa). Ajetaan vain Linuxilla (bash 5, GNU-tyokalut)."""
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
ISANTA = JUURI / "isanta"
JULKAISE = ISANTA / "julkaise.sh"
PAKETOI = ISANTA / "paketoi.sh"
ASENNA = ISANTA / "asenna-julkaise.sh"
LINUX = sys.platform.startswith("linux") and shutil.which("bash") is not None and shutil.which("git") is not None
EPA = "EP" + chr(0xC4) + "ONNISTUI"

PCT = r'''#!/usr/bin/env bash
LOG="$SIM/pct.log"
cmd="$1"; shift
case "$cmd" in
  status) if [ "${SIM_EI_KAYNNISSA:-}" = "$1" ]; then echo "status: stopped"; else echo "status: running"; fi ;;
  listsnapshot) f="$SIM/snap-$1"; [ -f "$f" ] && cat "$f"; echo '`-> current   You are here!' ;;
  snapshot) echo "snapshot $1 $2" >> "$LOG"; printf '%s\n' "\`-> $2 $(date '+%Y-%m-%d %H:%M:%S') sim" >> "$SIM/snap-$1" ;;
  rollback) echo "rollback $*" >> "$LOG" ;;
  push) echo "push $1 $2 $3" >> "$LOG"; if [ -n "${SIM_PUSH_VIKA:-}" ]; then case "$3" in *"$SIM_PUSH_VIKA") exit 1 ;; esac; fi; cp "$2" "$3" ;;
  pull) echo "pull $1 $2 $3" >> "$LOG"; cp "$2" "$3" ;;
  exec) ct="$1"; shift; [ "${1:-}" = "--" ] && shift; echo "exec $ct $*" >> "$LOG"; exec "$@" ;;
  *) echo "pct-tynka: tuntematon $cmd" >&2; exit 9 ;;
esac
'''

SYSTEMCTL = r'''#!/usr/bin/env bash
S="$SIM/sysd"; mkdir -p "$S"
case "$1" in
  is-active) if [ -f "$S/$2.active" ]; then cat "$S/$2.active"; else echo inactive; fi ;;
  is-enabled) if [ -f "$S/$2.enabled" ]; then cat "$S/$2.enabled"; else echo disabled; fi ;;
  stop) echo "systemctl stop $2" >> "$SIM/sysd.log"; echo inactive > "$S/$2.active" ;;
  start) echo "systemctl start $2" >> "$SIM/sysd.log"; echo active > "$S/$2.active" ;;
  daemon-reload) echo "systemctl daemon-reload" >> "$SIM/sysd.log" ;;
  list-timers) exec python3 -c 'import json, os, sys, time
d = sys.argv[1]; out = []
for f in sorted(os.listdir(d)):
    if f.endswith(".next"):
        out.append({"unit": f[:-5], "next": int((time.time() + float(open(os.path.join(d, f)).read())) * 1e6)})
print(json.dumps(out))' "$S" ;;
  *) exit 0 ;;
esac
'''

RUNUSER = r'''#!/usr/bin/env bash
# runuser -u KAYTTAJA [--] KOMENTO [ARGUMENTIT...]: ajaa komennon oikeasti (kayttajanvaihtoa ei simuloida), kirjaa kayttajan ja komennon alun.
[ "${1:-}" = "-u" ] && [ -n "${2:-}" ] || { echo "runuser: vaara kayttotapa: $*" >&2; exit 1; }
u="$2"; shift 2
[ "${1:-}" = "--" ] && shift
[ -n "${1:-}" ] || { echo "runuser: komento puuttuu" >&2; exit 1; }
case "$1" in -*) echo "runuser: virheellinen valitsin: $1" >&2; exit 1 ;; esac
command -v -- "$1" >/dev/null 2>&1 || { echo "runuser: failed to execute $1: No such file or directory" >&2; exit 127; }
echo "runuser $u $1 $2 $3" >> "$SIM/runuser.log"
exec "$@"
'''

KAUPPA_ASENNA = r'''#!/usr/bin/env bash
set -e
K="$FAKE_KOODI"; PKG="$1"; VER="$2"
rm -rf "$K.uusi"; mkdir "$K.uusi"
tar xzf "$PKG" -C "$K.uusi" --strip-components=1
mv "$K" "$K.prev-fake-$VER"; mv "$K.uusi" "$K"
echo "ASENNUS OK: $VER (fake)"
'''

KAUPPA_PALAUTA = r'''#!/usr/bin/env bash
set -e
K="$FAKE_KOODI"
P=$(ls -dt "$K".prev-* | head -1)
mv "$K" "$K.palautettu-fake"; mv "$P" "$K"
echo "PALAUTETTU: $P"
'''


def puu(polku):
    """Sisalto + nimet + oikeudet (ei aikaleimoja, ei omistajaa): riippumaton tarkistus tilan/koodin muuttumattomuudelle."""
    polku = str(polku)
    if not os.path.exists(polku):
        return "puuttuu"
    rivit = []
    for kansio, dirs, files in os.walk(polku):
        rel = os.path.relpath(kansio, polku)
        rivit.append("D:%s:%o" % (rel, os.stat(kansio).st_mode & 0o7777))
        for f in files:
            fp = os.path.join(kansio, f)
            rivit.append("F:%s:%o:%s" % (os.path.join(rel, f), os.lstat(fp).st_mode & 0o7777, hashlib.sha256(open(fp, "rb").read()).hexdigest()))
    return hashlib.sha256("\n".join(sorted(rivit)).encode()).hexdigest()


def loppurivi(ulos):
    rivit = [r for r in ulos.splitlines() if r.strip()]
    return rivit[-1] if rivit else ""


class Laatikko:
    """Yksi hiekkalaatikko: isanta (JH), kontti (sim/), git-repo ja pakettikansio."""

    def __init__(self, testi, ct="119"):
        self.testi = testi
        self.ct = ct
        self.t = Path(tempfile.mkdtemp(prefix="julkaise-testi-"))
        testi.addCleanup(shutil.rmtree, str(self.t), True)
        t = self.t
        self.sim, self.jh, self.bin, self.repo, self.bare, self.pak = t / "sim", t / "jh", t / "bin", t / "repo", t / "origin.git", t / "paketit"
        for d in (self.sim, self.jh, self.bin, self.pak, t / "tmp", t / "sysd"):
            d.mkdir()
        os.chmod(self.jh, 0o700)
        self.koodi, self.tila, self.konfig, self.yksikot = self.sim / "opt" / "app", self.sim / "var" / "lib" / "app", self.sim / "etc" / "app", self.sim / "etc" / "systemd" / "system"
        for d in (self.koodi, self.tila / "alihak", self.konfig, self.yksikot):
            d.mkdir(parents=True)
        for nimi, sisalto in (("PCT", PCT), ("pct", PCT), ("systemctl", SYSTEMCTL), ("runuser", RUNUSER), ("kauppa-asenna", KAUPPA_ASENNA), ("kauppa-palauta", KAUPPA_PALAUTA)):
            if nimi == "PCT":
                continue
            (self.bin / nimi).write_text(sisalto)
            os.chmod(self.bin / nimi, 0o755)
        # kohteen nykyinen (ei-julkaistu) koodi, tila ja konfiguraatio
        (self.koodi / "run.py").write_text("VERSIO = 0\n")
        (self.koodi / "config.py").write_text("X = 0\n")
        (self.koodi / "VANHA.txt").write_text("ennen julkaisutyokalua\n")
        (self.tila / "data.db").write_bytes(os.urandom(4096))
        (self.tila / "alihak" / "loki.txt").write_text("rivi 1\nrivi 2\n")
        (self.tila / "app.lock").write_text("")
        (self.konfig / "env").write_text("TOKEN=EI-OIKEA-SALAINEN-KONFIGURAATIOARVO-123\n")
        os.chmod(self.konfig / "env", 0o600)
        (t / "sysd" / "app.timer.active").write_text("active\n")
        (t / "sysd" / "app.timer.enabled").write_text("enabled\n")
        (t / "sysd" / "app.timer.next").write_text("3600\n")
        # git: repo + bare origin
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=t)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        self.git("remote", "add", "origin", str(self.bare))
        self.versio = 0
        self.commit(1)
        self.kirjoita_konf()
        self.env = dict(os.environ, PCT=str(self.bin / "pct"), JULKAISU_HAKEMISTO=str(self.jh), JULKAISU_YKSIKKOHAKEMISTO=str(self.yksikot),
                        JULKAISU_ASENTAJAHAKEMISTO=str(self.bin), SIM=str(t), PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        TMPDIR=str(t / "tmp"), FAKE_KOODI=str(self.koodi), GIT_CONFIG_GLOBAL=os.devnull)

    # ---- git ----
    def git(self, *args, cwd=None):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", "-C", str(cwd or self.repo), *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
        return r.stdout.strip()

    def commit(self, versio, testit_ok=True, savu_ok=True, lisaa=None, poista=(), viesti=None):
        """Kirjoittaa sovelluksen version, committaa ja pushaa origin/main:iin. Palauttaa commit-tunnisteen."""
        tiedostot = {
            "run.py": "VERSIO = %d\n" % versio,
            "config.py": "import os\nif os.environ.get('APP_KIRJOITA'):\n    open(os.path.join(os.environ['APP_KIRJOITA'], 'muuttui.txt'), 'w').write('x')\n"
                         + ("" if savu_ok else "raise RuntimeError('savukoe')\n"),
            "tests/test_app.py": "import unittest\nimport run\n\n\nclass T(unittest.TestCase):\n    def test_versio(self):\n        self.assertEqual(run.VERSIO, %d)\n" % (versio if testit_ok else -1),
            "README.md": "# sovellus %d\n" % versio,
            ".github/ci.yml": "x: %d\n" % versio,
            "VERSION": "%d\n" % versio,
        }
        tiedostot.update(lisaa or {})
        for p, s in tiedostot.items():
            f = self.repo / p
            f.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(s, bytes):
                f.write_bytes(s)
            else:
                f.write_text(s)
        for p in poista:
            (self.repo / p).unlink()
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", viesti or "versio %d" % versio)
        self.git("push", "-q", "-f", "origin", "main")
        self.versio = versio
        return self.git("rev-parse", "HEAD")

    # ---- konfiguraatio ----
    def kirjoita_konf(self, nimi="app", **yli):
        arvot = dict(KOHDE="app", CT=self.ct, REPO="owner/app", REPO_URL="file://%s" % self.bare, JULKINEN="1", HAARA="main", KOODI=str(self.koodi),
                     KOODI_MODE="755", TILA=str(self.tila), KONFIG=str(self.konfig), PALVELUT="app", AJASTIMET="app.timer", LUKOT=str(self.tila / "app.lock"),
                     IKKUNA_S="60", ASENTAJA="yleinen", POIS="README.md .github", VAADITUT="run.py", TESTI_TAPA="kontti", TESTI_ARGUMENTIT="discover -s tests",
                     SAVU_MODUULIT="run config", PREV_MAARA="3")
        arvot.update(yli)
        teksti = "# testikonfiguraatio\n" + "".join("%s=%s\n" % (k, v) for k, v in arvot.items() if v != "")
        (self.jh / ("%s.conf" % nimi)).write_text(teksti)

    # ---- ajo ----
    def aja(self, *args, kohde="app", skripti=None, env=None):
        julkaisu = "JULKAISU_TS" not in (env or {}) and not any(str(a) in ("--peruuta", "--tila", "--itsetesti", "--katastrofi-tila", "--katastrofi-palauta") for a in args)
        if julkaisu and int(time.time()) == getattr(self, "_viim_s", None):
            time.sleep(1.05 - time.time() % 1)        # edellinen ajo sai saman sekunnin: prev-nimi tormaisi
        r = subprocess.run(["bash", str(skripti or JULKAISE), kohde, *[str(a) for a in args]], env=env or self.env, capture_output=True, text=True, timeout=300)
        self._viim_s = int(time.time())
        return r.returncode, r.stdout + r.stderr

    def paketoi(self, ref="HEAD", kohde="app"):
        r = subprocess.run(["bash", str(PAKETOI), str(self.repo), ref, kohde, str(self.pak)], capture_output=True, text=True, env=self.env)
        if r.returncode != 0:
            raise AssertionError("paketoi: %s %s" % (r.stdout, r.stderr))
        commit = re.search(r"commit\s*: ([0-9a-f]{40})", r.stdout).group(1)
        sha = re.search(r"sha256\s*: ([0-9a-f]{64})", r.stdout).group(1)
        return self.pak / ("%s-%s.tgz" % (kohde, commit[:8])), commit, sha

    def julkaise(self, *liput, ref="HEAD", ensi=False, paketti=None, commit=None, sha=None, kohde="app"):
        p, c, s = self.paketoi(ref)
        args = [paketti or p, commit or c, sha or s] + (["--ensijulkaisu"] if ensi else []) + list(liput)
        return self.aja(*args, kohde=kohde)

    # ---- havainnot ----
    def kirjoitukset(self):
        rivit = []
        for nimi in ("pct.log", "sysd.log"):
            f = self.t / nimi
            if f.exists():
                rivit += [r for r in f.read_text().splitlines() if r.startswith(("snapshot", "rollback", "push", "pull", "systemctl stop", "systemctl start", "systemctl daemon-reload"))]
        return rivit

    def tilannekuva(self):
        snapit = sorted(p.name + hashlib.sha256(p.read_bytes()).hexdigest() for p in self.t.glob("snap-*"))
        return (puu(self.sim), puu(self.t / "sysd"), snapit, self.kirjoitukset())

    def pct_loki(self):
        f = self.t / "pct.log"
        return f.read_text().splitlines() if f.exists() else []

    def previt(self):
        return sorted(p.name for p in self.koodi.parent.glob(self.koodi.name + ".prev-*"))

    def versio_koodissa(self):
        return (self.koodi / "VERSION").read_text().strip() if (self.koodi / "VERSION").exists() else None

    def ensimmainen(self):
        """Tekee ensijulkaisun (jotta kohteella on manifesti) ja palauttaa tuloksen."""
        rc, ulos = self.julkaise(ensi=True)
        assert rc == 0, ulos
        return ulos


@unittest.skipUnless(LINUX, "julkaise.sh vaatii Linuxin (bash 5, GNU-tyokalut)")
class Pohja(unittest.TestCase):
    def setUp(self):
        self.l = Laatikko(self)

    def assertEpa(self, rc, ulos, osa=None):
        self.assertNotEqual(rc, 0, ulos)
        self.assertTrue(loppurivi(ulos).startswith(EPA + ":"), loppurivi(ulos))
        if osa:
            self.assertIn(osa, ulos)

    def assertOk(self, rc, ulos, osa=None):
        self.assertEqual(rc, 0, ulos)
        self.assertTrue(loppurivi(ulos).startswith("OK:"), loppurivi(ulos))
        if osa:
            self.assertIn(osa, loppurivi(ulos))

    def assertEiMuutoksia(self, ennen):
        self.assertEqual(self.l.tilannekuva(), ennen)


class PaketoiTesti(Pohja):
    def test_paketti_commit_ja_sha(self):
        p, c, s = self.l.paketoi()
        self.assertEqual(c, self.l.git("rev-parse", "HEAD"))
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), s)
        luettu = subprocess.run("gzip -dc %s | git get-tar-commit-id" % p, shell=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(luettu, c)
        nimet = tarfile.open(p).getnames()
        self.assertTrue(all(n.startswith("app-%s/" % c[:8]) or n == "app-%s" % c[:8] for n in nimet))
        self.assertIn("app-%s/tests/test_app.py" % c[:8], nimet)
        self.assertIn("app-%s/.github/ci.yml" % c[:8], nimet)

    def test_deterministinen(self):
        p1, c1, s1 = self.l.paketoi()
        p1.unlink()
        p2, c2, s2 = self.l.paketoi()
        self.assertEqual((c1, s1), (c2, s2))

    def test_virheet(self):
        r = subprocess.run(["bash", str(PAKETOI), str(self.l.repo), "ei-ole-refia", "app", str(self.l.pak)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(loppurivi(r.stdout).startswith(EPA))
        r = subprocess.run(["bash", str(PAKETOI), str(self.l.repo), "HEAD", "Vaara Nimi", str(self.l.pak)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_tulostaa_komennot_ja_karkitarkistuksen(self):
        r = subprocess.run(["bash", str(PAKETOI), str(self.l.repo), "HEAD", "app", str(self.l.pak)], capture_output=True, text=True)
        self.assertIn("commit on origin/main:n karki", r.stdout)
        self.assertIn("scp ", r.stdout)
        self.assertIn("--kuiva", r.stdout)
        self.l.commit(2)
        r = subprocess.run(["bash", str(PAKETOI), str(self.l.repo), "HEAD~1", "app", str(self.l.pak)], capture_output=True, text=True)
        self.assertIn("EI ole origin/main:n karki", r.stdout)


class KonfiguraatioTesti(Pohja):
    def test_toimitetut_konfiguraatiot_kelpaavat(self):
        kansio = self.l.t / "oikeat"
        shutil.copytree(ISANTA / "julkaisu", kansio)
        env = dict(self.l.env, JULKAISU_HAKEMISTO=str(kansio))
        r = subprocess.run(["bash", str(JULKAISE), "--itsetesti"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(loppurivi(r.stdout).startswith("OK:"))
        self.assertIn("3 konfiguraatiota", loppurivi(r.stdout))

    def test_toimitetut_konfiguraatiot_kayttavat_odotettuja_kohteita(self):
        tekstit = {p.stem: p.read_text() for p in (ISANTA / "julkaisu").glob("*.conf")}
        self.assertEqual(sorted(tekstit), ["infra-automaatio", "kauppatiimi", "yojono-ajuri"])
        self.assertIn("CT=119", tekstit["kauppatiimi"])
        self.assertIn("CT=118", tekstit["infra-automaatio"])
        self.assertIn("CT=115", tekstit["yojono-ajuri"])
        self.assertIn("JULKINEN=0", tekstit["infra-automaatio"])            # yksityinen repo
        self.assertIn("TESTI_TAPA=ct115", tekstit["infra-automaatio"])      # testeja ei ajeta CT118:ssa
        self.assertIn("ASENTAJA=kauppa-asenna", tekstit["kauppatiimi"])      # CT119 vaiheessa 2
        for teksti in tekstit.values():
            self.assertIn("TILA=", teksti)
            self.assertIn("KONFIG=", teksti)
            self.assertNotRegex(teksti, r"(?i)(token|secret|password)\s*=\s*\S")     # ei salaisuuksia

    def test_virheelliset_konfiguraatiot_hylataan(self):
        huonot = {
            "komentokorvaus": dict(PALVELUT="app $(touch /tmp/x)"),
            "backtick": dict(PALVELUT="app `id`"),
            "puolipiste": dict(PALVELUT="app; id"),
            "putki": dict(TESTI_ARGUMENTIT="discover | sh"),
            "kaksi_pistetta": dict(TILA="/tmp/../etc"),
            "vaara_asentaja": dict(ASENTAJA="muu"),
            "vaara_testi_tapa": dict(TESTI_TAPA="tuotanto"),
            "ct115_samakuin_ct": dict(TESTI_TAPA="ct115", TESTI_CT="119"),
            "ct115_ilman_ct": dict(TESTI_TAPA="ct115"),
            "ct_ei_kolminumeroinen": dict(CT="12"),
            "savumoduulit_puuttuu": dict(SAVU_MODUULIT=""),
            "prev_nolla": dict(PREV_MAARA="0"),
            "tila_puuttuu": dict(TILA=""),
            "suhteellinen_polku": dict(KOODI="opt/app"),
        }
        for nimi, yli in huonot.items():
            with self.subTest(nimi):
                self.l.kirjoita_konf(**yli)
                rc, ulos = self.l.aja("--tila")
                self.assertEpa(rc, ulos)
                self.assertFalse((self.l.t / "x").exists())
                self.assertEqual(self.l.kirjoitukset(), [])

    def test_tuntematon_avain_ja_rikkinainen_rivi(self):
        self.l.kirjoita_konf()
        konf = self.l.jh / "app.conf"
        for lisa in ("TUNTEMATON=1\n", "tama ei ole avain=arvo\n", "rm -rf /\n"):
            with self.subTest(lisa=lisa):
                konf.write_text(konf.read_text() + lisa)
                rc, ulos = self.l.aja("--tila")
                self.assertEpa(rc, ulos)
                self.l.kirjoita_konf()

    def test_kohteen_nimi_ja_tiedoston_nimi_tasmaavat(self):
        self.l.kirjoita_konf(nimi="muu")
        rc, ulos = self.l.aja("--tila", kohde="muu")
        self.assertEpa(rc, ulos, "KOHDE")
        rc, ulos = self.l.aja("--tila", kohde="../app")
        self.assertEpa(rc, ulos)
        rc, ulos = self.l.aja("--tila", kohde="ei-ole")
        self.assertEpa(rc, ulos)

    def test_konfiguraatiota_ei_suoriteta(self):
        self.l.kirjoita_konf()
        konf = self.l.jh / "app.conf"
        konf.write_text(konf.read_text() + "\ntouch %s\n" % (self.l.t / "suoritettiin"))
        self.l.aja("--tila")
        self.assertFalse((self.l.t / "suoritettiin").exists())


class AsennaJulkaiseTesti(Pohja):
    def paketti_isanta(self):
        """agentti-pohjan kaltainen paketti, jossa on isanta/-tiedostot (kopioidaan oikeat tiedostot repon testiin)."""
        for p in ("julkaise.sh", "paketoi.sh"):
            (self.l.repo / "isanta").mkdir(exist_ok=True)
            shutil.copy(ISANTA / p, self.l.repo / "isanta" / p)
        shutil.copytree(ISANTA / "julkaisu", self.l.repo / "isanta" / "julkaisu", dirs_exist_ok=True)
        self.l.git("add", "-A")
        self.l.git("commit", "-q", "-m", "isanta")
        return self.l.paketoi(kohde="agentti-pohja")

    def aja(self, *args, kohde_juuri=None):
        env = dict(self.l.env, JULKAISU_ASENNUSKOHDE=str(kohde_juuri or self.l.t / "root"))
        r = subprocess.run(["bash", str(ASENNA), *[str(a) for a in args]], env=env, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_asentaa_tiedostot_oikeilla_oikeuksilla(self):
        p, c, s = self.paketti_isanta()
        rc, ulos = self.aja(p, s, c)
        self.assertOk(rc, ulos)
        juuri = self.l.t / "root"
        self.assertEqual(oct((juuri / "julkaise.sh").stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct((juuri / "paketoi.sh").stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct((juuri / "julkaisu").stat().st_mode & 0o777), "0o700")
        for konf in (juuri / "julkaisu").glob("*.conf"):
            self.assertEqual(oct(konf.stat().st_mode & 0o777), "0o600")
        self.assertEqual((juuri / "julkaise.sh").read_bytes(), JULKAISE.read_bytes())
        self.assertEqual(len(list((juuri / "julkaisu").glob("*.conf"))), 3)
        self.assertIn("sha256", ulos)
        self.assertIn("itsetesti", ulos + "itsetesti")

    def test_vain_tarkista_ei_asenna(self):
        p, c, s = self.paketti_isanta()
        rc, ulos = self.aja(p, s, c, "--vain-tarkista")
        self.assertOk(rc, ulos, "esitarkistukset")
        self.assertFalse((self.l.t / "root").exists())

    def test_vaara_sha_tai_commit_keskeyttaa_ennen_muutoksia(self):
        p, c, s = self.paketti_isanta()
        for args, osa in (((p, "0" * 64, c), "sha256"), ((p, s, "0" * 40), "commit"), ((p, "abc", c), "virheellinen"), ((self.l.t / "ei-ole.tgz", s, c), "tavallinen tiedosto")):
            with self.subTest(osa=osa):
                rc, ulos = self.aja(*args)
                self.assertEpa(rc, ulos, osa)
                self.assertFalse((self.l.t / "root").exists())

    def test_uusinta_varmuuskopioi_eroavan_ja_on_idempotentti(self):
        p, c, s = self.paketti_isanta()
        self.assertOk(*self.aja(p, s, c))
        juuri = self.l.t / "root"
        (juuri / "julkaise.sh").write_text("#!/bin/bash\n# muokattu kasin\n")
        rc, ulos = self.aja(p, s, c)
        self.assertOk(rc, ulos)
        self.assertEqual(len(list(juuri.glob("julkaise.sh.ennen-*"))), 1)
        self.assertIn("muokattu kasin", list(juuri.glob("julkaise.sh.ennen-*"))[0].read_text())
        self.assertEqual((juuri / "julkaise.sh").read_bytes(), JULKAISE.read_bytes())

    def test_paketti_ilman_julkaise_sh_hylataan(self):
        p, c, s = self.l.paketoi()      # tavallinen sovelluspaketti
        rc, ulos = self.aja(p, s, c)
        self.assertEpa(rc, ulos, "julkaise.sh")
        self.assertFalse((self.l.t / "root").exists())


class JulkaisuTesti(Pohja):
    def test_ensijulkaisu_vaatii_lipun(self):
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "--ensijulkaisu")
        self.assertEiMuutoksia(ennen)

    def test_ensijulkaisu_ja_tila_ennallaan(self):
        tila0, konf0, koodi0 = puu(self.l.tila), puu(self.l.konfig), puu(self.l.koodi)
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos, "tila ja konfiguraatio ennallaan")
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertEqual(puu(self.l.tila), tila0, "tila muuttui")
        self.assertEqual(puu(self.l.konfig), konf0, "konfiguraatio muuttui")
        self.assertNotEqual(puu(self.l.koodi), koodi0)
        # edellinen versio sailyy sellaisenaan prev-kansiossa (+ manifesti ja tieto)
        (prev,) = self.l.previt()
        self.assertEqual((self.l.koodi.parent / prev / "run.py").read_text(), "VERSIO = 0\n")
        self.assertTrue((self.l.koodi.parent / prev / ".julkaisu-manifesti").exists())
        self.assertIn("lahtotila=ensijulkaisu", (self.l.koodi.parent / prev / ".julkaisu-tieto").read_text())

    def test_asennettu_koodi_vastaa_pakettia_ja_poistot_toteutuvat(self):
        self.l.ensimmainen()
        tiedot = (self.l.koodi / ".julkaisu-tieto").read_text()
        self.assertIn("commit=%s" % self.l.git("rev-parse", "HEAD"), tiedot)
        self.assertIn("kohde=app", tiedot)
        self.assertTrue((self.l.koodi / "tests" / "test_app.py").exists())
        self.assertFalse((self.l.koodi / "README.md").exists())       # POIS
        self.assertFalse((self.l.koodi / ".github").exists())          # POIS
        self.assertFalse((self.l.koodi / "VANHA.txt").exists())        # vanha tiedosto ei jaa uuteen koodiin
        r = subprocess.run("cd %s && sha256sum -c --quiet .julkaisu-manifesti" % self.l.koodi, shell=True, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_snapshot_otetaan_ja_ajastimet_palautetaan(self):
        ulos = self.l.ensimmainen()
        self.assertTrue(any(r.startswith("snapshot 119 pre-app-") for r in self.l.pct_loki()))
        sysd = (self.l.t / "sysd" / "app.timer.active").read_text().strip()
        self.assertEqual(sysd, "active")
        loki = (self.l.t / "sysd.log").read_text()
        self.assertIn("systemctl stop app.timer", loki)
        self.assertIn("systemctl start app.timer", loki)
        self.assertLess(loki.index("stop app.timer"), loki.index("start app.timer"))
        self.assertIn("ajastin app.timer k", ulos)

    def test_konfiguraation_sisaltoa_ei_tulosteta(self):
        ulos = self.l.ensimmainen()
        self.assertNotIn("EI-OIKEA-SALAINEN", ulos)
        loki = (self.l.jh / "loki.log").read_text()
        self.assertNotIn("EI-OIKEA-SALAINEN", loki)
        for ajo in (self.l.jh / "ajo").glob("*.log"):
            self.assertNotIn("EI-OIKEA-SALAINEN", ajo.read_text())

    def test_toinen_julkaisu_ilman_lippua_ja_jo_asennettu(self):
        self.l.ensimmainen()
        self.l.commit(2)
        tila0 = puu(self.l.tila)
        rc, ulos = self.l.julkaise()
        self.assertOk(rc, ulos)
        self.assertEqual(self.l.versio_koodissa(), "2")
        self.assertEqual(len(self.l.previt()), 2)
        self.assertEqual(puu(self.l.tila), tila0)
        snapit = len([r for r in self.l.pct_loki() if r.startswith("snapshot")])
        rc, ulos = self.l.julkaise()          # sama commit uudelleen
        self.assertOk(rc, ulos, "jo asennettu")
        self.assertEqual(len([r for r in self.l.pct_loki() if r.startswith("snapshot")]), snapit)
        self.assertEqual(len(self.l.previt()), 2)

    def test_uudelleen_pakottaa(self):
        self.l.ensimmainen()
        rc, ulos = self.l.julkaise("--uudelleen")
        self.assertOk(rc, ulos, "asennettu")
        self.assertEqual(len(self.l.previt()), 2)

    def test_vain_kolme_prev_versiota_sailyy(self):
        self.l.ensimmainen()
        for v in range(2, 7):
            self.l.commit(v)
            rc, ulos = self.l.julkaise()
            self.assertOk(rc, ulos)
            time.sleep(1.1)            # prev-nimessa on sekuntitarkkuus
        self.assertEqual(len(self.l.previt()), 3)
        self.assertEqual(self.l.versio_koodissa(), "6")

    def test_loki_kirjaa_julkaisun_ja_ohituslipun(self):
        self.l.ensimmainen()
        self.l.commit(2)
        self.l.commit(3)
        p, c, s = self.l.paketoi("HEAD~1")        # ei main:n karki
        rc, ulos = self.l.aja(p, c, s)
        self.assertEpa(rc, ulos, "ei ole main:n karki")
        rc, ulos = self.l.aja(p, c, s, "--salli-ei-karki")
        self.assertOk(rc, ulos)
        self.assertIn("EI ole main:n karki", ulos)
        loki = (self.l.jh / "loki.log").read_text()
        self.assertIn("POIKKEUS=salli-ei-karki", loki)
        self.assertIn("JULKAISU OK", loki)
        self.assertIn("ESITARKISTUS EP", loki)


class EsitarkistusTesti(Pohja):
    """Jokainen esitarkistusvirhe keskeyttaa ENNEN muutoksia: kontti, tila, ajastimet ja snapshotit ennallaan."""

    def ennen(self):
        self.l.ensimmainen()
        self.l.commit(2)
        return self.l.tilannekuva()

    def tarkista(self, osa, *liput, **kw):
        ennen = self.ennen() if not hasattr(self, "_ennen") else self._ennen
        rc, ulos = self.l.julkaise(*liput, **kw)
        self.assertEpa(rc, ulos, osa)
        self.assertEqual(self.l.tilannekuva(), ennen, "esitarkistusvirhe ei saa muuttaa mitaan")
        self.assertIn("Mitaan ei muutettu", ulos)
        return ulos

    def test_vaara_sha(self):
        self.tarkista("paketin sha256 ei tasmaa", sha="0" * 64)

    def test_vaara_commit(self):
        self.tarkista("paketin commit", commit="1" * 40)

    def test_virheellinen_commit_ja_sha_muoto(self):
        self.tarkista("commit pitaa olla 40", commit="abc")
        self.tarkista("sha256 pitaa olla 64", sha="abc")

    def test_paketti_ei_ole_tiedosto(self):
        self.tarkista("tavallinen tiedosto", paketti=self.l.t / "ei-ole.tgz")

    def test_paketissa_ei_commit_tunnistetta(self):
        p, c, s = self.l.paketoi()
        vanha = self.l.t / "ilman-commit.tgz"
        with tarfile.open(p) as lahde, tarfile.open(vanha, "w:gz") as ulos:          # uudelleenpakkaus ilman pax-otsikkoa
            for jasen in lahde.getmembers():
                ulos.addfile(jasen, lahde.extractfile(jasen) if jasen.isfile() else None)
        self.tarkista("commit-tunnistetta", paketti=vanha, sha=hashlib.sha256(vanha.read_bytes()).hexdigest())

    def test_ei_ole_main_n_karki_julkiselle_repolle(self):
        self.ennen()
        self.l.commit(3)
        self._ennen = self.l.tilannekuva()
        self.tarkista("ei ole main:n karki", ref="HEAD~1")

    def test_yksityinen_repo_ei_vaadi_karkea(self):
        self.l.kirjoita_konf(JULKINEN="0")
        self.l.ensimmainen()
        self.l.commit(2)
        self.l.commit(3)
        rc, ulos = self.l.julkaise(ref="HEAD~1")
        self.assertOk(rc, ulos)
        self.assertIn("yksityinen repo", ulos)

    def test_palvelu_kaynnissa(self):
        self.ennen()
        (self.l.t / "sysd" / "app.service.active").write_text("active\n")
        self._ennen = self.l.tilannekuva()
        self.tarkista("on 'active'")

    def test_prosessi_kaynnissa(self):
        self.l.kirjoita_konf(PROSESSI_KUVIO="julkaise-testi-uniikki-prosessi")
        self.ennen()
        self._ennen = self.l.tilannekuva()
        prosessi = subprocess.Popen(["bash", "-c", "exec -a julkaise-testi-uniikki-prosessi sleep 30"])
        self.addCleanup(prosessi.kill)
        time.sleep(0.3)
        self.tarkista("prosessi on kaynnissa")

    def test_lukko_varattu(self):
        self.ennen()
        self._ennen = self.l.tilannekuva()
        pito = subprocess.Popen(["flock", "-x", str(self.l.tila / "app.lock"), "sleep", "30"])
        self.addCleanup(pito.kill)
        time.sleep(0.3)
        self.tarkista("lukko on varattu")

    def test_koodi_poikkeaa_manifestista(self):
        self.ennen()
        (self.l.koodi / "run.py").write_text("VERSIO = 666  # kasin muokattu\n")
        self._ennen = self.l.tilannekuva()
        self.tarkista("poikkeaa julkaisumanifestistaan")

    def test_ei_manifestia_ilman_ensijulkaisulippua(self):
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "ensijulkaisu")
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_snapshot_raja_taynna(self):
        self.ennen()
        (self.l.t / ("snap-%s" % self.l.ct)).write_text("".join("`-> vanha%d 2026-01-01 00:00:00 x\n" % i for i in range(20)))
        self._ennen = self.l.tilannekuva()
        ulos = self.tarkista("raja 20 taynna")
        self.assertIn("20 snapshotia", ulos)

    def test_snapshot_raja_19_riittaa(self):
        self.ennen()
        (self.l.t / ("snap-%s" % self.l.ct)).write_text("".join("`-> vanha%d 2026-01-01 00:00:00 x\n" % i for i in range(19)))
        rc, ulos = self.l.julkaise()
        self.assertOk(rc, ulos)

    def test_ajastin_liian_lahella(self):
        self.ennen()
        (self.l.t / "sysd" / "app.timer.next").write_text("5\n")
        self._ennen = self.l.tilannekuva()
        self.tarkista("ajastin laukeaa")

    def test_ajastin_riittavan_kaukana(self):
        self.ennen()
        (self.l.t / "sysd" / "app.timer.next").write_text("90\n")
        rc, ulos = self.l.julkaise()
        self.assertOk(rc, ulos)

    def test_edellisen_ajon_jaanne(self):
        self.ennen()
        (self.l.koodi.parent / (self.l.koodi.name + ".uusi")).mkdir()
        self._ennen = self.l.tilannekuva()
        self.tarkista(".uusi")

    def test_kontti_ei_kaynnissa(self):
        self.ennen()
        self._ennen = self.l.tilannekuva()
        env = dict(self.l.env, SIM_EI_KAYNNISSA=self.l.ct)
        p, c, s = self.l.paketoi()
        rc, ulos = self.l.aja(p, c, s, env=env)
        self.assertEpa(rc, ulos, "ei ole kaynnissa")
        self.assertEqual(self.l.tilannekuva(), self._ennen)

    def test_ct115_testikontti_ei_kaynnissa(self):
        self.l.kirjoita_konf(TESTI_TAPA="ct115", TESTI_CT="115")
        self.ennen()
        self._ennen = self.l.tilannekuva()
        env = dict(self.l.env, SIM_EI_KAYNNISSA="115")
        p, c, s = self.l.paketoi()
        rc, ulos = self.l.aja(p, c, s, env=env)
        self.assertEpa(rc, ulos, "CT115 ei ole kaynnissa")
        self.assertEqual(self.l.tilannekuva(), self._ennen)

    def _tee_paketti(self, nimi, jasenet):
        commit = self.l.git("rev-parse", "HEAD")
        polku = self.l.pak / nimi
        with tarfile.open(polku, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as tf:
            for polku_sisalla, sisalto, tyyppi in jasenet:
                ti = tarfile.TarInfo(polku_sisalla)
                if tyyppi == "hak":
                    ti.type = tarfile.DIRTYPE; ti.mode = 0o755
                    tf.addfile(ti)
                elif tyyppi == "linkki":
                    ti.type = tarfile.SYMTYPE; ti.linkname = sisalto
                    tf.addfile(ti)
                else:
                    data = sisalto.encode()
                    ti.size = len(data); ti.mode = 0o644
                    tf.addfile(ti, io.BytesIO(data))
        return polku, commit, hashlib.sha256(polku.read_bytes()).hexdigest()

    def _vaarallinen(self, osa, jasenet):
        self.ennen()
        self._ennen = self.l.tilannekuva()
        p, c, s = self._tee_paketti("vaara.tgz", jasenet)
        # varmista, etta commit-tunniste luetaan (muuten testi ei osuisi poluntarkistukseen)
        luettu = subprocess.run("gzip -dc %s | git get-tar-commit-id" % p, shell=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(luettu, c)
        rc, ulos = self.l.aja(p, c, s)
        self.assertEpa(rc, ulos, osa)
        self.assertEqual(self.l.tilannekuva(), self._ennen)

    def test_paketissa_linkki(self):
        self._vaarallinen("linkkeja", [("x/", None, "hak"), ("x/run.py", "VERSIO = 9\n", "tied"), ("x/vaara", "/etc/passwd", "linkki")])

    def test_paketissa_ylahakemistoon_karkaava_polku(self):
        self._vaarallinen("vaarallisia polkuja", [("x/run.py", "VERSIO = 9\n", "tied"), ("../ulos.txt", "x", "tied")])

    def test_paketissa_absoluuttinen_polku(self):
        self._vaarallinen("vaarallisia polkuja", [("/tmp/ulos.txt", "x", "tied"), ("x/run.py", "VERSIO = 9\n", "tied")])

    def test_paketissa_kaksi_ylatason_hakemistoa(self):
        self._vaarallinen("yksi ylatason hakemisto", [("a/run.py", "VERSIO = 9\n", "tied"), ("b/run.py", "VERSIO = 9\n", "tied")])

    def test_paketista_puuttuu_vaadittu_tiedosto(self):
        self._vaarallinen("puuttuu vaadittu tiedosto", [("x/muu.py", "VERSIO = 9\n", "tied")])

    def test_paketissa_salaisuuden_nakoinen_tiedosto(self):
        self._vaarallinen("salaisuuden nakoisia", [("x/run.py", "VERSIO = 9\n", "tied"), ("x/asetukset.env", "T=1\n", "tied")])

    def test_paketissa_yksityinen_avain(self):
        runko = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
        self._vaarallinen("yksityisen avaimen", [("x/run.py", "VERSIO = 9\n", "tied"),
                                                 ("x/lisa.txt", "-----BEGIN OPENSSH PRIVATE KEY-----\n" + runko + "-----END OPENSSH PRIVATE KEY-----\n", "tied")])

    def test_testifixtuurin_otsikkorivi_ei_ole_avain(self):
        """Oikean agentti-pohjan test_tilannekuva.py sisaltaa pelkan otsikkorivin merkkijonona: ei saa laukaista (ei avainrunkoa)."""
        self.l.commit(2, lisaa={"tests/test_fixtuuri.py": "ARVOT = ('x', '-----BEGIN RSA PRIVATE KEY-----')\n"})
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos)

    def test_tyhja_paketti(self):
        self._vaarallinen("puuttuu vaadittu tiedosto", [("x/", None, "hak")])

    def test_paketissa_liikaa_tiedostoja(self):
        jasenet = [("x/run.py", "VERSIO = 9\n", "tied")] + [("x/t%d.txt" % i, "", "tied") for i in range(3000)]
        self._vaarallinen("liikaa tiedostoja (3001)", jasenet)

    def test_tiedostomaaran_raja_3000_riittaa(self):
        self.ennen()
        jasenet = [("x/", None, "hak"), ("x/run.py", "VERSIO = 9\n", "tied")] + [("x/t%d.txt" % i, "", "tied") for i in range(2998)]
        p, c, s = self._tee_paketti("raja.tgz", jasenet)
        rc, ulos = self.l.aja(p, c, s)
        self.assertNotIn("liikaa tiedostoja", ulos)

    def test_paketti_yli_50_mt_hylataan(self):
        self.ennen()
        self._ennen = self.l.tilannekuva()
        p, c, s = self._tee_paketti("iso.tgz", [("x/run.py", "VERSIO = 9\n", "tied")])
        with open(p, "ab") as f:
            f.write(b"\0" * (50 * 1024 * 1024 + 1))  # gzipin perassa oleva roska: koko ylittaa rajan ennen sisallon tarkistusta
        s = hashlib.sha256(p.read_bytes()).hexdigest()
        rc, ulos = self.l.aja(p, c, s)
        self.assertEpa(rc, ulos, "paketti on liian suuri")
        self.assertEqual(self.l.tilannekuva(), self._ennen)

    def test_levytila_ei_riita(self):
        self.ennen()
        self.l.kirjoita_konf(LEVY_MIN_MT="999999999")
        self._ennen = self.l.tilannekuva()
        p, c, s = self.l.paketoi()
        rc, ulos = self.l.aja(p, c, s)
        self.assertEpa(rc, ulos, "levya vapaana vain")
        self.assertIn("vaaditaan 999999999", ulos)
        self.assertEqual(self.l.tilannekuva(), self._ennen)


class KuivaTesti(Pohja):
    def laitteen_tila(self):
        return (self.l.tilannekuva(), sorted(p.name for p in self.l.jh.iterdir()), sorted(p.name for p in self.l.pak.iterdir()))

    def test_kuiva_julkaisu_ei_muuta_mitaan(self):
        self.l.ensimmainen()
        self.l.commit(2)
        p, c, s = self.l.paketoi()
        jh0 = sorted(p_.name for p_ in self.l.jh.iterdir())
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja(p, c, s, "--kuiva")
        self.assertOk(rc, ulos, "KUIVA-AJO")
        self.assertIn("SUUNNITELMA", ulos)
        self.assertEqual(self.l.tilannekuva(), ennen)
        self.assertEqual(sorted(p_.name for p_ in self.l.jh.iterdir()), jh0)
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertEqual(len([r for r in self.l.pct_loki() if r.startswith("snapshot")]), 1)       # vain ensijulkaisun snapshot

    def test_kuiva_ensijulkaisu_ei_muuta_mitaan_eika_kirjoita_lokia(self):
        p, c, s = self.l.paketoi()
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja(p, c, s, "--ensijulkaisu", "--kuiva")
        self.assertOk(rc, ulos, "KUIVA-AJO")
        self.assertEqual(self.l.tilannekuva(), ennen)
        self.assertEqual(sorted(p_.name for p_ in self.l.jh.iterdir()), ["app.conf"])        # ei loki.log:ia, ei ajo-kansiota
        self.assertEqual(self.l.versio_koodissa(), None)
        self.assertEqual(self.l.kirjoitukset(), [])

    def test_kuiva_ei_aja_testeja_eika_koske_kontin_tmp(self):
        p, c, s = self.l.paketoi()
        rc, ulos = self.l.aja(p, c, s, "--ensijulkaisu", "--kuiva")
        self.assertOk(rc, ulos)
        self.assertFalse([r for r in self.l.pct_loki() if "unittest" in r])
        self.assertFalse([r for r in self.l.pct_loki() if r.startswith(("push", "snapshot"))])

    def test_kuiva_virhe_on_silti_virhe(self):
        p, c, s = self.l.paketoi()
        rc, ulos = self.l.aja(p, c, "0" * 64, "--ensijulkaisu", "--kuiva")
        self.assertEpa(rc, ulos, "sha256")

    def test_kuiva_peruutus(self):
        self.l.ensimmainen()
        self.l.commit(2)
        self.assertOk(*self.l.julkaise())
        ennen = self.l.tilannekuva()
        jh0 = sorted(p_.name for p_ in self.l.jh.iterdir())
        rc, ulos = self.l.aja("--peruuta", "--kuiva")
        self.assertOk(rc, ulos, "KUIVA-AJO")
        self.assertEqual(self.l.tilannekuva(), ennen)
        self.assertEqual(sorted(p_.name for p_ in self.l.jh.iterdir()), jh0)
        self.assertEqual(self.l.versio_koodissa(), "2")

    def _kuiva_ilman_varhaista_poistumista(self, poistettava, *args):
        """Skriptista tehdaan kopio, jossa kuiva-ajon varhainen poistuminen on poistettu: muuta()-suojan pitaa silti estaa kaikki muutokset."""
        lahde = JULKAISE.read_text()
        self.assertEqual(lahde.count(poistettava), 1, poistettava)
        kopio = self.l.t / "ilman-varhaista.sh"
        kopio.write_text(lahde.replace(poistettava, ":", 1))
        ennen = self.l.tilannekuva()
        jh0 = sorted(p_.name for p_ in self.l.jh.iterdir())
        kirj0 = self.l.kirjoitukset()
        rc, ulos = self.l.aja(*args, "--kuiva", skripti=kopio)
        self.assertTrue("KUIVA: ei suoriteta" in ulos or "mitaan ei muutettu" in ulos, "muuta() ei esta muutoksia kuiva-ajossa: " + ulos[-300:])
        self.maxDiff = None
        self.assertEqual(self.l.tilannekuva(), ennen, "kuiva-ajo muutti jotakin, kun varhainen poistuminen puuttui")
        self.assertEqual(sorted(p_.name for p_ in self.l.jh.iterdir()), jh0)
        self.assertEqual(self.l.kirjoitukset(), kirj0, "kuiva-ajo kirjoitti pct/systemctl-komentoja")

    def test_kuiva_muuta_suoja_julkaisussa_pitaa_yksin(self):
        self.l.ensimmainen()
        self.l.commit(2)
        p, c, s = self.l.paketoi()
        self._kuiva_ilman_varhaista_poistumista('ok_loppu "KUIVA-AJO: esitarkistukset lapaisty ja paketti tarkistettu; mitaan ei muutettu."', p, c, s)

    def test_kuiva_muuta_suoja_peruutuksessa_pitaa_yksin(self):
        self.l.ensimmainen()
        self.l.commit(2)
        self.assertOk(*self.l.julkaise())
        self._kuiva_ilman_varhaista_poistumista('ok_loppu "KUIVA-AJO: peruutuksen esitarkistukset lapaisty; mitaan ei muutettu."', "--peruuta")

    def test_kuiva_katastrofi_tila(self):
        (self.l.t / ("snap-%s" % self.l.ct)).write_text("`-> pre-x 2020-01-01 00:00:00 sim\n")
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-x", "--kuiva")
        self.assertOk(rc, ulos, "KUIVA-AJO")
        self.assertIn("TUHOUTUISIVAT", ulos)
        self.assertEqual(self.l.tilannekuva(), ennen)
        self.assertFalse((self.l.jh / "katastrofi").exists())


class KanariaTesti(Pohja):
    """Tahallaan rikkinainen esitarkistus keskeyttaa ajon ennen kirjoituksia."""

    def test_kanaria_lippu(self):
        self.l.ensimmainen()
        self.l.commit(2)
        p, c, s = self.l.paketoi()
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja(p, c, s, "--kanaria")
        self.assertEqual(rc, 0, ulos)
        self.assertTrue(loppurivi(ulos).startswith("OK: KANARIA"), loppurivi(ulos))
        self.assertIn("kirjoituksia: 0", loppurivi(ulos))
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_jokainen_esitarkistusvaihe_keskeyttaa_kun_se_rikotaan(self):
        """Skriptista tehdaan kopio, jossa yksi esitarkistusfunktio epaonnistuu heti: ajo pysahtyy, mitaan ei muutu."""
        self.l.ensimmainen()
        self.l.commit(2)
        p, c, s = self.l.paketoi()
        ennen = self.l.tilannekuva()
        lahde = JULKAISE.read_text()
        funktiot = ["tyokalut_isanta", "paketti_tarkista", "karki_tarkista", "palvelut_vapaana", "ajastin_ikkuna", "snapshot_raja_tarkista", "levy_tarkista", "koodi_tarkista", "paketti_normalisoi",
                    "tyokalut_kontti"]
        for nimi in funktiot:
            with self.subTest(esitarkistus=nimi):
                self.assertIn("%s() {" % nimi, lahde)
                kopio = self.l.t / ("rikki-%s.sh" % nimi)
                rikki, n = re.subn(r"^(%s\(\) \{[^\n]*\n)" % nimi, lambda m: m.group(1) + "  vika \"kanaria: tahallinen virhe (%s)\"\n" % nimi, lahde, count=1, flags=re.M)
                self.assertEqual(n, 1, "funktiota %s ei loytynyt" % nimi)
                kopio.write_text(rikki)
                rc, ulos = self.l.aja(p, c, s, skripti=kopio)
                self.assertEpa(rc, ulos, "kanaria: tahallinen virhe (%s)" % nimi)
                self.assertEqual(self.l.tilannekuva(), ennen, "mitaan ei saa muuttua, kun %s epaonnistuu" % nimi)

    def test_kanaria_myos_peruutukselle_ja_tilalle(self):
        self.l.ensimmainen()
        self.l.commit(2)
        self.assertOk(*self.l.julkaise())
        ennen = self.l.tilannekuva()
        lahde = JULKAISE.read_text()
        for nimi in ("tyokalut_isanta", "palvelut_vapaana", "tyokalut_kontti"):
            with self.subTest(nimi):
                kopio = self.l.t / ("rikki-p-%s.sh" % nimi)
                rikki, n = re.subn(r"^(%s\(\) \{[^\n]*\n)" % nimi, lambda m: m.group(1) + "  vika \"kanaria: tahallinen virhe (%s)\"\n" % nimi, lahde, count=1, flags=re.M)
                self.assertEqual(n, 1, "funktiota %s ei loytynyt" % nimi)
                kopio.write_text(rikki)
                rc, ulos = self.l.aja("--peruuta", skripti=kopio)
                self.assertEpa(rc, ulos, "kanaria")
                self.assertEqual(self.l.tilannekuva(), ennen)


class PeruutusTesti(Pohja):
    def kaksi_julkaisua(self):
        self.l.ensimmainen()
        self.koodi_v1 = puu(self.l.koodi)
        self.l.commit(2)
        time.sleep(1.1)            # prev-nimessa on sekuntitarkkuus
        self.assertOk(*self.l.julkaise())

    def test_peruutus_palauttaa_vain_koodin_ja_tila_pysyy_tiivisteella(self):
        self.kaksi_julkaisua()
        tila0, konf0 = puu(self.l.tila), puu(self.l.konfig)
        tila_tiedostot0 = {str(p.relative_to(self.l.tila)): p.read_bytes() for p in self.l.tila.rglob("*") if p.is_file()}
        self.assertEqual(self.l.versio_koodissa(), "2")
        rc, ulos = self.l.aja("--peruuta")
        self.assertOk(rc, ulos, "koodi palautettu")
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertEqual(puu(self.l.koodi), self.koodi_v1, "palautettu koodi ei ole bitti bitilta edellinen versio")
        self.assertEqual(puu(self.l.tila), tila0, "TILA MUUTTUI peruutuksessa")
        self.assertEqual(puu(self.l.konfig), konf0, "konfiguraatio muuttui peruutuksessa")
        self.assertEqual({str(p.relative_to(self.l.tila)): p.read_bytes() for p in self.l.tila.rglob("*") if p.is_file()}, tila_tiedostot0)
        self.assertIn("tila ja konfiguraatio ennallaan", ulos)
        self.assertFalse([r for r in self.l.pct_loki() if r.startswith("rollback")], "peruutus ei saa ajaa pct rollbackia")
        self.assertEqual(len([r for r in self.l.pct_loki() if r.startswith("snapshot")]), 2, "peruutus ei ota snapshotia (toimii, vaikka raja olisi taynna)")
        self.assertTrue(list(self.l.koodi.parent.glob(self.l.koodi.name + ".palautettu-*")))
        self.assertEqual((self.l.t / "sysd" / "app.timer.active").read_text().strip(), "active")

    def test_peruutus_toimii_vaikka_snapshot_raja_on_taynna(self):
        self.kaksi_julkaisua()
        (self.l.t / ("snap-%s" % self.l.ct)).write_text("".join("`-> vanha%d 2026-01-01 00:00:00 x\n" % i for i in range(25)))
        rc, ulos = self.l.aja("--peruuta")
        self.assertOk(rc, ulos)

    def test_peruutus_varoittaa_ajastinikkunasta_mutta_jatkaa(self):
        self.kaksi_julkaisua()
        (self.l.t / "sysd" / "app.timer.next").write_text("3\n")
        rc, ulos = self.l.aja("--peruuta")
        self.assertOk(rc, ulos)
        self.assertIn("VAROITUS", ulos)
        self.assertIn("ajastin laukeaa", ulos)
        self.assertEqual(self.l.versio_koodissa(), "1")

    def test_julkaisu_samassa_tilanteessa_keskeytyy_mutta_peruutus_ei(self):
        self.kaksi_julkaisua()
        (self.l.t / "sysd" / "app.timer.next").write_text("3\n")
        self.l.commit(3)
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "ajastin laukeaa")

    def test_peruutus_vaatii_ettei_palvelu_ole_kaynnissa(self):
        self.kaksi_julkaisua()
        (self.l.t / "sysd" / "app.service.active").write_text("active\n")
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--peruuta")
        self.assertEpa(rc, ulos, "on 'active'")
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_ei_edellista_versiota(self):
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--peruuta")
        self.assertEpa(rc, ulos, "edellista versiota")
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_prev_kansion_eheys_tarkistetaan(self):
        self.kaksi_julkaisua()
        prev = self.l.koodi.parent / self.l.previt()[-1]
        (prev / "run.py").write_text("VERSIO = 13  # peukaloitu\n")
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--peruuta")
        self.assertEpa(rc, ulos, "ei vastaa manifestiaan")
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_peruutus_tilan_tiiviste_pysyy_kun_tilaa_on_paljon(self):
        self.kaksi_julkaisua()
        for i in range(40):
            (self.l.tila / "alihak" / ("t%d.txt" % i)).write_text("sisalto %d\n" % i * 50)
        (self.l.tila / "iso.bin").write_bytes(os.urandom(2 * 1024 * 1024))
        tila0 = puu(self.l.tila)
        rc, ulos = self.l.aja("--peruuta")
        self.assertOk(rc, ulos)
        self.assertEqual(puu(self.l.tila), tila0)

    def test_peruutus_tehty_mutta_savukoe_kaatuu_kerrotaan_tarkasti(self):
        """Edellinen versio ei sisalla savukokeen moduulia: peruutus onnistuu, mutta tulos on EPAONNISTUI ja viesti kertoo, ettei mitaan kumottu."""
        (self.l.koodi / "config.py").unlink()
        self.l.ensimmainen()
        tila0, konf0 = puu(self.l.tila), puu(self.l.konfig)
        rc, ulos = self.l.aja("--peruuta")
        self.assertEpa(rc, ulos, "PERUUTUS ON TEHTY")
        self.assertIn("peruutus itse onnistui", ulos)
        self.assertNotIn("keskenerainen muutos kumottu", ulos)
        self.assertTrue((self.l.koodi / "VANHA.txt").exists(), "koodi pitaa jaada palautetuksi")
        self.assertEqual(puu(self.l.tila), tila0)
        self.assertEqual(puu(self.l.konfig), konf0)
        self.assertEqual((self.l.t / "sysd" / "app.timer.active").read_text().strip(), "active")

    def test_toinen_peruutus_palauttaa_uudelleen_edellisen(self):
        self.kaksi_julkaisua()
        time.sleep(1.1)
        self.assertOk(*self.l.aja("--peruuta"))
        self.assertEqual(self.l.versio_koodissa(), "1")


class VikatilanneTesti(Pohja):
    def valmis(self):
        self.l.ensimmainen()
        self.koodi_ennen = puu(self.l.koodi)
        self.tila_ennen, self.konf_ennen = puu(self.l.tila), puu(self.l.konfig)

    def assertPalautui(self):
        self.assertEqual(puu(self.l.koodi), self.koodi_ennen, "koodi ei palautunut")
        self.assertEqual(puu(self.l.tila), self.tila_ennen)
        self.assertEqual(puu(self.l.konfig), self.konf_ennen)
        self.assertEqual((self.l.t / "sysd" / "app.timer.active").read_text().strip(), "active", "ajastin ei palautunut")
        self.assertFalse((self.l.koodi.parent / (self.l.koodi.name + ".uusi")).exists(), ".uusi jai jaljelle")

    def test_testit_kaatuvat_koodia_ei_vaihdeta(self):
        self.valmis()
        self.l.commit(2, testit_ok=False)
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "testit eivat mene lapi")
        self.assertPalautui()
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertIn("keskenerainen muutos kumottu", ulos)
        self.assertEqual(len(self.l.previt()), 1)           # vain ensijulkaisun prev

    def test_savukoe_kaatuu_automaattinen_paluu(self):
        self.valmis()
        self.l.commit(2, savu_ok=False)
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "savukoe")
        self.assertPalautui()
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertTrue(list(self.l.koodi.parent.glob(self.l.koodi.name + ".viallinen-*")))
        self.assertIn("keskenerainen muutos kumottu", ulos)
        self.assertIn("PERUUTUS", ulos)

    def test_prev_nimi_tormaa_keskeytetaan_ennen_muutoksia(self):
        self.valmis()
        prev = self.l.previt()[0]
        prev_polku = self.l.koodi.parent / prev
        prev_ennen = puu(prev_polku)
        self.l.commit(2)
        p, c, s = self.l.paketoi()
        kirj0 = self.l.kirjoitukset()
        env = dict(self.l.env, JULKAISU_TS=prev.split(".prev-")[1])        # sama sekunti kuin ensijulkaisussa
        rc, ulos = self.l.aja(p, c, s, env=env)
        self.assertEpa(rc, ulos, "on jo olemassa")
        self.assertIn("Mitaan ei muutettu", ulos)
        self.assertPalautui()
        self.assertEqual(self.l.previt(), [prev])
        self.assertEqual(puu(prev_polku), prev_ennen, "vanha prev-kansio muuttui")
        self.assertFalse((prev_polku / self.l.koodi.name).exists(), "vanha koodi siirtyi prev-kansion sisaan")
        self.assertEqual(self.l.kirjoitukset(), kirj0)
        self.assertEqual(self.l.versio_koodissa(), "1")

    def test_palvelu_aktivoituu_jalkitarkistuksessa_kumoaa_muutoksen(self):
        self.valmis()
        aktivoi = "open(os.path.join(os.environ['SIM'], 'sysd', 'app.service.active'), 'w').write('active\\n')\n"
        self.l.commit(2, lisaa={"config.py": "import os\n" + aktivoi})
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "palvelu app.service on 'active'")
        self.assertIn("keskenerainen muutos kumottu", ulos)
        self.assertPalautui()
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertTrue(list(self.l.koodi.parent.glob(self.l.koodi.name + ".viallinen-*")), "koodia ei kumottu (.viallinen puuttuu)")

    def test_palvelu_aktivoituu_ennen_vaihtoa_poistaa_uusi_ja_jattaa_koodin(self):
        self.valmis()
        aktivoi = "open(os.path.join(os.environ['SIM'], 'sysd', 'app.service.active'), 'w').write('active\\n')"
        testi = ("import os\nimport unittest\nimport run\n\n\nclass T(unittest.TestCase):\n    def test_versio(self):\n"
                 "        " + aktivoi + "\n        self.assertEqual(run.VERSIO, 2)\n")
        self.l.commit(2, lisaa={"tests/test_app.py": testi})
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "palvelu app.service on 'active'")
        self.assertIn("keskenerainen muutos kumottu", ulos)
        self.assertPalautui()                                  # sis. .uusi poissa ja ajastin takaisin
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertEqual(len(self.l.previt()), 1)
        self.assertFalse(list(self.l.koodi.parent.glob(self.l.koodi.name + ".viallinen-*")), "vaihtoa ei pitanyt tehda")

    def test_tila_muuttuu_kesken_julkaisun_on_kova_virhe(self):
        self.valmis()
        self.l.commit(2)
        env = dict(self.l.env, APP_KIRJOITA=str(self.l.tila))
        p, c, s = self.l.paketoi()
        rc, ulos = self.l.aja(p, c, s, env=env)
        self.assertEpa(rc, ulos, "TILAKANSIO MUUTTUI")
        self.assertTrue((self.l.tila / "muuttui.txt").exists())

    def test_konfiguraatio_muuttuu_kesken_julkaisun_on_kova_virhe(self):
        self.valmis()
        self.l.commit(2)
        env = dict(self.l.env, APP_KIRJOITA=str(self.l.konfig))
        p, c, s = self.l.paketoi()
        rc, ulos = self.l.aja(p, c, s, env=env)
        self.assertEpa(rc, ulos, "KONFIGURAATIO MUUTTUI")

    def test_ct115_testit_ajetaan_testikontissa_eika_tuotantokontissa(self):
        self.l.ct = "118"
        self.l.kirjoita_konf(CT="118", TESTI_TAPA="ct115", TESTI_CT="115")
        self.l.ensimmainen()
        loki = self.l.pct_loki()
        testit = [r for r in loki if "unittest" in r]
        self.assertTrue(testit, "testeja ei ajettu lainkaan")
        self.assertTrue(all(r.startswith("exec 115 ") for r in testit), testit)
        self.assertFalse([r for r in loki if r.startswith("exec 118 ") and "unittest" in r])
        self.assertTrue(any(r.startswith("snapshot 118 ") for r in loki))
        self.assertTrue(any(r.startswith("push 115 ") for r in loki))        # testipaketti CT115:een

    def test_ct115_testit_kaatuvat_ei_vaihtoa(self):
        self.l.ct = "118"
        self.l.kirjoita_konf(CT="118", TESTI_TAPA="ct115", TESTI_CT="115")
        self.valmis()
        self.l.commit(2, testit_ok=False)
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "CT115:ssa")
        self.assertPalautui()

    def test_testi_tapa_ei(self):
        self.l.kirjoita_konf(TESTI_TAPA="ei")
        self.l.commit(2, testit_ok=False)
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos)
        self.assertFalse([r for r in self.l.pct_loki() if "unittest" in r])


class KayttajaTesti(Pohja):
    """TESTI_KAYTTAJA / SAVU_KAYTTAJA: testit ja savukoe ajetaan runuserilla; komennon alku ei saa kadota (shift)."""

    def runuser_loki(self):
        f = self.l.t / "runuser.log"
        return f.read_text().splitlines() if f.exists() else []

    def test_testit_ja_savukoe_ajetaan_kayttajana_komento_ehjana(self):
        self.l.kirjoita_konf(TESTI_KAYTTAJA="kauppa", SAVU_KAYTTAJA="kauppa")
        self.l.ensimmainen()
        self.l.commit(2)
        loki0 = len(self.runuser_loki())
        rc, ulos = self.l.julkaise()
        self.assertOk(rc, ulos)
        self.assertEqual(self.l.versio_koodissa(), "2")
        uusi = self.runuser_loki()[loki0:]
        self.assertIn("runuser kauppa python3 -B -m", uusi)                       # testit: python3 mukana
        self.assertIn("runuser kauppa python3 -B -c", uusi)                       # savukoe: python3 mukana
        self.assertIn("Ran 1 test", ulos)

    def test_peruutuksen_savukoe_ajetaan_kayttajana_komento_ehjana(self):
        self.l.kirjoita_konf(TESTI_KAYTTAJA="kauppa", SAVU_KAYTTAJA="kauppa")
        self.l.ensimmainen()
        self.l.commit(2)
        self.assertOk(*self.l.julkaise())
        loki0 = len(self.runuser_loki())
        rc, ulos = self.l.aja("--peruuta")
        self.assertOk(rc, ulos)
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertIn("runuser kauppa python3 -B -c", self.runuser_loki()[loki0:])

    def test_vaara_kayttajan_komento_kaataa_julkaisun_ja_kumoaa(self):
        self.l.kirjoita_konf(TESTI_KAYTTAJA="kauppa", SAVU_KAYTTAJA="kauppa")
        self.l.ensimmainen()
        self.l.commit(2, savu_ok=False)
        rc, ulos = self.l.julkaise()
        self.assertEpa(rc, ulos, "savukoe")
        self.assertEqual(self.l.versio_koodissa(), "1")


class YksikkoTesti(Pohja):
    def test_yksikot_asennetaan_kun_asetus_paalla_ja_daemon_reload(self):
        self.l.kirjoita_konf(YKSIKOT_ASENNUS="1", YKSIKKOKANSIO="systemd")
        self.l.commit(2, lisaa={"systemd/app.service": "[Service]\nExecStart=/bin/true\n", "systemd/app.timer": "[Timer]\nOnCalendar=daily\n"})
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos)
        self.assertEqual((self.l.yksikot / "app.service").read_text(), "[Service]\nExecStart=/bin/true\n")
        self.assertIn("systemctl daemon-reload", (self.l.t / "sysd.log").read_text())

    def test_yksikoita_ei_asenneta_oletuksena_vain_raportoidaan(self):
        self.l.kirjoita_konf(YKSIKKOKANSIO="systemd")
        self.l.commit(2, lisaa={"systemd/app.service": "[Service]\nExecStart=/bin/true\n"})
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos)
        self.assertFalse((self.l.yksikot / "app.service").exists())
        self.assertIn("asenna kasin", ulos)
        self.assertNotIn("daemon-reload", (self.l.t / "sysd.log").read_text())

    def test_peruutus_palauttaa_yksikot_koodin_mukana(self):
        self.l.kirjoita_konf(YKSIKOT_ASENNUS="1", YKSIKKOKANSIO="systemd")
        self.l.commit(2, lisaa={"systemd/app.service": "VERSIO 2\n"})
        self.assertOk(*self.l.julkaise(ensi=True))
        self.l.commit(3, lisaa={"systemd/app.service": "VERSIO 3\n"})
        self.assertOk(*self.l.julkaise())
        self.assertEqual((self.l.yksikot / "app.service").read_text(), "VERSIO 3\n")
        self.assertOk(*self.l.aja("--peruuta"))
        self.assertEqual((self.l.yksikot / "app.service").read_text(), "VERSIO 2\n")


class SkriptikopioTesti(Pohja):
    def test_skriptien_ero_raportoidaan_eika_paiviteta(self):
        hak = self.l.sim / "opt" / "skriptit"
        hak.mkdir()
        (hak / "a.sh").write_text("vanha\n")
        self.l.kirjoita_konf(SKRIPTIT_HAKEMISTO=str(hak), SKRIPTIT_PAKETISSA="skriptit")
        self.l.commit(2, lisaa={"skriptit/a.sh": "uusi\n", "skriptit/b.sh": "toinen\n"})
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos)
        self.assertIn("a.sh eroaa", ulos)
        self.assertIn("b.sh puuttuu", ulos)
        self.assertEqual((hak / "a.sh").read_text(), "vanha\n")
        self.assertFalse((hak / "b.sh").exists())

    def _skriptit(self):
        hak = self.l.sim / "opt" / "skriptit"
        hak.mkdir()
        (hak / "a.sh").write_text("vanha a\n")
        os.chmod(hak / "a.sh", 0o750)
        (hak / "c.sh").write_text("vanha c\n")
        (hak / "d.sh").write_text("sama\n")
        self.l.kirjoita_konf(SKRIPTIT_HAKEMISTO=str(hak), SKRIPTIT_PAKETISSA="skriptit")
        self.l.commit(2, lisaa={"skriptit/a.sh": "uusi a\n", "skriptit/b.sh": "uusi b\n", "skriptit/c.sh": "uusi c\n", "skriptit/d.sh": "sama\n"})
        return hak

    def test_paivita_skriptit_paivittaa_vain_olemassa_olevat_eroavat(self):
        hak = self._skriptit()
        rc, ulos = self.l.julkaise("--paivita-skriptit", ensi=True)
        self.assertOk(rc, ulos)
        self.assertEqual((hak / "a.sh").read_text(), "uusi a\n")
        self.assertEqual((hak / "c.sh").read_text(), "uusi c\n")
        self.assertEqual((hak / "a.sh").stat().st_mode & 0o777, 0o750)        # oikeudet vanhasta
        self.assertFalse((hak / "b.sh").exists())                              # uutta ei luoda
        self.assertIn("b.sh puuttuu", ulos)
        varmuus = sorted(p.name for p in hak.glob("*.ennen-*"))
        self.assertEqual(len(varmuus), 2, varmuus)                              # d.sh ei eronnut: ei varmuuskopiota
        self.assertEqual(next(hak.glob("a.sh.ennen-*")).read_text(), "vanha a\n")
        self.assertEqual(next(hak.glob("c.sh.ennen-*")).read_text(), "vanha c\n")
        self.assertEqual(list(hak.glob("*.julkaisu-uusi")), [])
        self.assertIn("Skripti takaisin", ulos)
        self.assertEqual(self.l.versio_koodissa(), "2")

    def test_paivita_skriptit_virhe_kumoaa_skriptit_ja_koodin(self):
        hak = self._skriptit()
        self.l.env["SIM_PUSH_VIKA"] = "c.sh.julkaisu-uusi"          # a.sh ehtii vaihtua, c.sh:n siirto epaonnistuu
        rc, ulos = self.l.julkaise("--paivita-skriptit", ensi=True)
        self.assertNotEqual(rc, 0, ulos)
        self.assertIn(EPA, ulos)
        self.assertEqual((hak / "a.sh").read_text(), "vanha a\n")
        self.assertEqual((hak / "c.sh").read_text(), "vanha c\n")
        self.assertEqual((hak / "a.sh").stat().st_mode & 0o777, 0o750)
        self.assertEqual(list(hak.glob("*.julkaisu-uusi")), [])
        self.assertEqual(self.l.versio_koodissa(), None)             # koodikin takaisin alkuperaiseen

    def test_paivita_skriptit_kuivana_ei_muuta_mitaan(self):
        hak = self._skriptit()
        rc, ulos = self.l.julkaise("--paivita-skriptit", "--kuiva", ensi=True)
        self.assertOk(rc, ulos)
        self.assertIn("paivitetaan lopuksi", ulos)
        self.assertEqual((hak / "a.sh").read_text(), "vanha a\n")
        self.assertEqual(list(hak.glob("*.ennen-*")), [])

    def test_ilman_lippua_skriptit_ennallaan_vaikka_ero(self):
        hak = self._skriptit()
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos)
        self.assertEqual((hak / "a.sh").read_text(), "vanha a\n")
        self.assertIn("--paivita-skriptit paivittaa", ulos)


class DelegoituAsentajaTesti(Pohja):
    """Vaihe 1 (CT119): kauppa-asenna/kauppa-palauta hoitavat vaihdon; julkaise.sh tekee esi- ja jalkitarkistukset, tiivisteet ja snapshotin."""

    def test_delegoitu_julkaisu_ja_peruutus(self):
        self.l.kirjoita_konf(ASENTAJA="kauppa-asenna")
        tila0, konf0 = puu(self.l.tila), puu(self.l.konfig)
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertOk(rc, ulos)
        self.assertEqual(self.l.versio_koodissa(), "1")
        self.assertEqual(puu(self.l.tila), tila0)
        self.assertEqual(puu(self.l.konfig), konf0)
        self.assertTrue(any(r.startswith("snapshot 119 ") for r in self.l.pct_loki()))
        self.assertEqual((self.l.t / "sysd" / "app.timer.active").read_text().strip(), "active")
        self.assertIn("kauppa-asenna", ulos)
        rc, ulos = self.l.aja("--peruuta")
        self.assertOk(rc, ulos)
        self.assertEqual(self.l.versio_koodissa(), None)         # alkuperainen koodi (VANHA.txt, ei VERSION-tiedostoa)
        self.assertTrue((self.l.koodi / "VANHA.txt").exists())
        self.assertEqual(puu(self.l.tila), tila0)
        self.assertTrue(any("kauppa-palauta" in r or "palautettu" in r for r in [str(p) for p in self.l.koodi.parent.iterdir()]))

    def test_delegoitu_asentaja_puuttuu(self):
        self.l.kirjoita_konf(ASENTAJA="kauppa-asenna")
        (self.l.bin / "kauppa-asenna").unlink()
        rc, ulos = self.l.julkaise(ensi=True)
        self.assertEpa(rc, ulos, "kauppa-asenna puuttuu")
        self.assertEqual(self.l.versio_koodissa(), None)
        self.assertEqual((self.l.t / "sysd" / "app.timer.active").read_text().strip(), "active")


class KatastrofiTesti(Pohja):
    def lisaa_snapshot(self, nimi="pre-x", aika="2020-01-01 00:00:00"):
        with open(self.l.t / ("snap-%s" % self.l.ct), "a") as f:
            f.write("`-> %s %s sim\n" % (nimi, aika))

    def kopio(self, ulos):
        m = re.search(r"kopioitu: (\S+) \(", ulos)
        self.assertIsNotNone(m, ulos)
        return Path(m.group(1))

    def test_tila_kopioidaan_ennen_rollbackia_ja_kerrotaan_mita_tuhoutuisi(self):
        self.lisaa_snapshot()
        ennen = (puu(self.l.koodi), puu(self.l.tila), puu(self.l.konfig))
        tila0 = puu(self.l.tila)
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-x")
        self.assertOk(rc, ulos, "tila kopioitu")
        self.assertEqual((puu(self.l.koodi), puu(self.l.tila), puu(self.l.konfig)), ennen, "katastrofi-tila ei saa muuttaa kontin tilaa")
        self.assertFalse([r for r in self.l.pct_loki() if r.startswith("rollback")], "pct rollbackia EI saa ajaa")
        self.assertIn("TUHOUTUISIVAT", ulos)
        self.assertIn("data.db", ulos)
        self.assertIn("pct rollback %s pre-x" % self.l.ct, ulos)
        self.assertIn("--katastrofi-palauta", ulos)
        kopio = self.kopio(ulos)
        self.assertTrue(str(kopio).startswith(str(self.l.jh / "katastrofi")))
        for nimi in ("tila.tgz", "tila.tgz.sha256", "tila-manifesti.txt", "tila.tiiviste", "raportti.txt", "tiedot.txt"):
            self.assertTrue((kopio / nimi).exists(), nimi)
        self.assertEqual(oct(kopio.stat().st_mode & 0o777), "0o700")
        self.assertRegex((kopio / "tila.tiiviste").read_text().strip(), r"^[0-9a-f]{64}$")
        # kopion sisalto = tilakansio
        purku = self.l.t / "purku"
        purku.mkdir()
        with tarfile.open(kopio / "tila.tgz") as tf:
            tf.extractall(purku)
        self.assertEqual(puu(purku / str(self.l.tila).lstrip("/")), tila0, "kopio ei vastaa tilakansiota")

    def test_konfiguraation_sisaltoa_ei_kopioida_eika_tulosteta(self):
        self.lisaa_snapshot()
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-x")
        self.assertOk(rc, ulos)
        self.assertIn("KONFIGURAATIO", ulos)
        self.assertIn("env", ulos)
        kopio = self.kopio(ulos)
        for p in kopio.rglob("*"):
            if p.is_file() and p.name != "tila.tgz":
                self.assertNotIn("EI-OIKEA-SALAINEN", p.read_text(errors="ignore"))
        self.assertNotIn("EI-OIKEA-SALAINEN", ulos)
        with tarfile.open(kopio / "tila.tgz") as tf:
            self.assertFalse([n for n in tf.getnames() if "etc/app" in n])

    def test_raportti_erottaa_snapshotin_jalkeen_muuttuneet(self):
        vanha = time.time() - 86400 * 3
        for p in self.l.tila.rglob("*"):
            if p.is_file():
                os.utime(p, (vanha, vanha))
        snap_aika = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 86400))
        self.lisaa_snapshot("pre-rajattu", snap_aika)
        (self.l.tila / "uusi-tiedosto.db").write_bytes(b"x" * 1000)
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-rajattu")
        self.assertOk(rc, ulos)
        self.assertIn("1 tiedostoa muuttunut tai syntynyt snapshotin jalkeen", ulos)
        self.assertIn("uusi-tiedosto.db", ulos)
        self.assertNotIn("alihak/loki.txt", ulos.split("TILA:", 1)[1].split("KONFIGURAATIO", 1)[0])

    def test_snapshot_pitaa_antaa_ja_sen_pitaa_olla_olemassa(self):
        self.lisaa_snapshot()
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--katastrofi-tila")
        self.assertEpa(rc, ulos, "--snapshot NIMI")
        self.assertIn("pre-x", ulos)                 # listaa snapshotit
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "ei-ole")
        self.assertEpa(rc, ulos, "ei ole kohteella")
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "../x")
        self.assertEpa(rc, ulos)
        self.assertEqual(self.l.tilannekuva(), ennen)
        self.assertFalse((self.l.jh / "katastrofi").exists())

    def test_palvelu_kaynnissa_varoitus_mutta_kopio_otetaan(self):
        self.lisaa_snapshot()
        (self.l.t / "sysd" / "app.service.active").write_text("active\n")
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-x")
        self.assertOk(rc, ulos)
        self.assertIn("VAROITUS: palvelut kaynnissa", ulos)
        self.assertIn("palvelut_kaynnissa=app", (self.kopio(ulos) / "tiedot.txt").read_text())

    def test_palautus_kopiosta_rollbackin_jalkeen(self):
        self.lisaa_snapshot()
        tila0 = puu(self.l.tila)
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-x")
        kopio = self.kopio(ulos)
        # "rollback" tuhoaa tilan: simuloidaan
        (self.l.tila / "data.db").write_bytes(b"vanha rollbackin jalkeinen")
        shutil.rmtree(self.l.tila / "alihak")
        (self.l.tila / "roska.txt").write_text("x")
        self.assertNotEqual(puu(self.l.tila), tila0)
        konf0, koodi0 = puu(self.l.konfig), puu(self.l.koodi)
        rc, ulos = self.l.aja("--katastrofi-palauta", kopio)
        self.assertOk(rc, ulos, "tila palautettu")
        self.assertEqual(puu(self.l.tila), tila0, "tila ei palautunut tiivisteen mukaiseksi")
        self.assertEqual((puu(self.l.konfig), puu(self.l.koodi)), (konf0, koodi0), "palautus ei saa koskea koodiin eika konfiguraatioon")
        sivussa = list(self.l.tila.parent.glob(self.l.tila.name + ".ennen-palautusta-*"))
        self.assertEqual(len(sivussa), 1)
        self.assertTrue((sivussa[0] / "roska.txt").exists())          # rollbackin jalkeinen tila sailytetty
        self.assertFalse([r for r in self.l.pct_loki() if r.startswith("rollback")])

    def test_palautus_hylkaa_vioittuneen_kopion_ennen_muutoksia(self):
        self.lisaa_snapshot()
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-x")
        kopio = self.kopio(ulos)
        with open(kopio / "tila.tgz", "ab") as f:
            f.write(b"vioittunut")
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--katastrofi-palauta", kopio)
        self.assertEpa(rc, ulos, "sha256 ei tasmaa")
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_palautus_hylkaa_kansion_joka_ei_ole_katastrofikansiossa(self):
        muu = self.l.t / "muu"
        muu.mkdir()
        ennen = self.l.tilannekuva()
        for kansio in (muu, "", "/etc"):
            rc, ulos = self.l.aja("--katastrofi-palauta", kansio) if kansio != "" else self.l.aja("--katastrofi-palauta")
            self.assertEpa(rc, ulos)
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_palautus_vaatii_ettei_palvelu_ole_kaynnissa(self):
        self.lisaa_snapshot()
        rc, ulos = self.l.aja("--katastrofi-tila", "--snapshot", "pre-x")
        kopio = self.kopio(ulos)
        (self.l.t / "sysd" / "app.service.active").write_text("active\n")
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--katastrofi-palauta", kopio)
        self.assertEpa(rc, ulos, "on 'active'")
        self.assertEqual(self.l.tilannekuva(), ennen)


class TilaJaItsetestiTesti(Pohja):
    def test_tila_vain_luku(self):
        self.l.ensimmainen()
        ennen = self.l.tilannekuva()
        rc, ulos = self.l.aja("--tila")
        self.assertOk(rc, ulos, "tila luettu")
        self.assertIn("koodin eheys: ok", ulos)
        self.assertIn("prev-versiot:", ulos)
        self.assertNotIn("EI-OIKEA-SALAINEN", ulos)
        self.assertEqual(self.l.tilannekuva(), ennen)

    def test_tila_havaitsee_poikkeaman(self):
        self.l.ensimmainen()
        (self.l.koodi / "run.py").write_text("VERSIO = 5\n")
        rc, ulos = self.l.aja("--tila")
        self.assertIn("POIKKEAA MANIFESTISTA", ulos)

    def test_ohje_ja_virheelliset_argumentit(self):
        r = subprocess.run(["bash", str(JULKAISE), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--katastrofi-tila", r.stdout)
        self.assertIn("--kuiva", r.stdout)
        r = subprocess.run(["bash", str(JULKAISE)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        rc, ulos = self.l.aja("--tuntematon-lippu")
        self.assertEqual(rc, 2)
        rc, ulos = self.l.aja("liikaa", "argumentteja")
        self.assertNotEqual(rc, 0)

    def test_skriptit_ovat_ascii_ja_lf(self):
        for p in (JULKAISE, PAKETOI, ASENNA, *(ISANTA / "julkaisu").glob("*.conf")):
            b = p.read_bytes()
            self.assertFalse([x for x in b if x > 127], "%s sisaltaa ei-ASCII-merkkeja" % p.name)
            self.assertNotIn(b"\r", b, "%s sisaltaa CR-merkkeja" % p.name)
            if p.suffix == ".sh":
                self.assertTrue(b.startswith(b"#!/usr/bin/env bash\n"))

    def test_epaonnistumisen_loppurivi_on_utf8_ja_alkaa_epa(self):
        rc, ulos = self.l.aja("--peruuta")
        self.assertEpa(rc, ulos)
        self.assertTrue(loppurivi(ulos).encode().startswith(b"EP\xc3\x84ONNISTUI: "))


if __name__ == "__main__":
    unittest.main()
