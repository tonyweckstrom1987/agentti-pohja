"""Regressio: testit eivat saa vuotaa stdoutiin (kehitys.sh nayttaa lokin kolme viimeista rivia raportin Testit-rivilla)."""
import subprocess
import sys
import unittest
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
MODUULIT = ["yojono.test_ajuri", "tools.test_py310_tarkistus"]


class EiStdoutVuotoa(unittest.TestCase):
    def test_moduulit_eivat_tulosta_stdoutiin(self):
        for moduuli in MODUULIT:
            with self.subTest(moduuli=moduuli):
                r = subprocess.run([sys.executable, "-B", "-m", "unittest", moduuli],
                                   cwd=JUURI, capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr[-300:])
                self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
