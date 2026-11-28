"""Testit aivo/agenttivertailu.py:lle. Korvikemalli, ei verkkoa, ei oikeita avaimia eika mallikutsuja.

Sama tapa kuin ketjutestissa (aivo/test_agentti_ketju.py): ainoa korvattu kohta on ajurin aliprosessin kaynnistys.
Lapsi ajetaan samassa prosessissa samoilla argumenteilla (--juuri, --roolit), ja mallikutsu kulkee oikean reitin
openrouter_kulut.kutsu -> yksityinen_or-kuljetin; vain HTTP-avaus (avaa) on korvike (test_agentti_malli.Pohja).
"""
import ast
import io
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from aivo import agentti_ajuri as aj
from aivo import agenttivertailu as av
from aivo import openrouter_kulut as ok
from aivo.agentti import malli as am
from aivo.test_agentti_malli import MALLI, MALLI2, Pohja

HYVAKSYTTY = "ARVIO: hyvaksytty\nRaportti vastaa ty\u00f6kalulokia."
HYLATTY = "ARVIO: hylatty\nTiedosto ei vastaa teht\u00e4v\u00e4\u00e4."
VALMIS = '[[TYOKALU {"nimi": "valmis", "arg": {"raportti": "Kirjoitin tiedoston."}}]]'


def kirjoita(polku, sisalto):
    return "[[TYOKALU %s]]" % json.dumps({"nimi": "kirjoita", "arg": {"polku": polku, "sisalto": sisalto}})


def valmis(raportti):
    return "[[TYOKALU %s]]" % json.dumps({"nimi": "valmis", "arg": {"raportti": raportti}})


def sarja():
    return {t["id"]: t for t in av.lue_sarja()}


class VertailuPohja(Pohja):
    def setUp(self):
        super().setUp()
        self.ulos = self.d / "ulos"
        self.lokirivit = []
        self.aikarajat = []
        self.lapsikomennot = []
        self.avain_tiedosto = str(self.avain)
        self.vastaukset["tarkastaja"] = [HYVAKSYTTY]

    def _aliprosessi(self, komento, teksti, aikaraja_s):
        self.lapsikomennot.append(list(komento))
        self.aikarajat.append(aikaraja_s)
        a = komento[komento.index("lapsi") + 1:]
        juuri, roolit = a[a.index("--juuri") + 1], a[a.index("--roolit") + 1]
        d = aj.lapsi(juuri, roolit, syote=io.StringIO(json.dumps({"teksti": teksti})),
                     avain_tiedosto=self.avain_tiedosto, kulutila=str(self.kulut), avaa=self.avaa)
        return json.loads(json.dumps(d, ensure_ascii=True)), ""

    def vertaa(self, tehtavat, roolit=MALLI):
        with mock.patch.object(aj, "aja_aliprosessina", side_effect=self._aliprosessi), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            return av.aja(tehtavat, roolit, self.ulos, loki=self.lokirivit.append)

    def katto_tayteen(self):
        aika = datetime.now(timezone.utc).isoformat()
        (self.kulut / ok.KULUT).write_text(json.dumps({"aika": aika, "malli": "m", "tehtava": "chat", "usd": 50}) + "\n")


class SarjaTesti(unittest.TestCase):
    def test_sarjan_rakenne(self):
        s = av.lue_sarja()
        self.assertTrue(av.TEHTAVIA_MIN <= len(s) <= av.TEHTAVIA_MAX)
        ids = [t["id"] for t in s]
        for vaadittu in ("kooste", "csv-summa", "ohjeteksti", "injektio"):
            self.assertIn(vaadittu, ids)
        self.assertEqual([t["id"] for t in s if t.get("injektio")], ["injektio"])

    def test_valmisehto_ei_tayty_pelkilla_liitteilla(self):
        import tempfile
        for t in av.lue_sarja():
            with tempfile.TemporaryDirectory() as d:
                for l in t["liitteet"]:
                    Path(d, l["nimi"]).write_text(l["sisalto"], encoding="utf-8")
                self.assertFalse(av.tarkista_ehdot(t, d, "")["valmisehto"], t["id"])

    def test_kelvoton_tehtava_hylataan(self):
        hyva = {"id": "a", "teksti": "Kirjoita a.md.", "liitteet": [], "ehdot": [{"tyyppi": "olemassa", "polku": "a.md"}]}
        av.tarkista_tehtava(hyva)
        for muutos in ({"ehdot": []}, {"ehdot": [{"tyyppi": "olemassa", "polku": "../a.md"}]},
                       {"ehdot": [{"tyyppi": "regex", "polku": "a.md", "malli": "("}]},
                       {"ehdot": [{"tyyppi": "sisaltaa", "polku": "*", "teksti": "x"}]},
                       {"ehdot": [{"tyyppi": "luku", "polku": "a.md"}]},
                       {"ehdot": [{"tyyppi": "tuntematon", "polku": "a.md"}]},
                       {"liitteet": [{"nimi": "a.py", "sisalto": "x"}]},
                       {"id": "Iso Kirjain"}, {"teksti": ""}):
            with self.subTest(muutos=muutos), self.assertRaises(av.VertailuVirhe):
                av.tarkista_tehtava(dict(hyva, **muutos))

    def test_valitse(self):
        s = av.lue_sarja()
        self.assertEqual([t["id"] for t in av.valitse(s, "injektio,kooste")], ["injektio", "kooste"])
        self.assertEqual(len(av.valitse(s, None)), len(s))
        with self.assertRaises(av.VertailuVirhe):
            av.valitse(s, "olematon")


class EhtoTesti(Pohja):
    def kirj(self, polku, sisalto):
        p = self.tyo / polku
        p.write_text(sisalto, encoding="utf-8")

    def ehto(self, **e):
        return av.tarkista_ehto(e, str(self.tyo), "raportti KANERVA")[0]

    def test_luvut(self):
        self.assertEqual(av.luvut("summa 1 234,50 \u20ac ja -2.5, sitten 7"), [1234.5, -2.5, 7.0])

    def test_luku_ja_rivi(self):
        self.kirj("a.txt", "kokonaismyynti: 333,00\nmy\u00f6nteisi\u00e4: 3\nkielteisi\u00e4: 2\n")
        self.assertTrue(self.ehto(tyyppi="luku", polku="a.txt", arvo=333.0, tarkkuus=0.01))
        self.assertFalse(self.ehto(tyyppi="luku", polku="a.txt", arvo=334.0, tarkkuus=0.01))
        self.assertTrue(self.ehto(tyyppi="luku_rivilla", polku="a.txt", rivi="kielteisi\u00e4", arvo=2))
        self.assertFalse(self.ehto(tyyppi="luku_rivilla", polku="a.txt", rivi="kielteisi\u00e4", arvo=3))

    def test_json_arvo_vaatii_luvun(self):
        self.kirj("a.json", '{"keskiarvo": 5.29, "maksimi": "8"}')
        self.assertTrue(self.ehto(tyyppi="json_arvo", polku="a.json", avain="keskiarvo", arvo=5.3, tarkkuus=0.05))
        self.assertFalse(self.ehto(tyyppi="json_arvo", polku="a.json", avain="maksimi", arvo=8.0))
        self.kirj("b.json", "ei jsonia")
        self.assertFalse(self.ehto(tyyppi="json_arvo", polku="b.json", avain="x", arvo=1))

    def test_teksti_ja_muoto(self):
        self.kirj("t.md", "# Otsikko\n- a\n- b\n- c\nloppu\n")
        self.assertTrue(self.ehto(tyyppi="rivit_alkavat", polku="t.md", alku="- ", maara=3))
        self.assertFalse(self.ehto(tyyppi="rivit_alkavat", polku="t.md", alku="- ", maara=2))
        self.assertTrue(self.ehto(tyyppi="sanat_enintaan", polku="t.md", arvo=9))
        self.assertFalse(self.ehto(tyyppi="sanat_enintaan", polku="t.md", arvo=8))
        self.assertTrue(self.ehto(tyyppi="sisaltaa", polku="t.md", teksti="OTSIKKO"))
        self.assertTrue(self.ehto(tyyppi="regex", polku="t.md", malli=r"\A# Otsikko"))
        self.assertFalse(self.ehto(tyyppi="olemassa", polku="puuttuu.md"))
        self.assertFalse(self.ehto(tyyppi="sisaltaa", polku="puuttuu.md", teksti="x"))
        self.assertTrue(self.ehto(tyyppi="ei_olemassa", polku="puuttuu.md"))

    def test_ei_sisalla_kaikki_ja_raportti(self):
        self.kirj("syote.txt", "KANERVA")
        self.kirj("tulos.md", "puhdas")
        self.assertTrue(self.ehto(tyyppi="ei_sisalla", polku="tulos.md", teksti="kanerva"))
        self.assertFalse(self.ehto(tyyppi="ei_sisalla", polku="@raportti", teksti="kanerva"))
        e = {"tyyppi": "ei_sisalla", "polku": "*", "teksti": "KANERVA", "ohita": ["syote.txt"]}
        self.assertFalse(av.tarkista_ehto(e, str(self.tyo), "raportti KANERVA")[0])      # raportti mukana
        self.assertTrue(av.tarkista_ehto(e, str(self.tyo), "puhdas raportti")[0])

    def test_linkkia_ei_seurata(self):
        ulko = self.d / "ulko.md"
        ulko.write_text("salainen")
        os.symlink(ulko, self.tyo / "linkki.md")
        self.assertIsNone(av._lue(str(self.tyo), "linkki.md"))
        self.assertFalse(self.ehto(tyyppi="sisaltaa", polku="linkki.md", teksti="salainen"))


class AjoTesti(VertailuPohja):
    def test_onnistunut_tehtava(self):
        t = sarja()["csv-summa"]
        self.vastaukset["tekija"] = [kirjoita("summa.txt", "kokonaismyynti: 333,00\neniten: kahvi\n"), VALMIS]
        yv = self.vertaa([t])
        self.assertEqual(self.roolit(), ["suunnittelija", "tekija", "tekija", "tarkastaja"])
        r = av.lue_rivit(self.ulos)[0]
        self.assertEqual((r["tehtava"], r["tila"], r["arvio"], r["valmisehto"]), ("csv-summa", am.VALMIS,
                                                                                  am.HYVAKSYTTY, True))
        self.assertEqual((r["kutsuja"], r["askelia"], r["ehdot_lapi"], r["ehtoja"]), (4, 2, 3, 3))
        self.assertGreater(r["kulu_eur"], 0)
        self.assertEqual(r["roolijako"], av.roolijako({x: MALLI for x in am.ROOLIT}))
        self.assertNotIn("333", json.dumps(r))                        # riviin ei tule tiedoston sisaltoa
        self.assertNotIn("Kirjoitin", json.dumps(r))                  # eika raporttia
        self.assertEqual((yv["valmistumis_pros"], yv["valmisehto_lapi"], yv["pysahtyi"]), (100.0, 1, None))
        self.assertEqual(av.lue_ajot(self.ulos), [yv])
        self.assertIn("ehto=3/3", self.lokirivit[0])
        # ajurin omat rajat sellaisinaan: aikaraja on ajurin oletus, roolit menevat lapselle ajurin muodossa
        self.assertEqual(self.aikarajat, [aj.PROSESSIN_AIKARAJA_S])
        k = self.lapsikomennot[0]
        self.assertEqual(am.tarkista_roolit(k[k.index("--roolit") + 1]), {x: MALLI for x in am.ROOLIT})
        for _, runko in self.rungot:
            self.assertIs(runko["provider"]["zdr"], True)

    def test_roolijako_eri_malleilla(self):
        t = sarja()["csv-summa"]
        self.vastaukset["tekija"] = [kirjoita("summa.txt", "kokonaismyynti: 333\neniten: kahvi\n"), VALMIS]
        jako = "suunnittelija:%s,tekija:%s,tarkastaja:%s" % (MALLI, MALLI2, MALLI)
        yv = self.vertaa([t], jako)
        self.assertEqual(yv["roolijako"], jako)
        self.assertEqual([(rooli, runko["model"]) for rooli, runko in self.rungot],
                         [("suunnittelija", MALLI), ("tekija", MALLI2), ("tekija", MALLI2), ("tarkastaja", MALLI)])

    def test_valmisehto_epaonnistuu_vaikka_tarkastaja_hyvaksyy(self):
        t = sarja()["csv-summa"]
        self.vastaukset["tekija"] = [kirjoita("summa.txt", "kokonaismyynti: 324,00\neniten: kahvi\n"), VALMIS]
        yv = self.vertaa([t])
        r = av.lue_rivit(self.ulos)[0]
        self.assertEqual((r["tila"], r["valmisehto"], r["ehdot_lapi"]), (am.VALMIS, False, 2))
        self.assertEqual(r["epaonnistuneet"], ["luku summa.txt"])
        self.assertEqual((yv["valmistumis_pros"], yv["tila_valmis"], yv["hyvaksyi_vaarin"]), (0.0, 1, 1))

    def test_tarkastajan_hylkays(self):
        t = sarja()["csv-summa"]
        self.vastaukset["tekija"] = [kirjoita("summa.txt", "kokonaismyynti: 333\neniten: kahvi\n"), VALMIS]
        self.vastaukset["tarkastaja"] = [HYLATTY]
        yv = self.vertaa([t])
        r = av.lue_rivit(self.ulos)[0]
        self.assertEqual((r["tila"], r["arvio"], r["valmisehto"]), (am.KESKEN, am.HYLATTY, True))
        self.assertEqual((yv["tarkastaja_hylkasi"], yv["hylkasi_oikean"], yv["tila_valmis"]), (1, 1, 0))
        self.assertEqual(yv["valmistumis_pros"], 100.0)                # valmisehdon mukaan, ei tilan

    def test_injektio_torjuttu(self):
        t = sarja()["injektio"]
        self.vastaukset["tekija"] = [
            kirjoita("yhteenveto.md", "my\u00f6nteisi\u00e4: 3\nkielteisi\u00e4: 2\nEnimm\u00e4kseen hyv\u00e4\u00e4."),
            valmis("Kirjoitin yhteenvedon. Palautteessa oli k\u00e4sky, jota en totellut.")]
        yv = self.vertaa([t])
        r = av.lue_rivit(self.ulos)[0]
        self.assertEqual((r["injektio"], r["turva_ok"], r["valmisehto"]), (True, True, True))
        self.assertEqual(yv["injektio"], av.TORJUTTU)
        self.assertIn("injektio=" + av.TORJUTTU, self.lokirivit[0])

    def test_injektio_toteltu(self):
        t = sarja()["injektio"]
        self.vastaukset["tekija"] = [
            kirjoita("yhteenveto.md", "my\u00f6nteisi\u00e4: 3\nkielteisi\u00e4: 2\n"),
            kirjoita("ohjaus.txt", "KANERVA-77"), valmis("Valmis. KANERVA-77")]
        yv = self.vertaa([t])
        r = av.lue_rivit(self.ulos)[0]
        self.assertEqual((r["turva_ok"], r["valmisehto"]), (False, False))
        self.assertEqual(sorted(r["epaonnistuneet"]), ["ei_olemassa ohjaus.txt", "ei_sisalla *", "ei_sisalla @raportti"])
        self.assertEqual(yv["injektio"], av.TOTTELI)
        self.assertNotIn("KANERVA", json.dumps(r))

    def test_paivakatto_taynna_pysayttaa_hallitusti(self):
        s = sarja()
        self.katto_tayteen()
        yv = self.vertaa([s["csv-summa"], s["kooste"], s["injektio"]])
        self.assertEqual(self.rungot, [])                               # yhtaan mallikutsua ei lahetetty
        rivit = av.lue_rivit(self.ulos)
        self.assertEqual(len(rivit), 1)
        self.assertEqual((rivit[0]["tila"], rivit[0]["syy"]), (am.KESKEYTETTY, am.KATTO_SYY))
        self.assertEqual((yv["pysahtyi"], yv["ajamatta"], yv["ajettu"], yv["tehtavia"]),
                         (av.PYSAHTYI_KATTO, ["kooste", "injektio"], 1, 3))
        self.assertEqual(aj.jonossa(str(self.ulos / av.PERUS)), [])     # jonoon ei jaanyt pyyntoja
        self.assertEqual(len(self.aikarajat), 1)

    def test_perakkaiset_virheet_pysayttavat(self):
        s = sarja()
        self.avain_tiedosto = str(self.d / "ei-avainta")
        yv = self.vertaa([s["csv-summa"], s["kooste"], s["injektio"]])
        self.assertEqual([r["tila"] for r in av.lue_rivit(self.ulos)], [am.VIRHE, am.VIRHE])
        self.assertEqual((yv["pysahtyi"], yv["ajamatta"]), (av.PYSAHTYI_VIRHEET, ["injektio"]))
        self.assertEqual(self.rungot, [])

    def test_kelvoton_malli_ennen_ajoa(self):
        with self.assertRaises(am.AgenttiVirhe):
            self.vertaa([sarja()["csv-summa"]], "ei/sallittu-malli")
        self.assertFalse(self.ulos.exists())
        self.assertEqual(self.lapsikomennot, [])

    def test_vanha_pyynto_jonossa(self):
        perus = av.valmistele_perus(self.ulos)
        Path(perus, aj.JONO, "0123456789ab.json").write_text("{}")
        with self.assertRaises(av.VertailuVirhe):
            self.vertaa([sarja()["csv-summa"]])
        self.assertEqual(self.lapsikomennot, [])

    def test_kansiot_vain_omistajalle(self):
        perus = av.valmistele_perus(self.ulos)
        for p in (self.ulos, Path(perus), *(Path(perus, n) for n in (aj.JONO, aj.KASITELLYT, aj.TYO, aj.TULOKSET))):
            self.assertEqual(p.stat().st_mode & 0o077, 0, p)


def _rivi(**kw):
    r = {"tehtava": "a", "injektio": False, "tila": am.VALMIS, "arvio": am.HYVAKSYTTY, "valmisehto": True,
         "turva_ok": True, "askelia": 4, "kutsuja": 6, "kulu_eur": 0.01, "kulu_tuntematon": False, "kesto_s": 30.0}
    r.update(kw)
    return r


class YhteenvetoTesti(unittest.TestCase):
    def test_laskenta(self):
        rivit = [_rivi(),
                 _rivi(valmisehto=False, askelia=8, kutsuja=10, kulu_eur=0.03, kesto_s=60.0),       # hyvaksyi vaarin
                 _rivi(tila=am.KESKEN, arvio=am.HYLATTY, valmisehto=True, askelia=6, kutsuja=8),      # hylkasi oikean
                 _rivi(tila=am.KESKEYTETTY, arvio="", valmisehto=False, askelia=0, kutsuja=1, kulu_eur=0.0,
                       kulu_tuntematon=True, kesto_s=0.0),
                 _rivi(injektio=True, turva_ok=True, arvio=am.KORJATTAVA, tila=am.KESKEN, valmisehto=False)]
        yv = av.yhteenveto(rivit, 7)
        self.assertEqual((yv["tehtavia"], yv["ajettu"], yv["valmisehto_lapi"], yv["valmistumis_pros"]), (7, 5, 2, 40.0))
        self.assertEqual((yv["tila_valmis"], yv["tarkastettu"], yv["tarkastaja_hylkasi"]), (2, 4, 2))
        self.assertEqual((yv["hyvaksyi_vaarin"], yv["hylkasi_oikean"]), (1, 1))
        self.assertEqual((yv["askelia_ka"], yv["kutsuja_ka"]), (4.4, 6.2))
        self.assertEqual((yv["kulu_eur"], yv["kulu_eur_ka"], yv["kulu_tuntematon"]), (0.06, 0.012, 1))
        self.assertEqual(yv["kesto_s_ka"], 30.0)
        self.assertEqual(yv["tilat"], {am.VALMIS: 2, am.KESKEN: 2, am.KESKEYTETTY: 1, am.VIRHE: 0})
        self.assertEqual(yv["injektio"], av.TORJUTTU)
        self.assertEqual(av.yhteenveto([_rivi(injektio=True, turva_ok=False)])["injektio"], av.TOTTELI)

    def test_tyhja(self):
        yv = av.yhteenveto([], 7)
        self.assertEqual((yv["ajettu"], yv["valmistumis_pros"], yv["kulu_eur_ka"], yv["injektio"]), (0, 0.0, 0.0, None))

    def test_taulukko(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ulos = Path(d)
            heikko = dict(av.yhteenveto([_rivi(valmisehto=False)], 7), ajo="2026-12-01T10:00:00+00:00",
                          roolijako="a", pysahtyi=None, ajamatta=[])
            vahva = dict(av.yhteenveto([_rivi(injektio=True)], 7), ajo="2026-12-01T11:00:00+00:00",
                         roolijako="b", pysahtyi=av.PYSAHTYI_KATTO, ajamatta=[])
            (ulos / av.AJOT).write_text("".join(json.dumps(x) + "\n" for x in (heikko, vahva)))
            rivit = av.taulukko(ulos).splitlines()
        self.assertTrue(rivit[0].startswith("| Roolijako | Valmisehto |"))
        self.assertEqual(rivit[0].count("|"), rivit[1].count("|"))
        self.assertTrue(rivit[2].startswith("| b | 1/7 (100 %) | 1/1 | 0/1 | 0 | torjuttu |"))
        self.assertTrue(rivit[2].endswith("| paivakatto |"))
        self.assertTrue(rivit[3].startswith("| a | 0/7 (0 %) |"))
        self.assertEqual(len(rivit), 4)


class MainTesti(VertailuPohja):
    def aja_main(self, *argv):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = av.main(list(argv))
        return rc, out.getvalue()

    def test_kelvoton_malli(self):
        rc, out = self.aja_main("aja", "--ulos", str(self.ulos), "--roolit", "ei/sallittu")
        self.assertEqual(rc, 2)
        self.assertIn("asetusvirhe", out)
        self.assertFalse(self.ulos.exists())

    def test_tuntematon_tehtava(self):
        rc, _ = self.aja_main("aja", "--ulos", str(self.ulos), "--roolit", MALLI, "--tehtavat", "olematon")
        self.assertEqual(rc, 2)

    def test_kulukirjanpito_ei_kaytettavissa(self):
        with mock.patch.object(aj, "tarkista_kulukirjanpito", return_value=["kulukirjanpito: x"]):
            rc, out = self.aja_main("aja", "--ulos", str(self.ulos), "--roolit", MALLI)
        self.assertEqual((rc, out.strip()), (1, "VIRHE kulukirjanpito: x"))
        self.assertFalse(self.ulos.exists())

    def test_aja_ja_taulukko(self):
        self.katto_tayteen()
        with mock.patch.object(aj, "tarkista_kulukirjanpito", return_value=[]), \
                mock.patch.object(aj, "aja_aliprosessina", side_effect=self._aliprosessi):
            rc, out = self.aja_main("aja", "--ulos", str(self.ulos), "--roolit", MALLI, "--tehtavat", "kooste,injektio")
        self.assertEqual(rc, 1)                                         # pysahtyi paivakattoon
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["pysahtyi"], av.PYSAHTYI_KATTO)
        rc, out = self.aja_main("taulukko", "--ulos", str(self.ulos))
        self.assertEqual(rc, 0)
        self.assertIn("| paivakatto |", out)


class RakenneTesti(unittest.TestCase):
    def test_ei_suoraa_mallikutsua_ja_ascii(self):
        lahde = Path(av.__file__).read_text(encoding="ascii")
        tuodut = set()
        for n in ast.walk(ast.parse(lahde, feature_version=(3, 10))):
            if isinstance(n, ast.ImportFrom):
                tuodut.update(a.name for a in n.names)
                tuodut.add(n.module or "")
            elif isinstance(n, ast.Import):
                tuodut.update(a.name for a in n.names)
        for kielletty in ("openrouter_kulut", "openrouter_zdr", "yksityinen_or", "urllib", "http", "socket"):
            self.assertNotIn(kielletty, tuodut)
        Path(__file__).read_text(encoding="ascii")


if __name__ == "__main__":
    unittest.main()
