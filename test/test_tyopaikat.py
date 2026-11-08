import json
import os
import tempfile
import unittest

from agentti import tyopaikat as t

GH = {"jobs": [{"id": 1, "title": "Senior DevOps Engineer", "absolute_url": "https://x/1", "location": {"name": "Helsinki"}},
               {"id": 2, "title": "Sales Lead", "absolute_url": "https://x/2", "location": {"name": "Helsinki"}}]}
LV = [{"id": "a", "text": "Backend Developer (Python)", "hostedUrl": "https://l/a", "categories": {"team": "Eng", "location": "Remote EU"}}]
AB = {"jobs": [{"id": "z", "title": "Platform Engineer", "jobUrl": "https://a/z", "department": "Eng", "team": "Infra",
                "location": "Finland", "isListed": True},
               {"id": "y", "title": "MLOps", "jobUrl": "https://a/y", "isListed": False}]}
RSS = """<rss><channel><item><title>Ty\u00f6ss\u00e4oppiminen: ohjelmistokehitys</title><link>https://r/1</link><guid>g1</guid></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Python dev</title><link href="https://r/2"/><id>i2</id></entry></feed>"""


def avaa_mock(osoite):
    if "greenhouse" in osoite:
        return json.dumps(GH).encode()
    if "lever" in osoite:
        return json.dumps(LV).encode()
    if "ashby" in osoite:
        return json.dumps(AB).encode()
    if osoite.endswith("atom"):
        return ATOM.encode()
    if osoite.endswith("rss"):
        return RSS.encode()
    raise OSError("verkko")


class SuodatusTest(unittest.TestCase):
    def test_sopii(self):
        self.assertTrue(t.sopii({"otsikko": "Senior DevOps"}, t.OLETUS_HAKUSANAT))
        self.assertFalse(t.sopii({"otsikko": "Sales"}, t.OLETUS_HAKUSANAT))
        self.assertTrue(t.sopii({"otsikko": "Ty\u00f6ss\u00e4oppiminen"}, t.OLETUS_HAKUSANAT))

    def test_paikat(self):
        i = {"otsikko": "Python dev", "sijainti": "Berlin"}
        self.assertFalse(t.sopii(i, ["python"], ["helsinki"]))
        self.assertTrue(t.sopii(i, ["python"], ["berlin"]))


class SanarajaTest(unittest.TestCase):
    H = ["intern", "ai", "sre"]

    def test_alimerkkijono_ei_riita(self):
        self.assertFalse(t.sopii({"otsikko": "International Tax Manager"}, self.H))
        self.assertFalse(t.sopii({"otsikko": "Maintainer of sresearch"}, self.H))

    def test_kokonainen_sana_kay(self):
        self.assertTrue(t.sopii({"otsikko": "Backend Intern"}, self.H))
        self.assertTrue(t.sopii({"otsikko": "AI Infrastructure Engineer"}, self.H))
        self.assertTrue(t.sopii({"otsikko": "Intern, Platform"}, self.H))

    def test_internship_osuu_oletushakusanoilla(self):
        O = t.OLETUS_HAKUSANAT
        self.assertTrue(t.sopii({"otsikko": "Software Engineering Internship 2027"}, O))
        self.assertTrue(t.sopii({"otsikko": "Harjoittelupaikka, tietohallinto"}, O))
        self.assertFalse(t.sopii({"otsikko": "Internal Auditor"}, O))
        self.assertFalse(t.sopii({"otsikko": "International Tax Manager"}, O))

    def test_erikoismerkit_escapataan(self):
        self.assertTrue(t.sopii({"otsikko": "C++ Developer"}, ["c++"]))
        self.assertFalse(t.sopii({"otsikko": "Cxx Developer"}, ["c++"]))


class RemoteAlueTest(unittest.TestCase):
    P = ["helsinki", "remote", "finland"]

    def ok(self, sij):
        return t.sopii({"otsikko": "Python dev", "sijainti": sij}, ["python"], self.P)

    def test_hylkaa_ei_eurooppa(self):
        for sij in ("Remote - United States", "Remote, US", "Remote (USA)", "Remote - Canada", "Remote Americas",
                    "Remote - APAC", "Remote LATAM", "Remote - India", "Remote - U.S."):
            self.assertFalse(self.ok(sij), sij)

    def test_hyvaksy_remote_ilman_aluetta_ja_eurooppa(self):
        for sij in ("Remote", "Remote - Europe", "Remote EMEA", "Remote - EU", "Remote, Finland", "Remote Nordics",
                    "Helsinki, Finland"):
            self.assertTrue(self.ok(sij), sij)

    def test_ei_remote_ei_vaikuta(self):
        self.assertFalse(t.sopii({"otsikko": "Python dev", "sijainti": "Austin, US"}, ["python"], self.P))


class EiSoviTest(unittest.TestCase):
    RIVI = {"yritys": "ICEYE", "otsikko": "ICEYE Engineering Intern Lab 2027"}

    def test_lista_osuu(self):
        syy = t.ei_sovi_syy(self.RIVI, [{"yritys": "iceye", "otsikko": "intern lab", "syy": "A904"}])
        self.assertEqual(syy, "A904")

    def test_lista_ei_osu_muuhun_ilmoitukseen(self):
        lista = [{"yritys": "iceye", "otsikko": "intern lab"}]
        self.assertIsNone(t.ei_sovi_syy({"yritys": "ICEYE", "otsikko": "DevOps Engineer"}, lista))
        self.assertIsNone(t.ei_sovi_syy({"yritys": "Muu", "otsikko": "Intern Lab"}, lista))

    def test_tyhja_saanto_ei_osu_kaikkeen(self):
        self.assertIsNone(t.ei_sovi_syy(self.RIVI, [{"syy": "x"}, "roska"]))

    def test_lauseet(self):
        for teksti in ("Open to master's student", "Bachelor\u2019s student, year 3", "Korkeakouluopiskelija, kes\u00e4"):
            self.assertTrue(t.ei_sovi_syy({"otsikko": "Intern", "kuvaus": teksti}), teksti)
        self.assertIsNone(t.ei_sovi_syy({"otsikko": "DevOps Intern", "kuvaus": "no degree needed"}))

    def test_aja_hylkaa_eika_kirjaa(self):
        with tempfile.TemporaryDirectory() as d:
            tila = os.path.join(d, "n.json")
            ase = {"lahteet": [{"tyyppi": "greenhouse", "nimi": "X", "tunnus": "x"}], "hakusanat": ["devops"],
                   "ei_sovi": [{"yritys": "x", "otsikko": "devops", "syy": "testi"}]}
            hylatyt = []
            osumat, virheet = t.aja(ase, tila, avaa=avaa_mock, hylatyt=hylatyt)
            self.assertEqual(osumat, [])
            self.assertEqual([(i["otsikko"], s) for i, s in hylatyt], [("Senior DevOps Engineer", "testi")])
            self.assertFalse(os.path.exists(tila))


class LukuTest(unittest.TestCase):
    def test_lahteet(self):
        self.assertEqual(len(t.lue_greenhouse("g", GH)), 2)
        self.assertEqual(t.lue_lever("l", LV)[0]["tiimi"], "Eng")
        self.assertEqual(len(t.lue_ashby("a", AB)), 1)  # isListed=False pois
        self.assertEqual(t.lue_rss("r", RSS)[0]["osoite"], "https://r/1")
        self.assertEqual(t.lue_rss("r", ATOM)[0]["id"], "rss:r:i2")

    def test_doctype_hylataan(self):
        with self.assertRaises(ValueError):
            t.lue_rss("r", '<!DOCTYPE x [<!ENTITY a "b">]><rss/>')

    def test_tunnus_ja_https(self):
        with self.assertRaises(ValueError):
            t.hae_lahde({"tyyppi": "lever", "tunnus": "../x"}, avaa_mock)
        with self.assertRaises(ValueError):
            t._avaa("http://example.com/")
        with self.assertRaises(ValueError):
            t.hae_lahde({"tyyppi": "outo", "tunnus": "x"}, avaa_mock)


class AjoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tila = os.path.join(self.tmp.name, "tila.json")
        self.as_ = {"lahteet": [{"tyyppi": "greenhouse", "tunnus": "g"}, {"tyyppi": "lever", "tunnus": "l"},
                                {"tyyppi": "ashby", "tunnus": "a"}, {"tyyppi": "rss", "nimi": "r", "osoite": "https://x/rss"},
                                {"tyyppi": "lever", "nimi": "rikki", "tunnus": "verkkovika"}]}
        self.viestit = []

    def laheta(self, tila, otsikko, teksti, **kw):
        self.viestit.append((tila, otsikko, teksti))
        return "lahetetty"

    def aja(self, **kw):
        def avaa(o):
            if "verkkovika" in o:
                raise OSError("salainen-osoite")
            return avaa_mock(o)
        return t.aja(self.as_, self.tila, avaa=avaa, laheta_fn=self.laheta, **kw)

    def test_kuiva_ei_kirjaa_eika_laheta(self):
        osumat, virheet = self.aja()
        self.assertEqual(len(osumat), 4)
        self.assertEqual(self.viestit, [])
        self.assertFalse(os.path.exists(self.tila))
        self.assertEqual(virheet, ["rikki: OSError"])  # vain tyyppi, ei viestia

    def test_poistaa_nahdyt(self):
        self.aja(laheta=True)
        self.assertEqual(len(self.viestit), 1)
        self.assertIn("DevOps", self.viestit[0][2])
        self.assertNotIn("Sales", self.viestit[0][2])
        osumat, _ = self.aja(laheta=True)
        self.assertEqual(osumat, [])
        self.assertEqual(len(self.viestit), 1)

    def test_lahetysvirhe_ei_kirjaa(self):
        self.laheta = lambda *a, **k: "epaonnistui"
        osumat, virheet = t.aja(self.as_, self.tila, avaa=avaa_mock, laheta_fn=self.laheta, laheta=True)
        self.assertIn("telegram: epaonnistui", virheet)
        self.assertEqual(t.lataa_nahdyt(self.tila), [])

    def test_era_jako_ja_ajoraja(self):
        data = {"jobs": [{"id": n, "title": "DevOps %d" % n, "absolute_url": "https://x/%d" % n} for n in range(t.MAX_AJOSSA + 5)]}
        avaa = lambda o: json.dumps(data).encode()
        t.aja({"lahteet": [{"tyyppi": "greenhouse", "tunnus": "g"}]}, self.tila, avaa=avaa, laheta_fn=self.laheta, laheta=True)
        self.assertTrue(all(len(v[2]) <= 1200 for v in self.viestit))
        self.assertIn("+5 lisaa", self.viestit[-1][2])
        self.assertEqual(len(t.lataa_nahdyt(self.tila)), t.MAX_AJOSSA)

    def test_rikkinainen_tila(self):
        with open(self.tila, "w") as f:
            f.write("{ei json")
        self.assertEqual(t.lataa_nahdyt(self.tila), [])


class IlmoitaYhteysTest(unittest.TestCase):
    def test_oikea_ilmoita_polku_ei_tunnuksia(self):
        with tempfile.TemporaryDirectory() as d:
            tulos = t.ilmoita.ilmoita("valmis", "x", "y", env_polku=os.path.join(d, "ei"), tila_polku=os.path.join(d, "t"))
            self.assertEqual(tulos, "ei_tunnuksia")


if __name__ == "__main__":
    unittest.main()
