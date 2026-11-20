"""Testit aivo/openrouter_zdr.py:lle. Ei verkkoa, ei tunnuksia."""
import ast
import unittest
from pathlib import Path

from aivo import openrouter_zdr as oz

MSG = [{"role": "user", "content": "x"}]
AIVO = Path(oz.__file__).resolve().parent


class DataCollectionTesti(unittest.TestCase):
    def test_deny_aina_mukana(self):
        self.assertEqual(oz.rakenna_runko("m/x", MSG)["provider"]["data_collection"], "deny")
        r = oz.rakenna_runko("m/x", MSG, provider={"order": ["a"], "data_collection": "deny"})
        self.assertEqual(r["provider"], {"order": ["a"], "data_collection": "deny", "zdr": True})

    def test_muu_runko_koskematta(self):
        r = oz.rakenna_runko("m/x", MSG, provider={"order": ["a"]}, temperature=0)
        self.assertEqual(r, {"model": "m/x", "messages": MSG, "temperature": 0,
                             "provider": {"order": ["a"], "zdr": True, "data_collection": "deny"}})

    def test_ohitusyritys_kaatuu(self):
        for kw in ({"provider": {"data_collection": "allow"}},
                   {"provider": {"data_collection": "DENY"}},
                   {"provider": {"data_collection": None}},
                   {"provider": {"Data_Collection": "allow"}},
                   {"provider": {"DATA_COLLECTION": "deny"}},
                   {"provider": {" data_collection ": "deny"}},
                   {"provider": {"data_collection": "deny", "DATA_COLLECTION": "allow"}},
                   {"data_collection": "deny"}, {"Data_Collection": "allow"}):
            with self.subTest(kw=kw), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko("m/x", MSG, **kw)

    def test_syotetta_ei_muuteta(self):
        prov = {"order": ["a"]}
        oz.rakenna_runko("m/x", MSG, provider=prov)
        self.assertEqual(prov, {"order": ["a"]})

    def test_virhe_ei_vuoda_arvoa(self):
        with self.assertRaises(oz.ZdrVirhe) as c:
            oz.rakenna_runko("m/x", MSG, provider={"data_collection": "salainen-arvo"})
        self.assertNotIn("salainen-arvo", str(c.exception))


class ZdrTesti(unittest.TestCase):
    def test_zdr_aina_true(self):
        self.assertIs(oz.rakenna_runko("m/x", MSG)["provider"]["zdr"], True)
        r = oz.rakenna_runko("m/x", MSG, provider={"order": ["a"]}, temperature=0)
        self.assertIs(r["provider"]["zdr"], True)
        self.assertEqual(r["provider"]["order"], ["a"])
        self.assertEqual((r["model"], r["temperature"]), ("m/x", 0))
        self.assertIs(oz.rakenna_runko("m/x", MSG, provider={"zdr": True})["provider"]["zdr"], True)

    def test_syotteita_ei_muuteta(self):
        prov = {"order": ["a"]}
        oz.rakenna_runko("m/x", MSG, provider=prov)
        self.assertNotIn("zdr", prov)

    def test_ohitusyritys_kaatuu(self):
        for kw in ({"zdr": False}, {"zdr": True}, {"ZDR": False},
                   {"provider": {"zdr": False}}, {"provider": {"zdr": None}},
                   {"provider": {"zdr": 1}}, {"provider": "zdr"}):
            with self.subTest(kw=kw), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko("m/x", MSG, **kw)

    def test_provider_avaimen_kirjainkoko_ei_ohita(self):
        """Iso tai sekakirjaiminen avain ei saa ohittaa pakotusta."""
        for prov in ({"ZDR": False}, {"Zdr": False}, {"zDr": None}, {" zdr ": False},
                     {"ZDR": True}, {"zdr": True, "ZDR": False}, {"ZDR": False, "zdr": True}):
            with self.subTest(prov=prov), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko("m/x", MSG, provider=prov)

    def test_lisaparametrin_kirjainkoko(self):
        for k in ("ZDR", "Zdr", "zDR", " zdr"):
            with self.subTest(k=k), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko("m/x", MSG, **{k: False})

    def test_rungossa_vain_yksi_zdr_avain(self):
        r = oz.rakenna_runko("m/x", MSG, provider={"order": ["a"], "Order2": 1})
        self.assertEqual([k for k in r["provider"] if k.lower() == "zdr"], ["zdr"])

    def test_virheellinen_viesti_nostaa_zdrvirheen(self):
        for viestit in ("hei", b"hei", None, 5, ["hei"], [1], {"role": "user"},
                        [{"role": "user"}, "x"]):
            with self.subTest(viestit=viestit), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko("m/x", viestit)

    def test_virheellinen_viesti_ei_vuoda_sisaltoa(self):
        with self.assertRaises(oz.ZdrVirhe) as e:
            oz.rakenna_runko("m/x", ["salainen"])
        self.assertNotIn("salainen", str(e.exception))

    def test_generaattori_viesteina_toimii(self):
        r = oz.rakenna_runko("m/x", (m for m in MSG))
        self.assertEqual(r["messages"], MSG)

    def test_varatut_avaimet_kaatuvat(self):
        for k in ("model", "messages"):
            with self.subTest(k=k), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko("m/x", MSG, **{k: "y"})

    def test_yksityinen_chat_ei_totuusarvo_kaatuu(self):
        for arvo in (1, "kylla", None, "True"):
            with self.subTest(arvo=arvo), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko("meta-llama/llama-3.3-70b-instruct", MSG, yksityinen_chat=arvo)

    def test_yksityinen_chat_suljettu_malli_kaatuu(self):
        """A984: lippu sallitaan vain avoimella mallilla; muut mallit -> ZdrVirhe."""
        for malli in ("openai/gpt-4o", "anthropic/claude-sonnet-4", "qwen/qwen-max",
                      "meta-llama/llama-3.3-70b-instruct:free", "m/x"):
            with self.subTest(malli=malli), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko(malli, MSG, yksityinen_chat=True)

    def test_yksityinen_chat_avoin_malli_sallitaan_ja_zdr_pakotettu(self):
        r = oz.rakenna_runko("meta-llama/llama-3.3-70b-instruct", MSG, yksityinen_chat=True)
        self.assertIs(r["provider"]["zdr"], True)
        self.assertEqual(r["provider"]["data_collection"], "deny")
        self.assertNotIn("yksityinen_chat", r)

    def test_yksityinen_chat_ei_ohita_zdr_saantoa(self):
        malli = "meta-llama/llama-3.3-70b-instruct"
        for prov in ({"zdr": False}, {"data_collection": "allow"}, {"ZDR": True}):
            with self.subTest(prov=prov), self.assertRaises(oz.ZdrVirhe):
                oz.rakenna_runko(malli, MSG, yksityinen_chat=True, provider=prov)
        with self.assertRaises(oz.ZdrVirhe):
            oz.rakenna_runko(malli, MSG, yksityinen_chat=True, zdr=False)

    def test_ilman_lippua_sallittu_malli_ei_muutu(self):
        """Lipun puute ei ohita ZDR:aa: runko on silti pakotettu."""
        r = oz.rakenna_runko("openai/gpt-4o", MSG)
        self.assertIs(r["provider"]["zdr"], True)

    def test_tyhja_malli_kaatuu(self):
        with self.assertRaises(oz.ZdrVirhe):
            oz.rakenna_runko("", MSG)

    def test_virhe_ei_vuoda_sisaltoa(self):
        with self.assertRaises(oz.ZdrVirhe) as e:
            oz.rakenna_runko("m/x", [{"role": "user", "content": "salainen"}], yksityinen_chat=True)
        self.assertNotIn("salainen", str(e.exception))

    def test_ei_openrouter_ai_viittausta_moduulin_ulkopuolella(self):
        """Staattinen vartija: osoite saa olla vain tassa moduulissa, ei muualla aivo/-kansiossa."""
        haettu = "openrouter" + ".ai"
        osumat = []
        for p in sorted(AIVO.rglob("*")):
            if not p.is_file() or p.name == "openrouter_zdr.py" or p.name.startswith("test_"):
                continue
            if p.suffix in (".pyc", ".pyo"):
                continue
            try:
                teksti = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if haettu in teksti.lower():
                osumat.append(str(p.relative_to(AIVO)))
        self.assertEqual(osumat, [])

    def test_moduuli_ei_tee_verkkokutsua_eika_lue_avaimia(self):
        """AST-vartija: tarkistaa tuonnit ja kutsut, ei merkkijonoja (docstring ei kaada)."""
        puu = ast.parse((AIVO / "openrouter_zdr.py").read_text(encoding="utf-8"))
        kielletyt_moduulit = {"urllib", "requests", "httpx", "aiohttp", "socket", "http",
                              "subprocess", "os", "ssl", "ftplib", "smtplib", "importlib"}
        kielletyt_nimet = {"open", "getenv", "environ", "eval", "exec", "__import__"}
        loydot = []
        for n in ast.walk(puu):
            if isinstance(n, ast.Import):
                loydot += [a.name for a in n.names if a.name.split(".")[0] in kielletyt_moduulit]
            elif isinstance(n, ast.ImportFrom):
                if (n.module or "").split(".")[0] in kielletyt_moduulit:
                    loydot.append(n.module)
            elif isinstance(n, ast.Name) and n.id in kielletyt_nimet:
                loydot.append(n.id)
            elif isinstance(n, ast.Attribute) and n.attr in kielletyt_nimet:
                loydot.append(n.attr)
        self.assertEqual(loydot, [])


# Tulos 2026-11-19-50: nama 14 suljettua mallia (ei `hugging_face_id`:ta OpenRouterin mallilistalla) paasivat
# etuliitteen `qwen/qwen3` lapi.
SULJETUT_19_50 = (
    "qwen/qwen3.8-max-prime", "qwen/qwen3.8-omni-flash", "qwen/qwen3.8-max-0902", "qwen/qwen3.8-max-0902:batch",
    "qwen/qwen3.7-flash", "qwen/qwen3.7-plus", "qwen/qwen3.7-plus:batch", "qwen/qwen3.7-max",
    "qwen/qwen3.5-plus-20260420", "qwen/qwen3.6-flash", "qwen/qwen3.6-plus", "qwen/qwen3.5-flash-02-23",
    "qwen/qwen3.5-plus-02-15", "qwen/qwen3-coder-flash",
)
TUOTANTOMALLI = "qwen/qwen3-235b-a22b-2507"


class AvoinMalliTarkkaTesti(unittest.TestCase):
    def test_19_50_suljetut_hylataan(self):
        self.assertEqual(len(SULJETUT_19_50), 14)
        for m in SULJETUT_19_50:
            with self.subTest(m=m):
                self.assertFalse(oz.avoin_malli(m))
                with self.assertRaises(oz.ZdrVirhe):
                    oz.rakenna_runko(m, MSG, yksityinen_chat=True)

    def test_batch_hylataan(self):
        self.assertIn(":batch", oz.KIELLETYT_LIITTEET)
        for m in (TUOTANTOMALLI + ":batch", "qwen/qwen3.8-27b:batch", "meta-llama/llama-3.3-70b-instruct:batch"):
            with self.subTest(m=m):
                self.assertFalse(oz.avoin_malli(m))

    def test_kaikki_liitteet_ja_ketjut_hylataan(self):
        for liite in oz.KIELLETYT_LIITTEET + (":nitro:exacto", ":floor:batch", ":tuntematon"):
            with self.subTest(liite=liite):
                self.assertFalse(oz.avoin_malli(TUOTANTOMALLI + liite))

    def test_tuotantomalli_ja_listan_mallit_kelpaavat(self):
        self.assertIn(TUOTANTOMALLI, oz.AVOIMET_MALLIT)
        for m in oz.AVOIMET_MALLIT:
            with self.subTest(m=m):
                self.assertTrue(oz.avoin_malli(m))
        r = oz.rakenna_runko(TUOTANTOMALLI, MSG, yksityinen_chat=True)
        self.assertEqual((r["provider"]["zdr"], r["provider"]["data_collection"]), (True, "deny"))

    def test_etuliite_tai_jatke_ei_riita(self):
        for m in ("qwen/qwen3", "qwen/qwen3-", "qwen/qwen3.9-max", "meta-llama/llama-3", "deepseek/deepseek-r1",
                  TUOTANTOMALLI + "-max", TUOTANTOMALLI + "x", "qwen/qwen3-32b\n", " qwen/qwen3-32b"):
            with self.subTest(m=m):
                self.assertFalse(oz.avoin_malli(m))

    def test_lista_on_tarkka_ja_jokaisella_kommentti(self):
        """Jokainen sallittu malli on tarkka tunniste ja sen rivilla on hugging_face_id-kommentti."""
        teksti = Path(oz.__file__).read_text(encoding="utf-8")
        self.assertEqual(len(set(oz.AVOIMET_MALLIT)), len(oz.AVOIMET_MALLIT))
        for m in oz.AVOIMET_MALLIT:
            with self.subTest(m=m):
                self.assertTrue(oz._MALLI_RE.fullmatch(m))
                rivi = next(r for r in teksti.splitlines() if r.strip().startswith('"%s",' % m))
                self.assertRegex(rivi.split("#", 1)[1].strip(), r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


if __name__ == "__main__":
    unittest.main()
