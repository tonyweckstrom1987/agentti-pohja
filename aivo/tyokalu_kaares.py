"""Aivon tyokalutilan kaares: ajaa kiintean komennon ja liittaa vastauksen perassa koneluettavan paatosrivin.

Kaytto systemd-yksikossa (ExecStart, komento kiintea yksikkotiedostossa):
    python3 /KOKO/POLKU/aivo/tyokalu_kaares.py /KOKO/POLKU/claude -p --output-format text --max-turns 1 --tools ""

stdin -> aliprosessin stdin, aliprosessin stdout -> stdout, stderr jaa lokiin (journal) eika koskaan mene
socketin yli. Lopuksi stdoutiin kirjoitetaan `\\x00AIVO-EXIT <koodi>\\n`. Vastauksesta poistetaan NUL-tavut,
joten paatosrivia ei voi vaarentaa vastauksen sisalla. Jos kaares kuolee ennen paatosrivia (aikaraja, katkos),
rivi puuttuu, ja asiakas raportoi sen virheena.
"""
import subprocess
import sys

PAATOS_ALKU = b"\x00AIVO-EXIT "


def paatosrivi(koodi):
    return PAATOS_ALKU + str(int(koodi)).encode("ascii") + b"\n"


def aja(komento, syote, suorita=subprocess.run):
    """Palauttaa (vastaus_tavuina, koodi). Koodi 127: komentoa ei voitu kaynnistaa. Signaali n -> 128+n."""
    try:
        r = suorita(komento, input=syote, stdout=subprocess.PIPE, check=False)
    except OSError:
        return b"", 127
    koodi = r.returncode
    if koodi < 0:
        koodi = 128 - koodi
    return r.stdout.replace(b"\x00", b""), min(koodi, 255)


def main(argv=None, stdin=None, stdout=None):
    argv = sys.argv[1:] if argv is None else argv
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    if not argv:
        stdout.write(paatosrivi(2))
        stdout.flush()
        return 2
    vastaus, koodi = aja(argv, stdin.read())
    stdout.write(vastaus + paatosrivi(koodi))
    stdout.flush()
    return 0           # kaaresten oma onnistuminen; claude:n koodi kulkee paatosrivilla


if __name__ == "__main__":
    sys.exit(main())
