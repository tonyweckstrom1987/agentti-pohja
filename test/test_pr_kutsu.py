import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agentti import pr_kutsu as pk

TUNNUS = "github_pat_" + "A1b2C3d4E5" * 4


def git(hak, *args):
    subprocess.run(["git", "-C", hak, "-c", "user.name=t", "-c", "user.email=t@e", *args], check=True,
                   capture_output=True)


def tee_repo(muutokset):
    """Luo repon: main (yksi tiedosto) ja haara yo/koe, jossa annetut muutokset (polku -> sisalto)."""
    tmp = tempfile.TemporaryDirectory()
    hak = tmp.name
    git(hak, "init", "-q", "-b", "main")
    with open(os.path.join(hak, "README.md"), "w") as f:
        f.write("a")
    git(hak, "add", "-A")
    git(hak, "commit", "-q", "-m", "alku")
    git(hak, "checkout", "-q", "-b", "yo/koe")
    for polku, sisalto in muutokset.items():
        os.makedirs(os.path.dirname(os.path.join(hak, polku)) or hak, exist_ok=True)
        with open(os.path.join(hak, polku), "w") as f:
            f.write(sisalto)
    git(hak, "add", "-A")
    git(hak, "commit", "-q", "--allow-empty", "-m", "muutos")
    return tmp


class Vastaus:
    def __init__(self, data):
        self._d = json.dumps(data).encode()

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SuojatutTest(unittest.TestCase):
    def test_suojatut(self):
        for p in (".github/workflows/ci.yml", "tools/automerge.py", "systemd/x.service", "isanta/a.py", "agentti/rajat.py",
                  "config.py", "a/.env", "x/github_token.txt", "y/api_key.py", "k/id_ed25519"):
            self.assertTrue(pk.suojattu(p), p)
        for p in ("README.md", "agentti/viikkoraportti.py", "test/test_a.py", "docs/kuvaus.md"):
            self.assertFalse(pk.suojattu(p), p)


class TarkistaTest(unittest.TestCase):
    def test_kelvollinen(self):
        with tee_repo({"docs/uusi.md": "x"}) as hak:
            self.assertEqual(pk.tarkista("agentti-pohja", hak, "yo/koe"), ["docs/uusi.md"])

    def test_vaarat_repot_ja_haarat(self):
        with tee_repo({"a.md": "x"}) as hak:
            for repo in ("tuntematon-repo", "../x", ""):
                with self.assertRaises(pk.PrVirhe):
                    pk.tarkista(repo, hak, "yo/koe")
            for haara in ("main", "yo/", "yo/Iso", "feature/x", "yo/a b", "yo/../x", None):
                with self.assertRaises(pk.PrVirhe):
                    pk.tarkista("agentti-pohja", hak, haara)

    def test_suojattu_polku_estaa(self):
        with tee_repo({"docs/a.md": "x", ".github/workflows/x.yml": "y"}) as hak:
            with self.assertRaises(pk.PrVirhe) as ctx:
                pk.tarkista("agentti-pohja", hak, "yo/koe")
            self.assertIn(".github/workflows/x.yml", str(ctx.exception))

    def test_ei_muutoksia_ja_puuttuva_haara(self):
        with tee_repo({}) as hak:
            git(hak, "commit", "-q", "--allow-empty", "-m", "tyhja")
            with self.assertRaises(pk.PrVirhe):
                pk.tarkista("agentti-pohja", hak, "yo/koe")
            with self.assertRaises(pk.PrVirhe):
                pk.tarkista("agentti-pohja", hak, "yo/ei-ole")

    def test_liikaa_tiedostoja(self):
        with tee_repo({"f%d.md" % i: "x" for i in range(pk.MAX_TIEDOSTOJA + 1)}) as hak:
            with self.assertRaises(pk.PrVirhe):
                pk.tarkista("agentti-pohja", hak, "yo/koe")

    def test_ei_git_repo(self):
        with tempfile.TemporaryDirectory() as hak:
            with self.assertRaises(pk.PrVirhe):
                pk.tarkista("agentti-pohja", hak, "yo/koe")


class TunnusTest(unittest.TestCase):
    def kirjoita(self, teksti):
        tmp = tempfile.NamedTemporaryFile("w", delete=False)
        tmp.write(teksti)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_kelvollinen_ja_rivinvaihto(self):
        self.assertEqual(pk.lue_tunnus(self.kirjoita(TUNNUS + "\n")), TUNNUS)

    def test_virheet_eivat_vuoda_tunnusta(self):
        for sisalto in ("", "roskaa", "github_pat_lyhyt"):
            with self.assertRaises(pk.PrVirhe) as ctx:
                pk.lue_tunnus(self.kirjoita(sisalto))
            self.assertNotIn(sisalto or "tyhja-ei-osu", str(ctx.exception).replace("tunnustiedoston muoto ei kelpaa", ""))
        with self.assertRaises(pk.PrVirhe):
            pk.lue_tunnus("/ei/ole/olemassa")

    def test_botti_kayttaja(self):
        self.assertEqual(pk.botti_kayttaja({}), pk.OLETUS_BOTTI)
        self.assertEqual(pk.botti_kayttaja({"YOJONO_BOT_KAYTTAJA": "abc-1"}), "abc-1")
        for huono in ("-a", "a-", "a b", "a/b", "", "x" * 40):
            with self.assertRaises(pk.PrVirhe):
                pk.botti_kayttaja({"YOJONO_BOT_KAYTTAJA": huono})


class PushTest(unittest.TestCase):
    def test_push_ilman_forcea_ja_tunnus_vain_askpassissa(self):
        nahty = {}

        def suorita(cmd, **kw):
            nahty["cmd"], nahty["env"] = cmd, kw["env"]
            skripti = kw["env"]["GIT_ASKPASS"]
            nahty["tila"] = stat.S_IMODE(os.stat(skripti).st_mode)
            with open(skripti) as f:
                nahty["skripti"] = f.read()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        pk.pushaa("/h", "agentti-pohja", "yo/koe", "botti", "/etc/tunnus", suorita=suorita)
        cmd = " ".join(nahty["cmd"])
        self.assertNotIn("--force", cmd)
        self.assertNotIn("+refs", cmd)
        self.assertIn("refs/heads/yo/koe:refs/heads/yo/koe", cmd)
        self.assertIn("https://github.com/botti/agentti-pohja.git", cmd)
        self.assertIn("core.hooksPath=/dev/null", cmd)
        self.assertEqual(nahty["tila"], 0o700)
        self.assertIn("/etc/tunnus", nahty["skripti"])
        self.assertNotIn(TUNNUS, cmd + json.dumps(nahty["env"]) + nahty["skripti"])
        self.assertEqual(nahty["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_askpass_poistuu_ja_virhe_nostaa(self):
        polut = []

        def suorita(cmd, **kw):
            polut.append(kw["env"]["GIT_ASKPASS"])
            return SimpleNamespace(returncode=1, stdout="", stderr="remote: Permission denied")
        with self.assertRaises(pk.PrVirhe) as ctx:
            pk.pushaa("/h", "agentti-pohja", "yo/koe", "botti", "/etc/tunnus", suorita=suorita)
        self.assertIn("Permission denied", str(ctx.exception))
        self.assertFalse(os.path.exists(polut[0]))

    def test_ymparistosta_poistetaan_github_tunnukset(self):
        os.environ["GITHUB_TOKEN"] = "x"
        self.addCleanup(os.environ.pop, "GITHUB_TOKEN", None)
        nahty = {}

        def suorita(cmd, **kw):
            nahty.update(kw["env"])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        pk.pushaa("/h", "agentti-pohja", "yo/koe", "botti", "/t", suorita=suorita)
        self.assertNotIn("GITHUB_TOKEN", nahty)


class PrTest(unittest.TestCase):
    def test_workflow_oikeusvirhe_selitetaan(self):
        def suorita(cmd, **kw):
            return SimpleNamespace(returncode=1, stdout="", stderr="refusing to allow a Personal Access Token to create or update workflow `.github/workflows/a.yml` without `workflow` scope")
        with self.assertRaises(pk.PrVirhe) as ctx:
            pk.pushaa("/h", "agentti-pohja", "yo/koe", "botti", "/t", suorita=suorita, sync="HTTP 403")
        teksti = str(ctx.exception)
        self.assertIn("Sync fork", teksti)
        self.assertIn("HTTP 403", teksti)
        self.assertNotIn("onko fork olemassa", teksti)

    def test_oletuksena_synkronointi_on_ok_viestissa(self):
        def suorita(cmd, **kw):
            return SimpleNamespace(returncode=1, stdout="", stderr="refusing ... `workflow` scope")
        with self.assertRaises(pk.PrVirhe) as ctx:
            pk.pushaa("/h", "agentti-pohja", "yo/koe", "botti", "/t", suorita=suorita)
        self.assertIn("API-synkronointi: ok.", str(ctx.exception))

    def test_synkkavirhe_palautetaan_koodina(self):
        def avaa(pyynto, timeout):
            raise urllib.error.HTTPError(pyynto.full_url, 422, "x", {}, None)
        self.assertEqual(pk.paivita_fork("agentti-pohja", "botti", TUNNUS, avaa=avaa), "HTTP 422")
        self.assertIs(pk.paivita_fork("agentti-pohja", "botti", TUNNUS, avaa=lambda p, timeout: Vastaus({})), True)

    def test_pyynto(self):
        saatu = {}

        def avaa(pyynto, timeout):
            saatu["url"], saatu["h"] = pyynto.full_url, dict(pyynto.header_items())
            saatu["body"] = json.loads(pyynto.data.decode())
            return Vastaus({"html_url": "https://github.com/tonyweckstrom1987/agentti-pohja/pull/99"})
        osoite = pk.avaa_pr("agentti-pohja", "yo/koe", "botti", "Otsikko\nrivi", "kuvaus", TUNNUS, avaa=avaa)
        self.assertTrue(osoite.endswith("/pull/99"))
        self.assertEqual(saatu["url"], "https://api.github.com/repos/tonyweckstrom1987/agentti-pohja/pulls")
        self.assertEqual(saatu["body"]["head"], "botti:yo/koe")
        self.assertEqual(saatu["body"]["base"], "main")
        self.assertTrue(saatu["body"]["title"].startswith("[yojono] Otsikko"))
        self.assertNotIn("\n", saatu["body"]["title"])
        self.assertIn("Tony katselmoi", saatu["body"]["body"])
        self.assertFalse(saatu["body"]["maintainer_can_modify"])
        self.assertEqual(saatu["h"]["Authorization"], "Bearer " + TUNNUS)

    def test_otsikko_ja_kuvaus_rajataan(self):
        saatu = {}

        def avaa(pyynto, timeout):
            saatu.update(json.loads(pyynto.data.decode()))
            return Vastaus({"html_url": "https://github.com/x/y/pull/1"})
        pk.avaa_pr("agentti-pohja", "yo/koe", "b", "o" * 500, "k" * 9000, TUNNUS, avaa=avaa)
        self.assertLessEqual(len(saatu["title"]), len("[yojono] ") + pk.OTSIKKO_MAX)
        self.assertLessEqual(len(saatu["body"]), pk.KUVAUS_MAX + len(pk.ALAKUULO))

    def test_virheet(self):
        def http_virhe(pyynto, timeout):
            raise urllib.error.HTTPError(pyynto.full_url, 422, "x", {}, io.BytesIO(b"{}"))

        def verkkovirhe(pyynto, timeout):
            raise urllib.error.URLError("ei yhteytta")
        for avaa in (http_virhe, verkkovirhe, lambda p, timeout: Vastaus({"html_url": "http://evil/x"}),
                     lambda p, timeout: Vastaus({})):
            with self.assertRaises(pk.PrVirhe) as ctx:
                pk.avaa_pr("agentti-pohja", "yo/koe", "b", "o", "k", TUNNUS, avaa=avaa)
            self.assertNotIn(TUNNUS, str(ctx.exception))


class PaivarajaTest(unittest.TestCase):
    def test_raja(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "tila.json")
            for _ in range(pk.RAJA_PAIVASSA):
                self.assertTrue(pk.varaa_paikka(polku, "2026-10-21"))
            self.assertFalse(pk.varaa_paikka(polku, "2026-10-21"))
            self.assertTrue(pk.varaa_paikka(polku, "2026-10-22"))

    def test_rikkinainen_tila_aloittaa_alusta(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "tila.json")
            with open(polku, "w") as f:
                f.write("roskaa")
            self.assertTrue(pk.varaa_paikka(polku, "2026-10-21"))


class KokoketjuTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.tunnus = os.path.join(self.d.name, "tunnus")
        with open(self.tunnus, "w") as f:
            f.write(TUNNUS)
        self.env = {"YOJONO_BOT_TUNNUS": self.tunnus, "YOJONO_PR_TILA": os.path.join(self.d.name, "tila.json")}

    def test_ketju_onnistuu(self):
        komennot = []

        def suorita(cmd, **kw):
            komennot.append(cmd)
            if "push" in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return subprocess.run(cmd, **kw)
        with tee_repo({"docs/a.md": "x"}) as hak:
            osoite = pk.luo_pr("agentti-pohja", hak, "yo/koe", "o", "k", env=self.env, suorita=suorita,
                               avaa=lambda p, timeout: Vastaus([] if p.get_method() == "GET" else {"html_url": "https://github.com/a/b/pull/1"}))
        self.assertEqual(osoite, "https://github.com/a/b/pull/1")
        self.assertTrue(any("push" in c for c in komennot))

    def test_fork_paivitetaan_ennen_pushia_ja_virhe_ei_kaada(self):
        tapahtumat = []

        def suorita(cmd, **kw):
            if "push" in cmd:
                tapahtumat.append("push")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return subprocess.run(cmd, **kw)

        def avaa(pyynto, timeout):
            if pyynto.get_method() == "GET":
                return Vastaus([])
            if pyynto.full_url.endswith("/merge-upstream"):
                tapahtumat.append("sync")
                self.assertEqual(json.loads(pyynto.data.decode()), {"branch": "main"})
                self.assertIn("/repos/botti-x/agentti-pohja/", pyynto.full_url)
                raise urllib.error.URLError("ei verkkoa")
            tapahtumat.append("pr")
            return Vastaus({"html_url": "https://github.com/a/b/pull/1"})
        self.env["YOJONO_BOT_KAYTTAJA"] = "botti-x"
        with tee_repo({"docs/a.md": "x"}) as hak:
            pk.luo_pr("agentti-pohja", hak, "yo/koe", "o", "k", env=self.env, suorita=suorita, avaa=avaa)
        self.assertEqual(tapahtumat, ["sync", "push", "pr"])

    def test_suojattu_polku_ei_paase_pushiin_eika_kuluta_paivarajaa(self):
        komennot = []

        def suorita(cmd, **kw):
            komennot.append(cmd)
            return subprocess.run(cmd, **kw)
        with tee_repo({"tools/x.py": "x"}) as hak:
            with self.assertRaises(pk.PrVirhe):
                pk.luo_pr("agentti-pohja", hak, "yo/koe", "o", "k", env=self.env, suorita=suorita)
        self.assertFalse(any("push" in c for c in komennot))
        self.assertFalse(os.path.exists(self.env["YOJONO_PR_TILA"]))

    def test_puuttuva_tunnus_ei_kuluta_paivarajaa(self):
        self.env["YOJONO_BOT_TUNNUS"] = "/ei/ole"
        with tee_repo({"docs/a.md": "x"}) as hak:
            with self.assertRaises(pk.PrVirhe):
                pk.luo_pr("agentti-pohja", hak, "yo/koe", "o", "k", env=self.env)
        self.assertFalse(os.path.exists(self.env["YOJONO_PR_TILA"]))


def pr_lista(*kayttajat):
    return [{"number": i + 1, "user": {"login": k}} for i, k in enumerate(kayttajat)]


class AvoimetPrtTest(unittest.TestCase):
    def test_laskee_vain_botin_prt_ilman_tunnusta(self):
        pyynnot = []

        def avaa(p, timeout):
            pyynnot.append(p)
            return Vastaus(pr_lista("Agenttifirma-Bot", "tonyweckstrom1987", "agenttifirma-bot"))
        self.assertEqual(pk.avoimet_botin_prt("agentti-pohja", "agenttifirma-bot", avaa=avaa), [1, 3])
        self.assertIn("/repos/tonyweckstrom1987/agentti-pohja/pulls?state=open", pyynnot[0].full_url)
        self.assertIsNone(pyynnot[0].get_header("Authorization"))

    def test_alle_rajan_sallitaan_rajalla_estetaan(self):
        pk.tarkista_avoimet("r", "b", avaa=lambda p, timeout: Vastaus(pr_lista("b", "muu", "muu")))
        with self.assertRaises(pk.PrVirhe) as ctx:
            pk.tarkista_avoimet("r", "b", avaa=lambda p, timeout: Vastaus(pr_lista("b", "b")))
        self.assertIn("olemassa olevaan", str(ctx.exception))
        self.assertIn("#1, #2", str(ctx.exception))

    def test_raja_on_oletuksena_2(self):
        self.assertEqual(pk.RAJA_AVOIMIA_PR, 2)

    def test_lukuvirheet_estavat_pr_n(self):
        def http(p, timeout):
            raise urllib.error.HTTPError(p.full_url, 403, "x", {}, None)

        def verkko(p, timeout):
            raise urllib.error.URLError("ei")
        for avaa in (http, verkko, lambda p, timeout: Vastaus({"message": "x"})):
            with self.assertRaises(pk.PrVirhe):
                pk.avoimet_botin_prt("r", "b", avaa=avaa)

    def test_roskaa_listassa_ei_kaada(self):
        data = [1, None, {"user": None}, {"number": 5, "user": {"login": "b"}}]
        self.assertEqual(pk.avoimet_botin_prt("r", "b", avaa=lambda p, timeout: Vastaus(data)), [5])


class KokoketjuAvoimetTest(unittest.TestCase):
    def test_raja_taynna_ei_pushaa_eika_kuluta_paivarajaa(self):
        with tempfile.TemporaryDirectory() as d:
            tunnus = os.path.join(d, "tunnus")
            with open(tunnus, "w") as f:
                f.write(TUNNUS)
            env = {"YOJONO_BOT_TUNNUS": tunnus, "YOJONO_PR_TILA": os.path.join(d, "tila.json"), "YOJONO_BOT_KAYTTAJA": "botti-x"}
            komennot = []

            def suorita(cmd, **kw):
                komennot.append(cmd)
                return subprocess.run(cmd, **kw)
            with tee_repo({"docs/a.md": "x"}) as hak:
                with self.assertRaises(pk.PrVirhe):
                    pk.luo_pr("agentti-pohja", hak, "yo/koe", "o", "k", env=env, suorita=suorita,
                              avaa=lambda p, timeout: Vastaus(pr_lista("botti-x", "botti-x")))
            self.assertFalse(any("push" in c for c in komennot))
            self.assertFalse(os.path.exists(env["YOJONO_PR_TILA"]))


class MainTest(unittest.TestCase):
    def test_kayttovirhe_ja_tarkista(self):
        self.assertEqual(pk.main([]), 2)
        self.assertEqual(pk.main(["tarkista", "tuntematon-repo", "/x", "yo/a"]), 1)
        with tee_repo({"docs/a.md": "x"}) as hak:
            self.assertEqual(pk.main(["tarkista", "agentti-pohja", hak, "yo/koe"]), 0)


class RajatutPolutTest(unittest.TestCase):
    """Tonyn paatos 31.10.2026: kauppatiimissa vapaat kadet paperilla, oikean rahan lukot suojattu."""

    def test_kauppatiimi_strategia_ja_testit_sallitaan(self):
        with tee_repo({"test_uusi.py": "x", "README.md": "y", "ab_tilasto.py": "z", "paperi_asetukset.py": "w", "retro.py": "v"}) as hak:
            self.assertEqual(sorted(pk.tarkista("kauppatiimi", hak, "yo/koe")),
                             ["README.md", "ab_tilasto.py", "paperi_asetukset.py", "retro.py", "test_uusi.py"])

    def test_kauppatiimi_lukot_hylataan(self):
        for polku in ("riskivahti.py", "salkku.py", "kirjanpito.py", "asetukset.py", "rajat.py", "config.py", "kauppa-ajo.timer",
                      "kauppa.service", "data/havainnot.md", "historia.db", "kokeilut.sqlite3", "sub/salkku.py", "./riskivahti.py",
                      "test_a/../salkku.py", ".github/workflows/ci.yml"):
            with self.subTest(polku=polku), tee_repo({"test_ok.py": "x", polku: "y"}) as hak:
                with self.assertRaises(pk.PrVirhe):
                    pk.tarkista("kauppatiimi", hak, "yo/koe")

    def test_rajaus_ei_koske_muita_repoja(self):
        with tee_repo({"salkku.py": "x"}) as hak:
            self.assertEqual(pk.tarkista("agentti-pohja", hak, "yo/koe"), ["salkku.py"])

    def test_sallittu_polku(self):
        self.assertTrue(pk.sallittu_polku("kauppatiimi", "test_k7.py"))
        self.assertTrue(pk.sallittu_polku("kauppatiimi", "./README.md"))
        self.assertTrue(pk.sallittu_polku("kauppatiimi", "k7.py"))
        self.assertTrue(pk.sallittu_polku("kauppatiimi", "paperi_asetukset.py"))
        self.assertFalse(pk.sallittu_polku("kauppatiimi", "salkku.py"))
        self.assertFalse(pk.sallittu_polku("kauppatiimi", "paperi/../salkku.py"))
        self.assertFalse(pk.sallittu_polku("kauppatiimi", "../test_a.py"))
        self.assertTrue(pk.sallittu_polku("evalkit", "mika/tahansa.py"))


class RepolistaTesti(unittest.TestCase):
    def test_sallitut_repot_tarkasti(self):
        self.assertEqual(set(pk.REPOT), {"agentti-pohja", "mcp-model-router", "ai-pr-reviewer-action", "llm-stopcheck", "ideapankki", "evalkit", "kauppatiimi"})

    def test_tuntematon_repo_hylataan(self):
        with self.assertRaises(pk.PrVirhe):
            pk.tarkista("evalkit-vaara", "/ei/ole", "yo/koe")


class OletushaaraTesti(unittest.TestCase):
    def test_pohja_haara(self):
        self.assertEqual(pk.pohja_haara("ai-pr-reviewer-action"), "master")
        for repo in ("agentti-pohja", "mcp-model-router", "llm-stopcheck", "ideapankki", "evalkit"):
            self.assertEqual(pk.pohja_haara(repo), "main")

    def test_pr_avataan_master_pohjaan(self):
        saatu = {}

        class Vastaus:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"html_url": "https://github.com/x/y/pull/1"}'

        def avaa(pyynto, timeout=None):
            saatu["body"] = json.loads(pyynto.data.decode())
            return Vastaus()
        pk.avaa_pr("ai-pr-reviewer-action", "yo/a", "bot", "otsikko", "kuvaus", "tunnus", avaa=avaa)
        self.assertEqual(saatu["body"]["base"], "master")

    def test_fork_paivitetaan_oikeaan_haaraan(self):
        saatu = {}

        class Vastaus:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def avaa(pyynto, timeout=None):
            saatu["body"] = json.loads(pyynto.data.decode())
            return Vastaus()
        pk.paivita_fork("ai-pr-reviewer-action", "bot", "tunnus", avaa=avaa)
        self.assertEqual(saatu["body"], {"branch": "master"})
        pk.paivita_fork("llm-stopcheck", "bot", "tunnus", avaa=avaa)
        self.assertEqual(saatu["body"], {"branch": "main"})

    def test_tarkista_kayttaa_masteria(self):
        hak = tempfile.mkdtemp()
        git(hak, "init", "-q", "-b", "master")
        (Path(hak) / "a.md").write_text("a\n")
        git(hak, "add", "-A")
        git(hak, "commit", "-q", "-m", "a")
        git(hak, "checkout", "-q", "-b", "yo/koe")
        (Path(hak) / "b.md").write_text("b\n")
        git(hak, "add", "-A")
        git(hak, "commit", "-q", "-m", "b")
        self.assertEqual(pk.tarkista("ai-pr-reviewer-action", hak, "yo/koe"), ["b.md"])


def _g(*args, cwd=None, check=True):
    """Oikea git (testit kayttavat paikallisia bare-repoja, ei verkkoa)."""
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


class ForkPohja(unittest.TestCase):
    """Ylavirta ja fork ovat paikallisia bare-repoja; URL-kuvaus osoittaa niihin. Fork on aluksi ylavirran kopio."""
    REPO = "llm-stopcheck"
    HAARA = "main"
    BOTTI = "agenttifirma-bot"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.k = self.tmp.name
        self.ylavirta = os.path.join(self.k, "ylavirta.git")
        self.fork = os.path.join(self.k, "fork.git")
        self.tyo = os.path.join(self.k, "tyo")
        _g("init", "-q", "--bare", "-b", self.HAARA, self.ylavirta)
        _g("clone", "-q", self.ylavirta, self.tyo)
        _g("checkout", "-q", "-B", self.HAARA, cwd=self.tyo)
        self.commit(self.tyo, "a")
        _g("push", "-q", "origin", "HEAD:refs/heads/" + self.HAARA, cwd=self.tyo)
        _g("clone", "-q", "--bare", self.ylavirta, self.fork)
        self.tunnus = os.path.join(self.k, "token")
        with open(self.tunnus, "w") as f:
            f.write("ghp_" + "SALAINENTUNNUS" * 3)
        self.env = {"YOJONO_BOT_KAYTTAJA": self.BOTTI, "YOJONO_BOT_TUNNUS": self.tunnus}
        self.kutsut = []
        self.ymparistot = []

    def url(self, omistaja, repo):
        self.assertEqual(repo, self.REPO)
        return {pk.OMISTAJA: self.ylavirta, self.BOTTI: self.fork}[omistaja]

    def suorita(self, cmd, **kw):
        self.kutsut.append(list(cmd))
        self.ymparistot.append((list(cmd), dict(kw.get("env") or {})))
        return subprocess.run(cmd, **kw)

    def valmis(self, suorita=None, env=None):
        return pk.fork_valmis(self.REPO, self.env if env is None else env, suorita=suorita or self.suorita, url=self.url)

    @staticmethod
    def commit(hak, nimi):
        with open(os.path.join(hak, nimi + ".txt"), "w") as f:
            f.write(nimi)
        _g("add", "-A", cwd=hak)
        _g("commit", "-q", "-m", nimi, cwd=hak)

    def nosta_ylavirtaa(self, n=2):
        for i in range(n):
            self.commit(self.tyo, "u%d" % i)
        _g("push", "-q", "origin", "HEAD:refs/heads/" + self.HAARA, cwd=self.tyo)

    def kasvata_forkia(self, nimi="f"):
        """Fork saa oman commitin (erkaantuu tai menee edelle)."""
        tyo2 = os.path.join(self.k, "tyo2")
        if not os.path.isdir(tyo2):
            _g("clone", "-q", self.fork, tyo2)
        self.commit(tyo2, nimi)
        _g("push", "-q", "origin", "HEAD:refs/heads/" + self.HAARA, cwd=tyo2)

    def sha(self, polku, ref=None):
        return _g("--git-dir", polku, "rev-parse", ref or "refs/heads/" + self.HAARA)

    def refit(self, polku):
        return _g("--git-dir", polku, "for-each-ref", "--format=%(refname)").splitlines()

    def hylkaa_pushit(self, viesti):
        koukku = os.path.join(self.fork, "hooks", "pre-receive")
        with open(koukku, "w") as f:
            f.write("#!/bin/sh\necho '%s' >&2\nexit 1\n" % viesti)
        os.chmod(koukku, 0o755)

    def pushit(self):
        return [k for k in self.kutsut if "push" in k]


class ForkSynkkausTesti(ForkPohja):
    def test_ajan_tasalla_ei_pushaa_eika_hae(self):
        ennen = self.sha(self.fork)
        self.assertEqual(self.valmis(), (True, "", "ajan_tasalla"))
        self.assertEqual(self.sha(self.fork), ennen)
        self.assertFalse([k for k in self.kutsut if "push" in k or "fetch" in k])
        self.assertTrue(all("ls-remote" in k for k in self.kutsut))

    def test_fast_forward_kelaa_forkin_ylavirran_tasalle(self):
        self.nosta_ylavirtaa(3)
        self.assertNotEqual(self.sha(self.fork), self.sha(self.ylavirta))
        self.assertEqual(self.valmis(), (True, "", "kelattu"))
        self.assertEqual(self.sha(self.fork), self.sha(self.ylavirta))
        self.assertEqual(len(self.pushit()), 1)

    def test_push_on_aina_ilman_forcea_ja_vain_pohjahaaraan(self):
        self.nosta_ylavirtaa(1)
        self.valmis()
        (push,) = self.pushit()
        self.assertEqual(push[-1], "refs/synkka/ylavirta:refs/heads/" + self.HAARA)
        self.assertEqual(push[-2], self.fork)
        for k in self.kutsut:
            for kielletty in ("--force", "-f", "--force-with-lease", "--mirror", "--all", "--tags", "--delete", "--prune"):
                self.assertNotIn(kielletty, k)
            self.assertFalse([a for a in k if a.startswith(("+", ":"))], k)

    def test_muut_haarat_eivat_muutu_eika_uusia_synny(self):
        _g("--git-dir", self.fork, "branch", "yo/muu", "refs/heads/" + self.HAARA)
        muu = self.sha(self.fork, "refs/heads/yo/muu")
        ennen = sorted(self.refit(self.fork))
        self.nosta_ylavirtaa(2)
        self.assertEqual(self.valmis(), (True, "", "kelattu"))
        self.assertEqual(sorted(self.refit(self.fork)), ennen)
        self.assertEqual(self.sha(self.fork, "refs/heads/yo/muu"), muu)
        self.assertEqual(self.sha(self.ylavirta), self.sha(self.fork))

    def test_ylavirta_ei_muutu(self):
        self.nosta_ylavirtaa(2)
        ennen = (self.sha(self.ylavirta), sorted(self.refit(self.ylavirta)))
        self.valmis()
        self.assertEqual((self.sha(self.ylavirta), sorted(self.refit(self.ylavirta))), ennen)

    def test_ei_fast_forward_ei_tee_mitaan_ja_antaa_koodin(self):
        self.kasvata_forkia("forkin-oma")
        self.nosta_ylavirtaa(2)
        fork_ennen = self.sha(self.fork)
        ok, koodi, selitys = self.valmis()
        self.assertEqual((ok, koodi), (False, "fork_ei_ff"))
        self.assertIn("erkaantunut", selitys)
        self.assertEqual(self.sha(self.fork), fork_ennen)
        self.assertEqual(self.pushit(), [])

    def test_fork_vain_edella_ei_vaadi_toimia(self):
        self.kasvata_forkia("forkin-oma")
        fork_ennen = self.sha(self.fork)
        self.assertEqual(self.valmis(), (True, "", "edella"))
        self.assertEqual(self.sha(self.fork), fork_ennen)
        self.assertEqual(self.pushit(), [])

    def test_push_epaonnistuu_ohitus_ja_selva_koodi(self):
        self.nosta_ylavirtaa(2)
        self.hylkaa_pushit("pre-receive: ei sallittu")
        fork_ennen = self.sha(self.fork)
        ok, koodi, selitys = self.valmis()
        self.assertEqual((ok, koodi), (False, "fork_push_epaonnistui"))
        self.assertIn("ei sallittu", selitys)
        self.assertEqual(self.sha(self.fork), fork_ennen)
        self.assertEqual(len(self.pushit()), 1)

    def test_workflow_oikeuden_puute_tunnistetaan_omalla_koodilla(self):
        self.nosta_ylavirtaa(1)
        self.hylkaa_pushit("refusing to allow a Personal Access Token to create or update workflow `.github/workflows/a.yml` without `workflow` scope")
        ok, koodi, selitys = self.valmis()
        self.assertEqual((ok, koodi), (False, "fork_workflow_oikeus"))
        self.assertIn("workflow-oikeutta", selitys)
        self.assertIn("synkkaa fork kasin", selitys)

    def test_fork_muuttui_synkkauksen_aikana_push_ei_ff(self):
        self.nosta_ylavirtaa(2)

        def kilpa(cmd, **kw):
            if "push" in cmd and not getattr(kilpa, "tehty", False):
                kilpa.tehty = True
                self.kasvata_forkia("kilpa")          # fork etenee ennen pushia: push ei ole enaa fast-forward
            return self.suorita(cmd, **kw)
        ok, koodi, selitys = self.valmis(suorita=kilpa)
        self.assertEqual((ok, koodi), (False, "fork_ei_ff"))
        self.assertNotEqual(self.sha(self.fork), self.sha(self.ylavirta))     # mitaan ei ylikirjoitettu

    def test_push_ok_mutta_fork_ei_paivittynyt_on_fork_jaljessa(self):
        self.nosta_ylavirtaa(1)

        def nielee(cmd, **kw):
            if "push" in cmd:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return self.suorita(cmd, **kw)
        ok, koodi, _ = self.valmis(suorita=nielee)
        self.assertEqual((ok, koodi), (False, "fork_jaljessa"))

    def test_ylavirta_tai_fork_ei_luettavissa(self):
        for puuttuva in ("ylavirta", "fork"):
            with self.subTest(puuttuva=puuttuva):
                polut = {pk.OMISTAJA: self.ylavirta, self.BOTTI: self.fork}
                polut[pk.OMISTAJA if puuttuva == "ylavirta" else self.BOTTI] = os.path.join(self.k, "ei-ole.git")
                self.kutsut.clear()
                ok, koodi, _ = pk.fork_valmis(self.REPO, self.env, suorita=self.suorita, url=lambda o, r: polut[o])
                self.assertEqual((ok, koodi), (False, "fork_tarkistus_epaonnistui"))
                self.assertEqual(self.pushit(), [])

    def test_haaraa_ei_ole_forkissa_on_lukuvirhe(self):
        _g("--git-dir", self.fork, "branch", "-m", self.HAARA, "toinen")
        ok, koodi, _ = self.valmis()
        self.assertEqual((ok, koodi), (False, "fork_tarkistus_epaonnistui"))

    def test_repo_ei_sallittujen_listalla(self):
        ok, koodi, selitys = pk.fork_valmis("jokin-muu-repo", self.env, suorita=self.suorita, url=self.url)
        self.assertEqual((ok, koodi), (False, "fork_tarkistus_epaonnistui"))
        self.assertEqual(self.kutsut, [])

    def test_kelpaamaton_tunnus_ennen_pushia_ei_pushaa(self):
        self.nosta_ylavirtaa(1)
        fork_ennen = self.sha(self.fork)
        for sisalto in ("roskaa", "", "ghp_lyhyt"):
            with self.subTest(sisalto=sisalto):
                with open(self.tunnus, "w") as f:
                    f.write(sisalto)
                self.kutsut.clear()
                ok, koodi, selitys = self.valmis()
                self.assertEqual((ok, koodi), (False, "fork_push_epaonnistui"))
                self.assertEqual(self.pushit(), [])
                self.assertEqual(self.sha(self.fork), fork_ennen)
        ok, koodi, _ = self.valmis(env={"YOJONO_BOT_KAYTTAJA": self.BOTTI, "YOJONO_BOT_TUNNUS": os.path.join(self.k, "ei-ole")})
        self.assertEqual((ok, koodi), (False, "fork_push_epaonnistui"))

    def test_virheellinen_botin_nimi(self):
        ok, koodi, _ = self.valmis(env={"YOJONO_BOT_KAYTTAJA": "../x", "YOJONO_BOT_TUNNUS": self.tunnus})
        self.assertEqual((ok, koodi), (False, "fork_tarkistus_epaonnistui"))
        self.assertEqual(self.kutsut, [])

    def test_aikakatkaisut_antavat_koodin(self):
        self.nosta_ylavirtaa(1)
        for komento, odotettu in (("ls-remote", "fork_tarkistus_epaonnistui"), ("fetch", "fork_tarkistus_epaonnistui"), ("push", "fork_push_epaonnistui")):
            with self.subTest(komento=komento):
                def hidas(cmd, **kw):
                    if komento in cmd:
                        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
                    return self.suorita(cmd, **kw)
                ok, koodi, selitys = self.valmis(suorita=hidas)
                self.assertEqual((ok, koodi), (False, odotettu))
                self.assertIn("aikakatkaisu", selitys)

    def test_git_ei_kaynnisty(self):
        def puuttuu(cmd, **kw):
            raise FileNotFoundError("git")
        ok, koodi, _ = self.valmis(suorita=puuttuu)
        self.assertEqual((ok, koodi), (False, "fork_tarkistus_epaonnistui"))

    def test_tunnus_ei_vuoda_komentoriveille_ymparistoon_eika_viesteihin(self):
        self.nosta_ylavirtaa(1)
        self.hylkaa_pushit("hylatty")
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "gh-salainen", "GH_TOKEN": "gh-toinen"}):
            ok, koodi, selitys = self.valmis()
        tunnus = open(self.tunnus).read()
        self.assertNotIn(tunnus, selitys)
        for cmd, ymp in self.ymparistot:
            self.assertNotIn(tunnus, " ".join(cmd))
            self.assertNotIn(tunnus, json.dumps(ymp))
            self.assertNotIn("GITHUB_TOKEN", ymp)
            self.assertNotIn("GH_TOKEN", ymp)
            self.assertEqual(ymp.get("GIT_TERMINAL_PROMPT"), "0")
            self.assertTrue(all("@" not in a or a.startswith("-") for a in cmd))      # ei tunnus@-URL:ia
        push_ymp = [y for c, y in self.ymparistot if "push" in c][0]
        self.assertIn("GIT_ASKPASS", push_ymp)
        self.assertFalse(os.path.exists(push_ymp["GIT_ASKPASS"]))                     # askpass poistettu
        lukuymp = [y for c, y in self.ymparistot if "push" not in c]
        self.assertTrue(lukuymp and all("GIT_ASKPASS" not in y for y in lukuymp))     # julkinen luku ilman tunnusta

    def test_ei_kirjoita_muualle_kuin_forkin_pohjahaaraan(self):
        self.nosta_ylavirtaa(2)
        self.valmis()
        for cmd in self.kutsut:
            if "push" in cmd:
                self.assertEqual([a for a in cmd if a == self.ylavirta], [])      # ylavirtaan ei pushata koskaan
        self.assertEqual(sorted(self.refit(self.fork)), ["refs/heads/" + self.HAARA])


class ForkMasterHaaraTesti(ForkPohja):
    """ai-pr-reviewer-action kayttaa haaraa master: synkataan master, ei main."""
    REPO = "ai-pr-reviewer-action"
    HAARA = "master"

    def test_master_kelataan(self):
        self.nosta_ylavirtaa(2)
        self.assertEqual(self.valmis(), (True, "", "kelattu"))
        self.assertEqual(self.sha(self.fork), self.sha(self.ylavirta))
        (push,) = self.pushit()
        self.assertEqual(push[-1], "refs/synkka/ylavirta:refs/heads/master")


# --- Mutaatiotestauksessa (tools/mutantit.py) selvinneet haarat ---

def _ok(stdout="", stderr="", koodi=0):
    return SimpleNamespace(returncode=koodi, stdout=stdout, stderr=stderr)


class SuojattuRajatTest(unittest.TestCase):
    def test_alkupiste_vinoviiva_poistetaan(self):
        self.assertTrue(pk.suojattu("./tools/x.py"))
        self.assertTrue(pk.suojattu("././.github/a.yml"))
        self.assertFalse(pk.suojattu("./docs/a.md"))


class GitApuTest(unittest.TestCase):
    def test_git_parametrit_ja_tyhjat_tulosteet(self):
        nahty = {}

        def suorita(cmd, **kw):
            nahty["cmd"], nahty["kw"] = cmd, kw
            return _ok(stdout=None, stderr=" virhe \n", koodi=3)
        self.assertEqual(pk._git("/h", "status", suorita=suorita), (3, "", "virhe"))
        self.assertEqual(nahty["cmd"], ["git", "-C", "/h", "status"])
        self.assertEqual(nahty["kw"], {"capture_output": True, "text": True, "timeout": 60, "check": False})
        self.assertEqual(pk._git("/h", "x", suorita=lambda *a, **k: _ok(stdout=" ulos ", stderr=None)), (0, "ulos", ""))

    def test_vain_viisi_suojattua_nimea_viestissa(self):
        with tee_repo({"tools/%d.py" % i: "x" for i in range(7)}) as hak:
            with self.assertRaises(pk.PrVirhe) as ctx:
                pk.tarkista("agentti-pohja", hak, "yo/koe")
        self.assertEqual(str(ctx.exception).count("tools/"), 5)

    def test_github_url(self):
        self.assertEqual(pk._github_url("o", "r"), "https://github.com/o/r.git")


class PaivarajaTarkkaTest(unittest.TestCase):
    def tila(self, data):
        d = tempfile.mkdtemp()
        polku = os.path.join(d, "tila.json")
        with open(polku, "w") as f:
            json.dump(data, f)
        return polku

    def test_paikkoja_jaljella(self):
        self.assertEqual(pk.paikkoja_jaljella(self.tila({"pvm": "2026-10-21", "n": 3}), "2026-10-21"), pk.RAJA_PAIVASSA - 3)
        self.assertEqual(pk.paikkoja_jaljella(self.tila({"pvm": "2026-10-20", "n": 3}), "2026-10-21"), pk.RAJA_PAIVASSA)
        self.assertEqual(pk.paikkoja_jaljella(self.tila({"pvm": "2026-10-21", "n": "x"}), "2026-10-21"), pk.RAJA_PAIVASSA)
        self.assertEqual(pk.paikkoja_jaljella(self.tila({"pvm": "2026-10-21"}), "2026-10-21"), pk.RAJA_PAIVASSA)
        self.assertEqual(pk.paikkoja_jaljella(self.tila({"pvm": "2026-10-21", "n": pk.RAJA_PAIVASSA + 50}), "2026-10-21"), 0)
        self.assertEqual(pk.paikkoja_jaljella(os.path.join(tempfile.mkdtemp(), "ei-ole.json"), "2026-10-21"), pk.RAJA_PAIVASSA)

    def test_oletuspaiva_ja_ymparistopolku(self):
        tanaan = pk.date.today().isoformat()
        polku = self.tila({"pvm": tanaan, "n": 4})
        self.assertEqual(pk.paikkoja_jaljella(polku), pk.RAJA_PAIVASSA - 4)
        with mock.patch.dict(os.environ, {"YOJONO_PR_TILA": polku}):
            self.assertEqual(pk.paikkoja_jaljella(), pk.RAJA_PAIVASSA - 4)

    def test_varaa_paikka_laskuri(self):
        polku = self.tila({"pvm": "2026-10-21"})
        self.assertTrue(pk.varaa_paikka(polku, "2026-10-21"))
        with open(polku) as f:
            self.assertEqual(json.load(f), {"pvm": "2026-10-21", "n": 1})
        polku = self.tila({"pvm": "2026-10-20", "n": 50})
        self.assertTrue(pk.varaa_paikka(polku, "2026-10-21"))
        with open(polku) as f:
            self.assertEqual(json.load(f), {"pvm": "2026-10-21", "n": 1})
        polku = self.tila({"pvm": "2026-10-21", "n": pk.RAJA_PAIVASSA})
        self.assertIs(pk.varaa_paikka(polku, "2026-10-21"), False)
        polku = self.tila({"pvm": pk.date.today().isoformat(), "n": 2})
        self.assertTrue(pk.varaa_paikka(polku))
        with open(polku) as f:
            self.assertEqual(json.load(f)["n"], 3)


class PushaaTarkkaTest(unittest.TestCase):
    def aja(self, vastaus, sync=True):
        nahty = {}

        def suorita(cmd, **kw):
            nahty["kw"] = {k: v for k, v in kw.items() if k != "env"}
            return vastaus
        try:
            pk.pushaa("/h", "agentti-pohja", "yo/koe", "botti", "/t", suorita=suorita, sync=sync)
        except pk.PrVirhe as e:
            return str(e), nahty
        return None, nahty

    def test_parametrit(self):
        _, nahty = self.aja(_ok())
        self.assertEqual(nahty["kw"], {"capture_output": True, "text": True, "timeout": 180, "check": False})

    def test_viesti_yhdistaa_stderr_ja_stdout_ja_rajaa_300(self):
        viesti, _ = self.aja(_ok(stdout="OUT", stderr="E" * 400, koodi=1))
        self.assertEqual(viesti, "push epaonnistui (onko fork olemassa ja token voimassa?): " + "E" * 297 + "OUT")

    def test_workflow_vaatii_molemmat_sanat(self):
        for teksti in ("workflow rikki", "scope puuttuu"):
            viesti, _ = self.aja(_ok(stderr=teksti, koodi=1))
            self.assertTrue(viesti.startswith("push epaonnistui"), viesti)
        viesti, _ = self.aja(_ok(stderr="workflow scope", koodi=1))
        self.assertIn("API-synkronointi: ok. Push: workflow scope", viesti)
        viesti, _ = self.aja(_ok(stderr="workflow scope", koodi=1), sync="HTTP 404")
        self.assertIn("API-synkronointi: HTTP 404. Push:", viesti)


class PaivitaForkTest(unittest.TestCase):
    def test_tulokset_ja_aikakatkaisu(self):
        nahty = {}

        def avaa(pyynto, timeout=None):
            nahty["timeout"], nahty["data"] = timeout, json.loads(pyynto.data)
            return Vastaus({})
        self.assertIs(pk.paivita_fork("agentti-pohja", "botti", TUNNUS, avaa=avaa), True)
        self.assertEqual(nahty, {"timeout": 30, "data": {"branch": "main"}})

        def http(pyynto, timeout=None):
            raise urllib.error.HTTPError("u", 409, "x", {}, None)

        def yhteys(pyynto, timeout=None):
            raise urllib.error.URLError("verkko")
        self.assertEqual(pk.paivita_fork("agentti-pohja", "botti", TUNNUS, avaa=http), "HTTP 409")
        self.assertEqual(pk.paivita_fork("agentti-pohja", "botti", TUNNUS, avaa=yhteys), "URLError")


class AvaaPrTarkkaTest(unittest.TestCase):
    def test_aikakatkaisu(self):
        nahty = {}

        def avaa(pyynto, timeout=None):
            nahty["timeout"] = timeout
            return Vastaus({"html_url": "https://github.com/o/r/pull/1"})
        pk.avaa_pr("agentti-pohja", "yo/koe", "botti", "otsikko", "kuvaus", TUNNUS, avaa=avaa)
        self.assertEqual(nahty["timeout"], 30)


class ForkTarkkaTest(ForkPohja):
    def suorita(self, cmd, **kw):
        self.aikarajat = getattr(self, "aikarajat", [])
        self.aikarajat.append((list(cmd), kw.get("timeout")))
        return super().suorita(cmd, **kw)

    def raja(self, sana, nimi=None):
        return [t for c, t in self.aikarajat if sana in c and (nimi is None or nimi in c)]

    def test_aikarajat(self):
        self.nosta_ylavirtaa(1)
        self.assertEqual(self.valmis()[2], "kelattu")
        self.assertEqual(set(self.raja("ls-remote")), {60})
        self.assertEqual(set(self.raja("init")), {60})
        self.assertEqual(set(self.raja("fetch")), {180})
        self.assertEqual(set(self.raja("merge-base")), {60})
        self.assertEqual(set(self.raja("push")), {180})

    def test_luettavuusviesti_kertoo_kumpi(self):
        self.nosta_ylavirtaa(1)
        for nimi, odotettu in (("ylavirta.git", "ylavirran main ei ole luettavissa"), ("fork.git", "forkin main ei ole luettavissa")):
            with self.subTest(nimi=nimi):
                def rikki(cmd, **kw):
                    if "ls-remote" in cmd and cmd[-2].endswith(nimi):
                        return _ok(koodi=2)
                    return self.suorita(cmd, **kw)
                ok, koodi, selitys = self.valmis(suorita=rikki)
                self.assertEqual((ok, koodi, selitys), (False, "fork_tarkistus_epaonnistui", odotettu))

    def korvaa(self, sana, tulos):
        def f(cmd, **kw):
            if sana in cmd:
                return tulos
            return self.suorita(cmd, **kw)
        return f

    def test_fetch_virheen_viesti(self):
        self.nosta_ylavirtaa(1)
        ok, koodi, selitys = self.valmis(suorita=self.korvaa("fetch", _ok(stdout="O", stderr="E" * 300, koodi=1)))
        self.assertEqual((ok, koodi, selitys), (False, "fork_tarkistus_epaonnistui", "fetch epaonnistui: " + "E" * 199 + "O"))

    def test_push_virheen_viesti_ja_luokittelu(self):
        self.nosta_ylavirtaa(1)
        ok, koodi, selitys = self.valmis(suorita=self.korvaa("push", _ok(stdout="O", stderr="E" * 400, koodi=1)))
        self.assertEqual((ok, koodi), (False, "fork_push_epaonnistui"))
        self.assertTrue(selitys.endswith("haaraan epaonnistui: " + "E" * 299 + "O"), selitys)
        for teksti in ("workflow rikki", "scope puuttuu"):
            ok, koodi, _ = self.valmis(suorita=self.korvaa("push", _ok(stderr=teksti, koodi=1)))
            self.assertEqual(koodi, "fork_push_epaonnistui", teksti)
        ok, koodi, _ = self.valmis(suorita=self.korvaa("push", _ok(stderr="workflow scope", koodi=1)))
        self.assertEqual(koodi, "fork_workflow_oikeus")
        ok, koodi, _ = self.valmis(suorita=self.korvaa("push", _ok(stdout="non-fast-forward", koodi=1)))
        self.assertEqual(koodi, "fork_ei_ff")


class MainTarkkaTest(unittest.TestCase):
    def ajaa(self, argv):
        ulos, virhe = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", ulos), mock.patch.object(sys, "stderr", virhe):
            koodi = pk.main(argv)
        return koodi, ulos.getvalue(), virhe.getvalue()

    def test_argv_otetaan_sys_argv_sta(self):
        with tee_repo({"docs/a.md": "x"}) as hak, mock.patch.object(sys, "argv", ["x", "tarkista", "agentti-pohja", hak, "yo/koe"]):
            self.assertEqual(self.ajaa(None)[:2], (0, "ok: 1 tiedostoa\n"))

    def test_pr_komento(self):
        d = tempfile.mkdtemp()
        kuvaus = os.path.join(d, "k.md")
        with open(kuvaus, "w", encoding="utf-8") as f:
            f.write("Kuvaus")
        with mock.patch.object(pk, "luo_pr", return_value="https://github.com/o/r/pull/9") as luo:
            self.assertEqual(self.ajaa(["pr", "agentti-pohja", "/h", "yo/koe", "Otsikko", kuvaus]),
                             (0, "https://github.com/o/r/pull/9\n", ""))
        self.assertEqual(luo.call_args[0], ("agentti-pohja", "/h", "yo/koe", "Otsikko", "Kuvaus"))

    def test_virheet(self):
        koodi, _, virhe = self.ajaa(["pr", "agentti-pohja", "/h", "yo/koe", "O", "/ei/ole/tiedostoa"])
        self.assertEqual(koodi, 1)
        self.assertIn("pr_kutsu: tiedostovirhe: FileNotFoundError", virhe)
        koodi, _, virhe = self.ajaa(["tarkista", "tuntematon-repo", "/x", "yo/a"])
        self.assertEqual(koodi, 1)
        self.assertIn("pr_kutsu: repo ei ole sallittujen listalla", virhe)
        self.assertEqual(self.ajaa(["pr", "a"])[0], 2)
        self.assertIn("kaytto: pr_kutsu", self.ajaa([])[2])


if __name__ == "__main__":
    unittest.main()
