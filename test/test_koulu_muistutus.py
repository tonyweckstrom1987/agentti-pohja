import datetime
import os
import tempfile
import unittest
from unittest import mock
import contextlib
import io

from agentti import koulu_muistutus as km

D = datetime.date


class JasennaTesti(unittest.TestCase):
    def test_tyhja_tiedosto(self):
        self.assertEqual(km.jasenna(""), [])
        self.assertEqual(km.jasenna("# vain kommentti\n\n  \n"), [])
        self.assertEqual(km.muistutus([], D(2026, 11, 2)), "Koulu 02.11.2026\nEi muistutettavaa.")

    def test_virheellinen_rivi_antaa_selkean_virheen(self):
        for teksti, osa in [
            ("2026-11-02 | Mat | koe", "rivi 1: odotettiin"),
            ("# ok\n2026-13-45 | Mat | koe | tentti", "rivi 2: virheellinen paivamaara"),
            ("2026-11-02 | Mat | koe | joku", "tuntematon tyyppi"),
            ("2026-11-02 |  | koe | tentti", "eivat voi olla tyhjia"),
        ]:
            with self.assertRaises(km.KouluVirhe) as c:
                km.jasenna(teksti)
            self.assertIn(osa, str(c.exception))

    def test_paivarajat(self):
        t = km.jasenna("\n".join([
            "2026-11-01 | A | eilinen palautus | palautus",
            "2026-11-01 | B | eilinen tentti | tentti",
            "2026-11-02 | C | tanaan | kertaus",
            "2026-11-03 | D | huomenna | tentti",
            "2026-11-04 | E | alku | kertaus",
            "2026-11-09 | F | raja | kertaus",
            "2026-11-10 | G | liian myohaan | kertaus",
        ]))
        s = km.muistutus(t, D(2026, 11, 2))
        self.assertIn("Myohassa:\n- 01.11. A: eilinen palautus", s)
        self.assertNotIn("eilinen tentti", s)
        self.assertIn("Tanaan:\n- C: tanaan (kertaus)", s)
        self.assertIn("Huomenna:\n- D: huomenna (tentti)", s)
        self.assertIn("04.11. E", s)
        self.assertIn("09.11. F", s)
        self.assertNotIn("liian myohaan", s)

    def test_tehty_palautus_ei_ole_myohassa(self):
        t = km.jasenna("2026-10-01 | A | valmis | palautus | x")
        self.assertNotIn("Myohassa", km.muistutus(t, D(2026, 11, 2)))

    def test_kuukauden_ja_vuoden_vaihtuminen(self):
        t = km.jasenna("2026-11-01 | A | huomenna | tentti\n2026-11-07 | B | viikon paassa | kertaus")
        s = km.muistutus(t, D(2026, 10, 31))
        self.assertIn("Huomenna:\n- A: huomenna", s)
        self.assertIn("07.11. B", s)
        t = km.jasenna("2027-01-01 | A | uusi vuosi | tentti")
        self.assertIn("Huomenna:\n- A: uusi vuosi", km.muistutus(t, D(2026, 12, 31)))
        t = km.jasenna("2028-02-29 | A | karkaus | tentti")
        self.assertIn("Huomenna:", km.muistutus(t, D(2028, 2, 28)))


class MainTesti(unittest.TestCase):
    def ajo(self, sisalto, *args):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "koulu.md")
            with open(polku, "w", encoding="utf-8") as f:
                f.write(sisalto)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                koodi = km.main(["--tiedosto", polku, *args])
        return koodi, out.getvalue(), err.getvalue()

    def test_virheellinen_rivi_ei_kaada(self):
        koodi, out, err = self.ajo("rikki", "--pvm", "2026-11-02")
        self.assertEqual(koodi, 1)
        self.assertIn("rivi 1", err)
        self.assertEqual(out, "")

    def test_puuttuva_tiedosto_ja_huono_pvm(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km.main(["--tiedosto", "/ei/ole.md", "--pvm", "2026-11-02"]), 2)
            self.assertEqual(km.main(["--pvm", "huomenna"]), 2)

    def test_esimerkkitiedosto(self):
        esim = os.path.join(os.path.dirname(__file__), "..", "docs", "koulu.esimerkki.md")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(km.main(["--tiedosto", esim, "--pvm", "2026-11-02"]), 0)
        self.assertIn("Tanaan:", out.getvalue())
        self.assertIn("Viikkotehtava 5", out.getvalue())


if __name__ == "__main__":
    unittest.main()
