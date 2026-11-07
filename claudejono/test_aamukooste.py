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
        self.kirjoita("tulokset/2026-11-02-01-a.md", tulos("a", "TEHTY", "2026-11-02T20:00+02:00",
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

    def test_viisi_rivia_ja_linkit(self):
        teksti = aamukooste.muotoile(self.keraa())
        rivit, linkit = teksti.split("\n\nLinkit:\n")
        rivit = rivit.splitlines()
        self.assertEqual(len(rivit), 5)
        self.assertTrue(all(len(r) <= aamukooste.RIVI_MAX for r in rivit))
        for alku in ("Tehty:", "Tarvitaan sinulta:", "Opittu:", "H", "Kulut:"):
            self.assertTrue(any(r.startswith(alku) for r in rivit), alku)
        self.assertIn("https://github.com/tonyweckstrom1987/agentti-pohja/pull/9", linkit)
        self.assertIn("tonylle.md", linkit)

    def test_ikkuna_rajaa_vanhat_pois(self):
        t = self.keraa()
        self.assertEqual(sorted(r["nimi"][:13] for r in t["tulokset"]), ["2026-11-02-01", "2026-11-02-02"])
        self.assertEqual(t["uudet_prt"], ["agentti-pohja#7"])   # vanhan tuloksen PR ei mukaan
        self.assertEqual(len(t["tonylle"]), 2)

    def test_sisalto_oikeasta_datasta(self):
        rivit = aamukooste.rivit(self.keraa())
        self.assertIn("2 jonotulosta (1 tehty, 1 ep", rivit[0])
        self.assertIn("agentti-pohja#7", rivit[0])
        self.assertIn("1 PR:", rivit[1])
        self.assertIn("2 uutta kohtaa", rivit[1])
        self.assertIn("X ei onnistu, kayta Y", rivit[2])
        self.assertIn("failed: paivakatsaus.service", rivit[3])
        self.assertIn("95 %", rivit[3])   # kuukausikulu 4,25 + 0,5 = 4,75 / 5,0
        self.assertIn("y", rivit[3])
        self.assertIn("4.25 / 5.00 USD", rivit[4])   # ajopaiva vaihtuu klo 9: klo 6.30 on viela 2.11.

    def test_github_virhe_ei_nay_tyhjana_onnistumisena(self):
        def rikki(url):
            raise OSError("verkko")
        t = self.keraa(hae=rikki)
        rivit = aamukooste.rivit(t)
        self.assertIn("vajaa", rivit[1])
        self.assertIn("OSError", rivit[1])

    def test_puuttuva_yojono_ja_systemd_ei_kaada(self):
        t = aamukooste.keraa(self.repo, self.tmp / "ei-ole", NYT, self.hae, lambda: None)
        rivit = aamukooste.rivit(t)
        self.assertEqual(len(rivit), 5)
        self.assertIn("systemd-tilaa ei saatu", rivit[3])
        self.assertIn("tilaa ei saatu", rivit[4])

    def test_tyhja_oppi_ei_nay(self):
        self.kirjoita("tulokset/2026-11-03-01-c.md", tulos("c", "TEHTY", "2026-11-03T03:00+02:00", "## Uusi oppi\nTama ajo ei tuottanut uutta oppia.\n"))
        self.assertNotIn("Tama ajo ei", aamukooste.rivit(self.keraa())[2])

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


class TehtyJaVanhaValmis(Pohja):
    def test_tehty_ja_vanha_valmis_lasketaan_erikseen_ja_opit_poimitaan_molemmista(self):
        oppi = "## Uusi oppi\nX ei onnistu, kayta Y.\n"
        self.kirjoita("tulokset/2026-11-03-01-a.md", tulos("a", "TEHTY", "2026-11-03T01:00+02:00", oppi))
        self.kirjoita("tulokset/2026-11-03-02-b.md", tulos("b", "VALMIS", "2026-11-03T02:00+02:00", oppi.replace("Y", "Z")))
        t = aamukooste.keraa(self.repo, self.yj, NYT, lambda url: [], lambda: [])
        self.assertEqual(len(t["opit"]), 2)
        self.assertIn("(1 tehty, 1 valmis", aamukooste.rivit(t)[0])


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
        self.kirjoita("tulokset/2026-11-03-01-a.md", tulos("a", "TEHTY", "2026-11-03T01:00+02:00"))
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


if __name__ == "__main__":
    unittest.main()
