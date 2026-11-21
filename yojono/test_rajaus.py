"""Testit katselmoinnin rajaukselle (yojono/rajaus.py ja katselmoi.sh): kiintea git-repo tilapaishakemistossa, ei verkkoa, ei claudea."""
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from yojono import rajaus

NYT = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
HAKEMISTO = Path(__file__).resolve().parent


def git(kansio, *args, pvm=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    if pvm:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{pvm}T10:00:00+0000"
    return subprocess.run(["git", "-C", str(kansio), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


class Pohja(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name)
        self.repo = self.tmp / "r"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.perus = self.tmp / "perus"
        (self.perus / "raportit").mkdir(parents=True)

    def commit(self, pvm, **tiedostot):
        for nimi, sisalto in tiedostot.items():
            (self.repo / nimi.replace("__", "/")).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / nimi.replace("__", "/")).write_text(sisalto)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", f"c {pvm}", pvm=pvm)
        return git(self.repo, "rev-parse", "HEAD")

    def aja(self, raja=rajaus.OLETUS_RAJA):
        return rajaus.rajaa(self.repo, self.tmp / "ulos", self.perus, "r", raja, nyt=NYT)


class TestPohja(Pohja):
    def test_ilman_edellista_ajoa_oletus_on_7_paivaa(self):
        self.commit("2026-10-01", vanha="vanha\n")
        self.commit("2026-10-20", keski="keski\n")
        self.commit("2026-11-02", uusi="uusi\n")
        t = self.aja()
        self.assertEqual(t["muuttuneita"], 1)  # vain 2.11. commit; 29.10. rajan jalkeen ei ole committeja
        self.assertIn("uusi", (self.tmp / "ulos" / "muutokset.md").read_text())
        self.assertNotIn("keski", (self.tmp / "ulos" / "muutokset.md").read_text())
        self.assertIn("viimeiset 7 p\u00e4iv\u00e4\u00e4", (self.tmp / "ulos" / "otsikko.md").read_text())

    def test_ilman_edellista_ja_lyhyella_historialla_koko_historia_mukaan(self):
        self.commit("2026-11-03", a="a\n")
        t = self.aja()
        self.assertEqual(t["muuttuneita"], 1)
        self.assertIn("a (", (self.tmp / "ulos" / "muutokset.md").read_text())

    def test_tallennettu_commit_voittaa_raportin_paivan(self):
        vanha = self.commit("2026-10-10", a="1\n")
        self.commit("2026-10-15", b="2\n")
        self.commit("2026-11-03", c="3\n")
        (self.perus / "raportit" / "2026-11-01-r.md").write_text("x")
        (self.perus / "rajaus").mkdir()
        (self.perus / "rajaus" / "r.commit").write_text(vanha + "\n")
        t = self.aja()
        self.assertEqual(t["pohja"], vanha)
        self.assertEqual(t["muuttuneita"], 2)  # b ja c

    def test_ilman_commit_tietoa_kaytetaan_viimeisimman_raportin_paivaa(self):
        self.commit("2026-10-10", a="1\n")
        self.commit("2026-10-15", b="2\n")
        self.commit("2026-11-03", c="3\n")
        (self.perus / "raportit" / "2026-10-12-r.md").write_text("x")
        (self.perus / "raportit" / "2026-10-01-r.md").write_text("x")
        (self.perus / "raportit" / "2026-11-04-ideointi-r.md").write_text("x")  # toisen tyypin raportti ei kelpaa pohjaksi
        t = self.aja()
        self.assertEqual(t["muuttuneita"], 2)
        self.assertIn("2026-10-12", (self.tmp / "ulos" / "otsikko.md").read_text())

    def test_tuntematon_tallennettu_commit_ei_kaada_vaan_putoaa_paivamaaraan(self):
        self.commit("2026-11-03", c="3\n")
        (self.perus / "rajaus").mkdir()
        (self.perus / "rajaus" / "r.commit").write_text("0" * 40 + "\n")
        self.assertEqual(self.aja()["muuttuneita"], 1)

    def test_ei_muutoksia_kun_pohja_on_head(self):
        head = self.commit("2026-11-03", c="3\n")
        (self.perus / "rajaus").mkdir()
        (self.perus / "rajaus" / "r.commit").write_text(head + "\n")
        t = self.aja()
        self.assertEqual((t["muuttuneita"], t["diff_tavua"]), (0, 0))


class TestKoko(Pohja):
    def test_pieni_diff_menee_kokonaan(self):
        self.commit("2026-11-03", a="a\n", b="b\n")
        t = self.aja()
        self.assertEqual((t["mukana"], t["pois"]), (2, 0))
        self.assertNotIn("Katselmoimatta", (self.tmp / "ulos" / "otsikko.md").read_text())

    def test_liian_iso_diff_ottaa_suurimmat_rajan_sisalle_ja_kirjaa_pois_jaaneet(self):
        self.commit("2026-11-03", iso="x" * 400 + "\n", keski="y" * 100 + "\n", pieni="z\n", valtava__lukko="q" * 5000 + "\n")
        t = self.aja(raja=1100)
        diff = (self.tmp / "ulos" / "diff.patch").read_text()
        self.assertEqual(t["pois"], 1)  # vain valtava yksittainen ei mahdu
        self.assertLessEqual(t["mukana_tavua"], 1100)
        self.assertEqual(len(diff.encode()), t["mukana_tavua"])
        self.assertIn("xxxx", diff)
        self.assertNotIn("qqqq", diff)
        otsikko = (self.tmp / "ulos" / "otsikko.md").read_text()
        self.assertIn("Katselmoimatta j\u00e4i 1 tiedostoa: valtava/lukko", otsikko)
        self.assertIn("J\u00c4I POIS", (self.tmp / "ulos" / "muutokset.md").read_text())

    def test_otsikko_lyhentaa_pitkan_pois_jaaneiden_listan(self):
        tiedostot = {f"f{i:02d}": "x" * 300 + "\n" for i in range(15)}
        self.commit("2026-11-03", **tiedostot)
        t = self.aja(raja=1)
        self.assertEqual(t["pois"], 15)
        otsikko = (self.tmp / "ulos" / "otsikko.md").read_text()
        self.assertIn("ja 5 muuta", otsikko)
        self.assertLess(len(otsikko), 1500)

    def test_valitse_mukaan_ottaa_suurimman_ensin_ja_taytta_loput_pienilla(self):
        m = [("a", 80, b""), ("b", 50, b""), ("c", 20, b"")]
        mukana, pois, summa = rajaus.valitse_mukaan(m, 100)
        self.assertEqual([x[0] for x in mukana], ["a", "c"])  # a ensin; b (50) ei enaa mahdu, c mahtuu
        self.assertEqual([x[0] for x in pois], ["b"])
        self.assertEqual(summa, 100)

    def test_virheellinen_repo_hylataan(self):
        with self.assertRaises(ValueError):
            rajaus.rajaa(self.repo, self.tmp / "u", self.perus, "../x")


class TestKello(unittest.TestCase):
    def test_nyt_ymparistosta(self):
        vanha = os.environ.pop("KATSELMOI_NYT", None)
        try:
            self.assertIsNone(rajaus._nyt_ymparistosta())
            os.environ["KATSELMOI_NYT"] = "2026-11-05T12:00:00"
            self.assertEqual(rajaus._nyt_ymparistosta(), NYT)  # aikavyohykkeeton tulkitaan UTC:ksi
            os.environ["KATSELMOI_NYT"] = "2026-11-05T12:00:00+00:00"
            self.assertEqual(rajaus._nyt_ymparistosta(), NYT)
        finally:
            os.environ.pop("KATSELMOI_NYT", None)
            if vanha is not None:
                os.environ["KATSELMOI_NYT"] = vanha


class TestSkripti(Pohja):
    """katselmoi.sh kokonaan tekoclauden ja paikallisen 'GitHubin' kanssa."""

    def setUp(self):
        super().setUp()
        self.etuosa = self.tmp / "gh"
        self.etuosa.mkdir()
        self.claude = self.tmp / "claude"
        self.claude.write_text('#!/bin/sh\necho "$@" > "$KATSELMOI_PERUS/claude-argit"\n'
                               'echo \'{"result": "Ei loydoksia muutoksissa, kaikki kunnossa ja muutokset tarkistettu rajauksen mukaisesti ja huolellisesti tassa.", "total_cost_usd": 0.4}\'\n')
        self.claude.chmod(self.claude.stat().st_mode | stat.S_IXUSR)
        (self.tmp / "env").write_text("")

    def katselmoi(self, budjetti="2.50"):
        subprocess.run(["rm", "-rf", str(self.etuosa / "r")], check=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(self.repo), str(self.etuosa / "r")], check=True)
        env = dict(os.environ, KATSELMOI_PERUS=str(self.perus), KATSELMOI_ENV=str(self.tmp / "env"), KATSELMOI_CLAUDE=str(self.claude),
                   KATSELMOI_URL_ETUOSA=str(self.etuosa), KATSELMOI_PYTHON="python3", KATSELMOI_NYT=NYT.isoformat(), YOJONO_AJURI=str(HAKEMISTO.parent))
        return subprocess.run(["sh", str(HAKEMISTO / "katselmoi.sh"), "r", budjetti], capture_output=True, text=True, env=env, check=False)

    def raportti(self):
        return next((self.perus / "raportit").glob("*-r.md"))

    def test_ensimmainen_ajo_rajaa_ja_toinen_ajo_ilman_muutoksia_ohittaa_claude_n(self):
        self.commit("2026-11-03", a="a\n")
        r = self.katselmoi()
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        argit = (self.perus / "claude-argit").read_text()
        self.assertIn("--max-budget-usd 2.50", argit)  # budjetti valitetaan sellaisenaan
        self.assertIn("diff.patch", argit)
        self.assertTrue(self.raportti().read_text().startswith("> Rajaus: katselmoitu vain muutokset"))
        self.assertEqual((self.perus / "rajaus" / "r.commit").read_text().strip(), git(self.repo, "rev-parse", "HEAD"))
        (self.perus / "claude-argit").unlink()
        r = self.katselmoi()
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertFalse((self.perus / "claude-argit").exists())  # ei muutoksia: claudea ei kutsuttu
        self.assertIn("Ei muutoksia", self.raportti().read_text())
        self.assertGreaterEqual(self.raportti().stat().st_size, 200)  # ajurin tyhja-raportti-raja

    def test_uusi_commit_ajon_jalkeen_tulee_seuraavaan_diffiin(self):
        self.commit("2026-11-03", a="a\n")
        self.katselmoi()
        self.commit("2026-11-04", b="b\n")
        r = self.katselmoi()
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertIn("1/1 tiedostoa", self.raportti().read_text())


if __name__ == "__main__":
    unittest.main()
