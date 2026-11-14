"""ideoi.sh: agenttifirma luetaan paikallisesta dokumenttikopiosta (ei kloonia); muut repot kloonataan. Ei verkkoa, ei oikeaa claudea."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKRIPTI = Path(__file__).resolve().parent / "ideoi.sh"
OLETUS_DOCS = "/var/lib/claude-jono/docs"

CLAUDE_STUB = """#!/bin/sh
pwd > "$STUB_LOKI/claude-cwd"
printf '{"result": "# Ideat\\\\n1. koe", "total_cost_usd": 0.05}'
"""
GIT_STUB = """#!/bin/sh
echo "$@" >> "$STUB_LOKI/git-kutsut"
exit 1
"""


def suoritettava(polku, teksti):
    polku.write_text(teksti)
    polku.chmod(polku.stat().st_mode | stat.S_IXUSR)


class IdeoiTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.perus, self.docs, self.loki, bin_ = t / "perus", t / "docs", t / "loki", t / "bin"
        for p in (self.perus, self.docs, self.loki, bin_):
            p.mkdir()
        (self.docs / "00-hub.md").write_text("# hub\n")
        suoritettava(t / "claude", CLAUDE_STUB)
        suoritettava(bin_ / "git", GIT_STUB)
        (t / "env").write_text("")
        self.t, self.bin = t, bin_

    def aja(self, repo, **extra):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", STUB_LOKI=str(self.loki), YOJONO_PERUS=str(self.perus),
                   YOJONO_CLAUDE=str(self.t / "claude"), YOJONO_PYTHON=sys.executable, YOJONO_ENV=str(self.t / "env"),
                   YOJONO_DOCS=str(self.docs))
        env.update(extra)
        return subprocess.run(["sh", str(SKRIPTI), repo, "0.10"], capture_output=True, text=True, env=env, timeout=60)

    def git_kutsut(self):
        p = self.loki / "git-kutsut"
        return p.read_text() if p.exists() else ""

    def test_agenttifirma_luetaan_dokumenttikopiosta_ilman_kloonia(self):
        r = self.aja("agenttifirma")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.git_kutsut(), "")
        self.assertEqual(Path((self.loki / "claude-cwd").read_text().strip()).resolve(), self.docs.resolve())
        raportit = list((self.perus / "raportit").glob("*-ideointi-agenttifirma.md"))
        self.assertEqual(len(raportit), 1)
        self.assertIn("Ideat", raportit[0].read_text())
        self.assertEqual(json.loads(raportit[0].with_suffix(".json").read_text())["total_cost_usd"], 0.05)

    def test_dokumenttihakemisto_puuttuu(self):
        r = self.aja("agenttifirma", YOJONO_DOCS=str(self.t / "ei-ole"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("dokumenttihakemistoa ei ole", r.stderr)
        self.assertFalse((self.loki / "claude-cwd").exists())

    def test_yksityinen_talous_estaa_ajon(self):
        (self.docs / "yksityinen-talous").mkdir()
        r = self.aja("agenttifirma")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.loki / "claude-cwd").exists())

    def test_muu_repo_kloonataan_edelleen(self):
        r = self.aja("agentti-pohja")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("clone --depth 1 https://github.com/tonyweckstrom1987/agentti-pohja", self.git_kutsut())
        self.assertFalse((self.loki / "claude-cwd").exists())

    def test_oletuspolku_on_claude_jonon_docs_kopio(self):
        # Oletus kiinteana: ei vanhaa polkua, ja YOJONO_DOCS ohittaa sen (testit ylla).
        teksti = SKRIPTI.read_text()
        self.assertIn('DOCS="${YOJONO_DOCS:-/var/lib/claude-jono/docs}"', teksti)
        self.assertNotIn("/opt/agenttifirma-docs", teksti)

    @unittest.skipIf(os.path.isdir(OLETUS_DOCS), "isannalla oletuspolku on olemassa: ei ajeta oikeaa hakemistoa")
    def test_oletuspolku_kaytossa_ilman_ohitusta(self):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", STUB_LOKI=str(self.loki), YOJONO_PERUS=str(self.perus),
                   YOJONO_CLAUDE=str(self.t / "claude"), YOJONO_PYTHON=sys.executable, YOJONO_ENV=str(self.t / "env"))
        env.pop("YOJONO_DOCS", None)
        r = subprocess.run(["sh", str(SKRIPTI), "agenttifirma", "0.10"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("dokumenttihakemistoa ei ole: " + OLETUS_DOCS, r.stderr)
        self.assertFalse((self.loki / "claude-cwd").exists())

    def test_dokumenttikopiota_ei_muuteta(self):
        self.aja("agenttifirma")
        self.assertEqual(sorted(p.name for p in self.docs.iterdir()), ["00-hub.md"])


if __name__ == "__main__":
    unittest.main()
