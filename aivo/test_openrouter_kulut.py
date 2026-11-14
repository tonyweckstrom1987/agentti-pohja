"""Testit aivo/openrouter_kulut.py:lle. Ei verkkoa, ei tunnuksia; kuljetin on feikki, tila tilapaishakemisto."""
import ast
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from aivo import openrouter_kulut as ok

TZ = ZoneInfo("Europe/Helsinki")
NYT = datetime(2026, 11, 14, 12, 0, tzinfo=TZ)
MSG = [{"role": "user", "content": "x"}]
AIVO = Path(ok.__file__).resolve().parent
# USD, joka ylittaa katon hiukan (10.87 * 0.92 = 10.0004 EUR); tasaraja testataan erikseen tarkalla kertoimella
USD_KATTO = 10.87


class Pohja(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tila = Path(self._t.name)

    def rivi(self, aika, usd=None, malli="m/x", **lisa):
        d = {"aika": aika.isoformat(), "malli": malli, "tehtava": "t"}
        if usd is not None:
            d["usd"] = usd
        d.update(lisa)
        with open(self.tila / ok.KULUT, "a") as f:
            f.write(json.dumps(d) + "\n")


class KatkaisinTesti(Pohja):
    def test_tyhja_sallitaan(self):
        self.assertTrue(ok.saako_kutsua(NYT, self.tila))

    def test_alle_katon_sallitaan(self):
        self.rivi(NYT, USD_KATTO - 0.1)
        self.assertTrue(ok.saako_kutsua(NYT, self.tila))

    def test_tasan_katto_estaa_ja_sentti_alle_sallitaan(self):
        vanha = ok.USD_EUR
        ok.USD_EUR = Decimal("0.5")
        self.addCleanup(setattr, ok, "USD_EUR", vanha)
        self.rivi(NYT, 19.98)
        self.assertEqual(ok.kulut_paivalta(self.tila, NYT), Decimal("9.99"))
        self.assertTrue(ok.saako_kutsua(NYT, self.tila))
        self.rivi(NYT, 0.02)
        self.assertEqual(ok.kulut_paivalta(self.tila, NYT), ok.PAIVAKATTO_EUR)
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_katto_usealla_rivilla_ja_mallilla(self):
        self.rivi(NYT, USD_KATTO / 2, malli="a")
        self.rivi(NYT, USD_KATTO / 2, malli="b")
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_paivan_vaihto_nollaa(self):
        self.rivi(NYT, USD_KATTO * 2)
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))
        self.assertTrue(ok.saako_kutsua(NYT.replace(hour=0, minute=0) + timedelta(days=1), self.tila))

    def test_vuorokausi_on_helsingin_aikaa(self):
        # 22:30 UTC 13.11. = 00:30 14.11. Helsingissa -> kuuluu 14. paivalle
        self.rivi(datetime(2026, 11, 13, 22, 30, tzinfo=ZoneInfo("UTC")), USD_KATTO * 2)
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))
        # 21:30 UTC 13.11. = 23:30 13.11. Helsingissa -> ei kuulu 14. paivalle
        (self.tila / ok.KULUT).unlink()
        self.rivi(datetime(2026, 11, 13, 21, 30, tzinfo=ZoneInfo("UTC")), USD_KATTO * 2)
        self.assertTrue(ok.saako_kutsua(NYT, self.tila))

    def test_eilinen_ei_lasketa(self):
        self.rivi(NYT - timedelta(days=1), USD_KATTO * 2)
        self.assertTrue(ok.saako_kutsua(NYT, self.tila))


class FailClosedTesti(Pohja):
    def test_tuntematon_kulu_ilman_tokeneita_lasketaan(self):
        for _ in range(20):
            self.rivi(NYT)          # 20 x 0.50 USD x 0.92 = 9.20 EUR
        self.assertTrue(ok.saako_kutsua(NYT, self.tila))
        self.rivi(NYT)
        self.rivi(NYT)              # 22 x 0.46 = 10.12 EUR
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_tuntematon_kulu_tokeneista_oletushinnalla(self):
        # 1 Mtok sisaan oletushinnalla 15 USD -> 13.80 EUR > katto
        self.rivi(NYT, tokenit_in=1_000_000, tokenit_out=0)
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_pieni_tokenimaara_pienella_arviolla(self):
        self.rivi(NYT, tokenit_in=1000, tokenit_out=1000)   # 0.09 USD
        self.assertTrue(ok.saako_kutsua(NYT, self.tila))
        self.assertEqual(ok.rivin_usd({"malli": "m", "tokenit_in": 1000, "tokenit_out": 1000}), Decimal("0.09"))

    def test_rikkinainen_tiedosto_estaa(self):
        (self.tila / ok.KULUT).write_text("ei json\n")
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_rikkinainen_rivi_vanhalta_paivalta_estaa_myos(self):
        self.rivi(NYT - timedelta(days=9), 0.01)
        with open(self.tila / ok.KULUT, "a") as f:
            f.write('{"aika": "huono"}\n')
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_virheelliset_arvot_estavat(self):
        for usd in (-1, float("nan"), float("inf"), "5", True):
            with self.subTest(usd=usd):
                (self.tila / ok.KULUT).write_text(json.dumps({"aika": NYT.isoformat(), "malli": "m", "usd": usd}) + "\n")
                self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_aikavyohykkeeton_rivi_tai_nyt_estaa(self):
        (self.tila / ok.KULUT).write_text(json.dumps({"aika": "2026-11-14T12:00:00", "malli": "m", "usd": 0}) + "\n")
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))
        (self.tila / ok.KULUT).unlink()
        self.assertFalse(ok.saako_kutsua(datetime(2026, 11, 14, 12, 0), self.tila))
        self.assertFalse(ok.saako_kutsua("eilen", self.tila))

    def test_tila_puuttuu_estaa(self):
        self.assertFalse(ok.saako_kutsua(NYT, self.tila / "ei-ole"))

    def test_lukukelvoton_tiedosto_estaa(self):
        (self.tila / ok.KULUT).write_bytes(b"\xff\xfe\x00")
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))

    def test_tiedosto_on_hakemisto_estaa(self):
        (self.tila / ok.KULUT).mkdir()
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))


class KirjausTesti(Pohja):
    def test_kirjaa_kulun_ja_mallin(self):
        r = ok.kirjaa("deepseek/x", "t-1", {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0123}}, NYT, self.tila)
        self.assertEqual((r["malli"], r["tehtava"], r["usd"], r["tokenit_in"], r["tokenit_out"]), ("deepseek/x", "t-1", 0.0123, 10, 5))
        self.assertNotIn("kulu_tuntematon", r)
        self.assertEqual(json.loads((self.tila / ok.KULUT).read_text())["aika"], "2026-11-14T12:00:00+02:00")

    def test_puuttuva_kulu_merkitaan_tuntemattomaksi(self):
        r = ok.kirjaa("m", "t", {"usage": {"prompt_tokens": 3, "completion_tokens": 4}}, NYT, self.tila)
        self.assertTrue(r["kulu_tuntematon"])
        self.assertNotIn("usd", r)
        self.assertEqual((r["tokenit_in"], r["tokenit_out"]), (3, 4))

    def test_ei_usagea_tuntematon(self):
        for v in ({}, {"usage": "x"}, {"usage": {"cost": "paljon"}}, None):
            with self.subTest(v=v):
                self.assertTrue(ok.kirjaa("m", "t", v, NYT, self.tila)["kulu_tuntematon"])

    def test_tunniste_puhdistetaan(self):
        r = ok.kirjaa("m", "a b\nc;d" * 20, {}, NYT, self.tila)
        self.assertRegex(r["tehtava"], r"^[A-Za-z0-9._:/-]{1,60}$")

    def test_kirjoitus_epaonnistuu_nostaa(self):
        with self.assertRaises(ok.KulutVirhe):
            ok.kirjaa("m", "t", {}, NYT, self.tila / "ei-ole")

    def test_kirjattu_kulu_nakyy_katkaisimessa(self):
        ok.kirjaa("m", "t", {"usage": {"cost": USD_KATTO}}, NYT, self.tila)
        self.assertFalse(ok.saako_kutsua(NYT, self.tila))


class IlmoitusTesti(Pohja):
    def test_ilmoitus_kerran_vuorokaudessa(self):
        self.rivi(NYT, USD_KATTO * 2)
        viestit = []
        for _ in range(3):
            self.assertFalse(ok.saako_kutsua(NYT, self.tila, lambda t: viestit.append(t) or True))
        self.assertEqual(len(viestit), 1)
        self.assertIn("10", viestit[0])
        tapahtumat = [json.loads(r)["tyyppi"] for r in (self.tila / ok.TAPAHTUMAT).read_text().splitlines()]
        self.assertEqual(tapahtumat, ["katto", "ilmoitus"])

    def test_uusi_vuorokausi_ilmoittaa_uudelleen(self):
        self.rivi(NYT, USD_KATTO * 2)
        self.rivi(NYT + timedelta(days=1), USD_KATTO * 2)
        viestit = []
        for n in (NYT, NYT + timedelta(days=1)):
            ok.saako_kutsua(n, self.tila, lambda t: viestit.append(t) or True)
        self.assertEqual(len(viestit), 2)

    def test_epaonnistunut_ilmoitus_yritetaan_uudelleen(self):
        self.rivi(NYT, USD_KATTO * 2)
        vastaukset = iter([False, True, True])
        kutsut = []
        for _ in range(3):
            ok.saako_kutsua(NYT, self.tila, lambda t: kutsut.append(t) or next(vastaukset))
        self.assertEqual(len(kutsut), 2)   # False -> uusi yritys; True -> ei enaa
        tyypit = [json.loads(r)["tyyppi"] for r in (self.tila / ok.TAPAHTUMAT).read_text().splitlines()]
        self.assertEqual(tyypit, ["katto", "ilmoitus"])

    def test_ilmoituksen_virhe_ei_muuta_tulosta(self):
        self.rivi(NYT, USD_KATTO * 2)

        def rikki(_):
            raise RuntimeError("salainen")
        self.assertFalse(ok.saako_kutsua(NYT, self.tila, rikki))

    def test_ei_ilmoitusta_alle_katon(self):
        viestit = []
        self.assertTrue(ok.saako_kutsua(NYT, self.tila, viestit.append))
        self.assertEqual(viestit, [])
        self.assertFalse((self.tila / ok.TAPAHTUMAT).exists())


class KutsuTesti(Pohja):
    def kutsu(self, laheta, **kw):
        return ok.kutsu("m/x", MSG, laheta, tehtava="t", nyt=lambda: NYT, tila=self.tila, **kw)

    def test_onnistunut_kutsu_kirjataan(self):
        runkot = []

        def laheta(runko):
            runkot.append(runko)
            return {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.01}}
        self.kutsu(laheta)
        self.assertTrue(runkot[0]["provider"]["zdr"])
        self.assertEqual(runkot[0]["provider"]["data_collection"], "deny")
        self.assertEqual(len(ok._lue_rivit(self.tila, ok.KULUT)), 1)

    def test_katto_estaa_eika_laheta(self):
        self.rivi(NYT, USD_KATTO * 2)
        kutsuttu = []
        with self.assertRaises(ok.KutsuEstetty):
            self.kutsu(lambda r: kutsuttu.append(r) or {})
        self.assertEqual(kutsuttu, [])

    def test_rikkinainen_kirjanpito_estaa_kutsun(self):
        (self.tila / ok.KULUT).write_text("rikki\n")
        kutsuttu = []
        with self.assertRaises(ok.KutsuEstetty):
            self.kutsu(lambda r: kutsuttu.append(r) or {})
        self.assertEqual(kutsuttu, [])

    def test_zdr_saanto_ei_ohitu(self):
        kutsuttu = []
        with self.assertRaises(ok.openrouter_zdr.ZdrVirhe):
            self.kutsu(lambda r: kutsuttu.append(r) or {}, zdr=False)
        self.assertEqual(kutsuttu, [])

    def test_kuljetinvirhe_kirjataan_varovasti_ja_nostetaan(self):
        def laheta(_):
            raise OSError("x")
        with self.assertRaises(OSError):
            self.kutsu(laheta)
        r = ok._lue_rivit(self.tila, ok.KULUT)[0]
        self.assertTrue(r["virhe"] and r["kulu_tuntematon"])
        self.assertGreater(ok.kulut_paivalta(self.tila, NYT), 0)

    def test_virheloop_pysahtyy_kattoon(self):
        def laheta(_):
            raise OSError("x")
        for _ in range(60):
            try:
                self.kutsu(laheta)
            except OSError:
                pass
            except ok.KutsuEstetty:
                break
        else:
            self.fail("katkaisin ei pysaytanyt virheloopia")
        self.assertLessEqual(len(ok._lue_rivit(self.tila, ok.KULUT)), 22)


class RakenneTesti(unittest.TestCase):
    def test_katto_on_vakio_eika_ymparistosta(self):
        self.assertEqual(ok.PAIVAKATTO_EUR, 10)
        lahde = (AIVO / "openrouter_kulut.py").read_text()
        self.assertNotIn("os.environ", lahde)
        self.assertNotIn("getenv", lahde)
        self.assertEqual(lahde.count("PAIVAKATTO_EUR ="), 1)

    def test_saako_kutsua_ei_ota_kattoa_parametrina(self):
        import inspect
        for f in (ok.saako_kutsua, ok.kutsu):
            self.assertFalse([p for p in inspect.signature(f).parameters if "katto" in p.lower() or "raja" in p.lower()])

    def test_ei_ohituspolkua_muissa_aivo_moduuleissa(self):
        """Muut aivo-moduulit eivat tee OpenRouter-kutsuja: openrouter.ai tai rakenna_runko vain taman moduulin kautta."""
        for p in sorted(AIVO.glob("*.py")):
            if p.name in ("openrouter_kulut.py", "openrouter_zdr.py") or p.name.startswith("test_"):
                continue
            puu = ast.parse(p.read_text())
            for n in ast.walk(puu):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    self.assertNotIn("openrouter.ai", n.value, p.name)
                if isinstance(n, ast.Attribute) and n.attr == "rakenna_runko":
                    self.fail(p.name + " kutsuu rakenna_runko suoraan")
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    nimet = [a.name for a in n.names] + [getattr(n, "module", "") or ""]
                    self.assertFalse([x for x in nimet if "openrouter_zdr" in x], p.name)

    def test_kutsu_kutsuu_katkaisinta_ennen_lahetysta(self):
        puu = ast.parse((AIVO / "openrouter_kulut.py").read_text())
        f = next(n for n in puu.body if isinstance(n, ast.FunctionDef) and n.name == "kutsu")
        rivit = {}
        for n in ast.walk(f):
            if isinstance(n, ast.Call):
                nimi = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                rivit.setdefault(nimi, n.lineno)
        self.assertLess(rivit["saako_kutsua"], rivit["rakenna_runko"])
        self.assertLess(rivit["rakenna_runko"], rivit["laheta"])


class ViikkoraporttiTesti(Pohja):
    def test_paiva_ja_malli(self):
        self.rivi(NYT, 1.0, malli="a")
        self.rivi(NYT, 2.0, malli="b")
        self.rivi(NYT - timedelta(days=2), 1.0, malli="a")
        self.rivi(NYT - timedelta(days=8), 99.0, malli="a")
        t = ok.viikkoraportti(self.tila, NYT)
        self.assertIn("- 14.11. yhteens\u00e4 2.76 (a 0.92; b 1.84)", t)
        self.assertIn("- 12.11. yhteens\u00e4 0.92 (a 0.92)", t)
        self.assertIn("- 13.11. yhteens\u00e4 0.00 (ei kuluja)", t)
        self.assertNotIn("7.11.", t)
        self.assertIn("- 8.11. yhteens", t)   # seitsemas (vanhin) paiva mukana
        self.assertNotIn("Tarkista k\u00e4ytt\u00f6", t)

    def test_kolme_katto_paivaa_merkitsee_tarkista(self):
        for i in (0, 2, 4):
            self.rivi(NYT - timedelta(days=i), USD_KATTO)
        self.assertIn("Tarkista k\u00e4ytt\u00f6: katto t\u00e4yttyi 3 p\u00e4iv\u00e4n\u00e4 7:st\u00e4", ok.viikkoraportti(self.tila, NYT))

    def test_tasan_katto_paiva_lasketaan_viikossa(self):
        vanha = ok.USD_EUR
        ok.USD_EUR = Decimal("0.5")
        self.addCleanup(setattr, ok, "USD_EUR", vanha)
        for i in (0, 1, 2):
            self.rivi(NYT - timedelta(days=i), 20.0)   # tasan 10 EUR
        self.assertIn("katto t\u00e4yttyi 3 p\u00e4iv\u00e4n\u00e4", ok.viikkoraportti(self.tila, NYT))

    def test_kaksi_katto_paivaa_ei_merkitse(self):
        for i in (0, 2):
            self.rivi(NYT - timedelta(days=i), USD_KATTO)
        self.assertNotIn("Tarkista", ok.viikkoraportti(self.tila, NYT))

    def test_rikkinainen_kirjanpito(self):
        (self.tila / ok.KULUT).write_text("rikki\n")
        self.assertIn("tarkista k\u00e4ytt\u00f6", ok.viikkoraportti(self.tila, NYT))


if __name__ == "__main__":
    unittest.main()
