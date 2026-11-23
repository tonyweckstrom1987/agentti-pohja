"""Skriptit: pipefailin kanssa ei putkea `| grep -q` (23.11.2026).

`grep -q` lopettaa heti ensimmaiseen osumaan. Jos putken edellinen komento kirjoittaa viela, se saa SIGPIPEn
ja paluuarvon 141, ja `set -o pipefail` tekee koko putkesta epaonnistuneen, vaikka osuma loytyi. Tulos:
- `cmd | grep -q x` -> "ei loytynyt", vaikka loytyi (vaara-ala ehto),
- `! cmd | grep -q x` -> "ei loytynyt" -> esim. hiekkalaatikon tuhoamissuoja (mp-/dev-/lxc-rivit) ohittuu
  tai kaytossa oleva CT-ID valitaan vapaaksi (tulos 2026-11-18-11: test_ei_vapaata_id_ta kaatui kuormassa).
Korjaus on here-string: `grep -q x <<< "$muuttuja"` tai `grep -q x <<< "$(cmd)"`.

Testi kay lapi repon shell-skriptit (.sh, .sh.mall tai bash/sh-shebang), joissa on pipefail, ja hylkaa
kommentittomat rivit, joissa grep -q on putken loppupaassa.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
OHITA = {".git", "node_modules", "__pycache__", "test", "tests"}
PIPEFAIL = re.compile(r"^[^#]*\bpipefail\b", re.M)
# putki (ei ||) ja sen jalkeen grep, jonka lyhyissa valitsimissa on q (-q, -qx, -Eq, -qE ...) tai --quiet/--silent
GREP_Q = re.compile(r"(?<!\|)\|(?!\|)\s*grep\s+(?:-[A-Za-z]*q[A-Za-z]*\b|--quiet\b|--silent\b)")


def on_skripti(polku):
    if polku.name.endswith((".sh", ".sh.mall")):
        return True
    try:
        with polku.open("rb") as f:
            alku = f.readline(200)
    except OSError:
        return False
    return alku.startswith(b"#!") and (b"bash" in alku or alku.rstrip().endswith(b"/sh"))


def skriptit(juuri):
    for p in sorted(juuri.rglob("*")):
        if p.is_file() and not (set(p.relative_to(juuri).parts[:-1]) & OHITA) and on_skripti(p):
            yield p


def loydokset(teksti):
    """Palauttaa [(rivinumero, rivi)] riveista, joissa on '| grep -q', jos tiedostossa on pipefail."""
    if not PIPEFAIL.search(teksti):
        return []
    tulos = []
    for n, rivi in enumerate(teksti.splitlines(), 1):
        koodi = rivi.lstrip()
        if koodi.startswith("#"):
            continue
        if GREP_Q.search(koodi):
            tulos.append((n, rivi.strip()))
    return tulos


class GrepPutkiPipefail(unittest.TestCase):
    def test_repon_skripteissa_ei_putkea_grep_q_pipefailin_kanssa(self):
        virheet = []
        for p in skriptit(JUURI):
            for n, rivi in loydokset(p.read_text(errors="replace")):
                virheet.append("%s:%d: %s" % (p.relative_to(JUURI), n, rivi))
        self.assertEqual(virheet, [], "pipefail + '| grep -q' -> kayta here-stringia (grep -q x <<< \"$v\"):\n"
                         + "\n".join(virheet))

    def test_skripteja_loytyy(self):
        nimet = {str(p.relative_to(JUURI)) for p in skriptit(JUURI)}
        self.assertIn("hiekkalaatikko/aja-kokeilu.sh", nimet)
        self.assertIn("isanta/varmuuskopio-drive.sh", nimet)


class Tunnistus(unittest.TestCase):
    """Vartijan itsetesti: tunnistaa vaarat muodot eika halyta turvallisista."""

    def test_vaarat_muodot(self):
        for rivi in ("printf '%s\\n' \"$K\" | grep -qx \"$i\"",
                     "if ! printf '%s\\n' \"$c\" | grep -qE '^mp'; then",
                     "\"$PCT\" status 1 | grep -q running || exit 1",
                     "x | cut -c1-2 | grep -Eq '[DR]'",
                     "x | grep --quiet y"):
            with self.subTest(rivi=rivi):
                self.assertEqual(len(loydokset("set -euo pipefail\n" + rivi)), 1)

    def test_turvalliset_muodot(self):
        for rivi in ("grep -qx \"$i\" <<< \"$K\"",
                     "grep -q running <<< \"$(pct status 1)\"",
                     "a || grep -q x file",
                     "x | grep -c y",
                     "x | grep -v q",
                     "# x | grep -q y"):
            with self.subTest(rivi=rivi):
                self.assertEqual(loydokset("set -euo pipefail\n" + rivi), [])

    def test_ilman_pipefailia_ei_halyta(self):
        self.assertEqual(loydokset("set -eu\nx | grep -q y\n"), [])
        self.assertEqual(loydokset("# set -o pipefail\nx | grep -q y\n"), [])


class HereStringToimii(unittest.TestCase):
    """Here-string ei kaadu SIGPIPEen: osuma tunnistetaan, vaikka syote on pitka (putki antaisi 141)."""

    def test_pitka_syote_osuma_loytyy(self):
        skripti = ('set -euo pipefail\nK="$(seq 1 200000)"\n'
                   'if grep -qx 1 <<< "$K"; then echo loytyi; else echo ei; fi\n'
                   'if ! grep -qx 1 <<< "$K"; then echo vapaa; else echo varattu; fi\n')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.sh"
            p.write_text(skripti)
            r = subprocess.run(["bash", str(p)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["loytyi", "varattu"])

    def test_putki_voi_antaa_141(self):
        """Dokumentoi juurisyyn: putki + pipefail antaa 141 pitkalla syotteella (ohitetaan, jos ei toistu)."""
        r = subprocess.run(["bash", "-c", "set -o pipefail; seq 1 2000000 | grep -qx 1; echo $?"],
                           capture_output=True, text=True, timeout=60)
        if r.stdout.strip() != "141":
            self.skipTest("SIGPIPE ei toistunut tassa ymparistossa (ajoituksesta riippuva)")
        self.assertEqual(r.stdout.strip(), "141")


if __name__ == "__main__":
    unittest.main()
