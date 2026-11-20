"""Testit Aivon kulunakymalle (GET /api/kulut, chat.kulut_yhteenveto). Ei verkkoa ulos, ei tunnuksia;
kirjanpito on tilapaishakemistossa ja kello kiintea."""
import http.client
import json
import tempfile
import threading
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

from aivo import chat, openrouter_kulut as ok

SAL = "oikea-salasana-123"
TS = ".ts" + ".net"
NYT = datetime(2026, 11, 19, 12, 0, tzinfo=ok.TZ)


def ip(*osat):
    return ".".join(str(o) for o in osat)


def rivi(paivia_sitten, usd=None, malli="m/a", tehtava="aivo-yksityinen", **lisa):
    d = {"aika": (NYT - timedelta(days=paivia_sitten)).isoformat(timespec="seconds"), "malli": malli, "tehtava": tehtava}
    if usd is not None:
        d["usd"] = usd
    d.update(lisa)
    return d


def kirjoita(hakemisto, rivit):
    (Path(hakemisto) / ok.KULUT).write_text("".join(json.dumps(r) + "\n" for r in rivit), encoding="utf-8")


class YhteenvetoTesti(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = self._tmp.name

    def yv(self, kerroin="0.92", nyt=NYT):
        return chat.kulut_yhteenveto(self.d, nyt, chat.lue_kulukerroin(kerroin))

    def test_summat_oikein_testikirjanpidosta(self):
        kirjoita(self.d, [
            rivi(0, 1.0, malli="m/a"),
            rivi(0, 0.5, malli="m/b", tehtava="aivo-tiivistys"),
            rivi(0, 0.5, varaus=True, varaus_id="v1"),             # toteutunut kulu korvaa taman
            rivi(0, 0.25, varaus_id="v1"),
            rivi(3, 2.0, malli="m/b", tehtava="2026-11-19-55-tehtava"),
            rivi(6, 1.0),                                           # 7 vrk:n ikkunan viimeinen paiva
            rivi(7, 4.0),                                           # vain 30 vrk:ssa
            rivi(29, 8.0),                                          # 30 vrk:n ikkunan viimeinen paiva
            rivi(30, 100.0),                                        # ei missaan
            rivi(-1, 50.0),                                         # tulevaisuus: ei missaan
        ])
        y = self.yv()
        self.assertTrue(y["luettavissa"])
        self.assertEqual(y["tanaan"]["usd"], 1.75)
        self.assertEqual(y["tanaan"]["eur"], 1.61)
        self.assertEqual(y["paivat_7"]["usd"], 4.75)
        self.assertEqual(y["paivat_30"]["usd"], 16.75)
        self.assertEqual(y["paivat_30"]["eur"], 15.41)
        self.assertEqual(y["tanaan"]["tarkoitukset"], {"chat": {"usd": 1.25, "eur": 1.15}, "tiivistys": {"usd": 0.5, "eur": 0.46}})
        self.assertEqual(y["paivat_7"]["tarkoitukset"]["muu"]["usd"], 2.0)
        self.assertEqual(y["tanaan"]["mallit"], {"m/a": {"usd": 1.25, "eur": 1.15}, "m/b": {"usd": 0.5, "eur": 0.46}})
        self.assertEqual(y["paivat_7"]["mallit"]["m/b"]["usd"], 2.5)
        self.assertEqual((y["paivakatto_eur"], y["kerroin_usd_eur"], y["eur_on_arvio"]), (10.0, 0.92, True))

    def test_tehtavan_tunniste_ei_paady_vastaukseen(self):
        kirjoita(self.d, [rivi(0, 1.0, tehtava="2026-11-19-55-salainen-nimi")])
        self.assertNotIn("salainen", json.dumps(self.yv()))

    def test_paivat_vaihtuvat_helsingin_aikaan(self):
        kirjoita(self.d, [{"aika": "2026-11-18T22:30:00+00:00", "malli": "m", "tehtava": "aivo-yksityinen", "usd": 1.0}])
        self.assertEqual(self.yv()["tanaan"]["usd"], 1.0)          # Helsingissa 19.11. klo 0.30

    def test_tuntematon_kulu_arvioidaan_kuten_katkaisimessa(self):
        kirjoita(self.d, [rivi(0, kulu_tuntematon=True)])
        self.assertEqual(self.yv()["tanaan"]["usd"], float(ok.VARO_USD_KUTSU))

    def test_ok_kirjaa_rivit_kelpaavat(self):
        ok.kirjaa("m/x", "aivo-yksityinen", {"usage": {"cost": 0.3, "prompt_tokens": 10, "completion_tokens": 5}}, NYT, self.d)
        self.assertEqual(self.yv()["tanaan"]["mallit"], {"m/x": {"usd": 0.3, "eur": 0.28}})

    def test_puuttuva_kirjanpito_palauttaa_nollat(self):
        for y in (self.yv(), chat.kulut_yhteenveto(str(Path(self.d) / "ei-ole"), NYT, chat.lue_kulukerroin("0.92"))):
            self.assertTrue(y["luettavissa"])
            for k in ("tanaan", "paivat_7", "paivat_30"):
                self.assertEqual((y[k]["usd"], y[k]["eur"], y[k]["mallit"], y[k]["tarkoitukset"]), (0.0, 0.0, {}, {}))
            self.assertEqual((y["varoitus"], y["katto_taynna"], y["katosta_kaytetty"]), (False, False, 0.0))

    def test_tyhja_kirjanpito_palauttaa_nollat(self):
        (Path(self.d) / ok.KULUT).write_text("\n\n", encoding="utf-8")
        y = self.yv()
        self.assertTrue(y["luettavissa"])
        self.assertEqual((y["tanaan"]["usd"], y["paivat_30"]["eur"], y["varoitus"]), (0.0, 0.0, False))

    def test_rikkinainen_kirjanpito_ei_kaada(self):
        (Path(self.d) / ok.KULUT).write_text('{"aika": "2026-11-19T10:00:00+02:00", "usd": 1}\nei-jsonia\n', encoding="utf-8")
        y = self.yv()
        self.assertFalse(y["luettavissa"])
        self.assertTrue(y["varoitus"])              # katkaisin estaa kutsut, joten nakyma varoittaa
        self.assertFalse(y["katto_taynna"])
        self.assertEqual(y["tanaan"]["usd"], 0.0)

    def test_kirjanpito_hakemistona_ei_kaada(self):
        (Path(self.d) / ok.KULUT).mkdir()
        self.assertFalse(self.yv()["luettavissa"])

    def test_varoitus_80_prosentissa(self):
        # katkaisimen kerroin 0.92: 8.69 $ = 7.9948 EUR (< 8), 8.70 $ = 8.004 EUR (>= 8)
        kirjoita(self.d, [rivi(0, 8.69)])
        y = self.yv()
        self.assertEqual((y["varoitus"], y["katto_taynna"]), (False, False))
        self.assertEqual(y["katosta_kaytetty"], 0.7995)
        kirjoita(self.d, [rivi(0, 8.70)])
        y = self.yv()
        self.assertEqual((y["varoitus"], y["katto_taynna"]), (True, False))

    def test_varoitus_ei_riipu_nayton_kertoimesta(self):
        kirjoita(self.d, [rivi(0, 8.69)])
        self.assertFalse(self.yv("2")["varoitus"])
        self.assertEqual(self.yv("2")["tanaan"]["eur"], 17.38)
        kirjoita(self.d, [rivi(0, 8.70)])
        self.assertTrue(self.yv("0.1")["varoitus"])

    def test_katto_taynna_kuten_katkaisimessa(self):
        kirjoita(self.d, [rivi(0, 10.87)])          # 10.0004 EUR
        self.assertTrue(self.yv()["katto_taynna"])
        self.assertFalse(ok.saako_kutsua(NYT, self.d))
        kirjoita(self.d, [rivi(0, 10.86)])          # 9.9912 EUR
        self.assertFalse(self.yv()["katto_taynna"])
        self.assertTrue(ok.saako_kutsua(NYT, self.d))

    def test_mallien_maara_rajattu(self):
        kirjoita(self.d, [rivi(0, 1.0 + i, malli="m/%02d" % i) for i in range(chat.KULUT_MALLEJA + 3)])
        mallit = self.yv()["tanaan"]["mallit"]
        self.assertEqual(len(mallit), chat.KULUT_MALLEJA + 1)
        self.assertEqual(mallit["muut"]["usd"], 1.0 + 2.0 + 3.0)

    def test_kulukerroin(self):
        self.assertEqual(float(chat.lue_kulukerroin("0,92")), 0.92)
        self.assertEqual(float(chat.lue_kulukerroin(" 1.05 ")), 1.05)
        for huono in ("0", "-1", "nan", "inf", "abc", "", "11"):
            with self.assertRaises(chat.ChatVirhe, msg=huono):
                chat.lue_kulukerroin(huono)

    def test_oletuskerroin_ja_asetus(self):
        self.assertEqual(chat.KULUT_KERROIN_OLETUS, "0.92")
        self.assertEqual(chat.Tila(chat.tiivista_salasana(SAL), self.d, str(Path(self.d) / "data"), [TS]).kulut_kerroin,
                         chat.lue_kulukerroin("0.92"))
        saatu = []

        def kaynnista(osoite, portti, tila):
            saatu.append(tila.kulut_kerroin)
            raise chat.ChatVirhe("testi pysayttaa")
        with unittest.mock.patch.object(chat, "lue_tiiviste", return_value=chat.tiivista_salasana(SAL)), \
                unittest.mock.patch.object(chat, "kaynnista", kaynnista), \
                unittest.mock.patch("sys.stderr"):
            args = ["palvele", "--kirjasto", self.d, "--data", str(Path(self.d) / "data")]
            self.assertEqual(chat.main(args + ["--kulut-kerroin", "1,1"]), 1)
            self.assertEqual(saatu, [chat.lue_kulukerroin("1.1")])
            self.assertEqual(chat.main(args + ["--kulut-kerroin", "0"]), 1)      # virheellinen kerroin: ei kaynnisty
            self.assertEqual(len(saatu), 1)
            with unittest.mock.patch.dict("os.environ", {"AIVO_KULUT_USD_EUR": "0.5"}):
                chat.main(args)
            self.assertEqual(saatu[-1], chat.lue_kulukerroin("0.5"))


class KulutReittiTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        r = Path(cls.tmp.name)
        (r / "kirjasto").mkdir()
        (r / "kulut").mkdir()
        cls.kulut = r / "kulut"
        cls.tila = chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "data"), [TS],
                             kysy=lambda m, h: "x", or_kulutila=str(cls.kulut))
        cls.tila.kulut_kerroin = chat.lue_kulukerroin("0.9")
        cls.srv = chat.kaynnista(ip(127, 0, 0, 1), 0, cls.tila)
        cls.portti = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        (self.kulut / ok.KULUT).unlink(missing_ok=True)

    def pyynto(self, menetelma, polku, otsakkeet=None, runko=None):
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        c.request(menetelma, polku, body=runko, headers=otsakkeet or {})
        r = c.getresponse()
        return r.status, r.read()

    def kirjaudu(self):
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=" + SAL, headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        r.read()
        return r.getheader("Set-Cookie").split(";")[0]

    def test_ilman_kirjautumista_401(self):
        s, d = self.pyynto("GET", "/api/kulut", {"X-Aivo": "1"})
        self.assertEqual(s, 401)
        self.assertNotIn(b"usd", d)

    def test_vaara_istunto_401(self):
        s, _ = self.pyynto("GET", "/api/kulut", {"X-Aivo": "1", "Cookie": "aivo=vaara"})
        self.assertEqual(s, 401)

    def test_vaatii_x_aivo_ja_saman_alkuperan(self):
        ev = self.kirjaudu()
        self.assertEqual(self.pyynto("GET", "/api/kulut", {"Cookie": ev})[0], 403)
        self.assertEqual(self.pyynto("GET", "/api/kulut", {"Cookie": ev, "X-Aivo": "1", "Origin": "https://paha.example.com"})[0], 403)

    def test_vaara_host_421(self):
        ev = self.kirjaudu()
        self.assertEqual(self.pyynto("GET", "/api/kulut", {"Cookie": ev, "X-Aivo": "1", "Host": "paha.example.com"})[0], 421)

    def test_post_ei_kelpaa(self):
        ev = self.kirjaudu()
        s, _ = self.pyynto("POST", "/api/kulut", {"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"}, "{}")
        self.assertEqual(s, 404)

    def test_kirjautuneena_puuttuva_kirjanpito_nollat(self):
        ev = self.kirjaudu()
        s, d = self.pyynto("GET", "/api/kulut", {"Cookie": ev, "X-Aivo": "1"})
        self.assertEqual(s, 200)
        j = json.loads(d)
        self.assertTrue(j["luettavissa"])
        self.assertEqual((j["tanaan"]["usd"], j["paivat_7"]["eur"], j["paivat_30"]["eur"], j["varoitus"]), (0.0, 0.0, 0.0, False))
        self.assertEqual(j["kerroin_usd_eur"], 0.9)
        self.assertFalse((self.kulut / ok.KULUT).exists())      # nakyma ei luo kirjanpitoa
        self.assertFalse((self.kulut / ok.LUKKO).exists())

    def test_kirjautuneena_summat(self):
        nyt = datetime.now(ok.TZ)
        (self.kulut / ok.KULUT).write_text(json.dumps({"aika": nyt.isoformat(timespec="seconds"), "malli": "m/a",
                                                       "tehtava": "aivo-yksityinen", "usd": 2.0}) + "\n", encoding="utf-8")
        ev = self.kirjaudu()
        s, d = self.pyynto("GET", "/api/kulut", {"Cookie": ev, "X-Aivo": "1"})
        j = json.loads(d)
        self.assertEqual((s, j["tanaan"]["usd"], j["tanaan"]["eur"]), (200, 2.0, 1.8))
        self.assertEqual(j["tanaan"]["tarkoitukset"], {"chat": {"usd": 2.0, "eur": 1.8}})

    def test_sivulla_kulut_ja_vain_textcontent(self):
        html = chat.sivu_html("loopback")
        self.assertIn("a.id='kulut'", html)
        self.assertIn("/api/kulut", html)
        self.assertIn("arvioita", html)
        self.assertIn("varoitus", html)
        for kielletty in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(kielletty, html)


if __name__ == "__main__":
    unittest.main()
