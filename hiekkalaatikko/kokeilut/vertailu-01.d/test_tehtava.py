import unittest

from tehtava import kesto_sekunneiksi as k


class Testit(unittest.TestCase):
    def test_perus(self):
        self.assertEqual(k("45s"), 45)
        self.assertEqual(k("1h30m"), 5400)
        self.assertEqual(k("2d"), 172800)
        self.assertEqual(k("1d2h3m4s"), 93784)

    def test_valilyonnit_reunoilla(self):
        self.assertEqual(k("  5m "), 300)

    def test_nolla(self):
        self.assertEqual(k("0s"), 0)

    def test_virheet(self):
        for huono in ["", "   ", "10", "5x", "h", "1m1h", "1h1h", "-5s", "1.5h", "1 h", "s5", "1h 30m"]:
            with self.subTest(huono=huono):
                with self.assertRaises(ValueError):
                    k(huono)


if __name__ == "__main__":
    unittest.main()
