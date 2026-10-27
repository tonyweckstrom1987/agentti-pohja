import datetime
import unittest
import zoneinfo

from agentti import config


class ConfigTesti(unittest.TestCase):
    def test_paiva_on_helsingin_aikaa(self):
        # 22:30 UTC 15.10. on jo 16.10. Helsingissa (EEST, UTC+3)
        utc = datetime.datetime(2026, 10, 15, 22, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(config.paiva(utc), "2026-10-16")
        self.assertEqual(config.kuukausi(utc), "2026-10")

    def test_kuukauden_vaihtuminen(self):
        utc = datetime.datetime(2026, 10, 31, 22, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(config.kuukausi(utc), "2026-11")

    def test_naiivi_aika_hylataan(self):
        with self.assertRaises(ValueError):
            config.paiva(datetime.datetime(2026, 10, 16, 8, 0))

    def test_oletus_on_nyt(self):
        self.assertEqual(config.paiva(), config.paiva(config.nyt()))
        self.assertEqual(config.nyt().tzinfo, zoneinfo.ZoneInfo("Europe/Helsinki"))

    def test_lue_env(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            polku = os.path.join(t, "a.env")
            with open(polku, "w") as f:
                f.write("# kommentti\nA=1\nB = \"kaksi\"\n\nTYHJA\n")
            self.assertEqual(config.lue_env(polku), {"A": "1", "B": "kaksi"})
        with self.assertRaises(config.EnvVirhe):
            config.lue_env("/ei/ole/tata")


class EnvTarkkaTesti(unittest.TestCase):
    def test_arvossa_voi_olla_yhtasuuruusmerkki(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            polku = os.path.join(t, "a.env")
            with open(polku, "w") as f:
                f.write("URL=http://x/?a=b&c=d\nK=a=b=c\n")
            self.assertEqual(config.lue_env(polku), {"URL": "http://x/?a=b&c=d", "K": "a=b=c"})


if __name__ == "__main__":
    unittest.main()
