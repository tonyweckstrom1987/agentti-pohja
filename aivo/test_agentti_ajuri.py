"""Ajurin testit (osa 3): kansioiden oikeudet ja polut, jonosta luku, yksi tehtava kerrallaan, aikaraja,
tulostiedoston muoto, yksikkomalli ja rakennetesti. Ei verkkoa: mallikutsut vain korvikekuljettimella."""
import ast
import io
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

from aivo import agentti_ajuri as aj
from aivo.agentti import malli as am
from aivo.test_agentti_malli import Pohja, MALLI, NYT

JUURI = pathlib.Path(__file__).resolve().parent.parent
YKSIKKO = JUURI / "systemd" / "aivo-agentit.service.mall"
T1 = "0123456789ab"
T2 = "abcdef012345"


def lapsi_koodi(tulos):
    """Korvikelapsi: lukee syotteen ja tulostaa annetun tuloksen (ei mallia, ei verkkoa)."""
    return [sys.executable, "-c",
            "import sys, json; d = json.load(sys.stdin); t = json.loads(sys.argv[1]); "
            "t['raportti'] = t.get('raportti', '') + str(len(d['teksti'])); print(json.dumps(t))",
            json.dumps(tulos)]


VALMIS_TULOS = {"tila": "valmis", "syy": "", "raportti": "pituus ", "tarkastus": {"arvio": "hyvaksytty",
                "perustelu": "ok"}, "kutsuja": 4, "askelia": 2, "kulu_eur": 0.0012, "kulu_tuntematon": False,
                "mallit": {"suunnittelija": MALLI, "tekija": MALLI, "tarkastaja": MALLI}, "rajat": am.RAJAT,
                "loki": [{"askel": 1, "nimi": "kirjoita", "ok": True, "selite": "x"}], "kesto_s": 1.0}


class Kansiot(unittest.TestCase):
    """Luo oikein asennetun kansiorakenteen tilapaishakemistoon (omistaja = testin kayttaja)."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.perus = os.path.join(self._t.name, "aivo-agentit")
        os.mkdir(self.perus)
        # macOS (BSD): uusi kansio perii ylakansion ryhman, eika setgid-bittia voi asettaa vieraan ryhman kansiolle.
        os.chown(self.perus, -1, os.getegid())
        os.chmod(self.perus, 0o750)
        for nimi, tila in ((aj.JONO, 0o2770), (aj.KASITELLYT, 0o700), (aj.TYO, 0o700), (aj.TULOKSET, 0o2750)):
            p = os.path.join(self.perus, nimi)
            os.mkdir(p)
            os.chown(p, -1, os.getegid())
            os.chmod(p, tila)
            self.assertEqual(stat.S_IMODE(os.lstat(p).st_mode), tila, "testikansion oikeudet")

    def p(self, *osat):
        return os.path.join(self.perus, *osat)

    def jonoon(self, tunnus, data=None, aika=None):
        data = {"versio": 1, "teksti": "Kirjoita tiedosto tulos.md."} if data is None else data
        polku = self.p(aj.JONO, tunnus + ".json")
        with open(polku, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        if aika is not None:
            os.utime(polku, (aika, aika))
        return polku

    def kulutila(self):
        # testin kayttaja omistaa tiedostot: tarkistus nakee ne chatin kayttajan omistamina
        if not getattr(self, "_euid_estin", None):
            self._euid_estin = mock.patch.object(aj, "_palvelun_uid", return_value=os.geteuid() + 1)
            self._euid_estin.start()
            self.addCleanup(self._euid_estin.stop)
        kt = os.path.join(self._t.name, "kulut")
        if not os.path.isdir(kt):
            os.mkdir(kt, 0o700)
            for nimi in ("openrouter-kulut.jsonl", "openrouter-katto.jsonl", "openrouter-kulut.lock"):
                open(os.path.join(kt, nimi), "w").close()
        return kt

    def tulos(self, tunnus):
        with open(self.p(aj.TULOKSET, tunnus + ".json"), encoding="utf-8") as f:
            return json.load(f)


class KansioTesti(Kansiot):
    def test_oikea_asennus_kelpaa(self):
        self.assertEqual(aj.tarkista_kansiot(self.perus), [])

    def test_puuttuva_kansio(self):
        os.rmdir(self.p(aj.TYO))
        self.assertTrue(any(v.startswith("tyo: puuttuu") for v in aj.tarkista_kansiot(self.perus)))

    def test_vaaraomistaja(self):
        virheet = aj.tarkista_kansiot(self.perus, uid=os.geteuid() + 1)
        self.assertEqual(len([v for v in virheet if "omistaja" in v]), 5)

    def test_liian_laajat_oikeudet(self):
        for nimi, tila, odotus in (("", 0o755, "peruskansio"), (aj.TYO, 0o750, "tyo"),
                                   (aj.KASITELLYT, 0o710, "kasitellyt"), (aj.JONO, 0o2777, "jono"),
                                   (aj.JONO, 0o770, "jono"), (aj.TULOKSET, 0o2755, "tulokset"),
                                   (aj.TULOKSET, 0o750, "tulokset")):
            with self.subTest(nimi=nimi, tila=oct(tila)):
                polku = self.p(nimi) if nimi else self.perus
                vanha = stat.S_IMODE(os.lstat(polku).st_mode)
                os.chmod(polku, tila)
                try:
                    virheet = aj.tarkista_kansiot(self.perus)
                finally:
                    os.chmod(polku, vanha)
                self.assertTrue(any(v.startswith(odotus) for v in virheet), virheet)

    def test_linkki_kansion_tilalla_hylataan(self):
        os.rmdir(self.p(aj.TULOKSET))
        muu = os.path.join(self._t.name, "muu")
        os.mkdir(muu, 0o2750)
        os.symlink(muu, self.p(aj.TULOKSET))
        self.assertTrue(any("linkki" in v for v in aj.tarkista_kansiot(self.perus)))

    def test_cli_tarkista(self):
        kt = self.kulutila()
        estin = mock.patch.object(am.openrouter_kulut, "OLETUS_TILA", kt)
        estin.start()
        self.addCleanup(estin.stop)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as ulos:
            self.assertEqual(aj.main(["tarkista", "--perus", self.perus, "--roolit", MALLI]), 0)
        self.assertIn("OK", ulos.getvalue())
        os.chmod(self.p(aj.TYO), 0o755)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as ulos:
            self.assertEqual(aj.main(["tarkista", "--perus", self.perus, "--roolit", "openai/gpt-4o"]), 1)
        self.assertIn("VIRHEITA 2", ulos.getvalue())


class JonoTesti(Kansiot):
    def test_vanhin_ensin_ja_vain_kelvolliset_nimet(self):
        self.jonoon(T2, aika=1000)
        self.jonoon(T1, aika=2000)
        for nimi in ("x.json", "AAAAAAAAAAAA.json", "0123456789ab.json.tmp", ".0123456789ab.json"):
            open(self.p(aj.JONO, nimi), "w").close()
        os.mkdir(self.p(aj.JONO, "fedcba987654.json"))
        os.symlink(self.p(aj.JONO, T1 + ".json"), self.p(aj.JONO, "111111111111.json"))
        self.assertEqual(aj.jonossa(self.perus), [(T2, T2 + ".json"), (T1, T1 + ".json")])
        self.assertEqual(aj.kelvottomat_jononimet(self.perus), 3)

    def test_kelvollinen_syote(self):
        self.jonoon(T1, {"versio": 1, "teksti": "t", "keskustelu": "k-1",
                         "liitteet": [{"nimi": "a/b.md", "sisalto": "x"}]})
        self.assertEqual(aj.lue_syote(self.p(aj.JONO, T1 + ".json")), ("t", [("a/b.md", "x")], "k-1"))

    def test_hylatyt_syotteet(self):
        perus = {"versio": 1, "teksti": "t"}
        tapaukset = [
            ("ei json", "{"), ("lista", []), ("tuntematon kentta", dict(perus, budjetti_eur=1)),
            ("versio", dict(perus, versio=2)), ("versio bool", dict(perus, versio=True)),
            ("teksti puuttuu", {"versio": 1}), ("tyhja teksti", dict(perus, teksti="  ")),
            ("liian pitka", dict(perus, teksti="x" * (aj.RAJA_TEKSTI + 1))),
            ("keskustelu", dict(perus, keskustelu="../x")),
            ("liitteet ei lista", dict(perus, liitteet={})),
            ("liitteita liikaa", dict(perus, liitteet=[{"nimi": "a%d.md" % i, "sisalto": ""}
                                                        for i in range(aj.RAJA_LIITTEET + 1)])),
            ("..", dict(perus, liitteet=[{"nimi": "../a.md", "sisalto": ""}])),
            ("absoluuttinen", dict(perus, liitteet=[{"nimi": "/etc/a.md", "sisalto": ""}])),
            ("paate", dict(perus, liitteet=[{"nimi": "a.py", "sisalto": ""}])),
            ("varattu", dict(perus, liitteet=[{"nimi": am.SUUNNITELMA_TIEDOSTO, "sisalto": ""}])),
            ("kahdesti", dict(perus, liitteet=[{"nimi": "a.md", "sisalto": ""}, {"nimi": "./a.md", "sisalto": ""}])),
            ("liitteen kentat", dict(perus, liitteet=[{"nimi": "a.md", "sisalto": "", "x": 1}])),
        ]
        for nimi, data in tapaukset:
            with self.subTest(nimi):
                polku = self.jonoon(T1, data)
                with self.assertRaises(aj.SyoteVirhe):
                    aj.lue_syote(polku)

    def test_liian_suuri_ja_linkki(self):
        polku = self.jonoon(T1, "x" * (aj.RAJA_SYOTE_TAVUT + 1))
        with self.assertRaisesRegex(aj.SyoteVirhe, "liian suuri"):
            aj.lue_syote(polku)
        os.unlink(polku)
        kohde = self.jonoon(T2)
        os.symlink(kohde, polku)
        with self.assertRaisesRegex(aj.SyoteVirhe, "avata"):
            aj.lue_syote(polku)


class AjoTesti(Kansiot):
    def aja(self, komento=None, **kw):
        return aj.aja_seuraava(self.perus, MALLI, komento=komento or lapsi_koodi(VALMIS_TULOS), **kw)

    def test_tyhja_jono(self):
        self.assertIsNone(self.aja())

    def test_tulostiedoston_muoto(self):
        self.jonoon(T1, {"versio": 1, "teksti": "abcd", "keskustelu": "k1",
                         "liitteet": [{"nimi": "aineisto/a.md", "sisalto": "rivi\n"}]})
        self.assertEqual(self.aja()["tila"], am.VALMIS)
        polku = self.p(aj.TULOKSET, T1 + ".json")
        self.assertEqual(stat.S_IMODE(os.lstat(polku).st_mode), 0o640)
        t = self.tulos(T1)
        self.assertEqual(list(t), list(aj.KENTAT))
        self.assertEqual((t["versio"], t["tunnus"], t["keskustelu"], t["tila"]), (1, T1, "k1", "valmis"))
        self.assertEqual(t["raportti"], "pituus 4")
        self.assertEqual(t["tiedostot"], [{"polku": "aineisto/a.md", "koko": 5}])
        self.assertEqual((t["kutsuja"], t["kulu_eur"], t["rajat"]), (4, 0.0012, am.RAJAT))
        self.assertTrue(t["valmistui"].endswith("+00:00"))
        self.assertIsInstance(t["kesto_s"], float)
        # ei valiaikaistiedostoja; tiedostokopio chatin nakymaa varten (osa 4)
        self.assertEqual(sorted(os.listdir(self.p(aj.TULOKSET))), [T1, T1 + ".json"])

    def test_tiedostokopio_ryhmalle_luettava_ja_tyokansio_ennallaan(self):
        self.jonoon(T1, {"versio": 1, "teksti": "abcd",
                         "liitteet": [{"nimi": "aineisto/a.md", "sisalto": "rivi\n"}, {"nimi": "b.txt", "sisalto": "x"}]})
        self.assertEqual(self.aja()["tila"], am.VALMIS)
        k = self.p(aj.TULOKSET, T1)
        ryhma = os.lstat(self.p(aj.TULOKSET)).st_gid
        for kansio in (k, os.path.join(k, "aineisto")):
            st = os.lstat(kansio)
            self.assertEqual(stat.S_IMODE(st.st_mode) & 0o777, 0o750)
            self.assertEqual(st.st_gid, ryhma)             # ryhma periytyy tulokset/-kansiosta (chat lukee)
            if sys.platform.startswith("linux"):
                self.assertTrue(st.st_mode & stat.S_ISGID)  # Linux: setgid periytyy ilman chmodia
        for osa, sisalto in (("aineisto/a.md", "rivi\n"), ("b.txt", "x")):
            p = os.path.join(k, osa)
            self.assertEqual(stat.S_IMODE(os.lstat(p).st_mode), 0o640)
            with open(p, encoding="utf-8") as f:
                self.assertEqual(f.read(), sisalto)
        self.assertEqual(stat.S_IMODE(os.lstat(self.p(aj.TYO, T1)).st_mode), 0o700)

    def test_tiedostokopio_ei_seuraa_linkkeja(self):
        juuri = self.p(aj.TYO, T1)
        os.mkdir(juuri, 0o700)
        salainen = os.path.join(self._t.name, "salainen.md")
        with open(salainen, "w") as f:
            f.write("SALAINEN")
        os.symlink(salainen, os.path.join(juuri, "linkki.md"))
        with open(os.path.join(juuri, "oikea.md"), "w") as f:
            f.write("ok")
        kopioidut = aj.kopioi_tiedostot(self.perus, T1, juuri, [{"polku": "linkki.md", "koko": 8},
                                                                 {"polku": "oikea.md", "koko": 2}])
        self.assertEqual(kopioidut, ["oikea.md"])
        self.assertEqual(os.listdir(self.p(aj.TULOKSET, T1)), ["oikea.md"])

    def test_tulos_kirjoitetaan_vaikka_kopio_epaonnistuu(self):
        self.jonoon(T1)
        with mock.patch.object(aj, "kopioi_tiedostot", side_effect=PermissionError("x")):
            self.assertEqual(self.aja()["tila"], am.VALMIS)
        self.assertEqual(self.tulos(T1)["tila"], am.VALMIS)

    def test_pyynto_siirretaan_kasiteltyihin_ja_tyokansio_luodaan(self):
        self.jonoon(T1)
        self.aja()
        self.assertEqual(os.listdir(self.p(aj.JONO)), [])
        self.assertTrue(os.path.isfile(self.p(aj.KASITELLYT, T1 + ".json")))
        self.assertEqual(stat.S_IMODE(os.lstat(self.p(aj.TYO, T1)).st_mode), 0o700)

    def test_yksi_tehtava_kerrallaan_vanhin_ensin(self):
        self.jonoon(T1, aika=2000)
        self.jonoon(T2, aika=1000)
        self.assertEqual(self.aja()["tunnus"], T2)
        self.assertEqual(aj.jonossa(self.perus), [(T1, T1 + ".json")])
        self.assertFalse(os.path.exists(self.p(aj.TULOKSET, T1 + ".json")))
        self.assertEqual(self.aja()["tunnus"], T1)

    def test_toinen_ajuri_ei_kaynnisty(self):
        lukko = aj.Lukko(self.perus)
        self.addCleanup(lukko.vapauta)
        with self.assertRaises(aj.LukkoVarattu):
            aj.Lukko(self.perus)
        with mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch.object(am.openrouter_kulut, "OLETUS_TILA", self.kulutila()):
            self.assertEqual(aj.main(["palvelu", "--perus", self.perus, "--roolit", MALLI]), 3)

    def test_aikaraja_lopettaa_aliprosessin(self):
        self.jonoon(T1)
        alku = time.monotonic()
        t = self.aja([sys.executable, "-c", "import time; time.sleep(30)"], aikaraja_s=0.5)
        self.assertLess(time.monotonic() - alku, 15)
        self.assertEqual((t["tila"], t["syy"]), (am.KESKEYTETTY, aj.SYY_AIKARAJA))
        self.assertTrue(self.tulos(T1)["kulu_tuntematon"])

    def test_aikarajaa_ei_voi_kasvattaa(self):
        self.assertEqual(aj.PROSESSIN_AIKARAJA_S, am.AIKARAJA_S + 60)
        self.jonoon(T1)
        with mock.patch.object(aj.subprocess, "run", side_effect=aj.subprocess.TimeoutExpired("x", 1)) as run:
            self.aja(aikaraja_s=10 ** 9)
        self.assertEqual(run.call_args.kwargs["timeout"], aj.PROSESSIN_AIKARAJA_S)

    def test_aliprosessin_virheet(self):
        for nimi, komento in (("koodi", [sys.executable, "-c", "import sys; sys.exit(4)"]),
                              ("roska", [sys.executable, "-c", "print('ei jsonia')"]),
                              ("tila", lapsi_koodi(dict(VALMIS_TULOS, tila="ok")))):
            with self.subTest(nimi):
                for f in os.listdir(self.p(aj.JONO)):
                    os.unlink(self.p(aj.JONO, f))
                tunnus = "%012x" % (len(nimi) * 7919)
                self.jonoon(tunnus)
                t = self.aja(komento)
                self.assertEqual(t["tila"], am.VIRHE)
                self.assertEqual(self.tulos(tunnus)["raportti"], "")

    def test_kelvoton_pyynto_ei_kaynnista_agenttia(self):
        merkki = os.path.join(self._t.name, "ajettu")
        self.jonoon(T1, {"versio": 1, "teksti": "t", "liitteet": [{"nimi": "../x.md", "sisalto": "x"}]})
        t = self.aja([sys.executable, "-c", "open(%r, 'w').close()" % merkki])
        self.assertEqual(t["tila"], am.VIRHE)
        self.assertTrue(t["syy"].startswith("pyynto: liite 1"))
        self.assertFalse(os.path.exists(merkki))
        self.assertTrue(os.path.exists(self.p(aj.TULOKSET, T1 + ".json")))

    def test_kaytettya_tunnusta_ei_ylikirjoiteta(self):
        self.jonoon(T1)
        self.aja()
        vanha = self.tulos(T1)
        self.jonoon(T1, {"versio": 1, "teksti": "toinen"})
        t = self.aja()
        self.assertEqual((t["tila"], t["kirjoitettu"]), (am.VIRHE, False))
        self.assertEqual(self.tulos(T1), vanha)
        self.assertEqual(os.listdir(self.p(aj.JONO)), [])

    def test_suljettu_malli_hylataan_ennen_kasittelya(self):
        self.jonoon(T1)
        with self.assertRaises(am.AgenttiVirhe):
            aj.aja_seuraava(self.perus, "openai/gpt-4o", komento=lapsi_koodi(VALMIS_TULOS))
        self.assertEqual(aj.jonossa(self.perus), [(T1, T1 + ".json")])

    def test_keskeneraiset_saavat_tuloksen(self):
        open(self.p(aj.KASITELLYT, T1 + ".json"), "w").close()
        open(self.p(aj.KASITELLYT, T2 + ".json"), "w").close()
        aj.kirjoita_tulos(self.perus, T2, aj.perustulos(T2, "", am.VALMIS, "", {}))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(aj.palauta_keskeneraiset(self.perus, {}), 1)
        self.assertEqual((self.tulos(T1)["tila"], self.tulos(T1)["syy"]), (am.KESKEYTETTY, aj.SYY_PYSAHTYI))
        self.assertEqual(self.tulos(T2)["tila"], am.VALMIS)

    def test_palvelu_ei_kaynnisty_vaarilla_oikeuksilla(self):
        os.chmod(self.p(aj.TYO), 0o755)
        self.jonoon(T1)
        with mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch.object(aj, "aja_seuraava") as ajo:
            self.assertEqual(aj.palvelu(self.perus, MALLI, kierroksia=1, kulutila=self.kulutila()), 1)
        ajo.assert_not_called()

    def test_palvelu_kierros(self):
        self.jonoon(T1)
        with mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch.object(aj, "lapsikomento", return_value=lapsi_koodi(VALMIS_TULOS)):
            self.assertEqual(aj.palvelu(self.perus, MALLI, odotus_s=0, kierroksia=2, kulutila=self.kulutila()), 0)
        self.assertEqual(self.tulos(T1)["tila"], am.VALMIS)

    def test_palvelu_ei_kaynnisty_ilman_kulukirjanpitoa(self):
        kt = self.kulutila()
        os.unlink(os.path.join(kt, "openrouter-katto.jsonl"))
        self.jonoon(T1)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as ulos, \
                mock.patch.object(aj, "aja_seuraava") as ajo:
            self.assertEqual(aj.palvelu(self.perus, MALLI, kierroksia=1, kulutila=kt), 1)
        ajo.assert_not_called()
        self.assertIn("kulukirjanpito", ulos.getvalue())

    def test_kulukirjanpidon_tarkistus(self):
        kt = self.kulutila()
        self.assertEqual(aj.tarkista_kulukirjanpito(kt), [])
        self.assertEqual(len(aj.tarkista_kulukirjanpito(os.path.join(kt, "ei-ole"))), 1)
        os.chmod(kt, 0o500)
        try:
            if os.access(kt, os.W_OK):
                self.skipTest("ajetaan rootina")
            self.assertIn("kansioon", aj.tarkista_kulukirjanpito(kt)[0])
        finally:
            os.chmod(kt, 0o700)
        os.unlink(os.path.join(kt, "openrouter-kulut.lock"))
        os.symlink(os.path.join(kt, "openrouter-kulut.jsonl"), os.path.join(kt, "openrouter-kulut.lock"))
        self.assertEqual(len(aj.tarkista_kulukirjanpito(kt)), 1)

    def test_kulukirjanpito_ei_aukea_kirjoitettavaksi(self):
        # 25.11.2026: os.access sanoi kylla, mutta O_CREAT-avaus esti (fs.protected_regular sticky-kansiossa)
        kt = self.kulutila()
        alkuperainen = os.open

        def esta(polku, liput, *a):
            if str(polku).endswith("openrouter-kulut.lock") and liput & os.O_CREAT:
                raise PermissionError(13, "Permission denied")
            return alkuperainen(polku, liput, *a)
        with mock.patch.object(aj.os, "open", side_effect=esta):
            virheet = aj.tarkista_kulukirjanpito(kt)
        self.assertEqual(len(virheet), 1)
        self.assertIn("ei aukea kirjoitettavaksi (PermissionError)", virheet[0])

    def test_tiedostokopio_ei_kayta_setgid_chmodia(self):
        # yksikon RestrictSUIDSGID=yes estaa setuid/setgid-bitin asettamisen (EPERM)
        self.jonoon(T1, {"versio": 1, "teksti": "abcd", "liitteet": [{"nimi": "aineisto/a.md", "sisalto": "rivi\n"}]})
        alkuperainen = os.chmod

        def suoja(polku, tila, *a, **k):
            if tila & (stat.S_ISUID | stat.S_ISGID):
                raise PermissionError(1, "Operation not permitted")
            return alkuperainen(polku, tila, *a, **k)
        with mock.patch.object(aj.os, "chmod", side_effect=suoja), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as ulos:
            self.assertEqual(self.aja()["tila"], am.VALMIS)
        self.assertNotIn("tiedostojen kopio epaonnistui", ulos.getvalue())
        self.assertTrue(os.path.isfile(self.p(aj.TULOKSET, T1, "aineisto", "a.md")))

    def test_omat_kulutiedostot_hylataan(self):
        kt = self.kulutila()
        self._euid_estin.stop()
        self._euid_estin = None
        self.assertEqual(len(aj.tarkista_kulukirjanpito(kt)), 3)

    def test_syvasti_sisakkainen_json_on_syotevirhe(self):
        polku = self.jonoon(T1, "[" * 200000 + "]" * 200000)
        with self.assertRaises(aj.SyoteVirhe):
            aj.lue_syote(polku)

    def test_fifo_ei_jumita(self):
        polku = self.p(aj.JONO, T1 + ".json")
        os.mkfifo(polku)
        with self.assertRaises(aj.SyoteVirhe):
            aj.lue_syote(polku)

    def test_kadonnut_pyynto_ohitetaan(self):
        self.jonoon(T1)
        alkuperainen = aj.jonossa
        with mock.patch.object(aj, "jonossa", side_effect=lambda p: [x for x in alkuperainen(p)
                                                                       if not os.unlink(self.p(aj.JONO, x[1]))]):
            t = self.aja()
        self.assertEqual((t["tila"], t["kirjoitettu"]), (am.VIRHE, False))

    def test_loki_ei_sisalla_tehtavan_tekstia(self):
        self.jonoon(T1, {"versio": 1, "teksti": "SALAINEN-AIHE", "liitteet": [{"nimi": "a.md", "sisalto": "S2"}]})
        with mock.patch("sys.stdout", new_callable=io.StringIO) as ulos:
            self.aja(lapsi_koodi(dict(VALMIS_TULOS, raportti="SALAINEN-AIHE ")))
        self.assertIn("tunnus=" + T1, ulos.getvalue())
        self.assertNotIn("SALAINEN", ulos.getvalue())
        self.assertNotIn("S2", ulos.getvalue())

    def test_oikea_aliprosessi_ilman_avainta(self):
        """Koko reitti `python3 -m aivo.agentti_ajuri lapsi`: avain puuttuu -> virhe ennen mallikutsua."""
        if os.path.lexists(am.OLETUS_AVAIN):
            self.skipTest("palvelimella on oikea avaintiedosto")
        self.jonoon(T1)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            t = aj.aja_seuraava(self.perus, MALLI)
        self.assertEqual(t["tila"], am.VIRHE)
        self.assertIn("avain", t["syy"])
        self.assertEqual(t["kutsuja"], 0)
        self.assertEqual(t["mallit"], {r: MALLI for r in am.ROOLIT})


class LapsiTesti(Pohja):
    def test_lapsi_ajaa_agentin_korvikemallilla(self):
        syote = io.StringIO(json.dumps({"teksti": "Kirjoita tiedosto tulos.md."}))
        d = aj.lapsi(str(self.tyo), MALLI, syote=syote, avain_tiedosto=str(self.avain), kulutila=str(self.kulut),
                     avaa=self.avaa, nyt=lambda: NYT,
                     kello=lambda: self.aika[0])
        self.assertEqual(d["tila"], am.VALMIS)
        teksti = json.dumps(d)
        self.assertNotIn("sk-testi", teksti)
        self.assertEqual(self.roolit(), ["suunnittelija", "tekija", "tekija", "tarkastaja"])
        self.assertTrue((self.tyo / "tulos.md").exists())

    def test_tyhja_syote(self):
        d = aj.lapsi(str(self.tyo), MALLI, syote=io.StringIO("{}"))
        self.assertEqual(d["tila"], am.VIRHE)
        self.assertEqual(self.rungot, [])


class RakenneTesti(unittest.TestCase):
    def test_tuonnit(self):
        puu = ast.parse((JUURI / "aivo" / "agentti_ajuri.py").read_text(encoding="utf-8"))
        for n in ast.walk(puu):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.assertNotIn(a.name.split(".")[0], {"urllib", "socket", "http", "ssl", "requests", "shutil",
                                                            "ctypes"}, a.name)
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    # vain agenttiydin; verkko kulkee ainoastaan malli-moduulin kautta
                    self.assertEqual((n.level, n.module), (1, "agentti"))
                    self.assertLessEqual({a.name for a in n.names}, {"malli", "polut", "tyokalut"})
                else:
                    self.assertNotIn(n.module.split(".")[0], {"urllib", "socket", "http", "ssl", "aivo"}, n.module)
            elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "os":
                self.assertNotIn(n.attr, {"remove", "rmdir", "removedirs", "system", "popen"}, n.attr)

    def test_ei_uusia_kulu_tai_askelrajoja(self):
        """A1072: ajuri ei maarittele omia katto-, budjetti-, osuus- tai askelrajoja (vain agenttiytimen vakiot)."""
        puu = ast.parse((JUURI / "aivo" / "agentti_ajuri.py").read_text(encoding="utf-8"))
        nimet = {t.id.upper() for n in puu.body if isinstance(n, ast.Assign) for t in n.targets
                 if isinstance(t, ast.Name)}
        for sana in ("KATTO", "BUDJETTI", "OSUUS", "ASKEL", "EUR", "USD"):
            self.assertEqual([n for n in nimet if sana in n], [], sana)


class YksikkoTesti(unittest.TestCase):
    def setUp(self):
        self.rivit = [r.strip() for r in YKSIKKO.read_text(encoding="ascii").splitlines()]

    def test_kayttaja_ja_komento(self):
        self.assertIn("User=aivo-agentti", self.rivit)
        self.assertIn("SupplementaryGroups=aivo-agentit aivo-kulut", self.rivit)
        exec_rivi = [r for r in self.rivit if r.startswith("ExecStart=")]
        self.assertEqual(len(exec_rivi), 1)
        self.assertIn("-m aivo.agentti_ajuri palvelu --perus /var/lib/aivo-agentit --roolit ", exec_rivi[0])
        malli = exec_rivi[0].rsplit(" ", 1)[1]
        self.assertTrue(am.tarkista_roolit(malli))

    def test_kovennus(self):
        for rivi in ("NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes",
                     "PrivateDevices=yes", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
                     "ProtectControlGroups=yes", "RestrictSUIDSGID=yes", "LockPersonality=yes",
                     "MemoryDenyWriteExecute=yes", "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX",
                     "SystemCallFilter=@system-service", "SystemCallFilter=~@privileged", "CapabilityBoundingSet=",
                     "UMask=0077", "MemoryMax=512M", "CPUQuota=50%", "TasksMax=32"):
            self.assertIn(rivi, self.rivit)

    def arvot(self, avain):
        return [r.split("=", 1)[1] for r in self.rivit if r.startswith(avain + "=")]

    def test_kirjoitus_vain_omiin_kansioihin_ja_kulutiedostoihin(self):
        from aivo import openrouter_kulut as ok
        rw = " ".join(self.arvot("ReadWritePaths")).split()
        self.assertEqual(rw, ["/var/lib/aivo-agentit"])
        # kulukansio korvataan palvelun omalla tmpfs:lla (openrouter_kulut vaatii kirjoitettavan kansion), ei ro
        tfs = self.arvot("TemporaryFileSystem")
        self.assertEqual(len(tfs), 1)
        polku, _, valinnat = tfs[0].partition(":")
        self.assertEqual(polku, ok.OLETUS_TILA)
        self.assertNotIn("ro", valinnat.split(","))
        # ei sticky-bittia: fs.protected_regular estaisi O_CREAT-avauksen chatin omistamiin kulutiedostoihin
        tilat = [v.split("=", 1)[1] for v in valinnat.split(",") if v.startswith("mode=")]
        self.assertEqual(tilat, ["0777"])
        # oikeasta kansiosta liitetaan vain kolme kulutiedostoa samaan paikkaan (ei kansioita, ei muita tiedostoja)
        sidotut = " ".join(self.arvot("BindPaths")).split()
        self.assertEqual(sorted(sidotut), sorted(os.path.join(ok.OLETUS_TILA, n)
                                                 for n in (ok.KULUT, ok.TAPAHTUMAT, ok.LUKKO)))
        self.assertEqual(self.arvot("BindReadOnlyPaths"), [])
        esto = [r for r in self.rivit if r.startswith("InaccessiblePaths=")][0]
        for polku in ("/var/lib/aivo-yksityinen", "/var/lib/aivo", "/var/lib/aivo-tyokalu"):
            self.assertIn("-" + polku, esto.split("=", 1)[1].split())

    def test_ohjeen_ennakkotarkistus_kayttaa_samoja_liitoksia(self):
        ohje = (JUURI / "docs" / "aivo-agentit.md").read_text(encoding="utf-8")
        for avain in ("TemporaryFileSystem", "BindPaths"):
            self.assertIn('-p "%s=%s"' % (avain, self.arvot(avain)[0]), ohje, avain)
        self.assertNotIn("chmod g+rwx", ohje)
        self.assertNotIn("sudo ", ohje)


if __name__ == "__main__":
    unittest.main()
