import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

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
            for repo in ("kauppatiimi", "../x", ""):
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
                               avaa=lambda p, timeout: Vastaus({"html_url": "https://github.com/a/b/pull/1"}))
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


class MainTest(unittest.TestCase):
    def test_kayttovirhe_ja_tarkista(self):
        self.assertEqual(pk.main([]), 2)
        self.assertEqual(pk.main(["tarkista", "kauppatiimi", "/x", "yo/a"]), 1)
        with tee_repo({"docs/a.md": "x"}) as hak:
            self.assertEqual(pk.main(["tarkista", "agentti-pohja", hak, "yo/koe"]), 0)


class OletushaaraTesti(unittest.TestCase):
    def test_pohja_haara(self):
        self.assertEqual(pk.pohja_haara("ai-pr-reviewer-action"), "master")
        for repo in ("agentti-pohja", "mcp-model-router", "llm-stopcheck", "ideapankki"):
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


if __name__ == "__main__":
    unittest.main()


class ForkEsitarkistusTesti(unittest.TestCase):
    ENV = {"YOJONO_BOT_KAYTTAJA": "agenttifirma-bot", "YOJONO_BOT_TUNNUS": "/ei/ole"}

    @staticmethod
    def vastaus(data):
        class V:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return json.dumps(data).encode()
        return V()

    def test_ajan_tasalla_on_ok_ilman_tunnusta(self):
        ok, _ = pk.fork_valmis("llm-stopcheck", self.ENV, avaa=lambda p, timeout=0: self.vastaus({"behind_by": 0}))
        self.assertTrue(ok)

    def test_ei_selvia_tietoa_ei_estaa_ajoa(self):
        def rikki(p, timeout=0):
            raise urllib.error.URLError("ei verkkoa")
        self.assertTrue(pk.fork_valmis("llm-stopcheck", self.ENV, avaa=rikki)[0])

    def test_jaljessa_ilman_tunnusta_ei_estaa_ajoa(self):
        # tunnustiedostoa ei voi lukea -> PrVirhe -> epavarma -> sallitaan (push nostaa selkean virheen)
        ok, _ = pk.fork_valmis("llm-stopcheck", self.ENV, avaa=lambda p, timeout=0: self.vastaus({"behind_by": 3}))
        self.assertTrue(ok)

    def test_jaljessa_ja_synkka_ei_onnistu_estaa(self):
        with tempfile.TemporaryDirectory() as tmp:
            tunnus = os.path.join(tmp, "token")
            with open(tunnus, "w") as f:
                f.write("ghp_" + "a" * 30)
            env = dict(self.ENV, YOJONO_BOT_TUNNUS=tunnus)

            def avaa(p, timeout=0):
                if p.full_url.endswith("/merge-upstream"):
                    raise urllib.error.HTTPError(p.full_url, 422, "x", {}, None)
                return self.vastaus({"behind_by": 3})
            ok, selitys = pk.fork_valmis("llm-stopcheck", env, avaa=avaa)
        self.assertFalse(ok)
        self.assertIn("Sync fork", selitys)
        self.assertIn("3 commitia", selitys)

    def test_jaljessa_mutta_synkka_onnistuu(self):
        with tempfile.TemporaryDirectory() as tmp:
            tunnus = os.path.join(tmp, "token")
            with open(tunnus, "w") as f:
                f.write("ghp_" + "a" * 30)
            env = dict(self.ENV, YOJONO_BOT_TUNNUS=tunnus)
            kutsut = []

            def avaa(p, timeout=0):
                kutsut.append(p.full_url)
                if p.full_url.endswith("/merge-upstream"):
                    return self.vastaus({})
                return self.vastaus({"behind_by": 0 if any(u.endswith("/merge-upstream") for u in kutsut) else 3})
            self.assertTrue(pk.fork_valmis("llm-stopcheck", env, avaa=avaa)[0])
