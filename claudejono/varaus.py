"""Tonyn varaus Claude-tilauksen viikkorajasta ja kayttorajavirheen pysaytys.

Claude-tilauksen viikkoraja on yhteinen Tonyn kanssa. Agenttien osuus enintaan OSUUS_OLETUS_PROS % (oletus 60, Tony muuttaa
yhdesta asetuksesta CLAUDEJONO_AGENTTIEN_OSUUS_PROS). Tilauksen prosenttia ei voi lukea koneellisesti, joten kalibroidaan:
Tony ilmoittaa asetussivun luvun "Talla viikolla x % kaytetty" (`python3 -m claudejono.varaus kalibroi X`), ja mittari laskee
agenttien kayttaman API-arvon (kulutus.jsonl, vain Claude-reitit) ja prosenttimuutoksen suhteen.

Arvio on tarkoituksella varovainen: kahden ilmoituksen valisen prosenttimuutoksen katsotaan johtuvan kokonaan agenteista, vaikka osa
oli Tonyn omaa kayttoa. Siksi agenttien osuus yliarvioidaan, ei aliarvioida (Tonylle jaa varmasti varaus).

Tilat: ei_kalibroitu (ei hidastusta, mutta aamukooste kysyy lukua) -> ok -> hidasta (>= 80 % osuudesta: yksi kaista) -> pysayta (>= osuus).
Lisaksi: jos Tonyn tuoreen ilmoituksen kokonaisprosentti on >= KOKONAIS_PYSAYTYS_PROS, kaistat pysahtyvat.
OpenRouter-reitti ei kuluta tilausta, joten tama portti ei koske sita.

Kayttorajavirhe (rate limit) pysayttaa kaikki Claude-kaistat tauoksi (CLAUDEJONO_RAJA_TAUKO_S, oletus 3600 s) eika uusi ajoa silmukassa:
tehtavaa yritetaan enintaan RAJA_YRITYKSIA kertaa, minka jalkeen se kirjataan epaonnistuneeksi.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import kulutus

OSUUS_OLETUS_PROS = 60
OSUUS_MAX_PROS = 100
HIDASTUS_SUHDE = 0.8               # hidastus alkaa tasta osasta agenttien osuudesta
KOKONAIS_PYSAYTYS_PROS = 90        # Tonyn ilmoittama kokonaiskaytto (tuore): agentit pysahtyvat, jotta Tonylle jaa kayttoa
TUORE_ILMOITUS_H = 36
KALIBROINTI_VANHA_H = 48           # aamukooste kysyy lukua, kun viimeisin on tata vanhempi
MIN_KALIBROINTI_USD = 0.5          # ilmoitusten valilla oltava nain paljon agenttiarvoa, jotta suhde on luotettava
KALIBROINTI = "kalibrointi.jsonl"
RAJA_TILA = "raja-pysaytys.json"
RAJA_YRITYKSET = "raja-yritykset.json"
ILMOITETTU = "varaus-ilmoitettu.json"
OLETUS_RAJA_TAUKO_S = 3600
RAJA_YRITYKSIA = 3
TZ = kulutus.TZ
CLAUDE_REITIT = ("opus", "sonnet")


def osuus_pros(env):
    """Agenttien osuus % yhdesta asetuksesta. Virheellinen -> oletus 60; rajataan 0..100."""
    try:
        n = float(env.get("CLAUDEJONO_AGENTTIEN_OSUUS_PROS", OSUUS_OLETUS_PROS))
    except (TypeError, ValueError):
        return float(OSUUS_OLETUS_PROS)
    if n != n:    # NaN
        return float(OSUUS_OLETUS_PROS)
    return max(0.0, min(float(OSUUS_MAX_PROS), n))


def kirjaa_kalibrointi(tila, pros, nyt=None):
    """Tonyn ilmoittama luku 0..100. Palauttaa True, jos tallennettiin."""
    nyt = nyt or datetime.now(TZ)
    if isinstance(pros, bool) or not isinstance(pros, (int, float)) or not 0 <= pros <= 100:
        return False
    try:
        fd = os.open(Path(tila) / KALIBROINTI, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps({"aika": nyt.astimezone(TZ).isoformat(timespec="seconds"), "pros": pros}) + "\n")
    except OSError:
        return False
    return True


def _claude_usd(rivit):
    return sum(float(r.get("usd") or 0) for r in rivit if r.get("malli") in CLAUDE_REITIT)


def kalibrointi_aikaisin(tila, nyt):
    """Ilmoituksen ika tunteina (viimeisin) tai None, jos ilmoituksia ei ole."""
    luvut = kulutus.lue(tila, tiedosto=KALIBROINTI)
    if not luvut:
        return None
    return (nyt - max(r["_aika"] for r in luvut)).total_seconds() / 3600


def arvio(tila, env, nyt):
    """{tila, osuus, agentit_pros|None, kaistoja, syy}. kaistoja = sallittujen Claude-kaistojen enimmaismaara (0 = pysayta)."""
    from .ajuri import kaistojen_maara
    raja = osuus_pros(env)
    kaikki_kaistat = kaistojen_maara(env)
    viikko_alku = kulutus.viikon_alku(nyt)
    ilmoitukset = sorted(kulutus.lue(tila, nyt - timedelta(days=28), nyt + timedelta(seconds=1), tiedosto=KALIBROINTI), key=lambda r: r["_aika"])
    ulos = {"osuus": raja, "agentit_pros": None, "kaistoja": kaikki_kaistat, "tila": "ei_kalibroitu", "syy": "viikkorajan prosenttia ei ole kalibroitu"}
    tuore = [r for r in ilmoitukset if r["_aika"] >= viikko_alku and (nyt - r["_aika"]).total_seconds() <= TUORE_ILMOITUS_H * 3600]
    if tuore and isinstance(tuore[-1].get("pros"), (int, float)) and tuore[-1]["pros"] >= KOKONAIS_PYSAYTYS_PROS:
        return {**ulos, "tila": "pysayta", "kaistoja": 0, "syy": f"Tonyn ilmoittama kokonaiskaytto {tuore[-1]['pros']:g} % >= {KOKONAIS_PYSAYTYS_PROS} %"}
    # Suhde (% / USD) niista ilmoitusparista, jotka ovat samalla viikolla (viikkoraja nollautuu, joten viikon yli ei verrata)
    sum_pros = sum_usd = 0.0
    for a, b in zip(ilmoitukset, ilmoitukset[1:]):
        if kulutus.viikon_alku(a["_aika"]) != kulutus.viikon_alku(b["_aika"]):
            continue
        usd = _claude_usd(kulutus.lue(tila, a["_aika"], b["_aika"]))
        dp = float(b.get("pros", 0)) - float(a.get("pros", 0))
        if usd >= MIN_KALIBROINTI_USD and dp > 0:
            sum_pros += dp
            sum_usd += usd
    if sum_usd <= 0:
        return ulos
    viikon_usd = _claude_usd(kulutus.lue(tila, viikko_alku, nyt + timedelta(seconds=1)))
    pros = round(sum_pros / sum_usd * viikon_usd, 1)
    ulos["agentit_pros"] = pros
    if pros >= raja:
        return {**ulos, "tila": "pysayta", "kaistoja": 0, "syy": f"agenttien arvioitu osuus {pros:g} % >= raja {raja:g} %"}
    if pros >= HIDASTUS_SUHDE * raja:
        return {**ulos, "tila": "hidasta", "kaistoja": min(1, kaikki_kaistat), "syy": f"agenttien arvioitu osuus {pros:g} % >= {HIDASTUS_SUHDE * raja:g} % (80 % rajasta {raja:g} %)"}
    return {**ulos, "tila": "ok", "syy": f"agenttien arvioitu osuus {pros:g} % / raja {raja:g} %"}


# --- Kayttorajavirhe ---

def raja_tauko_s(env):
    try:
        return max(60, int(env.get("CLAUDEJONO_RAJA_TAUKO_S", OLETUS_RAJA_TAUKO_S)))
    except (TypeError, ValueError):
        return OLETUS_RAJA_TAUKO_S


def raja_tauolla(tila, nyt_s=None):
    """Sekunteja jaljella pysaytystaukoa tai 0."""
    nyt_s = time.time() if nyt_s is None else nyt_s
    try:
        d = json.loads((Path(tila) / RAJA_TILA).read_text(encoding="utf-8"))
        asti = float(d["asti"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return max(0, int(asti - nyt_s))


def kirjaa_raja(tila, kaista, tehtava, env, nyt_s=None):
    """Pysayttaa kaikki Claude-kaistat tauoksi ja laskee tehtavan yritykset. Palauttaa (taukoa_s, yritykset)."""
    nyt_s = time.time() if nyt_s is None else nyt_s
    tauko = raja_tauko_s(env)
    try:
        (Path(tila) / RAJA_TILA).write_text(json.dumps({"asti": nyt_s + tauko, "kaista": kaista, "tehtava": tehtava}), encoding="utf-8")
    except OSError:
        pass
    try:
        yritykset = json.loads((Path(tila) / RAJA_YRITYKSET).read_text(encoding="utf-8"))
        if not isinstance(yritykset, dict):
            yritykset = {}
    except (OSError, ValueError):
        yritykset = {}
    yritykset[tehtava] = int(yritykset.get(tehtava, 0)) + 1
    try:
        (Path(tila) / RAJA_YRITYKSET).write_text(json.dumps(yritykset), encoding="utf-8")
    except OSError:
        pass
    return tauko, yritykset[tehtava]


def raja_yrityksia(tila, tehtava):
    try:
        return int(json.loads((Path(tila) / RAJA_YRITYKSET).read_text(encoding="utf-8")).get(tehtava, 0))
    except (OSError, ValueError, AttributeError, TypeError):
        return 0


# --- Portti ajurille ---

def claude_estetty(tila, env, kaista, nyt=None, nyt_s=None):
    """Syy (teksti), jos Claude-reitin ajo on estetty talla kaistalla, muuten None. Ei koske OpenRouter-reittia."""
    nyt = nyt or datetime.now(TZ)
    j = raja_tauolla(tila, nyt_s)
    if j > 0:
        return f"kayttorajavirhe: Claude-kaistat tauolla viela {j} s"
    a = arvio(tila, env, nyt)
    if a["kaistoja"] < kaista:
        return f"Tonyn varaus ({a['tila']}): {a['syy']}; sallittuja Claude-kaistoja {a['kaistoja']}"
    return None


def ilmoita_muutos(tila, a, laheta, nyt_s=None):
    """Telegram-ilmoitus vain kun tila vaihtuu hidasta/pysayta-tilaan (tai pois). Palauttaa True, jos lahetettiin."""
    nyt_s = time.time() if nyt_s is None else nyt_s
    try:
        vanha = json.loads((Path(tila) / ILMOITETTU).read_text(encoding="utf-8")).get("tila")
    except (OSError, ValueError, AttributeError):
        vanha = "ok"
    uusi = a["tila"] if a["tila"] in ("hidasta", "pysayta") else "ok"
    if uusi == vanha or not laheta:
        return False
    if uusi == "ok":
        teksti = "Claude-kaistat palaavat normaaliksi (agenttien osuus alle rajan)."
    else:
        teksti = f"Claude-kaistat: {uusi}. {a['syy']}. Kerro asetussivun viikkoluku: python3 -m claudejono.varaus kalibroi <luku>"
    laheta(teksti)
    try:
        (Path(tila) / ILMOITETTU).write_text(json.dumps({"tila": uusi, "aika": nyt_s}), encoding="utf-8")
    except OSError:
        pass
    return True


def aamukooste_osa(tila, env, nyt):
    """(tilarivi, kysymys|None) aamukoosteeseen."""
    a = arvio(tila, env, nyt)
    if a["agentit_pros"] is None:
        rivi = f"Tonyn varaus: raja {a['osuus']:g} % agenteille, arvio puuttuu (ei kalibroitu)"
    else:
        rivi = f"Tonyn varaus: agentit {a['agentit_pros']:g} % / {a['osuus']:g} % ({a['tila']})"
    ika = kalibrointi_aikaisin(tila, nyt)
    kysymys = None
    if ika is None or ika > KALIBROINTI_VANHA_H:
        kysymys = 'kerro asetussivun luku "T\u00e4ll\u00e4 viikolla x % k\u00e4ytetty" (python3 -m claudejono.varaus kalibroi <luku>)'
    return rivi, kysymys


def main(argv=None):
    p = argparse.ArgumentParser(description="Tonyn varaus: kalibrointi ja tila")
    p.add_argument("komento", choices=["kalibroi", "tila"])
    p.add_argument("luku", nargs="?", type=float)
    p.add_argument("--tila", default=os.environ.get("CLAUDEJONO_TILA", "/var/lib/claude-jono"))
    a = p.parse_args(argv)
    if a.komento == "kalibroi":
        if a.luku is None or not kirjaa_kalibrointi(a.tila, a.luku):
            print("anna luku 0..100", file=sys.stderr)
            return 2
        print(f"kirjattu {a.luku:g} %")
        return 0
    print(json.dumps(arvio(a.tila, os.environ, datetime.now(TZ)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
