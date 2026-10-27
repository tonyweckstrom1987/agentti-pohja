import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentti import peilaus


class PeilausTesti(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.perus, self.kohde = self.t / "perus", self.t / "kohde"
        (self.perus / "raportit").mkdir(parents=True)

    def raportti(self, nimi, teksti, ika_pv=0):
        p = self.perus / "raportit" / nimi
        p.write_text(teksti)
        t = time.time() - ika_pv * 86400
        os.utime(p, (t, t))

    def test_kopioi_siivottuna(self):
        self.raportti("a.md", "# A\navain ghp_" + "x" * 30 + " loppu\n")
        (self.perus / "tila.json").write_text(json.dumps({"tehtavat": {}}))
        n = peilaus.peilaa(self.perus, self.kohde)
        self.assertEqual(n, 2)
        teksti = (self.kohde / "raportit" / "a.md").read_text()
        self.assertIn("[poistettu]", teksti)
        self.assertNotIn("ghp_", teksti)
        self.assertTrue((self.kohde / "tila.json").exists())
        self.assertTrue((self.kohde / "paivitetty.txt").exists())

    def test_vanhat_ja_muut_tiedostot_pois(self):
        self.raportti("uusi.md", "u")
        self.raportti("vanha.md", "v", ika_pv=30)
        self.raportti("kuva.png", "x")
        peilaus.peilaa(self.perus, self.kohde, paivia=14)
        nimet = sorted(p.name for p in (self.kohde / "raportit").iterdir())
        self.assertEqual(nimet, ["uusi.md"])

    def test_kohde_vastaa_lahdetta_poistuneet_poistetaan(self):
        self.raportti("a.md", "a")
        peilaus.peilaa(self.perus, self.kohde)
        (self.perus / "raportit" / "a.md").unlink()
        peilaus.peilaa(self.perus, self.kohde)
        self.assertEqual(list((self.kohde / "raportit").iterdir()) if (self.kohde / "raportit").exists() else [], [])

    def test_puuttuva_lahde_ei_kaada(self):
        self.assertEqual(peilaus.peilaa(self.t / "ei-ole", self.kohde), 0)

    def test_suuri_tiedosto_katkaistaan(self):
        self.raportti("iso.md", "x" * (peilaus.MAX_KOKO + 5000))
        peilaus.peilaa(self.perus, self.kohde)
        self.assertLessEqual(len((self.kohde / "raportit" / "iso.md").read_text()), peilaus.MAX_KOKO + 2)

    def test_main_virheet(self):
        self.assertEqual(peilaus.main([]), 2)
        self.assertEqual(peilaus.main(["a", "b", "x"]), 2)


class PeilausTarkkaTesti(unittest.TestCase):
    NYT = 2_000_000_000.0

    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t, True)
        self.perus = self.t / "perus"
        (self.perus / "raportit").mkdir(parents=True)

    def raportti(self, nimi, ika_s, teksti="x"):
        p = self.perus / "raportit" / nimi
        p.write_text(teksti)
        os.utime(p, (self.NYT - ika_s, self.NYT - ika_s))

    def kopiot(self, kohde):
        d = kohde / "raportit"
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def test_paivaraja_on_tarkka(self):
        self.raportti("tasan.md", 14 * 86400)
        self.raportti("hiukan_vanha.md", 14 * 86400 + 3)
        self.raportti("tuore.json", 86400)
        kohde = self.t / "kohde"
        self.assertEqual(peilaus.peilaa(self.perus, kohde, 14, nyt=self.NYT), 2)
        self.assertEqual(self.kopiot(kohde), ["tasan.md", "tuore.json"])

    def test_sisakkainen_kohde_luodaan(self):
        self.raportti("a.md", 10)
        kohde = self.t / "x" / "y" / "kohde"
        self.assertEqual(peilaus.peilaa(self.perus, kohde, nyt=self.NYT), 1)
        self.assertTrue((kohde / "paivitetty.txt").exists())

    def test_kopiointi_luo_puuttuvat_hakemistot_ja_siivoaa(self):
        lahde = self.t / "l.md"
        lahde.write_text("avain ghp_" + "x" * 30)
        kohde = self.t / "a" / "b" / "c.md"
        peilaus._kopioi_siivottuna(lahde, kohde)
        self.assertEqual(kohde.read_text(), "avain [poistettu]\n")

    def test_tila_ja_jono_kopioidaan_ja_paivitetty_aika(self):
        (self.perus / "tila.json").write_text('{"a": 1}')
        (self.perus / "jono.json").write_text('{"b": 2}\n')
        kohde = self.t / "kohde"
        self.assertEqual(peilaus.peilaa(self.perus, kohde, nyt=self.NYT), 2)
        self.assertEqual((kohde / "jono.json").read_text(), '{"b": 2}\n')
        self.assertEqual((kohde / "paivitetty.txt").read_text(),
                         time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.NYT)) + "\n")

    def test_hakemisto_samalla_nimella_ei_kopioidu(self):
        (self.perus / "raportit" / "kansio.md").mkdir()
        self.assertEqual(peilaus.peilaa(self.perus, self.t / "kohde", nyt=self.NYT), 0)


class MainTarkkaTesti(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.t, True)
        self.perus, self.kohde = self.t / "perus", self.t / "kohde"
        (self.perus / "raportit").mkdir(parents=True)
        for nimi, ika in (("tuore.md", 1), ("vanha18.md", 18), ("vanha15.md", 15)):
            p = self.perus / "raportit" / nimi
            p.write_text("x")
            t = time.time() - ika * 86400
            os.utime(p, (t, t))

    def aja(self, argv):
        ulos, virhe = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(ulos), contextlib.redirect_stderr(virhe):
            rc = peilaus.main(argv)
        return rc, ulos.getvalue(), virhe.getvalue()

    def kopiot(self):
        return sorted(p.name for p in (self.kohde / "raportit").iterdir())

    def test_oletus_14_paivaa(self):
        rc, ulos, _ = self.aja([str(self.perus), str(self.kohde)])
        self.assertEqual((rc, ulos), (0, "peilattu 1 tiedostoa\n"))
        self.assertEqual(self.kopiot(), ["tuore.md"])

    def test_paivat_argumentista(self):
        rc, ulos, _ = self.aja([str(self.perus), str(self.kohde), "20"])
        self.assertEqual((rc, ulos), (0, "peilattu 3 tiedostoa\n"))
        self.assertEqual(self.kopiot(), ["tuore.md", "vanha15.md", "vanha18.md"])

    def test_argumenttimaarat_ja_virheellinen_paivia(self):
        for argv in ([], ["a"], ["a", "b", "1", "2"]):
            rc, ulos, virhe = self.aja(argv)
            self.assertEqual((rc, ulos), (2, ""), argv)
            self.assertIn("kaytto:", virhe)
        rc, ulos, virhe = self.aja([str(self.perus), str(self.kohde), "x"])
        self.assertEqual((rc, ulos), (2, ""))
        self.assertIn("kokonaisluku", virhe)
        self.assertFalse(self.kohde.exists())

    def test_ilman_argumenttia_luetaan_sys_argv(self):
        with mock.patch.object(sys, "argv", ["x", str(self.perus), str(self.kohde)]):
            rc, ulos, _ = self.aja(None)
        self.assertEqual((rc, ulos), (0, "peilattu 1 tiedostoa\n"))


if __name__ == "__main__":
    unittest.main()
