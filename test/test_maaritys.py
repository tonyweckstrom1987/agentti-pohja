import json
import os
import tempfile
import unittest

from agentti import maaritys as m
from agentti import rajat


def ok(**kw):
    d = {"nimi": "ideointi", "kuvaus": "Ehdottaa kokeiluja", "ohje_tiedosto": "ohjeet/ideointi.md",
         "malli": "deepseek/deepseek-chat", "budjetti_usd_paivassa": 0.03, "max_tokens": 1500, "vyohyke": 1,
         "oikeustaso": "sandbox", "tyokalut": ["lue_tiedosto"], "lahteet": ["paikallinen:tapahtumaloki"],
         "tulosmuoto": "ehdotus_v1", "mittari": "ehdotuksista 30 % valitaan jatkoon", "ajastus": "01:00",
         "halytys": "telegram"}
    d.update(kw)
    return d


def virheet(d):
    try:
        m.tarkista(d)
    except m.MaaritysVirhe as e:
        return e.virheet
    return []


class MaaritysTest(unittest.TestCase):
    def test_kelvollinen(self):
        self.assertEqual(m.tarkista(ok())["nimi"], "ideointi")

    def test_valinnaiset_oletuksena_tyhjat(self):
        d = ok()
        del d["tyokalut"], d["lahteet"]
        t = m.tarkista(d)
        self.assertEqual((t["tyokalut"], t["lahteet"]), ([], []))

    def test_tuntematon_ja_puuttuva_kentta(self):
        self.assertIn("tuntematon kentta: x", virheet(ok(x=1)))
        d = ok()
        del d["mittari"]
        self.assertIn("puuttuu: mittari", virheet(d))
        self.assertTrue(virheet([]))

    def test_rajat_ovat_katto(self):
        self.assertTrue(virheet(ok(budjetti_usd_paivassa=rajat.KULU_PAIVA_USD + 0.01)))
        self.assertTrue(virheet(ok(budjetti_usd_paivassa=0)))
        self.assertTrue(virheet(ok(budjetti_usd_paivassa=True)))
        self.assertTrue(virheet(ok(max_tokens=rajat.MAX_TOKENS + 1)))
        self.assertTrue(virheet(ok(max_tokens=1.5)))
        self.assertEqual(virheet(ok(budjetti_usd_paivassa=rajat.KULU_PAIVA_USD, max_tokens=rajat.MAX_TOKENS)), [])

    def test_kenttien_muodot(self):
        for kw in ({"nimi": "Iso"}, {"nimi": "x"}, {"ohje_tiedosto": "../x.md"}, {"ohje_tiedosto": "/etc/passwd"},
                   {"malli": "a b"}, {"vyohyke": 3}, {"vyohyke": True}, {"oikeustaso": "tuotanto"},
                   {"tyokalut": ["shell"]}, {"lahteet": ["x"]}, {"lahteet": ["ulkoinen:A"]}, {"ajastus": "joskus"},
                   {"halytys": "sahkoposti"}, {"mittari": ""}, {"kuvaus": "x" * 301}):
            self.assertTrue(virheet(ok(**kw)), kw)

    def test_vyohyke_2_ei_lue_ulkopuolista(self):
        self.assertTrue(virheet(ok(vyohyke=2, lahteet=["ulkoinen:github"])))
        self.assertTrue(virheet(ok(vyohyke=2, tyokalut=["hae_github"])))
        self.assertEqual(virheet(ok(vyohyke=2, lahteet=["paikallinen:tavoitteet"])), [])
        self.assertEqual(virheet(ok(vyohyke=1, lahteet=["ulkoinen:github"], tyokalut=["hae_github"])), [])

    def test_kaikki_virheet_kerralla(self):
        self.assertGreaterEqual(len(virheet(ok(nimi="X", max_tokens=0, halytys="x"))), 3)

    def test_lue(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "a.json")
            with open(polku, "w") as f:
                json.dump(ok(), f)
            self.assertEqual(m.lue(polku)["nimi"], "ideointi")
            with open(polku, "w") as f:
                f.write("ei jsonia")
            with self.assertRaises(m.MaaritysVirhe):
                m.lue(polku)
            with self.assertRaises(m.MaaritysVirhe):
                m.lue(os.path.join(d, "puuttuu.json"))


class MaaritysRajatTarkkaTest(unittest.TestCase):
    def test_tekstikenttien_pituusrajat(self):
        for kentta, raja in (("kuvaus", 300), ("tulosmuoto", 60), ("mittari", 300)):
            self.assertEqual(virheet(ok(**{kentta: "x"})), [], kentta)
            self.assertEqual(virheet(ok(**{kentta: "x" * raja})), [], kentta)
            self.assertTrue(virheet(ok(**{kentta: "x" * (raja + 1)})), kentta)
            self.assertTrue(virheet(ok(**{kentta: "   "})), kentta)

    def test_max_tokens_ja_budjetti_alarajat(self):
        self.assertEqual(virheet(ok(max_tokens=1)), [])
        self.assertTrue(virheet(ok(max_tokens=0)))
        self.assertEqual(virheet(ok(budjetti_usd_paivassa=0.001)), [])


if __name__ == "__main__":
    unittest.main()
