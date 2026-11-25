import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import automerge as am

SHA = "a" * 40
MUU_SHA = "b" * 40
PR = {"draft": False, "head_repo": "o/r", "base_repo": "o/r", "labels": [], "author": "tonyweckstrom1987",
      "head_ref": "korjaus/x", "base_ref": "main", "head_sha": SHA}
BOTTI_PR = dict(PR, head_repo="agenttifirma-bot/r", author="agenttifirma-bot", head_ref="yo/jotain-2026-11-04")


def tied(nimi, status="modified", changes=5, patch="@@ -1 +1 @@\n-a\n+b", **lisa):
    d = {"filename": nimi, "status": status, "changes": changes, "patch": patch}
    d.update(lisa)
    return d


def hyvaksynta(sha=SHA, paatos="HYVAKSYTTY", kuka="agenttifirma-bot"):
    return {"user": kuka, "body": "Tarkastus\n\n<!-- riippumaton-tarkastus v1 sha=%s paatos=%s -->" % (sha, paatos)}


OK = [hyvaksynta()]


class RajatutTesti(unittest.TestCase):
    def test_tavallinen_koodi_kelpaa_tarkastettuna(self):
        for nimi in ("agentti/ajo.py", "claudejono/ajuri.py", "isanta/asenna.sh", "tools/mutantit.py", "README.md",
                     "test/test_x.py", "yojono/test_uusi.py", "requirements.txt", "docs/a.md"):
            ok, syyt = am.arvioi(PR, [tied(nimi)], OK)
            self.assertTrue(ok, (nimi, syyt))

    def test_rajatut_polut(self):
        for nimi, luokka in ((".github/workflows/ci.yml", "tyonkulut"), ("tools/automerge.py", "automergen"),
                             ("agentti/pr_kutsu.py", "oikeudet"), ("agentti/github_app.py", "oikeudet"),
                             ("claudejono/claudejono-pr", "oikeudet"), ("claudejono/sudoers.claudejono", "oikeudet"),
                             ("claudejono/asenna-kayttaja.sh", "oikeudet"), ("agentti/rajat.py", "kulukatot"),
                             ("agentti/config.py", "kulukatot"), ("claudejono/varaus.py", "kulukatot"),
                             ("yojono/ajuri.py", "kulukatot"), ("isanta/nftables.conf", "palomuuri"),
                             ("isanta/palomuuri.sh", "palomuuri"), (".env", "salaisuudet"), ("x/.env.local", "salaisuudet"),
                             ("docs/api_key.md", "salaisuudet"), ("a/token.txt", "salaisuudet"), ("k.pem", "salaisuudet"),
                             ("ssh/id_ed25519", "salaisuudet"), ("action.yml", "julkaistu"), ("dist/index.js", "julkaistu"),
                             ("../x.py", "polku"), ("/etc/passwd", "polku"), ("a b.py", "polku")):
            ok, syyt = am.arvioi(PR, [tied(nimi)], OK)
            self.assertFalse(ok, nimi)
            self.assertTrue(any(luokka in s for s in syyt), (nimi, syyt))

    def test_uudelleennimeaminen_rajatusta_ei_kelpaa(self):
        ok, syyt = am.arvioi(PR, [tied("agentti/x.py", status="renamed", previous_filename="agentti/rajat.py")], OK)
        self.assertFalse(ok)
        self.assertIn("rajattu (kulukatot ja rajat): agentti/rajat.py", syyt)

    def test_poisto_ja_uudelleennimeaminen_kelpaavat_muualla(self):
        self.assertTrue(am.arvioi(PR, [tied("agentti/vanha.py", status="removed", patch=None, changes=0)], OK)[0])
        self.assertTrue(am.arvioi(PR, [tied("agentti/uusi.py", status="renamed", previous_filename="agentti/vanha.py")], OK)[0])

    def test_yojonon_testi_kelpaa_mutta_muu_yojono_ei(self):
        self.assertTrue(am.arvioi(PR, [tied("yojono/test_ajuri.py")], OK)[0])
        for nimi in ("yojono/ajuri.py", "yojono/test_x.sh", "yojono/test_x.py\n", "yojono/sub/test_x.py"):
            self.assertFalse(am.arvioi(PR, [tied(nimi)], OK)[0], nimi)

    def test_kauppatiimin_raha_rajattu_vain_kauppatiimissa(self):
        kt = dict(PR, head_repo="o/kauppatiimi", base_repo="o/kauppatiimi")
        for nimi in ("riskivahti.py", "src/salkku.py", "kirjanpito.py", "asetukset.py", "data/x.csv", "kt.service", "a.db",
                     "k7.py", "src/k7.py", "systemd/kt.timer.malli", "kt.service.malli", "binance_keys.py"):
            ok, syyt = am.arvioi(kt, [tied(nimi)], OK)
            self.assertFalse(ok, nimi)
            self.assertTrue(any("oikea raha" in s for s in syyt), syyt)
        self.assertTrue(am.arvioi(kt, [tied("strategia.py")], OK)[0])
        self.assertTrue(am.arvioi(PR, [tied("salkku.py")], OK)[0])

    def test_rajatut_repot(self):
        for repo in ("o/tonyweckstrom1987.github.io", "o/agenttifirma-docs"):
            ok, syyt = am.arvioi(dict(PR, head_repo=repo, base_repo=repo), [tied("README.md")], OK)
            self.assertFalse(ok, repo)
            self.assertTrue(any(s.startswith("rajattu repo") for s in syyt), syyt)

    def test_aivon_rajatut_polut(self):
        for nimi, luokka in (("aivo/luovutus.py", "data ulos"), ("aivo/luovutuspaketti.py", "data ulos"),
                             ("aivo/yksityinen.py", "data ulos"), ("aivo/yksityinen_or.py", "data ulos"),
                             ("aivo/openrouter_zdr.py", "data ulos"), ("aivo/vuotovartija.py", "data ulos"),
                             ("aivo/agenttidata_kehote.py", "data ulos"), ("aivo/openrouter_kulut.py", "kulukatot"),
                             ("aivo/taustaajo.py", "kulukatot"), ("aivo/kulut_nakyma.py", "kulukatot"),
                             ("systemd/aivo-agentit.service.mall", "oikeudet"),
                             ("systemd/aivo-chat.service.mall", "oikeudet"), ("docs/aivo-agentit.md", "oikeudet"),
                             ("aivo/chat.py", "data ulos"), ("aivo/tiivista_yksityinen.py", "data ulos"),
                             ("aivo/drive.py", "data ulos"), ("aivo/yhdistelma.py", "kulukatot"),
                             ("aivo/mallivertailu.py", "kulukatot"), ("aivo/agentti_ajuri.py", "kulukatot"),
                             ("aivo/agentti/malli.py", "kulukatot"), ("docs/aivo-chat.md", "oikeudet"),
                             ("docs/aivo-yksityinen.md", "oikeudet"), ("docs/aivo-yksityinen-openrouter.md", "oikeudet")):
            ok, syyt = am.arvioi(PR, [tied(nimi)], OK)
            self.assertFalse(ok, nimi)
            self.assertTrue(any(luokka in s and "aivo" in s for s in syyt), (nimi, syyt))

    def test_aivon_testit_ja_muu_aivo_kelpaavat(self):
        for nimi in ("aivo/test_luovutus.py", "aivo/test_taustaajo.py", "aivo/test_openrouter_kulut.py",
                     "aivo/test_chat.py", "aivo/kalenteri.py", "aivo/agentti/silmukka.py", "docs/aivo-drive.md"):
            ok, syyt = am.arvioi(PR, [tied(nimi)], OK)
            self.assertTrue(ok, (nimi, syyt))

    def test_rajattu_sana_otsikossa_tai_kuvauksessa_estaa(self):
        for lisa in ({"title": "[yojono] aivo: RAJATTU muutos"}, {"body": "Kuvaus\n\nRAJATTU: odottaa Tonyn kyllaa."}):
            ok, syyt = am.arvioi(dict(PR, **lisa), [tied("docs/a.md")], OK)
            self.assertFalse(ok, lisa)
            self.assertIn("otsikossa tai kuvauksessa RAJATTU", syyt)
        ok, syyt = am.arvioi(dict(PR, title="rajattu pienella", body="ei rajattua"), [tied("docs/a.md")], OK)
        self.assertTrue(ok, syyt)

    def test_rajat_ovat_kaikki_pr_kutsun_suojatut(self):
        # Mita pr_kutsu pitaa Tonyn polkuna, sita ei automergeta (aamupaketti ja autonomia luokittelevat samoin).
        from agentti import pr_kutsu
        for nimi in (".github/workflows/ci.yml", "tools/automerge.py", "agentti/pr_kutsu.py", "agentti/rajat.py",
                     "agentti/config.py", "claudejono/varaus.py", "claudejono/sudoers.claudejono", "yojono/a.py",
                     "x/.env", "token.txt", "../x.md"):
            self.assertTrue(pr_kutsu.suojattu(nimi), nimi)
            self.assertIsNotNone(am.rajattu("agentti-pohja", nimi), nimi)


class SisaltoTesti(unittest.TestCase):
    def test_yksityinen_osoite_lisatylla_rivilla_estaa(self):
        for rivi in ("url = 'http://192." + "168.1.116:4000'", "10." + "0.0.5", "172." + "20.1.1", "host.tail1234.ts" + ".net", "100." + "101.2.3"):
            ok, syyt = am.arvioi(PR, [tied("a.py", patch="@@ -1 +1 @@\n-x\n+" + rivi)], OK)
            self.assertFalse(ok, rivi)
            self.assertIn("lisatylla rivilla yksityinen osoite tai tailnet-nimi: a.py", syyt)

    def test_poistettu_osoite_ja_tavallinen_teksti_eivat_esta(self):
        for patch in ("@@ -1 +1 @@\n-192." + "168.1.116\n+CLAUDEJONO_URL", "@@\n+versio 1.2.3\n+127.0.0.1\n+8.8.8.8",
                      "@@\n+172.15.0.1 ja 172.32.0.1\n+100.63.1.1"):
            self.assertTrue(am.arvioi(PR, [tied("a.py", patch=patch)], OK)[0], patch)

    def test_puuttuva_patch_estaa_kun_rivit_muuttuvat(self):
        ok, syyt = am.arvioi(PR, [tied("kuva.png", patch=None, changes=0), tied("iso.py", patch=None, changes=900)], OK)
        self.assertFalse(ok)
        self.assertEqual(syyt, ["patch puuttuu (binaari tai liian suuri): iso.py"])


class TarkastajaTesti(unittest.TestCase):
    def test_ilman_tarkastusta_ei_kelpaa(self):
        ok, syyt = am.arvioi(PR, [tied("a.py")])
        self.assertFalse(ok)
        self.assertEqual(syyt, ["riippumaton tarkastus: odottaa"])

    def test_vain_tarkastajan_kommentti_ja_oikea_sha(self):
        for kommentit in ([hyvaksynta(kuka="tonyweckstrom1987")], [hyvaksynta(kuka="joku")], [hyvaksynta(sha=MUU_SHA)],
                          [{"user": "agenttifirma-bot", "body": "riippumaton-tarkastus v1 sha=%s paatos=HYVAKSYTTY" % SHA}],
                          [{"user": "agenttifirma-bot", "body": None}], [{"user": None, "body": hyvaksynta()["body"]}]):
            self.assertFalse(am.arvioi(PR, [tied("a.py")], kommentit)[0], kommentit)

    def test_kaksi_merkkia_samassa_kommentissa_ei_kelpaa(self):
        kaksi = {"user": "agenttifirma-bot", "body": hyvaksynta(paatos="HYLATTY")["body"] + "\n" + hyvaksynta()["body"]}
        self.assertFalse(am.arvioi(PR, [tied("a.py")], [kaksi])[0])

    def test_viimeisin_paatos_ratkaisee(self):
        self.assertFalse(am.arvioi(PR, [tied("a.py")], [hyvaksynta(), hyvaksynta(paatos="HYLATTY")])[0])
        self.assertTrue(am.arvioi(PR, [tied("a.py")], [hyvaksynta(paatos="HYLATTY"), hyvaksynta()])[0])
        self.assertTrue(am.arvioi(PR, [tied("a.py")], [hyvaksynta(), hyvaksynta(sha=MUU_SHA, paatos="HYLATTY")])[0])

    def test_muut_paatokset_nakyvat_syyna(self):
        for p in ("HYLATTY", "RAJATTU", "EN_TIEDA"):
            ok, syyt = am.arvioi(PR, [tied("a.py")], [hyvaksynta(paatos=p)])
            self.assertFalse(ok)
            self.assertEqual(syyt, ["riippumaton tarkastus: " + p])

    def test_kelvoton_head_sha(self):
        for sha in ("", None, "A" * 40, "a" * 39, SHA + "\n"):
            self.assertFalse(am.arvioi(dict(PR, head_sha=sha), [tied("a.py")], [hyvaksynta(sha=(sha or "x"))])[0], sha)


class BotinForkkiTesti(unittest.TestCase):
    def test_botin_pr_kelpaa(self):
        self.assertTrue(am.arvioi(BOTTI_PR, [tied("agentti/ajo.py")], OK)[0])

    def test_muu_tekija_tai_forkki_ei_kelpaa(self):
        for kentat in ({"author": "joku"}, {"author": None}, {"head_repo": "joku/r"}, {"head_repo": "agenttifirma-bot/muu"},
                       {"head_ref": "main"}, {"head_ref": "yo/"}, {"head_ref": "yo/A"}, {"head_ref": None},
                       {"head_ref": "feature/x"}):
            self.assertFalse(am.arvioi(dict(BOTTI_PR, **kentat), [tied("a.py")], OK)[0], kentat)

    def test_yleiset_ehdot(self):
        for kentat in ({"draft": True}, {"labels": ["ei-automerge"]}, {"base_ref": "kehitys"}, {"base_ref": None}):
            self.assertFalse(am.arvioi(dict(PR, **kentat), [tied("a.py")], OK)[0], kentat)
        self.assertTrue(am.arvioi(dict(PR, base_ref="master"), [tied("a.py")], OK)[0])
        self.assertFalse(am.arvioi(PR, [], OK)[0])
        self.assertFalse(am.arvioi(PR, [tied("a.py", changes=am.MAX_RIVEJA + 1)], OK)[0])
        self.assertTrue(am.arvioi(PR, [tied("a.py", changes=am.MAX_RIVEJA)], OK)[0])
        self.assertFalse(am.arvioi(PR, [tied("a%d.py" % i, changes=1) for i in range(am.MAX_TIEDOSTOA + 1)], OK)[0])
        self.assertTrue(am.arvioi(PR, [tied("a%d.py" % i, changes=1) for i in range(am.MAX_TIEDOSTOA)], OK)[0])


class WorkflowTesti(unittest.TestCase):
    def test_workflow_ei_lataa_pr_koodia_ja_rajaa_kommentin(self):
        teksti = (pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "automerge.yml").read_text()
        self.assertIn("pull_request_target", teksti)
        self.assertIn("issue_comment", teksti)
        self.assertIn("github.event.comment.user.login == 'agenttifirma-bot'", teksti)
        self.assertIn("head.repo.owner.login == 'agenttifirma-bot'", teksti)
        self.assertNotIn("pull_request.head.sha", teksti)
        self.assertNotIn("head.ref", teksti)
        self.assertNotIn("ref:", teksti)


def gh_feikki(pr, tiedostot, kommentit, kutsut, merge_koodi=0):
    def f(args, **kw):
        kutsut.append(args)
        osat = args[1:]
        if osat[0] == "api" and "--paginate" in osat:
            lahde = kommentit if "/comments" in osat[2] else tiedostot
            ulos = "\n".join(json.dumps(t) for t in lahde)
        elif osat[0] == "api":
            ulos = json.dumps({"draft": pr["draft"], "state": pr.get("state", "open"), "user": {"login": pr["author"]},
                               "head": {"repo": {"full_name": pr["head_repo"]}, "ref": pr["head_ref"], "sha": pr["head_sha"]},
                               "base": {"repo": {"full_name": pr["base_repo"]}, "ref": pr["base_ref"]},
                               "labels": [{"name": n} for n in pr["labels"]]})
        elif osat[:2] == ["pr", "merge"] and "--auto" in osat:
            return subprocess.CompletedProcess(args, merge_koodi, "", "A" * 100 + "B" * 200 if merge_koodi else "")
        else:
            ulos = ""
        return subprocess.CompletedProcess(args, 0, ulos, "")
    return f


class MainTesti(unittest.TestCase):
    def aja(self, pr, tiedostot, kommentit, merge_koodi=0):
        kutsut = []
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "PR_NUMBER": "7"}, clear=False), \
                mock.patch.object(am.subprocess, "run", gh_feikki(pr, tiedostot, kommentit, kutsut, merge_koodi)), \
                mock.patch("builtins.print") as tuloste:
            koodi = am.main()
        return koodi, kutsut, tuloste

    def test_kelpaava_kytketaan_head_commitiin_sidottuna(self):
        koodi, kutsut, _ = self.aja(PR, [tied("a.py")], OK)
        self.assertEqual(koodi, 0)
        self.assertIn(["gh", "pr", "merge", "7", "--auto", "--squash", "--match-head-commit", SHA, "--repo", "o/r"], kutsut)

    def test_kelpaamaton_poistaa_automergen(self):
        koodi, kutsut, tuloste = self.aja(PR, [tied("a.py")], [])
        self.assertEqual(koodi, 0)
        self.assertIn(["gh", "pr", "merge", "7", "--disable-auto", "--repo", "o/r"], kutsut)
        self.assertIn("riippumaton tarkastus: odottaa", tuloste.call_args[0][0])

    def test_suljettu_pr_ei_tee_mitaan(self):
        koodi, kutsut, _ = self.aja(dict(PR, state="closed"), [tied("a.py")], OK)
        self.assertEqual(koodi, 0)
        self.assertFalse(any(k[1:3] == ["pr", "merge"] for k in kutsut))

    def test_kytkenta_epaonnistuu(self):
        koodi, _, tuloste = self.aja(PR, [tied("a.py")], OK, merge_koodi=1)
        self.assertEqual(koodi, 1)
        self.assertEqual(tuloste.call_args[0][0], "Automerge: kytkenta epaonnistui: " + "B" * 200)

    def test_virheellinen_ymparisto(self):
        for env in ({"GITHUB_REPOSITORY": "x", "PR_NUMBER": "7"}, {"GITHUB_REPOSITORY": "o/r", "PR_NUMBER": "a"}):
            with mock.patch.dict("os.environ", env, clear=False), mock.patch("builtins.print"):
                self.assertEqual(am.main(), 2)

    def test_hakuvirhe_ei_tee_mitaan(self):
        def f(args, **kw):
            return subprocess.CompletedProcess(args, 1, "", "A" * 100 + "B" * 200)
        with mock.patch.dict("os.environ", {"GITHUB_REPOSITORY": "o/r", "PR_NUMBER": "7"}, clear=False), \
                mock.patch.object(am.subprocess, "run", f), mock.patch("builtins.print") as tuloste:
            self.assertEqual(am.main(), 1)
        self.assertEqual(tuloste.call_args[0][0], "Automerge: arviointi epaonnistui (PR:n haku epaonnistui: %s), ei tehda mitaan" % ("B" * 200))

    def test_hakufunktioiden_virheet(self):
        def f(args, **kw):
            return subprocess.CompletedProcess(args, 1, "", "virhe")
        with mock.patch.object(am.subprocess, "run", f):
            for fn, alku in ((am.hae_tiedostot, "tiedostojen"), (am.hae_kommentit, "kommenttien")):
                with self.assertRaises(RuntimeError) as e:
                    fn("o/r", "7")
                self.assertTrue(str(e.exception).startswith(alku), str(e.exception))

    def test_gh_kutsun_parametrit(self):
        nahty = {}

        def f(args, **kw):
            nahty["args"], nahty["kw"] = args, kw
            return subprocess.CompletedProcess(args, 0, "", "")
        with mock.patch.object(am.subprocess, "run", f):
            am._gh("pr", "view")
        self.assertEqual(nahty["args"], ["gh", "pr", "view"])
        self.assertEqual(nahty["kw"], {"capture_output": True, "text": True, "timeout": 120})

    def test_yhteenveto_kirjoittaa_tiedostoon(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "y.md")
            with mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": polku}), mock.patch("builtins.print"):
                am.yhteenveto("eka")
                am.yhteenveto("toka")
            self.assertEqual(pathlib.Path(polku).read_text(), "eka\ntoka\n")
        with mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": "/ei/ole/y.md"}), mock.patch("builtins.print") as p:
            am.yhteenveto("x")
        p.assert_called_once_with("x")


if __name__ == "__main__":
    unittest.main()
