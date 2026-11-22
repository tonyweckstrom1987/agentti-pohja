import ast
import json
import os
import tempfile
import textwrap
import unittest
from unittest import mock

from tools import mutantit as mu

MODUULI = textwrap.dedent('''\
    ASETUS = 41

    def suurempi(a, b):
        return a > b

    def kaksi():
        return 2
''')

TESTI = textwrap.dedent('''\
    import unittest
    from agentti import mod

    class T(unittest.TestCase):
        def test_suurempi(self):
            self.assertTrue(mod.suurempi(2, 1))
            self.assertFalse(mod.suurempi(1, 2))
            self.assertFalse(mod.suurempi(1, 1))
''')


def args(**kw):
    a = mu.lue_argumentit([])
    a.vahimmaisaika = 20
    a.alkuaikaraja = 60
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class MiniRepo:
    """Pieni repo, jossa on yksi moduuli ja sen testi (testit eivat tarkista funktiota `kaksi`)."""

    def __init__(self, moduuli=MODUULI, testi=TESTI):
        self.tmp = tempfile.TemporaryDirectory()
        self.juuri = self.tmp.name
        for hak in ("agentti", "test"):
            os.makedirs(os.path.join(self.juuri, hak))
            open(os.path.join(self.juuri, hak, "__init__.py"), "w").close()
        self.kirjoita("agentti/mod.py", moduuli)
        self.kirjoita("test/test_mod.py", testi)

    def kirjoita(self, polku, sisalto):
        with open(os.path.join(self.juuri, polku), "w", encoding="utf-8") as f:
            f.write(sisalto)

    def sulje(self):
        self.tmp.cleanup()


class MutanttiLuontiTesti(unittest.TestCase):
    def test_lajit(self):
        lahde = textwrap.dedent('''\
            def f(a, b):
                if a == b and not a:
                    return a + b * 2
                x = True
                return x
        ''')
        lajit = {m["laji"] for m in mu.luo_mutantit(lahde)}
        self.assertEqual(lajit, {"vertailu", "and/or", "not pois", "aritmetiikka", "vakio", "if-ehto", "return None"})

    def test_jokainen_mutantti_muuttaa_tasan_yhden_kohdan(self):
        lahde = "def f(a, b):\n    if a < b or a == 3:\n        return a - b\n    return not b\n"
        perus = ast.unparse(ast.parse(lahde)) + "\n"
        mutantit = mu.luo_mutantit(lahde)
        self.assertGreaterEqual(len(mutantit), 6)
        for m in mutantit:
            uusi = mu.mutoi(lahde, m["id"])
            self.assertNotEqual(uusi, perus, m)
            ast.parse(uusi)  # kelvollista koodia

    def test_vertailu_kaannetaan_oikein(self):
        m = mu.luo_mutantit("x = a > b\n")[0]
        self.assertEqual(m["uusi"], "a <= b")
        self.assertIn("a <= b", mu.mutoi("x = a > b\n", m["id"]))

    def test_asetusvakioita_ei_mutatoida(self):
        self.assertEqual(mu.luo_mutantit("RAJA = 5\nNIMI = 'x'\n"), [])
        self.assertEqual(len(mu.luo_mutantit("raja = 5\n")), 1)

    def test_return_none_ei_mutatoida_uudelleen(self):
        self.assertEqual(mu.luo_mutantit("def f():\n    return None\n"), [])
        self.assertEqual(mu.luo_mutantit("def f():\n    return\n"), [])

    def test_toisto_erottaa_samanlaiset_mutantit(self):
        mutantit = mu.luo_mutantit("a = f(6)\nb = g(6)\n")
        self.assertEqual([m["toisto"] for m in mutantit], [0, 1])
        avaimet = {mu.sallittu_avain("m.py", m) for m in mutantit}
        self.assertEqual(len(avaimet), 2)

    def test_avain_ei_riipu_rivinumerosta(self):
        a = mu.luo_mutantit("x = f(6)\n")[0]
        b = mu.luo_mutantit("\n\n\nx = f(6)\n")[0]
        self.assertNotEqual(a["rivi"], b["rivi"])
        self.assertEqual(mu.sallittu_avain("m.py", a), mu.sallittu_avain("m.py", b))


class SallitutTesti(unittest.TestCase):
    def test_kommentit_ja_tyhjat_rivit_ohitetaan(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# otsikko\n\nagentti/x.py|vakio|6|7|0  # vastaava: indeksi\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(mu.lue_sallitut(f.name), {"agentti/x.py|vakio|6|7|0"})

    def test_puuttuva_tiedosto_on_tyhja(self):
        self.assertEqual(mu.lue_sallitut("/ei/ole/olemassa.txt"), set())
        self.assertEqual(mu.lue_sallitut(None), set())


class AjoTesti(unittest.TestCase):
    def setUp(self):
        self.repo = MiniRepo()
        self.addCleanup(self.repo.sulje)

    def test_lisatestit_liitetaan_moduuliin(self):
        os.makedirs(os.path.join(self.repo.juuri, "test"), exist_ok=True)
        with open(os.path.join(self.repo.juuri, "test", "test_apurit.py"), "w") as f:
            f.write("")
        with mock.patch.dict(mu.LISATESTIT, {"agentti/mod.py": ["test/test_apurit.py"]}):
            self.assertEqual(mu.testimoduulit("agentti/mod.py", self.repo.juuri), ["test.test_mod", "test.test_apurit"])
        self.assertEqual(mu.testimoduulit("agentti/mod.py", self.repo.juuri), ["test.test_mod"])

    def test_testimoduulit_loytyvat_nimella(self):
        self.assertEqual(mu.testimoduulit("agentti/mod.py", self.repo.juuri), ["test.test_mod"])
        self.assertEqual(mu.testimoduulit("agentti/ei.py", self.repo.juuri), [])

    def test_kaikki_moduulit_ohittaa_testittomat(self):
        self.repo.kirjoita("agentti/yksin.py", "x = 1\n")
        self.assertEqual(mu.kaikki_moduulit(self.repo.juuri), ["agentti/mod.py"])

    def test_testattu_koodi_kaatuu_ja_testaamaton_selviaa(self):
        t = mu.aja_moduuli("agentti/mod.py", args(), juuri=self.repo.juuri)
        rivit = {(s["laji"], s["vanha"]) for s in t["selvisi"]}
        # funktiota `kaksi` ei testata: sen paluuarvon muutokset selviavat
        self.assertIn(("vakio", "2"), rivit)
        self.assertIn(("return None", "return 2"), rivit)
        # funktio `suurempi` on testattu: sen mutantit eivat selvia
        self.assertFalse([s for s in t["selvisi"] if "a > b" in s["vanha"] or "a > b" in s["uusi"]], t["selvisi"])
        self.assertGreater(t["kaatui"], 0)
        self.assertEqual(t["kaatui"] + len(t["selvisi"]), t["mutantteja"])

    def test_alkuperainen_tiedosto_palautuu(self):
        mu.aja_moduuli("agentti/mod.py", args(), juuri=self.repo.juuri)
        with open(os.path.join(self.repo.juuri, "agentti/mod.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), MODUULI)

    def test_sallittu_siirtyy_selviytyjista(self):
        t0 = mu.aja_moduuli("agentti/mod.py", args(), juuri=self.repo.juuri)
        avain = t0["selvisi"][0]["avain"]
        t = mu.aja_moduuli("agentti/mod.py", args(), juuri=self.repo.juuri, sallitut={avain})
        self.assertEqual(len(t["selvisi"]), len(t0["selvisi"]) - 1)
        self.assertEqual([s["avain"] for s in t["sallittu"]], [avain])

    def test_rinnakkain_antaa_saman_tuloksen(self):
        a = mu.aja_moduuli("agentti/mod.py", args(), juuri=self.repo.juuri)
        b = mu.aja_moduuli("agentti/mod.py", args(rinnakkain=2), juuri=self.repo.juuri)
        self.assertEqual(a["kaatui"], b["kaatui"])
        self.assertEqual([s["avain"] for s in a["selvisi"]], [s["avain"] for s in b["selvisi"]])

    def test_otos_rajaa_mutanttien_maaran_toistettavasti(self):
        a = mu.aja_moduuli("agentti/mod.py", args(otos=3, siemen=7), juuri=self.repo.juuri)
        b = mu.aja_moduuli("agentti/mod.py", args(otos=3, siemen=7), juuri=self.repo.juuri)
        self.assertEqual(a["mutantteja"], 3)
        self.assertEqual([s["avain"] for s in a["selvisi"]], [s["avain"] for s in b["selvisi"]])

    def test_punainen_alkutila_pysayttaa(self):
        self.repo.kirjoita("test/test_mod.py", TESTI.replace("assertFalse(mod.suurempi(1, 1))",
                                                              "assertTrue(mod.suurempi(1, 1))"))
        with self.assertRaises(RuntimeError) as e:
            mu.aja_moduuli("agentti/mod.py", args(), juuri=self.repo.juuri)
        self.assertIn("alkutilan testit", str(e.exception))

    def test_aikaraja_ylitys_ei_ole_onnistuminen(self):
        self.repo.kirjoita("test/test_hidas.py", textwrap.dedent('''\
            import time
            import unittest

            class H(unittest.TestCase):
                def test_hidas(self):
                    time.sleep(10)
        '''))
        ok, kesto, loppu = mu.aja_testit(self.repo.juuri, ["test.test_hidas"], 1)
        self.assertFalse(ok)
        self.assertEqual(loppu, "aikaraja")
        self.assertLess(kesto, 8)

    def test_moduuli_ilman_testia_on_virhe(self):
        self.repo.kirjoita("agentti/yksin.py", "x = 1\n")
        with self.assertRaises(RuntimeError):
            mu.aja_moduuli("agentti/yksin.py", args(), juuri=self.repo.juuri)


AIVO_MODUULI = "def raja(x):\n    if x > 5:\n        return 5\n    return x\n"
AIVO_TESTI = textwrap.dedent('''\
    import unittest
    from aivo import aivomod

    class T(unittest.TestCase):
        def test_raja(self):
            self.assertEqual(aivomod.raja(9), 5)
            self.assertEqual(aivomod.raja(3), 3)
            self.assertEqual(aivomod.raja(5), 5)
            self.assertEqual(aivomod.raja(6), 5)
''')


class AivoKohdeTesti(unittest.TestCase):
    """aivo/-moduulit: testit aivo/test_<nimi>.py, mukaan vain nimetysti (--hakemistot), rajattavissa (--rajaa)."""

    def setUp(self):
        self.repo = MiniRepo()
        self.addCleanup(self.repo.sulje)
        os.makedirs(os.path.join(self.repo.juuri, "aivo"))
        self.repo.kirjoita("aivo/__init__.py", "")
        self.repo.kirjoita("aivo/aivomod.py", AIVO_MODUULI)
        self.repo.kirjoita("aivo/test_aivomod.py", AIVO_TESTI)
        self.repo.kirjoita("aivo/toinen.py", "x = 1\n")
        self.repo.kirjoita("aivo/test_toinen.py", "")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_aivo_testit_tunnistetaan(self):
        self.assertEqual(mu.testimoduulit("aivo/aivomod.py", self.repo.juuri), ["aivo.test_aivomod"])

    def test_oletus_ei_sisalla_aivoa(self):
        self.assertEqual(mu.kaikki_moduulit(self.repo.juuri), ["agentti/mod.py"])

    def test_hakemistot_ja_rajaus(self):
        self.assertEqual(mu.kaikki_moduulit(self.repo.juuri, ("aivo",)), ["aivo/aivomod.py", "aivo/toinen.py"])
        self.assertEqual(mu.kaikki_moduulit(self.repo.juuri, mu.HAKEMISTOT_KAIKKI, "aivo/aivom"), ["aivo/aivomod.py"])

    def test_listaa_ja_virheellinen_hakemisto(self):
        import contextlib
        import io
        tyhja = os.path.join(self.tmp.name, "s.txt")
        ulos = io.StringIO()
        with contextlib.redirect_stdout(ulos):
            self.assertEqual(mu.paa(["--sallitut", tyhja, "--kaikki", "--listaa", "--hakemistot", "aivo",
                                     "--rajaa", "aivo/aivom"], juuri=self.repo.juuri), 0)
        self.assertEqual(json.loads(ulos.getvalue()), ["aivo/aivomod.py"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mu.paa(["--kaikki", "--hakemistot", "../x"], juuri=self.repo.juuri), 2)

    def test_aivo_moduulin_nimi_hyvaksytaan_ja_rajan_poisto_kaatuu(self):
        tyhja = os.path.join(self.tmp.name, "s.txt")
        self.assertEqual(mu.paa(["--sallitut", tyhja, "aivo/aivomod.py"], juuri=self.repo.juuri), 0)
        t = mu.aja_moduuli("aivo/aivomod.py", args(), juuri=self.repo.juuri)
        self.assertEqual(t["selvisi"], [])
        self.assertGreater(t["kaatui"], 0)
        # tunnettu mutantti: `x > 5` -> `x <= 5` (rajan kaanto) on mukana ja kaatuu
        self.assertIn(("vertailu", "x > 5"), {(m["laji"], m["vanha"]) for m in mu.luo_mutantit(AIVO_MODUULI)})


class PaaTesti(unittest.TestCase):
    def setUp(self):
        self.repo = MiniRepo()
        self.addCleanup(self.repo.sulje)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def kutsu(self, *lisa):
        tyhja = os.path.join(self.tmp.name, "sallitut.txt")
        return mu.paa(["--sallitut", tyhja, *lisa], juuri=self.repo.juuri)

    def test_selviytyjista_poistumiskoodi_1_ja_json_ja_markdown(self):
        json_polku = os.path.join(self.tmp.name, "t.json")
        md_polku = os.path.join(self.tmp.name, "t.md")
        self.assertEqual(self.kutsu("agentti/mod.py", "--json", json_polku, "--markdown", md_polku), 1)
        with open(json_polku, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[0]["moduuli"], "agentti/mod.py")
        with open(md_polku, encoding="utf-8") as f:
            md = f.read()
        self.assertIn("| `agentti/mod.py` |", md)
        self.assertIn("Selviytyjat", md)

    def test_kaikki_sallittu_antaa_nolla(self):
        t = mu.aja_moduuli("agentti/mod.py", args(), juuri=self.repo.juuri)
        sallitut = os.path.join(self.tmp.name, "s.txt")
        with open(sallitut, "w", encoding="utf-8") as f:
            for s in t["selvisi"]:
                f.write(s["avain"] + "  # testi\n")
        self.assertEqual(mu.paa(["agentti/mod.py", "--sallitut", sallitut], juuri=self.repo.juuri), 0)

    def test_kaikki_valitsee_moduulit_itse(self):
        self.assertEqual(self.kutsu("--kaikki"), 1)

    def test_ala_kaadu_antaa_nollan_selviytyjista(self):
        self.assertEqual(self.kutsu("agentti/mod.py", "--ala-kaadu"), 0)

    def test_listaa_tulostaa_moduulit_jsonina(self):
        import contextlib
        import io
        ulos = io.StringIO()
        with contextlib.redirect_stdout(ulos):
            self.assertEqual(self.kutsu("--kaikki", "--listaa"), 0)
        self.assertEqual(json.loads(ulos.getvalue()), ["agentti/mod.py"])

    def test_tuntematon_tai_vaarannimi_hylataan(self):
        for nimi in ("agentti/ei_ole.py", "../etc/passwd", "agentti/mod.py; rm -rf x", "agentti/Mod.py"):
            self.assertEqual(self.kutsu(nimi), 2, nimi)

    def test_ilman_moduuleja_virhe(self):
        self.assertEqual(self.kutsu(), 2)

    def test_punainen_alkutila_antaa_kaksi(self):
        self.repo.kirjoita("test/test_mod.py", "import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail()\n")
        self.assertEqual(self.kutsu("agentti/mod.py"), 2)


class LisatestitTaulukkoTesti(unittest.TestCase):
    """Vartija LISATESTIT-taulukolle: virheellinen rivi pudottaisi testitiedoston mutanttiajosta ilman virheilmoitusta."""

    def _taulukko(self, lahde=None):
        if lahde is None:
            with open(mu.__file__, encoding="utf-8") as f:
                lahde = f.read()
        for solmu in ast.walk(ast.parse(lahde)):
            if isinstance(solmu, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "LISATESTIT" for t in solmu.targets):
                return solmu.value
        self.fail("LISATESTIT-sijoitusta ei loydy")

    @staticmethod
    def _toistuvat_avaimet(sanakirja):
        nahdyt, toistuvat = set(), []
        for avain in sanakirja.keys:
            arvo = ast.literal_eval(avain)
            if arvo in nahdyt:
                toistuvat.append(arvo)
            nahdyt.add(arvo)
        return toistuvat

    def test_ei_toistuvia_avaimia(self):
        # Kaksi saman avaimen rivia (esim. konfliktin ratkaisu liittamalla molemmat puolet) jattaa vain jalkimmaisen.
        self.assertEqual(self._toistuvat_avaimet(self._taulukko()), [])

    def test_vartija_havaitsee_toistuvan_avaimen(self):
        lahde = 'LISATESTIT = {\n    "aivo/a.py": ["aivo/test_x.py"],\n    "aivo/a.py": ["aivo/test_y.py"],\n}\n'
        self.assertEqual(self._toistuvat_avaimet(self._taulukko(lahde)), ["aivo/a.py"])

    def test_moduulit_ja_testitiedostot_ovat_olemassa(self):
        # testimoduulit() suodattaa puuttuvat polut hiljaa, joten kirjoitusvirhe poistaisi testin ajosta.
        puuttuvat = []
        for moduuli, testit in mu.LISATESTIT.items():
            for polku in [moduuli] + list(testit):
                if not os.path.isfile(os.path.join(mu.JUURI, polku)):
                    puuttuvat.append(polku)
        self.assertEqual(puuttuvat, [])

    def test_ei_toistuvia_testitiedostoja_rivilla(self):
        toistuvat = {m: t for m, t in mu.LISATESTIT.items() if len(t) != len(set(t))}
        self.assertEqual(toistuvat, {})


if __name__ == "__main__":
    unittest.main()
