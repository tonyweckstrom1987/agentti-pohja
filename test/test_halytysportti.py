import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agentti import halytysportti as hp

TZ = hp.TZ


def aika(pvm, h=12, m=0):
    """Kiintea kello: epoch Suomen ajan paivamaarasta."""
    y, kk, p = map(int, pvm.split("-"))
    return datetime(y, kk, p, h, m, tzinfo=TZ).timestamp()


class LuokitteluTest(unittest.TestCase):
    def test_halytykset(self):
        for t in ("HALYTYS claude-jono kaatui: X", "HALYTYS: 3 epaonnistui\nYojono ...", "  halytys pieni", "[VIRHE] Deploy",
                  "\n\nHALYTYS aamukooste: failed: x", "H\u00c4LYTYS aakkosin"):
            self.assertEqual(hp.luokittele(t), "halytys", t)

    def test_muut_ohjataan_docs_repoon(self):
        for t in ("Yojono 2026-11-03: kulu 1 / 5 USD", "claude-jono: x VALMIS", "PAATOS: hyvaksy?", "[VALMIS] Deploy",
                  "Claude-kaistat: hidasta. syy", "korjaaja: x diagnoosi epaonnistui", "", "   \n ",
                  "Yojono\nHALYTYS toisella rivilla", "Ei HALYTYS alussa"):
            self.assertEqual(hp.luokittele(t), "muu", t)


class PorttiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tila = os.path.join(self.tmp.name, "portti.json")
        self.lahti = []

    def lahetin(self, teksti):
        self.lahti.append(teksti)
        return True

    def laheta(self, teksti, nyt):
        return hp.laheta(teksti, self.lahetin, tila_polku=self.tila, nyt=nyt)

    def tila_json(self):
        return json.loads(Path(self.tila).read_text(encoding="utf-8"))

    def test_ei_halytys_ei_lahde_ja_lasketaan(self):
        nyt = aika("2026-11-05")
        self.assertEqual(self.laheta("Aamukooste: kaikki hyvin", nyt), hp.OHJATTU)
        self.assertEqual(self.laheta("Yojono 2026-11-05: kulu 0.4", nyt), hp.OHJATTU)
        self.assertEqual(self.lahti, [])
        self.assertEqual(self.tila_json()["ohjattu"], 2)
        self.assertEqual(self.tila_json()["lahetetty"], 0)

    def test_ensimmaiset_halytykset_lahtevat_sellaisenaan(self):
        nyt = aika("2026-11-05")
        for i in range(4):
            self.assertEqual(self.laheta(f"HALYTYS {i}", nyt + i), hp.LAHETETTY)
        self.assertEqual(self.lahti, [f"HALYTYS {i}" for i in range(4)])

    def test_viides_lahtee_maininnalla_kuudes_jonoon(self):
        nyt = aika("2026-11-05")
        tulokset = [self.laheta(f"HALYTYS {i}", nyt + i) for i in range(8)]
        self.assertEqual(tulokset, [hp.LAHETETTY] * 5 + [hp.JONOON] * 3)
        self.assertEqual(len(self.lahti), hp.PAIVAKATTO)
        self.assertIn("Paivakatto 5 taynna", self.lahti[4])
        self.assertNotIn("Paivakatto", self.lahti[3])
        self.assertEqual(self.tila_json()["jono"], ["HALYTYS 5", "HALYTYS 6", "HALYTYS 7"])

    def test_ylitys_kootaan_yhdeksi_viestiksi_seuraavana_paivana(self):
        nyt = aika("2026-11-05", 8)
        for i in range(8):
            self.laheta(f"HALYTYS {i}\nlisarivi", nyt + i)
        self.lahti.clear()
        # seuraava vuorokausi: ensimmainen kutsu (vaikka ei-halytys) lahettaa koosteen, joka laskee kattoon
        self.assertEqual(self.laheta("tavallinen", aika("2026-11-06", 6, 30)), hp.OHJATTU)
        self.assertEqual(len(self.lahti), 1)
        self.assertIn("3 halytysta", self.lahti[0])
        for i in (5, 6, 7):
            self.assertIn(f"- HALYTYS {i}", self.lahti[0])
        self.assertNotIn("lisarivi", self.lahti[0])
        t = self.tila_json()
        self.assertEqual((t["pvm"], t["lahetetty"], t["jono"]), ("2026-11-06", 1, []))

    def test_katto_nollautuu_vuorokauden_vaihtuessa(self):
        for i in range(6):
            self.laheta(f"HALYTYS {i}", aika("2026-11-05", 10) + i)
        self.lahti.clear()
        self.assertEqual(self.laheta("HALYTYS uusi", aika("2026-11-06", 0, 5)), hp.LAHETETTY)
        self.assertEqual(len(self.lahti), 2)            # eilinen koonti + uusi
        self.assertEqual(self.tila_json()["lahetetty"], 2)

    def test_vuorokauden_raja_on_suomen_ajassa(self):
        # 23:59 ja 00:01 Suomen aikaa ovat eri vuorokausia
        for i in range(5):
            self.laheta(f"HALYTYS {i}", aika("2026-11-05", 23, 59))
        self.assertEqual(self.laheta("HALYTYS yli", aika("2026-11-05", 23, 59)), hp.JONOON)
        self.assertEqual(self.laheta("HALYTYS uusi", aika("2026-11-06", 0, 1)), hp.LAHETETTY)

    def test_epaonnistunut_lahetys_ei_kuluta_kattoa(self):
        nyt = aika("2026-11-05")
        self.assertEqual(hp.laheta("HALYTYS x", lambda t: False, tila_polku=self.tila, nyt=nyt), hp.EPAONNISTUI)
        self.assertEqual(self.tila_json()["lahetetty"], 0)

    def test_koonti_sailyy_jos_sen_lahetys_epaonnistuu(self):
        for i in range(7):
            self.laheta(f"HALYTYS {i}", aika("2026-11-05") + i)
        huomenna = aika("2026-11-06", 6)
        self.assertEqual(hp.laheta("tavallinen", lambda t: False, tila_polku=self.tila, nyt=huomenna), hp.OHJATTU)
        self.assertEqual(len(self.tila_json()["jono"]), 2)
        self.assertEqual(self.tila_json()["lahetetty"], 0)

    def test_jono_on_rajattu(self):
        nyt = aika("2026-11-05")
        for i in range(hp.PAIVAKATTO + hp.JONO_MAX + 4):
            self.laheta("HALYTYS " + "x" * 500, nyt)
        t = self.tila_json()
        self.assertEqual(len(t["jono"]), hp.JONO_MAX)
        self.assertEqual(t["jonossa_yli"], 4)
        self.assertTrue(all(len(r) <= hp.RIVI_MAX for r in t["jono"]))

    def test_huuhtele(self):
        for i in range(7):
            self.laheta(f"HALYTYS {i}", aika("2026-11-05") + i)
        self.lahti.clear()
        self.assertFalse(hp.huuhtele(self.lahetin, self.tila, aika("2026-11-05", 20)))   # sama vuorokausi: ei mitaan
        self.assertTrue(hp.huuhtele(self.lahetin, self.tila, aika("2026-11-06", 6, 30)))
        self.assertEqual(len(self.lahti), 1)
        self.assertFalse(hp.huuhtele(self.lahetin, self.tila, aika("2026-11-06", 6, 31)))  # jono tyhja
        self.assertEqual(len(self.lahti), 1)

    def test_rikkinainen_tila_ei_estaa_halytysta(self):
        Path(self.tila).write_text("ei jsonia")
        self.assertEqual(self.laheta("HALYTYS x", aika("2026-11-05")), hp.LAHETETTY)

    def test_tilaa_ei_voi_kirjoittaa_halytys_lahtee_silti(self):
        polku = os.path.join(self.tmp.name, "ei", "ole", "portti.json")
        self.assertEqual(hp.laheta("HALYTYS x", self.lahetin, tila_polku=polku, nyt=aika("2026-11-05")), hp.LAHETETTY)
        self.assertEqual(self.lahti, ["HALYTYS x"])


class YksiPorttiTest(unittest.TestCase):
    """Rakenteellinen vartija: sendMessage-kutsu saa olla vain sallituissa tiedostoissa, ja ne kulkevat portin kautta."""
    JUURI = Path(__file__).resolve().parent.parent
    SALLITUT = {"agentti/telegram.py", "yojono/ajuri.py", "isanta/agentscope/virheilmoitus.py"}

    def test_sendmessage_vain_sallituissa(self):
        loydot = set()
        for p in self.JUURI.rglob("*.py"):
            rel = p.relative_to(self.JUURI).as_posix()
            if "/test" in "/" + rel or p.name.startswith("test_") or rel.startswith(("tools/", "docs/")):
                continue
            if "sendMessage" in p.read_text(encoding="utf-8", errors="replace"):
                loydot.add(rel)
        self.assertLessEqual(loydot - self.SALLITUT - {"paivakatsaus/send.py"}, set(), "uusi suora Telegram-lahettaja ohittaa halytysportin")

    def test_lahettajat_kayttavat_porttia(self):
        for rel in ("yojono/ajuri.py", "agentti/ilmoita.py", "agentti/ajo.py", "agentti/aamupaketti.py", "isanta/agentscope/virheilmoitus.py"):
            teksti = (self.JUURI / rel).read_text(encoding="utf-8")
            self.assertRegex(teksti, r"halytysportti", rel)

    def test_vain_halytykset_aamukoosteesta(self):
        teksti = (self.JUURI / "claudejono/aamukooste.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"laheta_telegram\(teksti", teksti), "koko kooste ei saa menna Telegramiin")


if __name__ == "__main__":
    unittest.main()
