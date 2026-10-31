import json
import os
import tempfile
import unittest
from pathlib import Path

from agentti import tilasivu as ts

NYT = 1793460000   # kiintea hetki testeihin
H = 3600


def tieto(tila="inactive", tulos="success", lopetus=None, alku=None, status="0", load="loaded"):
    return {"LoadState": load, "ActiveState": tila, "SubState": "x", "Result": tulos, "ExecMainStatus": status,
            "ExecMainStartTimestamp": "@%d" % alku if alku else "", "ExecMainExitTimestamp": "@%d" % lopetus if lopetus else ""}


def arvio(t, ajastimet=("active",), vanhenee=2, kesto=75, edellinen=None):
    return ts.arvioi_palvelu(t, list(ajastimet), vanhenee, kesto, edellinen, NYT)


class PalveluTesti(unittest.TestCase):
    def test_tuore_onnistunut_on_vihrea_ja_kirjaa_ajan(self):
        vari, _, ok = arvio(tieto(lopetus=NYT - 600))
        self.assertEqual((vari, ok), ("vihrea", NYT - 600))

    def test_epaonnistunut_on_punainen(self):
        vari, syy, _ = arvio(tieto(tila="failed", tulos="exit-code", status="1", lopetus=NYT - 60))
        self.assertEqual(vari, "punainen")
        self.assertIn("exit-code", syy)

    def test_epaonnistumisen_jalkeen_muistetaan_edellinen_onnistunut(self):
        vari, _, ok = arvio(tieto(tila="failed", tulos="exit-code", status="1", lopetus=NYT - 60), edellinen=NYT - H)
        self.assertEqual((vari, ok), ("punainen", NYT - H))   # epaonnistunut ajo ei ylikirjoita onnistunutta

    def test_vanhentuminen_keltainen_ja_punainen(self):
        self.assertEqual(arvio(tieto(lopetus=NYT - 3 * H))[0], "keltainen")    # yli 2 h
        self.assertEqual(arvio(tieto(lopetus=NYT - 5 * H))[0], "punainen")     # yli 4 h: 30.10. 20 h seisonta olisi punainen
        self.assertEqual(arvio(tieto(lopetus=NYT - 20 * H))[0], "punainen")

    def test_ajastin_pois_on_punainen(self):
        vari, syy, _ = arvio(tieto(lopetus=NYT - 60), ajastimet=("inactive",))
        self.assertEqual((vari, syy), ("punainen", "ajastin ei ole kaytossa"))

    def test_kaynnissa_ja_liian_pitka_ajo(self):
        self.assertEqual(arvio(tieto(tila="activating", alku=NYT - 600), edellinen=NYT - 600)[0], "vihrea")
        vari, syy, _ = arvio(tieto(tila="activating", alku=NYT - 100 * 60), edellinen=NYT - 600)
        self.assertEqual(vari, "keltainen")
        self.assertIn("yli 75 min", syy)

    def test_ei_asennettu_on_harmaa_ja_ei_hata(self):
        self.assertEqual(arvio(tieto(load="not-found"))[0], "harmaa")
        self.assertEqual(ts._huonoin(["harmaa", "harmaa"]), "vihrea")

    def test_systemctl_ei_vastaa(self):
        self.assertEqual(arvio({})[0], "keltainen")

    def test_ei_tietoa_onnistuneesta_ajosta_on_keltainen_ei_punainen(self):
        self.assertEqual(arvio(tieto(tila="activating", alku=NYT - 60))[0], "keltainen")

    def test_jatkuva_palvelu_ilman_ajastinta_ja_vanhenemista(self):
        self.assertEqual(arvio(tieto(tila="active"), ajastimet=(), vanhenee=0, kesto=0)[0], "vihrea")


class TiedostoTesti(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name)

    def kirjoita(self, polku, sisalto):
        p = self.d / polku
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sisalto if isinstance(sisalto, str) else json.dumps(sisalto), encoding="utf-8")
        return p

    def docs(self, tehtavat, tulokset, ika_s):
        for n in tehtavat:
            self.kirjoita("docs/claude-jono/tehtavat/" + n, "x")
        for n in tulokset:
            p = self.kirjoita("docs/claude-jono/tulokset/" + n, "# Tulos\n- Tila: VALMIS\n")
            os.utime(p, (NYT - ika_s, NYT - ika_s))
        return self.d / "docs"

    def test_jono_tyhja_on_vihrea(self):
        r = ts.claudejono_osio(self.docs(["2026-10-31-01-a.md"], ["2026-10-31-01-a.md"], 10 * H), NYT)
        self.assertEqual((r["vari"], r["odottavat"]), ("vihrea", 0))

    def test_jono_seisoo_20h_on_punainen(self):
        r = ts.claudejono_osio(self.docs(["2026-10-30-01-a.md", "2026-10-30-02-b.md"], ["2026-10-30-01-a.md"], 20 * H), NYT)
        self.assertEqual((r["vari"], r["odottavat"]), ("punainen", 1))
        self.assertIn("20.0 h", r["syy"])

    def test_jono_hidas_on_keltainen_ja_tuore_vihrea(self):
        self.assertEqual(ts.claudejono_osio(self.docs(["2026-10-31-01-a.md", "2026-10-31-02-b.md"], ["2026-10-31-01-a.md"], 3 * H), NYT)["vari"], "keltainen")
        self.assertEqual(ts.claudejono_osio(self.docs(["2026-10-31-01-a.md", "2026-10-31-02-b.md"], ["2026-10-31-01-a.md"], 20 * 60), NYT)["vari"], "vihrea")

    def test_tulosajaksi_luetaan_aika_rivi(self):
        p = self.kirjoita("a.md", "# T\n- Aika: 2026-10-31T10:00+00:00\n")
        self.assertEqual(ts._tulosaika(p), 1793440800)

    def test_docs_klooni_puuttuu(self):
        self.assertEqual(ts.claudejono_osio(self.d / "ei-ole", NYT)["vari"], "keltainen")

    def test_yojono_tanaan(self):
        self.kirjoita("tila.json", {"kulut": {}, "tehtavat": {"a": {"tila": "valmis", "pvm": "2026-10-31", "kulu": 1},
                                                              "b": {"tila": "epaonnistui", "pvm": "2026-10-31", "kulu": 1},
                                                              "c": {"tila": "valmis", "pvm": "2026-10-30", "kulu": 1}}})
        self.kirjoita("jono.json", {"tehtavat": [{"id": "a"}, {"id": "uusi"}]})
        r = ts.yojono_osio(self.d, "2026-10-31")
        self.assertEqual((r["tehdyt"], r["epaonnistuneet"], r["ei_ajettu"], r["vari"]), (["a"], ["b"], ["uusi"], "keltainen"))

    def test_yojono_tila_puuttuu(self):
        self.assertEqual(ts.yojono_osio(self.d, "2026-10-31")["vari"], "keltainen")

    def kulut(self, kulut, tehtavat=None, p_katto=5.0, k_katto=100.0):
        self.kirjoita("tila.json", {"kulut": kulut, "tehtavat": tehtavat or {}})
        self.kirjoita("jono.json", {"paivakatto": p_katto, "kuukausikatto": k_katto})
        return ts.kulut_osio(self.d, "2026-10-31")

    def test_kulut_katoissa(self):
        r = self.kulut({"2026-10-30": 3.0, "2026-10-31": 1.0})
        self.assertEqual((r["vari"], r["paiva_usd"], r["kuukausi_usd"]), ("vihrea", 1.0, 4.0))

    def test_kulut_raja_keltainen_ja_katto_punainen(self):
        self.assertEqual(self.kulut({"2026-10-31": 4.2})["vari"], "keltainen")
        self.assertEqual(self.kulut({"2026-10-31": 5.0})["vari"], "punainen")
        self.assertEqual(self.kulut({"2026-10-01": 98.0, "2026-10-31": 2.0})["vari"], "punainen")   # kuukausikatto

    def test_kulut_lahteiden_ero_huomautetaan(self):
        r = self.kulut({"2026-10-30": 3.0}, {"a": {"tila": "valmis", "pvm": "2026-10-30", "kulu": 4.0}})
        self.assertEqual(len(r["huomautukset"]), 1)
        self.assertIn("eroavat", r["huomautukset"][0])
        r = self.kulut({"2026-10-30": 4.0}, {"a": {"tila": "valmis", "pvm": "2026-10-30", "kulu": 4.0}})
        self.assertEqual(r["huomautukset"], [])

    def test_kulut_ei_muuta_kattoja(self):
        self.kulut({"2026-10-31": 1.0})
        self.assertEqual(json.loads((self.d / "jono.json").read_text()), {"paivakatto": 5.0, "kuukausikatto": 100.0})

    def test_kulut_rikkinainen_tila(self):
        self.kirjoita("tila.json", "{rikki")
        self.assertEqual(ts.kulut_osio(self.d, "2026-10-31")["vari"], "keltainen")

    def test_tonylle_suodattaa_ratkaistut_ja_kohinan(self):
        self.kirjoita("t.md", "# T\n\n## vanha\n- P\u00c4\u00c4TETT\u00c4V\u00c4: liian vanha osio\n\n## a\n- Valvonta: kohinaa\n"
                              "- P\u00c4\u00c4TETT\u00c4V\u00c4: npm 0.4.0?\n- RATKAISTU 31.10. P\u00c4\u00c4TETT\u00c4V\u00c4: tehty\n"
                              "- Odottaa mergea: x token=abcdefghijkl123456\n\n## b\n- Mergep\u00e4\u00e4t\u00f6s: PR 5\n")
        r = ts.tonylle_osio(self.d / "t.md")
        self.assertEqual(len(r["rivit"]), 3)
        self.assertTrue(r["rivit"][0].startswith("P\u00c4\u00c4TETT\u00c4V\u00c4: npm"))
        self.assertNotIn("abcdefghijkl123456", " ".join(r["rivit"]))   # salaisuuden nakoinen siivotaan
        self.assertNotIn("liian vanha", " ".join(r["rivit"]))

    def test_tonylle_puuttuu(self):
        self.assertEqual(ts.tonylle_osio(self.d / "ei.md")["vari"], "keltainen")


def pr_hae(otsikko="x"):
    def hae(url):
        if "/pulls?state=open" in url:
            return [{"number": 7, "title": otsikko, "draft": False, "head": {"sha": "abc"}, "html_url": "https://github.com/o/r/pull/7"}] \
                if "/agentti-pohja/" in url else []
        if "/check-runs" in url:
            return {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        if "/files" in url:
            return [{"filename": "yojono/ajuri.py", "additions": 3, "deletions": 1}]
        raise AssertionError(url)
    return hae


class PrTesti(unittest.TestCase):
    def test_tonyn_pr_ja_valimuisti(self):
        r, tila = ts.pr_osio({}, NYT, pr_hae())
        self.assertEqual([(x["repo"], x["numero"]) for x in r["rivit"]], [("agentti-pohja", 7)])
        r2, _ = ts.pr_osio({"pr": tila}, NYT + 600, lambda u: self.fail("ei saa hakea valimuistin aikana"))
        self.assertTrue(r2["valimuistista"])
        self.assertEqual(len(r2["rivit"]), 1)

    def test_haun_epaonnistuessa_pidetaan_vanhat(self):
        def rikki(url):
            raise OSError("verkko")
        _, tila = ts.pr_osio({}, NYT, pr_hae())
        # keraa() nielee yksittaiset repovirheet: kaikkien virhe nayttaa tyhjana, ei kaadu
        r, _ = ts.pr_osio({"pr": tila}, NYT + 2 * H, rikki)
        self.assertIn(r["vari"], ("vihrea", "keltainen"))


def feikki_show(tilat):
    def show(yksikko):
        return tilat.get(yksikko, tieto(load="not-found"))
    return show


class KokonaisuusTesti(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        d = Path(self.t.name)
        (d / "perus").mkdir()
        (d / "perus/tila.json").write_text(json.dumps({"kulut": {"2026-10-31": 1.0}, "tehtavat": {}}))
        (d / "perus/jono.json").write_text(json.dumps({"paivakatto": 5.0, "kuukausikatto": 100.0, "tehtavat": []}))
        (d / "docs/claude-jono/tehtavat").mkdir(parents=True)
        (d / "docs/claude-jono/tulokset").mkdir(parents=True)
        (d / "docs/claude-jono/tonylle.md").write_text("## a\n- P\u00c4\u00c4TETT\u00c4V\u00c4: x\n", encoding="utf-8")
        self.env = {"YOJONO_PERUS": str(d / "perus"), "CLAUDEJONO_REPO": str(d / "docs"), "TILASIVU_KOHDE": str(d / "ulos")}
        self.ulos = d / "ulos"
        self.tilat = {"claude-jono.service": tieto(lopetus=NYT - 60), "claude-jono.timer": tieto(tila="active")}
        self.viestit = []

    def laheta(self, tila, otsikko, teksti=""):
        self.viestit.append((tila, otsikko, teksti))
        return "lahetetty"

    def aja(self, *liput, laheta=None):
        return ts.main(list(liput) + ["--ei-pr"], env=self.env, show=feikki_show(self.tilat), laheta=laheta or self.laheta, nyt=NYT)

    def test_kaikki_kunnossa_ei_viestia_ja_tiedostot_syntyvat(self):
        self.assertEqual(self.aja(), 0)
        self.assertEqual(self.viestit, [])
        for n in ("index.html", "tila.md", "tila.json", "tilasivu-tila.json"):
            self.assertTrue((self.ulos / n).is_file(), n)
        self.assertEqual(json.loads((self.ulos / "tila.json").read_text())["yleinen"], "vihrea")

    def test_punainen_laukaisee_yhden_viestin_eika_toista(self):
        self.tilat["claude-jono.service"] = tieto(tila="failed", tulos="exit-code", status="1", lopetus=NYT - 60)
        self.aja()
        self.aja()
        self.aja()
        self.assertEqual(len(self.viestit), 1)
        self.assertEqual(self.viestit[0][0], "virhe")
        self.assertIn("claude-jono", self.viestit[0][2])

    def test_toipuminen_ja_uusi_vika_viestittaa_uudelleen(self):
        self.tilat["claude-jono.service"] = tieto(tila="failed", tulos="exit-code", status="1", lopetus=NYT - 60)
        self.aja()
        self.tilat["claude-jono.service"] = tieto(lopetus=NYT - 60)
        self.aja()
        self.assertEqual(len(self.viestit), 1)    # toipumisesta ei viestia
        self.tilat["claude-jono.service"] = tieto(tila="failed", tulos="exit-code", status="1", lopetus=NYT - 30)
        self.aja()
        self.assertEqual(len(self.viestit), 2)

    def test_uusi_punainen_toisen_jo_ollessa_punainen(self):
        self.tilat["claude-jono.service"] = tieto(tila="failed", tulos="exit-code", status="1")
        self.aja()
        self.tilat["yojono.service"] = tieto(tila="failed", tulos="exit-code", status="1")
        self.tilat["yojono.timer"] = tieto(tila="active")
        self.aja()
        self.assertEqual(len(self.viestit), 2)

    def test_epaonnistunut_lahetys_yritetaan_uudelleen(self):
        self.tilat["claude-jono.service"] = tieto(tila="failed", tulos="exit-code", status="1")
        self.aja(laheta=lambda *a: "epaonnistui")
        self.aja()
        self.assertEqual(len(self.viestit), 1)

    def test_ei_telegram_lippu(self):
        self.tilat["claude-jono.service"] = tieto(tila="failed", tulos="exit-code", status="1")
        self.aja("--ei-telegram")
        self.assertEqual(self.viestit, [])

    def test_html_escape_ja_ei_salaisuuksia(self):
        d = Path(self.t.name)
        (d / "docs/claude-jono/tonylle.md").write_text(
            "## a\n- P\u00c4\u00c4TETT\u00c4V\u00c4: <script>alert(1)</script> sk-abcdefghijklmnopqrstuvwx\n", encoding="utf-8")
        self.aja()
        sivu = (self.ulos / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)", sivu)
        self.assertIn("&lt;script&gt;alert(1)", sivu)
        for n in ("index.html", "tila.md", "tila.json"):
            self.assertNotIn("sk-abcdefghijklmnopqrstuvwx", (self.ulos / n).read_text(encoding="utf-8"))

    def test_pr_otsikko_ja_url_escape(self):
        data, _ = ts.keraa(self.env, NYT, feikki_show(self.tilat), pr_hae('<img src=x onerror=1>"'), tila={})
        sivu = ts.muotoile_html(data)
        self.assertNotIn("<img src=x", sivu)
        self.assertIn("&lt;img", sivu)

    def test_vanhentumisvahti_on_sivulla(self):
        self.aja()
        sivu = (self.ulos / "index.html").read_text(encoding="utf-8")
        self.assertIn("SIVU VANHENTUNUT", sivu)
        self.assertIn('data-t="%d"' % NYT, sivu)

    def test_vaara_lippu(self):
        self.assertEqual(ts.main(["--tuntematon"], env=self.env), 2)

    def test_onnistunut_ajo_kirjataan_tilaan(self):
        self.aja()
        tila = json.loads((self.ulos / "tilasivu-tila.json").read_text())
        self.assertEqual(tila["viimeisin_ok"]["claude-jono.service"], NYT - 60)


if __name__ == "__main__":
    unittest.main()
