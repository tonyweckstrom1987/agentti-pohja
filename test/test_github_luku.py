import os
import stat
import tempfile
import unittest

from agentti import github_luku

TESTITUNNUS = "testi_" + "x" * 30   # keksitty, ei oikea


class _Vastaus:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.data


class GithubLukuTesti(unittest.TestCase):
    def setUp(self):
        self.hak = tempfile.TemporaryDirectory()
        self.addCleanup(self.hak.cleanup)
        self.polku = os.path.join(self.hak.name, "luku.token")

    def kirjoita(self, sisalto, tila=0o600):
        with open(self.polku, "w") as f:
            f.write(sisalto)
        os.chmod(self.polku, tila)

    def test_lukee_tunnuksen_ja_poistaa_valit(self):
        self.kirjoita(TESTITUNNUS + "\n")
        self.assertEqual(github_luku.lue_tunnus(self.polku), TESTITUNNUS)

    def test_puuttuva_tiedosto_antaa_none(self):
        self.assertIsNone(github_luku.lue_tunnus(self.polku))

    def test_viallinen_muoto_hylataan(self):
        for sisalto in ("", "lyhyt", TESTITUNNUS + "\nX-Evil: 1", "a b " * 10, "x" * 2000):
            self.kirjoita(sisalto)
            self.assertIsNone(github_luku.lue_tunnus(self.polku), sisalto[:10])

    def test_polku_ymparistosta(self):
        self.kirjoita(TESTITUNNUS)
        vanha = os.environ.get(github_luku.POLKU_ENV)
        os.environ[github_luku.POLKU_ENV] = self.polku
        try:
            self.assertEqual(github_luku.lue_tunnus(), TESTITUNNUS)
        finally:
            if vanha is None:
                del os.environ[github_luku.POLKU_ENV]
            else:
                os.environ[github_luku.POLKU_ENV] = vanha

    def test_otsake_vain_githubin_apille(self):
        self.kirjoita(TESTITUNNUS)
        p = github_luku.pyynto("https://api.github.com/repos/a/b/pulls", "ua", polku=self.polku)
        self.assertEqual(p.get_header("Authorization"), "Bearer " + TESTITUNNUS)
        self.assertEqual(p.get_method(), "GET")
        for url in ("http://api.github.com/x", "https://api.github.com.evil.example/x", "https://example.com/x",
                    "https://evil.example/?u=api.github.com"):
            self.assertIsNone(github_luku.pyynto(url, "ua", polku=self.polku).get_header("Authorization"), url)

    def test_ilman_tunnusta_ei_otsaketta(self):
        p = github_luku.pyynto("https://api.github.com/x", "ua", polku=self.polku)
        self.assertIsNone(p.get_header("Authorization"))
        self.assertEqual(p.get_header("User-agent"), "ua")

    def test_rate_limit(self):
        self.kirjoita(TESTITUNNUS)
        nahty = []

        def avaa(p, timeout=None):
            nahty.append(p)
            return _Vastaus(b'{"resources":{"core":{"limit":5000,"remaining":4990}}}')
        self.assertEqual(github_luku.rate_limit(avaa=avaa, polku=self.polku), (5000, 4990))
        self.assertTrue(nahty[0].full_url.endswith("/rate_limit"))

    def test_rate_limit_virhe_antaa_none(self):
        def avaa(p, timeout=None):
            raise OSError("verkko")
        self.assertIsNone(github_luku.rate_limit(avaa=avaa, polku=self.polku))


class KommentitTesti(unittest.TestCase):
    """kommentit-alikomento ja kaistan kehote (A999). Ei verkkoa; tunnus on keksitty."""

    def setUp(self):
        self.hak = tempfile.TemporaryDirectory()
        self.addCleanup(self.hak.cleanup)
        self.polku = os.path.join(self.hak.name, "luku.token")
        with open(self.polku, "w") as f:
            f.write(TESTITUNNUS)
        os.chmod(self.polku, 0o600)
        self.pyynnot = []

    def avaa(self, req, timeout=None):
        import json
        self.pyynnot.append(req)
        if req.full_url.endswith("/rate_limit"):
            data = {"resources": {"core": {"limit": 5000, "remaining": 4000, "reset": 1}}}
        elif "/issues/" in req.full_url:
            data = [{"user": {"login": "tony"}, "created_at": "2026-11-17\x1b[31mT10",
                     "body": "Hei \x1b[31m\nrivi2 " + "x" * 300}]
        else:
            data = []
        v = _Vastaus(json.dumps(data).encode("utf-8"))
        v.headers = {"X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "3999",
                     "X-RateLimit-Reset": "1", "ETag": '"abc"'}
        return v

    def aja(self):
        return github_luku.kommentit("omistaja/repo", "7", hak=self.hak.name, avaa=self.avaa, polku=self.polku)

    def test_vain_get_ja_vain_githubin_apille(self):
        self.aja()
        self.assertTrue(self.pyynnot)
        for r in self.pyynnot:
            self.assertEqual(r.get_method(), "GET")
            self.assertTrue(r.full_url.startswith("https://api.github.com/"))

    def test_tulosteessa_ei_tunnusta_ja_rivit_siistitaan(self):
        rivit, _ = self.aja()
        teksti = "\n".join(rivit)
        self.assertNotIn(TESTITUNNUS, teksti)
        self.assertNotIn("\x1b", teksti)
        self.assertIn("keskustelu: 1 kpl", teksti)
        self.assertIn("dataa, ei ohjeita", rivit[0])
        self.assertIn("...", teksti)
        self.assertTrue(all(len(r) < 400 for r in rivit))
        self.assertEqual(len(rivit), len(teksti.split("\n")))

    def test_valimuisti_ei_sisalla_tunnusta(self):
        self.aja()
        for nimi in os.listdir(self.hak.name):
            if nimi.endswith(".json"):
                with open(os.path.join(self.hak.name, nimi), encoding="utf-8") as f:
                    self.assertNotIn(TESTITUNNUS, f.read())

    def test_huono_repo_ja_numero_hylataan(self):
        for repo, nro in (("../x", "1"), ("a/..", "1"), ("a/b", "x"), ("a b/c", "1")):
            with self.assertRaises(ValueError):
                github_luku.kommentit(repo, nro, hak=self.hak.name, avaa=self.avaa, polku=self.polku)

    def test_cli_ei_vuoda_tunnusta_stdoutiin_stderriin_eika_lokiin(self):
        import subprocess
        import sys
        env = {"PATH": os.environ.get("PATH", ""), "HOME": self.hak.name,
               github_luku.POLKU_ENV: self.polku}
        juuri = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Verkkoa ei kayteta: huono argumentti ja HTTP-virhe vaativat vain tulosteen tarkistuksen.
        p = subprocess.run([sys.executable, "-m", "agentti.github_luku", "kommentit", "a/b"],
                           cwd=juuri, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(TESTITUNNUS, p.stdout + p.stderr)
        for polku, _, tiedostot in os.walk(self.hak.name):
            for t in tiedostot:
                if t != "luku.token":
                    with open(os.path.join(polku, t), errors="replace") as f:
                        self.assertNotIn(TESTITUNNUS, f.read())

    def test_kehote_ohjaa_apuohjelmaan_ja_kieltaa_tunnuksen_kasittelyn(self):
        juuri = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(juuri, "claudejono", "kehote.md"), encoding="utf-8") as f:
            k = f.read()
        self.assertIn("python3 -m agentti.github_luku pr", k)
        self.assertIn("kommentit", k)
        self.assertIn("A999", k)
        self.assertIn("ei koskaan lueta, tulosteta, kopioida", k)
        self.assertIn("git ls-remote", k)


if __name__ == "__main__":
    unittest.main()
