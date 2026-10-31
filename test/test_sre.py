"""SRE-vikakirjasto: injektoinnin ja tarkistusten testit ilman oikeaa konttia (fixturet, valesystemctl, tilapaishakemisto).

Mitaan oikeaa kontaineria, systemd:ta tai isantaa ei kosketa: Ymp saa tilapaisen juuren ja valekomentojen ajajan."""
import glob
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
SRE = JUURI / "hiekkalaatikko" / "sre"
VIAT = SRE / "viat"
LINUX = sys.platform.startswith("linux") and shutil.which("bash") is not None


def lataa(nimi, polku):
    spec = importlib.util.spec_from_file_location(nimi, polku)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sys.path.insert(0, str(VIAT))
L = lataa("sre_lib", VIAT / "sre_lib.py")
sys.modules["sre_lib"] = L
V = {p.name[:3]: lataa(p.stem, p) for p in sorted(VIAT.glob("v[0-9][0-9]_*.py"))}
luo = lataa("luo_kokeilut_testi", SRE / "luo_kokeilut.py")
matriisi = lataa("matriisi_testi", SRE / "matriisi.py")
yhteenveto = lataa("yhteenveto_testi", SRE / "yhteenveto.py")


class Vale:
    """Valekomentojen ajaja: kirjaa kutsut ja vastaa ohjelmoidusti. start-kutsu voi kirjoittaa onnistumismerkin."""

    def __init__(self, y_juuri):
        self.juuri = y_juuri
        self.kutsut = []
        self.vastaus = {}      # (komento, alikomento) -> (rc, ulos)
        self.start_ok = {}     # yksikko -> merkkipolku jos start onnistuu, None jos ei
        self.result = "success"

    def __call__(self, komento, aika):
        k = tuple(komento)
        self.kutsut.append(k)
        if k[:2] == ("systemctl", "start"):
            merkki = self.start_ok.get(k[2])
            if merkki is None:
                return 1, "Job failed"
            p = os.path.join(self.juuri, merkki.lstrip("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(self.merkin_sisalto)
            return 0, ""
        if k[:2] == ("systemctl", "show"):
            return 0, self.result + "\n"
        return self.vastaus.get(k[:2], self.vastaus.get(k[:1], (0, "")))

    merkin_sisalto = "1\n"


class Pohja(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.vale = Vale(self.d)
        self.y = L.Ymp(juuri=self.d, ajo=self.vale, hostname="hl-9100")


class TurvaTesti(Pohja):
    def test_vain_kloonin_isantanimi(self):
        for nimi, odotus in (("hl-9100", True), ("hl-9199", True), ("hl-9200", False), ("hl-9000", False), ("hl-115", False),
                             ("agentscope", False), ("hl-9100x", False), ("xhl-9100", False), ("", False), ("hl-09100", False)):
            self.assertEqual(L.Ymp(juuri=self.d, ajo=self.vale, hostname=nimi).turvallinen(), odotus, nimi)

    def test_injektointi_kieltaytyy_tuotannossa_eika_kirjoita_mitaan(self):
        for tunnus, m in V.items():
            with self.subTest(vika=tunnus):
                d = tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, d)
                vale = Vale(d)
                y = L.Ymp(juuri=d, ajo=vale, hostname="agentscope")
                rc = L.kaynnista(m, ["injektoi"], y, io.StringIO())
                self.assertEqual(rc, 78)
                self.assertEqual(os.listdir(d), [])
                self.assertEqual(vale.kutsut, [])

    def test_tarkista_kieltaytyy_myos_tuotannossa(self):
        y = L.Ymp(juuri=self.d, ajo=self.vale, hostname="hl-115")
        self.assertEqual(L.kaynnista(V["v06"], ["tarkista"], y, io.StringIO()), 78)

    def test_kaytto(self):
        self.assertEqual(L.kaynnista(V["v01"], [], self.y, io.StringIO()), 2)
        self.assertEqual(L.kaynnista(V["v01"], ["tuhoa"], self.y, io.StringIO()), 2)
        self.assertEqual(L.kaynnista(V["v01"], ["injektoi", "x"], self.y, io.StringIO()), 2)


class KirjastoTesti(unittest.TestCase):
    def test_vahintaan_kuusi_vikaa_ja_oikeat_tunnisteet(self):
        self.assertGreaterEqual(len(V), 6)
        self.assertEqual(len({m.TUNNUS for m in V.values()}), len(V))
        self.assertEqual(len({m.KOKEILU for m in V.values()}), len(V))
        for m in V.values():
            self.assertRegex(m.KOKEILU, r"^sre-v\d\d-[a-z0-9]+$")
            for f in ("injektoi", "tarkista"):
                self.assertTrue(callable(getattr(m, f)))

    def test_oire_ei_paljasta_vikaa(self):
        paljastavat = re.compile(r"PATH|runuser|lainausm|valilyon|chmod|chown|oikeu|tmpfs|mount|levy|taynna|puuttuu ymparist|"
                                 r"enable|GIT_SSH|SRE_API_URL|injekt|tarkista|v0\d_", re.I)
        for t, m in V.items():
            with self.subTest(vika=t):
                if t == "v06":   # ajastinoire kertoo oireen (ei koskaan ajettu), mutta ei kaytteleisi 'enable'-sanaa
                    self.assertNotRegex(m.OIRE, r"enable|WantedBy|timers\.target")
                else:
                    self.assertIsNone(paljastavat.search(m.OIRE), m.OIRE)

    def test_kaikki_ascii_ja_suoritettavia(self):
        for p in list(VIAT.glob("*.py")) + list(SRE.glob("*.py")):
            p.read_bytes().decode("ascii")
        for p in VIAT.glob("v[0-9][0-9]_*.py"):
            self.assertTrue(os.stat(p).st_mode & stat.S_IXUSR, p.name)

    def test_tyokuormaskriptit_ovat_kelvollista_bashia(self):
        if not LINUX:
            self.skipTest("bash puuttuu")
        for t, m in V.items():
            sk = getattr(m, "SKRIPTI_TEKSTI", None)
            self.assertIsNotNone(sk, t)
            r = subprocess.run(["bash", "-n"], input=sk, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (t, r.stderr))

    def test_kokeilutiedostot_vastaavat_generaattoria(self):
        """kokeilut/sre-*: md (vain oire), alustus, tarkistus ja salainen.d-linkki ovat ajan tasalla."""
        self.assertEqual(luo.main(["--tarkista"]), 0, "aja: python3 hiekkalaatikko/sre/luo_kokeilut.py")

    def test_agentin_kuvaus_ei_sisalla_vikaa(self):
        for t, m in V.items():
            md = (JUURI / "hiekkalaatikko" / "kokeilut" / (m.KOKEILU + ".md")).read_text()
            self.assertIn(m.OIRE, md)
            self.assertNotRegex(md, r"v0\d_|injekt|\.hl-salainen|SKRIPTI|alustus")


class Vika1(Pohja):
    def test_injektointi_kirjoittaa_rikkinaisen_yksikon(self):
        V["v01"].injektoi(self.y)
        yks = self.y.lue("/etc/systemd/system/sre-raportti.service")
        self.assertIn("Environment=PATH=/opt/sre/bin", yks)
        self.assertIn(("systemctl", "daemon-reload"), self.vale.kutsut)

    def test_korjaamaton_ei_kay(self):
        V["v01"].injektoi(self.y)
        ok, syy = V["v01"].tarkista(self.y)
        self.assertFalse(ok)
        self.assertIn("ei kaynnisty", syy)

    def test_korjattu_kay(self):
        V["v01"].injektoi(self.y)
        self.vale.start_ok["sre-raportti.service"] = V["v01"].MERKKI
        self.assertEqual(V["v01"].tarkista(self.y), (True, "ok"))

    def test_tyokuorman_ontoistaminen_ei_kelpaa(self):
        V["v01"].injektoi(self.y)
        self.y.kirjoita(V["v01"].SKRIPTI, "#!/bin/bash\ntrue\n", 0o755)
        self.vale.start_ok["sre-raportti.service"] = V["v01"].MERKKI
        ok, syy = V["v01"].tarkista(self.y)
        self.assertFalse(ok)
        self.assertIn("rakenne puuttuu", syy)

    def test_absoluuttinen_polku_skriptissa_kelpaa(self):
        V["v01"].injektoi(self.y)
        t = self.y.lue(V["v01"].SKRIPTI).replace("runuser -u", "/usr/sbin/runuser -u")
        self.y.kirjoita(V["v01"].SKRIPTI, t, 0o755)
        self.vale.start_ok["sre-raportti.service"] = V["v01"].MERKKI
        self.assertTrue(V["v01"].tarkista(self.y)[0])

    @unittest.skipUnless(LINUX, "bash")
    def test_skripti_kuolee_ilman_runuseria_polussa(self):
        r = subprocess.run([shutil.which("bash"), "-c", V["v01"].SKRIPTI_TEKSTI.replace("/var/lib/sre", self.d)],
                           capture_output=True, text=True, env={"PATH": self.d + "/tyhja"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("runuser", r.stderr)


class Vika2(Pohja):
    def test_systemd_halkoo_lainaamattoman_arvon(self):
        v = "GIT_SSH_COMMAND=ssh -i /opt/sre/avain -o StrictHostKeyChecking=yes"
        self.assertGreater(len(shlex.split(v)), 1)         # systemd jakaa sanoihin kuten shell
        self.assertEqual(len(shlex.split('"%s"' % v)), 1)  # lainausmerkeilla yksi arvo

    def test_injektoitu_yksikko_on_lainaamaton(self):
        V["v02"].injektoi(self.y)
        rivi = [x for x in self.y.lue("/etc/systemd/system/sre-synkka.service").splitlines() if x.startswith("Environment=")][0]
        self.assertFalse(rivi.startswith('Environment="'))

    def test_tarkistus(self):
        V["v02"].injektoi(self.y)
        self.assertFalse(V["v02"].tarkista(self.y)[0])
        self.vale.start_ok["sre-synkka.service"] = V["v02"].MERKKI
        self.assertTrue(V["v02"].tarkista(self.y)[0])

    def test_tarkistuksen_heikentaminen_ei_kelpaa(self):
        V["v02"].injektoi(self.y)
        t = self.y.lue(V["v02"].SKRIPTI).replace('!= "$odotettu"', '= "x"')
        self.y.kirjoita(V["v02"].SKRIPTI, t, 0o755)
        self.vale.start_ok["sre-synkka.service"] = V["v02"].MERKKI
        self.assertFalse(V["v02"].tarkista(self.y)[0])

    @unittest.skipUnless(LINUX, "bash")
    def test_skripti_hylkaa_halkeneen_ja_hyvaksyy_oikean_arvon(self):
        sk = V["v02"].SKRIPTI_TEKSTI.replace("/var/lib/sre", self.d)
        ajo = lambda arvo: subprocess.run(["bash", "-c", sk], capture_output=True, text=True,
                                          env={"PATH": os.environ["PATH"], "GIT_SSH_COMMAND": arvo})
        self.assertEqual(ajo("ssh").returncode, 1)
        self.assertIn("vaara", ajo("ssh").stderr)
        self.assertEqual(ajo("ssh -i /opt/sre/avain -o StrictHostKeyChecking=yes").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.d, "synkka.ok")))


class Vika3(Pohja):
    def test_injektointi_luo_kayttajan_ja_sulkee_tiedoston(self):
        self.vale.vastaus[("id", "-u")] = (1, "")
        V["v03"].injektoi(self.y)
        self.assertIn(("useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", "sre-palvelu"), self.vale.kutsut)
        self.assertEqual(self.y.mode("/etc/sre/lukija.conf"), 0o600)
        self.assertIn(("chown", "root:root", self.y.p("/etc/sre/lukija.conf")), self.vale.kutsut)

    def test_kayttajaa_ei_luoda_kahdesti(self):
        V["v03"].injektoi(self.y)
        self.assertFalse([k for k in self.vale.kutsut if k[0] == "useradd"])

    def test_oikeudet_vaarin(self):
        V["v03"].injektoi(self.y)
        self.vale.start_ok["sre-lukija.service"] = V["v03"].MERKKI
        self.assertTrue(V["v03"].tarkista(self.y)[0])      # 0600: ok kun palvelu toimii (chown/ryhma korjasi)
        for mode in (0o644, 0o666, 0o777, 0o604):
            self.y.kirjoita(V["v03"].ASETUS, "tila=ok\n", mode)
            ok, syy = V["v03"].tarkista(self.y)
            self.assertFalse(ok, oct(mode))
            self.assertIn("liian laajat", syy)
        for mode in (0o640, 0o660):
            self.y.kirjoita(V["v03"].ASETUS, "tila=ok\n", mode)
            self.assertTrue(V["v03"].tarkista(self.y)[0], oct(mode))

    def test_korjaamaton_ei_kay(self):
        V["v03"].injektoi(self.y)
        self.assertFalse(V["v03"].tarkista(self.y)[0])

    def test_kayttajan_vaihto_rootiksi_ei_kelpaa(self):
        V["v03"].injektoi(self.y)
        yks = "/etc/systemd/system/sre-lukija.service"
        self.y.kirjoita(yks, self.y.lue(yks).replace("User=sre-palvelu\n", ""))
        self.vale.start_ok["sre-lukija.service"] = V["v03"].MERKKI
        ok, syy = V["v03"].tarkista(self.y)
        self.assertFalse(ok)
        self.assertIn("User=", syy)

    def test_asetustiedoston_poisto_ei_kelpaa(self):
        V["v03"].injektoi(self.y)
        self.y.poista(V["v03"].ASETUS)
        self.vale.start_ok["sre-lukija.service"] = V["v03"].MERKKI
        self.assertFalse(V["v03"].tarkista(self.y)[0])


class Vika4(Pohja):
    def test_injektointi_mounttaa_tmpfs_ja_tayttaa(self):
        V["v04"].injektoi(self.y)
        mount = [k for k in self.vale.kutsut if k[0] == "mount"][0]
        self.assertEqual(mount[:5], ("mount", "-t", "tmpfs", "-o", "size=1m"))
        self.assertTrue([k for k in self.vale.kutsut if k[0] == "dd" and k[2].endswith("vanha-vedos.bin")])

    def test_mountin_epaonnistuminen_nakyy_injektointivirheena(self):
        self.vale.vastaus[("mount",)] = (32, "denied")
        rc = L.kaynnista(V["v04"], ["injektoi"], self.y, io.StringIO())
        self.assertEqual(rc, 3)

    def test_tarkistus(self):
        V["v04"].injektoi(self.y)
        self.assertFalse(V["v04"].tarkista(self.y)[0])
        self.vale.start_ok["sre-data.service"] = V["v04"].MERKKI
        ok, syy = V["v04"].tarkista(self.y)
        self.assertFalse(ok)                               # tulos.dat puuttuu (tyokuorma ei oikeasti kirjoittanut)
        self.y.kirjoita("/var/lib/sre-data/tulos.dat", "x")
        self.assertTrue(V["v04"].tarkista(self.y)[0])

    def test_tyokuorman_kutistaminen_ei_kelpaa(self):
        V["v04"].injektoi(self.y)
        self.y.kirjoita(V["v04"].SKRIPTI, self.y.lue(V["v04"].SKRIPTI).replace("204800", "10"), 0o755)
        self.y.kirjoita("/var/lib/sre-data/tulos.dat", "x")
        self.vale.start_ok["sre-data.service"] = V["v04"].MERKKI
        self.assertFalse(V["v04"].tarkista(self.y)[0])


class Vika5(Pohja):
    def test_injektio_poistaa_muuttujan(self):
        V["v05"].injektoi(self.y)
        env = self.y.lue("/etc/sre/ymparisto.conf")
        self.assertNotIn("SRE_API_URL", env)
        self.assertIn("EnvironmentFile=/etc/sre/ymparisto.conf", self.y.lue("/etc/systemd/system/sre-api.service"))

    def test_tarkistus(self):
        V["v05"].injektoi(self.y)
        self.assertFalse(V["v05"].tarkista(self.y)[0])
        self.vale.start_ok["sre-api.service"] = V["v05"].MERKKI
        self.vale.merkin_sisalto = "http://127.0.0.1:8080\n"
        self.assertTrue(V["v05"].tarkista(self.y)[0])
        self.vale.merkin_sisalto = "\n"
        ok, syy = V["v05"].tarkista(self.y)
        self.assertFalse(ok)
        self.assertIn("URL", syy)

    @unittest.skipUnless(LINUX, "bash")
    def test_skripti_ilman_muuttujaa_ja_kanssa(self):
        sk = V["v05"].SKRIPTI_TEKSTI.replace("/var/lib/sre", self.d)
        ajo = lambda **e: subprocess.run(["bash", "-c", sk], capture_output=True, text=True, env=dict(PATH=os.environ["PATH"], **e))
        r = ajo(SRE_TILA="testi")
        self.assertEqual(r.returncode, 78)
        self.assertIn("SRE_API_URL puuttuu", r.stderr)
        self.assertEqual(ajo(SRE_TILA="testi", SRE_API_URL="http://127.0.0.1:8080").returncode, 0)
        self.assertEqual(ajo(SRE_TILA="testi", SRE_API_URL="ei-url").returncode, 78)


class Vika6(Pohja):
    def tila(self, enabled, active):
        self.vale.vastaus[("systemctl", "is-enabled")] = (0 if enabled == "enabled" else 1, enabled + "\n")
        self.vale.vastaus[("systemctl", "is-active")] = (0 if active == "active" else 3, active + "\n")

    def test_injektio_sammuttaa_ajastimen(self):
        V["v06"].injektoi(self.y)
        self.assertIn(("systemctl", "disable", "--now", "sre-siivous.timer"), self.vale.kutsut)
        self.assertIn("WantedBy=timers.target", self.y.lue("/etc/systemd/system/sre-siivous.timer"))

    def test_tilayhdistelmat(self):
        V["v06"].injektoi(self.y)
        self.vale.start_ok["sre-siivous.service"] = V["v06"].MERKKI
        for enabled, active, odotus in (("disabled", "inactive", False), ("enabled", "inactive", False),
                                        ("disabled", "active", False), ("enabled", "active", True)):
            self.tila(enabled, active)
            ok, syy = V["v06"].tarkista(self.y)
            self.assertEqual(ok, odotus, (enabled, active, syy))

    def test_ajastimen_ontoistaminen_ei_kelpaa(self):
        V["v06"].injektoi(self.y)
        self.tila("enabled", "active")
        self.vale.start_ok["sre-siivous.service"] = V["v06"].MERKKI
        t = "/etc/systemd/system/sre-siivous.timer"
        self.y.kirjoita(t, self.y.lue(t).replace("OnCalendar=*:0/5\n", ""))
        self.assertFalse(V["v06"].tarkista(self.y)[0])


class OnnistumisLogiikka(Pohja):
    def test_result_ei_success_hylataan(self):
        V["v01"].injektoi(self.y)
        self.vale.start_ok["sre-raportti.service"] = V["v01"].MERKKI
        self.vale.result = "exit-code"
        ok, syy = V["v01"].tarkista(self.y)
        self.assertFalse(ok)
        self.assertIn("Result=exit-code", syy)

    def test_vanha_merkki_ei_riita(self):
        V["v01"].injektoi(self.y)
        self.y.kirjoita(V["v01"].MERKKI, "vanha\n")       # agentti kirjoittaa merkin kasin
        ok, _ = V["v01"].tarkista(self.y)                  # start epaonnistuu -> hylataan
        self.assertFalse(ok)
        self.assertFalse(self.y.on(V["v01"].MERKKI))      # tarkistus poisti vanhan merkin ennen ajoa

    def test_cli_tulostaa_ok_ja_vika(self):
        V["v01"].injektoi(self.y)
        ulos = io.StringIO()
        self.assertEqual(L.kaynnista(V["v01"], ["tarkista"], self.y, ulos), 1)
        self.assertTrue(ulos.getvalue().startswith("VIKA: "))
        self.vale.start_ok["sre-raportti.service"] = V["v01"].MERKKI
        ulos = io.StringIO()
        self.assertEqual(L.kaynnista(V["v01"], ["tarkista"], self.y, ulos), 0)
        self.assertEqual(ulos.getvalue(), "OK: ok\n")

    def test_oire_komento(self):
        ulos = io.StringIO()
        self.assertEqual(L.kaynnista(V["v02"], ["oire"], L.Ymp(hostname="mikatahansa"), ulos), 0)
        self.assertEqual(ulos.getvalue().strip(), V["v02"].OIRE)


def rivi(kok, malli, tulos, kesto=100, kulu=0.01):
    return {"id": "x%s%s%s" % (kok, malli, kesto), "aika": "2026-10-31T10:00:00Z", "kokeilu": kok, "adapteri": "litellm", "malli": malli,
            "kesto_s": kesto, "kustannus_usd": kulu, "lopputulos": tulos, "rajarikkeet": [], "lokit": "/x"}


class YhteenvetoTesti(unittest.TestCase):
    def setUp(self):
        self.rivit = [
            rivi("sre-v01-polku", "a", "onnistui", 100, 0.02), rivi("sre-v02-valilyonti", "a", "onnistui", 300, 0.04),
            rivi("sre-v03-oikeus", "a", "epaonnistui", 900, 0.10), rivi("sre-v04-levy", "a", "keskeytetty", 1800, 0.5),
            rivi("sre-v01-polku", "b", "epaonnistui", 50, None), rivi("sre-v02-valilyonti", "b", "epaonnistui", 60, 0.01),
            rivi("turva-01", "a", "onnistui"),      # ei SRE: ei mukaan
        ]

    def test_mallikohtaiset_luvut(self):
        m = {x["malli"]: x for x in yhteenveto.mallikohtainen(self.rivit)}
        a = m["a"]
        self.assertEqual((a["ajoja"], a["ratkaistut"], a["onnistui"], a["keskeytetty"]), (4, 3, 2, 1))
        self.assertAlmostEqual(a["prosentti"], 200 / 3)
        self.assertEqual(a["korjausaika_mediaani_s"], 200)
        self.assertAlmostEqual(a["kustannus_summa_usd"], 0.66)
        b = m["b"]
        self.assertEqual((b["onnistui"], b["prosentti"], b["korjausaika_mediaani_s"], b["kustannus_tuntematon"]), (0, 0.0, None, 1))
        self.assertAlmostEqual(b["kustannus_summa_usd"], 0.01)

    def test_jarjestys_ja_taulut(self):
        self.assertEqual([x["malli"] for x in yhteenveto.mallikohtainen(self.rivit)], ["a", "b"])
        t = yhteenveto.taulu(self.rivit)
        self.assertIn("| a | 4 | 2/3 | 67 | 1 | 200 |", t)
        self.assertIn("| b | 2 | 0/2 | 0 | 0 | ? |", t)
        mv = yhteenveto.malli_vika_taulu(self.rivit)
        self.assertIn("| sre-v01-polku | 1/1 | 0/1 |", mv)
        self.assertIn("| sre-v04-levy | - | - |", mv)  # keskeytetty ei ole ratkaistu

    def test_komentorivi(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "kokeilut.jsonl")
        with open(p, "w") as f:
            f.write("\n".join(json.dumps(r) for r in self.rivit) + "\n")
        ulos = io.StringIO()
        vanha, sys.stdout = sys.stdout, ulos
        try:
            self.assertEqual(yhteenveto.main([p, "--malli-vika"]), 0)
        finally:
            sys.stdout = vanha
        self.assertIn("| malli |", ulos.getvalue())
        self.assertEqual(yhteenveto.main([]), 2)
        self.assertEqual(yhteenveto.main([os.path.join(d, "ei-ole")]), 3)


class MatriisiTesti(unittest.TestCase):
    def setUp(self):
        import contextlib
        pino = contextlib.ExitStack()
        pino.enter_context(contextlib.redirect_stdout(io.StringIO()))
        pino.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.addCleanup(pino.close)

    def test_viat_loytyvat_kokeiluista(self):
        v = matriisi.viat()
        self.assertEqual(sorted(v), sorted(V))
        self.assertEqual(v["v01"], "sre-v01-polku")

    def test_suunnitelma_ja_validointi(self):
        k = matriisi.viat()
        self.assertEqual(len(matriisi.suunnitelma(["a", "b"], ["kaikki"], k)), 2 * len(k))
        self.assertEqual(matriisi.suunnitelma(["a"], ["v02"], k), [("sre-v02-valilyonti", "a")])
        for huono in ("a b", "-x", "a;ls", "", "a$(x)"):
            with self.assertRaises(SystemExit):
                matriisi.suunnitelma([huono] if huono else [], ["v01"], k)
        with self.assertRaises(SystemExit):
            matriisi.suunnitelma(["a"], ["v99"], k)

    def test_paivaraja_ei_ylity_eika_kiertyy(self):
        kutsut = []
        ajo = lambda k, env: kutsut.append(k) or subprocess.CompletedProcess(k, 0)
        self.assertEqual(matriisi.main(["--mallit", "a,b,c"], env={}, ajo=ajo), 2)    # 18 > 10
        self.assertEqual(kutsut, [])
        self.assertEqual(matriisi.main(["--mallit", "a,b,c"], env={"HL_KOKEILUA_PAIVA": "100"}, ajo=ajo), 0)
        # 100 katkaistaan koodin kovaan rajaan 20: 18 ajoa mahtuu; 21 ei mahtuisi
        self.assertEqual(len(kutsut), 18)

    def test_ajo_keskeytyy_ensimmaiseen_virheeseen(self):
        kutsut = []

        def ajo(k, env):
            kutsut.append(k)
            return subprocess.CompletedProcess(k, 5 if len(kutsut) == 2 else 0)
        self.assertEqual(matriisi.main(["--mallit", "a,b", "--viat", "v01,v02"], env={}, ajo=ajo), 5)
        self.assertEqual(len(kutsut), 2)

    def test_komento_sisaltaa_vain_aja_kokeilun_ja_validoidut_arvot(self):
        k = matriisi.komento("sre-v01-polku", "qwen-coder", "litellm", True)
        self.assertEqual(k[0], "bash")
        self.assertTrue(k[1].endswith("hiekkalaatikko/aja-kokeilu.sh"))
        self.assertEqual(k[2:], ["--kuiva", "--kokeilu", "sre-v01-polku", "--adapteri", "litellm", "--malli", "qwen-coder"])


@unittest.skipUnless(LINUX and shutil.which("timeout") and shutil.which("flock"), "vaatii Linuxin, timeoutin ja flockin")
class AjokehysTesti(unittest.TestCase):
    """aja-kokeilu.sh:n salainen alustus vaaralla pct:lla: vikaskriptit ovat kontissa vain alustuksen ja tarkistuksen ajan."""

    def setUp(self):
        sys.path.insert(0, str(JUURI / "test"))
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        self.kopio = self.d / "hl"
        shutil.copytree(JUURI / "hiekkalaatikko", self.kopio, ignore=shutil.ignore_patterns("__pycache__"))
        (self.d / "sim").mkdir()
        (self.d / "fw").mkdir()
        pct = self.d / "pct"
        pct.write_text('#!/usr/bin/env bash\necho "$*" >> "$SIM/pct.log"\n'
                       'case "$1" in\n'
                       '  list) echo "VMID Status Name"; exit 0 ;;\n'
                       '  status) echo "status: running"; exit 0 ;;\n'
                       '  config) echo "hostname: hl-${2}"; echo "net0: name=eth0,bridge=vmbr2"; exit 0 ;;\n'
                       '  exec) if [ "${7##*/}" = alustus.sh ] && [ -n "${FAKE_ALUSTUS_RC:-}" ]; then exit "$FAKE_ALUSTUS_RC"; fi\n'
                       '        if [ "${7##*/}" = tarkistus.sh ]; then exit "${FAKE_TARKISTUS_RC:-0}"; fi; exit 0 ;;\n'
                       'esac\nexit 0\n')
        pct.chmod(0o755)
        self.env = {"PATH": os.environ["PATH"], "SIM": str(self.d / "sim"), "HL_PCT": str(pct), "HL_TILA": str(self.d / "tila"),
                    "HL_FW_HAKEMISTO": str(self.d / "fw")}

    def aja(self, kokeilu, **extra):
        e = dict(self.env)
        e.update(extra)
        return subprocess.run(["bash", str(self.kopio / "aja-kokeilu.sh"), "--kokeilu", kokeilu, "--adapteri", "kuiva"],
                              capture_output=True, text=True, env=e, timeout=120)

    def loki(self):
        return (self.d / "sim" / "pct.log").read_text().splitlines()

    def rivit(self):
        p = self.d / "tila" / "kokeilut.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def indeksi(self, loki, alku):
        return [i for i, x in enumerate(loki) if x.startswith(alku)]

    def test_vikaskriptit_poistetaan_ennen_adapteria_ja_tuodaan_tarkistukseen(self):
        r = self.aja("sre-v01-polku")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        loki = self.loki()
        alustus = [i for i, x in enumerate(loki) if "alustus.sh" in x and x.startswith("exec")]
        poisto = self.indeksi(loki, "exec 9100 -- rm -rf /root/.hl-salainen")
        adapteri = [i for i, x in enumerate(loki) if "kuiva.txt" in x]
        self.assertEqual(len(alustus), 1)
        self.assertEqual(len(poisto), 2, loki)                    # alustuksen ja tarkistuksen jalkeen
        self.assertTrue(alustus[0] < poisto[0] < adapteri[0] < poisto[1], loki)
        pushit = [x for x in loki if x.startswith("push") and ".hl-salainen" in x]
        self.assertTrue([x for x in pushit if x.endswith("v01_polku_puuttuu.py")])
        # agentin kuvaus on vain oire: KUVAUS.md pusketaan, mutta vikatiedosto ei mene /root/kokeilu:un
        self.assertFalse([x for x in loki if x.startswith("push") and "/root/kokeilu/" in x and "v01_" in x])
        self.assertEqual(self.rivit()[0]["lopputulos"], "onnistui")

    def test_tarkistus_ratkaisee_eika_agentin_vaite(self):
        r = self.aja("sre-v02-valilyonti", FAKE_TARKISTUS_RC="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rivit()[0]["lopputulos"], "epaonnistui")

    def test_alustuksen_epaonnistuminen_ei_ole_mallin_syy(self):
        r = self.aja("sre-v04-levy", FAKE_ALUSTUS_RC="3")
        self.assertEqual(r.returncode, 1)
        loki = self.loki()
        self.assertFalse([x for x in loki if "kuiva.txt" in x], "adapteria ei saa ajaa")
        self.assertEqual(self.rivit()[0]["lopputulos"], "keskeytetty")
        self.assertTrue(self.indeksi(loki, "exec 9100 -- rm -rf /root/.hl-salainen"), "salainen poistettiin silti")
        self.assertTrue(self.indeksi(loki, "destroy 9100"), "klooni tuhottiin")

    def test_yhteinen_skripti_valitsee_vian_kokeilun_nimesta(self):
        """sre.alustus.sh / sre.tarkistus.sh: nimi sre-vNN-* valitsee vNN_*.py:n; muu nimi hylataan."""
        bin_ = self.d / "bin"
        bin_.mkdir()
        (bin_ / "python3").write_text('#!/bin/sh\necho "$@" >> "$SIM/py.log"\n')
        (bin_ / "python3").chmod(0o755)
        tyo = self.d / "salainen"
        tyo.mkdir()
        for nimi in ("v01_polku_puuttuu.py", "v06_ajastin_ei_kaynnisty.py"):
            (tyo / nimi).write_text("")
        for skripti, komento in (("sre.alustus.sh", "injektoi"), ("sre.tarkistus.sh", "tarkista")):
            teksti = (self.kopio / "kokeilut" / skripti).read_text().replace("/root/.hl-salainen", str(tyo))
            polku = self.d / skripti
            polku.write_text(teksti)
            env = dict(self.env, PATH=str(bin_) + ":" + os.environ["PATH"])
            ajo = lambda *a: subprocess.run(["bash", str(polku), *a], capture_output=True, text=True, env=env)
            self.assertEqual(ajo("sre-v06-ajastin").returncode, 0)
            self.assertEqual((self.d / "sim" / "py.log").read_text().split(), ["v06_ajastin_ei_kaynnisty.py", komento])
            (self.d / "sim" / "py.log").unlink()
            for huono in ("", "turva-01", "sre-v99-x", "sre-v01", "../sre-v01-x", "sre-vab-x"):
                self.assertNotEqual(ajo(huono).returncode, 0, huono)
            self.assertFalse((self.d / "sim" / "py.log").exists())

    def test_muut_kokeilut_eivat_kayta_salaista(self):
        r = self.aja("turva-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse([x for x in self.loki() if ".hl-salainen" in x])

    def test_kuiva_ajo_nayttaa_alustuksen_eika_koske_mihinkaan(self):
        r = subprocess.run(["bash", str(self.kopio / "aja-kokeilu.sh"), "--kuiva", "--kokeilu", "sre-v06-ajastin", "--adapteri", "kuiva"],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alustus.sh", r.stdout)
        self.assertFalse((self.d / "sim" / "pct.log").exists())


if __name__ == "__main__":
    unittest.main()
