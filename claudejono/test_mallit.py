"""Mallien tyonjako: mallin valinta, kulutusmittari, Tonyn varaus ja kayttorajan pysaytys (tehtava 2026-11-03 doc 71)."""
import json
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claudejono import ajuri, kulutus, litellm_ajo, malli, varaus
from claudejono.test_ajuri import Pohja

TZ = kulutus.TZ
# Oikea `claude -p --output-format json` -tuloste (3.11.2026, lyhennetty): kentat ovat samat kuin oikeassa ajossa.
CLAUDE_JSON = json.dumps({
    "type": "result", "subtype": "success", "is_error": False, "result": "ok", "total_cost_usd": 0.0498512, "duration_api_ms": 1400,
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 12124, "cache_read_input_tokens": 13112, "output_tokens": 4},
    "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 2, "outputTokens": 4, "costUSD": 0.0498512}}})


def ajo(kom_lista, teksti="valmis\nTodennettu k\u00e4yt\u00f6ss\u00e4: testiajo", **lisa):
    def suorita(kom, **kw):
        kom_lista.append(kom)
        return subprocess.CompletedProcess(kom, 0, json.dumps({"subtype": "success", "result": teksti, **lisa}), "")
    return suorita


class MallinValinta(unittest.TestCase):
    def test_kentta_voittaa_tyypin(self):
        m = malli.valitse("2026-11-03-01-suunnittelu-x.md", "Malli: sonnet\nteksti")
        self.assertEqual((m.reitti, m.nimi, m.lahde), ("claude", "sonnet", "kentta"))
        m = malli.valitse("2026-11-03-01-x.md", "Malli: openrouter:deepseek-flash\nTiivista tama.")
        self.assertEqual((m.reitti, m.nimi, m.tunniste), ("openrouter", "deepseek-flash", "openrouter:deepseek-flash"))
        self.assertEqual(malli.valitse("a", "  malli:  OPUS  \n").tunniste, "opus")

    def test_tyyppi_antaa_mallin_ja_oletus_on_sonnet(self):
        self.assertEqual(malli.valitse("2026-11-03-02-arkkitehtuuri-lukko.md", "").tunniste, "opus")
        self.assertEqual(malli.valitse("2026-11-03-03-paivakatsaus-teksti.md", "Tiivista.").tunniste, "openrouter:deepseek-flash")
        m = malli.valitse("2026-11-03-04-korjaa-bugi.md", "")
        self.assertEqual((m.tunniste, m.lahde), ("sonnet", "oletus"))

    def test_halpa_reitti_ei_kaiken_tyokaluja_vaativan_tehtavan_tyyppiin(self):
        m = malli.valitse("2026-11-03-03-tarkkailu-x.md", "Avaa PR claudejono-pr:lla.")
        self.assertEqual(m.tunniste, "sonnet")

    def test_sallitun_listan_ulkopuolinen_malli_hylataan(self):
        for huono in ("gpt-5", "openrouter:posti-zdr", "openrouter:../x", "opus; rm", "openrouter:", "haiku"):
            m = malli.valitse("2026-11-03-04-korjaa.md", f"Malli: {huono}\n")
            self.assertEqual((m.tunniste, m.lahde), ("sonnet", "kentta-hylatty"), huono)

    def test_maininta_tekstin_seassa_ei_laukaise(self):
        self.assertEqual(malli.valitse("a", "kirjoita rivi `Malli: opus` tiedostoon").lahde, "oletus")

    def test_claude_komento_lisaa_mallin_ja_hylkaa_vieraan(self):
        self.assertEqual(ajuri.claude_komento("k", {}, "opus")[-2:], ["--model", "opus"])
        self.assertNotIn("--model", ajuri.claude_komento("k", {}))
        with self.assertRaises(ajuri.JonoVirhe):
            ajuri.claude_komento("k", {}, "gpt-5")


class AvainEiAgentille(unittest.TestCase):
    def test_litellm_avain_ei_valu_lapsen_ymparistoon(self):
        env = {"CLAUDEJONO_LITELLM_KEY": "x", "CLAUDEJONO_LITELLM_URL": "u", "PATH": "/bin", "CLAUDEJONO_AGENTTIEN_OSUUS_PROS": "60"}
        _kom, alaenv, _cwd = ajuri.lapsen_ajo("k", env, "/opt/agentscope", 1, "sonnet")
        self.assertNotIn("CLAUDEJONO_LITELLM_KEY", alaenv)
        self.assertNotIn("CLAUDEJONO_LITELLM_URL", alaenv)
        _kom, alaenv, _cwd = ajuri.lapsen_ajo("k", {**env, "CLAUDEJONO_KAYTTAJA": "claudejono"}, "/opt/agentscope", 1, "sonnet")
        self.assertNotIn("CLAUDEJONO_LITELLM_KEY", alaenv)


class Kulutus(unittest.TestCase):
    def setUp(self):
        self.tila = Path(tempfile.mkdtemp())
        self.nyt = datetime(2026, 11, 4, 8, 0, tzinfo=TZ)   # keskiviikko

    def test_claude_luvut_oikeasta_tulosteesta(self):
        l = kulutus.claude_luvut(CLAUDE_JSON)
        self.assertEqual((l["tokenit_in"], l["tokenit_out"], l["cache_luku"], l["cache_kirjoitus"]), (2, 4, 13112, 12124))
        self.assertEqual((l["usd"], l["api_ms"], l["malli_todellinen"]), (0.049851, 1400, "claude-sonnet-5-5"))
        self.assertEqual(kulutus.claude_luvut("ei jsonia"), {})
        self.assertEqual(kulutus.claude_luvut("[1]"), {})

    def test_kirjaus_ja_malleittain_ja_rivi(self):
        k = lambda malli_, usd, ok=True, paivia=1: kulutus.kirjaa(
            self.tila, malli_, "t", 1, "tehtava", ok, 5.0, {"usd": usd, "tokenit_in": 10, "tokenit_out": 5}, self.nyt - timedelta(days=paivia))
        k("sonnet", 1.0); k("sonnet", 0.5, ok=False); k("opus", 2.0); k("openrouter:deepseek-flash", 0.01)
        k("sonnet", 4.0, paivia=2)   # maanantai: mukana viikossa, ei eilisessa
        rivi = kulutus.kulutusrivi(self.tila, self.nyt)
        self.assertIn("Mallikulutus eilen: ", rivi)
        eilen, viikko = rivi.split("Viikko:")
        self.assertIn("sonnet 2 ajoa 1.50 USD", eilen)
        self.assertIn("opus 1 ajoa 2.00 USD", eilen)
        self.assertIn("openrouter:deepseek-flash 1 ajoa 0.01 USD", eilen)
        self.assertIn("sonnet 3 ajoa 5.50 USD", viikko)
        m = kulutus.malleittain(kulutus.lue(self.tila))
        self.assertEqual((m["sonnet"]["ajoja"], m["sonnet"]["epaonnistui"], m["sonnet"]["tokenit_in"]), (3, 1, 30))

    def test_tyhja_loki_ei_kaada(self):
        self.assertIn("ei ajoja", kulutus.kulutusrivi(self.tila, self.nyt))
        (self.tila / kulutus.KULUTUS).write_text("rikki\n{\"aika\": 1}\n")
        self.assertEqual(kulutus.lue(self.tila), [])

    def test_laatu_malleittain_ja_siirtoehdotus(self):
        for malli_, otos, virheet in (("sonnet", 20, 2), ("openrouter:deepseek-flash", 20, 8), ("opus", 5, 0)):
            self.assertTrue(kulutus.kirjaa_laatu(self.tila, malli_, otos, virheet, self.nyt))
        p = kulutus.virheprosentit(self.tila)
        self.assertEqual(p["sonnet"], (20, 2, 10.0))
        self.assertIn("openrouter:deepseek-flash virheprosentti 40.0 %", kulutus.siirtoehdotus(self.tila))
        self.assertFalse(kulutus.kirjaa_laatu(self.tila, "x", 3, 5))   # virheita > otos
        self.assertFalse(kulutus.kirjaa_laatu(self.tila, "x", 0, 0))

    def test_ei_ehdotusta_pienella_otoksella_tai_pienella_erolla(self):
        kulutus.kirjaa_laatu(self.tila, "sonnet", 20, 2, self.nyt)
        kulutus.kirjaa_laatu(self.tila, "openrouter:deepseek-flash", 5, 5, self.nyt)
        self.assertIsNone(kulutus.siirtoehdotus(self.tila))
        uusi = Path(tempfile.mkdtemp())
        kulutus.kirjaa_laatu(uusi, "sonnet", 20, 2, self.nyt)
        kulutus.kirjaa_laatu(uusi, "openrouter:deepseek-flash", 20, 3, self.nyt)   # 15 % vs 10 %: ero alle 15 prosenttiyksikkoa
        self.assertIsNone(kulutus.siirtoehdotus(uusi))


class TononVaraus(unittest.TestCase):
    def setUp(self):
        self.tila = Path(tempfile.mkdtemp())
        self.ma = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)   # maanantai
        self.nyt = self.ma + timedelta(days=2)

    def claude_kulutus(self, usd, aika):
        kulutus.kirjaa(self.tila, "sonnet", "t", 1, "tehtava", True, 1.0, {"usd": usd}, aika)

    def kalibroi(self, pros, aika):
        self.assertTrue(varaus.kirjaa_kalibrointi(self.tila, pros, aika))

    def test_osuus_oletus_60_ja_yksi_asetus(self):
        self.assertEqual(varaus.osuus_pros({}), 60.0)
        self.assertEqual(varaus.osuus_pros({"CLAUDEJONO_AGENTTIEN_OSUUS_PROS": "40"}), 40.0)
        for huono in ("x", "nan", "-5", "500", ""):
            self.assertIn(varaus.osuus_pros({"CLAUDEJONO_AGENTTIEN_OSUUS_PROS": huono}), (60.0, 0.0, 100.0), huono)

    def test_ilman_kalibrointia_ei_hidastusta_mutta_kysytaan(self):
        a = varaus.arvio(self.tila, {}, self.nyt)
        self.assertEqual((a["tila"], a["agentit_pros"], a["kaistoja"]), ("ei_kalibroitu", None, 1))
        rivi, kysymys = varaus.aamukooste_osa(self.tila, {}, self.nyt)
        self.assertIn("ei kalibroitu", rivi)
        self.assertIn("T\u00e4ll\u00e4 viikolla", kysymys)

    def test_kalibrointi_antaa_arvion_ja_hidastaa_ja_pysayttaa(self):
        env = {"CLAUDEJONO_KAISTOJA": "3"}
        self.kalibroi(10, self.ma)
        self.claude_kulutus(10.0, self.ma + timedelta(hours=5))
        self.kalibroi(20, self.ma + timedelta(days=1))          # 10 % / 10 USD = 1 % per USD
        a = varaus.arvio(self.tila, env, self.ma + timedelta(days=1, hours=1))
        self.assertEqual((a["tila"], a["agentit_pros"], a["kaistoja"]), ("ok", 10.0, 3))
        self.claude_kulutus(40.0, self.ma + timedelta(days=1, hours=2))   # yhteensa 50 USD -> 50 % >= 48 % (80 % rajasta 60)
        a = varaus.arvio(self.tila, env, self.nyt)
        self.assertEqual((a["tila"], a["agentit_pros"], a["kaistoja"]), ("hidasta", 50.0, 1))
        self.claude_kulutus(15.0, self.ma + timedelta(days=1, hours=3))   # 65 % >= 60 %
        a = varaus.arvio(self.tila, env, self.nyt)
        self.assertEqual((a["tila"], a["kaistoja"]), ("pysayta", 0))
        self.assertIsNotNone(varaus.claude_estetty(self.tila, env, 1, self.nyt))

    def test_raja_muuttuu_yhdesta_asetuksesta(self):
        self.kalibroi(0, self.ma); self.claude_kulutus(10.0, self.ma + timedelta(hours=1)); self.kalibroi(10, self.ma + timedelta(days=1))
        self.claude_kulutus(30.0, self.ma + timedelta(days=1, hours=1))   # 40 %
        self.assertEqual(varaus.arvio(self.tila, {}, self.nyt)["tila"], "ok")
        self.assertEqual(varaus.arvio(self.tila, {"CLAUDEJONO_AGENTTIEN_OSUUS_PROS": "40"}, self.nyt)["tila"], "pysayta")

    def test_openrouter_kulutus_ei_laske_tilauksen_osuutta(self):
        self.kalibroi(0, self.ma); self.claude_kulutus(10.0, self.ma + timedelta(hours=1)); self.kalibroi(10, self.ma + timedelta(days=1))
        kulutus.kirjaa(self.tila, "openrouter:deepseek-flash", "t", 1, "tehtava", True, 1.0, {"usd": 500.0}, self.nyt)
        self.assertEqual(varaus.arvio(self.tila, {}, self.nyt)["agentit_pros"], 10.0)

    def test_kokonaiskaytto_yli_rajan_pysayttaa(self):
        self.kalibroi(93, self.nyt - timedelta(hours=2))
        a = varaus.arvio(self.tila, {}, self.nyt)
        self.assertEqual((a["tila"], a["kaistoja"]), ("pysayta", 0))

    def test_viikon_yli_ei_verrata_ja_virheelliset_luvut_hylataan(self):
        for huono in (-1, 101, True, "x", None):
            self.assertFalse(varaus.kirjaa_kalibrointi(self.tila, huono))
        self.kalibroi(80, self.ma - timedelta(days=1)); self.claude_kulutus(10.0, self.ma - timedelta(hours=10))
        self.kalibroi(5, self.ma + timedelta(hours=2))   # uusi viikko: nollautuminen, ei kelpaa laskentaan
        self.assertEqual(varaus.arvio(self.tila, {}, self.nyt)["tila"], "ei_kalibroitu")

    def test_ilmoitus_vain_tilan_vaihtuessa(self):
        viestit = []
        a = {"tila": "hidasta", "syy": "s"}
        self.assertTrue(varaus.ilmoita_muutos(self.tila, a, viestit.append, 1.0))
        self.assertFalse(varaus.ilmoita_muutos(self.tila, a, viestit.append, 2.0))
        self.assertTrue(varaus.ilmoita_muutos(self.tila, {"tila": "pysayta", "syy": "s"}, viestit.append, 3.0))
        self.assertTrue(varaus.ilmoita_muutos(self.tila, {"tila": "ok", "syy": "s"}, viestit.append, 4.0))
        self.assertEqual(len(viestit), 3)

    def test_raja_tauko_ja_yritysten_lasku(self):
        self.assertEqual(varaus.raja_tauolla(self.tila, 1000.0), 0)
        tauko, n = varaus.kirjaa_raja(self.tila, 1, "t.md", {}, 1000.0)
        self.assertEqual((tauko, n), (3600, 1))
        self.assertEqual(varaus.raja_tauolla(self.tila, 1500.0), 3100)
        self.assertEqual(varaus.raja_tauolla(self.tila, 5000.0), 0)
        self.assertEqual(varaus.kirjaa_raja(self.tila, 1, "t.md", {"CLAUDEJONO_RAJA_TAUKO_S": "x"}, 1.0)[1], 2)


class ViikonVaihto(unittest.TestCase):
    """Tonyn viikkoraja nollautuu lauantaina 21.59 (Helsinki), ei maanantaina."""
    def test_la_2158_on_viela_vanhaa_viikkoa_ja_la_2200_uutta(self):
        vanha = datetime(2026, 10, 31, 21, 58, tzinfo=TZ)
        uusi = datetime(2026, 10, 31, 22, 0, tzinfo=TZ)
        self.assertEqual(kulutus.viikon_alku(vanha), datetime(2026, 10, 24, 21, 59, tzinfo=TZ))
        self.assertEqual(kulutus.viikon_alku(uusi), datetime(2026, 10, 31, 21, 59, tzinfo=TZ))
        self.assertEqual(kulutus.viikon_alku(datetime(2026, 10, 31, 21, 59, tzinfo=TZ)), datetime(2026, 10, 31, 21, 59, tzinfo=TZ))

    def test_maanantai_ei_vaihda_viikkoa(self):
        ma = datetime(2026, 11, 2, 0, 5, tzinfo=TZ)
        self.assertEqual(kulutus.viikon_alku(ma), datetime(2026, 10, 31, 21, 59, tzinfo=TZ))
        self.assertEqual(kulutus.seuraava_vaihto(ma), datetime(2026, 11, 7, 21, 59, tzinfo=TZ))

    def test_vaihto_asetuksesta_ja_virheellinen_asetus_antaa_oletuksen(self):
        env = {"CLAUDEJONO_VIIKKO_VAIHTUU": "ma 00:00"}
        self.assertEqual(kulutus.viikon_alku(datetime(2026, 11, 4, 12, 0, tzinfo=TZ), env), datetime(2026, 11, 2, 0, 0, tzinfo=TZ))
        for huono in ("", "x", "la 25:00", "la 21:99", "lauantai 21:59"):
            self.assertEqual(kulutus.viikon_vaihto({"CLAUDEJONO_VIIKKO_VAIHTUU": huono}), (5, 21, 59), huono)
        self.assertEqual(kulutus.viikon_vaihto({"CLAUDEJONO_VIIKKO_VAIHTUU": "LA 21.59"}), (5, 21, 59))

    def test_aika_muunnetaan_helsingin_aikaan(self):
        utc = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)   # 22.00 Helsingissa (EET, kesaaika loppui 25.10.)
        self.assertEqual(kulutus.viikon_alku(utc), datetime(2026, 10, 31, 21, 59, tzinfo=TZ))

    def test_kulutus_la_2158_kuuluu_vanhalle_viikolle(self):
        tila = Path(tempfile.mkdtemp())
        kulutus.kirjaa(tila, "sonnet", "t", 1, "tehtava", True, 1.0, {"usd": 5.0}, datetime(2026, 10, 31, 21, 58, tzinfo=TZ))
        kulutus.kirjaa(tila, "sonnet", "t", 1, "tehtava", True, 1.0, {"usd": 2.0}, datetime(2026, 10, 31, 22, 0, tzinfo=TZ))
        nyt = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)
        self.assertIn("2.00 USD", kulutus.kulutusrivi(tila, nyt).split("Viikko:")[1])
        self.assertNotIn("7.00", kulutus.kulutusrivi(tila, nyt))

    def test_ilmoitukset_eri_viikoilta_ei_verrata_la_rajan_yli(self):
        tila = Path(tempfile.mkdtemp())
        # 37 % (vanha viikko, la 21.58) ja 5 % (uusi viikko, la 22.00): jos viikko vaihtuisi maanantaina, tama olisi ennen "samaa viikkoa"
        varaus.kirjaa_kalibrointi(tila, 90, datetime(2026, 10, 31, 21, 58, tzinfo=TZ))
        varaus.kirjaa_kalibrointi(tila, 5, datetime(2026, 10, 31, 22, 0, tzinfo=TZ))
        kulutus.kirjaa(tila, "sonnet", "t", 1, "tehtava", True, 1.0, {"usd": 50.0}, datetime(2026, 10, 31, 21, 59, 30, tzinfo=TZ))
        a = varaus.arvio(tila, {}, datetime(2026, 11, 2, 9, 0, tzinfo=TZ))
        self.assertEqual(a["tila"], "ei_kalibroitu")   # ei paria samalta viikolta, vanhan viikon 90 % ei pysayta uutta viikkoa


class KayttorajaViikonVaihtoon(unittest.TestCase):
    def setUp(self):
        self.tila = Path(tempfile.mkdtemp())

    def test_viikkorajavirheen_tauko_kestaa_viikon_vaihtoon(self):
        nyt = datetime(2026, 11, 3, 16, 11, tzinfo=TZ)
        tauko, n = varaus.kirjaa_raja(self.tila, 1, "t.md", {}, nyt.timestamp(), viikkoraja=True)
        self.assertEqual(n, 1)
        self.assertEqual(tauko, int((datetime(2026, 11, 7, 21, 59, tzinfo=TZ) - nyt).total_seconds()))
        self.assertGreater(varaus.raja_tauolla(self.tila, nyt.timestamp() + 3 * 86400), 0)          # sunnuntaina yha tauolla
        self.assertEqual(varaus.raja_tauolla(self.tila, datetime(2026, 11, 7, 22, 0, tzinfo=TZ).timestamp()), 0)   # vaihdon jalkeen vapaa

    def test_ohimeneva_virhe_pysyy_tunnin_taukona(self):
        tauko, _ = varaus.kirjaa_raja(self.tila, 1, "t.md", {}, 1000.0)
        self.assertEqual(tauko, 3600)
        self.assertTrue(varaus.on_viikkoraja("Claude usage limit reached"))
        self.assertFalse(varaus.on_viikkoraja("API overloaded 529"))

    def test_claude_estetty_viikkorajan_tauon_aikana_ja_rivi_aamukoosteeseen(self):
        nyt = datetime(2026, 11, 3, 16, 11, tzinfo=TZ)
        varaus.kirjaa_raja(self.tila, 1, "t.md", {}, nyt.timestamp(), viikkoraja=True)
        self.assertIn("kayttorajavirhe", varaus.claude_estetty(self.tila, {}, 1, nyt, nyt.timestamp() + 86400))
        rivi, _ = varaus.aamukooste_osa(self.tila, {}, nyt)
        self.assertIn("k\u00e4ytt\u00f6raja t\u00e4ynn\u00e4", rivi)
        self.assertIn("07.11. klo 21.59", rivi)

    def test_jarru_arvio_ylittaa_rajan_pysayttaa_uudella_viikkoalulla(self):
        # Kalibrointi 3.11. klo 16.11: 37 %, ja uusi viikko alkoi la 31.10. 21.59
        varaus.kirjaa_kalibrointi(self.tila, 0, datetime(2026, 10, 31, 22, 0, tzinfo=TZ))
        varaus.kirjaa_kalibrointi(self.tila, 37, datetime(2026, 11, 3, 16, 11, tzinfo=TZ))
        kulutus.kirjaa(self.tila, "sonnet", "t", 1, "tehtava", True, 1.0, {"usd": 37.0}, datetime(2026, 11, 1, 12, 0, tzinfo=TZ))
        kulutus.kirjaa(self.tila, "sonnet", "t", 1, "tehtava", True, 1.0, {"usd": 30.0}, datetime(2026, 11, 3, 17, 0, tzinfo=TZ))
        a = varaus.arvio(self.tila, {}, datetime(2026, 11, 3, 18, 0, tzinfo=TZ))   # 1 %/USD * 67 USD = 67 % >= 60 %
        self.assertEqual((a["tila"], a["agentit_pros"], a["kaistoja"]), ("pysayta", 67.0, 0))

    def test_kalibrointi_voidaan_kirjata_menneelle_ajalle(self):
        self.assertEqual(varaus.main(["kalibroi", "37", "--aika", "2026-11-03T16:11", "--tila", str(self.tila)]), 0)
        r = kulutus.lue(self.tila, tiedosto=varaus.KALIBROINTI)[0]
        self.assertEqual((r["pros"], r["aika"]), (37.0, "2026-11-03T16:11:00+02:00"))
        self.assertEqual(varaus.main(["kalibroi", "37", "--aika", "huono", "--tila", str(self.tila)]), 2)


class AjuriReitit(Pohja):
    ENV = {"CLAUDEJONO_ITSESUUNNITTELU": "0"}

    def test_claude_reitti_valitsee_mallin_kirjaa_kulutuksen_ja_mallin_tulokseen(self):
        self.lisaa_tehtava("2026-11-03-01-x.md", "Malli: opus\ntee")
        self.lisaa_tehtava("2026-11-03-02-y.md", "tee")
        komennot = []
        suorita = ajo(komennot, usage={"input_tokens": 3, "output_tokens": 4}, total_cost_usd=0.5)
        self.assertEqual(ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=suorita), "claude-jono: 2026-11-03-01-x.md VALMIS")
        self.assertEqual(ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=suorita), "claude-jono: 2026-11-03-02-y.md VALMIS")
        self.assertEqual([k[k.index("--model") + 1] for k in komennot], ["opus", "sonnet"])
        rivit = kulutus.lue(self.tmp)
        self.assertEqual([(r["malli"], r["tehtava"], r["tokenit_in"], r["usd"]) for r in rivit],
                         [("opus", "2026-11-03-01-x.md", 3, 0.5), ("sonnet", "2026-11-03-02-y.md", 3, 0.5)])
        self.assertIn("- Malli: opus\n", (self.repo / ajuri.JONO / "tulokset" / "2026-11-03-01-x.md").read_text())

    def test_openrouter_reitti_ei_kayta_claudea(self):
        self.lisaa_tehtava("2026-11-03-01-x.md", "Malli: openrouter:deepseek-flash\nTiivista.")
        kutsut = []

        def litellm(alias, teksti, env):
            kutsut.append(alias)
            return True, "Tiivistelma.", {"tokenit_in": 50, "tokenit_out": 9, "usd": 0.0004}
        def ei_saa(*a, **kw):
            raise AssertionError("claudea ei pida kutsua")
        v = ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=ei_saa, litellm=litellm)
        self.assertEqual(v, "claude-jono: 2026-11-03-01-x.md VALMIS")
        self.assertEqual(kutsut, ["deepseek-flash"])
        r = kulutus.lue(self.tmp)[0]
        self.assertEqual((r["malli"], r["tokenit_out"], r["usd"]), ("openrouter:deepseek-flash", 9, 0.0004))
        teksti = (self.repo / ajuri.JONO / "tulokset" / "2026-11-03-01-x.md").read_text()
        self.assertIn("- Malli: openrouter:deepseek-flash", teksti)
        self.assertIn("Tiivistelma.", teksti)

    def test_openrouter_ilman_avainta_jaa_jonoon_eika_jumita_muita(self):
        self.lisaa_tehtava("2026-11-03-01-x.md", "Malli: openrouter:deepseek-flash\nTiivista.")
        self.lisaa_tehtava("2026-11-03-02-y.md", "tee")
        komennot = []
        v = ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=ajo(komennot))
        self.assertEqual(v, "claude-jono: 2026-11-03-02-y.md VALMIS")
        v = ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=ajo(komennot))
        self.assertTrue(v.startswith("jono odottaa"), v)
        self.assertFalse((self.repo / ajuri.JONO / "tulokset" / "2026-11-03-01-x.md").exists())

    def test_varaus_pysayttaa_claude_mutta_ei_openrouteria(self):
        self.lisaa_tehtava("2026-11-03-01-x.md", "tee")
        self.lisaa_tehtava("2026-11-03-02-y.md", "Malli: openrouter:qwen-coder\nluokittele")
        nyt = datetime.now(TZ)
        ma = kulutus.viikon_alku(nyt)
        # Kalibrointi: 1 % / USD, agenttien kulutus 70 USD -> 70 % >= 60 %
        kulutus.kirjaa(self.tmp, "sonnet", "a", 1, "tehtava", True, 1.0, {"usd": 70.0}, ma + timedelta(seconds=2))
        (self.tmp / varaus.KALIBROINTI).write_text(
            json.dumps({"aika": (ma + timedelta(seconds=1)).isoformat(), "pros": 0}) + "\n"
            + json.dumps({"aika": (nyt - timedelta(seconds=1)).isoformat(), "pros": 70}) + "\n")
        komennot = []
        v = ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=ajo(komennot),
                      litellm=lambda a, t, e: (True, "luokiteltu\nTodennettu k\u00e4yt\u00f6ss\u00e4: testiajo", {}))
        self.assertEqual(v, "claude-jono: 2026-11-03-02-y.md VALMIS")
        self.assertEqual(komennot, [])
        v = ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=ajo(komennot))
        self.assertIn("jono odottaa", v)
        self.assertEqual(komennot, [])
        self.assertFalse((self.repo / ajuri.JONO / "tulokset" / "2026-11-03-01-x.md").exists())

    def test_kayttorajavirhe_pysayttaa_kaistat_eika_uusi_silmukassa(self):
        self.lisaa_tehtava("2026-11-03-01-x.md", "tee")
        kutsut = []

        def raja(kom, **kw):
            kutsut.append(1)
            return subprocess.CompletedProcess(kom, 1, json.dumps({"subtype": "error_during_execution", "is_error": True,
                                                                     "result": "Claude usage limit reached"}), "")
        viestit = []
        v = ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=raja, laheta=viestit.append)
        self.assertIn("KAYTTORAJA", v)
        self.assertFalse((self.repo / ajuri.JONO / "tulokset" / "2026-11-03-01-x.md").exists())   # tehtava jaa jonoon
        self.assertTrue(any("kayttoraja" in m for m in viestit))
        for _ in range(5):   # taukon aikana ei yhtaan uutta Claude-kutsua
            v = ajuri.aja(self.repo, self.tmp, env=dict(self.ENV), suorita=raja)
            self.assertIn("jono odottaa", v)
        self.assertEqual(len(kutsut), 1)
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tmp), [])

    def test_toistuva_rajaosuma_kirjataan_lopulta_epaonnistuneeksi(self):
        self.lisaa_tehtava("2026-11-03-01-x.md", "tee")

        def raja(kom, **kw):
            return subprocess.CompletedProcess(kom, 1, json.dumps({"subtype": "error", "is_error": True, "result": "rate limit"}), "")
        env = {**self.ENV, "CLAUDEJONO_RAJA_TAUKO_S": "60"}
        tulos = self.repo / ajuri.JONO / "tulokset" / "2026-11-03-01-x.md"
        for n in range(varaus.RAJA_YRITYKSIA):
            (self.tmp / varaus.RAJA_TILA).unlink(missing_ok=True)   # simuloi tauon kulumista
            ajuri.aja(self.repo, self.tmp, env=dict(env), suorita=raja)
            self.assertEqual(tulos.exists(), n == varaus.RAJA_YRITYKSIA - 1)
        self.assertIn("EPAONNISTUI", tulos.read_text())

    def test_suunnittelu_ajetaan_opuksella(self):
        komennot = []
        ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_ITSESUUNNITTELU": "1"}, suorita=ajo(komennot, '{"tehtavat": [], "tonylle": ""}'))
        self.assertEqual(komennot[0][komennot[0].index("--model") + 1], "opus")
        self.assertEqual(kulutus.lue(self.tmp)[0]["laji"], "suunnittelu")


class LiteLLM(unittest.TestCase):
    class Otsakkeet(dict):
        pass

    def vastaus(self, teksti="hei", syy="stop", kustannus="0.0003"):
        def laheta(url, avain, payload):
            self.pyynto = (url, avain, payload)
            return ({"choices": [{"message": {"content": teksti}, "finish_reason": syy}],
                     "usage": {"prompt_tokens": 11, "completion_tokens": 7}}, {"x-litellm-response-cost": kustannus})
        return laheta

    def test_onnistunut_kutsu_antaa_luvut_ja_ei_tyokaluja(self):
        env = {"CLAUDEJONO_LITELLM_KEY": "avain-testi", "CLAUDEJONO_LITELLM_URL": "http://litellm.test:4000/v1"}
        ok, teksti, luvut = litellm_ajo.aja("deepseek-flash", "Tiivista", env, self.vastaus())
        self.assertEqual((ok, teksti, luvut), (True, "hei", {"tokenit_in": 11, "tokenit_out": 7, "usd": 0.0003}))
        url, _avain, payload = self.pyynto
        self.assertEqual(url, "http://litellm.test:4000/v1/chat/completions")
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertNotIn("tools", payload)

    def test_virheet_ovat_tyyppeja_eika_vuoda_avainta(self):
        self.assertEqual(litellm_ajo.aja("a", "t", {})[:2], (False, "litellm_asetus: avainta ei ole asetettu"))
        env = {"CLAUDEJONO_LITELLM_KEY": "salainen-arvo", "CLAUDEJONO_LITELLM_URL": "http://litellm.test:4000/v1"}

        def http_virhe(*a):
            raise RuntimeError("litellm_http_429")
        ok, teksti, _ = litellm_ajo.aja("a", "t", env, http_virhe)
        self.assertEqual((ok, teksti), (False, "litellm_http_429"))
        ok, teksti, _ = litellm_ajo.aja("a", "t", env, self.vastaus(syy="length"))
        self.assertEqual((ok, teksti), (False, "litellm_vastaus_katkaistu"))
        ok, teksti, _ = litellm_ajo.aja("a", "t", env, self.vastaus(teksti="  "))
        self.assertEqual((ok, teksti), (False, "litellm_vastaus_tyhja"))
        self.assertNotIn("salainen-arvo", teksti)
        self.assertEqual(litellm_ajo.aja("a", "t", {**env, "CLAUDEJONO_LITELLM_URL": "file:///x"})[1], "litellm_asetus: osoite ei kelpaa")


class Aamukooste(Pohja):
    def test_koosteen_kulutusrivi_nayttaa_mallit_ja_varauksen(self):
        from claudejono import aamukooste
        nyt = datetime(2026, 11, 4, 8, 0, tzinfo=TZ)
        kulutus.kirjaa(self.tmp, "sonnet", "t", 1, "tehtava", True, 1.0, {"usd": 1.25}, nyt - timedelta(days=1, hours=2))
        kulutus.kirjaa(self.tmp, "openrouter:deepseek-flash", "t", 1, "tehtava", True, 1.0, {"usd": 0.002}, nyt - timedelta(days=1, hours=3))
        t = aamukooste.keraa(str(self.repo), str(self.tmp / "yojono"), nyt, hae=lambda url: [], yksikot=lambda: [])
        rivi = aamukooste.rivit(t)[6]
        self.assertIn("Mallikulutus eilen: ", rivi)
        self.assertIn("sonnet 1 ajoa 1.25 USD", rivi)
        self.assertIn("Tonyn varaus:", rivi)
        self.assertIn("T\u00e4ll\u00e4 viikolla", aamukooste.rivit(t)[0])


if __name__ == "__main__":
    unittest.main()
