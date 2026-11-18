"""Testit aivo/yksityinen_or.py:lle (yksityisen tilan OpenRouter-taustapaa). Ei verkkoa, ei oikeita avaimia."""
import ast
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock

from aivo import chat, openrouter_kulut as ok, openrouter_zdr, yksityinen, yksityinen_or as yo

AIVO = Path(__file__).resolve().parent
MALLI = "meta-llama/llama-3.3-70b-instruct"
AVAIN = "sk-testi-" + "x" * 30          # keksitty, ei oikea avain
NYT = datetime(2026, 11, 16, 12, 0, tzinfo=ok.TZ)
HISTORIA = [{"rooli": "tony", "teksti": "moi"}]


class FeikkiVastaus:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self, n=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Pohja(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        self.avain = self.d / "avain"
        self.avain.write_text(AVAIN + "\n")
        os.chmod(self.avain, 0o600)
        self.kulut = self.d / "kulut"
        self.kulut.mkdir(mode=0o700)
        self.kutsut = []

    def avaa(self, vastaus=None):
        def avaa(pyynto, timeout=None):
            self.kutsut.append(pyynto)
            return FeikkiVastaus(vastaus or {"choices": [{"message": {"content": " Hei! "}}],
                                              "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}})
        return avaa

    def kysy(self, avaa=None, **lisa):
        arg = dict(malli=MALLI, avain_tiedosto=str(self.avain), kulutila=str(self.kulut),
                   avaa=avaa or self.avaa(), nyt=lambda: NYT)
        arg.update(lisa)
        return yo.kysy_openrouterilta("muisti", HISTORIA, "ohje", **arg)

    def kulurivit(self):
        p = self.kulut / ok.KULUT
        return [json.loads(r) for r in p.read_text().splitlines()] if p.exists() else []


class OnnistunutKutsuTesti(Pohja):
    def test_vastaus_runko_ja_kirjaus(self):
        self.assertEqual(self.kysy(), "Hei!")
        [p] = self.kutsut
        self.assertEqual(p.full_url, ok.OSOITE)
        self.assertEqual(ok.OSOITE, openrouter_zdr.OSOITE)
        self.assertEqual(p.get_header("Authorization"), "Bearer " + AVAIN)
        runko = json.loads(p.data)
        self.assertEqual(runko["model"], MALLI)
        self.assertIs(runko["provider"]["zdr"], True)
        self.assertEqual(runko["provider"]["data_collection"], "deny")
        self.assertEqual(runko["messages"][0]["role"], "system")
        self.assertIn("ZDR", runko["messages"][0]["content"])            # kehote ei vaita, etta mikaan ei lahde ulos
        self.assertNotIn("ei l\u00e4hde millek\u00e4\u00e4n ulkoiselle", runko["messages"][0]["content"])
        rivit = [r for r in self.kulurivit() if not r.get("varaus")]
        self.assertEqual(len(rivit), 1)
        self.assertEqual(rivit[0]["usd"], 0.001)
        self.assertNotIn("muisti", json.dumps(self.kulurivit()))         # viestien sisaltoa ei kirjata

    def test_kehote_vaihtuu_vain_kun_lause_loytyy(self):
        self.assertNotEqual(yo.KEHOTE, yksityinen.YKSITYINEN_KEHOTE)


class ZdrPuuttuuTesti(Pohja):
    def test_zdr_false_ei_kutsua(self):
        with self.assertRaises(yksityinen.YksityinenVirhe):
            self.kysy(provider={"zdr": False})
        self.assertEqual(self.kutsut, [])
        self.assertEqual(self.kulurivit(), [])

    def test_data_collection_allow_ei_kutsua(self):
        with self.assertRaises(yksityinen.YksityinenVirhe):
            self.kysy(provider={"data_collection": "allow"})
        self.assertEqual(self.kutsut, [])

    def test_kuljetin_kieltaytyy_ilman_zdr_runkoa(self):
        laheta = yo.tee_kuljetin(AVAIN, self.avaa())
        for runko in ({"model": MALLI, "messages": []},
                      {"model": MALLI, "provider": {"zdr": True}},
                      {"model": MALLI, "provider": {"zdr": False, "data_collection": "deny"}},
                      {"model": MALLI, "provider": {"zdr": True, "data_collection": "allow"}},
                      {"model": MALLI, "provider": {"zdr": 1, "data_collection": "deny"}}):
            with self.assertRaises(yksityinen.YksityinenVirhe):
                laheta(runko)
        self.assertEqual(self.kutsut, [])
        laheta({"model": MALLI, "provider": {"zdr": True, "data_collection": "deny"}})
        self.assertEqual(len(self.kutsut), 1)


class YhteinenKattoTesti(Pohja):
    """A963/A984: yksityinen tila kuluttaa samaa kirjanpitoa ja samaa 10 EUR/vrk kattoa kuin muut kutsut."""

    def test_ei_omaa_kulutilaa(self):
        self.assertFalse(hasattr(yo, "OLETUS_KULUTILA"))
        import inspect
        self.assertIsNone(inspect.signature(yo.kysy_openrouterilta).parameters["kulutila"].default)
        self.assertIsNone(inspect.signature(chat.Tila.__init__).parameters["or_kulutila"].default)
        self.assertNotIn("--openrouter-kulutila", (AIVO / "chat.py").read_text(encoding="utf-8"))

    def test_oletuskulutila_on_ajurin_yhteinen(self):
        with mock.patch.object(ok, "OLETUS_TILA", str(self.kulut)):
            yo.kysy_openrouterilta("m", HISTORIA, malli=MALLI, avain_tiedosto=str(self.avain),
                                   avaa=self.avaa(), nyt=lambda: NYT)
        self.assertEqual(len([r for r in self.kulurivit() if not r.get("varaus")]), 1)

    def test_ajurin_kulut_tayttavat_yksityisen_katon(self):
        with mock.patch.object(ok, "OLETUS_TILA", str(self.kulut)):
            ok.kutsu(MALLI, [{"role": "user", "content": "x"}], lambda r: {"usage": {"cost": 20.0}},
                     tehtava="ajuri", nyt=lambda: NYT)
            with self.assertRaises(yksityinen.YksityinenVirhe) as e:
                yo.kysy_openrouterilta("m", HISTORIA, malli=MALLI, avain_tiedosto=str(self.avain),
                                       avaa=self.avaa(), nyt=lambda: NYT)
        self.assertIn("p\u00e4iv\u00e4katto", str(e.exception))
        self.assertEqual(self.kutsut, [])

    def test_yksityisen_kulut_tayttavat_ajurin_katon(self):
        with mock.patch.object(ok, "OLETUS_TILA", str(self.kulut)):
            yo.kysy_openrouterilta("m", HISTORIA, malli=MALLI, avain_tiedosto=str(self.avain), nyt=lambda: NYT,
                                   avaa=self.avaa({"choices": [{"message": {"content": "x"}}], "usage": {"cost": 20.0}}))
            self.assertFalse(ok.saako_kutsua(NYT))

    def test_pyynto_valittaa_yksityisen_lipun(self):
        with mock.patch.object(ok.openrouter_zdr, "rakenna_runko", wraps=ok.openrouter_zdr.rakenna_runko) as m:
            self.kysy()
            self.assertIs(m.call_args.kwargs["yksityinen_chat"], True)
            self.kysy(yksityinen_chat=False)                  # kutsuja ei voi poistaa lippua
            self.assertIs(m.call_args.kwargs["yksityinen_chat"], True)


class PaivakattoTesti(Pohja):
    def tayta(self, usd):
        with open(self.kulut / ok.KULUT, "w") as f:
            f.write(json.dumps({"aika": NYT.isoformat(), "malli": "m/x", "tehtava": "t", "usd": usd}) + "\n")

    def test_katto_taynna_ei_kutsua(self):
        self.tayta(20.0)                                  # 20 USD * 0,92 > 10 EUR
        with self.assertRaises(yksityinen.YksityinenVirhe) as e:
            self.kysy()
        self.assertIn("p\u00e4iv\u00e4katto", str(e.exception))
        self.assertEqual(self.kutsut, [])

    def test_alle_katon_kutsutaan(self):
        self.tayta(1.0)
        self.kysy()
        self.assertEqual(len(self.kutsut), 1)

    def test_kirjanpito_rikki_ei_kutsua(self):
        (self.kulut / ok.KULUT).write_text("ei json\n")
        with self.assertRaises(yksityinen.YksityinenVirhe):
            self.kysy()
        self.assertEqual(self.kutsut, [])

    def test_kulutila_puuttuu_ei_kutsua(self):
        with self.assertRaises(yksityinen.YksityinenVirhe):
            self.kysy(kulutila=str(self.d / "ei-ole"))
        self.assertEqual(self.kutsut, [])

    def test_kuljetinvirhe_kirjataan_kulutuksena(self):
        def avaa(pyynto, timeout=None):
            raise OSError("verkko")
        with self.assertRaises(yksityinen.YksityinenVirhe):
            self.kysy(avaa=avaa)
        [r] = [r for r in self.kulurivit() if not r.get("varaus")]
        self.assertTrue(r["virhe"])


class AvainTesti(Pohja):
    def assert_selkea_virhe_ei_kutsua(self, osa):
        with self.assertRaises(yksityinen.YksityinenVirhe) as e:
            self.kysy()
        self.assertIn(osa, str(e.exception))
        self.assertNotIn(AVAIN, str(e.exception))
        self.assertEqual(self.kutsut, [])
        self.assertEqual(self.kulurivit(), [])            # ei varausta

    def test_avain_puuttuu(self):
        self.avain.unlink()
        self.assert_selkea_virhe_ei_kutsua("avain puuttuu")

    def test_avaintiedosto_tyhja(self):
        self.avain.write_text("\n")
        self.assert_selkea_virhe_ei_kutsua("avain puuttuu")

    def test_oikeudet_liian_laajat(self):
        os.chmod(self.avain, 0o640)
        self.assert_selkea_virhe_ei_kutsua("oikeudet")

    def test_linkki_hylataan(self):
        oikea = self.d / "oikea"
        self.avain.rename(oikea)
        self.avain.symlink_to(oikea)
        self.assert_selkea_virhe_ei_kutsua("linkki")

    def test_ymparistomuuttujasta_ei_lueta_avainta(self):
        self.avain.unlink()
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": AVAIN, "AIVO_OPENROUTER_AVAIN": AVAIN}):
            self.assert_selkea_virhe_ei_kutsua("avain puuttuu")

    def test_http_virhe_nayttaa_vain_tilakoodin(self):
        def avaa(pyynto, timeout=None):
            raise urllib.error.HTTPError(pyynto.full_url, 401, "Authorization: Bearer " + AVAIN, {}, None)
        with self.assertRaises(yksityinen.YksityinenVirhe) as e:
            self.kysy(avaa=avaa)
        self.assertIn("401", str(e.exception))
        self.assertNotIn(AVAIN, str(e.exception))
        self.assertNotIn("Bearer", str(e.exception))

    def test_avainta_ei_kirjata_kulutiedostoon(self):
        self.kysy()
        for p in self.kulut.iterdir():
            self.assertNotIn(AVAIN, p.read_text())


class MalliTesti(unittest.TestCase):
    def test_avoimet_kelpaavat(self):
        for m in (MALLI, "qwen/qwen3-32b", "google/gemma-3-27b-it", "mistralai/mistral-small-3.2-24b-instruct"):
            self.assertTrue(yo.avoin_malli(m), m)

    def test_suljetut_ja_liitteet_hylataan(self):
        for m in (None, "", "anthropic/claude-sonnet-4", "openai/gpt-4o", "qwen/qwen-max", "mistralai/mistral-large",
                  MALLI + ":free", MALLI + ":online", "meta-llama/llama-3.3-70b-instruct:nitro", "x", "../meta-llama/llama-3",
                  "meta-llama/llama-3 ", "META-LLAMA/llama-3.3"):
            self.assertFalse(yo.avoin_malli(m), m)

    def test_oletusmallia_ei_ole(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yo.kysy_openrouterilta("m", HISTORIA, malli=None, avain_tiedosto=d + "/a", kulutila=d)


class TyokalutilaTesti(Pohja):
    """Tyokalutilan koodi ei kayta yksityista taustapaata."""

    def test_tyokalupolku_ei_viittaa_yksityiseen_taustapaahan(self):
        puu = ast.parse((AIVO / "chat.py").read_text(encoding="utf-8"))
        funktiot = {n.name: n for n in ast.walk(puu) if isinstance(n, ast.FunctionDef)}
        for nimi in ("kysy_claudelta", "_kysy_socketista", "kirjaa_paatos", "muodosta_syote"):
            kasitelty = {n.id for n in ast.walk(funktiot[nimi]) if isinstance(n, ast.Name)}
            attr = {n.attr for n in ast.walk(funktiot[nimi]) if isinstance(n, ast.Attribute)}
            self.assertFalse({"yksityinen_or", "openrouter_kulut", "openrouter_zdr"} & (kasitelty | attr), nimi)
        kaares = (AIVO / "tyokalu_kaares.py").read_text(encoding="utf-8")
        for sana in ("yksityinen", "openrouter", "yksityinen_or"):
            self.assertNotIn(sana, kaares.lower())

    def test_vain_yksityinen_moduuli_ja_chat_tuovat_openrouter_koodia(self):
        sallitut = {"yksityinen_or.py", "openrouter_kulut.py", "openrouter_zdr.py", "chat.py"}
        for p in sorted(AIVO.glob("*.py")):
            if p.name.startswith("test_") or p.name in sallitut:
                continue
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    nimet = [a.name for a in n.names] + [getattr(n, "module", "") or ""]
                    self.assertFalse([x for x in nimet if "yksityinen_or" in x or "openrouter" in x], p.name)

    def test_tyokalutilan_viesti_ei_kutsu_openrouteria(self):
        r = self.d
        (r / "kirjasto").mkdir()
        (r / "kirjasto" / "faktat.md").write_text("fakta")
        kysytty = []
        tila = chat.Tila(chat.tiivista_salasana("oikea-salasana-123"), str(r / "kirjasto"), str(r / "data"),
                         ["localhost"], kysy=lambda m, h: kysytty.append(1) or "pilvi", yks_data=str(r / "yks"),
                         taustapaa="openrouter", or_malli=MALLI, or_avain=str(self.avain), or_kulutila=str(self.kulut),
                         tiivistelmat=str(r / "tiiv"), yks_tiivistelmat=str(r / "ytiiv"))
        srv = chat.kaynnista("127.0.0.1", 0, tila)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        portti = srv.server_address[1]

        def api(ev, polku, olio):
            c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
            c.request("POST", polku, body=json.dumps(olio),
                      headers={"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
            x = c.getresponse()
            return x.status, json.loads(x.read())
        c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=oikea-salasana-123",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        x = c.getresponse()
        x.read()
        ev = x.getheader("Set-Cookie").split(";")[0]
        with mock.patch.object(yo, "kysy_openrouterilta", side_effect=AssertionError("ei saa kutsua")):
            s, d = api(ev, "/api/uusi", {"tila": "tyokalu"})
            self.assertEqual(s, 200)
            s, v = api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "moi"})
        self.assertEqual((s, v["vastaus"]), (200, "pilvi"))
        self.assertEqual(kysytty, [1])
        self.assertEqual(self.kulurivit(), [])

    def test_yksityinen_viesti_ilman_avainta_antaa_selkean_502_eika_503(self):
        self.avain.unlink()
        r = self.d
        (r / "kirjasto").mkdir()
        tila = chat.Tila(chat.tiivista_salasana("oikea-salasana-123"), str(r / "kirjasto"), str(r / "data"),
                         ["localhost"], yks_data=str(r / "yks"), yks_muisti=str(r / "yksm"),
                         ydinohjeet=str(r / "ydin"), ehdotukset=str(r / "ehd"),
                         taustapaa="openrouter", or_malli=MALLI, or_avain=str(self.avain), or_kulutila=str(self.kulut),
                         tiivistelmat=str(r / "tiiv"), yks_tiivistelmat=str(r / "ytiiv"))
        srv = chat.kaynnista("127.0.0.1", 0, tila)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        portti = srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=oikea-salasana-123",
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        x = c.getresponse()
        x.read()
        ev = x.getheader("Set-Cookie").split(";")[0]

        def api(polku, olio):
            c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
            c.request("POST", polku, body=json.dumps(olio),
                      headers={"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
            x = c.getresponse()
            return x.status, json.loads(x.read())
        s, d = api("/api/uusi", {})
        self.assertEqual(s, 200)
        s, v = api("/api/viesti", {"keskustelu": d["id"], "teksti": "moi"})
        self.assertEqual(s, 502)
        self.assertIn("avain puuttuu", v["virhe"])


class ValintaTekstiTesti(unittest.TestCase):
    def test_teksti_taustapaan_mukaan(self):
        lb = chat.sivu_html("loopback")
        orr = chat.sivu_html("openrouter")
        self.assertIn(">Yksityinen (oma malli, ei verkkoa)</option>", lb)
        self.assertNotIn("OpenRouter", lb.split("</select>")[0])
        self.assertIn(">Yksityinen (avoin malli OpenRouterissa, ZDR: ei tallennusta)</option>", orr)
        self.assertNotIn("ei verkkoa", orr)
        self.assertNotIn("{YKSITYINEN_TEKSTI}", lb + orr)

    def test_tuntematon_taustapaa_ei_vaita_verkotonta(self):
        self.assertNotIn("ei verkkoa", chat.sivu_html("muu"))

    def test_tila_valitsee_tekstin_ja_sivu_palvelee_sen(self):
        with tempfile.TemporaryDirectory() as d:
            for tp, odotus in (("loopback", "oma malli, ei verkkoa"), ("openrouter", "avoin malli OpenRouterissa")):
                kw = {"or_malli": MALLI} if tp == "openrouter" else {}
                tila = chat.Tila(chat.tiivista_salasana("oikea-salasana-123"), d, d, ["localhost"], yks_data=d + "/y" + tp,
                                 taustapaa=tp, **kw)
                self.assertEqual(tila.taustapaa, tp)
                srv = chat.kaynnista("127.0.0.1", 0, tila)
                self.addCleanup(srv.server_close)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                self.addCleanup(srv.shutdown)
                portti = srv.server_address[1]
                c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
                c.request("POST", "/kirjaudu", body="salasana=oikea-salasana-123",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
                x = c.getresponse()
                x.read()
                ev = x.getheader("Set-Cookie").split(";")[0]
                c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
                c.request("GET", "/", headers={"Cookie": ev})
                sivu = c.getresponse().read().decode("utf-8")
                self.assertIn(odotus, sivu)


class KaynnistysTesti(unittest.TestCase):
    def tila(self, d, **kw):
        return chat.Tila(chat.tiivista_salasana("oikea-salasana-123"), d, d, ["localhost"], yks_data=d + "/y", **kw)

    def test_suljettu_malli_estaa_kaynnistyksen(self):
        with tempfile.TemporaryDirectory() as d:
            for m in (None, "openai/gpt-4o", MALLI + ":free"):
                with self.assertRaises(chat.ChatVirhe):
                    self.tila(d, taustapaa="openrouter", or_malli=m)

    def test_avain_ei_ole_kaynnistyksen_edellytys(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNotNone(self.tila(d, taustapaa="openrouter", or_malli=MALLI).yks)

    def test_loopback_on_edelleen_oletus_ja_ulkoinen_osoite_estetty(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                self.tila(d, malli_url="http://example.com")
            with self.assertRaises(chat.ChatVirhe):
                self.tila(d, taustapaa="muu", malli_url="http://127.0.0.1:8081")


if __name__ == "__main__":
    unittest.main()
