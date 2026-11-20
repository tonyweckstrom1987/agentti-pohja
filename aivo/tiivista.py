"""Aivon keskustelumuistin tiivistyksen komentorivi: valitsee muistialueen ja antaa ajon alueen omalle moduulille.

Kaytto:  python3 -m aivo.tiivista --alue yksityinen [--kuiva] [--juuri KANSIO] [--malli-url URL] [--taustapaa loopback|openrouter]
         python3 -m aivo.tiivista --alue tyokalu [--kuiva] [--juuri KANSIO]
Yksityisen alueen ajo on moduulissa aivo.tiivista_yksityinen (loopback tai OpenRouter ZDR:lla) ja tyokalualueen ajo
moduulissa aivo.tiivista_tyokalu (tyokalutilan socket). Moduuli tuodaan vasta valinnan jalkeen: tyokalualueen ajo ei lataa
yksityisen alueen moduulia."""
import argparse
import sys


def main(argv=None, **kw):
    ap = argparse.ArgumentParser(prog="python3 -m aivo.tiivista", description=__doc__.split("\n")[0], add_help=False)
    ap.add_argument("--alue", required=True, choices=("yksityinen", "tyokalu"), help="muistialue")
    a, loput = ap.parse_known_args(argv)
    if a.alue == "tyokalu":
        from aivo import tiivista_tyokalu
        return tiivista_tyokalu.main(["--alue", "tyokalu"] + loput, **kw)
    from aivo import tiivista_yksityinen
    return tiivista_yksityinen.main(loput)


if __name__ == "__main__":
    sys.exit(main())
