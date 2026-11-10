"""Tavoitesidonta, kaistaosuudet, jalkimittari ja Aivon varjoarvio (A839-A841)."""
import json
import random
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

from claudejono import aivo_arvio, ajuri, tavoite


def kirj(hak, nimi, teksti):
    p = Path(hak) / nimi
    p.write_text(teksti, encoding="utf-8")
    return p


class Jasennys(unittest.TestCase):
    def test_sallitut_arvot(self):
        for rivi, odotettu in (("portfolio", "portfolio"), ("TO-paikka/portfolio", "portfolio"), ("TO-paikka", "portfolio"),
                               ("ansainta", "ansainta"), ("Aivo", "aivo"), ("yllapito", "yllapito"), ("yll\u00e4pito", "yllapito"),
                               ("Yll\u00e4pito.", "yllapito")):
            self.assertEqual(tavoite.tavoite_tekstista(f"# T\nTavoite: {rivi}\n"), odotettu, rivi)

    def test_virheelliset_ovat_none(self):
        for teksti in ("ei rivia", "Tavoite: rahaa", "Tavoite:", "tavoite: aivo", "  Tavoite: aivo", "Tavoite: aivo ja muuta", "x Tavoite: aivo"):
            self.assertIsNone(tavoite.tavoite_tekstista(teksti), teksti)

    def test_kuvausrivi_ennen_tavoiterivia_ei_hylkaa(self):
        # 10.11.2026: tehtavat 25 ja 27 alkoivat kuvausrivilla "Tavoite: tarkastaja havaitsi ..." ja hylattiin hiljaa.
        teksti = "# T\nTavoite: tarkastaja havaitsi, etta main() ei kytke arvioijaa.\n\nTavoite: Aivo\n"
        self.assertEqual(tavoite.tavoite_tekstista(teksti), "aivo")
        self.assertEqual(tavoite.tavoite_tekstista("Tavoite: rahaa\nTavoite: rahaa taas\n"), None)
        self.assertEqual(tavoite.tavoite_tekstista("Tavoite: ansainta\nTavoite: Aivo\n"), "ansainta")

    def test_luokittele_kuvausrivi_ensin(self):
        h = tempfile.mkdtemp()
        tav, syy = tavoite.luokittele(kirj(h, "2026-11-10-27-a.md", "Tavoite: tarkastajan loydokset:\nTavoite: yll\u00e4pito\n"))
        self.assertEqual((tav, syy), ("yllapito", ""))

    def test_pakollisuus_paivayksen_mukaan(self):
        self.assertFalse(tavoite.pakollinen("2026-11-07-01-vanha.md"))
        self.assertTrue(tavoite.pakollinen("2026-11-08-01-uusi.md"))
        self.assertTrue(tavoite.pakollinen("outo-nimi.md"))

    def test_luokittele(self):
        h = tempfile.mkdtemp()
        self.assertEqual(tavoite.luokittele(kirj(h, "2026-11-09-01-a.md", "Tavoite: Aivo\n"))[0], "aivo")
        tav, syy = tavoite.luokittele(kirj(h, "2026-11-09-02-b.md", "ei rivi\u00e4"))
        self.assertIsNone(tav)
        self.assertIn("puuttuu", syy)
        self.assertEqual(tavoite.luokittele(kirj(h, "2026-11-01-01-c.md", "ei rivi\u00e4")), ("yllapito", ""))


class Kaistat(unittest.TestCase):
    def setUp(self):
        self.h = tempfile.mkdtemp()
        self.ts = {}
        for i, t in enumerate(("portfolio", "ansainta", "aivo", "yllapito"), 1):
            self.ts[t] = kirj(self.h, f"2026-11-09-0{i}-{t}.md", f"Tavoite: {t}\n")
        self.kaikki = [(p, None) for p in self.ts.values()]

    def nimet(self, kaista, kaistoja=3, odottavat=None):
        return [p.name.split("-", 4)[-1][:-3] for p in tavoite.valitse(odottavat or self.kaikki, kaista, kaistoja)]

    def test_kaista3_vain_aivo_kun_sita_on(self):
        self.assertEqual(self.nimet(3), ["aivo"])

    def test_kaista3_ottaa_yllapitoa_eika_muuta_ilman_aivoa(self):
        self.assertEqual(self.nimet(3, odottavat=[x for x in self.kaikki if "aivo" not in x[0].name]), ["yllapito"])

    def test_kaistat_1_2_eivat_ota_aivoa_kolmen_kaistan_tilassa(self):
        self.assertNotIn("aivo", self.nimet(1))
        self.assertNotIn("aivo", self.nimet(2))
        self.assertEqual(self.nimet(1)[0], "portfolio")
        self.assertEqual(self.nimet(2)[0], "ansainta")
        self.assertEqual(self.nimet(1)[1], "yllapito")

    def test_alle_kolme_kaistaa_ei_aivo_rajausta(self):
        self.assertIn("aivo", self.nimet(1, kaistoja=2))
        self.assertIn("aivo", self.nimet(1, kaistoja=1))

    def test_kiinnitys_ylittaa_osuuden(self):
        pin = [(self.ts["portfolio"], 3)]
        self.assertEqual(self.nimet(3, odottavat=pin), ["portfolio"])
        self.assertEqual(self.nimet(1, odottavat=pin), [])

    def test_ilman_tavoitetta_ohitetaan_ja_kirjataan(self):
        p = kirj(self.h, "2026-11-09-09-ei-tavoitetta.md", "# T")
        ohit = []
        v = tavoite.valitse([(p, None)], 1, 3, ohitettu=lambda q, s: ohit.append(q.name))
        self.assertEqual(v, [])
        self.assertEqual(ohit, [p.name])

    def test_hylkays_kirjataan_kerran(self):
        t = tempfile.mkdtemp()
        self.assertTrue(tavoite.kirjaa_hylkays(t, "a.md", "syy"))
        self.assertFalse(tavoite.kirjaa_hylkays(t, "a.md", "syy"))
        self.assertEqual(len((Path(t) / tavoite.HYLKAYSLOKI).read_text().splitlines()), 1)


class AjuriValinta(unittest.TestCase):
    def test_varaa_seuraava_kunnioittaa_kaistaa_ja_ohittaa_tavoitteettoman(self):
        repo = Path(tempfile.mkdtemp()) / "docs"
        teht = repo / "claude-jono" / "tehtavat"
        teht.mkdir(parents=True)
        kirj(teht, "2026-11-09-01-ei-tavoitetta.md", "# T")
        kirj(teht, "2026-11-09-02-aivo-asia.md", "Tavoite: Aivo\n")
        kirj(teht, "2026-11-09-03-portfolio-asia.md", "Tavoite: portfolio\n")
        tila = repo.parent
        self.assertEqual(ajuri.varaa_seuraava(repo, tila, 3).name, "2026-11-09-02-aivo-asia.md")
        self.assertEqual(ajuri.varaa_seuraava(repo, tila, 1).name, "2026-11-09-03-portfolio-asia.md")
        self.assertIsNone(ajuri.varaa_seuraava(repo, tila, 2))   # jaljella vain tavoitteeton
        self.assertEqual(ajuri.avoimet_tehtavat(repo, tila), 0)   # varatut + tavoitteeton pois laskusta
        self.assertTrue((tila / tavoite.HYLKAYSLOKI).is_file())


class Jalkimittari(unittest.TestCase):
    def test_laskee_tavoitteittain(self):
        repo = Path(tempfile.mkdtemp())
        t, r = repo / "claude-jono" / "tehtavat", repo / "claude-jono" / "tulokset"
        t.mkdir(parents=True)
        r.mkdir()
        for nimi, tav, tulos in (("2026-11-09-01-a.md", "Aivo", "PR https://github.com/o/x/pull/5"),
                                 ("2026-11-09-02-b.md", "Aivo", "ei PR:aa"),
                                 ("2026-11-09-03-c.md", "ansainta", "https://github.com/o/y/pull/7 ja https://github.com/o/y/pull/8")):
            kirj(t, nimi, f"Tavoite: {tav}\n")
            kirj(r, nimi, tulos)
        kirj(t, "2026-11-09-04-d.md", "ei tavoitetta")   # ei tulosta -> ei lasketa
        m = tavoite.jalkimittari(repo, {"o/x#5", "o/y#8"}, date(2026, 11, 1))
        self.assertEqual((m["aivo"]["tehtavia"], m["aivo"]["pr"], m["aivo"]["yhdistetty"]), (2, 1, 1))
        self.assertEqual((m["ansainta"]["tehtavia"], m["ansainta"]["pr"], m["ansainta"]["yhdistetty"]), (1, 1, 1))
        self.assertEqual(tavoite.pr_avaimet(repo), {"o/x#5", "o/y#7", "o/y#8"})
        self.assertIn("ei tietoa", tavoite.jalkimittari_teksti(m, False))
        self.assertNotIn("ei tietoa", tavoite.jalkimittari_teksti(m, True))

    def test_haku_virhe_antaa_none(self):
        def rikki(osoite):
            raise OSError("verkko")
        self.assertIsNone(tavoite.yhdistetyt_haku({"o/x#1"}, rikki))
        self.assertEqual(tavoite.yhdistetyt_haku({"o/x#1", "o/x#2"}, lambda o: {"merged_at": "x"} if o.endswith("/1") else {"merged_at": None}), {"o/x#1"})


class AivoArvio(unittest.TestCase):
    def setUp(self):
        self.tila = tempfile.mkdtemp()
        self.t = kirj(self.tila, "2026-11-09-01-a.md", "# tehtava")

    def test_ei_arvioijaa_ei_kirjausta(self):
        self.assertFalse(aivo_arvio.arvioi_varjossa(self.tila, self.t, None))
        self.assertEqual(aivo_arvio.arviot(self.tila), {})

    def test_kirjaa_kerran_ja_virhe_ei_kaada(self):
        self.assertTrue(aivo_arvio.arvioi_varjossa(self.tila, self.t, lambda s: (True, "oleellinen")))
        self.assertFalse(aivo_arvio.arvioi_varjossa(self.tila, self.t, lambda s: (False, "toinen")))
        self.assertEqual(aivo_arvio.arviot(self.tila)[self.t.name]["syy"], "oleellinen")
        u = kirj(self.tila, "2026-11-09-02-b.md", "x")

        def rikki(s):
            raise RuntimeError("salainen")
        self.assertFalse(aivo_arvio.arvioi_varjossa(self.tila, u, rikki))
        self.assertFalse(aivo_arvio.arvioi_varjossa(self.tila, u, lambda s: ("kylla", "x")))   # ei bool

    def test_otos_osuma_ja_viikkoosio(self):
        for i in range(8):
            aivo_arvio.kirjaa_arvio(self.tila, f"t{i}.md", i % 2 == 0, "syy")
        o = aivo_arvio.otos(self.tila, 5, random.Random(1))
        self.assertEqual(len(o), 5)
        aivo_arvio.tarkista(self.tila, "t0.md", True)    # osuma
        aivo_arvio.tarkista(self.tila, "t1.md", True)    # ohi (Aivo sanoi ei)
        self.assertEqual(aivo_arvio.osuma(self.tila), (2, 1, 50.0, 8))
        self.assertNotIn("t0.md", [k for k, _ in aivo_arvio.otos(self.tila, 8)])
        self.assertIn("50.0 %", aivo_arvio.viikkoosio(self.tila))
        with self.assertRaises(KeyError):
            aivo_arvio.tarkista(self.tila, "tuntematon.md", True)

    def test_aja_ei_estyy_arviosta(self):
        # ajuri.aja kutsuu arvioijaa, mutta kaatuva arvioija ei esta ajoa: kutsu on aivo_arvio.arvioi_varjossa (poikkeus nielty)
        self.assertFalse(aivo_arvio.arvioi_varjossa(self.tila, Path(self.tila) / "puuttuu.md", lambda s: (True, "x")))


if __name__ == "__main__":
    unittest.main()
