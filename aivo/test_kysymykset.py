"""Aivon kysymyslistan testit feikkikirjastolla (ei oikeaa dataa)."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from aivo import kysymykset as k

PV = date(2030, 1, 7)
PITKA = "x" * 300


def merkinta(kans, nimi, aihe, runko=PITKA, luokka="yleinen", **muut):
    kentat = {"aihe": aihe, "paivitetty": "2030-01-01", **muut}
    if luokka:
        kentat["luokka"] = luokka
    teksti = "---\n" + "\n".join(f"{a}: {b}" for a, b in kentat.items()) + "\n---\n" + runko + "\n"
    p = Path(kans) / nimi
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(teksti, encoding="utf-8")
    return p


class Pohja(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        r = Path(self.t.name)
        self.kirj, self.ulos, self.vast = r / "kirj", r / "ulos", r / "vast"
        self.tila = r / "tila.json"
        self.vast.mkdir()

    def laadi(self, pv=PV):
        return k.laadi(self.kirj, self.ulos, self.tila, pv)


class TestLaadi(Pohja):
    def test_ohut_aihe_tulee_listalle(self):
        merkinta(self.kirj, "a.md", "Python-testaus", runko="lyhyt")
        merkinta(self.kirj, "b.md", "Git-haarat")
        polku, tiedot = self.laadi()
        teksti = polku.read_text(encoding="utf-8")
        self.assertIn("Python-testaus", teksti)
        self.assertNotIn("Git-haarat", teksti)
        self.assertEqual(tiedot["yleisia"], 1)

    def test_ristiriita_ensin(self):
        merkinta(self.kirj, "a.md", "Editori", runko="lyhyt", avain="editori")
        merkinta(self.kirj, "b.md", "Editori", avain="paatos-editori", kanta="vim")
        merkinta(self.kirj, "c.md", "Editori", avain="paatos-editori", kanta="vscode")
        teksti = self.laadi()[0].read_text(encoding="utf-8")
        self.assertLess(teksti.index("kaksi eri kantaa"), teksti.index("vain v\u00e4h\u00e4n tietoa"))

    def test_henkilokohtainen_vain_tunnisteella(self):
        merkinta(self.kirj, "a.md", "\u00c4idin sairaus", runko="lyhyt", luokka="henkilokohtainen")
        merkinta(self.kirj, "b.md", "Is\u00e4n syntt\u00e4rit", runko="lyhyt", luokka=None)
        teksti = self.laadi()[0].read_text(encoding="utf-8")
        self.assertIn("henkilokohtainen-aihe-1", teksti)
        self.assertIn("henkilokohtainen-aihe-2", teksti)
        for kielletty in ("sairaus", "\u00c4idin", "syntt\u00e4r", "Is\u00e4n"):
            self.assertNotIn(kielletty, teksti)

    def test_yleiseksi_merkitty_arka_aihe_on_silti_henkilokohtainen(self):
        merkinta(self.kirj, "a.md", "Unen laatu ja syke", runko="lyhyt")
        merkinta(self.kirj, "b.md", "Palkkaneuvottelu", runko="lyhyt")
        merkinta(self.kirj, "c.md", "Siisti aihe", runko="lyhyt", avain="\u00c4idin l\u00e4\u00e4kkeet")
        teksti = self.laadi()[0].read_text(encoding="utf-8")
        for kielletty in ("Unen", "syke", "Palkka", "Siisti"):
            self.assertNotIn(kielletty, teksti)
        self.assertEqual(teksti.count("henkilokohtainen-aihe-"), 3)

    def test_kirjaston_runko_ei_vuoda(self):
        merkinta(self.kirj, "a.md", "Rust", runko="SALAINEN-RUNKO")
        merkinta(self.kirj, "b.md", "Go", runko="SALAINEN-RUNKO lyhyt")
        teksti = self.laadi()[0].read_text(encoding="utf-8")
        self.assertNotIn("SALAINEN", teksti)

    def test_kysyy_kerran(self):
        merkinta(self.kirj, "a.md", "Rust", runko="lyhyt")
        self.assertEqual(self.laadi(PV)[1]["uusia"], 1)
        polku, tiedot = self.laadi(date(2030, 1, 14))
        self.assertIsNone(polku)
        self.assertEqual(tiedot["uusia"], 0)

    def test_sama_paiva_ei_tuplaa(self):
        merkinta(self.kirj, "a.md", "Rust", runko="lyhyt")
        merkinta(self.kirj, "b.md", "Go", runko="lyhyt")
        self.laadi()
        self.assertEqual(self.laadi()[1]["uusia"], 0)

    def test_rajat(self):
        for i in range(9):
            merkinta(self.kirj, f"a{i}.md", f"Aihe {i}", runko="lyhyt")
            merkinta(self.kirj, f"h{i}.md", f"Muu {i}", runko="lyhyt", luokka="henkilokohtainen")
        tiedot = self.laadi()[1]
        self.assertEqual((tiedot["yleisia"], tiedot["henkilokohtaisia"]), (k.MAX_YLEISIA, k.MAX_HENKILOKOHTAISIA))

    def test_tyhja_kirjasto_ja_rikkinaiset_tiedostot(self):
        self.kirj.mkdir()
        (self.kirj / "rikki.md").write_bytes(b"\xff\xfe---\n")
        (self.kirj / "ei-fm.md").write_text("pelkka teksti", encoding="utf-8")
        self.assertIsNone(self.laadi()[0])

    def test_vanha_merkinta_on_ohut(self):
        merkinta(self.kirj, "a.md", "Docker", paivitetty="2029-01-01")
        self.assertEqual(self.laadi()[1]["uusia"], 1)

    def test_tarkistusportti_hylkaa_arkaluonteisen(self):
        with self.assertRaises(ValueError):
            k._tarkista_lista("1. **K1** (x): Kerro perheest\u00e4si\n")


class TestVastaukset(Pohja):
    def setUp(self):
        super().setUp()
        self.a = merkinta(self.kirj, "a.md", "Rust", runko="lyhyt")
        self.b = merkinta(self.kirj, "b.md", "Muu", runko="lyhyt", luokka="henkilokohtainen")
        self.laadi()

    def vastaa(self, teksti, pv=PV):
        (self.vast / f"{pv.isoformat()}.md").write_text(teksti, encoding="utf-8")

    def test_jasennys(self):
        self.assertEqual(k.jasenna_vastaukset("alku\n## K1\nyksi\nkaksi\n## K2\n\n## K3\nkolme"),
                         {"K1": "yksi\nkaksi", "K3": "kolme"})

    def test_vastaus_kirjataan_muistiin_ja_testi_menee_lapi(self):
        self.vastaa("## K1\nRust on t\u00e4rke\u00e4, koska X.\n## K2\nKyll\u00e4, asia on Y.\n")
        self.assertEqual(k.lue(self.kirj, self.vast, self.tila, date(2030, 1, 9)), 2)
        a = self.a.read_text(encoding="utf-8")
        self.assertIn("paivitetty: 2030-01-09", a)
        self.assertIn("## Haastattelu 2030-01-09", a)
        self.assertIn("Rust on t\u00e4rke\u00e4", a)
        rivit, ok = k.tarkista(self.tila, date(2030, 1, 20))
        self.assertTrue(ok, rivit)

    def test_lupa_ei_ei_kirjaa_sisaltoa(self):
        self.vastaa("## K2\nei\n")
        k.lue(self.kirj, self.vast, self.tila, PV)
        self.assertNotIn("Haastattelu", self.b.read_text(encoding="utf-8"))
        self.assertTrue(k.tarkista(self.tila, PV)[1])

    def test_kahta_kertaa_ei_tuplaa(self):
        self.vastaa("## K1\nvastaus\n")
        k.lue(self.kirj, self.vast, self.tila, PV)
        k.lue(self.kirj, self.vast, self.tila, PV)
        self.assertEqual(self.a.read_text(encoding="utf-8").count("## Haastattelu"), 1)

    def test_myohassa_epaonnistuu(self):
        self.vastaa("## K1\nvastaus\n")
        self.a.unlink()
        k.lue(self.kirj, self.vast, self.tila, PV)
        rivit, ok = k.tarkista(self.tila, date(2030, 1, 20))
        self.assertFalse(ok)
        self.assertTrue(any("MYOHASSA" in r for r in rivit))

    def test_polku_kirjaston_ulkopuolelta_ei_kirjoiteta(self):
        ulko = Path(self.t.name) / "ulko.md"
        ulko.write_text("---\naihe: x\n---\nalku\n", encoding="utf-8")
        tila = json.loads(self.tila.read_text(encoding="utf-8"))
        tila["listat"][PV.isoformat()]["kysymykset"][0]["polut"] = [str(ulko)]
        self.tila.write_text(json.dumps(tila), encoding="utf-8")
        self.vastaa("## K1\nvastaus\n")
        self.assertEqual(k.lue(self.kirj, self.vast, self.tila, PV), 0)
        self.assertEqual(ulko.read_text(encoding="utf-8"), "---\naihe: x\n---\nalku\n")


class TestKomentorivi(Pohja):
    def test_main_laadi_ja_tarkista(self):
        merkinta(self.kirj, "a.md", "Rust", runko="lyhyt")
        args = ["--kirjasto", str(self.kirj), "--ulos", str(self.ulos), "--tila", str(self.tila),
                "--vastaukset", str(self.vast)]
        with redirect_stdout(io.StringIO()):
            rc = k.main(["laadi"] + args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(list(self.ulos.glob("*.md"))), 1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(k.main(["tarkista"] + args), 0)

    def test_virheesta_vain_tyyppi(self):
        self.kirj.mkdir()
        self.ulos.write_text("tiedosto, ei hakemisto", encoding="utf-8")
        merkinta(self.kirj, "a.md", "Rust", runko="lyhyt")
        virhe = io.StringIO()
        with redirect_stderr(virhe):
            rc = k.main(["laadi", "--kirjasto", str(self.kirj), "--ulos", str(self.ulos), "--tila", str(self.tila)])
        self.assertEqual(rc, 2)
        self.assertRegex(virhe.getvalue(), r"^virhe: \w+\n$")


if __name__ == "__main__":
    unittest.main()
