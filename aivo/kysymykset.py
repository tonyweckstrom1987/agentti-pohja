"""Aivon oma haastattelu: viikoittainen kysymyslista ja vastausten luku (A502, A616-A618).

Ajetaan isannalla kayttajana `aivo` (ks. docs/aivo-kysymykset.md). Komennot:

  laadi    Kay lapi kirjaston (muisti ja paatokset), etsii ohuimmat kohdat ja ristiriidat ja
           kirjoittaa kysymyslistan <ulos>/YYYY-MM-DD.md. Sama aihe kysytaan vain kerran (A140).
  lue      Lukee vastaustiedostot <vastaukset>/YYYY-MM-DD.md ja kirjaa vastaukset kirjaston
           merkintoihin (paivitetty-kentta paivittyy).
  tarkista Takautuva testi (A577): onko jokainen luettu vastaus kirjattu muistiin 7 pv sisalla.

Tietosuoja (A329): listaan paatyy vain kysymyksia aiheista, joiden front matterissa on
`luokka: yleinen`. Kaikki muu (luokka puuttuu tai on jokin muu) on henkilokohtainen ja nakyy
listassa vain tunnisteella "henkilokohtainen aihe N". Lisaksi yleisen aiheen nimi tarkistetaan
arkaluonteisten sanojen varalta; osuma tekee aiheesta henkilokohtaisen. Valmis lista
tarkistetaan viela kerran ennen kirjoitusta. Kirjaston sisaltoa ei kopioida listaan, vain aiheen nimi.
Lokiin tulostetaan vain lukumaarat ja virheiden tyyppinimet."""
import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

OLETUS_KIRJASTO = "/var/lib/aivo/kirjasto"
OLETUS_ULOS = "/var/lib/aivo/kysymykset-ulos"
OLETUS_VASTAUKSET = "/var/lib/aivo/vastaukset"
OLETUS_TILA = "/var/lib/aivo/kysymykset-tila.json"
MALLIT = Path(__file__).with_name("kysymysmallit.json")

OHUT_MERKKEJA = 200          # runko alle taman -> ohut
VANHA_PV = 180               # paivitetty yli taman verran sitten -> ohut
MAX_YLEISIA = 5
MAX_HENKILOKOHTAISIA = 3
KIRJAUS_PV = 7               # A577: vastaus kirjattava muistiin viikon sisalla
AIHE_SALLITTU = re.compile(r"^[\w .,/+()-]{2,60}$")

# Arkaluonteiset sanat (taivutusrungot, vertailu ilman aakkosia). Osuma -> aihe on henkilokohtainen.
ARKA = ("terve", "sairau", "laak", "diagnoo", "kipu", "masenn", "ahdist", "mielen", "terapi",
        "perhe", "aiti", "isa ", "veli", "sisko", "vanhem", "lapsi", "puoliso", "tyttoyst", "poikayst",
        "seurustel", "suhde", "ystava", "kaveri", "raha", "talou", "tili", "velka", "palkk", "tulot",
        "vero", "laina", "tunne", "pelko", "suru", "viha", "yksinai", "uni", "stressi", "syke", "paino",
        "garmin", "body battery", "hrv", "salasana", "tunnus", "avain", "token")


def _laskos(teksti):
    """Pienet kirjaimet ja \u00e4\u00e4kkoset pois, jotta vertailu on ymmarrettava ja ASCII-kestava."""
    t = unicodedata.normalize("NFKD", str(teksti).lower())
    return "".join(c for c in t if not unicodedata.combining(c))


_ARKA_LASKOS = tuple(_laskos(s) for s in ARKA)


def on_arka(teksti):
    t = _laskos(teksti) + " "
    return any(s in t for s in _ARKA_LASKOS)


def _nyt():
    return datetime.now(timezone.utc)


def _paiva(s):
    try:
        return date.fromisoformat(str(s).strip())
    except ValueError:
        return None


def lue_merkinta(polku):
    """Palauttaa {'polku','kentat','runko'} tai None. Front matter: rivit 'avain: arvo' valilla ---."""
    try:
        teksti = Path(polku).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    rivit = teksti.split("\n")
    if not rivit or rivit[0].strip() != "---":
        return None
    kentat, i = {}, 1
    while i < len(rivit) and rivit[i].strip() != "---":
        if ":" in rivit[i]:
            k, v = rivit[i].split(":", 1)
            kentat[k.strip().lower()] = v.strip()
        i += 1
    if i >= len(rivit):
        return None
    return {"polku": Path(polku), "kentat": kentat, "runko": "\n".join(rivit[i + 1:]).strip()}


def lue_kirjasto(kirjasto):
    kaikki = []
    for p in sorted(Path(kirjasto).rglob("*.md")):
        if ".git" in p.parts or p.is_symlink():
            continue
        m = lue_merkinta(p)
        if m and m["kentat"].get("aihe"):
            kaikki.append(m)
    return kaikki


def _tunniste(aihe):
    return re.sub(r"[^a-z0-9]+", "-", _laskos(aihe)).strip("-")[:50] or "aihe"


def _yleinen(m):
    return (m["kentat"].get("luokka", "").lower() == "yleinen"
            and all(AIHE_SALLITTU.match(k) and not on_arka(k)
                    for k in (m["kentat"]["aihe"], m["kentat"].get("avain") or m["kentat"]["aihe"])))


def etsi_ehdokkaat(merkinnat, tanaan):
    """Palauttaa lista (paino, tyyppi, aihe-avain, aihe, merkinnat). Ristiriidat ensin, sitten ohuimmat."""
    ehdokkaat, ryhmat = [], {}
    for m in merkinnat:
        ryhmat.setdefault(m["kentat"].get("avain") or m["kentat"]["aihe"], []).append(m)
    for avain, ms in ryhmat.items():
        kannat = {m["kentat"].get("kanta", "").lower() for m in ms if m["kentat"].get("kanta")}
        if len(kannat) > 1:
            ehdokkaat.append((0, "ristiriita", avain, ms[0]["kentat"]["aihe"], ms))
            continue
        for m in ms:
            pv = _paiva(m["kentat"].get("paivitetty", ""))
            vanha = pv is None or (tanaan - pv).days > VANHA_PV
            if len(m["runko"]) < OHUT_MERKKEJA or vanha:
                ehdokkaat.append((1 + len(m["runko"]) / OHUT_MERKKEJA, "ohut", avain, m["kentat"]["aihe"], [m]))
                break
    ehdokkaat.sort(key=lambda e: (e[0], e[2]))
    return ehdokkaat


def _tila_lue(polku):
    try:
        t = json.loads(Path(polku).read_text(encoding="utf-8"))
        if isinstance(t, dict):
            return t
    except (OSError, ValueError):
        pass
    return {}


def _kirjoita(polku, teksti, tila=0o600):
    polku = Path(polku)
    polku.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(polku.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(teksti)
        os.chmod(tmp, tila)
        os.replace(tmp, polku)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _tarkista_lista(teksti):
    """Viimeinen portti: kysymyksista ei saa loytya arkaluonteisia sanoja, paitsi mallien oma
    'Henkilokohtainen aihe'-rivi, joka kayttaa vain tunnistetta."""
    for rivi in teksti.split("\n"):
        if re.match(r"^\d+\. \*\*K\d+\*\* \(henkilokohtainen-aihe-\d+\): Henkil\u00f6kohtainen aihe \d+:", rivi):
            continue
        if re.match(r"^\d+\. \*\*K\d+\*\*", rivi) and on_arka(rivi):
            raise ValueError("ArkaluonteinenSana")


def laadi(kirjasto, ulos, tila_polku, tanaan=None):
    """Kirjoittaa listan. Palauttaa (polku tai None, lukumaarat)."""
    tanaan = tanaan or _nyt().date()
    mallit = json.loads(MALLIT.read_text(encoding="utf-8"))
    tila = _tila_lue(tila_polku)
    kysytty = tila.setdefault("kysytty", {})
    listat = tila.setdefault("listat", {})
    tiedosto = Path(ulos) / f"{tanaan.isoformat()}.md"
    if tanaan.isoformat() in listat and tiedosto.exists():
        return tiedosto, {"uusia": 0, "kommentti": "lista on jo laadittu"}
    yleiset, henk = [], []
    for _, tyyppi, avain, aihe, ms in etsi_ehdokkaat(lue_kirjasto(kirjasto), tanaan):
        if _tunniste(avain) in kysytty:
            continue                                     # A140: kysy kerran
        (yleiset if all(_yleinen(m) for m in ms) else henk).append((tyyppi, avain, aihe, ms))
    yleiset, henk = yleiset[:MAX_YLEISIA], henk[:MAX_HENKILOKOHTAISIA]
    if not yleiset and not henk:
        return None, {"uusia": 0, "kommentti": "ei uusia kysyttavia"}
    rivit = [f"# Aivon kysymykset {tanaan.isoformat()}", "",
             "Ohje Claudelle: esit\u00e4 yleiset kysymykset sellaisenaan. Henkil\u00f6kohtaisista aiheista kysy ensin Tonylta, "
             "saako aiheesta kysy\u00e4, \u00e4l\u00e4k\u00e4 arvaile aiheen sis\u00e4lt\u00f6\u00e4. Vastaukset: yleiset docs-repoon tiedostoon "
             f"`vastaukset/{tanaan.isoformat()}.md` (otsikko `## K1` jne.), henkil\u00f6kohtaiset Giteaan Tonyn kautta.", ""]
    kuvaus, n_henk = [], 0
    nro = 0
    for tyyppi, avain, aihe, ms in yleiset:
        nro += 1
        rivit.append(f"{nro}. **K{nro}** ({_tunniste(aihe)}): " + mallit[tyyppi].format(aihe=aihe))
        kuvaus.append({"k": f"K{nro}", "avain": _tunniste(avain), "henkilokohtainen": False,
                       "polut": [str(m["polku"]) for m in ms]})
        kysytty[_tunniste(avain)] = tanaan.isoformat()
    for tyyppi, avain, aihe, ms in henk:
        nro += 1
        n_henk += 1
        rivit.append(f"{nro}. **K{nro}** (henkilokohtainen-aihe-{n_henk}): " + mallit["henkilokohtainen"].format(n=n_henk))
        kuvaus.append({"k": f"K{nro}", "avain": _tunniste(avain), "henkilokohtainen": True,
                       "polut": [str(m["polku"]) for m in ms]})
        kysytty[_tunniste(avain)] = tanaan.isoformat()
    teksti = "\n".join(rivit) + "\n"
    _tarkista_lista(teksti)
    _kirjoita(tiedosto, teksti, 0o644)
    listat[tanaan.isoformat()] = {"kysymykset": kuvaus, "vastaukset": {}}
    _kirjoita(tila_polku, json.dumps(tila, ensure_ascii=False, indent=1))
    return tiedosto, {"uusia": nro, "yleisia": len(yleiset), "henkilokohtaisia": n_henk}


def jasenna_vastaukset(teksti):
    """'## K1' -otsikoiden alla oleva teksti -> {'K1': teksti}."""
    tulos, nyk = {}, None
    for rivi in teksti.split("\n"):
        m = re.match(r"^##\s+(K\d+)\s*$", rivi)
        if m:
            nyk = m.group(1)
            tulos[nyk] = []
        elif nyk:
            tulos[nyk].append(rivi)
    return {k: "\n".join(v).strip() for k, v in tulos.items() if "\n".join(v).strip()}


def _paivita_merkinta(polku, paiva, vastaus):
    """Lisaa vastauksen merkinnan loppuun ja asettaa paivitetty-kentan. Vain kirjaston sisalle."""
    teksti = Path(polku).read_text(encoding="utf-8")
    uusi, n = re.subn(r"(?m)^paivitetty:.*$", f"paivitetty: {paiva}", teksti, count=1)
    if n == 0:
        uusi = re.sub(r"\A---\n", f"---\npaivitetty: {paiva}\n", teksti, count=1)
    uusi = uusi.rstrip("\n") + f"\n\n## Haastattelu {paiva}\n\n{vastaus}\n"
    _kirjoita(polku, uusi, 0o600)


def lue(kirjasto, vastaukset, tila_polku, tanaan=None):
    tanaan = tanaan or _nyt().date()
    tila = _tila_lue(tila_polku)
    juuri = Path(kirjasto).resolve()
    kirjattu = 0
    for pv, lista in tila.get("listat", {}).items():
        tiedosto = Path(vastaukset) / f"{pv}.md"
        if not tiedosto.exists():
            continue
        try:
            vast = jasenna_vastaukset(tiedosto.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        for kys in lista["kysymykset"]:
            k = kys["k"]
            if k not in vast or (lista["vastaukset"].get(k) or {}).get("muistiin"):
                continue
            tieto = lista["vastaukset"].setdefault(k, {"nahty": tanaan.isoformat(), "muistiin": None})
            if vast[k].strip().lower() in ("ei", "ei saa", "ei saa kysya", "ei saa kysy\u00e4"):
                tieto["muistiin"] = tanaan.isoformat()
                tieto["huomio"] = "lupaa ei annettu"
                continue
            for p in kys["polut"]:
                if Path(p).resolve().is_relative_to(juuri) and Path(p).exists():
                    _paivita_merkinta(p, tanaan.isoformat(), vast[k])
                    tieto["muistiin"] = tanaan.isoformat()
                    kirjattu += 1
                    break
    _kirjoita(tila_polku, json.dumps(tila, ensure_ascii=False, indent=1))
    return kirjattu


def tarkista(tila_polku, tanaan=None):
    """A577: palauttaa rivit (teksti) ja ok-lipun. Myohassa = nahty yli 7 pv sitten eika kirjattu."""
    tanaan = tanaan or _nyt().date()
    tila = _tila_lue(tila_polku)
    rivit, ok = [], True
    for pv, lista in sorted(tila.get("listat", {}).items()):
        vastattu = lista.get("vastaukset", {})
        for kys in lista["kysymykset"]:
            v = vastattu.get(kys["k"])
            if v is None:
                rivit.append(f"{pv} {kys['k']}: ei vastausta")
            elif v["muistiin"]:
                rivit.append(f"{pv} {kys['k']}: kirjattu {v['muistiin']}")
            elif (tanaan - _paiva(v["nahty"])).days > KIRJAUS_PV:
                rivit.append(f"{pv} {kys['k']}: MYOHASSA (vastaus nahty {v['nahty']})")
                ok = False
            else:
                rivit.append(f"{pv} {kys['k']}: odottaa kirjausta")
    return rivit, ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("komento", choices=("laadi", "lue", "tarkista"))
    ap.add_argument("--kirjasto", default=OLETUS_KIRJASTO)
    ap.add_argument("--ulos", default=OLETUS_ULOS)
    ap.add_argument("--vastaukset", default=OLETUS_VASTAUKSET)
    ap.add_argument("--tila", default=OLETUS_TILA)
    a = ap.parse_args(argv)
    try:
        if a.komento == "laadi":
            polku, tiedot = laadi(a.kirjasto, a.ulos, a.tila)
            print(f"laadi: {tiedot} {polku or ''}".strip())
        elif a.komento == "lue":
            print(f"lue: kirjattu {lue(a.kirjasto, a.vastaukset, a.tila)} merkintaa")
        else:
            rivit, ok = tarkista(a.tila)
            print("\n".join(rivit) or "ei listoja")
            return 0 if ok else 1
    except Exception as e:                                   # vain tyyppinimi (voi sisaltaa dataa)
        print(f"virhe: {type(e).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
