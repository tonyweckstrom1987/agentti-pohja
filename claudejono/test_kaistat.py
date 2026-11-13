"""Rinnakkaiset kaistat: varaus, lukot, kilpa-ajo oikeilla prosesseilla ja push-torma."""
import json
import multiprocessing
import os
import subprocess
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from claudejono import ajuri, silmukka
from claudejono.test_ajuri import Pohja, git, git_ulos


def malli_vastaus(teksti="raportti\nTodennettu k\u00e4yt\u00f6ss\u00e4: testiajo", **lisa):
    return json.dumps({"subtype": "success", "result": teksti, **lisa})


def tehtava_kehotteesta(kom):
    kehote = kom[kom.index("-p") + 1]
    return kehote.split("TEHTAVA-", 1)[1].split()[0]


class Asetus(unittest.TestCase):
    def test_kaistojen_maara_yhdesta_asetuksesta_ja_ylaraja(self):
        self.assertEqual(ajuri.kaistojen_maara({}), 1)
        self.assertEqual(ajuri.kaistojen_maara({"CLAUDEJONO_KAISTOJA": "2"}), 2)
        self.assertEqual(ajuri.kaistojen_maara({"CLAUDEJONO_KAISTOJA": "3"}), 3)
        self.assertEqual(ajuri.kaistojen_maara({"CLAUDEJONO_KAISTOJA": "9"}), ajuri.KAISTOJA_MAX)
        self.assertEqual(ajuri.kaistojen_maara({"CLAUDEJONO_KAISTOJA": "0"}), 1)
        self.assertEqual(ajuri.kaistojen_maara({"CLAUDEJONO_KAISTOJA": "x"}), 1)

    def test_kaista_ulkopuolella_hylataan(self):
        env = {"CLAUDEJONO_KAISTOJA": "2"}
        self.assertEqual(ajuri.tarkista_kaista(2, env), 2)
        for huono in (0, 3, -1, "2", None):
            with self.assertRaises(ajuri.JonoVirhe):
                ajuri.tarkista_kaista(huono, env)

    def test_kaistan_tyohakemisto_pysyy_pr_tyokalun_juuren_alla(self):
        self.assertEqual(ajuri.kaistan_tyohak("/opt/agentscope", 1), Path("/opt/agentscope/tyo"))
        self.assertEqual(ajuri.kaistan_tyohak("/opt/agentscope", 2), Path("/opt/agentscope/tyo/k2"))
        kom, env, cwd = ajuri.lapsen_ajo("k", {"CLAUDEJONO_KAYTTAJA": "claudejono"}, "/opt/agentscope", 3)
        self.assertEqual(cwd, "/opt/agentscope/tyo/k3")
        self.assertEqual(ajuri.lapsen_ajo("k", {"CLAUDEJONO_KAYTTAJA": "claudejono"}, "/opt/agentscope")[2], "/opt/agentscope/tyo")

    def test_kehote_kertoo_kaistan_hakemiston_vain_kaistoille_2_ja_3(self):
        self.assertEqual(ajuri.kaistahuomautus(1), "")
        self.assertIn("/opt/agentscope/tyo/k2/<repo>", ajuri.kaistahuomautus(2))


class Varaus(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tila = Path(tempfile.mkdtemp())

    def test_vain_yksi_saie_saa_varauksen(self):
        tulokset, portti = [], threading.Barrier(16)

        def yrita(n):
            portti.wait()
            tulokset.append(ajuri.varaa(self.tila, "2026-11-03-01-a.md", n))
        saikeet = [threading.Thread(target=yrita, args=(n,)) for n in range(16)]
        [s.start() for s in saikeet]
        [s.join() for s in saikeet]
        self.assertEqual(tulokset.count(True), 1)
        self.assertEqual(len(tulokset), 16)

    def test_toinen_varaus_epaonnistuu_ja_vapautus_avaa(self):
        self.assertTrue(ajuri.varaa(self.tila, "a", 1))
        self.assertFalse(ajuri.varaa(self.tila, "a", 2))
        self.assertFalse(ajuri.vapauta(self.tila, "a", 2))   # vieras kaista ei vapauta
        self.assertTrue(ajuri.vapauta(self.tila, "a", 1))
        self.assertTrue(ajuri.varaa(self.tila, "a", 2))

    def test_vanhentunut_varaus_otetaan_yli(self):
        self.assertTrue(ajuri.varaa(self.tila, "a", 1))
        self.assertFalse(ajuri.varaa(self.tila, "a", 2, nyt=time.time() + 100, vanhenee_s=3600))
        self.assertTrue(ajuri.varaa(self.tila, "a", 2, nyt=time.time() + 7200, vanhenee_s=3600))

    def test_kuolleen_prosessin_varaus_otetaan_yli(self):
        self.assertTrue(ajuri.varaa(self.tila, "a", 1))
        p = subprocess.Popen(["true"])
        p.wait()
        polku = self.tila / ajuri.VARAUS_HAK / "a"
        polku.write_text(json.dumps({"kaista": 1, "pid": p.pid, "aika": time.time()}))
        self.assertTrue(ajuri.varaa(self.tila, "a", 2))

    def test_vanhentunut_varaus_ei_poista_uuden_omistajan_varausta(self):
        self.assertTrue(ajuri.varaa(self.tila, "a", 1))
        self.assertTrue(ajuri.varaa(self.tila, "a", 2, nyt=time.time() + 7200, vanhenee_s=3600))
        self.assertFalse(ajuri.vapauta(self.tila, "a", 1))   # kaista 1 ei omista enaa
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tila), ["a"])

    def test_lukko_on_uudelleenkaytettava_samassa_saikeessa(self):
        with ajuri.lukko(self.tila / "l"):
            with ajuri.lukko(self.tila / "l") as odotus:
                self.assertEqual(odotus, 0.0)

    def test_lukko_aikakatkaisu_kertoo_tyypin(self):
        olio = {}
        valmis, vapaa = threading.Event(), threading.Event()

        def pida():
            with ajuri.lukko(self.tila / "l"):
                valmis.set()
                vapaa.wait(5)
        s = threading.Thread(target=pida)
        s.start()
        valmis.wait(5)
        with self.assertRaises(ajuri.JonoVirhe):
            with ajuri.lukko(self.tila / "l", odotus_s=0.2):
                olio["sisalla"] = True
        vapaa.set()
        s.join()
        self.assertNotIn("sisalla", olio)


def _kaista_tyontekija(repo, tyohak, kaista, loki, kesto, env):
    """Yksi kaista omassa prosessissaan: ajaa kunnes jono on tyhja."""
    def suorita(kom, **kw):
        nimi = tehtava_kehotteesta(kom)
        _kirjaa(loki, f"alku {nimi} {kaista} {time.time():.4f}")
        time.sleep(kesto)
        _kirjaa(loki, f"loppu {nimi} {kaista} {time.time():.4f}")
        return subprocess.CompletedProcess(kom, 0, malli_vastaus(f"valmis {nimi}\nTodennettu k\u00e4yt\u00f6ss\u00e4: testiajo"), "")
    while True:
        v = ajuri.aja(repo, tyohak, env=dict(env), suorita=suorita, kaista=kaista)
        if v.startswith("jono tyhja"):
            return


def _kirjaa(polku, rivi):
    fd = os.open(polku, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    os.write(fd, (rivi + "\n").encode())
    os.close(fd)


def _suunnitteleva_kaista(repo, tyohak, kaista, loki, viive, env):
    """Yksi kaista kerran: tehtava -> pitka ajo; ei tehtavaa -> suunnittelu (vastaus kirjataan lokiin)."""
    def suorita(kom, **kw):
        kehote = kom[kom.index("-p") + 1]
        if "Ehdota enintaan" in kehote:
            _kirjaa(loki, f"suunnittelu-alku {kaista} {time.time():.4f}")
            time.sleep(0.5)
            _kirjaa(loki, f"suunnittelu-loppu {kaista} {time.time():.4f}")
            return subprocess.CompletedProcess(kom, 0, malli_vastaus(json.dumps(
                {"tehtavat": [{"nimi": f"uusi-{kaista}", "sisalto": f"# Uusi {kaista}\nTavoite: yllapito"}], "tonylle": ""})), "")
        _kirjaa(loki, f"tehtava-alku {kaista} {time.time():.4f}")
        time.sleep(1.5)
        _kirjaa(loki, f"tehtava-loppu {kaista} {time.time():.4f}")
        return subprocess.CompletedProcess(kom, 0, malli_vastaus("valmis"), "")
    time.sleep(viive)
    _kirjaa(loki, f"tulos {kaista} " + ajuri.aja(repo, tyohak, env=dict(env), suorita=suorita, kaista=kaista).replace(" ", "_"))


class KilpaSuunnittelu(Pohja):
    def test_kaksi_kaistaa_ajaa_ja_kolmas_suunnittelee_rinnakkain(self):
        # jonossa kaksi tehtavaa: kaistat 1 ja 2 ottavat ne, kaista 3 (ja 4. yritys) suunnittelee niiden aikana
        for n in (1, 2):
            self.lisaa_tehtava(f"2026-11-03-{n:02d}-t.md", f"tee {n}")
        loki = str(self.tmp / "ajot.log")
        env = {"CLAUDEJONO_KAISTOJA": "3"}
        ctx = multiprocessing.get_context("fork")
        ps = [ctx.Process(target=_suunnitteleva_kaista, args=(self.repo, self.tmp, k, loki, v, env))
              for k, v in ((1, 0.0), (2, 0.3), (3, 0.9))]
        [p.start() for p in ps]
        [p.join(120) for p in ps]
        self.assertTrue(all(p.exitcode == 0 for p in ps), [p.exitcode for p in ps])
        rivit = [r.split() for r in Path(loki).read_text().splitlines()]
        aika = {(r[0], r[1]): float(r[2]) for r in rivit if r[0].endswith(("alku", "loppu"))}
        self.assertEqual(sorted(r[1] for r in rivit if r[0] == "suunnittelu-alku"), ["3"])
        # suunnittelu on paallekkain kaistan 1 ja 2 tehtavien kanssa
        for k in ("1", "2"):
            self.assertLess(aika[("tehtava-alku", k)], aika[("suunnittelu-loppu", "3")])
            self.assertGreater(aika[("tehtava-loppu", k)], aika[("suunnittelu-alku", "3")])
        tarkistus = self.tmp / "tarkistus"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(tarkistus)], check=True, capture_output=True)
        self.assertEqual(len(list((tarkistus / ajuri.JONO / "tehtavat").glob("*-auto-uusi-3.md"))), 1)
        self.assertEqual(sorted(p.name for p in (tarkistus / ajuri.JONO / "tulokset").iterdir()),
                         ["2026-11-03-01-t.md", "2026-11-03-02-t.md"])
        subprocess.run(["git", "-C", str(self.origin), "fsck", "--strict"], check=True, capture_output=True)
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tmp), [])

    def test_kaksi_vapaata_kaistaa_yksi_suunnittelija(self):
        self.lisaa_tehtava("2026-11-03-01-t.md", "tee 1")
        loki = str(self.tmp / "ajot.log")
        env = {"CLAUDEJONO_KAISTOJA": "3"}
        ctx = multiprocessing.get_context("fork")
        ps = [ctx.Process(target=_suunnitteleva_kaista, args=(self.repo, self.tmp, k, loki, v, env))
              for k, v in ((1, 0.0), (2, 0.4), (3, 0.4))]
        [p.start() for p in ps]
        [p.join(120) for p in ps]
        self.assertTrue(all(p.exitcode == 0 for p in ps), [p.exitcode for p in ps])
        rivit = [r.split() for r in Path(loki).read_text().splitlines()]
        self.assertEqual(len([r for r in rivit if r[0] == "suunnittelu-alku"]), 1)   # varaus: vain yksi suunnittelija
        self.assertEqual(len([r for r in rivit if r[0] == "tehtava-alku"]), 1)


class KilpaAjo(Pohja):
    def aja_kaistat(self, kaistoja, tehtavia, kesto=0.3):
        for n in range(1, tehtavia + 1):
            self.lisaa_tehtava(f"2026-11-03-{n:02d}-t.md", f"TEHTAVA-2026-11-03-{n:02d}-t.md")
        loki = str(self.tmp / "ajot.log")
        env = {"CLAUDEJONO_ITSESUUNNITTELU": "0", "CLAUDEJONO_KAISTOJA": str(kaistoja)}
        ctx = multiprocessing.get_context("fork")
        prosessit = [ctx.Process(target=_kaista_tyontekija, args=(self.repo, self.tmp, k, loki, kesto, env))
                     for k in range(1, kaistoja + 1)]
        [p.start() for p in prosessit]
        [p.join(120) for p in prosessit]
        self.assertTrue(all(p.exitcode == 0 for p in prosessit), [p.exitcode for p in prosessit])
        rivit = [r.split() for r in Path(loki).read_text().splitlines()]
        return rivit

    def tarkista(self, rivit, tehtavia, kaistoja, rinnakkain=True):
        alut = [r for r in rivit if r[0] == "alku"]
        nimet = [r[1] for r in alut]
        self.assertEqual(len(nimet), tehtavia)
        self.assertEqual(len(set(nimet)), tehtavia, f"sama tehtava kahdesti: {nimet}")   # EI KOSKAAN samaa tehtavaa kahdesti
        if rinnakkain:
            self.assertEqual({r[2] for r in alut}, {str(k) for k in range(1, kaistoja + 1)})   # kaikki kaistat tekivat tyota
        # aidosti rinnakkain: jokin kahden eri kaistan ajo on paallekkain
        jaksot = {}
        for r in rivit:
            jaksot.setdefault((r[1], r[2]), {})[r[0]] = float(r[3])
        lista = list(jaksot.items())
        paallekkain = any(a[0][1] != b[0][1] and a[1]["alku"] < b[1]["loppu"] and b[1]["alku"] < a[1]["loppu"]
                          for i, a in enumerate(lista) for b in lista[i + 1:])
        if rinnakkain:
            self.assertTrue(paallekkain, "ajot eivat olleet paallekkain")
        # origin: kaikki tulokset kirjattu, ei hukkaa eika kaksoiskirjauksia, historia ehea
        tarkistus = self.tmp / "tarkistus"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(tarkistus)], check=True, capture_output=True)
        tulokset = sorted(p.name for p in (tarkistus / ajuri.JONO / "tulokset").iterdir())
        self.assertEqual(tulokset, sorted(nimet))
        for n in nimet:
            self.assertIn("VALMIS", (tarkistus / ajuri.JONO / "tulokset" / n).read_text())
        self.assertEqual(git_ulos(tarkistus, "status", "--porcelain"), "")
        subprocess.run(["git", "-C", str(self.origin), "fsck", "--strict"], check=True, capture_output=True)
        self.assertEqual(git_ulos(self.repo, "status", "--porcelain"), "")
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tmp), [])   # kaikki varaukset vapautettu

    def test_kaksi_kaistaa_eivat_ota_samaa_tehtavaa(self):
        self.tarkista(self.aja_kaistat(2, 8), 8, 2)

    def test_kolme_kaistaa_eivat_ota_samaa_tehtavaa(self):
        self.tarkista(self.aja_kaistat(3, 9, kesto=0.4), 9, 3)

    def test_toistettu_kilpa_ajo_ei_koskaan_tuplaa(self):
        # Kilpa on aikariippuvainen: toistetaan lyhyilla ajoilla, jotta lukitukseen osuu useita eri lomituksia.
        for _ in range(3):
            Pohja.setUp(self)
            self.tarkista(self.aja_kaistat(2, 6, kesto=0.02), 6, 2, rinnakkain=False)

    def test_ilman_varausta_kilpa_olisi_tuplannut(self):
        """Todistaa testin tehon: jos varaus ohitetaan (aina True), sama tehtava lahtee molemmille kaistoille."""
        for n in range(1, 4):
            self.lisaa_tehtava(f"2026-11-03-{n:02d}-t.md", f"TEHTAVA-2026-11-03-{n:02d}-t.md")
        loki = str(self.tmp / "ajot.log")
        env = {"CLAUDEJONO_ITSESUUNNITTELU": "0", "CLAUDEJONO_KAISTOJA": "2"}

        def rikki(*a, **k):
            return True
        ctx = multiprocessing.get_context("fork")

        def tyontekija(kaista):
            with mock.patch.object(ajuri, "varaa", rikki):
                try:
                    _kaista_tyontekija(self.repo, self.tmp, kaista, loki, 0.3, env)
                except Exception:
                    pass
        ps = [ctx.Process(target=tyontekija, args=(k,)) for k in (1, 2)]
        [p.start() for p in ps]
        [p.join(60) for p in ps]
        nimet = [r.split()[1] for r in Path(loki).read_text().splitlines() if r.startswith("alku")]
        self.assertGreater(len(nimet), len(set(nimet)), "ilman varausta tuplaus olisi pitanyt nakya")


class Ajo(Pohja):
    def setUp(self):
        super().setUp()
        # Nama testit koskevat kaistojen varauksia, eivat toistuvia tehtavia: tyhja jono pysyy tyhjana.
        paikka = mock.patch.object(silmukka, "toistuvat_erapaivalla", return_value=[])
        paikka.start()
        self.addCleanup(paikka.stop)

    def f(self, palaute=None, koodi=0, sivu=None):
        def suorita(kom, **kw):
            if sivu:
                sivu(kom, kw)
            return subprocess.CompletedProcess(kom, koodi, json.dumps(palaute or {"subtype": "success", "result": "r\nTodennettu k\u00e4yt\u00f6ss\u00e4: testiajo"}), "")
        return suorita

    def test_push_torma_palautuu_pull_rebasella(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        muu = self.tmp / "muu"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(muu)], check=True, capture_output=True)
        git(muu, "config", "user.email", "m@m")
        git(muu, "config", "user.name", "m")

        def toinen_ehtii_ensin(kom, kw):   # origin etenee Clauden ajon aikana: ajurin push on non-fast-forward
            (muu / "muu.txt").write_text("x\n")
            git(muu, "add", "muu.txt")
            git(muu, "commit", "-q", "-m", "muu")
            git(muu, "push", "-q", "origin", "main")
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_ITSESUUNNITTELU": "0"}, suorita=self.f(sivu=toinen_ehtii_ensin))
        self.assertEqual(v, "claude-jono: 2026-11-03-01-a.md VALMIS")   # ei "push epaonnistui"
        loki = git_ulos(self.origin, "log", "--format=%s", "main").splitlines()
        self.assertIn("muu", loki)
        self.assertIn("claude-jono: tulos 2026-11-03-01-a.md", loki)

    def test_konflikti_perutaan_eika_klooni_jaa_kesken(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        muu = self.tmp / "muu"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(muu)], check=True, capture_output=True)
        git(muu, "config", "user.email", "m@m")
        git(muu, "config", "user.name", "m")

        def sama_tiedosto(kom, kw):   # tuhoisa tilanne: joku kirjoittaa saman tulostiedoston originiin
            p = muu / ajuri.JONO / "tulokset" / "2026-11-03-01-a.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("toinen\n")
            git(muu, "add", "-A")
            git(muu, "commit", "-q", "-m", "toinen tulos")
            git(muu, "push", "-q", "origin", "main")
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_ITSESUUNNITTELU": "0"}, suorita=self.f(sivu=sama_tiedosto))
        self.assertIn("push epaonnistui", v)
        self.assertFalse((self.repo / ".git" / "rebase-merge").exists())
        self.assertFalse((self.repo / ".git" / "rebase-apply").exists())

    def test_varaus_vapautuu_virheessa_ja_tehtava_on_taas_otettavissa(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")

        def kaatuu(kom, **kw):
            raise RuntimeError("rikki")
        with self.assertRaises(RuntimeError):
            ajuri.aja(self.repo, self.tmp, env={}, suorita=kaatuu)
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tmp), [])
        p = ajuri.varaa_seuraava(self.repo, self.tmp, 2)
        self.assertEqual(p.name, "2026-11-03-01-a.md")

    def test_toisen_kaistan_varaamaa_tehtavaa_ei_oteta_vaan_seuraava(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        self.lisaa_tehtava("2026-11-03-02-b.md")
        self.assertTrue(ajuri.varaa(self.tmp, "2026-11-03-01-a.md", 1))
        nahdyt = []
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "2", "CLAUDEJONO_ITSESUUNNITTELU": "0"},
                      suorita=self.f(sivu=lambda kom, kw: nahdyt.append(kw["cwd"])), kaista=2)
        self.assertEqual(v, "claude-jono: 2026-11-03-02-b.md VALMIS [kaista 2]")
        self.assertTrue(nahdyt[0].endswith("/tyo/k2"), nahdyt)
        self.assertTrue((self.tmp / "tyo" / "k2").is_dir())

    def test_kaistan_2_kehote_sisaltaa_hakemiston(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        kehotteet = []
        ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "2", "CLAUDEJONO_ITSESUUNNITTELU": "0"},
                  suorita=self.f(sivu=lambda kom, kw: kehotteet.append(kom[kom.index("-p") + 1])), kaista=2)
        self.assertIn("/opt/agentscope/tyo/k2/<repo>", kehotteet[0])

    def test_kaista_yli_asetuksen_hylataan_ennen_mitaan(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        with self.assertRaises(ajuri.JonoVirhe):
            ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "1"}, suorita=self.f(), kaista=2)
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tmp), [])

    def test_vapaa_kaista_suunnittelee_vaikka_toinen_kaista_on_toissa(self):
        # 3.11. klo 11: kaistat 2 ja 3 jaivat tyhjaksi, koska suunnittelu odotti kaikkien kaistojen vapautumista
        self.assertTrue(ajuri.varaa(self.tmp, "2026-11-03-01-a.md", 1))
        vastaus = json.dumps({"tehtavat": [{"nimi": "uusi", "sisalto": "# Uusi\nTavoite: yllapito"}], "tonylle": ""})
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "3"}, kaista=2,
                      suorita=self.f({"subtype": "success", "result": vastaus}))
        self.assertIn("itsesuunnittelu", v)
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tmp), ["2026-11-03-01-a.md"])   # kaistan 1 varaus ei haviaa

    def test_suunnittelu_tarpeen_vain_kun_avoimia_alle_2_x_kaistat(self):
        env = {"CLAUDEJONO_KAISTOJA": "3"}
        for i in range(5):
            self.lisaa_tehtava(f"2026-11-03-0{i + 1}-t{i}.md", f"sisalto {i}")
        self.assertTrue(ajuri.suunnittelu_tarpeen(self.repo, self.tmp, env))        # 5 < 6
        self.lisaa_tehtava("2026-11-03-06-t5.md", "sisalto 5")
        self.assertFalse(ajuri.suunnittelu_tarpeen(self.repo, self.tmp, env))       # 6 ei ole < 6
        tul = self.repo / ajuri.JONO / "tulokset"
        tul.mkdir(parents=True)
        (tul / "2026-11-03-01-t0.md").write_text("tehty")
        self.assertTrue(ajuri.suunnittelu_tarpeen(self.repo, self.tmp, env))        # tehty ei ole avoin
        self.assertTrue(ajuri.varaa(self.tmp, "2026-11-03-02-t1.md", 1))
        self.assertEqual(ajuri.avoimet_tehtavat(self.repo, self.tmp), 4)             # varattu ei ole avoin

    def test_suunnittelu_ei_luo_jonossa_tai_tehtya_tehtavaa(self):
        self.lisaa_tehtava("2026-11-03-01-auto-sama-nimi.md", "# Eri sisalto", raaka=True)
        self.lisaa_tehtava("2026-11-02-04-vanha-sisalto.md", "# Tehty\n\nTee   tama.", raaka=True)
        tul = self.repo / ajuri.JONO / "tulokset"
        tul.mkdir(parents=True)
        (tul / "2026-11-03-01-auto-sama-nimi.md").write_text("tehty")
        (tul / "2026-11-02-04-vanha-sisalto.md").write_text("tehty")
        vastaus = json.dumps({"tehtavat": [
            {"nimi": "sama-nimi", "sisalto": "# Muu"},                    # nimi jo tehty
            {"nimi": "kopio", "sisalto": "<!-- x -->\n# TEHTY tee tama."},  # sisalto sama (tyhjat, kirjainkoko, kommentti ohitetaan)
            {"nimi": "oikeasti-uusi", "sisalto": "# Uusi\nTavoite: yllapito"},
            {"nimi": "oikeasti-uusi", "sisalto": "# Uusi 2\nTavoite: yllapito"},             # sama nimi samassa erassa
        ], "tonylle": ""})
        ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "3"}, kaista=2,
                  suorita=self.f({"subtype": "success", "result": vastaus}))
        luodut = sorted(p.name for p in (self.repo / ajuri.JONO / "tehtavat").glob("*-auto-*.md"))
        # Uuden tehtavan paivamaara on ajopaiva (ei kiinteaa paivaa: 4.11. kiintea 2026-11-03 kaatoi testin, aikapommi).
        self.assertEqual([n for n in luodut if "kopio" in n], [])
        self.assertEqual([n for n in luodut if "sama-nimi" in n], ["2026-11-03-01-auto-sama-nimi.md"])
        uudet = [n for n in luodut if "oikeasti-uusi" in n]
        self.assertEqual(len(uudet), 1, uudet)
        self.assertTrue(uudet[0].startswith(datetime.now().astimezone().date().isoformat() + "-"), uudet)

    def test_porttitarkistin_ajetaan_jokaiselle_uudelle_tehtavalle(self):
        nahdyt = []

        def portti(sisalto):
            nahdyt.append(sisalto)
            return "kielletty" if "rahaa" in sisalto else None
        ok, hyl = ajuri.suodata_uudet(self.repo, [{"nimi": "a", "sisalto": "# a"}, {"nimi": "b", "sisalto": "osta rahaa"},
                                                  {"nimi": "c", "sisalto": "# c"}], portti)
        self.assertEqual([t["nimi"] for t in ok], ["a", "c"])
        self.assertEqual(hyl, [("b", "porttitarkistin: kielletty")])
        self.assertEqual(len(nahdyt), 3)

    def test_paivakatto_pysyy_kun_toinen_kaista_on_toissa(self):
        tul = self.repo / ajuri.JONO / "tulokset"
        tul.mkdir(parents=True)
        for i in range(ajuri.AUTO_MAX_PAIVA):
            n = f"{datetime.now().astimezone().date().isoformat()}-{i + 1:02d}-auto-x{i}.md"
            self.lisaa_tehtava(n, f"s{i}")
            (tul / n).write_text("tehty")
        self.assertTrue(ajuri.varaa(self.tmp, "2026-11-03-01-a.md", 1))
        kutsut = []
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "3"}, kaista=2,
                      suorita=self.f(sivu=lambda k, w: kutsut.append(1)))
        self.assertIn("paivaraja", v)
        self.assertEqual(kutsut, [])

    def test_vain_yksi_kaista_suunnittelee(self):
        self.assertTrue(ajuri.varaa(self.tmp, ajuri.SUUNNITTELU_VARAUS, 1))
        kutsut = []
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "2"}, suorita=self.f(sivu=lambda k, w: kutsut.append(1)), kaista=2)
        self.assertIn("suunnittelee", v)
        self.assertEqual(kutsut, [])

    def test_suunnittelu_varaa_ja_vapauttaa(self):
        vastaus = json.dumps({"tehtavat": [{"nimi": "a", "sisalto": "# A\n\nTavoite: yllapito\nPisteet: hyoty 4, varmuus 4, vaiva 2 h\nSuunta: jatkoa (kompassi 1)\nValmis kun: PR.\n"}], "tonylle": ""})
        nahdyt = []
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_KAISTOJA": "2"}, kaista=2,
                      suorita=self.f({"subtype": "success", "result": vastaus},
                                     sivu=lambda k, w: nahdyt.append(ajuri.aktiiviset_varaukset(self.tmp))))
        self.assertIn("itsesuunnittelu", v)
        self.assertEqual(nahdyt, [[ajuri.SUUNNITTELU_VARAUS]])
        self.assertEqual(ajuri.aktiiviset_varaukset(self.tmp), [])

    def test_kaistaloki_kirjaa_kaistan_keston_ja_rajaan_osumisen(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        self.lisaa_tehtava("2026-11-03-02-b.md")
        env = {"CLAUDEJONO_KAISTOJA": "2", "CLAUDEJONO_ITSESUUNNITTELU": "0"}
        ajuri.aja(self.repo, self.tmp, env=env, kaista=1,
                  suorita=self.f({"subtype": "success", "result": "r\nTodennettu k\u00e4yt\u00f6ss\u00e4: testiajo", "num_turns": 7, "total_cost_usd": 1.25}))
        ajuri.aja(self.repo, self.tmp, env=env, kaista=2,
                  suorita=self.f({"subtype": "error_during_execution", "is_error": True, "result": "Claude AI usage limit reached|1762170000"}))
        rivit = [json.loads(r) for r in (self.tmp / ajuri.KAISTALOKI).read_text().splitlines()]
        self.assertEqual([r["kaista"] for r in rivit], [1, 2])
        self.assertEqual((rivit[0]["ok"], rivit[0]["raja"], rivit[0]["kierrokset"], rivit[0]["arvo_usd"]), (True, False, 7, 1.25))
        self.assertEqual((rivit[1]["ok"], rivit[1]["raja"]), (False, True))
        self.assertIn("kesto_s", rivit[0])

    def test_kaistaloki_virhe_ei_kaada_ajoa(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        (self.tmp / ajuri.KAISTALOKI).mkdir()   # kirjoitus epaonnistuu (hakemisto)
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_ITSESUUNNITTELU": "0"}, suorita=self.f())
        self.assertIn("VALMIS", v)


class Paaohjelma(Pohja):
    def test_oma_lukko_per_kaista(self):
        import fcntl
        env = {"CLAUDEJONO_REPO": str(self.repo), "CLAUDEJONO_TYOHAK": str(self.tmp), "CLAUDEJONO_KAISTOJA": "2",
               "CLAUDEJONO_ITSESUUNNITTELU": "0"}
        with mock.patch.dict(os.environ, env), open(self.tmp / "lukko-1", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            from io import StringIO
            with mock.patch("sys.stdout", new=StringIO()) as ulos:
                self.assertEqual(ajuri.main(["ajo", "--kaista", "1"]), 0)
            self.assertIn("toinen ajo kaynnissa (kaista 1)", ulos.getvalue())
            with mock.patch("sys.stdout", new=StringIO()) as ulos:
                self.assertEqual(ajuri.main(["ajo", "--kaista", "2"]), 0)   # kaista 2 ei j\u00e4\u00e4 kaistan 1 lukon taakse
            self.assertIn("jono tyhja", ulos.getvalue())

    def test_kaista_yli_asetuksen_palauttaa_2_eika_aja(self):
        env = {"CLAUDEJONO_REPO": str(self.repo), "CLAUDEJONO_TYOHAK": str(self.tmp), "CLAUDEJONO_KAISTOJA": "2"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(ajuri.main(["ajo", "--kaista", "3"]), 2)
            self.assertFalse((self.tmp / "lukko-3").exists())


if __name__ == "__main__":
    unittest.main()
