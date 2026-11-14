"""Paivittainen laatuotos (Tonyn linjaus 2.11.2026 klo 16.10).

Riippumaton tarkastaja (oma `claude -p` -istunto, vain lukutyokalut Read/Grep/Glob) tarkistaa 2-3 satunnaista eilista
tuotosta lahteita vasten. Tulos kirjataan tilatiedostoon (CLAUDEJONO_TYOHAK/laatu-tila.json, ei docs-repoon, joten
ajurin polkuraja `sallittu_polku` pysyy ennallaan). Pysaytysehto: vakava virhe kahtena kalenteripaivana perakkain ->
itsesuunnittelu ja toistuvat tehtavat pysahtyvat, Telegramiin lahtee halytys. Pysaytys poistuu vain komennolla
`python3 -m claudejono vapauta` (Tony).

Komennot:
  python3 -m claudejono laatu [--pvm VVVV-KK-PP]   tarkistaa annetun (oletus: eilisen) paivan otoksen
  python3 -m claudejono mittari                    tulostaa aamukoosteen mittaririvin
  python3 -m claudejono vapauta                    poistaa pysaytyksen
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from agentti import github_luku
from claudejono import ajuri, silmukka

JONO = "claude-jono"
KEHOTE = Path(__file__).resolve().parent / "laatu-kehote.md"
OTOS_MIN, OTOS_MAX = 2, 3
TULOS_RAJA_TAVUA = 12000       # per tuotos kehotteeseen
OLETUS_PR_REPOT = ("agentti-pohja", "kauppatiimi", "mcp-doctor")
OLETUS_AIKARAJA_S = 20 * 60
OLETUS_MAX_KIERROKSET = 25
PAIVIA_TILASSA = 30
TODENNETTU_RE = re.compile(r"todennettu k.yt.ss.", re.IGNORECASE)
VALMIS_RE = re.compile(r"^- Tila:\s*(?:TEHTY|VALMIS)\s*$", re.MULTILINE)
VIRHELOKI_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}):", re.MULTILINE)
KUVAUS_MAX = 300


def eilinen(nyt):
    return (nyt.date() - timedelta(days=1)).isoformat()


def kandidaatit(repo, pvm):
    """Annetun paivan tulostiedostot (nimi alkaa paivalla), nimen mukaan jarjestettyna."""
    hak = Path(repo) / JONO / "tulokset"
    if not hak.is_dir():
        return []
    return sorted(p for p in hak.glob(f"{pvm}-*.md") if p.is_file() and p.stat().st_size > 0)


def valitse_otos(polut, rng=None):
    """2-3 satunnaista. Jos kandidaatteja on vahemman, kaikki."""
    rng = rng or random.Random()
    if len(polut) <= OTOS_MIN:
        return list(polut)
    return sorted(rng.sample(polut, rng.randint(OTOS_MIN, min(OTOS_MAX, len(polut)))))


def rakenna_kehote(otos, kehote_polku=KEHOTE):
    """Kehote: tarkastajan ohje + tarkastettavat tuotokset <tuotos>-lohkoissa (dataa, ei ohjeita)."""
    osat = [Path(kehote_polku).read_text(encoding="utf-8")]
    for p in otos:
        teksti = p.read_bytes()[:TULOS_RAJA_TAVUA].decode("utf-8", "ignore").replace("</tuotos>", "")
        osat.append(f'<tuotos nimi="{p.name}">\n{teksti}\n</tuotos>')
    return "\n\n".join(osat) + "\n"


def jasenna(teksti, odotetut):
    """Tarkastajan JSON -> [{'tulos', 'virheet': [{'vakavuus', 'kuvaus', 'todiste'}]}] tai None.

    Vakava on vain, jos vakavuus on tasmalleen 'vakava' JA todiste (lahde + lainaus) on annettu: perusteeton
    halytys ei saa pysayttaa silmukkaa. Muut virheet lasketaan lievina. Vain odotetut tuotosnimet hyvaksytaan."""
    try:
        alku, loppu = teksti.index("{"), teksti.rindex("}")
        d = json.loads(teksti[alku:loppu + 1])
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("tarkastukset"), list):
        return None
    ulos = []
    for t in d["tarkastukset"]:
        if not isinstance(t, dict) or t.get("tulos") not in odotetut or not isinstance(t.get("virheet", []), list):
            continue
        virheet = []
        for v in t.get("virheet", []):
            if not isinstance(v, dict) or not isinstance(v.get("kuvaus"), str) or not v["kuvaus"].strip():
                continue
            todiste = v.get("todiste") if isinstance(v.get("todiste"), str) else ""
            vakava = v.get("vakavuus") == "vakava" and len(todiste.strip()) >= 10
            virheet.append({"vakavuus": "vakava" if vakava else "lieva", "kuvaus": v["kuvaus"].strip()[:KUVAUS_MAX],
                            "todiste": todiste.strip()[:KUVAUS_MAX]})
        ulos.append({"tulos": t["tulos"], "virheet": virheet})
    return ulos


def lue_tila(tyohak):
    d = silmukka.lue_laatu_tila(tyohak)
    if not isinstance(d.get("paivat"), dict):
        d["paivat"] = {}
    return d


def kirjoita_tila(tyohak, d):
    paivat = d.get("paivat", {})
    for vanha in sorted(paivat)[:-PAIVIA_TILASSA]:
        del paivat[vanha]
    silmukka.tila_polku(tyohak).write_text(json.dumps(d, ensure_ascii=True, indent=1), encoding="utf-8")


def vakavia(merkinta):
    return sum(1 for t in merkinta.get("tarkastukset", []) for v in t.get("virheet", []) if v.get("vakavuus") == "vakava")


def pysaytysehto(paivat, pvm):
    """True, jos paivalla pvm ja edellisella kalenteripaivalla on molemmilla vakava virhe."""
    edellinen = (date.fromisoformat(pvm) - timedelta(days=1)).isoformat()
    return vakavia(paivat.get(pvm, {})) > 0 and vakavia(paivat.get(edellinen, {})) > 0


def kirjaa(tyohak, pvm, otos_nimet, tarkastukset, nyt, laheta=None, tila="valmis"):
    """Kirjaa paivan tulos, arvioi pysaytysehto ja halyta. Palauttaa (merkinta, pysaytettiin)."""
    d = lue_tila(tyohak)
    merkinta = {"tila": tila, "aika": nyt.isoformat(timespec="minutes"), "otos": list(otos_nimet),
                "tarkastukset": tarkastukset or []}
    merkinta["vakavia"] = vakavia(merkinta)
    merkinta["lievia"] = sum(1 for t in merkinta["tarkastukset"] for v in t["virheet"] if v["vakavuus"] != "vakava")
    d["paivat"][pvm] = merkinta
    pysayta = pysaytysehto(d["paivat"], pvm) and "pysaytetty" not in d
    if pysayta:
        d["pysaytetty"] = {"syy": f"vakava laatuvirhe kahtena paivana perakkain ({pvm} ja edellinen)",
                           "aika": nyt.isoformat(timespec="minutes")}
    kirjoita_tila(tyohak, d)
    if pysayta and laheta:
        laheta("HALYTYS itsesuunnittelu pysaytetty: laatuotos loysi vakavan virheen kahtena paivana perakkain "
               f"({pvm}). Katso {silmukka.tila_polku(tyohak)}. Vapauta: python3 -m claudejono vapauta")
    return merkinta, pysayta


def vapauta(tyohak):
    d = lue_tila(tyohak)
    oli = d.pop("pysaytetty", None) is not None
    kirjoita_tila(tyohak, d)
    return oli


def laatuotos(repo, tyohak, env, suorita=subprocess.run, nyt=None, pvm=None, rng=None, laheta=None):
    """Yksi laatuotos. Idempotentti: jo tarkastettua paivaa ei tarkisteta uudelleen. Palauttaa yhden rivin tiivistelman."""
    nyt = nyt or datetime.now().astimezone()
    pvm = pvm or eilinen(nyt)
    if lue_tila(tyohak)["paivat"].get(pvm, {}).get("tila") in ("valmis", "ei-tuotoksia"):
        return f"laatuotos {pvm}: jo tehty"
    polut = kandidaatit(repo, pvm)
    if not polut:
        kirjaa(tyohak, pvm, [], [], nyt, laheta, tila="ei-tuotoksia")
        return f"laatuotos {pvm}: ei tuotoksia"
    otos = valitse_otos(polut, rng)
    nimet = [p.name for p in otos]
    kehote = rakenna_kehote(otos)
    docs_jono = str(Path(repo) / JONO)
    kom, alaenv, cwd = ajuri.lapsen_ajo(
        kehote, env, tyohak,
        lisaliput=("--tools", "Read,Grep,Glob", "--no-session-persistence", "--add-dir", docs_jono,
                   "--add-dir", env.get("CLAUDEJONO_LAHTEET", "/opt/agentscope/tyo")))
    try:
        r = suorita(kom, cwd=cwd, capture_output=True, text=True,
                    timeout=int(env.get("CLAUDEJONO_LAATU_AIKARAJA_S", OLETUS_AIKARAJA_S)), env=alaenv, check=False)
        ok, teksti = ajuri.tulkitse(r.stdout, r.returncode)
    except subprocess.TimeoutExpired:
        ok, teksti = False, ""
    tark = jasenna(teksti, set(nimet)) if ok else None
    if tark is None:
        kirjaa(tyohak, pvm, nimet, [], nyt, laheta, tila="epaonnistui")
        return f"laatuotos {pvm}: tarkastus epaonnistui (ei kelvollista JSONia), otos {len(nimet)}"
    merkinta, pysaytettiin = kirjaa(tyohak, pvm, nimet, tark, nyt, laheta)
    return (f"laatuotos {pvm}: tarkastettu {len(nimet)}, vakavia {merkinta['vakavia']}, lievia {merkinta['lievia']}"
            + (", ITSESUUNNITTELU PYSAYTETTY" if pysaytettiin else ""))


def hae_pr_luvut(pvm, repot=OLETUS_PR_REPOT, avaa=urllib.request.urlopen):
    """(hyvaksytyt, hylatyt) PR:t, jotka suljettiin paivana pvm (UTC-paiva). None, jos haku epaonnistui.
    Vain luku -tunnus tiedostosta (5000 hakua/h), muuten julkinen API (60 hakua/h): yksi haku per repo."""
    hyv = hyl = 0
    for repo in repot:
        url = f"https://api.github.com/repos/tonyweckstrom1987/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=50"
        try:
            with avaa(github_luku.pyynto(url, "claudejono-laatu"), timeout=15) as r:
                lista = json.loads(r.read().decode("utf-8"))
        except Exception:
            return None
        for pr in lista if isinstance(lista, list) else []:
            if str(pr.get("closed_at") or "").startswith(pvm):
                if pr.get("merged_at"):
                    hyv += 1
                else:
                    hyl += 1
    return hyv, hyl


def mittaririvi(tyohak, repo, nyt=None, hae_pr=hae_pr_luvut):
    """Yksi rivi aamukoosteeseen: otoksen tulos, PR:t, uudet virhelokin merkinnat ja puuttuvat todennusrivit."""
    nyt = nyt or datetime.now().astimezone()
    pvm = eilinen(nyt)
    d = lue_tila(tyohak)
    m = d["paivat"].get(pvm)
    if m is None:
        otos = "ei tehty"
    elif m.get("tila") == "ei-tuotoksia":
        otos = "ei tuotoksia"
    elif m.get("tila") != "valmis":
        otos = "epaonnistui"
    else:
        otos = f"{len(m.get('otos', []))} tarkastettu, vakavia {m.get('vakavia', 0)}, lievia {m.get('lievia', 0)}"
    pr = hae_pr(pvm)
    pr_osa = "PR:t hyvaksytty/hylatty ?" if pr is None else f"PR:t hyvaksytty/hylatty {pr[0]}/{pr[1]}"
    try:
        loki = (Path(repo) / JONO / "virheloki.md").read_text(encoding="utf-8")
        uusia = sum(1 for p in VIRHELOKI_RE.findall(loki) if p == pvm)
        loki_osa = f"virhelokiin uusia {uusia}"
    except OSError:
        loki_osa = "virheloki ?"
    valmiit = puuttuu = 0
    for p in kandidaatit(repo, pvm):
        teksti = p.read_text(encoding="utf-8", errors="ignore")
        if VALMIS_RE.search(teksti):
            valmiit += 1
            if not TODENNETTU_RE.search(teksti):
                puuttuu += 1
    tod_osa = f"Todennettu-rivi puuttui {puuttuu}/{valmiit} valmiista"
    pysaytys = silmukka.pysaytetty(tyohak)
    return (f"Laatu ({pvm}): otos {otos}; {pr_osa}; {loki_osa}; {tod_osa}."
            + (f" ITSESUUNNITTELU PYSAYTETTY: {pysaytys}." if pysaytys else ""))


def main(argv=None):
    p = argparse.ArgumentParser(prog="claudejono")
    p.add_argument("komento", choices=["laatu", "mittari", "vapauta"])
    p.add_argument("--pvm", help="tarkastettava paiva VVVV-KK-PP (oletus: eilinen)")
    a = p.parse_args(argv)
    if a.pvm:
        try:
            date.fromisoformat(a.pvm)
        except ValueError:
            print("virheellinen --pvm", file=sys.stderr)
            return 2
    repo = Path(os.environ.get("CLAUDEJONO_REPO", "/var/lib/claude-jono/docs"))
    tyohak = Path(os.environ.get("CLAUDEJONO_TYOHAK", "/opt/agentscope"))
    if a.komento == "vapauta":
        print("pysaytys poistettu" if vapauta(tyohak) else "ei pysaytysta")
        return 0
    if a.komento == "mittari":
        print(mittaririvi(tyohak, repo))
        return 0
    try:
        from yojono.ajuri import laheta_telegram
    except Exception:
        laheta_telegram = None
    try:
        ajuri.git(repo, "pull", "-q", "--rebase", "origin", "main")
        print(laatuotos(repo, tyohak, dict(os.environ), laheta=laheta_telegram, pvm=a.pvm))
    except Exception as virhe:
        print(f"laatuotos virhe: {type(virhe).__name__}", file=sys.stderr)
        return 1
    return 0
