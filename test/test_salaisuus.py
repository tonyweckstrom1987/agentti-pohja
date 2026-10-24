import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SKRIPTI = Path(__file__).resolve().parent.parent / "tools" / "salaisuus.sh"


def aja(tiedosto, muuttuja, syote, *lisa, env=None):
    return subprocess.run(["sh", str(SKRIPTI), str(tiedosto), muuttuja, *lisa], input=syote, capture_output=True, text=True,
                          env=env or dict(os.environ), timeout=30)


class SalaisuusTesti(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.f = self.t / "x" / "a.env"

    def test_tallentaa_ja_ei_tulosta_arvoa(self):
        r = aja(self.f, "AVAIN", "salainen-arvo-123\n", "", "600")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.f.read_text(), "AVAIN=salainen-arvo-123\n")
        self.assertNotIn("salainen-arvo-123", r.stdout + r.stderr)
        self.assertIn("17 merkkia", r.stderr)
        self.assertEqual(stat.S_IMODE(self.f.stat().st_mode), 0o600)

    def test_korvaa_vanhan_rivin_ja_sailyttaa_muut(self):
        self.f.parent.mkdir()
        self.f.write_text("A=1\nAVAIN=vanha\nB=2\n")
        r = aja(self.f, "AVAIN", "uusi\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.f.read_text(), "A=1\nB=2\nAVAIN=uusi\n")

    def test_oikeudet_640(self):
        self.assertEqual(aja(self.f, "K", "v\n", "", "640").returncode, 0)
        self.assertEqual(stat.S_IMODE(self.f.stat().st_mode), 0o640)

    def test_virheelliset_syotteet(self):
        for muuttuja, syote, oikeudet in (("1X", "a\n", "600"), ("A B", "a\n", "600"), ("", "a\n", "600"), ("A", "\n", "600"),
                                          ("A", "a\n", "999"), ("A", "a\tb\n", "600")):
            with self.subTest(muuttuja=muuttuja, syote=syote, oikeudet=oikeudet):
                r = aja(self.f, muuttuja, syote, "", oikeudet)
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(self.f.exists())

    def test_ymparistosta_arvoa_ei_oteta(self):
        r = aja(self.f, "A", "x\n", env=dict(os.environ, SALAISUUS_ARVO="vuotaa"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.f.exists())

    def test_tulostettavuustarkistus_ei_riipu_localesta(self):
        for loc in ("C", "en_US.UTF-8", "fi_FI.UTF-8"):
            with self.subTest(loc=loc):
                r = aja(self.f, "A", "a\tb\n", env=dict(os.environ, LC_ALL=loc))
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(self.f.exists())

    def test_ascii(self):
        SKRIPTI.read_bytes().decode("ascii")


if __name__ == "__main__":
    unittest.main()
