import os
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timezone

from agentti import talonmies as t

NYT = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
PVM = "2026-11-02"
ISANTA = """## Kontit (pct list)
VMID Status Lock Name
115 running agentscope

## Viimeisimmat varmuuskopiot
total 12
-rw-r--r-- 1 root root 1234 Nov  2 03:00 vzdump-lxc-115-2026_11_02-03_00_01.tar.zst
-rw-r--r-- 1 root root  100 Nov  2 03:00 vzdump-lxc-115-2026_11_02-03_00_01.log
-rw-r--r-- 1 root root 1234 Oct 30 03:00 vzdump-lxc-115-2026_10_30-03_00_01.tar.zst

## CT116 epaonnistuneet palvelut
foo.service loaded failed failed Foo

## CT117 epaonnistuneet palvelut
(ei epaonnistuneita palveluita)
"""


def tekoinen(vastaukset):
    kutsut = []

    def suorita(komento, **kw):
        kutsut.append(list(komento))
        return subprocess.CompletedProcess(komento, 0, stdout=vastaukset.get(komento[0], ""), stderr="")
    suorita.kutsut = kutsut
    return suorita


class AnalyysiTesti(unittest.TestCase):
    def test_kaatunut_palvelu_ja_kohina(self):
        teksti = "* foo.service loaded failed failed Foo\n  tmp.mount loaded failed failed Tmp\n"
        e = t.ehdota_kaatuneet(teksti, PVM)
        self.assertEqual([x["kohde"] for x in e], ["foo.service"])
        self.assertEqual(e[0]["taso"], "A")

    def test_levy_rajat(self):
        df = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 100 %d 10 %d%% /\n"
        self.assertEqual(t.ehdota_levy(df % (79, 79), PVM), [])
        self.assertEqual(t.ehdota_levy(df % (80, 80), PVM)[0]["taso"], "A")
        self.assertEqual(t.ehdota_levy(df % (95, 95), PVM)[0]["taso"], "C")

    def test_varmuuskopio_tuore_ja_vanha(self):
        self.assertAlmostEqual(t.uusin_varmuuskopio_ika(ISANTA, NYT), 9.0, places=1)
        vanha = ISANTA.replace("Nov  2 03:00 vzdump-lxc-115-2026_11_02", "Oct 20 03:00 vzdump-lxc-115-2026_11_02")
        e = t.ehdota_varmuuskopiot(vanha, PVM, NYT, date(2026, 10, 25))
        self.assertEqual([x["toimenpide"] for x in e], ["varmuuskopio-tuoreus"])
        self.assertEqual(t.ehdota_varmuuskopiot(ISANTA, PVM, NYT, date(2026, 10, 25)), [])

    def test_loki_ei_lasketa_varmuuskopioksi_ja_tyhja_halyttaa(self):
        vain_loki = "## Viimeisimmat varmuuskopiot\n-rw-r--r-- 1 root root 1 Nov  2 03:00 vzdump-x.log\n"
        self.assertIsNone(t.uusin_varmuuskopio_ika(vain_loki, NYT))
        e = t.ehdota_varmuuskopiot(vain_loki, PVM, NYT, date(2026, 10, 25))
        self.assertEqual(e[0]["toimenpide"], "varmuuskopio-tuoreus")

    def test_isanta_puuttuu_ja_palautustesti_vanha(self):
        e = t.ehdota_varmuuskopiot(None, PVM, NYT, None)
        self.assertEqual({x["toimenpide"] for x in e}, {"varmuuskopio-tuoreus", "palautustesti"})

    def test_paivitykset(self):
        apt = ("Listing...\nlibssl3/stable-security 3.5 amd64 [upgradable from: 3.4]\n"
               "bash/stable 5.2 amd64 [upgradable from: 5.1]\n")
        e = {x["toimenpide"]: x for x in t.ehdota_paivitykset(apt, PVM)}
        self.assertEqual(e["tietoturvapaivitys"]["taso"], "B")
        self.assertIn("libssl3", e["tietoturvapaivitys"]["syy"])
        self.assertEqual(e["muut-paivitykset"]["taso"], "C")
        self.assertEqual(t.ehdota_paivitykset("Listing...\n", PVM), [])


class AjoTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tila = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.suorita = tekoinen({
            "systemctl": "* a.service loaded failed failed A\n",
            "df": "F 1 2 3 Capacity M\n/dev/x 100 85 15 85% /\n",
            "apt": "Listing...\n"})

    def aja(self, isanta=ISANTA):
        return t.ajo(self.tila, NYT, self.suorita, lambda: isanta)

    def test_ajo_kirjoittaa_raportin_ja_kirjanpidon_ei_tuplaa(self):
        teksti, ehd = self.aja()
        kohteet = {e["kohde"] for e in ehd}
        self.assertIn("a.service", kohteet)
        self.assertIn("ct116:foo.service", kohteet)
        self.assertIn("/", kohteet)
        self.assertTrue(os.path.exists(os.path.join(self.tila, "raportit", PVM + ".md")))
        n = len(t._lue_jsonl(os.path.join(self.tila, t.EHDOTUKSET)))
        self.aja()
        self.assertEqual(len(t._lue_jsonl(os.path.join(self.tila, t.EHDOTUKSET))), n)
        self.assertIn("mit\u00e4\u00e4n ei ole tehty", teksti)

    def test_vain_kiinteat_vain_luku_komennot(self):
        self.aja()
        sallitut = [k for k in t.KOMENNOT.values()]
        for k in self.suorita.kutsut:
            self.assertIn(k, sallitut)
        for k in sallitut:
            self.assertNotIn(k[0], ("rm", "kill", "reboot", "sudo"))
            self.assertFalse({"restart", "start", "stop", "install", "upgrade", "remove"} & set(k[1:2]))

    def test_pysaytystiedosto_estaa_kaiken(self):
        open(os.path.join(self.tila, t.PYSAYTUS), "w").close()
        teksti, ehd = self.aja()
        self.assertEqual(ehd, [])
        self.assertEqual(self.suorita.kutsut, [])
        self.assertFalse(os.path.exists(os.path.join(self.tila, "raportit")))

    def test_isannan_virhe_ei_kaada_ajoa(self):
        def rikki():
            raise RuntimeError("salainen-viesti")
        teksti, ehd = t.ajo(self.tila, NYT, self.suorita, rikki)
        self.assertIn("RuntimeError", teksti)
        self.assertNotIn("salainen-viesti", teksti)


class KirjanpitoTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tila = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.ids = []
        for i in range(4):
            pvm = "2026-11-%02d" % (i + 1)
            e = t.ehdotus(pvm, "kaatunut-palvelu", "x.service", "s", "e", taso="A")
            t._liita(os.path.join(self.tila, t.EHDOTUKSET), e)
            self.ids.append(e["tunniste"])

    def test_paatokset_ja_osuus(self):
        t.paata(self.tila, self.ids[0], "kylla")
        t.paata(self.tila, self.ids[1], "kylla")
        t.paata(self.tila, self.ids[2], "ei", "ei tarpeen\nrivi")
        m = t.mittari(self.tila)["kaatunut-palvelu"]
        self.assertEqual((m["ehdotettu"], m["kylla"], m["ei"], m["vastaamatta"]), (4, 2, 1, 1))
        self.assertAlmostEqual(m["osuus"], 0.667, places=3)
        self.assertFalse(m["kypsa"])    # raja puuttuu: Tony paattaa

    def test_myohempi_paatos_korvaa_ja_kypsyys_vaatii_maaran(self):
        t.paata(self.tila, self.ids[0], "ei")
        t.paata(self.tila, self.ids[0], "kylla")
        for i in (1, 2, 3):
            t.paata(self.tila, self.ids[i], "kylla")
        m = t.mittari(self.tila, raja=0.8, vahintaan=10)["kaatunut-palvelu"]
        self.assertEqual(m["osuus"], 1.0)
        self.assertFalse(m["kypsa"])    # vain 4 paatosta
        self.assertTrue(t.mittari(self.tila, raja=0.8, vahintaan=4)["kaatunut-palvelu"]["kypsa"])

    def test_virheelliset_paatokset(self):
        for tunn, v in (("../x", "kylla"), (self.ids[0], "ehka"), ("20261101-tuntematon-abcdef", "kylla")):
            with self.assertRaises(t.TalonmiesVirhe):
                t.paata(self.tila, tunn, v)


def isanta_poolilla(data, meta):
    return ISANTA + "\n## Thin pool (lvs)\nkonttilevy,konttilevy,twi-aotz--,,%s,%s\n" % (data, meta)


class ThinPoolTesti(unittest.TestCase):
    def test_lvs_osio_jasentyy(self):
        self.assertEqual(t.jasenna_thinpool(isanta_poolilla("10.44", "0.87")), [("konttilevy/konttilevy", 10.44, 0.87)])

    def test_pvesm_vara_antaa_vain_datan(self):
        teksti = "## Tallennustila (pvesm status)\nName Type Status Total Used Available %\nkonttilevy lvmthin active 100 10 90 10.44%\nlocal dir active 1 1 1 5.00%\n"
        self.assertEqual(t.jasenna_thinpool(teksti), [("konttilevy", 10.44, None)])

    def test_roskaa_ei_hyvaksyta(self):
        for rivi in ("a,b,twi,,nan,1", "a,b,twi,,101,1", "a,b,Vwi,,50,50", "a,b,twi,,x,y", "liian,lyhyt"):
            self.assertEqual(t.jasenna_thinpool("## Thin pool (lvs)\n" + rivi + "\n"), [], rivi)

    def test_rajat_ovat_tiukasti_ylittavia_ja_korkeampi_ratkaisee(self):
        for data, meta, odotus in ((10.44, 0.87, "ok"), (60, 0, "ok"), (60.01, 0, "kooste"), (75, 0, "kooste"),
                                   (75.01, 0, "halytys"), (85, 0, "halytys"), (85.01, 0, "kiireellinen"),
                                   (10, 90, "kiireellinen"), (70, None, "kooste")):
            self.assertEqual(t.thinpool_taso(data, meta), odotus, (data, meta))

    def test_ehdotus_vain_ylityksesta_ja_nimi_kertoo_kanavan(self):
        self.assertEqual(t.ehdota_thinpool([("k", 10.44, 0.87)], PVM), [])
        (e,) = t.ehdota_thinpool([("k", 80, 1)], PVM)
        self.assertEqual((e["toimenpide"], e["taso"]), ("thinpool-halytys", "C"))
        self.assertIn("80,00 %", e["syy"])


class ThinPoolAjoTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tila = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.suorita = tekoinen({"systemctl": "", "df": "F 1 2 3 Capacity M\n/dev/x 100 10 90 10% /\n", "apt": "Listing...\n"})

    def aja(self, isanta, halyta=None):
        return t.ajo(self.tila, NYT, self.suorita, lambda: isanta, halyta)

    def test_raportti_nayttaa_poolin_rivin_myos_kun_kaikki_kunnossa(self):
        teksti, ehd = self.aja(isanta_poolilla("10.44", "0.87"))
        self.assertIn("Thin pool konttilevy/konttilevy: data 10,44 %, metadata 0,87 % (taso: ok).", teksti)
        self.assertFalse([e for e in ehd if e["toimenpide"].startswith("thinpool")])

    def test_halytys_lahtee_kerran_ja_sisalto_on_kiintea(self):
        viestit = []
        _, ehd = self.aja(isanta_poolilla("80.00", "1.00"), viestit.append)
        self.aja(isanta_poolilla("80.00", "1.00"), viestit.append)      # saman paivan uusinta ei halyta uudelleen
        self.assertEqual(viestit, [t.HALYTYSOTSIKKO["thinpool-halytys"]])
        self.assertNotRegex(viestit[0], r"\d")

    def test_koosteen_ylitys_ei_halyta_ja_kiireellinen_halyttaa(self):
        viestit = []
        self.aja(isanta_poolilla("65.00", "1.00"), viestit.append)
        self.assertEqual(viestit, [])
        self.aja(isanta_poolilla("90.00", "1.00"), viestit.append)
        self.assertEqual(viestit, [t.HALYTYSOTSIKKO["thinpool-kiireellinen"]])

    def test_ilman_halyta_callbackia_ei_lahteta_mitaan(self):
        _, ehd = self.aja(isanta_poolilla("90.00", "1.00"))
        self.assertTrue(any(e["toimenpide"] == "thinpool-kiireellinen" for e in ehd))

    def test_puuttuva_lvs_osio_nakyy_huomautuksena(self):
        teksti, _ = self.aja(ISANTA)
        self.assertIn("thin pool: ei luettavissa", teksti)

    def test_isanta_ei_luettavissa_nakyy_tilassa(self):
        def rikki():
            raise RuntimeError("x")
        teksti, _ = t.ajo(self.tila, NYT, self.suorita, rikki)
        self.assertIn("Thin pool: is\u00e4nn\u00e4n tilaa ei saatu luettua.", teksti)


def listaus(*nimet):
    """pct listsnapshot -tulosteen muoto: ensimmainen `-> ja sisennetyt jatkot, lopussa current."""
    rivit = []
    for i, (nimi, aika) in enumerate(nimet):
        rivit.append(" " * i + "`-> %-28s %s     kuvaus" % (nimi, aika))
    rivit.append(" " * len(nimet) + "`-> current                                           You are here!")
    return "\n".join(rivit)


class SnapshotSiivousTesti(unittest.TestCase):
    def teksti(self):
        kuusi = [("perus-toimii", "2026-10-01 10:00:00")] + [("ennen-a%d" % i, "2026-10-%02d 10:00:00" % (i + 2)) for i in range(5)]
        return ("## CT110\n" + listaus(*kuusi) + "\n\n## CT111\n" + listaus(("perus-toimii", "2026-10-01 10:00:00"), ("ennen-x", "2026-10-02 10:00:00")) + "\n")

    def test_vain_yli_viiden_kontit_ja_sailytys(self):
        (e,) = t.ehdota_snapshot_siivous(t.jasenna_snapshotit(self.teksti()))
        self.assertEqual(e["ct"], "110")
        self.assertEqual(e["yhteensa"], 6)
        self.assertEqual(e["sailyta"], ["perus-toimii", "ennen-a3", "ennen-a4"])
        self.assertEqual(e["poista"], ["ennen-a0", "ennen-a1", "ennen-a2"])

    def test_tasan_viisi_ei_ehdoteta(self):
        v = [("perus-toimii", "2026-10-01 10:00:00")] + [("n%d" % i, "2026-10-%02d 10:00:00" % (i + 2)) for i in range(4)]
        self.assertEqual(t.ehdota_snapshot_siivous(t.jasenna_snapshotit("## CT112\n" + listaus(*v))), [])

    def test_ilman_perus_toimii_ei_poistoja(self):
        v = [("n%d" % i, "2026-10-%02d 10:00:00" % (i + 1)) for i in range(7)]
        (e,) = t.ehdota_snapshot_siivous(t.jasenna_snapshotit("## CT113\n" + listaus(*v)))
        self.assertEqual(e["poista"], [])
        self.assertIn("perus-toimii", e["varoitus"])

    def test_jarjestys_ajasta_ei_listauksen_jarjestyksesta(self):
        v = [("perus-toimii", "2026-10-01 10:00:00"), ("uusin", "2026-10-30 10:00:00"), ("a", "2026-10-02 10:00:00"),
             ("b", "2026-10-03 10:00:00"), ("c", "2026-10-04 10:00:00"), ("toiseksi", "2026-10-29 10:00:00")]
        (e,) = t.ehdota_snapshot_siivous(t.jasenna_snapshotit("## CT114\n" + listaus(*v)))
        self.assertEqual(e["sailyta"], ["perus-toimii", "toiseksi", "uusin"])

    def test_vaara_nimi_ei_paase_komentoihin(self):
        vaara = "## CT110\n`-> a;rm  2026-10-01 10:00:00\n`-> $(x) 2026-10-01 10:00:00\n"
        self.assertEqual(t.jasenna_snapshotit(vaara), {"110": []})

    def test_teksti_sisaltaa_vain_delsnapshot_komennot_ja_cli_toimii(self):
        with tempfile.TemporaryDirectory() as d:
            polku = os.path.join(d, "s.txt")
            with open(polku, "w", encoding="utf-8") as f:
                f.write(self.teksti())
            kutsut = []
            from unittest import mock
            with mock.patch("builtins.print", side_effect=lambda *a, **k: kutsut.append(a[0])):
                self.assertEqual(t.main(["snapshot-siivous", polku]), 0)
                self.assertEqual(t.main(["snapshot-siivous", os.path.join(d, "ei-ole")]), 1)
        ulos = kutsut[0]
        self.assertIn("pct delsnapshot 110 ennen-a0", ulos)
        self.assertNotIn("CT111", ulos)
        for rivi in ulos.splitlines():
            if rivi.startswith("pct "):
                self.assertRegex(rivi, r"^pct delsnapshot \d+ [A-Za-z][A-Za-z0-9_-]*$")
        self.assertIn("Mit\u00e4\u00e4n ei ole poistettu", ulos)
        self.assertIn("VIRHE", kutsut[1])


if __name__ == "__main__":
    unittest.main()
