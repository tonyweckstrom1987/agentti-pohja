"""Tavoiteportin aanekas hylkays (PR #287:n korvaaja) ja tools/tarkista_tehtava.py."""
import contextlib
import fcntl
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from claudejono import ajuri, tavoite
from tools import tarkista_tehtava

NIMI = "2026-11-12-01-testi.md"


def pystyta(teksti, nimi=NIMI):
    repo = Path(tempfile.mkdtemp()) / "docs"
    teht = repo / "claude-jono" / "tehtavat"
    teht.mkdir(parents=True)
    (teht / nimi).write_text(teksti, encoding="utf-8")
    return repo, repo.parent


def tulos(repo, nimi=NIMI):
    return repo / "claude-jono" / "tulokset" / nimi


class Hylkaystulos(unittest.TestCase):
    def test_virheellinen_ensimmainen_rivi_antaa_epaonnistui_tuloksen(self):
        repo, tila = pystyta("# T\nTavoite: rahaa\n")
        hylatyt = []
        self.assertIsNone(ajuri.varaa_seuraava(repo, tila, 1, hylatyt=hylatyt))
        self.assertEqual(hylatyt, [tulos(repo)])
        t = tulos(repo).read_text(encoding="utf-8")
        self.assertIn("- Tila: EPAONNISTUI", t)
        self.assertIn("rivi 2", t)
        self.assertIn("portfolio, ansainta, Aivo, yllapito", t)

    def test_puuttuva_rivi_antaa_tuloksen(self):
        repo, tila = pystyta("# T\nei rivia\n")
        ajuri.varaa_seuraava(repo, tila, 1)
        self.assertIn("puuttuu", tulos(repo).read_text(encoding="utf-8"))

    def test_toista_kertaa_ei_kirjoiteta(self):
        repo, tila = pystyta("Tavoite: rahaa\n")
        h1, h2 = [], []
        ajuri.varaa_seuraava(repo, tila, 1, hylatyt=h1)
        ensin = tulos(repo).read_text(encoding="utf-8")
        tulos(repo).write_text(ensin + "MUOKATTU\n", encoding="utf-8")
        ajuri.varaa_seuraava(repo, tila, 1, hylatyt=h2)
        ajuri.varaa_seuraava(repo, tila, 2, hylatyt=h2)
        self.assertEqual((len(h1), h2), (1, []))
        self.assertTrue(tulos(repo).read_text(encoding="utf-8").endswith("MUOKATTU\n"))
        self.assertEqual(len((tila / tavoite.HYLKAYSLOKI).read_text().splitlines()), 1)

    def test_olemassa_olevaa_tulosta_ei_ylikirjoiteta(self):
        repo, tila = pystyta("Tavoite: rahaa\n")
        tulos(repo).parent.mkdir(parents=True)
        tulos(repo).write_text("vanha", encoding="utf-8")
        h = []
        self.assertIsNone(ajuri.kirjaa_hylkaystulos(repo, tila, repo / "claude-jono" / "tehtavat" / NIMI, "syy", h))
        self.assertEqual((tulos(repo).read_text(encoding="utf-8"), h), ("vanha", []))

    def _kirjoita_tulos_nostaa(self, virhe):
        vanha = ajuri.kirjoita_tulos
        def nosta(*a, **k):
            raise virhe
        ajuri.kirjoita_tulos = nosta
        self.addCleanup(setattr, ajuri, "kirjoita_tulos", vanha)

    def test_kirjoitusvirhe_ei_kaada_ajokierrosta_ja_nakyy_lokissa(self):
        """JonoVirhe (ei OSError) kirjoita_tulos-kutsusta: seuraava kelvollinen tehtava valitaan ja virhe kirjataan stderriin tehtavan nimella."""
        repo, tila = pystyta("Tavoite: rahaa\n")
        (repo / "claude-jono" / "tehtavat" / "2026-11-12-02-kelpaa.md").write_text("Tavoite: portfolio\n", encoding="utf-8")
        self._kirjoita_tulos_nostaa(ajuri.JonoVirhe("salaisuus-ei-tulosteta"))
        hylatyt = []
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            valittu = ajuri.varaa_seuraava(repo, tila, 1, hylatyt=hylatyt)
        self.assertEqual(valittu.name, "2026-11-12-02-kelpaa.md")
        self.assertEqual(hylatyt, [])
        self.assertIn(NIMI, err.getvalue())
        self.assertIn("JonoVirhe", err.getvalue())
        self.assertNotIn("salaisuus", err.getvalue())

    def test_baseexception_nousee_edelleen(self):
        repo, tila = pystyta("Tavoite: rahaa\n")
        self._kirjoita_tulos_nostaa(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            ajuri.varaa_seuraava(repo, tila, 1)

    def test_epaonnistunut_kirjoitus_ei_kuluta_kertakirjausta(self):
        """Hylkayslokiin kirjataan vasta onnistuneen tuloksen jalkeen: epaonnistunut kirjoitus yritetaan seuraavalla kierroksella uudelleen."""
        repo, tila = pystyta("Tavoite: rahaa\n")
        vanha = ajuri.kirjoita_tulos
        self._kirjoita_tulos_nostaa(ajuri.JonoVirhe("x"))
        with contextlib.redirect_stderr(io.StringIO()):
            ajuri.varaa_seuraava(repo, tila, 1)
        self.assertFalse((tila / tavoite.HYLKAYSLOKI).exists())
        ajuri.kirjoita_tulos = vanha
        h = []
        ajuri.varaa_seuraava(repo, tila, 1, hylatyt=h)
        self.assertEqual(h, [tulos(repo)])
        self.assertEqual(len((tila / tavoite.HYLKAYSLOKI).read_text().splitlines()), 1)

    def test_kelpaava_ei_tulosta(self):
        repo, tila = pystyta("Tavoite: portfolio\n")
        hylatyt = []
        self.assertEqual(ajuri.varaa_seuraava(repo, tila, 1, hylatyt=hylatyt).name, NIMI)
        self.assertEqual(hylatyt, [])
        self.assertFalse(tulos(repo).exists())

    def test_vanha_tehtava_ilman_rivia_ei_hylata(self):
        repo, tila = pystyta("# vanha\n", "2026-11-01-01-vanha.md")
        self.assertEqual(ajuri.varaa_seuraava(repo, tila, 1).name, "2026-11-01-01-vanha.md")
        self.assertFalse(tulos(repo, "2026-11-01-01-vanha.md").exists())

    def test_ensimmainen_rivi_ratkaisee_myos_kun_myohempi_kelpaa(self):
        """Portti ei loysty: virheellinen ensimmainen + kelpaava lainaus myohemmin = hylkays (PR #287:n aukko)."""
        teksti = "Tavoite: rahaa\n\n> Tavoite: portfolio\n\nTavoite: yllapito\n"
        self.assertIsNone(tavoite.tavoite_tekstista(teksti))
        repo, tila = pystyta(teksti)
        self.assertIsNone(ajuri.varaa_seuraava(repo, tila, 1))
        self.assertTrue(tulos(repo).exists())

    def test_julkaisu_kutsutaan_poistuttaessa_ja_virhe_ei_kaada(self):
        kutsut = []
        vanha = ajuri.julkaise
        ajuri.julkaise = lambda r, p, v=None: kutsut.append(p) or (_ for _ in ()).throw(ajuri.JonoVirhe("x"))
        try:
            with ajuri._hylkayksen_julkaisu("r", [Path("a")]):
                pass
            with ajuri._hylkayksen_julkaisu("r", []):
                pass
        finally:
            ajuri.julkaise = vanha
        self.assertEqual(kutsut, [[Path("a")]])


class Tyokalu(unittest.TestCase):
    def aja(self, teksti, nimi=NIMI):
        h = Path(tempfile.mkdtemp())
        p = h / nimi
        p.write_text(teksti, encoding="utf-8")
        return tarkista_tehtava.main([str(p)])

    def test_koodit(self):
        self.assertEqual(self.aja("Tavoite: yllapito\n"), 0)
        self.assertEqual(self.aja("Tavoite: rahaa\n"), 1)
        self.assertEqual(self.aja("ei rivia\n"), 1)
        self.assertEqual(self.aja("Tavoite: yllapito\n", "VAARA.md"), 1)
        self.assertEqual(self.aja("ei rivia\n", "2026-11-01-01-vanha.md"), 0)
        self.assertEqual(self.aja("Tavoite: yllapito\n" + "x" * 20001), 1)
        self.assertEqual(tarkista_tehtava.main([]), 2)
        self.assertEqual(tarkista_tehtava.main(["/ei/ole/2026-11-12-01-olematon.md"]), 2)

    def test_ensimmainen_rivi_ratkaisee(self):
        self.assertEqual(self.aja("Tavoite: rahaa\nTavoite: portfolio\n"), 1)
        self.assertEqual(self.aja("Tavoite: portfolio\nTavoite: rahaa\n"), 0)

    def test_monta_tiedostoa_suurin_koodi(self):
        h = Path(tempfile.mkdtemp())
        (h / NIMI).write_text("Tavoite: aivo\n")
        (h / "2026-11-12-02-b.md").write_text("x\n")
        self.assertEqual(tarkista_tehtava.main([str(h / NIMI), str(h / "2026-11-12-02-b.md")]), 1)


class KaistanOmistus(unittest.TestCase):
    """PR #295, tarkastus (ajuri.py:384): hylkaystuloksen kirjoittaa vain kaista, jolle tehtava kuuluu."""

    def test_toiselle_kaistalle_kiinnitettya_ei_kirjata(self):
        repo, tila = pystyta("Kaista: 3\nTavoite: rahaa\n")
        h = []
        self.assertIsNone(ajuri.varaa_seuraava(repo, tila, 1, kaistoja=3, hylatyt=h))
        self.assertIsNone(ajuri.varaa_seuraava(repo, tila, 2, kaistoja=3, hylatyt=h))
        self.assertEqual(h, [])
        self.assertFalse(tulos(repo).exists())
        self.assertFalse((tila / tavoite.HYLKAYSLOKI).exists())
        self.assertIsNone(ajuri.varaa_seuraava(repo, tila, 3, kaistoja=3, hylatyt=h))
        self.assertEqual(h, [tulos(repo)])
        self.assertIn("- Tila: EPAONNISTUI", tulos(repo).read_text(encoding="utf-8"))

    def test_kaistojen_ulkopuolinen_kiinnitys_on_vapaa_eika_jaa_hiljaiseksi(self):
        repo, tila = pystyta("Kaista: 3\nTavoite: rahaa\n")
        h = []
        ajuri.varaa_seuraava(repo, tila, 1, kaistoja=2, hylatyt=h)
        self.assertEqual(h, [tulos(repo)])

    def test_valitse_kutsuu_ohitettua_vain_omalle(self):
        h = Path(tempfile.mkdtemp())
        a, b, c = (h / "2026-11-12-01-a.md", h / "2026-11-12-02-b.md", h / "2026-11-12-03-c.md")
        for p in (a, b, c):
            p.write_text("Tavoite: rahaa\n", encoding="utf-8")
        ohit = []
        tavoite.valitse([(a, 3), (b, None), (c, 1)], 1, 3, ohitettu=lambda q, s: ohit.append(q.name))
        self.assertEqual(ohit, [b.name, c.name])
        ohit.clear()
        tavoite.valitse([(a, 3), (b, None), (c, 1)], 3, 3, ohitettu=lambda q, s: ohit.append(q.name))
        self.assertEqual(ohit, [a.name, b.name])


def _git(hak, *a):
    subprocess.run(["git", "-C", str(hak), *a], check=True, capture_output=True, text=True)


class JulkaisuLukonSisalla(unittest.TestCase):
    """PR #295, tarkastus (ajuri.py:1112): hylkaystuloksen commit + push tehdaan git-lukon sisalla (oikea git, bare-origin)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        self.repo = self.tmp / "docs"
        subprocess.run(["git", "clone", "-q", str(origin), str(self.repo)], check=True, capture_output=True)
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "checkout", "-q", "-b", "main")
        t = self.repo / "claude-jono" / "tehtavat" / NIMI
        t.parent.mkdir(parents=True)
        t.write_text("Tavoite: rahaa\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "alku")
        _git(self.repo, "push", "-q", "origin", "main")

    def lukko_vapaa(self):
        """Uusi avoin tiedosto = eri flock-kuvaus: jos lukko on pidossa (myos samassa prosessissa), LOCK_NB epaonnistuu."""
        with open(self.tmp / ajuri.GIT_LUKKO, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(f, fcntl.LOCK_UN)
            return True

    def test_julkaisu_lukon_sisalla(self):
        havainnot = []
        alkuperainen = ajuri.julkaise

        def vakoja(repo, polut, viesti=None):
            havainnot.append(self.lukko_vapaa())
            return alkuperainen(repo, polut, viesti)
        ajuri.julkaise = vakoja
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                viesti = ajuri.aja(self.repo, self.tmp, env={"CLAUDEJONO_ITSESUUNNITTELU": "0"},
                                   suorita=lambda *a, **k: self.fail("tehtavaa ei saa ajaa"))
        finally:
            ajuri.julkaise = alkuperainen
        self.assertEqual(viesti, "jono tyhja")
        self.assertEqual(havainnot, [False])   # lukko oli pidossa julkaisun aikana
        loki = subprocess.run(["git", "-C", str(self.repo), "log", "-1", "--format=%s", "origin/main"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(loki, "claude-jono: tavoiteportti hylkasi tehtavan")
        tila = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
        self.assertEqual(tila, "")
        self.assertTrue(self.lukko_vapaa())


if __name__ == "__main__":
    unittest.main()
