"""Telegramin halytysportti (Tonyn linjaukset A62 ja A427, 5.11.2026).

Saanto: Telegramiin paasee vain halytysluokan viesti. Kaikki muu (aamukooste, tulokset, raportit, valmis-ilmoitukset)
kuuluu docs-repoon; portti hylkaa ne ja laskee ne. Halytyksia lahtee enintaan PAIVAKATTO kappaletta vuorokaudessa
(Suomen aikaa). Kun kattoon on jaljella yksi paikka, se kaytetaan halytykselle, johon liitetaan maininta ylityksesta.
Sen jalkeen tulevat halytykset kootaan jonoon ja lahtevat seuraavan vuorokauden ensimmaisessa porttikutsussa yhtena viestina.

Halytys = viesti, jonka ensimmainen rivi alkaa sanalla HALYTYS tai [VIRHE]: vakava (raha, julkinen, data), palvelu
kaatunut yli tunniksi tai jono seisoo yli 2 h. Lahettajan vastuulla on antaa etuliite vain tahan luokkaan.

Turva: tila on paikallinen JSON-tiedosto (ei salaisuuksia), vastaanottaja ja tunnukset kuuluvat varsinaiselle lahettajalle.
Jos tilaa ei voi lukea tai kirjoittaa, halytys lahetetaan silti (hiljaa jaava halytys on pahempi kuin ylimaarainen).
Vain stdlib."""
import fcntl
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
PAIVAKATTO = 5
HALYTYS_ALUT = ("HALYTYS", "H\u00c4LYTYS", "[VIRHE]")
JONO_MAX = 30          # kootun jonon pituus; ylimenevat vain lasketaan
RIVI_MAX = 150         # yhden jonotetun halytyksen pituus kootussa viestissa
OLETUS_TILA = "/var/lib/claude-jono/halytysportti.json"
TILA_ENV = "HALYTYSPORTTI_TILA"

LAHETETTY, JONOON, OHJATTU, EPAONNISTUI = "lahetetty", "jonoon", "ohjattu", "epaonnistui"


def luokittele(teksti):
    """'halytys' tai 'muu'. Ratkaisee vain ensimmaisen ei-tyhjan rivin alku (isot/pienet kirjaimet eivat merkitse)."""
    for rivi in str(teksti).splitlines():
        rivi = rivi.strip()
        if rivi:
            return "halytys" if rivi.upper().startswith(HALYTYS_ALUT) else "muu"
    return "muu"


def _pvm(nyt):
    return datetime.fromtimestamp(nyt, TZ).strftime("%Y-%m-%d")


def _tyhja(pvm):
    return {"pvm": pvm, "lahetetty": 0, "ohjattu": 0, "jono": [], "jonossa_yli": 0}


def _lue(polku, pvm):
    try:
        with open(polku, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("jono"), list):
            raise ValueError
        d.setdefault("lahetetty", 0)
        d.setdefault("ohjattu", 0)
        d.setdefault("jonossa_yli", 0)
        return d
    except (OSError, ValueError):
        return _tyhja(pvm)


def _kirjoita(polku, d):
    try:
        tmp = f"{polku}.tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, polku)
        return True
    except OSError:
        return False


def _lyhyt(teksti):
    rivi = next((r.strip() for r in str(teksti).splitlines() if r.strip()), "")
    return rivi[:RIVI_MAX]


def _kooste(jono, yli):
    rivit = [f"HALYTYS eilisen ylitys: {len(jono) + yli} halytysta jai paivakaton vuoksi koosteeseen"]
    rivit += [f"- {r}" for r in jono]
    if yli:
        rivit.append(f"- (+{yli} muuta, ks. journalctl)")
    return "\n".join(rivit)


class _Lukko:
    def __init__(self, polku):
        self.polku = polku + ".lock"
        self.f = None

    def __enter__(self):
        try:
            self.f = open(self.polku, "a")
            fcntl.flock(self.f, fcntl.LOCK_EX)
        except OSError:
            self.f = None
        return self

    def __exit__(self, *a):
        if self.f:
            self.f.close()


def _vaihda_vuorokausi(d, pvm, laheta_fn):
    """Uusi vuorokausi: nollaa laskurit. Eilisen jono lahtee ensimmaisena viestina (laskee uuden paivan kattoon);
    jos lahetys epaonnistuu, jono sailyy seuraavaa yritysta varten."""
    if d.get("pvm") == pvm:
        return d
    uusi = _tyhja(pvm)
    if d["jono"]:
        if laheta_fn(_kooste(d["jono"], d.get("jonossa_yli", 0))):
            uusi["lahetetty"] = 1
        else:
            uusi["jono"], uusi["jonossa_yli"] = d["jono"], d.get("jonossa_yli", 0)
    return uusi


def laheta(teksti, laheta_fn, tila_polku=None, nyt=None, paivakatto=PAIVAKATTO):
    """Ainoa sallittu reitti Telegramiin. laheta_fn(teksti) -> bool on varsinainen lahetin.
    Palauttaa 'lahetetty' | 'jonoon' (katto taynna, koottu) | 'ohjattu' (ei halytys, kuuluu docs-repoon) | 'epaonnistui'."""
    nyt = time.time() if nyt is None else nyt
    polku = tila_polku or os.environ.get(TILA_ENV) or OLETUS_TILA
    pvm = _pvm(nyt)
    halytys = luokittele(teksti) == "halytys"
    with _Lukko(polku):
        d = _vaihda_vuorokausi(_lue(polku, pvm), pvm, laheta_fn)
        if not halytys:
            d["ohjattu"] += 1
            _kirjoita(polku, d)
            return OHJATTU
        if d["lahetetty"] >= paivakatto:
            if len(d["jono"]) < JONO_MAX:
                d["jono"].append(_lyhyt(teksti))
            else:
                d["jonossa_yli"] += 1
            _kirjoita(polku, d)
            return JONOON
        viesti = str(teksti)
        if d["lahetetty"] == paivakatto - 1:
            viesti += "\n(Paivakatto %d taynna: seuraavat halytykset kootaan yhdeksi viestiksi huomenna.)" % paivakatto
        if not laheta_fn(viesti):
            _kirjoita(polku, d)
            return EPAONNISTUI
        d["lahetetty"] += 1
        _kirjoita(polku, d)   # kirjausvirhe ei esta: lahetys on jo tehty
        return LAHETETTY


def huuhtele(laheta_fn, tila_polku=None, nyt=None):
    """Lahettaa edellisen vuorokauden jonon (jos on) ilman uutta halytysta, esim. aamukoosteesta. Palauttaa True, jos jono lahti."""
    nyt = time.time() if nyt is None else nyt
    polku = tila_polku or os.environ.get(TILA_ENV) or OLETUS_TILA
    pvm = _pvm(nyt)
    with _Lukko(polku):
        d = _lue(polku, pvm)
        if d.get("pvm") == pvm or not d["jono"]:
            return False
        uusi = _vaihda_vuorokausi(d, pvm, laheta_fn)
        _kirjoita(polku, uusi)
        return not uusi["jono"]
