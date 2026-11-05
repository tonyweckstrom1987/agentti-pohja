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


class ValmisRikkiTesti(unittest.TestCase):
    TULOS = "# Tulos: %s\n\n- Tila: VALMIS\n- Aika: 2026-11-04T10:00+02:00\n\nTehty.\n"
    RIKKI = "- Tarkastaja: opus\n- Toimii k\u00e4yt\u00f6ss\u00e4: ei\n- Todiste: journalctl: Traceback\n"

    def test_valmis_joka_todettiin_toimimattomaksi_lasketaan(self):
        nyt = datetime.date(2026, 11, 5)
        tulokset = [self.TULOS % "a.md", self.TULOS % "b.md"]
        rivit, t = vm.laske("", tulokset, nyt, {"a.md": self.RIKKI, "b.md": self.RIKKI.replace(": ei", ": kyll\u00e4")})
        self.assertEqual(t["valmis_rikki"], 1)
        self.assertEqual(sum(r["valmis_rikki"] for _, r in rivit), 1)
        self.assertEqual(sum(r["luokat"]["1"] for _, r in rivit), 1)

    def test_ilman_tarkastuksia_ei_muutosta(self):
        _, t = vm.laske("", [self.TULOS % "a.md"], datetime.date(2026, 11, 5))
        self.assertEqual((t["valmis_rikki"], t["ajot"]), (0, 0))

    def test_osiossa_sarake(self):
        rivit, t = vm.laske("", [self.TULOS % "a.md"], datetime.date(2026, 11, 5), {"a.md": self.RIKKI})
        self.assertIn("VALMIS, my\u00f6hemmin toimimaton", "\n".join(vm.muodosta_osio(rivit, t, datetime.date(2026, 11, 5))))


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


class AsciiTesti(unittest.TestCase):
    def test_koodi_on_ascii(self):
        for p in (Path(vm.__file__), Path(__file__)):
            p.read_text(encoding="ascii")


if __name__ == "__main__":
    unittest.main()
