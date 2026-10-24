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
from datetime import date
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


def valitse_ajettavat(tehtavat, tila, pvm):
    """Valmiit ohitetaan, paitsi toistuva tehtava (`toisto_pv`), joka ajetaan taas kun edellisesta valmiista ajosta on
    vahintaan toisto_pv paivaa. Epaonnistunut yritetaan uudelleen vain myohempana paivana ja enintaan MAX_YRITYKSIA kertaa."""
    ulos = []
    for t in tehtavat:
        s = tila.get("tehtavat", {}).get(t["id"])
        if s is None:
            ulos.append(t)
        elif s.get("tila") == "valmis":
            if t.get("toisto_pv") and _paivia_valissa(s.get("pvm"), pvm) >= t["toisto_pv"]:
                ulos.append(t)
            continue
        elif s.get("yrityksia", 0) < MAX_YRITYKSIA and s.get("pvm") != pvm:
            ulos.append(t)
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
        return Tulos(False, budjetti, "", f"aikakatkaisu {aikaraja} s jalkeen (kulu arvioitu budjetiksi)")
    except OSError as virhe:
        return Tulos(False, 0.0, "", f"ajuria ei voitu kaynnistaa: {virhe}")
    paivittyi = json_polku.exists() and json_polku.stat().st_mtime != vanha_mtime
    if not paivittyi:
        kulu = 0.0  # claude ei ehtinyt alkaa (esim. kloonaus epaonnistui): katselmoi.sh luo JSON:n vasta claude-kutsussa
    else:
        kulu = None
        try:
            raaka = json.loads(json_polku.read_text(encoding="utf-8")).get("total_cost_usd")
            if isinstance(raaka, (int, float)) and not isinstance(raaka, bool) and raaka >= 0:
                kulu = float(raaka)
        except (ValueError, OSError):
            pass
        if kulu is None:
            kulu = budjetti  # ajo alkoi mutta kulua ei saada: katto ei saa alittua arvauksella
    ok = koodi == 0 and md_polku.exists() and paivittyi
    tyhja = ok and tyyppi != "kehitys" and md_polku.stat().st_size < RAPORTTI_MIN_KOKO
    if tyhja:  # tyhja raportti ei ole tulos: ei merkita valmiiksi, yritys kuluu (kulu on jo syntynyt)
        ok = False
        loppu = f"raportti tyhja tai liian lyhyt (<{RAPORTTI_MIN_KOKO} tavua)"
    elif not ok:
        loppu = syy_viestiksi(md_polku if paivittyi else None, loppu)
    return Tulos(ok, kulu, str(md_polku) if ok else "", "ok" if ok else (loppu or f"poistumiskoodi {koodi}"))


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


def halytys(tila, ajetut_idt):
    """Rivi yhteenvedon alkuun, jos taman ajon tehtavista epaonnistui vahintaan HALYTYS_EPAONNISTUNUTTA tai jokin
    on kayttanut kaikki yrityksensa (ei enaa yritetaan). Tyhja merkkijono jos kaikki hyvin."""
    tehtavat = tila.get("tehtavat", {})
    kaatuneet = [i for i in ajetut_idt if tehtavat.get(i, {}).get("tila") == "epaonnistui"]
    loppuun = [i for i in kaatuneet if tehtavat[i].get("yrityksia", 0) >= MAX_YRITYKSIA]
    if len(kaatuneet) >= HALYTYS_EPAONNISTUNUTTA or loppuun:
        osat = [f"{len(kaatuneet)} epaonnistui"]
        if loppuun:
            osat.append("yritykset loppu: " + ", ".join(loppuun))
        return "HALYTYS: " + "; ".join(osat)
    return ""


def yhteenveto(rivit, kulu_tanaan, katto, pvm, halytysrivi=""):
    otsikko = f"Yojono {pvm}: kulu tanaan {kulu_tanaan:.2f} / {katto:.2f} USD"
    return "\n".join(([halytysrivi] if halytysrivi else []) + [otsikko] + (rivit or ["Ei ajettavia tehtavia."]))


def kirjaa_lokiin(t, tulos, pvm, polku=None):
    """Valinnainen tapahtumaloki (YOJONO_LOKI = tietokannan polku). Virhe ei koskaan kaada ajoa."""
    polku = polku or os.environ.get("YOJONO_LOKI")
    if not polku:
        return False
    try:
        from agentti import tapahtumaloki
        yht = tapahtumaloki.avaa(polku)
        try:
            tapahtumaloki.kirjaa(yht, "yojono", "yojono", t.get("tyyppi", "katselmointi"), "ok" if tulos.ok else "virhe",
                                 kulu_usd=round(tulos.kulu, 4),
                                 tiedot={"tehtava": t["id"], "repo": t["repo"], "pvm": pvm})
        finally:
            yht.close()
        return True
    except Exception as virhe:  # loki on lisa; ajo jatkuu aina
        print(f"Lokikirjaus epaonnistui: {type(virhe).__name__}", file=sys.stderr)
        return False


def skripti_tyypille(skripti, tyyppi):
    """`skripti` on joko polku (vanha: vain katselmointi) tai sanakirja tyyppi -> polku."""
    if isinstance(skripti, dict):
        return skripti.get(tyyppi, "")
    return skripti if tyyppi == "katselmointi" else ""


def aja(perus, skripti, laheta=print, suorita=suorita_katselmointi, nyt=time.monotonic, pvm=None, kuiva=False,
        telegram=laheta_telegram):
    """Ajaa jonon. Palauttaa tekstiyhteenvedon. Paivakatto: ennen tehtavaa varataan sen koko budjetti."""
    pvm = pvm or date.today().isoformat()
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
        for t in ajettavat:
            kulu_nyt = tila["kulut"].get(pvm, 0.0)
            if nyt() - alku > ILTA_AIKARAJA_S:
                rivit.append(f"- {t['id']}: ohitettu (yon aikaraja)")
                continue
            if kuukausikatto is not None and kuukausikatto - kuukauden_kulu(tila, pvm) < t["budjetti"]:
                rivit.append(f"- {t['id']}: ohitettu (kuukausikatto: jaljella "
                             f"{max(kuukausikatto - kuukauden_kulu(tila, pvm), 0):.2f} < {t['budjetti']:.2f})")
                continue
            if katto - kulu_nyt < t["budjetti"]:
                rivit.append(f"- {t['id']}: ohitettu (paivakatto: jaljella {katto - kulu_nyt:.2f} < {t['budjetti']:.2f})")
                continue
            if t["tyyppi"] == "kehitys" and pr_kutsu.paikkoja_jaljella() <= 0:
                # Ei kalliin mallikutsun jalkeen hukkaan menevaa tyota: PR-paivaraja on taynna, yritys ei kulu
                rivit.append(f"- {t['id']}: ohitettu (PR-paivaraja taynna, ajetaan seuraavana paivana)")
                continue
            tulos = suorita(t, pvm, perus, skripti_tyypille(skripti, t["tyyppi"]))
            ajetut.append(t["id"])
            tila["kulut"][pvm] = round(kulu_nyt + tulos.kulu, 4)
            vanha = tila["tehtavat"].get(t["id"], {})
            tila["tehtavat"][t["id"]] = {
                "tila": "valmis" if tulos.ok else "epaonnistui",
                "yrityksia": vanha.get("yrityksia", 0) + (0 if tulos.ok else 1),
                "pvm": pvm, "kulu": round(tulos.kulu, 4), "raportti": tulos.raportti, "viesti": tulos.viesti,
            }
            kirjoita_tila(tila_polku, tila)  # heti: katkennut ajo ei hukkaa kulukirjanpitoa
            kirjaa_lokiin(t, tulos, pvm)
            rivit.append(f"- {t['id']}: {'VALMIS' if tulos.ok else 'EPAONNISTUI'} ({tulos.kulu:.2f} USD)"
                         + (f" raportti {tulos.raportti}" if tulos.ok else f" syy: {tulos.viesti}")
                         + (raportin_lisatiedot(t["tyyppi"], tulos.raportti) if tulos.ok else ""))
        kirjoita_tila(tila_polku, tila)
        teksti = yhteenveto(rivit, tila["kulut"].get(pvm, 0.0), katto, pvm, halytys(tila, ajetut))
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


def main(argv=None):
    p = argparse.ArgumentParser(description="Yojonon ajuri")
    p.add_argument("komento", choices=["ajo", "kuiva", "tila"])
    a = p.parse_args(argv)
    perus = os.environ.get("YOJONO_PERUS", OLETUS_PERUS)
    skripti = {tyyppi: os.environ.get(muuttuja, oletus) for tyyppi, (muuttuja, oletus) in SKRIPTIT.items()}
    if a.komento == "tila":
        print(json.dumps(lue_json(Path(perus) / "tila.json", {}), indent=2, ensure_ascii=False))
        return 0
    try:
        tulos = aja(perus, skripti, kuiva=a.komento == "kuiva")
        if a.komento == "kuiva":
            print(tulos)
        elif a.komento == "ajo":
            peilaa(os.environ.get("YOJONO_PEILAA"))
    except JonoVirhe as virhe:
        print(f"jono.json virheellinen: {virhe}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
