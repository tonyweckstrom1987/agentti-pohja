"""kehitys.sh: oikea alaprosessi, paikalliset git-repot ja tekoagentti (ei verkkoa, ei oikeaa claudea, ei oikeaa PR:aa)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKRIPTI = Path(__file__).resolve().parent / "kehitys.sh"
REPO = "koe-repo"


def git(hak, *args):
    subprocess.run(["git", "-C", str(hak), "-c", "user.name=t", "-c", "user.email=t@e", *args], check=True, capture_output=True)


def kirjoita(polku, teksti, suoritettava=False):
    polku.parent.mkdir(parents=True, exist_ok=True)
    polku.write_text(teksti)
    if suoritettava:
        polku.chmod(polku.stat().st_mode | stat.S_IXUSR)


class Pohja(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.perus, self.ajuri, self.lahde = t / "perus", t / "ajuri", t / "lahde"
        r = self.lahde / REPO
        r.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
        kirjoita(r / "README.md", "# koe\n")
        kirjoita(r / "moduuli.py", "def f():\n    return 1\n")
        kirjoita(r / "test" / "test_a.py", "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n")
        kirjoita(r / "test" / "__init__.py", "")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "alku")
        # tekoagentti: komento tulee KOE_AGENTTI-ymparistomuuttujasta
        self.agentti = t / "claude"
        self.pr_kutsu_tulos = "https://github.com/x/y/pull/7"
        self.env_tiedosto = t / "env"
        kirjoita(self.env_tiedosto, "KOE_ARVO=1\n")

    def agentti_skripti(self, runko, kulu=0.3):
        kirjoita(self.agentti, "#!/bin/sh\n" + runko + f"\necho '{{\"total_cost_usd\": {kulu}, \"result\": \"agentti valmis\"}}'\n", True)

    def pr_kutsu(self, onnistuu=True):
        kirjoita(self.ajuri / "agentti" / "__init__.py", "")
        if onnistuu:
            runko = f"import sys\nopen(sys.argv[0] + '.kutsut', 'a').write(' '.join(sys.argv[1:]) + '\\n')\nprint('{self.pr_kutsu_tulos}')\n"
        else:
            runko = "import sys\nprint('hylatty', file=sys.stderr)\nsys.exit(1)\n"
        kirjoita(self.ajuri / "agentti" / "pr_kutsu.py", runko)

    def aja(self, *args, kuiva=None, **extra):
        env = dict(os.environ, KEHITYS_PERUS=str(self.perus), KEHITYS_TYO=str(self.perus / "tyo"), YOJONO_AJURI=str(self.ajuri), KEHITYS_ENV=str(self.env_tiedosto),
                   KEHITYS_URL_ETUOSA=str(self.lahde), KEHITYS_CLAUDE=str(self.agentti), KEHITYS_PYTHON=sys.executable, **extra)
        env.pop("KEHITYS_KUIVA", None)
        if kuiva:
            env["KEHITYS_KUIVA"] = kuiva
        return subprocess.run(["sh", str(SKRIPTI), *args], capture_output=True, text=True, env=env, timeout=120)

    def raportti(self, aihe="readme"):
        (md,) = list((self.perus / "raportit").glob(f"*-kehitys-{aihe}-{REPO}.md"))
        return md.read_text()

    def kutsut(self):
        f = self.ajuri / "agentti" / "pr_kutsu.py.kutsut"
        return f.read_text() if f.exists() else ""


class Argumentit(Pohja):
    def test_vaara_aihe_ja_repo(self):
        self.assertEqual(self.aja(REPO, "1.00", "muu").returncode, 2)
        self.assertEqual(self.aja(REPO, "1.00", "").returncode, 2)
        for repo in ("", "..", "a/b", "a b", "a;b"):
            self.assertEqual(self.aja(repo, "1.00", "readme").returncode, 2, repo)
        self.assertFalse((self.perus / "tyo").exists())


@unittest.skipUnless(sys.platform.startswith("linux"), "kehitys.sh on palvelinskripti: vaatii GNU timeoutin (Linux); macOS-CI paljasti, ettei sita ole")
class Ajo(Pohja):
    def test_readme_muutos_avaa_pr_ja_siivoaa(self):
        self.agentti_skripti('echo "uusi rivi" >> README.md')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "readme")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pr: " + self.pr_kutsu_tulos, r.stdout)
        rap = self.raportti()
        self.assertIn("- PR: " + self.pr_kutsu_tulos, rap)
        self.assertIn("README.md", rap)
        self.assertIn("agentti valmis", rap)
        kutsu = self.kutsut().split()
        self.assertEqual(kutsu[:3], ["pr", REPO, kutsu[2]])
        self.assertRegex(kutsu[3], r"^yo/readme-\d{4}-\d{2}-\d{2}$")
        self.assertEqual(list((self.perus / "tyo").iterdir()), [])
        (js,) = list((self.perus / "raportit").glob("*.json"))
        self.assertEqual(json.loads(js.read_text())["total_cost_usd"], 0.3)

    def test_agentti_saa_budjetin_ja_rajatut_tyokalut(self):
        self.agentti_skripti('echo "$@" > "$0.args"')
        self.pr_kutsu()
        self.aja(REPO, "1.25", "readme")
        args = (self.agentti.parent / "claude.args").read_text()
        self.assertIn("--max-budget-usd 1.25", args)
        self.assertIn("Bash(python3 -B -m unittest:*)", args)
        self.assertIn("--permission-mode acceptEdits", args)
        self.assertIn("Edit(//etc/**)", args)
        self.assertIn("Write(//opt/**)", args)
        self.assertIn("Write(//var/lib/yojono/*.json)", args)
        # ei yleista Edit/Write-lupaa sallittujen listassa (muuten kirjoitus sallittaisiin kaikkialle)
        sallitut = args.split("--allowedTools ")[1].split(" --disallowedTools")[0].split(",")
        self.assertNotIn("Edit", sallitut)
        self.assertNotIn("Write", sallitut)

    def test_oletus_tyohakemisto_ei_ole_var_lib_yojonon_alla(self):
        rivi = [l for l in SKRIPTI.read_text().splitlines() if l.startswith("TYO=")][0]
        self.assertIn("/var/tmp/yojono-tyo", rivi)
        self.assertNotIn("/var/lib/yojono", rivi)

    def test_ei_muutoksia_ei_prta(self):
        self.agentti_skripti(":")
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "readme")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ei muutoksia", self.raportti())
        self.assertEqual(self.kutsut(), "")

    def test_readme_aiheessa_koodimuutos_estetaan(self):
        self.agentti_skripti('echo "x = 1" >> moduuli.py')
        self.pr_kutsu()
        self.assertEqual(self.aja(REPO, "1.00", "readme").returncode, 0)
        self.assertIn("aiheen (readme) ulkopuolella: moduuli.py", self.raportti())
        self.assertEqual(self.kutsut(), "")

    def test_poisto_estetaan(self):
        self.agentti_skripti("rm README.md")
        self.pr_kutsu()
        self.aja(REPO, "1.00", "readme")
        self.assertIn("poistoja", self.raportti())
        self.assertEqual(self.kutsut(), "")

    def test_punaiset_testit_estavat_prn(self):
        self.agentti_skripti('printf "import unittest\\nclass B(unittest.TestCase):\\n    def test_b(self):\\n        self.fail()\\n" > test/test_b.py')
        self.pr_kutsu()
        self.assertEqual(self.aja(REPO, "1.00", "testit").returncode, 0)
        rap = self.raportti("testit")
        self.assertIn("punainen", rap)
        self.assertIn("testit punaisella", rap)
        self.assertEqual(self.kutsut(), "")

    def test_vihrea_testilisays_avaa_pr(self):
        self.agentti_skripti('printf "import unittest\\nclass B(unittest.TestCase):\\n    def test_b(self):\\n        self.assertEqual(1, 1)\\n" > test/test_b.py')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "testit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vihrea", self.raportti("testit"))
        self.assertIn(REPO, self.kutsut())

    TESTI = 'printf "import unittest\\nclass B(unittest.TestCase):\\n    def test_b(self):\\n        self.assertEqual(1, 1)\\n" > test/test_b.py'

    def test_ominaisuus_vaatii_teeman(self):
        self.assertEqual(self.aja(REPO, "1.00", "ominaisuus").returncode, 2)
        self.assertEqual(self.aja(REPO, "1.00", "ominaisuus", YOJONO_TEEMA="x" * 301).returncode, 2)
        self.assertEqual(self.aja(REPO, "1.00", "ominaisuus", YOJONO_TEEMA="x", YOJONO_TUNNUS="Ei/Hyva").returncode, 2)

    def test_ominaisuus_koodi_ja_testi_avaa_pr_omalla_haaralla_ja_raportilla(self):
        self.agentti_skripti('echo "def g():\\n    return 2" >> moduuli.py\n' + self.TESTI)
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "ominaisuus", YOJONO_TEEMA="lisaa g", YOJONO_TUNNUS="o-1", YOJONO_RAPORTTI="2026-10-23-kehitys-o-1-" + REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        kutsu = self.kutsut().split()
        self.assertEqual(kutsu[0], "pr")
        self.assertRegex(kutsu[3], r"^yo/ominaisuus-\d{4}-\d{2}-\d{2}-o-1$")
        self.assertTrue((self.perus / "raportit" / ("2026-10-23-kehitys-o-1-" + REPO + ".md")).exists())

    def test_ominaisuus_ilman_testimuutosta_ei_prta(self):
        self.agentti_skripti('echo "x = 1" >> moduuli.py')
        self.pr_kutsu()
        self.assertEqual(self.aja(REPO, "1.00", "ominaisuus", YOJONO_TEEMA="x").returncode, 0)
        self.assertIn("ei yhtaan testimuutosta", self.raportti("ominaisuus"))
        self.assertEqual(self.kutsut(), "")

    def test_ominaisuus_github_hakemisto_estetaan(self):
        self.agentti_skripti('mkdir -p .github/workflows && echo x > .github/workflows/a.yml\n' + self.TESTI)
        self.pr_kutsu()
        self.aja(REPO, "1.00", "ominaisuus", YOJONO_TEEMA="x")
        self.assertIn(".github/workflows/a.yml", self.raportti("ominaisuus"))
        self.assertEqual(self.kutsut(), "")

    def js_repo(self, npm_test_koodi):
        """Lisaa lahderepoon package.json:n ja tekee PATH-hakemiston, jossa on tekonpm (kirjaa argumentit)."""
        r = self.lahde / REPO
        kirjoita(r / "package.json", '{"name": "koe", "scripts": {"test": "x"}}\n')
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "js")
        bin_ = self.perus.parent / "bin"
        kirjoita(bin_ / "npm", '#!/bin/sh\necho "$@" >> "' + str(self.perus.parent / "npm.kutsut") + '"\n'
                 'if [ "$1" = "install" ]; then mkdir -p node_modules/better-sqlite3; fi\n'
                 'if [ "$1" = "test" ]; then exit ' + str(npm_test_koodi) + '; fi\nexit 0\n', True)
        return dict(PATH=str(bin_) + os.pathsep + os.environ["PATH"])

    def test_js_repo_asentaa_riippuvuudet_ja_ajaa_npm_test(self):
        env = self.js_repo(0)
        self.agentti_skripti('echo "uusi rivi" >> README.md')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "readme", **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        kutsut = (self.perus.parent / "npm.kutsut").read_text().splitlines()
        self.assertTrue(kutsut[0].startswith("install --ignore-scripts"), kutsut)
        self.assertEqual(kutsut[-1], "test")
        self.assertTrue(any(k.startswith("rebuild better-sqlite3") for k in kutsut), kutsut)
        self.assertLess([k.split()[0] for k in kutsut].index("rebuild"), len(kutsut) - 1)
        self.assertIn(REPO, self.kutsut())

    def test_js_repo_punainen_npm_test_estaa_prn(self):
        env = self.js_repo(1)
        self.agentti_skripti('echo "uusi rivi" >> README.md')
        self.pr_kutsu()
        self.aja(REPO, "1.00", "readme", **env)
        self.assertIn("punainen", self.raportti())
        self.assertEqual(self.kutsut(), "")

    def test_pr_kutsun_virhe_antaa_koodin_1_ja_syyn(self):
        self.agentti_skripti('echo "uusi" >> README.md')
        self.pr_kutsu(onnistuu=False)
        r = self.aja(REPO, "1.00", "readme")
        self.assertEqual(r.returncode, 1)
        rap = self.raportti()
        self.assertIn("pr_kutsu epaonnistui: hylatty", rap)
        self.assertIn("- PR: ei avattu", rap)

    def test_agentti_kaatuu_ei_muutoksia(self):
        kirjoita(self.agentti, "#!/bin/sh\nexit 3\n", True)
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "readme")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ei muutoksia", self.raportti())
        self.assertIn("JSON ei luettavissa", self.raportti())

    def test_kuivakoe_ei_kutsu_agenttia_eika_avaa_prta(self):
        kirjoita(self.agentti, "#!/bin/sh\ntouch \"$0.kaytetty\"\n", True)
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "readme", kuiva="1")
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.agentti.parent / "claude.kaytetty").exists())
        self.assertEqual(self.kutsut(), "")

    def test_env_tiedosto_luetaan_ja_valittyy_agentille(self):
        self.agentti_skripti('echo "${KOE_ARVO:-puuttuu}" > "$0.env"')
        self.pr_kutsu()
        self.aja(REPO, "1.00", "readme")
        self.assertEqual((self.agentti.parent / "claude.env").read_text().strip(), "1")


if __name__ == "__main__":
    unittest.main()
