import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from collections import namedtuple
from unittest import mock

from agentti import tapahtumaloki as tl
from agentti import viikkoraportti as vr

NYT = 1_000_000.0


class Pohja(unittest.TestCase):
    def setUp(self):
        self.y = tl.avaa(":memory:")
        self.addCleanup(self.y.close)

    def kirjaa(self, agentti, tulos="ok", kulu=0.0, tiedot=None, ika_pv=1):
        tl.kirjaa(self.y, "yojono", agentti, "ajo", tulos, kulu_usd=kulu, tiedot=tiedot, nyt=NYT - ika_pv * 86400)


class ViikkoraporttiTest(Pohja):
    def test_tyhja_loki(self):
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertEqual(t["tapahtumia"], 0)
        teksti = vr.muodosta(t, {})
        self.assertIn("Ei tapahtumia", teksti)
        self.assertIn("ei huomautettavaa", teksti)

    def test_kulut_ja_virheet(self):
        self.kirjaa("katsaus", kulu=0.5)
        self.kirjaa("yojono", kulu=1.5)
        self.kirjaa("yojono", tulos="virhe", tiedot={"virhekoodi": "E42"})
        self.kirjaa("yojono", tulos="virhe", tiedot={"virhekoodi": "E42"})
        self.kirjaa("yojono", tulos="virhe")
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertEqual(t["tapahtumia"], 5)
        self.assertEqual(t["virheita"], 3)
        self.assertEqual(t["virhekoodit"], {"E42": 2, "tuntematon": 1})
        self.assertAlmostEqual(t["kulu_agentti"]["yojono"], 1.5)

    def test_vanhat_ohitetaan(self):
        self.kirjaa("yojono", kulu=9.0, ika_pv=30)
        self.assertEqual(vr.keraa(self.y, 7, nyt=NYT)["tapahtumia"], 0)

    def test_vihjeet(self):
        self.kirjaa("yojono", kulu=3.0)
        self.kirjaa("katsaus", kulu=0.1)
        for _ in range(3):
            self.kirjaa("yojono", tulos="virhe", tiedot={"virhekoodi": "E7"})
        t = vr.keraa(self.y, 7, nyt=NYT)
        v = " ".join(vr.vihjeet(t, {"levy_kaytetty_pros": 91.0, "muisti_vapaa_pros": 10.0}))
        self.assertIn("yojono", v)
        self.assertIn("E7", v)
        self.assertIn("Levy", v)
        self.assertIn("muistia", v)

    def test_kulu_vihje_vaatii_kaksi_kuluttajaa(self):
        self.kirjaa("yojono", kulu=3.0)
        self.kirjaa("katsaus", kulu=0.0)
        self.assertEqual(vr.vihjeet(vr.keraa(self.y, 7, nyt=NYT), {}), [])

    def test_hyvaksymis_vihje_vaatii_tarpeeksi_paatoksia(self):
        for i in range(4):
            tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "o%d" % i, "p"), "hylkaa")
        self.assertEqual(vr.vihjeet(vr.keraa(self.y, 7, nyt=NYT), {}), [])
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "viides", "p"), "hylkaa")
        self.assertTrue(any("hyvaksytty" in v for v in vr.vihjeet(vr.keraa(self.y, 7, nyt=NYT), {})))

    def test_ei_muuta_kantaa_ja_teksti_rajattu(self):
        for i in range(60):
            self.kirjaa("agentti%d" % i, tulos="virhe", kulu=0.1, tiedot={"virhekoodi": "K%d" % i})
        ennen = self.y.execute("SELECT COUNT(*) FROM tapahtuma").fetchone()[0]
        teksti = vr.muodosta(vr.keraa(self.y, 7, nyt=NYT), vr.resurssit())
        self.assertLessEqual(len(teksti), vr.TEKSTI_MAX)
        self.assertEqual(self.y.execute("SELECT COUNT(*) FROM tapahtuma").fetchone()[0], ennen)

    def test_resurssit_palauttaa_avaimet(self):
        self.assertEqual(set(vr.resurssit()), {"levy_kaytetty_pros", "muisti_vapaa_pros", "kuorma_1min"})

    def test_main_kayttovirheet(self):
        self.assertEqual(vr.main(["1", "2"]), 2)
        import os
        os.environ.pop("TAPAHTUMALOKI", None)
        self.assertEqual(vr.main([]), 2)

    def test_ohitukset_koodeineen_raporttiin_eivatka_ole_virheita(self):
        for _ in range(3):
            self.kirjaa("yojono", tulos="ohitettu", tiedot={"virhekoodi": "fork_ei_ff"})
        self.kirjaa("yojono", tulos="ohitettu", tiedot={"virhekoodi": "fork_push_epaonnistui"})
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertEqual((t["ohitettuja"], t["virheita"]), (4, 0))
        self.assertEqual(t["ohituskoodit"], {"fork_ei_ff": 3, "fork_push_epaonnistui": 1})
        teksti = vr.muodosta(t, {})
        self.assertIn("Ohitettu 4 (ei kulua): fork_ei_ff x3, fork_push_epaonnistui x1", teksti)
        v = " ".join(vr.vihjeet(t, {}))
        self.assertIn("Forkin synkkaus esti 4 tehtavaa", v)
        self.assertIn("fork_workflow_oikeus", v)

    def test_ohitus_ilman_koodia_on_tuntematon(self):
        self.kirjaa("yojono", tulos="ohitettu")
        self.assertEqual(vr.keraa(self.y, 7, nyt=NYT)["ohituskoodit"], {"tuntematon": 1})

    def test_ei_ohituksia_ei_ohitusrivia_eika_fork_vihjetta(self):
        self.kirjaa("yojono", kulu=0.5)
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertEqual((t["ohitettuja"], t["ohituskoodit"]), (0, {}))
        self.assertNotIn("Ohitettu", vr.muodosta(t, {}))
        self.assertEqual(vr.vihjeet(t, {}), [])

    def test_fork_virhekoodi_virheissa_laukaisee_vihjeen(self):
        self.kirjaa("yojono", kulu=0.5)
        self.kirjaa("yojono", tulos="virhe", tiedot={"virhekoodi": "fork_push_epaonnistui"})
        v = " ".join(vr.vihjeet(vr.keraa(self.y, 7, nyt=NYT), {}))
        self.assertIn("Forkin synkkaus esti 1 tehtavaa", v)

    def test_ajurin_koodit_nakyvat_raportissa_eika_tuntematonta(self):
        for koodi in ("aikakatkaisu", "malli_virhe", "raporttia_ei_syntynyt", "budjetti_loppui", "fork_jaljessa"):
            self.kirjaa("yojono", tulos="virhe", tiedot={"virhekoodi": koodi})
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertNotIn("tuntematon", t["virhekoodit"])
        teksti = vr.muodosta(t, {})
        self.assertIn("aikakatkaisu x1", teksti)
        self.assertNotIn("tuntematon", teksti)


class KeraaTarkkaTest(Pohja):
    def test_oletus_on_seitseman_paivaa_ja_raja_on_tarkka(self):
        self.kirjaa("a", ika_pv=6.9)
        self.kirjaa("b", ika_pv=7.0)
        self.kirjaa("c", ika_pv=7 + 3 / 86400.0)
        self.kirjaa("d", ika_pv=7.5)
        t = vr.keraa(self.y, nyt=NYT)
        self.assertEqual((t["paivaa"], t["tapahtumia"]), (7, 2))
        self.assertEqual(vr.keraa(self.y, 8, nyt=NYT)["tapahtumia"], 4)

    def test_kulu_pyoristetaan_kuuteen_desimaaliin(self):
        self.kirjaa("a", kulu=0.1234567)
        self.assertEqual(vr.keraa(self.y, 7, nyt=NYT)["kulu_agentti"], {"a": 0.123457})

    def test_paatettyja_laskee_vain_paatetyt(self):
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "a", "p"), "hylkaa")
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "b", "p"), "hylkaa")
        self.assertEqual(vr.keraa(self.y, 7, nyt=NYT)["paatettyja"], 2)
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "c", "p"), "hyvaksy")
        self.assertEqual(vr.keraa(self.y, 7, nyt=NYT)["paatettyja"], 3)

    def test_vain_hyvaksytyt(self):
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "a", "p"), "hyvaksy")
        self.assertEqual(vr.keraa(self.y, 7, nyt=NYT)["paatettyja"], 1)

    def test_ei_paatoksia_nolla(self):
        self.assertEqual(vr.keraa(self.y, 7, nyt=NYT)["paatettyja"], 0)


class ResurssitTarkkaTest(unittest.TestCase):
    MEMINFO = "MemTotal:         300 kB\nMemFree:           50 kB\nMemAvailable:     100 kB\nBuffers: 7 kB\n"

    def hae(self, levy=(300, 100), meminfo=None, kuorma=(1.2345, 0.0, 0.0)):
        K = namedtuple("K", "total used free")
        with mock.patch.object(shutil, "disk_usage", return_value=K(levy[0], levy[1], 0)), \
                mock.patch("builtins.open", mock.mock_open(read_data=self.MEMINFO if meminfo is None else meminfo)), \
                mock.patch.object(os, "getloadavg", return_value=kuorma):
            return vr.resurssit()

    def test_luvut_ja_pyoristys(self):
        self.assertEqual(self.hae(), {"levy_kaytetty_pros": 33.3, "muisti_vapaa_pros": 33.3, "kuorma_1min": 1.23})

    def test_levy_prosentti(self):
        self.assertEqual(self.hae(levy=(200, 50))["levy_kaytetty_pros"], 25.0)

    def test_muisti_prosentti(self):
        self.assertEqual(self.hae(meminfo="MemTotal: 400 kB\nMemAvailable: 100 kB\n")["muisti_vapaa_pros"], 25.0)

    def test_kuorma_on_ensimmainen_arvo(self):
        self.assertEqual(self.hae(kuorma=(0.5, 9.0, 8.0))["kuorma_1min"], 0.5)

    def test_puuttuvat_arvot_ovat_none(self):
        K = namedtuple("K", "total used free")
        with mock.patch.object(shutil, "disk_usage", side_effect=OSError), \
                mock.patch("builtins.open", side_effect=OSError), \
                mock.patch.object(os, "getloadavg", side_effect=OSError):
            self.assertEqual(vr.resurssit(), {"levy_kaytetty_pros": None, "muisti_vapaa_pros": None, "kuorma_1min": None})
        with mock.patch.object(shutil, "disk_usage", return_value=K(0, 0, 0)), \
                mock.patch("builtins.open", mock.mock_open(read_data="MemTotal: 0 kB\nMemAvailable: 0 kB\n")), \
                mock.patch.object(os, "getloadavg", return_value=(0.0, 0.0, 0.0)):
            r = vr.resurssit()
        self.assertEqual((r["levy_kaytetty_pros"], r["muisti_vapaa_pros"], r["kuorma_1min"]), (None, None, 0.0))


class VihjeetTarkkaTest(Pohja):
    def test_kulu_vihje_pienilla_summilla_ja_tarkka_teksti(self):
        self.kirjaa("yojono", kulu=0.3)
        self.kirjaa("katsaus", kulu=0.1)
        v = vr.vihjeet(vr.keraa(self.y, 7, nyt=NYT), {})
        self.assertEqual(v, ["Agentti yojono kuluttaa 75 % kuluista: harkitse halvempaa mallia tai tehtavan rajausta."])

    def test_virhevihje_teksti_ja_raja(self):
        self.kirjaa("a", kulu=1.0)
        self.kirjaa("a", tulos="virhe", tiedot={"virhekoodi": "E7"})
        self.kirjaa("a", tulos="virhe", tiedot={"virhekoodi": "E7"})
        self.kirjaa("a", tulos="virhe", tiedot={"virhekoodi": "E8"})
        v = vr.vihjeet(vr.keraa(self.y, 7, nyt=NYT), {})
        self.assertEqual(v, ["Virheita 75 % tapahtumista, yleisin koodi E7: selvita syy."])

    def test_vahan_virheita_ei_vihjetta(self):
        for _ in range(19):
            self.kirjaa("a")
        self.kirjaa("a", tulos="virhe", tiedot={"virhekoodi": "E7"})
        self.assertEqual(vr.vihjeet(vr.keraa(self.y, 7, nyt=NYT), {}), [])


class MuodostaTarkkaTest(Pohja):
    def test_rivit_ja_viiden_raja(self):
        for i in range(7):
            self.kirjaa("ag%d" % i, tulos="virhe", kulu=1.0 + i, tiedot={"virhekoodi": "K%d" % i})
        for i in range(7):
            self.kirjaa("ag", tulos="ohitettu", tiedot={"virhekoodi": "O%d" % i})
        r = vr.muodosta(vr.keraa(self.y, 7, nyt=NYT), {}).split("\n")
        self.assertEqual(r[0], "Viikkoraportti (7 pv)")
        self.assertEqual(r[1], "Tapahtumia 14, virheita 7, kulu 28.00 USD.")
        self.assertEqual(r[2], "Kulu agenteittain: ag6 7.00, ag5 6.00, ag4 5.00, ag3 4.00, ag2 3.00")
        self.assertEqual(len(r[3].split(", ")), 5)
        self.assertTrue(r[3].startswith("Virhekoodit: K"))
        self.assertTrue(r[4].startswith("Ohitettu 7 (ei kulua): O"))
        self.assertEqual(len(r[4].split(", ")), 5)

    def test_resurssirivi(self):
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertNotIn("Resurssit", vr.muodosta(t, {}))
        self.assertNotIn("Resurssit", vr.muodosta(t, {"levy_kaytetty_pros": None, "muisti_vapaa_pros": None, "kuorma_1min": None}))
        res = {"levy_kaytetty_pros": 50.0, "muisti_vapaa_pros": 25.0, "kuorma_1min": 1.5}
        self.assertIn("Resurssit: levy 50 %, muisti vapaa 25 %, kuorma 1.50.", vr.muodosta(t, res))
        self.assertIn("Resurssit: kuorma 0.00.", vr.muodosta(t, {"kuorma_1min": 0.0}))

    def test_hyvaksymisrivi(self):
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertIn("Ehdotusten hyvaksymis-%: - (paatettyja 0).", vr.muodosta(t, {}))
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "a", "p"), "hyvaksy")
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "b", "p"), "hylkaa")
        t = vr.keraa(self.y, 7, nyt=NYT)
        self.assertIn("Ehdotusten hyvaksymis-%: 50 (paatettyja 2).", vr.muodosta(t, {}))


class MainTarkkaTest(unittest.TestCase):
    def setUp(self):
        self.hakem = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.hakem, True)
        self.polku = os.path.join(self.hakem, "loki.db")
        tl.avaa(self.polku).close()
        env = mock.patch.dict(os.environ, {"TAPAHTUMALOKI": self.polku})
        env.start()
        self.addCleanup(env.stop)

    def aja(self, argv=None):
        ulos, virhe = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(ulos), contextlib.redirect_stderr(virhe):
            rc = vr.main(argv)
        return rc, ulos.getvalue(), virhe.getvalue()

    def test_oletus_seitseman_paivaa(self):
        rc, ulos, _ = self.aja([])
        self.assertEqual(rc, 0)
        self.assertIn("Viikkoraportti (7 pv)", ulos)

    def test_argumentti_valitaan_paivat(self):
        rc, ulos, _ = self.aja(["3"])
        self.assertEqual(rc, 0)
        self.assertIn("Viikkoraportti (3 pv)", ulos)

    def test_ilman_argumenttia_luetaan_sys_argv(self):
        with mock.patch.object(sys, "argv", ["viikkoraportti", "4"]):
            rc, ulos, _ = self.aja(None)
        self.assertEqual(rc, 0)
        self.assertIn("(4 pv)", ulos)
        with mock.patch.object(sys, "argv", ["viikkoraportti"]):
            self.assertIn("(7 pv)", self.aja(None)[1])

    def test_liikaa_argumentteja(self):
        rc, ulos, virhe = self.aja(["1", "2"])
        self.assertEqual((rc, ulos), (2, ""))
        self.assertIn("kaytto:", virhe)

    def test_ei_ymparistomuuttujaa(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("TAPAHTUMALOKI")
            self.assertEqual(self.aja([])[0], 2)

    def test_paivarajat(self):
        for ok in ("1", "366"):
            self.assertEqual(self.aja([ok])[0], 0, ok)
        for huono in ("0", "367", "-1", "abc"):
            rc, ulos, virhe = self.aja([huono])
            self.assertEqual((rc, ulos), (2, ""), huono)
            self.assertIn("viikkoraportti:", virhe)
        self.assertIn("paivaa 1-366", self.aja(["0"])[2])

    def test_puuttuva_loki(self):
        with mock.patch.dict(os.environ, {"TAPAHTUMALOKI": os.path.join(self.hakem, "ei_ole.db")}):
            rc, ulos, virhe = self.aja([])
        self.assertEqual((rc, ulos), (2, ""))
        self.assertIn("lokia ei ole", virhe)
        self.assertFalse(os.path.exists(os.path.join(self.hakem, "ei_ole.db")))

    def test_muistilokki_sallitaan(self):
        with mock.patch.dict(os.environ, {"TAPAHTUMALOKI": ":memory:"}):
            rc, ulos, _ = self.aja([])
        self.assertEqual(rc, 0)
        self.assertIn("Ei tapahtumia", ulos)


if __name__ == "__main__":
    unittest.main()
