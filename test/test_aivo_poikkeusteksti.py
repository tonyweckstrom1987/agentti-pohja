"""Pysyva vartija: aivo/-koodi ei saa vieda poikkeuksen tekstia tulosteeseen, lokiin tai HTTP-vastaukseen.

Saanto (CLAUDE.md): poikkeuksista tulostetaan vain tyypin nimi, koska viestissa voi olla salaisuuksia.
Testi etsii AST:lla except-lohkoista (except X as e) poikkeusnimen muuttamisen tekstiksi:
  str(e), repr(e), format(e), f"{e}", "..." % e, "...".format(e)
seka e:n antamisen suoraan tulostus-/lokikutsulle (print, .write, logging-metodit, HTTP-vastaus).

Nykyiset hyvaksytyt kohdat ovat SALLITUT-listassa perusteluineen. Avain on (tiedosto, funktio,
poikkeustyyppi, muoto), joten rivinumeroiden siirtyminen ei riko testia. Uusi kohta kaatuu testiin:
kayta type(e).__name__ tai lisaa kohta listaan perustelun kanssa katselmoinnissa. Jos kohta poistuu
koodista, poista se myos listalta (testi kertoo vanhentuneet rivit).
"""
import ast
import unittest
from collections import Counter
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
AIVO = JUURI / "aivo"

TULOSTUSKUTSUT = {"print"}
JALJITYS = {"print_exc", "print_exception", "format_exc", "format_exception"}
METODIKUTSUT = {"write", "debug", "info", "warning", "warn", "error", "exception", "critical", "log",
                "_json", "_laheta", "send_error"}

# (tiedosto, funktio, poikkeustyyppi, muoto): (enimmaismaara, perustelu)
OMA_VIRHE = "oma virheluokka: viestit kirjoitetaan aivon omassa koodissa, ei salaisuuksia eika ulkoista tekstia"
SALLITUT = {
    ("aivo/agentti/tyokalut.py", "suorita", "(PolkuVirhe, TyokaluVirhe)", "str"):
        (1, OMA_VIRHE + "; tyokalun tulos mallille, muut poikkeukset vain tyypin nimella"),
    ("aivo/chat.py", "tee_kasittelija.K.do_GET", "ChatVirhe", "str"): (1, OMA_VIRHE + "; HTTP 400 -vastaus"),
    ("aivo/chat.py", "tee_kasittelija.K.do_POST", "ChatVirhe", "str"): (1, OMA_VIRHE + "; HTTP-vastaus"),
    ("aivo/chat.py", "tee_kasittelija.K._viesti_yksityinen", "ChatVirhe", "str"): (1, OMA_VIRHE + "; HTTP-vastaus"),
    ("aivo/chat.py", "tee_kasittelija.K._viesti", "ChatVirhe", "str"): (1, OMA_VIRHE + "; HTTP-vastaus"),
    ("aivo/chat.py", "tee_kasittelija.K._ehdotus_hyvaksy", "ChatVirhe", "str"): (1, OMA_VIRHE + "; HTTP 409"),
    ("aivo/chat.py", "tee_kasittelija.K._ehdotus_hylkaa", "ChatVirhe", "str"): (1, OMA_VIRHE + "; HTTP 409"),
    ("aivo/chat.py", "main", "ChatVirhe", "prosentti"): (1, OMA_VIRHE + "; kaynnistysvirhe stderriin"),
    ("aivo/haku.py", "main", "(HakuVirhe, AlueVirhe)", "prosentti"): (1, OMA_VIRHE + "; CLI-virhe stderriin"),
    ("aivo/yhdistelma.py", "_tiimi", "YksityinenVirhe", "str"):
        (2, "ei tulostu: str(e) vain aikakatkaisun tunnistukseen (in-vertailu), ei vieda tulosteeseen"),
    ("aivo/yksityinen.py", "main", "YksityinenVirhe", "prosentti"): (1, OMA_VIRHE + "; CLI-virhe stderriin"),
    ("aivo/drive.py", "main", "DriveVirhe", "suora:print"):
        (1, "oma virheluokka, CLI-virhe stderriin (ei journaliin); HUOM: yksi viesti sisaltaa Drive-API:n "
            "palauttaman mimeType-arvon (ei salaisuus, mutta ulkoista tekstia); muut poikkeukset vain tyypin nimella"),
    ("aivo/tiivistelma.py", "main", "TiivistelmaVirhe", "suora:print"): (1, OMA_VIRHE + "; CLI-virhe stderriin"),
    ("aivo/tiivista_yksityinen.py", "main", "(TiivistaVirhe, TiivistelmaVirhe)", "suora:print"):
        (1, OMA_VIRHE + "; CLI-virhe stderriin"),
    ("aivo/tiivista_tyokalu.py", "main", "(TiivistaVirhe, TiivistelmaVirhe)", "suora:print"):
        (1, OMA_VIRHE + "; CLI-virhe stderriin"),
    ("aivo/kalenteri.py", "hae_https", "urllib.error.HTTPError", "prosentti"):
        (1, "vain HTTP-tilakoodi e.code (kokonaisluku, %d), ei viestia eika osoitetta"),
    ("aivo/yksityinen_or.py", "tee_kuljetin.laheta", "urllib.error.HTTPError", "prosentti"):
        (1, "vain HTTP-tilakoodi e.code, ei viestia eika vastauksen runkoa"),
    ("aivo/kalenteri.py", "IcalLahde._teksti", "KalenteriVirhe", "suora:warning"):
        (1, OMA_VIRHE + "; KalenteriVirhe ei sisalla osoitetta, ulkoiset virheet vain tyypin nimella"),
}


def _on_nimi(solmu, nimi):
    """Tosi, jos solmu on poikkeusnimi tai sen attribuutti/indeksi (e, e.args[0], e.strerror)."""
    while isinstance(solmu, (ast.Attribute, ast.Subscript)):
        solmu = solmu.value
    return isinstance(solmu, ast.Name) and solmu.id == nimi


def _muoto(solmu, nimi):
    """Palauttaa loydon muodon, jos solmu muuttaa poikkeusnimen tekstiksi tai vie sen tulosteeseen."""
    if isinstance(solmu, ast.Raise) and isinstance(solmu.exc, ast.Call) \
            and any(_on_nimi(a, nimi) for a in solmu.exc.args):
        return "raise"                        # ulkoisen tekstin "pesu" omaan virheluokkaan
    if isinstance(solmu, ast.Call):
        f = solmu.func
        if isinstance(f, ast.Attribute) and (f.attr in JALJITYS or (f.attr == "exception")):
            return "jaljitys"
        if any(k.arg == "exc_info" for k in solmu.keywords):
            return "jaljitys"
        if isinstance(f, ast.Name) and f.id in ("str", "repr", "format") and solmu.args and _on_nimi(solmu.args[0], nimi):
            return f.id
        if isinstance(f, ast.Attribute) and f.attr == "format" and any(_on_nimi(a, nimi) for a in solmu.args):
            return "format"
        argit = list(solmu.args) + [k.value for k in solmu.keywords if k.arg != "file"]
        if any(_on_nimi(a, nimi) for a in argit):
            if isinstance(f, ast.Name) and f.id in TULOSTUSKUTSUT:
                return "suora:" + f.id
            if isinstance(f, ast.Attribute) and f.attr in METODIKUTSUT:
                return "suora:" + f.attr
    if isinstance(solmu, ast.FormattedValue) and _on_nimi(solmu.value, nimi):
        return "fstr"
    if isinstance(solmu, ast.BinOp) and isinstance(solmu.op, ast.Mod) \
            and isinstance(solmu.left, ast.Constant) and isinstance(solmu.left.value, str):
        oikea = solmu.right.elts if isinstance(solmu.right, ast.Tuple) else [solmu.right]
        if any(_on_nimi(x, nimi) for x in oikea):
            return "prosentti"
    return None


def etsi(lahde, tiedosto="<testi>"):
    """Palauttaa listan (funktio, poikkeustyyppi, muoto, rivi) kaikista loydoista."""
    puu = ast.parse(lahde, tiedosto)
    loydot = []

    def kay(solmu, pino):
        for lapsi in ast.iter_child_nodes(solmu):
            uusi = pino + [lapsi.name] if isinstance(lapsi, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else pino
            if isinstance(lapsi, ast.ExceptHandler) and lapsi.name:
                tyyppi = ast.unparse(lapsi.type) if lapsi.type is not None else "-"
                for alla in ast.walk(lapsi):
                    if alla is lapsi:
                        continue
                    if isinstance(alla, ast.ExceptHandler) and alla.name == lapsi.name:
                        continue
                    m = _muoto(alla, lapsi.name)
                    if m:
                        loydot.append((".".join(uusi) or "<moduuli>", tyyppi, m, alla.lineno))
            kay(lapsi, uusi)

    kay(puu, [])
    return loydot


def skannaa_aivo():
    laskuri, rivit = Counter(), {}
    for p in sorted(AIVO.rglob("*.py")):
        if p.name.startswith("test_"):
            continue
        suht = p.relative_to(JUURI).as_posix()
        for funktio, tyyppi, muoto, rivi in etsi(p.read_text(encoding="utf-8"), str(p)):
            avain = (suht, funktio, tyyppi, muoto)
            laskuri[avain] += 1
            rivit.setdefault(avain, []).append(rivi)
    return laskuri, rivit


class SkanneriTesti(unittest.TestCase):
    """Skannerin oma toiminta pienilla esimerkeilla (positiiviset ja negatiiviset)."""

    def muodot(self, lahde):
        return [m for _, _, m, _ in etsi(lahde)]

    def test_loytaa_riskialttiit_muodot(self):
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n print(str(e))\n"), ["str"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n log.error(repr(e))\n"), ["repr"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n print(f'v {e}')\n"), ["fstr"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n s = 'v %s' % e\n"), ["prosentti"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n s = 'v %s %s' % (1, e)\n"), ["prosentti"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n s = 'v {}'.format(e)\n"), ["format"])
        self.assertEqual(self.muodot("import sys\ntry:\n x()\nexcept OSError as e:\n print('v', e, file=sys.stderr)\n"),
                         ["suora:print"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n LOG.warning('v %s', e)\n"), ["suora:warning"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n self._json(500, e)\n"), ["suora:_json"])

    def test_loytaa_attribuutit_jaljityksen_ja_pesun(self):
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n print(e.args[0])\n"), ["suora:print"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n s = f'{e.strerror}'\n"), ["fstr"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n LOG.exception('v')\n"), ["jaljitys"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n LOG.error('v', exc_info=True)\n"),
                         ["jaljitys"])
        self.assertEqual(self.muodot("import traceback\ntry:\n x()\nexcept OSError as e:\n traceback.print_exc()\n"),
                         ["jaljitys"])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n raise Oma(e)\n"), ["raise"])

    def test_ei_vaarahalytysta_tyypin_nimesta(self):
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n print(type(e).__name__)\n"), [])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError:\n print(str(x))\n"), [])
        self.assertEqual(self.muodot("try:\n x()\nexcept OSError as e:\n raise Oma('v') from e\n"), [])
        self.assertEqual(self.muodot("e = 1\nprint(str(e))\n"), [])

    def test_funktion_nimi_ja_tyyppi(self):
        lahde = "class A:\n def f(self):\n  try:\n   x()\n  except (OSError, ValueError) as e:\n   print(str(e))\n"
        self.assertEqual(etsi(lahde), [("A.f", "(OSError, ValueError)", "str", 6)])


class AivoPoikkeustekstiTesti(unittest.TestCase):
    def test_ei_uusia_poikkeustekstikohtia(self):
        laskuri, rivit = skannaa_aivo()
        uudet = []
        for avain, n in sorted(laskuri.items()):
            sallittu = SALLITUT.get(avain, (0, ""))[0]
            if n > sallittu:
                uudet.append("%s rivit %s (%d kpl, sallittu %d)" % (avain, rivit[avain], n, sallittu))
        self.assertEqual(uudet, [], "uusi poikkeuksen teksti tulosteeseen/lokiin/vastaukseen; kayta type(e).__name__ "
                                    "tai perustele kohta SALLITUT-listassa:\n" + "\n".join(uudet))

    def test_sallitut_lista_ajantasalla(self):
        laskuri, _ = skannaa_aivo()
        vanhat = ["%s (sallittu %d, loytyi %d)" % (k, v[0], laskuri.get(k, 0))
                  for k, v in sorted(SALLITUT.items()) if laskuri.get(k, 0) < v[0]]
        self.assertEqual(vanhat, [], "SALLITUT-listalla on kohtia, joita koodissa ei enaa ole; pienenna tai poista:\n"
                                     + "\n".join(vanhat))

    def test_jokaisella_sallitulla_perustelu(self):
        for k, (n, perustelu) in SALLITUT.items():
            self.assertGreater(n, 0, k)
            self.assertGreater(len(perustelu.strip()), 10, k)


if __name__ == "__main__":
    unittest.main()
