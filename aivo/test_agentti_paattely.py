"""Paattelyn ylaraja eristettyjen agenttien mallikutsuissa (sama raja kuin chatissa, A1127).

Korvikekutsuja (mock openrouter_kulut.kutsu) tai korvikekuljetin: ei verkkoa, ei oikeita avaimia."""
import io
import json
import os
import unittest
from unittest import mock

from aivo import agentti_ajuri as aj
from aivo import openrouter_kulut as ok, yksityinen_or as yo
from aivo.agentti import malli as am
from aivo.test_agentti_ajuri import Kansiot
from aivo.test_agentti_malli import Pohja, MALLI, NYT

AJATTELEVA = "qwen/qwen3-235b-a22b-thinking-2507"     # AVOIMET_MALLIT, PAATTELY_TUKI ja OLETUKSENA_PAATTELEVAT
KAIKKI_ROOLIT = ("suunnittelija", "tekija", "tarkastaja", "vartija")
# Odotetut rajat oletuksella 2 000: min(2 000, max_tokens - min(2 000, max_tokens // 2)), kuten chatissa.
ODOTUS_OLETUS = {"suunnittelija": 750, "tekija": 2000, "tarkastaja": 500, "vartija": 400}
VASTAUS = {"choices": [{"message": {"content": "ok"}}], "usage": {"cost": 0.001}}


def _kutsuja(paattely_max=yo.PAATTELY_MAX_OLETUS, malli=AJATTELEVA):
    roolit = {r: malli for r in am.ROOLIT}
    return am._Kutsuja(roolit, "sk-testi-" + "z" * 30, None, None, lambda: NYT, None, lambda: 0.0, 10_000.0,
                       paattely_max=paattely_max)


def _reasoning_roolittain(kutsuja):
    """Kutsuu jokaista roolia kerran korvikekutsujalla; palauttaa {rooli: reasoning-kentta tai None}."""
    with mock.patch.object(ok, "kutsu", return_value=VASTAUS) as m:
        for rooli in KAIKKI_ROOLIT:
            kutsuja.kysy(rooli, [{"role": "user", "content": "x"}])
    tulos = {}
    for rooli, c in zip(KAIKKI_ROOLIT, m.call_args_list):
        assert c.kwargs["tehtava"] == am.TEHTAVA + ":" + rooli
        assert c.kwargs["max_tokens"] == am.MAX_TOKENIA[rooli]
        tulos[rooli] = c.kwargs.get("reasoning")
    return tulos


class RooliTesti(unittest.TestCase):
    def test_jokainen_rooli_saa_oletusrajan(self):
        r = _reasoning_roolittain(_kutsuja())
        for rooli in KAIKKI_ROOLIT:
            with self.subTest(rooli):
                self.assertEqual(r[rooli], {"max_tokens": ODOTUS_OLETUS[rooli], "exclude": True})
                # sama laskenta kuin chatissa (yksityinen_or.paattelyraja)
                self.assertEqual(r[rooli]["max_tokens"],
                                 yo.paattelyraja(AJATTELEVA, am.MAX_TOKENIA[rooli], yo.PAATTELY_MAX_OLETUS))
                self.assertLess(r[rooli]["max_tokens"], am.MAX_TOKENIA[rooli])

    def test_pienempi_asetus_rajaa_kaikki_roolit(self):
        r = _reasoning_roolittain(_kutsuja(paattely_max=500))
        self.assertEqual({k: v["max_tokens"] for k, v in r.items()},
                         {"suunnittelija": 500, "tekija": 500, "tarkastaja": 500, "vartija": 400})

    def test_nolla_ei_laheta_reasoning_kenttaa(self):
        self.assertEqual(set(_reasoning_roolittain(_kutsuja(paattely_max=0)).values()), {None})

    def test_ei_paatteleva_malli_ei_saa_reasoning_kenttaa(self):
        self.assertEqual(set(_reasoning_roolittain(_kutsuja(malli=MALLI)).values()), {None})

    def test_oletus_on_chatin_oletus(self):
        self.assertEqual(_kutsuja().paattely_max, yo.PAATTELY_MAX_OLETUS)
        self.assertEqual(yo.PAATTELY_MAX_OLETUS, 2000)


class TarkistusTesti(unittest.TestCase):
    def test_sallitut_arvot(self):
        for arvo, odotus in ((None, 2000), ("", 2000), ("  ", 2000), ("0", 0), ("1500", 1500), ("6000", 6000)):
            with self.subTest(arvo=arvo):
                self.assertEqual(am.tarkista_paattely_max(arvo), odotus)

    def test_virheellinen_arvo_hylataan_chatin_tarkistuksella(self):
        for arvo in ("abc", "-1", "6001", "1.5", True):
            with self.subTest(arvo=arvo):
                with self.assertRaises(yo.YksityinenVirhe):
                    yo.tarkista_paattely_max(arvo)
                with self.assertRaises(am.AgenttiVirhe):
                    am.tarkista_paattely_max(arvo)

    def test_tarkistus_kulkee_yksityinen_or_funktion_kautta(self):
        with mock.patch.object(yo, "tarkista_paattely_max", wraps=yo.tarkista_paattely_max) as m:
            am.tarkista_paattely_max("1234")
        m.assert_called_once_with("1234")


class AjoTesti(Pohja):
    """Koko agenttiajo korvikekuljettimella: reasoning paatyy OpenRouterin runkoon."""

    def test_runko_sisaltaa_rajan_jokaiselle_roolille(self):
        t = self.aja(roolit=AJATTELEVA, paattely_max="1000")
        self.assertEqual(t.tila, am.VALMIS)
        self.assertEqual(self.roolit(), ["suunnittelija", "tekija", "tekija", "tarkastaja"])
        for rooli, runko in self.rungot:
            with self.subTest(rooli):
                self.assertEqual(runko["reasoning"], {"max_tokens": min(1000, ODOTUS_OLETUS[rooli]),
                                                      "exclude": True})
                self.assertIs(runko["provider"]["zdr"], True)

    def test_virheellinen_arvo_estaa_ennen_kutsuja(self):
        t = self.aja(roolit=AJATTELEVA, paattely_max="9999")
        self.assertEqual((t.tila, t.kutsuja), (am.VIRHE, 0))
        self.assertIn("0-6000", t.syy)
        self.assertEqual(self.rungot, [])
        self.assertEqual(self.kulurivit(), [])

    def test_lapsi_lukee_ymparistomuuttujan(self):
        syote = io.StringIO(json.dumps({"teksti": "Kirjoita tiedosto tulos.md."}))
        with mock.patch.dict(os.environ, {aj.PAATTELY_MUUTTUJA: "300"}):
            d = aj.lapsi(str(self.tyo), AJATTELEVA, syote=syote, avain_tiedosto=str(self.avain),
                         kulutila=str(self.kulut), avaa=self.avaa, nyt=lambda: NYT, kello=lambda: self.aika[0])
        self.assertEqual(d["tila"], am.VALMIS)
        self.assertEqual({r["reasoning"]["max_tokens"] for _, r in self.rungot}, {300})

    def test_lapsi_virheellinen_ymparisto_on_virhe(self):
        syote = io.StringIO(json.dumps({"teksti": "t"}))
        with mock.patch.dict(os.environ, {aj.PAATTELY_MUUTTUJA: "paljon"}):
            d = aj.lapsi(str(self.tyo), AJATTELEVA, syote=syote, avain_tiedosto=str(self.avain),
                         kulutila=str(self.kulut), avaa=self.avaa, nyt=lambda: NYT, kello=lambda: self.aika[0])
        self.assertEqual((d["tila"], d["kutsuja"]), (am.VIRHE, 0))
        self.assertEqual(self.rungot, [])


class MainTesti(Kansiot):
    """Paaohjelman (main) kautta: palvelu ja tarkista hylkaavat kelvottoman arvon kaynnistyksessa."""

    def test_palvelu_ei_kaynnisty_virheellisella_arvolla(self):
        with mock.patch.dict(os.environ, {aj.PAATTELY_MUUTTUJA: "7000"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as ulos, \
                mock.patch.object(aj, "aja_seuraava") as ajo:
            rc = aj.main(["palvelu", "--perus", self.perus, "--roolit", MALLI])
        self.assertEqual(rc, 2)
        self.assertIn("asetusvirhe", ulos.getvalue())
        ajo.assert_not_called()

    def test_tarkista_raportoi_virheellisen_arvon(self):
        with mock.patch.dict(os.environ, {aj.PAATTELY_MUUTTUJA: "x"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as ulos:
            rc = aj.main(["tarkista", "--perus", self.perus])
        self.assertEqual(rc, 1)
        self.assertIn(aj.PAATTELY_MUUTTUJA, ulos.getvalue())

    def test_ymparistosta_oletus_ja_arvo(self):
        self.assertEqual(aj.paattely_max_ymparistosta({}), 2000)
        self.assertEqual(aj.paattely_max_ymparistosta({aj.PAATTELY_MUUTTUJA: "800"}), 800)


if __name__ == "__main__":
    unittest.main()
