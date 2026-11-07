import io
import json
import subprocess
import unittest
import urllib.error

from tools import kayttoonotto_ero as k

SHA = "a" * 40


class Vastaus(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def avaaja(data):
    def avaa(pyynto, timeout=None):
        avaa.url = pyynto.full_url
        return Vastaus(json.dumps(data).encode())
    return avaa


def git_ok(*a, **k_):
    return subprocess.CompletedProcess(a, 0, stdout=SHA + "\n", stderr="")


def aja(avaa, suorita=git_ok):
    ulos = io.StringIO()
    return k.main(["--hakemisto", "/x"], avaa=avaa, suorita=suorita, tuloste=ulos), ulos.getvalue()


class Testit(unittest.TestCase):
    def test_ajan_tasalla(self):
        koodi, ulos = aja(avaaja({"ahead_by": 0, "behind_by": 0, "commits": []}))
        self.assertEqual(koodi, 0)
        self.assertIn("ajan tasalla", ulos)

    def test_jaljessa_kertoo_maaran_ja_prt(self):
        data = {"ahead_by": 3, "behind_by": 0, "commits": [
            {"commit": {"message": "Tarkastaja: syy lokiin (#214)\n\nviittaa #1"}},
            {"commit": {"message": "Merge pull request #193 from x"}},
            {"commit": {"message": "ilman numeroa"}}]}
        avaa = avaaja(data)
        koodi, ulos = aja(avaa)
        self.assertEqual(koodi, 1)
        self.assertIn("3 committia", ulos)
        self.assertIn("#214, #193", ulos)
        self.assertNotIn("#1,", ulos)
        self.assertTrue(avaa.url.endswith("/agentti-pohja/compare/%s...main" % SHA))

    def test_omat_commitit_eivat_ole_ajan_tasalla(self):
        koodi, ulos = aja(avaaja({"ahead_by": 0, "behind_by": 2, "commits": []}))
        self.assertEqual(koodi, 1)
        self.assertIn("2 committia, joita GitHubissa ei ole", ulos)

    def test_tuntematon_commit_on_tila_ei_selvinnyt(self):
        def avaa(pyynto, timeout=None):
            if "/compare/" in pyynto.full_url:
                raise urllib.error.HTTPError(pyynto.full_url, 404, "x", None, None)
            return Vastaus(b"{}")  # repo nakyy
        koodi, ulos = aja(avaa)
        self.assertEqual(koodi, 2)
        self.assertIn("ei loydy GitHubista", ulos)
        self.assertNotIn("yksityinen", ulos)

    def test_404_kun_repo_ei_nay_ohjaa_reposta_ei_commitista(self):
        def avaa(pyynto, timeout=None):
            raise urllib.error.HTTPError(pyynto.full_url, 404, "x", None, None)
        koodi, ulos = aja(avaa)
        self.assertEqual(koodi, 2)
        self.assertIn("404 myos repolle", ulos)
        self.assertIn("yksityinen", ulos)
        self.assertIn("--repo", ulos)
        self.assertNotIn("omia committeja", ulos)
        self.assertNotIn("nakyvyytta", ulos)

    def test_404_kun_repon_tila_ei_selvia(self):
        def avaa(pyynto, timeout=None):
            if "/compare/" in pyynto.full_url:
                raise urllib.error.HTTPError(pyynto.full_url, 404, "x", None, None)
            raise urllib.error.URLError("x")
        koodi, ulos = aja(avaa)
        self.assertEqual(koodi, 2)
        self.assertIn("syy voi olla commit, yksityinen repo", ulos)

    def test_pr_numerot_vain_yhdistamisotsikoista(self):
        data = {"ahead_by": 4, "behind_by": 0, "commits": [
            {"commit": {"message": "Korjaa vika #7 ja #8"}},
            {"commit": {"message": "Merge pull request #193 from x/y\n\n#5"}},
            {"commit": {"message": "Otsikko (#246)"}},
            {"commit": {"message": "Viittaa (#9) keskella"}}]}
        koodi, ulos = aja(avaaja(data))
        self.assertIn("#193, #246", ulos)
        for ei in ("#7", "#8", "#5", "#9"):
            self.assertNotIn(ei, ulos)

    def test_verkkovirhe_ei_ole_ajan_tasalla(self):
        def avaa(pyynto, timeout=None):
            raise urllib.error.URLError("x")
        self.assertEqual(aja(avaa)[0], 2)

    def test_git_virhe(self):
        def huono(*a, **k_):
            return subprocess.CompletedProcess(a, 128, stdout="", stderr="dubious ownership")
        koodi, ulos = aja(avaaja({}), suorita=huono)
        self.assertEqual(koodi, 2)
        self.assertIn("safe.directory", ulos)


if __name__ == "__main__":
    unittest.main()
