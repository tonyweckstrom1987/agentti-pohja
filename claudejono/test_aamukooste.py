import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from agentti import ilmoita
from claudejono import aamukooste, viikkoehdotus
from yojono import ajuri as yojono_ajuri

TZ = aamukooste.TZ
NYT = datetime(2026, 11, 3, 6, 30, tzinfo=TZ)


def tulos(nimi, tila, aika, runko=""):
    return f"# Tulos: {nimi}\n\n- Tila: {tila}\n- Aika: {aika}\n\n{runko}\n"



class Pohja(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "docs"
        self.jono = self.repo / "claude-jono"
        (self.jono / "tulokset").mkdir(parents=True)
        (self.jono / "tehtavat").mkdir()
        self.yj = self.tmp / "yojono"
        self.yj.mkdir()

    def kirjoita(self, polku, teksti):
        p = self.jono / polku
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(teksti, encoding="utf-8")


class Kooste(Pohja):
    def setUp(self):
        super().setUp()
        self.kirjoita("tulokset/2026-11-02-01-a.md", tulos("a", "VALMIS", "2026-11-02T20:00+02:00",
                      "PR https://github.com/tonyweckstrom1987/agentti-pohja/pull/7\n\n## Uusi oppi\nX ei onnistu, kayta Y.\n"))
        self.kirjoita("tulokset/2026-11-02-02-b.md", tulos("b", "EPAONNISTUI", "2026-11-03T02:00+02:00", "aikakatkaisu"))
        self.kirjoita("tulokset/2026-10-30-01-vanha.md", tulos("vanha", "VALMIS", "2026-10-30T10:00+02:00",
                      "https://github.com/tonyweckstrom1987/mcp-doctor/pull/1"))
        self.kirjoita("tonylle.md", "# Tonyn paatettavat\n\n## 2026-10-30T10:00+02:00 (CT115)\nvanha\n\n"
                      "## 2026-11-02T22:10+02:00 (CT115)\nuusi 1\n\n## 2026-11-03 klo 05.40 (pilvi-Claude)\nuusi 2\n")
        (self.yj / "tila.json").write_text(json.dumps({
            "kulut": {"2026-11-02": 4.25, "2026-11-03": 0.5, "2026-10-31": 1.0},
            "tehtavat": {"t1": {"pvm": "2026-11-02", "tila": "valmis"}, "t2": {"pvm": "2026-11-02", "tila": "epaonnistui"},
                         "t0": {"pvm": "2026-10-20", "tila": "epaonnistui"}}}))
        (self.yj / "jono.json").write_text(json.dumps({"paivakatto": 5.0, "kuukausikatto": 5.0, "tehtavat": []}))

    def hae(self, url):
        if "/pulls?state=open" in url:
            if "/agentti-pohja/" in url:
                return [{"number": 9, "title": "claude  jono\nmuutos", "html_url": "https://github.com/tonyweckstrom1987/agentti-pohja/pull/9",
                         "head": {"sha": "abc"}, "draft": False}]
            return []
        if "check-runs" in url:
            return {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        if "/pulls/9/files" in url:
            return [{"filename": "yojono/ajuri.py", "additions": 3, "deletions": 1, "status": "modified"}]
        raise AssertionError(url)

    def keraa(self, hae=None, yksikot=None):
        return aamukooste.keraa(self.repo, self.yj, NYT, hae or self.hae, yksikot or (lambda: ["paivakatsaus.service"]))

    def test_rivit_paatokset_ensin_ja_linkit(self):
        teksti = aamukooste.muotoile(self.keraa())
        rivit, linkit = teksti.split("\n\nLinkit:\n")
        rivit = rivit.splitlines()
        self.assertLessEqual(len(rivit), 10)
        self.assertTrue(rivit[0].startswith("P\u00e4\u00e4t\u00f6kset sinulle:"))   # A546: p\u00e4\u00e4t\u00f6kset ensin
        self.assertTrue(all(len(r) <= aamukooste.RIVI_MAX for r in rivit))
        for alku in ("Tehty:", "Kesken", "S\u00e4\u00e4nt\u00f6p\u00e4ivitykset", "Opittu:", "H", "Kulut:"):
            self.assertTrue(any(r.startswith(alku) for r in rivit), alku)
        self.assertIn("https://github.com/tonyweckstrom1987/agentti-pohja/pull/9", linkit)
        self.assertIn("tonylle.md", linkit)

    def test_aivo_valmius_kerrotaan_heti_kun_kaikki_ruudut_rastitettu(self):
        self.assertNotIn("Aivo on valmis", aamukooste.muotoile(self.keraa()))      # tiedostoa ei ole
        osa = "- [x] a\n- [x] b\n- [x] c\n- [x] d\n"
        self.kirjoita("aivo-valmius.md", osa + "- [ ] e\n")
        self.assertNotIn("Aivo on valmis", aamukooste.muotoile(self.keraa()))      # yksi ruutu auki
        rivit_ennen = len(aamukooste.muotoile(self.keraa()).split("\n\nLinkit:\n")[0].splitlines())
        self.kirjoita("aivo-valmius.md", osa + "- [x] e\n")
        teksti = aamukooste.muotoile(self.keraa())
        self.assertIn("Aivo on valmis OpenRouteriin", teksti)
        self.assertEqual(len(teksti.split("\n\nLinkit:\n")[0].splitlines()), rivit_ennen)   # rivimaara ei kasva

    def test_ikkuna_rajaa_vanhat_pois(self):
        t = self.keraa()
        self.assertEqual(sorted(r["nimi"][:13] for r in t["tulokset"]), ["2026-11-02-01", "2026-11-02-02"])
        self.assertEqual(t["uudet_prt"], ["agentti-pohja#7"])   # vanhan tuloksen PR ei mukaan
        self.assertEqual(len(t["tonylle"]), 2)

    def test_sisalto_oikeasta_datasta(self):
        rivit = aamukooste.rivit(self.keraa())
        self.assertIn("1 PR:", rivit[0])
        self.assertNotIn("avointa kohtaa", rivit[0])   # fixturen otsikot eiv\u00e4t ole p\u00e4\u00e4t\u00f6spyynt\u00f6j\u00e4
        # alkuperaisessa fixturessa VALMIS-tuloksella ei ole tarkastajan merkintaa -> portti nayttaa sen keskena
        self.assertIn("2 jonotulosta (0 valmis, 1 kesken (heist\u00e4 1 ilman tarkastajan merkint\u00e4\u00e4), 1 ep", rivit[1])
        self.assertIn("agentti-pohja#7", rivit[1])
        self.assertIn("Kesken (ei tarkastajan vahvistusta): 2026-11-02-01-a", rivit[2])   # VALMIS ilman tarkastajan merkint\u00e4\u00e4
        self.assertIn("ei.", rivit[3])
        self.assertIn("X ei onnistu, kayta Y", rivit[4])
        self.assertIn("failed: paivakatsaus.service", rivit[5])
        self.assertIn("95 %", rivit[5])   # kuukausikulu 4,25 + 0,5 = 4,75 / 5,0
        self.assertIn("4.25 / 5.00 USD", rivit[6])   # ajopaiva vaihtuu klo 9: klo 6.30 on viela 2.11.

    def test_github_virhe_ei_nay_tyhjana_onnistumisena(self):
        def rikki(url):
            raise OSError("verkko")
        t = self.keraa(hae=rikki)
        rivit = aamukooste.rivit(t)
        self.assertIn("tieto puuttuu: PR-lista OSError", rivit[0])
        self.assertNotIn("ei mergattavia", rivit[0])   # puuttuva tieto ei ole aito nolla

    def test_puuttuva_yojono_ja_systemd_ei_kaada(self):
        t = aamukooste.keraa(self.repo, self.tmp / "ei-ole", NYT, self.hae, lambda: None)
        rivit = aamukooste.rivit(t)
        self.assertEqual(len(rivit), 7)
        self.assertIn("systemd-tilaa ei saatu", rivit[5])
        self.assertIn("tilaa ei saatu", rivit[6])

    def test_tyhja_oppi_ei_nay(self):
        self.kirjoita("tulokset/2026-11-03-01-c.md", tulos("c", "VALMIS", "2026-11-03T03:00+02:00", "## Uusi oppi\nTama ajo ei tuottanut uutta oppia.\n"))
        self.assertNotIn("Tama ajo ei", aamukooste.rivit(self.keraa())[4])

    def test_tonylle_aikaleimat(self):
        self.kirjoita("tonylle.md", "## 2026-11-02T05:00+02:00 a\n## 2026-11-02 klo 23.15 b\n## 2026-11-03 c\n## 2026-11-01 d\n")
        uudet = aamukooste.tonylle_uudet(self.repo, NYT - __import__("datetime").timedelta(hours=24))
        self.assertEqual([o for _, o in uudet], ["b", "c"])   # 2.11. klo 5 on yli 24 h vanha; pelkka 3.11. lasketaan uudeksi

    def test_main_kuiva_ei_laheta(self):
        with mock.patch.object(yojono_ajuri, "laheta_telegram") as m, mock.patch.object(aamukooste, "keraa", return_value=self.keraa()):
            with mock.patch("sys.stdout"):
                self.assertEqual(aamukooste.main(["--repo", str(self.repo), "--yojono", str(self.yj)]), 0)
        m.assert_not_called()

    def test_main_laheta_pakottaa(self):
        with mock.patch.object(yojono_ajuri, "laheta_telegram", return_value=True) as m, mock.patch.object(aamukooste, "keraa", return_value=self.keraa()):
            with mock.patch("sys.stdout"):
                self.assertEqual(aamukooste.main(["--laheta"]), 0)
        self.assertIs(m.call_args.kwargs.get("pakota"), True)


class KayttoonottoEro(Pohja):
    """Aamukooste kertoo, kun mainissa on ajureihin ottamattomia yhdistettyja muutoksia (tools/kayttoonotto_ero.py)."""
    def setUp(self):
        super().setUp()
        import subprocess
        # tunnuspolku vaihdetaan olemattomaan valiaikaispolkuun: testi ei saa lukea oikeaa lukutunnusta
        env = mock.patch.dict(os.environ, {"GITHUB_LUKU_TUNNUS_POLKU": str(Path(self.tmp) / "ei-tunnusta.token")})
        env.start()
        self.addCleanup(env.stop)
        self.pohja = Path(self.tmp) / "pohja"
        self.pohja.mkdir()
        for komento in (["init", "-q", "-b", "main"], ["-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "a"]):
            subprocess.run(["git", "-C", str(self.pohja)] + komento, check=True, capture_output=True)
        self.yojono_ajuri = Path(self.tmp) / "yojono-ajuri"
        self.yojono_ajuri.mkdir()
        (self.yojono_ajuri / ".julkaisu-tieto").write_text("kohde=yojono-ajuri\ncommit=" + "a" * 40 + "\n")

    def rivi(self, vertailu):
        kutsut = []

        def hae(repo, commit, haara):
            kutsut.append(commit)
            return vertailu(commit) if callable(vertailu) else vertailu
        teksti = aamukooste.kayttoonotto_ero_rivi(str(self.pohja), str(self.yojono_ajuri), hae_vertailu=hae)
        return teksti, kutsut

    @staticmethod
    def vertailu(jaljessa, *otsikot):
        return {"ahead_by": jaljessa, "behind_by": 0, "commits": [{"commit": {"message": o}} for o in otsikot]}

    def test_jaljessa_kertoo_maarat_ja_prt(self):
        teksti, kutsut = self.rivi(self.vertailu(3, "Merge pull request #251 from x/y", "muu", "Merge pull request #242 from x/z"))
        self.assertIn("agentti-pohja 3 committia", teksti)
        self.assertIn("y\u00f6jono-ajuri 3 committia", teksti)
        self.assertIn("#251, #242", teksti)
        self.assertIn("paivita-ajurit.sh", teksti)
        self.assertEqual(len(kutsut), 2)
        self.assertEqual(kutsut[1], "a" * 40)   # yojonon commit luetaan .julkaisu-tieto-tiedostosta

    def test_ajan_tasalla_ei_tulosta_mitaan(self):
        self.assertIsNone(self.rivi(self.vertailu(0))[0])

    def test_vain_toinen_jaljessa(self):
        teksti, _ = self.rivi(lambda commit: self.vertailu(2 if commit == "a" * 40 else 0))
        self.assertIn("y\u00f6jono-ajuri 2 committia", teksti)
        self.assertNotIn("agentti-pohja 2", teksti)

    def test_verkkovirhe_nakyy_vain_tyyppina(self):
        def rikki(commit):
            raise OSError("salainen-osoite.invalid")
        teksti, _ = self.rivi(rikki)
        self.assertIn("ei selvinnyt (OSError)", teksti)
        self.assertNotIn("salainen", teksti)

    def test_ero_virheen_lyhyt_syy_nakyy_mutta_ei_poikkeuksen_teksti(self):
        from tools import kayttoonotto_ero as ero

        def rikki(commit):
            raise ero.EroVirhe("salainen-polku /etc/x", "git: kopion omistaja ei tasmaa (safe.directory ei auttanut)")
        teksti, _ = self.rivi(rikki)
        self.assertIn("ei selvinnyt (EroVirhe: git: kopion omistaja ei tasmaa", teksti)
        self.assertNotIn("salainen", teksti)

    def test_puuttuva_julkaisutieto_ei_kaada(self):
        (self.yojono_ajuri / ".julkaisu-tieto").unlink()
        self.assertIn("ei selvinnyt", self.rivi(self.vertailu(0))[0])

    def test_ei_git_kopiota_ei_kaada(self):
        teksti = aamukooste.kayttoonotto_ero_rivi(str(self.tmp) + "/ei-ole", str(self.yojono_ajuri), hae_vertailu=lambda *a: self.vertailu(0))
        self.assertIn("ei selvinnyt", teksti)

    def test_vertailuun_valitetaan_pelkka_reponimi_ja_url_on_omistaja_repo(self):
        """hae_vertailu lisaa omistajan itse: valitetyn repon pitaa olla pelkka nimi, muuten URL olisi omistaja/omistaja/repo."""
        from tools import kayttoonotto_ero as ero
        valitetut, urlit = [], []

        class Vastaus:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ahead_by": 0, "behind_by": 0, "commits": []}'

        def avaa(pyynto, timeout=None):
            urlit.append(pyynto.full_url)
            return Vastaus()

        def hae(repo, commit, haara):
            valitetut.append(repo)
            return ero.hae_vertailu(repo, commit, haara, avaa=avaa)
        aamukooste.kayttoonotto_ero_rivi(str(self.pohja), str(self.yojono_ajuri), hae_vertailu=hae)
        self.assertEqual(set(valitetut), {"agentti-pohja"})
        self.assertTrue(all("/" not in r for r in valitetut))
        self.assertEqual(len(urlit), 2)
        for url in urlit:
            self.assertTrue(url.startswith("https://api.github.com/repos/tonyweckstrom1987/agentti-pohja/compare/"), url)

    def test_rivi_nakyy_tarvitaan_rivilla(self):
        t = aamukooste.keraa(self.repo, self.yj, NYT, lambda url: [], lambda: [], ero=lambda: "ajurit j\u00e4ljess\u00e4 mainista: testi")
        self.assertIn("ajurit j\u00e4ljess\u00e4 mainista: testi", aamukooste.rivit(t)[0])

    def test_oletuksena_ei_hae_eroa_eika_nayta_rivia(self):
        t = aamukooste.keraa(self.repo, self.yj, NYT, lambda url: [], lambda: [])
        self.assertIsNone(t["ero"])
        self.assertNotIn("ajurit", aamukooste.rivit(t)[0])

    def test_ero_virhe_ei_kaada_koostetta(self):
        def rikki():
            raise RuntimeError("x")
        self.assertIsNone(aamukooste.keraa(self.repo, self.yj, NYT, lambda url: [], lambda: [], ero=rikki)["ero"])


class UusiMuoto(Kooste):
    load_tests = None
    def test_suljettu_paatos_ei_muistuteta_ja_avoin_muistutetaan_seuraavanakin_paivana(self):
        self.kirjoita("tonylle.md", "## 2026-11-01 AAMULLA ENSIN TEHTY: a\n## 2026-11-01 AAMULLA ENSIN b\n## 2026-11-01 tiedote c\n")
        t = self.keraa()
        self.assertEqual([o for _, o in t["paatokset"]], ["AAMULLA ENSIN b"])   # 2 pv vanha, silti avoin
        self.assertEqual(t["tonylle"], [])   # 24 h:n uusien lista tyhj\u00e4, p\u00e4\u00e4t\u00f6s silti rivill\u00e4
        self.assertIn("1 avointa kohtaa", aamukooste.rivit(t)[0])

    def test_avainsanaan_osumaton_kohta_ei_katoa_hiljaa(self):
        self.kirjoita("tonylle.md", "## 2026-11-03T01:00+02:00 Lis\u00e4tietoa X\n## 2026-11-03T02:00+02:00 Ilmoitus Y\n## 2026-11-03T03:00+02:00 AAMULLA ENSIN Z\n")
        t = self.keraa()
        rivit = aamukooste.rivit(t)
        self.assertEqual([o for _, o in t["paatokset"]], ["AAMULLA ENSIN Z"])
        self.assertIn("+2 muuta kohtaa tonylle.md:ss\u00e4", rivit[0])
        self.assertLessEqual(len(rivit), 10)

    def test_laskuri_ei_katoa_pitkien_otsikoiden_taakse(self):
        otsikot = "".join(f"## 2026-11-03T0{i}:00+02:00 AAMULLA ENSIN " + "p" * 150 + "\n" for i in range(1, 4))
        self.kirjoita("tonylle.md", otsikot + "## 2026-11-03T04:00+02:00 tiedote\n")
        self.assertIn("+1 muuta kohtaa tonylle.md:ss\u00e4", aamukooste.rivit(self.keraa())[0])

    def test_varaus_kysymys_ei_katoa_pitkien_otsikoiden_taakse(self):
        otsikot = "".join(f"## 2026-11-03T0{i}:00+02:00 AAMULLA ENSIN " + "p" * 150 + "\n" for i in range(1, 4))
        self.kirjoita("tonylle.md", otsikot)
        t = self.keraa()
        kysymys = 'kerro asetussivun luku "T\u00e4ll\u00e4 viikolla x % k\u00e4ytetty" (python3 -m claudejono.varaus kalibroi <luku>)'
        t["varaus_kysymys"] = kysymys
        rivi = aamukooste.rivit(t)[0]
        self.assertIn(kysymys, rivi)
        self.assertLessEqual(len(rivi), aamukooste.RIVI_MAX)

    def test_kysymysotsikko_jossa_sana_tehty_ei_ole_suljettu(self):
        self.kirjoita("tonylle.md", "## 2026-11-03T03:00+02:00 kysymys: onko X tehty?\n")
        self.assertEqual(len(aamukooste.avoimet_paatokset(self.repo, NYT)), 1)

    def test_suljettu_tunnistaa_tilamerkinnan_ei_sanaa_kesken_otsikkoa(self):
        for suljettu in ("kysymys: otetaanko X k\u00e4ytt\u00f6\u00f6n? VASTATTU", "kysymys: X [TEHTY]", "TEHTY: kysymys X",
                         "[p\u00e4\u00e4tetty] kysymys X", "kysymys X (suljettu)", "AAMULLA ENSIN: Y - hoidettu"):
            self.assertTrue(aamukooste.suljettu(suljettu), suljettu)
        for avoin in ("kysymys: onko X tehty?", "AAMULLA ENSIN: varmuuskopiota ei ole tehty",
                      "p\u00e4\u00e4tett\u00e4v\u00e4: aiempi korjaus rikki", "kysymys: ei viel\u00e4 tehty",
                      "kysymys: tehty vai ei?", "AAMULLA ENSIN Z"):
            self.assertFalse(aamukooste.suljettu(avoin), avoin)

    def test_sulkusana_pitkan_otsikon_lopussa_toimii_vaikka_nayttorivi_leikataan(self):
        otsikko = "kysymys: " + "pitka kuvaus " * 8 + "VASTATTU"
        self.kirjoita("tonylle.md", "## 2026-11-03T03:00+02:00 %s\n## 2026-11-03T04:00+02:00 kysymys: onko X tehty?\n" % otsikko)
        avoimet = aamukooste.avoimet_paatokset(self.repo, NYT)
        self.assertEqual([o for _, o in avoimet], ["kysymys: onko X tehty?"])

    def test_varaus_kysymys_sailyy_pitkien_otsikoiden_jalkeen(self):
        otsikot = "".join("## 2026-11-03T0%d:00+02:00 kysymys: %s\n" % (i, "pitka otsikko " * 4) for i in range(1, 4))
        self.kirjoita("tonylle.md", otsikot)
        t = self.keraa()
        t["varaus_kysymys"] = "kerro asetussivun luku (python3 -m claudejono.varaus kalibroi <luku>)"
        self.assertIn("kalibroi <luku>", aamukooste.rivit(t)[0])

    def test_ei_laskuria_kun_kaikki_kohdat_ovat_paatoksia(self):
        self.kirjoita("tonylle.md", "## 2026-11-03T03:00+02:00 AAMULLA ENSIN Z\n")
        self.assertNotIn("muuta kohtaa", aamukooste.rivit(self.keraa())[0])

    def test_kesken_ja_saantopaivitykset(self):
        self.kirjoita("tulokset/2026-11-03-01-k.md", tulos("k", "KESKEN", "2026-11-03T03:00+02:00"))
        self.kirjoita("saantopaivitykset.md", "# S\u00e4\u00e4nn\u00f6t\n## 2026-11-03T01:00+02:00 raja X tarkennettu\n## 2026-10-01 vanha\n")
        rivit = aamukooste.rivit(self.keraa())
        self.assertIn("Kesken (ei tarkastajan vahvistusta): 2026-11-02-01-a, 2026-11-03-01-k", rivit[2])
        self.assertIn("raja X tarkennettu", rivit[3])
        self.assertNotIn("vanha", rivit[3])

    def test_yli_kymmenen_rivia_ei_tule(self):
        self.assertLessEqual(len(aamukooste.muotoile(self.keraa()).split("\n\nLinkit:\n")[0].splitlines()), 10)

class TarkastajaVirheet(Pohja):
    def aineisto(self):
        return aamukooste.keraa(self.repo, self.yj, NYT, hae=lambda url: [], yksikot=lambda: [])

    def test_virhe_nousee_halytyksiin(self):
        (self.tmp / "tarkastukset.jsonl").write_text(json.dumps(
            {"repo": "agentti-pohja", "haara": "korjaus/x", "tulos": "virhe", "syy": "GitHub HTTP 403: /repos", "aika": "2026-11-03T05:00:00+02:00"}) + "\n")
        rivi = next(r for r in aamukooste.rivit(self.aineisto()) if r.startswith("H\u00e4lytykset:"))
        self.assertIn("PR-tarkastaja/-avaaja: 1 virhett\u00e4, viimeisin korjaus/x: GitHub HTTP 403", rivi)

    def test_ohitettu_pr_ei_nouse_halytyksiin(self):
        (self.tmp / "tarkastukset.jsonl").write_text(json.dumps(
            {"repo": "mcp-doctor", "pr": 29, "tulos": "OHITETTU", "syyt": ["luonnos"], "sha": "c" * 12, "aika": "2026-11-03T05:00:00+02:00"}) + "\n")
        rivi = next(r for r in aamukooste.rivit(self.aineisto()) if r.startswith("H\u00e4lytykset:"))
        self.assertNotIn("PR-tarkastaja", rivi)

    def test_ilman_lokia_ei_halytysta(self):
        rivi = next(r for r in aamukooste.rivit(self.aineisto()) if r.startswith("H\u00e4lytykset:"))
        self.assertNotIn("PR-tarkastaja", rivi)


class IlmoitusRajaus(unittest.TestCase):
    ENV = {"YOJONO_TELEGRAM_TOKEN": "t", "YOJONO_TELEGRAM_CHAT": "c"}

    def laheta(self, teksti, **env):
        e = dict(self.ENV, **env)
        with mock.patch("urllib.request.urlopen") as u:
            u.return_value.__enter__.return_value.status = 200
            tulos = yojono_ajuri.laheta_telegram(teksti, e)
        return tulos, u.called

    def test_oletus_heti_kaikki_lahtee(self):
        self.assertEqual(self.laheta("claude-jono: x VALMIS"), (True, True))
        self.assertEqual(self.laheta("claude-jono: x VALMIS", CLAUDEJONO_ILMOITUKSET="ihme"), (True, True))

    def test_kooste_rajaa_tavalliset(self):
        self.assertEqual(self.laheta("claude-jono: x VALMIS", CLAUDEJONO_ILMOITUKSET="kooste"), (False, False))
        self.assertEqual(self.laheta("Yojono 2026-11-03: kulu 1 / 5 USD", CLAUDEJONO_ILMOITUKSET="kooste"), (False, False))

    def test_kooste_sallii_halytys_ja_paatos(self):
        self.assertEqual(self.laheta("HALYTYS claude-jono kaatui: X", CLAUDEJONO_ILMOITUKSET="kooste"), (True, True))
        self.assertEqual(self.laheta("HALYTYS: 3 epaonnistui\nYojono ...", CLAUDEJONO_ILMOITUKSET="kooste"), (True, True))
        self.assertEqual(self.laheta("PAATOS: hyvaksy?", CLAUDEJONO_ILMOITUKSET="kooste"), (True, True))

    def test_pakota_ohittaa_rajauksen(self):
        with mock.patch("urllib.request.urlopen") as u:
            u.return_value.__enter__.return_value.status = 200
            self.assertTrue(yojono_ajuri.laheta_telegram("Tehty: x", dict(self.ENV, CLAUDEJONO_ILMOITUKSET="kooste"), pakota=True))

    def test_ilmoita_valmis_kooste_virhe_heti(self):
        with mock.patch.dict(os.environ, {"CLAUDEJONO_ILMOITUKSET": "kooste"}):
            self.assertEqual(ilmoita.ilmoita("valmis", "T", "x", env_polku="/ei/ole"), "kooste")
            self.assertEqual(ilmoita.ilmoita("virhe", "T", "x", env_polku="/ei/ole"), "ei_tunnuksia")   # ei rajata: yritetaan lahettaa
            self.assertEqual(ilmoita.ilmoita("paatos", "T", "x", env_polku="/ei/ole"), "ei_tunnuksia")


class Viikkoehdotus(Pohja):
    def tehtava(self, nimi, pisteet, runko="# Otsikko\n"):
        self.kirjoita(f"tehtavat/{nimi}.md", f"{runko}\nPisteet: {pisteet}\n")

    def test_pisteet_ja_jarjestys(self):
        self.tehtava("2026-11-03-01-a", "hyoty 5 (30 min/pv), varmuus 4, vaiva 4 h", "# Aamukooste\n")
        self.tehtava("2026-11-03-02-b", "hyoty 4, varmuus 4, vaiva 2 h", "# Pieni kysynta\n")
        self.tehtava("2026-11-03-03-c", "hyoty 9, varmuus 4, vaiva 2 h", "# Virheellinen\n")   # hyoty yli 5 -> ei pisteita
        self.kirjoita("tehtavat/2026-11-03-04-d.md", "# Ei pisteita\n")
        e = viikkoehdotus.ehdokkaat(self.repo, NYT)
        self.assertEqual([x["nimi"] for x in e], ["Pieni kysynta", "Aamukooste"])
        self.assertEqual([x["pisteet"] for x in e], [8.0, 5.0])
        self.assertEqual(e[0]["tavoite"], 5)

    def test_tehtava_jolla_on_tulos_ei_ehdokas(self):
        self.tehtava("2026-11-03-01-a", "hyoty 5, varmuus 4, vaiva 4 h")
        self.kirjoita("tulokset/2026-11-03-01-a.md", tulos("a", "VALMIS", "2026-11-03T01:00+02:00"))
        self.assertEqual(viikkoehdotus.ehdokkaat(self.repo, NYT), [])

    def test_uusinta_epaonnistuneesta(self):
        self.tehtava("2026-11-01-01-a", "hyoty 3, varmuus 3, vaiva 3 h", "# Kokeilu\n")
        self.kirjoita("tulokset/2026-11-01-01-a.md", tulos("a", "EPAONNISTUI", "2026-11-01T10:00+02:00"))
        e = viikkoehdotus.ehdokkaat(self.repo, NYT)
        self.assertEqual(e[0]["nimi"], "Uusinta: Kokeilu")
        self.assertTrue(e[0]["lahde"].startswith("epaonnistui"))

    def test_ehdokkaat_md_ja_rajat(self):
        self.kirjoita("ehdokkaat.md", "# Ehdokkaat\n- Portfolio-sivu | 4 | 3 | 6 h | 1\n- Rikkinainen | 7 | 3 | 1 h\n- Nolla | 4 | 3 | 0 h\n")
        e = viikkoehdotus.ehdokkaat(self.repo, NYT)
        self.assertEqual([x["nimi"] for x in e], ["Portfolio-sivu"])
        self.assertEqual(e[0]["pisteet"], 2.0)
        self.assertEqual(e[0]["tavoite"], 1)

    def test_enintaan_20(self):
        self.kirjoita("ehdokkaat.md", "\n".join(f"- E{i} | 3 | 3 | {i + 1} h" for i in range(30)))
        _, teksti, lista = viikkoehdotus.rakenna(self.repo, NYT)
        self.assertEqual(len(lista), viikkoehdotus.MAX_EHDOTUKSIA)
        self.assertNotIn("Huomio: pisteytettyj", teksti)

    def test_vahan_ehdokkaita_kerrotaan_eika_keksita(self):
        self.tehtava("2026-11-03-01-a", "hyoty 5, varmuus 4, vaiva 4 h")
        _, teksti, lista = viikkoehdotus.rakenna(self.repo, NYT)
        self.assertEqual(len(lista), 1)
        self.assertIn("vain 1", teksti)

    def test_aukot_valitavoitteista(self):
        self.kirjoita("valitavoitteet.md", "## Q4\n- Tavoite 1: portfolio\n- Tavoite 5 (Tony lisasi): kysynta\n")
        self.tehtava("2026-11-03-01-a", "hyoty 4 (valitavoite 5), varmuus 4, vaiva 2 h")
        _, teksti, _ = viikkoehdotus.rakenna(self.repo, NYT)
        self.assertIn("- Tavoite 1: portfolio", teksti)
        self.assertNotIn("- Tavoite 5:", teksti)

    def test_painopiste_kompassista(self):
        self.kirjoita("kompassi.md", "## Viikon painopiste\n- Viikko 45 (2.-8.11.): KAUPPATIIMI (Tonyn valinta)\n- Viikko 46 (9.-15.11.): KYSYNTA (x)\n")
        self.assertEqual(viikkoehdotus.painopiste(self.repo, 46), "KYSYNTA")
        self.assertEqual(viikkoehdotus.painopiste(self.repo, 47), "")

    def test_viikon_nimi_sunnuntaina_seuraava_viikko(self):
        self.assertEqual(viikkoehdotus.viikon_nimi(datetime(2026, 11, 8, 7, 0, tzinfo=TZ))[2], "2026-46")
        self.assertEqual(viikkoehdotus.viikon_nimi(datetime(2026, 12, 27, 7, 0, tzinfo=TZ))[2], "2026-53")
        self.assertEqual(viikkoehdotus.viikon_nimi(datetime(2027, 1, 3, 7, 0, tzinfo=TZ))[2], "2027-01")

    def test_main_kirjoittaa_tiedoston_hakemistoon(self):
        self.tehtava("2026-11-03-01-a", "hyoty 5, varmuus 4, vaiva 4 h")
        hak = self.tmp / "ulos"
        with mock.patch("sys.stdout"):
            self.assertEqual(viikkoehdotus.main(["--repo", str(self.repo), "--hakemisto", str(hak), "--nyt", "2026-11-08T07:00+02:00"]), 0)
        self.assertTrue((hak / "2026-46-ehdotus.md").is_file())
        self.assertFalse(list(self.repo.glob("**/*-ehdotus.md")))   # ei docs-kloonin sisaan


def tulos_mallilla(nimi, tila, aika, malli="sonnet", runko=""):
    return f"# Tulos: {nimi}\n\n- Tila: {tila}\n- Aika: {aika}\n- Malli: {malli}\n\n{runko}\n"


def tarkastus(tarkastaja="opus", toimii="kyll\u00e4", todiste="journalctl-rivi 20:05 ok", lisarivit=""):
    return f"# Tarkastus\n\n{lisarivit}- Tarkastaja: {tarkastaja}\n- Toimii k\u00e4yt\u00f6ss\u00e4: {toimii}\n- Todiste: {todiste}\n"


class ValmisPortti(Pohja):
    """Oma fixture (ei jaa Kooste.setUp:ia): tarkastusmerkinn\u00e4t eiv\u00e4t saa n\u00e4ky\u00e4 omina tuloksinaan, ja portti vaatii riippumattoman mallin."""

    def keraa(self):
        return aamukooste.keraa(self.repo, self.yj, NYT, lambda url: [] if "/pulls?" in url else {"check_runs": []}, lambda: [])

    def tilat(self):
        return {r["nimi"]: r["tila"] for r in self.keraa()["tulokset"]}

    def test_tarkastustiedosto_ei_nay_tuloksena_vaikka_siina_on_tila_ja_aika(self):
        self.kirjoita("tulokset/2026-11-02-01-a.md", tulos_mallilla("a", "VALMIS", "2026-11-02T20:00+02:00"))
        self.kirjoita("tulokset/2026-11-02-01-a.tarkastus.md",
                      tarkastus(lisarivit="- Tila: VALMIS\n- Aika: 2026-11-02T20:05+02:00\n- Malli: opus\nhttps://github.com/tonyweckstrom1987/agentti-pohja/pull/99\n"))
        t = self.keraa()
        self.assertEqual([r["nimi"] for r in t["tulokset"]], ["2026-11-02-01-a.md"])
        self.assertEqual(t["tulokset"][0]["tila"], "VALMIS")
        self.assertEqual(t["uudet_prt"], [])   # tarkastustiedoston teksti ei tuota PR-linkkej\u00e4
        self.assertIn("1 jonotulosta (1 valmis", next(r for r in aamukooste.rivit(t) if "jonotulosta" in r))

    def test_valmis_ilman_tarkastajan_merkintaa_nakyy_keskena(self):
        self.kirjoita("tulokset/2026-11-02-03-c.md", tulos_mallilla("c", "VALMIS", "2026-11-02T21:00+02:00"))
        self.assertEqual(self.tilat(), {"2026-11-02-03-c.md": "KESKEN"})
        rivi = next(r for r in aamukooste.rivit(self.keraa()) if "jonotulosta" in r)
        self.assertIn("0 valmis, 1 kesken (heist\u00e4 1 ilman tarkastajan merkint\u00e4\u00e4)", rivi)

    def test_mallinimien_eri_muodot_ja_puuttuva_malli(self):
        tapaukset = [   # (tulosteksti-malli, tarkastaja, odotettu tila)
            ("sonnet", "opus", "VALMIS"),
            ("claude-sonnet-5-5", "claude-opus-5-5", "VALMIS"),
            ("sonnet", "anthropic/claude-opus-5-5", "VALMIS"),
            ("opus", "claude-opus-5-5", "KESKEN"),        # sama malli eri muodossa
            ("claude-opus-5-5", "opus", "KESKEN"),
            ("opus", "Opus-istunto-7", "KESKEN"),         # sama perhe, eri kirjainkoko ja jatke
            ("claude-opus-5-5", "claude-opus-5-5", "KESKEN"),
            (None, "opus", "KESKEN"),                     # puuttuva Malli-rivi on epavarma
            ("-", "opus", "KESKEN"),
            ("sonnet", "-", "KESKEN"),                    # tarkastajan nimi tyhja
        ]
        for i, (malli, tarkastaja, odotettu) in enumerate(tapaukset, 1):
            nimi = f"2026-11-02-{i:02d}-x"
            teksti = (tulos_mallilla("x", "VALMIS", "2026-11-02T20:00+02:00", malli) if malli
                      else tulos("x", "VALMIS", "2026-11-02T20:00+02:00"))
            self.kirjoita(f"tulokset/{nimi}.md", teksti)
            self.kirjoita(f"tulokset/{nimi}.tarkastus.md", tarkastus(tarkastaja))
        tilat = self.tilat()
        for i, (malli, tarkastaja, odotettu) in enumerate(tapaukset, 1):
            self.assertEqual(tilat[f"2026-11-02-{i:02d}-x.md"], odotettu, (malli, tarkastaja))

    def test_ei_todistetta_tai_ei_toimi_on_keskena(self):
        self.kirjoita("tulokset/2026-11-02-01-a.md", tulos_mallilla("a", "VALMIS", "2026-11-02T20:00+02:00"))
        self.kirjoita("tulokset/2026-11-02-01-a.tarkastus.md", tarkastus(todiste="-"))
        self.kirjoita("tulokset/2026-11-02-02-b.md", tulos_mallilla("b", "VALMIS", "2026-11-02T20:10+02:00"))
        self.kirjoita("tulokset/2026-11-02-02-b.tarkastus.md", tarkastus(toimii="ei"))
        self.assertEqual(set(self.tilat().values()), {"KESKEN"})


if __name__ == "__main__":
    unittest.main()
