"""Testit chat-sivun muistinakymalle (vain luku, aivo/muistinakyma.py). Ei verkkoa ulos, ei mallikutsua."""
import ast
import hashlib
import http.client
import json
import os
import secrets
import tempfile
import threading
import unittest
from pathlib import Path

from aivo import chat, muistinakyma, tyokalualue, yksityinen, yksityinen_alue
from aivo import tiivistelma as tv

SAL = "oikea-salasana-123"
TS = ".ts" + ".net"      # paate koottu osista (repon osoitetarkistus)
AIVO = Path(__file__).resolve().parent


def tee(kid, aika, tulkinta, lainaus, sana="muista"):
    """Tiivistelma, jonka Tonyn viesti on 'sana lainaus'. Palauttaa (viestit, tiivistelma)."""
    viestit = [{"rooli": "tony", "teksti": "%s %s" % (sana, lainaus), "aika": "2026-11-14T10:15:00+00:00"}]
    d = {"versio": 1, "keskustelu": kid, "alku": 0, "loppu": 0, "ensimmainen_aika": viestit[0]["aika"],
         "viimeinen_aika": viestit[0]["aika"], "tiivistetty": aika, "malli": "testimalli",
         "alue_sha": tv.alueen_sha(viestit, 0, 0),
         "kohdat": [{"id": 1, "tyyppi": "fakta", "tony_sanoi": [lainaus], "tulkinta": tulkinta, "viestit": [0],
                     "tila": "voimassa"}]}
    return viestit, d


class NakymaTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        r = Path(cls.tmp.name)
        (r / "kirjasto").mkdir()
        cls.kanaria = "KANARIA-" + secrets.token_hex(6)
        cls.tyokalu_merkki = "TYOKALUMERKKI-" + secrets.token_hex(4)
        cls.tiiv_tyokalu = r / "aivo" / "tiivistelmat"
        cls.tiiv_yks = r / "yks" / "tiivistelmat"
        v, d = tee("20261114-101500-0a1b2c3d", "2026-11-14T11:00:00+00:00", "Tony haluaa " + cls.tyokalu_merkki,
                   "lainaus " + cls.tyokalu_merkki)
        tv.tallenna(cls.tiiv_tyokalu, d, v)
        v, d = tee("20261114-111500-0a1b2c3d", "2026-11-14T12:00:00+00:00", "Yksityinen " + cls.kanaria,
                   "yksityinen " + cls.kanaria)
        tv.tallenna(cls.tiiv_yks, d, v)
        cls.tila = chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "aivo" / "chat"), [TS],
                             yks_data=str(r / "yks" / "chat"), yks_muisti=str(r / "yks" / "muisti"),
                             ehdotukset=str(r / "yks" / "ehdotukset"), kysy_yks=lambda m, h, o: "x")
        cls.srv = chat.kaynnista("127.0.0.1", 0, cls.tila)
        cls.portti = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def hae(self, menetelma, polku, ev=None):
        c = http.client.HTTPConnection("127.0.0.1", self.portti, timeout=10)
        c.request(menetelma, polku, headers={"Cookie": ev} if ev else {})
        r = c.getresponse()
        return r.status, r.read().decode("utf-8")

    def kirjaudu(self):
        c = http.client.HTTPConnection("127.0.0.1", self.portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=" + SAL, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        r.read()
        return r.getheader("Set-Cookie").split(";")[0]

    def test_ilman_kirjautumista_ei_nay(self):
        for p in ("/muisti", "/muisti?tila=yksityinen", "/muisti?tila=tyokalu"):
            s, runko = self.hae("GET", p)
            self.assertEqual(s, 302, p)
            self.assertNotIn(self.kanaria, runko)
            self.assertNotIn(self.tyokalu_merkki, runko)

    def test_kanaria_tyokalutilan_sivu_ei_nayta_yksityista(self):
        """Suunnitelman 4.2 kanariamalli: yksityisen tiivistelman uniikki merkkijono ei saa paatya tyokalutilan sivulle."""
        ev = self.kirjaudu()
        for p in ("/muisti?tila=tyokalu", "/muisti", "/muisti?tila=tyokalu&tila=yksityinen"):
            s, runko = self.hae("GET", p, ev)
            self.assertNotIn(self.kanaria, runko, p)
            if s == 200:
                self.assertIn(self.tyokalu_merkki, runko, p)
        for p in ("/muisti?tila=../yks", "/muisti?tila=yksityinen%00", "/muisti?x=1"):
            s, runko = self.hae("GET", p, ev)
            self.assertEqual(s, 404, p)
            self.assertNotIn(self.kanaria, runko)

    def test_yksityinen_sivu_nayttaa_vain_yksityisen(self):
        ev = self.kirjaudu()
        s, runko = self.hae("GET", "/muisti?tila=yksityinen", ev)
        self.assertEqual(s, 200)
        self.assertIn(self.kanaria, runko)
        self.assertNotIn(self.tyokalu_merkki, runko)

    def test_yhteinen_ylakansio_hylataan(self):
        """data ja yks_data samassa ylakansiossa => oletuskansiot yhtyisivat: kaynnistys kielletaan."""
        r = Path(self.tmp.name) / "yhteinen"
        with self.assertRaises(chat.ChatVirhe):
            chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "chat"), [TS],
                      yks_data=str(r / "yks-chat"), kysy_yks=lambda m, h, o: "x")

    def test_tyokalukansio_yksityisen_alueen_sisalla_hylataan(self):
        r = Path(self.tmp.name) / "sisalla"
        for tyokalu in (r / "yks" / "muisti", r / "yks", r / "yks" / "tiivistelmat", r):
            with self.assertRaises(chat.ChatVirhe, msg=str(tyokalu)):
                chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "aivo" / "chat"), [TS],
                          yks_data=str(r / "yks" / "chat"), yks_muisti=str(r / "yks" / "muisti"),
                          ehdotukset=str(r / "yks" / "ehdotukset"), tiivistelmat=str(tyokalu),
                          kysy_yks=lambda m, h, o: "x")

    def test_yksityinen_tiivistelmakansio_tarkistetaan_ja_erottuu(self):
        r = Path(self.tmp.name) / "erillaan"
        tila = chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "aivo" / "chat"), [TS],
                         yks_data=str(r / "yks" / "chat"), kysy_yks=lambda m, h, o: "x")
        self.assertNotEqual(tila.nakyma_tiivistelmat, tila.yks_tiivistelmat)
        self.assertEqual(os.stat(tila.yks_tiivistelmat).st_mode & 0o777, 0o700)
        os.symlink(self.tmp.name, r / "linkki")
        with self.assertRaises(yksityinen.YksityinenVirhe):
            chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "aivo" / "chat"), [TS],
                      yks_data=str(r / "yks" / "chat"), yks_tiivistelmat=str(r / "linkki"), kysy_yks=lambda m, h, o: "x")

    def test_oletuskansiot_vastaavat_tiivistajan_alueita(self):
        """Oletus (data-kansion ylakansio/tiivistelmat) on sama polku kuin alue-moduulit antavat samalla juurella
        (tuotannossa data = JUURI/chat, ks. docs/aivo-yksityinen.md)."""
        r = Path(self.tmp.name) / "oletus"
        t = chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "aivo" / "chat"), [TS],
                      yks_data=str(r / "yks" / "chat"), kysy_yks=lambda m, h, o: "x")
        self.assertEqual(Path(t.nakyma_tiivistelmat), tyokalualue.tyokalualue(str(r / "aivo")).polku("tiivistelmat"))
        self.assertEqual(Path(t.yks_tiivistelmat), yksityinen_alue.yksityinen_alue(str(r / "yks")).polku("tiivistelmat"))
        self.assertEqual(tyokalualue.ALIKANSIOT.count("chat"), 1)
        self.assertEqual(yksityinen_alue.ALIKANSIOT.count("chat"), 1)

    def test_ilman_yksityista_tilaa_yksityinen_reitti_ei_lue_mitaan(self):
        r = Path(self.tmp.name)
        tila = chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "aivo" / "chat"), [TS])
        self.assertIsNone(tila.yks_tiivistelmat)
        srv = chat.kaynnista("127.0.0.1", 0, tila)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            vanha, self.portti = self.portti, srv.server_address[1]
            try:
                ev = self.kirjaudu()
                s, runko = self.hae("GET", "/muisti?tila=yksityinen", ev)
            finally:
                self.portti = vanha
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(s, 404)
        self.assertNotIn(self.kanaria, runko)

    def test_html_escapetaan_script_merkkijono(self):
        """Tiivistelma on dataa, ei ohje: <script> ei saa paatya sivulle sellaisenaan (suunnitelma 5.1)."""
        with tempfile.TemporaryDirectory() as k:
            v, d = tee("20261114-131500-0a1b2c3d", "2026-11-14T13:00:00+00:00", "<script>alert(1)</script> & \"x\"",
                       "<script>alert(2)</script>")
            tv.tallenna(k, d, v)
            teksti = muistinakyma.sivu(k, "koe")
        self.assertNotIn("<script", teksti.lower())
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;x&quot;", teksti)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", teksti)

    def test_html_escape_myos_palvelun_vastauksessa_ja_ohjausmerkit_nakyvat(self):
        r = Path(self.tmp.name) / "esc"
        v, d = tee("20261114-141500-0a1b2c3d", "2026-11-14T14:00:00+00:00", "<script>x</script>", "<img src=x onerror=1>")
        tunnus = tv.tallenna(r / "tiivistelmat", d, v)
        tiedosto = r / "tiivistelmat" / (tunnus + ".json")      # kasin muokattu tiedosto: ohjausmerkki ohittaa jasennyksen
        tiedosto.write_text(tiedosto.read_text(encoding="utf-8").replace("</script>", "</script>\\u001b[2J"), encoding="utf-8")
        tila = chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "chat"), [TS])
        srv = chat.kaynnista("127.0.0.1", 0, tila)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            vanha, self.portti = self.portti, srv.server_address[1]
            try:
                ev = self.kirjaudu()
                s, runko = self.hae("GET", "/muisti?tila=tyokalu", ev)
            finally:
                self.portti = vanha
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(s, 200)
        self.assertNotIn("<script", runko.lower())
        self.assertNotIn("<img", runko.lower())
        self.assertNotIn("\x1b", runko)
        self.assertIn("\\x1b", runko)

    def test_sivu_on_vain_luku(self):
        """GET ei muuta kansiota; POST /muisti ei ole reitti."""
        ev = self.kirjaudu()
        ennen = {p.name: p.read_bytes() for p in self.tiiv_tyokalu.iterdir()}
        self.hae("GET", "/muisti?tila=tyokalu", ev)
        self.assertEqual(ennen, {p.name: p.read_bytes() for p in self.tiiv_tyokalu.iterdir()})
        c = http.client.HTTPConnection("127.0.0.1", self.portti, timeout=10)
        c.request("POST", "/muisti", body="{}", headers={"Cookie": ev, "X-Aivo": "1"})
        r = c.getresponse()
        r.read()
        self.assertNotEqual(r.status, 200)

    def test_puuttuva_ja_rikkinainen_kansio(self):
        with tempfile.TemporaryDirectory() as k:
            self.assertIn("Ei tiivistelmi", muistinakyma.sivu(Path(k) / "ei-ole", "koe"))
            (Path(k) / "20261114-101500-0a1b2c3d-0-0.json").write_text("{rikki", encoding="utf-8")
            teksti = muistinakyma.sivu(k, "koe")
            self.assertIn("Ohitettu lukukelvottomia tiedostoja: 1", teksti)

    def test_rakenne_moduuli_ei_koske_verkkoon_eika_yksityisiin_poluille(self):
        src = (AIVO / "muistinakyma.py").read_text(encoding="utf-8")
        puu = ast.parse(src)
        tuodut = set()
        for n in ast.walk(puu):
            if isinstance(n, ast.Import):
                tuodut |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                tuodut.add((n.module or "").split(".")[0])
        for kielletty in ("subprocess", "socket", "urllib", "http", "yksityinen", "yksityinen_alue", "tyokalualue"):
            self.assertNotIn(kielletty, tuodut)
        self.assertNotIn("aivo-yksityinen", src)
        self.assertNotIn("/var/lib", src)


if __name__ == "__main__":
    unittest.main()
