import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from claudejono import aamukooste, todennettavat

TZ = aamukooste.TZ
NYT = datetime(2026, 11, 8, 6, 30, tzinfo=TZ)   # kiintea kello: ei aikapommia

TAULU = """# Todennettavat

| Mekanismi | Luotu | Ensimm\u00e4inen ajo | Miten todennetaan | Tila |
|---|---|---|---|---|
| Aivon haastattelu send_later-ketjuna (kotimatka) | 5.11. | 5.11. klo 15.02 | Haastattelu alkaa | avoin |
| Kuukauden kunnossapitop\u00e4iv\u00e4 | 5.11. | la 7.11. klo 10.17 | Raportti on olemassa | avoin |
| Palautustesti CT113 (Gitea) | 5.11. | 15.11. klo 17.41 | Palautus onnistuu | avoin |
| Automerge v2 seitsem\u00e4ss\u00e4 repossa | 5.11. | kun Tony yhdist\u00e4\u00e4 PR:t | Ensimm\u00e4inen PR yhdistyy | odottaa yhdist\u00e4mist\u00e4 |
| Vanha palvelu | 1.11. | 2.11. | Toimii | todennettu |
| PR-avaaja (#192) | 4.11. | 4.11. | Avaa PR:n | EI TOIMI (5.11.) |
"""


class Todennettavat(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        (self.repo / "claude-jono").mkdir()

    def kirjoita(self, teksti=TAULU):
        (self.repo / "claude-jono" / "todennettavat.md").write_text(teksti, encoding="utf-8")

    def test_vain_eraantyneet_avoimet(self):
        self.kirjoita()
        nimet = [r["mekanismi"] for r in todennettavat.eraantyneet(self.repo, NYT)]
        self.assertEqual(nimet, ["Aivon haastattelu send_later-ketjuna (kotimatka)", "Kuukauden kunnossapitop\u00e4iv\u00e4"])

    def test_tuleva_ja_ajankohdaton_eivat_eraannu(self):
        self.kirjoita()
        nimet = " ".join(r["mekanismi"] for r in todennettavat.eraantyneet(self.repo, NYT))
        self.assertNotIn("Palautustesti", nimet)   # 15.11. vasta tulossa
        self.assertNotIn("Automerge", nimet)       # ei paivamaaraa
        self.assertNotIn("Vanha", nimet)           # todennettu
        self.assertNotIn("PR-avaaja", nimet)       # tila ei ole avoin

    def test_kellonaika_ratkaisee_samana_paivana(self):
        self.kirjoita()
        ennen = datetime(2026, 11, 7, 10, 16, tzinfo=TZ)
        jalkeen = datetime(2026, 11, 7, 10, 17, tzinfo=TZ)
        self.assertEqual(len(todennettavat.eraantyneet(self.repo, ennen)), 1)
        self.assertEqual(len(todennettavat.eraantyneet(self.repo, jalkeen)), 2)

    def test_kellonajaton_paiva_eraantyy_paivan_lopussa(self):
        self.kirjoita("| Mekanismi | L | A | M | Tila |\n|---|---|---|---|---|\n| X | 1.11. | 7.11. | m | avoin |\n")
        self.assertEqual(todennettavat.eraantyneet(self.repo, datetime(2026, 11, 7, 12, 0, tzinfo=TZ)), [])
        self.assertEqual(len(todennettavat.eraantyneet(self.repo, datetime(2026, 11, 8, 0, 5, tzinfo=TZ))), 1)

    def test_puuttuva_tiedosto_ja_roskaa(self):
        self.assertEqual(todennettavat.eraantyneet(self.repo, NYT), [])
        self.kirjoita("ei taulukkoa\n| yksi | kaksi |\n| X | 99.99. | 31.2. klo 99.99 | m | avoin |\n")
        self.assertEqual(todennettavat.eraantyneet(self.repo, NYT), [])

    def test_kysymysmuoto_ja_raja(self):
        self.kirjoita()
        k = todennettavat.kysymykset(todennettavat.eraantyneet(self.repo, NYT))
        self.assertIn("Toimiko Aivon haastattelu send_later-ketjuna?", k)   # sulkeet pois nimesta
        self.assertTrue(k.endswith("kyll\u00e4 / ei / en tied\u00e4"))
        self.assertIsNone(todennettavat.kysymykset([]))
        monta = [{"mekanismi": f"M{i}"} for i in range(5)]
        self.assertIn("(+2 muuta", todennettavat.kysymykset(monta))

    def test_aamukooste_nostaa_rivin_tarvitaan_osioon(self):
        self.kirjoita()
        t = aamukooste.keraa(self.repo, self.repo / "ei", NYT, lambda u: [] if "pulls" in u else {"check_runs": []}, lambda: [])
        rivit = aamukooste.rivit(t)
        self.assertEqual(len(rivit), 5)
        self.assertIn("Toimiko Kuukauden kunnossapitop\u00e4iv\u00e4?", rivit[1])
        self.assertNotIn("Palautustesti", rivit[1])

    def test_aamukoosteessa_ei_rivia_kun_ei_eraantyneita(self):
        t = aamukooste.keraa(self.repo, self.repo / "ei", NYT, lambda u: [] if "pulls" in u else {"check_runs": []}, lambda: [])
        self.assertNotIn("Toimiko", aamukooste.rivit(t)[1])


if __name__ == "__main__":
    unittest.main()
