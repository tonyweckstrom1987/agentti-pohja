"""Paattelyn ylaraja (reasoning.max_tokens) ja yksi uusinta ilman paattelya, kun vastaus katkeaa ennen tekstia.
Stdlib unittest. Ei verkkoa, ei mallikutsuja, ei oikeita avaimia: kuljetin on feikki ja kulukirjanpito tilapainen."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from aivo import chat, mallivertailu as mv, openrouter_kulut as ok, openrouter_zdr, salasana_apu, taustaajo as ta
from aivo import tiivista_yksityinen as ty, yhdistelma, yksityinen_or as yo

UUSI = "qwen/qwen3.8-2.4t-a95b"            # paattely pakollinen, tasot xhigh/medium/low (oletus xhigh)
QWEN35 = "qwen/qwen3.5-397b-a17b"          # paattely valinnainen, tasoja ei kerrota
Q27 = "qwen/qwen3.8-27b"                   # paattely valinnainen, taso "none" tuettu
R1 = "deepseek/deepseek-r1-0528"           # paattely pakollinen, tasoja ei kerrota
MAVERICK = "meta-llama/llama-4-maverick"   # ei reasoning-parametria
VALINNAISET = ("deepseek/deepseek-chat-v3.1", "mistralai/mistral-small-2603")   # tukevat, mutta eivat paattele oletuksena
AVAIN = "sk-testi-" + "x" * 30             # keksitty, ei oikea avain
NYT = datetime(2026, 11, 26, 19, 43, tzinfo=ok.TZ)
H = [{"rooli": "tony", "teksti": "Mit\u00e4 tied\u00e4t minusta?"}]


def vastaus(teksti="Hei!", finish="stop", kulu=0.001):
    return {"choices": [{"message": {"content": teksti}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": kulu}}


TYHJA = vastaus("", "length", 0.0834)       # loki 26.11.: koko raja paattelyyn, ei tekstia


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
        self.rungot = []
        self.err = io.StringIO()

    def avaa(self, *vastaukset):
        jono = list(vastaukset)

        def avaa(pyynto, timeout=None):
            self.rungot.append(json.loads(pyynto.data))
            return FeikkiVastaus(jono.pop(0) if len(jono) > 1 else jono[0])
        return avaa

    def yhteiset(self, *vastaukset):
        return dict(avain_tiedosto=str(self.avain), kulutila=str(self.kulut), avaa=self.avaa(*vastaukset),
                    nyt=lambda: NYT)

    def kysy(self, malli, *vastaukset, **kw):
        with contextlib.redirect_stderr(self.err):
            return yo.kysy_openrouterilta("muisti", H, "ohje", malli=malli, **self.yhteiset(*vastaukset), **kw)


class AsetusTesti(unittest.TestCase):
    def test_oletus(self):
        self.assertEqual(yo.PAATTELY_MAX_OLETUS, 2_000)
        for arvo in (None, "", "  "):
            self.assertEqual(yo.tarkista_paattely_max(arvo), 2_000)

    def test_asetus_ja_rajat(self):
        self.assertEqual(yo.tarkista_paattely_max("0"), 0)
        self.assertEqual(yo.tarkista_paattely_max(" 6000 "), 6_000)
        self.assertEqual(yo.tarkista_paattely_max(1500), 1_500)

    def test_virheellinen_estaa(self):
        for arvo in ("-1", "6001", "abc", "2e3", "1.5", True):
            with self.subTest(arvo=arvo), self.assertRaises(chat.ChatVirhe):
                yo.tarkista_paattely_max(arvo)

    def test_tila_ja_valitsin(self):
        with tempfile.TemporaryDirectory() as d:
            def tila(**kw):
                return chat.Tila(salasana_apu.tiiviste("oikea-salasana-123"), d, d, ["localhost"], yks_data=d + "/y",
                                 taustapaa="openrouter", or_malli=UUSI, **kw)
            self.assertEqual(tila().paattely_max, 2_000)
            self.assertEqual(tila(paattely_max="0").paattely_max, 0)
            with self.assertRaises(chat.ChatVirhe):
                tila(paattely_max="7000")
        lahde = Path(chat.__file__).read_text(encoding="utf-8")
        self.assertIn('"--yksityinen-paattely-max", default=os.environ.get("AIVO_YKSITYINEN_PAATTELY_MAX")', lahde)
        self.assertIn("paattely_max=a.yksityinen_paattely_max", lahde)
        # itsetarkistus saa chatin todellisen kutsumaaran (uusinta mukaan lukien)
        self.assertIn('getattr(vastaus, "kutsuja", 1)', lahde)

    def test_tila_valittaa_asetuksen_tarkistukselle(self):
        with tempfile.TemporaryDirectory() as d:
            tila = chat.Tila(salasana_apu.tiiviste("oikea-salasana-123"), d, d, ["localhost"], yks_data=d + "/y",
                             taustapaa="openrouter", or_malli=UUSI, itsetarkistus="paalla", paattely_max="1200")
            with mock.patch.object(yo, "tarkista_vastaus", lambda *a, **kw: kw):
                kw = tila.tarkista_yks("m", H, "o", "vastaus", UUSI, 1)
        self.assertEqual(kw["paattely_max"], 1_200)


class RajaTesti(unittest.TestCase):
    def test_tukitaulu_vain_sallittuja_malleja(self):
        for m in yo.PAATTELY_TUKI:
            self.assertTrue(openrouter_zdr.avoin_malli(m), m)
        self.assertNotIn(MAVERICK, yo.PAATTELY_TUKI)

    def test_raja_aina_alle_max_tokens(self):
        for max_tokens in (1, 2, 200, 800, 1_500, 2_000, 4_000, 8_000):
            for asetus in (1, 2_000, 6_000):
                raja = yo.paattelyraja(UUSI, max_tokens, asetus)
                self.assertLess(raja, max_tokens)
                self.assertGreaterEqual(max_tokens - raja, max(1, min(yo.PAATTELY_VASTAUSVARA, max_tokens // 2)))

    def test_raja_arvot(self):
        self.assertEqual(yo.paattelyraja(UUSI, 8_000), 2_000)
        self.assertEqual(yo.paattelyraja(UUSI, 8_000, 6_000), 6_000)       # vastaukselle jaa 2 000
        self.assertEqual(yo.paattelyraja(UUSI, 1_500), 750)                # tarkastaja: puolet
        self.assertEqual(yo.paattelyraja(UUSI, 8_000, 0), 0)
        self.assertEqual(yo.paattelyraja(MAVERICK, 8_000), 0)


class ChatTesti(Pohja):
    def test_oletus_lahettaa_rajan(self):
        teksti = self.kysy(UUSI, vastaus())
        [runko] = self.rungot
        self.assertEqual(runko["max_tokens"], 8_000)
        self.assertEqual(runko["reasoning"], {"max_tokens": 2_000, "exclude": True})
        self.assertLess(runko["reasoning"]["max_tokens"], runko["max_tokens"])
        self.assertEqual(runko["provider"]["zdr"], True)                  # ZDR ennallaan
        self.assertEqual(runko["provider"]["data_collection"], "deny")
        self.assertEqual(teksti, "Hei!")
        self.assertEqual(teksti.kutsuja, 1)
        self.assertIn("vastaus enintaan 8000, paattely enintaan 2000", self.err.getvalue())

    def test_asetus_nolla_ei_parametria(self):
        self.kysy(UUSI, vastaus(), paattely_max=0)
        [runko] = self.rungot
        self.assertNotIn("reasoning", runko)
        self.assertIn("paattely enintaan 0", self.err.getvalue())

    def test_ei_tuettu_malli_ilman_parametria(self):
        self.assertEqual(self.kysy(MAVERICK, vastaus()), "Hei!")
        [runko] = self.rungot
        self.assertNotIn("reasoning", runko)

    def test_uusinta_kerran_pakollinen_malli(self):
        teksti = self.kysy(UUSI, TYHJA, vastaus("Vastaus."), paattely_max=6_000)   # 75 % -> xhigh, uusinta low
        self.assertEqual(len(self.rungot), 2)
        self.assertEqual(self.rungot[1]["reasoning"], {"effort": "low", "exclude": True})
        self.assertEqual(self.rungot[1]["max_tokens"], 8_000)
        self.assertEqual(teksti, "Vastaus.")
        self.assertEqual(teksti.kutsuja, 2)
        loki = self.err.getvalue()
        self.assertIn("uusinta kerran pienemmalla paattelytasolla (reasoning effort=low;", loki)
        self.assertNotIn("Vastaus.", loki)                                # ei sisaltoa journaliin
        self.assertNotIn("Mit\u00e4 tied\u00e4t", loki)

    def test_uusinta_valinnainen_malli(self):
        self.kysy(QWEN35, TYHJA, vastaus())
        self.assertEqual(self.rungot[1]["reasoning"], {"enabled": False})
        self.rungot.clear()
        self.kysy(Q27, TYHJA, vastaus())
        self.assertEqual(self.rungot[1]["reasoning"], {"effort": "none", "exclude": True})

    def test_uusinta_vain_kerran(self):
        with self.assertRaises(yo.VastausKatkesi):
            self.kysy(UUSI, TYHJA, paattely_max=6_000)    # aina tyhja
        self.assertEqual(len(self.rungot), 2)
        self.assertIn("uusintakin katkesi", self.err.getvalue())

    def test_ei_uusintaa_ilman_hyotya(self):
        for malli, kw in ((R1, {}), (MAVERICK, {}), (UUSI, {"paattely_max": 0})):
            with self.subTest(malli=malli, **kw):
                self.rungot.clear()
                with self.assertRaises(yo.VastausKatkesi):
                    self.kysy(malli, TYHJA, **kw)
                self.assertEqual(len(self.rungot), 1)

    def test_kutsuraja_estaa_uusinnan(self):
        with mock.patch.object(yhdistelma, "MAX_KUTSUT_VIESTI", 1), self.assertRaises(yo.VastausKatkesi):
            self.kysy(UUSI, TYHJA, paattely_max=6_000)
        self.assertEqual(len(self.rungot), 1)
        self.assertIn("uusintaa ei tehty (kutsuraja)", self.err.getvalue())

    def test_paivakatto_estaa_uusinnan(self):
        kallis = vastaus("", "length", 50.0)          # yli 10 EUR/vrk katon: seuraava kutsu estetaan
        with self.assertRaises(yhdistelma.Katkaisu):
            self.kysy(UUSI, kallis, vastaus(), paattely_max=6_000)
        self.assertEqual(len(self.rungot), 1)
        self.assertEqual(ok.PAIVAKATTO_EUR, 10)

    def test_osakutsu_ei_uusi(self):
        with contextlib.redirect_stderr(self.err), self.assertRaises(yo.VastausKatkesi):
            yo._kysy_viestit(UUSI, [{"role": "user", "content": "x"}], **self.yhteiset(TYHJA, vastaus()))
        self.assertEqual(len(self.rungot), 1)
        self.assertEqual(self.rungot[0]["reasoning"]["max_tokens"], 2_000)


class TarkistusTesti(Pohja):
    def test_tarkastaja_ja_korjaus_saavat_rajan(self):
        tarkastaja = vastaus(json.dumps({"kohdat": [{"kohta": "x", "syy": "y"}]}))
        with contextlib.redirect_stderr(self.err):
            tulos = yo.tarkista_vastaus("muisti", H, "ohje", "Tony asuu Espoossa vuodesta 2020.", vastaaja=UUSI,
                                        **self.yhteiset(tarkastaja, vastaus("Korjattu.")))
        self.assertEqual(len(self.rungot), 2, tulos.merkinta)
        tark, korj = self.rungot
        self.assertEqual(tark["reasoning"], {"max_tokens": tark["max_tokens"] // 2, "exclude": True})
        self.assertLess(tark["reasoning"]["max_tokens"], tark["max_tokens"])
        self.assertEqual(korj["reasoning"], {"max_tokens": 2_000, "exclude": True})

    def test_tarkastaja_ei_uusi(self):
        with contextlib.redirect_stderr(self.err):
            tulos = yo.tarkista_vastaus("muisti", H, "ohje", "Tony asuu Espoossa vuodesta 2020.", vastaaja=UUSI,
                                        **self.yhteiset(TYHJA))
        self.assertEqual(len(self.rungot), 1, tulos.merkinta)


class OletuksenaPaattelevatTesti(Pohja):
    """reasoning-kentta vain malleille, jotka paattelevat oletuksena (katselmointi 26.11.)."""

    def test_tuki_on_osajoukko_ja_yhteinen_lahde(self):
        self.assertLessEqual(set(yo.PAATTELY_TUKI), yo.OLETUKSENA_PAATTELEVAT)
        self.assertIs(mv.AJATTELEVAT_MALLIT, yo.OLETUKSENA_PAATTELEVAT)
        for m in VALINNAISET:
            self.assertNotIn(m, yo.PAATTELY_TUKI)
            self.assertNotIn(m, yo.OLETUKSENA_PAATTELEVAT)

    def test_valinnainen_malli_ilman_parametria_ja_uusintaa(self):
        for m in VALINNAISET:
            with self.subTest(malli=m):
                if not openrouter_zdr.avoin_malli(m):
                    self.skipTest("%s ei ole AVOIMET_MALLIT-listalla: chat ei voi kutsua sita" % m)
                self.rungot.clear()
                self.assertEqual(self.kysy(m, vastaus()), "Hei!")
                [runko] = self.rungot
                self.assertNotIn("reasoning", runko)
                self.assertEqual(yo.paattelyraja(m, 8_000), 0)
                self.assertIsNone(yo.uusinnan_parametri(m))
                self.rungot.clear()
                with self.assertRaises(yo.VastausKatkesi):
                    self.kysy(m, TYHJA, vastaus())
                self.assertEqual(len(self.rungot), 1)                     # ei uusintaa
                self.assertNotIn("reasoning", self.rungot[0])

    def test_tukitaulu_ei_riita_ilman_oletuspaattelya(self):
        m = "deepseek/deepseek-chat-v3.1"
        with mock.patch.dict(yo.PAATTELY_TUKI, {m: (False, None)}):
            self.assertEqual(yo.paattelyraja(m, 8_000), 0)
            self.assertIsNone(yo.paattelyparametri(m, 2_000))
            self.assertIsNone(yo.uusinnan_parametri(m))


class TaustaJaTiivistysTesti(Pohja):
    def test_taustatyo_saa_rajan_vain_paattelevalle(self):
        for malli, odotus in ((UUSI, {"max_tokens": 2_000, "exclude": True}), (MAVERICK, None)):
            with self.subTest(malli=malli):
                self.rungot.clear()
                with contextlib.redirect_stderr(self.err):
                    yo.kysy_taustatyo(malli, [{"role": "user", "content": "x"}], aikaraja_s=60,
                                      **self.yhteiset(vastaus()))
                [runko] = self.rungot
                self.assertEqual(runko.get("reasoning"), odotus)
                self.assertEqual(runko["max_tokens"], yo.MAX_TOKENIA)
                self.assertEqual(runko["provider"]["zdr"], True)

    def test_taustatyo_asetus_nolla_ja_ei_uusintaa(self):
        with contextlib.redirect_stderr(self.err):
            yo.kysy_taustatyo(UUSI, [{"role": "user", "content": "x"}], aikaraja_s=60, paattely_max=0,
                              **self.yhteiset(vastaus()))
        self.assertNotIn("reasoning", self.rungot[0])
        self.rungot.clear()
        with contextlib.redirect_stderr(self.err), self.assertRaises(yo.VastausKatkesi):
            yo.kysy_taustatyo(UUSI, [{"role": "user", "content": "x"}], aikaraja_s=60, **self.yhteiset(TYHJA, vastaus()))
        self.assertEqual(len(self.rungot), 1)

    def test_taustaajon_main_valittaa_asetuksen(self):
        saadut = []

        def tee_kysyja(avain, **kw):
            saadut.append(kw)
            return lambda *a: "ok"
        env = {"AIVO_OPENROUTER_MALLIT": UUSI, "AIVO_YKSITYINEN_PAATTELY_MAX": "1200"}
        jono, keskustelut = self.d / "jono", self.d / "chat"
        for k in (jono, keskustelut):
            k.mkdir(mode=0o700)
            os.chmod(k, 0o700)
        with mock.patch.object(ta, "tee_kysyja", tee_kysyja), contextlib.redirect_stderr(self.err):
            rc = ta.main(["--jono", str(jono), "--chat", str(self.d / "chat"), "--kuiva"], env=env)
        self.assertEqual(rc, 0, self.err.getvalue())
        self.assertEqual(saadut, [{"paattely_max": 1_200}])
        with mock.patch.object(ta, "tee_kysyja", tee_kysyja), contextlib.redirect_stderr(self.err):
            rc = ta.main(["--jono", str(jono), "--chat", str(self.d / "chat"), "--kuiva"],
                         env={**env, "AIVO_YKSITYINEN_PAATTELY_MAX": "9000"})
        self.assertEqual(rc, 1)
        self.assertEqual(len(saadut), 1)

    def test_taustaajon_kysyja_valittaa_rajan_kutsuun(self):
        kysy = ta.tee_kysyja(str(self.avain), paattely_max=1_000, **{k: v for k, v in self.yhteiset(vastaus()).items()
                                                                      if k != "avain_tiedosto"})
        with contextlib.redirect_stderr(self.err):
            kysy(UUSI, [{"role": "user", "content": "x"}], 60)
        self.assertEqual(self.rungot[0]["reasoning"], {"max_tokens": 1_000, "exclude": True})

    def test_tiivistys_saa_rajan(self):
        kw = {k: v for k, v in self.yhteiset(vastaus("{}")).items() if k != "avain_tiedosto"}
        kysy, _ = ty.tee_or_kysyja(UUSI, str(self.avain), paattely_max="1000", **kw)
        kysy("ohje", "data")
        self.assertEqual(self.rungot[0]["reasoning"], {"max_tokens": 750, "exclude": True})   # 1 500 // 2
        self.assertEqual(self.rungot[0]["max_tokens"], yo.TIIVISTYS_MAX_TOKENIA)
        self.rungot.clear()
        kysy, _ = ty.tee_or_kysyja(MAVERICK, str(self.avain), paattely_max=None, **{**kw, "avaa": self.avaa(vastaus("{}"))})
        kysy("ohje", "data")
        self.assertNotIn("reasoning", self.rungot[0])
        with self.assertRaises(ty.TiivistaVirhe):
            ty.tee_or_kysyja(UUSI, str(self.avain), paattely_max="abc", **kw)

    def test_tiivistyksen_main_lukee_asetuksen(self):
        lahde = Path(ty.__file__).read_text(encoding="utf-8")
        self.assertIn('tee_or_kysyja(a.or_malli, a.or_avain, paattely_max=env.get("AIVO_YKSITYINEN_PAATTELY_MAX"))',
                      lahde)              # main()-reitti: test_yksityinen_tiivistys_or (asetus valittyy)


class EffortTasoTesti(Pohja):
    """Effort-tasoja kayttava pakollisen paattelyn malli (UUSI): OpenRouter muuntaa reasoning.max_tokens-arvon
    tasoksi. Uusinta tasolla low ei auta, jos ensimmainen kutsu oli jo todennakoisesti tasolla low (25 % -> low)."""

    def test_oletusraja_vastaa_tasoa_low_eika_uusita(self):
        with contextlib.suppress(yo.VastausKatkesi):
            self.kysy(UUSI, TYHJA, vastaus("Vastaus."))
        self.assertEqual(len(self.rungot), 1, "uusinta toisti saman pyynnon: %s"
                         % [r.get("reasoning") for r in self.rungot])
        self.assertEqual(self.rungot[0]["reasoning"], {"max_tokens": 2_000, "exclude": True})
        self.assertEqual(yo.todennakoinen_taso(UUSI, 8_000, 2_000), "low")
        loki = self.err.getvalue()
        self.assertIn("uusintaa ei tehty: ensimmainen kutsu oli todennakoisesti jo tasolla low", loki)
        self.assertNotIn("ilman paattelya", loki)

    def test_suuri_raja_uusitaan_tasolla_low(self):
        self.assertEqual(yo.todennakoinen_taso(UUSI, 8_000, 6_000), "xhigh")      # 75 %
        self.assertEqual(yo.todennakoinen_taso(UUSI, 8_000, 4_000), "medium")     # 50 %
        teksti = self.kysy(UUSI, TYHJA, vastaus("Vastaus."), paattely_max=6_000)
        self.assertEqual(len(self.rungot), 2)
        self.assertEqual(self.rungot[1]["reasoning"], {"effort": "low", "exclude": True})
        self.assertEqual(teksti.kutsuja, 2)
        loki = self.err.getvalue()
        self.assertIn("uusinta kerran pienemmalla paattelytasolla (reasoning effort=low; ensimmainen todennakoisesti "
                      "xhigh)", loki)
        self.assertNotIn("ilman paattelya", loki)

    def test_raja_tasojen_valissa(self):
        # low 20 %, medium 50 %: lahin taso; tasapelissa pienempi (uusintaa ei tehda epavarmassa tilanteessa)
        self.assertEqual(yo.todennakoinen_taso(UUSI, 8_000, 2_800), "low")        # 35 %
        self.assertEqual(yo.todennakoinen_taso(UUSI, 8_000, 2_880), "medium")     # 36 %
        self.assertIsNone(yo.todennakoinen_taso(QWEN35, 8_000, 2_000))            # tasoja ei kerrota
        self.assertIsNone(yo.todennakoinen_taso(UUSI, 8_000, 0))

    def test_ilman_paattelya_uusinta_sailyy(self):
        self.kysy(Q27, TYHJA, vastaus())
        self.assertEqual(self.rungot[1]["reasoning"], {"effort": "none", "exclude": True})
        self.assertIn("uusinta kerran ilman paattelya (reasoning effort=none)", self.err.getvalue())
        self.rungot.clear()
        self.kysy(QWEN35, TYHJA, vastaus())
        self.assertEqual(self.rungot[1]["reasoning"], {"enabled": False})


class KehoteLokiTesti(Pohja):
    def test_taustatyo_ja_tiivistys_kirjaavat_kehotteen(self):
        with contextlib.redirect_stderr(self.err):
            yo.kysy_taustatyo(UUSI, [{"role": "user", "content": "salainen sisalto"}], aikaraja_s=60,
                              **self.yhteiset(vastaus()))
        self.assertIn("vastaus enintaan 8000, paattely enintaan 2000", self.err.getvalue())
        self.err = io.StringIO()
        with contextlib.redirect_stderr(self.err):
            yo.kysy_tiivistys("ohje", "salainen sisalto", malli=UUSI, **self.yhteiset(vastaus("{}")))
        loki = self.err.getvalue()
        self.assertIn("vastaus enintaan 1500, paattely enintaan 750", loki)
        self.assertNotIn("salainen sisalto", loki)


class LokiTesti(unittest.TestCase):
    def test_reasoning_tokens_kirjataan(self):
        err = io.StringIO()
        data = {"usage": {"prompt_tokens": 10, "completion_tokens": 2400,
                          "completion_tokens_details": {"reasoning_tokens": 1987}, "cost": 0.01}}
        with contextlib.redirect_stderr(err):
            yo.loki_toteutunut(UUSI, data)
        self.assertIn("reasoning_tokens=1987", err.getvalue())

    def test_puuttuva_tai_vaara_tyyppi_ohitetaan(self):
        for tiedot in (None, {}, {"reasoning_tokens": True}, {"reasoning_tokens": "5"}, "x"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                yo.loki_toteutunut(UUSI, {"usage": {"completion_tokens": 5, "completion_tokens_details": tiedot}})
            self.assertNotIn("reasoning_tokens", err.getvalue())
            self.assertIn("completion_tokens=5", err.getvalue())


if __name__ == "__main__":
    unittest.main()
