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


if __name__ == "__main__":
    unittest.main()
