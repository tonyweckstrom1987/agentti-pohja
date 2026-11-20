"""Yksityisen tilan kehote: ei lupauksia taustatyosta, keskustelun aiemmat viestit kaytossa, taustapaakohtainen historia.

Testit kulkevat oikean kutsupolun lapi (kysy_paikalliselta ja kysy_openrouterilta) ja lukevat lahtevan rungon.
Ei verkkoa eika oikeita avaimia."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from aivo import openrouter_kulut as ok, yksityinen, yksityinen_or as yo

MALLI = "meta-llama/llama-3.3-70b-instruct"
AVAIN = "sk-testi-" + "x" * 30          # keksitty, ei oikea avain
NYT = datetime(2026, 11, 19, 12, 0, tzinfo=ok.TZ)
EI_TYOKALUJA = "T\u00e4ss\u00e4 tilassa sinulla ei ole ty\u00f6kaluja, verkkoa eik\u00e4 taustaprosesseja."
EI_LUPAUKSIA = "\u00c4l\u00e4 koskaan sano aloittavasi, suorittavasi tai jatkavasi teht\u00e4v\u00e4\u00e4 my\u00f6hemmin, \u00e4l\u00e4k\u00e4 arvioi kestoa."


def historia(n):
    return [{"rooli": "tony" if i % 2 == 0 else "aivo", "teksti": "viesti %d" % i} for i in range(n)]


class Vastaus:
    def __init__(self, data):
        self._d = json.dumps(data).encode()

    def read(self, n=-1):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Taustapaat(unittest.TestCase):
    """Kumpikin taustapaa: palauttaa lahteneen viestilistan."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = Path(t.name)
        self.avain = self.d / "avain"
        self.avain.write_text(AVAIN + "\n")
        os.chmod(self.avain, 0o600)
        self.kulut = self.d / "kulut"
        self.kulut.mkdir(mode=0o700)

    def _avaa(self, talteen):
        def avaa(pyynto, timeout=None):
            talteen.append(json.loads(pyynto.data))
            return Vastaus({"choices": [{"message": {"content": "ok"}}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0}})
        return avaa

    def paikallinen(self, h):
        runko = []
        yksityinen.kysy_paikalliselta("MUISTI", h, "OHJE", avaa=self._avaa(runko))
        return runko[0]["messages"]

    def openrouter(self, h):
        runko = []
        yo.kysy_openrouterilta("MUISTI", h, "OHJE", malli=MALLI, avain_tiedosto=str(self.avain),
                               kulutila=str(self.kulut), avaa=self._avaa(runko), nyt=lambda: NYT)
        return runko[0]["messages"]


class EiTyokalujaSaantoTesti(Taustapaat):
    def test_paikallinen_kehote(self):
        k = self.paikallinen(historia(1))[0]["content"]
        self.assertIn(EI_TYOKALUJA, k)
        self.assertIn(EI_LUPAUKSIA, k)
        self.assertIn("tee se heti t\u00e4ss\u00e4 vastauksessa", k)
        self.assertIn("ettei se onnistu t\u00e4ss\u00e4 tilassa", k)

    def test_openrouter_kehote(self):
        k = self.openrouter(historia(1))[0]["content"]
        self.assertIn(EI_TYOKALUJA, k)
        self.assertIn(EI_LUPAUKSIA, k)
        self.assertIn("ZDR", k)                                  # OpenRouterin oma lause on yha mukana

    def test_tyokalutilan_kehote_ennallaan(self):
        from aivo import chat
        self.assertNotIn(EI_TYOKALUJA, chat.KEHOTE)


class Saanto2Testi(Taustapaat):
    def test_saanto2_mainitsee_keskustelun_aiemmat_viestit(self):
        for k in (self.paikallinen(historia(1))[0]["content"], self.openrouter(historia(1))[0]["content"]):
            saanto2 = k.split("\n2. ", 1)[1].split("\n3. ", 1)[0]
            self.assertIn("muistissani ei ole t\u00e4st\u00e4 tietoa", saanto2)
            self.assertIn("eik\u00e4 t\u00e4m\u00e4n\n   keskustelun aiemmista viesteist\u00e4", saanto2)
            self.assertIn("keskustelun aiemmat viestit ovat k\u00e4ytett\u00e4viss\u00e4si", saanto2)
            self.assertNotIn('Jos muisti ei kerro asiasta, sano "muistissani', saanto2)   # vanha ehdoton muoto


class HistoriarajaTesti(Taustapaat):
    def test_openrouter_vie_40_viestia(self):
        v = self.openrouter(historia(60))
        self.assertEqual(len(v), 1 + 40)
        self.assertEqual(v[1]["content"], "viesti 20")
        self.assertEqual(v[-1]["content"], "viesti 59")

    def test_paikallinen_vie_12_viestia(self):
        v = self.paikallinen(historia(60))
        self.assertEqual(len(v), 1 + 12)
        self.assertEqual(v[1]["content"], "viesti 48")

    def test_vakiot(self):
        self.assertEqual(yksityinen.HISTORIA_YLARAJA, 12)
        self.assertEqual(yo.HISTORIA_YLARAJA, 40)

    def test_ensimmainen_viesti_mukana_openrouterissa_20_vaihdon_jalkeen(self):
        """Tehtava annettiin keskustelun alussa: se on yha mallin nakyvissa 20 vaihdon (40 viestin) jalkeen."""
        h = [{"rooli": "tony", "teksti": "TEHTAVA: tee suunnitelma"}] + historia(40)[1:]
        self.assertIn("TEHTAVA: tee suunnitelma", [x["content"] for x in self.openrouter(h)])
        self.assertNotIn("TEHTAVA: tee suunnitelma", [x["content"] for x in self.paikallinen(h)])


if __name__ == "__main__":
    unittest.main()
