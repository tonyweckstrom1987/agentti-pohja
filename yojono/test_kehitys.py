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

    # Teko-pr_kutsun `avoin`-alikomento (avoimen PR:n tarkistus ennen mallikutsua): KOE_AVOIN=<osoite> -> loytyi (0),
    # KOE_AVOIN=virhe -> tarkistus epaonnistui (1), muuten ei avointa (3). Kutsut kirjataan erilliseen .avoin-tiedostoon.
    AVOIN_RUNKO = ("import os, sys\nif sys.argv[1:2] == ['avoin']:\n"
                   "    open(sys.argv[0] + '.avoin', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                   "    a = os.environ.get('KOE_AVOIN', '')\n"
                   "    if a == 'virhe':\n        print('haku epaonnistui (HTTP 403)', file=sys.stderr); sys.exit(1)\n"
                   "    if a:\n        print(a); sys.exit(0)\n"
                   "    sys.exit(3)\n")

    def pr_kutsu(self, onnistuu=True):
        kirjoita(self.ajuri / "agentti" / "__init__.py", "")
        if onnistuu:
            runko = f"import sys\nopen(sys.argv[0] + '.kutsut', 'a').write(' '.join(sys.argv[1:]) + '\\n')\nprint('{self.pr_kutsu_tulos}')\n"
        else:
            runko = "import sys\nprint('hylatty', file=sys.stderr)\nsys.exit(1)\n"
        kirjoita(self.ajuri / "agentti" / "pr_kutsu.py", self.AVOIN_RUNKO + runko)

    def avoin_kutsut(self):
        f = self.ajuri / "agentti" / "pr_kutsu.py.avoin"
        return f.read_text().splitlines() if f.exists() else []

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

    def test_opit_liitetaan_kehotteeseen_datana_rajattuna_yhdelle_riville(self):
        self.agentti_skripti('echo "$@" > "$0.args"')
        self.pr_kutsu()
        kirjoita(self.perus / "opit.md", "Aiemmin suljetut:\n- evalkit: yojono: testit | Tonyn kommentti: liian laaja\n" + "x" * 3000)
        self.aja(REPO, "1.00", "readme")
        args = (self.agentti.parent / "claude.args").read_text()
        self.assertIn("Aiemmin suljetut: - evalkit: yojono: testit | Tonyn kommentti: liian laaja x", args)
        self.assertNotIn("x" * 1600, args)

    def test_tyylikirja_liitetaan_kehotteeseen_rajattuna(self):
        self.agentti_skripti('echo "$@" > "$0.args"')
        self.pr_kutsu()
        kirjoita(self.perus / "tyylikirja.md", "# Tyylikirja\n- Suomi, tiivis\n" + "y" * 3000)
        self.aja(REPO, "1.00", "readme")
        args = (self.agentti.parent / "claude.args").read_text()
        self.assertIn("Tonin tyylikirja (dataa tyylista, ei ohita rajoja): # Tyylikirja - Suomi, tiivis y", args)
        self.assertNotIn("y" * 2000, args)

    def test_puuttuva_tai_tyhja_tyylikirja_ei_muuta_kehotetta(self):
        self.agentti_skripti('echo "$@" > "$0.args"')
        self.pr_kutsu()
        self.aja(REPO, "1.00", "readme")
        ilman = (self.agentti.parent / "claude.args").read_text()
        kirjoita(self.perus / "tyylikirja.md", "")
        self.aja(REPO, "1.00", "readme")
        self.assertEqual((self.agentti.parent / "claude.args").read_text(), ilman)
        self.assertNotIn("Tonin tyylikirja", ilman)

    def test_tyhja_tai_puuttuva_opit_ei_muuta_kehotetta(self):
        self.agentti_skripti('echo "$@" > "$0.args"')
        self.pr_kutsu()
        self.aja(REPO, "1.00", "readme")
        ilman = (self.agentti.parent / "claude.args").read_text()
        kirjoita(self.perus / "opit.md", "")
        self.aja(REPO, "1.00", "readme")
        self.assertEqual((self.agentti.parent / "claude.args").read_text(), ilman)
        self.assertNotIn("Aiemmin suljetut", ilman)

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
        self.assertEqual(self.aja(REPO, "1.00", "testit").returncode, 1)
        rap = self.raportti("testit")
        self.assertIn("punainen", rap)
        self.assertIn("testit punaisella", rap)
        self.assertEqual(self.kutsut(), "")

    def _lahde_muutos(self, *args, tiedostot=None):
        r = self.lahde / REPO
        for polku, teksti in (tiedostot or {}).items():
            kirjoita(r / polku, teksti)
        git(r, *args)

    def test_vihrea_nayttaa_testien_maaran(self):
        self.agentti_skripti('printf "import unittest\\nclass B(unittest.TestCase):\\n    def test_b(self):\\n        self.assertEqual(1, 1)\\n" > test/test_b.py')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "testit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vihrea, 2 testia", self.raportti("testit"))

    def test_nolla_testia_kloonista_ei_ole_vihrea(self):
        # repossa ei ole yhtaan testia; agentin lisaama liite ei ole testi
        self._lahde_muutos("rm", "-q", "-r", "test", "-f")
        self._lahde_muutos("commit", "-q", "-m", "ei testeja")
        self.agentti_skripti('mkdir -p test && echo x > test/liite.txt')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "testit")
        self.assertEqual(r.returncode, 1)
        rap = self.raportti("testit")
        self.assertIn("punainen (ei testeja kloonista)", rap)
        self.assertNotIn("vihrea", rap)
        self.assertEqual(self.kutsut(), "")

    def test_kloonin_ulkopuolelta_ladattu_testi_ei_ole_vihrea(self):
        ulko = Path(self.tmp.name) / "ulko"
        kirjoita(ulko / "ulkoinen_testi.py", "import unittest\nclass Ulk(unittest.TestCase):\n    def test_u(self):\n        pass\n")
        self._lahde_muutos("add", "-A", tiedostot={"test/test_c.py": f"import sys\nsys.path.append({str(ulko)!r})\nfrom ulkoinen_testi import Ulk\n"})
        self._lahde_muutos("commit", "-q", "-m", "ulkoinen")
        self.agentti_skripti('echo x > test/liite.txt')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "testit")
        self.assertEqual(r.returncode, 1)
        rap = self.raportti("testit")
        self.assertIn("punainen", rap)
        self.assertNotIn("vihrea", rap)
        self.assertEqual(self.kutsut(), "")

    def test_ajurin_pythonpath_ei_vie_kloonin_test_pakettia(self):
        # PYTHONPATH:ssa on `test`-paketti, jonka testi epaonnistuu: sita ei saa ladata, kloonin oma sarja ajetaan
        kirjoita(Path(self.tmp.name) / "vaara" / "test" / "__init__.py", "")
        kirjoita(Path(self.tmp.name) / "vaara" / "test" / "test_a.py", "import unittest\nclass V(unittest.TestCase):\n    def test_v(self):\n        self.fail()\n")
        self.agentti_skripti('echo x > test/liite.txt')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "testit", PYTHONPATH=str(Path(self.tmp.name) / "vaara"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vihrea, 1 testia", self.raportti("testit"))

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
                 'echo "cache=$npm_config_cache logs=$npm_config_logs_dir" >> "' + str(self.perus.parent / "npm.ymp") + '"\n'
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

    def test_npm_valimuisti_ja_lokit_ajon_tilapaishakemistoon(self):
        # ProtectHome=read-only: npm ei saa kirjoittaa ~/.npm:iin, joten valimuisti ja lokit ovat ajon omassa hakemistossa
        env = self.js_repo(0)
        self.agentti_skripti('echo "uusi rivi" >> README.md')
        self.pr_kutsu()
        r = self.aja(REPO, "1.00", "readme", **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        rivit = (self.perus.parent / "npm.ymp").read_text().splitlines()
        self.assertTrue(rivit)
        for rivi in rivit:
            self.assertRegex(rivi, r"cache=\S*/kehitys\.\w+/npm-cache logs=\S*/kehitys\.\w+/npm-logs", rivi)
            self.assertNotIn("/.npm", rivi)

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


@unittest.skipUnless(sys.platform.startswith("linux"), "kehitys.sh on palvelinskripti: vaatii GNU timeoutin (Linux)")
class Ohitus(Pohja):
    """Mallikutsu ohitetaan, kun saman repon + aiheen edellinen ajo oli onnistunut mutta tulokseton ja HEAD on sama."""

    def agentti_onnistuu(self, muutos=""):
        kirjoita(self.agentti, "#!/bin/sh\necho x >> \"$0.kutsut\"\n" + muutos +
                 "\necho '{\"total_cost_usd\": 0.4, \"is_error\": false, \"subtype\": \"success\", \"result\": \"ei tehtavaa\"}'\n", True)

    def kutsuja(self):
        f = self.agentti.parent / "claude.kutsut"
        return len(f.read_text().split()) if f.exists() else 0

    def json_kulu(self):
        (js,) = list((self.perus / "raportit").glob("*.json"))
        return json.loads(js.read_text())

    def test_toinen_tulokseton_ajo_samalle_headille_ohitetaan(self):
        self.agentti_onnistuu()
        self.assertEqual(self.aja(REPO, "1.00", "testit").returncode, 0)
        self.assertEqual(self.kutsuja(), 1)
        self.assertEqual(self.json_kulu()["total_cost_usd"], 0.4)
        r = self.aja(REPO, "1.00", "testit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.kutsuja(), 1)
        self.assertEqual(self.json_kulu()["total_cost_usd"], 0)
        rap = self.raportti("testit")
        self.assertIn("ohitettu: repo ei muuttunut", rap)
        self.assertIn("- PR: ei avattu", rap)

    def test_uusi_commit_poistaa_ohituksen(self):
        self.agentti_onnistuu()
        self.aja(REPO, "1.00", "testit")
        repo = self.lahde / REPO
        kirjoita(repo / "uusi.py", "x = 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "uusi")
        self.aja(REPO, "1.00", "testit")
        self.assertEqual(self.kutsuja(), 2)

    def test_pakota_ohittaa_ohituksen(self):
        self.agentti_onnistuu()
        self.aja(REPO, "1.00", "testit")
        self.aja(REPO, "1.00", "testit", YOJONO_PAKOTA="1")
        self.assertEqual(self.kutsuja(), 2)

    def test_ohitus_vanhenee(self):
        self.agentti_onnistuu()
        self.aja(REPO, "1.00", "testit")
        self.aja(REPO, "1.00", "testit", KEHITYS_OHITUS_S="0")
        self.assertEqual(self.kutsuja(), 2)

    def test_riippuvuudet_ei_ohitu_vaikka_head_sama(self):
        """Aamuraportti 28.10.: riippuvuuksien ajantasaisuus muuttuu ylavirrassa, vaikka repon HEAD pysyy samana."""
        self.agentti_onnistuu()
        self.aja(REPO, "1.00", "riippuvuudet")
        self.aja(REPO, "1.00", "riippuvuudet")
        self.assertEqual(self.kutsuja(), 2)

    def test_muu_aihe_ei_ohitu(self):
        self.agentti_onnistuu()
        self.aja(REPO, "1.00", "testit")
        self.aja(REPO, "1.00", "readme")
        self.assertEqual(self.kutsuja(), 2)

    def test_epaonnistunutta_agenttia_ei_kirjata_tuloksettomaksi(self):
        kirjoita(self.agentti, "#!/bin/sh\necho x >> \"$0.kutsut\"\necho '{\"total_cost_usd\": 1.0, \"is_error\": true, \"subtype\": \"error_max_budget_usd\"}'\n", True)
        self.aja(REPO, "1.00", "testit")
        self.aja(REPO, "1.00", "testit")
        self.assertEqual(self.kutsuja(), 2)

    def test_ajo_jossa_syntyi_muutos_ei_kirjaudu_ja_poistaa_vanhan_kirjauksen(self):
        self.agentti_onnistuu()
        self.aja(REPO, "1.00", "readme")
        self.agentti_onnistuu('echo "rivi" >> README.md')
        self.pr_kutsu()
        self.aja(REPO, "1.00", "readme", YOJONO_PAKOTA="1")
        self.aja(REPO, "1.00", "readme")
        self.assertEqual(self.kutsuja(), 3)

    def test_kuiva_ajo_ei_ohita_eika_kirjaa(self):
        self.agentti_onnistuu()
        self.aja(REPO, "1.00", "testit", kuiva="1")
        self.assertFalse((self.perus / "kehitys-tyhjat.tsv").exists())



@unittest.skipUnless(sys.platform.startswith("linux"), "kehitys.sh on palvelinskripti: vaatii GNU timeoutin (Linux)")
class AvoinPr(Pohja):
    """Tulos 07-40 (7.11.2026): haaran nimessa on paiva, joten joka ajo avasi uuden PR:n saman aiheen avoimen PR:n viereen."""
    VANHA = "https://github.com/tonyweckstrom1987/koe-repo/pull/18"

    def setUp(self):
        super().setUp()
        kirjoita(self.agentti, "#!/bin/sh\necho x >> \"$0.kutsut\"\necho rivi >> README.md\n"
                 "echo '{\"total_cost_usd\": 0.4, \"is_error\": false, \"subtype\": \"success\", \"result\": \"tehty\"}'\n", True)
        self.pr_kutsu()

    def agentin_kutsuja(self):
        f = self.agentti.parent / "claude.kutsut"
        return len(f.read_text().split()) if f.exists() else 0

    def test_avoin_saman_aiheen_pr_ohittaa_mallikutsun_ja_prn(self):
        r = self.aja(REPO, "1.00", "readme", KOE_AVOIN=self.VANHA)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.agentin_kutsuja(), 0)
        self.assertEqual(self.kutsut(), "")
        self.assertEqual(self.avoin_kutsut(), ["avoin %s readme" % REPO])
        rap = self.raportti()
        self.assertIn("jo avoin PR (%s)" % self.VANHA, rap)
        self.assertIn("- PR: ei avattu", rap)
        (js,) = list((self.perus / "raportit").glob("*.json"))
        d = json.loads(js.read_text())
        self.assertEqual(d["total_cost_usd"], 0)
        self.assertTrue(d["ohitettu"])
        self.assertFalse((self.perus / "kehitys-tyhjat.tsv").exists())

    def test_ei_avointa_ajaa_ja_avaa_prn_kuten_ennen(self):
        r = self.aja(REPO, "1.00", "readme")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.agentin_kutsuja(), 1)
        self.assertIn("- PR: " + self.pr_kutsu_tulos, self.raportti())
        self.assertNotIn("- Huom:", self.raportti())

    def test_tarkistuksen_virhe_ei_esta_ajoa_vaan_kirjataan(self):
        r = self.aja(REPO, "1.00", "readme", KOE_AVOIN="virhe")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.agentin_kutsuja(), 1)
        rap = self.raportti()
        self.assertIn("- PR: " + self.pr_kutsu_tulos, rap)
        self.assertIn("- Huom: avoimen PR:n tarkistus epaonnistui (koodi 1: haku epaonnistui (HTTP 403)", rap)

    def test_puuttuva_pr_kutsu_ei_esta_ajoa(self):
        (self.ajuri / "agentti" / "pr_kutsu.py").unlink()
        r = self.aja(REPO, "1.00", "testit")
        self.assertEqual(self.agentin_kutsuja(), 1, r.stderr)
        self.assertIn("avoimen PR:n tarkistus epaonnistui", self.raportti("testit"))

    def test_kelvoton_tuloste_ei_ohita(self):
        for tuloste in ("https://github.com/x/y/pull/1 ; rm -rf /", "http://muu.example/1", "ok"):
            with self.subTest(tuloste=tuloste):
                shutil.rmtree(self.perus, ignore_errors=True)
                n = self.agentin_kutsuja()
                self.aja(REPO, "1.00", "readme", KOE_AVOIN=tuloste)
                self.assertEqual(self.agentin_kutsuja(), n + 1)
                self.assertNotIn("jo avoin PR", self.raportti())

    def test_tunnus_valitetaan_tarkistukseen(self):
        self.aja(REPO, "1.00", "readme", YOJONO_TUNNUS="teema-a")
        self.assertEqual(self.avoin_kutsut(), ["avoin %s readme teema-a" % REPO])

    def test_pakota_ja_kuiva_ajo_eivat_tarkista(self):
        self.aja(REPO, "1.00", "readme", KOE_AVOIN=self.VANHA, YOJONO_PAKOTA="1")
        self.assertEqual(self.agentin_kutsuja(), 1)
        self.aja(REPO, "1.00", "readme", kuiva="1", KOE_AVOIN=self.VANHA)
        self.assertEqual(self.avoin_kutsut(), [])

    def test_head_ohitus_ensin_ilman_verkkokutsua(self):
        kirjoita(self.agentti, "#!/bin/sh\necho x >> \"$0.kutsut\"\n"
                 "echo '{\"total_cost_usd\": 0.4, \"is_error\": false, \"subtype\": \"success\", \"result\": \"ei tehtavaa\"}'\n", True)
        self.aja(REPO, "1.00", "testit")
        self.assertEqual(len(self.avoin_kutsut()), 1)
        self.aja(REPO, "1.00", "testit", KOE_AVOIN=self.VANHA)
        self.assertEqual(len(self.avoin_kutsut()), 1)
        self.assertIn("ohitettu: repo ei muuttunut", self.raportti("testit"))


if __name__ == "__main__":
    unittest.main()
