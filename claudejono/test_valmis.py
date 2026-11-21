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

    def test_malliperhe_normalisoi_nimimuodot(self):
        for nimi in ("opus", "Opus", "claude-opus-5-5", "anthropic/claude-opus-5-5", "opus-istunto-7", "`opus`"):
            self.assertEqual(valmis.malliperhe(nimi), "opus", nimi)
        for nimi in ("", None, "-", "5-5", "  "):
            self.assertEqual(valmis.malliperhe(nimi), "", nimi)

    def test_puuttuva_malli_on_epavarma(self):
        self.assertFalse(valmis.kelvollinen("# Tulos: a\n\n- Tila: VALMIS\n", OK))
        self.assertFalse(valmis.kelvollinen("# Tulos: a\n\n- Malli: -\n", OK))

    def test_eri_muotoinen_sama_malli_ei_ole_riippumaton(self):
        tulos = TULOS.replace("sonnet", "claude-opus-5-5")
        self.assertFalse(valmis.kelvollinen(tulos, OK))   # tarkastaja 'opus'
        self.assertTrue(valmis.kelvollinen(tulos, OK.replace("opus", "claude-sonnet-5-5")))

    def test_tulostiedostot_ohittaa_tarkastukset(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            for n in ("a.md", "a.tarkastus.md", "b.md", "c.txt"):
                (Path(d) / n).write_text("x", encoding="utf-8")
            self.assertEqual([p.name for p in valmis.tulostiedostot(d)], ["a.md", "b.md"])
        self.assertTrue(valmis.on_tarkastustiedosto("a.tarkastus.md"))
        self.assertFalse(valmis.on_tarkastustiedosto("a.md"))

    def test_polku(self):
        self.assertEqual(valmis.tarkastuspolku("/x", "2026-11-05-17.md").name, "2026-11-05-17.tarkastus.md")


if __name__ == "__main__":
    unittest.main()
