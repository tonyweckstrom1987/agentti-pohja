import tempfile
import unittest
from pathlib import Path

from claudejono import aamukooste, vaiteportti


class Vaiteportti(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "aanikirjat" / "matka").mkdir(parents=True)

    def kirjoita(self, polku, teksti):
        (self.d / "aanikirjat" / polku).write_text(teksti, encoding="utf-8")

    def test_valmis_ilman_tarkastusta_hylataan(self):
        self.kirjoita("matka/M01-kokonaiskuva.md", "# M01\n")
        self.kirjoita("README.md", "| M01-kokonaiskuva | valmis |\n")
        self.assertEqual(vaiteportti.puutteet(self.d), ["M01-kokonaiskuva"])
        self.assertEqual(vaiteportti.main([str(self.d)]), 1)

    def test_tyhja_tarkastus_ei_kelpaa(self):
        self.kirjoita("matka/M01-a.md", "x")
        self.kirjoita("matka/M01-a.tarkastus.md", "  \n")
        self.kirjoita("README.md", "- M01-a: valmis\n")
        self.assertEqual(vaiteportti.puutteet(self.d), ["M01-a"])

    def test_valmis_tarkastuksella_menee_lapi(self):
        self.kirjoita("matka/M01-a.md", "x")
        self.kirjoita("matka/M01-a.tarkastus.md", "Tarkastaja: 12 v\u00e4itett\u00e4 tarkistettu, 0 virhett\u00e4.")
        self.kirjoita("README.md", "- M01-a: valmis\n")
        self.assertEqual(vaiteportti.puutteet(self.d), [])
        self.assertEqual(vaiteportti.main([str(self.d)]), 0)

    def test_ei_valmis_ja_tyon_alla_eivat_laukaise(self):
        self.kirjoita("matka/M02-b.md", "x")
        self.kirjoita("matka/M03-c.md", "x")
        self.kirjoita("README.md", "- M02-b: ei valmis, tarkastus kesken\n- M03-c: ty\u00f6n alla\n")
        self.assertEqual(vaiteportti.puutteet(self.d), [])

    def test_tunnus_riittaa_readme_riviksi(self):
        self.kirjoita("matka/M04-raportti-osa.md", "x")
        self.kirjoita("README.md", "| M04 | valmis |\n")
        self.assertEqual(vaiteportti.puutteet(self.d), ["M04-raportti-osa"])

    def test_puuttuva_hakemisto_ei_kaada(self):
        self.assertEqual(vaiteportti.puutteet(self.d / "ei-ole"), [])

    def test_aamukooste_nostaa_halytyksen(self):
        t = {"tulokset": [], "uudet_prt": [], "opit": [], "tonylle": [], "pr": None, "pr_virheita": [], "yojono": None,
             "yksikot": [], "vaiteportti": ["M01-a"]}
        self.assertIn("v\u00e4iteportti: M01-a", aamukooste.rivit(t)[5])


if __name__ == "__main__":
    unittest.main()
