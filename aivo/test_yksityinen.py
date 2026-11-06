import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aivo import yksityinen


def ip(*osat):
    return ".".join(str(o) for o in osat)


class HakemistoTesti(unittest.TestCase):
    def test_luo_0700_ja_korjaa_laajat_oikeudet(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "yks"
            yksityinen.tarkista_yksityinen_hakemisto(p)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700)
            os.chmod(p, 0o755)
            yksityinen.tarkista_yksityinen_hakemisto(p)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700)

    def test_symlinkki_ja_tiedosto_hylataan(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "oikea").mkdir()
            os.symlink(Path(d) / "oikea", Path(d) / "linkki")
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yksityinen.tarkista_yksityinen_hakemisto(Path(d) / "linkki")
            (Path(d) / "f").write_text("x")
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yksityinen.tarkista_yksityinen_hakemisto(Path(d) / "f")

    def test_toisen_kayttajan_kansio_hylataan(self):
        with tempfile.TemporaryDirectory() as d:
            vanha = os.geteuid
            try:
                os.geteuid = lambda: vanha() + 1
                with self.assertRaises(yksityinen.YksityinenVirhe):
                    yksityinen.tarkista_yksityinen_hakemisto(d)
            finally:
                os.geteuid = vanha


class MalliTesti(unittest.TestCase):
    def test_vain_loopback_ip(self):
        self.assertTrue(yksityinen.sallittu_malli_osoite("http://" + ip(127, 0, 0, 1) + ":8081"))
        self.assertTrue(yksityinen.sallittu_malli_osoite("http://[::1]:8081"))
        for paha in ("https://" + ip(127, 0, 0, 1), "http://localhost:8081", "http://example.com", "http://" + ip(10, 0, 0, 5),
                     "http://u:p@" + ip(127, 0, 0, 1), "", "roskaa"):
            self.assertFalse(yksityinen.sallittu_malli_osoite(paha), paha)

    def test_ulkoiseen_osoitteeseen_ei_lahteta_mitaan(self):
        kutsuttu = []
        with self.assertRaises(yksityinen.YksityinenVirhe):
            yksityinen.kysy_paikalliselta("m", [], "", "http://example.com", avaa=lambda *a, **k: kutsuttu.append(1))
        self.assertEqual(kutsuttu, [])

    def test_pyynto_ja_vastaus(self):
        saatu = {}

        class Vast(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def avaa(pyynto, timeout):
            saatu["url"] = pyynto.full_url
            saatu["runko"] = json.loads(pyynto.data)
            return Vast(json.dumps({"choices": [{"message": {"content": " Hei. "}}]}).encode())
        v = yksityinen.kysy_paikalliselta("MUISTI", [{"rooli": "tony", "teksti": "moi"}], "OHJE", avaa=avaa)
        self.assertEqual(v, "Hei.")
        self.assertTrue(saatu["url"].endswith("/v1/chat/completions"))
        viestit = saatu["runko"]["messages"]
        self.assertIn("OHJE", viestit[0]["content"])
        self.assertIn("MUISTI", viestit[0]["content"])
        self.assertEqual(viestit[1], {"role": "user", "content": "moi"})

    def test_virhe_ei_vuoda_sisaltoa(self):
        def avaa(*a, **k):
            raise OSError("salainen-sisalto")
        with self.assertRaises(yksityinen.YksityinenVirhe) as c:
            yksityinen.kysy_paikalliselta("m", [], avaa=avaa)
        self.assertNotIn("salainen", str(c.exception))


class OhjeetTesti(unittest.TestCase):
    def test_ydinohjeet_luetaan_mutta_ei_ulkopuolelta_eika_gitista(self):
        with tempfile.TemporaryDirectory() as d:
            juuri = Path(d) / "ohjeet"
            (juuri / ".git").mkdir(parents=True)
            (juuri / ".git" / "x.md").write_text("GIT")
            (juuri / "saannot.md").write_text("Saanto 1")
            (Path(d) / "ulko.md").write_text("ULKO")
            os.symlink(Path(d) / "ulko.md", juuri / "linkki.md")
            t = yksityinen.lue_ydinohjeet(juuri)
            self.assertIn("Saanto 1", t)
            self.assertNotIn("GIT", t)
            self.assertNotIn("ULKO", t)

    def test_ehdotus_ei_muuta_ohjeita_ennen_hyvaksyntaa(self):
        with tempfile.TemporaryDirectory() as d:
            eh, oh = Path(d) / "eh", Path(d) / "oh"
            oh.mkdir()
            (oh / "saannot.md").write_text("vanha\n")
            vastaus = yksityinen.irrota_ehdotukset(
                "Ehdotan.\n[[EHDOTUS saannot.md]]\nuusi\n[[/EHDOTUS]]\nValmis.",
                lambda n, t: yksityinen.tallenna_ehdotus(eh, n, t))
            self.assertNotIn("uusi", vastaus)
            self.assertIn("ei ole voimassa", vastaus)
            self.assertEqual((oh / "saannot.md").read_text(), "vanha\n")
            [eid] = yksityinen.listaa_ehdotukset(eh)
            self.assertEqual(stat.S_IMODE((eh / (eid + ".json")).stat().st_mode), 0o600)
            d1 = yksityinen.lue_ehdotus(eh, eid)
            for paha in ("", "kylla", "kyll\u00e4 00000000", "kyll\u00e4 " + d1["sha"][:8] + " x"):
                with self.assertRaises(yksityinen.YksityinenVirhe):
                    yksityinen.hyvaksy_ehdotus(eh, oh, eid, paha, suorita=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
            self.assertEqual((oh / "saannot.md").read_text(), "vanha\n")
            yksityinen.hyvaksy_ehdotus(eh, oh, eid, "kyll\u00e4 " + d1["sha"][:8],
                                       suorita=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
            self.assertEqual((oh / "saannot.md").read_text(), "uusi\n")
            self.assertEqual(yksityinen.listaa_ehdotukset(eh), [])

    def test_muokattu_ehdotus_ei_mene_lapi(self):
        with tempfile.TemporaryDirectory() as d:
            eid = yksityinen.tallenna_ehdotus(d, "a.md", "teksti\n")
            p = Path(d) / (eid + ".json")
            j = json.loads(p.read_text())
            j["teksti"] = "vaihdettu\n"
            p.write_text(json.dumps(j))
            with self.assertRaises(yksityinen.YksityinenVirhe):
                yksityinen.lue_ehdotus(d, eid)

    def test_ehdotuksen_nimi_ei_paase_polkuun(self):
        with tempfile.TemporaryDirectory() as d:
            for nimi in ("../x.md", "a/b.md", "x.sh", ".md"):
                with self.assertRaises(yksityinen.YksityinenVirhe):
                    yksityinen.tallenna_ehdotus(d, nimi, "x")

    def test_github_etarepo_estaa_ydinohjeiden_pushin(self):
        def suorita(kom, **k):
            if "get-url" in kom:
                return SimpleNamespace(returncode=0, stdout="https://github.com/x/y.git")
            return SimpleNamespace(returncode=0, stdout="")
        with self.assertRaises(yksityinen.YksityinenVirhe):
            yksityinen.julkaise_kirjastoon("k", "k/a.md", suorita)


class MittaaTesti(unittest.TestCase):
    def test_mittaus_ja_ulkoinen_osoite(self):
        from aivo import mittaa_malli

        class Vast(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        avaa = lambda p, timeout: Vast(json.dumps({"choices": [{"message": {"content": "vastaus"}}],
                                                    "usage": {"completion_tokens": 10}}).encode())
        t = mittaa_malli.mittaa("http://" + ip(127, 0, 0, 1) + ":8081", avaa)
        self.assertEqual(len(t), len(mittaa_malli.KYSYMYKSET))
        self.assertEqual(t[0]["tokenia"], 10)
        with self.assertRaises(yksityinen.YksityinenVirhe):
            mittaa_malli.mittaa("http://example.com", avaa)


if __name__ == "__main__":
    unittest.main()
