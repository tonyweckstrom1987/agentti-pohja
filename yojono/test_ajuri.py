import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yojono import ajuri

PVM = "2026-10-19"


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
        self.assertEqual(len(kutsut), 3)  # kolme tehtavaa a 1,00 USD tayttaa 3,00 USD:n katon
        self.assertLessEqual(self.tila()["kulut"][PVM], 3.0)

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
        for huono in (self.kt(aihe="muu"), self.kt(aihe=None), self.kt(repo="kauppatiimi"), self.kt(repo="agenttifirma")):
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
                  self.ot(aihe="readme"), self.ot(repo="kauppatiimi")]
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
        env = {"YOJONO_TELEGRAM_TOKEN": "SALAINEN-TOKEN", "YOJONO_TELEGRAM_CHAT": "1"}
        with mock.patch("urllib.request.urlopen", side_effect=OSError("SALAINEN-TOKEN yhteys")), \
                mock.patch("sys.stderr") as err:
            self.assertFalse(ajuri.laheta_telegram("x", env=env))
        kirjoitettu = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertNotIn("SALAINEN-TOKEN", kirjoitettu)

    def test_teksti_katkaistaan(self):
        env = {"YOJONO_TELEGRAM_TOKEN": "t", "YOJONO_TELEGRAM_CHAT": "1"}
        with mock.patch("urllib.request.urlopen") as u:
            u.return_value.__enter__.return_value.status = 200
            ajuri.laheta_telegram("x" * 10000, env=env)
        pyynto = u.call_args.args[0]
        self.assertLessEqual(len(json.loads(pyynto.data)["text"]), ajuri.TELEGRAM_MAX)
        self.assertTrue(pyynto.full_url.startswith("https://"))


class TestMain(Pohja):
    def test_virheellinen_jono_palauttaa_2(self):
        self.kirjoita_jono(jono([t("a", repo="../x")]))
        with mock.patch.dict(os.environ, {"YOJONO_PERUS": str(self.perus)}), mock.patch("sys.stderr"):
            self.assertEqual(ajuri.main(["ajo"]), 2)

    def test_kuiva_tulostaa(self):
        self.kirjoita_jono(jono([t("a")]))
        with mock.patch.dict(os.environ, {"YOJONO_PERUS": str(self.perus)}), mock.patch("builtins.print") as pr:
            self.assertEqual(ajuri.main(["kuiva"]), 0)
        self.assertIn("- a: r", pr.call_args.args[0])


if __name__ == "__main__":
    unittest.main()


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
