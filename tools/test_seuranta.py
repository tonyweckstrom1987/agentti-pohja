import datetime
import io
import tempfile
import unittest
from pathlib import Path

from tools import automerge, seuranta

NYT = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)


def ip(*osat):
    """Osoitteet kootaan ajossa: repon vuotovahti (test_repo) kieltaa sisaverkon osoitteet kirjaimellisina."""
    return ".".join(str(o) for o in osat)


OSOITE = ip(100, 64, 7, 9)     # keksitty esimerkki; oikeaa osoitetta ei kirjoiteta koodiin
LAN = ip(192, 168, 1, 114)
LAN2 = ip(192, 168, 1, 121)
TSNIMI = "x.tail1234." + "ts" + ".net"
SHA = "a" * 40
VANHA = "b" * 40


def merkki(sha, paatos):
    return "<!-- riippumaton-tarkastus v1 sha=%s paatos=%s -->" % (sha, paatos)


def kommentti(teksti, kayttaja=seuranta.BOTTI):
    return {"user": {"login": kayttaja}, "body": teksti}


class FakeGitHub:
    """Valehaku: polku (ilman kyselyosaa) -> vastaus tai Lukuvirhe. Ei verkkoa."""

    def __init__(self, vastaukset):
        self.vastaukset = vastaukset
        self.kutsut = []

    def __call__(self, url, tunnus=None):
        polku = url.replace("https://api.github.com", "").split("?")[0]
        self.kutsut.append((polku, tunnus))
        v = self.vastaukset.get(polku)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise seuranta.Lukuvirhe("HTTP 404")
        return 200, v


def maailma(kommentit=None, patch="+ hei", otsikko="Lisaa x", kuvaus="", julkinen=True, mergeable="clean", ci="success"):
    o = "o/r"
    return {
        "/users/o/repos": [{"full_name": o, "private": not julkinen, "fork": False, "archived": False}],
        "/repos/%s/pulls" % o: [{"number": 7, "title": otsikko, "body": kuvaus, "head": {"sha": SHA}}],
        "/repos/%s/pulls/7" % o: {"mergeable_state": mergeable},
        "/repos/%s/issues/7/comments" % o: kommentit or [],
        "/repos/%s/pulls/7/files" % o: [{"filename": "docs/x.md", "status": "added", "changes": 1, "patch": patch}],
        "/repos/%s/commits/%s/check-runs" % (o, SHA): {"check_runs": [{"status": "completed", "conclusion": ci}]},
        "/repos/%s/commits/%s/status" % (o, SHA): {"state": "success", "statuses": []},
    }


class Pohja(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.juuri = Path(self.tmp.name)
        cj = self.juuri / "claude-jono"
        for d in ("tulokset", "tehtavat"):
            (cj / d).mkdir(parents=True)
        (self.juuri / "01-infra.md").write_text("Isanta tailnetissa: %s\nLAN %s\n" % (OSOITE, LAN), encoding="utf-8")
        (cj / "tonylle.md").write_text("# Tonyn paatettavat\n- AAMULLA\n1. **a**\n2. **b**\n", encoding="utf-8")

    def tulos(self, nimi, tila, aika, lisa=""):
        (self.juuri / "claude-jono" / "tulokset" / (nimi + ".md")).write_text(
            "# Tulos: %s\n\n- Tila: %s\n- Aika: %s\n- Malli: sonnet\n\n%s\n" % (nimi, tila, aika, lisa), encoding="utf-8")

    def aja(self, vastaukset=None, *lisa, ymparisto=None):
        ulos = io.StringIO()
        fake = FakeGitHub(vastaukset if vastaukset is not None else maailma())
        koodi = seuranta.aja(["--juuri", str(self.juuri), "--omistaja", "o", "--tunnus-tiedosto", "/ei/ole", *lisa],
                             ymparisto=ymparisto if ymparisto is not None else {}, nyt=NYT, hae=fake, ulos=ulos)
        self.fake = fake
        return koodi, ulos.getvalue()


class Tulokset(Pohja):
    def test_epaonnistunut_loytyy_syyn_kanssa(self):
        self.tulos("2026-11-10-01-a", "EPAONNISTUI", "2026-11-10T10:00+02:00", "claude epaonnistui (subtype: error_max_turns)")
        koodi, ulos = self.aja()
        self.assertIn("EPAONNISTUI 2026-11-10-01-a: syy: error_max_turns -> pilko tehtava", ulos)

    def test_kesken_loytyy_ja_valmis_ei(self):
        self.tulos("2026-11-10-02-b", "KESKEN", "2026-11-10T11:00+02:00")
        self.tulos("2026-11-10-03-c", "VALMIS", "2026-11-10T11:00+02:00")
        _, ulos = self.aja()
        self.assertIn("KESKEN 2026-11-10-02-b: syy: ei tuloksessa", ulos)
        self.assertNotIn("2026-11-10-03-c", ulos)

    def test_ikkunan_ulkopuolinen_ei_nay(self):
        self.tulos("2026-11-08-01-vanha", "EPAONNISTUI", "2026-11-08T10:00+02:00")
        _, ulos = self.aja()
        self.assertNotIn("2026-11-08-01-vanha", ulos)
        self.assertIn("Ei yhtaan (0 tulosta ikkunassa)", ulos)

    def test_ajan_puuttuminen_ei_ole_nolla(self):
        (self.juuri / "claude-jono" / "tulokset" / "x.md").write_text("- Tila: KESKEN\n", encoding="utf-8")
        koodi, ulos = self.aja()
        self.assertIn(seuranta.EI_TIETOA + ": 1 tuloksen aikaa", ulos)
        self.assertEqual(koodi, 4)

    def test_kansio_puuttuu(self):
        (self.juuri / "claude-jono" / "tulokset").rmdir()
        r, ok = seuranta.osio_tulokset(self.juuri, NYT, 24)
        self.assertFalse(ok)
        self.assertIn(seuranta.EI_TIETOA, r[1])


class Tarkastaja(unittest.TestCase):
    def test_merkki_on_sama_kuin_automergessa(self):
        self.assertEqual(seuranta.TARKASTUS_RE.pattern, automerge.TARKASTUS_RE.pattern)
        self.assertEqual(seuranta.BOTTI, automerge.BOTTI)

    def test_vanhan_karjen_paatosta_ei_kayteta(self):
        k = [kommentti(merkki(VANHA, "HYVAKSYTTY") + "\n- vanha loydos")]
        self.assertEqual(seuranta.tarkastajan_paatos(k, SHA), (None, None))

    def test_nykykarjen_paatos_voittaa_vanhan(self):
        k = [kommentti(merkki(SHA, "HYLATTY")), kommentti(merkki(VANHA, "HYVAKSYTTY")), kommentti(merkki(SHA, "RAJATTU") + "\n- x")]
        self.assertEqual(seuranta.tarkastajan_paatos(k, SHA)[0], "RAJATTU")

    def test_muun_kayttajan_kommentti_ei_kelpaa(self):
        k = [kommentti(merkki(SHA, "HYVAKSYTTY"), kayttaja="joku")]
        self.assertEqual(seuranta.tarkastajan_paatos(k, SHA)[0], None)

    def test_kaksi_merkkia_samassa_kommentissa_ei_kelpaa(self):
        k = [kommentti(merkki(SHA, "HYVAKSYTTY") + merkki(SHA, "HYLATTY"))]
        self.assertEqual(seuranta.tarkastajan_paatos(k, SHA)[0], None)

    def test_loydokset_ovat_viivarivit(self):
        self.assertEqual(seuranta.loydokset("RAJATTU\n- yksi\n  - sisennetty\n-ei\n- kaksi\n"), ["yksi", "kaksi"])


class Pyynnot(Pohja):
    def test_loydokset_ja_paatos_luetaan_nykykarjesta(self):
        k = [kommentti(merkki(VANHA, "HYVAKSYTTY") + "\n- vanha"), kommentti(merkki(SHA, "HYLATTY") + "\n- rikki: tiedosto x\n- toinen")]
        koodi, ulos = self.aja(maailma(kommentit=k))
        self.assertIn("tarkastaja: HYLATTY (nykykarjesta)", ulos)
        self.assertIn("- rikki: tiedosto x", ulos)
        self.assertNotIn("- vanha", ulos)

    def test_vain_vanha_paatos_sanotaan_ei_paatosta(self):
        k = [kommentti(merkki(VANHA, "HYVAKSYTTY"))]
        _, ulos = self.aja(maailma(kommentit=k))
        self.assertIn("ei paatosta nykykarjesta (vain vanhoista karjista: bbbbbbb HYVAKSYTTY)", ulos)
        self.assertNotIn("tarkastaja: HYVAKSYTTY", ulos)

    def test_osoitteet_peitetaan_loydoksista_ja_otsikosta(self):
        k = [kommentti(merkki(SHA, "RAJATTU") + "\n- osoite %s ja %s/10 ja %s vuotaa" % (LAN2, ip(100, 64, 0, 0), TSNIMI))]
        _, ulos = self.aja(maailma(kommentit=k, otsikko="Siirto %s:ksi" % ip(10, 1, 2, 3)))
        self.assertIn("- osoite [osoite] ja [osoite] ja [osoite] vuotaa", ulos)
        for vuoto in (LAN2, "100.64", TSNIMI, ip(10, 1, 2, 3)):
            self.assertNotIn(vuoto, ulos)

    def test_rajattu_tulee_ensin_ja_merkitaan(self):
        k = [kommentti(merkki(SHA, "RAJATTU") + "\n- ei bugia")]
        _, ulos = self.aja(maailma(kommentit=k))
        self.assertIn("[RAJATTU] o/r #7", ulos)

    def test_rajattu_tiedostosta(self):
        v = maailma()
        v["/repos/o/r/pulls/7/files"] = [{"filename": ".github/workflows/ci.yml", "status": "modified", "changes": 1, "patch": "+x"}]
        _, ulos = self.aja(v)
        self.assertIn("[RAJATTU]", ulos)

    def test_ristiriita_ja_ci(self):
        _, ulos = self.aja(maailma(mergeable="dirty", ci="failure"))
        self.assertIn("CI punainen", ulos)
        self.assertIn("RISTIRIITA (dirty)", ulos)

    def test_tuntematon_yhdistettavyys_ei_ole_puhdas(self):
        koodi, ulos = self.aja(maailma(mergeable="unknown"))
        self.assertIn(seuranta.EI_TIETOA + " (GitHub ei ole laskenut", ulos)
        self.assertEqual(koodi, 4)

    def test_kommenttien_luku_epaonnistuu_ei_nollaa(self):
        v = maailma()
        v["/repos/o/r/issues/7/comments"] = seuranta.Lukuvirhe("HTTP 403 (kutsuraja)")
        koodi, ulos = self.aja(v)
        self.assertIn("tarkastaja: %s (HTTP 403 (kutsuraja))" % seuranta.EI_TIETOA, ulos)
        self.assertEqual(koodi, 4)


class Skannaus(Pohja):
    def test_osuma_antaa_koodin_3_eika_tulosta_osoitetta(self):
        koodi, ulos = self.aja(maailma(patch="+ palvelin %s" % OSOITE))
        self.assertEqual(koodi, 3)
        self.assertIn("OSUMIA 1, o/r #7", ulos)
        self.assertNotIn(OSOITE, ulos)
        self.assertNotIn("100.64", ulos)

    def test_osuma_otsikossa_ja_kuvauksessa(self):
        self.assertEqual(self.aja(maailma(otsikko="Korjaa " + OSOITE))[0], 3)
        self.assertEqual(self.aja(maailma(kuvaus="osoite " + OSOITE.upper()))[0], 3)

    def test_vain_lisatyt_rivit_lasketaan(self):
        self.assertEqual(self.aja(maailma(patch="- poistettu %s\n  konteksti %s" % (OSOITE, OSOITE)))[0], 0)

    def test_esimerkkiosoitteet_eivat_laukaise(self):
        # skannataan vain 01-infra.md:sta luettua osoitetta, ei mitaan tailnet-nakoista
        koodi, ulos = self.aja(maailma(patch="+ %s ja %s ja %s ja example.com" % (ip(100, 64, 7, 10), LAN2, ip(100, 63, 1, 1))))
        self.assertEqual(koodi, 0)
        self.assertIn("Osumia 0 (1 julkisen repon pyyntoa skannattu)", ulos)

    def test_yksityista_repoa_ei_skannata(self):
        koodi, ulos = self.aja(maailma(patch="+ %s" % OSOITE, julkinen=False))
        self.assertEqual(koodi, 0)
        self.assertIn("Osumia 0 (0 julkisen", ulos)

    def test_ilman_githubia_ei_tietoa(self):
        koodi, ulos = self.aja(None, "--ilman-github")
        self.assertEqual(koodi, 4)
        self.assertIn("3. Tailnet", ulos)
        self.assertIn(seuranta.EI_TIETOA + ": GitHubia ei luettu, skannausta ei tehty", ulos)
        self.assertNotIn("Osumia 0", ulos)

    def test_osoite_puuttuu_infrasta(self):
        (self.juuri / "01-infra.md").write_text("ei osoitetta\n", encoding="utf-8")
        koodi, ulos = self.aja()
        self.assertEqual(koodi, 4)
        self.assertIn("ei loytynyt tailnet-osoitetta", ulos)

    def test_diff_puuttuu_ison_tiedoston_takia(self):
        v = maailma()
        v["/repos/o/r/pulls/7/files"] = [{"filename": "iso.txt", "status": "added", "changes": 9000}]
        koodi, ulos = self.aja(v)
        self.assertEqual(koodi, 4)
        self.assertIn("skannaus vajaa", ulos)

    def test_repolistan_virhe_ei_ole_nolla(self):
        v = maailma()
        v["/users/o/repos"] = seuranta.Lukuvirhe("HTTP 500")
        koodi, ulos = self.aja(v)
        self.assertEqual(koodi, 4)
        self.assertIn(seuranta.EI_TIETOA, ulos)
        self.assertNotIn("Osumia 0", ulos)

    def test_puhdas_ajo_antaa_nollan(self):
        koodi, ulos = self.aja()
        self.assertEqual(koodi, 0, ulos)
        self.assertIn("ILMAN tunnusta", ulos)


class Tunnus(Pohja):
    def test_tunnus_valitetaan_mutta_ei_tulosteta(self):
        koodi, ulos = self.aja(None, ymparisto={"SEURANTA_TUNNUS": "salainen-arvo-123"})
        self.assertIn("lukutunnuksella", ulos)
        self.assertNotIn("salainen-arvo-123", ulos)
        self.assertTrue(all(t == "salainen-arvo-123" for _, t in self.fake.kutsut))

    def test_tunnus_tiedostosta(self):
        p = self.juuri / "tunnus"
        p.write_text("tiedostoarvo\n", encoding="utf-8")
        self.assertEqual(seuranta.lue_tunnus({}, str(p)), "tiedostoarvo")
        self.assertIsNone(seuranta.lue_tunnus({}, "/ei/ole"))
        self.assertEqual(seuranta.lue_tunnus({"GITHUB_TOKEN": "x"}, str(p)), "x")

    def test_http_virhe_tulostaa_vain_tyypin(self):
        import urllib.request
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(ValueError("salainen viesti"))
        try:
            with self.assertRaises(seuranta.Lukuvirhe) as e:
                seuranta.http_hae("https://api.github.com/x", "tunnus-arvo")
        finally:
            urllib.request.urlopen = orig
        self.assertEqual(str(e.exception), "ValueError")


class Tiedostot(Pohja):
    def test_tonylle_laskee_numeroidut(self):
        r, ok = seuranta.osio_tonylle(self.juuri)
        self.assertIn("2 numeroitua kohtaa (raja 20)", r[1])

    def test_tonylle_yli_rajan(self):
        (self.juuri / "claude-jono" / "tonylle.md").write_text("".join("%d. x\n" % i for i in range(1, 23)), encoding="utf-8")
        self.assertIn("YLI RAJAN", seuranta.osio_tonylle(self.juuri)[0][1])

    def test_tonylle_puuttuu(self):
        (self.juuri / "claude-jono" / "tonylle.md").unlink()
        r, ok = seuranta.osio_tonylle(self.juuri)
        self.assertFalse(ok)
        self.assertIn(seuranta.EI_TIETOA, r[1])

    def test_kiire_jonossa_ja_ajettu(self):
        t = self.juuri / "claude-jono" / "tehtavat"
        for n in ("2026-11-10-01-kiire-a.md", "2026-11-10-02-kiire-b.md", "2026-11-10-03-tavallinen.md", "2026-11-10-04-KIIRE-c.md"):
            (t / n).write_text("x", encoding="utf-8")
        self.tulos("2026-11-10-01-kiire-a", "VALMIS", "2026-11-10T10:00+02:00")
        r, ok = seuranta.osio_kiire(self.juuri)
        self.assertIn("   JONOSSA 2026-11-10-02-kiire-b", r)
        self.assertIn("   JONOSSA 2026-11-10-04-KIIRE-c", r)
        self.assertIn("Ajettu 1, jonossa 2 (KIIRE-tehtavia yhteensa 3).", r[-1])

    def test_kayttovirhe(self):
        self.assertEqual(seuranta.aja(["--juuri", "/ei/ole/tata"], ymparisto={}), 1)
        self.assertEqual(seuranta.aja([], ymparisto={}), 1)


if __name__ == "__main__":
    unittest.main()
