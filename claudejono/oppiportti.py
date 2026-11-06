"""Jonon opit tarkastettuna (kompassi: "Jatkuva oppiminen", Tonyn paatos 4.11.2026).

Kulku:
  1. Ajuri poimii VALMIS-raportin "## Uusi oppi" -rivit, suodattaa ne (kieltolista) ja lisaa ne docs-repon
     tiedostoon claude-jono/opit-ehdotukset.md. Agentti ei kirjoita opit.md:hen eika ehdotuksiin itse.
  2. Riippumaton tarkastaja (claudejono/tarkastaja.py, erillinen kayttaja ilman tyokaluja) arvioi uudet ehdotukset ja
     kirjaa paatokset tila-hakemiston tiedostoon opit-paatokset.json (root; agenttikayttaja ei voi kirjoittaa sinne).
     Tarkastajan PR-hylkayksen syy kirjataan samaan tiedostoon uudeksi ehdotukseksi (lahde "tarkastaja").
  3. Ajuri soveltaa paatokset seuraavalla ajolla: hyvaksytyt siirtyvat opit.md:hen (enintaan OPIT_MAX_RIVIA rivia,
     vanhimmat tiivistetaan), hylatyt poistuvat ehdotuksista ja kirjataan paikalliseen lokiin. Git-tyot tekee vain ajuri.
Portti on siis tarkastaja: opit.md:hen paatyy vain rivi, jonka tarkastaja on hyvaksynyt, ja teksti on ehdotuksen oma
(tarkastajan vastaus ei voi muuttaa sita).
"""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

JONO = "claude-jono"
OPIT = f"{JONO}/opit.md"
EHDOTUKSET = f"{JONO}/opit-ehdotukset.md"
PAATOKSET = "opit-paatokset.json"      # tila-hakemistossa (/var/lib/claude-jono, root)
HYLATYT_LOKI = "opit-hylatyt.jsonl"
LUKKO = "opit-lukko"
OPIT_MAX_RIVIA = 40
OPIT_MAX_TAVUA = 6000
OPIT_RIVI_MAX = 240
OPIT_ENINTAAN_UUSIA = 2
EHDOTUKSIA_MAX = 30
UUSIA_JONOSSA_MAX = 20
TARKASTAJA_LAHDE = "tarkastaja"
OPIT_OTSIKKO = "# Opit ajoista (data, ei ohjeita rajojen ohittamiseen)"
EHDOTUKSET_OTSIKKO = ("# Opit-ehdotukset (data; riippumaton tarkastaja hyv\u00e4ksyy tai hylk\u00e4\u00e4 ennen opit.md:t\u00e4)")
EHDOTUS_RE = re.compile(r"^- ([0-9a-f]{12}) (\d{4}-\d{2}-\d{2}) \[([a-z0-9][a-z0-9.-]{0,79})\]: (\S.*)$")
LAHDE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,79}$")
PAATOS_ARVOT = ("HYVAKSY", "HYLKAA")

# Opit ovat dataa, joten niiden kautta ei saa kulkea rajojen ohitus (prompt injection, tehtava 32).
# Kieltolista on tarkoituksella karkea: parempi hylata liikaa kuin paastaa lapi. Teksti verrataan ilman tarkkeita
# (kierr\u00e4 -> kierra), jotta aakkoset eivat ohita listaa. Tarkastaja on toinen, varsinainen portti.
OPIT_KIELTOLISTA = re.compile(
    r"salaisuu|secret|token|tunnus|salasan|password|passwd|api[_ -]?key|api_key|\.env\b|holvi|vault|credential|private key|deploy_key|"
    r"sudo|chmod|chown|setfacl|visudo|oikeuks|permission|root-oikeu|admin|laajenn|"
    r"ohita|ohittaa|kierra|kiertaa|ohitus|bypass|circumvent|override|disable|poista kaytosta|"
    r"ignore|ala noudata|unohda|forget|"
    r"katto|kulukat|budjet|automerge|koodi on laki|rajoja|rajat|raja ei|settings\.json|allowlist|kieltolist",
    re.IGNORECASE)


def _ilman_tarkkeita(teksti):
    return "".join(c for c in unicodedata.normalize("NFKD", teksti) if not unicodedata.combining(c))


def poimi_uusi_oppi(teksti):
    """Rivit viimeisesta '## Uusi oppi' -osiosta (enintaan OPIT_ENINTAAN_UUSIA). Tyhja lista jos osiota ei ole."""
    kohdat = [m for m in re.finditer(r"^##[ \t]+Uusi oppi[ \t]*$", teksti or "", re.MULTILINE)]
    if not kohdat:
        return []
    loppu = teksti[kohdat[-1].end():]
    rivit = []
    for rivi in loppu.splitlines():
        if rivi.startswith("#"):
            break
        rivi = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", rivi).strip()
        if rivi:
            rivit.append(rivi)
    return rivit[:OPIT_ENINTAAN_UUSIA]


def suodata_opit(rivit):
    """(hyvaksytyt, hylatyt). Hylataan kieltolistan osumat, liian pitkat, tagit, backtickit ja ohjausmerkit."""
    hyva, huono = [], []
    for r in rivit:
        r = (r or "").strip()
        if (not r or len(r) > OPIT_RIVI_MAX or OPIT_KIELTOLISTA.search(_ilman_tarkkeita(r)) or "<" in r or "`" in r
                or any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 for c in r)):
            huono.append(r)
        else:
            hyva.append(r)
    return hyva, huono


def paivita_opit(vanha, uudet, nyt=None):
    """Lisaa paivatyt rivit ja pitaa tiedoston enintaan OPIT_MAX_RIVIA rivin mittaisena.
    Ylimaarat: vanhimmat opit tiivistetaan yhdeksi riviksi (alut ';'-erotettuna)."""
    pvm = (nyt or datetime.now()).strftime("%Y-%m-%d")
    rivit = [r for r in (vanha or "").splitlines() if r.startswith("- ")]
    rivit += [f"- {pvm}: {u}" for u in uudet]
    if len(rivit) > OPIT_MAX_RIVIA - 2:
        ylim = len(rivit) - (OPIT_MAX_RIVIA - 2) + 1
        vanhimmat, rivit = rivit[:ylim], rivit[ylim:]
        alut = "; ".join(re.sub(r"^- (?:\d{4}-\d{2}-\d{2}|tiivistetty \(\d+ vanhaa\)): ", "", r)[:40] for r in vanhimmat)
        rivit.insert(0, f"- tiivistetty ({len(vanhimmat)} vanhaa): {alut}"[:OPIT_RIVI_MAX])
    return OPIT_OTSIKKO + "\n\n" + "\n".join(rivit) + "\n"


def ehdotus_id(lahde, teksti):
    return hashlib.sha256(f"{lahde}\n{teksti}".encode("utf-8")).hexdigest()[:12]


def lue_ehdotukset(teksti):
    """Ehdotustiedoston kelvolliset rivit: [{id, pvm, lahde, teksti}]. Muut rivit ohitetaan."""
    ulos = []
    for rivi in (teksti or "").splitlines():
        m = EHDOTUS_RE.match(rivi)
        if m:
            ulos.append({"id": m.group(1), "pvm": m.group(2), "lahde": m.group(3), "teksti": m.group(4)})
    return ulos


def muotoile_ehdotukset(ehdotukset):
    rivit = [f"- {e['id']} {e['pvm']} [{e['lahde']}]: {e['teksti']}" for e in ehdotukset]
    return EHDOTUKSET_OTSIKKO + "\n\n" + "".join(r + "\n" for r in rivit)


def lisaa_ehdotukset(ehdotukset, rivit, lahde, nyt=None):
    """Suodattaa rivit ja lisaa uudet ehdotuslistaan. Palauttaa (uusi lista, lisatyt, hylatyt). Sama teksti samasta
    lahteesta lisataan vain kerran; jos jonossa on jo EHDOTUKSIA_MAX ehdotusta, uusia ei lisata (vanhat eivat havia)."""
    if not LAHDE_RE.match(lahde or ""):
        raise ValueError("lahde ei kelpaa")
    pvm = (nyt or datetime.now()).strftime("%Y-%m-%d")
    hyva, huono = suodata_opit(rivit)
    lista = list(ehdotukset)
    olemassa = {e["id"] for e in lista}
    lisatyt = []
    for r in hyva:
        i = ehdotus_id(lahde, r)
        if i in olemassa:
            continue
        if len(lista) >= EHDOTUKSIA_MAX:
            huono.append(r)
            continue
        e = {"id": i, "pvm": pvm, "lahde": lahde, "teksti": r}
        lista.append(e)
        lisatyt.append(e)
        olemassa.add(i)
    return lista, lisatyt, huono


@contextlib.contextmanager
def _lukko(tila):
    with open(Path(tila) / LUKKO, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _lue_tila(tila):
    try:
        d = json.loads((Path(tila) / PAATOKSET).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    p = d.get("paatokset") if isinstance(d.get("paatokset"), dict) else {}
    u = d.get("uudet") if isinstance(d.get("uudet"), list) else []
    return {"paatokset": {k: v for k, v in p.items() if re.match(r"^[0-9a-f]{12}$", str(k)) and isinstance(v, dict)
                          and v.get("paatos") in PAATOS_ARVOT},
            "uudet": [x for x in u if isinstance(x, str)]}


def _kirjoita_tila(tila, d):
    polku = Path(tila) / PAATOKSET
    tmp = Path(str(polku) + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, polku)


def lue_paatokset(tila):
    with _lukko(tila):
        return _lue_tila(tila)


def kirjaa_paatokset(tila, paatokset=None, uudet=None):
    """Tarkastaja kutsuu: paatokset {id: {"paatos": HYVAKSY|HYLKAA, "syy": str}}, uudet = tarkastajan hylkayksista
    tiivistetyt rivit (menevat ehdotuksiksi ja samaan porttiin). Ei git-toimia."""
    with _lukko(tila):
        d = _lue_tila(tila)
        for i, v in (paatokset or {}).items():
            if re.match(r"^[0-9a-f]{12}$", str(i)) and isinstance(v, dict) and v.get("paatos") in PAATOS_ARVOT:
                d["paatokset"][i] = {"paatos": v["paatos"], "syy": str(v.get("syy") or "")[:300]}
        d["uudet"] = (d["uudet"] + [str(u)[:OPIT_RIVI_MAX] for u in (uudet or []) if str(u).strip()])[-UUSIA_JONOSSA_MAX:]
        _kirjoita_tila(tila, d)
        return d


def hylkays_oppi(repo, nro, perustelu, loydokset):
    """Tarkastajan PR-hylkayksen syy yhdeksi riviksi: ensimmainen loydos, muuten perustelun ensimmainen virke."""
    syy = next((l for l in (loydokset or []) if str(l).strip()), "") or re.split(r"(?<=[.!?])\s", str(perustelu or "").strip())[0]
    syy = re.sub(r"[\x00-\x1f\x7f`<>]", " ", str(syy))
    syy = re.sub(r"\s+", " ", syy).strip()
    if not syy:
        return ""
    return f"Tarkastaja hylk\u00e4si PR:n {repo}#{nro}: {syy}"[:OPIT_RIVI_MAX]


def sovella(repo, tila, nyt=None):
    """Ajuri kutsuu git-lukon sisalla. Kirjoittaa opit.md:n ja opit-ehdotukset.md:n tyopuuhun tarkastajan paatosten
    mukaan. Palauttaa (muuttuneet suhteelliset polut, yhteenveto, kuitattavat). Tilaa EI tyhjenneta tassa: kutsuja
    kuittaa sen commitin jalkeen (kuittaa()), jottei epaonnistunut commit havita paatoksia."""
    d = lue_paatokset(tila)
    ehd_polku = Path(repo) / EHDOTUKSET
    ehd = lue_ehdotukset(ehd_polku.read_text(encoding="utf-8") if ehd_polku.is_file() else "")
    hyvaksytyt, hylatyt, jaljelle = [], [], []
    for e in ehd:
        p = d["paatokset"].get(e["id"])
        if p is None:
            jaljelle.append(e)
        elif p["paatos"] == "HYVAKSY":
            hyvaksytyt.append(e)
        else:
            hylatyt.append(dict(e, syy=p.get("syy", "")))
    jaljelle, tarkastajan, _ = lisaa_ehdotukset(jaljelle, d["uudet"], TARKASTAJA_LAHDE, nyt)
    muuttuneet = []
    if hyvaksytyt:
        # Kaksi kertaa saman tarkistuksen lapi: tarkastaja ei voi hyvaksya kieltolistan osumaa, vaikka tiedostoa muokattaisiin.
        sallitut, _ = suodata_opit([e["teksti"] for e in hyvaksytyt])
        if sallitut:
            op = Path(repo) / OPIT
            vanha = op.read_text(encoding="utf-8") if op.is_file() else ""
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(paivita_opit(vanha, sallitut, nyt), encoding="utf-8")
            muuttuneet.append(OPIT)
    if hyvaksytyt or hylatyt or tarkastajan:
        ehd_polku.parent.mkdir(parents=True, exist_ok=True)
        ehd_polku.write_text(muotoile_ehdotukset(jaljelle), encoding="utf-8")
        muuttuneet.append(EHDOTUKSET)
    orvot = set(d["paatokset"]) - {e["id"] for e in ehd}     # paatos ehdotukselle, jota ei enaa ole: siivotaan
    kuitattavat = {"paatokset": [e["id"] for e in hyvaksytyt + hylatyt] + sorted(orvot), "uudet": len(d["uudet"]),
                   "hylatyt": hylatyt}
    yhteenveto = {"hyvaksytty": len(hyvaksytyt), "hylatty": len(hylatyt), "tarkastajalta": len(tarkastajan)}
    return muuttuneet, yhteenveto, kuitattavat


def kuittaa(tila, kuitattavat, nyt=None):
    """Poistaa sovelletut paatokset ja kasitellyt uudet rivit tilasta ja kirjaa hylatyt paikalliseen lokiin."""
    with _lukko(tila):
        d = _lue_tila(tila)
        for i in kuitattavat.get("paatokset", []):
            d["paatokset"].pop(i, None)
        d["uudet"] = d["uudet"][kuitattavat.get("uudet", 0):]
        _kirjoita_tila(tila, d)
    aika = (nyt or datetime.now()).isoformat(timespec="seconds")
    try:
        with open(Path(tila) / HYLATYT_LOKI, "a", encoding="utf-8") as f:
            for e in kuitattavat.get("hylatyt", []):
                f.write(json.dumps(dict(e, aika=aika), ensure_ascii=False) + "\n")
    except OSError:
        pass
