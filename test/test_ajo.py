import os
import tempfile
import unittest

from agentti import ajo, rajat
from agentti.agentit import esimerkki
from test.apu import EI_TIETOA_JSON, ENV, OK_JSON, kello, llm_vastaus


class AjoTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tila = os.path.join(self.tmp.name, "tila")
        self.llm_kutsut = []
        self.tg = []
        self.sydan = []

    def hae_llm(self, vastaus):
        def _h(url, avain, payload):
            self.llm_kutsut.append(payload)
            if isinstance(vastaus, Exception):
                raise vastaus
            return vastaus
        return _h

    def aja(self, vastaus=None, tg_ok=True, env=ENV, **kw):
        return ajo.suorita(
            esimerkki, env, self.tila, kello=kello(),
            hae_llm=self.hae_llm(vastaus if vastaus is not None else llm_vastaus(OK_JSON)),
            hae_tg=lambda t, c, s: self.tg.append(s) or tg_ok,
            hae_sydan=lambda u: self.sydan.append(u) or True, **kw)

    def test_kuiva_ajo_ei_kayta_verkkoa(self):
        t = self.aja(llm=False, laheta=False)
        self.assertEqual((t["koodi"], t["virhe"], t["lahetys"]), (0, None, None))
        self.assertIn("kuiva-ajo", t["teksti"])
        self.assertEqual((self.llm_kutsut, self.tg), ([], []))

    def test_llm_ok_ja_lahetys(self):
        t = self.aja(llm=True, laheta=True)
        self.assertEqual((t["koodi"], t["lahetys"]), (0, "lahetetty"))
        self.assertIn("Kaikki hyvin.", t["teksti"])
        self.assertEqual(len(self.tg), 1)

    def test_ei_tietoa_on_normaali_lopputulos(self):
        t = self.aja(llm_vastaus(EI_TIETOA_JSON), llm=True, laheta=True)
        self.assertEqual(t["koodi"], 0)
        self.assertIsNone(t["virhe"])
        self.assertIn("en tieda", t["teksti"])
        self.assertIn("Data ei riita.", t["teksti"])

    def test_lahetys_vain_kerran_paivassa(self):
        self.aja(llm=True, laheta=True)
        t = self.aja(llm=True, laheta=True)
        self.assertEqual(t["lahetys"], "jo_lahetetty")
        self.assertEqual(len(self.tg), 1)

    def test_epaonnistunut_lahetys_ei_kuluta_paivan_lahetysta(self):
        t = self.aja(llm=True, laheta=True, tg_ok=False)
        self.assertEqual((t["koodi"], t["lahetys"]), (1, "epaonnistui"))
        t = self.aja(llm=True, laheta=True, tg_ok=True)
        self.assertEqual((t["koodi"], t["lahetys"]), (0, "lahetetty"))

    def test_llm_virhe_lahettaa_varaviestin_ja_palauttaa_1(self):
        t = self.aja(llm_vastaus("ei jsonia"), llm=True, laheta=True)
        self.assertEqual((t["koodi"], t["virhe"]), (1, "llm_vastaus_ei_json"))
        self.assertIn("tiivistys ei onnistunut (llm_vastaus_ei_json)", t["teksti"])
        self.assertIn("levy_kaytetty_prosenttia", t["teksti"])  # raakadata mukana
        self.assertEqual(len(self.tg), 1)

    def test_kulukatto_ylittyy(self):
        for _ in range(rajat.KUTSUT_PAIVA):
            self.aja(llm=True)
        t = self.aja(llm=True)
        self.assertEqual((t["koodi"], t["virhe"]), (1, "kulukatto_kutsut_paiva"))
        self.assertEqual(len(self.llm_kutsut), rajat.KUTSUT_PAIVA)

    def test_asetukset_puuttuvat(self):
        t = self.aja(llm=True, env={})
        self.assertEqual((t["koodi"], t["virhe"]), (1, "llm_asetus"))

    def test_sydan_vain_onnistuneen_lahetyksen_jalkeen(self):
        self.aja(llm=True, laheta=True, env=dict(ENV, SYDAN_URL="http://kuma/x"))
        self.assertEqual(self.sydan, ["http://kuma/x"])

    def test_sydan_ei_virheessa_eika_kuivassa(self):
        env = dict(ENV, SYDAN_URL="http://kuma/x")
        self.aja(llm_vastaus("rikki"), llm=True, laheta=True, env=env)
        self.aja(llm=False, laheta=False, env=env)
        self.assertEqual(self.sydan, [])

    def test_ei_koskaan_vuoda_avainta(self):
        t = self.aja(llm_vastaus("rikki"), llm=True, laheta=True)
        self.assertNotIn(ENV["LLM_API_KEY"], t["teksti"])
        self.assertNotIn(ENV["TELEGRAM_BOT_TOKEN"] + "/", t["teksti"])

    def test_tilahakemisto_luodaan_yksityisena(self):
        self.aja(llm=False)
        self.assertEqual(oct(os.stat(self.tila).st_mode & 0o777), "0o700")


class OletusliputTesti(AjoTesti):
    def test_oletuksena_kuiva_ajo_ilman_llm_ja_lahetysta(self):
        t = ajo.suorita(esimerkki, ENV, self.tila, kello=kello(),
                        hae_llm=lambda *a: self.fail("LLM:aa ei saa kutsua"),
                        hae_tg=lambda *a: self.fail("Telegramia ei saa kutsua"),
                        hae_sydan=lambda *a: self.fail("Sydanta ei saa kutsua"))
        self.assertEqual((t["koodi"], t["virhe"], t["lahetys"]), (0, None, None))
        self.assertIn("kuiva-ajo", t["teksti"])


if __name__ == "__main__":
    unittest.main()
