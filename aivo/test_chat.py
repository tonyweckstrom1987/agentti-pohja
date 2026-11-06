import http.client
import json
import os
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from aivo import chat

SAL = "oikea-salasana-123"


def ip(*osat):
    """Testiosoite osista: repon osoitetarkistus ei saa nahda kirjaimellisia osoitteita."""
    return ".".join(str(o) for o in osat)


TS = ".ts" + ".net"      # nimen paate koottu osista samasta syysta


class SalasanaTesti(unittest.TestCase):
    def test_tiiviste_toimii_ja_ei_sisalla_salasanaa(self):
        t = chat.tiivista_salasana(SAL)
        self.assertNotIn(SAL, t)
        self.assertTrue(chat.tarkista_salasana(SAL, t))
        self.assertFalse(chat.tarkista_salasana("vaara", t))
        self.assertFalse(chat.tarkista_salasana(SAL, "roskaa"))

    def test_aseta_salasana_kirjoittaa_600_ja_hylkaa_lyhyen(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sal"
            vastaukset = iter([SAL, SAL])
            chat.aseta_salasana(p, kysy=lambda _: next(vastaukset), tulosta=lambda *_: None)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertNotIn(SAL, p.read_text())
            with self.assertRaises(chat.ChatVirhe):
                chat.aseta_salasana(Path(d) / "x", kysy=lambda _: "lyhyt", tulosta=lambda *_: None)
            vastaukset = iter([SAL, SAL + "x"])
            with self.assertRaises(chat.ChatVirhe):
                chat.aseta_salasana(Path(d) / "y", kysy=lambda _: next(vastaukset), tulosta=lambda *_: None)

    def test_laaja_tiedosto_hylataan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sal"
            p.write_text("x")
            os.chmod(p, 0o644)
            with self.assertRaises(chat.ChatVirhe):
                chat.lue_tiiviste(p)


class VerkkoTesti(unittest.TestCase):
    def test_vain_loopback_ja_tailscale(self):
        for ok in (ip(127, 0, 0, 1), "::1", ip(100, 100, 100, 100), ip(100, 64, 0, 1)):
            self.assertTrue(chat.sallittu_osoite(ok), ok)
        for ei in (ip(0, 0, 0, 0), ip(192, 168, 1, 5), ip(8, 8, 8, 8), "::", ip(100, 128, 0, 1), "esimerkki.fi"):
            self.assertFalse(chat.sallittu_osoite(ei), ei)
        with self.assertRaises(chat.ChatVirhe):
            chat.kaynnista(ip(0, 0, 0, 0), 0, None)

    def test_host_tarkistus(self):
        self.assertTrue(chat.sallittu_host("aivo.tail1234" + TS, [TS]))
        self.assertTrue(chat.sallittu_host("aivo.tail1234" + TS + ":443", [TS]))
        self.assertTrue(chat.sallittu_host("127.0.0.1:8780", []))
        self.assertFalse(chat.sallittu_host("paha.example.com", [TS]))
        self.assertFalse(chat.sallittu_host("ts.net" + ".paha.com", [TS]))
        self.assertFalse(chat.sallittu_host(None, [TS]))


class IstuntoTesti(unittest.TestCase):
    def test_lukitus_viidennen_virheen_jalkeen_ja_vanheneminen(self):
        aika = [0.0]
        i = chat.Istunnot(kello=lambda: aika[0])
        for _ in range(chat.VIRHE_RAJA):
            self.assertFalse(i.lukittu())
            i.virhe()
        self.assertTrue(i.lukittu())
        aika[0] += chat.LUKITUS_S + 1
        self.assertFalse(i.lukittu())
        t = i.luo()
        self.assertTrue(i.kelvollinen(t))
        self.assertFalse(i.kelvollinen("keksitty"))
        self.assertFalse(i.kelvollinen(None))
        aika[0] += chat.ISTUNTO_KESTO_S + 1
        self.assertFalse(i.kelvollinen(t))


class MuistiTesti(unittest.TestCase):
    def test_muisti_jarjestys_rajaus_ja_lahteet(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as ulko:
            k = Path(d)
            (k / "aivo-paatoskirjasto.md").write_text("paatos A")
            (k / "kompassi.md").write_text("kompassi B")
            (k / "muu.md").write_text("muu C")
            (k / "kuva.png").write_bytes(b"x")
            (k / ".git").mkdir()
            (k / ".git" / "config.md").write_text("EI")
            (Path(ulko) / "salainen.md").write_text("ULKOPUOLINEN")
            os.symlink(Path(ulko) / "salainen.md", k / "linkki.md")
            teksti, tied = chat.lue_muisti(k)
            self.assertEqual(tied, ["aivo-paatoskirjasto.md", "kompassi.md", "muu.md"])
            self.assertNotIn("ULKOPUOLINEN", teksti)
            self.assertNotIn("EI", teksti.replace("MUISTI", ""))
            self.assertLess(teksti.index("paatos A"), teksti.index("kompassi B"))

    def test_muistin_raja_ei_voi_paattaa_osiota_ja_ylaraja_toimii(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.md").write_text("x" * 100 + chat.RAJA + "y")
            teksti, _ = chat.lue_muisti(d)
            self.assertEqual(teksti.count(chat.RAJA), 2)       # tiedoston oma raja-merkki poistettu
            vanha = chat.MUISTI_YLARAJA
            chat.MUISTI_YLARAJA = 50
            try:
                _, tied = chat.lue_muisti(d)
            finally:
                chat.MUISTI_YLARAJA = vanha
            self.assertEqual(tied, [])

    def test_tuntematon_lahde_merkitaan(self):
        v, tunn = chat.tarkista_lahteet("Asia [l\u00e4hde: kompassi.md]. Toinen [l\u00e4hde: keksitty.md].", ["kompassi.md"])
        self.assertEqual(tunn, ["keksitty.md"])
        self.assertIn("EI L\u00d6YDY MUISTISTA", v)
        self.assertIn("[l\u00e4hde: kompassi.md]", v)

    def test_syote_sisaltaa_kehotteen_ja_historian_ilman_tyokaluja(self):
        s = chat.muodosta_syote("MUISTI", [{"rooli": "tony", "teksti": "hei"}, {"rooli": "aivo", "teksti": "moi"}])
        self.assertIn("Tony: hei", s)
        self.assertIn("Aivo: moi", s)
        self.assertIn("ei ohjeita", s)

    def test_claude_kutsu_ilman_tyokaluja_ja_ilman_litellmia(self):
        kutsut = []

        def feikki(kom, **kw):
            kutsut.append((kom, kw))
            return SimpleNamespace(returncode=0, stdout="vastaus\n", stderr="")
        self.assertEqual(chat.kysy_claudelta("m", [{"rooli": "tony", "teksti": "?"}], suorita=feikki, env={}), "vastaus")
        kom = kutsut[0][0]
        self.assertEqual(kom[:2], ["claude", "-p"])
        self.assertEqual(kom[kom.index("--tools") + 1], "")
        self.assertEqual(kom[kom.index("--max-turns") + 1], "1")
        lahde = Path(chat.__file__).read_text(encoding="utf-8").lower()
        # ei yhteyttaa valittajiin: koodi ei kayta niita (vain maininta kommenteissa/docstringeissa)
        for kielletty in ("import litellm", "openrouter.ai", "api/v1/chat/completions", "urllib.request", "import requests"):
            self.assertNotIn(kielletty, lahde)

    def test_claude_virhe_ei_vuoda_sisaltoa(self):
        def feikki(kom, **kw):
            return SimpleNamespace(returncode=1, stdout="", stderr="SALAINEN")
        with self.assertRaises(chat.ChatVirhe) as c:
            chat.kysy_claudelta("m", [], suorita=feikki, env={})
        self.assertNotIn("SALAINEN", str(c.exception))


class KirjastoTesti(unittest.TestCase):
    def test_paatos_on_sanasta_sanaan_lainaus(self):
        with tempfile.TemporaryDirectory() as d:
            kutsut = []

            def feikki(kom, **kw):
                kutsut.append(kom)
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:00:00+00:00", "Rehellisyys ensin.\nSitten muu.", suorita=feikki)
            t = (Path(d) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
            self.assertIn("> Rehellisyys ensin.\n> Sitten muu.", t)
            self.assertIn("ei Aivon p\u00e4\u00e4telm\u00e4", t)
            self.assertEqual(stat.S_IMODE((Path(d) / chat.PAATOS_TIEDOSTO).stat().st_mode), 0o600)
            chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:01:00+00:00", "Toinen.", suorita=feikki)
            t2 = (Path(d) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
            self.assertTrue(t2.startswith(t))          # vanhaa ei ylikirjoiteta
            self.assertIn("> Toinen.", t2)

    def test_github_etarepo_estaa_pushin(self):
        def feikki(kom, **kw):
            if "get-url" in kom:
                return SimpleNamespace(returncode=0, stdout="https://github.com/x/y.git\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="true\n", stderr="")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.julkaise_kirjastoon(d, Path(d) / "a.md", feikki)

    def test_keskustelun_tunniste_ei_paase_polkuun(self):
        with tempfile.TemporaryDirectory() as d:
            k = chat.Keskustelut(d)
            for paha in ("../../etc/passwd", "", None, "x"):
                with self.assertRaises(chat.ChatVirhe):
                    k.lue(paha)


class PalveluApu:
    """Yhteiset pyyntoapurit palvelutesteille (self.portti asetetaan luokassa)."""

    def pyynto(self, menetelma, polku, runko=None, otsakkeet=None, host=None):
        c = http.client.HTTPConnection(ip(127, 0, 0, 1), self.portti, timeout=10)
        h = dict(otsakkeet or {})
        if host:
            h["Host"] = host
        c.request(menetelma, polku, body=runko, headers=h)
        r = c.getresponse()
        data = r.read()
        return r.status, dict(r.getheaders()), data

    def kirjaudu(self):
        s, h, _ = self.pyynto("POST", "/kirjaudu", "salasana=" + SAL, {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(s, 303)
        ev = h["Set-Cookie"]
        for osa in ("HttpOnly", "Secure", "SameSite=Strict"):
            self.assertIn(osa, ev)
        return ev.split(";")[0]

    def api(self, ev, polku, olio):
        s, _, d = self.pyynto("POST", polku, json.dumps(olio), {"Cookie": ev, "X-Aivo": "1", "Content-Type": "application/json"})
        return s, json.loads(d)


class PalveluTesti(PalveluApu, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        k = Path(cls.tmp.name) / "kirjasto"
        k.mkdir()
        (k / "faktat.md").write_text("Tony opiskelee ohjelmistokehitt\u00e4j\u00e4ksi.")
        cls.kysytty = []
        cls.paatokset = []

        def kysy(muisti, historia):
            cls.kysytty.append((muisti, historia))
            return "Opiskelet ohjelmistokehitt\u00e4j\u00e4ksi [l\u00e4hde: faktat.md] ja [l\u00e4hde: haamu.md]."

        def julkaise(kirjasto, kid, aika, teksti):
            cls.paatokset.append(teksti)
            return "ei-gitissa"
        cls.tila = chat.Tila(chat.tiivista_salasana(SAL), str(k), str(Path(cls.tmp.name) / "data"), [TS],
                             kysy=kysy, julkaise=julkaise)
        cls.srv = chat.kaynnista(ip(127, 0, 0, 1), 0, cls.tila)
        cls.portti = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def test_ilman_kirjautumista_ei_paase(self):
        s, h, _ = self.pyynto("GET", "/")
        self.assertEqual((s, h["Location"]), (302, "/kirjaudu"))
        s, _, _ = self.pyynto("POST", "/api/uusi", "{}", {"X-Aivo": "1"})
        self.assertEqual(s, 401)

    def test_vaara_host_hylataan(self):
        s, _, _ = self.pyynto("GET", "/kirjaudu", host="paha.example.com")
        self.assertEqual(s, 421)

    def test_csrf_vaatii_otsakkeen_ja_saman_alkuperan(self):
        ev = self.kirjaudu()
        s, _, _ = self.pyynto("POST", "/api/uusi", "{}", {"Cookie": ev})
        self.assertEqual(s, 403)
        s, _, _ = self.pyynto("POST", "/api/uusi", "{}", {"Cookie": ev, "X-Aivo": "1", "Origin": "https://paha.example.com"})
        self.assertEqual(s, 403)

    def test_vaara_salasana_ja_lukitus(self):
        tila = chat.Tila(chat.tiivista_salasana(SAL), self.tmp.name, self.tmp.name, [TS])
        srv = chat.kaynnista(ip(127, 0, 0, 1), 0, tila)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def yrita(sal):
                c = http.client.HTTPConnection(ip(127, 0, 0, 1), srv.server_address[1], timeout=10)
                c.request("POST", "/kirjaudu", body="salasana=" + sal, headers={"Content-Type": "application/x-www-form-urlencoded"})
                r = c.getresponse()
                r.read()
                return r.status
            for _ in range(chat.VIRHE_RAJA):
                self.assertEqual(yrita("vaara"), 401)
            self.assertEqual(yrita(SAL), 429)        # oikeakaan ei kelpaa lukituksen aikana
        finally:
            srv.shutdown()
            srv.server_close()

    def test_keskustelu_muisti_lahteet_ja_paatos(self):
        ev = self.kirjaudu()
        s, d = self.api(ev, "/api/uusi", {})
        self.assertEqual(s, 200)
        kid = d["id"]
        s, d = self.api(ev, "/api/viesti", {"keskustelu": kid, "teksti": "mit\u00e4 tied\u00e4t minusta?"})
        self.assertEqual(s, 200)
        self.assertIn("[l\u00e4hde: faktat.md]", d["vastaus"])
        self.assertIn("EI L\u00d6YDY MUISTISTA", d["vastaus"])
        self.assertEqual(d["tuntemattomia_lahteita"], 1)
        self.assertIn("Tony opiskelee", self.kysytty[-1][0])        # muisti luettiin ennen vastausta
        # tallennus isannalle, 600
        p = Path(self.tmp.name) / "data" / (kid + ".jsonl")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(len(p.read_text(encoding="utf-8").splitlines()), 2)
        # paatos: vain Tonyn viesti (indeksi 0) kelpaa, Aivon (1) ei
        s, _ = self.api(ev, "/api/paatos", {"keskustelu": kid, "indeksi": 1})
        self.assertEqual(s, 400)
        s, _ = self.api(ev, "/api/paatos", {"keskustelu": kid, "indeksi": 0, "teksti": "mit\u00e4 tied\u00e4t minusta?"})
        self.assertEqual(s, 200)
        self.assertEqual(self.paatokset, ["mit\u00e4 tied\u00e4t minusta?"])

    def test_liian_pitka_viesti_hylataan(self):
        ev = self.kirjaudu()
        _, d = self.api(ev, "/api/uusi", {})
        s, _ = self.api(ev, "/api/viesti", {"keskustelu": d["id"], "teksti": "x" * (chat.VIESTI_YLARAJA + 1)})
        self.assertEqual(s, 400)

    def test_sivu_on_mobiiliystavallinen(self):
        ev = self.kirjaudu()
        s, h, d = self.pyynto("GET", "/", None, {"Cookie": ev})
        self.assertEqual(s, 200)
        self.assertIn(b'name="viewport"', d)
        self.assertEqual(h["Cache-Control"], "no-store")


def git_feikki(vastaukset, kutsut):
    """Feikki-git: vastaukset on sanakirja {komento-alku: (paluukoodi, tuloste)}; oletus rc 0 ja tyhja."""
    def f(kom, **kw):
        a = tuple(kom[3:])            # ["git", "-C", kirjasto, ...]
        kutsut.append(a)
        osuvat = [k for k in vastaukset if a[:len(k)] == k]
        if osuvat:
            rc, ulos = vastaukset[max(osuvat, key=len)]       # tarkin osuma voittaa
            return SimpleNamespace(returncode=rc, stdout=ulos, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return f


GITEA = "ssh://git@gitea.example.lan/tony/kirjasto.git\n"
GH = "https://github.com/x/y.git\n"


def perus_git(yli=None):
    v = {("rev-parse",): (0, "true\n"), ("remote",): (0, "origin\n"),
         ("remote", "get-url"): (0, GITEA), ("status",): (0, " M aivo-paatoskirjasto.md\n")}
    v.update(yli or {})
    return v


class JulkaisuKorjausTesti(unittest.TestCase):
    """PR #230:n tarkastajan hylkayssyyt: Kirjattu-tila ja GitHub-esto."""

    def test_commit_epaonnistuu_ei_ole_ei_muutosta(self):
        kutsut = []
        f = git_feikki(perus_git({("commit",): (1, "")}), kutsut)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(chat.julkaise_kirjastoon(d, Path(d) / "a.md", f), "commit-epaonnistui")
        self.assertFalse([k for k in kutsut if k[0] == "push"])      # ei pushata ilman commitia

    def test_push_epaonnistuu(self):
        f = git_feikki(perus_git({("push",): (1, "")}), [])
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(chat.julkaise_kirjastoon(d, Path(d) / "a.md", f), "push-epaonnistui")

    def test_ei_muutettavaa_mutta_push_yritetaan_uudelleen(self):
        # edellinen yritys commitoi mutta push kaatui: tyopuu on siisti, pushin pitaa silti yrittaa
        kutsut = []
        f = git_feikki(perus_git({("status",): (0, "")}), kutsut)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(chat.julkaise_kirjastoon(d, Path(d) / "a.md", f), "pushattu")
        self.assertFalse([k for k in kutsut if k[0] == "commit"])
        self.assertTrue([k for k in kutsut if k[0] == "push"])

    def test_push_kohdistetaan_nimettyyn_originiin(self):
        # pushRemote/pushDefault -asetukset eivat saa ohjata pushia tarkistamattomaan etaan
        kutsut = []
        with tempfile.TemporaryDirectory() as d:
            chat.julkaise_kirjastoon(d, Path(d) / "a.md", git_feikki(perus_git(), kutsut))
        self.assertIn(("push", "origin", "HEAD"), kutsut)

    def test_pushurl_github_estaa_vaikka_fetch_osoite_on_gitea(self):
        def f(kom, **kw):
            a = tuple(kom[3:])
            if a[:2] == ("remote", "get-url") and "--push" in a:
                return SimpleNamespace(returncode=0, stdout=GH, stderr="")
            return git_feikki(perus_git(), [])(kom, **kw)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.julkaise_kirjastoon(d, Path(d) / "a.md", f)

    def test_toinen_etarepo_github_estaa(self):
        def f(kom, **kw):
            a = tuple(kom[3:])
            if a == ("remote",):
                return SimpleNamespace(returncode=0, stdout="origin\nvaraus\n", stderr="")
            if a[:2] == ("remote", "get-url") and a[-1] == "varaus":
                return SimpleNamespace(returncode=0, stdout=GH, stderr="")
            return git_feikki(perus_git(), [])(kom, **kw)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.julkaise_kirjastoon(d, Path(d) / "a.md", f)

    def test_oikea_git_pushurl_github_estaa(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
            def g(*a):
                subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, env=env)
            g("init", "-q")
            g("remote", "add", "origin", "ssh://git@gitea.example.lan/tony/k.git")
            g("remote", "set-url", "--push", "origin", "https://github.com/x/y.git")
            (Path(d) / "a.md").write_text("x")
            with self.assertRaises(chat.ChatVirhe):
                chat.julkaise_kirjastoon(d, Path(d) / "a.md", lambda kom, **kw: subprocess.run(kom, env=env, **kw))

    def test_git_aikakatkaisu_on_chatvirhe(self):
        def f(kom, **kw):
            raise subprocess.TimeoutExpired(kom, 1)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.julkaise_kirjastoon(d, Path(d) / "a.md", f)

    def test_github_esto_ennen_kirjoitusta(self):
        f = git_feikki(perus_git({("remote", "get-url"): (0, GH)}), [])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(chat.ChatVirhe):
                chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:00:00+00:00", "Teksti.", suorita=f)
            self.assertFalse((Path(d) / chat.PAATOS_TIEDOSTO).exists())

    def test_uusinta_ei_tuplaa_kirjausta(self):
        f = git_feikki(perus_git({("push",): (1, "")}), [])
        with tempfile.TemporaryDirectory() as d:
            for _ in range(2):
                self.assertEqual(chat.kirjaa_paatos(d, "20261106-120000-abcdef01", "2026-11-06T12:00:00+00:00",
                                                    "Vain kerran.", suorita=f), "push-epaonnistui")
            t = (Path(d) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
            self.assertEqual(t.count("> Vain kerran."), 1)

    def test_rinnakkaiset_kirjaukset_eivat_havita_toisiaan(self):
        alkuperainen = chat.kirjoita_600

        def hidas(polku, teksti):
            time.sleep(0.03)
            alkuperainen(polku, teksti)
        f = git_feikki(perus_git(), [])
        chat.kirjoita_600 = hidas
        try:
            with tempfile.TemporaryDirectory() as d:
                saikeet = [threading.Thread(target=chat.kirjaa_paatos, args=(
                    d, "20261106-120000-abcdef01", "2026-11-06T12:0%d:00+00:00" % i, "Paatos numero %d." % i, f))
                    for i in range(6)]
                for t in saikeet:
                    t.start()
                for t in saikeet:
                    t.join()
                teksti = (Path(d) / chat.PAATOS_TIEDOSTO).read_text(encoding="utf-8")
        finally:
            chat.kirjoita_600 = alkuperainen
        for i in range(6):
            self.assertIn("> Paatos numero %d." % i, teksti)


class MuistiKorjausTesti(unittest.TestCase):
    def test_liian_suuri_tiedosto_sailyttaa_uusimman_lopun(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "aivo-paatoskirjasto.md").write_text("VANHA\n" + "x" * 500 + "\nUUSIN PAATOS")
            vanha = chat.TIEDOSTO_YLARAJA
            chat.TIEDOSTO_YLARAJA = 100
            try:
                teksti, tied = chat.lue_muisti(d)
            finally:
                chat.TIEDOSTO_YLARAJA = vanha
            self.assertIn("UUSIN PAATOS", teksti)
            self.assertNotIn("VANHA", teksti)
            self.assertEqual(tied, ["aivo-paatoskirjasto.md"])
            self.assertIn("katkaistu", teksti)

    def test_ylaraja_ei_pudota_pienempia_tiedostoja_ja_puute_mainitaan(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "aivo-paatoskirjasto.md").write_text("p" * 80)       # jarjestyksessa ensin, ei mahdu
            (Path(d) / "pieni.md").write_text("pieni")
            vanha = chat.MUISTI_YLARAJA
            chat.MUISTI_YLARAJA = 50
            try:
                teksti, tied = chat.lue_muisti(d)
            finally:
                chat.MUISTI_YLARAJA = vanha
            self.assertEqual(tied, ["pieni.md"])
            self.assertIn("aivo-paatoskirjasto.md", teksti.split("pieni")[0] + teksti.split("pieni")[-1])
            self.assertIn("pois", teksti)


class OsoiteKorjausTesti(unittest.TestCase):
    def test_ipv6_host_portilla(self):
        self.assertTrue(chat.sallittu_host("[::1]:8780", []))
        self.assertTrue(chat.sallittu_host("[::1]", []))
        self.assertFalse(chat.sallittu_host("[::2]:8780", []))

    def test_ipv6_loopback_sidotaan_oikeaan_perheeseen(self):
        try:
            koe = socket.socket(socket.AF_INET6)
            koe.bind(("::1", 0))
            koe.close()
        except OSError:
            self.skipTest("::1 ei kaytettavissa taalla")
        srv = chat.kaynnista("::1", 0, None)
        try:
            self.assertEqual(srv.address_family, socket.AF_INET6)
        finally:
            srv.server_close()


class IndeksiKorjausTesti(PalveluApu, unittest.TestCase):
    """Hylkaysperuste 1: paatospainike kirjasi vaaran viestin."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        k = Path(cls.tmp.name) / "kirjasto"
        k.mkdir()
        cls.tulos = {"kysy": "vastaus", "julkaise": "pushattu"}
        cls.paatokset = []

        def kysy(muisti, historia):
            if cls.tulos["kysy"] is None:
                raise chat.ChatVirhe("vastaus epaonnistui (paluukoodi 1)")
            return cls.tulos["kysy"]

        def julkaise(kirjasto, kid, aika, teksti):
            cls.paatokset.append(teksti)
            return cls.tulos["julkaise"]
        cls.tila = chat.Tila(chat.tiivista_salasana(SAL), str(k), str(Path(cls.tmp.name) / "data"), [TS],
                             kysy=kysy, julkaise=julkaise)
        cls.srv = chat.kaynnista(ip(127, 0, 0, 1), 0, cls.tila)
        cls.portti = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.tulos.update({"kysy": "vastaus", "julkaise": "pushattu"})
        self.paatokset.clear()
        self.ev = self.kirjaudu()

    def uusi(self):
        return self.api(self.ev, "/api/uusi", {})[1]["id"]

    def laheta(self, kid, teksti):
        return self.api(self.ev, "/api/viesti", {"keskustelu": kid, "teksti": teksti})

    def test_palvelin_kertoo_viestin_indeksin_ja_uusi_keskustelu_alkaa_nollasta(self):
        a = self.uusi()
        self.assertEqual(self.laheta(a, "yksi")[1]["indeksi"], 0)
        self.assertEqual(self.laheta(a, "kaksi")[1]["indeksi"], 2)
        b = self.uusi()
        self.assertEqual(self.laheta(b, "uuden eka")[1]["indeksi"], 0)

    def test_virhe_ei_siirra_indeksia_vaarin(self):
        kid = self.uusi()
        self.tulos["kysy"] = None
        s, j = self.laheta(kid, "ei saa vastausta")
        self.assertEqual(s, 502)
        self.assertEqual(j["indeksi"], 0)                  # Tonyn viesti tallentui, joten sillekin on indeksi
        self.tulos["kysy"] = "vastaus"
        s, j = self.laheta(kid, "toinen")
        self.assertEqual(j["indeksi"], 1)                  # edellisella ei ollut Aivon vastausta
        s, _ = self.api(self.ev, "/api/paatos", {"keskustelu": kid, "indeksi": 1, "teksti": "toinen"})
        self.assertEqual(s, 200)
        self.assertEqual(self.paatokset, ["toinen"])

    def test_paatos_vaatii_tasmaavan_tekstin(self):
        kid = self.uusi()
        self.laheta(kid, "oikea viesti")
        s, _ = self.api(self.ev, "/api/paatos", {"keskustelu": kid, "indeksi": 0, "teksti": "joku muu viesti"})
        self.assertEqual(s, 400)
        s, _ = self.api(self.ev, "/api/paatos", {"keskustelu": kid, "indeksi": 0})
        self.assertEqual(s, 400)
        self.assertEqual(self.paatokset, [])

    def test_epaonnistunut_julkaisu_ei_ole_200(self):
        kid = self.uusi()
        self.laheta(kid, "tama kirjataan")
        for tila, odotettu in (("push-epaonnistui", 502), ("commit-epaonnistui", 502), ("pushattu", 200),
                               ("ei-gitissa", 200), ("ei-etaa", 200)):
            self.tulos["julkaise"] = tila
            s, j = self.api(self.ev, "/api/paatos", {"keskustelu": kid, "indeksi": 0, "teksti": "tama kirjataan"})
            self.assertEqual(s, odotettu, tila)
            self.assertEqual(j.get("tila"), tila)

    def test_muu_kuin_merkkijono_tunniste_on_400_ei_poikkeus(self):
        for polku, olio in (("/api/viesti", {"keskustelu": 123, "teksti": "x"}),
                            ("/api/paatos", {"keskustelu": ["a"], "indeksi": 0, "teksti": "x"})):
            s, _ = self.api(self.ev, polku, olio)
            self.assertEqual(s, 400, polku)

    def test_sivu_ei_laske_indeksia_itse(self):
        # selain ei saa arvata indeksia laskurilla: se luetaan palvelimen vastauksesta
        self.assertNotIn("n+=2", chat.HTML)
        self.assertNotIn("let n=0", chat.HTML)
        self.assertIn("j.indeksi", chat.HTML)

    def test_sivu_nayttaa_kirjattu_vain_onnistuneesta_kirjauksesta(self):
        self.assertNotIn("b.textContent=r.ok?'Kirjattu'", chat.HTML)
        self.assertIn("r.ok", chat.HTML)
        self.assertIn("b.disabled=false", chat.HTML)        # epaonnistunut kirjaus voidaan yrittaa uudelleen


if __name__ == "__main__":
    unittest.main()
