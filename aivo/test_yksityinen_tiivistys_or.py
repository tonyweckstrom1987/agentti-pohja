"""Testit: yksityisen alueen tiivistys OpenRouter-taustapaalla (ZDR) ja yksityisten tiivistelmien kehote.

Ei verkkoa eika oikeita avaimia: kuljetin on feikki, kulukirjanpito ja alue ovat tilapaishakemistossa."""
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from aivo import (chat, openrouter_kulut as ok, tiivista, tiivista_yksityinen as ty, tiivistelma, tyokalualue,
                  yksityinen, yksityinen_alue, yksityinen_or as yo)

MALLI = "qwen/qwen3-235b-a22b-2507"
AVAIN = "sk-testi-" + "y" * 30          # keksitty, ei oikea avain
NYT = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
KANARIA = "KANARIA-7f3a sisalto ei saa vuotaa"
SAL = "oikea-salasana-123"
ISO = lambda d: d.isoformat(timespec="seconds")


def kid_nro(n):
    return "20261119-0900%02d-%08x" % (n, n)


def kirjoita_keskustelu(kansio, kid, viestit, viimeinen):
    rivit = []
    for n, (rooli, teksti) in enumerate(viestit):
        aika = viimeinen - timedelta(minutes=len(viestit) - 1 - n)
        rivit.append(json.dumps({"aika": ISO(aika), "rooli": rooli, "teksti": teksti, "lahteet": []}, ensure_ascii=False))
    p = Path(kansio) / (kid + ".jsonl")
    p.write_text("\n".join(rivit) + "\n", encoding="utf-8")
    os.chmod(p, 0o600)


class FeikkiVastaus:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self, n=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def mallin_vastaus(lainaus="lyhyet vastaukset"):
    return json.dumps({"kohdat": [{"id": 1, "tyyppi": "mieltymys", "tony_sanoi": [lainaus],
                                   "tulkinta": "Tony haluaa lyhyet vastaukset", "viestit": [0]}]})


class Pohja(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.r = Path(self._t.name)
        self.juuri = self.r / "aivo-yksityinen"
        self.a = yksityinen_alue.yksityinen_alue(self.juuri)
        self.a.valmista()
        self.avain = self.r / "avain"
        self.avain.write_text(AVAIN + "\n")
        os.chmod(self.avain, 0o600)
        self.kulut = self.r / "kulut"
        self.kulut.mkdir(mode=0o700)
        self.rungot = []

    def avaa(self, sisalto=None, virhe=None):
        def avaa(pyynto, timeout=None):
            self.rungot.append(json.loads(pyynto.data))
            if virhe:
                raise virhe
            return FeikkiVastaus({"choices": [{"message": {"content": sisalto or mallin_vastaus()}}],
                                  "usage": {"prompt_tokens": 500, "completion_tokens": 80, "cost": 0.0004}})
        return avaa

    def or_kysy(self, **lisa):
        return ty.tee_or_kysyja(MALLI, str(self.avain), kulutila=str(self.kulut), avaa=lisa.get("avaa") or self.avaa(),
                                nyt=lambda: NYT)

    def keskustelu(self, n=1, teksti="Haluan lyhyet vastaukset."):
        kirjoita_keskustelu(self.a.polku("chat"), kid_nro(n), [("tony", teksti), ("aivo", "Selv\u00e4.")],
                            NYT - timedelta(hours=1))

    def aja(self, kysy, malli, taustapaa="openrouter", nyt=NYT):
        ulos = io.StringIO()
        with redirect_stdout(ulos):
            tulos = ty.aja(self.juuri, kysy, nyt=nyt, taustapaa=taustapaa, malli=malli)
        return tulos, ulos.getvalue()

    def kulurivit(self):
        p = self.kulut / ok.KULUT
        return [json.loads(r) for r in p.read_text().splitlines()] if p.exists() else []


class OpenRouterTaustapaaTesti(Pohja):
    def test_kutsu_kulkee_zdr_polun_kautta_ja_kirjataan(self):
        self.keskustelu()
        kysy, malli = self.or_kysy()
        tulos, _ = self.aja(kysy, malli)
        self.assertEqual((tulos["tiivistetty"], tulos["kutsuja"]), (1, 1))
        [runko] = self.rungot
        self.assertIs(runko["provider"]["zdr"], True)
        self.assertEqual(runko["provider"]["data_collection"], "deny")
        self.assertEqual(runko["model"], MALLI)
        self.assertEqual(runko["messages"][0]["role"], "system")
        self.assertIn("Haluan lyhyet vastaukset.", runko["messages"][1]["content"])
        rivit = self.kulurivit()
        self.assertTrue(rivit)
        self.assertEqual({r.get("tehtava") for r in rivit}, {yo.TIIVISTYS_TEHTAVA})
        [nimi] = tiivistelma.luettele(self.a.polku("tiivistelmat"))
        t = tiivistelma.lue(self.a.polku("tiivistelmat"), nimi)
        self.assertEqual(t["malli"], "zdr:" + MALLI)
        self.assertEqual(t["kohdat"][0]["tony_sanoi"], ["lyhyet vastaukset"])

    def test_kutsu_kayttaa_openrouter_kulut_kutsua(self):
        self.keskustelu()
        kysy, malli = self.or_kysy()
        with mock.patch.object(ok, "kutsu", wraps=ok.kutsu) as m:
            self.aja(kysy, malli)
        self.assertEqual(m.call_count, 1)
        self.assertIs(m.call_args.kwargs["yksityinen_chat"], True)

    def test_ei_avoin_malli_hylataan_ennen_kutsua(self):
        for malli in ("anthropic/claude-3.5-sonnet", "openai/gpt-4o", MALLI + ":free", None, ""):
            with self.assertRaises(ty.TiivistaVirhe, msg=malli):
                ty.tee_or_kysyja(malli, str(self.avain), kulutila=str(self.kulut), avaa=self.avaa())
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yo.kysy_tiivistys("ohje", "data", malli=malli, avain_tiedosto=str(self.avain),
                                  kulutila=str(self.kulut), avaa=self.avaa())
        self.assertEqual(self.rungot, [])
        self.assertEqual(self.kulurivit(), [])

    def test_main_ei_avoin_malli_paluuarvo_1_eika_kutsua(self):
        self.keskustelu()
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rc = ty.main(["--juuri", str(self.juuri)], env={"AIVO_YKSITYINEN_TAUSTAPAA": "openrouter",
                                                             "AIVO_OPENROUTER_MALLI": "openai/gpt-4o",
                                                             "AIVO_OPENROUTER_AVAIN": str(self.avain)})
        self.assertEqual(rc, 1)
        self.assertEqual(tiivistelma.luettele(self.a.polku("tiivistelmat")), [])

    def test_main_valitsee_taustapaan_chatin_asetuksesta(self):
        self.keskustelu()
        nahty = []

        def tee(malli, avain, **kw):
            nahty.append((malli, avain))
            return (lambda ohje, data: mallin_vastaus()), "zdr:" + malli
        with mock.patch.object(ty, "tee_or_kysyja", side_effect=tee), redirect_stdout(io.StringIO()):
            rc = ty.main(["--juuri", str(self.juuri)], env={"AIVO_YKSITYINEN_TAUSTAPAA": "openrouter",
                                                             "AIVO_OPENROUTER_MALLI": MALLI})
        self.assertEqual(rc, 0)
        self.assertEqual(nahty, [(MALLI, None)])                 # avain: oletus (yksityinen_or.OLETUS_AVAIN)

    def test_tuntematon_taustapaa_ymparistosta_hylataan(self):
        with redirect_stderr(io.StringIO()):
            rc = ty.main(["--juuri", str(self.juuri), "--kuiva"], env={"AIVO_YKSITYINEN_TAUSTAPAA": "pilvi"})
        self.assertEqual(rc, 1)

    def test_loopback_polku_ennallaan(self):
        with mock.patch.object(ty, "tee_or_kysyja", side_effect=AssertionError("ei saa kutsua")), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(ty.main(["--juuri", str(self.juuri), "--malli-url", "http://example.com:8081"], env={}), 1)
        with mock.patch.object(ty, "kysy_paikalliselta", return_value=mallin_vastaus()) as m, \
                mock.patch.object(ty, "tee_or_kysyja", side_effect=AssertionError("ei saa kutsua")), \
                redirect_stdout(io.StringIO()):
            self.keskustelu()
            self.assertEqual(ty.main(["--juuri", str(self.juuri)], env={}, nyt=NYT), 0)
        self.assertEqual(m.call_count, 1)
        [nimi] = tiivistelma.luettele(self.a.polku("tiivistelmat"))
        self.assertEqual(tiivistelma.lue(self.a.polku("tiivistelmat"), nimi)["malli"], ty.MALLI)


class PaivakattoTesti(Pohja):
    def test_paivakatto_pysayttaa_kymmeneen(self):
        for n in range(ty.PAIVAKATTO + 2):
            self.keskustelu(n)
        kysy, malli = self.or_kysy()
        tulos, ulos = self.aja(kysy, malli)
        self.assertEqual(len(self.rungot), ty.PAIVAKATTO)
        self.assertEqual(tulos["tiivistetty"], ty.PAIVAKATTO)
        self.assertTrue(tulos["katto"])
        self.assertIn("lopetus: paivakatto 10/10", ulos)
        tulos, _ = self.aja(kysy, malli)                       # samana paivana ei enaa kutsuja
        self.assertEqual((len(self.rungot), tulos["kutsuja"]), (ty.PAIVAKATTO, 0))
        tulos, _ = self.aja(kysy, malli, nyt=NYT + timedelta(days=1))   # seuraavana paivana laskuri nollautuu
        self.assertEqual(tulos["tiivistetty"], 2)

    def test_loopbackilla_ei_kutsukattoa(self):
        for n in range(ty.PAIVAKATTO + 2):
            self.keskustelu(n)
        tulos, _ = self.aja(lambda o, d: mallin_vastaus(), ty.MALLI, taustapaa="loopback")
        self.assertEqual(tulos["tiivistetty"], ty.PAIVAKATTO + 2)

    def test_eurokatto_pysayttaa_eika_kuluta_yrityskertoja(self):
        for n in range(3):
            self.keskustelu(n)
        with mock.patch.object(ok, "_varaa", return_value=(None, ok.PAIVAKATTO_EUR)):
            kysy, malli = self.or_kysy()
            tulos, ulos = self.aja(kysy, malli)
        self.assertEqual(self.rungot, [])
        self.assertTrue(tulos["katto"])
        self.assertEqual(tulos["kutsuja"], 0)
        self.assertIn("lopetus: kulukatto", ulos)
        tila = ty.lue_tila(self.a.polku("indeksi"), NYT)
        self.assertEqual((tila["yritykset"], tila["kutsuja"]), ({}, 0))

    def test_rikkinainen_laskuri_pysayttaa(self):
        self.keskustelu()
        (self.a.polku("indeksi") / ty.TILA_TIEDOSTO).write_text(json.dumps(
            {"versio": 1, "yritykset": {}, "paiva": "2026-11-19", "kutsuja": "9"}))
        kysy, malli = self.or_kysy()
        with self.assertRaises(ty.TiivistaVirhe):
            self.aja(kysy, malli)
        self.assertEqual(self.rungot, [])

    def test_vanha_tilatiedosto_kelpaa(self):
        (self.a.polku("indeksi") / ty.TILA_TIEDOSTO).write_text(json.dumps({"versio": 1, "yritykset": {"x:0": 1}}))
        t = ty.lue_tila(self.a.polku("indeksi"), NYT)
        self.assertEqual((t["kutsuja"], t["yritykset"]), (0, {"x:0": 1}))


class LokiTesti(Pohja):
    def tiedostot(self):
        teksti = ""
        for juuri in (self.juuri / "indeksi", self.kulut):
            for p in Path(juuri).rglob("*"):
                if p.is_file() and p.suffix not in (".lukko", ".lock"):
                    teksti += p.read_text(encoding="utf-8", errors="replace")
        return teksti

    def test_keskustelun_sisalto_ei_paady_lokiin_onnistuessa(self):
        self.keskustelu(teksti=KANARIA)
        kysy, malli = self.or_kysy(avaa=self.avaa(sisalto=json.dumps({"kohdat": []})))
        _, ulos = self.aja(kysy, malli)
        self.assertEqual(len(self.rungot), 1)
        self.assertIn("tiivistetty:", ulos)
        self.assertNotIn("KANARIA", ulos)
        self.assertNotIn("KANARIA", self.tiedostot())

    def test_keskustelun_sisalto_ei_paady_lokiin_virheessa(self):
        self.keskustelu(teksti=KANARIA)
        virhe = urllib.error.HTTPError("https://x", 500, KANARIA, {}, None)
        kysy, malli = self.or_kysy(avaa=self.avaa(virhe=virhe))
        _, ulos = self.aja(kysy, malli)
        self.assertIn("epaonnistui:", ulos)
        self.assertNotIn("KANARIA", ulos)
        self.assertNotIn(AVAIN, ulos)
        self.assertNotIn("KANARIA", self.tiedostot())
        self.assertNotIn(AVAIN, self.tiedostot())

    def test_tiivista_cli_ohjaa_taustapaan(self):
        with mock.patch.object(ty, "main", return_value=0) as m:
            self.assertEqual(tiivista.main(["--alue", "yksityinen", "--taustapaa", "openrouter"]), 0)
        self.assertEqual(m.call_args.args[0], ["--taustapaa", "openrouter"])


class KehoteTesti(unittest.TestCase):
    """Yksityinen tila lukee vain yksityiset tiivistelmat (kun kytkin on paalla), tyokalutila vain omansa."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        r = Path(cls.tmp.name)
        (r / "kirjasto").mkdir()
        (r / "kirjasto" / "faktat.md").write_text("KIRJASTON-FAKTA")
        (r / "yks" / "muisti").mkdir(parents=True)
        (r / "yks" / "ydin").mkdir()
        tk = tyokalualue.tyokalualue(r / "aivo")
        tk.valmista()
        yk = yksityinen_alue.yksityinen_alue(r / "aivo-yksityinen")
        yk.valmista()
        cls._tallenna(tk.polku("tiivistelmat"), 1, "TYOKALU-TULKINTA")
        cls.yks_nimi = cls._tallenna(yk.polku("tiivistelmat"), 2, "YKSITYINEN-TULKINTA")
        cls.claude, cls.yks = [], []

        def kysy(muisti, historia):
            cls.claude.append(muisti)
            return "ok"

        def kysy_yks(muisti, historia, ohjeet):
            cls.yks.append(muisti)
            return "Muistan [l\u00e4hde: %s]." % cls.yks_nimi

        def tila(yks_tiivistelmat):
            return chat.Tila(chat.tiivista_salasana(SAL), str(r / "kirjasto"), str(r / "data"), ["localhost"],
                             kysy=kysy, yks_data=str(r / "yks" / "chat"), yks_muisti=str(r / "yks" / "muisti"),
                             ydinohjeet=str(r / "yks" / "ydin"), ehdotukset=str(r / "yks" / "ehd"), kysy_yks=kysy_yks,
                             tiivistelmat=str(tk.polku("tiivistelmat")), yks_tiivistelmat=yks_tiivistelmat)
        cls.paalla = cls._palvelin(tila(str(yk.polku("tiivistelmat"))))
        cls.pois = cls._palvelin(tila(None))
        cls.tk_tiiv = str(tk.polku("tiivistelmat"))
        cls.tila = staticmethod(tila)

    @staticmethod
    def _tallenna(kansio, n, tulkinta):
        kid = kid_nro(n)
        viestit = [{"rooli": "tony", "teksti": "Haluan lyhyet vastaukset aina.", "aika": "2026-11-10T10:00:00+00:00"}]
        d = {"versio": 1, "keskustelu": kid, "alku": 0, "loppu": 0, "ensimmainen_aika": viestit[0]["aika"],
             "viimeinen_aika": viestit[0]["aika"], "tiivistetty": "2026-11-10T11:00:00+00:00", "malli": "x",
             "alue_sha": tiivistelma.alueen_sha(viestit, 0, 0),
             "kohdat": [{"id": 1, "tyyppi": "mieltymys", "tony_sanoi": ["lyhyet vastaukset"],
                         "tulkinta": tulkinta, "viestit": [0], "tila": "ehdotus"}]}
        tiivistelma.tallenna(kansio, d, viestit)
        return "tiivistelmat/%s-0-0.json" % kid

    @classmethod
    def _palvelin(cls, tila):
        srv = chat.kaynnista("127.0.0.1", 0, tila)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        if not hasattr(cls, "palvelimet"):
            cls.palvelimet = []
        cls.palvelimet.append(srv)
        return srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        for s in cls.palvelimet:
            s.shutdown()
            s.server_close()
        cls.tmp.cleanup()

    def api(self, portti, ev, polku, olio):
        c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
        c.request("POST", polku, body=json.dumps(olio),
                  headers={"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
        x = c.getresponse()
        return x.status, json.loads(x.read())

    def viesti(self, portti, tila_nimi):
        c = http.client.HTTPConnection("127.0.0.1", portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=" + SAL, headers={"Content-Type": "application/x-www-form-urlencoded"})
        x = c.getresponse()
        x.read()
        ev = x.getheader("Set-Cookie").split(";")[0]
        _, d = self.api(portti, ev, "/api/uusi", {"tila": tila_nimi})
        return self.api(portti, ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "lyhyet vastaukset?"})

    def test_kytkin_paalla_yksityinen_kehote_sisaltaa_yksityiset_tiivistelmat(self):
        s, v = self.viesti(self.paalla, "yksityinen")
        self.assertEqual(s, 200)
        self.assertIn("YKSITYINEN-TULKINTA", self.yks[-1])
        self.assertIn("Tony sanoi: \"lyhyet vastaukset\"", self.yks[-1])
        self.assertEqual(v["tuntemattomia_lahteita"], 0)          # tiivistelman nimi kelpaa lahteeksi

    def test_kytkin_pois_yksityinen_kehote_ei_sisalla_tiivistelmia(self):
        s, v = self.viesti(self.pois, "yksityinen")
        self.assertEqual(s, 200)
        self.assertNotIn("YKSITYINEN-TULKINTA", self.yks[-1])
        self.assertNotIn("TIIVISTELMIEN OSIO", self.yks[-1])
        self.assertEqual(v["tuntemattomia_lahteita"], 1)

    def test_yksityinen_tila_ei_nae_tyokalutilan_tiivistelmia(self):
        self.viesti(self.paalla, "yksityinen")
        self.assertNotIn("TYOKALU-TULKINTA", self.yks[-1])

    def test_tyokalutila_ei_nae_yksityisia_tiivistelmia(self):
        s, _ = self.viesti(self.paalla, "tyokalu")
        self.assertEqual(s, 200)
        self.assertIn("TYOKALU-TULKINTA", self.claude[-1])
        self.assertNotIn("YKSITYINEN-TULKINTA", self.claude[-1])

    def test_sama_kansio_kummallekin_tilalle_hylataan(self):
        with self.assertRaises(chat.ChatVirhe):
            self.tila(self.tk_tiiv)

    def test_main_kytkin_ja_oletuskansio(self):
        nahty = {}

        class Seis(Exception):
            pass

        def tila(*a, **kw):
            nahty.update(kw)
            raise Seis()
        argv = ["palvele", "--yksityinen-data", "/x/aivo-yksityinen/chat"]
        with mock.patch.object(chat, "Tila", side_effect=tila), mock.patch.object(chat, "lue_tiiviste", return_value="t"):
            with mock.patch.dict(os.environ, {"AIVO_YKSITYISET_TIIVISTELMAT_KAYTOSSA": "1"}):
                with self.assertRaises(Seis):
                    chat.main(argv)
            self.assertEqual(nahty["yks_tiivistelmat"], "/x/aivo-yksityinen/tiivistelmat")
            with mock.patch.dict(os.environ, {"AIVO_YKSITYISET_TIIVISTELMAT_KAYTOSSA": "0"}):
                with self.assertRaises(Seis):
                    chat.main(argv)
            self.assertIsNone(nahty["yks_tiivistelmat"])


if __name__ == "__main__":
    unittest.main()
