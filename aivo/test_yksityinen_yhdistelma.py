"""Testit usean mallin yhdistelmille (aivo/yhdistelma.py, AIVO_YKSITYINEN_YHDISTELMA). Ei verkkoa, ei oikeita avaimia.

Kaksi tasoa:
  * Yksikkotasolla `yhdistelma.aja` saa korvikemallin (`kysy`), joka kirjaa kutsujarjestyksen.
  * Kutsupolkutasolla kulkee oikea `yksityinen_or._kysy_viestit` -> `openrouter_kulut.kutsu` -> ZDR-runko ->
    kuljetin. Vain verkkoyhteys (`avaa`) on korvattu. Naissa testeissa nahdaan jokaisen kutsun runko ja kulukirjaus.
  * HTTP-tasolla kulkee oikea Tila ja kasittelija samalla korvatulla verkkoyhteydella."""
import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from aivo import chat, openrouter_kulut as ok, yhdistelma as yh, yksityinen, yksityinen_or as yo

M1 = "qwen/qwen3-235b-a22b-2507"
M2 = "meta-llama/llama-3.3-70b-instruct"
M3 = "mistralai/mistral-small-3.1-24b-instruct"
AVAIN = "sk-testi-" + "x" * 30          # keksitty, ei oikea avain
SALASANA = "oikea-salasana-123"
NYT = datetime(2026, 11, 19, 12, 0, tzinfo=ok.TZ)
PERUS = [{"role": "system", "content": "KEHOTE"}, {"role": "user", "content": "Mit\u00e4 teen?"}]
ROOLIT = {"vastaaja": M1, "tarkastaja": M2, "vastaaja2": M2, "kokoaja": M3, "reititin": M3, "kevyt": M2,
          "vaativa": M1}

EHDOTUS = "[[EHDOTUS tyyli.md]]\nKirjoita lyhyesti.\n[[/EHDOTUS]]"
LUOVUTUS = "[[LUOVUTUS]]Suunnitelma jonolle.[[/LUOVUTUS]]"
VALINNAT = "[[VALINNAT: kyll\u00e4 | ei | my\u00f6hemmin]]"
LUONNOS = "Luonnos: tee A.\n" + EHDOTUS + "\n" + LUOVUTUS + "\n" + VALINNAT


class Korvike:
    """Korvikemalli: vastaus valitaan mallin ja roolin mukaan; virheet annetaan kutsunumeron mukaan."""

    def __init__(self, vastaukset=None, virheet=None):
        self.kutsut = []
        self.vastaukset = vastaukset or {}
        self.virheet = virheet or {}
        self._lukko = threading.Lock()

    def __call__(self, malli, viestit, aikaraja_s=None, max_tokens=None):
        with self._lukko:
            n = len(self.kutsut)
            self.kutsut.append((malli, viestit, aikaraja_s, max_tokens))
        rooli = _rooli(viestit)
        if (n, rooli) in self.virheet or rooli in self.virheet or (malli, rooli) in self.virheet:
            raise self.virheet.get((n, rooli)) or self.virheet.get(rooli) or self.virheet[(malli, rooli)]
        v = self.vastaukset.get((malli, rooli), self.vastaukset.get(rooli))
        return v if v is not None else "%s %s" % (rooli, malli)

    def mallit(self):
        return [k[0] for k in self.kutsut]


def _rooli(viestit):
    s = viestit[0].get("content", "")
    if "tarkastaja." in s:
        return "tarkastaja"
    if "kokoaja." in s:
        return "kokoaja"
    if s == yh.REITITIN_OHJE:
        return "reititin"
    return "vastaaja"


class Jarjestys(unittest.TestCase):
    """Jokainen yhdistelma kutsuu oikeat mallit oikeassa jarjestyksessa."""

    def test_yksi(self):
        k = Korvike()
        t = yh.aja("yksi", ROOLIT, M2, PERUS, k)
        self.assertEqual(k.mallit(), [M2])
        self.assertEqual((t.teksti, t.malli, t.kutsuja, t.mallit, t.merkinta), ("vastaaja " + M2, M2, 1, [M2], None))

    def test_tarkastus_vastaaja_sitten_tarkastaja(self):
        k = Korvike()
        t = yh.aja("tarkastus", ROOLIT, M3, PERUS, k)
        self.assertEqual(k.mallit(), [M1, M2])
        self.assertEqual([_rooli(v) for _, v, _, _ in k.kutsut], ["vastaaja", "tarkastaja"])
        self.assertIn("LUONNOS:\nvastaaja " + M1, k.kutsut[1][1][-1]["content"])
        self.assertTrue(k.kutsut[1][1][-1]["content"].startswith("Mit\u00e4 teen?"))   # Tonyn viesti sailyy alussa
        self.assertEqual((t.teksti, t.malli, t.luonnos, t.kutsuja), ("tarkastaja " + M2, M2, "vastaaja " + M1, 2))

    def test_rinnakkais_kaksi_vastaajaa_sitten_kokoaja(self):
        k = Korvike()
        t = yh.aja("rinnakkais", ROOLIT, M3, PERUS, k)
        self.assertEqual(sorted(k.mallit()[:2]), sorted([M1, M2]))
        self.assertEqual(k.mallit()[2], M3)
        syote = k.kutsut[2][1][-1]["content"]
        self.assertIn("VASTAUS A:\nvastaaja " + M1, syote)
        self.assertIn("VASTAUS B:\nvastaaja " + M2, syote)
        self.assertEqual((t.teksti, t.malli, t.kutsuja), ("kokoaja " + M3, M3, 3))
        self.assertIn("VASTAUS A (%s)" % M1, t.luonnos)

    def test_reititys_vaativa_ja_kevyt(self):
        for luokka, odotettu in (("vaativa", M1), ("Kevyt.", M2)):
            with self.subTest(luokka=luokka):
                k = Korvike({"reititin": luokka})
                t = yh.aja("reititys", ROOLIT, M3, PERUS, k)
                self.assertEqual(k.mallit(), [M3, odotettu])
                self.assertEqual(k.kutsut[0][1], [{"role": "system", "content": yh.REITITIN_OHJE},
                                                  {"role": "user", "content": "Mit\u00e4 teen?"}])
                self.assertEqual(k.kutsut[0][3], yh.REITITIN_MAX_TOKENIA)
                self.assertEqual((t.malli, t.kutsuja, t.merkinta), (odotettu, 2, None))

    def test_puuttuva_rooli_kayttaa_keskustelun_mallia(self):
        k = Korvike()
        yh.aja("tarkastus", {}, M3, PERUS, k)
        self.assertEqual(k.mallit(), [M3, M3])

    def test_tuntematon_yhdistelma_hylataan(self):
        with self.assertRaises(yksityinen.YksityinenVirhe):
            yh.aja("kolme-rinnakkais", ROOLIT, M1, PERUS, Korvike())


class Roolit(unittest.TestCase):
    def test_jasennys(self):
        self.assertEqual(yh.jasenna_roolit("vastaaja:%s, tarkastaja:%s" % (M1, M2), (M1, M2)),
                         {"vastaaja": M1, "tarkastaja": M2})
        self.assertEqual(yh.jasenna_roolit(None, (M1,)), {})
        for arvo in ("vastaaja:" + M3, "tuntematon:" + M1, "vastaaja", "vastaaja:%s,vastaaja:%s" % (M1, M2),
                     "vastaaja:openai/gpt-4o"):
            with self.subTest(arvo=arvo), self.assertRaises(yksityinen.YksityinenVirhe):
                yh.jasenna_roolit(arvo, (M1, M2))

    def test_enintaan_kolme_mallia_jokaisessa_yhdistelmassa(self):
        self.assertEqual(yh.MAX_MALLEJA, 3)
        for nimi, roolit in yh.ROOLIT.items():
            self.assertLessEqual(len(roolit), 3, nimi)

    def test_tila_ei_kaynnisty_virheellisella_asetuksella(self):
        with tempfile.TemporaryDirectory() as d:
            for kw in ({"taustapaa": "openrouter", "or_mallit": M1, "roolit": "vastaaja:" + M2},
                       {"taustapaa": "openrouter", "or_mallit": M1, "yhdistelma_oletus": "kaikki"},
                       {"taustapaa": "loopback", "yhdistelma_oletus": "tarkastus"},
                       {"taustapaa": "loopback", "roolit": "vastaaja:" + M1}):
                with self.subTest(kw=kw), self.assertRaises(chat.ChatVirhe):
                    chat.Tila(chat.tiivista_salasana(SALASANA), d, d, ["localhost"], yks_data=d + "/y", **kw)
            t = chat.Tila(chat.tiivista_salasana(SALASANA), d, d, ["localhost"], yks_data=d + "/y",
                          taustapaa="openrouter", or_mallit=[M1, M2], yhdistelma_oletus="tarkastus",
                          roolit="vastaaja:%s,tarkastaja:%s" % (M1, M2))
            self.assertEqual((t.yhdistelma_oletus, t.roolit), ("tarkastus", {"vastaaja": M1, "tarkastaja": M2}))

    def test_main_lukee_ymparistomuuttujat(self):
        saatu = {}

        def tila(*a, **kw):
            saatu.update(kw)
            raise chat.ChatVirhe("pysaytetty testissa")
        pois = {k: v for k, v in os.environ.items() if not k.startswith("AIVO_")}
        ymp = {"AIVO_YKSITYINEN_YHDISTELMA": "reititys", "AIVO_YKSITYINEN_ROOLIT": "reititin:" + M1}
        with mock.patch.dict(os.environ, {**pois, **ymp}, clear=True), \
                mock.patch.object(chat, "lue_tiiviste", return_value="x"), mock.patch.object(chat, "Tila", tila), \
                mock.patch("sys.stderr"):
            self.assertEqual(chat.main(["palvele"]), 1)
        self.assertEqual((saatu["yhdistelma_oletus"], saatu["roolit"]), ("reititys", "reititin:" + M1))


class Kutsuraja(unittest.TestCase):
    def test_vakio_on_nelja(self):
        self.assertEqual(yh.MAX_KUTSUT_VIESTI, 4)

    def test_viides_kutsu_estetaan_ennen_mallia(self):
        k = Korvike()
        ajo = yh._Ajo(k, lambda: 0.0, 100)
        for _ in range(4):
            ajo.kutsu(M1, PERUS)
        with self.assertRaises(yh.KutsurajaTaynna):
            ajo.kutsu(M1, PERUS)
        self.assertEqual(len(k.kutsut), 4)

    def test_mikaan_yhdistelma_ei_ylita_rajaa(self):
        for nimi in yh.YHDISTELMAT:
            for virheet in ({}, {"tarkastaja": yksityinen.YksityinenVirhe("x")},
                            {"kokoaja": yksityinen.YksityinenVirhe("x")}, {"reititin": yksityinen.YksityinenVirhe("x")}):
                with self.subTest(nimi=nimi, virheet=virheet):
                    k = Korvike(virheet=virheet)
                    t = yh.aja(nimi, ROOLIT, M1, PERUS, k)
                    self.assertLessEqual(len(k.kutsut), yh.MAX_KUTSUT_VIESTI)
                    self.assertEqual(t.kutsuja, len(k.kutsut))

    def test_aikaraja_lopettaa_uudet_kutsut(self):
        aika = [0.0]

        def hidas(malli, viestit, aikaraja_s=None, max_tokens=None):
            k.kutsut.append((malli, viestit, aikaraja_s, max_tokens))
            aika[0] += 300
            return "vastaus"
        k = Korvike()
        t = yh.aja("tarkastus", ROOLIT, M1, PERUS, hidas, kello=lambda: aika[0])
        self.assertEqual(len(k.kutsut), 1)                       # tarkastajaa ei aloitettu
        self.assertLessEqual(k.kutsut[0][2], yh.AIKARAJA_S)      # osakutsun aikaraja enintaan kokonaisraja
        self.assertEqual(t.teksti, "vastaus")
        self.assertIsNotNone(t.merkinta)

    def test_osakutsu_saa_jaljella_olevan_ajan(self):
        aika = [0.0]
        k = Korvike()

        def kysy(*a, **kw):
            aika[0] += 100
            return k(*a, **kw)
        yh.aja("tarkastus", ROOLIT, M1, PERUS, kysy, kello=lambda: aika[0])
        self.assertEqual([round(x[2]) for x in k.kutsut], [yh.AIKARAJA_S, yh.AIKARAJA_S - 100])


class Paluu(unittest.TestCase):
    """Osakutsun virhe palaa yhden mallin vastaukseen ja antaa merkinnan."""

    def test_tarkastajan_virhe(self):
        k = Korvike(virheet={"tarkastaja": yksityinen.YksityinenVirhe("mallipalvelu ei vastannut")})
        t = yh.aja("tarkastus", ROOLIT, M3, PERUS, k)
        self.assertEqual((t.teksti, t.malli, t.luonnos), ("vastaaja " + M1, M1, None))
        self.assertIn("yhden mallin vastaus", t.merkinta)

    def test_rinnakkaisen_vastaajan_virhe_ei_kutsu_kokoajaa(self):
        k = Korvike(virheet={(M2, "vastaaja"): yksityinen.YksityinenVirhe("x")})
        t = yh.aja("rinnakkais", ROOLIT, M3, PERUS, k)
        self.assertEqual(sorted(k.mallit()), sorted([M1, M2]))
        self.assertEqual((t.teksti, t.malli), ("vastaaja " + M1, M1))
        self.assertIn("yhden mallin vastaus", t.merkinta)

    def test_kokoajan_virhe(self):
        k = Korvike(virheet={"kokoaja": yksityinen.YksityinenVirhe("x")})
        t = yh.aja("rinnakkais", ROOLIT, M3, PERUS, k)
        self.assertEqual((t.teksti, t.malli, t.kutsuja), ("vastaaja " + M1, M1, 3))
        self.assertIsNotNone(t.merkinta)

    def test_reitittimen_virhe_tai_epaselva_luokka(self):
        for k in (Korvike(virheet={"reititin": yksityinen.YksityinenVirhe("x")}), Korvike({"reititin": "en tied\u00e4"})):
            with self.subTest(k=k):
                t = yh.aja("reititys", ROOLIT, M1, PERUS, k)
                self.assertEqual(k.mallit(), [M3, M2])               # kevyt malli vastaa
                self.assertIsNotNone(t.merkinta)

    def test_ensimmaisen_vastauksen_virhe_nostetaan(self):
        for nimi in ("yksi", "tarkastus"):
            with self.subTest(nimi=nimi), self.assertRaises(yksityinen.YksityinenVirhe):
                yh.aja(nimi, ROOLIT, M1, PERUS, Korvike(virheet={"vastaaja": yksityinen.YksityinenVirhe("x")}))
        with self.assertRaises(yksityinen.YksityinenVirhe):
            yh.aja("rinnakkais", ROOLIT, M1, PERUS, Korvike(virheet={"vastaaja": yksityinen.YksityinenVirhe("x")}))

    def test_katkaisun_jalkeen_ajo_ei_kutsu_mallia(self):
        k = Korvike(virheet={(0, "vastaaja"): yh.Katkaisu("katto")})
        ajo = yh._Ajo(k, lambda: 0.0, 100)
        for _ in range(2):
            with self.assertRaises(yh.Katkaisu):
                ajo.kutsu(M1, PERUS)
        self.assertEqual(len(k.kutsut), 1)

    def test_katkaisu_lopettaa_kaikki_seuraavat_kutsut(self):
        k = Korvike(virheet={"reititin": yh.Katkaisu("katto")})
        with self.assertRaises(yh.Katkaisu):
            yh.aja("reititys", ROOLIT, M1, PERUS, k)
        self.assertEqual(len(k.kutsut), 1)


class Merkinnat(unittest.TestCase):
    """Ehdotus-, valinta- ja luovutusmerkinnat sailyvat tarkastajan ja kokoajan lapi sanasta sanaan."""

    def tarkista(self, lopullinen):
        self.assertEqual(lopullinen.count(EHDOTUS), 1)
        self.assertEqual(lopullinen.count(LUOVUTUS), 1)
        self.assertTrue(lopullinen.endswith(VALINNAT))
        self.assertEqual(lopullinen.count("[["), 5)               # ei muita merkintoja

    def test_tarkastaja_pudottaa_merkinnat(self):
        k = Korvike({"vastaaja": LUONNOS, "tarkastaja": "Parempi: tee A ja B."})
        t = yh.aja("tarkastus", ROOLIT, M3, PERUS, k)
        self.assertTrue(t.teksti.startswith("Parempi: tee A ja B."))
        self.tarkista(t.teksti)
        self.assertNotIn("[[", k.kutsut[1][1][-1]["content"])    # tarkastaja ei nae merkintoja syotteessa
        self.assertEqual(t.luonnos, LUONNOS)

    def test_tarkastaja_muuttaa_tai_keksii_merkintoja(self):
        keksitty = ("Parempi.\n[[EHDOTUS tyyli.md]]\nMuutettu!\n[[/EHDOTUS]]\n[[EHDOTUS uusi.md]]x[[/EHDOTUS]]\n"
                    "[[LUOVUTUS]]vuoto[[/LUOVUTUS]]\n[[PAATOS 1]]\n[[VALINNAT: a | b]]")
        t = yh.aja("tarkastus", ROOLIT, M3, PERUS, Korvike({"vastaaja": LUONNOS, "tarkastaja": keksitty}))
        self.tarkista(t.teksti)
        for sana in ("Muutettu!", "uusi.md", "vuoto", "PAATOS", "a | b"):
            self.assertNotIn(sana, t.teksti)

    def test_kokoaja_saa_ensisijaisen_vastaajan_merkinnat(self):
        k = Korvike({(M1, "vastaaja"): LUONNOS, (M2, "vastaaja"): "B [[VALINNAT: x | y]]", "kokoaja": "Koottu."})
        t = yh.aja("rinnakkais", ROOLIT, M3, PERUS, k)
        self.assertTrue(t.teksti.startswith("Koottu."))
        self.tarkista(t.teksti)

    def test_katkennut_lohko_sailyy_ja_paatos_siirtyy(self):
        luonnos = "Teksti [[PAATOS 1]]\n[[EHDOTUS a.md]]\nkesken"
        t = yh.aja("tarkastus", ROOLIT, M3, PERUS, Korvike({"vastaaja": luonnos, "tarkastaja": "Korjattu."}))
        self.assertEqual(t.teksti, "Korjattu.\n\n[[PAATOS 1]]\n\n[[EHDOTUS a.md]]\nkesken")

    def test_ilman_merkintoja_tarkastajan_merkinnat_poistetaan(self):
        t = yh.aja("tarkastus", ROOLIT, M3, PERUS, Korvike({"tarkastaja": "Ok. " + EHDOTUS}))
        self.assertEqual(t.teksti, "Ok.")


class FeikkiVastaus:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self, n=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Verkko:
    """Korvaa vain verkkoyhteyden. Kirjaa jokaisen lahetetyn rungon ja aikarajan."""

    def __init__(self, vastaukset=None, kulu=0.001, virheet=()):
        self.rungot = []
        self.aikarajat = []
        self.vastaukset = vastaukset or {}
        self.kulu = kulu
        self.virheet = set(virheet)
        self._lukko = threading.Lock()

    def __call__(self, pyynto, timeout=None):
        runko = json.loads(pyynto.data)
        with self._lukko:
            self.rungot.append(runko)
            self.aikarajat.append(timeout)
        rooli = _rooli(runko["messages"])
        if (runko["model"], rooli) in self.virheet:
            raise OSError("yhteys katkesi")
        teksti = self.vastaukset.get((runko["model"], rooli), self.vastaukset.get(rooli, "%s %s" % (rooli, runko["model"])))
        return FeikkiVastaus({"choices": [{"message": {"content": teksti}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": self.kulu}})


class Kutsupolku(unittest.TestCase):
    """Oikea yksityinen_or + openrouter_kulut; vain verkkoyhteys korvattu."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        self.avain = self.d / "avain"
        self.avain.write_text(AVAIN + "\n")
        os.chmod(self.avain, 0o600)
        self.kulut = self.d / "kulut"
        self.kulut.mkdir(mode=0o700)

    def aja(self, nimi, verkko, roolit=ROOLIT):
        return yo.kysy_yhdistelmalla("muisti", [{"rooli": "tony", "teksti": "moi"}], "ohje", malli=M1,
                                     yhdistelma_nimi=nimi, roolit=roolit, avain_tiedosto=str(self.avain),
                                     kulutila=str(self.kulut), avaa=verkko, nyt=lambda: NYT)

    def kulurivit(self):
        p = self.kulut / ok.KULUT
        rivit = [json.loads(r) for r in p.read_text().splitlines()] if p.exists() else []
        return [r for r in rivit if not r.get("varaus")]

    def test_zdr_runko_ja_kulukirjaus_jokaisessa_kutsussa(self):
        for nimi, odotettu in (("yksi", 1), ("tarkastus", 2), ("rinnakkais", 3), ("reititys", 2)):
            with self.subTest(nimi=nimi):
                v = Verkko({"reititin": "vaativa"})
                ennen = len(self.kulurivit())
                t = self.aja(nimi, v)
                self.assertEqual(len(v.rungot), odotettu)
                self.assertEqual(t.kutsuja, odotettu)
                for runko in v.rungot:
                    self.assertIs(runko["provider"]["zdr"], True)
                    self.assertEqual(runko["provider"]["data_collection"], "deny")
                    self.assertTrue(yo.avoin_malli(runko["model"]))
                    self.assertLessEqual(runko["max_tokens"], yo.MAX_TOKENIA)
                    self.assertLessEqual(v.aikarajat[0], yo.VASTAUS_AIKARAJA_S)
                self.assertEqual(len(self.kulurivit()) - ennen, odotettu)
                self.assertEqual({r["tehtava"] for r in self.kulurivit()}, {yo.TEHTAVA})

    def test_jarjestys_kutsupolussa(self):
        v = Verkko()
        self.aja("tarkastus", v)
        self.assertEqual([r["model"] for r in v.rungot], [M1, M2])
        self.assertIn("ZDR", v.rungot[1]["messages"][0]["content"])          # tarkastaja saa saman yksityisen kehotteen
        self.assertIn("tarkastaja.", v.rungot[1]["messages"][0]["content"])

    def test_ei_avoin_rooli_malli_hylataan_ennen_kutsua(self):
        v = Verkko()
        with self.assertRaises(yksityinen.YksityinenVirhe):
            self.aja("tarkastus", v, {"vastaaja": M1, "tarkastaja": "openai/gpt-4o"})
        self.assertEqual(v.rungot, [])

    def test_paivakatto_taynna_ei_yhtaan_kutsua(self):
        ok.kirjaa(M1, "muu", {"usage": {"cost": 20.0}}, NYT, str(self.kulut))
        v = Verkko()
        for nimi in yh.YHDISTELMAT:
            with self.subTest(nimi=nimi), self.assertRaises(yksityinen.YksityinenVirhe):
                self.aja(nimi, v)
        self.assertEqual(v.rungot, [])

    def test_paivakatto_tayttyy_kesken_ja_katkaisee(self):
        v = Verkko(kulu=11.5)                  # ensimmainen kutsu tayttaa 10 EUR katon
        t = self.aja("tarkastus", v)
        self.assertEqual(len(v.rungot), 1)
        self.assertEqual((t.teksti, t.malli), ("vastaaja " + M1, M1))
        self.assertIn("p\u00e4iv\u00e4katto", t.merkinta)

    def test_osakutsun_verkkovirhe_palaa_yhteen_malliin(self):
        v = Verkko(virheet=[(M2, "tarkastaja")])
        t = self.aja("tarkastus", v)
        self.assertEqual((t.teksti, len(v.rungot)), ("vastaaja " + M1, 2))
        self.assertIsNotNone(t.merkinta)


class Palvelin(unittest.TestCase):
    """HTTP: valinta keskustelukohtaisesti samasta kentasta kuin malli; merkinnat sailyvat chatin kasittelyssa."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        r = self.r = Path(self._t.name)
        (r / "kirjasto").mkdir()
        (r / "avain").write_text(AVAIN + "\n")
        os.chmod(r / "avain", 0o600)
        (r / "kulut").mkdir(mode=0o700)
        self.verkko = Verkko({"reititin": "kevyt"})
        alkup = yo.tee_kuljetin
        p = mock.patch.object(yo, "tee_kuljetin", lambda avain, avaa=None, aikaraja_s=None: alkup(avain, self.verkko, aikaraja_s))
        p.start()
        self.addCleanup(p.stop)

    def kaynnista(self, **kw):
        r = self.r
        self.tila = chat.Tila(chat.tiivista_salasana(SALASANA), str(r / "kirjasto"), str(r / "data"), ["localhost"],
                              kysy=lambda m, h: "pilvi", yks_data=str(r / "yks"), yks_muisti=str(r / "yksm"),
                              ydinohjeet=str(r / "ydin"), ehdotukset=str(r / "ehd"), or_avain=str(r / "avain"),
                              or_kulutila=str(r / "kulut"), taustapaa="openrouter", or_mallit=[M1, M2, M3],
                              roolit="vastaaja:%s,tarkastaja:%s,reititin:%s,kevyt:%s" % (M1, M2, M3, M2), **kw)
        srv = chat.kaynnista("127.0.0.1", 0, self.tila)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.portti = srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", self.portti, timeout=10)
        c.request("POST", "/kirjaudu", body="salasana=" + SALASANA,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        x = c.getresponse()
        x.read()
        self.ev = x.getheader("Set-Cookie").split(";")[0]

    def api(self, polku, olio=None, metodi="POST"):
        c = http.client.HTTPConnection("127.0.0.1", self.portti, timeout=20)
        c.request(metodi, polku, body=None if olio is None else json.dumps(olio),
                  headers={"Cookie": self.ev, "X-Aivo": "1", "Content-Type": "application/json"})
        x = c.getresponse()
        return x.status, json.loads(x.read())

    def test_oletus_on_yksi_ja_vastaus_kuten_ennen(self):
        self.kaynnista()
        s, j = self.api("/api/uusi", {"tila": "yksityinen"})
        self.assertEqual((s, j["yhdistelma"]), (200, "yksi"))
        s, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi"})
        self.assertEqual((s, v["malli"], len(self.verkko.rungot)), (200, M1, 1))
        self.assertNotIn("mallit", v)

    def test_valinta_keskustelukohtaisesti_ja_tieto_vastauksessa(self):
        self.kaynnista()
        _, j = self.api("/api/uusi", {"tila": "yksityinen", "yhdistelma": "tarkastus"})
        s, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi"})
        self.assertEqual(s, 200)
        self.assertEqual((v["yhdistelma"], v["mallit"], v["kutsuja"], v["malli"]), ("tarkastus", [M1, M2], 2, M2))
        self.assertEqual(v["luonnos"], "vastaaja " + M1)
        rivi = self.tila.yks.lue(j["id"])[-1]
        self.assertEqual((rivi["malli"], rivi["mallit"], rivi["kutsuja"]), (M2, [M1, M2], 2))
        self.assertNotIn("luonnos", rivi)
        # toinen keskustelu on yh\u00e4 oletuksessa; valinta vaihtuu mallivalinnalla takaisin yhteen malliin
        _, j2 = self.api("/api/uusi", {"tila": "yksityinen"})
        self.assertEqual(self.tila.keskustelun_yhdistelma(j2["id"]), "yksi")
        s, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi", "malli": M2})
        self.assertEqual((s, v["malli"]), (200, M2))
        self.assertNotIn("mallit", v)
        s, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi", "yhdistelma": "reititys"})
        self.assertEqual((v["yhdistelma"], v["mallit"]), ("reititys", [M3, M2]))

    def test_virheellinen_yhdistelma_400_eika_viestia(self):
        self.kaynnista()
        _, j = self.api("/api/uusi", {"tila": "yksityinen"})
        for arvo in ("kaikki", 3, None):
            with self.subTest(arvo=arvo):
                self.assertEqual(self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "x", "yhdistelma": arvo})[0], 400)
        self.assertEqual(self.tila.yks.lue(j["id"]), [])
        self.assertEqual(self.api("/api/uusi", {"tila": "tyokalu", "yhdistelma": "tarkastus"})[0], 400)
        _, t = self.api("/api/uusi", {"tila": "tyokalu"})
        self.assertEqual(self.api("/api/viesti", {"keskustelu": t["id"], "teksti": "x", "yhdistelma": "yksi"})[0], 400)

    def test_oletusyhdistelma_asetuksesta(self):
        self.kaynnista(yhdistelma_oletus="tarkastus")
        s, m = self.api("/api/mallit", metodi="GET")
        self.assertEqual((m["yhdistelma"], m["yhdistelmat"]), ("tarkastus", list(yh.YHDISTELMAT)))
        _, j = self.api("/api/uusi", {"tila": "yksityinen"})
        _, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi"})
        self.assertEqual(v["kutsuja"], 2)

    def test_merkinnat_sailyvat_tarkastajan_lapi_chatissa(self):
        self.verkko.vastaukset = {"vastaaja": LUONNOS, "tarkastaja": "Parempi vastaus.\n[[EHDOTUS keksitty.md]]x[[/EHDOTUS]]"}
        self.kaynnista()
        _, j = self.api("/api/uusi", {"tila": "yksityinen", "yhdistelma": "tarkastus"})
        s, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi"})
        self.assertEqual(s, 200)
        self.assertEqual(v["valinnat"], ["kyll\u00e4", "ei", "my\u00f6hemmin"])
        self.assertEqual(len(v["ehdotukset"]), 1)
        tallennettu = json.loads(next((self.r / "ehd").glob("*.json")).read_text())
        self.assertEqual(tallennettu["tiedosto"], "tyyli.md")
        self.assertIn("Parempi vastaus.", v["vastaus"])
        self.assertIn(LUOVUTUS, v["vastaus"])                 # luovutuslohko kulkee sellaisenaan (#363 kasittelee)
        self.assertNotIn("keksitty", json.dumps(v))

    def test_paivakatto_katkaisee_http(self):
        ok.kirjaa(M1, "muu", {"usage": {"cost": 20.0}}, datetime.now(ok.TZ), str(self.r / "kulut"))
        self.kaynnista()
        _, j = self.api("/api/uusi", {"tila": "yksityinen", "yhdistelma": "rinnakkais"})
        s, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi"})
        self.assertEqual(s, 502)
        self.assertIn("p\u00e4iv\u00e4katto", v["virhe"])
        self.assertEqual(self.verkko.rungot, [])

    def test_osakutsun_virhe_nakyy_merkintana(self):
        self.verkko.virheet = {(M2, "tarkastaja")}
        self.kaynnista()
        _, j = self.api("/api/uusi", {"tila": "yksityinen", "yhdistelma": "tarkastus"})
        s, v = self.api("/api/viesti", {"keskustelu": j["id"], "teksti": "moi"})
        self.assertEqual((s, v["vastaus"], v["malli"]), (200, "vastaaja " + M1, M1))
        self.assertIn("yhden mallin vastaus", v["merkinta"])
        self.assertNotIn("luonnos", v)


class Kayttoliittyma(unittest.TestCase):
    def test_tekstit_textcontentilla(self):
        sivu = chat.sivu_html("openrouter")
        f = re.search(r"function yhdistelmatieto\(d,mn,j\)\{.*?\n(?=async function|function )", sivu, re.S).group(0)
        self.assertNotIn("innerHTML", sivu)
        self.assertIn("pre.textContent=j.luonnos", f)
        self.assertIn("k.textContent=j.merkinta", f)
        self.assertIn("'N\u00e4yt\u00e4 luonnos'", f)

    @unittest.skipUnless(shutil.which("node"), "node puuttuu")
    def test_valitsin_lahettaa_mallin_tai_yhdistelman(self):
        f = re.search(r"function valitsinNakyy\(t,l\)\{[^}]*\}", chat.HTML).group(0)
        f2 = re.search(r"function mallikentta\(b\)\{[^\n]*\}", chat.HTML).group(0)
        js = f + "\nconst M={value:'B'};let nyt='yksityinen';let mallit=['A','yhdistelma:tarkastus'];\n" + f2 + """
const r=[JSON.stringify(mallikentta({keskustelu:'k'}))];M.value='yhdistelma:tarkastus';
r.push(JSON.stringify(mallikentta({keskustelu:'k'})));nyt='tyokalu';r.push(JSON.stringify(mallikentta({keskustelu:'k'})));
console.log(JSON.stringify(r));"""
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(json.loads(out), ['{"keskustelu":"k","malli":"B"}',
                                           '{"keskustelu":"k","yhdistelma":"tarkastus"}', '{"keskustelu":"k"}'])

    @unittest.skipUnless(shutil.which("node"), "node puuttuu")
    def test_mallit_kutsut_merkinta_ja_luonnos_nakyvat(self):
        f = re.search(r"function yhdistelmatieto\(d,mn,j\)\{.*?\n(?=async function|function )", chat.HTML, re.S).group(0)
        js = """
class E{constructor(t){this.t=t;this.lapset=[];this.textContent='';this.hidden=false;}appendChild(x){this.lapset.push(x);}
 append(...x){this.lapset.push(...x);}}
const document={createElement:t=>new E(t)};
""" + f + """
const d=new E('div'),mn=new E('span');
yhdistelmatieto(d,mn,{mallit:['a','b','a'],kutsuja:3,merkinta:'<b>m</b>',luonnos:'<img src=x>'});
const a=d.lapset[1];a.onclick({preventDefault(){}});
console.log(JSON.stringify([mn.textContent,d.lapset[0].textContent,a.textContent,d.lapset[2].textContent,d.lapset[2].hidden]));"""
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(json.loads(out), ["Mallit: a, b \u00b7 kutsuja: 3", "<b>m</b>", "Piilota luonnos",
                                           "<img src=x>", False])


if __name__ == "__main__":
    unittest.main()
