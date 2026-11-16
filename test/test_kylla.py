"""Kylla-skriptit (isanta/kylla.py): sha-sidonta, chat-id, aikaraja, suodatus, kirjoittajat ja palvelujen 5b-poikkeus."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from isanta import kylla as k

TONY = 4242


def skripti(kuvaus="tulostaa paivan", kirjoittaja="pilvi-claude", runko="echo hei\n"):
    return "#!/bin/bash\n# kirjoittaja: %s\n# kuvaus: %s\nset -eu\n%s" % (kirjoittaja, kuvaus, runko)


class FeikkiBotti:
    def __init__(self, chat_id=TONY):
        self.chat_id = chat_id
        self.lahetetyt = []
        self.jono = []

    def laheta(self, teksti):
        self.lahetetyt.append(teksti)
        return True

    def paivitykset(self, offset):
        tulos, self.jono = self.jono, []
        return tulos


def viesti(uid, teksti, aika, chat=TONY, lahettaja=TONY):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "from": {"id": lahettaja}, "text": teksti, "date": aika}}


class Pohja(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.ajettavat = os.path.join(self.d, "ajettavat")
        os.makedirs(self.ajettavat)
        self.tila = os.path.join(self.d, "tila")
        self.ulos = os.path.join(self.d, "tulosteet")
        self.loki = os.path.join(self.d, "kylla.log")
        self.botti = FeikkiBotti()
        self.ajot = []

    def aja(self, polku, aikaraja):
        with open(polku, "rb") as f:
            self.ajot.append(f.read())
        return 0, b"valmis\n", 0.1

    def kirjoita(self, nimi, sisalto):
        with open(os.path.join(self.ajettavat, nimi), "w", encoding="utf-8") as f:
            f.write(sisalto)

    def kierros(self, aja=None):
        return k.Kierros(self.ajettavat, self.tila, self.ulos, self.botti, aja=aja or self.aja, loki=self.loki)

    def kysy(self, nimi="03-paiva.sh", sisalto=None):
        self.kirjoita(nimi, sisalto or skripti())
        kr = self.kierros()
        kr.kysy_uudet()
        return kr


class TestKysely(Pohja):
    def test_uusi_skripti_kysytaan_shan_kanssa(self):
        self.kysy()
        self.assertEqual(len(self.botti.lahetetyt), 1)
        v = self.botti.lahetetyt[0]
        sha = k.sha256(skripti().encode())[:8]
        self.assertIn("Aja 03: tulostaa paivan", v)
        self.assertIn(sha, v)
        self.assertIn("Vastaa: kyll\u00e4 03", v)

    def test_samaa_ei_kysyta_kahdesti(self):
        self.kysy()
        self.kierros().kysy_uudet()
        self.assertEqual(len(self.botti.lahetetyt), 1)

    def test_kirjoittaja_pakollinen_ja_sallittu(self):
        self.kirjoita("04-x.sh", skripti(kirjoittaja="jonoagentti"))
        self.kirjoita("05-y.sh", "#!/bin/bash\n# kuvaus: ilman kirjoittajaa\necho\n")
        self.kirjoita("06-z.sh", skripti(kirjoittaja="aivo"))
        self.kierros().kysy_uudet()
        teksti = "\n".join(self.botti.lahetetyt)
        self.assertIn("Hyl\u00e4tty 04-x.sh: kirjoittaja", teksti)
        self.assertIn("Hyl\u00e4tty 05-y.sh: kirjoittaja", teksti)
        self.assertIn("Aja 06:", teksti)

    def test_nimi_symlinkki_ja_ajajan_polut(self):
        self.kirjoita("7-x.sh", skripti())
        os.symlink("/etc/hostname", os.path.join(self.ajettavat, "08-linkki.sh"))
        self.kirjoita("09-ajaja.sh", skripti(runko="echo x > /opt/isanta/kylla.py\n"))
        self.kirjoita("10-tila.sh", skripti(runko="rm -rf /var/lib/kylla\n"))
        kelvolliset, hylatyt = k.skannaa(self.ajettavat)
        self.assertEqual(kelvolliset, {})
        syyt = dict(hylatyt)
        self.assertIn("nimi", syyt["7-x.sh"])
        self.assertIn("tavallinen", syyt["08-linkki.sh"])
        self.assertIn("ajajan", syyt["09-ajaja.sh"])
        self.assertIn("ajajan", syyt["10-tila.sh"])


class TestKylla(Pohja):
    def test_kylla_ajaa_naytetyn_sisallon(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        self.botti.jono = [viesti(1, "kyll\u00e4 03", kysytty + 5)]
        kr.kasittele_vastaukset()
        self.assertEqual(self.ajot, [skripti().encode()])
        self.assertIn("03: onnistui, paluukoodi 0.", self.botti.lahetetyt[-1])
        with open(os.path.join(self.ulos, "03.txt"), encoding="utf-8") as f:
            sisalto = f.read()
        self.assertIn("paluukoodi=0", sisalto)
        self.assertIn("valmis", sisalto)
        self.assertEqual(kr.t["offset"], 2)

    def test_sha_muutos_estaa_ajon(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        self.kirjoita("03-paiva.sh", skripti(runko="echo muutettu\n"))
        self.botti.jono = [viesti(1, "kylla 03", kysytty + 5)]
        kr.kasittele_vastaukset()
        self.assertEqual(self.ajot, [])
        self.assertIn("muuttui", self.botti.lahetetyt[-1])
        # uusi kysymys lahtee uudella shalla
        kr.kysy_uudet()
        self.assertIn(k.sha256(skripti(runko="echo muutettu\n").encode())[:8], self.botti.lahetetyt[-1])

    def test_peukaloitu_kopio_estaa_ajon(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        with open(kr.kopio("03"), "w") as f:
            f.write("echo peukaloitu\n")
        self.botti.jono = [viesti(1, "kylla 03", kysytty + 5)]
        kr.kasittele_vastaukset()
        self.assertEqual(self.ajot, [])
        self.assertIn("kopio", self.botti.lahetetyt[-1])

    def test_vanha_vastaus_ei_aja_uutta_versiota(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        self.botti.jono = [viesti(1, "kylla 03", kysytty - 60)]
        kr.kasittele_vastaukset()
        self.assertEqual(self.ajot, [])
        self.assertIn("vanhempi", self.botti.lahetetyt[-1])

    def test_vaara_sha8_ei_aja(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        self.botti.jono = [viesti(1, "kylla 03 00000000", kysytty + 1)]
        kr.kasittele_vastaukset()
        self.assertEqual(self.ajot, [])

    def test_vaara_chat_id_tai_lahettaja_ei_aja(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        n = len(self.botti.lahetetyt)
        self.botti.jono = [viesti(1, "kylla 03", kysytty + 1, chat=999),
                           viesti(2, "kylla 03", kysytty + 1, lahettaja=999),
                           viesti(3, "kylla 03", kysytty + 1, chat=999, lahettaja=999)]
        kr.kasittele_vastaukset()
        self.assertEqual(self.ajot, [])
        self.assertEqual(len(self.botti.lahetetyt), n)          # vieraille ei vastata
        self.assertEqual(kr.t["offset"], 4)                     # mutta ne kuitataan luetuiksi

    def test_ajetaan_vain_kerran(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        self.botti.jono = [viesti(1, "kylla 03", kysytty + 1), viesti(2, "kylla 03", kysytty + 2)]
        kr.kasittele_vastaukset()
        kr.kysy_uudet()
        self.assertEqual(len(self.ajot), 1)
        self.assertIn("ei odottavaa", self.botti.lahetetyt[-1])

    def test_muut_viestit_ohitetaan(self):
        for t in ("kylla", "kylla 3", "kylla 03; rm -rf /", "aja 03", "kylla 03 xyz"):
            self.assertIsNone(k.jasenna(t), t)


class TestAikaraja(Pohja):
    def test_aikaraja_tappaa_prosessiryhman(self):
        polku = os.path.join(self.d, "hidas.sh")
        merkki = os.path.join(self.d, "lapsi-elossa")
        with open(polku, "w") as f:
            f.write("(sleep 3; touch %s) &\nsleep 30\n" % merkki)
        koodi, ulos, kesto = k.aja_skripti(polku, aikaraja=1)
        self.assertIsNone(koodi)
        self.assertLess(kesto, 10)
        import time
        time.sleep(3.5)
        self.assertFalse(os.path.exists(merkki))                # myos taustalapsi tapettiin

    def test_aikarajasta_viesti(self):
        kr = self.kysy()
        kysytty = kr.t["odottavat"]["03"]["kysytty"]
        self.botti.jono = [viesti(1, "kylla 03", kysytty + 1)]
        k.Kierros(self.ajettavat, self.tila, self.ulos, self.botti, aja=lambda p, a: (None, b"", 600.0),
                  loki=self.loki).kasittele_vastaukset()
        self.assertIn("aikaraja (10 min)", self.botti.lahetetyt[-1])

    def test_paluukoodi_ja_tuloste(self):
        polku = os.path.join(self.d, "virhe.sh")
        with open(polku, "w") as f:
            f.write("echo ulos; echo virhe >&2; exit 3\n")
        koodi, ulos, _ = k.aja_skripti(polku, aikaraja=10)
        self.assertEqual(koodi, 3)
        self.assertEqual(ulos.decode().split(), ["ulos", "virhe"])


class TestSuodatus(unittest.TestCase):
    def test_osoitteet_ja_tunnukset(self):
        sisainen = ".".join(["192", "168", "7", "9"])
        tailscale = ".".join(["100", "77", "1", "2"])
        julkinen = ".".join(["203", "0", "113", "5"])
        teksti = "\n".join([
            "ssh %s ok" % sisainen, "tailscale %s" % tailscale, "julkinen %s" % julkinen,
            "nimi isanta.tail1234" + ".ts" + ".net", "ipv6 fe80::1ab:2cd", "TOKEN=abc123xyz", "api_key: qwerty9",
            "bot 123456789:" + "A" * 35, "gh ghp_" + "b1" * 18, "Authorization: Bearer abcdef",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nxxx\n-----END OPENSSH PRIVATE KEY-----",
            "ssh-ed25519 " + "AAAAC3Nza" * 4, "satunnainen " + "aB3" * 15])
        tulos = k.suodata(teksti)
        for salainen in (sisainen, tailscale, julkinen, "tail1234", "fe80", "abc123xyz", "qwerty9", "A" * 35, "ghp_",
                         "abcdef", "xxx", "AAAAC3Nza", "aB3aB3"):
            self.assertNotIn(salainen, tulos, salainen)

    def test_tavallinen_tuloste_sailyy(self):
        sha = "a" * 64
        teksti = "klo 12:30:45 palvelu active (running)\nsha256 %s\nloaded 3 units" % sha
        self.assertEqual(k.suodata(teksti), teksti)

    def test_osoite_re_sama_kuin_automergessa(self):
        from tools import automerge
        self.assertEqual(k.OSOITE_RE.pattern, automerge.OSOITE_RE.pattern)

    def test_tuloste_suodatetaan_ennen_tallennusta(self):
        t = Pohja("setUp")
        t.setUp()
        try:
            kr = t.kysy()
            kysytty = kr.t["odottavat"]["03"]["kysytty"]
            ip = ".".join(["10", "1", "2", "3"])
            t.botti.jono = [viesti(1, "kylla 03", kysytty + 1)]
            k.Kierros(t.ajettavat, t.tila, t.ulos, t.botti, aja=lambda p, a: (0, ("ip %s\n" % ip).encode(), 0.1),
                      loki=t.loki).kasittele_vastaukset()
            with open(os.path.join(t.ulos, "03.txt"), encoding="utf-8") as f:
                sisalto = f.read()
            self.assertNotIn(ip, sisalto)
            self.assertIn("<osoite>", sisalto)
            with open(t.loki, encoding="utf-8") as f:
                self.assertNotIn(ip, f.read())
        finally:
            t.doCleanups()


class TestDocsPolku(unittest.TestCase):
    def test_jonoagentti_ei_kirjoita_ajettaviin(self):
        from claudejono import ajuri
        self.assertFalse(ajuri.vapaa_docs_polku("claude-jono/isanta-kasin/ajettavat/01-x.txt"))
        self.assertFalse(ajuri.vapaa_docs_polku("claude-jono/isanta-kasin/ajettavat/01-x.md"))
        self.assertFalse(ajuri.vapaa_docs_polku("claude-jono/isanta-kasin/ajettavat/01-x.sh"))


class TestPalvelut(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.botti = FeikkiBotti()
        self.kutsut = []

    def suorita(self, kaatunut):
        def f(kom, **kw):
            self.kutsut.append(kom)
            koodi = 0 if ("is-failed" in kom and kaatunut) or "restart" in kom else 1
            return subprocess.CompletedProcess(kom, koodi, b"", b"")
        return f

    def test_lista_hylkaa_kauppatiimin_ja_virheelliset(self):
        polku = os.path.join(self.d, "palvelut.txt")
        with open(polku, "w") as f:
            f.write("# Tonyn hyvaksyma\n115 claude-aamukooste.service\n118 kauppatiimi.service\n"
                    "115 x.service; rm -rf /\n115 kauppa-seuranta.service\nabc y.service\n")
        self.assertEqual(k.lue_palvelulista(polku), [("115", "claude-aamukooste.service")])

    def test_ehjaa_ei_kaynnisteta(self):
        k.palvelukierros([("115", "a.service")], self.d, self.botti, suorita=self.suorita(False), loki=os.devnull)
        self.assertFalse(any("restart" in c for c in self.kutsut))

    def test_kolmas_kerta_lopettaa_ja_halyttaa(self):
        lista = [("115", "a.service")]
        for _ in range(5):
            k.palvelukierros(lista, self.d, self.botti, suorita=self.suorita(True), loki=os.devnull, paiva="2026-11-05")
        self.assertEqual(sum("restart" in c for c in self.kutsut), 3)
        self.assertEqual(len(self.botti.lahetetyt), 1)
        self.assertIn("H\u00e4lytys", self.botti.lahetetyt[0])
        with open(os.path.join(self.d, "juurisyyt.jsonl")) as f:
            self.assertEqual(json.loads(f.readline())["palvelu"], "115/a.service")
        with open(os.path.join(self.d, "uudelleenkaynnistykset.jsonl")) as f:
            self.assertEqual(len(f.readlines()), 3)
        # pysaytys pysyy seuraavana paivana, kunnes Tony poistaa sen
        k.palvelukierros(lista, self.d, self.botti, suorita=self.suorita(True), loki=os.devnull, paiva="2026-11-06")
        self.assertEqual(sum("restart" in c for c in self.kutsut), 3)


class TestKomentorivi(unittest.TestCase):
    def test_tarkista_on_kuiva_ajo(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, "01-testi.sh"), "w") as f:
            f.write(skripti(runko="touch %s/ajettiin\n" % d))
        r = subprocess.run(["python3", "-B", "isanta/kylla.py", "tarkista", "--ajettavat", d],
                           capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok 01 01-testi.sh", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(d, "ajettiin")))

    def test_ilman_tunnusta_virhe_ilman_tracebackia(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        ymp = {k_: v for k_, v in os.environ.items() if k_ != "KOMENTOBOTTI_TOKEN"}
        lukko = os.path.join(d, "lukko")
        r = subprocess.run(["python3", "-B", "-c", "import sys; sys.path.insert(0,'.'); from isanta import kylla as k; "
                            "k.LUKKO=%r; sys.exit(k.main(['palvelut','--lista','/x','--tila',%r,'--chat-tiedosto','/x']))"
                            % (lukko, d)],
                           capture_output=True, text=True, env=ymp,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(r.returncode, 1)
        self.assertIn("KOMENTOBOTTI_TOKEN puuttuu", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
