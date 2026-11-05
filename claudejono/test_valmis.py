import unittest

from claudejono import valmis

TULOS = "# Tulos: a.md\n\n- Tila: VALMIS\n- Aika: 2026-11-05T10:00+02:00\n- Malli: sonnet\n\nTehty.\n"
OK = "- Tarkastaja: opus\n- Toimii k\u00e4yt\u00f6ss\u00e4: kyll\u00e4\n- Todiste: https://example.invalid/log#L12\n"


class Valmis(unittest.TestCase):
    def test_kelvollinen(self):
        self.assertTrue(valmis.kelvollinen(TULOS, OK))
        self.assertEqual(valmis.tehokas_tila("VALMIS", TULOS, OK), "VALMIS")

    def test_puuttuva_tai_vajaa_merkinta_on_kesken(self):
        for teksti in (None, "", "- Tarkastaja: opus\n- Toimii k\u00e4yt\u00f6ss\u00e4: kyll\u00e4\n",
                       OK.replace("opus", "sonnet"),          # sama malli kuin tuloksessa
                       OK.replace("kyll\u00e4", "ei"),
                       OK.replace("https://example.invalid/log#L12", "-"),
                       "- Toimii k\u00e4yt\u00f6ss\u00e4: kyll\u00e4\n- Todiste: loki\n"):   # tarkastaja puuttuu
            self.assertEqual(valmis.tehokas_tila("VALMIS", TULOS, teksti), "KESKEN", teksti)

    def test_muut_tilat_ennallaan(self):
        for tila in ("KESKEN", "EPAONNISTUI", "HYLATTY"):
            self.assertEqual(valmis.tehokas_tila(tila, TULOS, None), tila)

    def test_toimimaton(self):
        self.assertTrue(valmis.toimimaton(OK.replace("kyll\u00e4", "ei")))
        self.assertFalse(valmis.toimimaton(OK))
        self.assertFalse(valmis.toimimaton("- Toimii k\u00e4yt\u00f6ss\u00e4: ei\n"))   # ilman todistetta ei lasketa

    def test_polku(self):
        self.assertEqual(valmis.tarkastuspolku("/x", "2026-11-05-17.md").name, "2026-11-05-17.tarkastus.md")


if __name__ == "__main__":
    unittest.main()
