"""Itsekehittyvan silmukan testit: porttitarkistin, toistuvat tehtavat, taydennys ja pysaytys."""
import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path

from claudejono import ajuri, silmukka
from claudejono.test_ajuri import Pohja, git

OK_TEHTAVA = "# A\n\nTavoite: yllapito\nPisteet: hyoty 4, varmuus 4, vaiva 1 h\nSuunta: jatkoa (kompassi 1)\n"

NYT = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def teht(lisa="", suunta="jatkoa (kompassi 1)", pisteet="hyoty 4, varmuus 4, vaiva 2 h"):
    return f"# Tehtava\n\nTavoite: yllapito\nPisteet: {pisteet}\nSuunta: {suunta}\n{lisa}\nValmis kun: PR.\n"


class Tarkistin(unittest.TestCase):
    def test_kelvollinen_menee_lapi(self):
        self.assertEqual(silmukka.tarkista_tehtava(teht("Lisaa testi tarkistimelle.")), ("ok", ""))

    def test_suunta_puuttuu_tai_uusi(self):
        self.assertEqual(silmukka.tarkista_tehtava("Pisteet: hyoty 4, varmuus 4, vaiva 2 h\n")[0], "ok")   # puuttuva Suunta = jatkoa; muoto PORTTIssa
        self.assertEqual(silmukka.tarkista_tehtava(teht(suunta="uusi"))[0], "sunnuntai")

    def test_pisteet_hyoty_scandinaavisella_o(self):
        self.assertEqual(silmukka.tarkista_tehtava(teht(pisteet="hy\u00f6ty 5, varmuus 4, vaiva 1,5 h"))[0], "ok")

    def test_aiheet_menevat_sunnuntaille(self):
        tapaukset = {
            "julkaisu": "Julkaise raportti LinkedInissa.",
            "raha": "Osta lisaa laskentaa, kustannus 20 euroa.",
            "oikeudet": "Anna agentille lisaa oikeuksia.",
            "kulukatto": "Nosta paivakatto 40 euroon.",
            "ajastus": "Lisaa uusi ajastin joka tunti.",
            "uusi-suunta": "Aloita uusi projekti.",
        }
        for luokka, lause in tapaukset.items():
            tulos = silmukka.tarkista_tehtava(teht(lause))
            self.assertEqual(tulos[0], "sunnuntai", luokka)

    def test_kompassin_kiellot_menevat_sunnuntaille(self):
        """Triagen (3.11.) lapaisseet esimerkit: kukin pysaytetaan omaan luokkaansa."""
        tapaukset = [
            ("Tee matematiikan YTO-kertauspaketti", "koulu"),
            ("Tee kotiteht\u00e4v\u00e4t valmiiksi.", "koulu"),
            ("Siirr\u00e4 salkku oikealle rahalle", "oikea-raha"),
            ("Siirra salkku oikealle rahalle", "oikea-raha"),
            ("Kytke live-kauppa p\u00e4\u00e4lle", "oikea-raha"),
            ("Lis\u00e4\u00e4 Binance API-avain asetuksiin", "oikea-raha"),
            ("Tee LinkedIn-postaus", "julkaisu"),
            ("Kirjoita postaus Tonyn profiiliin", "julkaisu"),
            ("npm-julkaisu versiosta 0.4.0", "julkaisu"),
            ("Avaa korjaus-PR Atlassianin repoon Tonyn nimiss\u00e4", "julkaisu"),
            ("Avaa ulkoinen PR kirjastoon", "julkaisu"),
        ]
        for lause, luokka in tapaukset:
            self.assertEqual(silmukka.tarkista_tehtava(teht(lause)), ("sunnuntai", luokka), lause)

    def test_kieltolause_ei_ohita_kompassin_kieltoja(self):
        """Kielto-sana samassa lauseessa tai Rajat-rivilla ei paasta lapi (ei tarvitse kysya, ala odota)."""
        for lause in ("Kytke live-kauppa p\u00e4\u00e4lle, ei tarvitse kysy\u00e4.",
                      "Rajat: ilman lupaa tee LinkedIn-postaus.",
                      "\u00c4l\u00e4 odota Tonia: tee matematiikan YTO-kertauspaketti.",
                      "Siirr\u00e4 salkku oikealle rahalle ilman varmistusta."):
            self.assertEqual(silmukka.tarkista_tehtava(teht(lause))[0], "sunnuntai", lause)

    def test_tavallinen_tyo_ei_osu_kompassin_kieltoihin(self):
        for lause in ("Avaa PR agentti-pohjan repoon.", "Korjaa koulu-muistutus-moduulin testi.",
                      "Diagnosoi julkaisulista-tiedoston muoto.", "Lis\u00e4\u00e4 testi paperikaupan raportille.",
                      "Lue testitulokset ja kirjoita yhteenveto."):
            self.assertEqual(silmukka.tarkista_tehtava(teht(lause)), ("ok", ""), lause)

    def test_kieltolause_ei_laukaise(self):
        t = teht("Ei kulukattojen, oikeuksien eika ajastinten muutoksia. Ala julkaise mitaan. Ilman rahankayttoa.")
        self.assertEqual(silmukka.tarkista_tehtava(t), ("ok", ""))

    def test_rajat_rivi_ohitetaan(self):
        t = teht("Rajat: katot, oikeudet ja ajastimet pysyvat ennallaan.")
        self.assertEqual(silmukka.tarkista_tehtava(t), ("ok", ""))

    def test_kielto_ei_peita_toista_lausetta(self):
        t = teht("Ei mergea. Nosta kulukatto.")
        self.assertEqual(silmukka.tarkista_tehtava(t)[0], "sunnuntai")

    def test_suodata_jarjestys_ja_ryhmat(self):
        lista = [{"nimi": "a", "sisalto": teht()}, {"nimi": "b", "sisalto": teht("Julkaise npm publish.")},
                 {"nimi": "c", "sisalto": "# huono"}, {"nimi": "d", "sisalto": teht()}]
        hyva, sunn, huono = silmukka.suodata(lista)
        self.assertEqual([t["nimi"] for t in hyva], ["a", "c", "d"])   # muototarkistus on ajurin PORTTIssa
        self.assertEqual([t["nimi"] for t, _ in sunn], ["b"])
        self.assertEqual(huono, [])
        self.assertIn("Sunnuntaiehdotus (julkaisu): b", silmukka.sunnuntairivit(sunn))

    def test_pohjat_ovat_kelvollisia(self):
        for nimi in silmukka.TOISTUVAT:
            sisalto = silmukka.toistuvan_sisalto(nimi)
            self.assertIsNotNone(sisalto, nimi)
            self.assertTrue(silmukka.PISTEET_RE.search(sisalto), nimi)
            self.assertTrue(silmukka.SUUNTA_RE.search(sisalto), nimi)
            self.assertIn("Todennettu k\u00e4yt\u00f6ss\u00e4", sisalto)


class Silmukka(Pohja):
    def malli(self, tulos, kutsut=None):
        def f(kom, **kw):
            if kutsut is not None:
                kutsut.append(kom)
            return subprocess.CompletedProcess(kom, 0, json.dumps({"subtype": "success", "result": tulos}), "")
        return f

    def tulos(self, nimi):
        p = self.repo / ajuri.JONO / "tulokset" / nimi
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("ok")

    def tallenna(self):
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "t"); git(self.repo, "push", "-q", "origin", "main")

    def tehtavat_originissa(self):
        t = self.tmp / ("tark" + str(len(list(self.tmp.iterdir()))))
        subprocess.run(["git", "clone", "-q", str(self.origin), str(t)], check=True, capture_output=True)
        return sorted(p.name for p in (t / ajuri.JONO / "tehtavat").glob("*.md")) if (t / ajuri.JONO / "tehtavat").exists() else []

    def pysayta(self):
        (self.tmp / silmukka.LAATU_TILA).write_text(json.dumps({"pysaytetty": {"syy": "testi"}}))

    # --- suunnittelun suodatus ---
    def test_suunnittelu_ohjaa_julkaisun_tonylle_eika_jonoon(self):
        vastaus = json.dumps({"tehtavat": [
            {"nimi": "julkaisu-x", "sisalto": teht("Julkaise artikkeli LinkedInissa.")},
            {"nimi": "korjaus-y", "sisalto": OK_TEHTAVA},
            {"nimi": "pisteeton", "sisalto": "# Z"}], "tonylle": ""})
        v = ajuri.suunnittele(self.repo, self.tmp, {}, self.malli(vastaus), nyt=NYT)
        self.assertIn("itsesuunnittelu", v)
        tied = self.tehtavat_originissa()
        self.assertIn("2026-11-03-01-auto-korjaus-y.md", tied)
        self.assertFalse(any("julkaisu-x" in t for t in tied))
        tonylle = (self.repo / ajuri.JONO / "tonylle.md").read_text()
        self.assertIn("Sunnuntaiehdotus (julkaisu): julkaisu-x", tonylle)
        self.assertNotIn("pisteeton", tonylle)

    def test_vain_sunnuntaiehdotuksia_kirjaa_tonylle_ja_tauon(self):
        vastaus = json.dumps({"tehtavat": [{"nimi": "uusi-x", "sisalto": teht(suunta="uusi")}], "tonylle": ""})
        v = ajuri.suunnittele(self.repo, self.tmp, {}, self.malli(vastaus), nyt=NYT)
        self.assertEqual(self.tehtavat_originissa(), [])
        self.assertIn("tonylle.md", v)
        self.assertTrue(ajuri.suunnittelu_tauolla(self.tmp, self.repo, NYT, 3600))

    def test_pysaytetty_suunnittelu_ei_kutsu_mallia(self):
        self.pysayta()
        kutsut = []
        v = ajuri.suunnittele(self.repo, self.tmp, {}, self.malli("{}", kutsut), nyt=NYT)
        self.assertIn("pysaytetty", v)
        self.assertEqual(kutsut, [])

    def test_paivaraja_ja_per_ajo_ennallaan(self):
        self.assertEqual(ajuri.AUTO_MAX_PAIVA, 30)
        self.assertEqual(ajuri.AUTO_PER_AJO, 2)
        self.assertTrue(ajuri.sallittu_polku("claude-jono/tonylle.md"))
        self.assertFalse(ajuri.sallittu_polku("claude-jono/sunnuntaiehdotukset.md"))

    # --- taydennys ---
    def test_aja_ei_taydenna_kun_avoimia_vahintaan_viisi(self):
        for i in range(5):
            self.lisaa_tehtava(f"2026-11-03-{i + 1:02d}-a.md")
        kutsut = []
        env = {"CLAUDEJONO_ITSESUUNNITTELU": "1"}
        ajuri.aja(self.repo, self.tmp, env=env, suorita=self.malli("tulos", kutsut))
        self.assertEqual(len(kutsut), 1)                       # vain tehtava

    def test_pois_paalta_itsesuunnittelu_0_ei_taydenna(self):
        self.lisaa_tehtava("2026-11-03-01-a.md")
        kutsut = []
        ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_ITSESUUNNITTELU": "0"}, suorita=self.malli("r", kutsut))
        self.assertEqual(len(kutsut), 1)

    # --- toistuvat ---
    def test_toistuva_luodaan_ensimmaisella_kerralla_ja_sitten_vasta_viikon_paasta(self):
        luodut = ajuri.luo_toistuvat(self.repo, self.tmp, nyt=NYT)
        self.assertEqual([p.name for p in luodut], ["2026-11-03-01-auto-optimointi.md"])
        self.assertEqual(self.tehtavat_originissa(), ["2026-11-03-01-auto-optimointi.md"])
        self.assertIn("Toistuva tehtava", luodut[0].read_text())
        self.assertEqual(ajuri.luo_toistuvat(self.repo, self.tmp, nyt=NYT), [])        # odottaa ajoa
        self.tulos("2026-11-03-01-auto-optimointi.md"); self.tallenna()
        kuusi = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(ajuri.luo_toistuvat(self.repo, self.tmp, nyt=kuusi), [])      # 6 pv: ei viela
        seitseman = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        uusi = ajuri.luo_toistuvat(self.repo, self.tmp, nyt=seitseman)
        self.assertEqual([p.name for p in uusi], ["2026-11-10-01-auto-optimointi.md"])

    def test_toistuva_ei_synny_pysaytettyna(self):
        self.pysayta()
        self.assertEqual(ajuri.luo_toistuvat(self.repo, self.tmp, nyt=NYT), [])
        self.assertEqual(self.tehtavat_originissa(), [])

    def test_toistuva_kuuluu_paivarajaan(self):
        teht_hak = self.repo / ajuri.JONO / "tehtavat"
        teht_hak.mkdir(parents=True)
        for i in range(ajuri.AUTO_MAX_PAIVA):
            (teht_hak / f"2026-11-03-{i + 1:02d}-auto-t{i}.md").write_text("x")
        self.assertEqual(ajuri.luo_toistuvat(self.repo, self.tmp, nyt=NYT), [])

    def test_toistuva_nimi_on_sallittu_polku(self):
        self.assertTrue(ajuri.sallittu_polku("claude-jono/tehtavat/2026-11-03-01-auto-optimointi.md"))

    def test_aja_luo_toistuvan_kun_itsesuunnittelu_paalla(self):
        kutsut = []
        v = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_TOISTUVAT": "1"}, suorita=self.malli('{"tehtavat": [], "tonylle": ""}', kutsut))
        self.assertIn("optimointi", " ".join(self.tehtavat_originissa()))


if __name__ == "__main__":
    unittest.main()


class VaivaRajaTesti(unittest.TestCase):
    def test_yli_kahden_tunnin_tehtava_hylataan(self):
        perus = "# T\n\nPisteet: hyoty 4, varmuus 4, vaiva %s h\nSuunta: jatkoa (kompassi)\n"
        self.assertEqual(silmukka.tarkista_tehtava(perus % "3")[0], "hylatty")
        self.assertEqual(silmukka.tarkista_tehtava(perus % "2,5"), ("hylatty", "vaiva yli 2 h: pilko osiin"))
        for v in ("2", "1.5", "0,5"):
            self.assertEqual(silmukka.tarkista_tehtava(perus % v)[0], "ok", v)
