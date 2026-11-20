"""Testit yksityisen tilan mallivalinnalle (AIVO_OPENROUTER_MALLIT). Ei verkkoa, ei oikeita avaimia.

Kutsupolku kulkee oikean Tila-olion ja HTTP-kasittelijan lapi; vain yksityinen_or.kysy_openrouterilta korvataan
tallentimella, joka kirjaa saamansa mallin ja kulutilan."""
import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from aivo import chat, yksityinen_or as yo

M1 = "qwen/qwen3-235b-a22b-2507"
M2 = "meta-llama/llama-3.3-70b-instruct"
AVOIN_EI_LISTALLA = "mistralai/mistral-small-3.1-24b-instruct"
SALASANA = "oikea-salasana-123"


class ListanValidointi(unittest.TestCase):
    def test_avoimet_mallit_kelpaavat_ja_ensimmainen_on_oletus(self):
        self.assertEqual(yo.tarkista_mallit(M1 + ", " + M2), (M1, M2))
        self.assertEqual(yo.tarkista_mallit([M2, M1]), (M2, M1))
        self.assertEqual(yo.tarkista_mallit(M1), (M1,))

    def test_ei_avoin_malli_hylkaa_koko_listan(self):
        for arvo in (M1 + ",openai/gpt-4o", "anthropic/claude-3.5-sonnet", M1 + "," + M2 + ":free",
                     M1 + ",", ",", "", None, [], [M1, 5], M1 + "," + M1, ",".join([M1] * 11)):
            with self.subTest(arvo=arvo), self.assertRaises(yo.YksityinenVirhe):
                yo.tarkista_mallit(arvo)

    def test_tila_ei_kaynnisty_ei_avoimella_mallilla(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.Tila(chat.tiivista_salasana(SALASANA), d, d, ["localhost"], yks_data=d + "/y",
                          taustapaa="openrouter", or_mallit=M1 + ",openai/gpt-4o")
            t = chat.Tila(chat.tiivista_salasana(SALASANA), d, d, ["localhost"], yks_data=d + "/y",
                          taustapaa="openrouter", or_mallit=M1 + "," + M2, or_malli="openai/gpt-4o")
            self.assertEqual(t.or_mallit, (M1, M2))       # lista voittaa vanhan yhden mallin asetuksen


class YmparistoMuuttujat(unittest.TestCase):
    """main() lukee AIVO_OPENROUTER_MALLIT ja vanhan AIVO_OPENROUTER_MALLI; Tila saa ne sellaisenaan."""

    def aja_main(self, ymparisto):
        saatu = {}

        def tila(*a, **kw):
            saatu.update(kw)
            raise chat.ChatVirhe("pysaytetty testissa")
        pois = {k: v for k, v in os.environ.items() if not k.startswith("AIVO_")}
        with mock.patch.dict(os.environ, {**pois, **ymparisto}, clear=True), \
                mock.patch.object(chat, "lue_tiiviste", return_value="x"), mock.patch.object(chat, "Tila", tila), \
                mock.patch("sys.stderr"):
            self.assertEqual(chat.main(["palvele", "--yksityinen-taustapaa", "openrouter"]), 1)
        return saatu

    def rakenna(self, kw):
        with tempfile.TemporaryDirectory() as d:
            return chat.Tila(chat.tiivista_salasana(SALASANA), d, d, ["localhost"], yks_data=d + "/y",
                             taustapaa="openrouter", or_malli=kw["or_malli"], or_mallit=kw["or_mallit"]).or_mallit

    def test_vanha_yksi_muuttuja_toimii(self):
        kw = self.aja_main({"AIVO_OPENROUTER_MALLI": M2})
        self.assertEqual((kw["or_malli"], kw["or_mallit"]), (M2, None))
        self.assertEqual(self.rakenna(kw), (M2,))

    def test_lista_muuttujasta(self):
        kw = self.aja_main({"AIVO_OPENROUTER_MALLIT": M1 + "," + M2, "AIVO_OPENROUTER_MALLI": M2})
        self.assertEqual(self.rakenna(kw), (M1, M2))


class Palvelin(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.r = Path(self._t.name)
        (self.r / "kirjasto").mkdir()
        self.kutsut = []
        self.claude = []

    def kaynnista(self, **kw):
        kw.setdefault("taustapaa", "openrouter")
        r = self.r
        self.tila = chat.Tila(chat.tiivista_salasana(SALASANA), str(r / "kirjasto"), str(r / "data"), ["localhost"],
                              kysy=lambda m, h: self.claude.append(1) or "pilvi",
                              yks_data=str(r / "yks"), yks_muisti=str(r / "yksm"), ydinohjeet=str(r / "ydin"),
                              ehdotukset=str(r / "ehd"), or_avain=str(r / "avain"), or_kulutila=str(r / "kulut"), **kw)
        srv = chat.kaynnista("127.0.0.1", 0, self.tila)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.portti = srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", self.portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=" + SALASANA,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        x = c.getresponse()
        x.read()
        self.ev = x.getheader("Set-Cookie").split(";")[0]
        p = mock.patch.object(yo, "kysy_openrouterilta", side_effect=self.tallennin)
        p.start()
        self.addCleanup(p.stop)

    def tallennin(self, muisti, historia, ohjeet, **kw):
        self.kutsut.append((kw["malli"], kw["kulutila"]))
        return "vastaus " + kw["malli"]

    def api(self, polku, olio=None, metodi="POST"):
        c = http.client.HTTPConnection("127.0.0.1", self.portti, timeout=10)
        c.request(metodi, polku, body=None if olio is None else json.dumps(olio),
                  headers={"Cookie": self.ev, "X-Aivo": "1", "Content-Type": "application/json"})
        x = c.getresponse()
        return x.status, json.loads(x.read())

    def rivit(self, kid):
        return self.tila.yks.lue(kid)

    def test_valinta_tallentuu_ja_vaihtuu_kesken_keskustelun(self):
        self.kaynnista(or_mallit=[M1, M2])
        self.assertEqual(self.api("/api/mallit", metodi="GET"), (200, {"mallit": [M1, M2], "yhdistelmat": ["yksi", "tarkastus", "rinnakkais", "reititys"], "yhdistelma": "yksi"}))
        s, d = self.api("/api/uusi", {})
        self.assertEqual((s, d["malli"]), (200, M1))                        # oletus = ensimmainen
        kid = d["id"]
        self.assertEqual(self.tila.yks.lue_malli(kid), M1)
        s, v = self.api("/api/viesti", {"keskustelu": kid, "teksti": "eka"})
        self.assertEqual((s, v["malli"], v["vastaus"]), (200, M1, "vastaus " + M1))
        s, v = self.api("/api/viesti", {"keskustelu": kid, "teksti": "toka", "malli": M2})
        self.assertEqual((s, v["malli"]), (200, M2))
        self.assertEqual(self.tila.yks.lue_malli(kid), M2)                  # metatieto vaihtui
        s, v = self.api("/api/viesti", {"keskustelu": kid, "teksti": "kolmas"})   # ilman kenttaa: tallennettu
        self.assertEqual((s, v["malli"]), (200, M2))
        self.assertEqual([m for m, _ in self.kutsut], [M1, M2, M2])
        self.assertEqual(len({k for _, k in self.kutsut}), 1)               # yksi yhteinen kulutila
        aivon = [x.get("malli") for x in self.rivit(kid) if x["rooli"] == "aivo"]
        self.assertEqual(aivon, [M1, M2, M2])                                 # jokainen vastaus kirjaa mallin
        self.assertNotIn("malli", [k for x in self.rivit(kid) if x["rooli"] == "tony" for k in x])
        self.assertTrue((self.r / "yks" / (kid + ".meta.json")).exists())

    def test_uusi_keskustelu_valitulla_mallilla(self):
        self.kaynnista(or_mallit=M1 + "," + M2)
        s, d = self.api("/api/uusi", {"tila": "yksityinen", "malli": M2})
        self.assertEqual((s, d["malli"]), (200, M2))
        s, v = self.api("/api/viesti", {"keskustelu": d["id"], "teksti": "moi"})
        self.assertEqual((s, v["malli"]), (200, M2))

    def test_listan_ulkopuolinen_malli_saa_400(self):
        self.kaynnista(or_mallit=[M1, M2])
        for huono in ("openai/gpt-4o", AVOIN_EI_LISTALLA, "", None, 7, [M1]):
            with self.subTest(malli=huono):
                s, _ = self.api("/api/uusi", {"tila": "yksityinen", "malli": huono})
                self.assertEqual(s, 400)
        s, d = self.api("/api/uusi", {})
        kid = d["id"]
        for huono in ("openai/gpt-4o", AVOIN_EI_LISTALLA, M1 + ":free", None):
            with self.subTest(malli=huono):
                s, v = self.api("/api/viesti", {"keskustelu": kid, "teksti": "moi", "malli": huono})
                self.assertEqual(s, 400)
                self.assertIn("listalla", v["virhe"])
        self.assertEqual(self.kutsut, [])
        self.assertEqual(self.rivit(kid), [])                 # hylatty pyynto ei kirjoita viestia
        self.assertEqual(self.tila.yks.lue_malli(kid), M1)    # eika muuta valintaa

    def test_vanha_yksi_malli_toimii(self):
        self.kaynnista(or_malli=M2)
        self.assertEqual(self.api("/api/mallit", metodi="GET"), (200, {"mallit": [M2], "yhdistelmat": ["yksi", "tarkastus", "rinnakkais", "reititys"], "yhdistelma": "yksi"}))
        s, d = self.api("/api/uusi", {})
        self.assertEqual(d["malli"], M2)
        s, v = self.api("/api/viesti", {"keskustelu": d["id"], "teksti": "moi"})
        self.assertEqual((s, v["malli"]), (200, M2))
        self.assertEqual(self.kutsut[0][0], M2)

    def test_tyokalutila_ei_nae_mallia(self):
        self.kaynnista(or_mallit=[M1, M2])
        s, d = self.api("/api/uusi", {"tila": "tyokalu"})
        self.assertEqual(s, 200)
        self.assertNotIn("malli", d)
        s, _ = self.api("/api/uusi", {"tila": "tyokalu", "malli": M1})
        self.assertEqual(s, 400)
        s, v = self.api("/api/viesti", {"keskustelu": d["id"], "teksti": "moi", "malli": M1})
        self.assertEqual(s, 400)
        s, v = self.api("/api/viesti", {"keskustelu": d["id"], "teksti": "moi"})
        self.assertEqual((s, v["vastaus"]), (200, "pilvi"))
        self.assertNotIn("malli", v)
        self.assertEqual((self.claude, self.kutsut), ([1], []))
        self.assertFalse(list((self.r / "data").glob("*.meta.json")))

    def test_loopback_ei_tarjoa_malleja(self):
        self.kaynnista(taustapaa="loopback", kysy_yks=lambda m, h, o: "paikallinen")
        self.assertEqual(self.api("/api/mallit", metodi="GET"), (200, {"mallit": []}))
        s, d = self.api("/api/uusi", {})
        self.assertNotIn("malli", d)
        s, v = self.api("/api/viesti", {"keskustelu": d["id"], "teksti": "moi"})
        self.assertEqual((s, v["vastaus"]), (200, "paikallinen"))
        self.assertNotIn("malli", v)
        s, _ = self.api("/api/viesti", {"keskustelu": d["id"], "teksti": "moi", "malli": M1})
        self.assertEqual(s, 400)


class Kayttoliittyma(unittest.TestCase):
    def test_sivulla_on_piilotettu_valitsin_ja_mallin_nimi(self):
        sivu = chat.sivu_html("openrouter")
        self.assertIn('<select id="malli" aria-label="Malli" hidden></select>', sivu)
        self.assertIn("'Malli: '+j.malli", sivu)
        self.assertIn("if(r.ok&&typeof j.malli==='string')", sivu)
        self.assertIn("mn.textContent=", sivu)              # mallin nimi tekstina, ei HTML:na

    @unittest.skipUnless(shutil.which("node"), "node puuttuu")
    def test_valitsin_nakyy_vain_yksityisessa_tilassa_kun_malleja_on_vahintaan_kaksi(self):
        f = re.search(r"function valitsinNakyy\(t,l\)\{[^}]*\}", chat.HTML).group(0)
        f2 = re.search(r"function mallikentta\(b\)\{[^}]*\}", chat.HTML).group(0)
        js = f + "\nconst M={value:'B'};let nyt='tyokalu';let mallit=['A','B'];\n" + f2 + """
const r=[valitsinNakyy('yksityinen',['A','B']),valitsinNakyy('yksityinen',['A']),valitsinNakyy('tyokalu',['A','B']),
 valitsinNakyy('yksityinen',[]),JSON.stringify(mallikentta({keskustelu:'k'})),JSON.stringify(mallikentta({tila:'yksityinen'}))];
console.log(JSON.stringify(r));"""
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(json.loads(out), [True, False, False, False, '{"keskustelu":"k"}',
                                           '{"tila":"yksityinen","malli":"B"}'])


if __name__ == "__main__":
    unittest.main()
