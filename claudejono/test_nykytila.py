import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from claudejono import ajuri, nykytila, tarkastaja as tk
from claudejono.test_ajuri import Pohja
from claudejono.test_tarkastaja import ENV, Feikki, SHA, pr_olio, tiedosto

RIVI = "Nykytila tarkistettu: 01-infra.md \u2192 varmuuskopio ajaa joka y\u00f6, rclone-kohde on Drive"
UUSI = "2026-11-07-01-varmuuskopio.md"
VANHA = "2026-11-05-01-varmuuskopio.md"
INFRATEHTAVA = "# Varmuuskopio\n\nLuo uusi varmuuskopio ja ajastin CT115:lle.\n\nValmis kun: ajastin toimii."


class RiviTesti(unittest.TestCase):
    def test_kelvollinen_rivi_hyvaksytaan(self):
        for rivi in (RIVI, "Nykytila tarkistettu: `systemctl list-timers` -> ei ajastinta", "**Nykytila tarkistettu:** tiedosto x \u2192 y",
                     "> Nykytila tarkistettu: tiedosto x => loydos"):
            self.assertTrue(nykytila.on_nykytila("Teksti\n" + rivi + "\n"), rivi)

    def test_tyhja_paikkamerkki_ja_nuoleton_rivi_hylataan(self):
        for rivi in ("Nykytila tarkistettu:", "Nykytila tarkistettu: kyll\u00e4", "Nykytila tarkistettu: 01-infra.md \u2192",
                     "Nykytila tarkistettu: \u2192 loytyi", "Nykytila tarkistettu: <l\u00e4hde> \u2192 <mit\u00e4 l\u00f6ytyi>",
                     "Nykytila tarkistettu: TBD \u2192 TBD", "Nykytila tarkistettu: x \u2192 -"):
            self.assertFalse(nykytila.on_nykytila(rivi), rivi)

    def test_rivi_koodilohkossa_ei_lasketa(self):
        self.assertFalse(nykytila.on_nykytila("Esimerkki:\n```\n" + RIVI + "\n```\n"))


class TehtavaTesti(unittest.TestCase):
    def test_infratehtava_ilman_rivia_hylataan(self):
        self.assertIn("Nykytila tarkistettu", nykytila.tehtavan_virhe(UUSI, INFRATEHTAVA))

    def test_infratehtava_rivin_kanssa_hyvaksytaan(self):
        self.assertIsNone(nykytila.tehtavan_virhe(UUSI, INFRATEHTAVA + "\n" + RIVI))

    def test_jokainen_avainsana_laukaisee(self):
        for sana in ("varmuuskopio", "cron-ty\u00f6", "ajastin", "palvelu", "valvonta", "rclone", "systemd-yksikk\u00f6", "kontti"):
            self.assertIsNotNone(nykytila.tehtavan_virhe(UUSI, "Asenna uusi %s." % sana), sana)

    def test_pelkka_maininta_tai_kielto_ei_laukaise(self):
        for teksti in ("Lue palvelun loki ja tee yhteenveto.", "Ala asenna uutta palvelua.", "Kirjoita testi parserille.",
                       "Raportoi valvonnan tila."):
            self.assertIsNone(nykytila.tehtavan_virhe(UUSI, teksti), teksti)

    def test_vanhat_tehtavat_eivat_hylkaannu_jalkikateen(self):
        self.assertIsNone(nykytila.tehtavan_virhe(VANHA, INFRATEHTAVA))


class AjuriTesti(Pohja):
    def test_ajuri_hylkaa_ilman_rivia_ja_jattaa_rivillisen_ajoon(self):
        self.lisaa_tehtava(UUSI, INFRATEHTAVA)
        self.lisaa_tehtava("2026-11-07-02-varmuuskopio-b.md", INFRATEHTAVA + "\n" + RIVI)
        viestit = []
        hylatyt = ajuri.hylkaa_ilman_nykytilaa(self.repo, viestit.append)
        self.assertEqual(hylatyt, [UUSI])
        tulos = (self.repo / ajuri.JONO / "tulokset" / UUSI).read_text(encoding="utf-8")
        self.assertIn("- Tila: HYLATTY", tulos)
        self.assertFalse((self.repo / ajuri.JONO / "tulokset" / "2026-11-07-02-varmuuskopio-b.md").exists())
        self.assertEqual([p.name for p in ajuri.odottavat_tehtavat(self.repo)], ["2026-11-07-02-varmuuskopio-b.md"])
        self.assertIn(UUSI, viestit[0])

    def test_ei_hylattavaa_ei_committia(self):
        self.lisaa_tehtava(UUSI, "Kirjoita testi.")
        self.assertEqual(ajuri.hylkaa_ilman_nykytilaa(self.repo), [])


class PrTesti(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.env = dict(ENV, CLAUDEJONO_TILA=self.d.name)
        self.ajot = []

    def tearDown(self):
        self.d.cleanup()

    def suorita(self, kom, **kw):
        self.ajot.append(kom)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def kierros(self, tiedostonimi, body, status="added"):
        api = Feikki([pr_olio(body=body)], [dict(tiedosto(tiedostonimi), status=status)], [])
        tulos = tk.kierros(self.env, "ghp_" + "x" * 30, suorita=self.suorita, avaa=api)
        return tulos, api

    def test_isanta_lisays_ilman_rivia_hylataan_ilman_mallia(self):
        tulos, api = self.kierros("isanta/uusi.sh", "Lisaa skriptin.")
        self.assertEqual(tulos[0]["tulos"], "HYLATTY")
        self.assertEqual(self.ajot, [])
        self.assertIn("paatos=HYLATTY", api.postit[0][1]["body"])

    def test_yojono_ajastus_ilman_rivia_hylataan(self):
        tulos, _ = self.kierros("yojono/systemd/uusi.timer.mall", "Lisaa ajastimen.")
        self.assertEqual(tulos[0]["tulos"], "HYLATTY")

    def test_rivin_kanssa_ei_hylata_rakenteen_takia(self):
        tulos, _ = self.kierros("isanta/uusi.sh", "Kuvaus\n" + RIVI)
        self.assertNotEqual(tulos[0].get("syy"), "nykytilarivi puuttuu")

    def test_muokkaus_ja_muu_hakemisto_ei_vaadi_rivia(self):
        self.assertFalse(nykytila.pr_vaatii_nykytilan("agentti-pohja", [{"filename": "isanta/kylla.py", "status": "modified"}]))
        self.assertFalse(nykytila.pr_vaatii_nykytilan("agentti-pohja", [{"filename": "agentti/uusi.py", "status": "added"}]))
        self.assertFalse(nykytila.pr_vaatii_nykytilan("evalkit", [{"filename": "isanta/x", "status": "added"}]))

    def test_hylkays_kirjataan_kerran_per_commit(self):
        api = Feikki([pr_olio(body="x")], [dict(tiedosto("isanta/a.sh"), status="added")], [])
        tk.kierros(self.env, "ghp_" + "x" * 30, suorita=self.suorita, avaa=api)
        api.kommentit = [{"user": {"login": "agenttifirma-bot"}, "body": api.postit[0][1]["body"]}]
        api.postit.clear()
        tulos = tk.kierros(self.env, "ghp_" + "x" * 30, suorita=self.suorita, avaa=api)
        self.assertEqual((tulos, api.postit), ([], []))


if __name__ == "__main__":
    unittest.main()
