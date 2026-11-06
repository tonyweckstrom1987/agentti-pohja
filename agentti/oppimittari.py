"""Oppimisen mittari (Tonyn paatos 4.11.2026, kompassi "Jatkuva oppiminen"): toistuvatko samat juurisyyt viikosta toiseen?

Ei omaa luokittelua: kayttaa virhemittarin (tools/virhemittari.py) juurisyyluokkia ja toistosaantoa, joten viikkoraportin rivi ja
tulostaulun Virhemittari-osio antavat saman vastauksen samasta lokista.
  luokat: 1 tehty != toimii, 2 muistista ei lahteesta, 3 vaara oletus ymparistosta, 4 muu, ? luokittelematon
  toisto: luokka 1-3 esiintyy kahtena perakkaisena viikkona -> korjausehdotus koodiin (testi, tarkistus tai rajaus),
          ei uutta ohjetta; kolmantena -> Tonylle.
Lukee vain virheloki.md:n (myos riippumattoman tarkastajan kirjaamat loydokset). Ei verkkoa, ei kirjoitusta, vain stdlib.

Kaytto: python3 -m agentti.oppimittari [VIRHELOKI.md]   (tulostaa mittaririvin)"""
import sys
from datetime import datetime, timedelta, timezone

from tools import virhemittari as vm

LYHYT = {"1": "1 tehty\u2260toimii", "2": "2 muistista", "3": "3 v\u00e4\u00e4r\u00e4 oletus", "4": "4 muu", "?": "luokittelematta"}


def _luvut(rivit, v):
    for vk, r in rivit:
        if vk == v:
            return r
    return {"loki": 0, "luokat": dict.fromkeys(vm.LUOKAT, 0), "toisto": "ei"}


def _luokat(r):
    osat = ["%s x%d" % (LYHYT[k], r["luokat"][k]) for k in vm.LUOKAT if r["luokat"][k]]
    return " (" + ", ".join(osat) + ")" if osat else ""


def rivi(teksti, nyt):
    """Yksi rivi viikkokoosteeseen. Puuttuva loki ja tyhja viikko sanotaan \u00e4\u00e4neen, ei arvata."""
    if teksti is None:
        return "Oppiminen: virhelokia ei saatu luettua."
    rivit, _ = vm.laske(teksti, [], nyt)
    nyt_v = vm.viikko(nyt)
    ed_v = vm.viikko(nyt - timedelta(days=7))
    a, e = _luvut(rivit, nyt_v), _luvut(rivit, ed_v)
    if a["loki"] == 0 and e["loki"] == 0:
        return "Oppiminen: data ei riit\u00e4 (virhelokissa ei merkint\u00f6j\u00e4 kahdelta viimeiselt\u00e4 viikolta)."
    osat = ["Oppiminen: viikolla %d virhelokimerkint\u00f6j\u00e4 %d%s" % (nyt_v[1], a["loki"], _luokat(a)),
            "edellisell\u00e4 viikolla %d%s" % (e["loki"], _luokat(e)),
            "toistuiko sama juurisyy: " + a["toisto"]]
    return "; ".join(osat) + "."


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("kaytto: python3 -m agentti.oppimittari VIRHELOKI.md", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as f:
            teksti = f.read()
    except (OSError, ValueError) as e:
        print("oppimittari: luku ep\u00e4onnistui (%s)" % type(e).__name__, file=sys.stderr)
        return 2
    print(rivi(teksti, datetime.now(timezone.utc).date()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
