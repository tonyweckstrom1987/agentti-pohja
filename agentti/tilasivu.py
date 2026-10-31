"""Tilasivu: yksi puhelimella luettava sivu kaikesta (palvelut, jonot, kulut, PR:t, Tonyn tehtavat).

Generaattori kokoaa staattisen index.html, tila.md ja tila.json -tiedostot kohdehakemistoon. Vain luku: ei muuta jonoja,
kattoja eika palveluja; kulukatot luetaan jono.json:sta vain naytettavaksi. Salaisuuden nakoiset merkkijonot siivotaan
ennen kirjoitusta (tilannekuva.SALAISUUDET). Vain stdlib.

Kaytto: python3 -m agentti.tilasivu [--ei-telegram] [--ei-pr] [kohdehakemisto]
Ymparisto (oletukset CT115:ssa): TILASIVU_KOHDE=/var/lib/tilasivu, YOJONO_PERUS=/var/lib/yojono,
CLAUDEJONO_REPO=/var/lib/claude-jono/docs, TILASIVU_TONYLLE=<CLAUDEJONO_REPO>/claude-jono/tonylle.md.

Punainen: yksi Telegram-viesti (agentti.ilmoita, joka rajaa ja puhdistaa viestin), vain kun punaisten asioiden joukkoon tulee
uusi asia. Lahetys kirjataan vasta kun se onnistui, joten epaonnistunut lahetys yritetaan uudelleen seuraavalla ajolla.
"""
import datetime
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import aamupaketti, config, ilmoita
from .lukko import kirjoita_atomisesti
from .tilannekuva import SALAISUUDET

VARIT = ("vihrea", "keltainen", "punainen")
HARMAA = "harmaa"   # ei asennettu: ei vaikuta kokonaistilaan
JARJESTYS = {"harmaa": 0, "vihrea": 1, "keltainen": 2, "punainen": 3}
TUNTI = 3600

# (nimi, palveluyksikko, ajastinyksikot, vanhenee_h: keltainen sen jalkeen, punainen kaksinkertaisena, kestoraja_min)
PALVELUT = (
    ("claude-jono", "claude-jono.service", ("claude-jono.timer",), 2, 75),
    ("yojono", "yojono.service", ("yojono.timer", "yojono-a.timer", "yojono-c.timer"), 36, 240),
    ("trendit (paiva)", "trendit-paiva.service", ("trendit-paiva.timer",), 30, 60),
    ("trendit (viikko)", "trendit-viikko.service", ("trendit-viikko.timer",), 8 * 24, 60),
    ("viikkoraportti", "viikkoraportti.service", ("viikkoraportti.timer",), 8 * 24, 60),
    ("ideapankki", "ideapankki.service", ("ideapankki.timer",), 8 * 24, 120),
    ("docs-pull", "docs-pull.service", ("docs-pull.timer",), 2, 10),
    ("portti", "portti.service", (), 0, 0),   # jatkuvasti kaynnissa oleva palvelu
    ("kauppatiimi", "kauppatiimi.service", ("kauppatiimi.timer",), 36, 120),   # harmaa kunnes asennettu
)
SHOW_KENTAT = "LoadState,ActiveState,SubState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp"

JONO_KELTAINEN_H = 2
JONO_PUNAINEN_H = 4
KULU_KELTAINEN_OSUUS = 0.8
TUORE_RAJA_MIN = 45        # sivu merkitsee itsensa vanhentuneeksi, jos generaattori ei ole ajanut
PR_VALIMUISTI_MIN = 60     # GitHub-API ilman tunnuksia sallii 60 kutsua/h: PR-osio haetaan harvemmin kuin sivu paivittyy
TONYLLE_OSIOITA = 2
# tonylle.md ei ole rakenteinen: vain nama alkusanat tulkitaan Tonyn avoimiksi tehtaviksi (heuristiikka)
TONYLLE_ALUT = re.compile(r"(?i)^(P\u00c4\u00c4TETT\u00c4V\u00c4|PAATETTAVA|MERGEP\u00c4\u00c4T|MERGEPAAT|ODOTTA|KATSELMOITAVANA|AAMULLA|TONYN)")
TONYLLE_RIVEJA_MAX = 15
RIVI_MAX = 220
ERO_USD = 0.01


def siivoa_rivi(teksti):
    for s in SALAISUUDET:
        teksti = s.sub("[poistettu]", teksti)
    return " ".join(str(teksti).split())


def _huonoin(varit):
    return max(varit, key=lambda v: JARJESTYS[v], default="vihrea") if any(v != HARMAA for v in varit) else "vihrea"


def _json(polku, oletus):
    try:
        with open(polku, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return oletus


# --- palvelut -------------------------------------------------------------

def systemctl_show(yksikko, suorita=subprocess.run):
    """Palauttaa {avain: arvo}; aikaleimat unix-sekunteina. Virhe antaa tyhjan dictin (-> tila 'ei saatu')."""
    try:
        r = suorita(["systemctl", "show", yksikko, "--timestamp=unix", "-p", SHOW_KENTAT],
                    capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    tulos = {}
    for rivi in (r.stdout or "").splitlines():
        avain, _, arvo = rivi.partition("=")
        if avain:
            tulos[avain] = arvo
    return tulos


def _aika(arvo):
    """'@1793413927' -> 1793413927; tyhja tai tuntematon -> None."""
    m = re.fullmatch(r"@(\d+)", (arvo or "").strip())
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def arvioi_palvelu(tieto, ajastimet, vanhenee_h, kesto_min, edellinen_ok, nyt):
    """Palauttaa (vari, syy, viimeisin_ok_unix). tieto: systemctl show -dict; ajastimet: [ajastimen ActiveState];
    edellinen_ok: aiemmin kirjattu onnistuneen ajon aika (systemd ei muista sita epaonnistumisen jalkeen)."""
    if not tieto:
        return "keltainen", "tilaa ei saatu (systemctl)", edellinen_ok
    if tieto.get("LoadState") == "not-found":
        return HARMAA, "ei asennettu", edellinen_ok
    ok = edellinen_ok
    tila, tulos = tieto.get("ActiveState", ""), tieto.get("Result", "")
    lopetus = _aika(tieto.get("ExecMainExitTimestamp"))
    if tila == "inactive" and tulos == "success" and tieto.get("ExecMainStatus", "0") == "0" and lopetus:
        ok = max(ok or 0, lopetus)
    vari, syy = "vihrea", "ok"
    if tila == "failed" or (tila == "inactive" and tulos not in ("", "success")):
        vari, syy = "punainen", "epaonnistui (%s)" % (tulos or "tuntematon")
    elif tila in ("activating", "active", "reloading") and kesto_min:
        alku = _aika(tieto.get("ExecMainStartTimestamp"))
        if alku and (nyt - alku) / 60 > kesto_min:
            vari, syy = "keltainen", "kaynnissa yli %d min" % kesto_min
        else:
            syy = "kaynnissa"
    elif tila not in ("active", "activating", "inactive", "reloading"):
        vari, syy = "keltainen", "tila: %s" % (tila or "?")
    if vari != "punainen" and ajastimet and not any(a == "active" for a in ajastimet):
        vari, syy = "punainen", "ajastin ei ole kaytossa"
    if vari != "punainen" and vanhenee_h:
        if ok is None:
            vari, syy = _pahempi((vari, syy), ("keltainen", "ei tietoa onnistuneesta ajosta"))
        else:
            ika_h = (nyt - ok) / TUNTI
            if ika_h > 2 * vanhenee_h:
                vari, syy = _pahempi((vari, syy), ("punainen", "viimeisin onnistunut ajo %s sitten" % _ika_teksti(nyt - ok)))
            elif ika_h > vanhenee_h:
                vari, syy = _pahempi((vari, syy), ("keltainen", "viimeisin onnistunut ajo %s sitten" % _ika_teksti(nyt - ok)))
    return vari, syy, ok


def _pahempi(a, b):
    return a if JARJESTYS[a[0]] >= JARJESTYS[b[0]] else b


def _ika_teksti(sek):
    sek = max(0, int(sek))
    if sek < 3600:
        return "%d min" % (sek // 60)
    if sek < 48 * 3600:
        return "%.1f h" % (sek / 3600)
    return "%d pv" % (sek // 86400)


def palvelut_osio(edellinen_ok, nyt, show=systemctl_show):
    rivit, uusi_ok = [], dict(edellinen_ok)
    for nimi, yksikko, ajastimet, vanhenee_h, kesto_min in PALVELUT:
        tieto = show(yksikko)
        at = [show(a).get("ActiveState", "") for a in ajastimet]
        vari, syy, ok = arvioi_palvelu(tieto, at, vanhenee_h, kesto_min, edellinen_ok.get(yksikko), nyt)
        if ok:
            uusi_ok[yksikko] = ok
        rivit.append({"id": yksikko, "nimi": nimi, "vari": vari, "syy": syy,
                      "viimeisin_ok": time.strftime("%Y-%m-%d %H:%M", time.localtime(ok)) if ok else None})
    return {"vari": _huonoin([r["vari"] for r in rivit]), "rivit": rivit}, uusi_ok


# --- jonot ----------------------------------------------------------------

def _tulosaika(polku):
    """Tuloksen 'Aika:'-rivi (ISO) unix-sekunteina; muuten tiedoston mtime."""
    try:
        teksti = Path(polku).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^- Aika: (\S+)", teksti, re.M)
        if m:
            return int(datetime.datetime.fromisoformat(m.group(1)).timestamp())
    except (OSError, ValueError):
        pass
    try:
        return int(Path(polku).stat().st_mtime)
    except OSError:
        return None


def claudejono_osio(repo, nyt):
    teht = Path(repo) / "claude-jono" / "tehtavat"
    tul = Path(repo) / "claude-jono" / "tulokset"
    nimi_re = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-[a-z0-9-]{1,60}\.md$")
    if not teht.is_dir():
        return {"vari": "keltainen", "syy": "docs-repon klooni puuttuu", "odottavat": None, "tulos_ika_s": None}
    odottavat = sorted(p.name for p in teht.iterdir() if p.is_file() and nimi_re.match(p.name) and not (tul / p.name).exists())
    viimeisin = None
    if tul.is_dir():
        ajat = [a for a in (_tulosaika(p) for p in tul.iterdir() if p.is_file() and p.suffix == ".md") if a]
        viimeisin = max(ajat) if ajat else None
    ika = (nyt - viimeisin) if viimeisin else None
    vari, syy = "vihrea", "jono tyhja" if not odottavat else "%d odottaa" % len(odottavat)
    if odottavat:
        if ika is None:
            vari, syy = "keltainen", "%d odottaa, tuloksia ei viela" % len(odottavat)
        elif ika > JONO_PUNAINEN_H * TUNTI:
            vari, syy = "punainen", "%d odottaa, viimeisin tulos %s sitten" % (len(odottavat), _ika_teksti(ika))
        elif ika > JONO_KELTAINEN_H * TUNTI:
            vari, syy = "keltainen", "%d odottaa, viimeisin tulos %s sitten" % (len(odottavat), _ika_teksti(ika))
        else:
            syy += ", viimeisin tulos %s sitten" % _ika_teksti(ika)
    return {"vari": vari, "syy": syy, "odottavat": len(odottavat), "seuraavat": odottavat[:3], "tulos_ika_s": ika}


def yojono_osio(perus, pvm):
    tila = _json(Path(perus) / "tila.json", None)
    jono = _json(Path(perus) / "jono.json", {})
    if not isinstance(tila, dict):
        return {"vari": "keltainen", "syy": "tila.json ei luettavissa", "tehdyt": None, "epaonnistuneet": None, "ei_ajettu": None}
    teht = tila.get("tehtavat") or {}
    tanaan = {k: s for k, s in teht.items() if isinstance(s, dict) and s.get("pvm") == pvm}
    tehdyt = sorted(k for k, s in tanaan.items() if s.get("tila") == "valmis")
    huonot = sorted(k for k, s in tanaan.items() if s.get("tila") != "valmis")
    ei_ajettu = sorted(t.get("id") for t in (jono.get("tehtavat") or []) if t.get("id") not in teht)
    vari = "keltainen" if huonot else "vihrea"
    syy = "tanaan %d tehty, %d epaonnistunut, %d ei koskaan ajettu" % (len(tehdyt), len(huonot), len(ei_ajettu))
    return {"vari": vari, "syy": syy, "tehdyt": tehdyt, "epaonnistuneet": huonot, "ei_ajettu": ei_ajettu}


# --- kulut ----------------------------------------------------------------

def kulut_osio(perus, pvm):
    """Yksi lahde: tila.json 'kulut'. Katot luetaan jono.json:sta vain naytettavaksi. Huomautus, jos tehtavien
    summa eroaa kirjatusta paivakulusta (uudelleenajot voivat selittaa eron)."""
    tila = _json(Path(perus) / "tila.json", None)
    jono = _json(Path(perus) / "jono.json", {})
    if not isinstance(tila, dict) or not isinstance(tila.get("kulut"), dict):
        return {"vari": "keltainen", "syy": "kulutietoa ei saatu", "huomautukset": []}
    kulut = {k: v for k, v in tila["kulut"].items() if isinstance(v, (int, float))}
    paiva = float(kulut.get(pvm, 0.0))
    kk = round(sum(v for k, v in kulut.items() if k.startswith(pvm[:7])), 4)
    p_katto, k_katto = jono.get("paivakatto"), jono.get("kuukausikatto")
    vari, syyt = "vihrea", []
    for nimi, arvo, katto in (("paiva", paiva, p_katto), ("kuukausi", kk, k_katto)):
        if not isinstance(katto, (int, float)) or katto <= 0:
            vari = _pahempi((vari, ""), ("keltainen", ""))[0]
            syyt.append("%s: kattoa ei saatu" % nimi)
        elif arvo >= katto:
            vari = "punainen"
            syyt.append("%s: katto tayttynyt" % nimi)
        elif arvo >= KULU_KELTAINEN_OSUUS * katto:
            vari = _pahempi((vari, ""), ("keltainen", ""))[0]
            syyt.append("%s: yli %d %% katosta" % (nimi, KULU_KELTAINEN_OSUUS * 100))
    huom = []
    for p in sorted(kulut)[-3:]:
        summa = sum(s.get("kulu") or 0 for s in (tila.get("tehtavat") or {}).values()
                    if isinstance(s, dict) and s.get("pvm") == p and isinstance(s.get("kulu"), (int, float)))
        if abs(summa - kulut[p]) >= ERO_USD:
            huom.append("%s: kirjattu %.2f USD, tehtavien viimeisimmat ajot %.2f USD (lahteet eroavat)" % (p, kulut[p], summa))
    return {"vari": vari, "syy": "; ".join(syyt) or "katoissa", "paiva_usd": round(paiva, 4), "paivakatto": p_katto,
            "kuukausi_usd": kk, "kuukausikatto": k_katto, "huomautukset": huom}


# --- PR:t -----------------------------------------------------------------

def pr_osio(tila, nyt, hae=None, ohita=False):
    """Tonyn mergattavat PR:t aamupaketin logiikalla. Valimuisti tilatiedostossa (PR_VALIMUISTI_MIN)."""
    vanha = tila.get("pr") if isinstance(tila.get("pr"), dict) else None
    if vanha and (ohita or nyt - vanha.get("haettu", 0) < PR_VALIMUISTI_MIN * 60):
        return dict(vanha, valimuistista=True), vanha
    if ohita:
        return {"vari": HARMAA, "syy": "ohitettu", "rivit": [], "haettu": 0}, vanha
    try:
        ryhmat = aamupaketti.keraa(hae)
    except Exception as e:
        syy = "haku epaonnistui: %s" % type(e).__name__
        return dict(vanha or {"rivit": [], "haettu": 0}, vari="keltainen", syy=syy), vanha
    rivit = [{"repo": r["repo"], "numero": r["numero"], "otsikko": siivoa_rivi(r["otsikko"]), "url": r["url"],
              "riski": r.get("riski", ""), "ci": r.get("ci", "")} for r in ryhmat.get("tony", [])]
    uusi = {"vari": "vihrea", "syy": "%d odottaa Tonyn mergea" % len(rivit), "rivit": rivit, "haettu": nyt}
    return uusi, uusi


# --- Tonyn tehtavat -------------------------------------------------------

def tonylle_osio(polku):
    """Avoimet rivit tonylle.md:n viimeisimmista osioista (ratkaistut ja paatetyt jatetaan pois)."""
    try:
        teksti = Path(polku).read_text(encoding="utf-8")
    except OSError:
        return {"vari": "keltainen", "syy": "tonylle.md ei luettavissa", "rivit": []}
    osiot, nyk = [], None
    for rivi in teksti.splitlines():
        if rivi.startswith("## "):
            nyk = []
            osiot.append(nyk)
        elif nyk is not None and rivi.startswith("- "):
            nyk.append(rivi[2:].strip())
    avoimet = []
    for osio in osiot[-TONYLLE_OSIOITA:]:
        for r in osio:
            if not TONYLLE_ALUT.match(r) or re.search(r"(?i)\b(RATKAISTU|PAATETTY|P\u00c4\u00c4TETTY)\b", r[:40]):
                continue
            r = siivoa_rivi(r)
            avoimet.append(r if len(r) <= RIVI_MAX else r[:RIVI_MAX - 1] + "...")
    avoimet = avoimet[-TONYLLE_RIVEJA_MAX:]
    return {"vari": "vihrea", "syy": "%d avointa" % len(avoimet), "rivit": avoimet}


# --- kokoaminen ja tulostus -----------------------------------------------

def punaiset(data):
    """Joukko punaisia asioita, avaimena 'osio:id'. Telegram-viesti lahtee, kun joukkoon tulee uusi."""
    ulos = {}
    for r in data["palvelut"]["rivit"]:
        if r["vari"] == "punainen":
            ulos["palvelu:" + r["id"]] = "%s: %s" % (r["nimi"], r["syy"])
    for avain, otsikko, osa in (("claude_jono", "claude-jono jono", data["jonot"]["claude_jono"]), ("kulut", "kulut", data["kulut"])):
        if osa["vari"] == "punainen":
            ulos["osio:" + avain] = "%s: %s" % (otsikko, osa["syy"])
    return ulos


def keraa(env=None, nyt=None, show=systemctl_show, hae=None, ohita_pr=False, tila=None):
    """Palauttaa (data, uusi_tila). Kaikki lahteet injektoitavissa testeja varten."""
    env = os.environ if env is None else env
    nyt = int(time.time()) if nyt is None else nyt
    perus = env.get("YOJONO_PERUS", "/var/lib/yojono")
    repo = env.get("CLAUDEJONO_REPO", "/var/lib/claude-jono/docs")
    tonylle = env.get("TILASIVU_TONYLLE", str(Path(repo) / "claude-jono" / "tonylle.md"))
    pvm = config.paiva(datetime.datetime.fromtimestamp(nyt, datetime.timezone.utc))
    tila = tila if tila is not None else {}
    palvelut, uusi_ok = palvelut_osio(tila.get("viimeisin_ok") or {}, nyt, show)
    pr, pr_tila = pr_osio(tila, nyt, hae, ohita_pr)
    cj, yj = claudejono_osio(repo, nyt), yojono_osio(perus, pvm)
    kulut = kulut_osio(perus, pvm)
    data = {"generoitu": nyt, "generoitu_teksti": time.strftime("%Y-%m-%d %H:%M", time.localtime(nyt)),
            "palvelut": palvelut, "jonot": {"vari": _huonoin([cj["vari"], yj["vari"]]), "claude_jono": cj, "yojono": yj},
            "kulut": kulut, "prt": pr, "tonylle": tonylle_osio(tonylle)}
    data["yleinen"] = _huonoin([palvelut["vari"], data["jonot"]["vari"], kulut["vari"]])
    data["punaiset"] = punaiset(data)
    return data, {"viimeisin_ok": uusi_ok, "pr": pr_tila}


KUVAKE = {"vihrea": "OK", "keltainen": "HUOM", "punainen": "VIKA", "harmaa": "-"}


def muotoile_md(d):
    o = ["# Tila: %s (%s)" % (KUVAKE[d["yleinen"]], d["generoitu_teksti"]), "", "## Palvelut"]
    o += ["- [%s] %s: %s%s" % (KUVAKE[r["vari"]], r["nimi"], r["syy"], " (ok %s)" % r["viimeisin_ok"] if r["viimeisin_ok"] else "")
          for r in d["palvelut"]["rivit"]]
    cj, yj = d["jonot"]["claude_jono"], d["jonot"]["yojono"]
    o += ["", "## Jonot", "- [%s] claude-jono: %s" % (KUVAKE[cj["vari"]], cj["syy"]), "- [%s] yojono: %s" % (KUVAKE[yj["vari"]], yj["syy"])]
    k = d["kulut"]
    o += ["", "## Kulut", "- [%s] %s" % (KUVAKE[k["vari"]], k["syy"])]
    if "paiva_usd" in k:
        o.append("- paiva %.2f / %s USD, kuukausi %.2f / %s USD (lahde: tila.json)" % (k["paiva_usd"], k["paivakatto"], k["kuukausi_usd"], k["kuukausikatto"]))
    o += ["- huom: " + h for h in k["huomautukset"]]
    o += ["", "## Tonyn mergattavat PR:t (%s)" % d["prt"]["syy"]]
    o += ["- %s #%s %s [%s] %s" % (r["repo"], r["numero"], r["otsikko"], r.get("riski") or "?", r["url"]) for r in d["prt"]["rivit"]]
    o += ["", "## Tonyn tehtavat (%s)" % d["tonylle"]["syy"]] + ["- " + r for r in d["tonylle"]["rivit"]]
    return "\n".join(o) + "\n"


CSS = ("body{font:16px/1.4 system-ui,sans-serif;margin:0;padding:12px;background:#111;color:#eee}h1{font-size:1.3em}"
       "h2{font-size:1.05em;margin:1.2em 0 .3em;border-bottom:1px solid #444}ul{list-style:none;padding:0;margin:0}"
       "li{padding:6px 8px;margin:3px 0;border-left:6px solid #888;background:#1b1b1b}a{color:#8ab4ff}"
       ".vihrea{border-color:#2ea043}.keltainen{border-color:#d29922}.punainen{border-color:#f85149}.harmaa{border-color:#555;color:#999}"
       "#banner{padding:10px;font-weight:bold;color:#000}#banner.vihrea{background:#2ea043}#banner.keltainen{background:#d29922}"
       "#banner.punainen{background:#f85149}small{color:#aaa}")


def _li(vari, teksti, lisa=""):
    return '<li class="%s">%s%s</li>' % (vari, html.escape(teksti), lisa)


def muotoile_html(d):
    e = html.escape
    o = ["<!doctype html><html lang=fi><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         "<meta name=robots content=noindex><title>Agenttifirman tila</title><style>%s</style></head><body>" % CSS,
         '<div id=banner class="%s">Tila: %s</div>' % (d["yleinen"], KUVAKE[d["yleinen"]]),
         "<small id=pvm data-t=\"%d\">Paivitetty %s</small>" % (d["generoitu"], e(d["generoitu_teksti"])),
         "<h1>Palvelut</h1><ul>"]
    o += [_li(r["vari"], "%s: %s" % (r["nimi"], r["syy"]), "<br><small>%s</small>" % e("viimeisin onnistunut: %s" % r["viimeisin_ok"]) if r["viimeisin_ok"] else "")
          for r in d["palvelut"]["rivit"]]
    cj, yj = d["jonot"]["claude_jono"], d["jonot"]["yojono"]
    o += ["</ul><h2>Jonot</h2><ul>", _li(cj["vari"], "claude-jono: " + cj["syy"]), _li(yj["vari"], "yojono: " + yj["syy"]), "</ul><h2>Kulut</h2><ul>"]
    k = d["kulut"]
    o.append(_li(k["vari"], k["syy"]))
    if "paiva_usd" in k:
        o.append(_li("vihrea", "paiva %.2f / %s USD, kuukausi %.2f / %s USD" % (k["paiva_usd"], k["paivakatto"], k["kuukausi_usd"], k["kuukausikatto"])))
    o += [_li("keltainen", h) for h in k["huomautukset"]]
    o.append("</ul><h2>Tonyn mergattavat PR:t (%s)</h2><ul>" % e(d["prt"]["syy"]))
    o += [_li("vihrea", "%s #%s %s [%s]" % (r["repo"], r["numero"], r["otsikko"], r.get("riski") or "?"),
              ' <a href="%s">avaa</a>' % e(r["url"], quote=True)) for r in d["prt"]["rivit"]]
    o.append("</ul><h2>Tonyn tehtavat (%s)</h2><ul>" % e(d["tonylle"]["syy"]))
    o += [_li("keltainen", r) for r in d["tonylle"]["rivit"]]
    o.append("</ul><script>var t=document.getElementById('pvm'),a=(Date.now()/1000-t.dataset.t)/60;"
             "if(a>%d){var b=document.getElementById('banner');b.className='punainen';"
             "b.textContent='SIVU VANHENTUNUT ('+Math.round(a)+' min): generaattori seisoo'}</script></body></html>" % TUORE_RAJA_MIN)
    return "\n".join(o)


def kirjoita(kohde, data):
    kohde = Path(kohde)
    kohde.mkdir(parents=True, exist_ok=True)
    for nimi, sisalto in (("index.html", muotoile_html(data)), ("tila.md", muotoile_md(data)),
                          ("tila.json", json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True))):
        kirjoita_atomisesti(str(kohde / nimi), sisalto)


def halyta(data, tila, laheta=ilmoita.ilmoita):
    """Yksi Telegram-viesti, kun punaisten joukkoon tulee uusi asia. Palauttaa (tulos, uusi_punaisten_lista)."""
    nyt_punaiset = data["punaiset"]
    ennen = set(tila.get("punaiset_lahetetty") or [])
    if not nyt_punaiset:
        return "ei_punaista", []
    uudet = sorted(set(nyt_punaiset) - ennen)
    if not uudet:
        return "ei_muutosta", sorted(ennen & set(nyt_punaiset))
    teksti = "\n".join("- " + v for v in nyt_punaiset.values())
    tulos = laheta("virhe", "Tilasivu: %d punaista" % len(nyt_punaiset), teksti)
    if tulos == "lahetetty":
        return tulos, sorted(nyt_punaiset)
    return tulos, sorted(ennen & set(nyt_punaiset))   # ei kirjata: yritetaan uudelleen


def main(argv=None, env=None, show=systemctl_show, hae=None, laheta=ilmoita.ilmoita, nyt=None):
    a = sys.argv[1:] if argv is None else list(argv)
    env = os.environ if env is None else env
    liput = {x for x in a if x.startswith("--")}
    muut = [x for x in a if not x.startswith("--")]
    if liput - {"--ei-telegram", "--ei-pr"} or len(muut) > 1:
        print("kaytto: python3 -m agentti.tilasivu [--ei-telegram] [--ei-pr] [kohdehakemisto]", file=sys.stderr)
        return 2
    kohde = muut[0] if muut else env.get("TILASIVU_KOHDE", "/var/lib/tilasivu")
    tila_polku = Path(kohde) / "tilasivu-tila.json"
    tila = _json(tila_polku, {})
    tila = tila if isinstance(tila, dict) else {}
    data, uusi = keraa(env, nyt, show, hae, "--ei-pr" in liput, tila)
    tulos = "ei_lahetetty"
    if "--ei-telegram" not in liput:
        tulos, lahetetty = halyta(data, tila, laheta)
        uusi["punaiset_lahetetty"] = lahetetty
    else:
        uusi["punaiset_lahetetty"] = tila.get("punaiset_lahetetty") or []
    kirjoita(kohde, data)
    Path(kohde).mkdir(parents=True, exist_ok=True)
    kirjoita_atomisesti(str(tila_polku), json.dumps(uusi, sort_keys=True, indent=1))
    print("tilasivu: %s, punaisia %d, telegram: %s" % (data["yleinen"], len(data["punaiset"]), tulos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
