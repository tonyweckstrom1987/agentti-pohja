"""Yojonon ajuri: ajaa jonotiedoston tehtavat perakkain kulukaton sisalla ja kertoo tuloksen.

Vain standardikirjasto. Ei shellia (subprocess saa argumenttilistan), ei verkkoa paitsi valinnainen Telegram-yhteenveto.
Kaynnistys: `python3 -B -m yojono ajo|kuiva|tila` (repon juuresta). Ajuri ei tee muuta kuin kutsuu tehtavatyypin skriptia (nyt vain `katselmointi` = katselmoi.sh); tehtava on read-only.

Tiedostot (hakemisto YOJONO_PERUS, oletus /var/lib/yojono):
  jono.json  {"paivakatto": 3.0, "tehtavat": [{"id": "...", "tyyppi": "katselmointi", "repo": "...", "budjetti": 2.0}]}
  tila.json  ajuri kirjoittaa: tehtavien tila ja paivakohtaiset kulut (USD)
  lukko      estaa kaksi ajoa yhta aikaa
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from agentti import pr_kutsu
from agentti.pr_kutsu import REPOT as PR_REPOT

OLETUS_PERUS = "/var/lib/yojono"
OLETUS_KATSELMOI = "/opt/yojono/katselmoi.sh"
# Tehtavatyyppi -> (ymparistomuuttuja, oletuspolku) skriptille. Skripti kutsutaan `<skripti> <repo> <budjetti>` ja se tuottaa
# raportit hakemistoon <perus>/raportit/: katselmointi `<pvm>-<repo>.json|md`, muut `<pvm>-<tyyppi>-<repo>.json|md`.
SKRIPTIT = {
    "katselmointi": ("YOJONO_KATSELMOI", OLETUS_KATSELMOI),
    "ideointi": ("YOJONO_IDEOI", "/opt/yojono/ideoi.sh"),
    # kehitys: muuttaa koodia haarassa `yo/...` ja avaa PR:n (agentti/pr_kutsu.py); kutsu `<skripti> <repo> <budjetti> <aihe>`
    "kehitys": ("YOJONO_KEHITYS", "/opt/yojono/kehitys.sh"),
    # tarkkailu: vain luku. Agentti lukee tilannekuvan (GitHub, yojono, kulut, palvelin) ja kirjoittaa jarjestetyn ehdotusraportin;
    # `teema` = rooli (+ valinnainen tarkennus). Kutsu `<skripti> <repo> <budjetti>`.
    "tarkkailu": ("YOJONO_TARKKAILU", "/opt/yojono/tarkkailu.sh"),
}
TARKKAILU_ROOLIT = ("infra", "automaatio", "laatu", "kasvu")
KEHITYS_AIHEET = ("readme", "testit", "riippuvuudet", "ominaisuus")  # ominaisuus vaatii `teema`n (mita toteutetaan)
TEEMA_MAX = 300  # ideointi-tehtavan valinnainen `teema` (vapaa teksti kehotteeseen)
TOISTO_MAX_PV = 365
OLETUS_PAIVAKATTO = 3.0
MAX_YRITYKSIA = 2
TEHTAVA_AIKARAJA_S = 1800
ILTA_AIKARAJA_S = 3 * 3600
AJOPAIVAN_ALKU_H = 9   # ajopaiva (kulukatto, toisto) vaihtuu klo 09 Helsingin aikaa: yoikkuna 21-07 mahtuu yhteen ajopaivaan
TELEGRAM_MAX = 3900
ID_RE = re.compile(r"^[a-z0-9_-]{1,60}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
TYYPIT = tuple(SKRIPTIT)


class JonoVirhe(ValueError):
    pass


@dataclass
class Tulos:
    ok: bool
    kulu: float
    raportti: str
    viesti: str
    koodi: str = ""     # epaonnistuneen ajon vakaa virhekoodi (ks. VIRHEKOODIT); tyhja kun ok


def tarkista_jono(jono):
    """Palauttaa (paivakatto, tehtavat) tai nostaa JonoVirhe. Kaikki kentat tarkistetaan: jonotiedosto on syote."""
    if not isinstance(jono, dict):
        raise JonoVirhe("jono.json ei ole objekti")
    katto = jono.get("paivakatto", OLETUS_PAIVAKATTO)
    if isinstance(katto, bool) or not isinstance(katto, (int, float)) or not 0 < katto <= 50:
        raise JonoVirhe("paivakatto pitaa olla luku valilta 0-50 USD")
    tehtavat = jono.get("tehtavat")
    if not isinstance(tehtavat, list):
        raise JonoVirhe("tehtavat pitaa olla lista")
    nahty = set()
    for t in tehtavat:
        if not isinstance(t, dict):
            raise JonoVirhe("tehtava ei ole objekti")
        if not isinstance(t.get("id"), str) or not ID_RE.match(t["id"]):
            raise JonoVirhe(f"virheellinen id: {t.get('id')!r}")
        if t["id"] in nahty:
            raise JonoVirhe(f"id esiintyy kahdesti: {t['id']}")
        nahty.add(t["id"])
        if t.get("tyyppi") not in TYYPIT:
            raise JonoVirhe(f"tuntematon tyyppi ({t['id']}): {t.get('tyyppi')!r}")
        if not isinstance(t.get("repo"), str) or not REPO_RE.match(t["repo"]) or t["repo"] in (".", ".."):
            raise JonoVirhe(f"virheellinen repo ({t['id']}): {t.get('repo')!r}")
        if t["tyyppi"] == "kehitys":
            if t.get("aihe") not in KEHITYS_AIHEET:
                raise JonoVirhe(f"kehitys-tehtavan aihe ({t['id']}) pitaa olla jokin: {', '.join(KEHITYS_AIHEET)}")
            if t["repo"] not in PR_REPOT:
                raise JonoVirhe(f"kehitys-tehtavan repo ({t['id']}) ei ole PR-sallittujen listalla")
            if (t["aihe"] == "ominaisuus") != ("teema" in t):
                raise JonoVirhe(f"teema vaaditaan aiheelle ominaisuus ja kuuluu vain sille ({t['id']})")
        elif "aihe" in t:
            raise JonoVirhe(f"aihe kuuluu vain kehitys-tehtavalle ({t['id']})")
        if t["tyyppi"] == "tarkkailu":
            rooli = str(t.get("teema", "")).split(" ", 1)[0]
            if rooli not in TARKKAILU_ROOLIT:
                raise JonoVirhe(f"tarkkailu-tehtavan teema ({t['id']}) alkaa roolilla: {', '.join(TARKKAILU_ROOLIT)}")
        if "teema" in t:
            if t["tyyppi"] not in ("ideointi", "tarkkailu") and not (t["tyyppi"] == "kehitys" and t.get("aihe") == "ominaisuus"):
                raise JonoVirhe(f"teema kuuluu vain ideointi-, tarkkailu- ja kehitys/ominaisuus-tehtavalle ({t['id']})")
            teema = t["teema"]
            if not isinstance(teema, str) or not teema.strip() or len(teema) > TEEMA_MAX or any(c in teema for c in "\r\n\x00"):
                raise JonoVirhe(f"teema ({t['id']}) pitaa olla yksirivinen teksti, 1-{TEEMA_MAX} merkkia")
        if "malli" in t:
            if t["tyyppi"] not in ("kehitys", "tarkkailu"):
                raise JonoVirhe(f"malli kuuluu vain kehitys- ja tarkkailu-tehtavalle ({t['id']})")
            if not isinstance(t["malli"], str) or not MALLI_RE.match(t["malli"]):
                raise JonoVirhe(f"malli ({t['id']}) pitaa olla LiteLLM-alias, esim. sonnet tai deepseek-pro")
        toisto = t.get("toisto_pv")
        if toisto is not None and (isinstance(toisto, bool) or not isinstance(toisto, int) or not 1 <= toisto <= TOISTO_MAX_PV):
            raise JonoVirhe(f"toisto_pv ({t['id']}) pitaa olla kokonaisluku 1-{TOISTO_MAX_PV}")
        b = t.get("budjetti")
        if isinstance(b, bool) or not isinstance(b, (int, float)) or not 0 < b <= katto:
            raise JonoVirhe(f"budjetti ({t['id']}) pitaa olla 0 < budjetti <= paivakatto")
    return float(katto), tehtavat


def lue_json(polku, oletus):
    try:
        return json.loads(Path(polku).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return oletus


def kirjoita_tila(polku, tila):
    """Atominen kirjoitus: valiaikaistiedosto + replace, ettei katkennut ajo riko tilaa."""
    polku = Path(polku)
    valiaikainen = polku.with_suffix(".tmp")
    valiaikainen.write_text(json.dumps(tila, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(valiaikainen, polku)


def lue_kuukausikatto(jono, paivakatto):
    """Valinnainen `kuukausikatto` (USD): None jos puuttuu. Pitaa olla >= paivakatto ja <= 2000."""
    arvo = jono.get("kuukausikatto") if isinstance(jono, dict) else None
    if arvo is None:
        return None
    if isinstance(arvo, bool) or not isinstance(arvo, (int, float)) or not paivakatto <= arvo <= 2000:
        raise JonoVirhe("kuukausikatto pitaa olla luku valilta paivakatto-2000 USD")
    return float(arvo)


def ajopaiva(nyt=None):
    """Ajopaivan paivays (ISO). Vaihtuu klo AJOPAIVAN_ALKU_H Europe/Helsinki: ajot A 21:30, B 01:00 ja C 05:00 jakavat yhden paivakaton,
    eika kalenteripaivan vaihtuminen yolla nollaa tai jaa sita kahtia."""
    try:
        from zoneinfo import ZoneInfo
        nyt = nyt or datetime.now(ZoneInfo("Europe/Helsinki"))
    except Exception:  # ei tzdataa: palvelimen oma aika
        nyt = nyt or datetime.now()
    return (nyt - timedelta(hours=AJOPAIVAN_ALKU_H)).date().isoformat()


def kuukauden_kulu(tila, pvm):
    """Kuukauden (pvm:n vuosi-kuukausi) kulut yhteensa tilatiedoston paivakirjanpidosta."""
    kk = pvm[:7]
    return sum(v for k, v in tila.get("kulut", {}).items() if k.startswith(kk) and isinstance(v, (int, float)))


def _paivia_valissa(alku, loppu):
    """Kalenteripaivien maara (loppu - alku); virheellinen paivays -> 0 (ei toistoa arvauksella)."""
    try:
        return (date.fromisoformat(loppu) - date.fromisoformat(alku)).days
    except (TypeError, ValueError):
        return 0


def budjetti_uusinta_turha(tehtava, edellinen):
    """Budjettiin kaatunut ajo kaatuisi samalla budjetilla todennakoisesti uudelleen (30.10. katselmointi-agentti-pohja 2,60/2,50 USD,
    diagnoosi 1.11.: 9 budjettiin kaatunutta ajoa, 11,64 USD ilman tulosta). Seuraavan paivan uusinta ohitetaan, kunnes Tony nostaa
    tehtavan budjetin yli edellisen ajon kulun. Toistuva tehtava ajetaan silti taas, kun toisto_pv on kulunut (yksi yritys per jakso)."""
    if edellinen.get("koodi") != "budjetti_loppui":
        return False
    try:
        return float(tehtava["budjetti"]) <= float(edellinen.get("kulu", 0))
    except (KeyError, TypeError, ValueError):
        return False


def valitse_ajettavat(tehtavat, tila, pvm):
    """Valmiit ohitetaan, paitsi toistuva tehtava (`toisto_pv`), joka ajetaan taas kun edellisesta valmiista ajosta on
    vahintaan toisto_pv paivaa. Epaonnistunut yritetaan uudelleen vain myohempana paivana ja enintaan MAX_YRITYKSIA kertaa,
    budjettiin kaatunut vain, jos budjettia on nostettu (budjetti_uusinta_turha)."""
    ulos = []
    for t in tehtavat:
        s = tila.get("tehtavat", {}).get(t["id"])
        if s is None:
            ulos.append(t)
        elif s.get("tila") == "valmis":
            if t.get("toisto_pv") and _paivia_valissa(s.get("pvm"), pvm) >= t["toisto_pv"]:
                ulos.append(t)
            continue
        elif s.get("yrityksia", 0) < MAX_YRITYKSIA and s.get("pvm") != pvm and not budjetti_uusinta_turha(t, s):
            ulos.append(t)
        elif t.get("toisto_pv") and _paivia_valissa(s.get("pvm"), pvm) >= t["toisto_pv"]:
            ulos.append(t)   # toistuva tehtava ei jumiudu: yritykset loppuneena yritetaan taas, kun toisto_pv on kulunut (laskuri nollataan ajossa)
    return ulos


RAPORTTI_MIN_KOKO = 200
MALLI_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")


def syy_viestiksi(md_polku, loppu):
    """Epaonnistuneen ajon viestiksi raportin `- Syy:`-rivi (jos on) ja ilman claude-tyokalun yleista `unrecognized_model`-kohinaa."""
    syy = ""
    if md_polku:
        try:
            syy = next((r[len("- Syy:"):].strip() for r in Path(md_polku).read_text(encoding="utf-8").splitlines() if r.startswith("- Syy:")), "")
        except (OSError, ValueError):
            syy = ""
    teksti = syy or re.sub(r"\[claude-code:unrecognized_model\]", "", loppu or "").strip()
    return teksti[:300]


# Vakaat virhekoodit (tila.json, Telegram-yhteenveto, tapahtumaloki `virhekoodi`, viikkoraportti). Vanhat lokirivit ilman koodia nakyvat koodina "tuntematon".
VIRHEKOODIT = {
    "aikakatkaisu": "tehtava ylitti aikarajan",
    "malli_virhe": "claude/malli palautti virheen tai ei tunnistanut mallia",
    "budjetti_loppui": "claude-kutsun budjetti loppui ennen raporttia",
    "raporttia_ei_syntynyt": "skripti ei tuottanut raporttia (md puuttuu tai ei paivittynyt)",
    "raportti_tyhja": "raportti syntyi, mutta on tyhja tai liian lyhyt",
    "skripti_ei_kaynnisty": "tehtavaskriptia ei voitu kaynnistaa",
    "pr_kutsu_epaonnistui": "PR:n avaus epaonnistui (muu kuin fork_*)",
    "testit_punaiset": "kehitys: testit punaisella agentin muutosten jalkeen",
    "fork_jaljessa": "fork jai jaljelle ylavirrasta synkkauksen jalkeen",
    "fork_ei_ff": "fork-main eroaa ylavirrasta (ei fast-forward): ei tehty mitaan",
    "fork_push_epaonnistui": "forkin synkkauksen push epaonnistui",
    "fork_workflow_oikeus": "forkin synkkaus vaatisi workflow-oikeuden, jota botin tunnuksella ei ole",
    "fork_tarkistus_epaonnistui": "forkin tilaa ei saatu luettua",
    "tuntematon": "syy ei selvia",
}
FORK_KOODI_RE = re.compile(r"\b(fork_[a-z_]+)\b")
SUBTYPE_RE = re.compile(r"^[a-z_]{1,40}$")


def luokittele_virhe(loppu, paivittyi, md_on, meta):
    """Epaonnistuneen ajon vakaa virhekoodi. Jarjestys: claude-tulos (budjetti, malli) -> tekstista (fork_*, pr_kutsu, testit, tuntematon malli) ->
    raportin puuttuminen -> tuntematon. `meta` = claude-JSON:n subtype/is_error/terminal_reason (tyhja, jos JSON ei paivittynyt)."""
    subtype = meta.get("subtype")
    if subtype == "error_max_budget_usd" or meta.get("terminal_reason") == "budget_exhausted":
        return "budjetti_loppui"
    if meta.get("is_error") is True or (subtype is not None and subtype != "success"):
        return "malli_virhe"
    loppu = loppu or ""
    m = FORK_KOODI_RE.search(loppu)
    if m and m.group(1) in VIRHEKOODIT:
        return m.group(1)
    if "pr_kutsu epaonnistui" in loppu:
        return "pr_kutsu_epaonnistui"
    if "testit punaisella" in loppu:
        return "testit_punaiset"
    if "unrecognized_model" in loppu:
        return "malli_virhe"
    if not paivittyi or not md_on:
        return "raporttia_ei_syntynyt"
    return "tuntematon"


def suorita_katselmointi(tehtava, pvm, perus, skripti, aikaraja=TEHTAVA_AIKARAJA_S):
    """Kutsuu tyypin skriptia <repo> <budjetti> (katselmointi: katselmoi.sh; ideointi: ideoi.sh). Kulu luetaan raportin JSON:sta (myos epaonnistuneesta ajosta).
    Jos ajo alkoi mutta kulua ei saada selville (aikakatkaisu, lukukelvoton JSON), laskutetaan koko budjetti: katto ei saa alittua arvauksella."""
    repo, budjetti = tehtava["repo"], float(tehtava["budjetti"])
    tyyppi = tehtava.get("tyyppi", "katselmointi")
    aihe = tehtava.get("aihe") if tyyppi == "kehitys" else None
    teema = tehtava.get("teema") if tyyppi in ("ideointi", "kehitys", "tarkkailu") else None
    if teema:  # teemallinen ideointi saa oman raportin (tehtavan id nimessa), jotta samaan repoon voi tehda useita ideointeja samana paivana
        nimi = f"{pvm}-{tyyppi}-{tehtava['id']}-{repo}"
    else:
        nimi = f"{pvm}-{repo}" if tyyppi == "katselmointi" else (f"{pvm}-{tyyppi}-{aihe}-{repo}" if aihe else f"{pvm}-{tyyppi}-{repo}")
    json_polku = Path(perus) / "raportit" / f"{nimi}.json"
    md_polku = json_polku.with_suffix(".md")
    vanha_mtime = json_polku.stat().st_mtime if json_polku.exists() else None
    try:
        # Telegram-tunnukset eivat kuulu alaprosessille (katselmoija ei tarvitse niita)
        ymparisto = {k: v for k, v in os.environ.items() if not k.startswith("YOJONO_TELEGRAM_")}
        if tehtava.get("malli"):  # mallivertailu: tehtavakohtainen malli ohittaa oletuksen (LiteLLM-alias)
            ymparisto["KEHITYS_MALLI" if tyyppi == "kehitys" else "TARKKAILU_MALLI"] = tehtava["malli"]
        if teema:  # skripti lukee teeman ymparistosta ja kirjoittaa raportin nimella $YOJONO_RAPORTTI.json|md
            ymparisto["YOJONO_TEEMA"] = teema
            ymparisto["YOJONO_RAPORTTI"] = nimi
            ymparisto["YOJONO_TUNNUS"] = tehtava["id"]
        r = subprocess.run([str(skripti), repo, f"{budjetti:.2f}"] + ([aihe] if aihe else []), capture_output=True, text=True, timeout=aikaraja,
                           check=False, env=ymparisto)
        koodi, loppu = r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except subprocess.TimeoutExpired:
        return Tulos(False, budjetti, "", f"aikakatkaisu: ei valmis {aikaraja} s jalkeen (kulu arvioitu budjetiksi)", "aikakatkaisu")
    except OSError as virhe:
        return Tulos(False, 0.0, "", f"skripti_ei_kaynnisty: ajuria ei voitu kaynnistaa: {virhe}", "skripti_ei_kaynnisty")
    paivittyi = json_polku.exists() and json_polku.stat().st_mtime != vanha_mtime
    meta = {}
    if not paivittyi:
        kulu = 0.0  # claude ei ehtinyt alkaa (esim. kloonaus epaonnistui): katselmoi.sh luo JSON:n vasta claude-kutsussa
    else:
        kulu = None
        try:
            data = json.loads(json_polku.read_text(encoding="utf-8"))
            raaka = data.get("total_cost_usd")
            if isinstance(raaka, (int, float)) and not isinstance(raaka, bool) and raaka >= 0:
                kulu = float(raaka)
            meta = {k: data.get(k) for k in ("subtype", "is_error", "terminal_reason")}
        except (ValueError, OSError, AttributeError):
            pass
        if kulu is None:
            kulu = budjetti  # ajo alkoi mutta kulua ei saada: katto ei saa alittua arvauksella
    ok = koodi == 0 and md_polku.exists() and paivittyi
    tyhja = ok and tyyppi != "kehitys" and md_polku.stat().st_size < RAPORTTI_MIN_KOKO
    if ok and not tyhja:
        return Tulos(True, kulu, str(md_polku), "ok")
    if tyhja:  # tyhja raportti ei ole tulos: ei merkita valmiiksi, yritys kuluu (kulu on jo syntynyt)
        syy, virhekoodi = f"raportti tyhja tai liian lyhyt (<{RAPORTTI_MIN_KOKO} tavua)", "raportti_tyhja"
        juuri = luokittele_virhe("", True, True, meta)      # claude-tulos kertoo todellisen syyn (budjetti, malli) tyhjan raportin takana
        if juuri in ("budjetti_loppui", "malli_virhe"):
            virhekoodi = juuri
    else:
        syy = syy_viestiksi(md_polku if paivittyi else None, loppu)
        virhekoodi = luokittele_virhe(f"{syy} {loppu}", paivittyi, md_polku.exists(), meta)
        if not syy:
            syy = f"poistumiskoodi {koodi}"
            if virhekoodi == "raporttia_ei_syntynyt":
                syy = f"skripti ei tuottanut raporttia (poistumiskoodi {koodi})"
    if virhekoodi == "budjetti_loppui":
        syy = f"tehtavan budjetti {budjetti:.2f} USD loppui ennen raporttia (kulu {kulu:.2f} USD)"
    elif virhekoodi == "malli_virhe" and (meta.get("is_error") is True or meta.get("subtype") not in (None, "success")):
        subtype = meta.get("subtype")
        syy = "claude palautti virheen (subtype: %s)" % (subtype if isinstance(subtype, str) and SUBTYPE_RE.match(subtype) else "?")
    return Tulos(False, kulu, "", syy if virhekoodi in syy else f"{virhekoodi}: {syy}", virhekoodi)


def raportin_lisatiedot(tyyppi, md_polku):
    """Yhteenvedon lisarivi raportista (vain luku, virhe ei kaada): kehitys -> PR-linkki tai syy miksi PR:aa ei avattu;
    tarkkailu -> enintaan 3 PARAS-merkittya ehdotusrivia."""
    try:
        rivit = Path(md_polku).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return ""
    if tyyppi == "kehitys":
        pr = next((r[len("- PR:"):].strip() for r in rivit if r.startswith("- PR:")), "")
        syy = next((r[len("- Syy:"):].strip() for r in rivit if r.startswith("- Syy:")), "")
        if pr.startswith("http"):
            return f" PR {pr}"
        return " EI PR:AA" + (f" ({syy[:120]})" if syy else "")
    if tyyppi == "tarkkailu":
        parhaat = [re.sub(r"^\d+[.)]\s*", "", r.strip().lstrip("-*# "))[:160] for r in rivit if "PARAS" in r][:3]
        return "\n    " + "\n    ".join(parhaat) if parhaat else ""
    return ""


def laheta_telegram(teksti, env=None):
    """Lahettaa yhteenvedon, jos YOJONO_TELEGRAM_TOKEN ja YOJONO_TELEGRAM_CHAT on asetettu. Virhe ei kaada ajoa."""
    env = os.environ if env is None else env
    token, chat = env.get("YOJONO_TELEGRAM_TOKEN"), env.get("YOJONO_TELEGRAM_CHAT")
    if not token or not chat:
        return False
    data = json.dumps({"chat_id": chat, "text": teksti[:TELEGRAM_MAX]}).encode()
    pyynto = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(pyynto, timeout=20) as vastaus:
            return vastaus.status == 200
    except Exception as virhe:  # verkkovirhe ei saa kaataa yoajoa; tokenia ei kirjata
        print(f"Telegram-lahetys epaonnistui: {type(virhe).__name__}", file=sys.stderr)
        return False


HALYTYS_EPAONNISTUNUTTA = 3
HALYTYS_OHITETTUA = 3


def halytys(tila, ajetut_idt, ohitukset=None):
    """Rivi yhteenvedon alkuun, jos taman ajon tehtavista epaonnistui vahintaan HALYTYS_EPAONNISTUNUTTA, jokin on kayttanut kaikki
    yrityksensa (ei enaa yritetaan) tai vahintaan HALYTYS_OHITETTUA tehtavaa ohitettiin (`ohitukset` = [(id, koodi)], nyt forkin synkkaus).
    Tyhja merkkijono jos kaikki hyvin."""
    tehtavat = tila.get("tehtavat", {})
    kaatuneet = [i for i in ajetut_idt if tehtavat.get(i, {}).get("tila") == "epaonnistui"]
    loppuun = [i for i in kaatuneet if tehtavat[i].get("yrityksia", 0) >= MAX_YRITYKSIA]
    osat = []
    if len(kaatuneet) >= HALYTYS_EPAONNISTUNUTTA or loppuun:
        osat.append(f"{len(kaatuneet)} epaonnistui")
        if loppuun:
            osat.append("yritykset loppu: " + ", ".join(loppuun))
    ohitukset = ohitukset or []
    if len(ohitukset) >= HALYTYS_OHITETTUA:
        koodit = {}
        for _, koodi in ohitukset:
            koodit[koodi] = koodit.get(koodi, 0) + 1
        osat.append(f"{len(ohitukset)} ohitettu (" + ", ".join(f"{k} x{n}" for k, n in sorted(koodit.items())) + ")")
    return "HALYTYS: " + "; ".join(osat) if osat else ""


def yhteenveto(rivit, kulu_tanaan, katto, pvm, halytysrivi=""):
    otsikko = f"Yojono {pvm}: kulu tanaan {kulu_tanaan:.2f} / {katto:.2f} USD"
    return "\n".join(([halytysrivi] if halytysrivi else []) + [otsikko] + (rivit or ["Ei ajettavia tehtavia."]))


PR_RIVI = re.compile(r"^- PR: \S*/pull/(\d+)\s*$", re.M)


def pr_numero(raportti):
    """PR-numero kehitys-raportin `- PR: <url>/pull/N` -riviltaan; None jos raporttia tai PR:aa ei ole. Vain luku, ei kaada."""
    if not raportti:
        return None
    try:
        with open(raportti, encoding="utf-8") as f:
            m = PR_RIVI.search(f.read(20000))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def _kirjaa_tapahtuma(t, tulos_sana, kulu, tiedot, polku=None):
    """Yhteinen kirjaus tapahtumalokiin (YOJONO_LOKI). Virhe ei koskaan kaada ajoa."""
    polku = polku or os.environ.get("YOJONO_LOKI")
    if not polku:
        return False
    try:
        from agentti import tapahtumaloki
        yht = tapahtumaloki.avaa(polku)
        try:
            tapahtumaloki.kirjaa(yht, "yojono", "yojono", t.get("tyyppi", "katselmointi"), tulos_sana, kulu_usd=round(kulu, 4), tiedot=tiedot)
        finally:
            yht.close()
        return True
    except Exception as virhe:  # loki on lisa; ajo jatkuu aina
        print(f"Lokikirjaus epaonnistui: {type(virhe).__name__}", file=sys.stderr)
        return False


def kirjaa_lokiin(t, tulos, pvm, polku=None, yrityksia=None):
    """Valinnainen tapahtumaloki (YOJONO_LOKI = tietokannan polku). Kirjaa tehtavan id:n, repon, mallin (jos tehtavalla oma),
    tuloksen, kulun, yritykset, PR-numeron ja epaonnistuneelle ajolle `virhekoodi`n (ks. VIRHEKOODIT; viikkoraportti ryhmittelee sen mukaan).
    Ei viestitekstia eika muuta vapaata tekstia. Virhe ei koskaan kaada ajoa."""
    tiedot = {"tehtava": t["id"], "repo": t["repo"], "pvm": pvm, "malli": t.get("malli") or "oletus", "yrityksia": yrityksia}
    if not tulos.ok:
        tiedot["virhekoodi"] = tulos.koodi if tulos.koodi in VIRHEKOODIT else "tuntematon"
    try:
        pr = pr_numero(tulos.raportti) if t.get("tyyppi") == "kehitys" else None
    except Exception:
        pr = None
    if pr is not None:
        tiedot["pr"] = pr
    return _kirjaa_tapahtuma(t, "ok" if tulos.ok else "virhe", tulos.kulu, tiedot, polku)


def kirjaa_ohitus(t, koodi, pvm, polku=None):
    """Ohitettu tehtava (ei ajettu, ei kulua, ei yritysta; nyt vain forkin synkkaus): tapahtuma tuloksella `ohitettu` ja `virhekoodi`, jotta
    viikkoraportti nayttaa ohitukset syineen eika vain epaonnistumisia."""
    tiedot = {"tehtava": t["id"], "repo": t["repo"], "pvm": pvm, "malli": t.get("malli") or "oletus", "virhekoodi": koodi if koodi in VIRHEKOODIT else "tuntematon"}
    return _kirjaa_tapahtuma(t, "ohitettu", 0.0, tiedot, polku)


# Koodi on laki (Tonyn paatos 30.10.2026): claude --max-budget-usd pysayttaa vasta budjetin ylittaneen vuoron jalkeen, joten
# todellinen kulu voi ylittaa tehtavan budjetin (28.10.: 1,86 / 1,50 USD -> paivakulu 5,32 / 5,00). Ennen tehtavaa varataan siksi
# budjetti x VARAUSKERROIN. Kerroin vain tiukentaa kattoa; sen pienentaminen alle 1,3:n vaatii Tonyn luvan.
VARAUSKERROIN = 1.3


def varattava(budjetti):
    """Paiva- ja kuukausikatosta ennen tehtavaa varattava summa (USD)."""
    return round(budjetti * VARAUSKERROIN, 4)


def skripti_tyypille(skripti, tyyppi):
    """`skripti` on joko polku (vanha: vain katselmointi) tai sanakirja tyyppi -> polku."""
    if isinstance(skripti, dict):
        return skripti.get(tyyppi, "")
    return skripti if tyyppi == "katselmointi" else ""


def aja(perus, skripti, laheta=print, suorita=suorita_katselmointi, nyt=time.monotonic, pvm=None, kuiva=False,
        telegram=laheta_telegram, fork_tarkista=None):
    """Ajaa jonon. Palauttaa tekstiyhteenvedon. Paivakatto: ennen tehtavaa varataan budjetti x VARAUSKERROIN."""
    pvm = pvm or ajopaiva()
    perus = Path(perus)
    perus.mkdir(parents=True, exist_ok=True)
    with open(perus / "lukko", "w") as lukko:
        try:
            fcntl.flock(lukko, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "Toinen ajo on kaynnissa; ei tehty mitaan."
        jono_data = lue_json(perus / "jono.json", {"tehtavat": []})
        katto, tehtavat = tarkista_jono(jono_data)
        kuukausikatto = lue_kuukausikatto(jono_data, katto)
        tila_polku = perus / "tila.json"
        tila = lue_json(tila_polku, {})
        tila.setdefault("tehtavat", {})
        tila.setdefault("kulut", {})
        ajettavat = valitse_ajettavat(tehtavat, tila, pvm)
        if kuiva:
            rivit = [f"- {t['id']}{'' if t['tyyppi'] == 'katselmointi' else ' [' + t['tyyppi'] + ('/' + t['aihe'] if t.get('aihe') else '') + ']'}: {t['repo']} "
                     f"(budjetti {t['budjetti']:.2f})" for t in ajettavat]
            return yhteenveto(rivit or ["Ei ajettavia tehtavia."], tila["kulut"].get(pvm, 0.0), katto, pvm)
        alku = nyt()
        rivit = []
        ajetut = []
        ohitukset = []       # [(tehtava-id, virhekoodi)]: forkin takia ohitetut (ei kulua, ei yritysta)
        fork_tulokset = {}   # repo -> (ok, koodi, selitys): synkkaus tehdaan kerran per repo ja ajo
        for t in ajettavat:
            kulu_nyt = tila["kulut"].get(pvm, 0.0)
            if nyt() - alku > ILTA_AIKARAJA_S:
                rivit.append(f"- {t['id']}: ohitettu (yon aikaraja)")
                continue
            varaus = varattava(t["budjetti"])
            if kuukausikatto is not None and kuukausikatto - kuukauden_kulu(tila, pvm) < varaus:
                rivit.append(f"- {t['id']}: ohitettu (kuukausikatto: jaljella "
                             f"{max(kuukausikatto - kuukauden_kulu(tila, pvm), 0):.2f} < varaus {varaus:.2f})")
                continue
            if katto - kulu_nyt < varaus:
                rivit.append(f"- {t['id']}: ohitettu (paivakatto: jaljella {katto - kulu_nyt:.2f} < varaus {varaus:.2f})")
                continue
            if t["tyyppi"] == "kehitys" and pr_kutsu.paikkoja_jaljella() <= 0:
                # Ei kalliin mallikutsun jalkeen hukkaan menevaa tyota: PR-paivaraja on taynna, yritys ei kulu
                rivit.append(f"- {t['id']}: ohitettu (PR-paivaraja taynna, ajetaan seuraavana paivana)")
                continue
            if t["tyyppi"] == "kehitys" and fork_tarkista is not None:
                if t["repo"] not in fork_tulokset:
                    fork_tulokset[t["repo"]] = fork_tarkista(t["repo"])
                fork_ok, fork_koodi, selitys = fork_tulokset[t["repo"]]
                if not fork_ok:  # ei kulua eika yritysta: mallikutsu menisi hukkaan, kun PR ei voi avautua
                    rivit.append(f"- {t['id']}: ohitettu ({fork_koodi}: {selitys})")
                    ohitukset.append((t["id"], fork_koodi))
                    kirjaa_ohitus(t, fork_koodi, pvm)
                    continue
            tulos = suorita(t, pvm, perus, skripti_tyypille(skripti, t["tyyppi"]))
            ajetut.append(t["id"])
            tila["kulut"][pvm] = round(kulu_nyt + tulos.kulu, 4)
            vanha = tila["tehtavat"].get(t["id"], {})
            if t.get("toisto_pv") and vanha.get("tila") == "epaonnistui" and vanha.get("yrityksia", 0) >= MAX_YRITYKSIA:
                vanha = {**vanha, "yrityksia": 0}   # uusi jakso: edellisen jakson loppuneet yritykset eivat esta
            tila["tehtavat"][t["id"]] = {
                "tila": "valmis" if tulos.ok else "epaonnistui",
                "yrityksia": vanha.get("yrityksia", 0) + (0 if tulos.ok else 1),
                "pvm": pvm, "kulu": round(tulos.kulu, 4), "raportti": tulos.raportti, "viesti": tulos.viesti,
                **({} if tulos.ok else {"koodi": tulos.koodi or "tuntematon"}),
            }
            kirjoita_tila(tila_polku, tila)  # heti: katkennut ajo ei hukkaa kulukirjanpitoa
            kirjaa_lokiin(t, tulos, pvm, yrityksia=tila["tehtavat"][t["id"]]["yrityksia"])
            rivit.append(f"- {t['id']}: {'VALMIS' if tulos.ok else 'EPAONNISTUI'} ({tulos.kulu:.2f} USD)"
                         + (f" raportti {tulos.raportti}" if tulos.ok else f" syy: {tulos.viesti}")
                         + (raportin_lisatiedot(t["tyyppi"], tulos.raportti) if tulos.ok else ""))
        kirjoita_tila(tila_polku, tila)
        teksti = yhteenveto(rivit, tila["kulut"].get(pvm, 0.0), katto, pvm, halytys(tila, ajetut, ohitukset))
    laheta(teksti)
    if ajettavat:
        telegram(teksti)
    return teksti


def peilaa(skripti):
    """Valinnainen raporttipeilaus ajon jalkeen (YOJONO_PEILAA = peilaa.sh:n polku). Virhe ei koskaan kaada ajoa."""
    if not skripti:
        return False
    try:
        r = subprocess.run([str(skripti)], capture_output=True, text=True, timeout=300, check=False)
        if r.returncode != 0:
            print(f"Peilaus epaonnistui (koodi {r.returncode}): {(r.stderr or r.stdout).strip()[-200:]}", file=sys.stderr)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as virhe:
        print(f"Peilaus epaonnistui: {type(virhe).__name__}", file=sys.stderr)
        return False


AAMUPAKETTI_TUNNIT = (5, 6, 7, 8)   # Helsingin kello: C-ajo alkaa 05:00 ja kestaa enintaan ILTA_AIKARAJA_S (3 h) eli voi paattya vasta 08:xx; ajopaiva vaihtuu 09:00; B (01:00) paattyy ennen 05:00


def laheta_aamupaketti(perus, nyt=None, laheta=None):
    """C-ajon jalkeen kerran per ajopaiva: avoimet PR:t ryhmiteltyina Telegramiin (agentti.aamupaketti --laheta), jotta Tony nakee aamulla
    mita mergataan. Virhe ei koskaan kaada ajoa. Palauttaa True vain jos paketti lahetettiin."""
    try:
        try:
            from zoneinfo import ZoneInfo
            nyt = nyt or datetime.now(ZoneInfo("Europe/Helsinki"))
        except Exception:
            nyt = nyt or datetime.now()
        if nyt.hour not in AAMUPAKETTI_TUNNIT:
            return False
        merkki = Path(perus) / "aamupaketti.pvm"
        pvm = ajopaiva(nyt)
        if merkki.exists() and merkki.read_text().strip() == pvm:
            return False
        if laheta is None:
            from agentti import aamupaketti
            laheta = aamupaketti.main
        if laheta(["--laheta"]) != 0:
            return False
        merkki.write_text(pvm + "\n")
        return True
    except Exception as virhe:  # aamupaketti on lisa, ei saa kaataa yoajoa
        print(f"Aamupaketti epaonnistui: {type(virhe).__name__}", file=sys.stderr)
        return False


OPIT_IKA_S = 20 * 3600   # opit.md paivitetaan enintaan kerran ~vuorokaudessa (tiedoston ika)


def paivita_opit(perus, nyt=None, kirjoita=None):
    """Oppiva silmukka: paivittaa <perus>/opit.md:n (suljetut yojono-PR:t ja Tonyn kommentit), jos se puuttuu tai on yli OPIT_IKA_S vanha.
    kehitys.sh liittaa sen kehotteeseen datana. Virhe ei koskaan kaada ajoa. Palauttaa True vain jos tiedosto paivitettiin."""
    try:
        polku = Path(perus) / "opit.md"
        nyt = time.time() if nyt is None else nyt
        if polku.exists() and nyt - polku.stat().st_mtime < OPIT_IKA_S:
            return False
        if kirjoita is None:
            from agentti import pr_opit
            kirjoita = pr_opit.main
        return kirjoita(["--kirjoita", str(polku)]) == 0
    except Exception as virhe:  # opit ovat lisa, ei saa kaataa yoajoa
        print(f"Opit-paivitys epaonnistui: {type(virhe).__name__}", file=sys.stderr)
        return False


def main(argv=None):
    p = argparse.ArgumentParser(description="Yojonon ajuri")
    p.add_argument("komento", choices=["ajo", "kuiva", "tila"])
    a = p.parse_args(argv)
    perus = os.environ.get("YOJONO_PERUS", OLETUS_PERUS)
    skripti = {tyyppi: os.environ.get(muuttuja, oletus) for tyyppi, (muuttuja, oletus) in SKRIPTIT.items()}
    if a.komento == "tila":
        print(json.dumps(lue_json(Path(perus) / "tila.json", {}), indent=2, ensure_ascii=False))
        return 0
    if a.komento == "ajo":
        paivita_opit(perus)
    try:
        tulos = aja(perus, skripti, kuiva=a.komento == "kuiva", fork_tarkista=pr_kutsu.fork_valmis)
        if a.komento == "kuiva":
            print(tulos)
        elif a.komento == "ajo":
            peilaa(os.environ.get("YOJONO_PEILAA"))
            laheta_aamupaketti(perus)
    except JonoVirhe as virhe:
        print(f"jono.json virheellinen: {virhe}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
