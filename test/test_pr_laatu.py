import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from unittest import mock
from datetime import datetime, timezone

from agentti import pr_laatu as pl

NYT = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def pr(nro, otsikko, tila="open", merged=False, luotu="2026-10-27T10:00:00Z", kirjoittaja="agenttifirma-bot"):
    return {"number": nro, "title": otsikko, "state": tila, "merged_at": "2026-10-27T12:00:00Z" if merged else None,
            "created_at": luotu, "user": {"login": kirjoittaja}}


def feikki(data):
    def hae(url):
        repo = url.split("/repos/%s/" % pl.OMISTAJA)[1].split("/")[0]
        return data.get(repo, [])
    return hae


class KerausTesti(unittest.TestCase):
    def test_laskee_tilat_repoittain_ja_aiheittain(self):
        hae = feikki({
            "llm-stopcheck": [pr(1, "[yojono] yojono: testit llm-stopcheck", "closed", merged=True),
                              pr(2, "yojono: readme llm-stopcheck", "closed"),
                              pr(3, "yojono: riippuvuudet llm-stopcheck", "open")],
            "mcp-model-router": [pr(4, "yojono: testit mcp-model-router", "closed", merged=True), pr(5, "jokin muu", "open")],
        })
        t = pl.keraa(hae, 30, NYT)
        self.assertEqual(t["yht"], {"mergattu": 2, "suljettu": 1, "auki": 2})
        self.assertEqual(t["repoittain"]["llm-stopcheck"], {"mergattu": 1, "suljettu": 1, "auki": 1})
        self.assertEqual(t["aiheittain"]["testit"], {"mergattu": 2, "suljettu": 0, "auki": 0})
        self.assertEqual(t["aiheittain"]["muu"], {"mergattu": 0, "suljettu": 0, "auki": 1})

    def test_vain_botin_prt_ja_vain_ikkunan_sisalta(self):
        hae = feikki({"llm-stopcheck": [pr(1, "yojono: testit x", "open", kirjoittaja="tonyweckstrom1987"),
                                        pr(2, "yojono: testit x", "open", luotu="2026-09-01T00:00:00Z"),
                                        pr(3, "yojono: testit x", "open", luotu="roska"),
                                        pr(4, "yojono: testit x", "open", kirjoittaja="AgenttiFirma-Bot")]})
        t = pl.keraa(hae, 30, NYT)
        self.assertEqual(t["yht"], {"mergattu": 0, "suljettu": 0, "auki": 1})

    def test_ikkunan_raja_paivissa(self):
        hae = feikki({"llm-stopcheck": [pr(1, "yojono: testit x", "open", luotu="2026-10-20T00:00:00Z")]})
        self.assertEqual(pl.keraa(hae, 7, NYT)["yht"]["auki"], 0)
        self.assertEqual(pl.keraa(hae, 9, NYT)["yht"]["auki"], 1)

    def test_hakuvirhe_ei_kaada(self):
        def hae(url):
            raise OSError("verkko")
        self.assertEqual(pl.keraa(hae, 30, NYT)["yht"], {"mergattu": 0, "suljettu": 0, "auki": 0})

    def test_app_tunnus_bot_paate_lasketaan(self):
        hae = feikki({"llm-stopcheck": [pr(1, "[yojono] testit x", "open", kirjoittaja="agenttifirma-bot[bot]"),
                                        pr(2, "[yojono] testit x", "closed", merged=True, kirjoittaja="agenttifirma-bot"),
                                        pr(3, "[yojono] testit x", "open", kirjoittaja="joku-toinen[bot]")]})
        self.assertEqual(pl.keraa(hae, 30, NYT)["yht"], {"mergattu": 1, "suljettu": 0, "auki": 1})

    def test_hakuvirhe_nakyy_tulosteessa_ilman_viestia(self):
        import urllib.error
        def hae(url):
            raise urllib.error.HTTPError(url, 403, "SALAINEN-VIESTI", {}, None)
        t = pl.keraa(hae, 30, NYT)
        self.assertEqual(len(t["virheet"]), len(pl.REPOT))
        teksti = pl.muotoile(t)
        self.assertIn("%d hakuvirhetta, luvut ovat alarajoja" % len(pl.REPOT), teksti)
        self.assertIn("HTTPError 403", teksti)
        self.assertNotIn("SALAINEN", teksti)

    def test_osittainen_hakuvirhe_sailyttaa_muut_luvut(self):
        def hae(url):
            if "/llm-stopcheck/" in url:
                raise OSError("verkko")
            return [pr(1, "[yojono] testit x", "open")] if "/evalkit/" in url else []
        t = pl.keraa(hae, 30, NYT)
        self.assertEqual(t["yht"]["auki"], 1)
        self.assertEqual(t["virheet"], ["llm-stopcheck: OSError"])
        self.assertIn("1 hakuvirhetta", pl.muotoile(t))

    def test_ei_varoitusta_ilman_virheita(self):
        self.assertNotIn("VAROITUS", pl.muotoile(pl.keraa(feikki({}), 30, NYT)))

    def test_aihe_hakasulkuotsikosta(self):
        self.assertEqual(pl.aihe("[yojono] testit llm-stopcheck"), "testit")
        self.assertEqual(pl.aihe("[yojono] README-paivitys"), "readme")
        self.assertEqual(pl.aihe("[yojono] yojono: ominaisuus x"), "ominaisuus")
        self.assertEqual(pl.aihe("[yojono] korjaus x"), "muu")

    def test_aihe_otsikosta(self):
        for otsikko, odotus in (("yojono: testit a", "testit"), ("[yojono] yojono: README b", "readme"), ("yojono: riippuvuudet c", "riippuvuudet"),
                                ("yojono: ominaisuus d", "ominaisuus"), ("yojono: katselmointi e", "muu"), (None, "muu")):
            self.assertEqual(pl.aihe(otsikko), odotus)


class MuotoiluJaMainTesti(unittest.TestCase):
    def test_muotoilu(self):
        hae = feikki({"llm-stopcheck": [pr(1, "yojono: testit x", "closed", merged=True), pr(2, "yojono: readme x", "closed"), pr(3, "yojono: readme x", "open")]})
        teksti = pl.muotoile(pl.keraa(hae, 30, NYT), 30)
        self.assertIn("Yojono-PR:t 30 vrk: 1 mergattu, 1 suljettu, 1 auki (mergausprosentti 50 %)", teksti)
        self.assertIn("repoittain (mergattu/kaikki): llm-stopcheck 1/3", teksti)
        self.assertIn("aiheittain (mergattu/kaikki): readme 0/2, testit 1/1", teksti)

    def test_mergausprosentti_pyoristyy(self):
        t = {"mergattu": 2, "suljettu": 1, "auki": 0}
        self.assertEqual(pl._osuus(t), "67 %")
        self.assertEqual(pl._osuus({"mergattu": 1, "suljettu": 2, "auki": 5}), "33 %")

    def test_oletushaku_lukee_json_ja_pyytaa_user_agentin(self):
        class V:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'[{"number": 1}]'
        with mock.patch.object(pl.urllib.request, "urlopen", return_value=V()) as u:
            self.assertEqual(pl._oletushaku("https://api.github.com/x"), [{"number": 1}])
        pyynto = u.call_args[0][0]
        self.assertEqual(pyynto.full_url, "https://api.github.com/x")
        self.assertEqual(pyynto.get_header("User-agent"), "agenttifirma-pr-laatu")
        self.assertEqual(u.call_args[1]["timeout"], 20)

    def test_main_lukee_sys_argvn(self):
        ulos = io.StringIO()
        with mock.patch.object(pl.sys, "argv", ["pr_laatu", "7"]), mock.patch.object(pl, "_oletushaku", return_value=[]), contextlib.redirect_stdout(ulos):
            self.assertEqual(pl.main(), 0)
        self.assertIn("7 vrk", ulos.getvalue())

    def test_tyhja_ei_jaa_nollalla(self):
        teksti = pl.muotoile({"yht": {"mergattu": 0, "suljettu": 0, "auki": 0}, "repoittain": {}, "aiheittain": {}}, 7)
        self.assertEqual(teksti, "Yojono-PR:t 7 vrk: 0 mergattu, 0 suljettu, 0 auki (mergausprosentti -)")

    def ajaa(self, argv):
        ulos, virhe = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(ulos), contextlib.redirect_stderr(virhe):
            rc = pl.main(argv, hae=feikki({}))
        return rc, ulos.getvalue(), virhe.getvalue()

    def test_main_oletus_ja_paivat(self):
        rc, ulos, _ = self.ajaa([])
        self.assertEqual(rc, 0)
        self.assertIn("30 vrk", ulos)
        self.assertIn("7 vrk", self.ajaa(["7"])[1])

    def test_main_virheellinen_argumentti(self):
        for a in (["x"], ["1", "2"]):
            rc, ulos, virhe = self.ajaa(a)
            self.assertEqual((rc, ulos), (2, ""))
            self.assertIn("kaytto:", virhe)


def _tulos(mergattu, suljettu):
    return {"yht": {"mergattu": mergattu, "suljettu": suljettu, "auki": 0}, "repoittain": {}, "aiheittain": {}}


class KuluTesti(unittest.TestCase):
    def test_kulu_ajalta_summaa_vain_ikkunan_sisalta(self):
        kulut = {"2026-09-27": 100.0, "2026-09-28": 1.5, "2026-10-28": 2.5, "x": 9.0, "2026-10-01": "ei-luku"}
        self.assertEqual(pl.kulu_ajalta(kulut, 30, NYT), 4.0)
        self.assertEqual(pl.kulu_ajalta(kulut, 29, NYT), 2.5)
        self.assertEqual(pl.kulu_ajalta(None, 30, NYT), 0)

    def test_kulu_ajalta_oletusaika_on_nyt(self):
        tanaan = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual(pl.kulu_ajalta({tanaan: 1.25}), 1.25)

    def test_lue_kulut_tiedostosta_ja_virheet_tyhjaksi(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pl.lue_kulut(d), {})
            with open(os.path.join(d, "tila.json"), "w") as f:
                json.dump({"kulut": {"2026-10-27": 3.0}}, f)
            self.assertEqual(pl.lue_kulut(d), {"2026-10-27": 3.0})
            for sisalto in ("ei json", "[]", '{"kulut": 5}'):
                with open(os.path.join(d, "tila.json"), "w") as f:
                    f.write(sisalto)
                self.assertEqual(pl.lue_kulut(d), {}, sisalto)

    def test_lue_kulut_oletuspolku_ymparistosta(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "tila.json"), "w") as f:
                json.dump({"kulut": {"a": 1}}, f)
            with unittest.mock.patch.dict(os.environ, {"YOJONO_PERUS": d}):
                self.assertEqual(pl.lue_kulut(), {"a": 1})


class NostoehdotTesti(unittest.TestCase):
    def test_kaikki_ehdot_tayttyvat(self):
        ok, rivi = pl.nostoehdot(_tulos(20, 0), 80.0)
        self.assertTrue(ok)
        self.assertIn("EHDOT TAYTTYVAT", rivi)
        self.assertIn("paatettyja 20/20", rivi)
        self.assertIn("kulu per merge 4.0 USD", rivi)

    def test_liian_vahan_prita(self):
        ok, rivi = pl.nostoehdot(_tulos(19, 0), 10.0)
        self.assertIs(ok, False)
        self.assertIn("PR:ia liian vahan", rivi)
        self.assertNotIn("mergausprosentti alle", rivi)

    def test_mergausprosentti_raja(self):
        ok75, rivi75 = pl.nostoehdot(_tulos(15, 5), 10.0)               # 75 % tasan
        self.assertIs(ok75, True)
        self.assertIn("mergausprosentti 75 %", rivi75)
        ok, rivi = pl.nostoehdot(_tulos(14, 6), 10.0)                   # 70 %
        self.assertIs(ok, False)
        self.assertIn("mergausprosentti alle rajan", rivi)

    def test_kulu_per_merge_raja(self):
        self.assertIs(pl.nostoehdot(_tulos(20, 0), 80.0)[0], True)
        ok, rivi = pl.nostoehdot(_tulos(20, 0), 80.1)
        self.assertIs(ok, False)
        self.assertIn("kulu per merge yli rajan", rivi)

    def test_ei_mergattuja(self):
        ok, rivi = pl.nostoehdot(_tulos(0, 0), 5.0)
        self.assertIs(ok, False)
        self.assertIn("kulu per merge - ", rivi)
        self.assertIn("mergausprosentti 0 %", rivi)
        self.assertIn("kulu per merge yli rajan", rivi)

    def test_paivia_nakyy_rivilla(self):
        self.assertIn("(14 vrk)", pl.nostoehdot(_tulos(1, 0), 1.0, 14)[1])


if __name__ == "__main__":
    unittest.main()
