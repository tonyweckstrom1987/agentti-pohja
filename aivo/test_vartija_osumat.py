"""Vuotovartijan osumaprosentti (aivo/vartija_osumat.py): kirjaus oikeista luovutus- ja pakettifunktioista
tilapaishakemistossa ja laskenta. Vain stdlib.

Osuman maaritelma: esta + hylatty/pala_poistettu/hyvaksytty (muutettu teksti) = osuma; sallittu + hyvaksytty = osuma;
sallittu + hylatty/pala_poistettu = ohi; kysy ei lasketa osumaprosenttiin (maara erikseen). Aiempi esto periytyy
vartijan arvioksi vain hyvaksynnassa; hylkayksessa ja palan poistossa pidetaan vartijan oma arvio (muutettu=true)."""
import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aivo import luovutus, luovutuspaketti as lp, vartija, vartija_osumat as vo, yksityinen
from aivo.test_luovutus import LuovutusPohja
from aivo.yksityinen import YksityinenVirhe

# Mekaaninen estotyyppi "yksityinen IP-osoite": link-local 169.254.0.0/16 (RFC 3927) on vartijan
# YKSITYISET_VERKOT-alueella, mutta se ei ole kotiverkon osoite eika test_repo kiella sita. Dokumentaatioalueen
# osoite (RFC 5737, esim. 192.0.2.10) ei kelpaa: vartija luokittelee sen tyypiksi "IP-osoite" eli kysy, ei esto.
ESTO = "Palvelin 169.254.0.50 vastaa.\n"
TAVALLINEN = "Tavallinen teksti ilman tunnisteita.\n"


def turvallinen(teksti, sha):
    return {"tulos": "turvallinen", "syyt": []}


def kysy(teksti, sha):
    return {"tulos": "kysy", "syyt": ["tarkista"]}


def sallii(teksti):
    return {"tila": "tarkistettu", "sallittu": True, "merkinnat": []}


def julkaise(*a, **k):
    return "ei-gitissa"


class Pohja(unittest.TestCase):
    def setUp(self):
        self.juuri = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.juuri, True)
        self.luov = os.path.join(self.juuri, "luovutukset")
        self.kirjasto = os.path.join(self.juuri, "kirjasto")
        os.mkdir(self.luov, 0o700)
        os.mkdir(self.kirjasto, 0o700)
        self.listat = os.path.join(self.juuri, "listat")     # tyhjat vartijan listat

    def rivit(self):
        return [d for d in vo.lue(self.luov) if d.get("tapahtuma") == "ratkaisu"]

    def ehdotus(self, teksti):
        t = luovutus.tallenna_ehdotus(self.luov, teksti, "viesti")
        return t, luovutus.lue_ehdotus(self.luov, t)["sha"]


class LuovutusKirjausTesti(Pohja):
    def test_hyvaksynta_sallittu_on_osuma(self):
        t, sha = self.ehdotus(TAVALLINEN)
        luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, tarkistus=turvallinen)
        [r] = self.rivit()
        self.assertEqual((r["lahde"], r["tunnus"], r["vartija"], r["ratkaisu"]), ("luovutus", t, "sallittu", "hyvaksytty"))
        self.assertEqual(set(r), {"aika", "lahde", "tunnus", "tapahtuma", "vartija", "ratkaisu"})
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "osuma")

    def test_rivissa_ei_tekstia_eika_shata(self):
        t, sha = self.ehdotus(TAVALLINEN)
        luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, tarkistus=turvallinen)
        raaka = (Path(self.luov) / vo.TIEDOSTO).read_text()
        self.assertNotIn("Tavallinen", raaka)
        self.assertNotIn(sha[:8], raaka)

    def test_hyvaksynta_kysy_lisavahvistuksella(self):
        t, sha = self.ehdotus(TAVALLINEN)
        luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, tarkistus=kysy,
                         lisavahvistus="tarkistin kohdat " + sha[:8])
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"]), ("kysy", "hyvaksytty"))

    def test_kysy_ilman_lisavahvistusta_ei_ole_ratkaisu(self):
        t, sha = self.ehdotus(TAVALLINEN)
        with self.assertRaises(luovutus.VartijaEsto):
            luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, tarkistus=kysy)
        self.assertEqual(vo.lue(self.luov), [])

    def test_hylkays_sallittu_on_ohi(self):
        t, _ = self.ehdotus(TAVALLINEN)
        luovutus.hylkaa_ehdotus(self.luov, t, tarkistus=turvallinen)
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"]), ("sallittu", "hylatty"))
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "ohi")

    def test_hylkays_ilman_tarkistusta_on_kysy(self):
        t, _ = self.ehdotus(TAVALLINEN)
        luovutus.hylkaa_ehdotus(self.luov, t)
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"]), ("kysy", "hylatty"))

    def test_hylkays_esta_on_osuma(self):
        t, _ = self.ehdotus(ESTO)
        luovutus.hylkaa_ehdotus(self.luov, t, tarkistus=turvallinen)      # mekaaninen esto ohittaa tarkistuksen
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"]), ("esta", "hylatty"))

    def test_esta_ja_muutetun_tekstin_hyvaksynta_on_osuma(self):
        t, sha = self.ehdotus(ESTO)
        with self.assertRaises(luovutus.VartijaEsto):
            luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, tarkistus=turvallinen)
        self.assertEqual(self.rivit(), [])
        uusi = luovutus.muokkaa_ehdotus(self.luov, t, TAVALLINEN)
        luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + uusi[:8], self.kirjasto, julkaise=julkaise, tarkistus=turvallinen)
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"], r.get("muutettu")), ("esta", "hyvaksytty", True))
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "osuma")

    def test_esta_ja_muutetun_tekstin_hylkays_sallittuna_on_ohi(self):
        t, sha = self.ehdotus(ESTO)
        with self.assertRaises(luovutus.VartijaEsto):
            luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, tarkistus=turvallinen)
        luovutus.muokkaa_ehdotus(self.luov, t, TAVALLINEN)
        luovutus.hylkaa_ehdotus(self.luov, t, tarkistus=turvallinen)
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"], r.get("muutettu")), ("sallittu", "hylatty", True))
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "ohi")

    def test_julkaisun_virhe_ei_kirjaa_ratkaisua(self):
        t, sha = self.ehdotus(TAVALLINEN)

        def kaatuu(*a, **k):
            raise OSError("x")
        with self.assertRaises(OSError):
            luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=kaatuu, tarkistus=turvallinen)
        self.assertEqual(self.rivit(), [])

    def test_tiedoston_oikeudet_0600(self):
        t, _ = self.ehdotus(TAVALLINEN)
        luovutus.hylkaa_ehdotus(self.luov, t)
        self.assertEqual(stat.S_IMODE(os.stat(Path(self.luov) / vo.TIEDOSTO).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.luov).st_mode), 0o700)


class KirjausVirheTesti(Pohja):
    def test_kirjauksen_virhe_ei_esta_hyvaksyntaa_eika_hylkaysta(self):
        t, sha = self.ehdotus(TAVALLINEN)
        t2, _ = self.ehdotus(TAVALLINEN)
        virhe = io.StringIO()
        with mock.patch.object(vo, "_kirjoita", side_effect=PermissionError("salainen-polku")), \
                contextlib.redirect_stderr(virhe):
            tulos = luovutus.hyvaksy(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise,
                                     tarkistus=turvallinen)
            luovutus.hylkaa_ehdotus(self.luov, t2, tarkistus=turvallinen)
        self.assertEqual(tulos, "ei-gitissa")
        self.assertTrue((Path(self.kirjasto) / luovutus.SAAPUNEET / (t + ".md")).is_file())
        self.assertTrue((Path(self.luov) / "hylatyt" / (t2 + ".json")).is_file())
        self.assertIn("PermissionError", virhe.getvalue())
        self.assertNotIn("salainen-polku", virhe.getvalue())

    def test_vartijan_arvion_virhe_ei_esta_hylkaysta(self):
        t, _ = self.ehdotus(TAVALLINEN)
        with mock.patch.object(luovutus, "vartijan_paatos", side_effect=RuntimeError("x")), \
                contextlib.redirect_stderr(io.StringIO()):
            luovutus.hylkaa_ehdotus(self.luov, t)
        self.assertTrue((Path(self.luov) / "hylatyt" / (t + ".json")).is_file())
        self.assertEqual(self.rivit(), [])


class PakettiKirjausTesti(Pohja):
    def paketti(self, palat, vartija=lp.vartija_koukku):
        return lp.tallenna_paketti(self.luov, "Testi", palat, vartija=vartija, vartija_listat=self.listat)

    def test_palan_poisto_esta_on_osuma(self):
        t = self.paketti([(1, TAVALLINEN), (2, ESTO)])
        d = lp.lue_paketti(self.luov, t)
        lp.poista_pala(self.luov, t, 2, d["sha"][:8])
        [r] = self.rivit()
        self.assertEqual((r["lahde"], r["tunnus"], r["pala"], r["vartija"], r["ratkaisu"]),
                         ("paketti", t, 2, "esta", "pala_poistettu"))
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "osuma")

    def test_palan_poisto_sallittu_on_ohi(self):
        t = self.paketti([(1, TAVALLINEN), (2, TAVALLINEN)], vartija=sallii)
        lp.poista_pala(self.luov, t, 1, lp.lue_paketti(self.luov, t)["sha"][:8])
        [r] = self.rivit()
        self.assertEqual((r["pala"], r["vartija"], r["ratkaisu"]), (1, "sallittu", "pala_poistettu"))
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "ohi")

    def test_paketin_hylkays_kirjaa_jokaisen_palan(self):
        t = self.paketti([(1, TAVALLINEN), (2, ESTO)])
        lp.hylkaa_paketti(self.luov, t)
        r = sorted(self.rivit(), key=lambda x: x["pala"])
        self.assertEqual([(x["pala"], x["vartija"], x["ratkaisu"]) for x in r], [(1, "kysy", "hylatty"), (2, "esta", "hylatty")])

    def test_paketin_hyvaksynta_kysy_lisavahvistuksella(self):
        t = self.paketti([(1, TAVALLINEN), (2, TAVALLINEN)])
        sha = lp.lue_paketti(self.luov, t)["sha"]
        lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise,
                           lisavahvistus="tarkistin kohdat " + sha[:8], vartija_listat=self.listat)
        self.assertEqual(sorted((x["pala"], x["vartija"], x["ratkaisu"]) for x in self.rivit()),
                         [(1, "kysy", "hyvaksytty"), (2, "kysy", "hyvaksytty")])

    def test_paketin_hyvaksynta_sallittu_on_osuma(self):
        t = self.paketti([(1, TAVALLINEN)], vartija=sallii)
        sha = lp.lue_paketti(self.luov, t)["sha"]
        lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, vartija=sallii,
                           vartija_listat=self.listat)
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"]), ("sallittu", "hyvaksytty"))

    def test_estetty_paketti_ei_kirjaa_ratkaisua(self):
        t = self.paketti([(1, ESTO)])
        sha = lp.lue_paketti(self.luov, t)["sha"]
        with self.assertRaises(luovutus.VartijaEsto):
            lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise,
                               lisavahvistus="tarkistin kohdat " + sha[:8], vartija_listat=self.listat)
        self.assertEqual(self.rivit(), [])

    def estot(self):
        return [d for d in vo.lue(self.luov) if d.get("tapahtuma") == "esto"]

    def test_estetty_paketti_kirjaa_eston_jokaiselle_estetylle_palalle(self):
        t = self.paketti([(1, TAVALLINEN), (2, ESTO), (3, ESTO)], vartija=sallii)
        sha = lp.lue_paketti(self.luov, t)["sha"]
        with self.assertRaises(luovutus.VartijaEsto):
            lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, vartija=sallii,
                               vartija_listat=self.listat)
        self.assertEqual(sorted((d["lahde"], d["tunnus"], d["pala"], d["vartija"]) for d in self.estot()),
                         [("paketti", t, 2, "esta"), ("paketti", t, 3, "esta")])
        self.assertEqual(vo.laske(vo.lue(self.luov))["laskettu"], 0)       # esto ei ole Tonyn ratkaisu

    def test_paketin_esto_periytyy_palan_poistoon(self):
        # palan tallennettu vartijan tulos on sallittu, mutta hyvaksynnan mekaaninen tarkistus esti sen; Tony poistaa
        # saman (muuttumattoman) palan -> osuma, ei ohi
        t = self.paketti([(1, TAVALLINEN), (2, ESTO)], vartija=sallii)
        sha = lp.lue_paketti(self.luov, t)["sha"]
        with self.assertRaises(luovutus.VartijaEsto):
            lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, vartija=sallii,
                               vartija_listat=self.listat)
        lp.poista_pala(self.luov, t, 2, sha[:8])
        [r] = self.rivit()
        self.assertEqual((r["pala"], r["vartija"], r["ratkaisu"]), (2, "esta", "pala_poistettu"))
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "osuma")

    def test_paketin_esto_periytyy_hylkaykseen_vain_estetylle_palalle(self):
        t = self.paketti([(1, TAVALLINEN), (2, ESTO)], vartija=sallii)
        sha = lp.lue_paketti(self.luov, t)["sha"]
        with self.assertRaises(luovutus.VartijaEsto):
            lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, vartija=sallii,
                               vartija_listat=self.listat)
        lp.hylkaa_paketti(self.luov, t)
        self.assertEqual(sorted((x["pala"], x["vartija"], x["ratkaisu"]) for x in self.rivit()),
                         [(1, "sallittu", "hylatty"), (2, "esta", "hylatty")])

    def test_automaattinen_hyvaksynta_ei_kirjaa_estoa(self):
        t = self.paketti([(1, ESTO)], vartija=sallii)
        sha = lp.lue_paketti(self.luov, t)["sha"]
        with self.assertRaises(luovutus.VartijaEsto):
            lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, vartija=sallii,
                               vartija_listat=self.listat, kirjaa_osumat=False)
        self.assertEqual(vo.lue(self.luov), [])

    def test_automaattinen_lisapala_ei_ole_tonyn_ratkaisu(self):
        t = self.paketti([(1, TAVALLINEN)], vartija=sallii)
        sha = lp.lue_paketti(self.luov, t)["sha"]
        lp.hyvaksy_paketti(self.luov, t, "kyll\u00e4 " + sha[:8], self.kirjasto, julkaise=julkaise, vartija=sallii,
                           vartija_listat=self.listat)
        ennen = len(self.rivit())
        _, lahti, _ = lp.tallenna_lisapalat(self.luov, self.kirjasto, t, ["Lis\u00e4.\n"], tila="automaattinen",
                                            julkaise=julkaise, vartija=sallii, vartija_listat=self.listat)
        self.assertTrue(lahti)
        self.assertEqual(len(self.rivit()), ennen)

    def test_kirjauksen_virhe_ei_esta_palan_poistoa(self):
        t = self.paketti([(1, TAVALLINEN), (2, TAVALLINEN)])
        with mock.patch.object(vo, "_kirjoita", side_effect=OSError("x")), contextlib.redirect_stderr(io.StringIO()):
            d = lp.poista_pala(self.luov, t, 1, lp.lue_paketti(self.luov, t)["sha"][:8])
            lp.hylkaa_paketti(self.luov, t)
        self.assertEqual([p["n"] for p in d["palat"]], [2])
        self.assertEqual(lp.listaa_paketit(self.luov), [])

    def test_kytkimen_oletus_ennallaan(self):
        self.assertEqual(lp.LISAPALAT_OLETUS, "kysy")
        self.assertEqual(lp.LISAPALAT_ARVOT, ("kysy", "automaattinen"))
        self.assertEqual((lp.PALOJA_ENINTAAN, lp.PAKETTI_YLARAJA), (20, 40_000))


class LaskentaTesti(Pohja):
    def kirjaa(self, n, vartija, ratkaisu, lahde="luovutus"):
        for i in range(n):
            self.assertTrue(vo.kirjaa_ratkaisu(self.luov, lahde, "%s-%d" % (vartija, i), vartija, ratkaisu))

    def test_esto_ja_sallittu_hylkays_on_ohi(self):
        vo.kirjaa_esto(self.luov, "luovutus", "t1")
        vo.kirjaa_ratkaisu(self.luov, "luovutus", "t1", "sallittu", "hylatty")
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"], r.get("muutettu")), ("sallittu", "hylatty", True))
        self.assertEqual(vo.laske(vo.lue(self.luov))["ohi"], 1)

    def test_esto_ja_sallittu_hyvaksynta_on_osuma(self):
        vo.kirjaa_esto(self.luov, "luovutus", "t1")
        vo.kirjaa_ratkaisu(self.luov, "luovutus", "t1", "sallittu", "hyvaksytty")
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r["ratkaisu"], r.get("muutettu")), ("esta", "hyvaksytty", True))
        self.assertEqual(vo.laske(vo.lue(self.luov))["osuma"], 1)

    def test_esto_ja_sallitun_palan_poisto_on_ohi(self):
        vo.kirjaa_esto(self.luov, "paketti", "p1", pala=2)
        vo.kirjaa_ratkaisu(self.luov, "paketti", "p1", "sallittu", "pala_poistettu", pala=2)
        vo.kirjaa_ratkaisu(self.luov, "paketti", "p1", "sallittu", "pala_poistettu", pala=1)   # eri pala: ei estoa
        r = {x["pala"]: x for x in self.rivit()}
        self.assertEqual((r[2]["vartija"], r[2]["ratkaisu"], r[2].get("muutettu")), ("sallittu", "pala_poistettu", True))
        self.assertNotIn("muutettu", r[1])
        self.assertEqual(vo.laske(vo.lue(self.luov))["ohi"], 2)

    def test_esto_ja_kysy_hylkays_pysyy_kysyna(self):
        vo.kirjaa_esto(self.luov, "luovutus", "t1")
        vo.kirjaa_ratkaisu(self.luov, "luovutus", "t1", "kysy", "hylatty")
        [r] = self.rivit()
        self.assertEqual((r["vartija"], r.get("muutettu")), ("kysy", True))

    def test_luokittelu(self):
        odotus = {("esta", "hylatty"): "osuma", ("esta", "pala_poistettu"): "osuma", ("esta", "hyvaksytty"): "osuma",
                  ("sallittu", "hyvaksytty"): "osuma", ("sallittu", "hylatty"): "ohi",
                  ("sallittu", "pala_poistettu"): "ohi", ("kysy", "hyvaksytty"): "kysy", ("kysy", "hylatty"): "kysy",
                  ("tuntematon", "hylatty"): None, ("sallittu", "muu"): None}
        for (v, r), o in odotus.items():
            self.assertEqual(vo.luokittele(v, r), o, (v, r))

    def test_tuntematon_arvo_hylataan(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(vo.kirjaa_ratkaisu(self.luov, "luovutus", "x", "turvallinen", "hyvaksytty"))
        self.assertEqual(vo.lue(self.luov), [])

    def test_raja_ei_tayty_alle_sadan(self):
        self.kirjaa(10, "sallittu", "hyvaksytty")
        self.kirjaa(3, "kysy", "hylatty")
        t = vo.laske(vo.lue(self.luov))
        self.assertEqual((t["laskettu"], t["osuma"], t["kysy"], t["prosentti"]), (10, 10, 3, 100.0))
        self.assertFalse(t["raja"]["taytetty"])
        self.assertIn("p\u00e4\u00e4t\u00f6ksi\u00e4 10/100", t["raja"]["puuttuu"])

    def test_raja_tayttyy_ja_ei_tayty_prosentilla(self):
        self.kirjaa(85, "sallittu", "hyvaksytty")
        self.kirjaa(6, "esta", "hylatty", "paketti")
        self.kirjaa(9, "sallittu", "hylatty")
        t = vo.laske(vo.lue(self.luov))
        self.assertEqual((t["laskettu"], t["osuma"], t["ohi"], t["prosentti"]), (100, 91, 9, 91.0))
        self.assertTrue(t["raja"]["taytetty"])
        self.assertEqual(t["lahteittain"]["paketti"]["osuma"], 6)
        self.kirjaa(2, "sallittu", "pala_poistettu", "paketti")
        t = vo.laske(vo.lue(self.luov))
        self.assertEqual(t["prosentti"], 89.2)
        self.assertFalse(t["raja"]["taytetty"])

    def test_tyhja_ja_rikkinainen_tiedosto(self):
        self.assertEqual(vo.laske(vo.lue(self.luov))["prosentti"], None)
        (Path(self.luov) / vo.TIEDOSTO).write_text('ei-jsonia\n[1]\n{"tapahtuma": "esto", "vartija": "esta"}\n')
        t = vo.laske(vo.lue(self.luov))
        self.assertEqual((t["laskettu"], t["kysy"]), (0, 0))

    def test_komento_tulostaa_maarat_ja_rajan(self):
        self.kirjaa(3, "sallittu", "hyvaksytty")
        self.kirjaa(1, "esta", "pala_poistettu", "paketti")
        self.kirjaa(1, "sallittu", "hylatty")
        self.kirjaa(2, "kysy", "hyvaksytty")
        ulos = io.StringIO()
        with contextlib.redirect_stdout(ulos):
            self.assertEqual(vo.main(["--luovutukset", self.luov]), 0)
        s = ulos.getvalue()
        self.assertIn("Lasketut p\u00e4\u00e4t\u00f6kset: 5 (osuma 4, ohi 1)", s)
        self.assertIn("Osumaprosentti: 80,0 %", s)
        self.assertIn("Kysy (ei laskettu): 2", s)
        self.assertIn("ei t\u00e4yty (p\u00e4\u00e4t\u00f6ksi\u00e4 5/100, osumaprosentti alle 90 %)", s)
        ulos = io.StringIO()
        with contextlib.redirect_stdout(ulos):
            vo.main(["--luovutukset", self.luov, "--json"])
        d = json.loads(ulos.getvalue())
        self.assertEqual((d["laskettu"], d["raja"]["paatokset"], d["raja"]["prosentti"]), (5, 100, 90))

    def test_lahde_kentta_sallii_uudet_lahteet(self):
        self.assertTrue(vo.kirjaa_ratkaisu(self.luov, "haku", "q1", "sallittu", "hyvaksytty"))
        self.assertEqual(vo.laske(vo.lue(self.luov))["lahteittain"]["haku"]["osuma"], 1)



class ChatHylkaysTesti(LuovutusPohja):
    """aivo/chat.py _luovutus_hylkaa: hylkays valittaa vartijan tarkistuksen (tila.luovutuksen_tarkistus), joten
    tallennettu vartijamallin tulos samasta sha:sta nakyy osumarivilla. Ilman tarkistusta arvio olisi aina kysy."""

    def test_hylkays_chatista_kirjaa_vartijan_arvion(self):
        kid = self.uusi()
        teksti = "Toteuta mittari X.\n"
        t, _ = self.ehdota_luovutus(teksti)
        vartija.tallenna_tulos(self.tila.luovutukset, t, {"sha": yksityinen.sha(teksti), "tila": vartija.TILA_TARKISTETTU,
                                                          "arvio": "ei", "merkinnat": []})
        s, d = self.pyynto("POST", "/api/luovutus/hylkaa", {"keskustelu": kid, "tunnus": t})
        self.assertEqual((s, d["tulos"]), (200, "hylatty"))
        [r] = [x for x in vo.lue(self.tila.luovutukset) if x.get("tapahtuma") == "ratkaisu"]
        self.assertEqual((r["lahde"], r["tunnus"], r["vartija"], r["ratkaisu"]), ("luovutus", t, "sallittu", "hylatty"))
        self.assertEqual(vo.luokittele(r["vartija"], r["ratkaisu"]), "ohi")

    def test_hylkays_chatista_ilman_mallin_tulosta_on_kysy(self):
        kid = self.uusi()
        t, _ = self.ehdota_luovutus()
        self.assertEqual(self.pyynto("POST", "/api/luovutus/hylkaa", {"keskustelu": kid, "tunnus": t})[0], 200)
        [r] = [x for x in vo.lue(self.tila.luovutukset) if x.get("tapahtuma") == "ratkaisu"]
        self.assertEqual(r["vartija"], "kysy")


class EstoIkkunaTesti(Pohja):
    """Eston perinta lukee vain tiedoston loppuosan (ESTO_IKKUNA_TAVUT), ei koko tiedostoa jokaisella ratkaisulla."""

    def tayta(self, maara):
        for i in range(maara):
            vo.kirjaa_ratkaisu(self.luov, "luovutus", "muu%d" % i, "sallittu", "hyvaksytty")

    def test_ikkunan_sisalla_esto_loytyy(self):
        vo.kirjaa_esto(self.luov, "luovutus", "t1")
        self.tayta(5)
        self.assertTrue(vo._oli_esto(self.luov, "luovutus", "t1", None, max_tavut=4096))

    def test_ikkunaa_vanhempi_esto_ei_periydy(self):
        vo.kirjaa_esto(self.luov, "luovutus", "t1")
        self.tayta(50)
        self.assertFalse(vo._oli_esto(self.luov, "luovutus", "t1", None, max_tavut=1000))
        self.assertTrue(vo._oli_esto(self.luov, "luovutus", "t1", None, max_tavut=10 ** 6))

    def test_katkennut_ensimmainen_rivi_ohitetaan(self):
        self.tayta(20)
        koko = (Path(self.luov) / vo.TIEDOSTO).stat().st_size
        rivit = vo._lue_loppu(self.luov, koko // 2)
        self.assertTrue(rivit)
        self.assertTrue(all(d.get("tapahtuma") == "ratkaisu" for d in rivit))
        self.assertEqual(rivit, vo.lue(self.luov)[-len(rivit):])

    def test_puuttuva_tiedosto(self):
        self.assertEqual(vo._lue_loppu(self.luov, 100), [])
        self.assertFalse(vo._oli_esto(self.luov, "luovutus", "t1", None))

    def test_ratkaisu_kayttaa_rajattua_lukua(self):
        vo.kirjaa_esto(self.luov, "luovutus", "t1")
        self.tayta(50)
        with mock.patch.object(vo, "ESTO_IKKUNA_TAVUT", 1000), mock.patch.object(vo, "lue", side_effect=AssertionError):
            self.assertTrue(vo.kirjaa_ratkaisu(self.luov, "luovutus", "t1", "sallittu", "hyvaksytty"))
        r = self.rivit()[-1]
        self.assertEqual((r["tunnus"], r["vartija"]), ("t1", "sallittu"))
        self.assertNotIn("muutettu", r)
        self.assertGreater((Path(self.luov) / vo.TIEDOSTO).stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
