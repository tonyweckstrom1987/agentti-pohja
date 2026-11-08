import os
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
        with tee(F312) as d:
            self.assertEqual(t.main(["--python", TULKKI, d]), 1)
        with tee("x = 1\n") as d:
            self.assertEqual(t.main(["--python", TULKKI, d]), 0)

    @VAIN_JOS_TULKKI
    def test_ei_jata_pycache_repoon(self):
        with tee("x = 1\n") as d:
            t.tarkista(d, TULKKI)
            self.assertEqual(os.listdir(d), ["a.py"])

    def test_tulkki_puuttuu_antaa_oman_koodin(self):
        vanha = os.environ.pop("PY310", None)
        alku, t.OLETUS_HAKU = t.OLETUS_HAKU, "/olematon/*/python3.10"
        try:
            with tee("x = 1\n") as d:
                self.assertEqual(t.main([d]), t.EI_TULKKIA)
        finally:
            t.OLETUS_HAKU = alku
            if vanha is not None:
                os.environ["PY310"] = vanha

    def test_jasennys_ei_tulosta_viestia(self):
        teksti = ("*** Error compiling '/x/a.py'...\n  File \"/x/a.py\", line 7\n    kood\n    ^\n"
                  "SyntaxError: salainen viesti\n")
        self.assertEqual(t.jasenna(teksti), [("/x/a.py", "7", "SyntaxError")])


if __name__ == "__main__":
    unittest.main()
