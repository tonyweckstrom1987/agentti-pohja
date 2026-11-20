"""PR-rajan testit (Tonyn paatos A843): raja 5, rajatut erikseen, viimeistelytehtava sallittu."""
import unittest

from claudejono import prraja


def rivi(repo, n):
    return {"repo": repo, "numero": n}


def ryhmat(ap=0, muut=0, tony=0, repo="agentti-pohja"):
    return {"automerge": [rivi(repo, i) for i in range(ap)],
            "muut": [rivi(repo, 100 + i) for i in range(muut)],
            "tony": [rivi(repo, 200 + i) for i in range(tony)]}


class Raja(unittest.TestCase):
    def test_vakio_on_viisi(self):
        self.assertEqual(prraja.RAJA_AVOIMIA_PR, 5)

    def test_nelja_ei_ole_taynna_viisi_on(self):
        self.assertEqual(prraja.taynna(ryhmat(ap=3, muut=1)), set())
        self.assertEqual(prraja.taynna(ryhmat(ap=3, muut=2)), {"agentti-pohja"})

    def test_rajatut_eivat_kuluta_rajaa(self):
        r = ryhmat(ap=2, muut=2, tony=10)
        self.assertEqual(prraja.taynna(r), set())
        self.assertEqual(prraja.laske(r), {"agentti-pohja": 4})
        self.assertEqual(prraja.rajatut_luvut(r), {"agentti-pohja": 10})

    def test_repot_lasketaan_erikseen(self):
        r = ryhmat(ap=5)
        r["muut"] = [rivi("evalkit", 1)]
        self.assertEqual(prraja.taynna(r), {"agentti-pohja"})

    def test_tyhja(self):
        self.assertEqual(prraja.taynna({}), set())


class Suodatus(unittest.TestCase):
    TAYNNA = {"agentti-pohja"}

    def test_uusi_tyo_taynna_olevaan_repoon_lykataan(self):
        self.assertEqual(prraja.avaa_uuden_pr("Lisaa testi repoon agentti-pohja.", self.TAYNNA), "agentti-pohja")

    def test_viimeistely_sallitaan(self):
        for teksti in ("Korjaa hylatty PR repossa agentti-pohja.", "Pura ristiriidat agentti-pohjan PR:issa: agentti-pohja.",
                       "Sulje korvatut PR:t, repo agentti-pohja.", "Viimeistele agentti-pohja PR #12."):
            self.assertIsNone(prraja.avaa_uuden_pr(teksti, self.TAYNNA), teksti)

    def test_muu_repo_ei_lykkaannu(self):
        self.assertIsNone(prraja.avaa_uuden_pr("Lisaa testi repoon evalkit.", self.TAYNNA))

    def test_repon_nimi_osana_toista_nimea_ei_osu(self):
        self.assertIsNone(prraja.avaa_uuden_pr("Lisaa testi: agentti-pohja-purku.", self.TAYNNA))

    def test_suodata_sailyttaa_jarjestyksen(self):
        ts = [{"nimi": "a", "sisalto": "Lisaa X repoon agentti-pohja."}, {"nimi": "b", "sisalto": "Lisaa Y repoon evalkit."},
              {"nimi": "c", "sisalto": "Korjaa hylatty PR, agentti-pohja."}]
        sallitut, lykatyt = prraja.suodata(ts, self.TAYNNA)
        self.assertEqual([t["nimi"] for t in sallitut], ["b", "c"])
        self.assertEqual([(t["nimi"], r) for t, r in lykatyt], [("a", "agentti-pohja")])
        self.assertIn("agentti-pohja", prraja.lykkaysrivit(lykatyt))

    def test_ei_taynna_ei_suodatusta(self):
        ts = [{"nimi": "a", "sisalto": "Lisaa X repoon agentti-pohja."}]
        self.assertEqual(prraja.suodata(ts, set()), (ts, []))


class Haku(unittest.TestCase):
    def test_hae_taynna_kayttaa_keraajaa(self):
        self.assertEqual(prraja.hae_taynna(lambda: ryhmat(ap=5)), {"agentti-pohja"})

    def test_lukuvirhe_ei_lukitse_suunnittelua(self):
        def rikki():
            raise OSError("verkko")
        self.assertEqual(prraja.hae_taynna(rikki), set())


if __name__ == "__main__":
    unittest.main()
