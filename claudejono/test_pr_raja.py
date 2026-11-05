"""PR-rajan testit: raja 10 (sallittu) ja 11 (purkutila), luokittelu, suodatus ja ajurin kytkenta. Ei verkkoa: haku on injektoitu."""
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentti import pr_kutsu
from claudejono import ajuri, pr_raja, silmukka
from claudejono.test_ajuri import Pohja, git

NYT = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def pr(n, paivia=1, tila="clean", koko=10, draft=False):
    return {"number": n, "title": "PR %d" % n, "draft": draft, "head": {"ref": "yo/haara-%d" % n},
            "updated_at": (NYT - timedelta(days=paivia)).isoformat().replace("+00:00", "Z"),
            "mergeable_state": tila, "additions": koko, "deletions": 0, "changed_files": 1}


def haku(prt):
    """Feikki-API: listaus ja yksittaishaku samasta datasta."""
    def hae(url):
        if "/pulls?" in url:
            return [{k: v for k, v in p.items() if k not in ("mergeable_state", "additions", "deletions", "changed_files")} for p in prt]
        n = int(url.rsplit("/", 1)[1])
        return next(p for p in prt if p["number"] == n)
    return hae


class Raja(unittest.TestCase):
    def test_tasan_kymmenen_on_sallittu(self):
        t = pr_raja.keraa(["r"], NYT, haku([pr(i) for i in range(1, 11)]))
        self.assertEqual(t, {})

    def test_yksitoista_laukaisee_purkutilan(self):
        prt = [pr(i) for i in range(1, 12)]
        t = pr_raja.keraa(["r"], NYT, haku(prt))
        self.assertEqual(list(t), ["r"])
        self.assertEqual(t["r"]["maara"], 11)
        self.assertTrue(t["r"]["yli"])

    def test_raja_on_kymmenen(self):
        self.assertEqual(pr_raja.RAJA, 10)

    def test_luokittelu(self):
        prt = [pr(1, tila="dirty"), pr(2, paivia=20), pr(3, paivia=20, tila="dirty"), pr(4)] + [pr(i) for i in range(5, 12)]
        t = pr_raja.keraa(["r"], NYT, haku(prt))["r"]
        self.assertEqual([p["number"] for p in t["konflikti"]], [1, 3])     # konflikti voittaa vanhentumisen
        self.assertEqual([p["number"] for p in t["vanhentunut"]], [2])
        self.assertEqual(len(t["tarkastajalle"]), 8)

    def test_tarkastusjarjestys_pienin_ensin_luonnokset_viimeisina(self):
        prt = [pr(1, koko=500), pr(2, koko=5, draft=True), pr(3, koko=20), pr(4, koko=20)] + [pr(i, koko=100) for i in range(5, 12)]
        t = pr_raja.keraa(["r"], NYT, haku(prt))["r"]
        jarj = [p["number"] for p in t["tarkastajalle"]]
        self.assertEqual(jarj[:2], [3, 4])
        self.assertEqual(jarj[-1], 2)

    def test_lukuvirhe_ei_estaa_eika_kaada(self):
        def rikki(url):
            raise OSError("ei verkkoa")
        self.assertEqual(pr_raja.keraa(["r"], NYT, rikki), {})

    def test_tuntematon_tila_ei_ole_konflikti(self):
        prt = [dict(pr(i), mergeable_state=None) for i in range(1, 12)]
        t = pr_raja.keraa(["r"], NYT, haku(prt))["r"]
        self.assertEqual(t["konflikti"], [])

    def test_repolista_vastaa_pr_kutsua(self):
        self.assertEqual(tuple(pr_raja.PR_REPOT), tuple(pr_kutsu.REPOT))


class Tehtavat(unittest.TestCase):
    def setUp(self):
        prt = [pr(1, tila="dirty"), pr(2, tila="dirty"), pr(3, paivia=30)] + [pr(i) for i in range(4, 13)]
        self.tilat = pr_raja.keraa(["agentti-pohja"], NYT, haku(prt))

    def test_purkutehtava_lapaisee_porttitarkistimen(self):
        purku = pr_raja.purkutehtavat(self.tilat)
        self.assertEqual([t["nimi"] for t in purku], ["purku-agentti-pohja-rebase-pr-1", "purku-agentti-pohja-rebase-pr-2"])
        hyva, sunn, huono = silmukka.suodata(purku)
        self.assertEqual((len(hyva), sunn, huono), (2, [], []))

    def test_purkutehtava_kieltaa_force_muualle_ja_mergen(self):
        s = pr_raja.purkutehtavat(self.tilat)[0]["sisalto"]
        self.assertIn("Force-with-lease sallitaan vain agentin omaan yo/-haaraan", s)
        self.assertIn("ala yhdista mitaan", s)

    def test_tonylle_sulkuehdotus_ei_sulje(self):
        r = pr_raja.tonylle_rivit(self.tilat)
        self.assertIn("Sulkuehdotus", r)
        self.assertIn("Tony paattaa", r)
        self.assertIn("#3", r)
        self.assertIn("Tarkastusjarjestys", r)

    def test_kehitys_hylataan_rajan_ylittavasta_reposta_mutta_muut_menevat(self):
        ehd = [{"nimi": "a", "sisalto": "# Paranna agentti-pohja ajuria"}, {"nimi": "b", "sisalto": "# Paranna evalkitia"}]
        ok, huono = pr_raja.suodata_kehitys(ehd, self.tilat)
        self.assertEqual([t["nimi"] for t in ok], ["b"])
        self.assertEqual([t["nimi"] for t, _ in huono], ["a"])

    def test_ei_rajaa_ei_suodatusta(self):
        ehd = [{"nimi": "a", "sisalto": "# agentti-pohja"}]
        self.assertEqual(pr_raja.suodata_kehitys(ehd, {}), (ehd, []))
        self.assertEqual(pr_raja.kehotelisa({}), "")

    def test_valimuisti(self):
        with tempfile.TemporaryDirectory() as d:
            kutsut = []
            def hae(url):
                kutsut.append(url)
                return haku([pr(i) for i in range(1, 12)])(url)
            a = pr_raja.keraa_valimuistilla(["r"], d, NYT, hae)
            n = len(kutsut)
            b = pr_raja.keraa_valimuistilla(["r"], d, NYT + timedelta(minutes=5), hae)
            self.assertEqual(len(kutsut), n)
            self.assertEqual(list(a), list(b))
            pr_raja.keraa_valimuistilla(["r"], d, NYT + timedelta(hours=1), hae)
            self.assertGreater(len(kutsut), n)


class AjuriKytkenta(Pohja):
    def origin_tiedostot(self):
        t = self.tmp / ("tark" + str(len(list(self.tmp.iterdir()))))
        subprocess.run(["git", "clone", "-q", str(self.origin), str(t)], check=True, capture_output=True)
        return sorted(str(p.relative_to(t)) for p in (t / ajuri.JONO).rglob("*.md")) if (t / ajuri.JONO).exists() else []

    def malli(self, tulos, kutsut):
        def f(kom, **kw):
            kutsut.append(kom)
            return subprocess.CompletedProcess(kom, 0, json.dumps({"subtype": "success", "result": tulos}), "")
        return f

    def test_yli_rajan_ajuri_kirjoittaa_purun_ja_hylkaa_kehityksen(self):
        prt = [pr(1, tila="dirty"), pr(2, paivia=30)] + [pr(i) for i in range(3, 13)]
        tilat = pr_raja.keraa(["agentti-pohja"], NYT, haku(prt))
        vastaus = json.dumps({"tehtavat": [
            {"nimi": "uusi-ominaisuus", "sisalto": "# Lisaa ominaisuus agentti-pohja:an\n\nPisteet: hyoty 4, varmuus 4, vaiva 1 h\nSuunta: jatkoa (k1)\n"},
            {"nimi": "muu-repo", "sisalto": "# Evalkit\n\nPisteet: hyoty 4, varmuus 4, vaiva 1 h\nSuunta: jatkoa (k1)\n"}], "tonylle": ""})
        kutsut = []
        v = ajuri.suunnittele(self.repo, self.tmp, {}, self.malli(vastaus, kutsut), nyt=NYT, pr_tilat=tilat)
        self.assertIn("itsesuunnittelu", v)
        self.assertIn("PR-RAJA", str(kutsut))
        tied = " ".join(self.origin_tiedostot())
        self.assertIn("purku-agentti-pohja-rebase-pr-1", tied)
        self.assertIn("muu-repo", tied)
        self.assertNotIn("uusi-ominaisuus", tied)
        tonylle = (self.repo / ajuri.JONO / "tonylle.md").read_text()
        self.assertIn("Sulkuehdotus", tonylle)

    def test_alle_rajan_ei_muutosta(self):
        vastaus = json.dumps({"tehtavat": [
            {"nimi": "ominaisuus", "sisalto": "# agentti-pohja\n\nPisteet: hyoty 4, varmuus 4, vaiva 1 h\nSuunta: jatkoa (k1)\n"}], "tonylle": ""})
        kutsut = []
        ajuri.suunnittele(self.repo, self.tmp, {}, self.malli(vastaus, kutsut), nyt=NYT, pr_tilat={})
        self.assertNotIn("PR-RAJA", str(kutsut))
        self.assertIn("ominaisuus", " ".join(self.origin_tiedostot()))


if __name__ == "__main__":
    unittest.main()
