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


def lokiin(hae, pyynnot):
    def kirjaava(osoite):
        pyynnot.append(osoite)
        return hae(osoite)
    return kirjaava


REPOLISTA = [{"name": r, "stargazers_count": 3, "forks_count": 2} for r in k.REPOT]


def kohde(numero, kayttaja, pr=False, repo="evalkit"):
    """Yksi hakutuloksen rivi (search/issues): issue tai PR."""
    rivi = {"user": kayttaja, "number": numero, "repository_url": "https://api.github.com/repos/tonyweckstrom1987/" + repo}
    if pr:
        rivi["pull_request"] = {}
    return rivi


def hakutulos(*kohteet, kokonais=None):
    return {"items": list(kohteet), "total_count": len(kohteet) if kokonais is None else kokonais}


TERVE = {"/forks": [{"owner": BOTTI}, {"owner": VIERAS}],
         "search/issues": hakutulos(kohde(1, VIERAS), kohde(2, TONY), kohde(3, VIERAS, True), kohde(4, DEPENDABOT, True), kohde(5, BOTTI, True)),
         "downloads/point": {"downloads": 42},
         "/users/": REPOLISTA}


class UlkoinenTesti(unittest.TestCase):
    def test_omat_ja_botit_eivat_ole_ulkoisia(self):
        self.assertTrue(k.ulkoinen(VIERAS))
        for omat in (TONY, BOTTI, DEPENDABOT, {"login": "TonyWeckstrom1987", "type": "User"}, {"login": "x", "type": "Bot"},
                     {"login": "renovate[bot]", "type": "User"}, None, {}):
            self.assertFalse(k.ulkoinen(omat), omat)


class ReponLuvutTesti(unittest.TestCase):
    def test_botin_forkki_eika_oma_issue_kasvata_lukuja(self):
        luvut, virheet = k.mittaa_github(["evalkit"], haku(TERVE))
        self.assertEqual(luvut["evalkit"], {"tahdet": 3, "forkit": 1, "issuet": 1, "prt": 1, "numerot": {"issuet": [1], "prt": [3]}})
        self.assertEqual(virheet, {"evalkit": []})

    def test_yksi_epaonnistunut_haku_on_kysymysmerkki_ei_nolla_eika_vie_muita(self):
        vastaukset = dict(TERVE)
        vastaukset["/forks"] = urllib.error.URLError("salainen-osoite")
        luvut, virheet = k.mittaa_github(["evalkit"], haku(vastaukset))
        self.assertEqual(luvut["evalkit"]["forkit"], "?")
        self.assertEqual(luvut["evalkit"]["tahdet"], 3)
        self.assertEqual(luvut["evalkit"]["issuet"], 1)
        self.assertEqual(virheet, {"evalkit": ["forkit: URLError"]})
        self.assertNotIn("salainen-osoite", virheet["evalkit"][0])

    def test_hakupyynnon_epaonnistuminen_on_kysymysmerkki_eika_tyhjenna_numeroita(self):
        vastaukset = dict(TERVE)
        vastaukset["search/issues"] = urllib.error.URLError("x")
        luvut, virheet = k.mittaa_github(["evalkit"], haku(vastaukset))
        self.assertEqual((luvut["evalkit"]["issuet"], luvut["evalkit"]["prt"]), ("?", "?"))
        self.assertNotIn("numerot", luvut["evalkit"])
        self.assertEqual(luvut["evalkit"]["tahdet"], 3)
        self.assertEqual(virheet, {"evalkit": ["issuet: URLError"]})

    def test_taysi_sivu_on_alaraja(self):
        vastaukset = dict(TERVE)
        vastaukset["search/issues"] = hakutulos(*[kohde(n, VIERAS) for n in range(1, k.SIVUKOKO + 1)], kokonais=250)
        luvut, _ = k.mittaa_github(["evalkit", "mcp-doctor"], haku(vastaukset))
        self.assertEqual(luvut["evalkit"]["issuet"], "%d+" % k.SIVUKOKO)
        self.assertEqual(luvut["evalkit"]["prt"], "0+")
        self.assertEqual(luvut["mcp-doctor"]["issuet"], "0+")

    def test_koko_tulos_mahtuu_sivulle_on_tarkka(self):
        vastaukset = dict(TERVE)
        vastaukset["search/issues"] = hakutulos(*[kohde(n, VIERAS) for n in range(1, k.SIVUKOKO + 1)])
        luvut, _ = k.mittaa_github(["evalkit"], haku(vastaukset))
        self.assertEqual(luvut["evalkit"]["issuet"], k.SIVUKOKO)

    def test_issuet_jaetaan_repoittain_ja_vieraan_omistajan_repo_ohitetaan(self):
        vieras_repo = kohde(9, VIERAS)
        vieras_repo["repository_url"] = "https://api.github.com/repos/joku-toinen/evalkit"
        vastaukset = dict(TERVE)
        vastaukset["search/issues"] = hakutulos(kohde(1, VIERAS, repo="evalkit"), kohde(2, VIERAS, repo="mcp-doctor"), vieras_repo)
        luvut, _ = k.mittaa_github(["evalkit", "mcp-doctor"], haku(vastaukset))
        self.assertEqual((luvut["evalkit"]["issuet"], luvut["mcp-doctor"]["issuet"]), (1, 1))

    def test_repo_jolla_ei_ole_forkkeja_ei_kuluta_forkkipyyntoa(self):
        vastaukset = dict(TERVE)
        vastaukset["/users/"] = [{"name": "evalkit", "stargazers_count": 0, "forks_count": 0}]
        pyynnot = []
        luvut, _ = k.mittaa_github(["evalkit"], lokiin(haku(vastaukset), pyynnot))
        self.assertEqual(luvut["evalkit"]["forkit"], 0)
        self.assertFalse([p for p in pyynnot if "/forks" in p])

    def test_repoa_ei_ole_listalla(self):
        luvut, virheet = k.mittaa_github(["uusi-repo"], haku(TERVE))
        self.assertEqual((luvut["uusi-repo"]["tahdet"], luvut["uusi-repo"]["forkit"]), ("?", "?"))
        self.assertEqual(virheet["uusi-repo"], ["tahdet: ei repolistassa", "forkit: ei repolistassa"])


class HttpVirheTesti(unittest.TestCase):
    def http(self, koodi):
        return urllib.error.HTTPError("https://api.github.com/salainen", koodi, "viesti-joka-ei-nay", {}, io.BytesIO(b"vastauksen-sisalto"))

    def test_401_kirjataan_tilan_kanssa_ilman_poikkeusta_ja_ilman_sisaltoa(self):
        vastaukset = dict(TERVE)
        vastaukset["/users/"] = self.http(401)
        luvut, virheet = k.mittaa_github(["evalkit"], haku(vastaukset))
        self.assertEqual(luvut["evalkit"]["tahdet"], "?")
        self.assertEqual(luvut["evalkit"]["issuet"], 1)
        self.assertEqual(virheet, {"evalkit": ["tahdet: HTTPError 401", "forkit: HTTPError 401"]})
        self.assertNotIn("salainen", virheet["evalkit"][0])
        self.assertNotIn("sisalto", virheet["evalkit"][0])

    def test_muut_tilat_ja_npm(self):
        self.assertEqual(k._virhe(self.http(403)), "HTTPError 403")
        self.assertEqual(k._virhe(self.http(404)), "HTTPError 404")
        self.assertEqual(k._virhe(TimeoutError()), "TimeoutError")
        self.assertEqual(k.npm_viikko("p", haku({"point": self.http(404)})), ("?", "npm: HTTPError 404"))

    def test_viallinen_tahtikentta_on_kysymysmerkki(self):
        for paha in ({"name": "evalkit", "forks_count": 0}, {"name": "evalkit", "stargazers_count": True, "forks_count": 0},
                     {"name": "evalkit", "stargazers_count": "3", "forks_count": 0}):
            vastaukset = dict(TERVE)
            vastaukset["/users/"] = [paha]
            luvut, virheet = k.mittaa_github(["evalkit"], haku(vastaukset))
            self.assertEqual(luvut["evalkit"]["tahdet"], "?")
            self.assertEqual(virheet, {"evalkit": ["tahdet: ValueError"]})


class PyyntoMaaraTesti(unittest.TestCase):
    """Laskee valekasittelijalla, montako GitHub-pyyntoa yksi ajo tekee. Tuntiraja on 60; tavoite on alle 30 per ajo."""
    RAJA = 30

    def github(self, vastaukset=TERVE, repot=None):
        pyynnot = []
        return lokiin(haku(vastaukset), pyynnot), pyynnot

    @staticmethod
    def vain_github(pyynnot):
        return [p for p in pyynnot if p.startswith(k.GITHUB)]

    def test_oletusajo_alle_rajan_ja_tasan_koostepyynnot(self):
        hae, pyynnot = self.github()
        k.mittaa(hae=hae)
        gh = self.vain_github(pyynnot)
        # 1 repolista + 1 hakupyynto + 1 forkkilista per repo (kaikilla fixture-repoilla on forkkeja)
        self.assertEqual(len(gh), 2 + len(k.REPOT))
        self.assertLess(len(gh), self.RAJA)
        self.assertEqual(len(set(gh)), len(gh))   # mitaan osoitetta ei haeta kahdesti

    def test_ilman_forkkeja_pyyntoja_on_vain_kaksi(self):
        v = dict(TERVE)
        v["/users/"] = [dict(r, forks_count=0) for r in REPOLISTA]
        hae, pyynnot = self.github(v)
        k.mittaa(hae=hae)
        self.assertEqual(len(self.vain_github(pyynnot)), 2)

    def test_pyyntomaara_ei_kasva_issuejen_maaran_mukana(self):
        v = dict(TERVE)
        v["search/issues"] = hakutulos(*[kohde(n, VIERAS, repo=r) for n, r in enumerate(k.REPOT, 1)])
        hae, pyynnot = self.github(v)
        k.mittaa(hae=hae)
        self.assertEqual(len(self.vain_github(pyynnot)), 2 + len(k.REPOT))

    def test_main_ja_halytys_alle_rajan(self):
        hae, pyynnot = self.github()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            k.main(["--tila", os.path.join(d, "k.json"), "--halyta"], hae=hae, tanaan="2026-11-02", laheta=lambda *a: "lahetetty")
        self.assertLess(len(self.vain_github(pyynnot)), self.RAJA)

    def test_raja_loppuu_kesken_ajon_hallitusti(self):
        """Toisesta GitHub-pyynnosta alkaen raja on tayna: taulukko valmistuu, puuttuvat luvut ovat ?, mitaan ei jaa puolittain."""
        kutsut = []

        def hae(osoite):
            if osoite.startswith(k.GITHUB):
                kutsut.append(osoite)
                if len(kutsut) > 1:
                    raise k.RajaLoppu()
                return REPOLISTA
            return {"downloads": 1}
        luvut, virheet = k.mittaa(hae=hae)
        self.assertEqual(len(kutsut), 2)    # repolista ja kaatunut hakupyynto; forkkilistoja ei yriteta, kun raja on tullut vastaan
        self.assertEqual(set(luvut), set(k.REPOT))
        for repo in k.REPOT:
            self.assertEqual((luvut[repo]["issuet"], luvut[repo]["prt"], luvut[repo]["forkit"]), ("?", "?", "?"))
            self.assertEqual(luvut[repo]["tahdet"], 3)
            self.assertNotIn("numerot", luvut[repo])
        self.assertIn("evalkit issuet: RajaLoppu", virheet)

    def test_koko_ajo_exit_0_vaikka_raja_loppuu_ja_tila_tallentuu(self):
        def hae(osoite):
            if osoite.startswith(k.GITHUB) and "users" not in osoite:
                raise k.RajaLoppu()
            return REPOLISTA if osoite.startswith(k.GITHUB) else {"downloads": 1}
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as ulos:
            koodi = k.main(["--tila", os.path.join(d, "k.json")], hae=hae, tanaan="2026-11-02")
            self.assertEqual(koodi, 0)
            self.assertIn("RajaLoppu", ulos.getvalue())
            self.assertEqual(lue(os.path.join(d, "k.json"))["2026-11-02"]["evalkit"]["issuet"], "?")


class RajaOtsakeTesti(unittest.TestCase):
    """hae_json lukee X-RateLimit-Remaining-otsakkeen eika kayta viimeisia pyyntoja."""

    class Vastaus:
        def __init__(self, jaljella, resurssi="core"):
            self.headers = {"X-RateLimit-Remaining": str(jaljella), "X-RateLimit-Resource": resurssi}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def setUp(self):
        k.RAJA.clear()
        self.addCleanup(k.RAJA.clear)

    def test_viimeiset_pyynnot_jatetaan_tekematta_ja_haku_ja_npm_eivat_kuulu_samaan_laskuriin(self):
        kutsut = []

        def urlopen(pyynto, timeout=None):
            kutsut.append(pyynto.full_url)
            return self.Vastaus(k.VARARAJA, "search" if "/search/" in pyynto.full_url else "core")
        alkup = k.urllib.request.urlopen
        k.urllib.request.urlopen = urlopen
        try:
            k.hae_json(k.GITHUB + "users/x/repos")                    # core: jaljella VARARAJA
            with self.assertRaises(k.RajaLoppu):
                k.hae_json(k.GITHUB + "repos/x/y/forks")              # core pysaytetaan ennen pyyntoa
            self.assertEqual(len(kutsut), 1)
            self.assertEqual(k.hae_json("https://api.npmjs.org/downloads/point/last-week/p"), {})   # npm ei kuulu GitHubin rajaan
            k.RAJA["search"] = 10
            k.hae_json(k.HAKU_URL)                                    # search-raja on erillinen
        finally:
            k.urllib.request.urlopen = alkup

    def test_otsake_luetaan_myos_virhevastauksesta(self):
        virhe = urllib.error.HTTPError(k.GITHUB + "x", 403, "x", {"X-RateLimit-Remaining": "0", "X-RateLimit-Resource": "core"}, io.BytesIO(b""))
        alkup = k.urllib.request.urlopen

        def urlopen(pyynto, timeout=None):
            raise virhe
        k.urllib.request.urlopen = urlopen
        try:
            with self.assertRaises(urllib.error.HTTPError):
                k.hae_json(k.GITHUB + "users/x/repos")
            with self.assertRaises(k.RajaLoppu):
                k.hae_json(k.GITHUB + "users/x/repos")
        finally:
            k.urllib.request.urlopen = alkup

    def test_viallinen_otsake_ei_kaada(self):
        k._raja_talteen({"X-RateLimit-Remaining": "ei-luku"})
        k._raja_talteen(None)
        self.assertEqual(k.RAJA, {})


class MuistiinTesti(unittest.TestCase):
    def test_sama_osoite_haetaan_kerran_virhetta_ei_muisteta(self):
        kutsut = []

        def hae(osoite):
            kutsut.append(osoite)
            if len(kutsut) == 1:
                raise TimeoutError()
            return {"a": 1}
        m = k.muistiin(hae)
        self.assertIs(k.muistiin(m), m)
        with self.assertRaises(TimeoutError):
            m("x")
        self.assertEqual((m("x"), m("x")), ({"a": 1}, {"a": 1}))
        self.assertEqual(len(kutsut), 2)


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


def issuet(*rivit):
    """TERVE-aineisto, jossa hakutulos on annettu: rivit (numero, kayttaja, onko_pr), kaikki evalkit-repossa."""
    v = dict(TERVE)
    v["search/issues"] = hakutulos(*[kohde(n, u, pr) for n, u, pr in rivit])
    return v


class HalytysTesti(unittest.TestCase):
    def ajo(self, argv, hae, tanaan, lahetetyt, tulos="lahetetty"):
        def laheta(tila, otsikko, teksti=""):
            lahetetyt.append((tila, otsikko, teksti))
            return tulos
        ulos = io.StringIO()
        with contextlib.redirect_stdout(ulos):
            koodi = k.main(argv, hae=hae, tanaan=tanaan, laheta=laheta)
        return koodi, ulos.getvalue()

    def test_ensimmainen_ajo_ilman_tilaa_kirjaa_lahtotason_eika_halyta(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            lahetetyt = []
            self.ajo(["--tila", polku, "--halyta"], haku(issuet((7, VIERAS, False))), "2026-11-02", lahetetyt)
            self.assertEqual(lahetetyt, [])
            self.assertEqual(lue(polku)["2026-11-02"]["evalkit"]["numerot"], {"issuet": [7], "prt": []})

    def test_uusi_ulkopuolinen_issue_ja_pr_halyttavat_kerran_ja_sama_ei_toisella_ajolla(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            lahetetyt = []
            self.ajo(["--tila", polku, "--halyta"], haku(issuet((7, VIERAS, False))), "2026-11-02", lahetetyt)
            uusi = haku(issuet((9, VIERAS, True), (8, VIERAS, False), (7, VIERAS, False)))
            self.ajo(["--tila", polku, "--halyta"], uusi, "2026-11-03", lahetetyt)
            self.assertEqual(len(lahetetyt), 1)
            tila, otsikko, teksti = lahetetyt[0]
            self.assertEqual(tila, "paatos")
            self.assertIn("evalkit issue #8 https://github.com/tonyweckstrom1987/evalkit/issues/8", teksti)
            self.assertIn("evalkit PR #9 https://github.com/tonyweckstrom1987/evalkit/pull/9", teksti)
            self.assertNotIn("#7", teksti)
            # Sama haku toisella ajolla (myos samana paivana): ei uutta halytysta.
            self.ajo(["--tila", polku, "--halyta"], uusi, "2026-11-03", lahetetyt)
            self.ajo(["--tila", polku, "--halyta"], uusi, "2026-11-04", lahetetyt)
            self.assertEqual(len(lahetetyt), 1)

    def test_oma_tunnus_ja_botti_eivat_halyta(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            lahetetyt = []
            self.ajo(["--tila", polku, "--halyta"], haku(issuet()), "2026-11-02", lahetetyt)
            v = issuet((1, TONY, False), (2, BOTTI, True), (3, DEPENDABOT, True), (4, {"login": "x", "type": "Bot"}, False))
            self.ajo(["--tila", polku, "--halyta"], v, "2026-11-03", lahetetyt)
            self.assertEqual(lahetetyt, [])

    def test_kaatunut_haku_ei_halyta_eika_tyhjenna_tilaa(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            lahetetyt = []
            self.ajo(["--tila", polku, "--halyta"], haku(issuet((7, VIERAS, False))), "2026-11-02", lahetetyt)
            kaatunut = issuet((7, VIERAS, False))
            kaatunut["search/issues"] = urllib.error.URLError("x")
            self.ajo(["--tila", polku, "--halyta"], kaatunut, "2026-11-03", lahetetyt)
            self.assertEqual(lahetetyt, [])
            self.assertEqual(lue(polku)["2026-11-03"]["evalkit"]["numerot"], {"issuet": [7], "prt": []})
            # Haku palaa: 7 ei ole uusi, 8 on.
            self.ajo(["--tila", polku, "--halyta"], haku(issuet((8, VIERAS, False), (7, VIERAS, False))), "2026-11-04", lahetetyt)
            self.assertEqual(len(lahetetyt), 1)
            self.assertIn("#8", lahetetyt[0][2])
            self.assertNotIn("#7", lahetetyt[0][2])

    def test_kaatunut_haku_ensimmaisella_ajolla_ei_ole_lahtotaso(self):
        self.assertEqual(k.uudet_ulkoiset({}, {"r": {"issuet": "?", "prt": "?"}}), [])
        self.assertEqual(k.uudet_ulkoiset({"r": {"issuet": [1], "prt": []}}, {"r": {"issuet": "?", "prt": "?"}}), [])

    def test_ilmoitus_joka_ei_lahde_toistuu_seuraavalla_ajolla(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            lahetetyt = []
            self.ajo(["--tila", polku, "--halyta"], haku(issuet()), "2026-11-02", lahetetyt)
            uusi = haku(issuet((5, VIERAS, False)))
            self.ajo(["--tila", polku, "--halyta"], uusi, "2026-11-03", lahetetyt, tulos="raja")
            self.ajo(["--tila", polku, "--halyta"], uusi, "2026-11-04", lahetetyt)
            self.assertEqual(len(lahetetyt), 2)

    def test_kuiva_ei_laheta_eika_kirjoita(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            lahetetyt = []
            self.ajo(["--tila", polku, "--halyta"], haku(issuet()), "2026-11-02", lahetetyt)
            ennen = lue(polku)
            _, teksti = self.ajo(["--tila", polku, "--halyta", "--kuiva"], haku(issuet((5, VIERAS, False))), "2026-11-03", lahetetyt)
            self.assertEqual(lahetetyt, [])
            self.assertIn("evalkit issue #5", teksti)
            self.assertEqual(lue(polku), ennen)

    def test_halyta_vaatii_tilan_ja_kuiva_halytyksen(self):
        for argv in (["--halyta", "--ei-tilaa"], ["--kuiva"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                k.main(argv, hae=haku({}))

    def test_viesti_rajataan(self):
        uudet = [("evalkit", "issue", n) for n in range(1, 40)]
        otsikko, teksti = k.halytysviesti(uudet)
        self.assertIn("39 uutta", otsikko)
        self.assertIn("ja 29 muuta", teksti)
        self.assertLessEqual(len(teksti), 1200)


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
            self.assertEqual(lue(polku)["2026-11-08"]["evalkit"]["npm_viikko"], 51)
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
        koodi, teksti = self.ajo(["--ei-tilaa"], haku({}), "2026-11-02")
        self.assertEqual(koodi, 1)
        self.assertIn("| evalkit | ? | ? | ? | ? | ? |", teksti)
        self.assertIn("| llm-stopcheck | - | ? | ? | ? | ? |", teksti)

    def test_ei_tilaa_ei_kirjoita(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "k.json")
            self.ajo(["--tila", polku, "--ei-tilaa"], haku(TERVE), "2026-11-02")
            self.assertEqual(os.listdir(d), [])

    def test_tyhja_tila_ensimmainen_ajo_tallentaa_lahtotason_ilman_muutoksia(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "uusi-hakemisto", "k.json")
            koodi, teksti = self.ajo(["--tila", polku], haku(TERVE), "2026-11-02")
            self.assertEqual(koodi, 0)
            self.assertNotIn("muutos mittaukseen", teksti)
            self.assertIn("| evalkit | 42 | 3 | 1 | 1 | 1 |", teksti)
            self.assertEqual(lue(polku)["2026-11-02"]["evalkit"]["tahdet"], 3)

    def test_oletuspolku_on_kirjoitettavassa_hakemistossa(self):
        self.assertTrue(k.TILA_OLETUS.startswith("/opt/agentscope/tyo/"))
        self.assertNotIn("/var/lib/claude-jono", k.TILA_OLETUS)


if __name__ == "__main__":
    unittest.main()
