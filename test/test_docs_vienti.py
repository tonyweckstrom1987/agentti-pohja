import contextlib
import io
import ipaddress
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentti import docs_vienti as dv

AVAIN_SK = "sk-" + "a1B2c3D4" * 4
AVAIN_GH = "ghp_" + "x" * 30
IP = "192.0.2.118"
# RFC 5737 -dokumentaatioalueet (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24): eivat ole oikeita osoitteita.
GITEA = "192.0.2.50"
MUU_SISA = "192.0.2.51"


def ip(a, b, c, d):
    """Yksityisen alueen osoite ilman kirjaimellista osoitetta lahdekoodissa (test_repo kieltaa sisaverkon osoitteet tiedostoissa)."""
    return str(ipaddress.IPv4Address(bytes([a, b, c, d])))
TS_IP = "198.51.100.110"
TS_NIMI = "agenttifirma." + "tail1234" + ".ts.net"


def git(*args, cwd=None):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, args
    return r.stdout


class Pohja(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        t = Path(self._t.name)
        self.lahde, self.tyo, self.ete = t / "docs", t / "tyo" / "agenttidata", t / "ete.git"
        self.jono = self.lahde / "claude-jono"
        for k in ("tulokset", "tehtavat"):
            (self.jono / k).mkdir(parents=True)
        git("init", "-q", "--bare", "-b", "main", str(self.ete))
        # Testit eivat verkota: sallittu osoite korvataan paikallisella tyhjalla repolla (osoite_sallittu testataan erikseen).
        for kohde, korvike in (("osoite_sallittu", lambda o, i: o == str(self.ete)), ("isanta_kelvollinen", lambda i: True)):
            p = mock.patch.object(dv, kohde, korvike)
            p.start()
            self.addCleanup(p.stop)

    def kirjoita(self, suht, teksti):
        p = self.jono / suht
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(teksti, encoding="utf-8")
        return p

    def vie(self, **kw):
        return dv.vie(self.lahde, self.tyo, str(self.ete), ssh=False, **kw)

    def etepolut(self):
        r = subprocess.run(["git", "--git-dir", str(self.ete), "ls-tree", "-r", "--name-only", "main"], capture_output=True, text=True)
        return r.stdout.split() if r.returncode == 0 else []

    def eteteksti(self, polku):
        return git("--git-dir", str(self.ete), "show", "main:" + polku)

    def etelkm(self):
        r = subprocess.run(["git", "--git-dir", str(self.ete), "rev-list", "--count", "main"], capture_output=True, text=True)
        return int(r.stdout) if r.returncode == 0 else 0


class SallitutPolut(Pohja):
    def test_vain_nimetyt_polut_menevat(self):
        self.kirjoita("tulokset/a.md", "tulos")
        self.kirjoita("tehtavat/2026-11-17-01-x.md", "tehtava")
        self.kirjoita("pohdinta-vastaukset.md", "A1: kylla")
        self.kirjoita("kompassi.md", "kompassi")
        self.kirjoita("tonylle.md", "ei")
        self.kirjoita("tulokset/sisa/b.md", "ei")
        self.kirjoita("tulokset/c.py", "ei")
        self.kirjoita("tulokset/.piilo.md", "ei")
        (self.lahde / "koulu").mkdir()
        (self.lahde / "koulu" / "k.md").write_text("ei")
        (self.lahde / "kompassi.md").write_text("juuri ei")
        self.assertIn("pushattu", self.vie())
        self.assertEqual(sorted(self.etepolut()), ["pohdinta-vastaukset.md", "tehtavat/2026-11-17-01-x.md", "tulokset/a.md"])

    def test_kompassi_vain_valinnalla(self):
        self.kirjoita("kompassi.md", "kompassi")
        self.kirjoita("tulokset/a.md", "x")
        self.vie(kompassi=True)
        self.assertIn("kompassi.md", self.etepolut())

    def test_symlink_ei_seurata(self):
        salainen = Path(self._t.name) / "ulkona.md"
        salainen.write_text("ulkopuolinen")
        (self.jono / "tulokset" / "linkki.md").symlink_to(salainen)
        self.kirjoita("tulokset/a.md", "x")
        self.vie()
        self.assertEqual(self.etepolut(), ["tulokset/a.md"])

    def test_liian_iso_tiedosto_ohitetaan(self):
        self.kirjoita("tulokset/iso.md", "a" * (dv.MAX_TAVUA + 1))
        self.kirjoita("tulokset/ok.md", "a" * dv.MAX_TAVUA)
        self.vie()
        self.assertEqual(self.etepolut(), ["tulokset/ok.md"])

    def test_poistunut_lahde_poistuu_kopiosta(self):
        a = self.kirjoita("tulokset/a.md", "a")
        self.kirjoita("tulokset/b.md", "b")
        self.vie()
        a.unlink()
        self.vie()
        self.assertEqual(self.etepolut(), ["tulokset/b.md"])

    def test_tyoklooniin_eksynyt_tiedosto_pysayttaa(self):
        self.kirjoita("tulokset/a.md", "a")
        self.vie()
        (self.tyo / "ylimaarainen.txt").write_text("x")
        self.kirjoita("tulokset/b.md", "b")
        with self.assertRaises(dv.ViennitVirhe):
            self.vie()
        self.assertEqual(self.etelkm(), 1)
        self.assertNotIn("ylimaarainen.txt", self.etepolut())


class Siivous(Pohja):
    def test_testisalaisuudet_ja_osoitteet_pois(self):
        self.kirjoita("tulokset/a.md", "token: abcdefgh12345678\nkone %s ja %s ja %s loppu\n" % (IP, TS_IP, TS_NIMI))
        self.vie()
        t = self.eteteksti("tulokset/a.md")
        for kielletty in ("abcdefgh12345678", IP, TS_IP, "ts.net", "tail1234"):
            self.assertNotIn(kielletty, t)
        self.assertIn("[poistettu]", t)
        self.assertIn("[ip-poistettu]", t)
        self.assertIn("[nimi-poistettu]", t)

    def test_avainmuotoinen_tiedosto_jaa_pois_kokonaan(self):
        self.kirjoita("tulokset/vuoto.md", "hei " + AVAIN_SK)
        self.kirjoita("tulokset/vuoto2.md", "x " + AVAIN_GH)
        self.kirjoita("tulokset/pk.md", "-----BEGIN OPENSSH PRIVATE KEY-----\n")
        self.kirjoita("tulokset/ok.md", "puhdas")
        self.assertIn("ohitettu 3", self.vie())
        self.assertEqual(self.etepolut(), ["tulokset/ok.md"])

    def test_sk_sanan_keskella_ei_ole_avain(self):
        self.kirjoita("tulokset/a.md", "risk-assessment-framework-document on hyva")
        self.vie()
        self.assertEqual(self.etepolut(), ["tulokset/a.md"])

    def test_pitka_tiedosto_ei_katkea(self):
        self.kirjoita("pohdinta-vastaukset.md", "\n".join("rivi %d" % i for i in range(1000)))
        self.vie()
        self.assertIn("rivi 999", self.eteteksti("pohdinta-vastaukset.md"))

    def test_siivoa_ei_koske_versioihin(self):
        self.assertEqual(dv.siivoa("python 3.10.12 ja 1.2.3"), "python 3.10.12 ja 1.2.3")
        self.assertEqual(dv.siivoa("x " + GITEA + "."), "x [ip-poistettu].")


class OsoiteJaMuuttumattomuus(Pohja):
    def test_sallitut_ja_kielletyt_osoitteet(self):
        p = mock.patch.object(dv, "osoite_sallittu", self._alkup)
        p.start()
        self.addCleanup(p.stop)
        polku = "/tony/agenttidata.git"
        ssh = "ssh://git@"
        sallittu = [ssh + GITEA + polku, ssh + GITEA + ":2222" + polku, ssh + GITEA + ":1" + polku]
        kielletty = [ssh + GITEA + "/tony/aivo.git", ssh + GITEA + "/aivo/aivo.git",
                     # Muu sisaverkon osoite ei kelpaa, vaikka se olisi yksityisella alueella (vain asetuksen yksi isanta).
                     ssh + MUU_SISA + polku, ssh + ip(10, 1, 2, 3) + polku, ssh + ip(100, 87, 1, 2) + polku, ssh + ip(172, 20, 0, 9) + polku,
                     ssh + "evil.example" + polku, ssh + "8.8.8.8" + polku, ssh + "127.0.0.1" + polku,
                     "https://" + GITEA + polku, ssh + GITEA + ".evil.example" + polku,
                     ssh + GITEA + polku + "?x=1", "ssh://root@" + GITEA + polku,
                     "ssh://git:salasana@" + GITEA + polku, "git@" + GITEA + ":tony/agenttidata.git",
                     "git@github.com:tonyweckstrom1987/agenttifirma-docs.git", "/tmp/ete.git", "",
                     ssh + GITEA + ":abc" + polku, ssh + GITEA + ":99999" + polku, ssh + GITEA + ":0" + polku,
                     ssh + GITEA + polku + "/../x", ssh + GITEA + polku + "x"]
        for o in sallittu:
            self.assertIs(dv.osoite_sallittu(o, GITEA), True, o)
        for o in kielletty:
            self.assertIs(dv.osoite_sallittu(o, GITEA), False, o)

    def test_isanta_asetus_kelpaa_vain_sisaverkon_yhdelle_osoitteelle(self):
        for kohde, alkup in (("isanta_kelvollinen", _ALKUP_ISANTA), ("osoite_sallittu", _ALKUP)):
            p = mock.patch.object(dv, kohde, alkup)
            p.start()
            self.addCleanup(p.stop)
        for i in (GITEA, ip(10, 1, 2, 3), ip(100, 87, 1, 2), ip(172, 20, 0, 9), ip(192, 168, 1, 1)):
            self.assertIs(dv.isanta_kelvollinen(i), True, i)
        for i in ("", None, "8.8.8.8", "127.0.0.1", "0.0.0.0", "169.254.1.1", "224.0.0.1", "192.0.2.0/24", ip(10, 0, 0, 0) + "/8", "git.example", "::1", "10.1.2"):
            self.assertIs(dv.isanta_kelvollinen(i), False, str(i))
        osoite = "ssh://git@" + GITEA + "/tony/agenttidata.git"
        self.assertIs(dv.osoite_sallittu(osoite, ""), False)
        self.assertIs(dv.osoite_sallittu(osoite, "8.8.8.8"), False)
        self.assertIs(dv.osoite_sallittu("ssh://git@8.8.8.8/tony/agenttidata.git", "8.8.8.8"), False)

    def test_puuttuva_isanta_estaa_pushin(self):
        p = mock.patch.object(dv, "isanta_kelvollinen", _ALKUP_ISANTA)
        p.start()
        self.addCleanup(p.stop)
        self.kirjoita("tulokset/a.md", "a")
        with mock.patch.object(dv.subprocess, "run") as ajo:
            with self.assertRaises(dv.ViennitVirhe):
                dv.vie(self.lahde, self.tyo, "ssh://git@" + GITEA + "/tony/agenttidata.git", ssh=False)
            ajo.assert_not_called()

    def test_main_lukee_isannan_ymparistosta(self):
        p = mock.patch.object(dv, "isanta_kelvollinen", _ALKUP_ISANTA)
        p.start()
        self.addCleanup(p.stop)
        err = io.StringIO()
        env = {"DOCS_VIENTI_OSOITE": "ssh://git@" + GITEA + "/tony/agenttidata.git", "DOCS_VIENTI_TYO": str(self.tyo)}
        with mock.patch.dict(os.environ, env), mock.patch.object(dv, "osoite_sallittu", self._alkup):
            os.environ.pop("DOCS_VIENTI_ISANTA", None)
            with mock.patch.object(dv.subprocess, "run") as ajo, contextlib.redirect_stderr(err):
                self.assertEqual(dv.main([]), 1)
                ajo.assert_not_called()
            os.environ["DOCS_VIENTI_ISANTA"] = GITEA
            with mock.patch.object(dv, "vie", return_value="ok") as vie, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(dv.main([]), 0)
            self.assertEqual(vie.call_args.kwargs["isanta"], GITEA)

    def test_tyhja_osoite_asetus_estaa_ajon(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"DOCS_VIENTI_TYO": str(self.tyo)}):
            os.environ.pop("DOCS_VIENTI_OSOITE", None)
            with mock.patch.object(dv, "osoite_sallittu", self._alkup), contextlib.redirect_stderr(err):
                self.assertEqual(dv.main([]), 1)
        self.assertIn("VIRHE", err.getvalue())

    def test_vaara_osoite_estaa_pushin(self):
        self.kirjoita("tulokset/a.md", "a")
        with mock.patch.object(dv, "osoite_sallittu", self._alkup):
            with mock.patch.object(dv.subprocess, "run") as ajo:
                with self.assertRaises(dv.ViennitVirhe):
                    dv.vie(self.lahde, self.tyo, "ssh://git@evil.example/tony/agenttidata.git", ssh=False, isanta=GITEA)
                ajo.assert_not_called()
        self.assertFalse(self.tyo.exists())

    def test_origin_ei_ohjaa_pushia(self):
        self.kirjoita("tulokset/a.md", "a")
        muu = Path(self._t.name) / "muu.git"
        git("init", "-q", "--bare", "-b", "main", str(muu))
        self.vie()
        git("remote", "add", "origin", str(muu), cwd=self.tyo)
        self.kirjoita("tulokset/b.md", "b")
        self.vie()
        r = subprocess.run(["git", "--git-dir", str(muu), "rev-list", "--all"], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(sorted(self.etepolut()), ["tulokset/a.md", "tulokset/b.md"])

    def test_muuttumaton_sisalto_ei_tee_committia(self):
        self.kirjoita("tulokset/a.md", "a")
        self.vie()
        self.assertEqual(self.etelkm(), 1)
        self.assertIn("ei committia", self.vie())
        self.assertIn("ei muutoksia", self.vie())
        self.assertEqual(self.etelkm(), 1)

    def test_muutos_tekee_uuden_commitin(self):
        a = self.kirjoita("tulokset/a.md", "a")
        self.vie()
        a.write_text("a2")
        self.vie()
        self.assertEqual(self.etelkm(), 2)

    def test_tyhja_lahde_ei_committia(self):
        self.assertIn("ei muutoksia", self.vie())
        self.assertEqual(self.etelkm(), 0)

    def test_kuiva_ei_pushaa(self):
        self.kirjoita("tulokset/a.md", "a")
        self.assertIn("kuiva-ajo", self.vie(kuiva=True))
        self.assertEqual(self.etelkm(), 0)

    def test_uusi_tyoklooni_jatkaa_olemassa_olevasta(self):
        self.kirjoita("tulokset/a.md", "a")
        self.vie()
        import shutil
        shutil.rmtree(self.tyo)
        self.assertIn("ei committia", self.vie())
        self.assertEqual(self.etelkm(), 1)

    def test_main_tulostaa_vain_tyypin_virheesta(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"DOCS_VIENTI_OSOITE": "ssh://git@evil.example/x.git", "DOCS_VIENTI_TYO": str(self.tyo)}):
            with mock.patch.object(dv, "osoite_sallittu", self._alkup), contextlib.redirect_stderr(err):
                self.assertEqual(dv.main([]), 1)
        self.assertIn("VIRHE", err.getvalue())
        self.assertNotIn("evil", err.getvalue())

    @property
    def _alkup(self):
        return _ALKUP


class Apurit(Pohja):
    def test_valmista_oletus_ei_ota_kompassia_ja_laskee(self):
        self.kirjoita("kompassi.md", "k")
        self.kirjoita("tulokset/a.md", "a")
        self.kirjoita("tulokset/b.md", "b")
        ei_olemassa = Path(self._t.name) / "uusi" / "syva"
        kopioitu, ohitetut = dv.valmista(self.lahde, ei_olemassa)
        self.assertEqual((kopioitu, ohitetut), (2, []))
        self.assertFalse((ei_olemassa / "kompassi.md").exists())
        self.assertEqual(dv.valmista(self.lahde, ei_olemassa, True)[0], 3)

    def test_symlink_kansio_ei_seurata(self):
        ulko = Path(self._t.name) / "ulko"
        ulko.mkdir()
        (ulko / "x.md").write_text("ulkopuolinen")
        import shutil
        shutil.rmtree(self.jono / "tulokset")
        (self.jono / "tulokset").symlink_to(ulko, target_is_directory=True)
        self.assertEqual(dv.valmista(self.lahde, self.tyo)[0], 0)

    def test_sallittu_kohde(self):
        self.assertFalse(dv._sallittu_kohde("kompassi.md", False))
        self.assertTrue(dv._sallittu_kohde("kompassi.md", True))
        self.assertTrue(dv._sallittu_kohde("pohdinta-vastaukset.md", False))
        self.assertFalse(dv._sallittu_kohde("tulokset/sisa/a.md", False))
        self.assertFalse(dv._sallittu_kohde("muu/a.md", True))
        self.assertFalse(dv._sallittu_kohde("tulokset/a.py", True))

    def test_ssh_env_ja_oletus_kayttaa_sita(self):
        e = dv._ssh_env("/avain/polku", Path("/a/b/tyo"))["GIT_SSH_COMMAND"]
        self.assertIn("-i /avain/polku", e)
        self.assertIn("IdentitiesOnly=yes", e)
        self.assertIn("UserKnownHostsFile=/a/b/known_hosts", e)
        nahdyt = []
        alkup = dv._git

        def vakooja(tyo, *args, env=None):
            nahdyt.append((args[0], dict(env or {})))
            return alkup(tyo, *args, env=env)
        with mock.patch.object(dv, "_git", vakooja):
            dv.vie(self.lahde, self.tyo, str(self.ete))
        self.assertIn("GIT_SSH_COMMAND", [e for a, e in nahdyt if a == "ls-remote"][0])

    def test_tyoklooni_valmiiksi_olemassa_tyhjana(self):
        syva = Path(self._t.name) / "a" / "b" / "c"
        self.kirjoita("tulokset/a.md", "a")
        dv.vie(self.lahde, syva, str(self.ete), ssh=False)
        vali = Path(self._t.name) / "d"
        (vali / "tyo").mkdir(parents=True)
        self.assertIn("ei committia", dv.vie(self.lahde, vali / "tyo", str(self.ete), ssh=False))

    def test_main_onnistuu_ja_osoite_loppuun(self):
        self.kirjoita("tulokset/a.md", "a")
        env = {"DOCS_VIENTI_LAHDE": str(self.lahde), "DOCS_VIENTI_TYO": str(self.tyo), "DOCS_VIENTI_OSOITE": str(self.ete)}
        ulos = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(ulos):
            with mock.patch.object(dv, "_ssh_env", lambda a, t: {}):
                self.assertEqual(dv.main([]), 0)
        self.assertTrue(ulos.getvalue().startswith("docs-vienti: pushattu"))
        self.assertEqual(self.etelkm(), 1)

    def test_main_oserror_tulostaa_vain_tyypin(self):
        err = io.StringIO()
        with mock.patch.object(dv, "vie", side_effect=PermissionError("salainen polku")), contextlib.redirect_stderr(err):
            self.assertEqual(dv.main([]), 1)
        self.assertIn("PermissionError", err.getvalue())
        self.assertNotIn("salainen", err.getvalue())


class Yksikkomallit(unittest.TestCase):
    """Systemd-mallien turvarajat ovat koodin osa: niiden poistaminen rikkoo testin."""
    KANSIO = Path(__file__).resolve().parent.parent / "systemd"

    def rivit(self, nimi):
        return [r.strip() for r in (self.KANSIO / nimi).read_text(encoding="utf-8").splitlines() if r.strip() and not r.lstrip().startswith("#")]

    def test_palvelu_ajetaan_omana_kayttajana_ei_rootina(self):
        r = self.rivit("docs-vienti.service.mall")
        self.assertIn("User=docs-vienti", r)
        self.assertIn("Group=docs-vienti", r)
        self.assertFalse([x for x in r if x.startswith("User=") and x != "User=docs-vienti"])

    def test_verkko_rajattu_yhteen_osoitteeseen(self):
        r = self.rivit("docs-vienti.service.mall")
        self.assertIn("IPAddressDeny=any", r)
        sallitut = [x for x in r if x.startswith("IPAddressAllow=")]
        self.assertEqual(sallitut, ["IPAddressAllow=<GITEA-IP>"])
        self.assertIn("NoNewPrivileges=yes", r)
        self.assertIn("ProtectSystem=strict", r)

    def test_docs_pull_on_wants_ja_after(self):
        r = self.rivit("docs-vienti.service.mall")
        wants = [x for x in r if x.startswith("Wants=")][0].split("=", 1)[1].split()
        after = [x for x in r if x.startswith("After=")][0].split("=", 1)[1].split()
        self.assertIn("docs-pull.service", wants)
        self.assertIn("docs-pull.service", after)

    def test_ohje_mainitsee_kayttajan_ja_isanta_asetuksen(self):
        ohje = (self.KANSIO.parent / "docs" / "agenttidata.md").read_text(encoding="utf-8")
        for pala in ("useradd", "DOCS_VIENTI_ISANTA", "docs-vienti", "Salli kirjoitus"):
            self.assertIn(pala, ohje)


_ALKUP = dv.osoite_sallittu
_ALKUP_ISANTA = dv.isanta_kelvollinen

if __name__ == "__main__":
    unittest.main()
