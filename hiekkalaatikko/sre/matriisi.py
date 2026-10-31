#!/usr/bin/env python3
"""SRE-matriisi: sama vika usealle mallille, yksi kertakayttoklooni per ajo (aja-kokeilu.sh hoitaa kloonin, katot ja kirjauksen).

Ajetaan Proxmox-isannalla (Tony). Katot (aika, kustannus, ajoja/paiva) tulevat aja-kokeilu.sh:sta ja hl.py:n koodin ylarajoista;
tama ohjelma ei muuta niita eika kierra niita: liian iso matriisi hylataan ennen ensimmaista ajoa.
  python3 matriisi.py --mallit qwen-coder,deepseek-flash [--viat v01,v03|kaikki] [--kuiva]
"""
import argparse
import glob
import os
import re
import subprocess
import sys

SRE = os.path.dirname(os.path.abspath(__file__))
HL = os.path.dirname(SRE)
MALLI_RE = re.compile(r"^[A-Za-z0-9._/:][A-Za-z0-9._/:-]{0,60}$")
OLETUS_AJOJA_PAIVA = 10   # sama oletus kuin aja-kokeilu.sh:ssa (HL_KOKEILUA_PAIVA)
KOVA_AJOJA_PAIVA = 20     # hl.py ENINT_KOKEILUA_PAIVA


def viat():
    """{lyhenne: kokeilun nimi}, esim. {'v01': 'sre-v01-polku'}; lahde on kokeilut/sre-*.md."""
    out = {}
    for polku in sorted(glob.glob(os.path.join(HL, "kokeilut", "sre-v[0-9][0-9]-*.md"))):
        nimi = os.path.basename(polku)[:-3]
        out[nimi.split("-")[1]] = nimi
    return out


def suunnitelma(mallit, valitut, kaikki):
    if not mallit:
        raise SystemExit("matriisi.py: --mallit vaaditaan")
    for m in mallit:
        if not MALLI_RE.match(m):
            raise SystemExit("matriisi.py: mallin nimi ei kelpaa: %r" % m)
    if valitut == ["kaikki"]:
        valitut = sorted(kaikki)
    for v in valitut:
        if v not in kaikki:
            raise SystemExit("matriisi.py: tuntematon vika: %s (tunnetut: %s)" % (v, ",".join(sorted(kaikki))))
    return [(kaikki[v], m) for v in valitut for m in mallit]


def ajoja_paiva(env):
    try:
        n = int(env.get("HL_KOKEILUA_PAIVA", OLETUS_AJOJA_PAIVA))
    except ValueError:
        raise SystemExit("matriisi.py: HL_KOKEILUA_PAIVA ei ole luku")
    return min(n, KOVA_AJOJA_PAIVA)


def komento(kokeilu, malli, adapteri, kuiva):
    k = ["bash", os.path.join(HL, "aja-kokeilu.sh")]
    if kuiva:
        k.append("--kuiva")
    return k + ["--kokeilu", kokeilu, "--adapteri", adapteri, "--malli", malli]


def main(argv=None, env=None, ajo=subprocess.run):
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(prog="matriisi.py")
    p.add_argument("--mallit", required=True, help="pilkuilla erotetut LiteLLM-mallit")
    p.add_argument("--viat", default="kaikki", help="pilkuilla erotetut v01,v02,... tai kaikki")
    p.add_argument("--adapteri", default="litellm")
    p.add_argument("--kuiva", action="store_true", help="tulosta suunnitelma; kuiva-ajo aja-kokeilu.sh:lla, ei kosketa mihinkaan")
    n = p.parse_args(argv)
    kaikki = viat()
    suunn = suunnitelma([x for x in n.mallit.split(",") if x], [x for x in n.viat.split(",") if x], kaikki)
    raja = ajoja_paiva(env)
    print("# suunnitelma: %d ajoa (paivaraja %d)" % (len(suunn), raja))
    for kok, mal in suunn:
        print("#  %s  %s" % (kok, mal))
    if len(suunn) > raja:
        print("matriisi.py: %d ajoa ylittaa paivarajan %d; jaa matriisi osiin (--viat / --mallit) eri paiville. "
              "Rajaa ei kierreta." % (len(suunn), raja), file=sys.stderr)
        return 2
    for kok, mal in suunn:
        r = ajo(komento(kok, mal, n.adapteri, n.kuiva), env=dict(env))
        if r.returncode != 0:
            print("matriisi.py: %s/%s palautti %d; keskeytetaan (ei jatketa seuraaviin ajoihin)" % (kok, mal, r.returncode), file=sys.stderr)
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
