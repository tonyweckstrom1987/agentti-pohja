"""Elonmerkki: hiljainen jono pushaa claude-jono/elonmerkki.md:n enintaan kerran 2 h:ssa (ulkoinen valvoja, 21.11.2026)."""
import subprocess
from datetime import datetime, timedelta
from unittest import mock

from claudejono import ajuri
from claudejono.test_ajuri import Pohja, git


def origin_log(pohja):
    r = subprocess.run(["git", "--git-dir", str(pohja.origin), "log", "--format=%s", "main"],
                       capture_output=True, text=True, check=True)
    return r.stdout.splitlines()


class Elonmerkki(Pohja):
    def test_vanha_commit_pushaa_elonmerkin(self):
        nyt = datetime.now().astimezone() + timedelta(hours=3)
        self.assertTrue(ajuri.elonmerkki(self.repo, "jono riittaa (avoimia tehtavia yli 2 x kaistat)", nyt=nyt))
        self.assertEqual(origin_log(self)[0], "claude-jono: elonmerkki")
        teksti = (self.repo / "claude-jono" / "elonmerkki.md").read_text(encoding="utf-8")
        self.assertIn("jono riittaa", teksti)
        self.assertIn(nyt.isoformat(timespec="minutes"), teksti)

    def test_tuore_oma_commit_ei_pushaa(self):
        nyt = datetime.now().astimezone() + timedelta(minutes=30)
        self.assertFalse(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt))
        self.assertNotIn("claude-jono: elonmerkki", origin_log(self))
        self.assertFalse((self.repo / "claude-jono" / "elonmerkki.md").exists())

    def test_toisen_kirjoittajan_commit_ei_estaa(self):
        # Tonyn tai muun istunnon commit ei todista, etta tama kone on paalla
        git(self.repo, "config", "user.email", "toinen@t")
        (self.repo / "muu.md").write_text("x\n", encoding="utf-8")
        git(self.repo, "add", "muu.md")
        git(self.repo, "commit", "-q", "-m", "toinen")
        git(self.repo, "config", "user.email", "ct115@t")
        nyt = datetime.now().astimezone() + timedelta(minutes=10)
        self.assertTrue(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt))

    def test_toinen_kerta_vasta_valin_jalkeen(self):
        nyt = datetime.now().astimezone() + timedelta(hours=3)
        with mock.patch.dict("os.environ", {"GIT_COMMITTER_DATE": nyt.isoformat()}):   # commitin aika = nyt
            self.assertTrue(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt))
        self.assertFalse(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt + timedelta(hours=1)))
        self.assertTrue(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt + timedelta(hours=2, minutes=1)))
        self.assertEqual(origin_log(self).count("claude-jono: elonmerkki"), 2)

    def test_virhe_ei_kaada_eika_jata_likaista_tyopuuta(self):
        nyt = datetime.now().astimezone() + timedelta(hours=3)
        with mock.patch.object(ajuri, "julkaise", side_effect=ajuri.JonoVirhe("testi")):
            self.assertFalse(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt))
        tila = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"],
                              capture_output=True, text=True, check=True).stdout
        self.assertEqual(tila, "")

    def test_merkit_siivotaan(self):
        nyt = datetime.now().astimezone() + timedelta(hours=3)
        self.assertTrue(ajuri.elonmerkki(self.repo, "jono\n# otsikko\x1b[31m", nyt=nyt))
        teksti = (self.repo / "claude-jono" / "elonmerkki.md").read_text(encoding="utf-8")
        self.assertNotIn("\x1b", teksti)
        self.assertNotIn("\n# otsikko", teksti)

    def test_konenimi_ajonaikaisesti(self):
        nyt = datetime.now().astimezone() + timedelta(hours=3)
        with mock.patch.object(ajuri.socket, "gethostname", return_value="kone-x\n# y"):
            self.assertTrue(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt))
        teksti = (self.repo / "claude-jono" / "elonmerkki.md").read_text(encoding="utf-8")
        self.assertIn("# Elonmerkki (kone-x?# y, claude-jono)", teksti)
        self.assertIn("- Kone: kone-x?# y", teksti)
        self.assertNotIn("CT115", teksti)

    def test_pushaamaton_oma_commit_ei_estaa(self):
        # julkaise kaatui commitin jalkeen ennen pushia: paikallinen commit ei nay originissa, joten uusi yritys tehdaan
        (self.repo / "paikallinen.md").write_text("x\n", encoding="utf-8")
        git(self.repo, "add", "paikallinen.md")
        git(self.repo, "commit", "-q", "-m", "pushaamaton")
        nyt = datetime.now().astimezone() + timedelta(hours=3)
        with mock.patch.dict("os.environ", {"GIT_COMMITTER_DATE": nyt.isoformat()}):
            git(self.repo, "commit", "-q", "--amend", "--no-edit")
        self.assertTrue(ajuri.elonmerkki(self.repo, "jono tyhja", nyt=nyt + timedelta(minutes=5)))
        self.assertIn("claude-jono: elonmerkki", origin_log(self))

    def test_sposti_ei_ole_regex_eika_osajono(self):
        # t.t -sposti ei saa tunnistaa committeja osoitteesta tXt tai xt.t (regex / osajono)
        for vieras in ("tXt@t", "xt.t@t"):
            git(self.repo, "config", "user.email", vieras)
            (self.repo / f"{vieras}.md").write_text("x\n", encoding="utf-8")
            git(self.repo, "add", f"{vieras}.md")
            git(self.repo, "commit", "-q", "-m", vieras)
            git(self.repo, "push", "-q", "origin", "HEAD:main")
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "config", "user.email", "t.t@t")
        self.assertIsNone(ajuri.viimeisin_oma_commit(self.repo))

    def test_polku_on_sallittu(self):
        self.assertTrue(ajuri.sallittu_polku("claude-jono/elonmerkki.md"))


class MainKutsuu(Pohja):
    def _main(self, viesti, kaista):
        with mock.patch.dict("os.environ", {"CLAUDEJONO_REPO": str(self.repo), "CLAUDEJONO_TYOHAK": str(self.tmp)}), \
                mock.patch.object(ajuri, "aja", return_value=viesti), \
                mock.patch.object(ajuri, "aivo_arvioija", return_value=None), \
                mock.patch.object(ajuri, "tarkista_kaista"), \
                mock.patch.object(ajuri, "elonmerkki") as em:
            self.assertEqual(ajuri.main(["ajo", "--kaista", str(kaista)]), 0)
        return em

    def test_kaista1_hiljainen_kutsuu(self):
        self._main("jono riittaa (avoimia tehtavia yli 2 x kaistat)", 1).assert_called_once()

    def test_muu_kaista_ei_kutsu(self):
        self._main("jono tyhja", 2).assert_not_called()

    def test_tehty_tyo_ei_kutsu(self):
        self._main("claude-jono: valmis 2026-11-21-01-x.md", 1).assert_not_called()
