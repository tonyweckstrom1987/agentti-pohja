import http.client
import json
import stat
import sys
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from aivo import chat, yksityinen

SAL = "oikea-salasana-123"


def ip(*osat):
    return ".".join(str(o) for o in osat)


TS = ".ts" + ".net"

PAATOS0 = b"\x00AIVO-EXIT 0\n"


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
            return "Paikallinen [l\u00e4hde: oma.md].\n[[EHDOTUS saannot.md]]uusi saanto[[/EHDOTUS]]"

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

    # Palvelin vapauttaa vastauslukon vasta kun vastaus on jo lahtenyt, joten heti perassa
    # lahetetty seuraava viesti voi saada 429. Testi yrittaa uudelleen lyhyella tauolla;
    # palvelimen lukkoa ei muuteta.
    UUDELLEENYRITYKSIA = 50
    TAUKO = 0.02

    def api(self, ev, polku, olio):
        for yritys in range(self.UUDELLEENYRITYKSIA + 1):
            c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
            c.request("POST", polku, body=json.dumps(olio), headers={"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
            r = c.getresponse()
            s, d = r.status, json.loads(r.read())
            if s != 429 or polku != "/api/viesti" or yritys == self.UUDELLEENYRITYKSIA:
                return s, d
            time.sleep(self.TAUKO)

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
        s, v = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "mit\u00e4 pid\u00e4n hiljaisuudesta?"})
        self.assertEqual(s, 200)
        self.assertEqual(len(self.claude_kutsut), n)                 # Claude ei kutsuttu
        muisti, _, ohjeet = self.malli_kutsut[-1]
        self.assertIn("hiljaisuudesta", muisti)
        self.assertNotIn("TYOKALUTILAN-FAKTA", muisti)              # tyokalutilan muisti ei sekoitu
        self.assertIn("YDINSAANTO-X", ohjeet)
        self.assertIn("[l\u00e4hde: oma.md]", v["vastaus"])
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

    def test_paatos_yksityisesta_menee_yksityiseen_muistiin_eika_julkaista(self):
        ev = self.kirjaudu()
        _, d = self.api(ev, "/api/uusi", {})
        _, v = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "p\u00e4\u00e4t\u00e4n lep\u00e4\u00e4v\u00e4ni"})
        n = len(self.paatokset)
        s, r = self.api(ev, "/api/paatos", {"keskustelu": d["id"], "indeksi": v["indeksi"]})
        self.assertEqual((s, r["tila"]), (200, "ei-julkaistu"))
        self.assertEqual(len(self.paatokset), n)         # tyokalutilan julkaisufunktiota ei kutsuttu
        t = (Path(self.tila.yks_muisti) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
        self.assertIn("> p\u00e4\u00e4t\u00e4n lep\u00e4\u00e4v\u00e4ni", t)

    def test_yksityinen_paatos_ei_aja_gitia(self):
        with tempfile.TemporaryDirectory() as d:
            def ei_saa_kutsua(*a, **k):
                raise AssertionError("git-kutsu yksityisessa paatoksessa")
            chat.kirjaa_paatos_yksityinen(d, "20261106-120000-abcdef01", "2026-11-06T12:00:00+00:00", "x")
            chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:01:00+00:00", "y", suorita=ei_saa_kutsua,
                               julkaise=False)

    def test_palvelin_kertoo_viestin_indeksin_kun_edellinen_viesti_ei_tallentunut(self):
        ev = self.kirjaudu()
        _, d = self.api(ev, "/api/uusi", {})
        idx = []
        for i in range(3):
            s, v = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "viesti %d" % i})
            self.assertEqual(s, 200)
            idx.append(v["indeksi"])
        self.assertEqual(idx, [0, 2, 4])
        # 502: Tonyn viesti tallentuu ja indeksi palautetaan; kirjaus osuu juuri siihen viestiin
        def rikki(muisti, historia, ohjeet):
            raise chat.ChatVirhe("malli kaatui")
        vanha, self.tila.kysy_yks = self.tila.kysy_yks, rikki
        try:
            s, v = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "virheviesti"})
        finally:
            self.tila.kysy_yks = vanha
        self.assertEqual((s, v["indeksi"]), (502, 6))
        self.assertEqual(self.tila.yks.lue(d["id"])[6]["teksti"], "virheviesti")

    def test_sivu_ei_arvaa_indeksia(self):
        self.assertNotIn("n+=2", chat.HTML)
        self.assertIn("j.indeksi", chat.HTML)

    def test_keskustelu_ei_merkkijono_ei_kaada_yhteytta(self):
        ev = self.kirjaudu()
        for paha in (5, ["x"], {"a": 1}, None):
            for polku, runko in (("/api/viesti", {"keskustelu": paha, "teksti": "x"}),
                                 ("/api/paatos", {"keskustelu": paha, "indeksi": 0})):
                s, v = self.api(ev, polku, runko)
                self.assertEqual(s, 400, (polku, paha))
                self.assertIn("virhe", v)

    def test_liian_iso_runko_hylataan_eika_luo_keskustelua(self):
        ev = self.kirjaudu()
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        n = len(list(Path(self.tila.yks.data).glob("*.jsonl")))
        c.request("POST", "/api/uusi", body=b"{" + b" " * 30000 + b"}",
                  headers={"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 413)
        self.assertEqual(len(list(Path(self.tila.yks.data).glob("*.jsonl"))), n)

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

    def test_suora_polku_ei_sisalla_sudoa(self):
        kom = []

        def suorita(k, **kw):
            kom.extend(k)
            return SimpleNamespace(returncode=0, stdout="ok")
        chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "x"}], suorita, env={})
        self.assertNotIn("sudo", kom)
        self.assertIn("--tools", kom)

    def test_vanha_sudo_asetus_kieltaytyy(self):
        def suorita(k, **kw):
            raise AssertionError("ei saa ajaa")
        with self.assertRaises(chat.ChatVirhe):
            chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "x"}], suorita, env={"AIVO_TYOKALU_KAYTTAJA": "aivo-tyokalu"})

    def _palvelin(self, polku, vastaa):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(polku)
        srv.listen(1)
        saatu = []

        def ajo():
            c, _ = srv.accept()
            with c:
                data = b""
                while True:
                    o = c.recv(4096)
                    if not o:
                        break
                    data += o
                saatu.append(data)
                try:
                    c.sendall(vastaa)
                except OSError:
                    pass
        t = threading.Thread(target=ajo, daemon=True)
        t.start()
        self.addCleanup(srv.close)
        return saatu, t

    def test_socket_valittaa_syotteen_ja_vastauksen(self):
        with tempfile.TemporaryDirectory() as d:
            polku = d + "/t.sock"
            saatu, t = self._palvelin(polku, "Kyll\u00e4, t\u00e4m\u00e4 on keksitty vastaus.\n".encode("utf-8") + PAATOS0)

            def suorita(*a, **k):
                raise AssertionError("socket-polussa ei kaynnisteta aliprosessia")
            v = chat.kysy_claudelta("keksitty muisti", [{"rooli": "tony", "teksti": "moi"}], suorita,
                                    env={"AIVO_TYOKALU_SOCKET": polku})
            t.join(5)
            self.assertEqual(v, "Kyll\u00e4, t\u00e4m\u00e4 on keksitty vastaus.")
            self.assertIn(b"Tony: moi", saatu[0])

    def test_socket_tyhja_vastaus_ja_puuttuva_socket_ovat_virheita(self):
        with tempfile.TemporaryDirectory() as d:
            polku = d + "/t.sock"
            _, t = self._palvelin(polku, b"  \n" + PAATOS0)
            with self.assertRaises(chat.ChatVirhe):
                chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "x"}], env={"AIVO_TYOKALU_SOCKET": polku})
            t.join(5)
            with self.assertRaises(chat.ChatVirhe) as ctx:
                chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "x"}], env={"AIVO_TYOKALU_SOCKET": d + "/ei.sock"})
            self.assertNotIn(d, str(ctx.exception))      # virheessa vain tyyppi, ei polkua

    def test_socket_liian_pitka_vastaus_hylataan(self):
        with tempfile.TemporaryDirectory() as d:
            polku = d + "/t.sock"
            self._palvelin(polku, b"x" * (chat.VASTAUS_YLARAJA + 70000))
            with self.assertRaises(chat.ChatVirhe):
                chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "x"}], env={"AIVO_TYOKALU_SOCKET": polku})


    def _kysy(self, vastaus):
        with tempfile.TemporaryDirectory() as d:
            polku = d + "/t.sock"
            _, t = self._palvelin(polku, vastaus)
            try:
                return chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "x"}], env={"AIVO_TYOKALU_SOCKET": polku})
            finally:
                t.join(5)

    def test_paluukoodi_1_nakyy_lyhyena_virheena_ilman_vastauksen_sisaltoa(self):
        with self.assertRaises(chat.ChatVirhe) as ctx:
            self._kysy(b"SALAINEN-stderr-tai-osittainen vastaus" + b"\x00AIVO-EXIT 1\n")
        viesti = str(ctx.exception)
        self.assertEqual(viesti, "Ty\u00f6kalu ep\u00e4onnistui (koodi 1), katso journalctl -u aivo-tyokalu@*")
        self.assertNotIn("SALAINEN", viesti)

    def test_puuttuva_paatosrivi_on_katkennut_yhteys(self):
        with self.assertRaises(chat.ChatVirhe) as ctx:
            self._kysy(b"vastaus joka katkesi")
        self.assertIn("yhteys katkesi", str(ctx.exception))
        self.assertNotIn("vastaus joka", str(ctx.exception))

    def test_paatosrivin_nakoinen_teksti_keskella_ei_tulkita_paatokseksi(self):
        # Pelkka teksti ilman NUL-tavua keskella: osa vastausta, koodi tulee lopusta.
        v = self._kysy(b"Rivi: AIVO-EXIT 1\nja viela AIVO-EXIT 7\nloppu" + PAATOS0)
        self.assertEqual(v, "Rivi: AIVO-EXIT 1\nja viela AIVO-EXIT 7\nloppu")
        # NUL-tavun kanssa keskella ja oikea paatos lopussa: vain viimeinen ratkaisee.
        v = self._kysy(b"a\x00AIVO-EXIT 1\nb" + PAATOS0)
        self.assertIn("b", v)
        # Keskella oleva (vaarennos) ei korvaa puuttuvaa loppurivia: virhe, ei onnistuminen.
        with self.assertRaises(chat.ChatVirhe):
            self._kysy(b"a\x00AIVO-EXIT 0\nlisaa tekstia")

    def test_erota_paatos_suoraan(self):
        self.assertEqual(chat.erota_paatos(b"ok" + PAATOS0), ("ok", 0))
        self.assertEqual(chat.erota_paatos(b"" + b"\x00AIVO-EXIT 137\n"), ("", 137))
        self.assertEqual(chat.erota_paatos(b"ok"), ("ok", None))
        self.assertEqual(chat.erota_paatos(b"ok\x00AIVO-EXIT x\n"), ("ok\x00AIVO-EXIT x\n", None))


class KaaresTesti(unittest.TestCase):
    """aivo/tyokalu_kaares.py: oikea aliprosessi, ei verkkoa."""

    def _aja(self, komento, syote=b""):
        import io
        from aivo import tyokalu_kaares as k
        ulos = io.BytesIO()
        self.assertEqual(k.main(komento, io.BytesIO(syote), ulos), 2 if not komento else 0)
        return ulos.getvalue()

    def test_onnistunut_ajo_valittaa_vastauksen_ja_koodin_0(self):
        raaka = self._aja([sys.executable, "-c", "import sys;sys.stdout.write(sys.stdin.read().upper())"], b"moi")
        self.assertEqual(chat.erota_paatos(raaka), ("MOI", 0))

    def test_epaonnistunut_ajo_antaa_koodin_eika_stderria(self):
        raaka = self._aja([sys.executable, "-c", "import sys;sys.stderr.write('SALAINEN');sys.stdout.write('x');sys.exit(3)"])
        self.assertEqual(chat.erota_paatos(raaka), ("x", 3))
        self.assertNotIn(b"SALAINEN", raaka)

    def test_nul_poistetaan_vastauksesta_joten_paatosta_ei_voi_vaarentaa(self):
        raaka = self._aja([sys.executable, "-c", "import sys;sys.stdout.write('a\\x00AIVO-EXIT 0\\n');sys.exit(1)"])
        self.assertEqual(chat.erota_paatos(raaka), ("aAIVO-EXIT 0\n", 1))

    def test_puuttuva_komento_ja_signaali(self):
        self.assertEqual(chat.erota_paatos(self._aja(["/ei/ole/olemassa"]))[1], 127)
        raaka = self._aja([sys.executable, "-c", "import os,signal;os.kill(os.getpid(),signal.SIGKILL)"])
        self.assertEqual(chat.erota_paatos(raaka)[1], 137)

    def test_ilman_komentoa_koodi_2(self):
        self.assertEqual(chat.erota_paatos(self._aja([]))[1], 2)


class OhjeenRajatTesti(unittest.TestCase):
    """Ohje ei saa ohjata heikentamaan suojauksia (A929)."""

    def setUp(self):
        self.ohje = (Path(__file__).resolve().parent.parent / "docs" / "aivo-chat.md").read_text(encoding="utf-8")

    def test_ei_sudoers_nopasswd_eika_nnp_poistoa(self):
        self.assertNotIn("NOPASSWD", self.ohje)
        self.assertNotIn("poista rivi", self.ohje)

    def test_jokaisessa_service_lohkossa_nnp_paalla(self):
        lohkot = re.findall(r"\[Service\](.*?)```", self.ohje, re.S)
        self.assertGreaterEqual(len(lohkot), 2)
        for l in lohkot:
            self.assertRegex(l, r"NoNewPrivileges=(yes|true)")

    def test_tyokaluyksikon_komento_on_kiintea(self):
        m = re.search(r"aivo-tyokalu@\.service`:.*?ExecStart=(.*?)\n", self.ohje, re.S)
        self.assertIsNotNone(m)
        self.assertIn('--tools ""', m.group(1))
        self.assertNotRegex(m.group(1), r"[*$;|&]|%[iIfFhH]")      # ei jokereita eika sijaismerkkeja
        self.assertIn("SocketMode=0600", self.ohje)

    def test_yksikko_ajaa_kaaresta_ja_kaares_on_repossa(self):
        m = re.search(r"aivo-tyokalu@\.service`:.*?ExecStart=(.*?)\n", self.ohje, re.S)
        self.assertIn("aivo/tyokalu_kaares.py", m.group(1))
        self.assertTrue((Path(__file__).resolve().parent / "tyokalu_kaares.py").is_file())
