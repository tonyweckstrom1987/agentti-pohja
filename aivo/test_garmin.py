"""Aivon Garmin-haun testit feikkidatalla (ei verkkoa, ei garminconnect-kirjastoa, ei oikeaa terveysdataa)."""
import json
import os
import stat
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from aivo import garmin
from claudejono import aamukooste

TANAAN = date(2030, 1, 7)   # maanantai; kiintea paiva ei vertaudu oikeaan kelloon
MERKKI = 987654             # feikkiarvo, jonka ei saa nakya tilatiedostossa


class GarminConnectAuthenticationError(Exception):
    pass


class GarminConnectTooManyRequestsError(Exception):
    pass


class Feikki:
    """Matkii garminconnect.Garmin-luokan metodeja. `rikki` = {metodi: poikkeus}."""

    def __init__(self, rikki=None):
        self.rikki = rikki or {}
        self.kutsut = []

    def __getattr__(self, nimi):
        if not nimi.startswith("get_"):
            raise AttributeError(nimi)

        def kutsu(*argit):
            self.kutsut.append((nimi, argit))
            if nimi in self.rikki:
                raise self.rikki[nimi]
            if nimi == "get_user_summary":
                return {"totalSteps": MERKKI, "restingHeartRate": 50, "averageStressLevel": 20,
                        "bodyBatteryHighestValue": 90, "bodyBatteryLowestValue": 10}
            if nimi == "get_sleep_data":
                return {"dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 77}}}}
            if nimi == "get_activities_by_date":
                return [{"activityId": 1}, {"activityId": 2}]
            if nimi == "get_hrv_data":
                return {"hrvSummary": {"lastNightAvg": 44}}
            return {"feikki": nimi, "argit": list(argit)}
        return kutsu


def tila(polku):
    return stat.S_IMODE(os.stat(polku).st_mode)


class HakuTestit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "garmin"
        self.nukahda = mock.Mock()

    def test_hakee_kaksi_paivaa_kaikki_lajit_ja_oikeudet(self):
        f = Feikki()
        t = garmin.hae(f, self.data, TANAAN, nukahda=self.nukahda)
        self.assertEqual(t["tila"], "ok")
        self.assertEqual(t["paivat"], {"2030-01-06": 8, "2030-01-07": 8})
        self.assertEqual(len(f.kutsut), 16)
        self.assertEqual(tila(self.data), 0o700)
        polku = self.data / "garmin-2030-01-06.jsonl"
        self.assertEqual(tila(polku), 0o600)
        rivit = [json.loads(r) for r in polku.read_text().splitlines()]
        self.assertEqual({r["laji"] for r in rivit},
                         {"yhteenveto", "uni", "stressi", "body_battery", "askeleet", "treenit", "leposyke", "hrv"})
        self.assertTrue(all(r["paiva"] == "2030-01-06" for r in rivit))
        self.assertIn(("get_body_battery", ("2030-01-06", "2030-01-06")), f.kutsut)

    def test_tilatiedostossa_ei_terveysarvoja(self):
        garmin.hae(Feikki(), self.data, TANAAN, nukahda=self.nukahda)
        teksti = (self.data / "tila.json").read_text()
        self.assertNotIn(str(MERKKI), teksti)
        self.assertEqual(set(json.loads(teksti)), {"tila", "aika", "paivat", "virheet"})

    def test_vanhentunut_kirjautuminen_keskeyttaa_ja_pyytaa_kirjautumaan(self):
        f = Feikki({"get_stress_data": GarminConnectAuthenticationError("SALAINEN-viesti")})
        t = garmin.hae(f, self.data, TANAAN, nukahda=self.nukahda)
        self.assertEqual(t["tila"], "kirjaudu")
        self.assertEqual(t["paivat"], {"2030-01-06": 2})          # yhteenveto ja uni ehtivat
        self.assertEqual(t["virheet"], ["stressi:GarminConnectAuthenticationError"])
        self.assertFalse((self.data / "garmin-2030-01-07.jsonl").exists())
        self.assertNotIn("SALAINEN", (self.data / "tila.json").read_text())

    def test_liikaa_kutsuja_lopettaa_virheena(self):
        t = garmin.hae(Feikki({"get_user_summary": GarminConnectTooManyRequestsError()}), self.data, TANAAN,
                       nukahda=self.nukahda)
        self.assertEqual(t["tila"], "virhe")

    def test_yksittainen_virhe_ei_kaada(self):
        t = garmin.hae(Feikki({"get_hrv_data": ValueError("x")}), self.data, TANAAN, nukahda=self.nukahda)
        self.assertEqual(t["tila"], "ok")
        self.assertEqual(t["paivat"], {"2030-01-06": 7, "2030-01-07": 7})
        self.assertEqual(t["virheet"], ["hrv:ValueError", "hrv:ValueError"])

    def test_uusi_ajo_korvaa_paivan(self):
        garmin.hae(Feikki(), self.data, TANAAN, nukahda=self.nukahda)
        garmin.hae(Feikki(), self.data, TANAAN, nukahda=self.nukahda)
        self.assertEqual(len((self.data / "garmin-2030-01-07.jsonl").read_text().splitlines()), 8)

    def test_hakemiston_vanha_tila_korjataan(self):
        self.data.mkdir(mode=0o755)
        os.chmod(self.data, 0o755)
        garmin.hae(Feikki(), self.data, TANAAN, nukahda=self.nukahda)
        self.assertEqual(tila(self.data), 0o700)

    def test_main_hae_ilman_kirjautumista(self):
        with mock.patch.object(garmin, "_asiakas_tunnisteella", side_effect=GarminConnectAuthenticationError("x")):
            koodi = garmin.main(["hae", "--data", str(self.data), "--tunniste", str(self.tmp / "t")])
        self.assertEqual(koodi, 2)
        self.assertEqual(json.loads((self.data / "tila.json").read_text())["tila"], "kirjaudu")

    def test_main_ajo_maanantaina_tekee_viikon(self):
        kirjasto = self.tmp / "kirjasto"
        with mock.patch.object(garmin, "_asiakas_tunnisteella", return_value=Feikki()), \
                mock.patch.object(garmin.time, "sleep"):
            koodi = garmin.main(["ajo", "--data", str(self.data), "--kirjasto", str(kirjasto), "--paiva", "2030-01-07"])
        self.assertEqual(koodi, 0)
        self.assertTrue((kirjasto / "garmin" / "2030-W01.md").exists())

    def test_salasanaa_ei_voi_antaa_komentorivilla(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            garmin.main(["kirjaudu", "--salasana", "x"])

    def test_kirjaudu_vaatii_paatteen(self):
        with mock.patch("sys.stdin") as stdin, mock.patch("sys.stderr"):
            stdin.isatty.return_value = False
            self.assertEqual(garmin.kirjaudu(str(self.tmp / "t"), syote=mock.Mock(side_effect=AssertionError)), 2)


class ViikkoTestit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "garmin"
        garmin.hae(Feikki(), self.data, date(2029, 12, 31), nukahda=lambda s: None)   # 30.12. ja 31.12.

    def test_tiivistelma_poimii_arvot_ja_puuttuvat_viivoina(self):
        teksti = garmin.viikon_tiivistelma(self.data, date(2029, 12, 31))
        self.assertIn("2030-W01", teksti)
        rivi = next(r for r in teksti.splitlines() if r.startswith("| 2029-12-31"))
        self.assertIn(str(MERKKI), rivi)
        self.assertIn("7.5", rivi)          # 27000 s unta -> 7.5 h
        self.assertTrue(rivi.rstrip().endswith("| 2 |"))
        tyhja = next(r for r in teksti.splitlines() if r.startswith("| 2030-01-01"))
        self.assertEqual(tyhja.count("-"), 2 + len(garmin.TUNNUSLUVUT) + 1)   # paivamaaran viivat + puuttuvat

    def test_viikko_ilman_gitia_vain_isannalle(self):
        polku, tulos = garmin.viikko(self.data, self.tmp / "kirjasto", date(2030, 1, 7))
        self.assertEqual(polku.name, "2030-W01.md")
        self.assertEqual(tila(polku), 0o600)
        self.assertIn("vain is\u00e4nt\u00e4", tulos)

    def test_github_etarepo_estetaan(self):
        kirjasto = self.tmp / "kirjasto"
        (kirjasto / ".git").mkdir(parents=True)
        kutsut = []

        def suorita(argit, **_):
            kutsut.append(argit[3:])
            return mock.Mock(returncode=0, stdout="origin\thttps://GitHub.com/x/y.git (push)\n")
        tulos = garmin.julkaise_kirjastoon(kirjasto, kirjasto / "a.md", "v", suorita=suorita)
        self.assertIn("estetty", tulos)
        self.assertEqual(kutsut, [["remote", "-v"]])

    def test_gitea_push(self):
        kirjasto = self.tmp / "kirjasto"
        (kirjasto / ".git").mkdir(parents=True)
        kutsut = []

        def suorita(argit, **_):
            kutsut.append(argit[3])
            return mock.Mock(returncode=0, stdout="origin\tssh://git@gitea.local/tony/aivo-kirjasto.git (push)\n")
        self.assertEqual(garmin.julkaise_kirjastoon(kirjasto, kirjasto / "a.md", "v", suorita=suorita), "push ok")
        self.assertEqual(kutsut, ["remote", "add", "commit", "push"])


class AamukoosteTestit(unittest.TestCase):
    NYT = datetime(2030, 1, 7, 6, 30, tzinfo=timezone.utc)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.polku = self.tmp / "garmin-tila.json"

    def kirjoita(self, tila, aika=None, virheet=()):
        self.polku.write_text(json.dumps({"tila": tila, "aika": (aika or self.NYT - timedelta(minutes=30)).isoformat(),
                                          "paivat": {}, "virheet": list(virheet)}))

    def test_puuttuva_ei_rivia(self):
        self.assertEqual(garmin.aamukooste_osa(self.polku, self.NYT), (None, None))

    def test_ok_ei_rivia(self):
        self.kirjoita("ok")
        self.assertEqual(garmin.aamukooste_osa(self.polku, self.NYT), (None, None))

    def test_kirjaudu_pyynto(self):
        self.kirjoita("kirjaudu")
        tarvitaan, hal = garmin.aamukooste_osa(self.polku, self.NYT)
        self.assertIn("kirjaudu uudelleen", tarvitaan)
        self.assertIsNone(hal)

    def test_vanha_tila_halyttaa(self):
        self.kirjoita("ok", aika=self.NYT - timedelta(hours=40))
        self.assertIn("ei ole p\u00e4ivittynyt", garmin.aamukooste_osa(self.polku, self.NYT)[1])

    def test_virhe_halyttaa_tyypilla(self):
        self.kirjoita("virhe", virheet=["uni:GarminConnectTooManyRequestsError"])
        self.assertIn("TooManyRequests", garmin.aamukooste_osa(self.polku, self.NYT)[1])

    def test_rikkinainen_tiedosto(self):
        self.polku.write_text("{")
        self.assertIn("ei saatu", garmin.aamukooste_osa(self.polku, self.NYT)[1])

    def test_aamukoosteen_tarvitaan_rivi(self):
        self.kirjoita("kirjaudu")
        t = {"tulokset": [], "yojono": None, "uudet_prt": [], "kaistat": None, "pr": {"tony": []}, "tonylle": [],
             "opit": [], "yksikot": [], "garmin_tarvitaan": garmin.aamukooste_osa(self.polku, self.NYT)[0]}
        self.assertIn("Garmin: kirjaudu uudelleen", aamukooste.rivit(t)[0])   # Paatokset sinulle -rivi

    def test_aamukooste_keraa_lukee_tilan(self):
        self.kirjoita("ok", aika=self.NYT - timedelta(hours=48))
        with mock.patch("agentti.aamupaketti.keraa", return_value={"tony": []}):
            t = aamukooste.keraa(self.tmp / "docs", self.tmp / "yj", self.NYT, hae=lambda u: [], yksikot=lambda: [],
                                 garmin_tila=self.polku)
        self.assertTrue(any(r.startswith("H\u00e4lytykset:") and "Garmin-haku ei ole" in r for r in aamukooste.rivit(t)))


if __name__ == "__main__":
    unittest.main()
