"""Ajon lopun yhteenvetorivi stderriin (systemd-journal): paluukoodi, vaihe ja virhekoodit nakyvat ilman lokin
lukuoikeutta. Rivi ei saa sisaltaa poikkeusviestia, mallin tekstia eika tunnuksia. Poistumiskoodit ennallaan.
Ajo: python3 -B -m unittest discover -s tests (hakemistosta paivakatsaus)."""
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ymparisto import TOKEN, Mock, Ymparisto, llm_vastaus  # noqa: E402
import run  # noqa: E402


def yhteenvedot(err):
    return [r for r in err.splitlines() if r.startswith("Ajo paattyi: ")]


class JournalYhteenvetoTesti(Ymparisto, unittest.TestCase):
    def test_onnistunut_lahetys_rivi_ilman_koodeja(self):
        self.normaali_data()
        self.assertEqual(self.ajo(laheta=True), 0)
        self.assertEqual(yhteenvedot(self.err), ["Ajo paattyi: paluu=0 vaihe=laheta virhekoodi=-"])

    def test_lahetetty_varaviesti_nayttaa_koodin_ja_paluun_1(self):
        # 2.11./3.11./5.11. tilanne: "Lahetys: lahetetty" ja exit 1; rivi kertoo syyn journalissa
        self.normaali_data()
        self.assertEqual(self.ajo(laheta=True, llm=Mock(llm_vastaus("ei json"))), 1)
        self.assertIn("Lahetys: lahetetty", self.err)
        self.assertEqual(yhteenvedot(self.err), ["Ajo paattyi: paluu=1 vaihe=laheta virhekoodi=llm_vastaus_ei_json"])
        self.assertEqual(self.lukot(), ["2026-10-14.sent"])                                  # viesti lahti, lukko syntyi

    def test_collector_virhe_nakyy(self):
        self.normaali_data()
        keraa = lambda *a: [{"nimi": "ideat", "tila": "ok", "kohdemaara": 2, "teksti": "<data>\nx\n</data>"},
                            {"nimi": "trendit", "tila": "virhe", "kohdemaara": 0, "teksti": ""}]
        self.assertEqual(self.ajo(laheta=True, keraa=keraa), 1)
        self.assertIn("virhekoodi=collector_virhe", yhteenvedot(self.err)[0])

    def test_poikkeuksesta_vain_tyypin_nimi(self):
        def keraa(*a):
            raise RuntimeError("SALAINEN-VIESTI " + TOKEN)
        self.assertEqual(self.ajo(laheta=True, keraa=keraa), 1)
        self.assertEqual(len(yhteenvedot(self.err)), 1)
        self.assertNotIn("SALAINEN", self.err)
        self.assertNotIn(TOKEN, self.err)

    def test_lahetysvirhe_nakyy(self):
        self.normaali_data()
        virhe = urllib.error.URLError("x")
        self.assertEqual(self.ajo(laheta=True, tg=Mock(virhe, virhe, virhe)), 1)
        self.assertIn("virhekoodi=laheta_virhe", yhteenvedot(self.err)[0])

    def test_jo_lahetetty_tanaan(self):
        self.normaali_data()
        self.ajo(laheta=True)
        self.assertEqual(self.ajo(laheta=True), 0)
        self.assertEqual(yhteenvedot(self.err), ["Ajo paattyi: paluu=0 vaihe=lukko virhekoodi=-"])

    def test_dry_run_rivi_stderriin_ei_stdoutiin(self):
        self.assertEqual(self.ajo(), 0)
        self.assertEqual(len(yhteenvedot(self.err)), 1)
        self.assertNotIn("Ajo paattyi", self.ulos)


class YhteenvetoYksikkoTesti(unittest.TestCase):
    def test_vapaa_teksti_korvataan(self):
        rivi = run._yhteenveto(1, "llm", ["llm_skeema", "Bearer abc def", "sk-1234567890abcdef", None, "x" * 61])
        self.assertEqual(rivi, "Ajo paattyi: paluu=1 vaihe=llm virhekoodi=llm_skeema,muu,muu,muu,muu")

    def test_kiinteat_koodit_sailyvat(self):
        koodit = ["llm_http_429", "odottamaton_KeyError", "lukko_kirjoitus_epaonnistui"]
        self.assertEqual(run._yhteenveto(3, "laheta", koodit),
                         "Ajo paattyi: paluu=3 vaihe=laheta virhekoodi=llm_http_429,odottamaton_KeyError,lukko_kirjoitus_epaonnistui")


if __name__ == "__main__":
    unittest.main()
