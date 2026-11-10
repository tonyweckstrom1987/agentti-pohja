"""Testit tools/ci_tila.py:lle valmiilla JSON-vastauksilla (ei verkkoa)."""
import unittest
import urllib.error
from unittest import mock

from tools import ci_tila as ct

SHA = "a" * 40


def ajo(nimi, tila="completed", tulos="success"):
    return {"name": nimi, "status": tila, "conclusion": tulos}


def hae_vastauksilla(ajot):
    def hae(url):
        if "/pulls/" in url:
            return {"head": {"sha": SHA}}
        return {"check_runs": ajot}
    return hae


def aja(hae, argv=("o/r", "5")):
    rivit = []
    koodi = ct.main(list(argv), hae=hae, ulos=rivit.append)
    return koodi, "\n".join(rivit)


class CiTilaTesti(unittest.TestCase):
    def test_vihrea(self):
        koodi, ulos = aja(hae_vastauksilla([ajo("testit"), ajo("lint", tulos="skipped")]))
        self.assertEqual(koodi, 0)
        self.assertIn(SHA, ulos)
        self.assertIn("testit: completed / success", ulos)
        self.assertIn("Yhteenveto: vihrea", ulos)

    def test_punainen(self):
        koodi, ulos = aja(hae_vastauksilla([ajo("testit", tulos="failure"), ajo("lint")]))
        self.assertEqual(koodi, 1)
        self.assertIn("Yhteenveto: punainen", ulos)

    def test_kesken(self):
        koodi, ulos = aja(hae_vastauksilla([ajo("testit"), ajo("lint", tila="in_progress", tulos=None)]))
        self.assertEqual(koodi, 2)
        self.assertIn("lint: in_progress / -", ulos)
        self.assertIn("Yhteenveto: kesken", ulos)

    def test_tyhja_lista(self):
        koodi, ulos = aja(hae_vastauksilla([]))
        self.assertEqual(koodi, 2)
        self.assertIn("Yhteenveto: ei ajoja", ulos)

    def _http(self, koodi_http):
        def hae(url):
            with mock.patch("urllib.request.urlopen",
                            side_effect=urllib.error.HTTPError(url, koodi_http, "viesti jota ei saa tulostua", {}, None)):
                return ct.hae(url)
        return hae

    def test_http_403(self):
        koodi, ulos = aja(self._http(403))
        self.assertEqual(koodi, 3)
        self.assertEqual(ulos, "Virhe: HTTPError 403")

    def test_http_404(self):
        koodi, ulos = aja(self._http(404))
        self.assertEqual(koodi, 3)
        self.assertEqual(ulos, "Virhe: HTTPError 404")

    def test_verkkovirhe_vain_tyyppi(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("salainen-osoite")):
            koodi, ulos = aja(ct.hae)
        self.assertEqual(koodi, 3)
        self.assertEqual(ulos, "Virhe: OSError")

    def test_vaarat_argumentit(self):
        for argv in ((), ("o/r",), ("o/r", "x"), ("../r", "1")):
            self.assertEqual(aja(hae_vastauksilla([]), argv)[0], 3)


if __name__ == "__main__":
    unittest.main()
