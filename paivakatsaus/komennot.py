"""Telegram-komentokasittelija: /hyvaksy N ja /lista (oneshot, ajastin 2 min valein; ei pysyvaa daemonia). Vain stdlib.

Ajo: getUpdates (timeout 0, limit 20, offset arkisto/update_offset.txt) -> offset tallennetaan -> komennot kasitellaan.
Hyvaksy = vain merkinta (proposals.kasittele_komento), mitaan ei koskaan suoriteta.

TURVAPAATOKSET
- Vain viestit (update.message), joissa chat.id JA from.id (kokonaislukuja) ovat TELEGRAM_CHAT_ID, chat.type=private, lahettaja ei botti
  eika viesti ole edelleenlahetetty. Kaikki muu hylataan hiljaa (vieraille ei vastata). Komennot VAIN /hyvaksy N (N = 1-4 numeroa,
  ei etunollia) ja /lista; muu teksti ohitetaan.
- At-most-once: offset tallennetaan (atominen kirjoitus + fsync) ENNEN komentojen kasittelya. Jos prosessi keskeytyy, komentoja ei
  ajeta uudelleen (pahimmillaan komento hukkuu ja Tony lahettaa sen uudelleen); toisin pain (offset vasta kasittelyn jalkeen) sama
  komento voisi suorittua ja vastata kahdesti. Jos offsetin tallennus epaonnistuu, mitaan ei kasitella (fail closed).
- Rikkinainen/puuttuva-mutta-epaluotettava offset (ei numero, symlink, liian pitka) -> ei kasittelya. Ensiajo (tiedostoa ei ole)
  vain alustaa offsetin viimeisimman paivityksen jalkeen eika kasittele vanhoja viesteja.
- Vastaus lahtee send.laheta():lla (vastaanottaja aina TELEGRAM_CHAT_ID); lukkonimi k<update_id> estaa saman vastauksen
  kahdesti (eika kosketa paivan katsauksen .sent-lukkoa).
- Vain https://api.telegram.org, ei uudelleenohjauksia, ei ymparistoproxya, aikakatkaisu. Token ei koskaan lokiin eika virheviesteihin:
  virheista kirjataan vain kiintea tapahtumatyyppi (tai odottamaton_<Tyyppi>) ja kesto.
- Rinnakkaisajo estetty flock-lukolla (arkisto/komennot.lock).
Poistumiskoodit: 0 ok (myos ohimenevä verkko-/Telegram-virhe: seuraava kierros yrittaa uudelleen), 1 pysyva virhe, 64 kayttovirhe.
Journaliin (stderr) kirjoitetaan jokaisesta virheesta yksi rivi: vain tapahtumatyyppi, poikkeuksen tyyppi ja HTTP-tila.
"""
import fcntl
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config
import llm
import proposals
import send

OK, VIRHE, KAYTTOVIRHE = 0, 1, 64
PAIVITYKSIA_MAX = 20
KOMENTO_MAX = 32
LISTA_MAX_RIVEJA = 10
LISTA_RIVI_MAX = 140
AIKAKATKAISU = send.AIKAKATKAISU
VASTAUS_MAX = 256 * 1024
OFFSET_TIEDOSTO = "update_offset.txt"
LUKKO_TIEDOSTO = "komennot.lock"

_HYVAKSY = re.compile(r"/hyvaksy ([1-9][0-9]{0,3})", re.ASCII)
_LISTA = re.compile(r"/lista", re.ASCII)
_OFFSET = re.compile(r"[0-9]{1,15}\n?", re.ASCII)


class KomentoVirhe(Exception):
    """Ajo epaonnistui. .tapahtuma on lokiin kirjattava kiintea tyyppi; viesti ei sisalla tokenia, osoitetta eika sisaltoa."""

    def __init__(self, tapahtuma, tyyppi=None, http_tila=None, ohimeneva=False):
        super().__init__(tapahtuma)
        self.tapahtuma = tapahtuma
        self.tyyppi = tyyppi                                    # poikkeuksen luokan nimi (ei viestia)
        self.http_tila = http_tila                              # int tai None
        self.ohimeneva = ohimeneva                              # True: seuraava kierros yrittaa uudelleen, ei failed-tilaa


def luokittele_poikkeus(e):
    """(tyyppi, http_tila, ohimeneva). Ohimenevia: verkko-/aikakatkaisuvirhe, HTTP 429 ja 5xx. Muut (esim. 401, 404, rikkinainen JSON) pysyvia."""
    tyyppi = type(e).__name__
    if isinstance(e, urllib.error.HTTPError):                   # ennen URLErroria: HTTPError on sen aliluokka
        tila = e.code if isinstance(e.code, int) and not isinstance(e.code, bool) else None
        return tyyppi, tila, tila is not None and (tila == 429 or 500 <= tila <= 599)
    return tyyppi, None, isinstance(e, (urllib.error.URLError, OSError, http.client.HTTPException))


# ---------------------------------------------------------------- HTTP

class _EiUudelleenohjauksia(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                                             # 3xx -> HTTPError


def post(url, runko):
    """Bot API -kutsu: vain send.TELEGRAM_API (https://api.telegram.org), ei uudelleenohjauksia, ei proxya. Testeissa korvataan http-parametrilla."""
    if not url.startswith(send.TELEGRAM_API + "/bot"):
        raise KomentoVirhe("osoite_ei_sallittu")
    pyynto = urllib.request.Request(url, data=json.dumps(runko).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    avaaja = urllib.request.build_opener(urllib.request.ProxyHandler({}), _EiUudelleenohjauksia)
    with avaaja.open(pyynto, timeout=AIKAKATKAISU) as r:
        return json.loads(r.read(VASTAUS_MAX + 1)[:VASTAUS_MAX])


# ---------------------------------------------------------------- offset

def lue_offset(arkisto):
    """None, jos tiedostoa ei ole (ensiajo). Muu vika (symlink, ei tavallinen tiedosto, muoto) -> KomentoVirhe('offset_virhe')."""
    polku = Path(arkisto) / OFFSET_TIEDOSTO
    try:
        os.lstat(polku)
    except FileNotFoundError:
        return None
    except OSError:
        raise KomentoVirhe("offset_virhe") from None
    try:
        fd = os.open(polku, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            data = os.read(fd, 64)
        finally:
            os.close(fd)
        teksti = data.decode("ascii")
    except (OSError, UnicodeDecodeError):
        raise KomentoVirhe("offset_virhe") from None
    if not _OFFSET.fullmatch(teksti):
        raise KomentoVirhe("offset_virhe")
    return int(teksti)


def _kirjoita_offset(arkisto, arvo):
    """Atominen kirjoitus: tmp + fsync + os.replace + hakemiston fsync."""
    arkisto = Path(arkisto)
    polku = arkisto / OFFSET_TIEDOSTO
    tmp = arkisto / f".{OFFSET_TIEDOSTO}.tmp{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
        try:
            os.write(fd, f"{arvo}\n".encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, polku)
        dfd = os.open(arkisto, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def tallenna_offset(arkisto, arvo):
    try:
        _kirjoita_offset(arkisto, arvo)
    except OSError:
        raise KomentoVirhe("offset_virhe") from None


# ---------------------------------------------------------------- paivitykset

def _hae(a, offset, limit, http):
    url = f"{send.TELEGRAM_API}/bot{a.telegram_token}/getUpdates"
    runko = {"offset": offset, "limit": limit, "timeout": 0, "allowed_updates": ["message"]}
    try:
        vastaus = (http or post)(url, runko)
    except Exception as e:
        tyyppi, tila, ohimeneva = luokittele_poikkeus(e)        # ei poikkeuksen sisaltoa: URL sisaltaa tokenin
        raise KomentoVirhe("haku_virhe", tyyppi, tila, ohimeneva) from None
    if not isinstance(vastaus, dict) or vastaus.get("ok") is not True or not isinstance(vastaus.get("result"), list):
        raise KomentoVirhe("haku_virhe")
    tulos = []
    for p in vastaus["result"]:
        uid = p.get("update_id") if isinstance(p, dict) else None
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise KomentoVirhe("haku_virhe")                    # rikkinainen vastaus: ei kasittelya, offset ei liiku
        tulos.append(p)
    tulos.sort(key=lambda p: p["update_id"])
    return tulos


def _on_omistaja(x, a):
    return isinstance(x, int) and not isinstance(x, bool) and str(x) == a.telegram_chat_id


def poimi_komento(paivitys, a):
    """('hyvaksy', n) | ('lista', None) | None. Palauttaa komennon VAIN omistajan yksityisviestista (chat.id JA from.id)."""
    m = paivitys.get("message")
    if not isinstance(m, dict):
        return None
    chat, frm, teksti = m.get("chat"), m.get("from"), m.get("text")
    if not isinstance(chat, dict) or not isinstance(frm, dict) or not isinstance(teksti, str):
        return None
    if chat.get("type") != "private" or frm.get("is_bot") is True:
        return None
    if not _on_omistaja(chat.get("id"), a) or not _on_omistaja(frm.get("id"), a):
        return None
    if any(k in m for k in ("forward_origin", "forward_from", "forward_from_chat", "forward_date", "via_bot")):
        return None
    if len(teksti) > KOMENTO_MAX:
        return None
    t = teksti.strip()
    h = _HYVAKSY.fullmatch(t)
    if h:
        return ("hyvaksy", int(h.group(1)))
    if _LISTA.fullmatch(t):
        return ("lista", None)
    return None


# ---------------------------------------------------------------- komennot

def _hyvaksy(n, a, nyt, hakemisto, loki):
    vastaus = proposals.kasittele_komento(a.telegram_chat_id, f"/hyvaksy {n}", asetukset=a, nyt=nyt, hakemisto=hakemisto, loki=loki)
    if vastaus is None:
        return "Komentoa ei voitu suorittaa (ks. loki)"
    if vastaus.startswith("Hyvaksytty"):
        return vastaus
    try:
        tila = {e["n"]: e["tila"] for e in proposals.lue_kaikki(hakemisto)[0]}.get(n)
    except Exception:
        tila = None
    if tila == "APPROVED":
        return f"Ehdotus {n} on jo hyvaksytty (mitaan ei tehty uudelleen)"
    if tila == "EXPIRED":
        return f"Ehdotus {n} on vanhentunut (yli {proposals.VANHENEMISRAJA_PAIVIA} pv) eika sita voi hyvaksya"
    return f"Ei avointa ehdotusta numerolla {n}"


def _lista(nyt, hakemisto):
    proposals.vanhenna(nyt, hakemisto)
    avoimet = proposals.avoimet(hakemisto)
    if not avoimet:
        return "Ei avoimia ehdotuksia"
    rivit = []
    for e in avoimet[:LISTA_MAX_RIVEJA]:
        teksti = llm.neutraloi_linkit(" ".join(str(e["teksti"]).split()))[:LISTA_RIVI_MAX]
        rivit.append(f"{e['n']}. {teksti}")
    if len(avoimet) > LISTA_MAX_RIVEJA:
        rivit.append(f"... ja {len(avoimet) - LISTA_MAX_RIVEJA} muuta")
    return ("Avoimet ehdotukset (/hyvaksy N):\n" + "\n".join(rivit))[:config.VIESTI_MAX_MERKKEJA]


def _kasittele(paivitys, a, nyt, hakemisto, loki):
    """Palauttaa vastausteksti tai None (ei vastata)."""
    k = poimi_komento(paivitys, a)
    if k is None:
        return None
    if k[0] == "hyvaksy":
        return _hyvaksy(k[1], a, nyt, hakemisto, loki)
    return _lista(nyt, hakemisto)


# ---------------------------------------------------------------- ajo

def _kirjaa(loki, aika, tapahtuma, kesto):
    rivi = f"{aika.isoformat(timespec='seconds')} komennot tapahtuma={tapahtuma} kesto_s={kesto:.2f}\n"
    try:
        fd = os.open(loki, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, re.sub(r"[^A-Za-z0-9_=. :+\-\n]", "?", rivi).encode("ascii"))
        finally:
            os.close(fd)
    except OSError:
        pass


def _journaali(tapahtuma, tyyppi, http_tila):
    """Yksi rivi stderriin (systemd-journal): vain tapahtuma, poikkeuksen tyyppi ja HTTP-tila. Ei viestia, osoitetta eika tokenia."""
    osat = [f"komennot virhe tapahtuma={tapahtuma}"]
    if tyyppi:
        osat.append(f"tyyppi={tyyppi}")
    if isinstance(http_tila, int) and not isinstance(http_tila, bool):
        osat.append(f"http={http_tila}")
    rivi = re.sub(r"[^A-Za-z0-9_=. \-]", "?", " ".join(osat))
    try:
        print(rivi, file=sys.stderr, flush=True)
    except OSError:
        pass


def _ota_lukko(arkisto):
    """fd tai None, jos toinen ajo on kesken. Muu vika -> KomentoVirhe."""
    try:
        Path(arkisto).mkdir(parents=True, exist_ok=True)
        fd = os.open(Path(arkisto) / LUKKO_TIEDOSTO, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o640)
    except OSError:
        raise KomentoVirhe("lukko_virhe") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError:
        os.close(fd)
        raise KomentoVirhe("lukko_virhe") from None
    return fd


def _ajo(env, nyt, arkisto, hakemisto, loki, http, viive):
    try:
        a = config.lue_asetukset(env)
    except config.AsetusVirhe as e:
        print(f"Asetusvirhe: {e}", file=sys.stderr)                 # nimeaa vain muuttujat
        raise KomentoVirhe("asetus_virhe") from None
    fd = _ota_lukko(arkisto)
    if fd is None:
        return "lukko_varattu"
    try:
        offset = lue_offset(arkisto)
        if offset is None:                                          # ensiajo: alusta, ala kasittele vanhoja
            viimeisin = _hae(a, -1, 1, http)
            tallenna_offset(arkisto, viimeisin[-1]["update_id"] + 1 if viimeisin else 0)
            return "alustus"
        paivitykset = [p for p in _hae(a, offset, PAIVITYKSIA_MAX, http) if p["update_id"] >= offset][:PAIVITYKSIA_MAX]
        if not paivitykset:
            return "tyhja"
        tallenna_offset(arkisto, paivitykset[-1]["update_id"] + 1)  # ENNEN kasittelya: at-most-once
        lahetysvirhe = False
        for p in paivitykset:
            vastaus = _kasittele(p, a, nyt, hakemisto, loki)
            if vastaus is None:
                continue
            try:
                send.laheta(vastaus, asetukset=a, http=http or post, arkisto=arkisto, nyt=nyt, viive=viive, lukko_nimi=f"k{p['update_id']}")
            except (send.SendVirhe, OSError):
                lahetysvirhe = True                                 # offset on jo edennyt; jatka seuraavaan
        if lahetysvirhe:
            raise KomentoVirhe("lahetys_virhe")
        return "ok"
    finally:
        os.close(fd)


def ajo(*, env=None, nyt=None, arkisto=None, hakemisto=None, loki=None, http=None, viive=send.VIIVE):
    """Palauttaa poistumiskoodin (0 ok, 1 virhe). Lokiin: vain tapahtumatyyppi ja kesto. BaseException (esim. keskeytys) ei nielaista."""
    alku = time.monotonic()
    arkisto = arkisto or config.ARKISTO
    hakemisto = hakemisto or config.EHDOTUKSET
    loki = loki or config.LOKI
    aika = (nyt or datetime.now(timezone.utc)).astimezone(ZoneInfo(config.PAIVA_TZ))
    try:
        tapahtuma, koodi = _ajo(env, nyt or aika, arkisto, hakemisto, loki, http, viive), OK
    except KomentoVirhe as e:
        tapahtuma, koodi = e.tapahtuma, OK if e.ohimeneva else VIRHE
        if e.ohimeneva:
            tapahtuma += "_ohimeneva"
        _journaali(tapahtuma, e.tyyppi, e.http_tila)
    except Exception as e:
        tapahtuma, koodi = "odottamaton_" + type(e).__name__, VIRHE
        _journaali(tapahtuma, type(e).__name__, None)
    _kirjaa(loki, aika, tapahtuma, time.monotonic() - alku)
    return koodi


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print("Kaytto: komennot.py (ei argumentteja)", file=sys.stderr)
        return KAYTTOVIRHE
    return ajo(env=os.environ)


if __name__ == "__main__":
    sys.exit(main())
