"""Ajattelun kilpailutus: feikkimallit, ei verkkoa eika oikeaa claudea."""
import json
import random
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from claudejono import kilpailu, kulutus

TZ = kulutus.TZ
NYT = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)
ENV = {"CLAUDEJONO_KAYTTAJA": "claudejono"}      # ilman kayttajaa claude-kutsua ei tehda
KYSYMYS = "Kilpailu: opus, openrouter:deepseek-pro, openrouter:qwen-coder\nLaji: arkkitehtuuri\nMika on suurin kitkakohta?"


def tuomio_json(voittaja="A", pisteet=None, etiketit=("A", "B", "C")):
    p = {e: {k: 3 for k in kilpailu.KRITEERIT} for e in etiketit}
    p[voittaja] = {k: 5 for k in kilpailu.KRITEERIT}
    p.update(pisteet or {})
    return json.dumps({"pisteet": p, "voittaja": voittaja, "perustelu": "Paras perusteltu.",
                       "tiivistelmat": {e: "tiivistelma " + e for e in etiketit}})


class Feikki:
    """Feikki suorita: tuomari tunnistetaan kehotteesta, muut ovat kilpailijoita. Tallentaa kutsut."""
    def __init__(self, tuomio=None, kilpailija_ok=True):
        self.kutsut = []
        self.tuomio = tuomio
        self.ok = kilpailija_ok

    def __call__(self, kom, **kw):
        self.kutsut.append((kom, kw))
        kehote = kom[kom.index("-p") + 1]
        if "riippumaton tuomari" in kehote:
            etiketit = [e for e in "ABC" if "--- VASTAUS %s ---" % e in kehote]
            teksti = self.tuomio if self.tuomio is not None else tuomio_json("A", etiketit=etiketit)
        else:
            teksti = "opuksen vastaus" if self.ok else ""
        d = {"subtype": "success", "is_error": not teksti, "result": teksti, "usage": {"input_tokens": 5, "output_tokens": 7}, "total_cost_usd": 0.01}
        return subprocess.CompletedProcess(kom, 0, json.dumps(d), "")


class FeikkiLitellm:
    def __init__(self, usd=0.02, ok=True):
        self.kutsut = []
        self.usd = usd
        self.ok = ok

    def __call__(self, alias, teksti, env):
        self.kutsut.append((alias, teksti))
        return (True, "litellm-vastaus " + str(len(alias)), {"tokenit_in": 10, "tokenit_out": 20, "usd": self.usd}) if self.ok else (False, "litellm_http_500", {})


class Pohja(unittest.TestCase):
    def setUp(self):
        self.tila = Path(tempfile.mkdtemp())

    def rivit(self, tiedosto):
        p = self.tila / tiedosto
        return [json.loads(r) for r in p.read_text().splitlines()] if p.exists() else []


class Syote(unittest.TestCase):
    def test_kentta_ja_osallistujat(self):
        m, virhe = kilpailu.osallistujat(kilpailu.kentta(KYSYMYS))
        self.assertIsNone(virhe)
        self.assertEqual([x.tunniste for x in m], ["opus", "openrouter:deepseek-pro", "openrouter:qwen-coder"])
        self.assertIsNone(kilpailu.kentta("tekstin seassa Kilpailu: opus, sonnet"))

    def test_kelvottomat_hylataan_eika_lista_laajene(self):
        for arvo in ("opus", "opus, opus", "opus, sonnet, deepseek-pro, qwen-coder", "opus, openrouter:kimi",
                     "opus, openrouter:deepseek", "opus, gpt", "", None):
            m, virhe = kilpailu.osallistujat(arvo)
            self.assertEqual(m, [], arvo)
            self.assertTrue(virhe, arvo)
            self.assertNotIn("kimi", virhe)   # virhe ei toista arvoa

    def test_laji_ja_kysymys(self):
        self.assertEqual(kilpailu.laji("2026-11-04-01-x.md", "Laji: strategia"), "strategia")
        self.assertEqual(kilpailu.laji("2026-11-04-01-arkkitehtuuri-x.md", ""), "arkkitehtuuri")
        self.assertEqual(kilpailu.laji("2026-11-04-01-x.md", "Laji: salainen"), "muu")
        self.assertEqual(kilpailu.kysymys(KYSYMYS), "Mika on suurin kitkakohta?")

    def test_katto_env(self):
        self.assertEqual(kilpailu.katto_usd({}), 1.0)
        self.assertEqual(kilpailu.katto_usd({kilpailu.KATTO_ENV: "0.25"}), 0.25)
        self.assertEqual(kilpailu.katto_usd({kilpailu.KATTO_ENV: "roskaa"}), 1.0)
        self.assertEqual(kilpailu.katto_usd({kilpailu.KATTO_ENV: "nan"}), 1.0)
        self.assertEqual(kilpailu.katto_usd({kilpailu.KATTO_ENV: "-3"}), 0.0)


class Kulukatto(Pohja):
    def test_laskee_vain_openrouterin_kilpailu_ja_otoskulut_tanaan(self):
        k = lambda malli, laji, usd, aika=NYT: kulutus.kirjaa(self.tila, malli, "t", 1, laji, True, 1, {"usd": usd} if usd is not None else {}, aika)
        k("openrouter:deepseek-pro", "kilpailu", 0.2)
        k("openrouter:deepseek-flash", "otos", 0.1)
        k("opus", "kilpailu", 5.0)                                   # Claude ei kuulu OpenRouter-kattoon
        k("openrouter:deepseek-flash", "tehtava", 3.0)               # tavallinen OpenRouter-tehtava ei kuulu tahan kattoon
        k("openrouter:deepseek-pro", "kilpailu", 9.0, NYT - timedelta(days=1))   # eilinen
        k("openrouter:qwen-coder", "kilpailu", None)                 # kustannus puuttuu -> varovainen arvio
        self.assertAlmostEqual(kilpailu.kulutettu_tanaan(self.tila, NYT), 0.3 + kilpailu.KULU_ARVIO_USD, places=6)

    def test_katon_tayttyessa_vain_clauden_mallit(self):
        m, _ = kilpailu.osallistujat("opus, openrouter:deepseek-pro, openrouter:qwen-coder")
        valittu, huomio = kilpailu.rajaa_katolla(m, 0.5, 1.0)
        self.assertEqual(len(valittu), 3)
        self.assertIsNone(huomio)
        valittu, huomio = kilpailu.rajaa_katolla(m, 1.0, 1.0)
        self.assertEqual([x.tunniste for x in valittu], ["opus", "sonnet"])
        self.assertIn("katto", huomio)
        m2, _ = kilpailu.osallistujat("sonnet, openrouter:deepseek-pro")
        self.assertEqual([x.tunniste for x in kilpailu.rajaa_katolla(m2, 2.0, 1.0)[0]], ["sonnet", "opus"])


class ClaudeAjo(unittest.TestCase):
    def test_ei_tyokaluja_oikea_malli_ja_salaisuudet_pois(self):
        f = Feikki()
        env = {**ENV, "PATH": "/usr/bin", "CLAUDEJONO_LITELLM_KEY": "x", "YOJONO_TELEGRAM_TOKEN": "y", "GIT_SSH_COMMAND": "z", "CLAUDEJONO_CLAUDE": "/k/claude"}
        ok, teksti, luvut = kilpailu.claude_ajo("opus", "kysy", env, f)
        kom, kw = f.kutsut[0]
        self.assertTrue(ok)
        self.assertEqual(kom[:4], [kilpailu.RUNUSER, "-u", "claudejono", "--"])
        self.assertEqual(kom[4], "/k/claude")
        self.assertEqual(kom[kom.index("--tools") + 1], "")
        self.assertEqual(kom[kom.index("--model") + 1], "opus")
        self.assertEqual(set(kw["env"]), {"PATH", "HOME", "USER", "LOGNAME", "LANG"})     # ei salaisuuksia, ei CLAUDEJONO_*-muuttujia
        self.assertEqual(luvut["tokenit_out"], 7)

    def test_kayttaja_ajetaan_runuserilla_ja_kelvoton_hylataan(self):
        f = Feikki()
        kilpailu.claude_ajo("sonnet", "kysy", {"CLAUDEJONO_KAYTTAJA": "claudejono"}, f)
        self.assertEqual(f.kutsut[0][0][:4], [kilpailu.RUNUSER, "-u", "claudejono", "--"])
        n = len(f.kutsut)
        for huono in ("root", "", "Bad Name", "a;b"):
            ok, teksti, _ = kilpailu.claude_ajo("sonnet", "kysy", {"CLAUDEJONO_KAYTTAJA": huono}, f)
            self.assertEqual((ok, teksti), (False, "claude_kayttaja"))
        self.assertEqual(len(f.kutsut), n)       # kieltaytyminen ei aja mitaan

    def test_kayttajaton_ajo_kieltaytyy_eika_aja_lasta(self):
        f = Feikki()
        self.assertEqual(kilpailu.claude_ajo("opus", "kysy", {}, f)[:2], (False, "claude_kayttaja"))
        self.assertEqual(kilpailu.claude_ajo("opus", "kysy", {"PATH": "/usr/bin"}, f)[:2], (False, "claude_kayttaja"))
        self.assertEqual(f.kutsut, [])

    def test_uid_nolla_hylataan_nimesta_riippumatta(self):
        class Pw:
            pw_uid = 0
        f = Feikki()
        orig = kilpailu.pwd.getpwnam
        kilpailu.pwd.getpwnam = lambda nimi: Pw()
        try:
            self.assertEqual(kilpailu.claude_ajo("opus", "k", {"CLAUDEJONO_KAYTTAJA": "toor"}, f)[:2], (False, "claude_kayttaja"))
        finally:
            kilpailu.pwd.getpwnam = orig
        self.assertEqual(f.kutsut, [])
        self.assertEqual(kilpailu.claude_ajo("gpt", "kysy", {}, f)[:2], (False, "kilpailu_claude_malli"))

    def test_aikakatkaisu_ja_virhe_palauttavat_vain_tyypin(self):
        def aikakatkaisu(kom, **kw):
            raise subprocess.TimeoutExpired(kom, 1)

        def puuttuu(kom, **kw):
            raise FileNotFoundError("salainen-polku")
        self.assertEqual(kilpailu.claude_ajo("opus", "k", ENV, aikakatkaisu)[:2], (False, "claude_aikakatkaisu"))
        self.assertEqual(kilpailu.claude_ajo("opus", "k", ENV, puuttuu)[:2], (False, "claude_FileNotFoundError"))


class Tuomio(unittest.TestCase):
    def test_kelvollinen(self):
        t = kilpailu.lue_tuomio("T\u00e4ss\u00e4:\n" + tuomio_json("B", etiketit=("A", "B")) + "\nloppu", ["A", "B"])
        self.assertEqual(t["voittaja"], "B")
        self.assertEqual(t["pisteet"]["B"]["oikeellisuus"], 5)

    def test_kelvottomat(self):
        hyva = json.loads(tuomio_json("A", etiketit=("A", "B")))
        def muunna(f):
            d = json.loads(json.dumps(hyva))
            f(d)
            return json.dumps(d)
        for teksti in ("ei jsonia", "{rikki", "[]",
                       muunna(lambda d: d["pisteet"].pop("B")),
                       muunna(lambda d: d["pisteet"]["A"].update(oikeellisuus=6)),
                       muunna(lambda d: d["pisteet"]["A"].update(riskit=0)),
                       muunna(lambda d: d["pisteet"]["A"].update(riskit=True)),
                       muunna(lambda d: d["pisteet"]["A"].update(riskit="5")),
                       muunna(lambda d: d.update(voittaja="C")),
                       muunna(lambda d: d["pisteet"]["A"].pop("hyoty"))):
            self.assertIsNone(kilpailu.lue_tuomio(teksti, ["A", "B"]), teksti[:60])


class Kilpailu(Pohja):
    def aja(self, teksti=KYSYMYS, f=None, l=None, env=None, seed=1):
        f, l = f or Feikki(), l or FeikkiLitellm()
        t = kilpailu.kilpaile(self.tila, "2026-11-04-01-arkkitehtuuri-kitka.md", teksti, {**ENV, **(env or {})}, f, l, NYT, random.Random(seed))
        return t, f, l

    def test_koko_kilpailu(self):
        t, f, l = self.aja()
        self.assertEqual(t["tila"], "valmis")
        self.assertEqual(len(f.kutsut), 2)         # opus-kilpailija + tuomari
        self.assertEqual(len(l.kutsut), 2)
        # kaikki kilpailijat saivat saman kysymyksen, eivat toistensa nimia
        self.assertTrue(all("Mika on suurin kitkakohta?" in k for _, k in l.kutsut))
        self.assertTrue(all("Kilpailu:" not in k and "deepseek" not in k for _, k in l.kutsut))
        self.assertEqual(set(t["pisteet"]), {"opus", "openrouter:deepseek-pro", "openrouter:qwen-coder"})
        self.assertIn(t["voittaja"], t["pisteet"])
        self.assertEqual(t["pisteet"][t["voittaja"]]["yhteensa"], 25)
        self.assertEqual(len(t["tiivistelmat"]), 2)
        self.assertNotIn(t["voittaja"], t["tiivistelmat"])
        self.assertEqual(t["vastaus"].split()[0], "opuksen" if t["voittaja"] == "opus" else "litellm-vastaus")

    def test_tuomari_ei_nae_nimia_ja_jarjestys_satunnainen(self):
        jarjestykset = set()
        for seed in range(12):
            t, f, l = self.aja(seed=seed)
            kehote = [k for k, _ in f.kutsut if "riippumaton tuomari" in k[k.index("-p") + 1]][0]
            kehote = kehote[kehote.index("-p") + 1]
            for nimi in ("opus", "deepseek", "qwen", "openrouter"):
                self.assertNotIn(nimi, kehote.replace("riippumaton", ""))
            jarjestykset.add(tuple(kehote.split("--- VASTAUS A ---\n")[1].split("\n")[0:1]))
            tj = [k for k in f.kutsut if k[0][k[0].index("-p") + 1].startswith("Olet riippumaton")][0]
            self.assertEqual(tj[0][tj[0].index("--tools") + 1], "")     # tuomarilla ei tyokaluja
            self.assertEqual(tj[0][tj[0].index("--model") + 1], "opus")
        self.assertGreater(len(jarjestykset), 1)

    def test_pisteet_ja_kulut_kirjataan(self):
        t, f, l = self.aja()
        rivit = self.rivit(kilpailu.PISTEET)
        self.assertEqual(len(rivit), 3)
        self.assertEqual(sum(r["voitto"] for r in rivit), 1)
        self.assertTrue(all(r["laji"] == "arkkitehtuuri" and r["tuomari"] == "opus" and r["osallistujia"] == 3 for r in rivit))
        self.assertEqual({r["malli"] for r in rivit}, {"opus", "openrouter:deepseek-pro", "openrouter:qwen-coder"})
        kulut = self.rivit(kulutus.KULUTUS)
        self.assertEqual(sorted(r["laji"] for r in kulut), ["kilpailu", "kilpailu", "kilpailu", "kilpailu-tuomari"])
        self.assertAlmostEqual(kilpailu.kulutettu_tanaan(self.tila, NYT), 0.04)

    def test_katto_taynna_ei_kutsu_openrouteria(self):
        kulutus.kirjaa(self.tila, "openrouter:deepseek-pro", "x", 1, "kilpailu", True, 1, {"usd": 1.0}, NYT)
        t, f, l = self.aja()
        self.assertEqual(t["tila"], "valmis")
        self.assertEqual(l.kutsut, [])
        self.assertEqual(set(t["pisteet"]), {"opus", "sonnet"})
        self.assertIn("katto", t["huomio"])

    def test_katto_env_toimii(self):
        t, f, l = self.aja(env={kilpailu.KATTO_ENV: "0.0"})
        self.assertEqual(l.kutsut, [])
        t, f, l = self.aja(env={kilpailu.KATTO_ENV: "5"})
        self.assertEqual(len(l.kutsut), 2)

    def test_yksi_kilpailija_epaonnistuu_muut_pisteytetaan(self):
        t, f, l = self.aja(l=FeikkiLitellm(ok=False))
        self.assertEqual(t["tila"], "epaonnistui")          # vain opus onnistui -> ei kilpailua
        self.assertEqual(self.rivit(kilpailu.PISTEET), [])
        l2 = FeikkiLitellm()
        def osittain(alias, teksti, env):
            return (False, "litellm_http_500", {}) if alias == "qwen-coder" else l2(alias, teksti, env)
        t = kilpailu.kilpaile(self.tila, "x", KYSYMYS, ENV, Feikki(), osittain, NYT, random.Random(1))
        self.assertEqual(t["tila"], "valmis")
        self.assertEqual(t["epaonnistuneet"], ["openrouter:qwen-coder"])
        rivit = self.rivit(kilpailu.PISTEET)
        self.assertEqual(len(rivit), 3)
        self.assertEqual([r["ok"] for r in rivit if r["malli"] == "openrouter:qwen-coder"], [False])

    def test_huono_tuomio_ei_kirjaa_pisteita(self):
        t, f, l = self.aja(f=Feikki(tuomio="en osaa"))
        self.assertEqual(t["tila"], "epaonnistui")
        self.assertIn("tuomari", t["virhe"])
        self.assertEqual(self.rivit(kilpailu.PISTEET), [])
        self.assertTrue(any(r["laji"] == "kilpailu-tuomari" for r in self.rivit(kulutus.KULUTUS)))

    def test_kelvoton_rivi_ei_aja_mitaan(self):
        t, f, l = self.aja(teksti="Kilpailu: opus, openrouter:kimi\nkysymys")
        self.assertEqual(t["tila"], "epaonnistui")
        self.assertEqual((f.kutsut, l.kutsut), ([], []))

    def test_muotoilu(self):
        t, _, _ = self.aja()
        teksti = kilpailu.muotoile(t)
        for osa in ("Voittaja", "| Malli |", "Tuomarin perustelu", "Muiden vastausten tiivistelm", "Voittajan vastaus"):
            self.assertIn(osa, teksti)
        self.assertIn("ep\u00e4onnistui", kilpailu.muotoile({"tila": "epaonnistui", "virhe": "x"}))


class Otos(Pohja):
    def test_joka_viides(self):
        self.assertEqual([kilpailu.otos_vuorossa(self.tila) for _ in range(10)], [False] * 4 + [True] + [False] * 4 + [True])
        (self.tila / kilpailu.OTOS_LASKURI).write_text("roskaa")
        self.assertFalse(kilpailu.otos_vuorossa(self.tila))

    def test_vain_tuomarin_pisteet_kirjataan(self):
        f, l = Feikki(), FeikkiLitellm()
        rivit = kilpailu.otos(self.tila, "2026-11-04-02-x.md", "tee x", "sonnetin raportti", "sonnet", ENV, f, l, NYT, random.Random(2))
        self.assertEqual(len(rivit), 2)
        self.assertEqual({r["malli"] for r in rivit}, {"sonnet", kilpailu.OTOS_MALLI})
        self.assertTrue(all(r["laji"] == "otos" for r in rivit))
        self.assertEqual(sum(r["voitto"] for r in rivit), 1)
        self.assertEqual(len(f.kutsut), 1)                                # vain tuomari kutsui clauden
        self.assertEqual([a for a, _ in l.kutsut], ["deepseek-flash"])
        self.assertEqual(self.rivit(kilpailu.PISTEET), rivit)
        self.assertEqual(sorted(r["laji"] for r in self.rivit(kulutus.KULUTUS)), ["kilpailu-tuomari", "otos"])

    def test_katto_ja_virheet_ohittavat_otoksen(self):
        kulutus.kirjaa(self.tila, "openrouter:deepseek-flash", "x", 1, "otos", True, 1, {"usd": 2.0}, NYT)
        l = FeikkiLitellm()
        self.assertIsNone(kilpailu.otos(self.tila, "n", "t", "v", "sonnet", ENV, Feikki(), l, NYT))
        self.assertEqual(l.kutsut, [])
        t2 = Path(tempfile.mkdtemp())
        self.assertIsNone(kilpailu.otos(t2, "n", "t", "v", "sonnet", ENV, Feikki(), FeikkiLitellm(ok=False), NYT))
        self.assertIsNone(kilpailu.otos(t2, "n", "t", "", "sonnet", ENV, Feikki(), FeikkiLitellm(), NYT))
        self.assertFalse((t2 / kilpailu.PISTEET).exists())


class Varaus(Pohja):
    """Kutsukohtainen katto: varaus lukon alla ennen jokaista OpenRouter-kutsua."""
    ENV_KATTO = {**ENV, kilpailu.KATTO_ENV: "0.1"}       # mahtuu tasan kaksi arviota (2 x KULU_ARVIO_USD)

    def test_rinnakkaiset_varaukset_eivat_ylita_kattoa(self):
        tulos, portti = [], threading.Barrier(8)

        def saie():
            portti.wait()
            tulos.append(kilpailu.varaa(self.tila, self.ENV_KATTO, NYT))
        saikeet = [threading.Thread(target=saie) for _ in range(8)]
        [t.start() for t in saikeet]
        [t.join() for t in saikeet]
        self.assertEqual(len([t for t in tulos if t]), 2)
        self.assertEqual(len(set(t for t in tulos if t)), 2)

    def test_varaus_laskee_mukaan_kirjatun_kulutuksen_ja_vapautus_palauttaa_tilan(self):
        a = kilpailu.varaa(self.tila, self.ENV_KATTO, NYT)
        self.assertTrue(a)
        kulutus.kirjaa(self.tila, "openrouter:deepseek-pro", "x", 1, "kilpailu", True, 1, {"usd": 0.05}, NYT)
        self.assertIsNone(kilpailu.varaa(self.tila, self.ENV_KATTO, NYT))      # 0.05 kulutettu + 0.05 varattu + 0.05 arvio > 0.1
        kilpailu.vapauta(self.tila, a, NYT)
        self.assertTrue(kilpailu.varaa(self.tila, self.ENV_KATTO, NYT))        # 0.05 kulutettu + 0.05 arvio = 0.1, mahtuu
        self.assertIsNone(kilpailu.varaa(self.tila, self.ENV_KATTO, NYT))

    def test_vanhentunut_varaus_vapautuu(self):
        a = kilpailu.varaa(self.tila, {**ENV, kilpailu.KATTO_ENV: "0.05"}, NYT)
        self.assertTrue(a)
        self.assertIsNone(kilpailu.varaa(self.tila, {**ENV, kilpailu.KATTO_ENV: "0.05"}, NYT))
        myohemmin = NYT + timedelta(seconds=kilpailu.VARAUS_VANHENEE_S + 1)
        self.assertTrue(kilpailu.varaa(self.tila, {**ENV, kilpailu.KATTO_ENV: "0.05"}, myohemmin))

    def test_rikkinainen_varaustiedosto_ei_kaada_eika_ohita_kattoa(self):
        (self.tila / kilpailu.VARAUKSET).write_text("roskaa")
        self.assertTrue(kilpailu.varaa(self.tila, self.ENV_KATTO, NYT))
        self.assertIsNone(kilpailu.varaa(Path("/ei/ole/olemassa"), self.ENV_KATTO, NYT))    # levyvirhe -> ei kutsua

    def test_varaus_vapautuu_myos_kun_kutsu_kaatuu(self):
        def kaatuu(alias, teksti, env):
            raise ValueError("salainen")
        with self.assertRaises(ValueError):
            kilpailu.otos(self.tila, "n", "t", "v", "sonnet", ENV, Feikki(), kaatuu, NYT)
        self.assertEqual(json.loads((self.tila / kilpailu.VARAUKSET).read_text()), {})

    def test_samanaikainen_toinen_kilpailu_nakee_ensimmaisen_varaukset(self):
        """Kilpailu A pitaa kahta OpenRouter-kutsua kesken; sisakkainen kilpailu B ei saa ylittaa kattoa (0.1 = kaksi kutsua)."""
        sisa = {}
        molemmat_sisalla, b_valmis = threading.Barrier(2), threading.Event()
        syvyys = []

        def kutsu(alias, teksti, env):
            if syvyys:                                    # B:n kutsu: katon pitaisi estaa kaikki
                return True, "b-vastaus", {"usd": 0.05}
            molemmat_sisalla.wait(timeout=10)
            if alias == "deepseek-pro":                   # vain toinen saie ajaa B:n
                syvyys.append(1)
                t = kilpailu.kilpaile(self.tila, "b", KYSYMYS, self.ENV_KATTO, Feikki(), kutsu, NYT, random.Random(2))
                sisa["b"] = t
                b_valmis.set()
            b_valmis.wait(timeout=10)
            return True, "a-vastaus", {"usd": 0.05}
        a = kilpailu.kilpaile(self.tila, "a", KYSYMYS, self.ENV_KATTO, Feikki(), kutsu, NYT, random.Random(1))
        self.assertEqual(a["tila"], "valmis")
        b = sisa["b"]
        # B:ssa vain opus pystyi vastaamaan (OpenRouter-kutsut ohitettiin) -> alle kaksi vastausta, eika yhtaan b-vastausta
        self.assertEqual(b["tila"], "epaonnistui")
        self.assertIn("ohitettu", b["huomio"])
        kulut = [r for r in self.rivit(kulutus.KULUTUS) if r["malli"].startswith("openrouter:")]
        self.assertEqual(len(kulut), 2)
        self.assertLessEqual(sum(r["usd"] for r in kulut), 0.1 + 1e-9)


class Aivo(Pohja):
    def test_aivon_tehtava_ei_kilpaile_eika_otoksia(self):
        f, l = Feikki(), FeikkiLitellm()
        for nimi, teksti in (("2026-11-07-01-aivo-chat.md", KYSYMYS), ("x", KYSYMYS + "\nAivon muistiinpanot"), ("x", KYSYMYS + " AIVO")):
            t = kilpailu.kilpaile(self.tila, nimi, teksti, ENV, f, l, NYT, random.Random(1))
            self.assertEqual(t["tila"], "epaonnistui")
            self.assertIn("Aivon", t["virhe"])
            self.assertIsNone(kilpailu.otos(self.tila, nimi, teksti, "v", "sonnet", ENV, f, l, NYT))
        self.assertEqual((f.kutsut, l.kutsut), ([], []))
        self.assertFalse((self.tila / kilpailu.PISTEET).exists())
        self.assertFalse((self.tila / kulutus.KULUTUS).exists())

    def test_tunnistus_ei_osu_muihin_sanoihin(self):
        self.assertFalse(kilpailu.aivo_tehtava("2026-11-07-kaivo.md", "kaivon syvyys, paivo"))
        self.assertTrue(kilpailu.aivo_tehtava("", "kysymys Aivosta"))
        self.assertTrue(kilpailu.aivo_tehtava("aivo-chat", ""))


class Viikkokooste(Pohja):
    def test_viime_viikko_malleittain(self):
        maanantai = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)
        vk = datetime(2026, 11, 3, 12, 0, tzinfo=TZ)
        rivi = lambda aika, malli, yht, voitto, ok=True: {"aika": aika.isoformat(), "malli": malli, "yhteensa": yht, "voitto": voitto, "ok": ok}
        rivit = [rivi(vk, "opus", 22, True), rivi(vk, "openrouter:deepseek-pro", 18, False), rivi(vk, "opus", 20, False),
                 rivi(vk, "openrouter:deepseek-pro", 24, True), rivi(vk, "openrouter:qwen-coder", 0, False, False),
                 rivi(maanantai, "opus", 1, False)]   # kuluva viikko ei mukaan
        (self.tila / kilpailu.PISTEET).write_text("\n".join(json.dumps(r) for r in rivit) + "\nrikki\n")
        teksti = kilpailu.viikkokooste(self.tila, maanantai)
        self.assertIn("opus 2 kisaa, 1 voittoa, keskiarvo 21.0/25", teksti)
        self.assertIn("openrouter:deepseek-pro 2 kisaa, 1 voittoa, keskiarvo 21.0/25", teksti)
        self.assertIn("openrouter:qwen-coder 1 kisaa, 0 voittoa, keskiarvo -/25", teksti)
        self.assertIsNone(kilpailu.viikkokooste(Path(tempfile.mkdtemp()), maanantai))


if __name__ == "__main__":
    unittest.main()
