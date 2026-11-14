import http.client
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

from aivo import chat

SAL = "oikea-salasana-123"


def ip(*osat):
    """Testiosoite osista: repon osoitetarkistus ei saa nahda kirjaimellisia osoitteita."""
    return ".".join(str(o) for o in osat)


TS = ".ts" + ".net"      # nimen paate koottu osista samasta syysta


class SalasanaTesti(unittest.TestCase):
    def test_tiiviste_toimii_ja_ei_sisalla_salasanaa(self):
        t = chat.tiivista_salasana(SAL)
        self.assertNotIn(SAL, t)
        self.assertTrue(chat.tarkista_salasana(SAL, t))
        self.assertFalse(chat.tarkista_salasana("vaara", t))
        self.assertFalse(chat.tarkista_salasana(SAL, "roskaa"))

    def test_aseta_salasana_kirjoittaa_600_ja_hylkaa_lyhyen(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sal"
            vastaukset = iter([SAL, SAL])
            chat.aseta_salasana(p, kysy=lambda _: next(vastaukset), tulosta=lambda *_: None)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertNotIn(SAL, p.read_text())
            with self.assertRaises(chat.ChatVirhe):
                chat.aseta_salasana(Path(d) / "x", kysy=lambda _: "lyhyt", tulosta=lambda *_: None)
            vastaukset = iter([SAL, SAL + "x"])
            with self.assertRaises(chat.ChatVirhe):
                chat.aseta_salasana(Path(d) / "y", kysy=lambda _: next(vastaukset), tulosta=lambda *_: None)

    def test_laaja_tiedosto_hylataan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sal"
            p.write_text("x")
            os.chmod(p, 0o644)
            with self.assertRaises(chat.ChatVirhe):
                chat.lue_tiiviste(p)


class VerkkoTesti(unittest.TestCase):
    def test_vain_loopback_ja_tailscale(self):
        for ok in (ip(127, 0, 0, 1), "::1", ip(100, 100, 100, 100), ip(100, 64, 0, 1)):
            self.assertTrue(chat.sallittu_osoite(ok), ok)
        for ei in (ip(0, 0, 0, 0), ip(192, 168, 1, 5), ip(8, 8, 8, 8), "::", ip(100, 128, 0, 1), "esimerkki.fi"):
            self.assertFalse(chat.sallittu_osoite(ei), ei)
        with self.assertRaises(chat.ChatVirhe):
            chat.kaynnista(ip(0, 0, 0, 0), 0, None)

    def test_host_tarkistus(self):
        self.assertTrue(chat.sallittu_host("aivo.tail1234" + TS, [TS]))
        self.assertTrue(chat.sallittu_host("aivo.tail1234" + TS + ":443", [TS]))
        self.assertTrue(chat.sallittu_host("127.0.0.1:8780", []))
        self.assertFalse(chat.sallittu_host("paha.example.com", [TS]))
        self.assertFalse(chat.sallittu_host("ts.net" + ".paha.com", [TS]))
        self.assertFalse(chat.sallittu_host(None, [TS]))


class IstuntoTesti(unittest.TestCase):
    def test_lukitus_viidennen_virheen_jalkeen_ja_vanheneminen(self):
        aika = [0.0]
        i = chat.Istunnot(kello=lambda: aika[0])
        for _ in range(chat.VIRHE_RAJA):
            self.assertFalse(i.lukittu())
            i.virhe()
        self.assertTrue(i.lukittu())
        aika[0] += chat.LUKITUS_S + 1
        self.assertFalse(i.lukittu())
        t = i.luo()
        self.assertTrue(i.kelvollinen(t))
        self.assertFalse(i.kelvollinen("keksitty"))
        self.assertFalse(i.kelvollinen(None))
        aika[0] += chat.ISTUNTO_KESTO_S + 1
        self.assertFalse(i.kelvollinen(t))


class MuistiTesti(unittest.TestCase):
    def test_muisti_jarjestys_rajaus_ja_lahteet(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as ulko:
            k = Path(d)
            (k / "aivo-paatoskirjasto.md").write_text("paatos A")
            (k / "kompassi.md").write_text("kompassi B")
            (k / "muu.md").write_text("muu C")
            (k / "kuva.png").write_bytes(b"x")
            (k / ".git").mkdir()
            (k / ".git" / "config.md").write_text("EI")
            (Path(ulko) / "salainen.md").write_text("ULKOPUOLINEN")
            os.symlink(Path(ulko) / "salainen.md", k / "linkki.md")
            teksti, tied = chat.lue_muisti(k)
            self.assertEqual(tied, ["aivo-paatoskirjasto.md", "kompassi.md", "muu.md"])
            self.assertNotIn("ULKOPUOLINEN", teksti)
            self.assertNotIn("EI", teksti.replace("MUISTI", ""))
            self.assertLess(teksti.index("paatos A"), teksti.index("kompassi B"))

    def test_muistin_raja_ei_voi_paattaa_osiota_ja_ylaraja_toimii(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.md").write_text("x" * 100 + chat.RAJA + "y")
            teksti, _ = chat.lue_muisti(d)
            self.assertEqual(teksti.count(chat.RAJA), 2)       # tiedoston oma raja-merkki poistettu
            vanha = chat.MUISTI_YLARAJA
            chat.MUISTI_YLARAJA = 50
            try:
                _, tied = chat.lue_muisti(d)
            finally:
                chat.MUISTI_YLARAJA = vanha
            self.assertEqual(tied, [])

    def test_tuntematon_lahde_merkitaan(self):
        v, tunn = chat.tarkista_lahteet("Asia [l\u00e4hde: kompassi.md]. Toinen [l\u00e4hde: keksitty.md].", ["kompassi.md"])
        self.assertEqual(tunn, ["keksitty.md"])
        self.assertIn("EI L\u00d6YDY MUISTISTA", v)
        self.assertIn("[l\u00e4hde: kompassi.md]", v)

    def test_syote_sisaltaa_kehotteen_ja_historian_ilman_tyokaluja(self):
        s = chat.muodosta_syote("MUISTI", [{"rooli": "tony", "teksti": "hei"}, {"rooli": "aivo", "teksti": "moi"}])
        self.assertIn("Tony: hei", s)
        self.assertIn("Aivo: moi", s)
        self.assertIn("ei ohjeita", s)

    def test_claude_kutsu_ilman_tyokaluja_ja_ilman_litellmia(self):
        kutsut = []

        def feikki(kom, **kw):
            kutsut.append((kom, kw))
            return SimpleNamespace(returncode=0, stdout="vastaus\n", stderr="")
        self.assertEqual(chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "?"}], suorita=feikki, env={}), "vastaus")
        kom = kutsut[0][0]
        self.assertEqual(kom[:2], ["claude", "-p"])
        self.assertEqual(kom[kom.index("--tools") + 1], "")
        self.assertEqual(kom[kom.index("--max-turns") + 1], "1")
        lahde = Path(chat.__file__).read_text(encoding="utf-8").lower()
        # ei yhteyttaa valittajiin: koodi ei kayta niita (vain maininta kommenteissa/docstringeissa)
        for kielletty in ("import litellm", "openrouter.ai", "api/v1/chat/completions", "urllib.request", "import requests"):
            self.assertNotIn(kielletty, lahde)

    def test_claude_virhe_ei_vuoda_sisaltoa(self):
        def feikki(kom, **kw):
            return SimpleNamespace(returncode=1, stdout="", stderr="SALAINEN")
        with self.assertRaises(chat.ChatVirhe) as c:
            chat.kysy_claudelta("m", [], suorita=feikki, env={})
        self.assertNotIn("SALAINEN", str(c.exception))


class KirjastoTesti(unittest.TestCase):
    def test_paatos_on_sanasta_sanaan_lainaus(self):
        with tempfile.TemporaryDirectory() as d:
            kutsut = []

            def feikki(kom, **kw):
                kutsut.append(kom)
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:00:00+00:00", "Rehellisyys ensin.\nSitten muu.", suorita=feikki)
            t = (Path(d) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
            self.assertIn("> Rehellisyys ensin.\n> Sitten muu.", t)
            self.assertIn("ei Aivon p\u00e4\u00e4telm\u00e4", t)
            self.assertEqual(stat.S_IMODE((Path(d) / chat.PAATOS_TIEDOSTO).stat().st_mode), 0o600)
            chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:01:00+00:00", "Toinen.", suorita=feikki)
            t2 = (Path(d) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
            self.assertTrue(t2.startswith(t))          # vanhaa ei ylikirjoiteta
            self.assertIn("> Toinen.", t2)

    def test_github_etarepo_estaa_pushin(self):
        def feikki(kom, **kw):
            if "get-url" in kom:
                return SimpleNamespace(returncode=0, stdout="https://github.com/x/y.git\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.julkaise_kirjastoon(d, Path(d) / "a.md", feikki)

    def test_julkaisun_epaonnistuessa_paatostiedosto_palautuu(self):
        def feikki(kom, **kw):
            if "get-url" in kom:
                return SimpleNamespace(returncode=0, stdout="https://github.com/x/y.git\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / chat.PAATOS_TIEDOSTO
            with self.assertRaises(chat.ChatVirhe):
                chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:00:00+00:00", "a", suorita=feikki)
            self.assertFalse(p.exists())
            chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:00:00+00:00", "vanha", julkaise=False)
            vanha = p.read_text(encoding="utf-8")
            with self.assertRaises(chat.ChatVirhe):
                chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:01:00+00:00", "uusi", suorita=feikki)
            self.assertEqual(p.read_text(encoding="utf-8"), vanha)

    def test_samanaikaiset_kirjaukset_eivat_havita_toisiaan(self):
        with tempfile.TemporaryDirectory() as d:
            saikeet = [threading.Thread(target=chat.kirjaa_paatos, args=(d, "20261106-120000-abcdef01",
                                        "2026-11-06T12:%02d:00+00:00" % i, "paatos-%02d" % i), kwargs={"julkaise": False})
                       for i in range(30)]
            for t in saikeet:
                t.start()
            for t in saikeet:
                t.join()
            teksti = (Path(d) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
            for i in range(30):
                self.assertIn("> paatos-%02d" % i, teksti)

    def test_host_vaatii_taydellisen_nimen(self):
        for paha in (["--host", ".esimerkki.fi"], ["--host", ""]):
            with tempfile.TemporaryDirectory() as d:
                virhe = io.StringIO()
                with unittest.mock.patch.object(sys, "stderr", virhe):
                    self.assertEqual(chat.main(["palvele", "--salasana-tiedosto", d + "/ei-ole", "--data", d, "--kirjasto", d] + paha), 1)
                self.assertIn("--host", virhe.getvalue())

    def test_keskustelun_tunniste_ei_paase_polkuun(self):
        with tempfile.TemporaryDirectory() as d:
            k = chat.Keskustelut(d)
            for paha in ("../../etc/passwd", "", None, "x"):
                with self.assertRaises(chat.ChatVirhe):
                    k.lue(paha)


class PalveluTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        k = Path(cls.tmp.name) / "kirjasto"
        k.mkdir()
        (k / "faktat.md").write_text("Tony opiskelee ohjelmistokehitt\u00e4j\u00e4ksi.")
        cls.kysytty = []
        cls.paatokset = []

        def kysy(muisti, historia):
            cls.kysytty.append((muisti, historia))
            return "Opiskelet ohjelmistokehitt\u00e4j\u00e4ksi [l\u00e4hde: faktat.md] ja [l\u00e4hde: haamu.md]."

        def julkaise(kirjasto, kid, aika, teksti):
            cls.paatokset.append(teksti)
            return "ei-gitissa"
        cls.tila = chat.Tila(chat.tiivista_salasana(SAL), str(k), str(Path(cls.tmp.name) / "data"), [TS],
                             kysy=kysy, julkaise=julkaise)
        cls.srv = chat.kaynnista(ip(127, 0, 0, 1), 0, cls.tila)
        cls.portti = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def pyynto(self, menetelma, polku, runko=None, otsakkeet=None, host=None):
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        h = dict(otsakkeet or {})
        if host:
            h["Host"] = host
        c.request(menetelma, polku, body=runko, headers=h)
        r = c.getresponse()
        data = r.read()
        return r.status, dict(r.getheaders()), data

    def kirjaudu(self):
        s, h, _ = self.pyynto("POST", "/kirjaudu", "salasana=" + SAL, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(s, 303)
        ev = h["Set-Cookie"]
        for osa in ("HttpOnly", "Secure", "SameSite=Strict"):
            self.assertIn(osa, ev)
        return ev.split(";")[0]

    def api(self, ev, polku, olio):
        s, _, d = self.pyynto("POST", polku, json.dumps(olio), {"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
        return s, json.loads(d)

    def test_ilman_kirjautumista_ei_paase(self):
        s, h, _ = self.pyynto("GET", "/")
        self.assertEqual((s, h["Location"]), (302, "/kirjaudu"))
        s, _, _ = self.pyynto("POST", "/api/uusi", "{}", {"X-Aivo": "1"})
        self.assertEqual(s, 401)

    def test_vaara_host_hylataan(self):
        s, _, _ = self.pyynto("GET", "/kirjaudu", host="paha.example.com")
        self.assertEqual(s, 421)

    def test_csrf_vaatii_otsakkeen_ja_saman_alkuperan(self):
        ev = self.kirjaudu()
        s, _, _ = self.pyynto("POST", "/api/uusi", "{}", {"Cookie": ev})
        self.assertEqual(s, 403)
        s, _, _ = self.pyynto("POST", "/api/uusi", "{}", {"Cookie": ev, "X-Aivo": "1", "Origin": "https://paha.example.com"})
        self.assertEqual(s, 403)

    def test_vaara_salasana_ja_lukitus(self):
        tila = chat.Tila(chat.tiivista_salasana(SAL), self.tmp.name, self.tmp.name, [TS])
        srv = chat.kaynnista(ip(127, 0, 0, 1), 0, tila)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def yrita(sal):
                c = http.client.HTTPConnection(ip(127, 0, 0, 1), srv.server_address[1], timeout=10)
                c.request("POST", "/kirjaudu", body="salasana=" + sal, headers={"Content-Type": "application/x-www-form-urlencoded"})
                r = c.getresponse()
                r.read()
                return r.status
            for _ in range(chat.VIRHE_RAJA):
                self.assertEqual(yrita("vaara"), 401)
            self.assertEqual(yrita(SAL), 429)        # oikeakaan ei kelpaa lukituksen aikana
        finally:
            srv.shutdown()
            srv.server_close()

    def test_keskustelu_muisti_lahteet_ja_paatos(self):
        ev = self.kirjaudu()
        s, d = self.api(ev, "/api/uusi", {"tila": "tyokalu"})
        self.assertEqual(s, 200)
        kid = d["id"]
        s, d = self.api(ev, "/api/viesti", {"keskustelu": kid, "teksti": "mit\u00e4 tied\u00e4t minusta?"})
        self.assertEqual(s, 200)
        self.assertIn("[l\u00e4hde: faktat.md]", d["vastaus"])
        self.assertIn("EI L\u00d6YDY MUISTISTA", d["vastaus"])
        self.assertEqual(d["tuntemattomia_lahteita"], 1)
        self.assertIn("Tony opiskelee", self.kysytty[-1][0])        # muisti luettiin ennen vastausta
        # tallennus isannalle, 600
        p = Path(self.tmp.name) / "data" / (kid + ".jsonl")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(len(p.read_text(encoding="utf-8").splitlines()), 2)
        # paatos: vain Tonyn viesti (indeksi 0) kelpaa, Aivon (1) ei
        s, _ = self.api(ev, "/api/paatos", {"keskustelu": kid, "indeksi": 1})
        self.assertEqual(s, 400)
        s, _ = self.api(ev, "/api/paatos", {"keskustelu": kid, "indeksi": 0})
        self.assertEqual(s, 200)
        self.assertEqual(self.paatokset, ["mit\u00e4 tied\u00e4t minusta?"])

    def test_liian_pitka_viesti_hylataan(self):
        ev = self.kirjaudu()
        _, d = self.api(ev, "/api/uusi", {"tila": "tyokalu"})
        s, _ = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "x" * (chat.VIESTI_YLARAJA + 1)})
        self.assertEqual(s, 400)

    def test_sivu_on_mobiiliystavallinen(self):
        ev = self.kirjaudu()
        s, h, d = self.pyynto("GET", "/", None, {"Cookie": ev})
        self.assertEqual(s, 200)
        self.assertIn(b'name="viewport"', d)
        self.assertEqual(h["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
