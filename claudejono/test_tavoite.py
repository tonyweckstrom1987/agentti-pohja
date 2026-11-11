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

    def test_sulkuhuomautus_sallitaan_vain_kelpaavan_arvon_perassa(self):
        # Tehtava 11-04 (11.11.2026): "Tavoite: yll\u00e4pito (laatu)" hylattiin hiljaa 9 tunniksi.
        for rivi, odotettu in (("yll\u00e4pito (laatu)", "yllapito"), ("portfolio (k\u00e4rkiprojekti)", "portfolio"),
                               ("Aivo (varjotila).", "aivo"), ("ansainta ()", "ansainta")):
            self.assertEqual(tavoite.tavoite_tekstista(f"Tavoite: {rivi}\n"), odotettu, rivi)
        for rivi in ("rahaa (aivo)", "(yllapito)", "yllapito (a) (b)", "aivo ja muuta (x)", "yllapito (laatu) lisaa"):
            self.assertIsNone(tavoite.tavoite_tekstista(f"Tavoite: {rivi}\n"), rivi)

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


class AivoTurva(unittest.TestCase):
    """#288 tarkastajan loydokset: ymparisto, virhejarjestys, kayton kirjaus."""

    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def test_ymparisto_on_allowlist_eika_valita_salaisuutta(self):
        env = {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C", "SALAINEN_X": "arvo", "CLAUDEJONO_LITELLM_AVAIN": "a",
               "GIT_SSH_COMMAND": "x", "CLAUDE_CONFIG_DIR": "/c", "CLAUDE_MUU": "y"}
        _, alaenv, _ = ajuri.aivo_komento(env)
        self.assertEqual(alaenv, {"PATH": "/usr/bin", "HOME": "/h", "LANG": "C", "CLAUDE_CONFIG_DIR": "/c"})
        self.assertNotIn("SALAINEN_X", alaenv)

    def test_ymparisto_kayttajatilassa_ei_valita_salaisuutta(self):
        _, alaenv, _ = ajuri.aivo_komento({"CLAUDEJONO_KAYTTAJA": "claudejono", "SALAINEN_X": "arvo"})
        self.assertNotIn("SALAINEN_X", alaenv)

    def _arvioi(self, rc, out):
        return ajuri.aivo_arvioija({}, suorita=lambda *a, **k: self.R(rc, out))("teksti")

    def test_tyhja_stdout_on_jonovirhe(self):
        for rc in (0, 1):
            with self.assertRaises(ajuri.JonoVirhe):
                self._arvioi(rc, "")

    def test_returncode_tarkistetaan_ennen_jsonia(self):
        with self.assertRaises(ajuri.JonoVirhe) as e:
            self._arvioi(2, "ei jsonia")
        self.assertIn("paluukoodi 2", str(e.exception))

    def test_ei_json_tuloste_ja_rikkinainen_vastaus_ovat_jonovirhe(self):
        for out in ("roskaa", json.dumps({"result": "{rikki"}), json.dumps({"result": "ei oliota"})):
            with self.assertRaises(ajuri.JonoVirhe):
                self._arvioi(0, out)

    def test_hyva_vastaus_kay(self):
        out = json.dumps({"is_error": False, "result": '{"kylla": true, "syy": "ok"}'})
        self.assertEqual(self._arvioi(0, out), (True, "ok"))

    def test_kaytto_kirjataan_onnistumisesta_ja_virheesta(self):
        tila = tempfile.mkdtemp()
        t = kirj(tila, "a.md", "x")

        def rikki(s):
            raise RuntimeError("x")
        self.assertTrue(aivo_arvio.arvioi_varjossa(tila, t, lambda s: (True, "ok")))
        self.assertFalse(aivo_arvio.arvioi_varjossa(tila, kirj(tila, "b.md", "x"), rikki))
        n, ok, kesto = aivo_arvio.kaytto(tila)
        self.assertEqual((n, ok), (2, 1))
        self.assertGreaterEqual(kesto, 0)
        self.assertIn("2 arviointiajoa (1 onnistui)", aivo_arvio.viikkoosio(tila))

    def test_ei_paivarajaa(self):
        tila = tempfile.mkdtemp()
        for i in range(350):
            aivo_arvio.arvioi_varjossa(tila, kirj(tila, f"t{i}.md", "x"), lambda s: (True, "ok"))
        self.assertEqual(aivo_arvio.kaytto(tila)[0], 350)


class AivoKytkenta(unittest.TestCase):
    """Vika 10.11.2026 (#251): main() ei antanut aja():lle arvioijaa, joten varjotila ei kaynnistynyt koskaan."""

    def test_main_kytkee_arvioijan_aja_funktiolle(self):
        from unittest import mock
        from claudejono import ajuri
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "docs"
            repo.mkdir()
            with mock.patch.dict("os.environ", {"CLAUDEJONO_REPO": str(repo), "CLAUDEJONO_TYOHAK": d, "CLAUDEJONO_KAISTOJA": "1"}), \
                 mock.patch.object(ajuri, "aja", return_value="ok") as aja_mock:
                self.assertEqual(ajuri.main(["ajo"]), 0)
            arvioija = aja_mock.call_args.kwargs.get("aivo_arvioija")
            self.assertTrue(callable(arvioija))

    def _vastaus(self, result, koodi=0):
        from types import SimpleNamespace
        return SimpleNamespace(stdout=json.dumps({"result": result, "subtype": "success"}), returncode=koodi)

    def test_arvioija_kutsuu_claudea_ilman_tyokaluja_ja_tulkitsee_vastauksen(self):
        from claudejono import ajuri
        kutsut = []

        def suorita(kom, **kw):
            kutsut.append((kom, kw))
            return self._vastaus('{"kylla": true, "syy": "tukee portfoliota"}')
        arvioi = ajuri.aivo_arvioija({"CLAUDEJONO_CLAUDE": "claude"}, suorita)
        self.assertEqual(arvioi("# tehtava"), (True, "tukee portfoliota"))
        kom, kw = kutsut[0]
        self.assertEqual(kom[kom.index("--tools") + 1], "")      # ei tyokaluja
        self.assertEqual(kom[kom.index("--model") + 1], "opus")  # Clauden tyokalutila (A952), ei OpenRouter/LiteLLM
        self.assertNotIn("LITELLM", " ".join(kw["env"]))
        self.assertIn("# tehtava", kw["input"])

    def test_arvioija_virheet_nostetaan_ja_varjotila_nielee_ne(self):
        from claudejono import ajuri
        tila = tempfile.mkdtemp()
        t = kirj(tila, "2026-11-10-01-a.md", "# tehtava")
        for result, koodi in (("ei jsonia", 0), ('{"kylla": "joo"}', 0), ('{"kylla": true}', 1)):
            arvioi = ajuri.aivo_arvioija({}, lambda kom, r=result, k=koodi, **kw: self._vastaus(r, k))
            with self.assertRaises(ajuri.JonoVirhe):
                arvioi("x")
            self.assertFalse(aivo_arvio.arvioi_varjossa(tila, t, arvioi))
        self.assertEqual(aivo_arvio.arviot(tila), {})
        ok = ajuri.aivo_arvioija({}, lambda kom, **kw: self._vastaus('{"kylla": false, "syy": "ei"}'))
        self.assertTrue(aivo_arvio.arvioi_varjossa(tila, t, ok))
        self.assertEqual(aivo_arvio.arviot(tila)[t.name]["kylla"], False)

    def test_systemd_malli_kolme_kaistaa(self):
        teksti = (Path(__file__).parent / "systemd" / "claude-jono@.service.mall").read_text()
        self.assertIn("Environment=CLAUDEJONO_KAISTOJA=3", teksti)


if __name__ == "__main__":
    unittest.main()
