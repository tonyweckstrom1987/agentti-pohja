#!/usr/bin/env python3
"""Kylla-skriptit: isanta ajaa Tonyn hyvaksyman skriptin ja palauttaa suodatetun tulosteen (tehtava 2026-11-05, A433-A439).

Ajetaan Proxmox-isannalla rootina systemd-ajastimella (5 min). Ohje: docs/kylla-skriptit.md.

  kylla.py kierros --ajettavat DIR --tila DIR --tulosteet DIR --chat-tiedosto F [--hae-git DOCS_KLOONI]
  kylla.py palvelut --lista F --tila DIR --chat-tiedosto F
  kylla.py tarkista --ajettavat DIR          kuiva-ajo: listaa skriptit, sha ja hylkayksen syy; ei aja eika laheta

Kierros (yksi ajo kerrallaan, fcntl-lukko):
  1. (valinnainen) git pull --ff-only docs-kloonissa
  2. Tonyn vastaukset komentobotilta (getUpdates). Vain viestit, joiden chat.id JA from.id ovat Tonyn chat-id;
     muut ohitetaan hiljaa. Hyvaksytty muoto: "kylla NN" tai "kylla NN abcd1234" (a-kirjain saa olla myos ae-muodossa).
  3. Ajo: skriptin nykyinen sha256 PITAA olla sama kuin kysymyksessa naytetty, ja vastauksen pitaa olla kysymysta
     uudempi. Ajetaan rootin omistama kopio (tila/odottavat/NN.sh), jonka sha tarkistetaan viela juuri ennen ajoa.
     Aikaraja 10 min (koko prosessiryhma tapetaan). Tuloste suodatetaan (osoitteet, tailnet-nimet, tunnukset).
  4. Uudet tai muuttuneet skriptit: kysymys Tonylle "Aja NN: <kuvaus> (sha 8 merkkia)? Vastaa: kylla NN".

Skriptin vaatimukset (muuten hylataan ja Tony saa syyn):
  - nimi NN-nimi.sh (NN = 01-99), vain hakemiston ajettavat/ juuressa, tavallinen tiedosto (ei symlinkkia), enintaan 64 kt
  - otsakkeessa (20 ensimmaista rivia) "# kirjoittaja: pilvi-claude" tai "# kirjoittaja: aivo" (A436)
    ja "# kuvaus: <yksi rivi>"
Skripti ei voi muuttaa omaa ajajaansa: ajettava sisalto ei saa mainita taman ajajan polkuja (KIELLETYT_KOHTEET);
lisaksi pr_kutsu.AGENTILTA_KIELLETYT_RE:hen pitaa lisata isanta/kylla.py (Tony, ks. ohje).

Palvelut (5b, A437-A439): listatiedostossa (Tonyn hyvaksyma, root 0644) rivit "CT palvelu.service". Kaatunut
palvelu (systemctl is-failed) kaynnistetaan uudelleen ilman kyllaa. Kauppatiimin palvelut eivat kelpaa koskaan.
Kolmas uudelleenkaynnistys samana vuorokautena lopettaa automaattisen kaynnistyksen sille palvelulle,
lahettaa halytyksen ja kirjaa juurisyyn selvityksen tila/juurisyyt.jsonl:aan (jonoon vienti: ks. ohje).

Tunnukset: komentobotin tunnus ymparistomuuttujassa KOMENTOBOTTI_TOKEN (EnvironmentFile), Tonyn chat-id VAIN
tiedostossa. Tunnuksia ei tulosteta; verkkovirheista vain tyyppi ja HTTP-tila. Vain stdlib, koodi ASCII-muotoista.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request

AIKARAJA = 600
SKRIPTI_MAX = 64 * 1024
TULOSTE_MAX = 100 * 1024
VIESTI_MAX = 3500
VIESTEJA_MAX = 20
KYSYMYKSIA_MAX = 5
NIMI_RE = re.compile(r"^((?:0[1-9]|[1-9][0-9]))-[a-z0-9][a-z0-9-]{0,40}\.sh$")
KIRJOITTAJAT = ("pilvi-claude", "aivo")
KIRJOITTAJA_RE = re.compile(r"^#\s*kirjoittaja:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
KUVAUS_RE = re.compile(r"^#\s*kuvaus:\s*(.{3,200}?)\s*$", re.IGNORECASE | re.MULTILINE)
KYLLA_RE = re.compile(r"^(?:kyll(?:\u00e4|a)|kylla)\s+([0-9]{2})(?:\s+([0-9a-f]{8}))?$", re.IGNORECASE)
LUKKO = "/run/kylla.lock"
LOKI = "/var/log/kylla.log"
# Ajajan omat polut: skripti ei saa koskea niihin (ei voi muuttaa omaa ajajaansa eika sen tilaa).
KIELLETYT_KOHTEET = ("kylla.py", "kylla-kierros", "kylla-palvelut", "/var/lib/kylla", "/etc/kylla", "kylla.log")
PALVELU_RE = re.compile(r"^([1-9][0-9]{2,3})\s+([A-Za-z0-9@_.-]{1,80}\.service)$")
KAUPPA_RE = re.compile(r"kauppa", re.IGNORECASE)
PALVELU_PAIVAKATTO = 3
AIKAVYOHYKE = datetime.timezone.utc

# Suodatus. OSOITE_RE on sama kuin tools/automerge.py:n (testi varmistaa), lisaksi kaikki IPv4/IPv6 ja tunnukset.
OSOITE_RE = re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b|\b[a-z0-9-]+\.ts\.net\b|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
                       re.IGNORECASE)
SUODATTIMET = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL), "<avain>"),
    (OSOITE_RE, "<osoite>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<osoite>"),
    (re.compile(r"\b(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}\b|(?<![0-9a-z:])(?:[0-9a-f]{1,4}:)+:(?:[0-9a-f]{1,4}(?::[0-9a-f]{1,4})*)?",
                re.IGNORECASE), "<osoite>"),                                           # IPv6 (ei kellonaikoja)
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"), "<tunnus>"),                        # Telegram-bottitunnus
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "<tunnus>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "<tunnus>"),
    (re.compile(r"\b(?:ssh-(?:ed25519|rsa)|ecdsa-sha2-[a-z0-9-]+)\s+[A-Za-z0-9+/=]{20,}"), "<avain>"),
    (re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key)\b(\s*[:=]\s*)\S+"), r"\1\2<salaisuus>"),
    (re.compile(r"(?i)\b(authorization:\s*\w+|bearer)\s+\S+"), r"\1 <salaisuus>"),
    # pitka satunnaisen nakoinen merkkijono (iso kirjain ja numero mukana): sha256-heksat jaavat nakyviin
    (re.compile(r"(?<![A-Za-z0-9+/_-])(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*[0-9])[A-Za-z0-9+/_-]{40,}={0,2}"),
     "<pitka-merkkijono>"),
]


class Virhe(Exception):
    """Odotettu virhe: viesti on turvallinen nayttaa (ei tunnuksia eika skriptin sisaltoa)."""


class TelegramVirhe(Virhe):
    pass


# ---------- apurit ----------

def nyt():
    return time.time()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def suodata(teksti):
    for malli, korvike in SUODATTIMET:
        teksti = malli.sub(korvike, teksti)
    return teksti


def kirjoita_atomisesti(polku, data, tila=0o600):
    os.makedirs(os.path.dirname(polku) or ".", exist_ok=True)
    tmp = "%s.%d.tmp" % (polku, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, tila)
    with os.fdopen(fd, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.replace(tmp, polku)


def lue_json(polku, oletus):
    try:
        with open(polku, encoding="utf-8") as f:
            arvo = json.load(f)
        return arvo if isinstance(arvo, type(oletus)) else oletus
    except (OSError, ValueError):
        return oletus


def kirjaa(rivi, loki=None):
    """Ajoloki: yksi JSON-rivi, ei skriptin tulostetta eika tunnuksia."""
    rivi = dict(rivi, aika=datetime.datetime.now(AIKAVYOHYKE).strftime("%Y-%m-%dT%H:%M:%SZ"))
    try:
        with open(loki or LOKI, "a", encoding="utf-8") as f:
            f.write(json.dumps(rivi, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError:
        pass


def lue_chat_id(polku):
    try:
        with open(polku, encoding="ascii") as f:
            arvo = f.read().strip()
    except (OSError, UnicodeDecodeError):
        raise Virhe("chat-id-tiedostoa ei voi lukea") from None
    if not re.fullmatch(r"-?[0-9]{3,20}", arvo):
        raise Virhe("chat-id-tiedosto ei ole kelvollinen")
    return int(arvo)


# ---------- skriptien tarkistus ----------

def tarkista_skripti(polku):
    """Palauttaa dict(nn, nimi, sha, data, kuvaus, kirjoittaja) tai nostaa Virhe(syy)."""
    nimi = os.path.basename(polku)
    m = NIMI_RE.match(nimi)
    if not m:
        raise Virhe("nimi ei ole muotoa NN-nimi.sh")
    st = os.lstat(polku)
    if not os.path.isfile(polku) or os.path.islink(polku):
        raise Virhe("ei tavallinen tiedosto")
    if st.st_size > SKRIPTI_MAX:
        raise Virhe("liian suuri")
    with open(polku, "rb") as f:
        data = f.read(SKRIPTI_MAX + 1)
    if len(data) > SKRIPTI_MAX:
        raise Virhe("liian suuri")
    try:
        teksti = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Virhe("ei UTF-8") from None
    otsake = "\n".join(teksti.splitlines()[:20])
    k = KIRJOITTAJA_RE.search(otsake)
    if not k or k.group(1).lower() not in KIRJOITTAJAT:
        raise Virhe("kirjoittaja puuttuu tai ei ole sallittu (pilvi-claude, aivo)")
    kuv = KUVAUS_RE.search(otsake)
    if not kuv:
        raise Virhe("kuvaus-rivi puuttuu")
    for kohde in KIELLETYT_KOHTEET:
        if kohde in teksti:
            raise Virhe("skripti koskee ajajan omiin polkuihin")
    return {"nn": m.group(1), "nimi": nimi, "sha": sha256(data), "data": data,
            "kuvaus": suodata(kuv.group(1))[:200], "kirjoittaja": k.group(1).lower()}


def skannaa(ajettavat):
    """Palauttaa (kelvolliset {nn: skripti}, hylatyt [(nimi, syy)]). Vain hakemiston juuri; alihakemistot ohitetaan."""
    kelvolliset, hylatyt = {}, []
    try:
        nimet = sorted(os.listdir(ajettavat))
    except OSError:
        raise Virhe("ajettavat-hakemistoa ei voi lukea") from None
    for nimi in nimet:
        if not nimi.endswith(".sh"):
            continue
        try:
            s = tarkista_skripti(os.path.join(ajettavat, nimi))
        except Virhe as e:
            hylatyt.append((nimi, str(e)))
            continue
        if s["nn"] in kelvolliset:
            hylatyt.append((nimi, "sama numero kahdella skriptilla"))
            kelvolliset.pop(s["nn"])
            continue
        kelvolliset[s["nn"]] = s
    return kelvolliset, hylatyt


# ---------- Telegram ----------

def _telegram(token, metodi, parametrit, hae=None):
    if hae:
        return hae(metodi, parametrit)
    osoite = "https://api.telegram.org/bot%s/%s" % (token, metodi)
    try:
        data = urllib.parse.urlencode(parametrit).encode()
        with urllib.request.urlopen(osoite, data=data, timeout=20) as v:
            vastaus = json.loads(v.read().decode())
    except Exception as e:
        # URL sisaltaa tunnuksen: uusi poikkeus ilman ketjua, vain tyyppi ja HTTP-tila
        tila = getattr(e, "code", None)
        raise TelegramVirhe("%s epaonnistui: %s%s" % (metodi, type(e).__name__,
                                                      " %d" % tila if isinstance(tila, int) else "")) from None
    if not isinstance(vastaus, dict) or vastaus.get("ok") is not True:
        raise TelegramVirhe("%s ei ok" % metodi)
    return vastaus.get("result")


class Botti:
    def __init__(self, token, chat_id, hae=None):
        self.token, self.chat_id, self.hae = token, chat_id, hae

    def laheta(self, teksti):
        try:
            _telegram(self.token, "sendMessage", {"chat_id": self.chat_id, "text": teksti[:VIESTI_MAX]}, self.hae)
            return True
        except TelegramVirhe:
            return False

    def paivitykset(self, offset):
        p = {"limit": VIESTEJA_MAX, "timeout": 0, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            p["offset"] = offset
        tulos = _telegram(self.token, "getUpdates", p, self.hae)
        return tulos if isinstance(tulos, list) else []


def tonyn_viestit(paivitykset, chat_id):
    """Palauttaa (viimeinen update_id, [(teksti, unix-aika)]) vain Tonyn chatista. Muut ohitetaan hiljaa."""
    viimeinen, viestit = None, []
    for p in paivitykset:
        if not isinstance(p, dict) or not isinstance(p.get("update_id"), int):
            continue
        viimeinen = max(viimeinen or 0, p["update_id"])
        v = p.get("message")
        if not isinstance(v, dict):
            continue
        chat = (v.get("chat") or {}).get("id")
        lahettaja = (v.get("from") or {}).get("id")
        if chat != chat_id or lahettaja != chat_id:
            continue
        if isinstance(v.get("text"), str) and isinstance(v.get("date"), int):
            viestit.append((v["text"], v["date"]))
    return viimeinen, viestit


def jasenna(teksti):
    """('kylla', NN, sha8|None) tai None."""
    if not isinstance(teksti, str) or len(teksti) > 40:
        return None
    m = KYLLA_RE.match(teksti.strip())
    if not m:
        return None
    return ("kylla", m.group(1), (m.group(2) or "").lower() or None)


# ---------- ajo ----------

def aja_skripti(polku, aikaraja=AIKARAJA, popen=subprocess.Popen):
    """Ajaa skriptin omassa prosessiryhmassa. Palauttaa (paluukoodi | None aikarajalla, tuloste-bytes, kesto s)."""
    alku = time.monotonic()
    ymp = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/root"}
    p = popen(["/bin/bash", polku], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              cwd="/", env=ymp, start_new_session=True)
    try:
        ulos, _ = p.communicate(timeout=aikaraja)
        koodi = p.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        ulos, _ = p.communicate()
        koodi = None
    return koodi, (ulos or b"")[:TULOSTE_MAX * 4], round(time.monotonic() - alku, 1)


class Kierros:
    def __init__(self, ajettavat, tila, tulosteet, botti, aja=aja_skripti, loki=None, aikaraja=AIKARAJA):
        self.ajettavat, self.tila, self.tulosteet, self.botti = ajettavat, tila, tulosteet, botti
        self.aja, self.loki, self.aikaraja = aja, loki, aikaraja
        self.tila_polku = os.path.join(tila, "tila.json")
        self.t = lue_json(self.tila_polku, {})
        self.t.setdefault("odottavat", {})    # nn -> {sha, kysytty, nimi, kuvaus}
        self.t.setdefault("ajetut", {})       # nn -> [sha, ...]
        self.t.setdefault("hylatyt", {})      # nimi -> syy (ettei samaa syyta laheteta joka kierros)

    def tallenna(self):
        kirjoita_atomisesti(self.tila_polku, json.dumps(self.t, sort_keys=True))

    def kopio(self, nn):
        return os.path.join(self.tila, "odottavat", "%s.sh" % nn)

    # vaihe 2-3
    def kasittele_vastaukset(self):
        offset = self.t.get("offset")
        try:
            paiv = self.botti.paivitykset(offset)
        except TelegramVirhe as e:
            kirjaa({"tapahtuma": "telegram_virhe", "syy": str(e)}, self.loki)
            return
        viimeinen, viestit = tonyn_viestit(paiv, self.botti.chat_id)
        if viimeinen is not None:
            self.t["offset"] = viimeinen + 1
            self.tallenna()                   # offset ennen ajoa: sama viesti ei aja kahdesti
        for teksti, aika in viestit:
            j = jasenna(teksti)
            if j:
                self.kylla(j[1], j[2], aika)

    def kylla(self, nn, sha8, vastausaika):
        o = self.t["odottavat"].get(nn)
        if not o:
            self.botti.laheta("%s: ei odottavaa kysymyst\u00e4, ei ajettu." % nn)
            return "ei_odottavaa"
        if vastausaika < int(o["kysytty"]):
            self.botti.laheta("%s: vastaus on vanhempi kuin kysymys (skripti muuttui), ei ajettu. Vastaa uuteen kysymykseen." % nn)
            return "vanha_vastaus"
        if sha8 and sha8 != o["sha"][:8]:
            self.botti.laheta("%s: sha %s ei vastaa kysytty\u00e4 (%s), ei ajettu." % (nn, sha8, o["sha"][:8]))
            return "sha_eri"
        # skriptin nykyinen sisalto lahteessa: muuttunut skripti vaatii uuden kyllan
        lahde = os.path.join(self.ajettavat, o["nimi"])
        try:
            nyky = tarkista_skripti(lahde)
        except (Virhe, OSError):
            nyky = None
        if not nyky or nyky["sha"] != o["sha"]:
            del self.t["odottavat"][nn]
            self.tallenna()
            self.botti.laheta("%s: skripti muuttui tai poistui kysymyksen j\u00e4lkeen, ei ajettu. Uusi kysymys tulee, jos skripti on yh\u00e4 olemassa." % nn)
            kirjaa({"tapahtuma": "hylatty", "nn": nn, "syy": "sha_muuttui", "sha": o["sha"][:16]}, self.loki)
            return "sha_muuttui"
        # ajettava kopio: rootin oma, sha tarkistetaan viela juuri ennen ajoa
        try:
            with open(self.kopio(nn), "rb") as f:
                data = f.read(SKRIPTI_MAX + 1)
        except OSError:
            data = b""
        if sha256(data) != o["sha"]:
            del self.t["odottavat"][nn]
            self.tallenna()
            self.botti.laheta("%s: ajettava kopio ei vastaa kysytty\u00e4 sha:ta, ei ajettu." % nn)
            kirjaa({"tapahtuma": "hylatty", "nn": nn, "syy": "kopio_eri", "sha": o["sha"][:16]}, self.loki)
            return "kopio_eri"
        # kirjaus ennen ajoa: kaatuminen kesken ei johda uusintaan
        del self.t["odottavat"][nn]
        self.t["ajetut"].setdefault(nn, []).append(o["sha"])
        self.tallenna()
        kirjaa({"tapahtuma": "ajo_alkaa", "nn": nn, "nimi": o["nimi"], "sha": o["sha"][:16],
                "kirjoittaja": o.get("kirjoittaja")}, self.loki)
        koodi, ulos, kesto = self.aja(self.kopio(nn), self.aikaraja)
        teksti = suodata(ulos.decode("utf-8", "replace"))
        if len(teksti) > TULOSTE_MAX:
            teksti = teksti[:TULOSTE_MAX] + "\n[katkaistu]\n"
        tila = "aikaraja" if koodi is None else ("onnistui" if koodi == 0 else "ep\u00e4onnistui")
        otsake = "# %s sha256=%s\n# tila=%s paluukoodi=%s kesto=%ss aika=%s\n\n" % (
            o["nimi"], o["sha"], tila, "-" if koodi is None else koodi, kesto,
            datetime.datetime.now(AIKAVYOHYKE).strftime("%Y-%m-%dT%H:%M:%SZ"))
        kirjoita_atomisesti(os.path.join(self.tulosteet, "%s.txt" % nn), otsake + teksti, 0o640)
        kirjaa({"tapahtuma": "ajo_valmis", "nn": nn, "sha": o["sha"][:16], "tila": tila, "paluukoodi": koodi,
                "kesto": kesto}, self.loki)
        if koodi is None:
            self.botti.laheta("%s: aikaraja (%d min) ylittyi, ajo keskeytettiin." % (nn, self.aikaraja // 60))
        else:
            self.botti.laheta("%s: %s, paluukoodi %d." % (nn, tila, koodi))
        return tila

    # vaihe 4
    def kysy_uudet(self):
        kelvolliset, hylatyt = skannaa(self.ajettavat)
        lahetetty = 0
        for nimi, syy in hylatyt:
            if self.t["hylatyt"].get(nimi) != syy and lahetetty < KYSYMYKSIA_MAX:
                self.botti.laheta("Hyl\u00e4tty %s: %s." % (suodata(nimi)[:60], syy))
                kirjaa({"tapahtuma": "hylatty", "nimi": suodata(nimi)[:60], "syy": syy}, self.loki)
                self.t["hylatyt"][nimi] = syy
                lahetetty += 1
        for nn, s in sorted(kelvolliset.items()):
            if lahetetty >= KYSYMYKSIA_MAX:
                break
            if s["sha"] in self.t["ajetut"].get(nn, []):
                continue
            o = self.t["odottavat"].get(nn)
            if o and o["sha"] == s["sha"]:
                continue
            kirjoita_atomisesti(self.kopio(nn), s["data"], 0o700)
            self.t["odottavat"][nn] = {"sha": s["sha"], "kysytty": int(nyt()), "nimi": s["nimi"],
                                       "kuvaus": s["kuvaus"], "kirjoittaja": s["kirjoittaja"]}
            self.tallenna()
            self.botti.laheta("Aja %s: %s (kirjoittaja %s, sha256 %s)? Vastaa: kyll\u00e4 %s" % (
                nn, s["kuvaus"], s["kirjoittaja"], s["sha"][:8], nn))
            kirjaa({"tapahtuma": "kysytty", "nn": nn, "sha": s["sha"][:16], "kirjoittaja": s["kirjoittaja"]}, self.loki)
            lahetetty += 1
        # poistuneet skriptit: odottava kysymys raukeaa
        for nn in list(self.t["odottavat"]):
            if nn not in kelvolliset:
                del self.t["odottavat"][nn]
        self.tallenna()

    def aja_kierros(self):
        self.kasittele_vastaukset()
        self.kysy_uudet()


# ---------- palvelut (5b) ----------

def lue_palvelulista(polku):
    """Rivit 'CT palvelu.service'. Kauppatiimin palvelut ja virheelliset rivit ohitetaan."""
    tulos = []
    try:
        with open(polku, encoding="ascii") as f:
            rivit = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return tulos
    for r in rivit:
        r = r.split("#", 1)[0].strip()
        m = PALVELU_RE.match(r)
        if m and not KAUPPA_RE.search(m.group(2)):
            tulos.append((m.group(1), m.group(2)))
    return tulos


def palvelukierros(lista, tila, botti, suorita=subprocess.run, loki=None, paiva=None):
    polku = os.path.join(tila, "palvelut.json")
    t = lue_json(polku, {})
    paiva = paiva or datetime.datetime.now(AIKAVYOHYKE).strftime("%Y-%m-%d")
    if t.get("paiva") != paiva:
        t = {"paiva": paiva, "laskurit": {}, "pysaytetyt": t.get("pysaytetyt", [])}
    tulokset = []
    for ct, palvelu in lista:
        avain = "%s/%s" % (ct, palvelu)
        if avain in t["pysaytetyt"]:
            continue
        try:
            r = suorita(["pct", "exec", ct, "--", "systemctl", "is-failed", "--quiet", palvelu],
                        capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            kirjaa({"tapahtuma": "palvelu_tarkistus_virhe", "palvelu": avain, "syy": type(e).__name__}, loki)
            continue
        if r.returncode != 0:
            continue                          # ei kaatunut
        try:
            rr = suorita(["pct", "exec", ct, "--", "systemctl", "restart", palvelu],
                         capture_output=True, timeout=120, check=False)
            koodi = rr.returncode
        except (OSError, subprocess.SubprocessError) as e:
            koodi = type(e).__name__
        n = t["laskurit"].get(avain, 0) + 1
        t["laskurit"][avain] = n
        rivi = {"tapahtuma": "palvelu_uudelleen", "palvelu": avain, "kerta": n, "paluukoodi": koodi, "paiva": paiva}
        kirjaa(rivi, loki)
        with open(os.path.join(tila, "uudelleenkaynnistykset.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rivi, sort_keys=True) + "\n")      # aamukooste lukee taman
        tulokset.append(rivi)
        if n >= PALVELU_PAIVAKATTO:
            t["pysaytetyt"].append(avain)
            with open(os.path.join(tila, "juurisyyt.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"palvelu": avain, "paiva": paiva, "kertoja": n}, sort_keys=True) + "\n")
            botti.laheta("H\u00e4lytys: %s k\u00e4ynnistettiin uudelleen %d kertaa t\u00e4n\u00e4\u00e4n. "
                         "Automaattinen k\u00e4ynnistys lopetettu, juurisyyn selvitys kirjattu." % (avain, n))
    kirjoita_atomisesti(polku, json.dumps(t, sort_keys=True))
    return tulokset


# ---------- komentorivi ----------

def _lukittu(polku):
    fd = os.open(polku, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _botti(chat_tiedosto):
    token = os.environ.get("KOMENTOBOTTI_TOKEN", "")
    if not token:
        raise Virhe("KOMENTOBOTTI_TOKEN puuttuu")
    return Botti(token, lue_chat_id(chat_tiedosto))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kylla.py")
    ali = ap.add_subparsers(dest="komento", required=True)
    k = ali.add_parser("kierros")
    k.add_argument("--ajettavat", required=True)
    k.add_argument("--tila", required=True)
    k.add_argument("--tulosteet", required=True)
    k.add_argument("--chat-tiedosto", required=True)
    k.add_argument("--hae-git")
    p = ali.add_parser("palvelut")
    p.add_argument("--lista", required=True)
    p.add_argument("--tila", required=True)
    p.add_argument("--chat-tiedosto", required=True)
    t = ali.add_parser("tarkista")
    t.add_argument("--ajettavat", required=True)
    a = ap.parse_args(argv)
    os.umask(0o077)
    try:
        if a.komento == "tarkista":
            kelvolliset, hylatyt = skannaa(a.ajettavat)
            for nn, s in sorted(kelvolliset.items()):
                print("ok %s %s sha256=%s kirjoittaja=%s" % (nn, s["nimi"], s["sha"][:8], s["kirjoittaja"]))
            for nimi, syy in hylatyt:
                print("hylatty %s: %s" % (suodata(nimi)[:60], syy))
            return 0
        fd = _lukittu(LUKKO)
        if fd is None:
            print("kylla: edellinen ajo kaynnissa, ohitetaan")
            return 0
        botti = _botti(a.chat_tiedosto)
        if a.komento == "palvelut":
            os.makedirs(a.tila, exist_ok=True)
            palvelukierros(lue_palvelulista(a.lista), a.tila, botti)
            return 0
        if a.hae_git:
            r = subprocess.run(["git", "-C", a.hae_git, "pull", "-q", "--ff-only"], capture_output=True, timeout=120,
                               check=False)
            if r.returncode != 0:
                kirjaa({"tapahtuma": "git_virhe", "paluukoodi": r.returncode})
        os.makedirs(a.tila, exist_ok=True)
        Kierros(a.ajettavat, a.tila, a.tulosteet, botti).aja_kierros()
        return 0
    except Virhe as e:
        print("kylla: %s" % e, file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - vain tyyppi, ei viestia (voi sisaltaa tunnuksia)
        print("kylla: odottamaton virhe %s" % type(e).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
