import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from agentti import ilmoita


class MuodostaTest(unittest.TestCase):
    def test_muoto(self):
        self.assertEqual(ilmoita.muodosta("valmis", "Deploy", "ok"), "[VALMIS] Deploy\nok")
        self.assertEqual(ilmoita.muodosta("virhe", "X"), "[VIRHE] X")

    def test_virheet(self):
        with self.assertRaises(ValueError):
            ilmoita.muodosta("muu", "x")
        with self.assertRaises(ValueError):
            ilmoita.muodosta("valmis", "  ")

    def test_puhdistus_ja_rajat(self):
        v = ilmoita.muodosta("valmis", "a\x1b[31m\nb", "t\x00\x07x" + "z" * 5000)
        self.assertNotIn("\x1b", v)
        self.assertNotIn("\x00", v)
        self.assertEqual(v.splitlines()[0], "[VALMIS] a[31m b")
        self.assertLessEqual(len(v), 20 + ilmoita.OTSIKKO_MAX + ilmoita.TEKSTI_MAX)


class LahetysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = os.path.join(self.tmp.name, "t.env")
        self.tila = os.path.join(self.tmp.name, "tila.json")
        with open(self.env, "w") as f:
            f.write("# kommentti\nYOJONO_TELEGRAM_TOKEN=tok\nYOJONO_TELEGRAM_CHAT='123'\n")
        self.lahetetyt = []

    def hae(self, token, chat, teksti):
        self.lahetetyt.append((token, chat, teksti))
        return True

    def aja(self, **kw):
        return ilmoita.ilmoita("valmis", "T", "x", env_polku=self.env, tila_polku=self.tila, hae=self.hae, **kw)

    def test_lahettaa_tunnuksilla_tiedostosta(self):
        self.assertEqual(self.aja(), "lahetetty")
        self.assertEqual(self.lahetetyt, [("tok", "123", "[VALMIS] T\nx")])

    def test_ei_tunnuksia(self):
        tulos = ilmoita.ilmoita("valmis", "T", env_polku="/ei/ole", tila_polku=self.tila, hae=self.hae)
        self.assertEqual(tulos, "ei_tunnuksia")
        self.assertEqual(self.lahetetyt, [])

    def test_epaonnistunut_lahetys(self):
        tulos = ilmoita.ilmoita("valmis", "T", env_polku=self.env, tila_polku=self.tila, hae=lambda *a: False)
        self.assertEqual(tulos, "epaonnistui")

    def test_tuntiraja(self):
        for _ in range(ilmoita.RAJA_TUNNISSA):
            self.assertEqual(self.aja(nyt=1000), "lahetetty")
        self.assertEqual(self.aja(nyt=1100), "raja")
        self.assertEqual(self.aja(nyt=1000 + 3601), "lahetetty")  # vanhat kirjaukset vanhenevat

    def test_rikkinainen_tilatiedosto_ei_estaa(self):
        with open(self.tila, "w") as f:
            f.write("ei jsonia")
        self.assertEqual(self.aja(), "lahetetty")
        with open(self.tila) as f:
            self.assertEqual(len(json.load(f)), 1)

    def test_vastaanottaja_ei_tule_viestista(self):
        ilmoita.ilmoita("valmis", "chat_id=999", env_polku=self.env, tila_polku=self.tila, hae=self.hae)
        self.assertEqual(self.lahetetyt[0][1], "123")

    def test_main_kaytto(self):
        self.assertEqual(ilmoita.main([]), 2)
        self.assertEqual(ilmoita.main(["muu", "x"]), 2)


class TunnuksetTarkkaTest(unittest.TestCase):
    def test_rivien_jasennys(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "t.env")
            with open(polku, "w") as f:
                f.write("\n# YOJONO_X=1\nilman_yhtasuuruutta\nA = b \nK=\"q\"\nV=a=b\n   \n  #sisennetty=3\nS='x'\n")
            self.assertEqual(ilmoita.lue_tunnukset(polku), {"A": "b", "K": "q", "V": "a=b", "S": "x"})
        self.assertEqual(ilmoita.lue_tunnukset("/ei/ole/tata"), {})


class RajaTarkkaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tila = os.path.join(self.tmp.name, "tila.json")

    def kirjoita(self, ajat):
        with open(self.tila, "w") as f:
            json.dump(ajat, f)

    def luettu(self):
        with open(self.tila) as f:
            return json.load(f)

    def test_tunnin_raja_on_tarkka(self):
        self.kirjoita([1000 - 3599, 1000 - 3600, 1000 - 3601, 1000 - 10])
        self.assertIs(ilmoita.raja_sallii(self.tila, 1000), True)
        self.assertEqual(self.luettu(), [1000 - 3599, 1000 - 10, 1000])

    def test_roskakirjaukset_pudotetaan(self):
        self.kirjoita(["x", None, [1], 995])
        ilmoita.raja_sallii(self.tila, 1000)
        self.assertEqual(self.luettu(), [995, 1000])

    def test_raja_palauttaa_tasan_false_eika_kirjaa(self):
        self.kirjoita([990] * ilmoita.RAJA_TUNNISSA)
        self.assertIs(ilmoita.raja_sallii(self.tila, 1000), False)
        self.assertEqual(len(self.luettu()), ilmoita.RAJA_TUNNISSA)

    def test_yksi_alle_rajan_sallitaan(self):
        self.kirjoita([990] * (ilmoita.RAJA_TUNNISSA - 1))
        self.assertIs(ilmoita.raja_sallii(self.tila, 1000), True)

    def test_tilaa_ei_voi_kirjoittaa_ei_estaa(self):
        self.assertIs(ilmoita.raja_sallii(os.path.join(self.tmp.name, "ei", "ole", "t.json"), 1000), True)


class TunnusPuutteetTarkkaTest(unittest.TestCase):
    def aja(self, sisalto):
        with tempfile.TemporaryDirectory() as d:
            env = os.path.join(d, "t.env")
            with open(env, "w") as f:
                f.write(sisalto)
            return ilmoita.ilmoita("valmis", "T", env_polku=env, tila_polku=os.path.join(d, "t.json"), hae=lambda *a: True)

    def test_kumpikin_tunnus_vaaditaan(self):
        self.assertEqual(self.aja("YOJONO_TELEGRAM_TOKEN=a\n"), "ei_tunnuksia")
        self.assertEqual(self.aja("YOJONO_TELEGRAM_CHAT=1\n"), "ei_tunnuksia")
        self.assertEqual(self.aja("YOJONO_TELEGRAM_TOKEN=\nYOJONO_TELEGRAM_CHAT=1\n"), "ei_tunnuksia")
        self.assertEqual(self.aja("YOJONO_TELEGRAM_TOKEN=a\nYOJONO_TELEGRAM_CHAT=1\n"), "lahetetty")


class MainTarkkaTest(unittest.TestCase):
    def aja(self, argv, tulos="lahetetty", poikkeus=None):
        ulos, virhe = io.StringIO(), io.StringIO()
        with mock.patch.object(ilmoita, "ilmoita", side_effect=poikkeus, return_value=tulos) as m, \
                contextlib.redirect_stdout(ulos), contextlib.redirect_stderr(virhe):
            rc = ilmoita.main(argv)
        return rc, ulos.getvalue(), virhe.getvalue(), m

    def test_argumenttimaarat(self):
        for argv in ([], ["valmis"], ["valmis", "a", "b", "c"]):
            rc, ulos, virhe, m = self.aja(argv)
            self.assertEqual((rc, ulos), (2, ""), argv)
            self.assertIn("kaytto:", virhe)
            self.assertFalse(m.called)

    def test_kaksi_ja_kolme_argumenttia_valittyvat_oikein(self):
        rc, ulos, _, m = self.aja(["virhe", "Otsikko"])
        self.assertEqual((rc, ulos), (0, "ilmoitus: lahetetty\n"))
        m.assert_called_once_with("virhe", "Otsikko", "")
        rc, _, _, m = self.aja(["paatos", "O", "teksti"])
        self.assertEqual(rc, 0)
        m.assert_called_once_with("paatos", "O", "teksti")

    def test_paluukoodit(self):
        for tulos in ("raja", "ei_tunnuksia", "epaonnistui"):
            rc, ulos, _, _ = self.aja(["valmis", "x"], tulos=tulos)
            self.assertEqual((rc, ulos), (1, "ilmoitus: %s\n" % tulos))

    def test_arvovirhe_on_kayttovirhe(self):
        rc, ulos, virhe, _ = self.aja(["valmis", "x"], poikkeus=ValueError("otsikko puuttuu"))
        self.assertEqual((rc, ulos), (2, ""))
        self.assertEqual(virhe, "ilmoita: otsikko puuttuu\n")

    def test_ilman_argumenttia_luetaan_sys_argv(self):
        with mock.patch.object(sys, "argv", ["x", "valmis", "Sys"]):
            rc, _, _, m = self.aja(None)
        self.assertEqual(rc, 0)
        m.assert_called_once_with("valmis", "Sys", "")


if __name__ == "__main__":
    unittest.main()
