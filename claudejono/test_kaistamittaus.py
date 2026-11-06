import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claudejono import ajuri, kaistamittaus as km

NYT = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def aja_rivi(kaista, lopussa, kesto_s, ok=True, raja=False, **lisa):
    return {"aika": lopussa.isoformat(), "kaista": kaista, "laji": "tehtava", "tehtava": f"t{kaista}", "ok": ok,
            "kesto_s": kesto_s, "raja": raja, "lukko_odotus_s": 0.3, "push": True, **lisa}


class Mittaus(unittest.TestCase):
    def setUp(self):
        self.tila = Path(tempfile.mkdtemp())

    def kirjoita(self, nimi, rivit):
        (self.tila / nimi).write_text("\n".join(json.dumps(r) for r in rivit) + "\nroskaa\n")   # rikkinainen rivi ohitetaan

    def aineisto(self, raja=False, tunteja=48):
        alku = NYT - timedelta(hours=tunteja)
        ajot, kuorma = [], []
        for i in range(8):
            loppu = alku + timedelta(hours=i * 6 + 1)
            ajot.append(aja_rivi(1, loppu, 1200, arvo_usd=2.0, kierrokset=10))
            ajot.append(aja_rivi(2, loppu + timedelta(minutes=5), 900, ok=(i != 3), raja=(raja and i == 3), arvo_usd=1.0, kierrokset=5))
        for i in range(tunteja * 4):
            kuorma.append({"aika": (alku + timedelta(minutes=15 * i)).isoformat(), "load1": 0.5 + (i % 5) * 0.1, "ytimet": 2,
                           "muisti_vapaa_mb": 3000 - i, "swap_kaytossa_mb": 2})
        self.kirjoita(ajuri.KAISTALOKI, ajot)
        self.kirjoita(km.KUORMA, kuorma)
        return alku

    def test_nayte_kirjaa_rivin(self):
        r = km.nayte(self.tila, NYT, lataus=[0.5, 0.4, 0.3], muisti={"MemTotal": 6144, "MemAvailable": 4000, "SwapTotal": 512, "SwapFree": 500}, ytimet=2)
        d = json.loads((self.tila / km.KUORMA).read_text())
        self.assertEqual((d["load1"], d["muisti_vapaa_mb"], d["swap_kaytossa_mb"], d["varaukset"]), (0.5, 4000, 12, 0))
        self.assertEqual(r, d)

    @unittest.skipUnless(os.path.exists("/proc/meminfo"), "vaatii Linuxin /proc-tiedostot")
    def test_nayte_oikealla_proc_lahteella(self):
        r = km.nayte(self.tila)
        self.assertIsInstance(r["load1"], float)
        self.assertGreater(r["muisti_kaikki_mb"], 0)

    def test_yhteenveto_kaistoittain_ja_rinnakkaisuus(self):
        alku = self.aineisto()
        y = km.yhteenveto(self.tila, alku, NYT, self.tila)
        self.assertEqual({n: k["tehtavia"] for n, k in y["kaistat"].items()}, {1: 8, 2: 8})
        self.assertEqual(y["kaistat"][2]["epaonnistui"], 1)
        self.assertEqual(y["kaistat"][1]["arvo"], 16.0)
        self.assertEqual(y["huippurinnakkaisuus"], 2)
        self.assertGreater(y["paallekkain_min"], 0)
        self.assertEqual(y["muisti_vapaa_min_mb"], 3000 - (48 * 4 - 1))

    def test_paatelma_vahan_dataa_ei_paata(self):
        alku = self.aineisto(tunteja=6)
        p = km.paatelma(km.yhteenveto(self.tila, alku, NYT, self.tila))
        self.assertEqual(len(p), 1)
        self.assertIn("Data ei riit", p[0])

    def test_paatelma_rajaosuma_ja_rauta(self):
        alku = self.aineisto(raja=True)
        p = " ".join(km.paatelma(km.yhteenveto(self.tila, alku, NYT, self.tila)))
        self.assertIn("1 osumaa", p)
        self.assertIn("ei rajoita", p)   # kuorma ja muisti ovat kunnossa

    def test_paatelma_muisti_loppuu(self):
        alku = self.aineisto()
        self.kirjoita(km.KUORMA, [{"aika": (NYT - timedelta(hours=1)).isoformat(), "load1": 9.0, "ytimet": 2, "muisti_vapaa_mb": 100,
                                   "swap_kaytossa_mb": 400}, {"aika": (NYT - timedelta(hours=40)).isoformat(), "load1": 0.1, "ytimet": 2,
                                                              "muisti_vapaa_mb": 3000, "swap_kaytossa_mb": 0}])
        p = " ".join(km.paatelma(km.yhteenveto(self.tila, alku, NYT, self.tila)))
        self.assertIn("rajoittaa", p)
        self.assertIn("100 Mt", p)

    def test_kulut_luetaan_yojonon_tilasta_vain_valilta(self):
        (self.tila / "tila.json").write_text(json.dumps({"kulut": {"2026-11-02": 1.5, "2026-11-04": 2.25, "2026-10-01": 9.0, "x": "ei"}}))
        y = km.yhteenveto(self.tila, NYT - timedelta(days=4), NYT, self.tila)
        self.assertEqual(y["kulut"], {"2026-11-02": 1.5, "2026-11-04": 2.25})

    def test_raportti_sisaltaa_taulukot_ja_rajoitteet(self):
        alku = self.aineisto(raja=True)
        (self.tila / "tila.json").write_text(json.dumps({"kulut": {"2026-11-04": 2.25}}))
        t = km.raportti(self.tila, alku, NYT, self.tila)
        for osa in ("# Kaistakoe 2026-11", "| 1 | 8 | 8 | 0 | 0 |", "| 2 | 8 | 7 | 1 | 1 |", "2026-11-04: 2.25", "Rajoitteet", "arvaus"):
            self.assertIn(osa, t)

    def test_tyhja_aineisto_ei_kaada_raporttia(self):
        t = km.raportti(self.tila, NYT - timedelta(days=1), NYT, self.tila)
        self.assertIn("Data ei riit", t)

    def test_rivi_none_ilman_lokia_ja_muuten_yksi_rivi(self):
        self.assertIsNone(km.rivi(self.tila, NYT, self.tila))
        self.aineisto()
        r = km.rivi(self.tila, NYT, self.tila)
        self.assertNotIn("\n", r)
        self.assertIn("k1", r)
        self.assertIn("k2", r)

    def test_aamukoosteen_tehty_rivi_saa_kaistaosan_kymmenen_rivin_sisalla(self):
        from claudejono import aamukooste
        t = {"nyt": NYT, "tulokset": [], "uudet_prt": [], "opit": [], "tonylle": [], "pr": {"tony": []}, "pr_virheita": [],
             "yojono": None, "yksikot": [], "kaistat": "kaistat k1 3, k2 2"}
        rivit = aamukooste.rivit(t)
        self.assertLessEqual(len(rivit), 10)
        self.assertIn("kaistat k1 3, k2 2", rivit[1])
        t["kaistat"] = None
        self.assertNotIn("kaistat", aamukooste.rivit(t)[1])


if __name__ == "__main__":
    unittest.main()
