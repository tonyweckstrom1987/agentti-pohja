"""Yhteinen Telegram-virheilmoitus systemd-yksikoille (trendit, yojono). Vain stdlib, ASCII.

Kaytto (virheilmoitus@.service kutsuu):
  virheilmoitus.py yksikko <yksikon-nimi>    # OnFailure: yksikko paattyi virheeseen
  virheilmoitus.py yojono-tila                # yojonon tehtavia epaonnistui (ajuri palauttaa silti 0)

Muoto on sama kuin paivakatsauksessa (render.virherivit): 'Virhe: <koodit>', 'Syy: ...', 'Loki: <kiintea tiivistelma>'.
Vain kiinteat kentat: koodit, poistumiskoodi, tulos, kesto, skriptin omat [tunniste]-tilarivit ja poikkeusten tyyppinimet.
Poikkeusviestia, mallin sisaltoa tai salaisuuksia ei laiteta viestiin; rivit suodatetaan lisaksi avainkuvioista.
Telegram-tunnukset tulevat ymparistosta (TELEGRAM_TOKEN + TELEGRAM_CHAT_ID tai YOJONO_TELEGRAM_TOKEN + _CHAT); niita ei lueta tiedostosta eika tulosteta.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

YOJONO_TILA = "/var/lib/yojono/tila.json"
AJOLOKIT = {"paivakatsaus": "/opt/agentscope/paivakatsaus/paivakatsaus.log"}      # yksikko -> loki, jonka ajoriveilla on virhekoodi=
RENDER_HAKEMISTO = "/opt/agentscope/paivakatsaus"
TELEGRAM_MAX = 2000
_UNIT = re.compile(r"[A-Za-z0-9_.@:-]{1,80}")
_SUODATIN = [
    (re.compile(r"(sk-|xoxb-|ghp_|github_pat_)[A-Za-z0-9_-]+"), r"\1<x>"),
    (re.compile(r"(Bearer |token=|TOKEN=|KEY=|key=)\S+"), r"\1<x>"),
    (re.compile(r"[0-9]{8,}:[A-Za-z0-9_-]{30,}"), "<tg-token>"),
]
_KOODI = re.compile(r"[a-z0-9_]{1,60}")
_LOKI_AIKA = re.compile(r"^(\d{4}-\d{2}-\d{2})T\S+ ")
_LOKI_KENTAT = (("tokenit", re.compile(r"\btokenit=(\d+|-)/(\d+|-)(?=\s|$)")), ("finish", re.compile(r"\bfinish=([a-z_]{1,24})(?=\s|$)")),
                ("http", re.compile(r"\bhttp=(\d{3})(?=\s|$)")))
_TILARIVI = re.compile(r"^\[[a-z_]{1,20}\] ")
_POIKKEUS = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Exit|Interrupt))\b")
SYYT = {
    "exit-code": "prosessi palautti nollasta poikkeavan poistumiskoodin",
    "timeout": "aikakatkaisu",
    "signal": "prosessi paattyi signaaliin",
    "core-dump": "prosessi kaatui (core dump)",
    "oom-kill": "muisti loppui",
}


def suodata(teksti):
    for kuvio, korvaus in _SUODATIN:
        teksti = kuvio.sub(korvaus, teksti)
    return teksti.encode("ascii", errors="replace").decode("ascii")


def koodiksi(nimi):
    return re.sub(r"[^a-z0-9]+", "_", nimi.lower().replace(".service", "")).strip("_")[:50]


def muotoile(otsikko, koodit, syy="", loki=""):
    rivit = [otsikko, "Virhe: " + ", ".join(koodit)]
    if syy:
        rivit.append("Syy: " + syy)
    if loki:
        rivit.append("Loki: " + loki)
    return suodata("\n".join(rivit))[:TELEGRAM_MAX]


def loki_tiivistelma(rivit, poistumiskoodi=None, kesto_s=None):
    """rivit: yksikon lokirivit. Mukaan vain [tunniste]-tilarivit (viimeiset 2) ja poikkeusten tyyppinimet."""
    osat = []
    if poistumiskoodi not in (None, ""):
        osat.append(f"exit={poistumiskoodi}")
    if kesto_s is not None:
        osat.append(f"kesto_s={kesto_s:.1f}")
    tilarivit = [suodata(r.strip())[:100] for r in rivit if _TILARIVI.match(r.strip())][-2:]
    osat.extend(f"'{r}'" for r in tilarivit)
    tyypit = []
    for r in rivit:
        for t in _POIKKEUS.findall(r):
            if t not in tyypit:
                tyypit.append(t)
    if tyypit:
        osat.append("poikkeus=" + ",".join(tyypit[:3]))
    return " ".join(osat)


def ajorivin_tiedot(polku, pvm, lue=None):
    """Lokin viimeinen ajorivi (rivi jolla on tila= ja virhekoodi=; komennot- ja proposals-rivit ohitetaan) -> (koodit, loki_osat).
    Vain jos rivin paivamaara on pvm (muuten rivi on vanhentunut). Puuttuva, tyhja ('-') tai vanhentunut -> ([], [])."""
    try:
        teksti = lue(polku) if lue else Path(polku).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []
    rivi = next((r for r in reversed(teksti.splitlines()[-400:]) if " tila=" in r and " virhekoodi=" in r), "")
    a = _LOKI_AIKA.match(rivi)
    m = re.search(r"\bvirhekoodi=(\S+)", rivi)
    if not a or not m or a.group(1) != pvm:
        return [], []
    koodit = [k for k in m.group(1).split(",") if _KOODI.fullmatch(k)]
    if not koodit:
        return [], []
    osat = []
    for nimi, kuvio in _LOKI_KENTAT:
        k = kuvio.search(rivi)
        if k:
            osat.append(f"{nimi}=" + "/".join(k.groups()))
    return koodit, osat


def koodin_syy(koodi):
    """Tarkka syy paivakatsauksen render.VIRHEOHJEET-taulusta, jos saatavilla."""
    try:
        if RENDER_HAKEMISTO not in sys.path:
            sys.path.append(RENDER_HAKEMISTO)
        import render
        return str(render.VIRHEOHJEET.get(koodi, ""))
    except Exception:                                                   # puuttuva moduuli tms.: yleinen syy riittaa
        return ""


def _systemctl_show(yksikko):
    ulos = subprocess.run(["systemctl", "show", yksikko, "-p", "Result", "-p", "ExecMainStatus", "-p", "InvocationID",
                           "-p", "ExecMainStartTimestampMonotonic", "-p", "ExecMainExitTimestampMonotonic"],
                          capture_output=True, text=True, timeout=20, check=False).stdout
    return dict(r.split("=", 1) for r in ulos.splitlines() if "=" in r)


def yksikon_viesti(yksikko, show=None, rivit=None, nyt=None, ajolokit=None, syy_haku=None):
    """Rakentaa viestin epaonnistuneesta yksikosta. show/rivit/ajolokit/syy_haku voi antaa testeissa.
    Jos yksikolla on ajoloki (AJOLOKIT) ja sen tanaisen viimeisen ajorivin virhekoodi= on asetettu, se korvaa yleisen varakoodin."""
    if not _UNIT.fullmatch(yksikko):
        raise ValueError("virheellinen yksikon nimi")
    show = show if show is not None else _systemctl_show(yksikko)
    if rivit is None:
        inv = show.get("InvocationID", "")
        komento = ["journalctl", "--no-pager", "-o", "cat", "-n", "60"]
        komento += [f"_SYSTEMD_INVOCATION_ID={inv}"] if re.fullmatch(r"[0-9a-f]{32}", inv) else ["-u", yksikko]
        rivit = subprocess.run(komento, capture_output=True, text=True, timeout=20, check=False).stdout.splitlines()
    tulos = show.get("Result") or "tuntematon"
    kesto = None
    try:
        kesto = (int(show["ExecMainExitTimestampMonotonic"]) - int(show["ExecMainStartTimestampMonotonic"])) / 1e6
        kesto = kesto if kesto >= 0 else None
    except (KeyError, ValueError):
        pass
    nimi = koodiksi(yksikko)
    paikallinen = nyt or datetime.now()          # ajolokin paiva verrataan palvelimen omaan paivaan (ennallaan)
    nyt = nyt or helsinki_nyt()                   # viestin aika Tonin kellossa
    otsikko = f"{yksikko} {nyt.strftime('%d.%m.%Y %H:%M')}"
    koodit, lisa = [], []
    polku = (AJOLOKIT if ajolokit is None else ajolokit).get(nimi)
    if polku:
        koodit, lisa = ajorivin_tiedot(polku, paikallinen.strftime("%Y-%m-%d"))
    syy = SYYT.get(tulos, "")
    if koodit:
        syy = (syy_haku or koodin_syy)(koodit[0]) or syy
    else:
        koodit = [f"{nimi}_{koodiksi(tulos)}"]                          # varakoodi: yksikon nimi + systemd-tulos
    loki = loki_tiivistelma(rivit, show.get("ExecMainStatus"), kesto)
    return muotoile(otsikko, koodit, syy, " ".join(x for x in (loki, " ".join(lisa)) if x))


ILMOITETTU = "/var/lib/yojono/ilmoitettu.json"


def helsinki_nyt():
    """Nykyhetki Europe/Helsinki-ajassa (ajurin ajopaiva lasketaan siina); ilman tzdataa palvelimen oma aika."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Helsinki"))
    except Exception:
        return datetime.now()


def _ajopaiva(nyt=None):
    """Ajurin ajopaiva vaihtuu klo 09:00 Helsingin aikaa (yojono/ajuri.py): yoajot A/B/C ovat samaa paivaa.
    Ilman annettua aikaa kaytetaan Helsingin aikaa, jotta paiva tasmaa tila.json:n `pvm`-arvoihin palvelimen aikavyohykkeesta riippumatta."""
    from datetime import timedelta
    return ((nyt or helsinki_nyt()) - timedelta(hours=9)).strftime("%Y-%m-%d")


def _lue_ilmoitetut():
    try:
        return set(json.loads(Path(ILMOITETTU).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def _tallenna_ilmoitetut(nahdyt):
    nyt = _ajopaiva()
    try:
        Path(ILMOITETTU).write_text(json.dumps(sorted(k for k in nahdyt if k.split("|")[0] == nyt)), encoding="utf-8")
    except OSError as e:
        print(f"Ilmoitettujen tallennus epaonnistui: {type(e).__name__}", file=sys.stderr)


def yojono_viesti(tila, pvm=None, nyt=None, nahdyt=None):
    """Tehtavat, jotka epaonnistuivat tanaan. Palauttaa '' jos ei ole. Vain id, kulu ja luokiteltu syy (ei skriptin ulostuloa)."""
    pvm = pvm or _ajopaiva(nyt)
    huonot = {i: s for i, s in (tila.get("tehtavat") or {}).items()
              if isinstance(s, dict) and s.get("tila") == "epaonnistui" and s.get("pvm") == pvm}
    if nahdyt is not None:
        # vain uudet: sama tehtava + ajopaiva + yritysmaara ilmoitetaan kerran (A/B/C-ajo toistivat ennen saman halytyksen)
        avain = lambda i, s: f"{pvm}|{i}|{int(s.get('yrityksia') or 0)}"
        huonot = {i: x for i, x in huonot.items() if avain(i, x) not in nahdyt}
        nahdyt.update(avain(i, x) for i, x in huonot.items())
    if not huonot:
        return ""
    osat = []
    for i, s in sorted(huonot.items()):
        v = str(s.get("viesti") or "")
        syy = "aikakatkaisu" if v.startswith("aikakatkaisu") else "kaynnistys" if v.startswith("ajuria ei voitu") else \
            "poistumiskoodi" if v.startswith("poistumiskoodi") else "muu"
        osat.append(f"{i[:40]}({syy},{float(s.get('kulu') or 0):.2f}USD,yr={int(s.get('yrityksia') or 0)})")
    return muotoile(f"yojono {pvm}", ["yojono_tehtava_epaonnistui"], "katselmointitehtava ei tuottanut raporttia", "tehtavat: " + " ".join(osat))


AJURI_POLKU = "/opt/yojono-ajuri"   # taalta loytyy agentti.halytysportti (paivakatto 5/vrk); puuttuessa lahetetaan suoraan


def laheta(teksti, env=None):
    """Halytys Telegramiin. Portin kautta, jos agentti.halytysportti on asennettu; muuten suoraan (halytys ei saa jaada pois)."""
    env = os.environ if env is None else env
    try:
        if AJURI_POLKU not in sys.path:
            sys.path.append(AJURI_POLKU)
        from agentti import halytysportti
    except Exception:
        return _laheta_raaka(teksti, env)
    viesti = teksti if halytysportti.luokittele(teksti) == "halytys" else "HALYTYS " + teksti
    tulos = halytysportti.laheta(viesti, lambda t: _laheta_raaka(t, env))
    return tulos in (halytysportti.LAHETETTY, halytysportti.JONOON)   # jonoon = tallessa ja lahtee koosteena


def _laheta_raaka(teksti, env):
    token = env.get("TELEGRAM_TOKEN") or env.get("YOJONO_TELEGRAM_TOKEN")
    chat = env.get("TELEGRAM_CHAT_ID") or env.get("YOJONO_TELEGRAM_CHAT")
    if not token or not chat:
        print("Telegram-tunnukset puuttuvat; ilmoitusta ei lahetetty", file=sys.stderr)
        return False
    pyynto = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                    data=json.dumps({"chat_id": chat, "text": teksti}).encode(),
                                    headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pyynto, timeout=20) as v:
            return v.status == 200
    except Exception as e:                                              # vain tyypin nimi: viesti voisi sisaltaa tokenin
        print(f"Telegram-lahetys epaonnistui: {type(e).__name__}", file=sys.stderr)
        return False


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    nahdyt = None
    try:
        if len(a) == 2 and a[0] == "yksikko":
            teksti = yksikon_viesti(a[1])
        elif a == ["yojono-tila"]:
            try:
                tila = json.loads(Path(YOJONO_TILA).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                tila = {}
            nahdyt = _lue_ilmoitetut()
            teksti = yojono_viesti(tila, nahdyt=nahdyt)
        else:
            print("kaytto: virheilmoitus.py yksikko <nimi> | yojono-tila", file=sys.stderr)
            return 64
    except Exception as e:
        print(f"Viestin muodostus epaonnistui: {type(e).__name__}", file=sys.stderr)
        return 1
    if not teksti:
        return 0
    ok = laheta(teksti)
    if ok and nahdyt is not None:
        _tallenna_ilmoitetut(nahdyt)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
