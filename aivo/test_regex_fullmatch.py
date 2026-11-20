"""Tunnus- ja muototarkistukset hylkaavat lopun rivinvaihdon (Pythonin `$` hyvaksyy yhden lopussa).

Toiminnalliset testit syottavat kelvollisen arvon + "\\n" ja odottavat hylkaysta. Vartijatesti varmistaa, ettei
`^...$`-regexia kayteta `.match`-kutsulla (pitaa olla `.fullmatch`). aivo/alue.py:n ALIKANSIO_RE on tiedostopolkurajaus
(Koodi on laki) eika kuulu tahan: se on vartijatestissa tarkoituksella poissa ja sen nykytila on vain dokumentoitu."""
import ast
import re
import tempfile
import unittest
from pathlib import Path

from aivo import alue, chat, openrouter_zdr, tiivistelma, yksityinen, yksityinen_or
from aivo.chat import ChatVirhe, Keskustelut
from aivo.tiivistelma import TiivistelmaVirhe
from aivo.yksityinen import YksityinenVirhe

AIVO = Path(__file__).resolve().parent
KID = "20261114-101500-0a1b2c3d"
POIKKEUS = {("alue", "ALIKANSIO_RE")}      # polkurajaus: ei muuteta ilman Tonyn lupaa


class TunnusRivinvaihto(unittest.TestCase):
    def test_chat_polku(self):
        with tempfile.TemporaryDirectory() as d:
            k = Keskustelut(d)
            k._polku(KID)                     # kelvollinen menee lapi
            with self.assertRaises(ChatVirhe):
                k._polku(KID + "\n")

    def test_ehdotuksen_tunnus(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(YksityinenVirhe):
                yksityinen.lue_ehdotus(d, "0123456789ab\n")

    def test_ehdotuksen_listaus_ohittaa_rivinvaihtonimen(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "0123456789ab.json").write_text("{}")
            (Path(d) / "ba9876543210\n.json").write_text("{}")
            self.assertEqual(yksityinen.listaa_ehdotukset(d), ["0123456789ab"])

    def test_ydinohjeen_nimi(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(YksityinenVirhe):
                yksityinen.tallenna_ehdotus(d, "ohje.md\n", "teksti")

    def test_tiivistelman_tunnus(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TiivistelmaVirhe):
                tiivistelma._polku(d, KID + "-1-2\n")

    def test_tiivistelman_luettelo_ohittaa_rivinvaihtonimen(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / (KID + "-1-2.json")).write_text("{}")
            (Path(d) / (KID + "-3-4\n.json")).write_text("{}")
            self.assertEqual(tiivistelma.luettele(d), [KID + "-1-2"])

    def test_katettu_loppu_hylkaa_kid_rivinvaihdolla(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TiivistelmaVirhe):
                tiivistelma.katettu_loppu(d, KID + "\n")

    def test_mallin_nimi(self):
        self.assertTrue(openrouter_zdr.avoin_malli("meta-llama/llama-3.3-70b-instruct"))
        self.assertFalse(openrouter_zdr.avoin_malli("meta-llama/llama-3.3-70b-instruct\n"))


class Vartija(unittest.TestCase):
    """Kattaa myos kohdat, joihin ei paase helposti kayttajan syotteella (avain on strip()-kasitelty, kansiolistaukset)."""

    def test_ei_dollar_regexia_match_kutsulla(self):
        loydetty = []
        for polku in sorted(AIVO.glob("*.py")):
            if polku.name.startswith("test_"):
                continue
            puu = ast.parse(polku.read_text(encoding="utf-8"))
            dollar = set()
            for n in puu.body:
                if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                        and isinstance(n.value, ast.Call) and getattr(n.value.func, "attr", "") == "compile"
                        and n.value.args and "$" in ast.unparse(n.value.args[0])):
                    dollar.add(n.targets[0].id)
            for n in ast.walk(puu):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "match"
                        and isinstance(n.func.value, ast.Name) and n.func.value.id in dollar
                        and (polku.stem, n.func.value.id) not in POIKKEUS):
                    loydetty.append("%s:%d %s.match" % (polku.name, n.lineno, n.func.value.id))
        self.assertEqual(loydetty, [])

    def test_avain_regex_hylkaa_rivinvaihdon(self):
        self.assertIsNone(yksityinen_or._AVAIN_RE.fullmatch("sk-testi-" + "x" * 30 + "\n"))

    def test_alue_poikkeus_on_olemassa(self):
        # Dokumentoi nykytilan: ALIKANSIO_RE on tarkoituksella koskematon. Jos tama kaatuu, katso raportin ehdotus.
        self.assertIsNotNone(alue.ALIKANSIO_RE.match("abc\n"))


if __name__ == "__main__":
    unittest.main()
