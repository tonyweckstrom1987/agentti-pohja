#!/usr/bin/env python3
"""Hiekkalaatikon apuohjelma: CT-valin tarkistus, kustannusvaraus, kokeilulokin kirjaus ja jatkokaytto.

Vain vakiokirjasto. Ei salaisuuksia, ei verkkoa. Ajetaan isannalla aja-kokeilu.sh:sta (python3 -I hl.py ...).
Kirjanpito noudattaa saman periaatetta kuin yojonon kulu.py: varaus ENNEN ajoa, tasaus sen jalkeen,
rikkinainen kirjanpito pysayttaa ajon (fail closed) eika nollaudu hiljaa.
"""
import argparse
import fcntl
import json
import os
import re
import statistics
import sys
import tempfile
import time

# Varattu CT-ID-valinta kokeilukloonneille. Tuotantokontit ovat 110-121 ja 211; mallipohja ja proxy ovat valin ulkopuolella.
ALKU_ID = 9100
LOPPU_ID = 9199
POHJA_ID = 9000
PROXY_ID = 9001

# Kovat ylarajat: ymparistomuuttuja saa vain pienentaa. Suurempi raja vaatii koodimuutoksen (PR + Tonyn hyvaksynta).
ENINT_KOKEILU_USD = 1.00
ENINT_PAIVA_USD = 5.00
ENINT_KOKEILUA_PAIVA = 20
ENINT_AIKA_S = 3600

LOPPUTULOKSET = ("onnistui", "epaonnistui", "keskeytetty")
NIMI_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
KENTAT = ("id", "aika", "kokeilu", "adapteri", "malli", "kesto_s", "kustannus_usd", "lopputulos", "rajarikkeet", "lokit")


class Virhe(Exception):
    pass


def ct_id_sallittu(arvo):
    """Vain kokeilukloonien varattu vali. Mallipohja (9000), proxy (9001) ja kaikki tuotanto-CT:t hylataan."""
    if isinstance(arvo, bool):
        return False
    if isinstance(arvo, str):
        if not re.fullmatch(r"[1-9][0-9]{0,5}", arvo):
            return False
        arvo = int(arvo, 10)
    if not isinstance(arvo, int):
        return False
    return ALKU_ID <= arvo <= LOPPU_ID


def nimi_sallittu(nimi):
    return isinstance(nimi, str) and NIMI_RE.fullmatch(nimi) is not None


def _nyt():
    return time.time()


def _paiva(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


# ---------- kustannusvaraus ----------

class _Lukko:
    def __init__(self, polku):
        self.polku = polku

    def __enter__(self):
        self.fd = os.open(self.polku, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        os.close(self.fd)


def _lue_kulu(polku):
    try:
        with open(polku, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"varaukset": {}}
    except (OSError, ValueError):
        raise Virhe("kulu.json ei ole luettavissa")
    if not isinstance(data, dict) or not isinstance(data.get("varaukset"), dict):
        raise Virhe("kulu.json rakenne vaara")
    for v in data["varaukset"].values():
        if not isinstance(v, dict) or not isinstance(v.get("paiva"), str) \
                or not isinstance(v.get("arvio"), (int, float)) \
                or not (v.get("kulu") is None or isinstance(v.get("kulu"), (int, float))):
            raise Virhe("kulu.json rivi vaara")
    return data


def _kirjoita_kulu(polku, data):
    paivat = sorted({v["paiva"] for v in data["varaukset"].values()})[-62:]
    data["varaukset"] = {k: v for k, v in data["varaukset"].items() if v["paiva"] in paivat}
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(polku), prefix=".kulu.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, polku)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def rajat_tarkista(kokeilu_usd, paiva_usd, kokeilua_paiva, aika_s):
    if not (0 < kokeilu_usd <= ENINT_KOKEILU_USD):
        raise Virhe("kokeilukatto ylittaa koodin ylarajan %s USD" % ENINT_KOKEILU_USD)
    if not (0 < paiva_usd <= ENINT_PAIVA_USD):
        raise Virhe("paivakatto ylittaa koodin ylarajan %s USD" % ENINT_PAIVA_USD)
    if not (0 < kokeilua_paiva <= ENINT_KOKEILUA_PAIVA):
        raise Virhe("kokeilumaara/paiva ylittaa koodin ylarajan %d" % ENINT_KOKEILUA_PAIVA)
    if not (0 < aika_s <= ENINT_AIKA_S):
        raise Virhe("aikakatto ylittaa koodin ylarajan %d s" % ENINT_AIKA_S)


def varaa(hakemisto, kokeilu_usd, paiva_usd, kokeilua_paiva, aika_s, kello=None):
    """Varaa kokeilun koko kustannuskaton ennen ajoa. Palauttaa varaus-id:n. Virhe = ajoa ei aloiteta."""
    rajat_tarkista(kokeilu_usd, paiva_usd, kokeilua_paiva, aika_s)
    t = (kello or _nyt)()
    paiva = _paiva(t)
    polku = os.path.join(hakemisto, "kulu.json")
    with _Lukko(os.path.join(hakemisto, "kulu.lock")):
        data = _lue_kulu(polku)
        tanaan = [v for v in data["varaukset"].values() if v["paiva"] == paiva]
        if len(tanaan) >= kokeilua_paiva:
            raise Virhe("kokeilut_paiva")
        summa = sum(v["arvio"] if v["kulu"] is None else v["kulu"] for v in tanaan)
        if summa + kokeilu_usd > paiva_usd + 1e-9:
            raise Virhe("kulu_paiva")
        vid = "%s-%d" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t)), os.getpid())
        while vid in data["varaukset"]:
            vid += "x"
        data["varaukset"][vid] = {"paiva": paiva, "arvio": round(kokeilu_usd, 6), "kulu": None}
        _kirjoita_kulu(polku, data)
    return vid


def tasaa(hakemisto, vid, kulu_usd):
    """Korvaa varauksen todellisella kululla. None = jata pessimistinen varaus (koko katto)."""
    if kulu_usd is None:
        return
    if not isinstance(kulu_usd, (int, float)) or isinstance(kulu_usd, bool) or kulu_usd < 0 or kulu_usd != kulu_usd:
        raise Virhe("kulu ei kelpaa")
    polku = os.path.join(hakemisto, "kulu.json")
    with _Lukko(os.path.join(hakemisto, "kulu.lock")):
        data = _lue_kulu(polku)
        if vid not in data["varaukset"]:
            raise Virhe("varausta ei loydy")
        data["varaukset"][vid]["kulu"] = round(float(kulu_usd), 6)
        _kirjoita_kulu(polku, data)


# ---------- lokirivi ----------

def tarkista_rivi(r):
    if not isinstance(r, dict):
        raise Virhe("rivi ei ole objekti")
    if set(r) != set(KENTAT):
        raise Virhe("kentat eivat tasmaa: %s" % sorted(set(r) ^ set(KENTAT)))
    for k in ("id", "aika", "kokeilu", "adapteri", "malli", "lokit"):
        if not isinstance(r[k], str) or not r[k]:
            raise Virhe("kentta %s puuttuu" % k)
    if not nimi_sallittu(r["kokeilu"]) or not nimi_sallittu(r["adapteri"]):
        raise Virhe("kokeilun tai adapterin nimi ei kelpaa")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r["aika"]):
        raise Virhe("aika ei ole UTC-ISO")
    if not isinstance(r["kesto_s"], (int, float)) or isinstance(r["kesto_s"], bool) or r["kesto_s"] < 0:
        raise Virhe("kesto_s ei kelpaa")
    k = r["kustannus_usd"]
    if k is not None and (not isinstance(k, (int, float)) or isinstance(k, bool) or k < 0):
        raise Virhe("kustannus_usd ei kelpaa")
    if r["lopputulos"] not in LOPPUTULOKSET:
        raise Virhe("lopputulos ei kelpaa")
    if not isinstance(r["rajarikkeet"], list):
        raise Virhe("rajarikkeet ei ole lista")
    for x in r["rajarikkeet"]:
        if not isinstance(x, dict) or not isinstance(x.get("tyyppi"), str) or not isinstance(x.get("maara"), int):
            raise Virhe("rajarike ei kelpaa")


def kirjaa_rivi(polku, rivi):
    tarkista_rivi(rivi)
    teksti = json.dumps(rivi, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
    with _Lukko(polku + ".lock"):
        fd = os.open(polku, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            os.write(fd, teksti.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)


def lue_rivit(polku):
    rivit = []
    with open(polku, encoding="utf-8") as f:
        for n, rivi in enumerate(f, 1):
            if not rivi.strip():
                continue
            try:
                r = json.loads(rivi)
                tarkista_rivi(r)
            except (ValueError, Virhe) as e:
                raise Virhe("rivi %d: %s" % (n, type(e).__name__))
            rivit.append(r)
    return rivit


# ---------- lopputuloksen paatteleminen ----------

def lue_yhteenveto(teksti):
    """Adapterin viimeinen ei-tyhja stdout-rivi on JSON-objekti. Muu tulos = tuntematon (kustannus None)."""
    rivit = [x for x in (teksti or "").splitlines() if x.strip()]
    if not rivit:
        return {}
    try:
        d = json.loads(rivit[-1])
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def paata(adapteri_rc, yhteenveto, tarkistus_rc, keskeytetty):
    """Palauttaa (lopputulos, kustannus_usd tai None, malli tai None).
    Aikakatto tai signaali = keskeytetty. Objektiivinen tarkistus (jos on) voittaa agentin oman vaitteen."""
    kulu = yhteenveto.get("kustannus_usd")
    if isinstance(kulu, bool) or not isinstance(kulu, (int, float)) or kulu < 0 or kulu != kulu:
        kulu = None
    malli = yhteenveto.get("malli") if isinstance(yhteenveto.get("malli"), str) and yhteenveto.get("malli") else None
    if keskeytetty:
        return "keskeytetty", kulu, malli
    if tarkistus_rc is not None:
        tulos = "onnistui" if tarkistus_rc == 0 else "epaonnistui"
    elif adapteri_rc == 0 and yhteenveto.get("lopputulos") == "onnistui":
        tulos = "onnistui"
    else:
        tulos = "epaonnistui"
    if adapteri_rc != 0:
        tulos = "epaonnistui"
    return tulos, kulu, malli


# ---------- rajarikkeet (proxyn access.log) ----------

def rajarikkeet_lokista(teksti, ip, alku, loppu):
    """Squidin natiivilokista (aika, kesto, asiakas, tila/koodi, koko, metodi, url, ...) kielletyt pyynnot ajalta.
    Palauttaa listan rajarikkeita; kohteissa vain isanta:portti, ei polkua eika kyselya (voisi sisaltaa salaisuuden)."""
    kielletyt = {}
    for rivi in (teksti or "").splitlines():
        k = rivi.split()
        if len(k) < 7 or k[2] != ip:
            continue
        try:
            t = float(k[0])
        except ValueError:
            continue
        if not (alku - 1 <= t <= loppu + 1) or "DENIED" not in k[3]:
            continue
        url = k[6]
        kohde = re.sub(r"^[a-z]+://", "", url).split("/", 1)[0][:100]
        kielletyt[kohde] = kielletyt.get(kohde, 0) + 1
    if not kielletyt:
        return []
    kohteet = sorted(kielletyt, key=lambda x: (-kielletyt[x], x))[:20]
    return [{"tyyppi": "estetty_verkkoyhteys", "maara": sum(kielletyt.values()), "kohteet": kohteet}]


# ---------- jatkokaytto ----------

def _mediaani(xs):
    return statistics.median(xs) if xs else None


def taulu(rivit):
    """Markdown-tulostaulu: kokeilu x malli. Keskeytetyt eivat laske onnistumisprosenttiin, mutta nakyvat omassa sarakkeessa."""
    ryhmat = {}
    for r in rivit:
        ryhmat.setdefault((r["kokeilu"], r["adapteri"], r["malli"]), []).append(r)
    out = ["| kokeilu | adapteri | malli | ajoja | onnistui | keskeytetty | kesto (mediaani s) | hinta (keskiarvo USD) | rajarikkeita |",
           "|---|---|---|---|---|---|---|---|---|"]
    for (kok, ada, mal), rs in sorted(ryhmat.items()):
        ratkaistut = [r for r in rs if r["lopputulos"] != "keskeytetty"]
        ok = sum(1 for r in ratkaistut if r["lopputulos"] == "onnistui")
        osuus = "%d/%d" % (ok, len(ratkaistut))
        hinnat = [r["kustannus_usd"] for r in rs if r["kustannus_usd"] is not None]
        hinta = "%.4f" % (sum(hinnat) / len(hinnat)) if hinnat else "?"
        med = _mediaani([r["kesto_s"] for r in rs])
        rike = sum(x["maara"] for r in rs for x in r["rajarikkeet"])
        out.append("| %s | %s | %s | %d | %s | %d | %s | %s | %d |" % (
            kok, ada, mal, len(rs), osuus, len(rs) - len(ratkaistut), "%.0f" % med, hinta, rike))
    return "\n".join(out) + "\n"


def eval_vienti(rivit):
    """JSONL mallivertailudataksi: yksi rivi per ratkaistu ajo (keskeytetyt jaavat pois, koska ne eivat kerro mallin kyvysta)."""
    out = []
    for r in rivit:
        if r["lopputulos"] == "keskeytetty":
            continue
        out.append(json.dumps({
            "lahde": "hiekkalaatikko", "ajo_id": r["id"], "aika": r["aika"], "tehtava": r["kokeilu"],
            "malli": r["malli"], "adapteri": r["adapteri"], "onnistui": r["lopputulos"] == "onnistui",
            "kesto_s": r["kesto_s"], "kustannus_usd": r["kustannus_usd"],
        }, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    return "\n".join(out) + ("\n" if out else "")


# ---------- komentorivi ----------

def _luku(s):
    try:
        return float(s)
    except ValueError:
        raise Virhe("luku ei kelpaa")


def main(argv=None):
    p = argparse.ArgumentParser(prog="hl.py")
    s = p.add_subparsers(dest="komento", required=True)
    a = s.add_parser("ct-sallittu"); a.add_argument("id")
    a = s.add_parser("nimi-sallittu"); a.add_argument("nimi")
    a = s.add_parser("varaa")
    a.add_argument("--tila", required=True)
    a.add_argument("--kokeilu-usd", required=True); a.add_argument("--paiva-usd", required=True)
    a.add_argument("--kokeilua-paiva", required=True); a.add_argument("--aika-s", required=True)
    a = s.add_parser("rajat")
    a.add_argument("--kokeilu-usd", required=True); a.add_argument("--paiva-usd", required=True)
    a.add_argument("--kokeilua-paiva", required=True); a.add_argument("--aika-s", required=True)
    a = s.add_parser("paata")
    a.add_argument("--tila", required=True); a.add_argument("--varaus", required=True)
    a.add_argument("--id", required=True); a.add_argument("--aika", required=True)
    a.add_argument("--kokeilu", required=True); a.add_argument("--adapteri", required=True)
    a.add_argument("--malli-pyydetty", default=""); a.add_argument("--kesto", required=True)
    a.add_argument("--adapteri-rc", type=int, required=True)
    a.add_argument("--yhteenveto", required=True)
    a.add_argument("--tarkistus-rc", type=int, default=None)
    a.add_argument("--keskeytetty", action="store_true")
    a.add_argument("--rajarikkeet", required=True, help="JSON-lista tiedostossa")
    a.add_argument("--lokit", required=True)
    a = s.add_parser("rajarikkeet")
    a.add_argument("--loki", required=True); a.add_argument("--ip", required=True)
    a.add_argument("--alku", required=True); a.add_argument("--loppu", required=True)
    a = s.add_parser("taulu"); a.add_argument("jsonl")
    a = s.add_parser("eval-vienti"); a.add_argument("jsonl")
    a = s.add_parser("tarkista"); a.add_argument("jsonl")
    n = p.parse_args(argv)
    try:
        if n.komento == "ct-sallittu":
            return 0 if ct_id_sallittu(n.id) else 1
        if n.komento == "nimi-sallittu":
            return 0 if nimi_sallittu(n.nimi) else 1
        if n.komento in ("varaa", "rajat"):
            args = (_luku(n.kokeilu_usd), _luku(n.paiva_usd), int(_luku(n.kokeilua_paiva)), int(_luku(n.aika_s)))
            if n.komento == "rajat":
                rajat_tarkista(*args)
                return 0
            print(varaa(n.tila, *args))
            return 0
        if n.komento == "paata":
            with open(n.yhteenveto, encoding="utf-8", errors="replace") as f:
                ys = lue_yhteenveto(f.read(65536))
            with open(n.rajarikkeet, encoding="utf-8") as f:
                rike = json.load(f)
            tulos, kulu, malli = paata(n.adapteri_rc, ys, n.tarkistus_rc, n.keskeytetty)
            malli = malli or n.malli_pyydetty or "tuntematon"
            tasaa(n.tila, n.varaus, kulu)
            rivi = {"id": n.id, "aika": n.aika, "kokeilu": n.kokeilu, "adapteri": n.adapteri, "malli": malli,
                    "kesto_s": int(_luku(n.kesto)), "kustannus_usd": kulu, "lopputulos": tulos,
                    "rajarikkeet": rike, "lokit": n.lokit}
            if kulu is not None and kulu > ENINT_KOKEILU_USD:
                rivi["rajarikkeet"].append({"tyyppi": "kustannus_yli_koodin_ylarajan", "maara": 1})
            kirjaa_rivi(os.path.join(n.tila, "kokeilut.jsonl"), rivi)
            print(tulos)
            return 0
        if n.komento == "rajarikkeet":
            with open(n.loki, encoding="utf-8", errors="replace") as f:
                print(json.dumps(rajarikkeet_lokista(f.read(), n.ip, _luku(n.alku), _luku(n.loppu)), sort_keys=True))
            return 0
        if n.komento == "taulu":
            sys.stdout.write(taulu(lue_rivit(n.jsonl)))
            return 0
        if n.komento == "eval-vienti":
            sys.stdout.write(eval_vienti(lue_rivit(n.jsonl)))
            return 0
        if n.komento == "tarkista":
            print("%d rivia" % len(lue_rivit(n.jsonl)))
            return 0
    except Virhe as e:
        print("hl.py: %s" % e, file=sys.stderr)
        return 3
    except OSError as e:
        print("hl.py: %s" % type(e).__name__, file=sys.stderr)
        return 4
    return 2


if __name__ == "__main__":
    sys.exit(main())
