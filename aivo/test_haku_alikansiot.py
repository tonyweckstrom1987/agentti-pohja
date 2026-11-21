"""Haun indeksin rakennus yksityisella alueella: ehdotukset/hylatyt/ ja ydinohjeet/.git eivat kaada rakennusta,
mutta haun kayttamien kansioiden (tiivistelmat, indeksi) linkit hylataan edelleen.
Ajo: python3 -m unittest aivo.test_haku_alikansiot"""
import os
import tempfile
import unittest
from pathlib import Path

from aivo import haku
from aivo.alue import Alue
from aivo.test_haku import tee_tiivistelma
from aivo.yksityinen_alue import ALIKANSIOT as YKSITYINEN_ALIKANSIOT


class HakuAlikansiotTesti(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.a = Alue("yksityinen", Path(self._tmp.name) / "yksityinen", YKSITYINEN_ALIKANSIOT)
        self.a.valmista()
        tee_tiivistelma(self.a, 1, "haluan lyhyet vastaukset", "Tony haluaa lyhyet vastaukset.")

    def hylatty(self):
        h = self.a.polku("ehdotukset") / "hylatyt"
        h.mkdir(mode=0o700)
        (h / "0a1b2c3d4e5f.json").write_text("{}", encoding="utf-8")

    def git(self):
        g = self.a.polku("ydinohjeet") / ".git"
        g.mkdir(mode=0o700)
        (g / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    def test_hylatyt_olemassa_rakennus_toimii(self):
        self.hylatty()
        self.assertEqual(haku.rakenna(self.a)[0], 1)

    def test_ydinohjeet_git_olemassa_rakennus_toimii(self):
        self.git()
        self.assertEqual(haku.rakenna(self.a)[0], 1)

    def test_molemmat_ja_haku_loytaa(self):
        self.hylatty()
        self.git()
        haku.rakenna(self.a)
        self.assertTrue(haku.hae(self.a, "lyhyet vastaukset"))

    def test_symlinkki_tiivistelmissa_hylataan_edelleen(self):
        os.symlink("/etc/hostname", self.a.polku("tiivistelmat") / "linkki.json")
        with self.assertRaises(haku.HakuVirhe):
            haku.rakenna(self.a)

    def test_symlinkki_indeksissa_hylataan_edelleen(self):
        os.symlink("/etc/hostname", self.a.polku("indeksi") / "linkki")
        with self.assertRaises(haku.HakuVirhe):
            haku.rakenna(self.a)


if __name__ == "__main__":
    unittest.main()
