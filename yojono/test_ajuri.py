import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yojono import ajuri

PVM = "2026-10-19"


def setUpModule():
    # Tuotannossa YOJONO_LOKI on asetettu (kehitys-testit-ajo perii sen): ilman tata testit kirjoittivat feikkitapahtumia (repo "r") oikeaan
    # tapahtumalokiin ja vaaristivat mittarit (havainto 29.10.). Testit, jotka tarvitsevat lokin, asettavat polun itse (mock.patch.dict).
    patcher = mock.patch.dict(os.environ)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    os.environ.pop("YOJONO_LOKI", None)


def jono(tehtavat, katto=3.0):
    return {"paivakatto": katto, "tehtavat": tehtavat}


def t(id_, repo="r", budjetti=1.0):
    return {"id": id_, "tyyppi": "katselmointi", "repo": repo, "budjetti": budjetti}


class Pohja(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.perus = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def kirjoita_jono(self, data):
        (self.perus / "jono.json").write_text(json.dumps(data))

    def tila(self):
        return json.loads((self.perus / "tila.json").read_text())

    def aja(self, suorita, **kw):
        viestit = []
        teksti = ajuri.aja(self.perus, "/ei/kaytossa", laheta=viestit.append, suorita=suorita, pvm=PVM,
                           telegram=kw.pop("telegram", lambda x: None), **kw)
        return teksti, viestit


class TestTarkistus(unittest.TestCase):
    def test_hyvaksyy_kelvollisen(self):
        katto, tehtavat = ajuri.tarkista_jono(jono([t("a-1")]))
        self.assertEqual(katto, 3.0)
        self.assertEqual(len(tehtavat), 1)

    def test_hylkaa_virheelliset(self):
        huonot = [
            [],  # ei objekti
            {"tehtavat": "x"},
            jono([t("A")]),  # iso kirjain id:ssa
            jono([t("a", repo="../x")]),
            jono([t("a", repo="a b")]),
            jono([t("a", repo="..")]),
            jono([t("a", budjetti=0)]),
            jono([t("a", budjetti=-1)]),
            jono([t("a", budjetti=3.5)]),  # yli paivakaton
            jono([t("a", budjetti=True)]),
            jono([t("a"), t("a")]),  # sama id kahdesti
            jono([dict(t("a"), tyyppi="shell")]),
            jono([t("a")], katto=0),
            jono([t("a")], katto=999),
            jono([t("a")], katto=True),
        ]
        for huono in huonot:
            with self.subTest(huono=huono):
                with self.assertRaises(ajuri.JonoVirhe):
                    ajuri.tarkista_jono(huono)


class TestValinta(unittest.TestCase):
    def test_ohittaa_valmiit_ja_rajaa_uudelleenyritykset(self):
        tehtavat = [t("uusi"), t("valmis"), t("kaatui-tanaan"), t("kaatui-eilen"), t("kaatui-kahdesti")]
        tila = {"tehtavat": {
            "valmis": {"tila": "valmis"},
            "kaatui-tanaan": {"tila": "epaonnistui", "yrityksia": 1, "pvm": PVM},
            "kaatui-eilen": {"tila": "epaonnistui", "yrityksia": 1, "pvm": "2026-10-18"},
            "kaatui-kahdesti": {"tila": "epaonnistui", "yrityksia": 2, "pvm": "2026-10-17"},
        }}
        ids = [x["id"] for x in ajuri.valitse_ajettavat(tehtavat, tila, PVM)]
        self.assertEqual(ids, ["uusi", "kaatui-eilen"])


class TestAjo(Pohja):
    def test_toistuva_tehtava_ajetaan_taas_kun_paivia_kulunut(self):
        tehtavat = [{"id": "a", "tyyppi": "ideointi", "repo": "r", "budjetti": 1.0, "toisto_pv": 7}]
        tila = {"tehtavat": {"a": {"tila": "valmis", "yrityksia": 0, "pvm": "2026-10-12"}}}
        self.assertEqual(ajuri.valitse_ajettavat(tehtavat, tila, "2026-10-18"), [])
        self.assertEqual([t["id"] for t in ajuri.valitse_ajettavat(tehtavat, tila, "2026-10-19")], ["a"])

    def test_valmis_ilman_toistoa_ei_ajeta_koskaan_uudelleen(self):
        tehtavat = [{"id": "a", "tyyppi": "katselmointi", "repo": "r", "budjetti": 1.0}]
        tila = {"tehtavat": {"a": {"tila": "valmis", "pvm": "2020-01-01"}}}
        self.assertEqual(ajuri.valitse_ajettavat(tehtavat, tila, "2026-10-21"), [])

    def test_toisto_ilman_kelvollista_paivaysta_ei_toista(self):
        tehtavat = [{"id": "a", "tyyppi": "ideointi", "repo": "r", "budjetti": 1.0, "toisto_pv": 1}]
        for pvm in (None, "roskaa"):
            tila = {"tehtavat": {"a": {"tila": "valmis", "pvm": pvm}}}
            self.assertEqual(ajuri.valitse_ajettavat(tehtavat, tila, "2026-10-21"), [])

    def test_toisto_pv_validointi(self):
        for huono in (0, -1, 366, 1.5, True, "7"):
            jono = {"tehtavat": [{"id": "a", "tyyppi": "ideointi", "repo": "r", "budjetti": 1.0, "toisto_pv": huono}]}
            with self.assertRaises(ajuri.JonoVirhe):
                ajuri.tarkista_jono(jono)
        ajuri.tarkista_jono({"tehtavat": [{"id": "a", "tyyppi": "ideointi", "repo": "r", "budjetti": 1.0, "toisto_pv": 7}]})

    def test_skripti_valitaan_tyypin_mukaan(self):
        sk = {"katselmointi": "/k.sh", "ideointi": "/i.sh"}
        self.assertEqual(ajuri.skripti_tyypille(sk, "ideointi"), "/i.sh")
        self.assertEqual(ajuri.skripti_tyypille("/vanha.sh", "katselmointi"), "/vanha.sh")
        self.assertEqual(ajuri.skripti_tyypille("/vanha.sh", "ideointi"), "")

    def test_ajaa_ideointitehtavan_oikealla_skriptilla(self):
        (self.perus / "jono.json").write_text(json.dumps({"paivakatto": 3.0, "tehtavat": [
            {"id": "i", "tyyppi": "ideointi", "repo": "agenttifirma", "budjetti": 1.0, "toisto_pv": 7}]}), encoding="utf-8")
        kutsut = []

        def suorita(t, pvm, perus, skripti):
            kutsut.append(skripti)
            return ajuri.Tulos(True, 0.2, "x.md", "ok")
        viestit = []
        ajuri.aja(self.perus, {"ideointi": "/i.sh"}, laheta=viestit.append, suorita=suorita, pvm=PVM,
                  telegram=lambda *_: None)
        self.assertEqual(kutsut, ["/i.sh"])

    def test_ajaa_tehtavat_ja_kirjaa_kulut(self):
        self.kirjoita_jono(jono([t("a", budjetti=1.5), t("b", budjetti=1.0)]))
        kutsut = []

        def suorita(tehtava, pvm, perus, skripti):
            kutsut.append(tehtava["id"])
            return ajuri.Tulos(True, 0.6, f"/r/{tehtava['id']}.md", "ok")

        teksti, viestit = self.aja(suorita)
        self.assertEqual(kutsut, ["a", "b"])
        tila = self.tila()
        self.assertAlmostEqual(tila["kulut"][PVM], 1.2)
        self.assertEqual(tila["tehtavat"]["a"]["tila"], "valmis")
        self.assertIn("kulu tanaan 1.20 / 3.00 USD", teksti)
        self.assertEqual(viestit, [teksti])

    def test_paivakatto_ohittaa_liian_ison_tehtavan_mutta_ajaa_pienemman(self):
        self.kirjoita_jono(jono([t("iso", budjetti=2.0), t("pieni", budjetti=0.5)]))
        (self.perus / "tila.json").write_text(json.dumps({"kulut": {PVM: 1.8}, "tehtavat": {}}))
        kutsut = []

        def suorita(tehtava, *a):
            kutsut.append(tehtava["id"])
            return ajuri.Tulos(True, 0.4, "x.md", "ok")

        teksti, _ = self.aja(suorita)
        self.assertEqual(kutsut, ["pieni"])
        self.assertIn("iso: ohitettu (paivakatto", teksti)

    def test_kulu_ei_ylita_kattoa_useassa_tehtavassa(self):
        self.kirjoita_jono(jono([t(f"t{i}", budjetti=1.0) for i in range(6)], katto=3.0))
        kutsut = []

        def suorita(tehtava, *a):
            kutsut.append(tehtava["id"])
            return ajuri.Tulos(True, 1.0, "x.md", "ok")

        self.aja(suorita)
        # varaus 1,30/tehtava: 0 -> 1 -> 2 kulua, kolmas tarvitsisi 1,30 > 1,00 jaljella -> kaksi ajoa
        self.assertEqual(len(kutsut), 2)
        self.assertLessEqual(self.tila()["kulut"][PVM], 3.0)

    def test_varaus_kattaa_budjetin_ylityksen(self):
        """28.10.: katselmointi maksoi 1,86 / 1,50 USD ja vei paivakulun 5,32 / 5,00. Varauksella ylitys mahtuu kattoon."""
        self.kirjoita_jono(jono([t("a", budjetti=1.5), t("b", budjetti=1.5), t("c", budjetti=1.5)], katto=5.0))
        self.aja(lambda *_: ajuri.Tulos(True, 1.86, "x.md", "ok"))
        self.assertLessEqual(self.tila()["kulut"][PVM], 5.0)
        self.assertEqual(ajuri.varattava(1.5), 1.95)

    def test_varauskerroin_ei_alle_laissa_sovitun(self):
        self.assertGreaterEqual(ajuri.VARAUSKERROIN, 1.3)

    def test_epaonnistunut_kirjataan_ja_kulu_lasketaan(self):
        self.kirjoita_jono(jono([t("a", budjetti=1.5)]))
        teksti, _ = self.aja(lambda *a: ajuri.Tulos(False, 1.15, "", "budjetti loppui"))
        s = self.tila()
        self.assertEqual(s["tehtavat"]["a"]["tila"], "epaonnistui")
        self.assertEqual(s["tehtavat"]["a"]["yrityksia"], 1)
        self.assertAlmostEqual(s["kulut"][PVM], 1.15)
        self.assertIn("EPAONNISTUI", teksti)
        self.assertIn("budjetti loppui", teksti)

    def test_tila_tallentuu_heti_vaikka_seuraava_tehtava_kaatuu(self):
        self.kirjoita_jono(jono([t("a"), t("b")]))
        n = {"i": 0}

        def suorita(tehtava, *a):
            n["i"] += 1
            if n["i"] == 2:
                raise RuntimeError("yllatys")
            return ajuri.Tulos(True, 0.7, "x.md", "ok")

        with self.assertRaises(RuntimeError):
            self.aja(suorita)
        self.assertAlmostEqual(self.tila()["kulut"][PVM], 0.7)  # ensimmaisen kulu ei hukkaantunut

    def test_kuiva_ajo_ei_kutsu_eika_kirjoita_tilaa(self):
        self.kirjoita_jono(jono([t("a")]))
        kutsut = []
        teksti, viestit = self.aja(lambda *a: kutsut.append(1), kuiva=True)
        self.assertEqual(kutsut, [])
        self.assertFalse((self.perus / "tila.json").exists())
        self.assertIn("- a: r (budjetti 1.00)", teksti)
        self.assertEqual(viestit, [])

    def test_ilta_aikaraja_ohittaa_loput(self):
        self.kirjoita_jono(jono([t("a"), t("b")]))
        kellot = iter([0, 0, ajuri.ILTA_AIKARAJA_S + 1])  # alku, tarkistus ennen a:ta, tarkistus ennen b:ta
        kutsut = []

        def suorita(tehtava, *a):
            kutsut.append(tehtava["id"])
            return ajuri.Tulos(True, 0.1, "x.md", "ok")

        teksti, _ = self.aja(suorita, nyt=lambda: next(kellot))
        self.assertEqual(kutsut, ["a"])
        self.assertIn("b: ohitettu (yon aikaraja)", teksti)

    def test_lukko_estaa_rinnakkaisajon(self):
        import fcntl
        self.kirjoita_jono(jono([t("a")]))
        with open(self.perus / "lukko", "w") as lukko:
            fcntl.flock(lukko, fcntl.LOCK_EX | fcntl.LOCK_NB)
            kutsut = []
            teksti, _ = self.aja(lambda *a: kutsut.append(1))
        self.assertEqual(kutsut, [])
        self.assertIn("Toinen ajo on kaynnissa", teksti)

    def test_virheellinen_jono_nostaa_jonovirheen_eika_aja_mitaan(self):
        self.kirjoita_jono(jono([t("a", repo="../x")]))
        kutsut = []
        with self.assertRaises(ajuri.JonoVirhe):
            self.aja(lambda *a: kutsut.append(1))
        self.assertEqual(kutsut, [])

    def test_tyhja_jono(self):
        teksti, _ = self.aja(lambda *a: None)
        self.assertIn("Ei ajettavia tehtavia", teksti)

    def test_telegram_kutsutaan_vain_kun_ajettiin_jotain(self):
        self.kirjoita_jono(jono([t("a")]))
        lahetetyt = []
        self.aja(lambda *a: ajuri.Tulos(True, 0.1, "x.md", "ok"), telegram=lahetetyt.append)
        self.assertEqual(len(lahetetyt), 1)
        lahetetyt.clear()
        self.aja(lambda *a: None, telegram=lahetetyt.append)  # "a" on jo valmis
        self.assertEqual(lahetetyt, [])


class TestKatselmointiSkripti(Pohja):
    """Oikea alaprosessi: tekoskripti, joka toimii kuten katselmoi.sh."""

    def skripti(self, runko):
        polku = self.perus / "katselmoi.sh"
        polku.write_text("#!/bin/sh\n" + runko)
        polku.chmod(polku.stat().st_mode | stat.S_IXUSR)
        (self.perus / "raportit").mkdir(exist_ok=True)
        return polku

    def test_onnistunut_ajo_lukee_kulun_json_ista_ja_saa_budjetin_argumenttina(self):
        s = self.skripti(f'echo "$1 $2" > {self.perus}/argumentit\n'
                         f'echo \'{{"total_cost_usd": 0.58, "result": "x"}}\' > "{self.perus}/raportit/{PVM}-$1.json"\n'
                         f'yes raportti | head -n 40 > "{self.perus}/raportit/{PVM}-$1.md"\n')
        tulos = ajuri.suorita_katselmointi(t("a", repo="mcp-homelab", budjetti=2.0), PVM, self.perus, s)
        self.assertTrue(tulos.ok)
        self.assertAlmostEqual(tulos.kulu, 0.58)
        self.assertEqual((self.perus / "argumentit").read_text().strip(), "mcp-homelab 2.00")
        self.assertTrue(tulos.raportti.endswith(f"{PVM}-mcp-homelab.md"))

    def test_ideointi_raportti_on_nimetty_tyypin_mukaan(self):
        s = self.skripti(f'echo \'{{"total_cost_usd": 0.3}}\' > "{self.perus}/raportit/{PVM}-ideointi-$1.json"\n'
                         f'yes raportti | head -n 40 > "{self.perus}/raportit/{PVM}-ideointi-$1.md"\n')
        tehtava = dict(t("i", repo="agenttifirma", budjetti=1.0), tyyppi="ideointi")
        tulos = ajuri.suorita_katselmointi(tehtava, PVM, self.perus, s)
        self.assertTrue(tulos.ok)
        self.assertTrue(tulos.raportti.endswith(f"{PVM}-ideointi-agenttifirma.md"))

    def test_budjetin_ylitys_json_ssa_mutta_ei_raporttia_on_epaonnistuminen_ja_kulu_tiedossa(self):
        s = self.skripti(f'echo \'{{"total_cost_usd": 1.15, "subtype": "error_max_budget_usd"}}\' > "{self.perus}/raportit/{PVM}-$1.json"\nexit 1\n')
        tulos = ajuri.suorita_katselmointi(t("a", repo="mcp-model-router", budjetti=1.5), PVM, self.perus, s)
        self.assertFalse(tulos.ok)
        self.assertAlmostEqual(tulos.kulu, 1.15)

    def test_kloonaus_epaonnistuu_ennen_claudea_kulu_nolla(self):
        s = self.skripti("echo 'fatal: repository not found' >&2\nexit 128\n")
        tulos = ajuri.suorita_katselmointi(t("a", repo="puuttuu"), PVM, self.perus, s)
        self.assertFalse(tulos.ok)
        self.assertEqual(tulos.kulu, 0.0)
        self.assertIn("repository not found", tulos.viesti)

    def test_lukukelvoton_json_laskutetaan_koko_budjetti(self):
        s = self.skripti(f'echo "ei json" > "{self.perus}/raportit/{PVM}-$1.json"\nexit 1\n')
        tulos = ajuri.suorita_katselmointi(t("a", budjetti=1.7), PVM, self.perus, s)
        self.assertFalse(tulos.ok)
        self.assertAlmostEqual(tulos.kulu, 1.7)

    def test_aikakatkaisu_laskutetaan_koko_budjetti(self):
        s = self.skripti("sleep 5\n")
        tulos = ajuri.suorita_katselmointi(t("a", budjetti=1.2), PVM, self.perus, s, aikaraja=0.2)
        self.assertFalse(tulos.ok)
        self.assertAlmostEqual(tulos.kulu, 1.2)
        self.assertIn("aikakatkaisu", tulos.viesti)

    def test_vanha_raportti_ei_nay_onnistumisena(self):
        s = self.skripti("exit 1\n")
        (self.perus / "raportit" / f"{PVM}-a.json").write_text('{"total_cost_usd": 9}')
        (self.perus / "raportit" / f"{PVM}-a.md").write_text("vanha")
        tulos = ajuri.suorita_katselmointi(t("a", repo="a"), PVM, self.perus, s)
        self.assertFalse(tulos.ok)
        self.assertEqual(tulos.kulu, 0.0)

    def test_poistumiskoodi_nolla_ilman_raporttia_ei_ole_onnistuminen(self):
        s = self.skripti("exit 0\n")
        tulos = ajuri.suorita_katselmointi(t("a", repo="a"), PVM, self.perus, s)
        self.assertFalse(tulos.ok)

    def test_json_paivittyi_mutta_md_puuttuu_ei_ole_onnistuminen(self):
        s = self.skripti(f'echo \'{{"total_cost_usd": 0.3}}\' > "{self.perus}/raportit/{PVM}-$1.json"\n')
        tulos = ajuri.suorita_katselmointi(t("a", repo="a"), PVM, self.perus, s)
        self.assertFalse(tulos.ok)
        self.assertAlmostEqual(tulos.kulu, 0.3)

    def test_totuusarvo_kulukentassa_ei_kelpaa_kuluksi(self):
        s = self.skripti(f'echo \'{{"total_cost_usd": true}}\' > "{self.perus}/raportit/{PVM}-$1.json"\nexit 1\n')
        tulos = ajuri.suorita_katselmointi(t("a", repo="a", budjetti=1.4), PVM, self.perus, s)
        self.assertAlmostEqual(tulos.kulu, 1.4)  # luetaan lukukelvottomaksi, ei 1.0:ksi

    def test_telegram_tunnukset_eivat_kulkeudu_alaprosessille(self):
        s = self.skripti(f'echo "$YOJONO_TELEGRAM_TOKEN|$YOJONO_TELEGRAM_CHAT|$MUU" > {self.perus}/ymparisto\nexit 1\n')
        with mock.patch.dict(os.environ, {"YOJONO_TELEGRAM_TOKEN": "salainen", "YOJONO_TELEGRAM_CHAT": "42", "MUU": "ok"}):
            ajuri.suorita_katselmointi(t("a", repo="a"), PVM, self.perus, s)
        self.assertEqual((self.perus / "ymparisto").read_text().strip(), "||ok")

    def test_puuttuva_skripti(self):
        tulos = ajuri.suorita_katselmointi(t("a"), PVM, self.perus, self.perus / "ei-ole.sh")
        self.assertFalse(tulos.ok)
        self.assertEqual(tulos.kulu, 0.0)


class TestKehitys(Pohja):
    def kt(self, **kw):
        return dict({"id": "k", "tyyppi": "kehitys", "repo": "agentti-pohja", "aihe": "readme", "budjetti": 1.0}, **kw)

    def test_kelvollinen(self):
        ajuri.tarkista_jono({"tehtavat": [self.kt()]})

    def test_virheet(self):
        for huono in (self.kt(aihe="muu"), self.kt(aihe=None), self.kt(repo="tuntematon-repo"), self.kt(repo="agenttifirma")):
            with self.assertRaises(ajuri.JonoVirhe):
                ajuri.tarkista_jono({"tehtavat": [huono]})
        with self.assertRaises(ajuri.JonoVirhe):
            ajuri.tarkista_jono({"tehtavat": [{"id": "a", "tyyppi": "katselmointi", "repo": "r", "budjetti": 1.0, "aihe": "readme"}]})

    def test_kuiva_nayttaa_aiheen(self):
        self.kirjoita_jono({"tehtavat": [self.kt()]})
        teksti, _ = self.aja(None, kuiva=True)
        self.assertIn("[kehitys/readme]", teksti)

    def test_skripti_saa_aiheen_ja_raportti_nimetaan_aiheella(self):
        skripti = self.perus / "kehitys.sh"
        skripti.write_text("#!/bin/sh\n"
                           f'echo "$1 $2 $3" > {self.perus}/argumentit\n'
                           f'mkdir -p {self.perus}/raportit\n'
                           f'echo \'{{"total_cost_usd": 0.4}}\' > "{self.perus}/raportit/{PVM}-kehitys-$3-$1.json"\n'
                           f'echo pr > "{self.perus}/raportit/{PVM}-kehitys-$3-$1.md"\n')
        skripti.chmod(skripti.stat().st_mode | stat.S_IXUSR)
        tulos = ajuri.suorita_katselmointi(self.kt(), PVM, self.perus, skripti)
        self.assertTrue(tulos.ok)
        self.assertEqual((self.perus / "argumentit").read_text().strip(), "agentti-pohja 1.00 readme")
        self.assertTrue(tulos.raportti.endswith(f"{PVM}-kehitys-readme-agentti-pohja.md"))

    def test_katselmointi_ei_saa_ylimaaraista_argumenttia(self):
        skripti = self.perus / "k.sh"
        skripti.write_text("#!/bin/sh\n" f'echo "$#" > {self.perus}/n\n')
        skripti.chmod(skripti.stat().st_mode | stat.S_IXUSR)
        (self.perus / "raportit").mkdir(exist_ok=True)
        ajuri.suorita_katselmointi({"id": "a", "tyyppi": "katselmointi", "repo": "r", "budjetti": 1.0}, PVM, self.perus, skripti)
        self.assertEqual((self.perus / "n").read_text().strip(), "2")


class TestOminaisuus(Pohja):
    def ot(self, **kw):
        return dict({"id": "o-1", "tyyppi": "kehitys", "repo": "llm-stopcheck", "aihe": "ominaisuus", "budjetti": 1.0, "teema": "CLI: --version"}, **kw)

    def test_kelvollinen(self):
        ajuri.tarkista_jono({"tehtavat": [self.ot()]})

    def test_teema_vaaditaan_ja_kuuluu_vain_ominaisuudelle(self):
        huonot = [{k: v for k, v in self.ot().items() if k != "teema"}, self.ot(teema=""), self.ot(teema="a\nb"),
                  self.ot(aihe="readme"), self.ot(repo="tuntematon-repo")]
        for huono in huonot:
            with self.subTest(huono=huono):
                with self.assertRaises(ajuri.JonoVirhe):
                    ajuri.tarkista_jono({"tehtavat": [huono]})

    def test_skripti_saa_teeman_tunnuksen_ja_oman_raporttinimen(self):
        skripti = self.perus / "kehitys.sh"
        skripti.write_text("#!/bin/sh\n"
                           f'echo "$#|$3|$YOJONO_TEEMA|$YOJONO_TUNNUS|$YOJONO_RAPORTTI" > {self.perus}/tiedot\n'
                           f'mkdir -p {self.perus}/raportit\n'
                           f'echo \'{{"total_cost_usd": 0.4}}\' > "{self.perus}/raportit/$YOJONO_RAPORTTI.json"\n'
                           f'echo pr > "{self.perus}/raportit/$YOJONO_RAPORTTI.md"\n')
        skripti.chmod(skripti.stat().st_mode | stat.S_IXUSR)
        tulos = ajuri.suorita_katselmointi(self.ot(), PVM, self.perus, skripti)
        self.assertTrue(tulos.ok)
        self.assertEqual((self.perus / "tiedot").read_text().strip(),
                         f"3|ominaisuus|CLI: --version|o-1|{PVM}-kehitys-o-1-llm-stopcheck")
        self.assertTrue(tulos.raportti.endswith(f"{PVM}-kehitys-o-1-llm-stopcheck.md"))


class TestSyyJaPrRaja(Pohja):
    def test_syy_raportista_ja_kohina_pois(self):
        md = self.perus / "r.md"
        md.write_text("# K\n- PR: ei avattu\n- Syy: paivaraja PR-yritysta taynna\n")
        self.assertEqual(ajuri.syy_viestiksi(md, "[claude-code:unrecognized_model]"), "paivaraja PR-yritysta taynna")
        self.assertEqual(ajuri.syy_viestiksi(None, "[claude-code:unrecognized_model]"), "")
        self.assertEqual(ajuri.syy_viestiksi(None, "oikea virhe [claude-code:unrecognized_model]"), "oikea virhe")
        self.assertEqual(ajuri.syy_viestiksi(self.perus / "ei-ole.md", "x"), "x")

    def test_kehitys_ohitetaan_kun_pr_raja_taynna_eika_yritys_kulu(self):
        import json as _j
        import os as _o
        from datetime import date as _d
        from unittest import mock as _m
        from agentti import pr_kutsu as pk0
        tila = self.perus / "pr-tila.json"
        tila.write_text(_j.dumps({"pvm": _d.today().isoformat(), "n": pk0.RAJA_PAIVASSA}))
        self.kirjoita_jono(jono([dict(t("kehitys-1", budjetti=0.5), tyyppi="kehitys", aihe="testit", repo="agentti-pohja"),
                                 t("katselmointi-1", budjetti=0.5)]))
        kutsut = []

        def suorita(tehtava, *a):
            kutsut.append(tehtava["id"])
            return ajuri.Tulos(True, 0.1, "x.md", "ok")

        with _m.patch.dict(_o.environ, {"YOJONO_PR_TILA": str(tila)}):
            teksti, _ = self.aja(suorita)
        self.assertEqual(kutsut, ["katselmointi-1"])
        self.assertIn("kehitys-1: ohitettu (PR-paivaraja taynna", teksti)
        self.assertNotIn("kehitys-1", _j.loads((self.perus / "tila.json").read_text()).get("tehtavat", {}))
        from agentti import pr_kutsu as pk
        self.assertEqual(pk.paikkoja_jaljella(str(tila)), 0)
        tila.write_text(_j.dumps({"pvm": "2000-01-01", "n": 999}))
        self.assertEqual(pk.paikkoja_jaljella(str(tila)), pk.RAJA_PAIVASSA)
        self.assertEqual(pk.paikkoja_jaljella(str(self.perus / "ei-ole.json")), pk.RAJA_PAIVASSA)


class TestPeilaus(Pohja):
    def test_ei_asetettu_ei_tee_mitaan(self):
        self.assertFalse(ajuri.peilaa(None))
        self.assertFalse(ajuri.peilaa(""))

    def test_skripti_ajetaan_ja_virhe_ei_kaada(self):
        ok = self.perus / "ok.sh"
        ok.write_text("#!/bin/sh\necho x > " + str(self.perus / "ajettu") + "\n")
        ok.chmod(ok.stat().st_mode | stat.S_IXUSR)
        self.assertTrue(ajuri.peilaa(str(ok)))
        self.assertTrue((self.perus / "ajettu").exists())
        huono = self.perus / "huono.sh"
        huono.write_text("#!/bin/sh\nexit 3\n")
        huono.chmod(huono.stat().st_mode | stat.S_IXUSR)
        self.assertFalse(ajuri.peilaa(str(huono)))
        self.assertFalse(ajuri.peilaa(str(self.perus / "ei-ole.sh")))


class TestLisatiedot(Pohja):
    def md(self, teksti):
        p = self.perus / "r.md"
        p.write_text(teksti)
        return p

    def test_kehitys_pr_linkki(self):
        self.assertEqual(ajuri.raportin_lisatiedot("kehitys", self.md("# K\n- PR: https://github.com/x/y/pull/3\n- Testit: vihrea\n")),
                         " PR https://github.com/x/y/pull/3")

    def test_kehitys_ei_prta_nayttaa_syyn(self):
        t = ajuri.raportin_lisatiedot("kehitys", self.md("- PR: ei avattu\n- Syy: testit punaisella\n"))
        self.assertEqual(t, " EI PR:AA (testit punaisella)")
        self.assertEqual(ajuri.raportin_lisatiedot("kehitys", self.md("- PR: ei avattu\n")), " EI PR:AA")

    def test_tarkkailu_kolme_parasta(self):
        teksti = "## Ehdotukset\n" + "".join(f"{i}. PARAS ehdotus {i}\n" for i in range(1, 6)) + "6. muu\n"
        t = ajuri.raportin_lisatiedot("tarkkailu", self.md(teksti))
        self.assertEqual(t.count("PARAS"), 3)
        self.assertIn("ehdotus 1", t)
        self.assertNotIn("ehdotus 4", t)

    def test_muut_tyypit_ja_virheet_eivat_kaada(self):
        self.assertEqual(ajuri.raportin_lisatiedot("ideointi", self.md("- PR: x\n")), "")
        self.assertEqual(ajuri.raportin_lisatiedot("kehitys", self.perus / "ei-ole.md"), "")
        self.assertEqual(ajuri.raportin_lisatiedot("tarkkailu", self.md("ei mitaan")), "")


class TestTarkkailu(Pohja):
    def tt(self, **kw):
        return dict({"id": "tark-infra", "tyyppi": "tarkkailu", "repo": "agentti-pohja", "budjetti": 1.0, "teema": "infra proxmox ja litellm"}, **kw)

    def test_kelvolliset_roolit(self):
        for rooli in ajuri.TARKKAILU_ROOLIT:
            ajuri.tarkista_jono({"tehtavat": [self.tt(teema=rooli)]})

    def test_virheelliset(self):
        huonot = [{k: v for k, v in self.tt().items() if k != "teema"}, self.tt(teema="tuntematon"), self.tt(teema=""),
                  self.tt(teema="infra\nx"), self.tt(aihe="readme"), self.tt(repo="../x")]
        for huono in huonot:
            with self.subTest(huono=huono):
                with self.assertRaises(ajuri.JonoVirhe):
                    ajuri.tarkista_jono({"tehtavat": [huono]})

    def test_skripti_saa_teeman_ja_raporttinimen_eika_aihetta(self):
        skripti = self.perus / "tarkkailu.sh"
        skripti.write_text("#!/bin/sh\n"
                           f'echo "$#|$YOJONO_TEEMA|$YOJONO_TUNNUS|$YOJONO_RAPORTTI" > {self.perus}/tiedot\n'
                           f'mkdir -p {self.perus}/raportit\n'
                           f'echo \'{{"total_cost_usd": 0.3}}\' > "{self.perus}/raportit/$YOJONO_RAPORTTI.json"\n'
                           f'yes raportti | head -n 40 > "{self.perus}/raportit/$YOJONO_RAPORTTI.md"\n')
        skripti.chmod(skripti.stat().st_mode | stat.S_IXUSR)
        tulos = ajuri.suorita_katselmointi(self.tt(), PVM, self.perus, skripti)
        self.assertTrue(tulos.ok, tulos.viesti)
        self.assertEqual((self.perus / "tiedot").read_text().strip(),
                         f"2|infra proxmox ja litellm|tark-infra|{PVM}-tarkkailu-tark-infra-agentti-pohja")

    def test_tyyppi_on_skripteissa(self):
        self.assertIn("tarkkailu", ajuri.SKRIPTIT)


class TestTeema(Pohja):
    def it(self, **kw):
        return dict({"id": "i", "tyyppi": "ideointi", "repo": "mcp-model-router", "budjetti": 1.0}, **kw)

    def test_kelvollinen_teema(self):
        ajuri.tarkista_jono({"tehtavat": [self.it(teema="roadmap")]})
        ajuri.tarkista_jono({"tehtavat": [self.it(teema="x" * ajuri.TEEMA_MAX)]})

    def test_virheelliset_teemat(self):
        huonot = [self.it(teema=""), self.it(teema="  "), self.it(teema=5), self.it(teema=None),
                  self.it(teema="x" * (ajuri.TEEMA_MAX + 1)), self.it(teema="a\nb"), self.it(teema="a\rb"),
                  dict(t("k"), teema="x"), dict(t("k"), tyyppi="kehitys", repo="agentti-pohja", aihe="readme", teema="x")]
        for huono in huonot:
            with self.subTest(huono=huono):
                with self.assertRaises(ajuri.JonoVirhe):
                    ajuri.tarkista_jono({"tehtavat": [huono]})

    def skripti(self):
        polku = self.perus / "ideoi.sh"
        polku.write_text("#!/bin/sh\n"
                         f'echo "$#|$YOJONO_TEEMA|$YOJONO_RAPORTTI" > {self.perus}/tiedot\n'
                         f'mkdir -p {self.perus}/raportit\n'
                         f'N="${{YOJONO_RAPORTTI:-{PVM}-ideointi-$1}}"\n'
                         f'echo \'{{"total_cost_usd": 0.2}}\' > "{self.perus}/raportit/$N.json"\n'
                         f'yes r | head -n 150 > "{self.perus}/raportit/$N.md"\n')
        polku.chmod(polku.stat().st_mode | stat.S_IXUSR)
        return polku

    def test_teema_valitetaan_ymparistona_ja_raportti_saa_oman_nimen(self):
        tulos = ajuri.suorita_katselmointi(self.it(id="i-dogfood", teema="roadmap: 3 PR-ehdotusta"), PVM, self.perus, self.skripti())
        self.assertTrue(tulos.ok)
        self.assertEqual((self.perus / "tiedot").read_text().strip(),
                         f"2|roadmap: 3 PR-ehdotusta|{PVM}-ideointi-i-dogfood-mcp-model-router")
        self.assertTrue(tulos.raportti.endswith(f"{PVM}-ideointi-i-dogfood-mcp-model-router.md"))

    def test_kaksi_teemaa_samaan_repoon_eivat_ylikirjoita_toisiaan(self):
        s = self.skripti()
        a = ajuri.suorita_katselmointi(self.it(id="a", teema="x"), PVM, self.perus, s)
        b = ajuri.suorita_katselmointi(self.it(id="b", teema="y"), PVM, self.perus, s)
        self.assertNotEqual(a.raportti, b.raportti)

    def test_ilman_teemaa_nimi_ja_ymparisto_ennallaan(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YOJONO_TEEMA", None)
            os.environ.pop("YOJONO_RAPORTTI", None)
            tulos = ajuri.suorita_katselmointi(self.it(), PVM, self.perus, self.skripti())
        self.assertEqual((self.perus / "tiedot").read_text().strip(), "2||")
        self.assertTrue(tulos.raportti.endswith(f"{PVM}-ideointi-mcp-model-router.md"))


class TestTelegram(unittest.TestCase):
    def test_ei_laheta_ilman_asetuksia(self):
        with mock.patch("urllib.request.urlopen") as u:
            self.assertFalse(ajuri.laheta_telegram("x", env={}))
            u.assert_not_called()

    def test_verkkovirhe_ei_kaada_eika_vuoda_tokenia(self):
        env = {"YOJONO_TELEGRAM_TOKEN": "SALAINEN-TOKEN", "YOJONO_TELEGRAM_CHAT": "1", "HALYTYSPORTTI_TILA": "/ei/ole/p.json"}
        with mock.patch("urllib.request.urlopen", side_effect=OSError("SALAINEN-TOKEN yhteys")), \
                mock.patch("sys.stderr") as err:
            self.assertFalse(ajuri.laheta_telegram("HALYTYS x", env=env))
        kirjoitettu = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertNotIn("SALAINEN-TOKEN", kirjoitettu)

    def test_teksti_katkaistaan(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"YOJONO_TELEGRAM_TOKEN": "t", "YOJONO_TELEGRAM_CHAT": "1", "HALYTYSPORTTI_TILA": os.path.join(d, "p.json")}
            with mock.patch("urllib.request.urlopen") as u:
                u.return_value.__enter__.return_value.status = 200
                ajuri.laheta_telegram("HALYTYS " + "x" * 10000, env=env)
        pyynto = u.call_args.args[0]
        self.assertLessEqual(len(json.loads(pyynto.data)["text"]), ajuri.TELEGRAM_MAX)
        self.assertTrue(pyynto.full_url.startswith("https://"))


class PysyvaJonoTesti(unittest.TestCase):
    """yojono/jono.pysyva.json: valmis pysyva jono ikkunaan 21-07 (doc 51); tarkistetaan ettei se rikkoudu ajuria vasten."""
    POLKU = Path(__file__).resolve().parent / "jono.pysyva.json"

    def lue(self):
        return json.loads(self.POLKU.read_text(encoding="utf-8"))

    def test_jono_on_kelvollinen_ja_katot_jarkevat(self):
        d = self.lue()
        katto, tehtavat = ajuri.tarkista_jono(d)
        self.assertEqual(katto, 5.0)
        self.assertGreaterEqual(d["kuukausikatto"], katto)
        self.assertEqual(len({x["id"] for x in tehtavat}), len(tehtavat))
        self.assertTrue(all(x.get("toisto_pv") for x in tehtavat), "kaikki tehtavat toistuvia (pysyva jono)")

    def test_kehitys_ja_katselmointi_vain_sallituille_repoille(self):
        from agentti import pr_kutsu
        for x in self.lue()["tehtavat"]:
            if x["tyyppi"] in ("kehitys", "katselmointi"):
                self.assertIn(x["repo"], pr_kutsu.REPOT, x["id"])

    def test_aikataulumallit_ovat_yon_ikkunassa(self):
        hak = Path(__file__).resolve().parent / "systemd"
        a = (hak / "yojono-a.timer.mall").read_text()
        c = (hak / "yojono-c.timer.mall").read_text()
        self.assertIn("OnCalendar=*-*-* 21:30:00 Europe/Helsinki", a)
        self.assertIn("OnCalendar=*-*-* 05:00:00 Europe/Helsinki", c)
        self.assertIn("Unit=yojono.service", a + c)

    def test_katselmointi_ajetaan_ennen_halvempia_tehtavia(self):
        """28.-30.10.: katselmointi-agentti-pohja (2,50 USD) ohittui joka yo, koska tarkkailu ja kehitys kuluttivat paivakaton ensin.
        Jarjestys = prioriteetti, joten kallein tehtava ensimmaiseksi: tuore paiva (kulu 0) mahtuu aina ajoon."""
        d = self.lue()
        katto, tehtavat = ajuri.tarkista_jono(d)
        ids = [x["id"] for x in tehtavat]
        kalleimmat = max(x["budjetti"] for x in tehtavat)
        self.assertEqual(tehtavat[0]["id"], "katselmointi-agentti-pohja")
        self.assertEqual(tehtavat[0]["budjetti"], kalleimmat)
        self.assertLess(ids.index("katselmointi-agentti-pohja"), ids.index("tarkkailu-automaatio"))
        self.assertLessEqual(tehtavat[0]["budjetti"], katto)

    def test_katselmoinnin_budjetti_kattaa_toteutuneen_kulun(self):
        """27.-28.10. yo: katselmointi maksoi 1,86 USD ja kaatui 1,50 USD:n budjettiin ilman raporttia."""
        for x in self.lue()["tehtavat"]:
            if x["tyyppi"] == "katselmointi":
                self.assertGreaterEqual(x["budjetti"], 2.0, x["id"])

    def test_ohittamattomien_tarkkailuroolien_budjetti_kattaa_toteutuneen_kulun(self):
        """29.10.: tarkkailu-automaatio maksoi 1,02 USD ja kaatui 0,80 USD:n budjettiin ilman raporttia. infra ja automaatio
        eivat ohitu (tarkkailu.sh lukee palvelimen, ajastimet ja kulut), joten ne tarvitsevat enemman kuin laatu/kasvu."""
        for x in self.lue()["tehtavat"]:
            if x["tyyppi"] == "tarkkailu" and x.get("teema") in ("automaatio", "infra"):
                self.assertGreaterEqual(x["budjetti"], 1.1, x["id"])


class ToistuvaJumiTesti(Pohja):
    """Aamuraportti 28.10.: epaonnistunut toistuva tehtava ei saa jumittua, kun yritykset (3 ajoa yossa) on kaytetty."""

    def tila_jumissa(self, pvm="2026-10-10"):
        (self.perus / "tila.json").write_text(json.dumps({"kulut": {}, "tehtavat": {
            "a": {"tila": "epaonnistui", "yrityksia": ajuri.MAX_YRITYKSIA, "pvm": pvm, "kulu": 0.5, "raportti": "", "viesti": "x", "koodi": "malli_virhe"}}}))

    def test_ei_yritykseen_ennen_toisto_pv(self):
        self.kirjoita_jono(jono([{**t("a"), "toisto_pv": 5}]))
        self.tila_jumissa("2026-10-17")   # 2 pv sitten (PVM 2026-10-19)
        kutsut = []
        self.aja(lambda *a: kutsut.append(1))
        self.assertEqual(kutsut, [])

    def test_toisto_pv_kuluttua_yritetaan_taas_ja_laskuri_nollataan(self):
        self.kirjoita_jono(jono([{**t("a"), "toisto_pv": 5}]))
        self.tila_jumissa("2026-10-10")   # 9 pv sitten
        kutsut = []

        def suorita(tehtava, *a):
            kutsut.append(1)
            return ajuri.Tulos(False, 0.3, "", "taas virhe", "malli_virhe")

        self.aja(suorita)
        self.assertEqual(len(kutsut), 1)
        self.assertEqual(self.tila()["tehtavat"]["a"]["yrityksia"], 1)   # ei MAX+1

    def test_ilman_toisto_pv_jumi_pysyy(self):
        self.kirjoita_jono(jono([t("a")]))
        self.tila_jumissa("2026-10-10")
        kutsut = []
        self.aja(lambda *a: kutsut.append(1))
        self.assertEqual(kutsut, [])


class OpitTesti(Pohja):
    """Oppiva silmukka: opit.md paivittyy enintaan kerran ~vuorokaudessa, virhe ei kaada ajoa."""

    def test_puuttuva_tiedosto_paivitetaan_polulla(self):
        kutsut = []
        self.assertIs(ajuri.paivita_opit(self.perus, 1000.0, lambda a: kutsut.append(a) or 0), True)
        self.assertEqual(kutsut, [["--kirjoita", str(self.perus / "opit.md")]])

    def test_tuore_tiedosto_jaa_rauhaan_vanha_paivitetaan(self):
        polku = self.perus / "opit.md"
        polku.write_text("x")
        os.utime(polku, (1000, 1000))
        kutsut = []
        f = lambda a: kutsut.append(a) or 0
        self.assertIs(ajuri.paivita_opit(self.perus, 1000 + ajuri.OPIT_IKA_S - 1, f), False)
        self.assertIs(ajuri.paivita_opit(self.perus, 1000 + ajuri.OPIT_IKA_S, f), True)
        self.assertEqual(len(kutsut), 1)

    def test_nollasta_poikkeava_paluukoodi_ei_ole_onnistuminen(self):
        self.assertIs(ajuri.paivita_opit(self.perus, 1.0, lambda a: 1), False)

    def test_poikkeus_ei_kaada(self):
        def rikki(a):
            raise OSError("verkko")
        with mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertIs(ajuri.paivita_opit(self.perus, 1.0, rikki), False)
        self.assertIn("Opit-paivitys epaonnistui: OSError", err.getvalue())

    def test_oletuskirjoittaja_on_pr_opit_ja_nyt_oletus_kello(self):
        from agentti import pr_opit
        with mock.patch.object(pr_opit, "main", return_value=0) as m:
            self.assertIs(ajuri.paivita_opit(self.perus), True)
        m.assert_called_once_with(["--kirjoita", str(self.perus / "opit.md")])


class AamupakettiTesti(Pohja):
    """C-ajon jalkeen PR-lista Telegramiin kerran per ajopaiva (agentti.aamupaketti)."""

    def nyt(self, *a):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(*a, tzinfo=ZoneInfo("Europe/Helsinki"))

    def test_lahetetaan_c_ajon_aikaan_ja_merkitaan(self):
        kutsut = []
        self.assertIs(ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, 5, 40), lambda a: kutsut.append(a) or 0), True)
        self.assertEqual(kutsut, [["--laheta"]])
        self.assertEqual((self.perus / "aamupaketti.pvm").read_text().strip(), "2026-10-28")

    def test_ei_kahta_kertaa_samalla_ajopaivalla(self):
        kutsut = []
        f = lambda a: kutsut.append(a) or 0
        ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, 5, 10), f)
        self.assertIs(ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, 6, 0), f), False)
        self.assertEqual(len(kutsut), 1)

    def test_ei_muina_kellonaikoina(self):
        kutsut = []
        for h in (21, 1, 4, 9, 12):
            self.assertIs(ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, h, 0), lambda a: kutsut.append(a) or 0), False)
        self.assertEqual(kutsut, [])

    def test_myohaan_paattynyt_c_ajo_lahettaa_viela_klo_8(self):
        kutsut = []
        self.assertIs(ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, 8, 40), lambda a: kutsut.append(a) or 0), True)
        self.assertEqual(kutsut, [["--laheta"]])
        self.assertEqual((self.perus / "aamupaketti.pvm").read_text().strip(), "2026-10-28")   # sama ajopaiva kuin klo 05:xx

    def test_ikkuna_kattaa_c_ajon_koko_keston(self):
        self.assertEqual(ajuri.AAMUPAKETTI_TUNNIT[0], 5)
        self.assertGreaterEqual(ajuri.AAMUPAKETTI_TUNNIT[-1], 5 + ajuri.ILTA_AIKARAJA_S // 3600)
        self.assertLess(ajuri.AAMUPAKETTI_TUNNIT[-1], ajuri.AJOPAIVAN_ALKU_H)

    def test_uusi_ajopaiva_lahettaa_taas(self):
        f = lambda a: 0
        ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, 5, 10), f)
        self.assertIs(ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 30, 5, 10), f), True)

    def test_ilman_tzdataa_kaytetaan_annettua_aikaa(self):
        from datetime import datetime
        with mock.patch.dict("sys.modules", {"zoneinfo": None}):
            self.assertIs(ajuri.laheta_aamupaketti(self.perus, datetime(2026, 10, 29, 5, 10), lambda a: 0), True)

    def test_lahetysvirhe_ei_merkitse_eika_kaada(self):
        self.assertIs(ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, 5, 10), lambda a: 1), False)
        self.assertFalse((self.perus / "aamupaketti.pvm").exists())

        def kaatuu(a):
            raise OSError("verkko")
        self.assertIs(ajuri.laheta_aamupaketti(self.perus, self.nyt(2026, 10, 29, 5, 10), kaatuu), False)


class AjopaivaTesti(unittest.TestCase):
    """Yoikkunan ajot A 21:30, B 01:00 ja C 05:00 jakavat yhden paivakaton (ajopaiva vaihtuu klo 09 Helsingin aikaa)."""

    def pvm(self, *a):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return ajuri.ajopaiva(datetime(*a, tzinfo=ZoneInfo("Europe/Helsinki")))

    def test_yon_kolme_ajoa_samalla_ajopaivalla(self):
        a = self.pvm(2026, 10, 27, 21, 30)
        self.assertEqual(a, "2026-10-27")
        self.assertEqual(self.pvm(2026, 10, 28, 1, 0), a)
        self.assertEqual(self.pvm(2026, 10, 28, 5, 0), a)
        self.assertEqual(self.pvm(2026, 10, 28, 8, 59), a)

    def test_ajopaiva_vaihtuu_klo_9(self):
        self.assertEqual(self.pvm(2026, 10, 28, 9, 0), "2026-10-28")
        self.assertEqual(self.pvm(2026, 10, 28, 21, 30), "2026-10-28")

    def test_ajopaiva_kayttaa_helsingin_aikaa_vaikka_palvelin_olisi_utc(self):
        from datetime import datetime, timezone
        # 2026-10-28 01:00 Helsinki (EET, UTC+2) = 2026-10-27 23:00 UTC
        self.assertEqual(ajuri.ajopaiva(datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc)), "2026-10-27")

    def test_ilman_tzdataa_kaytetaan_annettua_aikaa_tai_palvelimen_aikaa(self):
        from datetime import datetime
        with mock.patch.dict("sys.modules", {"zoneinfo": None}):
            self.assertEqual(ajuri.ajopaiva(datetime(2026, 10, 28, 1, 0)), "2026-10-27")
            self.assertRegex(ajuri.ajopaiva(), r"^\d{4}-\d{2}-\d{2}$")


class TestMain(Pohja):
    def test_virheellinen_jono_palauttaa_2(self):
        self.kirjoita_jono(jono([t("a", repo="../x")]))
        with mock.patch.dict(os.environ, {"YOJONO_PERUS": str(self.perus)}), mock.patch("sys.stderr"), \
                mock.patch.object(ajuri, "paivita_opit"):        # ei oikeaa verkkoa (GitHub-haku)
            self.assertEqual(ajuri.main(["ajo"]), 2)

    def test_kuiva_tulostaa(self):
        self.kirjoita_jono(jono([t("a")]))
        with mock.patch.dict(os.environ, {"YOJONO_PERUS": str(self.perus)}), mock.patch("builtins.print") as pr:
            self.assertEqual(ajuri.main(["kuiva"]), 0)
        self.assertIn("- a: r", pr.call_args.args[0])


if __name__ == "__main__":
    unittest.main()


class TestEristys(Pohja):
    def test_ymparistossa_oleva_tuotantoloki_ei_saa_testiajoja(self):
        # simuloi tuotantoa: setUpModule poistaa YOJONO_LOKI:n, joten aja() ei kirjaa mihinkaan
        self.assertNotIn("YOJONO_LOKI", os.environ)
        self.kirjoita_jono(jono([t("a")]))
        self.aja(lambda tehtava, pvm, perus, skripti: ajuri.Tulos(True, 0.1, "", "ok"))
        self.assertFalse(ajuri._kirjaa_tapahtuma(t("a"), "ok", 0.1, {}, None))


class LokiKirjaus(Pohja):
    def test_kirjaa_tapahtuman_kun_polku_annettu(self):
        from agentti import tapahtumaloki
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([t("a")]))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda *_: ajuri.Tulos(True, 0.5, "x.md", "ok"))
        yht = tapahtumaloki.avaa(polku)
        try:
            rivit = yht.execute("SELECT lahde, tyyppi, tulos, kulu_usd FROM tapahtuma").fetchall()
        finally:
            yht.close()
        self.assertEqual(rivit, [("yojono", "katselmointi", "ok", 0.5)])

    def _tiedot(self, polku):
        from agentti import tapahtumaloki
        yht = tapahtumaloki.avaa(polku)
        try:
            return [json.loads(r[0]) for r in yht.execute("SELECT tiedot_json FROM tapahtuma ORDER BY id")]
        finally:
            yht.close()

    def test_kirjaa_mallin_yritykset_ja_pr_numeron(self):
        polku = str(self.perus / "loki.db")
        raportti = self.perus / "r.md"
        raportti.write_text("# Kehitys\n\n- Haara: yo/x\n- PR: https://github.com/o/r/pull/42\n- Testit: vihrea\n")
        k = dict(t("a", repo="agentti-pohja"), tyyppi="kehitys", aihe="readme", malli="deepseek-pro")
        self.kirjoita_jono(jono([k]))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda *_: ajuri.Tulos(True, 0.5, str(raportti), "ok"))
        (tiedot,) = self._tiedot(polku)
        self.assertEqual((tiedot["tehtava"], tiedot["malli"], tiedot["yrityksia"], tiedot["pr"]), ("a", "deepseek-pro", 0, 42))

    def test_epaonnistunut_kirjaa_yrityksen_eika_pr_numeroa(self):
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([t("a")]))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda *_: ajuri.Tulos(False, 0.1, "", "virhe"))
        (tiedot,) = self._tiedot(polku)
        self.assertEqual((tiedot["malli"], tiedot["yrityksia"]), ("oletus", 1))
        self.assertNotIn("pr", tiedot)

    def test_pr_numero_puuttuvasta_tai_pr_ei_avattu_raportista_on_none(self):
        self.assertIsNone(ajuri.pr_numero(""))
        self.assertIsNone(ajuri.pr_numero(str(self.perus / "ei-ole.md")))
        r = self.perus / "r.md"
        r.write_text("- PR: ei avattu\n")
        self.assertIsNone(ajuri.pr_numero(str(r)))

    def test_salaisuuden_nakoinen_siivotaan_lokista(self):
        polku = str(self.perus / "loki.db")
        vaarallinen = "sk-" + "abcdefghijklmnopqrstu"
        self.kirjoita_jono(jono([dict(t("a", repo="agentti-pohja"), tyyppi="kehitys", aihe="readme", malli=vaarallinen)]))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda *_: ajuri.Tulos(True, 0.5, "x.md", "ok"))
        from agentti import tapahtumaloki
        yht = tapahtumaloki.avaa(polku)
        try:
            raaka = yht.execute("SELECT tiedot_json FROM tapahtuma").fetchone()[0]
        finally:
            yht.close()
        self.assertNotIn(vaarallinen, raaka)
        self.assertIn("[poistettu]", raaka)

    def test_ilman_polkua_ei_kirjata_ja_virhe_ei_kaada(self):
        self.kirjoita_jono(jono([t("a")]))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YOJONO_LOKI", None)
            teksti, _ = self.aja(lambda *_: ajuri.Tulos(True, 0.5, "x.md", "ok"))
        self.assertIn("VALMIS", teksti)
        self.kirjoita_jono(jono([t("a"), t("b")]))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": "/ei/ole/loki.db"}):
            teksti, _ = self.aja(lambda *_: ajuri.Tulos(False, 0.1, "", "virhe"))
        self.assertIn("b: EPAONNISTUI", teksti)  # loki ei toimi, ajo silti valmistuu


class Kuukausikatto(Pohja):
    def kirjoita(self, katto, kuukausi, tehtavat):
        self.kirjoita_jono({"paivakatto": katto, "kuukausikatto": kuukausi, "tehtavat": tehtavat})

    def test_lukeminen(self):
        self.assertIsNone(ajuri.lue_kuukausikatto({}, 3.0))
        self.assertEqual(ajuri.lue_kuukausikatto({"kuukausikatto": 40}, 3.0), 40.0)
        for huono in (True, "40", 2.0, 2001, -1):
            with self.assertRaises(ajuri.JonoVirhe):
                ajuri.lue_kuukausikatto({"kuukausikatto": huono}, 3.0)

    def test_kuukauden_kulu_vain_samalta_kuulta(self):
        tila = {"kulut": {"2026-10-01": 1.0, "2026-10-19": 2.0, "2026-09-30": 9.0}}
        self.assertEqual(ajuri.kuukauden_kulu(tila, "2026-10-19"), 3.0)

    def test_ylittava_tehtava_ohitetaan_ja_pienempi_mahtuu(self):
        self.kirjoita(3.0, 3.0, [t("iso", budjetti=2.0), t("pieni", budjetti=0.5)])
        (self.perus / "tila.json").write_text(json.dumps({"kulut": {"2026-10-01": 1.5}, "tehtavat": {}}))
        ajettu = []

        def suorita(tehtava, *_):
            ajettu.append(tehtava["id"])
            return ajuri.Tulos(True, 0.1, "x.md", "ok")

        teksti, _ = self.aja(suorita)
        self.assertEqual(ajettu, ["pieni"])
        self.assertIn("iso: ohitettu (kuukausikatto", teksti)

    def test_ilman_kuukausikattoa_kaytos_ennallaan(self):
        self.kirjoita_jono(jono([t("a", budjetti=1.0)]))
        (self.perus / "tila.json").write_text(json.dumps({"kulut": {"2026-10-01": 100.0}, "tehtavat": {}}))
        teksti, _ = self.aja(lambda *_: ajuri.Tulos(True, 0.1, "x.md", "ok"))
        self.assertIn("a: VALMIS", teksti)


class TestLuotettavuus(Pohja):
    def skripti(self, koko_komento):
        polku = self.perus / "katselmoi.sh"
        polku.write_text("#!/bin/sh\n"
                         f'mkdir -p {self.perus}/raportit\n'
                         f'echo \'{{"total_cost_usd": 0.3}}\' > "{self.perus}/raportit/{PVM}-$1.json"\n'
                         f'{koko_komento} > "{self.perus}/raportit/{PVM}-$1.md"\n')
        polku.chmod(polku.stat().st_mode | stat.S_IXUSR)
        return polku

    def test_tyhja_raportti_ei_ole_valmis(self):
        tulos = ajuri.suorita_katselmointi(t("a", repo="r"), PVM, self.perus, self.skripti("echo x"))
        self.assertFalse(tulos.ok)
        self.assertIn("tyhja", tulos.viesti)
        self.assertAlmostEqual(tulos.kulu, 0.3)  # kulu on jo syntynyt ja kirjataan

    def test_riittavan_pitka_raportti_on_valmis(self):
        tulos = ajuri.suorita_katselmointi(t("a", repo="r"), PVM, self.perus, self.skripti("yes raportti | head -n 60"))
        self.assertTrue(tulos.ok)

    def test_halytys_kun_kolme_epaonnistuu(self):
        self.kirjoita_jono(jono([t("a"), t("b"), t("c")], katto=9.0))
        teksti, _ = self.aja(lambda *a: ajuri.Tulos(False, 0.1, "", "rikki"))
        self.assertTrue(teksti.startswith("HALYTYS: 3 epaonnistui"))

    def test_halytys_kun_yritykset_loppuvat(self):
        self.kirjoita_jono(jono([t("a")], katto=9.0))
        (self.perus / "tila.json").write_text(json.dumps({"tehtavat": {"a": {"tila": "epaonnistui", "yrityksia": ajuri.MAX_YRITYKSIA - 1,
                                                                            "pvm": "2026-10-18"}}, "kulut": {}}))
        teksti, _ = self.aja(lambda *a: ajuri.Tulos(False, 0.1, "", "rikki"))
        self.assertIn("HALYTYS: 1 epaonnistui; yritykset loppu: a", teksti)

    def test_ei_halytysta_kun_yksi_epaonnistuu_ensimmaista_kertaa(self):
        self.kirjoita_jono(jono([t("a"), t("b")], katto=9.0))
        vuoro = iter([False, True])
        teksti, _ = self.aja(lambda *a: ajuri.Tulos(next(vuoro), 0.1, "x.md" if False else "", "x"))
        self.assertNotIn("HALYTYS", teksti)


class TestTehtavaMalli(Pohja):
    def test_malli_hyvaksytaan_kehitykselle_ja_hylataan_muilta(self):
        k = {"id": "k", "tyyppi": "kehitys", "aihe": "testit", "repo": "llm-stopcheck", "budjetti": 1.0, "malli": "deepseek-pro"}
        ajuri.tarkista_jono({"tehtavat": [k]})
        for huono in ("Deep Seek", "../x", "", 5, "a" * 41):
            with self.subTest(malli=huono):
                with self.assertRaises(ajuri.JonoVirhe):
                    ajuri.tarkista_jono({"tehtavat": [dict(k, malli=huono)]})
        with self.assertRaises(ajuri.JonoVirhe):
            ajuri.tarkista_jono({"tehtavat": [dict(t("a"), malli="haiku")]})

    def test_malli_valitetaan_ymparistona(self):
        skripti = self.perus / "kehitys.sh"
        skripti.write_text("#!/bin/sh\n"
                           f'echo "$KEHITYS_MALLI" > {self.perus}/malli\n'
                           f'mkdir -p {self.perus}/raportit\n'
                           f'echo \'{{"total_cost_usd": 0.1}}\' > "{self.perus}/raportit/{PVM}-kehitys-testit-$1.json"\n'
                           f'yes "- PR: https://x" | head -n 30 > "{self.perus}/raportit/{PVM}-kehitys-testit-$1.md"\n')
        skripti.chmod(skripti.stat().st_mode | stat.S_IXUSR)
        tehtava = {"id": "k", "tyyppi": "kehitys", "aihe": "testit", "repo": "llm-stopcheck", "budjetti": 1.0, "malli": "deepseek-pro"}
        tulos = ajuri.suorita_katselmointi(tehtava, PVM, self.perus, skripti)
        self.assertTrue(tulos.ok, tulos.viesti)
        self.assertEqual((self.perus / "malli").read_text().strip(), "deepseek-pro")


class TestForkEsitarkistus(Pohja):
    """Forkin synkkaus ennen kehitys-tehtavaa: fork_tarkista palauttaa (ok, koodi, selitys); ohitus ei kuluta rahaa eika yritysta."""

    def kehitys(self, id_="k", repo="llm-stopcheck"):
        return {"id": id_, "tyyppi": "kehitys", "aihe": "testit", "repo": repo, "budjetti": 1.0}

    def _tiedot(self, polku):
        from agentti import tapahtumaloki
        yht = tapahtumaloki.avaa(polku)
        try:
            return yht.execute("SELECT tyyppi, tulos, kulu_usd, tiedot_json FROM tapahtuma ORDER BY id").fetchall()
        finally:
            yht.close()

    def test_fork_jaljessa_ohittaa_ilman_kulua_ja_yritysta(self):
        self.kirjoita_jono(jono([self.kehitys()], katto=9.0))
        kutsut = []
        teksti, _ = self.aja(lambda *a: kutsut.append(1) or ajuri.Tulos(True, 0.5, "x.md", "ok"),
                             fork_tarkista=lambda repo: (False, "fork_ei_ff", "fork-main on erkaantunut ylavirrasta"))
        self.assertEqual(kutsut, [])
        self.assertIn("ohitettu (fork_ei_ff: fork-main on erkaantunut ylavirrasta)", teksti)
        self.assertNotIn("k", self.tila().get("tehtavat", {}))
        self.assertEqual(self.tila()["kulut"].get(PVM, 0.0), 0.0)

    def test_fork_ok_ajaa_normaalisti(self):
        self.kirjoita_jono(jono([self.kehitys()], katto=9.0))
        with mock.patch("yojono.ajuri.pr_kutsu.paikkoja_jaljella", return_value=10):
            teksti, _ = self.aja(lambda *a: ajuri.Tulos(True, 0.5, "", "ok"), fork_tarkista=lambda repo: (True, "", "kelattu"))
        self.assertIn("k: VALMIS", teksti)

    def test_ohitus_kirjataan_tapahtumalokiin_virhekoodilla_ilman_kulua(self):
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([self.kehitys()], katto=9.0))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda *a: self.fail("ei saa ajaa"), fork_tarkista=lambda repo: (False, "fork_push_epaonnistui", "push hylatty"))
        ((tyyppi, tulos, kulu, tiedot),) = self._tiedot(polku)
        tiedot = json.loads(tiedot)
        self.assertEqual((tyyppi, tulos, kulu), ("kehitys", "ohitettu", 0.0))
        self.assertEqual((tiedot["virhekoodi"], tiedot["tehtava"], tiedot["repo"]), ("fork_push_epaonnistui", "k", "llm-stopcheck"))
        self.assertNotIn("push hylatty", json.dumps(tiedot))          # vapaata tekstia ei kirjata, vain koodi

    def test_tuntematon_fork_koodi_kirjataan_tuntemattomana(self):
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([self.kehitys()], katto=9.0))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda *a: self.fail("ei saa ajaa"), fork_tarkista=lambda repo: (False, "keksitty_koodi", "x"))
        self.assertEqual(json.loads(self._tiedot(polku)[0][3])["virhekoodi"], "tuntematon")

    def test_fork_tarkistetaan_kerran_per_repo_ja_ajo(self):
        self.kirjoita_jono(jono([self.kehitys("k1"), self.kehitys("k2"), self.kehitys("k3", repo="mcp-model-router")], katto=9.0))
        kutsut = []

        def tarkista(repo):
            kutsut.append(repo)
            return (False, "fork_ei_ff", "x") if repo == "llm-stopcheck" else (True, "", "ajan_tasalla")
        with mock.patch("yojono.ajuri.pr_kutsu.paikkoja_jaljella", return_value=10):
            teksti, _ = self.aja(lambda *a: ajuri.Tulos(True, 0.5, "", "ok"), fork_tarkista=tarkista)
        self.assertEqual(kutsut, ["llm-stopcheck", "mcp-model-router"])
        self.assertIn("k1: ohitettu (fork_ei_ff", teksti)
        self.assertIn("k2: ohitettu (fork_ei_ff", teksti)
        self.assertIn("k3: VALMIS", teksti)

    def test_muut_tyypit_eivat_laukaise_fork_tarkistusta(self):
        self.kirjoita_jono(jono([t("a")], katto=9.0))
        kutsut = []
        self.aja(lambda *a: ajuri.Tulos(True, 0.1, "", "ok"), fork_tarkista=lambda repo: kutsut.append(repo) or (True, "", ""))
        self.assertEqual(kutsut, [])

    def test_halytys_kun_kolme_tehtavaa_ohitetaan_forkin_takia(self):
        self.kirjoita_jono(jono([self.kehitys("k1"), self.kehitys("k2"), self.kehitys("k3")], katto=9.0))
        teksti, viestit = self.aja(lambda *a: self.fail("ei saa ajaa"), fork_tarkista=lambda repo: (False, "fork_ei_ff", "x"))
        self.assertEqual(teksti.splitlines()[0], "HALYTYS: 3 ohitettu (fork_ei_ff x3)")

    def test_ei_halytysta_kahdesta_ohituksesta(self):
        self.kirjoita_jono(jono([self.kehitys("k1"), self.kehitys("k2")], katto=9.0))
        teksti, _ = self.aja(lambda *a: self.fail("ei saa ajaa"), fork_tarkista=lambda repo: (False, "fork_ei_ff", "x"))
        self.assertNotIn("HALYTYS", teksti)

    def test_halytys_funktio_yhdistaa_epaonnistumiset_ja_ohitukset(self):
        tila = {"tehtavat": {"a": {"tila": "epaonnistui", "yrityksia": 2}}}
        self.assertEqual(ajuri.halytys(tila, ["a"], [("x", "fork_ei_ff"), ("y", "fork_push_epaonnistui"), ("z", "fork_ei_ff")]),
                         "HALYTYS: 1 epaonnistui; yritykset loppu: a; 3 ohitettu (fork_ei_ff x2, fork_push_epaonnistui x1)")
        self.assertEqual(ajuri.halytys({"tehtavat": {}}, [], []), "")

    def test_ohitettu_tehtava_ajetaan_seuraavana_paivana_kun_fork_kunnossa(self):
        self.kirjoita_jono(jono([self.kehitys()], katto=9.0))
        self.aja(lambda *a: self.fail("ei saa ajaa"), fork_tarkista=lambda repo: (False, "fork_ei_ff", "x"))
        with mock.patch("yojono.ajuri.pr_kutsu.paikkoja_jaljella", return_value=10):
            teksti, _ = self.aja(lambda *a: ajuri.Tulos(True, 0.5, "", "ok"), fork_tarkista=lambda repo: (True, "", "kelattu"))
        self.assertIn("k: VALMIS", teksti)


class TestVirhekoodit(Pohja):
    """Epaonnistuneen ajon vakaa virhekoodi (tila.json, yhteenveto, tapahtumaloki). Oikea alaprosessi kuten TestKatselmointiSkripti."""

    def skripti(self, runko):
        polku = self.perus / "katselmoi.sh"
        polku.write_text("#!/bin/sh\n" + runko)
        polku.chmod(polku.stat().st_mode | stat.S_IXUSR)
        (self.perus / "raportit").mkdir(exist_ok=True)
        return polku

    def kirjoita(self, json_teksti, md_runko, poistuu, nimi="{PVM}-$1"):
        """Skripti, joka kirjoittaa raportin JSON:n (jos annettu) ja md:n (jos annettu) ja poistuu annetulla koodilla."""
        rivit = []
        if json_teksti is not None:
            rivit.append("printf '%s\\n' '" + json_teksti + "' > \"" + str(self.perus) + "/raportit/" + nimi.replace("{PVM}", PVM) + ".json\"\n")
        if md_runko is not None:
            rivit.append(md_runko.replace("{POLKU}", str(self.perus) + "/raportit/" + nimi.replace("{PVM}", PVM) + ".md"))
        return self.skripti("".join(rivit) + "exit %d\n" % poistuu)

    def aja_skripti(self, s, tehtava=None, **kw):
        return ajuri.suorita_katselmointi(tehtava or t("a", repo="r", budjetti=0.8), PVM, self.perus, s, **kw)

    BUDJETTI_JSON = json.dumps({"total_cost_usd": 0.95, "is_error": True, "subtype": "error_max_budget_usd", "terminal_reason": "budget_exhausted",
                                "errors": ["Reached maximum budget ($0.8)"]})
    LYHYT_MD = 'echo "ei agentin tulosta (subtype: error_max_budget_usd)" > "{POLKU}"\n'
    PITKA_MD = 'yes raportti | head -n 60 > "{POLKU}"\n'

    def test_tarkkailun_26_10_tapaus_budjetti_loppui(self):
        # tarkkailu.sh kirjoittaa md:n ("ei agentin tulosta") ja poistuu koodilla 1: ei saa jaada "tuntematon"-koodille
        tulos = self.aja_skripti(self.kirjoita(self.BUDJETTI_JSON, self.LYHYT_MD, 1))
        self.assertFalse(tulos.ok)
        self.assertEqual(tulos.koodi, "budjetti_loppui")
        self.assertAlmostEqual(tulos.kulu, 0.95)
        self.assertTrue(tulos.viesti.startswith("budjetti_loppui: "), tulos.viesti)
        self.assertIn("0.80 USD", tulos.viesti)
        self.assertIn("0.95 USD", tulos.viesti)

    def test_budjetti_loppui_myos_kun_skripti_poistuu_nollalla_ja_raportti_on_lyhyt(self):
        tulos = self.aja_skripti(self.kirjoita(self.BUDJETTI_JSON, self.LYHYT_MD, 0))
        self.assertEqual((tulos.ok, tulos.koodi), (False, "budjetti_loppui"))      # ei "raportti_tyhja"

    def test_budjetti_loppui_vaikka_raportti_olisi_pitka(self):
        tulos = self.aja_skripti(self.kirjoita(self.BUDJETTI_JSON, self.PITKA_MD, 1))
        self.assertEqual(tulos.koodi, "budjetti_loppui")

    def test_malli_virhe_claude_json_ista(self):
        j = json.dumps({"total_cost_usd": 0.1, "is_error": True, "subtype": "error_during_execution"})
        tulos = self.aja_skripti(self.kirjoita(j, self.LYHYT_MD, 1))
        self.assertEqual(tulos.koodi, "malli_virhe")
        self.assertIn("error_during_execution", tulos.viesti)
        self.assertTrue(tulos.viesti.startswith("malli_virhe: "))

    def test_malli_virhe_vaikka_skripti_poistuu_nollalla_ja_raportti_tyhja(self):
        j = json.dumps({"total_cost_usd": 0.1, "is_error": True, "subtype": "error_during_execution"})
        self.assertEqual(self.aja_skripti(self.kirjoita(j, self.LYHYT_MD, 0)).koodi, "malli_virhe")

    def test_subtype_siivotaan_viestissa(self):
        j = json.dumps({"total_cost_usd": 0.1, "is_error": True, "subtype": "x y; rm -rf /"})
        tulos = self.aja_skripti(self.kirjoita(j, self.LYHYT_MD, 1))
        self.assertEqual(tulos.koodi, "malli_virhe")
        self.assertIn("(subtype: ?)", tulos.viesti)
        self.assertNotIn("rm -rf", tulos.viesti)

    def test_tuntematon_malli_tekstista(self):
        s = self.skripti('echo "[claude-code:unrecognized_model] {}"\nexit 1\n')
        tulos = self.aja_skripti(s)
        self.assertEqual((tulos.ok, tulos.koodi), (False, "malli_virhe"))

    def test_onnistunut_json_ei_ole_malli_virhe(self):
        j = json.dumps({"total_cost_usd": 0.3, "is_error": False, "subtype": "success"})
        tulos = self.aja_skripti(self.kirjoita(j, self.PITKA_MD, 0))
        self.assertTrue(tulos.ok)
        self.assertEqual(tulos.koodi, "")

    def test_raporttia_ei_syntynyt_kun_mitaan_ei_kirjoiteta(self):
        tulos = self.aja_skripti(self.kirjoita(None, None, 0))
        self.assertEqual((tulos.ok, tulos.koodi), (False, "raporttia_ei_syntynyt"))
        self.assertTrue(tulos.viesti.startswith("raporttia_ei_syntynyt: "), tulos.viesti)
        self.assertEqual(tulos.kulu, 0.0)

    def test_raporttia_ei_syntynyt_kun_vain_json(self):
        j = json.dumps({"total_cost_usd": 0.2, "subtype": "success"})
        tulos = self.aja_skripti(self.kirjoita(j, None, 0))
        self.assertEqual((tulos.ok, tulos.koodi), (False, "raporttia_ei_syntynyt"))
        self.assertAlmostEqual(tulos.kulu, 0.2)

    def test_raporttia_ei_syntynyt_kun_skripti_kaatuu_ennen_claudea(self):
        s = self.skripti('echo "fatal: clone epaonnistui"\nexit 128\n')
        tulos = self.aja_skripti(s)
        self.assertEqual((tulos.ok, tulos.koodi), (False, "raporttia_ei_syntynyt"))
        self.assertIn("clone epaonnistui", tulos.viesti)

    def test_raportti_tyhja(self):
        j = json.dumps({"total_cost_usd": 0.3, "is_error": False, "subtype": "success"})
        tulos = self.aja_skripti(self.kirjoita(j, 'echo x > "{POLKU}"\n', 0))
        self.assertEqual((tulos.ok, tulos.koodi), (False, "raportti_tyhja"))

    def test_aikakatkaisu(self):
        tulos = self.aja_skripti(self.skripti("sleep 5\n"), aikaraja=0.2)
        self.assertEqual((tulos.ok, tulos.koodi), (False, "aikakatkaisu"))
        self.assertTrue(tulos.viesti.startswith("aikakatkaisu: "))
        self.assertAlmostEqual(tulos.kulu, 0.8)

    def test_skripti_ei_kaynnisty(self):
        tulos = self.aja_skripti(self.perus / "ei-ole.sh")
        self.assertEqual((tulos.ok, tulos.koodi), (False, "skripti_ei_kaynnisty"))

    def test_tuntematon_vain_kun_syyta_ei_voi_paatella(self):
        j = json.dumps({"total_cost_usd": 0.3, "is_error": False, "subtype": "success"})
        tulos = self.aja_skripti(self.kirjoita(j, self.PITKA_MD, 1))
        self.assertEqual((tulos.ok, tulos.koodi), (False, "tuntematon"))

    def kehitys_skripti(self, syy, poistuu=1):
        return self.kirjoita(json.dumps({"total_cost_usd": 0.4, "subtype": "success"}),
                             'printf "# Kehitys\\n\\n- PR: ei avattu\\n- Syy: %s\\n" > "{POLKU}"\n' % syy.replace('"', ""), poistuu,
                             nimi="{PVM}-kehitys-testit-$1")

    KEHITYS = {"id": "k", "tyyppi": "kehitys", "aihe": "testit", "repo": "llm-stopcheck", "budjetti": 1.0}

    def test_kehitys_fork_koodi_luetaan_pr_kutsun_viestista(self):
        for koodi in ("fork_ei_ff", "fork_push_epaonnistui", "fork_workflow_oikeus", "fork_jaljessa", "fork_tarkistus_epaonnistui"):
            with self.subTest(koodi=koodi):
                s = self.kehitys_skripti("pr_kutsu epaonnistui: pr_kutsu: %s: jotain meni vikaan" % koodi)
                tulos = ajuri.suorita_katselmointi(self.KEHITYS, PVM, self.perus, s)
                self.assertEqual((tulos.ok, tulos.koodi), (False, koodi))
                self.assertEqual(tulos.viesti.count(koodi), 1, tulos.viesti)

    def test_kehitys_muu_pr_kutsu_virhe(self):
        s = self.kehitys_skripti("pr_kutsu epaonnistui: pr_kutsu: GitHub hylkasi PR:n (HTTP 422)")
        self.assertEqual(ajuri.suorita_katselmointi(self.KEHITYS, PVM, self.perus, s).koodi, "pr_kutsu_epaonnistui")

    def test_kehitys_testit_punaisella(self):
        s = self.kehitys_skripti("testit punaisella agentin muutosten jalkeen: PR:aa ei avattu")
        self.assertEqual(ajuri.suorita_katselmointi(self.KEHITYS, PVM, self.perus, s).koodi, "testit_punaiset")

    def test_luokittele_virhe_ensisijaisuus(self):
        meta_budj = {"subtype": "error_max_budget_usd"}
        self.assertEqual(ajuri.luokittele_virhe("fork_ei_ff unrecognized_model", True, True, meta_budj), "budjetti_loppui")
        self.assertEqual(ajuri.luokittele_virhe("fork_ei_ff", True, True, {"is_error": True}), "malli_virhe")
        self.assertEqual(ajuri.luokittele_virhe("fork_ei_ff pr_kutsu epaonnistui", True, True, {}), "fork_ei_ff")
        self.assertEqual(ajuri.luokittele_virhe("fork_keksitty", False, False, {}), "raporttia_ei_syntynyt")      # tuntematon fork_-koodi ei kelpaa
        self.assertEqual(ajuri.luokittele_virhe("", False, True, {}), "raporttia_ei_syntynyt")
        self.assertEqual(ajuri.luokittele_virhe("", True, False, {}), "raporttia_ei_syntynyt")
        self.assertEqual(ajuri.luokittele_virhe("", True, True, {"subtype": "success", "is_error": False}), "tuntematon")

    def test_vaaditut_koodit_ovat_olemassa(self):
        for koodi in ("fork_jaljessa", "fork_ei_ff", "fork_push_epaonnistui", "raporttia_ei_syntynyt", "aikakatkaisu", "malli_virhe"):
            self.assertIn(koodi, ajuri.VIRHEKOODIT)

    def test_pr_kutsun_fork_koodit_ovat_ajurin_koodilistalla(self):
        import re
        from agentti import pr_kutsu
        lahde = Path(pr_kutsu.__file__).read_text(encoding="utf-8")
        for koodi in set(re.findall(r'"(fork_[a-z_]+)"', lahde)):
            self.assertIn(koodi, ajuri.VIRHEKOODIT, koodi)


class TestVirhekoodiKirjaus(Pohja):
    def _tiedot(self, polku):
        from agentti import tapahtumaloki
        yht = tapahtumaloki.avaa(polku)
        try:
            return [json.loads(r[0]) for r in yht.execute("SELECT tiedot_json FROM tapahtuma ORDER BY id")]
        finally:
            yht.close()

    def test_epaonnistunut_ajo_kirjaa_virhekoodin_lokiin_ja_tilaan(self):
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([t("a")]))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            teksti, _ = self.aja(lambda *_: ajuri.Tulos(False, 0.9, "", "budjetti_loppui: tehtavan budjetti loppui", "budjetti_loppui"))
        (tiedot,) = self._tiedot(polku)
        self.assertEqual(tiedot["virhekoodi"], "budjetti_loppui")
        self.assertEqual(self.tila()["tehtavat"]["a"]["koodi"], "budjetti_loppui")
        self.assertIn("syy: budjetti_loppui:", teksti)

    def test_onnistunut_ajo_ei_kirjaa_virhekoodia(self):
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([t("a")]))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda *_: ajuri.Tulos(True, 0.5, "x.md", "ok"))
        (tiedot,) = self._tiedot(polku)
        self.assertNotIn("virhekoodi", tiedot)
        self.assertNotIn("koodi", self.tila()["tehtavat"]["a"])

    def test_kooditon_tai_tuntematon_koodi_on_tuntematon(self):
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([t("a"), t("b")], katto=9.0))
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda tehtava, *_: ajuri.Tulos(False, 0.1, "", "x") if tehtava["id"] == "a" else ajuri.Tulos(False, 0.1, "", "x", "keksitty"))
        self.assertEqual([x["virhekoodi"] for x in self._tiedot(polku)], ["tuntematon", "tuntematon"])

    def test_viikkoraportti_ryhmittelee_ajurin_kirjaamat_koodit(self):
        from agentti import tapahtumaloki, viikkoraportti
        polku = str(self.perus / "loki.db")
        self.kirjoita_jono(jono([t("a"), t("b"), t("c")], katto=9.0))
        koodit = {"a": "aikakatkaisu", "b": "malli_virhe", "c": "aikakatkaisu"}
        with mock.patch.dict(os.environ, {"YOJONO_LOKI": polku}):
            self.aja(lambda tehtava, *_: ajuri.Tulos(False, 0.1, "", "x", koodit[tehtava["id"]]))
        yht = tapahtumaloki.avaa(polku)
        try:
            tiedot = viikkoraportti.keraa(yht, 7)
        finally:
            yht.close()
        self.assertEqual(tiedot["virhekoodit"], {"aikakatkaisu": 2, "malli_virhe": 1})
        self.assertNotIn("tuntematon", tiedot["virhekoodit"])


# --- Mutaatiotestauksessa (tools/mutantit.py) selvinneet haarat ---

class RajatTesti(Pohja):
    def test_paivakaton_rajat(self):
        self.assertEqual(ajuri.tarkista_jono(jono([], katto=0.5))[0], 0.5)
        self.assertEqual(ajuri.tarkista_jono(jono([], katto=1))[0], 1.0)
        self.assertEqual(ajuri.tarkista_jono(jono([], katto=50))[0], 50.0)
        with self.assertRaises(ajuri.JonoVirhe):
            ajuri.tarkista_jono(jono([], katto=50.5))

    def test_toiston_rajat(self):
        for ok in (1, 365):
            ajuri.tarkista_jono(jono([dict(t("a"), toisto_pv=ok)]))
        for huono in (0, 366, 1.5, True, "7"):
            with self.assertRaises(ajuri.JonoVirhe):
                ajuri.tarkista_jono(jono([dict(t("a"), toisto_pv=huono)]))

    def test_tilatiedosto_on_jarjestetty_ja_sisennetty(self):
        polku = self.perus / "tila.json"
        ajuri.kirjoita_tila(polku, {"b": 1, "a": {"y": 2}})
        self.assertEqual(polku.read_text(), '{\n  "a": {\n    "y": 2\n  },\n  "b": 1\n}')

    def test_syy_katkeaa_300_merkkiin(self):
        self.assertEqual(len(ajuri.syy_viestiksi(None, "x" * 500)), 300)


class SkriptiViestitTesti(Pohja):
    def skripti(self, runko):
        polku = self.perus / "s.sh"
        polku.write_text("#!/bin/sh\n" + runko)
        polku.chmod(polku.stat().st_mode | stat.S_IXUSR)
        (self.perus / "raportit").mkdir(exist_ok=True)
        return polku

    def raportit(self, json_teksti, md_runko, koodi):
        r = self.perus / "raportit"
        rivit = ""
        if json_teksti is not None:
            rivit += "printf '%s' '" + json_teksti + "' > \"" + str(r) + "/" + PVM + "-$1.json\"\n"
        if md_runko is not None:
            rivit += md_runko.replace("{MD}", str(r) + "/" + PVM + "-$1.md")
        return self.skripti(rivit + "exit %d\n" % koodi)

    def aja_skripti(self, s):
        return ajuri.suorita_katselmointi(t("a", budjetti=0.8), PVM, self.perus, s)

    def test_pitka_tuloste_rajataan_viimeiseen_300_merkkiin(self):
        tulos = self.aja_skripti(self.skripti("printf 'A' >&2\nprintf 'E%.0s' $(seq 1 300) >&2\nexit 1\n"))
        self.assertEqual(tulos.viesti.count("E"), 300)
        self.assertNotIn("A", tulos.viesti)

    def test_ei_tulostetta_ei_raporttia_kertoo_poistumiskoodin(self):
        tulos = self.aja_skripti(self.skripti("exit 3\n"))
        self.assertEqual((tulos.koodi, tulos.viesti), ("raporttia_ei_syntynyt", "raporttia_ei_syntynyt: skripti ei tuottanut raporttia (poistumiskoodi 3)"))

    def test_raportti_syntyi_mutta_koodi_nollasta_poikkeaa(self):
        tulos = self.aja_skripti(self.raportit('{"total_cost_usd": 0.1}', 'yes raportti | head -n 60 > "{MD}"\n', 2))
        self.assertEqual((tulos.koodi, tulos.viesti), ("tuntematon", "tuntematon: poistumiskoodi 2"))

    def test_malli_virheen_viesti(self):
        cases = (
            ('{"total_cost_usd": 0.1, "is_error": true}', "claude palautti virheen (subtype: ?)"),
            ('{"total_cost_usd": 0.1, "is_error": true, "subtype": "success"}', "claude palautti virheen (subtype: success)"),
            ('{"total_cost_usd": 0.1, "is_error": false, "subtype": "error_during_execution"}', "claude palautti virheen (subtype: error_during_execution)"),
            ('{"total_cost_usd": 0.1, "is_error": true, "subtype": "Huono Arvo"}', "claude palautti virheen (subtype: ?)"),
        )
        for j, odotettu in cases:
            with self.subTest(j=j):
                tulos = self.aja_skripti(self.raportit(j, "echo lyhyt > \"{MD}\"\n", 1))
                self.assertEqual(tulos.koodi, "malli_virhe")
                self.assertEqual(tulos.viesti, "malli_virhe: " + odotettu if "malli_virhe" not in odotettu else odotettu)

    def test_tuntematon_malli_ei_ylikirjoita_viestia(self):
        tulos = self.aja_skripti(self.skripti("echo 'unrecognized_model: foo' >&2\nexit 1\n"))
        self.assertEqual(tulos.koodi, "malli_virhe")
        self.assertIn("unrecognized_model: foo", tulos.viesti)
        self.assertNotIn("subtype", tulos.viesti)


class LisatiedotRajatTesti(Pohja):
    def md(self, teksti):
        p = self.perus / "r.md"
        p.write_text(teksti)
        return p

    def test_syyn_ja_ehdotuksen_pituusrajat(self):
        self.assertEqual(ajuri.raportin_lisatiedot("kehitys", self.md("- PR: ei\n- Syy: " + "s" * 200 + "\n")), " EI PR:AA (" + "s" * 120 + ")")
        ulos = ajuri.raportin_lisatiedot("tarkkailu", self.md("1. PARAS " + "p" * 300 + "\n"))
        self.assertEqual(ulos.strip(), "PARAS " + "p" * 154)


class TelegramTarkkaTesti(unittest.TestCase):
    ENV = {"YOJONO_TELEGRAM_TOKEN": "t", "YOJONO_TELEGRAM_CHAT": "1"}

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.ENV = dict(self.ENV, HALYTYSPORTTI_TILA=os.path.join(self._d.name, "p.json"))

    def test_puuttuva_asetus_palauttaa_false(self):
        with mock.patch("urllib.request.urlopen") as u:
            self.assertIs(ajuri.laheta_telegram("x", env={}), False)
            self.assertIs(ajuri.laheta_telegram("x", env={"YOJONO_TELEGRAM_TOKEN": "t"}), False)
            self.assertIs(ajuri.laheta_telegram("x", env={"YOJONO_TELEGRAM_CHAT": "1"}), False)
            u.assert_not_called()

    def test_tulos_ja_aikakatkaisu(self):
        with mock.patch("urllib.request.urlopen") as u:
            u.return_value.__enter__.return_value.status = 200
            self.assertIs(ajuri.laheta_telegram("HALYTYS x", env=self.ENV), True)
            self.assertEqual(u.call_args.kwargs, {"timeout": 20})
            u.return_value.__enter__.return_value.status = 500
            self.assertIs(ajuri.laheta_telegram("HALYTYS x", env=self.ENV), False)

    def test_verkkovirhe_palauttaa_false(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("x")), mock.patch("sys.stderr"):
            self.assertIs(ajuri.laheta_telegram("x", env=self.ENV), False)


class PrNumeroRajaTesti(Pohja):
    def test_lukuraja_20000_merkkia(self):
        r = self.perus / "r.md"
        r.write_text("a" * 19977 + "\n- PR: https://x/pull/55\n")
        self.assertEqual(ajuri.pr_numero(str(r)), 5)


class LokiTarkkaTesti(Pohja):
    def tiedot(self, polku):
        from agentti import tapahtumaloki
        yht = tapahtumaloki.avaa(polku)
        try:
            return yht.execute("SELECT kulu_usd, tulos, tiedot_json FROM tapahtuma ORDER BY id").fetchall()
        finally:
            yht.close()

    def test_paluuarvot(self):
        self.assertIs(ajuri._kirjaa_tapahtuma(t("a"), "ok", 0.1, {}, None), False)
        polku = str(self.perus / "loki.db")
        self.assertIs(ajuri._kirjaa_tapahtuma(t("a"), "ok", 0.123456, {"x": 1}, polku), True)
        (kulu, tulos, _), = self.tiedot(polku)
        self.assertEqual((kulu, tulos), (0.1235, "ok"))
        with mock.patch("sys.stderr"):
            self.assertIs(ajuri._kirjaa_tapahtuma(t("a"), "ok", 0.1, {}, "/ei/ole/loki.db"), False)

    def test_ohituksen_malli_ja_koodi(self):
        polku = str(self.perus / "loki.db")
        self.assertIs(ajuri.kirjaa_ohitus(dict(t("a"), malli="sonnet"), "fork_ei_ff", PVM, polku), True)
        self.assertIs(ajuri.kirjaa_ohitus(t("b"), "outo_koodi", PVM, polku), True)
        a, b = [json.loads(r[2]) for r in self.tiedot(polku)]
        self.assertEqual((a["malli"], a["virhekoodi"]), ("sonnet", "fork_ei_ff"))
        self.assertEqual((b["malli"], b["virhekoodi"]), ("oletus", "tuntematon"))


class AjoTarkkaTesti(Pohja):
    def test_perushakemisto_luodaan_sisakkain(self):
        sisakkain = self.perus / "a" / "b"
        ajuri.aja(sisakkain, "/x", laheta=lambda x: None, pvm=PVM, kuiva=True)
        self.assertTrue((sisakkain / "lukko").exists())
        ajuri.aja(sisakkain, "/x", laheta=lambda x: None, pvm=PVM, kuiva=True)

    def test_yon_aikaraja_mittaa_kulunutta_aikaa(self):
        self.kirjoita_jono(jono([t("a")]))
        ajettu = []
        ok = lambda tehtava, *_: (ajettu.append(tehtava["id"]), ajuri.Tulos(True, 0.1, "x.md", "ok"))[1]
        self.aja(ok, nyt=lambda: 6000)       # ei aikaa kulunut: ajetaan
        self.assertEqual(ajettu, ["a"])
        (self.perus / "tila.json").unlink()
        ajettu.clear()
        aika = iter([0, ajuri.ILTA_AIKARAJA_S + 1])
        teksti, _ = self.aja(ok, nyt=lambda: next(aika))
        self.assertEqual(ajettu, [])
        self.assertIn("- a: ohitettu (yon aikaraja)", teksti)

    def test_ohitusviestit_ovat_tarkkoja(self):
        (self.perus / "tila.json").write_text(json.dumps({"kulut": {PVM: 2.5}, "tehtavat": {}}))
        self.kirjoita_jono({"paivakatto": 3.0, "kuukausikatto": 3.0, "tehtavat": [t("a", budjetti=1.0)]})
        teksti, _ = self.aja(lambda *_: ajuri.Tulos(True, 0.1, "x.md", "ok"))
        self.assertIn("- a: ohitettu (kuukausikatto: jaljella 0.50 < varaus 1.30)", teksti)
        (self.perus / "tila.json").write_text(json.dumps({"kulut": {PVM: 5.0}, "tehtavat": {}}))
        teksti, _ = self.aja(lambda *_: ajuri.Tulos(True, 0.1, "x.md", "ok"))
        self.assertIn("- a: ohitettu (kuukausikatto: jaljella 0.00 < varaus 1.30)", teksti)
        self.kirjoita_jono(jono([t("a", budjetti=1.0)]))
        (self.perus / "tila.json").write_text(json.dumps({"kulut": {PVM: 2.5}, "tehtavat": {}}))
        teksti, _ = self.aja(lambda *_: ajuri.Tulos(True, 0.1, "x.md", "ok"))
        self.assertIn("- a: ohitettu (paivakatto: jaljella 0.50 < varaus 1.30)", teksti)

    def test_yksi_pr_paikka_riittaa(self):
        k = dict(t("a", repo="agentti-pohja"), tyyppi="kehitys", aihe="readme")
        self.kirjoita_jono(jono([k]))
        with mock.patch.object(ajuri.pr_kutsu, "paikkoja_jaljella", return_value=1):
            teksti, _ = self.aja(lambda *_: ajuri.Tulos(True, 0.1, "x.md", "ok"))
        self.assertIn("a: VALMIS", teksti)
        (self.perus / "tila.json").unlink()
        with mock.patch.object(ajuri.pr_kutsu, "paikkoja_jaljella", return_value=0):
            teksti, _ = self.aja(lambda *_: ajuri.Tulos(True, 0.1, "x.md", "ok"))
        self.assertIn("PR-paivaraja taynna", teksti)

    def test_kulu_kirjataan_nelja_desimaalia(self):
        self.kirjoita_jono(jono([t("a")]))
        self.aja(lambda *_: ajuri.Tulos(True, 0.123456, "x.md", "ok"))
        tila = self.tila()
        self.assertEqual(tila["kulut"][PVM], 0.1235)
        self.assertEqual(tila["tehtavat"]["a"]["kulu"], 0.1235)


class PeilausTarkkaTesti(Pohja):
    def test_paluuarvot_ja_parametrit(self):
        self.assertIs(ajuri.peilaa(None), False)
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as r:
            self.assertIs(ajuri.peilaa("/p.sh"), True)
        self.assertEqual(r.call_args.args[0], ["/p.sh"])
        self.assertEqual(r.call_args.kwargs, {"capture_output": True, "text": True, "timeout": 300, "check": False})
        with mock.patch.object(subprocess, "run", side_effect=OSError("x")), mock.patch("sys.stderr"):
            self.assertIs(ajuri.peilaa("/p.sh"), False)

    def test_virheilmoitus(self):
        def aja(koodi, ulos, virhe):
            err = io.StringIO()
            with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], koodi, ulos, virhe)), mock.patch.object(sys, "stderr", err):
                tulos = ajuri.peilaa("/p.sh")
            return tulos, err.getvalue()
        self.assertEqual(aja(0, "ok", ""), (True, ""))
        self.assertEqual(aja(3, "ulos", "virhe"), (False, "Peilaus epaonnistui (koodi 3): virhe\n"))
        self.assertEqual(aja(3, "ulos", ""), (False, "Peilaus epaonnistui (koodi 3): ulos\n"))
        self.assertEqual(aja(3, "", "v" * 300), (False, "Peilaus epaonnistui (koodi 3): " + "v" * 200 + "\n"))


class MainTarkkaTesti(Pohja):
    def env(self):
        return mock.patch.dict(os.environ, {"YOJONO_PERUS": str(self.perus)})

    def test_tila_tulostaa_json_sisennettyna(self):
        (self.perus / "tila.json").write_text(json.dumps({"viesti": "k\u00e4yt\u00f6"}))
        ulos = io.StringIO()
        with self.env(), mock.patch.object(sys, "stdout", ulos):
            self.assertEqual(ajuri.main(["tila"]), 0)
        self.assertEqual(ulos.getvalue(), '{\n  "viesti": "k\u00e4yt\u00f6"\n}\n')
        (self.perus / "tila.json").unlink()
        ulos = io.StringIO()
        with self.env(), mock.patch.object(sys, "stdout", ulos):
            ajuri.main(["tila"])
        self.assertEqual(ulos.getvalue(), "{}\n")

    def test_peilaus_vain_ajolla(self):
        self.kirjoita_jono(jono([]))
        with self.env(), mock.patch.dict(os.environ, {"YOJONO_PEILAA": "/p.sh"}), mock.patch.object(ajuri, "peilaa") as p, \
                mock.patch.object(ajuri, "paivita_opit"), mock.patch.object(ajuri, "laheta_aamupaketti"), mock.patch("builtins.print"):
            self.assertEqual(ajuri.main(["ajo"]), 0)
            p.assert_called_once_with("/p.sh")
            p.reset_mock()
            self.assertEqual(ajuri.main(["kuiva"]), 0)
            p.assert_not_called()

    def test_opit_paivittyy_vain_ajolla(self):
        self.kirjoita_jono(jono([]))
        with self.env(), mock.patch.object(ajuri, "paivita_opit") as p, \
                mock.patch.object(ajuri, "laheta_aamupaketti"), mock.patch("builtins.print"):
            self.assertEqual(ajuri.main(["kuiva"]), 0)
            p.assert_not_called()
            self.assertEqual(ajuri.main(["ajo"]), 0)
            p.assert_called_once_with(str(self.perus))
