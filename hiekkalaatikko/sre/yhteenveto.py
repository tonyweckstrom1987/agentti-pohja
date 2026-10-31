#!/usr/bin/env python3
"""SRE-tulosten yhteenveto mallikohtaisesti kokeilut.jsonl-tiedostosta (hl.py:n rivimuoto).

  python3 yhteenveto.py /var/lib/hiekkalaatikko/kokeilut.jsonl [--malli-vika]
Onnistumisprosentti = onnistui / ratkaistut (keskeytetyt eivat laske, koska ne eivat kerro mallin kyvysta; ne nakyvat omassa sarakkeessa).
Korjausaika = rivin kesto_s (koko ajo: klooni, alustus, agentti, tarkistus). Se sisaltaa vakioylimaaran; vertaa malleja keskenaan, ala ota absoluuttisena.
Kustannus: vain rivit, joilla kustannus on tiedossa; tuntemattomat lasketaan erikseen (pessimistinen varaus pysyy kirjanpidossa).
"""
import importlib.util
import os
import statistics
import sys

HL_POLKU = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hl.py")
spec = importlib.util.spec_from_file_location("hl_sre_yhteenveto", HL_POLKU)
hl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hl)


def sre_rivit(rivit):
    return [r for r in rivit if r["kokeilu"].startswith("sre-v")]


def mallikohtainen(rivit):
    """Lista sanakirjoja malleittain (jarjestetty onnistumisen mukaan)."""
    ryhmat = {}
    for r in sre_rivit(rivit):
        ryhmat.setdefault(r["malli"], []).append(r)
    out = []
    for malli, rs in ryhmat.items():
        ratkaistut = [r for r in rs if r["lopputulos"] != "keskeytetty"]
        ok = [r for r in ratkaistut if r["lopputulos"] == "onnistui"]
        hinnat = [r["kustannus_usd"] for r in rs if r["kustannus_usd"] is not None]
        out.append({
            "malli": malli, "ajoja": len(rs), "ratkaistut": len(ratkaistut), "onnistui": len(ok),
            "keskeytetty": len(rs) - len(ratkaistut),
            "prosentti": (100.0 * len(ok) / len(ratkaistut)) if ratkaistut else None,
            "korjausaika_mediaani_s": statistics.median([r["kesto_s"] for r in ok]) if ok else None,
            "kustannus_summa_usd": sum(hinnat), "kustannus_keskiarvo_usd": (sum(hinnat) / len(hinnat)) if hinnat else None,
            "kustannus_tuntematon": sum(1 for r in rs if r["kustannus_usd"] is None),
        })
    return sorted(out, key=lambda x: (-(x["prosentti"] if x["prosentti"] is not None else -1), x["malli"]))


def _f(x, kaava):
    return kaava % x if x is not None else "?"


def taulu(rivit):
    out = ["| malli | ajoja | onnistui | onnistumis-% | keskeytetty | korjausaika (mediaani s, onnistuneet) | hinta/ajo (USD) | hinta yht. (USD) | kulu tuntematon |",
           "|---|---|---|---|---|---|---|---|---|"]
    for m in mallikohtainen(rivit):
        out.append("| %s | %d | %d/%d | %s | %d | %s | %s | %s | %d |" % (
            m["malli"], m["ajoja"], m["onnistui"], m["ratkaistut"], _f(m["prosentti"], "%.0f"), m["keskeytetty"],
            _f(m["korjausaika_mediaani_s"], "%.0f"), _f(m["kustannus_keskiarvo_usd"], "%.4f"),
            _f(m["kustannus_summa_usd"], "%.4f"), m["kustannus_tuntematon"]))
    return "\n".join(out) + "\n"


def malli_vika_taulu(rivit):
    """Matriisi vika x malli: onnistui/ratkaistut."""
    rs = sre_rivit(rivit)
    mallit = sorted({r["malli"] for r in rs})
    viat = sorted({r["kokeilu"] for r in rs})
    out = ["| vika | " + " | ".join(mallit) + " |", "|---|" + "---|" * len(mallit)]
    for v in viat:
        solut = []
        for m in mallit:
            xs = [r for r in rs if r["kokeilu"] == v and r["malli"] == m and r["lopputulos"] != "keskeytetty"]
            solut.append("%d/%d" % (sum(1 for r in xs if r["lopputulos"] == "onnistui"), len(xs)) if xs else "-")
        out.append("| %s | %s |" % (v, " | ".join(solut)))
    return "\n".join(out) + "\n"


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("kaytto: yhteenveto.py kokeilut.jsonl [--malli-vika]", file=sys.stderr)
        return 2
    try:
        rivit = hl.lue_rivit(args[0])
    except (hl.Virhe, OSError) as e:
        print("yhteenveto.py: %s" % type(e).__name__, file=sys.stderr)
        return 3
    if not sre_rivit(rivit):
        print("ei SRE-rivia (kokeilu sre-v..)", file=sys.stderr)
        return 1
    sys.stdout.write(taulu(rivit))
    if "--malli-vika" in argv:
        sys.stdout.write("\n" + malli_vika_taulu(rivit))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
