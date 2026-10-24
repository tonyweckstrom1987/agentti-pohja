import json
import tempfile
import unittest
from pathlib import Path

from agentti import tilannekuva as tk


def hae_tekoinen(url):
    if "/pulls" in url:
        return [{"number": 7, "state": "closed", "merged_at": "2026-10-20T10:00:00Z", "title": "Korjaus",
                 "user": {"login": "bot"}, "updated_at": "2026-10-20T10:00:00Z"}]
    return [{"sha": "abcdef123456", "commit": {"message": "viesti\n\nrunko"}}]


class SiivousTesti(unittest.TestCase):
    def test_salaisuudet_poistetaan(self):
        for arvo in ("sk-" + "or-v1-abcdefghijklmnopqrstu", "ghp_" + "a" * 30, "github_pat_" + "B" * 30,
                     "123456789:" + "A" * 35, "api_key = abcdefgh12345", "-----BEGIN RSA PRIVATE KEY-----"):
            ulos = tk.siivoa("rivi " + arvo + " loppu")
            self.assertIn("[poistettu]", ulos, arvo)
            self.assertNotIn(arvo, ulos)

    def test_pituus_rajataan(self):
        ulos = tk.siivoa("\n".join("r%d" % i for i in range(1000)))
        self.assertEqual(len(ulos.splitlines()), tk.RAJA_RIVEJA + 1)
        self.assertIn("(katkaistu)", ulos)

    def test_tavallinen_teksti_sailyy(self):
        self.assertEqual(tk.siivoa("PR #3 mergattu"), "PR #3 mergattu\n")


class OsioTesti(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        (self.t / "raportit").mkdir()
        (self.t / "tila.json").write_text(json.dumps({
            "tehtavat": {"a": {"tila": "epaonnistui", "yrityksia": 1, "pvm": "2026-10-23", "kulu": 0.4, "viesti": "node puuttuu"}},
            "kulut": {"2026-10-23": 1.5}}))
        (self.t / "jono.json").write_text(json.dumps({"paivakatto": 8, "tehtavat": [
            {"id": "a", "tyyppi": "kehitys", "aihe": "testit", "repo": "r"}, {"id": "b", "tyyppi": "ideointi", "repo": "r"}]}))
        (self.t / "raportit" / "x.md").write_text("# Kehitys\n- PR: ei avattu\n- Syy: testit punaiset\nmuuta\n")

    def test_github_osio(self):
        teksti = tk.github_osio(hae_tekoinen)
        self.assertIn("PR #7 [mergattu] Korjaus", teksti)
        self.assertIn("commit abcdef1 viesti", teksti)

    def test_github_virhe_ei_kaada(self):
        def rikki(url):
            raise OSError("verkko")
        self.assertIn("haku epaonnistui: OSError", tk.github_osio(rikki))

    def test_yojono_osio(self):
        teksti = tk.yojono_osio(self.t)
        self.assertIn("a: epaonnistui, yrityksia 1", teksti)
        self.assertIn("- b [ideointi] r", teksti)
        self.assertIn("PR: ei avattu", teksti)

    def test_kulut_osio(self):
        self.assertIn("yojono 2026-10-23: 1.5", tk.kulut_osio(self.t, None))

    def test_kulut_osio_kuukausiyhteenveto_ja_ennuste(self):
        (self.t / "tila.json").write_text(json.dumps({"kulut": {"2026-10-23": 3.0, "2026-10-24": 5.0, "2026-09-30": 1.0}}))
        teksti = tk.kulut_osio(self.t, None)
        self.assertIn("kuukausi 2026-10 yhteensa: 8.00, kirjattuja paivia 2 (keskim. 4.00/pv, 30 pv ennuste 120.0)", teksti)
        self.assertIn("kuukausi 2026-09 yhteensa: 1.00", teksti)
        self.assertIn("paivakatto: 8, kuukausikatto: None", teksti)

    def test_raportin_koko_nakyy(self):
        (self.t / "raportit" / "tyhja.md").write_text("# T\n")
        teksti = tk.yojono_osio(self.t)
        self.assertIn("tyhja.md (4 merkkia)", teksti)
        self.assertIn("x.md (", teksti)

    def test_puuttuva_tila_ei_kaada(self):
        tyhja = Path(tempfile.mkdtemp())
        self.assertIn("# Yojono", tk.yojono_osio(tyhja))

    def test_keraa_kaikki_kirjoittaa_tiedostot(self):
        kohde = Path(tempfile.mkdtemp()) / "kuva"
        nimet = tk.keraa_kaikki(kohde, self.t, None, hae_tekoinen, lambda: "kontti 111 running")
        self.assertEqual(sorted(nimet), ["github.md", "isanta.md", "kulut.md", "palvelin.md", "yojono.md"])
        self.assertIn("kontti 111 running", (kohde / "isanta.md").read_text())
        for n in nimet:
            self.assertTrue((kohde / n).read_text())
        self.assertTrue((kohde / "aika.txt").exists())

    def test_osion_virhe_ei_kaada_muita(self):
        def rikki(url):
            raise RuntimeError("x")
        kohde = Path(tempfile.mkdtemp())
        orig = tk.palvelin_osio
        tk.palvelin_osio = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            tk.keraa_kaikki(kohde, self.t, None, rikki, lambda: "x")
        finally:
            tk.palvelin_osio = orig
        self.assertIn("osio epaonnistui", (kohde / "palvelin.md").read_text())
        self.assertIn("# Yojono", (kohde / "yojono.md").read_text())

    def test_isanta_osio_virhe_ei_kaada(self):
        def rikki():
            raise RuntimeError("ei yhteytta")
        self.assertIn("isantaa ei saatavilla: ei yhteytta", tk.isanta_osio(rikki))
        self.assertIn("kontit ok", tk.isanta_osio(lambda: "kontit ok"))

    def test_main_vaatii_yhden_argumentin(self):
        self.assertEqual(tk.main([]), 2)


if __name__ == "__main__":
    unittest.main()
