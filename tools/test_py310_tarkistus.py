import contextlib
import io
import os
import sys
import tempfile
import unittest

from tools import py310_tarkistus as t

TULKKI = t.loyda_tulkki()
VAIN_JOS_TULKKI = unittest.skipUnless(TULKKI, "Python 3.10 -tulkkia ei ole")
# Kenoviiva f-merkkijonon lausekkeessa on kelvollinen vasta 3.12:ssa (PEP 701).
F312 = "x = 1\nprint(f\"{'\\n'.join(['a'])}\")\n"


def tee(sisalto, nimi="a.py"):
    d = tempfile.TemporaryDirectory()
    with open(os.path.join(d.name, nimi), "w") as f:
        f.write(sisalto)
    return d


class Py310Testit(unittest.TestCase):
    @VAIN_JOS_TULKKI
    def test_312_fmerkkijono_hylataan(self):
        with tee(F312) as d:
            koodi, virheet = t.tarkista(d, TULKKI)
        self.assertEqual(koodi, 1)
        self.assertEqual(len(virheet), 1)
        self.assertTrue(virheet[0][0].endswith("a.py"))
        self.assertEqual(virheet[0][1], "2")
        self.assertEqual(virheet[0][2], "SyntaxError")

    @VAIN_JOS_TULKKI
    def test_kelvollinen_hyvaksytaan(self):
        with tee("x = 1\nprint(f'{x}')\n") as d:
            self.assertEqual(t.tarkista(d, TULKKI), (0, []))

    @VAIN_JOS_TULKKI
    def test_cli_paluukoodit(self):
        with tee(F312) as d, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(t.main(["--python", TULKKI, d]), 1)
        with tee("x = 1\n") as d, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(t.main(["--python", TULKKI, d]), 0)

    @VAIN_JOS_TULKKI
    def test_ei_jata_pycache_repoon(self):
        with tee("x = 1\n") as d:
            t.tarkista(d, TULKKI)
            self.assertEqual(os.listdir(d), ["a.py"])

    def test_tulkki_puuttuu_antaa_oman_koodin(self):
        vanha = os.environ.pop("PY310", None)
        alku, t.OLETUS_HAKU = t.OLETUS_HAKU, "/olematon/*/python3.10"
        pysyva, t.PYSYVA_HAUT = t.PYSYVA_HAUT, ["/olematon/pysyva/python3.10"]
        try:
            with tee("x = 1\n") as d, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(t.main([d]), t.EI_TULKKIA)
        finally:
            t.OLETUS_HAKU = alku
            t.PYSYVA_HAUT = pysyva
            if vanha is not None:
                os.environ["PY310"] = vanha

    def test_vaara_versio_ei_anna_ok(self):
        with tee("x = 1\n") as d, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(t.main(["--python", sys.executable, d]), t.EI_TULKKIA if sys.version_info[:2] != (3, 10) else 0)

    def test_epavarma_tulostetaan(self):
        vanha = os.environ.pop("PY310", None)
        alku, t.OLETUS_HAKU = t.OLETUS_HAKU, "/olematon/*/python3.10"
        pysyva, t.PYSYVA_HAUT = t.PYSYVA_HAUT, ["/olematon/pysyva/python3.10"]
        try:
            with tee("x = 1\n") as d, contextlib.redirect_stdout(io.StringIO()) as ulos:
                t.main([d])
            self.assertIn("EPAVARMA: python3.10 puuttuu", ulos.getvalue())
            self.assertNotIn("OK", ulos.getvalue())
        finally:
            t.OLETUS_HAKU = alku
            t.PYSYVA_HAUT = pysyva
            if vanha is not None:
                os.environ["PY310"] = vanha

    def test_hakujarjestys_pysyva_ennen_varapolkua(self):
        with tempfile.TemporaryDirectory() as d:
            pysyva, vara = (os.path.join(d, n, "python3.10") for n in ("pysyva", "vara"))
            for polku in (pysyva, vara):
                os.makedirs(os.path.dirname(polku))
                open(polku, "w").close()
                os.chmod(polku, 0o755)
            vanha_env = os.environ.pop("PY310", None)
            alku = (t.PYSYVA_HAUT, t.OLETUS_HAKU)
            try:
                t.PYSYVA_HAUT, t.OLETUS_HAKU = [pysyva], vara
                self.assertEqual(t.loyda_tulkki(), pysyva)
                os.environ["PY310"] = vara
                self.assertEqual(t.loyda_tulkki(), vara)  # PY310 voittaa pysyvan
                self.assertEqual(t.loyda_tulkki(pysyva), pysyva)  # --python voittaa kaiken
                os.environ.pop("PY310")
                os.remove(pysyva)
                self.assertEqual(t.loyda_tulkki(), vara)  # pysyva puuttuu -> varapolku
            finally:
                t.PYSYVA_HAUT, t.OLETUS_HAKU = alku
                os.environ.pop("PY310", None)
                if vanha_env is not None:
                    os.environ["PY310"] = vanha_env

    def test_jasennys_ei_tulosta_viestia(self):
        teksti = ("*** Error compiling '/x/a.py'...\n  File \"/x/a.py\", line 7\n    kood\n    ^\n"
                  "SyntaxError: salainen viesti\n")
        self.assertEqual(t.jasenna(teksti), [("/x/a.py", "7", "SyntaxError")])


if __name__ == "__main__":
    unittest.main()
