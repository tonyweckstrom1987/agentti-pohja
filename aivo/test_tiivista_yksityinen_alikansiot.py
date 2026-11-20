"""Yksityisen tilan tiivistys ja alueen alikansiot: ehdotukset/hylatyt/ (hylkays, #352) ja ydinohjeet/.git eivat
kaada tiivistysta, mutta kaytettyjen kansioiden vieras omistaja ja linkit hylataan edelleen. Kuiva-ajo ei luo kansioita
ja kertoo puuttuvat nimet. Ajo: python3 -m unittest aivo.test_tiivista_yksityinen_alikansiot"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from aivo import alue, tiivista, tiivista_yksityinen as ty, tiivistelma, yksityinen, yksityinen_alue

NYT = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
KID = "20261118-100000-0a1b2c3d"
EID = "0a1b2c3d4e5f"


def kirjoita_keskustelu(chat, kid=KID, viimeinen=NYT - timedelta(hours=1)):
    viestit = [("tony", "Haluan lyhyet vastaukset."), ("aivo", "Selva."), ("tony", "Kiitos.")]
    rivit = []
    for n, (rooli, teksti) in enumerate(viestit):
        aika = viimeinen - timedelta(minutes=len(viestit) - 1 - n)
        rivit.append(json.dumps({"aika": aika.isoformat(timespec="seconds"), "rooli": rooli, "teksti": teksti,
                                 "lahteet": []}))
    p = Path(chat) / (kid + ".jsonl")
    p.write_text("\n".join(rivit) + "\n", encoding="utf-8")
    os.chmod(p, 0o600)


class Malli:
    def __init__(self):
        self.kutsut = 0

    def __call__(self, ohje, data):
        self.kutsut += 1
        return json.dumps({"kohdat": []})


class Pohja(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.juuri = Path(self.tmp.name) / "aivo-yksityinen"
        self.a = yksityinen_alue.yksityinen_alue(self.juuri)
        self.a.valmista()
        kirjoita_keskustelu(self.a.polku("chat"))

    def aja(self, **kw):
        with redirect_stdout(io.StringIO()):
            return ty.aja(self.juuri, Malli(), nyt=NYT, **kw)

    def hylatty_ehdotus(self):
        """Tekee hylatyt/-kansion oikealla koodipolulla (yksityinen.hylkaa_ehdotus), kuten chat-sivun hylkays."""
        ehd = self.a.polku("ehdotukset")
        p = ehd / (EID + ".json")
        p.write_text("{}", encoding="utf-8")
        os.chmod(p, 0o600)
        yksityinen.hylkaa_ehdotus(ehd, EID)
        self.assertTrue((ehd / "hylatyt" / (EID + ".json")).is_file())

    def git_ydinohjeet(self):
        g = self.a.polku("ydinohjeet") / ".git"
        g.mkdir(mode=0o700)
        (g / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


class SallitutAlikansiotTesti(Pohja):
    def test_hylatyt_olemassa_tiivistys_toimii(self):
        self.hylatty_ehdotus()
        t = self.aja()
        self.assertEqual((t["kutsuja"], t["tiivistetty"]), (1, 1))
        self.assertEqual(len(tiivistelma.luettele(self.a.polku("tiivistelmat"))), 1)

    def test_ydinohjeet_git_olemassa_tiivistys_toimii(self):
        self.git_ydinohjeet()
        t = self.aja()
        self.assertEqual((t["kutsuja"], t["tiivistetty"]), (1, 1))

    def test_molemmat_ja_kuiva_ajo_komentorivilta(self):
        self.hylatty_ehdotus()
        self.git_ydinohjeet()
        ulos = io.StringIO()
        with redirect_stdout(ulos), redirect_stderr(io.StringIO()), mock.patch.object(ty, "_nyt", return_value=NYT):
            rc = tiivista.main(["--alue", "yksityinen", "--kuiva", "--juuri", str(self.juuri)])
        self.assertEqual(rc, 0)
        self.assertIn("tiivistettaisiin %s viestit 0-2" % KID, ulos.getvalue())

    def test_tiivistys_ei_muuta_muiden_alikansioiden_sisaltoa(self):
        self.hylatty_ehdotus()
        self.git_ydinohjeet()
        hyl = self.a.polku("ehdotukset") / "hylatyt" / (EID + ".json")
        os.chmod(hyl, 0o644)                       # tiivistys ei tarkista eika korjaa kansioita, joita se ei kayta
        self.aja()
        self.assertEqual(os.stat(hyl).st_mode & 0o777, 0o644)
        self.assertTrue((self.a.polku("ydinohjeet") / ".git" / "HEAD").is_file())

    def test_koko_alueen_tarkistus_on_ennallaan(self):
        """Rajaus koskee vain tiivistysta: Alue.valmista() ilman rajausta hylkaa edelleen hylatyt/-kansion."""
        self.hylatty_ehdotus()
        with self.assertRaises(alue.AlueVirhe):
            self.a.valmista()


class HylataanEdelleenTesti(Pohja):
    def odota_virhe(self):
        with self.assertRaises(ty.TiivistaVirhe):
            self.aja()

    def test_vieras_omistaja_kaytetyssa_tiedostossa_hylataan(self):
        vieras = self.a.polku("chat") / (KID + ".jsonl")
        alkup = os.lstat

        class St:
            def __init__(self, st):
                self._st = st
                self.st_uid = os.geteuid() + 1

            def __getattr__(self, n):
                return getattr(self._st, n)

        def lstat(p, *a, **kw):
            st = alkup(p, *a, **kw)
            return St(st) if Path(p) == vieras else st

        with mock.patch("aivo.alue.os.lstat", side_effect=lstat):
            self.odota_virhe()

    def test_vieras_omistaja_kansiossa_hylataan(self):
        with mock.patch("aivo.alue.os.geteuid", return_value=os.geteuid() + 1):
            self.odota_virhe()

    def test_symlink_kaytetyssa_kansiossa_hylataan(self):
        os.symlink("/etc/hostname", self.a.polku("chat") / "linkki.jsonl")
        self.odota_virhe()

    def test_symlink_tiivistelmissa_hylataan(self):
        os.symlink("/etc/hostname", self.a.polku("tiivistelmat") / "x.json")
        self.odota_virhe()

    def test_kaytetty_kansio_linkkina_hylataan(self):
        ind = self.a.polku("indeksi")
        os.rename(ind, self.juuri / "muualla")
        os.symlink(self.juuri / "muualla", ind)
        self.odota_virhe()

    def test_alikansio_kaytetyssa_kansiossa_hylataan(self):
        (self.a.polku("chat") / "hylatyt").mkdir(mode=0o700)      # sallittu vain ehdotukset/-kansiossa
        self.odota_virhe()

    def test_git_kaytetyssa_kansiossa_hylataan(self):
        (self.a.polku("tiivistelmat") / ".git").mkdir(mode=0o700)  # sallittu vain ydinohjeet/-kansiossa
        self.odota_virhe()

    def test_vieras_alikansio_rajauksessa_hylataan(self):
        with self.assertRaises(alue.AlueVirhe):
            self.a.valmista(alikansiot=("chat", "muu"))
        with self.assertRaises(alue.AlueVirhe):
            self.a.valmista(alikansiot=())


class KuivaAjoTuoreellaAsennuksellaTesti(unittest.TestCase):
    """Tuore asennus docs/aivo-yksityinen.md:n mukaan: juuri ja chat, muisti, ehdotukset (ei tiivistelmat/indeksi)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.juuri = Path(self.tmp.name) / "aivo-yksityinen"
        for n in ("chat", "muisti", "ehdotukset"):
            (self.juuri / n).mkdir(mode=0o700, parents=True)
        os.chmod(self.juuri, 0o700)
        kirjoita_keskustelu(self.juuri / "chat")

    def kuiva(self):
        ulos, virhe = io.StringIO(), io.StringIO()
        with redirect_stdout(ulos), redirect_stderr(virhe), mock.patch.object(ty, "_nyt", return_value=NYT):
            rc = tiivista.main(["--alue", "yksityinen", "--kuiva", "--juuri", str(self.juuri)])
        return rc, ulos.getvalue(), virhe.getvalue()

    def test_kuiva_ajo_kertoo_puuttuvat_kansiot_eika_luo_mitaan(self):
        rc, _, virhe = self.kuiva()
        self.assertEqual(rc, 1)
        self.assertIn("puuttuu: tiivistelmat, indeksi", virhe)
        self.assertNotIn(str(self.juuri), virhe)
        self.assertEqual(sorted(os.listdir(self.juuri)), ["chat", "ehdotukset", "muisti"])

    def test_kuiva_ajo_toimii_kun_kansiot_on_luotu_ohjeen_mukaan(self):
        for n in ty.KUIVA_VAATII:                     # vastaa: install -d -m 700 ... tiivistelmat indeksi
            (self.juuri / n).mkdir(mode=0o700)
        rc, ulos, _ = self.kuiva()
        self.assertEqual(rc, 0)
        self.assertIn("tiivistettaisiin %s viestit 0-2" % KID, ulos)
        self.assertNotIn("siirrot", os.listdir(self.juuri))       # kuiva-ajo ei vaadi eika luo siirrot-kansiota

    def test_varsinainen_ajo_luo_kaytetyt_kansiot(self):
        with redirect_stdout(io.StringIO()):
            t = ty.aja(self.juuri, Malli(), nyt=NYT)
        self.assertEqual(t["tiivistetty"], 1)
        for n in ty.KAYTETYT_ALIKANSIOT:
            self.assertEqual(os.stat(self.juuri / n).st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
