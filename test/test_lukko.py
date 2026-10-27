import fcntl
import os
import tempfile
import unittest
from unittest import mock

from agentti import lukko


class LukittuTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_lukko_luo_tiedoston_ja_pitaja_voi_vapauttaa(self):
        polku = os.path.join(self.tmp.name, "lukko")
        with lukko.lukittu(polku):
            self.assertTrue(os.path.exists(polku))
        # lukon vapauduttua sama lukko voidaan hankkia uudelleen
        with lukko.lukittu(polku):
            pass

    def test_lukittu_palauttaa_none(self):
        polku = os.path.join(self.tmp.name, "lukko")
        with lukko.lukittu(polku) as tulos:
            self.assertIsNone(tulos)

    def test_lukittu_sulkee_poikkeuksenkin_jalkeen(self):
        polku = os.path.join(self.tmp.name, "lukko")
        with self.assertRaises(RuntimeError):
            with lukko.lukittu(polku):
                raise RuntimeError("virhe")
        # lukon pitaa olla vapaa: sama lukko onnistuu uudelleen
        with lukko.lukittu(polku):
            pass

    def test_lukko_estaa_toisen_haltijan_kunnes_vapautuu(self):
        polku = os.path.join(self.tmp.name, "lukko")
        with lukko.lukittu(polku):
            toinen = os.open(polku, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(toinen, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(toinen)
        toinen = os.open(polku, os.O_RDWR)
        try:
            fcntl.flock(toinen, fcntl.LOCK_EX | fcntl.LOCK_NB)      # vapautui: onnistuu
        finally:
            os.close(toinen)

    def test_lukko_kunnioittaa_0600_oikeuksia(self):
        polku = os.path.join(self.tmp.name, "lukko")
        with lukko.lukittu(polku):
            pass
        self.assertEqual(os.stat(polku).st_mode & 0o777, 0o600)


class KirjoitaAtomisestiTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_kirjoittaa_sisallon(self):
        polku = os.path.join(self.tmp.name, "tiedosto.txt")
        lukko.kirjoita_atomisesti(polku, "sisalto\n")
        with open(polku, encoding="utf-8") as f:
            self.assertEqual(f.read(), "sisalto\n")

    def test_puuttuva_hakemisto_nostaa_virheen(self):
        # kirjoita_atomisesti ei luo hakemistoa: se vaatii olemassa olevan kohdehakemiston
        polku = os.path.join(self.tmp.name, "ala", "tiedosto.txt")
        with self.assertRaises(FileNotFoundError):
            lukko.kirjoita_atomisesti(polku, "x")

    def test_vanha_sisalto_yli_kirjoitetaan(self):
        polku = os.path.join(self.tmp.name, "tiedosto.txt")
        lukko.kirjoita_atomisesti(polku, "vanha")
        lukko.kirjoita_atomisesti(polku, "uusi")
        with open(polku, encoding="utf-8") as f:
            self.assertEqual(f.read(), "uusi")

    def test_ei_jaa_vaeliaikaisia_tiedostoja(self):
        hakemisto = self.tmp.name
        polku = os.path.join(hakemisto, "tiedosto.txt")
        lukko.kirjoita_atomisesti(polku, "x")
        tmp_tiedostot = [n for n in os.listdir(hakemisto) if n.startswith(".tmp-")]
        self.assertEqual(tmp_tiedostot, [])

    def test_poikkeus_siivoaa_vaeliaikaisen_eika_koske_vanhaan(self):
        hakemisto = self.tmp.name
        polku = os.path.join(hakemisto, "kohde")
        lukko.kirjoita_atomisesti(polku, "vanha")
        # vaihto epaonnistuu kesken: oikea toteutus ajetaan, vain os.replace kaatuu
        with mock.patch("agentti.lukko.os.replace", side_effect=OSError("vaihto epaonnistui")):
            with self.assertRaises(OSError):
                lukko.kirjoita_atomisesti(polku, "uusi")
        self.assertEqual(os.listdir(hakemisto), ["kohde"])          # ei jaa .tmp-tiedostoa
        with open(polku, encoding="utf-8") as f:
            self.assertEqual(f.read(), "vanha")                      # vanha sisalto ehjana

    def test_keskeytys_kesken_kirjoituksen_siivoaa_myos(self):
        hakemisto = self.tmp.name
        polku = os.path.join(hakemisto, "kohde")
        # BaseException (esim. KeyboardInterrupt) siivotaan myos
        with mock.patch("agentti.lukko.os.fsync", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                lukko.kirjoita_atomisesti(polku, "x")
        self.assertEqual(os.listdir(hakemisto), [])

    def test_tiedosto_on_ehja_myos_pitkalla_sisallolla(self):
        polku = os.path.join(self.tmp.name, "iso.txt")
        teksti = "rivi \u00e5\u00e4\u00f6\n" * 5000
        lukko.kirjoita_atomisesti(polku, teksti)
        with open(polku, encoding="utf-8") as f:
            self.assertEqual(f.read(), teksti)


if __name__ == "__main__":
    unittest.main()