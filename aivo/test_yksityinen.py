import http.client
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from aivo import chat, yksityinen

SAL = "oikea-salasana-123"


def ip(*osat):
    return ".".join(str(o) for o in osat)


TS = ".ts" + ".net"


class HakemistoTesti(unittest.TestCase):
    def test_luo_0700_ja_korjaa_laajat_oikeudet(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "yks"
            yksityinen.tarkista_yksityinen_hakemisto(p)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700)
            os.chmod(p, 0o755)
            yksityinen.tarkista_yksityinen_hakemisto(p)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700)

    def test_symlinkki_ja_tiedosto_hylataan(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "oikea").mkdir()
            os.symlink(Path(d) / "oikea", Path(d) / "linkki")
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yksityinen.tarkista_yksityinen_hakemisto(Path(d) / "linkki")
            (Path(d) / "f").write_text("x")
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yksityinen.tarkista_yksityinen_hakemisto(Path(d) / "f")

    def test_toisen_kayttajan_kansio_hylataan(self):
        with tempfile.TemporaryDirectory() as d:
            vanha = os.geteuid
            try:
                os.geteuid = lambda: vanha() + 1
                with self.assertRaises(yksityinen.YksityinenVirhe):
                    yksityinen.tarkista_yksityinen_hakemisto(d)
            finally:
                os.geteuid = vanha


class MalliTesti(unittest.TestCase):
    def test_vain_loopback_ip(self):
        self.assertTrue(yksityinen.sallittu_malli_osoite("http://" + ip(127, 0, 0, 1) + ":8081"))
        self.assertTrue(yksityinen.sallittu_malli_osoite("http://[::1]:8081"))
        for paha in ("https://" + ip(127, 0, 0, 1), "http://localhost:8081", "http://example.com", "http://" + ip(10, 0, 0, 5),
                     "http://u:p@" + ip(127, 0, 0, 1), "", "roskaa"):
            self.assertFalse(yksityinen.sallittu_malli_osoite(paha), paha)

    def test_ulkoiseen_osoitteeseen_ei_lahteta_mitaan(self):
        kutsuttu = []
        with self.assertRaises(yksityinen.YksityinenVirhe):
            yksityinen.kysy_paikalliselta("m", [], "", "http://example.com", avaa=lambda *a, **k: kutsuttu.append(1))
        self.assertEqual(kutsuttu, [])

    def test_pyynto_ja_vastaus(self):
        saatu = {}

        class Vast(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def avaa(pyynto, timeout):
            saatu["url"] = pyynto.full_url
            saatu["runko"] = json.loads(pyynto.data)
            return Vast(json.dumps({"choices": [{"message": {"content": " Hei. "}}]}).encode())
        v = yksityinen.kysy_paikalliselta("MUISTI", [{"rooli": "tony", "teksti": "moi"}], "OHJE", avaa=avaa)
        self.assertEqual(v, "Hei.")
        self.assertTrue(saatu["url"].endswith("/v1/chat/completions"))
        viestit = saatu["runko"]["messages"]
        self.assertIn("OHJE", viestit[0]["content"])
        self.assertIn("MUISTI", viestit[0]["content"])
        self.assertEqual(viestit[1], {"role": "user", "content": "moi"})

    def test_virhe_ei_vuoda_sisaltoa(self):
        def avaa(*a, **k):
            raise OSError("salainen-sisalto")
        with self.assertRaises(yksityinen.YksityinenVirhe) as c:
            yksityinen.kysy_paikalliselta("m", [], avaa=avaa)
        self.assertNotIn("salainen", str(c.exception))


class OhjeetTesti(unittest.TestCase):
    def test_ydinohjeet_luetaan_mutta_ei_ulkopuolelta_eika_gitista(self):
        with tempfile.TemporaryDirectory() as d:
            juuri = Path(d) / "ohjeet"
            (juuri / ".git").mkdir(parents=True)
            (juuri / ".git" / "x.md").write_text("GIT")
            (juuri / "saannot.md").write_text("Saanto 1")
            (Path(d) / "ulko.md").write_text("ULKO")
            os.symlink(Path(d) / "ulko.md", juuri / "linkki.md")
            t = yksityinen.lue_ydinohjeet(juuri)
            self.assertIn("Saanto 1", t)
            self.assertNotIn("GIT", t)
            self.assertNotIn("ULKO", t)

    def test_ehdotus_ei_muuta_ohjeita_ennen_hyvaksyntaa(self):
        with tempfile.TemporaryDirectory() as d:
            eh, oh = Path(d) / "eh", Path(d) / "oh"
            oh.mkdir()
            (oh / "saannot.md").write_text("vanha\n")
            vastaus = yksityinen.irrota_ehdotukset(
                "Ehdotan.\n[[EHDOTUS saannot.md]]\nuusi\n[[/EHDOTUS]]\nValmis.",
                lambda n, t: yksityinen.tallenna_ehdotus(eh, n, t))
            self.assertNotIn("uusi", vastaus)
            self.assertIn("ei ole voimassa", vastaus)
            self.assertEqual((oh / "saannot.md").read_text(), "vanha\n")
            [eid] = yksityinen.listaa_ehdotukset(eh)
            self.assertEqual(stat.S_IMODE((eh / (eid + ".json")).stat().st_mode), 0o600)
            d1 = yksityinen.lue_ehdotus(eh, eid)
            for paha in ("", "kylla", "kyllä 00000000", "kyllä " + d1["sha"][:8] + " x"):
                with self.assertRaises(yksityinen.YksityinenVirhe):
                    yksityinen.hyvaksy_ehdotus(eh, oh, eid, paha, suorita=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
            self.assertEqual((oh / "saannot.md").read_text(), "vanha\n")
            yksityinen.hyvaksy_ehdotus(eh, oh, eid, "kyllä " + d1["sha"][:8],
                                       suorita=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
            self.assertEqual((oh / "saannot.md").read_text(), "uusi\n")
            self.assertEqual(yksityinen.listaa_ehdotukset(eh), [])

    def test_muokattu_ehdotus_ei_mene_lapi(self):
        with tempfile.TemporaryDirectory() as d:
            eid = yksityinen.tallenna_ehdotus(d, "a.md", "teksti\n")
            p = Path(d) / (eid + ".json")
            j = json.loads(p.read_text())
            j["teksti"] = "vaihdettu\n"
            p.write_text(json.dumps(j))
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yksityinen.lue_ehdotus(d, eid)

    def test_ehdotuksen_nimi_ei_paase_polkuun(self):
        with tempfile.TemporaryDirectory() as d:
            for nimi in ("../x.md", "a/b.md", "x.sh", ".md"):
                with self.assertRaises(yksityinen.YksityinenVirhe):
                    yksityinen.tallenna_ehdotus(d, nimi, "x")

    def test_github_etarepo_estaa_ydinohjeiden_pushin(self):
        def suorita(kom, **k):
            if "get-url" in kom:
                return SimpleNamespace(returncode=0, stdout="https://github.com/x/y.git")
            return SimpleNamespace(returncode=0, stdout="")
        with self.assertRaises(yksityinen.YksityinenVirhe):
            yksityinen.julkaise_kirjastoon("k", "k/a.md", suorita)


class MuistiValintaTesti(unittest.TestCase):
    def test_osuvin_tiedosto_ensin_ja_raja_ei_pysayta_pienempia(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "iso.md").write_text("x" * 5000)
            (Path(d) / "kahvi.md").write_text("Tony juo kahvia aamulla.")
            (Path(d) / "koulu.md").write_text("Koulussa on ohjelmointikurssi.")
            teksti, mukana = chat.lue_muisti(d, 100, 400, "milloin Tony juo kahvia")
            self.assertEqual(mukana[0], "kahvi.md")
            self.assertNotIn("iso.md", mukana)
            self.assertIn("koulu.md", mukana)


class YksityinenPalveluTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        r = Path(cls.tmp.name)
        (r / "kirjasto").mkdir()
        (r / "kirjasto" / "faktat.md").write_text("TYOKALUTILAN-FAKTA")
        (r / "yks" / "muisti").mkdir(parents=True)
        (r / "yks" / "muisti" / "oma.md").write_text("Tony pitaa hiljaisuudesta.")
        (r / "yks" / "ydin").mkdir()
        (r / "yks" / "ydin" / "saannot.md").write_text("YDINSAANTO-X")
        cls.claude_kutsut, cls.malli_kutsut, cls.paatokset = [], [], []

        def kysy_claude(muisti, historia):
            cls.claude_kutsut.append((muisti, historia))
            return "pilvivastaus"

        def kysy_yks(muisti, historia, ohjeet):
            cls.malli_kutsut.append((muisti, historia, ohjeet))
            return "Paikallinen [lähde: oma.md].\n[[EHDOTUS saannot.md]]uusi saanto[[/EHDOTUS]]"

        def julkaise(kirjasto, kid, aika, teksti):
            cls.paatokset.append((kirjasto, teksti))
            return "ei-gitissa"
        cls.tila = chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "data"), [TS],
                             kysy=kysy_claude, julkaise=julkaise, yks_data=str(r / "yks" / "chat"),
                             yks_muisti=str(r / "yks" / "muisti"), ydinohjeet=str(r / "yks" / "ydin"),
                             ehdotukset=str(r / "yks" / "ehd"), kysy_yks=kysy_yks)
        cls.srv = chat.kaynnista(ip(127, 0, 0, 1), 0, cls.tila)
        cls.portti = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def api(self, ev, polku, olio):
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        c.request("POST", polku, body=json.dumps(olio), headers={"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def kirjaudu(self):
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=" + SAL, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        r.read()
        return r.getheader("Set-Cookie").split(";")[0]

    def test_oletus_on_yksityinen_eika_claudea_kutsuta(self):
        ev = self.kirjaudu()
        s, d = self.api(ev, "/api/uusi", {})
        self.assertEqual((s, d["tila"]), (200, "yksityinen"))
        n = len(self.claude_kutsut)
        s, v = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "mitä pidän hiljaisuudesta?"})
        self.assertEqual(s, 200)
        self.assertEqual(len(self.claude_kutsut), n)                 # Claude ei kutsuttu
        muisti, _, ohjeet = self.malli_kutsut[-1]
        self.assertIn("hiljaisuudesta", muisti)
        self.assertNotIn("TYOKALUTILAN-FAKTA", muisti)              # tyokalutilan muisti ei sekoitu
        self.assertIn("YDINSAANTO-X", ohjeet)
        self.assertIn("[lähde: oma.md]", v["vastaus"])
        self.assertNotIn("uusi saanto", v["vastaus"])
        self.assertIn("ei ole voimassa", v["vastaus"])
        [eid] = yksityinen.listaa_ehdotukset(self.tila.ehdotukset)[-1:]
        self.assertEqual(yksityinen.lue_ehdotus(self.tila.ehdotukset, eid)["teksti"], "uusi saanto\n")
        self.assertEqual((Path(self.tmp.name) / "yks" / "ydin" / "saannot.md").read_text(), "YDINSAANTO-X")
        # tallennus 0600 yksityiseen kansioon, ei tyokalukansioon
        p = Path(self.tmp.name) / "yks" / "chat" / (d["id"] + ".jsonl")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertFalse((Path(self.tmp.name) / "data" / (d["id"] + ".jsonl")).exists())

    def test_tyokalutila_ei_nae_ydinohjeita_eika_yksityista_muistia(self):
        ev = self.kirjaudu()
        s, d = self.api(ev, "/api/uusi", {"tila": "tyokalu"})
        self.assertEqual((s, d["tila"]), (200, "tyokalu"))
        m = len(self.malli_kutsut)
        s, v = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "moi"})
        self.assertEqual(s, 200)
        muisti, historia = self.claude_kutsut[-1]
        self.assertIn("TYOKALUTILAN-FAKTA", muisti)
        self.assertNotIn("YDINSAANTO-X", muisti + json.dumps(historia))
        self.assertNotIn("hiljaisuudesta", muisti)
        self.assertEqual(len(self.malli_kutsut), m)

    def test_paatos_yksityisesta_menee_yksityiseen_muistiin(self):
        ev = self.kirjaudu()
        _, d = self.api(ev, "/api/uusi", {})
        self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "päätän lepääväni"})
        s, _ = self.api(ev, "/api/paatos", {"keskustelu": d["id"], "indeksi": 0})
        self.assertEqual(s, 200)
        self.assertEqual(self.paatokset[-1], (self.tila.yks_muisti, "päätän lepääväni"))

    def test_tuntematon_tila_hylataan(self):
        s, _ = self.api(self.kirjaudu(), "/api/uusi", {"tila": "muu"})
        self.assertEqual(s, 400)

    def test_sivulla_valinta_ja_oletus_yksityinen(self):
        ev = self.kirjaudu()
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        c.request("GET", "/", headers={"Cookie": ev})
        html = c.getresponse().read().decode("utf-8")
        self.assertIn('<option value="yksityinen" selected>', html)
        self.assertIn('value="tyokalu"', html)


class KaynnistysTesti(unittest.TestCase):
    def test_ilman_yks_dataa_yksityinen_tila_on_pois(self):
        with tempfile.TemporaryDirectory() as d:
            tila = chat.Tila(chat.tiivista_salasana(SAL), d, d, [TS])
            self.assertIsNone(tila.yks)

    def test_ulkoinen_mallipalvelin_estaa_kaynnistyksen(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.Tila(chat.tiivista_salasana(SAL), d, d, [TS], yks_data=d + "/y", malli_url="http://example.com")

    def test_tyokalukayttaja_lisaa_sudo_etuliitteen(self):
        kom = []

        def suorita(k, **kw):
            kom.extend(k)
            return SimpleNamespace(returncode=0, stdout="ok")
        chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "x"}], suorita, env={"AIVO_TYOKALU_KAYTTAJA": "aivo-tyokalu"})
        self.assertEqual(kom[:5], ["sudo", "-n", "-u", "aivo-tyokalu", "--"])
        self.assertIn("--tools", kom)


class MittaaTesti(unittest.TestCase):
    def test_mittaus_ja_ulkoinen_osoite(self):
        from aivo import mittaa_malli

        class Vast(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        avaa = lambda p, timeout: Vast(json.dumps({"choices": [{"message": {"content": "vastaus"}}],
                                                    "usage": {"completion_tokens": 10}}).encode())
        t = mittaa_malli.mittaa("http://" + ip(127, 0, 0, 1) + ":8081", avaa)
        self.assertEqual(len(t), len(mittaa_malli.KYSYMYKSET))
        self.assertEqual(t[0]["tokenia"], 10)
        with self.assertRaises(yksityinen.YksityinenVirhe):
            mittaa_malli.mittaa("http://example.com", avaa)


if __name__ == "__main__":
    unittest.main()
