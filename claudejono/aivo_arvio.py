"""Aivon oleellisuusarvio varjotilassa (Tonyn paatos A841, 7.11.2026).

Ennen kuin uusi tehtava ajetaan, Aivo (Clauden tyokalutila, ei OpenRouter/LiteLLM, A329) kirjaa arvion kylla/ei + syy.
Arvio EI esta ajoa. Kirjaukset menevat isannan tilahakemistoon (`aivo-arviot.jsonl`, repon vieressa), ei GitHubiin eika docs-repoon.
Tony tarkistaa viikkosuunnittelussa 5 satunnaista arviota (`otos`) ja kirjaa oman tuomionsa (`tarkista`); osumaprosentti =
Aivon ja Tonyn tuomion yhtenevyys. Kunnes arvioija (Aivon tyokalutila) on kytketty, ajuri ei kirjaa mitaan: arvioija annetaan
`arvioi_varjossa`-funktiolle parametrina, eika tama moduuli keksi arvioita.
"""
import argparse
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

TIEDOSTO = "aivo-arviot.jsonl"
SYY_MAX = 300
OTOS_OLETUS = 5
VAHIMMAIS_LAHTOTASOON = 20   # aivo-valmius.md: vahintaan 20 arvioitua paatosta


def _polku(tila):
    return Path(tila) / TIEDOSTO


def lue(tila):
    """Kaikki rivit; viallinen rivi ohitetaan. Tuoreempi rivi samalla (tehtava, laji) korvaa vanhan."""
    p = _polku(tila)
    if not p.is_file():
        return []
    rivit = []
    for r in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(r)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("tehtava"):
            rivit.append(d)
    return rivit


def _liita(tila, d):
    fd = os.open(_polku(tila), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")


def arviot(tila):
    """{tehtava: {"kylla": bool, "syy": str, "tony": bool|None}}"""
    ulos = {}
    for d in lue(tila):
        e = ulos.setdefault(d["tehtava"], {"kylla": None, "syy": "", "tony": None})
        if d.get("laji") == "aivo" and isinstance(d.get("kylla"), bool):
            e["kylla"], e["syy"] = d["kylla"], str(d.get("syy", ""))[:SYY_MAX]
        elif d.get("laji") == "tony" and isinstance(d.get("kylla"), bool):
            e["tony"] = d["kylla"]
    return {k: v for k, v in ulos.items() if v["kylla"] is not None}


def kirjaa_arvio(tila, tehtava, kylla, syy, nyt=None):
    nyt = nyt or datetime.now().astimezone()
    _liita(tila, {"aika": nyt.isoformat(timespec="seconds"), "laji": "aivo", "tehtava": tehtava, "kylla": bool(kylla), "syy": str(syy)[:SYY_MAX]})


def arvioi_varjossa(tila, tehtava_polku, arvioija, nyt=None):
    """Kutsuu arvioijaa (teksti -> (kylla: bool, syy: str)) kerran per tehtava. Ei koskaan nosta virhetta eika esta ajoa.
    Palauttaa True, jos arvio kirjattiin. Virheesta kirjataan vain tyyppi."""
    if arvioija is None:
        return False
    nimi = Path(tehtava_polku).name
    try:
        if nimi in arviot(tila):
            return False
        teksti = Path(tehtava_polku).read_bytes()[:20000].decode("utf-8", "replace")
        kylla, syy = arvioija(teksti)
        if not isinstance(kylla, bool):
            return False
        kirjaa_arvio(tila, nimi, kylla, syy, nyt)
        return True
    except Exception as e:   # varjotila: mikaan ei saa kaataa ajoa; viestia ei tulosteta (voi sisaltaa salaisuuksia)
        print(f"aivo_arvio: arviointi epaonnistui ({type(e).__name__})", file=sys.stderr)
        return False


def otos(tila, n=OTOS_OLETUS, rng=None):
    """n satunnaista arviota, joita Tony ei ole viela tarkistanut."""
    avoimet = sorted(k for k, v in arviot(tila).items() if v["tony"] is None)
    rng = rng or random.Random()
    return [(k, arviot(tila)[k]) for k in rng.sample(avoimet, min(n, len(avoimet)))]


def tarkista(tila, tehtava, kylla, nyt=None):
    if tehtava not in arviot(tila):
        raise KeyError("ei Aivon arviota tehtavalle")
    nyt = nyt or datetime.now().astimezone()
    _liita(tila, {"aika": nyt.isoformat(timespec="seconds"), "laji": "tony", "tehtava": tehtava, "kylla": bool(kylla)})


def osuma(tila):
    """(tarkistettuja, osumia, prosentti|None, arvioituja_yhteensa)"""
    a = arviot(tila)
    tark = [v for v in a.values() if v["tony"] is not None]
    osumat = sum(1 for v in tark if v["tony"] == v["kylla"])
    return len(tark), osumat, (round(100 * osumat / len(tark), 1) if tark else None), len(a)


def viikkoosio(tila, n=OTOS_OLETUS, rng=None):
    """Markdown viikkosuunnittelun pohjaan: otos Tonyn tarkistettavaksi ja osumaprosentti."""
    tark, osumia, pros, yht = osuma(tila)
    rivit = ["## Aivon oleellisuusarviot (varjotila, ei est\u00e4 ajoa)", "",
             f"Arvioituja yhteens\u00e4 {yht}; Tony on tarkistanut {tark}; osumia {osumia}; osumaprosentti "
             + (f"{pros} %." if pros is not None else "ei viel\u00e4 laskettavissa.")
             + f" L\u00e4ht\u00f6tason raja: v\u00e4hint\u00e4\u00e4n {VAHIMMAIS_LAHTOTASOON} arvioitua p\u00e4\u00e4t\u00f6st\u00e4."]
    o = otos(tila, n, rng)
    if not o:
        rivit += ["", "Tarkistettavia arvioita ei ole (arvioija ei ole viel\u00e4 kytketty tai kaikki on tarkistettu)."]
    else:
        rivit += ["", f"Tarkista {len(o)} satunnaista arviota (`python3 -m claudejono.aivo_arvio tarkista <tehtava> kylla|ei`):", ""]
        kylla_sana = "kyll\u00e4"
        rivit += [f"- {k}: Aivo sanoi {kylla_sana if v['kylla'] else 'ei'} - {v['syy']}" for k, v in o]
    return "\n".join(rivit) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Aivon oleellisuusarviot (varjotila)")
    p.add_argument("--tila", default=os.environ.get("CLAUDEJONO_TILA", "/var/lib/claude-jono"))
    s = p.add_subparsers(dest="komento", required=True)
    s.add_parser("otos")
    s.add_parser("osuma")
    t = s.add_parser("tarkista")
    t.add_argument("tehtava")
    t.add_argument("tuomio", choices=("kylla", "ei"))
    a = p.parse_args(argv)
    if a.komento == "otos":
        print(viikkoosio(a.tila), end="")
    elif a.komento == "osuma":
        print(osuma(a.tila))
    else:
        try:
            tarkista(a.tila, a.tehtava, a.tuomio == "kylla")
        except KeyError as e:
            print(f"virhe: {e.args[0]}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


VALMIUS_TIEDOSTO = "claude-jono/aivo-valmius.md"
RUUTU_RE = __import__("re").compile(r"(?m)^[ \t]*- \[([ xX])\]")


def valmius_taynna(repo):
    """True, kun aivo-valmius.md:ssa on vahintaan 5 ruutua ja jokainen on rastitettu. Puuttuva tiedosto -> False.
    Ruudut rastittaa Tony tai Claude vasta todennettuaan kohdan; tama funktio vain lukee."""
    try:
        teksti = (Path(repo) / VALMIUS_TIEDOSTO).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    ruudut = RUUTU_RE.findall(teksti)
    return len(ruudut) >= 5 and all(r in "xX" for r in ruudut)
