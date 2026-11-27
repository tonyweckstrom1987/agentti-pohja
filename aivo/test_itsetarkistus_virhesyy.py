"""Itsetarkistuksen epaonnistunut mallikutsu: syy journaliin ja merkintaan, yksi uusintayritys tilapaiseen virheeseen.

Kutsupolku on oikea: `yksityinen_or.tarkista_vastaus` -> `itsetarkistus.aja` -> `_kysy_viestit` ->
`openrouter_kulut.kutsu` -> kuljetin. Vain verkkoyhteys (`avaa`) on korvattu. Ei verkkoa, ei oikeita avaimia,
ei mallikutsuja.
"""
import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock

from aivo import itsetarkistus as it, openrouter_kulut as ok, yhdistelma, yksityinen, yksityinen_or as yo

M1 = "qwen/qwen3-235b-a22b-2507"
MAVERICK = "meta-llama/llama-4-maverick"
AVAIN = "sk-testi-" + "x" * 30          # keksitty, ei oikea avain
NYT = datetime(2026, 11, 26, 20, 9, tzinfo=ok.TZ)
FAKTA = "P\u00e4iv\u00e4katto on 20 \u20ac/vrk (A963)."
SALAINEN = "SALAINEN-KEHOTTEEN-OSA"     # virheen vapaa teksti: ei saa paatya journaliin eika merkintaan


class FeikkiVastaus:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self, n=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_virhe(tila, runko):
    data = json.dumps(runko).encode() if runko is not None else b""
    return urllib.error.HTTPError("https://esimerkki.invalid/", tila, "x", {}, io.BytesIO(data))


OK_VASTAUS = {"choices": [{"message": {"content": "[]"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


class Verkko:
    """Valekuljetin: palauttaa tai nostaa jonon kohteet jarjestyksessa (Exception -> nostetaan)."""

    def __init__(self, *tulokset):
        self.tulokset = list(tulokset)
        self.kutsut = 0

    def __call__(self, pyynto, timeout=None):
        self.kutsut += 1
        t = self.tulokset.pop(0) if self.tulokset else OK_VASTAUS
        if isinstance(t, BaseException):
            raise t
        return FeikkiVastaus(t)


class Pohja(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        self.avain = self.d / "avain"
        self.avain.write_text(AVAIN + "\n")
        os.chmod(self.avain, 0o600)
        self.kulut = self.d / "kulut"
        self.kulut.mkdir(mode=0o700)

    def tarkista(self, verkko, kaytetyt=1):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            t = yo.tarkista_vastaus("muisti: katto 10 \u20ac/vrk", [{"rooli": "tony", "teksti": "katto?"}], "ohje",
                                    FAKTA, vastaaja=M1, tarkastaja=MAVERICK, kaytetyt=kaytetyt,
                                    avain_tiedosto=str(self.avain), kulutila=str(self.kulut), avaa=verkko,
                                    nyt=lambda: NYT)
        return t, err.getvalue()

    def virherivit(self, loki):
        return [r for r in loki.splitlines() if r.startswith("yksityinen_or: virhe ")]


class JournaliJaMerkinta(Pohja):
    """Jokainen virheluokka: yksi journalirivi (tehtava, malli, luokka, HTTP-tila, koodi, kesto) ja syy merkinnassa."""

    def tapaus(self, virhe, luokka, http, koodi, merkinnassa):
        # sama virhe kahdesti: tilapainen virhe yritetaan kerran uudelleen, eika uusinta saa peittaa syyta
        t, loki = self.tarkista(Verkko(virhe, virhe))
        self.assertEqual(t.tila, it.EPAONNISTUI)
        self.assertIn(merkinnassa, t.merkinta)
        self.assertIn(luokka, t.merkinta)
        self.assertNotIn("mallikutsu ep\u00e4onnistui", t.merkinta)
        rivit = self.virherivit(loki)
        self.assertEqual(len(rivit), t.kutsuja, loki)           # yksi rivi per epaonnistunut kutsu
        self.assertRegex(rivit[0], r"^yksityinen_or: virhe tehtava aivo-yksityinen-tarkistus, malli "
                                   r"meta-llama/llama-4-maverick, luokka %s, http %s, koodi %s, kesto \d+\.\d s$"
                         % (luokka, http, koodi))
        self.assertNotIn(SALAINEN, loki)
        self.assertNotIn(SALAINEN, t.merkinta)
        return t

    def test_aikakatkaisu(self):
        t = self.tapaus(socket.timeout("timed out " + SALAINEN), "Aikakatkaisu", "-", "-", "aikaraja")
        self.assertEqual(t.kutsuja, 1)                           # aikakatkaisua ei yriteta uudelleen

    def test_http_429_koodilla(self):
        runko = {"error": {"code": 429, "message": SALAINEN, "metadata": {"raw": SALAINEN}}}
        t = self.tapaus(http_virhe(429, runko), "HttpVirhe", "429", "429", "palvelu ei vastannut (HTTP 429)")
        self.assertEqual(t.kutsuja, 2)                           # tilapainen: yksi uusinta

    def test_http_404_ei_uusintaa(self):
        runko = {"error": {"code": 404, "message": "No endpoints found matching your data policy " + SALAINEN}}
        t = self.tapaus(http_virhe(404, runko), "HttpVirhe", "404", "404", "palvelu hylk\u00e4si pyynn\u00f6n (HTTP 404)")
        self.assertEqual(t.kutsuja, 1)

    def test_http_virhe_ilman_runkoa(self):
        self.tapaus(http_virhe(502, None), "HttpVirhe", "502", "-", "HTTP 502")

    def test_tyhja_vastaus(self):
        tyhja = {"choices": [{"message": {"content": "  "}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 0}}
        t = self.tapaus(tyhja, "TyhjaVastaus", "-", "-", "vastaus oli tyhj\u00e4")
        self.assertEqual(t.kutsuja, 1)

    def test_virhe_vastauksen_rungossa_http_200(self):
        runko = {"error": {"code": 502, "message": SALAINEN}}
        t = self.tapaus(runko, "PalveluVirhe", "-", "502", "palvelu palautti virheen (koodi 502)")
        self.assertEqual(t.kutsuja, 2)

    def test_katkennut_yhteys(self):
        self.tapaus(ConnectionResetError(SALAINEN), "YhteysVirhe", "-", "-", "yhteys mallipalveluun katkesi")

    def test_kelvoton_koodi_ei_paady_journaliin(self):
        runko = {"error": {"code": "x" * 30 + " " + SALAINEN, "type": "rate_limit"}}
        _, loki = self.tarkista(Verkko(http_virhe(503, runko), http_virhe(503, runko)))
        self.assertIn("koodi rate_limit,", self.virherivit(loki)[0])


class Uusintayritys(Pohja):
    def test_tilapainen_virhe_ja_onnistunut_uusinta(self):
        v = Verkko(http_virhe(429, {"error": {"code": 429}}), OK_VASTAUS)
        t, loki = self.tarkista(v)
        self.assertEqual((t.tila, t.kutsuja, v.kutsut), (it.OK, 2, 2))
        self.assertEqual(len(self.virherivit(loki)), 1)

    def test_uusinta_vain_jos_kutsuraja_sallii(self):
        v = Verkko(http_virhe(429, {"error": {"code": 429}}), OK_VASTAUS)
        t, _ = self.tarkista(v, kaytetyt=yhdistelma.MAX_KUTSUT_VIESTI - 1)
        self.assertEqual((t.tila, t.kutsuja, v.kutsut), (it.EPAONNISTUI, 1, 1))

    def test_uusinta_vie_korjauksen_kutsun(self):
        kohta = json.dumps([{"kohta": "20 \u20ac/vrk", "syy": "10 \u20ac/vrk"}], ensure_ascii=False)
        v = Verkko(http_virhe(500, None), {"choices": [{"message": {"content": kohta}}]})
        t, _ = self.tarkista(v, kaytetyt=yhdistelma.MAX_KUTSUT_VIESTI - 2)
        self.assertEqual((t.tila, t.kutsuja), (it.EPAVARMA, 2))
        self.assertIn("korjausta ei tehty (kutsuraja)", t.merkinta)


class SyyIlmanKuljetinta(unittest.TestCase):
    """itsetarkistus.aja mock-kysylla: syy tulee virheen `syy`-attribuutista, muuten luokan nimi."""

    def aja(self, virhe):
        def kysy(malli, viestit, max_tokens=None, aikaraja_s=None):
            raise virhe
        return it.aja(FAKTA, [{"role": "user", "content": "katto?"}], kysy, tarkastaja=MAVERICK, vastaaja=M1,
                      nyt=lambda: NYT)

    def test_tuntematon_yksityinen_virhe_nayttaa_luokan(self):
        t = self.aja(yksityinen.YksityinenVirhe(SALAINEN))
        self.assertEqual(t.merkinta, "tarkistus: ei onnistunut (mallikutsu ep\u00e4onnistui; YksityinenVirhe); "
                                     "vastaus n\u00e4ytet\u00e4\u00e4n tarkistamattomana")

    def test_aliluokat(self):
        for virhe, odotettu in ((yo.Aikakatkaisu("x"), "aikaraja: mallipalvelu ei vastannut ajoissa; Aikakatkaisu"),
                                (yo.HttpVirhe(429), "palvelu ei vastannut (HTTP 429); HttpVirhe"),
                                (yo.TyhjaVastaus("x"), "vastaus oli tyhj\u00e4; TyhjaVastaus"),
                                (yo.VastausKatkesi("x"), "vastaus katkesi pituusrajaan; VastausKatkesi")):
            with self.subTest(virhe=type(virhe).__name__):
                self.assertIn("(%s)" % odotettu, self.aja(virhe).merkinta)

    def test_viestit_ennallaan(self):
        # kutsujat, jotka nayttavat str(e):n (chat, mallivertailu), eivat muutu
        self.assertEqual(str(yo.HttpVirhe(401)), "mallipalvelu hylkasi pyynnon (HTTP 401)")
        self.assertIsInstance(yo.HttpVirhe(401), yksityinen.YksityinenVirhe)
        self.assertNotIsInstance(yo.HttpVirhe(429), yo.Aikakatkaisu)


if __name__ == "__main__":
    unittest.main()
