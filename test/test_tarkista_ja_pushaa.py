import os
import subprocess
import tempfile
import unittest

SKRIPTI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "tarkista-ja-pushaa.sh")
GIT_YMP = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}

TESTI_OK = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n"
TESTI_KAATUU = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(False)\n"


def git(hak, *args):
    return subprocess.run(["git", "-C", hak, *args], capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_YMP})


class PushVahtiTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bare = os.path.join(self.tmp.name, "kaukoinen.git")
        self.repo = os.path.join(self.tmp.name, "repo")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.bare], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        git(self.repo, "remote", "add", "origin", self.bare)

    def commitoi(self, testi, nimi="test_x.py"):
        with open(os.path.join(self.repo, nimi), "w") as f:
            f.write(testi)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")

    def aja(self, *args):
        return subprocess.run(["bash", SKRIPTI, *args], cwd=self.repo, capture_output=True, text=True)

    def kaukana_on_main(self):
        return subprocess.run(["git", "-C", self.bare, "rev-parse", "--verify", "-q", "refs/heads/main"],
                              capture_output=True).returncode == 0

    def test_onnistuva_testi_pushaa(self):
        self.commitoi(TESTI_OK)
        r = self.aja("-q", "origin", "main")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TESTIT: OK 1", r.stdout)
        self.assertTrue(self.kaukana_on_main())

    def test_kaatuva_testi_estaa_pushin(self):
        self.commitoi(TESTI_KAATUU)
        r = self.aja("-q", "origin", "main")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "TESTIT: KAATUI")
        self.assertFalse(self.kaukana_on_main())

    def test_putki_ei_peita_virhetta(self):
        # Tarkistus kayttaa putkea (| tail), joka peittaisi paluuarvon ilman pipefailia.
        self.commitoi(TESTI_KAATUU)
        with open(os.path.join(self.repo, ".tarkistus"), "w") as f:
            f.write("# kommentti\npython3 -m unittest discover 2>&1 | tail -n 3\n")
        r = self.aja("-q", "origin", "main")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(self.kaukana_on_main())

    def test_kutsuja_putkittaa_skriptin_tulosteen(self):
        # Kutsuja ajaa "skripti | tail": putken paluuarvo on tailin, mutta pushi on jo estetty skriptin sisalla.
        self.commitoi(TESTI_KAATUU)
        r = subprocess.run("bash %s -q origin main 2>&1 | tail -n 2" % SKRIPTI, shell=True, cwd=self.repo,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)  # putki peitti paluuarvon, kuten virhelokissa
        self.assertIn("TESTIT: KAATUI", r.stdout)
        self.assertFalse(self.kaukana_on_main())

    def test_tarkistus_tiedoston_komento(self):
        self.commitoi(TESTI_KAATUU)
        with open(os.path.join(self.repo, ".tarkistus"), "w") as f:
            f.write("true\n")
        r = self.aja("-n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "TESTIT: OK ?")

    def test_ei_testikomentoa_ei_pushaa(self):
        self.commitoi("print(1)\n", nimi="muu.py")
        r = self.aja("-q", "origin", "main")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "TESTIT: KAATUI")
        self.assertFalse(self.kaukana_on_main())

    def test_kuiva_ajo_ei_pushaa(self):
        self.commitoi(TESTI_OK)
        r = self.aja("-n")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.kaukana_on_main())


if __name__ == "__main__":
    unittest.main()
