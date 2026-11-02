import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error

from agentti import kysynta as k

TONY = {"login": "tonyweckstrom1987", "type": "User"}
BOTTI = {"login": "agenttifirma-bot", "type": "User"}
DEPENDABOT = {"login": "dependabot[bot]", "type": "Bot"}
VIERAS = {"login": "vieras", "type": "User"}


def lue(polku):
    with open(polku, encoding="utf-8") as f:
        return json.load(f)


def haku(vastaukset):
    """Palauttaa ensimmaisen vastauksen, jonka avain on osoitteessa. Poikkeusolio nostetaan; tuntematon osoite on virhe."""
    def hae(osoite):
        for avain, arvo in vastaukset.items():
            if avain in osoite:
                if isinstance(arvo, Exception):
                    raise arvo
                return arvo
        raise urllib.error.URLError("tuntematon " + osoite)
    return hae


TERVE = {"/stargazers": [TONY, VIERAS], "/forks": [{"owner": BOTTI}, {"owner": VIERAS}],
         "/issues": [{"user": VIERAS}, {"user": TONY}, {"user": VIERAS, "pull_request": {}}, {"user": DEPENDABOT, "pull_request": {}},
                     {"user": BOTTI, "pull_request": {}}],
         "downloads/point": {"downloads": 42}}


class UlkoinenTesti(unittest.TestCase):
    def test_omat_ja_botit_eivat_ole_ulkoisia(self):
        self.assertTrue(k.ulkoinen(VIERAS))
        for omat in (TONY, BOTTI, DEPENDABOT, {"login": "TonyWeckstrom1987", "type": "User"}, {"login": "x", "type": "Bot"},
                     {"login": "renovate[bot]", "type": "User"}, None, {}):
            self.assertFalse(k.ulkoinen(omat), omat)


class ReponLuvutTesti(unittest.TestCase):
    def test_botin_forkki_ja_oma_tahti_eivat_kasvata_lukuja(self):
        luvut, virheet = k.repo_luvut("mcp-doctor", haku(TERVE))
        self.assertEqual(luvut, {"tahdet": 1, "forkit": 1, "issuet": 1, "prt": 1})
        self.assertEqual(virheet, [])

    def test_yksi_epaonnistunut_haku_on_kysymysmerkki_ei_nolla_eika_vie_muita(self):
        vastaukset = dict(TERVE)
        vastaukset["/forks"] = urllib.error.URLError("salainen-osoite")
        luvut, virheet = k.repo_luvut("r", haku(vastaukset))
        self.assertEqual(luvut["forkit"], "?")
        self.assertEqual(luvut["tahdet"], 1)
        self.assertEqual(virheet, ["forkit: URLError"])
        self.assertNotIn("salainen-osoite", virheet[0])

    def test_taysi_sivu_on_alaraja(self):
        vastaukset = dict(TERVE)
        vastaukset["/stargazers"] = [VIERAS] * k.SIVUKOKO
        vastaukset["/issues"] = [{"user": VIERAS}] * k.SIVUKOKO
        luvut, _ = k.repo_luvut("r", haku(vastaukset))
        self.assertEqual(luvut["tahdet"], "%d+" % k.SIVUKOKO)
        self.assertEqual(luvut["issuet"], "%d+" % k.SIVUKOKO)
        self.assertEqual(luvut["prt"], "0+")


class NpmTesti(unittest.TestCase):
    def test_luku(self):
        self.assertEqual(k.npm_viikko("p", haku({"point": {"downloads": 0}})), (0, None))

    def test_ei_pakettia(self):
        self.assertEqual(k.npm_viikko(None, haku({})), ("-", None))

    def test_virhe_ja_viallinen_vastaus_ovat_kysymysmerkki(self):
        self.assertEqual(k.npm_viikko("p", haku({"point": TimeoutError()})), ("?", "npm: TimeoutError"))
        self.assertEqual(k.npm_viikko("p", haku({"point": {"error": "not found"}}))[0], "?")
        self.assertEqual(k.npm_viikko("p", haku({"point": {"downloads": True}}))[0], "?")


class MuotoileTesti(unittest.TestCase):
    def test_muutos_vain_tarkoista_luvuista(self):
        self.assertEqual(k._muutos(5, 3), " (+2)")
        self.assertEqual(k._muutos(3, 5), " (-2)")
        self.assertEqual(k._muutos(3, 3), "")
        self.assertEqual(k._muutos("?", 3), "")
        self.assertEqual(k._muutos(3, "?"), "")
        self.assertEqual(k._muutos("5+", 3), "")
        self.assertEqual(k._muutos(3, None), "")

    def test_taulukko_virheet_ja_vertailu(self):
        luvut = {"a": {"npm_viikko": 9, "tahdet": 2, "forkit": "?", "issuet": 0, "prt": 0}}
        teksti = k.muotoile(luvut, ["a forkit: URLError"], {"a": {"npm_viikko": 4, "tahdet": 2}}, "2026-11-01")
        self.assertIn("| a | 9 (+5) | 2 | ? | 0 | 0 |", teksti)
        self.assertIn("muutos mittaukseen 2026-11-01", teksti)
        self.assertIn("- a forkit: URLError", teksti)


class MainTesti(unittest.TestCase):
    def ajo(self, argv, hae, tanaan):
        ulos = io.StringIO()
        with contextlib.redirect_stdout(ulos):
            koodi = k.main(argv, hae=hae, tanaan=tanaan)
        return koodi, ulos.getvalue()

    def test_historia_ja_muutos_edelliseen_paivaan(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "ali", "k.json")
            self.assertEqual(self.ajo(["--tila", polku], haku(TERVE), "2026-11-01")[0], 0)
            vastaukset = dict(TERVE)
            vastaukset["downloads/point"] = {"downloads": 50}
            koodi, teksti = self.ajo(["--tila", polku], haku(vastaukset), "2026-11-08")
            self.assertEqual(koodi, 0)
            self.assertIn("50 (+8)", teksti)
            self.assertEqual(sorted(lue(polku)), ["2026-11-01", "2026-11-08"])
            # Saman paivan toinen ajo vertaa edelliseen paivaan eika itseensa, ja korvaa paivan mittauksen.
            vastaukset["downloads/point"] = {"downloads": 51}
            _, teksti = self.ajo(["--tila", polku], haku(vastaukset), "2026-11-08")
            self.assertIn("51 (+9)", teksti)
            self.assertEqual(lue(polku)["2026-11-08"]["mcp-doctor"]["npm_viikko"], 51)
            self.assertEqual([n for n in os.listdir(os.path.dirname(polku)) if n.startswith(".")], [])

    def test_historia_karsitaan(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            tila = {"2020-01-%02d" % i: {} for i in range(1, 11)}
            alkup, k.HISTORIA_MAX = k.HISTORIA_MAX, 3
            try:
                k.kirjoita_tila(polku, tila, "2026-01-01", {})
            finally:
                k.HISTORIA_MAX = alkup
            self.assertEqual(sorted(lue(polku)), ["2020-01-09", "2020-01-10", "2026-01-01"])
            self.assertEqual(len(tila), 10)

    def test_rikkinainen_tila_ei_kaada(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            for sisalto in ("{rikki", "[1]"):
                with open(polku, "w") as f:
                    f.write(sisalto)
                self.assertEqual(k.lue_tila(polku), {})

    def test_kaikki_haut_epaonnistuvat_exit_1_eika_nollia(self):
        koodi, teksti = self.ajo([], haku({}), "2026-11-02")
        self.assertEqual(koodi, 1)
        self.assertIn("| mcp-doctor | ? | ? | ? | ? | ? |", teksti)
        self.assertIn("| llm-stopcheck | - | ? | ? | ? | ? |", teksti)

    def test_ilman_tilaa_ei_kirjoiteta(self):
        with tempfile.TemporaryDirectory() as d:
            nyt = os.getcwd()
            os.chdir(d)
            try:
                self.ajo([], haku(TERVE), "2026-11-02")
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(nyt)


if __name__ == "__main__":
    unittest.main()
