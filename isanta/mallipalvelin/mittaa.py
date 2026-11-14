"""Mittaa paikallisen mallipalvelimen (Ollama) ohjeiden noudatus, yksityisyyden erottelu ja nopeus.

Kayttaa vain keksittyja tapauksia (tapaukset.json). Osoite annetaan ymparistomuuttujalla
MALLIPALVELIN_URL; oletus on Aivon yksityisen tilan oletus http://127.0.0.1:8081. Osoitesaanto on sama kuin
Aivon yksityisessa tilassa (aivo/yksityinen.py, sallittu_malli_osoite): vain http ja loopback-IP-osoite,
ei nimia, jotta keskustelut eivat mene verkkoon.
Kaytto: python3 -I -m isanta.mallipalvelin.mittaa MALLI [MALLI...]
"""
import ipaddress
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

KANSIO = os.path.dirname(os.path.abspath(__file__))
OLETUS_URL = "http://127.0.0.1:8081"


def osoite_sallittu(url):
    """Sama saanto kuin aivo/yksityinen.py:n sallittu_malli_osoite: vain http + loopback-IP (ei nimia)."""
    try:
        osat = urllib.parse.urlparse(url)
        ip = ipaddress.ip_address(osat.hostname or "")
    except ValueError:
        return False
    return osat.scheme == "http" and ip.is_loopback and not osat.username and not osat.password


def kysy(url, malli, jarjestelma, kysymys, aikaraja=600):
    runko = json.dumps({
        "model": malli, "stream": False,
        "messages": [{"role": "system", "content": jarjestelma}, {"role": "user", "content": kysymys}],
        "options": {"temperature": 0},
    }).encode()
    pyynto = urllib.request.Request(url.rstrip("/") + "/api/chat", runko, {"Content-Type": "application/json"})
    avaa = urllib.request.build_opener(urllib.request.ProxyHandler({})).open  # ei valityspalvelinta (kuten Aivo)
    with avaa(pyynto, timeout=aikaraja) as v:
        return json.load(v)


def vastausteksti(vast):
    return (vast.get("message") or {}).get("content", "")


def tarkista_ohje(teksti, ehdot):
    """Palauttaa (ok, syy). Ehdot: luettelomerkit, json_kentat, max_virkkeita, alkaa, sisaltaa."""
    if "luettelomerkit" in ehdot:
        n = len(re.findall(r"^- ", teksti, re.M))
        if n != ehdot["luettelomerkit"]:
            return False, f"luettelomerkkeja {n}"
    if "json_kentat" in ehdot:
        try:
            olio = json.loads(teksti.strip())
        except ValueError:
            return False, "ei JSON"
        if not isinstance(olio, dict) or not all(k in olio for k in ehdot["json_kentat"]):
            return False, "kentat puuttuvat"
    if "max_virkkeita" in ehdot:
        n = len([s for s in re.split(r"(?<=[.!?])\s+", teksti.strip()) if s])
        if n > ehdot["max_virkkeita"]:
            return False, f"virkkeita {n}"
    if "alkaa" in ehdot and not teksti.strip().startswith(ehdot["alkaa"]):
        return False, "alku vaarin"
    for sana in ehdot.get("sisaltaa", []):
        if sana.lower() not in teksti.lower():
            return False, f"puuttuu {sana}"
    return True, ""


def tarkista_yksityisyys(teksti, tapaus):
    for kielletty in tapaus["ei_saa_sisaltaa"]:
        if kielletty.lower() in teksti.lower():
            return False, "vuoto"
    for pakollinen in tapaus["pitaa_sisaltaa"]:
        if pakollinen.lower() not in teksti.lower():
            return False, f"puuttuu {pakollinen}"
    return True, ""


def nopeus(vast):
    """Sanaa (tokenia) sekunnissa Ollaman kentista; None jos kentat puuttuvat."""
    n, ns = vast.get("eval_count"), vast.get("eval_duration")
    return round(n / (ns / 1e9), 2) if n and ns else None


def mittaa_malli(url, malli, tapaukset, kysy_fn=kysy):
    tulos = {"malli": malli, "ohjeet": [], "yksityisyys": [], "nopeus_tok_s": []}
    for t in tapaukset["ohjeet"]:
        ok, syy = tarkista_ohje(vastausteksti(kysy_fn(url, malli, t["jarjestelma"], t["kysymys"])), t["tarkista"])
        tulos["ohjeet"].append({"id": t["id"], "ok": ok, "syy": syy})
    for t in tapaukset["yksityisyys"]:
        ok, syy = tarkista_yksityisyys(vastausteksti(kysy_fn(url, malli, t["jarjestelma"], t["kysymys"])), t)
        tulos["yksityisyys"].append({"id": t["id"], "ok": ok, "syy": syy})
    n = tapaukset["nopeus"]
    for _ in range(n["toistoja"]):
        alku = time.monotonic()
        vast = kysy_fn(url, malli, "Vastaa suomeksi.", n["kysymys"])
        tulos["nopeus_tok_s"].append(nopeus(vast) or round(time.monotonic() - alku, 2) * -1)
    return tulos


def tiivista(tulos):
    o = sum(1 for x in tulos["ohjeet"] if x["ok"])
    y = sum(1 for x in tulos["yksityisyys"] if x["ok"])
    nop = [x for x in tulos["nopeus_tok_s"] if x > 0]
    ka = round(sum(nop) / len(nop), 2) if nop else None
    return f"| {tulos['malli']} | {o}/{len(tulos['ohjeet'])} | {y}/{len(tulos['yksityisyys'])} | {ka} |"


def main(argv):
    url = os.environ.get("MALLIPALVELIN_URL") or OLETUS_URL
    if not osoite_sallittu(url):
        print("osoite hylatty: vain http ja loopback-IP-osoite", file=sys.stderr)
        return 2
    if not argv:
        print("anna ainakin yksi malli", file=sys.stderr)
        return 2
    with open(os.path.join(KANSIO, "tapaukset.json"), encoding="utf-8") as f:
        tapaukset = json.load(f)
    rivit = ["| malli | ohjeet | yksityisyys | tok/s |", "|---|---|---|---|"]
    kaikki = []
    for malli in argv:
        try:
            t = mittaa_malli(url, malli, tapaukset)
        except Exception as e:  # vain tyypin nimi (voi sisaltaa salaisuuksia)
            print(f"{malli}: virhe {type(e).__name__}", file=sys.stderr)
            continue
        kaikki.append(t)
        rivit.append(tiivista(t))
    print("\n".join(rivit))
    print(json.dumps(kaikki, ensure_ascii=False))
    return 0 if kaikki else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
