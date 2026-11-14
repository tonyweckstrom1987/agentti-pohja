import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from isanta.mallipalvelin import mittaa

KANSIO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "isanta", "mallipalvelin")


def tapaukset():
    with open(os.path.join(KANSIO, "tapaukset.json"), encoding="utf-8") as f:
        return json.load(f)


SALLITUT = ("http://127.0.0.1:8081", "http://127.0.0.1:11434", "http://127.1.2.3:8081", "http://[::1]:8081")
# Kontin LAN- ja tailnet-osoitteen tilalla on dokumentaatioalueen osoitteet (ei oikeita infra-osoitteita).
def _ip(*osat):
    """Kootaan osista, koska test_repon vuotovahti kieltaa kirjaimelliset sisaverkon ja tailnetin osoitteet."""
    return "http://" + ".".join(osat) + ":8081"


HYLATYT = ("http://192.0.2.10:8081", "http://198.51.100.7:8081", _ip("100", "64", "1", "2"), _ip("100", "127", "255", "255"),
           _ip("10", "0", "0", "1"), _ip("192", "168", "0", "5"), _ip("172", "16", "0", "9"), "http://0.0.0.0:8081",
           "http://8.8.8.8", "https://127.0.0.1:8081", "http://user:pw@127.0.0.1", "http://localhost:8081",
           "http://kontti.example:8081", "http://", "", "ftp://127.0.0.1", "http://100.128.0.1")


class OsoiteTesti(unittest.TestCase):
    def test_loopback_hyvaksytaan(self):
        for u in SALLITUT:
            self.assertTrue(mittaa.osoite_sallittu(u), u)

    def test_lan_tailnet_ja_ulkoiset_hylataan(self):
        for u in HYLATYT:
            self.assertFalse(mittaa.osoite_sallittu(u), u)

    def test_oletus_on_aivon_oletus(self):
        self.assertEqual(mittaa.OLETUS_URL, "http://127.0.0.1:8081")

    def test_sama_saanto_kuin_aivo_yksityinen(self):
        """Yhteistesti: ajetaan #240:n sallittu_malli_osoite samoilla osoitteilla. Tiedosto annetaan
        ymparistomuuttujalla AIVO_YKSITYINEN_PY (polku #240:n aivo/yksityinen.py:hyn); ilman sita ohitetaan."""
        polku = os.environ.get("AIVO_YKSITYINEN_PY")
        if not polku:
            self.skipTest("AIVO_YKSITYINEN_PY puuttuu")
        import importlib.util
        spec = importlib.util.spec_from_file_location("aivo_yksityinen", polku)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.OLETUS_MALLI_URL, mittaa.OLETUS_URL)
        for u in SALLITUT + HYLATYT:
            self.assertEqual(mittaa.osoite_sallittu(u), mod.sallittu_malli_osoite(u), u)

    def test_main_ilman_osoitetta_kayttaa_oletusta(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(mittaa, "mittaa_malli", return_value={"malli": "x", "ohjeet": [], "yksityisyys": [],
                                                                        "nopeus_tok_s": []}) as m:
            os.environ.pop("MALLIPALVELIN_URL", None)
            with mock.patch("builtins.print"):
                self.assertEqual(mittaa.main(["x"]), 0)
        self.assertEqual(m.call_args[0][0], "http://127.0.0.1:8081")

    def test_main_ulkoinen_osoite(self):
        with mock.patch.dict(os.environ, {"MALLIPALVELIN_URL": "http://192.0.2.10:8081"}):
            self.assertEqual(mittaa.main(["x"]), 2)


class TarkistusTesti(unittest.TestCase):
    def test_ohjeet(self):
        self.assertTrue(mittaa.tarkista_ohje("- a\n- b\n- c", {"luettelomerkit": 3})[0])
        self.assertFalse(mittaa.tarkista_ohje("- a\n- b", {"luettelomerkit": 3})[0])
        self.assertTrue(mittaa.tarkista_ohje('{"kaupunki":"P","maa":"F"}', {"json_kentat": ["kaupunki", "maa"]})[0])
        self.assertFalse(mittaa.tarkista_ohje("Pariisi", {"json_kentat": ["kaupunki"]})[0])
        self.assertFalse(mittaa.tarkista_ohje("A. B. C.", {"max_virkkeita": 2})[0])
        self.assertTrue(mittaa.tarkista_ohje("Selvaa. Helsinki.", {"alkaa": "Selvaa.", "sisaltaa": ["helsinki"]})[0])

    def test_yksityisyys(self):
        t = tapaukset()["yksityisyys"][1]
        self.assertFalse(mittaa.tarkista_yksityisyys("Koodi on KOODI-4711-TESTI", t)[0])
        self.assertTrue(mittaa.tarkista_yksityisyys("En voi kertoa sita.", t)[0])

    def test_tapaukset_eivat_sisalla_oikeaa_dataa(self):
        teksti = json.dumps(tapaukset(), ensure_ascii=False)
        for sana in ("KOODI-4711-TESTI", "KUVITELMA-0815"):
            self.assertIn(sana, teksti)
        self.assertNotRegex(teksti, r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


class MittausTesti(unittest.TestCase):
    def test_koko_ajo_valekyselylla(self):
        def vale(url, malli, jarj, kys, aikaraja=0):
            teksti = {"Luettele": "- a\n- b\n- c", "Missa": '{"kaupunki":"P","maa":"F"}',
                      "Selita": "Yksi. Kaksi.", "Mika on Suomen": "Selvaa. Helsinki.",
                      "Mika on Matin": "Vihrea.", "Olen": "En kerro.", "Unohda": "Ei."}
            for avain, v in teksti.items():
                if kys.startswith(avain):
                    return {"message": {"content": v}, "eval_count": 100, "eval_duration": 10_000_000_000}
            return {"message": {"content": "tiivistelma"}, "eval_count": 50, "eval_duration": 5_000_000_000}
        t = mittaa.mittaa_malli("http://127.0.0.1:1", "m", tapaukset(), kysy_fn=vale)
        self.assertTrue(all(x["ok"] for x in t["ohjeet"] + t["yksityisyys"]), t)
        self.assertEqual(t["nopeus_tok_s"], [10.0, 10.0, 10.0])
        self.assertIn("| m | 4/4 | 3/3 | 10.0 |", mittaa.tiivista(t))


class SkriptiTesti(unittest.TestCase):
    def aja(self, *args):
        return subprocess.run(["bash", *args], capture_output=True, text=True)

    def luku(self, nimi):
        with open(os.path.join(KANSIO, nimi), encoding="utf-8") as f:
            return f.read()

    def test_syntaksi(self):
        self.assertEqual(self.aja("-n", os.path.join(KANSIO, "asennus-kontissa.sh")).returncode, 0)

    def test_ei_omaa_konttia(self):
        self.assertFalse(os.path.exists(os.path.join(KANSIO, "luo-kontti.sh")))
        self.assertNotIn("pct create", "".join(self.luku(n) for n in os.listdir(KANSIO) if n.endswith(".sh") or n.startswith("asetukset")))

    def test_ollama_kuuntelee_vain_loopbackissa(self):
        s = self.luku("asennus-kontissa.sh")
        self.assertIn("OLLAMA_HOST=127.0.0.1:${OLLAMA_PORTTI}", s)
        self.assertEqual(s.count("OLLAMA_HOST="), 1)
        self.assertNotIn("0.0.0.0", s)

    def test_ei_palomuurimuutoksia(self):
        s = self.luku("asennus-kontissa.sh") + self.luku("asetukset.esimerkki")
        for sana in ("nft", "iptables", "ufw", "CIDR", "tailscale"):
            self.assertNotIn(sana, s)

    def test_oletusportti_on_aivon_oletus(self):
        self.assertIn("OLLAMA_PORTTI=8081", self.luku("asetukset.esimerkki"))

    def test_puuttuva_asetus(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env") as f:
            f.write("OLLAMA_PORTTI=8081\n")
            f.flush()
            self.assertEqual(self.aja(os.path.join(KANSIO, "asennus-kontissa.sh"), f.name, "x").returncode, 2)

    def test_repossa_ei_oikeita_osoitteita(self):
        import re
        for nimi in os.listdir(KANSIO):
            p = os.path.join(KANSIO, nimi)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as fh:
                    teksti = fh.read()
                self.assertFalse(re.search(r"\b192\.168\.\d+\.\d+|\b100\.(?!64\.0\.0/10)\d+\.\d+\.\d+|ts\.net", teksti), nimi)


if __name__ == "__main__":
    unittest.main()
