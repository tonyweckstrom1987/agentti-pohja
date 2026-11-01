import io
import contextlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
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


NYT = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _pr(nro, merged, otsikko="Korjaus"):
    return {"number": nro, "state": "closed", "merged_at": merged, "title": otsikko}


class MergatutTesti(unittest.TestCase):
    def test_osio_ilmestyy_ja_vain_tuoreet(self):
        def hae(url):
            return [_pr(5, "2026-10-24T10:00:00Z", "Tuore"), _pr(4, "2026-10-10T10:00:00Z", "Vanha"), _pr(3, None, "Suljettu")]
        ulos = tk.mergatut_osio(hae, NYT)
        self.assertIn("# Mergatut PR:t, viimeiset 7 paivaa", ulos)
        self.assertIn("agentti-pohja #5 Tuore (mergattu 2026-10-24)", ulos)
        self.assertNotIn("Vanha", ulos)
        self.assertNotIn("Suljettu", ulos)

    def test_tyhja_lista_ei_kaada(self):
        ulos = tk.mergatut_osio(lambda url: [], NYT)
        self.assertIn("(ei mergattuja PR:ia)", ulos)

    def test_pitka_otsikko_katkeaa(self):
        ulos = tk.mergatut_osio(lambda url: [_pr(1, "2026-10-24T10:00:00Z", "X" * 300)], NYT)
        self.assertIn("X" * tk.MERGATUT_OTSIKKO_MAX + " (mergattu", ulos)
        self.assertNotIn("X" * (tk.MERGATUT_OTSIKKO_MAX + 1), ulos)

    def test_rivimaara_rajattu(self):
        ulos = tk.mergatut_osio(lambda url: [_pr(i, "2026-10-24T10:00:00Z") for i in range(30)], NYT)
        self.assertEqual(len([l for l in ulos.splitlines() if l.startswith("- ")]), tk.MERGATUT_MAX_RIVEJA)
        self.assertIn("jatetty pois", ulos)

    def test_haun_virhe_ei_esta_muita_repoja(self):
        def hae(url):
            if "/mcp-model-router/" in url:
                raise OSError("verkko")
            return [_pr(9, "2026-10-24T10:00:00Z", "Ok")]
        ulos = tk.mergatut_osio(hae, NYT)
        self.assertIn("agentti-pohja #9 Ok", ulos)
        self.assertIn("(haku epaonnistui: mcp-model-router: OSError)", ulos)

    def test_kokonaisvirhe_ei_esta_muuta_kuvaa(self):
        t = Path(tempfile.mkdtemp())
        def hae(url):
            if "state=closed" in url:
                raise OSError("verkko")
            return hae_tekoinen(url)
        nimet = tk.keraa_kaikki(t / "kuva", perus=t, portti_tila=None, hae=hae, hae_isanta=lambda: "ok")
        self.assertIn("yojono.md", nimet)
        teksti = (t / "kuva" / "github.md").read_text()
        self.assertIn("# GitHub (julkiset repot)", teksti)
        self.assertIn("# Mergatut PR:t", teksti)
        self.assertIn("haku epaonnistui", teksti)

    def test_osio_on_github_md_tiedostossa(self):
        t = Path(tempfile.mkdtemp())
        tk.keraa_kaikki(t / "kuva", perus=t, portti_tila=None, hae=hae_tekoinen, hae_isanta=lambda: "ok")
        self.assertIn("# Mergatut PR:t", (t / "kuva" / "github.md").read_text())


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

    def test_kulut_taasmays_merkitsee_eron(self):
        (self.t / "tila.json").write_text(json.dumps({"tehtavat": {"a": {"pvm": "2026-10-20", "kulu": 0.5}, "b": {"pvm": "2026-10-21", "kulu": 0.5}},
                                                      "kulut": {"2026-10-20": 0.5, "2026-10-21": 0.9}}))
        teksti = tk.kulut_osio(self.t, None)
        self.assertIn("2026-10-20: tehtavien viimeisimmat ajot 0.50, kirjattu 0.50 -> OK", teksti)
        self.assertIn("2026-10-21: tehtavien viimeisimmat ajot 0.50, kirjattu 0.90 -> ERO", teksti)

    def test_kulut_taasmays_erittelysta_nayttaa_uusinnat_ja_arvion(self):
        """27.10.: tila.json 11.05 USD vs raporttien summa 5.73. Erittely kertoo, mista ero tulee."""
        (self.t / "tila.json").write_text(json.dumps({
            "tehtavat": {"a": {"pvm": "2026-10-27", "kulu": 0.4}},
            "kulut": {"2026-10-27": 3.2},
            "kuluerittely": {"2026-10-27": [
                {"id": "a", "kulu": 1.3, "ok": False, "arvio": True},
                {"id": "a", "kulu": 0.4, "ok": True, "arvio": False},
                {"id": "b", "kulu": 1.5, "ok": False, "arvio": False}]}}))
        teksti = tk.kulut_osio(self.t, None)
        self.assertIn("2026-10-27: ajoja 3 (uusintoja 1), ajojen summa 3.20, josta ilman tulosta 2.80 ja arvioitu budjetiksi 1.30, "
                      "kirjattu 3.20 -> OK", teksti)

    def test_kulut_taasmays_vajaa_erittely_on_ero_ja_roska_ohitetaan(self):
        (self.t / "tila.json").write_text(json.dumps({
            "kulut": {"2026-10-28": 2.0},
            "kuluerittely": {"2026-10-28": [{"id": "a", "kulu": 0.5}, {"id": "x", "kulu": True}, "roska"]}}))
        teksti = tk.kulut_osio(self.t, None)
        self.assertIn("2026-10-28: ajoja 1 (uusintoja 0), ajojen summa 0.50", teksti)
        self.assertIn("kirjattu 2.00 -> ERO", teksti)

    def test_kulut_taasmays_erittely_vaaraa_tyyppia_ei_kaada(self):
        (self.t / "tila.json").write_text(json.dumps({"kulut": {"2026-10-20": 0.5}, "kuluerittely": ["x"],
                                                      "tehtavat": {"a": {"pvm": "2026-10-20", "kulu": 0.5}}}))
        self.assertIn("2026-10-20: tehtavien viimeisimmat ajot 0.50, kirjattu 0.50 -> OK", tk.kulut_osio(self.t, None))

    def test_isanta_osio_virhe_ei_kaada(self):
        def rikki():
            raise RuntimeError("ei yhteytta")
        self.assertIn("isantaa ei saatavilla: ei yhteytta", tk.isanta_osio(rikki))
        self.assertIn("kontit ok", tk.isanta_osio(lambda: "kontit ok"))

    def test_main_vaatii_yhden_argumentin(self):
        self.assertEqual(tk.main([]), 2)


# --- Mutaatiotestauksessa (tools/mutantit.py) selvinneet haarat ---

class _Vastaus:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.data).encode("utf-8")


def feikki_urlopen(data_fn, kutsut):
    def f(pyynto, timeout=None):
        kutsut.append((pyynto, timeout))
        return _Vastaus(data_fn(pyynto.full_url))
    return f


def pr(nro, **kw):
    d = {"number": nro, "state": "open", "merged_at": None, "title": "Otsikko", "user": {"login": "bot"},
         "updated_at": "2026-10-20T10:00:00Z"}
    d.update(kw)
    return d


class OletushakuTesti(unittest.TestCase):
    def test_github_osio_kayttaa_urlopenia(self):
        kutsut = []
        def data(url):
            if "/pulls" in url:
                return [pr(3, title="T" * 120), pr(4, user=None, merged_at="2026-10-21T00:00:00Z")]
            return [{"sha": "0123456789", "commit": {"message": "M" * 120 + "\nrunko"}}]
        with mock.patch("urllib.request.urlopen", feikki_urlopen(data, kutsut)):
            ulos = tk.github_osio()
        self.assertIn("- PR #3 [open] " + "T" * 90 + " (tekija bot, 2026-10-20)", ulos)
        self.assertNotIn("T" * 91, ulos)
        self.assertIn("- PR #4 [mergattu] Otsikko (tekija None, 2026-10-20)", ulos)
        self.assertIn("- commit 0123456 " + "M" * 90 + "\n", ulos + "\n")
        self.assertNotIn("M" * 91, ulos)
        self.assertTrue(all(t == 20 for _, t in kutsut))
        self.assertEqual(kutsut[0][0].get_header("User-agent"), "agenttifirma-tilannekuva")
        self.assertEqual(kutsut[0][0].get_header("Accept"), "application/vnd.github+json")

    def test_mergatut_osio_kayttaa_urlopenia(self):
        kutsut = []
        with mock.patch("urllib.request.urlopen", feikki_urlopen(lambda u: [pr(5, merged_at="2026-10-24T10:00:00Z")], kutsut)):
            ulos = tk.mergatut_osio(None, NYT)
        self.assertIn("#5 Otsikko (mergattu 2026-10-24)", ulos)
        self.assertTrue(all(t == 20 for _, t in kutsut))
        self.assertEqual(kutsut[0][0].get_header("User-agent"), "agenttifirma-tilannekuva")


class MergatutJarjestysTesti(unittest.TestCase):
    def test_uusin_ensin_ja_tarkka_ylitys(self):
        prt = [_pr(1, "2026-10-20T10:00:00Z", "A"), _pr(2, "2026-10-24T10:00:00Z", "B"), _pr(3, "2026-10-22T10:00:00Z", "C")]
        ulos = tk.mergatut_osio(lambda url: prt if "/agentti-pohja/" in url else [], NYT)
        rivit = [l for l in ulos.splitlines() if l.startswith("- ")]
        self.assertEqual([r.split(" ")[3] for r in rivit[:3]], ["B", "C", "A"])
        n = len(tk.REPOT) * 30
        ulos = tk.mergatut_osio(lambda url: [_pr(i, "2026-10-24T10:00:00Z") for i in range(30)], NYT)
        self.assertIn("(+%d vanhempaa jatetty pois)" % (n - tk.MERGATUT_MAX_RIVEJA), ulos)


class YojonoOsioTarkkaTesti(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        (self.t / "raportit").mkdir()
        (self.t / "tila.json").write_text(json.dumps({"tehtavat": {"a": {"tila": "ok", "viesti": "v" * 200}}}))
        (self.t / "jono.json").write_text(json.dumps({"tehtavat": [{"id": "x", "tyyppi": "kehitys", "aihe": "testit", "repo": "r"}]}))

    def test_viesti_ja_aihe(self):
        ulos = tk.yojono_osio(self.t)
        self.assertIn("viesti: " + "v" * 120 + "\n", ulos + "\n")
        self.assertNotIn("v" * 121, ulos)
        self.assertIn("- x [kehitys/testit] r", ulos)

    def test_raportit_uusin_ensin_ja_enintaan_12(self):
        for i in range(14):
            f = self.t / "raportit" / ("r%02d.md" % i)
            f.write_text("# otsikko\n")
            os.utime(f, (1000 + i, 1000 + i))
        ulos = tk.yojono_osio(self.t)
        nimet = [l.split(" ")[1] for l in ulos.splitlines() if l.startswith("- r")]
        self.assertEqual(nimet, ["r%02d.md" % i for i in range(13, 1, -1)])

    def test_raportin_alku_enintaan_5_rivia(self):
        (self.t / "raportit" / "x.md").write_text("".join("- PR %d\n" % i for i in range(8)))
        ulos = tk.yojono_osio(self.t)
        self.assertIn("- PR 0 | - PR 1 | - PR 2 | - PR 3 | - PR 4\n", ulos + "\n")
        self.assertNotIn("- PR 5", ulos)

    def test_lukukelvoton_raportti_ei_kaada(self):
        (self.t / "raportit" / "hakemisto.md").mkdir()
        self.assertIn("- hakemisto.md (0 merkkia): ", tk.yojono_osio(self.t))


class KulutTarkkaTesti(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())

    def kirjoita(self, data):
        (self.t / "tila.json").write_text(json.dumps(data))

    def test_vain_viimeiset_14_paivaa_ja_2_kuukautta(self):
        self.kirjoita({"kulut": {"2026-10-%02d" % p: 1.0 for p in range(1, 16)}})
        teksti = tk.kulut_osio(self.t, None)
        self.assertEqual(len([l for l in teksti.splitlines() if l.startswith("- yojono ")]), 14)
        self.assertNotIn("2026-10-01:", teksti)
        self.kirjoita({"kulut": {"2026-08-01": 1.0, "2026-09-01": 1.0, "2026-10-01": 1.0}})
        teksti = tk.kulut_osio(self.t, None)
        self.assertNotIn("kuukausi 2026-08", teksti)
        self.assertIn("kuukausi 2026-09", teksti)
        self.assertIn("kuukausi 2026-10", teksti)

    def test_taasmays_vain_viimeiset_7_ja_oikean_paivan_ajot(self):
        kulut = {"2026-10-%02d" % p: 0.0 for p in range(1, 9)}
        self.kirjoita({"kulut": kulut, "tehtavat": {
            "a": {"pvm": "2026-10-08", "kulu": 0.3}, "b": {"pvm": "2026-10-07", "kulu": 0.5},
            "c": {"pvm": "2026-10-06", "kulu": 0}, "d": {"pvm": "2026-10-06", "kulu": None}}})
        teksti = tk.kulut_osio(self.t, None)
        self.assertNotIn("- 2026-10-01: tehtavien", teksti)
        self.assertIn("- 2026-10-08: tehtavien viimeisimmat ajot 0.30,", teksti)
        self.assertIn("- 2026-10-07: tehtavien viimeisimmat ajot 0.50,", teksti)
        self.assertIn("- 2026-10-06: tehtavien viimeisimmat ajot 0.00,", teksti)

    def test_ei_numeerinen_kulu_lasketaan_nollaksi(self):
        self.kirjoita({"kulut": {"2026-10-20": "x"}})
        self.assertIn("kirjattu 0.00 -> OK", tk.kulut_osio(self.t, None))

    def test_portti_kirjanpito_mukaan_rajattuna(self):
        portti = Path(tempfile.mkdtemp())
        (portti / "portti-kulu.json").write_text(json.dumps({"2026-10-20": {"\u00e4": 0.5}}))
        teksti = tk.kulut_osio(self.t, portti)
        self.assertIn("## Portti (paivakirjanpito)", teksti)
        self.assertIn('{"2026-10-20": {"\\u00e4": 0.5}}', teksti)
        (portti / "portti-kulu.json").write_text(json.dumps({"k": "x" * 3000}))
        loppu = tk.kulut_osio(self.t, portti).splitlines()[-1]
        self.assertEqual(len(loppu), 1500)


class KomentoTesti(unittest.TestCase):
    def test_stdout_ja_stderr_yhdistetaan(self):
        self.assertEqual(tk._komento("sh", "-c", "echo a; echo b >&2"), "a\nb")

    def test_virheet_ja_parametrit(self):
        self.assertEqual(tk._komento("/ei/ole/tata"), "(ei saatavilla)")
        with mock.patch.object(tk.subprocess, "run", side_effect=tk.subprocess.TimeoutExpired("x", 15)):
            self.assertEqual(tk._komento("x"), "(ei saatavilla)")
        with mock.patch.object(tk.subprocess, "run", return_value=mock.Mock(stdout="o", stderr="")) as r:
            tk._komento("a", "b")
        self.assertEqual(r.call_args[0][0], ["a", "b"])
        self.assertEqual(r.call_args[1], {"capture_output": True, "text": True, "timeout": 15, "check": False})


class PalvelinOsioTesti(unittest.TestCase):
    def osio(self, meminfo="MemTotal: 1000 kB\nMemAvailable: 500 kB\nviallinen\nX: a:b\n", komento=None, virhe=False):
        def lue(self_, *a, **k):
            if virhe:
                raise OSError("ei")
            return {"/proc/meminfo": meminfo, "/proc/loadavg": "0.1 0.2 0.3 1/2 3\n"}[str(self_)]
        with mock.patch.object(tk.shutil, "disk_usage", return_value=(3e9, 2e9, 1e9)), \
                mock.patch.object(tk.Path, "read_text", autospec=True, side_effect=lue), \
                mock.patch.object(tk, "_komento", komento or (lambda *a: "")):
            return tk.palvelin_osio()

    def test_levy_muisti_kuorma(self):
        ulos = self.osio()
        self.assertIn("levy /: 2.0 GB kaytetty / 3.0 GB (vapaa 1.0 GB)", ulos)
        self.assertIn("muisti: yht 1000 kB, vapaana 500 kB", ulos)
        self.assertIn("kuorma: 0.1 0.2 0.3 1/2 3", ulos)

    def test_lukuvirhe(self):
        self.assertIn("(muisti/kuorma ei saatavilla)", self.osio(virhe=True))

    def test_komennot(self):
        self.assertIn("(ei yhtaan)", self.osio())
        ulos = self.osio(komento=lambda *a: ("z" * 2000 if "list-timers" in a else "ok" if a[0] == "systemctl" else "v" * 100))
        self.assertEqual(ulos.count("z"), 1500)
        self.assertIn("node: " + "v" * 60 + "\n", ulos + "\n")
        self.assertNotIn("v" * 61, ulos)
        for n in ("node", "npm", "python3", "git"):
            self.assertIn(n + ": ", ulos)

    def test_isanta_virheviesti_katkeaa(self):
        def rikki():
            raise RuntimeError("e" * 300)
        self.assertEqual(tk.isanta_osio(rikki).split("saatavilla: ")[1].rstrip(")"), "e" * 200)


class KeraaJaMainTesti(unittest.TestCase):
    def test_kohdehakemisto_luodaan_sisakkain(self):
        t = Path(tempfile.mkdtemp())
        kohde = t / "a" / "b" / "c"
        tk.keraa_kaikki(kohde, t, None, hae_tekoinen, lambda: "x")
        tk.keraa_kaikki(kohde, t, None, hae_tekoinen, lambda: "x")
        self.assertTrue((kohde / "github.md").exists())

    def ajaa(self, argv, env=None):
        ulos, virhe = io.StringIO(), io.StringIO()
        with mock.patch.object(tk, "keraa_kaikki", return_value=["a.md", "b.md"]) as k, \
                mock.patch.dict(os.environ, env or {}, clear=False), \
                contextlib.redirect_stdout(ulos), contextlib.redirect_stderr(virhe):
            koodi = tk.main(argv)
        return koodi, ulos.getvalue(), virhe.getvalue(), k

    def test_main_onnistuu(self):
        koodi, ulos, _, k = self.ajaa(["kohde"], {"YOJONO_PERUS": "/p", "PORTTI_TILA": "/q"})
        self.assertEqual(koodi, 0)
        self.assertEqual(ulos, "tilannekuva: a.md, b.md\n")
        self.assertEqual(k.call_args[0], ("kohde", "/p", "/q"))

    def test_main_ilman_argumenttia_tulostaa_kayton(self):
        koodi, ulos, virhe, k = self.ajaa([])
        self.assertEqual(koodi, 2)
        self.assertIn("kaytto: python3 -m agentti.tilannekuva", virhe)
        k.assert_not_called()
        self.assertEqual(self.ajaa(["a", "b"])[0], 2)

    def test_main_lukee_sys_argv(self):
        with mock.patch.object(sys, "argv", ["x", "kohde"]):
            koodi, _, _, k = self.ajaa(None)
        self.assertEqual((koodi, k.call_args[0][0]), (0, "kohde"))


if __name__ == "__main__":
    unittest.main()
