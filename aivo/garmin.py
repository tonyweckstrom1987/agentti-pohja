"""Aivo: Tonyn Garmin-datan paivittainen haku isannalle (yksityinen).

Ajetaan Proxmox-isannalla omana kayttajana `aivo` (ks. docs/aivo-garmin.md). Komennot:

  kirjaudu  Tony ajaa kerran kasin: kysyy tunnukset interaktiivisesti (getpass), tallentaa vain
            kirjautumistunnisteen (kirjasto kirjoittaa sen tilassa 600 hakemistoon 700). Salasanaa
            ei tallenneta eika sita voi antaa komentorivilla tai ymparistomuuttujassa.
  hae       Hakee eilisen ja taman paivan tiedot ja kirjoittaa ne tiedostoihin
            <data>/garmin-VVVV-KK-PP.jsonl (yksi rivi per tietolaji). Ei koskaan kysy tunnuksia.
  viikko    Kirjoittaa edellisen ISO-viikon tiivistelman kirjastoon; jos kirjasto on git-repo,
            commit ja push (vain muuhun kuin GitHubiin).
  ajo       hae + maanantaisin viikko (systemd-ajastin).

Tila: <data>/tila.json sisaltaa vain tilan, ajan, paivamaarat, rivimaarat ja virhetyypit,
ei terveysarvoja. Se viedaan CT115:lle aamukoostetta varten (aamukooste_osa).

Lokiin (stdout) tulostetaan vain rivimaarat, paivamaarat ja virheiden tyyppinimet.
Kolmannen osapuolen kirjasto (garminconnect) tuodaan vasta kirjaudu- ja hae-komennoissa,
joten testit ja aamukooste toimivat ilman sita."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

OLETUS_DATA = "/var/lib/aivo/garmin"
OLETUS_TUNNISTE = "/var/lib/aivo/garmin-kirjautuminen"
OLETUS_KIRJASTO = "/var/lib/aivo/kirjasto"
OLETUS_TILA_CT115 = "/var/lib/aivo-tila/garmin-tila.json"
TAUKO_S = 1.0                    # kutsujen valinen tauko (Garminin rajoitukset)
TILA_VANHA_H = 36                # aamukooste: tila vanhempi kuin taman -> halytys
KIRJAUDU_VIRHEET = ("GarminConnectAuthenticationError",)
LOPETA_VIRHEET = KIRJAUDU_VIRHEET + ("GarminConnectTooManyRequestsError",)

# Tietolaji -> (metodi, argumentit paivasta). Metodit ovat garminconnect.Garmin-luokan (0.3.20).
LAJIT = (
    ("yhteenveto", "get_user_summary", lambda p: (p,)),          # askeleet, leposyke, stressi, Body Battery paivatasolla
    ("uni", "get_sleep_data", lambda p: (p,)),
    ("stressi", "get_stress_data", lambda p: (p,)),
    ("body_battery", "get_body_battery", lambda p: (p, p)),
    ("askeleet", "get_daily_steps", lambda p: (p, p)),
    ("treenit", "get_activities_by_date", lambda p: (p, p)),
    ("leposyke", "get_rhr_day", lambda p: (p,)),
    ("hrv", "get_hrv_data", lambda p: (p,)),
)


def _nyt():
    return datetime.now(timezone.utc)


def _varmista_hakemisto(polku):
    """Luo hakemiston tilassa 700 ja korjaa tilan, jos hakemisto oli jo olemassa."""
    p = Path(polku)
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def _kirjoita_atomisesti(polku, teksti, tila=0o600):
    polku = Path(polku)
    fd, tmp = tempfile.mkstemp(dir=polku.parent, prefix="." + polku.name + ".")
    try:
        os.fchmod(fd, tila)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(teksti)
        os.replace(tmp, polku)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def hae_paiva(asiakas, paiva, nukahda=time.sleep, tauko=TAUKO_S):
    """Hakee yhden paivan kaikki lajit. Palauttaa (rivit, virheet, lopeta).

    rivit: lista {paiva, laji, haettu, data}; virheet: lista "laji:Tyyppi".
    lopeta: virhetyypin nimi, jos haku pitaa keskeyttaa (kirjautuminen vanhentunut, liikaa kutsuja)."""
    rivit, virheet = [], []
    for i, (laji, metodi, argit) in enumerate(LAJIT):
        if i:
            nukahda(tauko)
        try:
            data = getattr(asiakas, metodi)(*argit(paiva))
        except Exception as virhe:   # vain tyyppi talteen: viesti voi sisaltaa tunnisteita tai terveysdataa
            tyyppi = type(virhe).__name__
            virheet.append(f"{laji}:{tyyppi}")
            if tyyppi in LOPETA_VIRHEET:
                return rivit, virheet, tyyppi
            continue
        rivit.append({"paiva": paiva, "laji": laji, "haettu": _nyt().isoformat(timespec="seconds"), "data": data})
    return rivit, virheet, None


def tallenna_paiva(hakemisto, paiva, rivit):
    """Kirjoittaa paivan tiedoston kokonaan uudelleen (eilinen taydentyy seuraavassa ajossa). Tyhja -> ei kirjoiteta."""
    if not rivit:
        return None
    hak = _varmista_hakemisto(hakemisto)
    polku = hak / f"garmin-{paiva}.jsonl"
    _kirjoita_atomisesti(polku, "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rivit))
    return polku


def kirjoita_tila(hakemisto, tila, paivat, virheet, nyt=None):
    """Tilatiedosto aamukoostetta varten: ei terveysarvoja, vain tila, paivat, rivimaarat ja virhetyypit."""
    hak = _varmista_hakemisto(hakemisto)
    sisalto = {"tila": tila, "aika": (nyt or _nyt()).isoformat(timespec="seconds"),
               "paivat": {p: int(n) for p, n in paivat.items()}, "virheet": [str(v)[:80] for v in virheet][:20]}
    # 644: tiedosto viedaan CT115:lle (pct push), eika se sisalla terveysdataa; hakemisto itse on 700.
    _kirjoita_atomisesti(hak / "tila.json", json.dumps(sisalto, ensure_ascii=False, sort_keys=True) + "\n", tila=0o644)
    return sisalto


def hae(asiakas, hakemisto, tanaan, nukahda=time.sleep):
    """Hakee eilisen ja taman paivan. Palauttaa tilan sisallon (dict)."""
    paivat, virheet, tila = {}, [], "ok"
    for paiva in ((tanaan - timedelta(days=1)).isoformat(), tanaan.isoformat()):
        rivit, v, lopeta = hae_paiva(asiakas, paiva, nukahda=nukahda)
        virheet += v
        if tallenna_paiva(hakemisto, paiva, rivit):
            paivat[paiva] = len(rivit)
        if lopeta:
            tila = "kirjaudu" if lopeta in KIRJAUDU_VIRHEET else "virhe"
            break
    if tila == "ok" and not paivat:
        tila = "virhe"
    return kirjoita_tila(hakemisto, tila, paivat, virheet)


# --- viikkotiivistelma -------------------------------------------------------------------------

def _syva(d, *avaimet):
    for a in avaimet:
        if not isinstance(d, dict):
            return None
        d = d.get(a)
    return d


# Tiivistelman tunnusluvut: (otsikko, laji, polku raakadatassa). Kenttien nimet tarkistetaan ensimmaisesta oikeasta
# ajosta; puuttuva kentta nakyy viivana eika kaada tiivistelmaa.
TUNNUSLUVUT = (
    ("askeleet", "yhteenveto", ("totalSteps",)),
    ("leposyke", "yhteenveto", ("restingHeartRate",)),
    ("stressi (ka)", "yhteenveto", ("averageStressLevel",)),
    ("Body Battery max", "yhteenveto", ("bodyBatteryHighestValue",)),
    ("Body Battery min", "yhteenveto", ("bodyBatteryLowestValue",)),
    ("uni (h)", "uni", ("dailySleepDTO", "sleepTimeSeconds")),
    ("unipisteet", "uni", ("dailySleepDTO", "sleepScores", "overall", "value")),
    ("HRV yo (ka)", "hrv", ("hrvSummary", "lastNightAvg")),
)


def lue_paiva(hakemisto, paiva):
    polku = Path(hakemisto) / f"garmin-{paiva}.jsonl"
    ulos = {}
    try:
        for rivi in polku.read_text(encoding="utf-8").splitlines():
            if rivi.strip():
                r = json.loads(rivi)
                ulos[r.get("laji")] = r.get("data")
    except (OSError, ValueError):
        pass
    return ulos


def viikon_tiivistelma(hakemisto, maanantai):
    """Markdown-tiivistelma ISO-viikosta, joka alkaa `maanantai`. Sisaltaa terveysarvoja: vain isannalle ja Giteaan."""
    vuosi, viikko, _ = maanantai.isocalendar()
    rivit = [f"# Garmin-viikko {vuosi}-W{viikko:02d}", "",
             "L\u00e4hde: Aivon Garmin-haku. Yksityinen: ei GitHubiin.", "",
             "| p\u00e4iv\u00e4 | " + " | ".join(o for o, _, _ in TUNNUSLUVUT) + " | treenej\u00e4 |",
             "|---" * (len(TUNNUSLUVUT) + 2) + "|"]
    for i in range(7):
        p = (maanantai + timedelta(days=i)).isoformat()
        d = lue_paiva(hakemisto, p)
        arvot = []
        for otsikko, laji, polku in TUNNUSLUVUT:
            a = _syva(d.get(laji), *polku)
            if isinstance(a, (int, float)) and otsikko == "uni (h)":
                a = round(a / 3600, 1)
            arvot.append("-" if a is None else str(a))
        treenit = d.get("treenit")
        arvot.append(str(len(treenit)) if isinstance(treenit, list) else "-")
        rivit.append(f"| {p} | " + " | ".join(arvot) + " |")
    return "\n".join(rivit) + "\n"


def _git(kirjasto, *argit, suorita=subprocess.run):
    return suorita(["git", "-C", str(kirjasto), *argit], capture_output=True, text=True, timeout=120)


def julkaise_kirjastoon(kirjasto, polku, viesti, suorita=subprocess.run):
    """Commit ja push, jos kirjasto on git-repo. Kieltaytyy, jos jokin etarepo osoittaa GitHubiin."""
    if not (Path(kirjasto) / ".git").exists():
        return "vain is\u00e4nt\u00e4 (kirjasto ei ole git-repo)"
    etat = _git(kirjasto, "remote", "-v", suorita=suorita).stdout.lower()
    if "github" in etat:
        return "estetty: kirjaston et\u00e4repo osoittaa GitHubiin"
    _git(kirjasto, "add", "--", str(polku), suorita=suorita)
    r = _git(kirjasto, "commit", "-q", "-m", viesti, suorita=suorita)
    if r.returncode != 0:
        return "ei muutoksia"
    if not etat.strip():
        return "commit (ei et\u00e4repoa)"
    r = _git(kirjasto, "push", "-q", suorita=suorita)
    return "push ok" if r.returncode == 0 else f"push ep\u00e4onnistui (paluukoodi {r.returncode})"


def viikko(hakemisto, kirjasto, tanaan, suorita=subprocess.run):
    maanantai = tanaan - timedelta(days=tanaan.weekday() + 7)
    vuosi, nro, _ = maanantai.isocalendar()
    hak = _varmista_hakemisto(Path(kirjasto) / "garmin")
    polku = hak / f"{vuosi}-W{nro:02d}.md"
    _kirjoita_atomisesti(polku, viikon_tiivistelma(hakemisto, maanantai))
    return polku, julkaise_kirjastoon(kirjasto, polku, f"garmin {vuosi}-W{nro:02d}", suorita=suorita)


# --- aamukooste (CT115) ------------------------------------------------------------------------

def aamukooste_osa(polku, nyt):
    """(tarvitaan, halytys) aamukoosteeseen tilatiedostosta; kumpikin None, jos ei sanottavaa.

    Puuttuva tiedosto -> (None, None): hakua ei ole viela otettu kayttoon."""
    try:
        t = json.loads(Path(polku).read_text(encoding="utf-8"))
        aika = datetime.fromisoformat(t["aika"])
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, KeyError, TypeError):
        return None, "Garmin-tilaa ei saatu luettua"
    if t.get("tila") == "kirjaudu":
        return "Garmin: kirjaudu uudelleen is\u00e4nn\u00e4ll\u00e4 (docs/aivo-garmin.md, kohta Kirjautuminen)", None
    if aika.tzinfo is None:
        aika = aika.replace(tzinfo=timezone.utc)
    if nyt - aika > timedelta(hours=TILA_VANHA_H):
        return None, f"Garmin-haku ei ole p\u00e4ivittynyt {TILA_VANHA_H} tuntiin"
    if t.get("tila") != "ok":
        return None, "Garmin-haku ep\u00e4onnistui (" + ", ".join(t.get("virheet", [])[:2]) + ")"
    return None, None


# --- komentorivi -------------------------------------------------------------------------------

def _asiakas_tunnisteella(tunniste):
    from garminconnect import Garmin   # vasta isannalla: kolmannen osapuolen kirjasto
    asiakas = Garmin()
    asiakas.login(tunniste)            # ilman tunnuksia: vanhentunut tunniste -> GarminConnectAuthenticationError
    return asiakas


def kirjaudu(tunniste, syote=input):
    """Tony ajaa kasin. Tunnukset kysytaan interaktiivisesti; vain kirjautumistunniste tallennetaan."""
    import getpass
    if not sys.stdin.isatty():
        print("kirjaudu: aja interaktiivisessa paatteessa (tunnuksia ei lueta putkesta).", file=sys.stderr)
        return 2
    from garminconnect import Garmin
    _varmista_hakemisto(tunniste)
    sahkoposti = syote("Garmin-s\u00e4hk\u00f6posti: ").strip()
    salasana = getpass.getpass("Garmin-salasana (ei n\u00e4y): ")
    try:
        asiakas = Garmin(sahkoposti, salasana, prompt_mfa=lambda: syote("MFA-koodi: ").strip())
        asiakas.login(tunniste)
    except Exception as virhe:
        print(f"kirjaudu: ep\u00e4onnistui ({type(virhe).__name__})", file=sys.stderr)
        return 1
    finally:
        salasana = None
    tiedosto = Path(tunniste) / "garmin_tokens.json"
    tila = oct(tiedosto.stat().st_mode & 0o777) if tiedosto.exists() else "puuttuu"
    print(f"kirjaudu: ok, tunniste tallennettu ({tila})")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Aivon Garmin-haku (yksityinen, isannalla)")
    p.add_argument("komento", choices=("kirjaudu", "hae", "viikko", "ajo"))
    p.add_argument("--data", default=OLETUS_DATA)
    p.add_argument("--tunniste", default=OLETUS_TUNNISTE)
    p.add_argument("--kirjasto", default=OLETUS_KIRJASTO)
    p.add_argument("--paiva", help="VVVV-KK-PP (oletus: tanaan, paikallinen aika)")
    a = p.parse_args(argv)
    os.umask(0o077)
    tanaan = date.fromisoformat(a.paiva) if a.paiva else date.today()
    if a.komento == "kirjaudu":
        return kirjaudu(a.tunniste)
    koodi = 0
    if a.komento in ("hae", "ajo"):
        try:
            asiakas = _asiakas_tunnisteella(a.tunniste)
        except Exception as virhe:
            tyyppi = type(virhe).__name__
            kirjoita_tila(a.data, "kirjaudu" if tyyppi in KIRJAUDU_VIRHEET else "virhe", {}, [f"kirjautuminen:{tyyppi}"])
            print(f"hae: kirjautuminen ep\u00e4onnistui ({tyyppi})", file=sys.stderr)
            return 2
        t = hae(asiakas, a.data, tanaan)
        print(f"hae: tila={t['tila']} rivit={t['paivat']} virheet={len(t['virheet'])} {t['virheet'][:5]}")
        koodi = 0 if t["tila"] == "ok" else 2
    if a.komento == "viikko" or (a.komento == "ajo" and tanaan.weekday() == 0):
        polku, tulos = viikko(a.data, a.kirjasto, tanaan)
        print(f"viikko: {polku.name}: {tulos}")
    return koodi


if __name__ == "__main__":
    sys.exit(main())
