import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from agentti import oppimittari as om
from agentti import viikkoraportti as vr

NYT = date(2030, 6, 15)     # kiintea "nyt" annetaan parametrina: mittari ei lue kelloa


def rivi(ika_pv, teksti):
    """Virhelokin rivi: sama muoto kuin oikeassa lokissa."""
    p = (NYT - timedelta(days=ika_pv)).isoformat()
    return "- %s: %s" % (p, teksti)


def loki(*rivit):
    return "# Virheloki\n\n" + "\n".join(rivit) + "\n"


# Virhemittarin saantotaulun tunnisteet: "(juurisyy N)" ja LOKI_SAANNOT-osat.
class RiviTesti(unittest.TestCase):
    def test_kayttaa_virhemittarin_luokkia(self):
        r = om.rivi(loki(rivi(1, "x (juurisyy 1)"), rivi(2, "y (juurisyy 2)")), NYT)
        self.assertIn("1 tehty\u2260toimii x1", r)
        self.assertIn("2 muistista x1", r)

    def test_sama_luokka_kahtena_viikkona_ehdottaa_koodia(self):
        r = om.rivi(loki(rivi(8, "x (juurisyy 1)"), rivi(1, "y (juurisyy 1)")), NYT)
        self.assertIn("luokka 1 kahtena viikkona", r)
        self.assertIn("korjausehdotus koodiin", r)

    def test_eri_luokat_eivat_ole_toisto(self):
        r = om.rivi(loki(rivi(8, "x (juurisyy 1)"), rivi(1, "y (juurisyy 2)")), NYT)
        self.assertTrue(r.rstrip(".").endswith("toistuiko sama juurisyy: ei"))

    def test_kolmas_kerta_menee_tonylle(self):
        r = om.rivi(loki(rivi(15, "x (juurisyy 3)"), rivi(8, "y (juurisyy 3)"), rivi(1, "z (juurisyy 3)")), NYT)
        self.assertIn("kolmas kerta", r)
        self.assertIn("Tonylle", r)

    def test_luokittelematon_ei_ole_toisto(self):
        r = om.rivi(loki(rivi(8, "aivan uusi asia"), rivi(1, "toinen uusi asia")), NYT)
        self.assertIn("luokittelematta x1", r)
        self.assertTrue(r.rstrip(".").endswith("toistuiko sama juurisyy: ei"))

    def test_toisto_lasketaan_paivan_mukaan_ei_tiedostojarjestyksesta(self):
        a = om.rivi(loki(rivi(1, "y (juurisyy 1)"), rivi(8, "x (juurisyy 1)")), NYT)
        b = om.rivi(loki(rivi(8, "x (juurisyy 1)"), rivi(1, "y (juurisyy 1)")), NYT)
        self.assertEqual(a, b)

    def test_saanto_ascii_ja_umlaut_sama_tulos(self):
        t1 = "- %s: x (juurisyy 1). Saanto: tarkista muistista." % (NYT - timedelta(days=1))
        t2 = "- %s: x (juurisyy 1). S\u00e4\u00e4nt\u00f6: tarkista muistista." % (NYT - timedelta(days=1))
        self.assertEqual(om.rivi(loki(t1), NYT), om.rivi(loki(t2), NYT).replace("S\u00e4\u00e4nt\u00f6", "Saanto"))

    def test_paivamaaraton_otsikko_katkaisee_otsikkomerkinnan(self):
        t = "## %d.%d.%d: git stash\n- Mita: x\n## Muu osio\n- Miksi: x (juurisyy 1)\n" % (
            (NYT - timedelta(days=1)).day, (NYT - timedelta(days=1)).month, (NYT - timedelta(days=1)).year)
        r = om.rivi(t, NYT)
        self.assertIn("virhelokimerkint\u00f6j\u00e4 1", r)
        self.assertIn("3 v\u00e4\u00e4r\u00e4 oletus x1", r)      # "git stash" -saanto osuu otsikkoon
        self.assertNotIn("tehty\u2260toimii", r)       # tunniste kuului muuhun osioon, ei merkintaan

    def test_puuttuva_loki(self):
        self.assertIn("ei saatu luettua", om.rivi(None, NYT))

    def test_tyhja_loki_sanoo_data_ei_riita(self):
        self.assertIn("data ei riit", om.rivi("# Virheloki\n", NYT))

    def test_vanha_data_ei_ole_taman_viikon_luku(self):
        self.assertIn("data ei riit", om.rivi(loki(rivi(60, "x (juurisyy 1)")), NYT))

    def test_rivi_on_lyhyt(self):
        t = loki(*[rivi(1, "x%d (juurisyy %d)" % (i, 1 + i % 4)) for i in range(30)])
        self.assertLessEqual(len(om.rivi(t, NYT)), 500)

    def test_main_ilman_tiedostoa_ei_kaadu(self):
        self.assertEqual(om.main(["/ei/ole/olemassa.md"]), 2)


class ViikkoraporttiLiitosTesti(unittest.TestCase):
    TIEDOT = {"paivaa": 7, "tapahtumia": 0, "virheita": 0, "virhekoodit": {}, "ohitettuja": 0, "ohituskoodit": {},
              "kulu_usd": 0.0, "kulu_agentti": {}, "hyvaksymis_pros": None, "paatettyja": 0}

    def test_rivi_mukana_vihjeiden_jalkeen(self):
        t = vr.muodosta(self.TIEDOT, {}, "Oppiminen: testirivi.")
        self.assertIn("Oppiminen: testirivi.", t)
        self.assertGreater(t.index("Oppiminen"), t.index("Vihjeet"))

    def test_pitka_oppimisrivi_ei_katkaise_vihjeita(self):
        # Tarkastajan loydos: TEKSTI_MAX-katkaisu leikkasi vihjeet (levy- ja muistihalytykset), kun oppimisrivi oli niiden edella.
        tiedot = dict(self.TIEDOT, tapahtumia=500, virheita=300, ohitettuja=40, kulu_usd=12.5, hyvaksymis_pros=10.0, paatettyja=9,
                      virhekoodit={"virhekoodi_pitka_%d" % i: i + 30 for i in range(30)},
                      ohituskoodit={"ohituskoodi_pitka_%d" % i: 5 for i in range(30)},
                      kulu_agentti={"agentti_nimi_%d" % i: 3.1 for i in range(30)})
        res = {"levy_kaytetty_pros": 95.0, "muisti_vapaa_pros": 5.0, "kuorma_1min": 3.0}
        t = vr.muodosta(tiedot, res, "Oppiminen: " + "x" * 450 + ".")
        self.assertLessEqual(len(t), vr.TEKSTI_MAX)
        self.assertIn("Levy 95", t)
        self.assertIn("harkitse lisamuistia.", t)

    def test_ilman_rivia_ennallaan(self):
        self.assertNotIn("Oppiminen", vr.muodosta(self.TIEDOT, {}))

    def test_oppimisrivi_lukee_ympariston_polun(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "virheloki.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(loki(rivi(1, "uusi (juurisyy 1)")))
            with mock.patch.dict(os.environ, {"VIRHELOKI": p}):
                self.assertIn("Oppiminen:", vr.oppimisrivi(NYT))
            with mock.patch.dict(os.environ, {"VIRHELOKI": os.path.join(d, "puuttuu.md")}):
                r = vr.oppimisrivi(NYT)
                self.assertTrue(r.startswith("Oppiminen:"))      # puuttuva loki ei katoa hiljaa
                self.assertIn("ei l\u00f6ytynyt", r)

    def test_oletuspolut_kokeillaan_jarjestyksessa(self):
        with tempfile.TemporaryDirectory() as d:
            toinen = os.path.join(d, "toinen.md")
            with open(toinen, "w", encoding="utf-8") as f:
                f.write(loki(rivi(1, "uusi (juurisyy 1)")))
            env = {k: v for k, v in os.environ.items() if k != "VIRHELOKI"}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(vr, "VIRHELOKI_OLETUKSET", (os.path.join(d, "eka.md"), toinen)):
                self.assertIn("virhelokimerkint\u00f6j\u00e4 1", vr.oppimisrivi(NYT))
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(vr, "VIRHELOKI_OLETUKSET", (os.path.join(d, "eka.md"), os.path.join(d, "toka.md"))):
                self.assertIn("ei l\u00f6ytynyt", vr.oppimisrivi(NYT))

    def test_oletuspolku_on_ct115_n_loki(self):
        self.assertEqual(vr.VIRHELOKI_OLETUKSET[0], "/var/lib/claude-jono/docs/claude-jono/virheloki.md")
        self.assertIn("/opt/agenttifirma-docs/claude-jono/virheloki.md", vr.VIRHELOKI_OLETUKSET)

    def test_main_tulostaa_rivin_vaikka_loki_puuttuu(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            from agentti import tapahtumaloki
            tapahtumaloki.avaa(db).close()
            with mock.patch.dict(os.environ, {"TAPAHTUMALOKI": db, "VIRHELOKI": os.path.join(d, "ei.md")}), \
                    mock.patch("sys.stdout") as ulos:
                vr.main(["7"])
            self.assertIn("Oppiminen:", "".join(c.args[0] for c in ulos.write.call_args_list))


class KuvausVastaaKoodiaTesti(unittest.TestCase):
    """Tarkastajan loydos (PR #198): kuvaus ja muutosloki puhuivat toteutuksesta, jota koodissa ei ole."""
    JUURI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _lue(self, *osat):
        with open(os.path.join(self.JUURI, *osat), encoding="utf-8") as f:
            return f.read()

    def test_muutosloki_ei_viittaa_poistettuun_koodiin(self):
        loki = self._lue("docs", "muutokset", "oppimisen-mittari-korjaukset.md")
        for poistettu in ("SYY_RE", "JUURISYYT", "[juurisyy: nimi]"):
            self.assertNotIn(poistettu, loki)
        self.assertIn("virhemittari", loki)

    def test_koodissa_ei_omaa_juurisyytaulukkoa(self):
        for osat in (("agentti", "oppimittari.py"), ("agentti", "viikkoraportti.py")):
            koodi = self._lue(*osat)
            self.assertNotIn("JUURISYYT", koodi)
            self.assertNotIn("SYY_RE", koodi)
        self.assertIn("virhemittari", self._lue("agentti", "oppimittari.py"))


if __name__ == "__main__":
    unittest.main()
