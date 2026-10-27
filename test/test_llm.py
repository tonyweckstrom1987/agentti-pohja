import http.server
import json
import socket
import threading
import tempfile
import unittest
import urllib.error
from unittest import mock

from agentti import kulu, llm, rajat
from test.apu import ENV, kello, llm_vastaus

VIESTIT = [{"role": "user", "content": "hei"}]


class LLMTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kulu = kulu.Kulu(self.tmp.name, kello())
        self.kutsut = []

    def hae(self, vastaus):
        def _hae(url, avain, payload):
            self.kutsut.append((url, avain, payload))
            if isinstance(vastaus, Exception):
                raise vastaus
            return vastaus
        return _hae

    def kysy(self, vastaus, **kw):
        return llm.kysy(VIESTIT, "m", ENV, self.kulu, hae=self.hae(vastaus), **kw)

    def test_onnistunut_kutsu(self):
        teksti, kaytto = self.kysy(llm_vastaus("vastaus"))
        self.assertEqual(teksti, "vastaus")
        self.assertEqual(kaytto["prompt_tokens"], 100)
        url, avain, payload = self.kutsut[0]
        self.assertEqual(url, "http://litellm.example:4000/v1/chat/completions")
        self.assertEqual(avain, "testiavain")

    def test_payloadissa_ei_tyokaluja(self):
        self.kysy(llm_vastaus("x"))
        payload = self.kutsut[0][2]
        self.assertNotIn("tools", payload)
        self.assertNotIn("functions", payload)
        self.assertNotIn("tool_choice", payload)

    def test_max_tokens_katto(self):
        self.kysy(llm_vastaus("x"), max_tokens=10 ** 6)
        self.assertEqual(self.kutsut[0][2]["max_tokens"], rajat.MAX_TOKENS)
        self.kysy(llm_vastaus("x"), max_tokens=50)
        self.assertEqual(self.kutsut[1][2]["max_tokens"], 50)

    def assertKoodi(self, koodi, vastaus, **kw):
        self.kulu = kulu.Kulu(tempfile.mkdtemp(dir=self.tmp.name), kello())  # tuore paivakiintio per tapaus
        with self.assertRaises(llm.LLMVirhe) as e:
            self.kysy(vastaus, **kw)
        self.assertEqual(e.exception.koodi, koodi)

    def test_virhekoodit(self):
        self.assertKoodi("llm_vastaus_tyhja", llm_vastaus("   "))
        self.assertKoodi("llm_vastaus_tyhja", llm_vastaus(None))
        self.assertKoodi("llm_vastaus_katkaistu", llm_vastaus("puoli", finish="length"))
        natiivi = llm_vastaus("puoli")
        natiivi["choices"][0]["native_finish_reason"] = "MAX_TOKENS"
        self.assertKoodi("llm_vastaus_katkaistu", natiivi)
        self.assertKoodi("llm_vastaus_rakenne", {"choices": []})
        self.assertKoodi("llm_vastaus_rakenne", {})
        self.assertKoodi("llm_http_500", llm.LLMVirhe("llm_http_500"))

    def test_kulu_varataan_myos_virheessa(self):
        self.assertKoodi("llm_yhteys", llm.LLMVirhe("llm_yhteys"))
        self.assertEqual(self.kulu.tilanne()["kutsut_tanaan"], 1)

    def test_kulukatto_estaa_kutsun_ennen_verkkoa(self):
        for _ in range(rajat.KUTSUT_PAIVA):
            self.kulu.varaa()
        with self.assertRaises(kulu.KulukattoYlittyi):
            self.kysy(llm_vastaus("x"))
        self.assertEqual(self.kutsut, [])

    def test_asetukset_puuttuvat(self):
        for env in ({}, {"LLM_BASE_URL": "http://x"}, {"LLM_API_KEY": "k"},
                    {"LLM_BASE_URL": "ftp://x", "LLM_API_KEY": "k"}):
            with self.assertRaises(llm.LLMVirhe) as e:
                llm.kysy(VIESTIT, "m", env, self.kulu, hae=self.hae(llm_vastaus("x")))
            self.assertEqual(e.exception.koodi, "llm_asetus")
        self.assertEqual(self.kutsut, [])

    def test_viestien_tarkistus(self):
        for v in ([], "x", [{"role": "tool", "content": "x"}], [{"role": "user", "content": 5}]):
            with self.assertRaises(llm.LLMVirhe):
                llm.kysy(v, "m", ENV, self.kulu, hae=self.hae(llm_vastaus("x")))

    def test_todellinen_kulu_hinnastosta(self):
        rajat.HINNAT_USD_PER_MILJOONA["m"] = (1.0, 2.0)
        self.addCleanup(rajat.HINNAT_USD_PER_MILJOONA.pop, "m")
        self.kysy(llm_vastaus("x", kaytto={"prompt_tokens": 1000, "completion_tokens": 500}))
        self.assertAlmostEqual(self.kulu.tilanne()["usd_tanaan"], 0.002, places=6)

    def test_tuntematon_malli_jattaa_varauksen(self):
        self.kysy(llm_vastaus("x"))
        self.assertAlmostEqual(self.kulu.tilanne()["usd_tanaan"], rajat.KUTSU_ARVIO_USD, places=6)

    def test_arvioi_usd_huono_kaytto(self):
        self.assertIsNone(llm.arvioi_usd(None, "m"))
        self.assertIsNone(llm.arvioi_usd({"prompt_tokens": "x"}, "m"))


class VerkkovirheTesti(unittest.TestCase):
    """_http_post: virheiden muunnos koodeiksi (ilman oikeaa verkkoa)."""

    def kokeile(self, poikkeus):
        import urllib.request
        alkup = urllib.request.OpenerDirector.open

        def feikki(self, *a, **kw):
            raise poikkeus
        urllib.request.OpenerDirector.open = feikki
        try:
            llm._http_post("http://x/v1/chat/completions", "k", {})
        except llm.LLMVirhe as e:
            return e.koodi
        finally:
            urllib.request.OpenerDirector.open = alkup

    def test_koodit(self):
        self.assertEqual(self.kokeile(urllib.error.HTTPError("u", 429, "m", {}, None)), "llm_http_429")
        self.assertEqual(self.kokeile(urllib.error.HTTPError("u", 302, "m", {}, None)), "llm_http_302")
        self.assertEqual(self.kokeile(urllib.error.URLError(socket.timeout())), "llm_aikakatkaisu")
        self.assertEqual(self.kokeile(urllib.error.URLError("refused")), "llm_yhteys")
        self.assertEqual(self.kokeile(socket.timeout()), "llm_aikakatkaisu")
        self.assertEqual(self.kokeile(ConnectionResetError()), "llm_yhteys")
        self.assertEqual(self.kokeile(ValueError()), "llm_vastaus_rakenne")

    def test_uudelleenohjauksia_ei_seurata(self):
        self.assertIsNone(llm._EiUudelleenohjauksia().redirect_request(None, None, 302, "m", {}, "http://muu"))


class ArvioiTarkkaTesti(unittest.TestCase):
    def test_hinnaston_mukainen_kulu(self):
        with mock.patch.dict(rajat.HINNAT_USD_PER_MILJOONA, {"m": (0.5, 1.5)}):
            self.assertEqual(llm.arvioi_usd({"prompt_tokens": 3_000_000, "completion_tokens": 1_000_000}, "m"), 3.0)
            self.assertIsNone(llm.arvioi_usd({"prompt_tokens": 1}, "m"))
            self.assertIsNone(llm.arvioi_usd({"prompt_tokens": "x", "completion_tokens": 1}, "m"))
            self.assertIsNone(llm.arvioi_usd(None, "m"))
            self.assertIsNone(llm.arvioi_usd({"prompt_tokens": 1, "completion_tokens": 1}, "tuntematon"))


class ValintaTarkkaTesti(LLMTesti):
    def test_lampotila_on_nolla(self):
        self.kysy(llm_vastaus("x"))
        self.assertEqual(self.kutsut[0][2]["temperature"], 0)

    def test_ensimmainen_vaihtoehto_valitaan(self):
        vastaus = {"choices": [{"message": {"content": "eka"}}, {"message": {"content": "toka"}}], "usage": {}}
        self.assertEqual(self.kysy(vastaus)[0], "eka")


class _Palvelin(http.server.BaseHTTPRequestHandler):
    vastaus = (200, b'{"choices": []}', {})
    nahty = {}

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        type(self).nahty = {"auth": self.headers.get("Authorization"), "tyyppi": self.headers.get("Content-Type"),
                            "runko": json.loads(self.rfile.read(n)), "polku": self.path}
        koodi, runko, otsakkeet = type(self).vastaus
        self.send_response(koodi)
        for k, v in otsakkeet.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(runko)))
        self.end_headers()
        self.wfile.write(runko)

    def log_message(self, *a):
        pass


class HttpPostTesti(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), _Palvelin)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = "http://127.0.0.1:%d/x" % self.srv.server_address[1]

    def laheta(self, vastaus):
        _Palvelin.vastaus = vastaus
        return llm._http_post(self.url, "avain", {"a": 1})

    def test_onnistunut_pyynto_palauttaa_jsonin_ja_lahettaa_otsakkeet(self):
        self.assertEqual(self.laheta((200, b'{"choices": [1]}', {})), {"choices": [1]})
        n = _Palvelin.nahty
        self.assertEqual((n["auth"], n["tyyppi"], n["runko"], n["polku"]), ("Bearer avain", "application/json", {"a": 1}, "/x"))

    def test_http_virheet_ja_uudelleenohjausta_ei_seurata(self):
        for koodi, odotettu in ((500, "llm_http_500"), (401, "llm_http_401"), (302, "llm_http_302")):
            with self.assertRaises(llm.LLMVirhe, msg=koodi) as e:
                self.laheta((koodi, b"{}", {"Location": "http://127.0.0.1:1/"}))
            self.assertEqual(e.exception.koodi, odotettu)

    def test_ei_jsonia_ja_ei_yhteytta(self):
        with self.assertRaises(llm.LLMVirhe) as e:
            self.laheta((200, b"ei json", {}))
        self.assertEqual(e.exception.koodi, "llm_vastaus_rakenne")
        self.srv.shutdown()
        self.srv.server_close()
        with self.assertRaises(llm.LLMVirhe) as e:
            llm._http_post(self.url, "avain", {})
        self.assertEqual(e.exception.koodi, "llm_yhteys")


if __name__ == "__main__":
    unittest.main()
