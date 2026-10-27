import os
import sqlite3
import stat
import sys
import tempfile
import unittest

from agentti import tapahtumaloki as tl


class LokiTest(unittest.TestCase):
    def setUp(self):
        self.y = tl.avaa(":memory:")
        self.addCleanup(self.y.close)

    def test_kirjaa_ja_yhteenveto(self):
        tl.kirjaa(self.y, "yojono", "katsaus", "ajo", "ok", kulu_usd=0.01, tiedot={"a": 1}, nyt=1000)
        tl.kirjaa(self.y, "yojono", "katsaus", "ajo", "virhe", kulu_usd=0.02, nyt=1000)
        y = tl.yhteenveto(self.y, 7, nyt=1000 + 86400)
        self.assertEqual(y["tapahtumat"], {"ajo/ok": 1, "ajo/virhe": 1})
        self.assertAlmostEqual(y["kulu_usd"], 0.03)

    def test_validointi(self):
        for kw in ({"lahde": ""}, {"agentti": " "}, {"tyyppi": None}, {"kulu_usd": -1}, {"kulu_usd": True}):
            args = dict(lahde="l", agentti="a", tyyppi="t", tulos="r")
            args.update(kw)
            with self.assertRaises(ValueError):
                tl.kirjaa(self.y, **args)

    def test_tapahtumaa_ei_voi_muokata_mutta_vanhenee(self):
        id_ = tl.kirjaa(self.y, "l", "a", "t", "r", nyt=100)
        with self.assertRaises(sqlite3.DatabaseError):
            self.y.execute("UPDATE tapahtuma SET tulos='x' WHERE id=?", (id_,))
        self.assertEqual(tl.siivoa(self.y, 90, nyt=100 + 91 * 86400), 1)
        self.assertEqual(tl.siivoa(self.y, 90, nyt=100 + 91 * 86400), 0)

    def test_salaisuudet_poistuvat(self):
        tl.kirjaa(self.y, "l", "a", "t", "r",
                  tiedot={"avain": "sk-abcdefghijklmnop", "h": "Bearer abc123", "tg": "123456789:" + "A" * 35})
        teksti = self.y.execute("SELECT tiedot_json FROM tapahtuma").fetchone()[0]
        for osa in ("sk-abcdef", "abc123", "AAAAAAAAAA"):
            self.assertNotIn(osa, teksti)
        self.assertIn("[poistettu]", teksti)

    def test_tiedot_rajataan(self):
        tl.kirjaa(self.y, "l", "a", "t", "r", tiedot={"x": "y" * 10000})
        self.assertLessEqual(len(self.y.execute("SELECT tiedot_json FROM tapahtuma").fetchone()[0]), tl.TIEDOT_MAX)

    def test_ehdotus_ja_palaute(self):
        a = tl.ehdota(self.y, "Idea", "toistui 4 kertaa", esitutkimus="ei valmista", mittari="alle 5 min")
        tl.paivita_ehdotus(self.y, a, tila="kokeilussa")
        tl.paivita_ehdotus(self.y, a, tila="onnistui", tulos="toimi")
        tl.kirjaa_palaute(self.y, a, "hyvaksy", "hyva")
        b = tl.ehdota(self.y, "Toinen", "x")
        tl.kirjaa_palaute(self.y, b, "hylkaa")
        y = tl.yhteenveto(self.y)
        self.assertEqual(y["ehdotukset"], {"hyvaksytty": 1, "hylatty": 1})
        self.assertEqual(y["hyvaksymis_pros"], 50.0)

    def test_ehdotus_virheet(self):
        with self.assertRaises(ValueError):
            tl.ehdota(self.y, "", "x")
        with self.assertRaises(ValueError):
            tl.paivita_ehdotus(self.y, 99, tila="onnistui")
        a = tl.ehdota(self.y, "I", "p")
        with self.assertRaises(ValueError):
            tl.paivita_ehdotus(self.y, a, tila="outo")
        with self.assertRaises(ValueError):
            tl.kirjaa_palaute(self.y, a, "ehka")

    def test_palautetta_ei_voi_muokata(self):
        a = tl.ehdota(self.y, "I", "p")
        p = tl.kirjaa_palaute(self.y, a, "hyvaksy")
        with self.assertRaises(sqlite3.DatabaseError):
            self.y.execute("UPDATE palaute SET paatos='hylkaa' WHERE id=?", (p,))

    def test_tyhja_yhteenveto(self):
        self.assertIsNone(tl.yhteenveto(self.y)["hyvaksymis_pros"])

    def test_tiedoston_oikeudet(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "loki.db")
            tl.avaa(polku).close()
            self.assertEqual(stat.S_IMODE(os.stat(polku).st_mode), 0o600)
            tl.avaa(polku).close()  # uudelleenavaus ei riko skeemaa



class KomentoriviTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.polku = os.path.join(self.tmp.name, "loki.db")

    def aja(self, *argv):
        import contextlib
        import io
        out = io.StringIO()
        with mock_env(self.polku), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            koodi = tl.main(list(argv))
        return koodi, out.getvalue()

    def test_kirjaa_ja_yhteenveto(self):
        self.assertEqual(self.aja("kirjaa", "terminal", "claude", "deploy", "ok", "0.02")[0], 0)
        koodi, ulos = self.aja("yhteenveto")
        self.assertEqual(koodi, 0)
        self.assertIn('"deploy/ok": 1', ulos)

    def test_virheelliset_kutsut(self):
        self.assertEqual(self.aja()[0], 2)
        self.assertEqual(self.aja("kirjaa", "a")[0], 2)
        self.assertEqual(self.aja("kirjaa", "l", "a", "t", "r", "ei-luku")[0], 2)
        self.assertEqual(self.aja("outo")[0], 2)

    def test_ilman_polkua(self):
        import contextlib
        import io
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tl.main(["yhteenveto"]), 2)


def mock_env(polku):
    from unittest import mock
    return mock.patch.dict(os.environ, {"TAPAHTUMALOKI": polku})


PV = 86400


class PuhdistaJaTallennusTarkkaTest(unittest.TestCase):
    def setUp(self):
        self.y = tl.avaa(":memory:")
        self.addCleanup(self.y.close)

    def rivi(self):
        return self.y.execute("SELECT lahde,agentti,tyyppi,tulos,tiedot_json FROM tapahtuma").fetchone()

    def test_ohjausmerkit_poistetaan_rivinvaihto_ja_tabulaattori_jaa(self):
        self.assertEqual(tl.puhdista("a\nb\tc\x01d\x7fe\x1ff"), "a\nb\tcdef")
        self.assertIsNone(tl.puhdista(None))
        self.assertEqual(tl.puhdista("  x  "), "x")
        self.assertEqual(tl.puhdista("abcdef", 3), "abc")

    def test_muistitietokanta_ei_luo_tiedostoa(self):
        vanha = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                tl.avaa(":memory:").close()
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(vanha)

    def test_tiedot_ascii_ja_avaimet_jarjestyksessa(self):
        tl.kirjaa(self.y, "l", "a", "t", "r", tiedot={"b": 1, "a": "\u00e4"})
        self.assertEqual(self.rivi()[4], '{"a": "\\u00e4", "b": 1}')

    def test_kenttien_pituusrajat(self):
        tl.kirjaa(self.y, "L" * 300, "A" * 300, "T" * 300, "R" * 300)
        l, a, t, r, _ = self.rivi()
        self.assertEqual((len(l), len(a), len(t), len(r)), (60, 60, 60, 200))

    def test_ehdotuksen_pituusrajat(self):
        i = tl.ehdota(self.y, "O" * 300, "P" * 2000, esitutkimus="E" * 2000, mittari="M" * 2000)
        o, p, e, m = self.y.execute("SELECT otsikko,perustelu,esitutkimus,mittari FROM ehdotus WHERE id=?", (i,)).fetchone()
        self.assertEqual((len(o), len(p), len(e), len(m)), (200, tl.TEKSTI_MAX, tl.TEKSTI_MAX, tl.TEKSTI_MAX))

    def test_aika_tallentuu(self):
        tl.kirjaa(self.y, "l", "a", "t", "r", nyt=123.5)
        self.assertEqual(self.y.execute("SELECT aika FROM tapahtuma").fetchone()[0], 123.5)


class EhdotusTarkkaTest(unittest.TestCase):
    def setUp(self):
        self.y = tl.avaa(":memory:")
        self.addCleanup(self.y.close)

    def tila(self, i):
        return self.y.execute("SELECT tila,tulos FROM ehdotus WHERE id=?", (i,)).fetchone()

    def test_tulos_tallentuu_ja_tila_sailyy(self):
        i = tl.ehdota(self.y, "I", "p")
        self.assertEqual(self.tila(i), ("ehdotettu", None))
        tl.paivita_ehdotus(self.y, i, tulos="toimi sk-abcdefghijklmnop")
        self.assertEqual(self.tila(i), ("ehdotettu", "toimi [poistettu]"))
        tl.paivita_ehdotus(self.y, i, tila="onnistui")
        self.assertEqual(self.tila(i), ("onnistui", "toimi [poistettu]"))

    def test_palaute_asettaa_tilan_ja_tallentaa_rivin(self):
        a = tl.ehdota(self.y, "A", "p")
        b = tl.ehdota(self.y, "B", "p")
        tl.kirjaa_palaute(self.y, a, "hyvaksy", "hyva", nyt=55.0)
        tl.kirjaa_palaute(self.y, b, "hylkaa")
        self.assertEqual(self.tila(a)[0], "hyvaksytty")
        self.assertEqual(self.tila(b)[0], "hylatty")
        self.assertEqual(self.y.execute("SELECT ehdotus_id,aika,paatos,huomio FROM palaute WHERE ehdotus_id=?", (a,)).fetchone(),
                         (a, 55.0, "hyvaksy", "hyva"))


class SiivoaYhteenvetoTarkkaTest(unittest.TestCase):
    def setUp(self):
        self.y = tl.avaa(":memory:")
        self.addCleanup(self.y.close)

    def lkm(self):
        return self.y.execute("SELECT COUNT(*) FROM tapahtuma").fetchone()[0]

    def kirjaa(self, aika, tulos="ok", kulu=None):
        tl.kirjaa(self.y, "l", "a", "t", tulos, kulu_usd=kulu, nyt=aika)

    def test_siivoa_oletus_90_paivaa_ja_raja(self):
        nyt = 1_000_000_000.0
        self.kirjaa(nyt - 89 * PV)
        self.kirjaa(nyt - 90 * PV)
        self.kirjaa(nyt - 90 * PV - 5)
        self.kirjaa(nyt - 91 * PV)
        self.assertEqual(tl.siivoa(self.y, nyt=nyt), 2)
        self.assertEqual(self.lkm(), 2)

    def test_siivoa_pitaa_nuoret(self):
        nyt = 1_000_000_000.0
        self.kirjaa(nyt - 1 * PV)
        self.assertEqual(tl.siivoa(self.y, 90, nyt=nyt), 0)
        self.assertEqual(self.lkm(), 1)

    def test_siivoa_kayttaa_annettua_nyt_ja_oletuksena_kelloa(self):
        self.kirjaa(1000.0)
        self.kirjaa(1000.0 + 80 * PV)
        self.assertEqual(tl.siivoa(self.y, 90, nyt=1000.0 + 95 * PV), 1)
        self.assertEqual(self.lkm(), 1)
        import time
        self.kirjaa(time.time() - 100 * PV)
        self.kirjaa(time.time())
        self.assertEqual(tl.siivoa(self.y), 2)
        self.assertEqual(self.lkm(), 1)

    def test_yhteenveto_oletus_7_paivaa_ja_raja(self):
        nyt = 1_000_000_000.0
        self.kirjaa(nyt - 6.9 * PV)
        self.kirjaa(nyt - 7 * PV)
        self.kirjaa(nyt - 7 * PV - 3)
        self.kirjaa(nyt - 7.5 * PV)
        self.assertEqual(sum(tl.yhteenveto(self.y, nyt=nyt)["tapahtumat"].values()), 2)
        self.assertEqual(sum(tl.yhteenveto(self.y, 8, nyt=nyt)["tapahtumat"].values()), 4)

    def test_yhteenveto_kulu_pyoristys(self):
        self.kirjaa(1000.0, kulu=0.1234567)
        self.assertEqual(tl.yhteenveto(self.y, 7, nyt=1000.0)["kulu_usd"], 0.123457)

    def test_hyvaksymisprosentti(self):
        a = tl.ehdota(self.y, "a", "p")
        tl.kirjaa_palaute(self.y, a, "hyvaksy")
        self.assertEqual(tl.yhteenveto(self.y)["hyvaksymis_pros"], 100.0)
        b = tl.ehdota(self.y, "b", "p")
        c = tl.ehdota(self.y, "c", "p")
        tl.kirjaa_palaute(self.y, b, "hylkaa")
        tl.kirjaa_palaute(self.y, c, "hylkaa")
        self.assertEqual(tl.yhteenveto(self.y)["hyvaksymis_pros"], 33.3)

    def test_vain_hylkaykset_nolla_prosenttia(self):
        tl.kirjaa_palaute(self.y, tl.ehdota(self.y, "a", "p"), "hylkaa")
        self.assertEqual(tl.yhteenveto(self.y)["hyvaksymis_pros"], 0.0)


class MainTarkkaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.polku = os.path.join(self.tmp.name, "loki.db")

    def aja(self, *argv):
        import contextlib
        import io
        ulos, virhe = io.StringIO(), io.StringIO()
        with mock_env(self.polku), contextlib.redirect_stdout(ulos), contextlib.redirect_stderr(virhe):
            koodi = tl.main(list(argv))
        return koodi, ulos.getvalue(), virhe.getvalue()

    def kirjaa_aikaan(self, ika_pv):
        import time
        y = tl.avaa(self.polku)
        tl.kirjaa(y, "l", "a", "t", "ok", nyt=time.time() - ika_pv * PV)
        y.close()

    def test_kirjaa_ilman_kulua_ja_kulun_kanssa(self):
        self.assertEqual(self.aja("kirjaa", "l", "a", "t", "ok"), (0, "kirjattu: 1\n", ""))
        self.assertEqual(self.aja("kirjaa", "l", "a", "t", "ok", "0.25"), (0, "kirjattu: 2\n", ""))
        y = tl.avaa(self.polku)
        self.assertEqual([r[0] for r in y.execute("SELECT kulu_usd FROM tapahtuma ORDER BY id")], [None, 0.25])
        y.close()

    def test_kirjaa_kentat_oikeassa_jarjestyksessa(self):
        self.aja("kirjaa", "lahde1", "agentti1", "tyyppi1", "tulos1")
        y = tl.avaa(self.polku)
        self.assertEqual(y.execute("SELECT lahde,agentti,tyyppi,tulos FROM tapahtuma").fetchone(),
                         ("lahde1", "agentti1", "tyyppi1", "tulos1"))
        y.close()

    def test_kirjaa_vaara_maara_argumentteja(self):
        self.assertEqual(self.aja("kirjaa", "l", "a", "t")[0], 2)
        self.assertEqual(self.aja("kirjaa", "l", "a", "t", "ok", "1", "extra")[0], 2)
        self.assertIn("tuntematon komento", self.aja("kirjaa", "l", "a", "t")[2])

    def test_kirjaa_virhe_ilmoitetaan(self):
        koodi, ulos, virhe = self.aja("kirjaa", "", "a", "t", "ok")
        self.assertEqual((koodi, ulos), (2, ""))
        self.assertIn("tapahtumaloki:", virhe)

    def test_yhteenveto_oletus_ja_paivat(self):
        self.kirjaa_aikaan(7.5)
        self.kirjaa_aikaan(3)
        self.assertIn('"t/ok": 1', self.aja("yhteenveto")[1])
        self.assertIn('"t/ok": 2', self.aja("yhteenveto", "8")[1])
        self.assertIn('"tapahtumat": {}', self.aja("yhteenveto", "2")[1])
        self.assertEqual(self.aja("yhteenveto", "1", "2")[0], 2)
        self.assertEqual(self.aja("yhteenveto", "x")[0], 2)

    def test_yhteenveto_json_avaimet_jarjestyksessa(self):
        koodi, ulos, _ = self.aja("yhteenveto")
        self.assertEqual(koodi, 0)
        self.assertTrue(ulos.startswith('{"ehdotukset"'))

    def test_ilman_argumenttia_luetaan_sys_argv(self):
        import contextlib
        import io
        from unittest import mock
        with mock_env(self.polku), mock.patch.object(sys, "argv", ["x", "kirjaa", "l", "a", "t", "ok"]), \
                contextlib.redirect_stdout(io.StringIO()) as ulos:
            self.assertEqual(tl.main(), 0)
        self.assertEqual(ulos.getvalue(), "kirjattu: 1\n")

    def test_tyhja_argumenttilista_on_kayttovirhe(self):
        koodi, ulos, virhe = self.aja()
        self.assertEqual((koodi, ulos), (2, ""))
        self.assertIn("kaytto:", virhe)


if __name__ == "__main__":
    unittest.main()
