"""Testit Aivon yksityisen tilan paivakatolle (tiivista_yksityinen.aja) ja jalkijulkaisulle (yksityinen.julkaise_kirjastoon).

Ei verkkoa: malli ja git korvataan testissa. Testit eivat ohita eivatka muuta kattoa: PAIVAKATTO luetaan moduulista
ja raja tarkistetaan arvoilla PAIVAKATTO-1, PAIVAKATTO ja PAIVAKATTO+1."""
import io
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from types import SimpleNamespace

from aivo import tiivista_yksityinen as ty, yksityinen
from aivo.test_tiivista_yksityinen import KID, NYT, Pohja, Vaarennos, kirjoita_keskustelu


class KattoPohja(Pohja):
    def keskustelut(self, n):
        """n erillista hiljaista keskustelua; jokaisesta tulee tasan yksi mallikutsu."""
        for i in range(n):
            kid = "20261118-%06d-%08x" % (100000 + i, 0xA0000000 + i)
            kirjoita_keskustelu(self.chat, kid, [("tony", "Haluan lyhyet vastaukset."), ("aivo", "Selva.")],
                                NYT - timedelta(hours=1, minutes=i))

    def aseta_kutsuja(self, n, katto_kirjattu=""):
        ty.kirjoita_tila(self.a.polku("indeksi"), {"versio": 1, "yritykset": {}, "paiva": ty._paiva(NYT),
                                                   "kutsuja": n, "katto_kirjattu": katto_kirjattu})

    def tila(self):
        return ty.lue_tila(self.a.polku("indeksi"), NYT)

    def aja_maksullinen(self, kysy, **kw):
        rivit = []
        with redirect_stdout(io.StringIO()):
            t = ty.aja(self.juuri, kysy, nyt=NYT, taustapaa="openrouter", tulosta=rivit.append, **kw)
        return t, rivit

    def kattorivit(self, rivit):
        return [r for r in rivit if "paivakatto" in r]


class PaivakattoRajaTesti(KattoPohja):
    def test_katto_on_kymmenen(self):
        self.assertEqual(ty.PAIVAKATTO, 10)       # arvon muutos vaatii Tonin luvan; testi tekee sen nakyvaksi

    def test_katto_miinus_yksi_sallii_viimeisen_kutsun_eika_ole_katto(self):
        self.keskustelut(1)
        self.aseta_kutsuja(ty.PAIVAKATTO - 1)
        k = Vaarennos()
        t, rivit = self.aja_maksullinen(k)
        self.assertEqual(len(k.syotteet), 1)
        self.assertEqual(t["kutsuja"], 1)
        self.assertFalse(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], ty.PAIVAKATTO)
        self.assertEqual(self.kattorivit(rivit), [])

    def test_katto_miinus_yksi_ja_toinen_keskustelu_pysahtyy_kattoon(self):
        self.keskustelut(2)
        self.aseta_kutsuja(ty.PAIVAKATTO - 1)
        k = Vaarennos()
        t, rivit = self.aja_maksullinen(k)
        self.assertEqual(len(k.syotteet), 1)      # toinen kutsu estyy: laskuri on nyt tasan PAIVAKATTO
        self.assertTrue(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], ty.PAIVAKATTO)
        self.assertEqual(len(self.kattorivit(rivit)), 1)

    def test_tasan_katto_estaa_kutsun(self):
        self.keskustelut(1)
        self.aseta_kutsuja(ty.PAIVAKATTO)
        k = Vaarennos()
        t, rivit = self.aja_maksullinen(k)
        self.assertEqual(k.syotteet, [])
        self.assertEqual(t["kutsuja"], 0)
        self.assertTrue(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], ty.PAIVAKATTO)
        self.assertEqual(len(self.kattorivit(rivit)), 1)

    def test_katon_ylitys_estaa_kutsun_eika_kasvata_laskuria(self):
        self.keskustelut(1)
        self.aseta_kutsuja(ty.PAIVAKATTO + 1)
        k = Vaarennos()
        t, _ = self.aja_maksullinen(k)
        self.assertEqual(k.syotteet, [])
        self.assertTrue(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], ty.PAIVAKATTO + 1)

    def test_tyhjasta_tilasta_tehdaan_tasan_katon_verran_kutsuja(self):
        self.keskustelut(ty.PAIVAKATTO + 3)
        k = Vaarennos()
        t, rivit = self.aja_maksullinen(k)
        self.assertEqual(len(k.syotteet), ty.PAIVAKATTO)
        self.assertEqual(t["kutsuja"], ty.PAIVAKATTO)
        self.assertTrue(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], ty.PAIVAKATTO)
        self.assertEqual(len(self.kattorivit(rivit)), 1)

    def test_uusi_paiva_nollaa_laskurin(self):
        self.keskustelut(1)
        ty.kirjoita_tila(self.a.polku("indeksi"), {"versio": 1, "yritykset": {}, "paiva": "2026-11-17",
                                                   "kutsuja": ty.PAIVAKATTO + 5, "katto_kirjattu": "2026-11-17"})
        k = Vaarennos()
        t, _ = self.aja_maksullinen(k)
        self.assertEqual(len(k.syotteet), 1)
        self.assertFalse(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], 1)


class MaksullinenJaIlmainenTesti(KattoPohja):
    def test_maksullinen_malli_kasvattaa_laskuria_kutsua_kohden(self):
        self.keskustelut(3)
        k = Vaarennos()
        self.aja_maksullinen(k)
        self.assertEqual(len(k.syotteet), 3)
        self.assertEqual(self.tila()["kutsuja"], 3)

    def test_ilmainen_loopback_ei_kasvata_laskuria(self):
        self.keskustelut(3)
        k = Vaarennos()
        with redirect_stdout(io.StringIO()):
            ty.aja(self.juuri, k, nyt=NYT, taustapaa="loopback")
        self.assertEqual(len(k.syotteet), 3)
        self.assertEqual(self.tila()["kutsuja"], 0)

    def test_ilmaista_mallia_katto_ei_rajaa_vaikka_laskuri_on_taynna(self):
        self.keskustelut(ty.PAIVAKATTO + 2)
        self.aseta_kutsuja(ty.PAIVAKATTO + 1)
        k = Vaarennos()
        rivit = []
        with redirect_stdout(io.StringIO()):
            t = ty.aja(self.juuri, k, nyt=NYT, taustapaa="loopback", tulosta=rivit.append)
        self.assertEqual(len(k.syotteet), ty.PAIVAKATTO + 2)
        self.assertFalse(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], ty.PAIVAKATTO + 1)
        self.assertEqual(self.kattorivit(rivit), [])

    def test_katto_taynna_poikkeus_palauttaa_varauksen(self):
        self.keskustelut(1)
        self.aseta_kutsuja(3)

        def kysy(ohje, data):
            raise ty.KattoTaynna("taynna")
        t, _ = self.aja_maksullinen(kysy)
        self.assertTrue(t["katto"])
        self.assertEqual(self.tila()["kutsuja"], 3)
        self.assertEqual(t["kutsuja"], 0)


class KattoKirjattuTesti(KattoPohja):
    def test_katto_kirjataan_tilaan_ja_lokiin_kerran_paivassa(self):
        self.keskustelut(1)
        self.aseta_kutsuja(ty.PAIVAKATTO)
        _, rivit1 = self.aja_maksullinen(Vaarennos())
        self.assertEqual(self.tila()["katto_kirjattu"], ty._paiva(NYT))
        self.assertEqual(len(self.kattorivit(rivit1)), 1)
        self.assertIn("%d/%d" % (ty.PAIVAKATTO, ty.PAIVAKATTO), self.kattorivit(rivit1)[0])
        _, rivit2 = self.aja_maksullinen(Vaarennos())
        self.assertEqual(self.kattorivit(rivit2), [])          # sama paiva: ei toista kirjausta
        loki = (self.a.polku("indeksi") / ty.LOKI_TIEDOSTO).read_text()
        self.assertEqual(loki.count("paivakatto"), 1)

    def test_aiemmin_kirjattu_katto_ei_kirjaudu_uudelleen(self):
        self.keskustelut(1)
        self.aseta_kutsuja(ty.PAIVAKATTO, katto_kirjattu=ty._paiva(NYT))
        t, rivit = self.aja_maksullinen(Vaarennos())
        self.assertTrue(t["katto"])
        self.assertEqual(self.kattorivit(rivit), [])

    def test_edellisen_paivan_kirjaus_ei_estaa_tanaan_kirjaamasta(self):
        self.keskustelut(1)
        self.aseta_kutsuja(ty.PAIVAKATTO, katto_kirjattu="2026-11-17")
        _, rivit = self.aja_maksullinen(Vaarennos())
        self.assertEqual(len(self.kattorivit(rivit)), 1)
        self.assertEqual(self.tila()["katto_kirjattu"], ty._paiva(NYT))

    def test_katto_ei_kirjaudu_ilman_kattoa(self):
        self.keskustelut(1)
        self.aseta_kutsuja(0)
        self.aja_maksullinen(Vaarennos())
        self.assertEqual(self.tila()["katto_kirjattu"], "")


class FakeGitJalki:
    """Korvaa git-kutsut. paikallinen/etana: (returncode, stdout) komennoille `rev-parse HEAD` ja
    `rev-parse --verify -q refs/remotes/origin/<haara>`."""
    def __init__(self, paikallinen=(0, "aaa\n"), etana=(0, "aaa\n"), push_rc=0, status_out="", commit_rc=0):
        self.paikallinen, self.etana, self.push_rc = paikallinen, etana, push_rc
        self.status_out, self.commit_rc, self.kutsut = status_out, commit_rc, []

    def __call__(self, kom, **k):
        a = list(kom[3:])
        self.kutsut.append(a)
        if a[:2] == ["rev-parse", "--is-inside-work-tree"]:
            return SimpleNamespace(returncode=0, stdout="true\n")
        if a == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=self.paikallinen[0], stdout=self.paikallinen[1])
        if a[:2] == ["rev-parse", "--verify"]:
            return SimpleNamespace(returncode=self.etana[0], stdout=self.etana[1])
        if a[0] == "status":
            return SimpleNamespace(returncode=0, stdout=self.status_out)
        if a[0] == "commit":
            return SimpleNamespace(returncode=self.commit_rc, stdout="")
        if a[0] == "push":
            return SimpleNamespace(returncode=self.push_rc, stdout="")
        return SimpleNamespace(returncode=0, stdout="")

    def pushit(self):
        return [a for a in self.kutsut if a and a[0] == "push"]


KOHDE = ("main", ["https://git.example.invalid/aivo/ydinohjeet.git"])  # osoite on pelkkaa dataa: julkaisu kayttaa vain haaraa


def julkaise(git, kohde=KOHDE, jalkijulkaise=True):
    return yksityinen.julkaise_kirjastoon("k", "k/a.md", git, kohde_tiedot=kohde, jalkijulkaise=jalkijulkaise)


class JalkijulkaisuTesti(unittest.TestCase):
    def test_samat_haarat_ei_pushia(self):
        g = FakeGitJalki()
        self.assertEqual(julkaise(g), "ei-muutosta")
        self.assertEqual(g.pushit(), [])

    def test_eri_haarat_pushaa_ja_onnistuu(self):
        g = FakeGitJalki(etana=(0, "bbb\n"))
        self.assertEqual(julkaise(g), "pushattu")
        self.assertEqual(g.pushit(), [["push", "origin", "HEAD:refs/heads/main"]])

    def test_eri_haarat_push_epaonnistuu(self):
        g = FakeGitJalki(etana=(0, "bbb\n"), push_rc=1)
        self.assertEqual(julkaise(g), "push-epaonnistui")
        self.assertEqual(len(g.pushit()), 1)

    def test_etahaaraa_ei_ole_pushaa_vaikka_tulosteet_olisivat_samat(self):
        g = FakeGitJalki(etana=(1, "aaa\n"))
        self.assertEqual(julkaise(g), "pushattu")
        self.assertEqual(len(g.pushit()), 1)

    def test_paikallista_headia_ei_saada_luettua_pushaa(self):
        g = FakeGitJalki(paikallinen=(1, "aaa\n"))
        self.assertEqual(julkaise(g), "pushattu")
        self.assertEqual(len(g.pushit()), 1)

    def test_etahaaran_viite_nimetaan_haaran_mukaan(self):
        g = FakeGitJalki()
        julkaise(g, kohde=("oma-haara", KOHDE[1]))
        self.assertIn(["rev-parse", "--verify", "-q", "refs/remotes/origin/oma-haara"], g.kutsut)
        g2 = FakeGitJalki(etana=(0, "bbb\n"))
        julkaise(g2, kohde=("oma-haara", KOHDE[1]))
        self.assertEqual(g2.pushit(), [["push", "origin", "HEAD:refs/heads/oma-haara"]])

    def test_ilman_jalkijulkaisua_eri_haarat_eivat_pushaa(self):
        g = FakeGitJalki(etana=(0, "bbb\n"))
        self.assertEqual(julkaise(g, jalkijulkaise=False), "ei-muutosta")
        self.assertEqual(g.pushit(), [])

    def test_ilman_etakohdetta_ei_pushia_vaikka_jalkijulkaisu_paalla(self):
        g = FakeGitJalki(etana=(0, "bbb\n"))
        self.assertEqual(julkaise(g, kohde=None), "ei-muutosta")
        self.assertEqual(g.pushit(), [])

    def test_uusi_muutos_pushataan_ja_epaonnistuminen_kirjataan(self):
        ok = FakeGitJalki(status_out="M a.md\n")
        self.assertEqual(julkaise(ok), "pushattu")
        self.assertEqual(len(ok.pushit()), 1)
        huono = FakeGitJalki(status_out="M a.md\n", push_rc=1)
        self.assertEqual(julkaise(huono), "push-epaonnistui")

    def test_ilman_etakohdetta_muutos_commitoidaan_mutta_ei_pushata(self):
        g = FakeGitJalki(status_out="M a.md\n")
        self.assertEqual(julkaise(g, kohde=None), "ei-etaa")
        self.assertEqual(g.pushit(), [])


if __name__ == "__main__":
    unittest.main()
