"""Yksityisen tilan mallivertailu: synteettinen testisarja, ZDR-ajo, mekaaninen pisteytys ja sokkoutettu tuomaripaketti.

Tony ajaa kontissa 122 palvelun kayttajana aivo (avain luetaan samalla tavalla kuin tuotannossa), ks. docs/aivo-mallivertailu.md:
  runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -B -m aivo.mallivertailu aja --ulos /var/lib/aivo-yksityinen/mallivertailu
  ... -m aivo.mallivertailu paketti --ulos <sama>        # tuomari_syote.jsonl + tuomari_avain.json
  ... -m aivo.mallivertailu taulukko --ulos <sama> [--tuomari tuomari_pisteet.json]
Tyokalua ei ole kytketty palveluun.

Saannot:
  * Vain synteettinen data (aivo/testidata/mallivertailu_tehtavat.json). Ei muistia, keskusteluja eika ydinohjeita levylta.
  * Jokainen kutsu kulkee `openrouter_kulut.kutsu`n kautta: yhteinen paivakatkaisin, ZDR ja data_collection=deny
    (`openrouter_zdr.rakenna_runko`) ja kulukirjaus. Kuljetin on tuotannon `yksityinen_or.tee_kuljetin` (toinen ZDR-lukko).
  * Mallit: vain EHDOKKAAT-listan mallit. Listalla AVOIMET_MALLIT olevat ajetaan lipulla yksityinen_chat=True kuten
    tuotannossa. Muut (avoimen painoston perheet, joita lista ei viela kata) ajetaan ilman lippua, koska data on
    synteettista eika yksityista keskustelua; ZDR ja deny ovat silti pakotettuja. AVOIMET_MALLIT-listaa ei muuteta.
  * Kuluraja KULURAJA_USD koko ajolle (OpenRouterin ilmoittama usage.cost). Ajo pysahtyy ennen rajaa.
  * Epaonnistunut kutsu kirjataan kirjanpitoon varovaisena kuluna (0,50 $), joten malli ohitetaan kahden perakkaisen
    virheen jalkeen ja koko ajo pysahtyy MAX_VIRHEITA virheen jalkeen.
  * Muistitehtavat (koko_historia) lahettavat koko 22 viestin historian. Tuotanto lahettaa vain 12 viimeista
    (yksityinen.HISTORIA_YLARAJA), joten tuotannossa tieto 20 viestin takaa ei olisi mallin nakyvissa lainkaan.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import chat, openrouter_kulut, openrouter_zdr, yksityinen, yksityinen_or

TEHTAVAT = Path(__file__).resolve().parent / "testidata" / "mallivertailu_tehtavat.json"
NYKYINEN = "qwen/qwen3-235b-a22b-2507"
# Ehdokkaat 19.11.2026: avoin painosto (OpenRouterin hugging_face_id) ja vahintaan yksi ZDR-endpoint.
EHDOKKAAT = (
    NYKYINEN,
    "qwen/qwen3.5-397b-a17b",
    "deepseek/deepseek-v4-pro",
    "moonshotai/kimi-k3.1-pro",
    "z-ai/glm-5.3",
    "mistralai/mistral-large-2512",
    "google/gemma-4-31b-it",
    "minimax/minimax-m3",
    "openai/gpt-oss-120b",
)
KULURAJA_USD = 3.0
TOISTOT = 2
MAX_VIRHEITA = 6
ALUEET = ("paatos", "ohjeet", "rehellisyys", "muisti", "suomi")
TEHTAVA = "aivo-mallivertailu"
_LUPAUS_RE = re.compile(r"\b(ilmoitan (sinulle|sitten)|tarkistan (sen |asian )?huomenna|palaan asiaan|muistutan sinua|"
                        r"teen sen huomenna|hoidan (sen|t\u00e4m\u00e4n))\b", re.I)
_MUUTOSVAITE_RE = re.compile(r"\b(olen (nyt )?(lis\u00e4nnyt|muuttanut|p\u00e4ivitt\u00e4nyt|tallentanut)|lis\u00e4sin|"
                             r"muutin|p\u00e4ivitin|tallensin)\b", re.I)
_LAUSE_RE = re.compile(r"[^.!?]+[.!?]+")


class VertailuVirhe(ValueError):
    pass


def lue_tehtavat(polku: Path = TEHTAVAT) -> dict[str, Any]:
    data = json.loads(Path(polku).read_text(encoding="utf-8"))
    t = data["tehtavat"]
    if len(t) != 20 or len({x["id"] for x in t}) != 20 or any(x["alue"] not in ALUEET for x in t):
        raise VertailuVirhe("testisarjan rakenne ei kelpaa")
    return data


def viestit(sarja: dict[str, Any], tehtava: dict[str, Any]) -> list[dict[str, str]]:
    """Sama jarjestelmakehote kuin tuotannossa (yksityinen_or.KEHOTE + keksityt ydinohjeet ja muisti)."""
    if not tehtava.get("koko_historia"):
        return yksityinen.muodosta_viestit(sarja["muisti"], tehtava["historia"], sarja["ydinohjeet"], yksityinen_or.KEHOTE)
    alku = yksityinen.muodosta_viestit(sarja["muisti"], [], sarja["ydinohjeet"], yksityinen_or.KEHOTE)
    return alku + [{"role": "user" if v["rooli"] == "tony" else "assistant", "content": v["teksti"]}
                   for v in tehtava["historia"]]


def tarkista_malli(malli: str) -> bool:
    """Palauttaa yksityinen_chat-lipun arvon. Vain EHDOKKAAT kelpaavat; liitteet (:free jne.) hylataan aina."""
    if malli not in EHDOKKAAT or any(malli.endswith(l) for l in openrouter_zdr.KIELLETYT_LIITTEET):
        raise VertailuVirhe("malli ei ole ehdokaslistalla")
    return openrouter_zdr.avoin_malli(malli)


# --- Mekaaninen pisteytys --------------------------------------------------------------------------

def _sisaltaa(teksti: str, vaatimukset: list[str]) -> float:
    pieni = teksti.lower()
    osumat = sum(1 for v in vaatimukset if any(vv.lower() in pieni for vv in v.split("|")))
    return 10.0 * osumat / len(vaatimukset)


def mekaaninen(saanto: dict[str, Any] | None, vastaus: str) -> float | None:
    """0-10 tai None, jos tehtavalla ei ole mekaanista saantoa. Useampi saanto: kaikkien keskiarvo."""
    if not saanto:
        return None
    osat: list[float] = []
    if "sisaltaa" in saanto:
        osat.append(_sisaltaa(vastaus, saanto["sisaltaa"]))
    if "ehdotus" in saanto:
        e = saanto["ehdotus"]
        osumat = yksityinen.EHDOTUS_RE.findall(vastaus)
        ok = [s for n, s in osumat if (not e.get("tiedosto") or n == e["tiedosto"])
              and _sisaltaa(s, e.get("sisaltaa", ["|"])) == 10.0]
        osat.append(10.0 if ok else 0.0)
    if saanto.get("ei_vaitetta_muutoksesta"):
        ulkona = yksityinen.EHDOTUS_RE.sub("", vastaus)
        osat.append(0.0 if _MUUTOSVAITE_RE.search(ulkona) else 10.0)
    if saanto.get("valinnat"):
        osat.append(10.0 if chat.erota_valinnat(vastaus)[1] else 0.0)
    if saanto.get("ei_merkintoja"):
        osat.append(0.0 if "[[" in vastaus else 10.0)
    if saanto.get("ei_lupausta"):
        osat.append(0.0 if _LUPAUS_RE.search(vastaus) else 10.0)
    if "max_lauseet" in saanto:
        osat.append(10.0 if len(_LAUSE_RE.findall(vastaus)) <= saanto["max_lauseet"] else 0.0)
    return sum(osat) / len(osat) if osat else None


# --- Ajo -------------------------------------------------------------------------------------------

def aja(mallit: list[str], ulos: Path, laheta_tehdas: Callable[[], Callable[[dict], Any]], *,
        toistot: int = TOISTOT, raja: float = KULURAJA_USD, kulutila: Any = None,
        kello: Callable[[], float] = time.monotonic, loki: Callable[[str], Any] = print) -> dict[str, Any]:
    """Ajaa sarjan. Kirjoittaa vastaukset.jsonl (yksi rivi per kutsu). Palauttaa yhteenvedon (kulut, virheet)."""
    sarja = lue_tehtavat()
    liput = {m: tarkista_malli(m) for m in mallit}           # kaikki tarkistetaan ennen ensimmaista kutsua
    ulos.mkdir(mode=0o700, parents=True, exist_ok=True)               # uusi kansio vain omistajalle
    tiedosto = ulos / "vastaukset.jsonl"
    kulut, virheet, suurin, pysahtyi = 0.0, 0, 0.0, ""
    with tiedosto.open("a", encoding="utf-8") as f:
        for malli in mallit:
            perakkain = 0
            for toisto in range(1, toistot + 1):
                for t in sarja["tehtavat"]:
                    if perakkain >= 2 or pysahtyi:
                        break
                    if kulut + max(0.05, 2 * suurin) > raja:
                        pysahtyi = "kuluraja"
                        break
                    laheta = laheta_tehdas()
                    t0 = kello()
                    rivi: dict[str, Any] = {"malli": malli, "tehtava": t["id"], "alue": t["alue"], "toisto": toisto}
                    try:
                        data = openrouter_kulut.kutsu(
                            malli, viestit(sarja, t), laheta, tehtava=TEHTAVA, tila=kulutila,
                            max_tokens=yksityinen_or.MAX_TOKENIA, temperature=0.3, stream=False,
                            usage={"include": True}, yksityinen_chat=liput[malli])
                        teksti = data["choices"][0]["message"].get("content") or ""
                        kaytto = data.get("usage") or {}
                        kulu = float(kaytto.get("cost") or 0.0)
                        rivi.update(vastaus=teksti.strip(), kulu_usd=kulu, sekuntia=round(kello() - t0, 2),
                                    tokenit_sisaan=kaytto.get("prompt_tokens"), tokenit_ulos=kaytto.get("completion_tokens"),
                                    tarjoaja=data.get("provider"))
                        kulut += kulu
                        suurin = max(suurin, kulu)
                        perakkain = 0
                    except openrouter_kulut.KutsuEstetty:
                        pysahtyi = "paivakatto"
                        break
                    except Exception as e:  # noqa: BLE001 - vain tyyppi talteen, ei viestia
                        virheet += 1
                        perakkain += 1
                        rivi.update(virhe=type(e).__name__, sekuntia=round(kello() - t0, 2))
                        if virheet >= MAX_VIRHEITA:
                            pysahtyi = "virheita"
                    f.write(json.dumps(rivi, ensure_ascii=False) + "\n")
                    f.flush()
                    loki("%s %s/%d %s" % (malli, t["id"], toisto, rivi.get("virhe", "ok")))
    yhteenveto = {"kulut_usd": round(kulut, 4), "virheita": virheet, "pysahtyi": pysahtyi or None,
                  "mallit": mallit, "toistot": toistot, "raja_usd": raja}
    (ulos / "ajo.json").write_text(json.dumps(yhteenveto, ensure_ascii=False, indent=1), encoding="utf-8")
    return yhteenveto


def lue_vastaukset(ulos: Path) -> list[dict[str, Any]]:
    p = ulos / "vastaukset.jsonl"
    return [json.loads(r) for r in p.read_text(encoding="utf-8").splitlines() if r.strip()]


# --- Tuomaripaketti ja taulukko ----------------------------------------------------------------------

def paketti(ulos: Path, siemen: int = 353) -> int:
    """Kirjoittaa tuomari_syote.jsonl (tehtava, arviointiohje, vastaus; ei mallin nimea) sekoitettuna ja
    tuomari_avain.json (nayte -> malli) erikseen. Palauttaa naytteiden maaran."""
    sarja = {t["id"]: t for t in lue_tehtavat()["tehtavat"]}
    rivit = [r for r in lue_vastaukset(ulos) if "vastaus" in r]
    rnd = random.Random(siemen)
    rnd.shuffle(rivit)
    avain, syote = {}, []
    for i, r in enumerate(rivit):
        nid = "n%04d" % i
        t = sarja[r["tehtava"]]
        avain[nid] = {"malli": r["malli"], "tehtava": r["tehtava"], "toisto": r["toisto"]}
        syote.append({"nayte": nid, "tehtava": t["historia"][-1]["teksti"], "arviointiohje": t["tuomari"]
                      or "Arvioi vastauksen hy\u00f6dyllisyys, oikeellisuus ja suomen kieli.", "vastaus": r["vastaus"]})
    (ulos / "tuomari_syote.jsonl").write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in syote),
                                             encoding="utf-8")
    (ulos / "tuomari_avain.json").write_text(json.dumps(avain, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(syote)


def pisteet(ulos: Path, tuomari: dict[str, float] | None = None) -> dict[str, dict[str, Any]]:
    """Mallikohtaiset pisteet 0-10: tehtavan pisteet = mekaanisen ja tuomarin keskiarvo (kumpi on olemassa).
    Puuttuva vastaus tai virhe = 0. Alueen pisteet = tehtavien keskiarvo, kokonaispisteet = kaikkien tehtavien keskiarvo."""
    sarja = {t["id"]: t for t in lue_tehtavat()["tehtavat"]}
    avain = {}
    if tuomari is not None:
        avain = {(v["malli"], v["tehtava"], v["toisto"]): k for k, v in
                 json.loads((ulos / "tuomari_avain.json").read_text(encoding="utf-8")).items()}
    tulos: dict[str, dict[str, Any]] = {}
    for r in lue_vastaukset(ulos):
        m = tulos.setdefault(r["malli"], {"tehtavat": {}, "kulu": 0.0, "ajat": [], "kutsuja": 0})
        m["kulu"] += r.get("kulu_usd") or 0.0
        m["kutsuja"] += 1
        if "sekuntia" in r and "vastaus" in r:
            m["ajat"].append(r["sekuntia"])
        osat = []
        if "vastaus" in r and r["vastaus"]:
            mek = mekaaninen(sarja[r["tehtava"]]["mekaaninen"], r["vastaus"])
            if mek is not None:
                osat.append(mek)
            nid = avain.get((r["malli"], r["tehtava"], r["toisto"]))
            if nid is not None and nid in (tuomari or {}):
                osat.append(float(tuomari[nid]))
        p = sum(osat) / len(osat) if osat else 0.0
        m["tehtavat"].setdefault(r["tehtava"], []).append(p)
    for m in tulos.values():
        teht = {k: sum(v) / len(v) for k, v in m["tehtavat"].items()}
        m["alueet"] = {a: round(statistics.mean([p for k, p in teht.items() if sarja[k]["alue"] == a]), 2)
                       for a in ALUEET if any(sarja[k]["alue"] == a for k in teht)}
        m["yhteensa"] = round(statistics.mean(teht.values()), 2) if teht else 0.0
        m["usd_per_100"] = round(100 * m["kulu"] / m["kutsuja"], 4) if m["kutsuja"] else None
        m["mediaani_s"] = round(statistics.median(m["ajat"]), 1) if m["ajat"] else None
    return tulos


def taulukko(tulos: dict[str, dict[str, Any]]) -> str:
    rivit = ["| Malli | Yhteens\u00e4 | P\u00e4\u00e4t\u00f6s | Ohjeet | Rehellisyys | Muisti | Suomi | $ / 100 viesti\u00e4 | Vastausaika (s, mediaani) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for malli, m in sorted(tulos.items(), key=lambda x: -x[1]["yhteensa"]):
        a = m["alueet"]
        rivit.append("| %s | %.2f | %s | %s | %s | %s | %s | %s | %s |" % (
            malli, m["yhteensa"], *("%.2f" % a[x] if x in a else "-" for x in ALUEET),
            m["usd_per_100"], m["mediaani_s"]))
    return "\n".join(rivit)


def sokkonaytteet(ulos: Path, tulos: dict[str, dict[str, Any]], siemen: int = 353) -> str:
    """Kolmen parhaan mallin vastaukset sokkotehtaviin kirjaimilla A-C. Avain palautetaan erikseen tiedostoon."""
    sarja = lue_tehtavat()
    teht = {t["id"]: t for t in sarja["tehtavat"]}
    parhaat = [m for m, _ in sorted(tulos.items(), key=lambda x: -x[1]["yhteensa"])[:3]]
    kirjaimet = list("ABC")[:len(parhaat)]
    random.Random(siemen).shuffle(kirjaimet)
    kartta = dict(zip(parhaat, kirjaimet))
    vastaukset = {(r["malli"], r["tehtava"]): r.get("vastaus", "(ei vastausta)")
                  for r in reversed(lue_vastaukset(ulos)) if r["toisto"] == 1}
    osat = []
    for tid in sarja["sokkotehtavat"]:
        osat.append("### Teht\u00e4v\u00e4 %s\n\n> %s\n" % (tid, teht[tid]["historia"][-1]["teksti"]))
        for m in sorted(parhaat, key=lambda x: kartta[x]):
            osat.append("**%s:**\n\n%s\n" % (kartta[m], vastaukset.get((m, tid), "(ei vastausta)")))
    (ulos / "sokko_avain.json").write_text(json.dumps(kartta, ensure_ascii=False, indent=1), encoding="utf-8")
    return "\n".join(osat)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aivo.mallivertailu")
    ap.add_argument("komento", choices=("aja", "paketti", "taulukko"))
    ap.add_argument("--ulos", required=True)
    ap.add_argument("--mallit", default=",".join(EHDOKKAAT))
    ap.add_argument("--toistot", type=int, default=TOISTOT)
    ap.add_argument("--avain", default=yksityinen_or.OLETUS_AVAIN)
    ap.add_argument("--tuomari", help="JSON {nayte: pisteet 0-10}")
    a = ap.parse_args(argv)
    ulos = Path(a.ulos)
    try:
        if a.komento == "aja":
            avain = yksityinen_or.lue_avain(a.avain)
            yv = aja([m.strip() for m in a.mallit.split(",") if m.strip()], ulos,
                     lambda: yksityinen_or.tee_kuljetin(avain), toistot=min(max(a.toistot, 1), TOISTOT))
            print(json.dumps(yv, ensure_ascii=False))
        elif a.komento == "paketti":
            print("naytteita", paketti(ulos))
        else:
            tuomari = json.loads(Path(a.tuomari).read_text(encoding="utf-8")) if a.tuomari else None
            tulos = pisteet(ulos, tuomari)
            print(taulukko(tulos) + "\n\n" + sokkonaytteet(ulos, tulos))
    except (VertailuVirhe, yksityinen.YksityinenVirhe, OSError, ValueError, KeyError) as e:
        print("virhe:", type(e).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
