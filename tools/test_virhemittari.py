import datetime
import tempfile
import unittest
from pathlib import Path

from tools import virhemittari as vm

LOKI = """# Virheloki

- 2026-11-01: tekstitykset tulivat videoihin.
- 2026-11-02: Jokin tuntematon virhe ilman syyta.
- 2026-11-03: Claude lupasi ja lopetti; mitaan ei tapahtunut 70 minuuttiin.

## 4.11.2026: git stash kesken testien
- Mita: x
- Saanto: y

## 9.11.2026: tuleva viikko (2)
- Mita: vanha tapaus (juurisyy 1)
"""


def tulos(tila, aika, runko=""):
    return "# Tulos: x\n\n- Tila: %s\n- Aika: %sT10:00+02:00\n\n%s\n" % (tila, aika, runko)


class LokiTesti(unittest.TestCase):
    def test_lukee_rivit_ja_osiot(self):
        m = vm.lue_loki(LOKI)
        self.assertEqual([p.isoformat() for p, _ in m],
                         ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-09"])
        self.assertIn("Saanto: y", m[3][1])

    def test_luokittelematon_ei_arvaa(self):
        self.assertEqual(vm.luokittele_loki("- 2026-11-02: Jokin tuntematon virhe"), "?")

    def test_saantotaulu_ja_oma_tunniste(self):
        self.assertEqual(vm.luokittele_loki("x tekstitykset y"), "1")
        self.assertEqual(vm.luokittele_loki("tuntematon (juurisyy 3)"), "3")


class AjoTesti(unittest.TestCase):
    def test_kesken_on_aina_luokka_1(self):
        self.assertEqual(vm.luokittele_ajo("KESKEN", "raportti"), "1")
        self.assertEqual(vm.luokittele_ajo("KESKEN", "Todennettu k\u00e4yt\u00f6ss\u00e4: -"), "1")

    def test_epaonnistui_raja_on_4_muuten_luokittelematon(self):
        self.assertEqual(vm.luokittele_ajo("EPAONNISTUI", "aikakatkaisu"), "4")
        self.assertEqual(vm.luokittele_ajo("EPAONNISTUI", "outo"), "?")

    def test_valmis_ei_ole_virhe(self):
        self.assertIsNone(vm.luokittele_ajo("VALMIS", "x"))

    def test_tulos_ilman_aikaa_ohitetaan(self):
        self.assertIsNone(vm.lue_tulos("- Tila: KESKEN\n"))


class LaskeTesti(unittest.TestCase):
    def test_viikkotaulukko_ja_toisto(self):
        nyt = datetime.date(2026, 11, 5)
        tulokset = [tulos("KESKEN", "2026-10-30"), tulos("VALMIS", "2026-11-02"),
                    tulos("EPAONNISTUI", "2026-11-03", "aikakatkaisu")]
        rivit, t = vm.laske(LOKI, tulokset, nyt)
        d = dict(rivit)
        v44, v45 = d[(2026, 44)], d[(2026, 45)]
        self.assertEqual((v44["yht"], v44["loki"], v44["ajot"]), (2, 1, 1))
        self.assertEqual(v44["luokat"]["1"], 2)
        self.assertEqual(v45["luokat"], {"1": 0, "2": 0, "3": 2, "4": 1, "?": 1})
        self.assertEqual(sum(v45["luokat"].values()), v45["yht"])
        self.assertEqual(v44["toisto"], "ei")
        self.assertEqual(t["ajot"], 2)
        self.assertEqual(t["tulostiedostoa"], 3)

    def test_toistuminen_kaksi_ja_kolme_viikkoa(self):
        nyt = datetime.date(2026, 11, 20)
        loki = "- 2026-11-02: tekstitykset\n- 2026-11-09: tekstitykset\n- 2026-11-16: tekstitykset\n"
        rivit, _ = vm.laske(loki, [], nyt)
        self.assertEqual([r["toisto"][:5] for _, r in rivit], ["ei", "kyll\u00e4", "kolma"])

    def test_tyhjat_viikot_mukana(self):
        rivit, _ = vm.laske("- 2026-11-02: tekstitykset\n", [], datetime.date(2026, 11, 20))
        self.assertEqual([v[1] for v, _ in rivit], [45, 46, 47])


class OsioTesti(unittest.TestCase):
    def test_korvaa_vain_osion_rungon(self):
        taulu = "# T\n\n## Muu\na\n\n## Virhemittari (x)\nvanha\n| a |\n\n## Kirjaukset\nb\n"
        uusi = vm.korvaa_osio(taulu, ["UUSI"])
        self.assertEqual(uusi, "# T\n\n## Muu\na\n\n## Virhemittari (x)\n\nUUSI\n\n## Kirjaukset\nb\n")

    def test_puuttuva_osio_nostaa_virheen(self):
        with self.assertRaises(ValueError):
            vm.korvaa_osio("# T\n", ["x"])

    def test_osio_sisaltaa_todennettu_rivin_ja_kaksi_viikkoa(self):
        nyt = datetime.date(2026, 11, 5)
        rivit, t = vm.laske(LOKI, [], nyt)
        osio = "\n".join(vm.muodosta_osio(rivit, t, nyt))
        self.assertIn("Todennettu k\u00e4yt\u00f6ss\u00e4:", osio)
        self.assertIn("44 (", osio)
        self.assertIn("45 (", osio)

    def test_kirjoita_ja_ulos(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "tulokset").mkdir()
            (d / "virheloki.md").write_text(LOKI, encoding="utf-8")
            (d / "tulostaulu.md").write_text("# T\n\n## Virhemittari\nvanha\n", encoding="utf-8")
            self.assertEqual(vm.main(["--docs", tmp, "--nyt", "2026-11-05", "--kirjoita"]), 0)
            self.assertIn("| Viikko |", (d / "tulostaulu.md").read_text(encoding="utf-8"))
            self.assertEqual(vm.main(["--docs", str(d / "ei-ole")]), 1)


class KeskenSyyTesti(unittest.TestCase):
    def kesken(self, syy=None, aika="2026-11-03"):
        rivi = "- KESKEN-syy: %s\n" % syy if syy else ""
        return "# Tulos: x\n\n- Tila: KESKEN\n%s- Aika: %sT10:00+02:00\n\nraportti\n" % (rivi, aika)

    def test_virhesyyt_ja_vanha_tulos_ovat_luokka_1(self):
        for syy in ("rivi-puuttuu", "ei-todennettu", "outo-koodi"):
            self.assertEqual(vm.luokittele_ajo("KESKEN", self.kesken(syy)), "1", syy)
        self.assertEqual(vm.luokittele_ajo("KESKEN", self.kesken()), "1")   # vanha tulos ilman syykoodia

    def test_odottavat_eivat_ole_virheita(self):
        for syy in ("odottaa-yhdistamista", "odottaa-kayttoonottoa"):
            self.assertEqual(vm.luokittele_ajo("KESKEN", self.kesken(syy)), "O")

    def test_laske_erottaa_odottavat_omaan_sarakkeeseen(self):
        nyt = datetime.date(2026, 11, 5)
        tulokset = [self.kesken("rivi-puuttuu"), self.kesken("ei-todennettu"), self.kesken(),
                    self.kesken("odottaa-yhdistamista"), self.kesken("odottaa-kayttoonottoa")]
        rivit, t = vm.laske("", tulokset, nyt)
        v45 = dict(rivit)[(2026, 45)]
        self.assertEqual((v45["yht"], v45["ajot"], v45["odottaa"], v45["luokat"]["1"]), (3, 3, 2, 3))
        self.assertEqual(t["ajot"], 3)
        self.assertEqual(t["tulostiedostoa"], 5)
        osio = "\n".join(vm.muodosta_osio(rivit, t, nyt))
        self.assertIn("Odottaa (ei virhe)", osio)
        self.assertIn("| 45 (2.11.\u20138.11.), kesken | 3 | 0 | 3 | 0 | 2 | 3 |", osio)


class LokiOtsikkoTesti(unittest.TestCase):
    LOKI2 = (
        "# Virheloki\n\n"
        "## 5.\u20136.11.2026 \u2013 V\u00e4ite oli v\u00e4\u00e4r\u00e4\n- Mit\u00e4: x\n- Vakavuus: vakava\n\n"
        "## 5.11.2026 klo 14.03 \u2013 V\u00e4\u00e4r\u00e4 palvelu\n- Mit\u00e4: y\n\n"
        "## 6.11.2026 \u2013 Tiivistevertailu\n- Mit\u00e4: z\n- Vakavuus: tavallinen\n"
    )

    def test_valin_otsikko_luetaan(self):
        m = vm.lue_loki(self.LOKI2)
        self.assertEqual([p.isoformat() for p, _ in m], ["2026-11-05", "2026-11-05", "2026-11-06"])
        self.assertEqual(vm.osio_paiva("30.10.\u20132.11.2026 \u2013 x").isoformat(), "2026-10-30")
        self.assertEqual(vm.osio_paiva("5.-6.11.2026: x").isoformat(), "2026-11-05")
        self.assertIsNone(vm.osio_paiva("31.2.2026: mahdoton"))

    def test_vakavuus_lasketaan_vain_kirjatusta_rivista(self):
        rivit, _ = vm.laske(self.LOKI2, [], datetime.date(2026, 11, 6))
        v45 = dict(rivit)[(2026, 45)]
        self.assertEqual((v45["loki"], v45["vakavia"]), (3, 1))
        osio = "\n".join(vm.muodosta_osio(rivit, {"loki": 3, "ajot": 0, "tulostiedostoa": 0}, datetime.date(2026, 11, 6)))
        self.assertIn("Vakavia (lokissa)", osio)


class AsciiTesti(unittest.TestCase):
    def test_koodi_on_ascii(self):
        for p in (Path(vm.__file__), Path(__file__)):
            p.read_text(encoding="ascii")


if __name__ == "__main__":
    unittest.main()
