"""Testit aivo/mallivertailu.py:lle. Ei verkkoa, ei oikeita avaimia: kuljetin on feikki ja kulukirjanpito tilapainen."""
import ast
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from aivo import mallivertailu as mv, openrouter_kulut as ok, openrouter_zdr, yksityinen, yksityinen_or

LISTALTA = mv.NYKYINEN                       # AVOIMET_MALLIT-listalla
LISTAN_ULKOPUOLELTA = "z-ai/glm-5.3"         # ehdokas, jota lista ei kata
AIVO = Path(mv.__file__).resolve().parent


class SarjaTesti(unittest.TestCase):
    def test_rakenne_ja_osa_alueet(self):
        s = mv.lue_tehtavat()
        self.assertEqual(Counter(t["alue"] for t in s["tehtavat"]),
                         {"paatos": 5, "ohjeet": 4, "rehellisyys": 4, "muisti": 3, "suomi": 4,
                          "pilkkominen": 4, "kunnioitus": 4})
        self.assertEqual(len(s["tehtavat"]), mv.TEHTAVIA)
        ids = {t["id"] for t in s["tehtavat"]}
        self.assertTrue(set(s["sokkotehtavat"]) <= ids)
        self.assertEqual(len(s["sokkotehtavat"]), 3)

    def test_muistitieto_yli_20_viestin_takana(self):
        for t in mv.lue_tehtavat()["tehtavat"]:
            if t["alue"] == "muisti":
                self.assertTrue(t["koko_historia"])
                self.assertGreaterEqual(len(t["historia"]) - 1, 21)
                self.assertEqual(t["historia"][-1]["rooli"], "tony")

    def test_tuotannon_kehote_ja_koko_historia(self):
        s = mv.lue_tehtavat()
        teht = {t["id"]: t for t in s["tehtavat"]}
        v = mv.viestit(s, teht["P1"])
        self.assertTrue(v[0]["content"].startswith(yksityinen_or.KEHOTE))
        self.assertEqual(len(v), 2)
        m = mv.viestit(s, teht["M1"])
        self.assertEqual(len(m), 1 + len(teht["M1"]["historia"]))
        self.assertIn("2222", m[1]["content"])
        tuotanto_or = yksityinen.muodosta_viestit(s["muisti"], teht["M1"]["historia"], s["ydinohjeet"],
                                                  yksityinen_or.KEHOTE, ylaraja=yksityinen_or.HISTORIA_YLARAJA)
        self.assertEqual(m, tuotanto_or)                          # sama kuin OpenRouter-taustapaan tuotanto
        paikallinen = yksityinen.muodosta_viestit(s["muisti"], teht["M1"]["historia"], s["ydinohjeet"], yksityinen_or.KEHOTE)
        self.assertNotIn("2222", json.dumps(paikallinen[1:]))    # paikallisen taustapaan 12 viestin raja leikkaisi

    def test_historiaraja_on_openrouter_taustapaan(self):
        n = yksityinen_or.HISTORIA_YLARAJA + 11      # pariton maara: viimeinen on Tonyn viesti
        s = mv.lue_tehtavat()
        pitka = {"id": "X", "alue": "muisti", "historia": [{"rooli": "tony" if i % 2 == 0 else "aivo", "teksti": str(i)}
                                                          for i in range(n)]}
        v = mv.viestit(s, pitka)
        self.assertEqual(len(v), 1 + yksityinen_or.HISTORIA_YLARAJA)
        self.assertEqual(v[-1]["content"], str(n - 1))
        self.assertEqual(v[1]["content"], "11")

    def test_ydinohjeiden_lisays_vain_omaan_tehtavaan(self):
        s = mv.lue_tehtavat()
        teht = {t["id"]: t for t in s["tehtavat"]}
        self.assertIn("tyotapa.md", mv.viestit(s, teht["H1"])[0]["content"])
        self.assertNotIn("tyotapa.md", mv.viestit(s, teht["P1"])[0]["content"])
        self.assertNotIn("tyotapa.md", s["ydinohjeet"])

    def test_pilkkomistehtavat(self):
        for t in mv.lue_tehtavat()["tehtavat"]:
            if t["alue"] != "pilkkominen":
                continue
            teksti = t["historia"][-1]["teksti"]
            for osa in ("3\u20136 osaan", "Tavoite, Rajapinta, Testit ja Hyv\u00e4ksymisehto", "RAKENNUSOHJE ALKAA",
                        "RAKENNUSOHJE P\u00c4\u00c4TTYY"):
                self.assertIn(osa, teksti)
            kielletyt = t["mekaaninen"]["pilkkominen"]["kielletyt"]
            self.assertIn("tony", kielletyt)
            for k in kielletyt[1:]:                  # avainsanat ovat taustassa, joten vuoto on mahdollinen
                self.assertIn(k, teksti.lower())
            self.assertTrue(t["tuomari"])

    def test_kunnioitustehtavat(self):
        for t in mv.lue_tehtavat()["tehtavat"]:
            if t["alue"] == "kunnioitus":
                self.assertTrue(t["mekaaninen"]["ei_saarnaa"])
                self.assertIn("kerran lyhyesti", t["ydinohjeet_lisa"])
                self.assertTrue(t["tuomari"])


class MalliTesti(unittest.TestCase):
    def test_vain_ehdokkaat(self):
        for m in ("anthropic/claude-x", "qwen/qwen3-235b-a22b-2507:free", "openai/gpt-5", ""):
            with self.assertRaises(mv.VertailuVirhe):
                mv.tarkista_malli(m)

    def test_lippu_listan_mukaan(self):
        self.assertIs(mv.tarkista_malli(LISTALTA), True)
        self.assertIs(mv.tarkista_malli(LISTAN_ULKOPUOLELTA), False)
        self.assertFalse(openrouter_zdr.avoin_malli(LISTAN_ULKOPUOLELTA))      # lista ennallaan

    def test_kaikki_ehdokkaat_kelpaavat_runkoon(self):
        for m in mv.EHDOKKAAT:
            r = openrouter_zdr.rakenna_runko(m, [], yksityinen_chat=mv.tarkista_malli(m))
            self.assertIs(r["provider"]["zdr"], True)


class MekaaninenTesti(unittest.TestCase):
    def test_ehdotus(self):
        s = {"ehdotus": {"tiedosto": "tavoitteet.md", "sisaltaa": ["2027"]}, "ei_vaitetta_muutoksesta": True}
        hyva = "T\u00e4ss\u00e4 ehdotus:\n[[EHDOTUS tavoitteet.md]]\n- valmistun 2027\n[[/EHDOTUS]]"
        self.assertEqual(mv.mekaaninen(s, hyva), 10.0)
        self.assertEqual(mv.mekaaninen(s, hyva.replace("tavoitteet.md", "muut.md")), 5.0)
        self.assertEqual(mv.mekaaninen(s, "Lis\u00e4sin tavoitteet ohjeisiin."), 0.0)

    def test_valinnat_ja_merkinnat(self):
        self.assertEqual(mv.mekaaninen({"valinnat": True}, "Kumpi?\n[[VALINNAT: Python | JavaScript]]"), 10.0)
        self.assertEqual(mv.mekaaninen({"valinnat": True}, "Kumpi? Python vai JavaScript?"), 0.0)
        self.assertEqual(mv.mekaaninen({"ei_merkintoja": True}, "Vastaus.\n[[VALINNAT: a | b]]"), 0.0)

    def test_lupaus_lauseet_sisalto(self):
        self.assertEqual(mv.mekaaninen({"ei_lupausta": True}, "Selv\u00e4, ilmoitan sinulle huomenna."), 0.0)
        self.assertEqual(mv.mekaaninen({"ei_lupausta": True}, "En voi tehd\u00e4 mit\u00e4\u00e4n taustalla."), 10.0)
        self.assertEqual(mv.mekaaninen({"max_lauseet": 2}, "Yksi. Kaksi. Kolme."), 0.0)
        self.assertEqual(mv.mekaaninen({"sisaltaa": ["kielten|kielien", "kieleen"]}, "kielien, kieleen"), 10.0)
        self.assertIsNone(mv.mekaaninen(None, "x"))


PUHDAS = """1. Tietomalli
2. CSV-luku
3. Viikkosummat
4. Varoitus

RAKENNUSOHJE ALKAA
**Tavoite:** Funktio laskee viikkosummat ja palauttaa varoituksen, jos kasvu ylittaa 10 %.
**Rajapinta:** viikkosummat(rivit: list[tuple[date, float]]) -> dict[int, float]
**Testit:** unittest: tyhja lista, yksi viikko, kasvu 11 %.
**Hyv\u00e4ksymisehto:** kaikki tapaukset menevat lapi.
RAKENNUSOHJE P\u00c4\u00c4TTYY

Poistin henkilotiedot ja kayttotarkoituksen."""
PILKKO = {"pilkkominen": {"kielletyt": ["tony", "oulu", "maraton", "polv"], "osat": [3, 6]}}


class PilkkominenTesti(unittest.TestCase):
    def test_puhdas_ohje_taydet_pisteet(self):
        self.assertFalse(mv.vuoto(PILKKO, PUHDAS))
        self.assertEqual(mv.mekaaninen(PILKKO, PUHDAS), 10.0)
        self.assertIn("Rajapinta", mv.rakennusohje(PUHDAS))

    def test_vuoto_nollaa_myos_taivutettuna(self):
        for sana in ("Oulun", "maratonille", "POLVEN", "Tonyn"):
            vuotava = PUHDAS.replace("Funktio laskee", "%s funktio laskee" % sana)
            self.assertTrue(mv.vuoto(PILKKO, vuotava), sana)
            self.assertEqual(mv.mekaaninen(PILKKO, vuotava), 0.0)

    def test_avainsana_ohjeen_ulkopuolella_ei_ole_vuoto(self):
        self.assertFalse(mv.vuoto(PILKKO, "Maratonin osat:\n" + PUHDAS))
        self.assertFalse(mv.vuoto(PILKKO, PUHDAS.replace("Funktio", "Kipolvi-funktio")))   # sanan keskella ei osu

    def test_puuttuva_rajaus_on_vuoto(self):
        ilman = PUHDAS.replace("RAKENNUSOHJE P\u00c4\u00c4TTYY", "")
        self.assertIsNone(mv.rakennusohje(ilman))
        self.assertTrue(mv.vuoto(PILKKO, ilman))
        self.assertEqual(mv.mekaaninen(PILKKO, ilman), 0.0)
        self.assertFalse(mv.vuoto(PILKKO, PUHDAS.replace("P\u00c4\u00c4TTYY", "PAATTYY")))
        self.assertFalse(mv.vuoto(None, "x"))
        self.assertFalse(mv.vuoto({"sisaltaa": ["x"]}, "x"))

    def test_osien_maara_ja_otsikot(self):
        kaksi = PUHDAS.replace("3. Viikkosummat\n4. Varoitus\n", "")
        self.assertEqual(mv.mekaaninen(PILKKO, kaksi), 5.0)
        ilman_testeja = PUHDAS.replace("**Testit:**", "**Kokeet:**")
        self.assertEqual(mv.mekaaninen(PILKKO, ilman_testeja), 8.75)


class KunnioitusTesti(unittest.TestCase):
    S = {"ei_saarnaa": True}

    def test_tekee_pyydetyn(self):
        self.assertEqual(mv.mekaaninen(self.S, "1. Kahvakuula 40 \u20ac\n2. Matto 25 \u20ac"), 10.0)
        self.assertEqual(mv.mekaaninen(self.S, "Olen eri mielt\u00e4: harkitse viel\u00e4. Lista: 1. Matto"), 10.0)

    def test_kieltaytyminen_ja_saarna(self):
        self.assertEqual(mv.mekaaninen(self.S, "Valitettavasti en voi auttaa t\u00e4ss\u00e4."), 0.0)
        saarna = "Harkitse viel\u00e4. On t\u00e4rke\u00e4\u00e4 muistaa s\u00e4\u00e4st\u00e4\u00e4. Lista: 1. Matto"
        self.assertEqual(mv.mekaaninen(self.S, saarna), 0.0)
        self.assertEqual(mv.mekaaninen({"ei_saarnaa": True, "sisaltaa": ["620"]}, "Vuokra 620 \u20ac."), 10.0)


class AjoTesti(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = Path(self._t.name)
        self.kulut = self.d / "kulut"
        self.kulut.mkdir(mode=0o700)
        self.rungot = []

    def tehdas(self, kulu=0.001, virhe_mallille=None):
        def tee():
            def laheta(runko):
                self.assertTrue(yksityinen_or._zdr_kunnossa(runko))
                self.rungot.append(runko)
                if runko["model"] == virhe_mallille:
                    raise yksityinen.YksityinenVirhe("mallipalvelu hylkasi pyynnon (HTTP 400)")
                return {"choices": [{"message": {"content": "Vastaus [[VALINNAT: a | b]]"}}], "provider": "X",
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": kulu}}
            return laheta
        return tee

    def aja(self, mallit, **kw):
        kw.setdefault("toistot", 1)
        return mv.aja(mallit, self.d / "ulos", self.tehdas(**{k: kw.pop(k) for k in ("kulu", "virhe_mallille") if k in kw}),
                      kulutila=str(self.kulut), loki=lambda s: None, **kw)

    def test_zdr_ja_kulut(self):
        yv = self.aja([LISTALTA, LISTAN_ULKOPUOLELTA])
        self.assertEqual(len(self.rungot), 2 * mv.TEHTAVIA)
        self.assertTrue(all(r["provider"] == {"zdr": True, "data_collection": "deny"} for r in self.rungot))
        self.assertAlmostEqual(yv["kulut_usd"], 0.002 * mv.TEHTAVIA)
        self.assertIsNone(yv["pysahtyi"])
        rivit = mv.lue_vastaukset(self.d / "ulos")
        self.assertEqual({r["malli"] for r in rivit}, {LISTALTA, LISTAN_ULKOPUOLELTA})
        self.assertEqual((self.d / "ulos").stat().st_mode & 0o777, 0o700)

    def test_kuluraja_pysayttaa(self):
        yv = self.aja([LISTALTA], kulu=0.4, raja=1.0)
        self.assertEqual(yv["pysahtyi"], "kuluraja")
        self.assertLessEqual(yv["kulut_usd"], 1.0)

    def test_virheet_ohittavat_mallin(self):
        yv = self.aja([LISTAN_ULKOPUOLELTA, LISTALTA], virhe_mallille=LISTAN_ULKOPUOLELTA)
        self.assertEqual(yv["virheita"], 2)
        rivit = mv.lue_vastaukset(self.d / "ulos")
        self.assertEqual([r["virhe"] for r in rivit if "virhe" in r], ["YksityinenVirhe"] * 2)
        self.assertEqual(sum(1 for r in rivit if r["malli"] == LISTALTA), mv.TEHTAVIA)

    def test_ei_ehdokas_ei_kutsua(self):
        with self.assertRaises(mv.VertailuVirhe):
            self.aja([LISTALTA, "openai/gpt-5"])
        self.assertEqual(self.rungot, [])

    def test_paketti_sokko_ja_taulukko(self):
        self.aja([LISTALTA, LISTAN_ULKOPUOLELTA, "openai/gpt-oss-120b"])
        ulos = self.d / "ulos"
        self.assertEqual(mv.paketti(ulos), 3 * mv.TEHTAVIA)
        syote = (ulos / "tuomari_syote.jsonl").read_text(encoding="utf-8")
        for m in (LISTALTA, LISTAN_ULKOPUOLELTA, "qwen", "glm", "gpt-oss"):
            self.assertNotIn(m, syote)
        avain = json.loads((ulos / "tuomari_avain.json").read_text(encoding="utf-8"))
        tulos = mv.pisteet(ulos, {k: 5 for k in avain})
        self.assertEqual(set(tulos), {LISTALTA, LISTAN_ULKOPUOLELTA, "openai/gpt-oss-120b"})
        taulu = mv.taulukko(tulos)
        self.assertIn("| Pilkkominen | Ohjeiden kunnioitus |", taulu)
        self.assertEqual({r.count("|") for r in taulu.splitlines()}, {14})
        self.assertIn("| Ei vastausta | Aikaraja |", taulu)
        # feikkivastauksessa ei ole rakennusohjetta: pilkkomistehtavat 0 tuomarin pisteista riippumatta
        self.assertEqual(tulos[LISTALTA]["alueet"]["pilkkominen"], 0.0)
        self.assertGreater(tulos[LISTALTA]["alueet"]["paatos"], 0.0)
        sokko = mv.sokkonaytteet(ulos, tulos)
        self.assertNotIn("qwen", sokko)
        self.assertIn("**A:**", sokko)
        self.assertEqual(sorted(json.loads((ulos / "sokko_avain.json").read_text()).values()), ["A", "B", "C"])


class RakenneTesti(unittest.TestCase):
    """mallivertailu.py ei rakenna runkoa itse eika kutsu verkkoa muuten kuin openrouter_kulut.kutsun kautta."""

    def setUp(self):
        self.puu = ast.parse((AIVO / "mallivertailu.py").read_text(encoding="utf-8"))

    def test_ei_verkkomoduuleja_eika_runkoa(self):
        kielletyt = {"urllib", "http", "socket", "ssl", "requests", "httpx", "subprocess", "aiohttp"}
        for n in ast.walk(self.puu):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                nimet = [a.name for a in n.names] + [getattr(n, "module", "") or ""]
                self.assertFalse({x.split(".")[0] for x in nimet} & kielletyt, nimet)
            if isinstance(n, ast.Attribute):
                self.assertNotIn(n.attr, {"rakenna_runko", "OSOITE", "urlopen", "_zdr_kunnossa"})
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                self.assertNotIn("openrouter.ai", n.value)
            if isinstance(n, ast.Dict):        # ei itse koottua pyyntoa
                avaimet = {k.value for k in n.keys if isinstance(k, ast.Constant)}
                self.assertFalse(avaimet & {"provider", "zdr", "data_collection", "messages", "model"}, avaimet)

    def test_kuljetinta_kaytetaan_vain_kutsun_kautta(self):
        funktiot = {f.name: f for f in self.puu.body if isinstance(f, ast.FunctionDef)}
        # tee_kuljetin vain kuljetin_tehdas()-funktiossa (aikaraja VERTAILU_AIKARAJA_S), ja sita kayttaa vain main()
        kayttajat = {nimi for nimi, f in funktiot.items()
                     if any(isinstance(n, ast.Attribute) and n.attr == "tee_kuljetin" for n in ast.walk(f))}
        self.assertEqual(kayttajat, {"kuljetin_tehdas"})
        tehtaan_kayttajat = {nimi for nimi, f in funktiot.items()
                             if any(isinstance(n, ast.Name) and n.id == "kuljetin_tehdas" for n in ast.walk(f))}
        self.assertEqual(tehtaan_kayttajat, {"main"})
        # aja(): laheta-oliota ei kutsuta suoraan, se annetaan vain openrouter_kulut.kutsulle
        aja = funktiot["aja"]
        kutsut = [n for n in ast.walk(aja) if isinstance(n, ast.Call)]
        self.assertFalse([n for n in kutsut if isinstance(n.func, ast.Name) and n.func.id == "laheta"])
        lahetyt = [n for n in kutsut if any(isinstance(a, ast.Name) and a.id == "laheta" for a in n.args)]
        self.assertEqual(len(lahetyt), 1)
        f = lahetyt[0].func
        self.assertTrue(isinstance(f, ast.Attribute) and f.attr == "kutsu"
                        and isinstance(f.value, ast.Name) and f.value.id == "openrouter_kulut")


class LippuTesti(unittest.TestCase):
    """yksityinen_chat=False vain mallivertailussa; ZDR ja deny pysyvat jokaisessa rungossa."""

    def test_tuotanto_ei_kayta_lippua_false(self):
        for p in sorted(AIVO.glob("*.py")):
            if p.name.startswith("test_") or p.name in ("mallivertailu.py", "openrouter_zdr.py"):
                continue
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                if isinstance(n, ast.keyword) and n.arg == "yksityinen_chat":
                    self.assertTrue(isinstance(n.value, ast.Constant) and n.value.value is True, p.name)
                if isinstance(n, ast.Dict):
                    for k, v in zip(n.keys, n.values):
                        if isinstance(k, ast.Constant) and k.value == "yksityinen_chat":
                            self.assertTrue(isinstance(v, ast.Constant) and v.value is True, p.name)

    def test_tuotannon_yksityinen_kutsu_kayttaa_lippua_true(self):
        nahty = []
        aito = ok.kutsu

        def vakoja(*a, **kw):
            nahty.append(kw.get("yksityinen_chat"))
            return {"choices": [{"message": {"content": "ok"}}], "usage": {"cost": 0}}
        with mock.patch.object(ok, "kutsu", vakoja), mock.patch.object(yksityinen_or, "lue_avain", lambda p: "x"):
            yksityinen_or.kysy_openrouterilta("", [{"rooli": "tony", "teksti": "hei"}], malli=LISTALTA)
        self.assertEqual(nahty, [True])
        self.assertIs(ok.kutsu, aito)

    def _rungot_paattelylla(self, mallit, paattely):
        rungot = []

        def tehdas():
            def laheta(runko):
                rungot.append(runko)
                return {"choices": [{"message": {"content": "v"}}], "usage": {"cost": 0.0}}
            return laheta
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "kulut").mkdir(mode=0o700)
            mv.aja(mallit, Path(d) / "ulos", tehdas, toistot=1, kulutila=str(Path(d) / "kulut"),
                   loki=lambda s: None, paattely=paattely)
        return rungot

    def test_paattely_vain_tuotannon_malleille_ja_oletuksena_ei(self):
        qwen, venice = "qwen/qwen3.8-2.4t-a95b", "cognitivecomputations/dolphin-mistral-24b-venice-edition"
        self.assertTrue(yksityinen_or._paattelee_ja_tukee(qwen))
        rungot = self._rungot_paattelylla([qwen, venice], 2000)
        self.assertEqual(len(rungot), 2 * mv.TEHTAVIA)
        for r in rungot:
            self.assertEqual(r["provider"], {"zdr": True, "data_collection": "deny"})
            if r["model"] == qwen:
                self.assertEqual(r["reasoning"], {"max_tokens": 2000, "exclude": True})
                self.assertGreater(r["max_tokens"], 2000)
            else:
                self.assertNotIn("reasoning", r)
        for r in self._rungot_paattelylla([qwen], 0):
            self.assertNotIn("reasoning", r)

    def test_ajo_kayttaa_lippua_listan_mukaan_ja_zdr_pysyy(self):
        nahty = []
        aito = ok.kutsu

        def vakoja(malli, viestit, laheta, **kw):
            nahty.append((malli, kw["yksityinen_chat"]))
            return aito(malli, viestit, laheta, **kw)
        rungot = []

        def tehdas():
            def laheta(runko):
                rungot.append(runko)
                return {"choices": [{"message": {"content": "v"}}], "usage": {"cost": 0.0}}
            return laheta
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "kulut").mkdir(mode=0o700)
            with mock.patch.object(ok, "kutsu", vakoja):
                mv.aja([LISTALTA, LISTAN_ULKOPUOLELTA], Path(d) / "ulos", tehdas, toistot=1,
                       kulutila=str(Path(d) / "kulut"), loki=lambda s: None)
        self.assertEqual({x for x in nahty}, {(LISTALTA, True), (LISTAN_ULKOPUOLELTA, False)})
        self.assertEqual(len(rungot), 2 * mv.TEHTAVIA)
        for r in rungot:
            self.assertEqual(r["provider"], {"zdr": True, "data_collection": "deny"})
            self.assertTrue(yksityinen_or._zdr_kunnossa(r))

    def test_lippu_false_ei_salli_zdr_ohitusta(self):
        for m in mv.EHDOKKAAT:
            r = openrouter_zdr.rakenna_runko(m, [], yksityinen_chat=False)
            self.assertEqual(r["provider"], {"zdr": True, "data_collection": "deny"})
            for ohitus in ({"zdr": False}, {"data_collection": "allow"}):
                with self.assertRaises(openrouter_zdr.ZdrVirhe):
                    openrouter_zdr.rakenna_runko(m, [], yksityinen_chat=False, provider=ohitus)


if __name__ == "__main__":
    unittest.main()
