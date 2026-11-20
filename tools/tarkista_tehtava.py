#!/usr/bin/env python3
"""Tarkistaa tehtavatiedostot samoilla saannoilla kuin ajuri (claudejono.ajuri + claudejono.tavoite) ennen pushia.

Kaytto: tools/tarkista_tehtava.py <tiedosto...>
Tarkistukset: tiedostonimi (kelvollinen_nimi), koko (TEHTAVA_MAX_TAVUA) ja tavoiteportti (ensimmainen `Tavoite:`-rivi ratkaisee;
pakollinen vain nimen paivayksesta TAVOITE_ALKAEN alkaen, kuten ajurissa).
Tuloste: yksi rivi per tiedosto, "OK tiedosto" tai "VIRHE tiedosto: syy". Poistumiskoodi 0 = kaikki kelpaavat, 1 = ainakin yksi hylataan,
2 = kayttovirhe tai tiedostoa ei voi lukea. Vain stdlib.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claudejono import ajuri, tavoite  # noqa: E402


def tarkista(polku):
    """Palauttaa syyn tekstina tai "" jos tehtava kelpaa. OSError nousee kutsujalle."""
    nimi = os.path.basename(polku)
    if not ajuri.kelvollinen_nimi(nimi):
        return "tiedostonimi ei kelpaa (muoto PVM-NN-nimi.md, a-z A-Z 0-9 -)"
    with open(polku, "rb") as f:
        data = f.read(ajuri.TEHTAVA_MAX_TAVUA + 1)
    if len(data) > ajuri.TEHTAVA_MAX_TAVUA:
        return f"tehtava yli {ajuri.TEHTAVA_MAX_TAVUA} tavua"
    if tavoite.pakollinen(nimi):
        return tavoite.hylkaysperuste(data.decode("utf-8", "replace"))
    return ""


def main(argv=None):
    tiedostot = list(sys.argv[1:] if argv is None else argv)
    if not tiedostot:
        print("kaytto: tarkista_tehtava.py <tiedosto...>", file=sys.stderr)
        return 2
    koodi = 0
    for polku in tiedostot:
        try:
            syy = tarkista(polku)
        except OSError as virhe:
            print(f"VIRHE {polku}: ei voi lukea ({type(virhe).__name__})")
            koodi = max(koodi, 2)
            continue
        if syy:
            print(f"VIRHE {polku}: {syy}")
            koodi = max(koodi, 1)
        else:
            print(f"OK {polku}")
    return koodi


if __name__ == "__main__":
    sys.exit(main())
