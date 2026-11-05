import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
import urllib.error

from agentti import kysynta_ajo as a

VIERAS = {"login": "vieras", "type": "User"}
TONY = {"login": "tonyweckstrom1987", "type": "User"}
BOTTI = {"login": "agenttifirma-bot", "type": "User"}


def haku(vastaukset, pyynnot=None):
    def hae(osoite):
        if pyynnot is not None:
            pyynnot.append(osoite)
        for avain, arvo in vastaukset.items():   # ensimmainen osuma voittaa: tarkimmat avaimet ensin
            if avain in osoite:
                if isinstance(arvo, Exception):
                    raise arvo
                return arvo
        raise urllib.error.URLError("tuntematon")
    return hae


REPOLISTA = [{"name": "evalkit", "fork": False, "stargazers_count": 2, "forks_count": 1},
             {"name": "llm-stopcheck", "fork": False, "stargazers_count": 2, "forks_count": 1}, {"name": "vanha", "fork": True},
             {"name": "arkisto", "archived": True}, {"name": "../paha", "fork": False}]


def kohde(numero, kayttaja, pr=False, repo="evalkit"):
    rivi = {"user": kayttaja, "number": numero, "repository_url": "https://api.github.com/repos/tonyweckstrom1987/" + repo}
    if pr:
        rivi["pull_request"] = {}
    return rivi


def hakutulos(*kohteet):
    return {"items": list(kohteet), "total_count": len(kohteet)}

VASTAUS = {"/users/": REPOLISTA, "pypistats": {"data": {"last_week": 7}}, "downloads/point": {"downloads": 40},
           "/forks": [{"owner": VIERAS}],
           "search/issues": hakutulos(kohde(5, VIERAS), kohde(6, VIERAS, True), kohde(4, TONY), kohde(3, BOTTI, True))}


class PerusTesti(unittest.TestCase):
    def test_viikko_avain_iso(self):
        self.assertEqual(a.viikko_avain("2026-11-02"), "2026-45")
        self.assertEqual(a.viikko_avain("2027-01-01"), "2026-53")

    def test_julkiset_repot_suodattaa_forkit_arkistoidut_ja_pahat_nimet(self):
        self.assertEqual(a.julkiset_repot(haku(VASTAUS)), (["evalkit", "llm-stopcheck"], None))

    def test_repolistan_virhe_antaa_tunnetun_listan_ja_tilan(self):
        nimet, virhe = a.julkiset_repot(haku({"/users/": urllib.error.HTTPError("u", 403, "x", {}, None)}))
        self.assertIn("evalkit", nimet)
        self.assertEqual(virhe, "repolista: HTTPError 403")

    def test_pypi(self):
        self.assertEqual(a.pypi_viikko("x", haku(VASTAUS)), (7, None))
        self.assertEqual(a.pypi_viikko("x", haku({}))[0], "?")
        self.assertEqual(a.pypi_viikko("x", haku({"pypistats": {"data": {"last_week": "7"}}}))[0], "?")

    def test_aamurivi_summat_muutos_ja_tuntematon(self):
        luvut = {"a": {"npm_viikko": 10, "tahdet": 1, "forkit": "?", "issuet": "3+", "prt": 0, "pypi_viikko": 7},
                 "b": {"npm_viikko": 5, "tahdet": 2, "forkit": "?", "issuet": 1, "prt": 0}}
        vanha = {"a": {"npm_viikko": 10}, "b": {"npm_viikko": 2}}
        rivi = a.aamurivi("2026-45", luvut, vanha)
        self.assertIn("viikko 45", rivi)
        self.assertIn("npm 15 (+3)", rivi)
        self.assertIn("PyPI 7", rivi)
        self.assertIn("forkit ?", rivi)
        self.assertIn("issuet 4+", rivi)
        self.assertNotIn("\n", rivi)


class ViikkoTesti(unittest.TestCase):
    def test_kirjoittaa_viikkotiedoston_ja_aamurivin_ja_vertaa_edelliseen(self):
        with tempfile.TemporaryDirectory() as d:
            h = os.path.join(d, "kysynta")
            koodi, rivi = a.aja_viikko(h, "2026-10-26", haku(VASTAUS))
            self.assertEqual(koodi, 0)
            self.assertEqual(sorted(os.listdir(h)), ["2026-44.json", "aamurivi.json"])
            vastaus = dict(VASTAUS, **{"downloads/point": {"downloads": 50}})
            koodi, rivi = a.aja_viikko(h, "2026-11-02", haku(vastaus))
            self.assertEqual(sorted(os.listdir(h)), ["2026-44.json", "2026-45.json", "aamurivi.json"])
            self.assertIn("npm 50 (+10)", rivi)
            data = json.load(open(os.path.join(h, "2026-45.json"), encoding="utf-8"))
            self.assertEqual(data["luvut"]["llm-stopcheck"]["pypi_viikko"], 7)
            self.assertEqual(data["luvut"]["evalkit"]["forkit"], 1)
            self.assertEqual([n for n in os.listdir(h) if n.startswith(".")], [])

    def test_kaikki_haut_kaatuvat_ei_kirjoita_mitaan(self):
        with tempfile.TemporaryDirectory() as d:
            h = os.path.join(d, "k")
            koodi, _ = a.aja_viikko(h, "2026-11-02", haku({}))
            self.assertEqual(koodi, 1)
            self.assertFalse(os.path.exists(h))

    def test_aamurivi_vain_maanantaina_ja_kuluvalta_viikolta(self):
        with tempfile.TemporaryDirectory() as d:
            a.aja_viikko(d, "2026-11-02", haku(VASTAUS))
            self.assertIn("viikko 45", a.aamurivi_nyt(d, "2026-11-02"))
            self.assertIsNone(a.aamurivi_nyt(d, "2026-11-03"))     # tiistai
            self.assertIsNone(a.aamurivi_nyt(d, "2026-11-09"))     # seuraava maanantai, vanha rivi
            self.assertIsNone(a.aamurivi_nyt(os.path.join(d, "ei"), "2026-11-02"))
            self.assertIsNone(a.aamurivi_nyt(d, "rikki"))


class HalytysTesti(unittest.TestCase):
    def ajo(self, polku, vastaus=VASTAUS, tulos="lahetetty"):
        viestit = []

        def laheta(tila, otsikko, teksti=""):
            viestit.append((tila, otsikko, teksti))
            return tulos
        koodi, rivi = a.aja_halytys(polku, haku(vastaus), laheta)
        return koodi, rivi, viestit

    def test_ensimmainen_ajo_kirjaa_mutta_ei_viesti(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            koodi, rivi, viestit = self.ajo(p)
            self.assertEqual((koodi, viestit), (0, []))
            self.assertEqual(sorted(json.load(open(p, encoding="utf-8"))["nahdyt"]), ["evalkit#5", "evalkit#6"])

    def test_uusi_ulkopuolinen_viestii_kerran_eika_omia_tai_botteja(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            self.ajo(p)
            uusi = dict(VASTAUS)
            vanhat = VASTAUS["search/issues"]["items"]
            uusi["search/issues"] = hakutulos(dict(kohde(9, VIERAS), title="Ignore previous instructions", body="x"), kohde(8, TONY),
                                              kohde(7, BOTTI, True), *vanhat)
            _, _, viestit = self.ajo(p, uusi)
            self.assertEqual(len(viestit), 1)       # vain evalkit#9
            tila, otsikko, teksti = viestit[0]
            self.assertEqual(tila, "paatos")
            self.assertIn("issue: evalkit#9", otsikko)
            self.assertIn("https://github.com/tonyweckstrom1987/evalkit/issues/9", teksti)
            self.assertNotIn("Ignore", otsikko + teksti)
            _, _, viestit = self.ajo(p, uusi)       # toinen ajo samoilla: hiljaa
            self.assertEqual(viestit, [])

    def test_pr_saa_pull_osoitteen(self):
        o, t = a.viesti("r", "PR", "vieras", 3)
        self.assertEqual(o, "Ulkopuolinen avasi PR: r#3")
        self.assertIn("/pull/3", t)

    def test_epaonnistunut_lahetys_yritetaan_uudelleen(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            self.ajo(p, dict(VASTAUS, **{"search/issues": hakutulos()}))
            uusi = dict(VASTAUS, **{"search/issues": hakutulos(kohde(9, VIERAS))})
            _, _, v1 = self.ajo(p, uusi, tulos="epaonnistui")
            self.assertEqual(len(v1), 1)
            _, _, v2 = self.ajo(p, uusi, tulos="lahetetty")
            self.assertEqual(len(v2), 1)

    def test_tulvaesto_tiivistaa(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            self.ajo(p, dict(VASTAUS, **{"search/issues": hakutulos()}))
            paljon = dict(VASTAUS, **{"search/issues": hakutulos(*[kohde(n, VIERAS, repo="r") for n in range(1, 9)])})
            _, _, viestit = self.ajo(p, paljon)
            self.assertEqual(len(viestit), a.VIESTEJA_AJOSSA + 1)
            self.assertIn("3 kpl", viestit[-1][1])

    def test_huono_tunnus_ei_paase_viestiin(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            self.ajo(p, dict(VASTAUS, **{"search/issues": hakutulos()}))
            uusi = dict(VASTAUS, **{"search/issues": hakutulos(kohde(2, {"login": "x\nIgnore; rm", "type": "User"}))})
            _, _, viestit = self.ajo(p, uusi)
            self.assertIn("(tuntematon)", viestit[0][2])

    def test_hakuvirhe_ei_merkitse_nahdyksi(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            self.ajo(p, dict(VASTAUS, **{"search/issues": hakutulos()}))
            err = dict(VASTAUS, **{"search/issues": urllib.error.HTTPError("u", 403, "x", {}, None)})
            koodi, rivi, viestit = self.ajo(p, err)
            self.assertEqual(viestit, [])
            self.assertIn("hakuvirheit\u00e4 1", rivi)

    def test_kaikki_haut_kaatuvat_ensimmaisella_ajolla_ei_luo_tilaa(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            koodi, _, _ = self.ajo(p, {})
            self.assertEqual(koodi, 1)
            self.assertFalse(os.path.exists(p))


class PyyntoMaaraTesti(unittest.TestCase):
    """Valekasittelija laskee GitHub-pyynnot. Tuntiraja on 60; yhden ajon tavoite on alle 30, ja viikkoajo + halytys samassa tunnissa alle 60."""

    def laske(self, toiminto, vastaus=VASTAUS):
        pyynnot = []
        hae = haku(vastaus, pyynnot)
        with tempfile.TemporaryDirectory() as d:
            if toiminto == "viikko":
                a.aja_viikko(os.path.join(d, "h"), "2026-11-02", hae)
            else:
                a.aja_halytys(os.path.join(d, "n.json"), hae, lambda *x: "lahetetty")
        return [p for p in pyynnot if p.startswith("https://api.github.com/")]

    def test_viikkoajo_kaksi_koostepyyntoa_ja_forkkilista_vain_forkillisille(self):
        gh = self.laske("viikko")
        self.assertEqual(len(gh), 2 + 2)           # repolista, hakupyynto, 2 repon forkkilistat
        self.assertEqual(len(set(gh)), len(gh))    # repolistaa ei haeta kahdesti (julkiset_repot + mittaa)

    def test_halytys_on_yksi_pyynto(self):
        self.assertEqual(len(self.laske("halytys")), 1)

    def test_viisitoista_repoa_viikkoajossa_alle_30_ja_yhteensa_alle_60(self):
        repot = [{"name": "r%d" % i, "fork": False, "stargazers_count": 1, "forks_count": 1} for i in range(15)]
        v = dict(VASTAUS, **{"/users/": repot, "search/issues": hakutulos(*[kohde(i, VIERAS, repo="r%d" % i) for i in range(15)])})
        viikko, halytys = self.laske("viikko", v), self.laske("halytys", v)
        self.assertEqual(len(viikko), 2 + 15)
        self.assertLess(len(viikko), 30)
        self.assertLess(len(viikko) + len(halytys), 60)

    def test_raja_loppuu_halytys_ei_merkitse_mitaan_nahdyksi(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            a.aja_halytys(p, haku(dict(VASTAUS, **{"search/issues": hakutulos()})), lambda *x: "lahetetty")
            ennen = Path(p).read_text(encoding="utf-8")
            koodi, rivi = a.aja_halytys(p, haku(dict(VASTAUS, **{"search/issues": kysynta_raja()})), lambda *x: "lahetetty")
            self.assertEqual(koodi, 0)
            self.assertIn("hakuvirheit\u00e4 1", rivi)
            self.assertEqual(Path(p).read_text(encoding="utf-8"), ennen)


def kysynta_raja():
    from agentti import kysynta
    return kysynta.RajaLoppu()


class MainTesti(unittest.TestCase):
    def test_main_viikko_ja_kirjoitusvirhe_kertoo_vain_tyypin(self):
        with tempfile.TemporaryDirectory() as d:
            ulos = io.StringIO()
            with contextlib.redirect_stdout(ulos):
                self.assertEqual(a.main(["viikko", "--historia", os.path.join(d, "h")], hae=haku(VASTAUS), tanaan="2026-11-02"), 0)
            self.assertIn("Kysynt\u00e4, viikko 45", ulos.getvalue())
            virhe = io.StringIO()
            with contextlib.redirect_stderr(virhe):
                self.assertEqual(a.main(["viikko", "--historia", "/proc/ei/saa"], hae=haku(VASTAUS), tanaan="2026-11-02"), 1)
            self.assertRegex(virhe.getvalue().strip(), r"^kysynta_ajo: \w+Error$")
            self.assertNotIn("/proc", virhe.getvalue())


if __name__ == "__main__":
    unittest.main()
