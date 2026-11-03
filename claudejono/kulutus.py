"""Kulutusmittari: yksi JSON-rivi per ajo tiedostoon <tila>/kulutus.jsonl (malli, tokenit, kesto, kustannusarvio).

Claude-ajot: `claude -p --output-format json` antaa usage-luvut ja total_cost_usd (API-hintainen arvo; tilauksessa se ei ole lasku
vaan suhdeluku). OpenRouter-ajot: LiteLLM:n vastauksen usage ja otsake x-litellm-response-cost (LiteLLM:n oma kirjanpito).
Kirjausvirhe ei saa kaataa ajoa. Ei salaisuuksia: rivilla on vain nimi, luvut ja tila.
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Helsinki")
KULUTUS = "kulutus.jsonl"
LAATU = "laatu.jsonl"
MIN_OTOS = 10                # alle taman ajomaaran virheprosenttia ei verrata
MERKITTAVA_ERO_PROS = 15     # halvan mallin virheprosentti vahintaan nain monta prosenttiyksikkoa korkeampi -> ehdotus


def _luku(d, avain):
    v = d.get(avain) if isinstance(d, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def claude_luvut(stdout):
    """{tokenit_in, tokenit_out, cache_luku, cache_kirjoitus, usd, api_ms, malli_todellinen} Clauden JSON-tulosteesta. Lukukelvoton -> {}."""
    try:
        d = json.loads(stdout)
    except (ValueError, TypeError):
        return {}
    if not isinstance(d, dict):
        return {}
    u = d.get("usage") if isinstance(d.get("usage"), dict) else {}
    ulos = {}
    for avain, nimi in (("input_tokens", "tokenit_in"), ("output_tokens", "tokenit_out"),
                        ("cache_read_input_tokens", "cache_luku"), ("cache_creation_input_tokens", "cache_kirjoitus")):
        if _luku(u, avain) is not None:
            ulos[nimi] = int(u[avain])
    if _luku(d, "total_cost_usd") is not None:
        ulos["usd"] = round(float(d["total_cost_usd"]), 6)
    if _luku(d, "duration_api_ms") is not None:
        ulos["api_ms"] = int(d["duration_api_ms"])
    mu = d.get("modelUsage")
    if isinstance(mu, dict) and mu:
        # Pelkka nimi (esim. claude-sonnet-5-5); jos aliagentti kaytti toista mallia, nimet erotetaan pilkulla
        ulos["malli_todellinen"] = ",".join(sorted(str(k)[:40] for k in mu))[:120]
    return ulos


def kirjaa(tila, malli, tehtava, kaista, laji, ok, kesto_s, luvut=None, nyt=None):
    """Lisaa yhden rivin. malli = Malli.tunniste (opus | sonnet | openrouter:<alias>). Palauttaa kirjatun rivin tai None."""
    nyt = nyt or datetime.now(TZ)
    rivi = {"aika": nyt.astimezone(TZ).isoformat(timespec="seconds"), "malli": malli, "tehtava": tehtava, "kaista": kaista,
            "laji": laji, "ok": bool(ok), "kesto_s": round(kesto_s, 1), **(luvut or {})}
    try:
        fd = os.open(Path(tila) / KULUTUS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(rivi, sort_keys=True) + "\n")
    except OSError:
        return None
    return rivi


def lue(tila, alku=None, loppu=None, tiedosto=KULUTUS):
    """Rivit aikavalilta [alku, loppu). Rikkinaiset rivit ohitetaan."""
    ulos = []
    try:
        teksti = (Path(tila) / tiedosto).read_text(encoding="utf-8")
    except OSError:
        return ulos
    for rivi in teksti.splitlines():
        try:
            d = json.loads(rivi)
            aika = datetime.fromisoformat(d["aika"])
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        if aika.tzinfo is None:
            aika = aika.replace(tzinfo=TZ)
        if (alku is None or aika >= alku) and (loppu is None or aika < loppu):
            d["_aika"] = aika
            ulos.append(d)
    return ulos


def malleittain(rivit):
    """{malli: {ajoja, epaonnistui, usd, tokenit_in, tokenit_out, cache}} (usd = Clauden API-arvo tai LiteLLM:n kustannus)."""
    ulos = {}
    for r in rivit:
        m = ulos.setdefault(str(r.get("malli", "?")), {"ajoja": 0, "epaonnistui": 0, "usd": 0.0, "tokenit_in": 0, "tokenit_out": 0, "cache": 0})
        m["ajoja"] += 1
        m["epaonnistui"] += 0 if r.get("ok") else 1
        m["usd"] += float(r.get("usd") or 0)
        m["tokenit_in"] += int(r.get("tokenit_in") or 0)
        m["tokenit_out"] += int(r.get("tokenit_out") or 0)
        m["cache"] += int(r.get("cache_luku") or 0) + int(r.get("cache_kirjoitus") or 0)
    return ulos


def viikon_alku(nyt):
    """Maanantai 00:00 (Helsinki)."""
    n = nyt.astimezone(TZ)
    return (n - timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _osa(nimi, m):
    teksti = f"{nimi} {m['ajoja']} ajoa"
    if m["usd"]:
        teksti += f" {m['usd']:.2f} USD"
    return teksti


def _ryhma(m):
    return "; ".join(_osa(k, v) for k, v in sorted(m.items())) or "ei ajoja"


def kulutusrivi(tila, nyt):
    """Aamukoosteen kulutusosa: eilinen kulutus malleittain + viikon kertyma. Claude-mallien USD on API-hintainen arvo, ei lasku."""
    nyt = nyt.astimezone(TZ)
    tanaan = nyt.replace(hour=0, minute=0, second=0, microsecond=0)
    eilen = malleittain(lue(tila, tanaan - timedelta(days=1), tanaan))
    viikko = malleittain(lue(tila, viikon_alku(nyt), nyt + timedelta(seconds=1)))
    return f"Mallikulutus eilen: {_ryhma(eilen)}. Viikko: {_ryhma(viikko)} (Clauden USD = API-hintainen arvo, tilauksessa ei lasku)."


# --- Laatu malleittain (tehtava 44: laatuotos kirjaa mallin) ---

def kirjaa_laatu(tila, malli, otos, virheita, nyt=None):
    """Laatuotos (tehtava 44) kutsuu tata: malli, otoksen koko ja virheiden maara. Palauttaa True, jos kirjaus onnistui."""
    nyt = nyt or datetime.now(TZ)
    if not (isinstance(otos, int) and isinstance(virheita, int) and 0 <= virheita <= otos and otos > 0):
        return False
    rivi = {"aika": nyt.astimezone(TZ).isoformat(timespec="seconds"), "malli": str(malli)[:60], "otos": otos, "virheita": virheita}
    try:
        fd = os.open(Path(tila) / LAATU, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(rivi, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def virheprosentit(tila, alku=None, loppu=None):
    """{malli: (otos, virheita, prosentti)}. Laatuotos (laatu.jsonl) ensin; jos sita ei ole, ajojen epaonnistumiset (kulutus.jsonl)."""
    laatu = {}
    for r in lue(tila, alku, loppu, tiedosto=LAATU):
        if isinstance(r.get("otos"), int) and isinstance(r.get("virheita"), int):
            a = laatu.setdefault(str(r.get("malli")), [0, 0])
            a[0] += r["otos"]
            a[1] += r["virheita"]
    if not laatu:
        laatu = {k: [v["ajoja"], v["epaonnistui"]] for k, v in malleittain(lue(tila, alku, loppu)).items()}
    return {k: (o, v, round(100 * v / o, 1)) for k, (o, v) in laatu.items() if o > 0}


def siirtoehdotus(tila, alku=None, loppu=None):
    """Viikkokatsauksen ehdotus: jos OpenRouter-mallin virheprosentti on selvasti Sonnetin yli, ehdota tehtavien siirtoa Sonnetille.
    Ei muuta mitaan itse; palauttaa tekstin tai None."""
    p = virheprosentit(tila, alku, loppu)
    sonnet = p.get("sonnet")
    if not sonnet or sonnet[0] < MIN_OTOS:
        return None
    ehdotukset = []
    for malli, (otos, _v, pros) in sorted(p.items()):
        if malli.startswith("openrouter:") and otos >= MIN_OTOS and pros - sonnet[2] >= MERKITTAVA_ERO_PROS:
            ehdotukset.append(f"{malli} virheprosentti {pros} % (otos {otos}) vs sonnet {sonnet[2]} %")
    if not ehdotukset:
        return None
    return "Ehdotus Tonylle: siirretaan halvan mallin tehtavat Sonnetille, koska " + "; ".join(ehdotukset) + "."
