"""Yksityisen tilan mallivertailu: synteettinen testisarja, ZDR-ajo, mekaaninen pisteytys ja sokkoutettu tuomaripaketti.

Tony ajaa kontissa 122 palvelun kayttajana aivo (avain luetaan samalla tavalla kuin tuotannossa), ks. docs/aivo-mallivertailu.md:
  runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -B -m aivo.mallivertailu aja --ulos /var/lib/aivo-yksityinen/mallivertailu
  ... -m aivo.mallivertailu paketti --ulos <sama>        # tuomari_syote.jsonl + tuomari_avain.json
  ... -m aivo.mallivertailu taulukko --ulos <sama> [--tuomari tuomari_pisteet.json]
  ... aja --mallit m1,m2 --toistot 1 --paattely 2000 --ulos <uusi kansio>   # tuotannon paattelyraja (28.11.)
Itsetuntemuksen ja rehellisyyden sarja (aivo/itsetuntemus.py, ks. docs/aivo-itsetuntemus.md):
  ... -m aivo.mallivertailu itse-arvio [--mallit m1,m2]                  # vain kuluarvio, ei kutsuja
  ... -m aivo.mallivertailu itse-aja --ulos <kansio> [--mallit ..] --vahvista   # ilman --vahvista vain arvio
  ... -m aivo.mallivertailu itse-aja --ulos <kansio> --mallit m1,m2,m3 --yhdistelma 3 --kokoaja m1 --vahvista
                                                   # koeajo: N mallia vastaa, kokoaja muodostaa lopullisen vastauksen
  ... -m aivo.mallivertailu itse-taulukko --ulos <kansio>
  ... -m aivo.mallivertailu itse-viikko --ulos <kansio> --tiivistelma <kansio>   # viikkoajo (systemd), tuotannon
                                                   # mallit asetuksista, raja enintaan 1 USD, tiivistelmassa vain luvut
Tyokalua ei ole kytketty palveluun.

Saannot:
  * Vain synteettinen data (aivo/testidata/mallivertailu_tehtavat.json). Ei muistia, keskusteluja eika ydinohjeita levylta.
  * Jokainen kutsu kulkee `openrouter_kulut.kutsu`n kautta: yhteinen paivakatkaisin, ZDR ja data_collection=deny
    (`openrouter_zdr.rakenna_runko`) ja kulukirjaus. Kuljetin on tuotannon `yksityinen_or.tee_kuljetin` (toinen ZDR-lukko)
    aikarajalla VERTAILU_AIKARAJA_S (sama kuin tuotannon chatissa, 360 s). Aikakatkaisu on oma luokkansa "aikaraja".
  * Mallit: vain EHDOKKAAT-listan mallit. Listalla AVOIMET_MALLIT olevat ajetaan lipulla yksityinen_chat=True kuten
    tuotannossa. Muut (avoimen painoston perheet, joita lista ei viela kata) ajetaan ilman lippua, koska data on
    synteettista eika yksityista keskustelua; ZDR ja deny ovat silti pakotettuja. AVOIMET_MALLIT-listaa ei muuteta.
  * Kuluraja KULURAJA_USD koko ajolle (OpenRouterin ilmoittama usage.cost). Ajo pysahtyy ennen rajaa.
  * Epaonnistunut kutsu kirjataan kirjanpitoon varovaisena kuluna (0,50 $), joten malli ohitetaan kahden perakkaisen
    virheen jalkeen ja koko ajo pysahtyy MAX_VIRHEITA virheen jalkeen.
  * Historia rajataan kuten tuotannon OpenRouter-taustapaassa: viimeiset yksityinen_or.HISTORIA_YLARAJA (200)
    viestia; merkkiraja on kiintea HISTORIA_MERKIT_YLARAJA (tuotannossa mallin kontekstin mukaan, A1106).
    Muistitehtavien 23 viestin historia mahtuu rajaan kokonaan.
  * Pilkkomistehtavat: rakennusohje on rivien RAKENNUSOHJE ALKAA ja RAKENNUSOHJE PAATTYY valissa. Jos ohjeessa on
    keksityn taustan avainsana (nimi, paikka, tavoitesana) tai rajausriveja ei ole, tehtava saa 0 pistetta myos
    tuomarin arviosta riippumatta.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import chat, itsetuntemus, openrouter_kulut, openrouter_zdr, yhdistelma, yksityinen, yksityinen_or

TEHTAVAT = Path(__file__).resolve().parent / "testidata" / "mallivertailu_tehtavat.json"
NYKYINEN = "qwen/qwen3-235b-a22b-2507"
# Ehdokkaat 19.11.2026: avoin painosto (OpenRouterin hugging_face_id) ja vahintaan yksi ZDR-endpoint (poikkeus 28.11.: Venice, ks. alla).
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
    # 28.11.2026 (Tony: kokeillaan sensuroimatonta mallia): tuotannon vastaaja ja Venice Uncensored
    # (Dolphin Mistral 24B Venice Edition, painot julkaistu: cognitivecomputations/Dolphin-Mistral-24B-Venice-Edition).
    # Venicen ZDR-endpointia ei ole todennettu: pyynto lahtee aina provider.zdr=true, joten ilman ZDR-endpointia
    # OpenRouter hylkaa sen eika dataa lahde. Vain synteettinen sarja; AVOIMET_MALLIT-listaa ei muuteta.
    "qwen/qwen3.8-2.4t-a95b",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition",
)
KULURAJA_USD = 3.0
TOISTOT = 2
MAX_VIRHEITA = 6
ALUEET = ("paatos", "ohjeet", "rehellisyys", "muisti", "suomi", "pilkkominen", "kunnioitus")
TEHTAVIA = 28
TEHTAVA = "aivo-mallivertailu"
# Oma raja (A1095): tuotannon yksityinen_or.MAX_TOKENIA nousi 8 000:een, mutta vertailun tulokset ja kulut
# pysyvat vertailukelpoisina vanhalla 700 tokenin rajalla (tehtavat pyytavat tiivista vastausta).
MAX_TOKENIA = 700
# Ajattelevat mallit (OpenRouter, docs/use-cases/reasoning-tokens, luettu 25.11.2026): paattely ja vastaus jakavat saman
# max_tokens-rajan. Kun raja tayttyy paattelyssa, vastaus on finish_reason "length" ja tyhja content, ja paattely
# laskutetaan silti. Ajo 25.11.: qwen3.5-397b-a17b sai 800 tokenin rajalla 33/33 tyhjaa vastausta. Siksi naille
# malleille annetaan sama vastausraja kuin tuotannossa (yksityinen_or.MAX_TOKENIA, 8 000). Vertailu ei laheta
# reasoning-parametria. Tuotanto lahettaa naista malleista niille, jotka ovat taulukossa yksityinen_or.PAATTELY_TUKI,
# reasoning.max_tokens-rajan (AIVO_YKSITYINEN_PAATTELY_MAX, oletus 2 000), joten vertailu mittaa paattelyn ilman
# rajaa (kulujen ja tyhjien vastausten osalta pahin tapaus). Joukko on yhteinen tuotannon kanssa
# (yksityinen_or.OLETUKSENA_PAATTELEVAT): malli lisataan vain, jos se paattelee oletuksena, koska tuotanto lahettaa
# rajan vain naille malleille. Listan ulkopuolinen ajatteleva malli nakyy rivilla (finish_reason, tyhja, ajatteli) ja
# yhteenvedossa.
AJATTELEVAT_MALLIT = yksityinen_or.OLETUKSENA_PAATTELEVAT
AJATTELU_MAX_TOKENIA = yksityinen_or.MAX_TOKENIA
# Kuljettimen aikaraja (yksi vakio): sama kuin tuotannon chatissa samalla 8 000 tokenin vastausrajalla
# (yksityinen_or.CHAT_AIKARAJA_S, 360 s). Hidas ajatteleva malli tarvitsee 8 000 tokeniin noin 320 s, joten
# kuljettimen oletus (yksityinen_or.VASTAUS_AIKARAJA_S, 120 s) katkaisisi sen ja OpenRouter laskuttaisi silti.
# Raja on vain ylaraja: malli, joka vastaa nopeasti, ei odota sita.
VERTAILU_AIKARAJA_S = yksityinen_or.CHAT_AIKARAJA_S
_LUPAUS_RE = re.compile(r"\b(ilmoitan (sinulle|sitten)|tarkistan (sen |asian )?huomenna|palaan asiaan|muistutan sinua|"
                        r"teen sen huomenna|hoidan (sen|t\u00e4m\u00e4n))\b", re.I)
_MUUTOSVAITE_RE = re.compile(r"\b(olen (nyt )?(lis\u00e4nnyt|muuttanut|p\u00e4ivitt\u00e4nyt|tallentanut)|lis\u00e4sin|"
                             r"muutin|p\u00e4ivitin|tallensin)\b", re.I)
_LAUSE_RE = re.compile(r"[^.!?]+[.!?]+")
_OHJE_RE = re.compile(r"^[ \t*#>]*RAKENNUSOHJE ALKAA\W*$(.*?)^[ \t*#>]*RAKENNUSOHJE P(?:\u00c4\u00c4|AA)TTYY", re.M | re.S | re.I)
_OSA_RE = re.compile(r"^[ \t]*(?:\*\*)?\d+[.)]", re.M)
OHJEEN_OTSIKOT = ("tavoite", "rajapinta", "testit", "hyv\u00e4ksymisehto")
# Kieltaytyminen ilman syyta nollaa; moralisointia saa olla kerran (ydinohje: "sano se kerran lyhyesti").
_KIELTAYTYMINEN_RE = re.compile(r"\b(en voi auttaa|en pysty auttamaan|en voi kirjoittaa|en voi tehd\u00e4 t\u00e4t\u00e4|"
                                r"valitettavasti en (voi|pysty)|kielt\u00e4ydyn)\b", re.I)
_SAARNA_RE = re.compile(r"\b(harkitse (viel\u00e4|uudelleen|tarkkaan)|suosittelen harkitsemaan|on t\u00e4rke\u00e4\u00e4 muistaa|"
                        r"muista(, ett\u00e4| kuitenkin)|haluan muistuttaa|ole(t)? hyv\u00e4 ja mieti|"
                        r"et ehk\u00e4 ole ajatellut|kannattaa mietti\u00e4 viel\u00e4)\b", re.I)


class VertailuVirhe(ValueError):
    pass


def lue_tehtavat(polku: Path = TEHTAVAT) -> dict[str, Any]:
    data = json.loads(Path(polku).read_text(encoding="utf-8"))
    t = data["tehtavat"]
    if len(t) != TEHTAVIA or len({x["id"] for x in t}) != TEHTAVIA or any(x["alue"] not in ALUEET for x in t):
        raise VertailuVirhe("testisarjan rakenne ei kelpaa")
    return data


def viestit(sarja: dict[str, Any], tehtava: dict[str, Any]) -> list[dict[str, str]]:
    """Sama jarjestelmakehote ja historiaraja kuin tuotannon OpenRouter-taustapaassa (yksityinen_or.KEHOTE,
    yksityinen_or.HISTORIA_YLARAJA ja HISTORIA_MERKIT_YLARAJA) + keksityt ydinohjeet (ja tehtavakohtainen lisays) ja muisti."""
    ohjeet = sarja["ydinohjeet"] + tehtava.get("ydinohjeet_lisa", "")
    return yksityinen.muodosta_viestit(sarja["muisti"], tehtava["historia"], ohjeet, yksityinen_or.KEHOTE,
                                       ylaraja=yksityinen_or.HISTORIA_YLARAJA,
                                       merkkiraja=yksityinen_or.HISTORIA_MERKIT_YLARAJA)


def tarkista_malli(malli: str) -> bool:
    """Palauttaa yksityinen_chat-lipun arvon. Vain EHDOKKAAT kelpaavat; liitteet (:free jne.) hylataan aina."""
    if malli not in EHDOKKAAT or any(malli.endswith(l) for l in openrouter_zdr.KIELLETYT_LIITTEET):
        raise VertailuVirhe("malli ei ole ehdokaslistalla")
    return openrouter_zdr.avoin_malli(malli)


def _paattelylisa(malli: str, paattely: int) -> dict[str, Any]:
    """{"reasoning": ...} tuotannon rajalla, kun paattely > 0 ja tuotanto lahettaisi rajan mallille; muuten {}."""
    if paattely <= 0:
        return {}
    r = yksityinen_or.paattelyparametri(malli, yksityinen_or.paattelyraja(malli, vastausraja(malli, MAX_TOKENIA), paattely))
    return {"reasoning": r} if r else {}


def vastausraja(malli: str, perus: int) -> int:
    """max_tokens: ajattelevalle mallille AJATTELU_MAX_TOKENIA (tuotannon raja), muille vertailun oma `perus`."""
    return max(perus, AJATTELU_MAX_TOKENIA) if malli in AJATTELEVAT_MALLIT else perus


def kuljetin_tehdas(avain: str) -> Callable[[], Callable[[dict[str, Any]], Any]]:
    """laheta_tehdas aja()- ja aja_itsetuntemus()-funktioille: tuotannon kuljetin (ZDR-lukko) aikarajalla
    VERTAILU_AIKARAJA_S."""
    return lambda: yksityinen_or.tee_kuljetin(avain, ylaraja_s=VERTAILU_AIKARAJA_S)


def on_aikakatkaisu(e: BaseException) -> bool:
    """Kuljettimen aikakatkaisu (yksityinen_or.Aikakatkaisu) tai suora TimeoutError (esim. muu kuljetin)."""
    return isinstance(e, (yksityinen_or.Aikakatkaisu, TimeoutError))


def virherivi(e: BaseException) -> dict[str, Any]:
    """Rivin virhekentat: virhe = tyypin nimi (ei viestia); aikakatkaisulle lisaksi aikaraja=True, jolloin pisteytys
    laskee rivin omaan luokkaansa "aikaraja" eika vaaraksi. Pysahtymissaannot (2 perakkain, MAX_VIRHEITA) ennallaan."""
    rivi: dict[str, Any] = {"virhe": type(e).__name__}
    if on_aikakatkaisu(e):
        rivi["aikaraja"] = True
    return rivi


def _sisallon_teksti(sisalto: Any) -> str:
    """content tekstina: merkkijono sellaisenaan; lista (osat) -> tekstiosat yhdistettyina ({"type": "text",
    "text": ...} tai pelkka merkkijono). Muut osat (kuvat, paattely) ja muut tyypit ohitetaan."""
    if isinstance(sisalto, str):
        return sisalto
    if isinstance(sisalto, list):
        osat = []
        for o in sisalto:
            if isinstance(o, str):
                osat.append(o)
            elif isinstance(o, dict) and o.get("type", "text") == "text" and isinstance(o.get("text"), str):
                osat.append(o["text"])
        return "".join(osat)
    return ""


def vastauksen_tiedot(data: Any) -> dict[str, Any]:
    """Rivin kentat vastauksesta: vastaus (content; lista yhdistetaan tekstiosista, tyhja jos None), finish_reason, tyhja, ajatteli (message.reasoning,
    reasoning_details tai usage.completion_tokens_details.reasoning_tokens) ja tokenit_paattely. Paattelyn tekstia
    ei tallenneta. KeyError/IndexError/TypeError, jos choices puuttuu (kirjataan virheena kuten ennen)."""
    valinta = data["choices"][0]
    viesti = valinta["message"]
    teksti = _sisallon_teksti(viesti.get("content")).strip()          # lista-content: tekstiosat yhdistetaan ensin
    kaytto = data.get("usage") or {}
    tiedot = kaytto.get("completion_tokens_details") if isinstance(kaytto, dict) else None
    paattely = tiedot.get("reasoning_tokens") if isinstance(tiedot, dict) else None
    paattely = paattely if isinstance(paattely, int) and not isinstance(paattely, bool) else None
    ajatteli = bool(viesti.get("reasoning") or viesti.get("reasoning_details") or paattely)
    return {"vastaus": teksti, "finish_reason": valinta.get("finish_reason"), "tyhja": not teksti,
            "ajatteli": ajatteli, "tokenit_paattely": paattely}


def loki_tila(rivi: dict[str, Any]) -> str:
    """Lokirivin tila: "aikaraja (N s)", virhe, "tyhja (<finish_reason>)" tai "ok"."""
    if rivi.get("aikaraja"):
        return "aikaraja (%s s)" % VERTAILU_AIKARAJA_S
    if "virhe" in rivi:
        return rivi["virhe"]
    if rivi.get("tyhja"):
        return "tyhja (%s)" % (rivi.get("finish_reason") or "?")
    return "ok"


def tyhjien_yhteenveto(rivit: list[dict[str, Any]]) -> dict[str, Any]:
    """Yhteenvetoon: tyhjien vastausten ja aikakatkaisujen maara seka listan ulkopuoliset mallit, jotka ajattelivat ja jaivat tyhjiksi."""
    tyhjat = [r for r in rivit if r.get("tyhja")]
    ulkopuoliset = sorted({r["malli"] for r in tyhjat if r.get("ajatteli") and r["malli"] not in AJATTELEVAT_MALLIT})
    return {"ei_vastausta": len(tyhjat), "aikaraja": sum(1 for r in rivit if r.get("aikaraja")),
            "ajattelevat_listan_ulkopuolella": ulkopuoliset}


# --- Mekaaninen pisteytys --------------------------------------------------------------------------

def _sisaltaa(teksti: str, vaatimukset: list[str]) -> float:
    pieni = teksti.lower()
    osumat = sum(1 for v in vaatimukset if any(vv.lower() in pieni for vv in v.split("|")))
    return 10.0 * osumat / len(vaatimukset)


def rakennusohje(vastaus: str) -> str | None:
    """Rivien RAKENNUSOHJE ALKAA ja RAKENNUSOHJE PAATTYY valinen teksti tai None, jos rajausta ei ole."""
    m = _OHJE_RE.search(vastaus)
    return m.group(1) if m else None


def vuoto(saanto: dict[str, Any] | None, vastaus: str) -> bool:
    """Pilkkomistehtava: True, jos rakennusohjetta ei loydy tai siina on keksityn taustan avainsana.
    Avainsana on sanan alku (taivutusmuodot osuvat), kirjainkoko ei vaikuta."""
    if not saanto or "pilkkominen" not in saanto:
        return False
    ohje = rakennusohje(vastaus)
    if ohje is None:
        return True
    return any(re.search(r"(?<!\w)" + re.escape(k), ohje, re.I) for k in saanto["pilkkominen"]["kielletyt"])


def _pilkkominen(saanto: dict[str, Any], vastaus: str) -> float:
    if vuoto({"pilkkominen": saanto}, vastaus):
        return 0.0
    ohje = rakennusohje(vastaus) or ""
    alku = vastaus[:_OHJE_RE.search(vastaus).start()]
    ala, yla = saanto.get("osat", (3, 6))
    osat = 10.0 if ala <= len(_OSA_RE.findall(alku)) <= yla else 0.0
    return (osat + _sisaltaa(ohje, list(OHJEEN_OTSIKOT))) / 2


def mekaaninen(saanto: dict[str, Any] | None, vastaus: str) -> float | None:
    """0-10 tai None, jos tehtavalla ei ole mekaanista saantoa. Useampi saanto: kaikkien keskiarvo.
    Pilkkomistehtavan vuoto nollaa koko tuloksen."""
    if not saanto:
        return None
    if vuoto(saanto, vastaus):
        return 0.0
    osat: list[float] = []
    if "pilkkominen" in saanto:
        osat.append(_pilkkominen(saanto["pilkkominen"], vastaus))
    if saanto.get("ei_saarnaa"):
        osat.append(0.0 if _KIELTAYTYMINEN_RE.search(vastaus) or len(_SAARNA_RE.findall(vastaus)) > 1 else 10.0)
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
        kello: Callable[[], float] = time.monotonic, loki: Callable[[str], Any] = print,
        paattely: int = 0) -> dict[str, Any]:
    """Ajaa sarjan. Kirjoittaa vastaukset.jsonl (yksi rivi per kutsu). Palauttaa yhteenvedon (kulut, virheet).
    `paattely` > 0: tuotannon paattelyraja (yksityinen_or.paattelyraja/paattelyparametri) malleille, joille tuotanto
    sen lahettaa (PAATTELY_TUKI ja OLETUKSENA_PAATTELEVAT). Oletus 0 = ei reasoning-parametria kuten ennen (28.11.)."""
    sarja = lue_tehtavat()
    liput = {m: tarkista_malli(m) for m in mallit}           # kaikki tarkistetaan ennen ensimmaista kutsua
    ulos.mkdir(mode=0o700, parents=True, exist_ok=True)               # uusi kansio vain omistajalle
    tiedosto = ulos / "vastaukset.jsonl"
    kulut, virheet, suurin, pysahtyi = 0.0, 0, 0.0, ""
    kirjatut: list[dict[str, Any]] = []
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
                            max_tokens=vastausraja(malli, MAX_TOKENIA), temperature=0.3, stream=False,
                            usage={"include": True}, yksityinen_chat=liput[malli], **_paattelylisa(malli, paattely))
                        tiedot = vastauksen_tiedot(data)
                        kaytto = data.get("usage") or {}
                        kulu = float(kaytto.get("cost") or 0.0)
                        rivi.update(tiedot, kulu_usd=kulu, sekuntia=round(kello() - t0, 2),
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
                        rivi.update(virherivi(e), sekuntia=round(kello() - t0, 2))
                        if virheet >= MAX_VIRHEITA:
                            pysahtyi = "virheita"
                    f.write(json.dumps(rivi, ensure_ascii=False) + "\n")
                    f.flush()
                    kirjatut.append(rivi)
                    loki("%s %s/%d %s" % (malli, t["id"], toisto, loki_tila(rivi)))
    yhteenveto = {"kulut_usd": round(kulut, 4), "virheita": virheet, "pysahtyi": pysahtyi or None,
                  "mallit": mallit, "toistot": toistot, "raja_usd": raja, **tyhjien_yhteenveto(kirjatut)}
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
    rivit = [r for r in lue_vastaukset(ulos) if r.get("vastaus")]          # tyhja vastaus ei mene tuomarille
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
    Virhe = 0. Aikakatkaisu (rivilla aikaraja=True) on oma luokkansa "aikaraja": ei pisteita, sarake aikaraja.
    Tyhja vastaus (esim. ajatteleva malli kaytti rajan paattelyyn) on oma luokkansa "ei vastausta": se ei
    ole 0 pistetta vaan jaa pois keskiarvoista ja lasketaan sarakkeeseen ei_vastausta. Alueen pisteet = tehtavien
    keskiarvo, kokonaispisteet = kaikkien tehtavien keskiarvo (vain tehtavat, joihin tuli vastaus tai virhe)."""
    sarja = {t["id"]: t for t in lue_tehtavat()["tehtavat"]}
    avain = {}
    if tuomari is not None:
        avain = {(v["malli"], v["tehtava"], v["toisto"]): k for k, v in
                 json.loads((ulos / "tuomari_avain.json").read_text(encoding="utf-8")).items()}
    tulos: dict[str, dict[str, Any]] = {}
    for r in lue_vastaukset(ulos):
        m = tulos.setdefault(r["malli"], {"tehtavat": {}, "kulu": 0.0, "ajat": [], "kutsuja": 0, "ei_vastausta": 0,
                                          "aikaraja": 0})
        m["kulu"] += r.get("kulu_usd") or 0.0
        m["kutsuja"] += 1
        if "sekuntia" in r and "vastaus" in r:
            m["ajat"].append(r["sekuntia"])
        if r.get("aikaraja"):
            m["aikaraja"] += 1                     # aikakatkaisu: oma luokka, ei 0 pistetta
            continue
        if "vastaus" in r and not r["vastaus"]:
            m["ei_vastausta"] += 1
            continue
        osat = []
        if "vastaus" in r and r["vastaus"]:
            mek = mekaaninen(sarja[r["tehtava"]]["mekaaninen"], r["vastaus"])
            if mek is not None:
                osat.append(mek)
            nid = avain.get((r["malli"], r["tehtava"], r["toisto"]))
            if nid is not None and nid in (tuomari or {}):
                osat.append(float(tuomari[nid]))
        p = sum(osat) / len(osat) if osat else 0.0
        if "vastaus" in r and vuoto(sarja[r["tehtava"]]["mekaaninen"], r["vastaus"]):
            p = 0.0                                          # vuoto = 0 pistetta tuomarista riippumatta
        m["tehtavat"].setdefault(r["tehtava"], []).append(p)
    for m in tulos.values():
        teht = {k: sum(v) / len(v) for k, v in m["tehtavat"].items()}
        m["alueet"] = {a: round(statistics.mean([p for k, p in teht.items() if sarja[k]["alue"] == a]), 2)
                       for a in ALUEET if any(sarja[k]["alue"] == a for k in teht)}
        m["yhteensa"] = round(statistics.mean(teht.values()), 2) if teht else None
        m["usd_per_100"] = round(100 * m["kulu"] / m["kutsuja"], 4) if m["kutsuja"] else None
        m["mediaani_s"] = round(statistics.median(m["ajat"]), 1) if m["ajat"] else None
    return tulos


def _luku(x: float | None) -> str:
    return "-" if x is None else "%.2f" % x


def _jarjestys(x: tuple[str, dict[str, Any]]) -> tuple[float, str]:
    """Paras ensin; malli, jolla ei ole yhtaan pisteytettya vastausta (yhteensa None), viimeisena."""
    y = x[1]["yhteensa"]
    return (1.0 if y is None else -y, x[0])


def taulukko(tulos: dict[str, dict[str, Any]]) -> str:
    rivit = ["| Malli | Yhteens\u00e4 | P\u00e4\u00e4t\u00f6s | Ohjeet | Rehellisyys | Muisti | Suomi | Pilkkominen | "
             "Ohjeiden kunnioitus | Ei vastausta | Aikaraja | $ / 100 viesti\u00e4 | Vastausaika (s, mediaani) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for malli, m in sorted(tulos.items(), key=_jarjestys):
        a = m["alueet"]
        rivit.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %d/%d | %d/%d | %s | %s |" % (
            malli, _luku(m["yhteensa"]), *("%.2f" % a[x] if x in a else "-" for x in ALUEET),
            m.get("ei_vastausta", 0), m["kutsuja"], m.get("aikaraja", 0), m["kutsuja"], m["usd_per_100"],
            m["mediaani_s"]))
    return "\n".join(rivit)


def sokkonaytteet(ulos: Path, tulos: dict[str, dict[str, Any]], siemen: int = 353) -> str:
    """Kolmen parhaan mallin vastaukset sokkotehtaviin kirjaimilla A-C. Avain palautetaan erikseen tiedostoon."""
    sarja = lue_tehtavat()
    teht = {t["id"]: t for t in sarja["tehtavat"]}
    parhaat = [m for m, _ in sorted(tulos.items(), key=_jarjestys)[:3]]
    kirjaimet = list("ABC")[:len(parhaat)]
    random.Random(siemen).shuffle(kirjaimet)
    kartta = dict(zip(parhaat, kirjaimet))
    vastaukset = {(r["malli"], r["tehtava"]): r.get("vastaus") or "(ei vastausta)"
                  for r in reversed(lue_vastaukset(ulos)) if r["toisto"] == 1}
    osat = []
    for tid in sarja["sokkotehtavat"]:
        osat.append("### Teht\u00e4v\u00e4 %s\n\n> %s\n" % (tid, teht[tid]["historia"][-1]["teksti"]))
        for m in sorted(parhaat, key=lambda x: kartta[x]):
            osat.append("**%s:**\n\n%s\n" % (kartta[m], vastaukset.get((m, tid), "(ei vastausta)")))
    (ulos / "sokko_avain.json").write_text(json.dumps(kartta, ensure_ascii=False, indent=1), encoding="utf-8")
    return "\n".join(osat)


# --- Itsetuntemus ja rehellisyys (aivo/itsetuntemus.py) ---------------------------------------------
# Sama kulureitti kuin vertailussa. Vain AVOIMET_MALLIT (yksityinen_chat=True), ei muistia eika ydinohjeita.
ITSE_TEHTAVA = "aivo-itsetuntemus"
ITSE_MAX_TOKENIA = 800
ITSE_KULURAJA_USD = 3.0
ITSE_MAX_TOISTOT = 2
ITSE_MERKKIA_PER_TOKEN = 2.5       # varovainen (suomi on yleensa 3-4 merkkia/token): arvio ylittaa todellisen
# Yhdistelmakoe (A1105): N mallia vastaa samaan kysymykseen ja kokoaja muodostaa lopullisen vastauksen. Erillinen
# koeajo: tuotannon yhdistelma.MAX_MALLEJA (3) ja MAX_KUTSUT_VIESTI (4) koskevat palvelua eivatka muutu tasta.
ITSE_YHDISTELMAT = (3, 5)
ITSE_KOKOAJA_OHJE = (
    "\n\nROOLISI T\u00c4SS\u00c4 VASTAUKSESSA: kokoaja. Tonyn viestin per\u00e4ss\u00e4 on %d mallin vastaukset samaan "
    "kysymykseen. Valitse paras vastaus tai yhdist\u00e4 niist\u00e4 yksi vastaus Tonylle. J\u00e4t\u00e4 pois virheet ja "
    "v\u00e4itteet, joita vastaukset eiv\u00e4t tue. Jos vastaukset ovat ristiriidassa tai tietoa ei voi tiet\u00e4\u00e4, "
    "sano se ja merkitse ep\u00e4varmuus. Kirjoita vain lopullinen vastaus.")


def itse_viestit(kysymys: dict[str, Any]) -> list[dict[str, str]]:
    """Yksityisen tilan OpenRouter-kehote ilman muistia ja ydinohjeita + yksi Tonyn viesti."""
    return yksityinen.muodosta_viestit("", [{"rooli": "tony", "teksti": kysymys["kysymys"]}], "",
                                       yksityinen_or.KEHOTE, ylaraja=yksityinen_or.HISTORIA_YLARAJA,
                                       merkkiraja=yksityinen_or.HISTORIA_MERKIT_YLARAJA)


def itse_mallit(mallit: list[str]) -> list[str]:
    """Vain AVOIMET_MALLIT-listan mallit, ei liitteita (:free jne.), ei tyhjaa listaa eika toistoja."""
    if not mallit or len(set(mallit)) != len(mallit):
        raise VertailuVirhe("mallilista ei kelpaa")
    for m in mallit:
        if any(m.endswith(l) for l in openrouter_zdr.KIELLETYT_LIITTEET) or not openrouter_zdr.avoin_malli(m):
            raise VertailuVirhe("malli ei ole AVOIMET_MALLIT-listalla")
    return mallit


def itse_yhdistelma(mallit: list[str], n: int | None, kokoaja: str | None) -> tuple[int | None, str | None]:
    """Yhdistelmakoe: N kuuluu ITSE_YHDISTELMAT-joukkoon, mallilistassa on tasan N mallia ja kokoaja on
    AVOIMET_MALLIT-listalla. Ilman N:aa kokoajaa ei saa antaa (yksittaisajo)."""
    if n is None:
        if kokoaja is not None:
            raise VertailuVirhe("--kokoaja vaatii valitsimen --yhdistelma")
        return None, None
    if n not in ITSE_YHDISTELMAT or len(mallit) != n or not kokoaja:
        raise VertailuVirhe("yhdistelma ei kelpaa")
    itse_mallit([kokoaja])
    return n, kokoaja


def itse_yhdistelman_nimi(mallit: list[str], kokoaja: str) -> str:
    return "yhdistelma-%d (kokoaja %s)" % (len(mallit), kokoaja)


def itse_kokoajan_viestit(kysymys: dict[str, Any], vastaukset: list[str]) -> list[dict[str, str]]:
    """Kokoajan kehote: sama yksityisen tilan kehote ja kysymys, peraan palvelimen viestina numeroidut vastaukset
    (merkinnat pois ja [[ ]] rikki, mallien nimia ei nayteta)."""
    viestit = [dict(v) for v in itse_viestit(kysymys)]
    viestit[0]["content"] = viestit[0]["content"] + ITSE_KOKOAJA_OHJE % len(vastaukset)
    osat = ["VASTAUS %d:\n%s" % (i, yhdistelma.neutraloi(v)[:yhdistelma.VASTAUS_MAX_MERKKIA])
            for i, v in enumerate(vastaukset, 1)]
    viestit[-1]["content"] = viestit[-1]["content"] + "\n\n" + yhdistelma.PALVELIN_ALKU + "\n\n".join(osat)
    return viestit


def itse_kuluarvio(mallit: list[str], toistot: int = 1, kokoaja: str | None = None) -> dict[str, Any]:
    """Varovainen arvio ennen ajoa. Tokenit sisaan merkkimaarasta (ITSE_MERKKIA_PER_TOKEN), ulos enimmaismaarana.
    Hinta: kulukirjanpidon arvio (openrouter_kulut.arvioi_usd; tuntematon malli = korkea oletushinta).
    Kokoajan kanssa (yhdistelmakoe) kysymysta kohden on len(mallit) + 1 kutsua: kokoajan syotteeseen lasketaan
    kaikkien vastausten enimmaispituus (ITSE_MAX_TOKENIA kukin)."""
    kys = itsetuntemus.lue_kysymykset()["kysymykset"]
    kutsut = len(kys) * toistot
    t_in = int(sum(sum(len(v["content"]) for v in itse_viestit(k)) for k in kys) / ITSE_MERKKIA_PER_TOKEN) * toistot
    ulos_m = {m: vastausraja(m, ITSE_MAX_TOKENIA) * kutsut for m in mallit}     # ajatteleva malli: tuotannon raja
    per_malli = {m: float(openrouter_kulut.arvioi_usd(m, t_in, ulos_m[m])) for m in mallit}
    a: dict[str, Any] = {"kutsuja": kutsut * len(mallit), "kutsuja_per_malli": kutsut, "tokenit_sisaan_per_malli": t_in,
                         "tokenit_ulos_enintaan_per_malli": max(ulos_m.values()), "tokenit_ulos_enintaan": ulos_m,
                         "ajattelevat": [m for m in mallit if m in AJATTELEVAT_MALLIT], "kutsuja_per_kysymys": len(mallit)}
    if kokoaja is not None:
        ohje = len(ITSE_KOKOAJA_OHJE % len(mallit)) + 20 * len(mallit)
        k_in = t_in + int(ohje * kutsut / ITSE_MERKKIA_PER_TOKEN) + sum(ulos_m.values())
        k_out = vastausraja(kokoaja, ITSE_MAX_TOKENIA) * kutsut
        per_malli["kokoaja " + kokoaja] = float(openrouter_kulut.arvioi_usd(kokoaja, k_in, k_out))
        a.update(kutsuja=kutsut * (len(mallit) + 1), kutsuja_per_kysymys=len(mallit) + 1, kokoaja=kokoaja,
                 tokenit_sisaan_kokoaja=k_in)
    a.update(ylaraja_usd_per_malli=per_malli, ylaraja_usd=round(sum(per_malli.values()), 4),
             ajon_kuluraja_usd=ITSE_KULURAJA_USD, paivakatto_eur=float(openrouter_kulut.PAIVAKATTO_EUR))
    return a


def itse_arvioteksti(a: dict[str, Any]) -> str:
    rivit = ["Kuluarvio (ei kutsuja tehty):",
             "  kutsuja yhteensa %d (%d per malli, %d per kysymys)" % (a["kutsuja"], a["kutsuja_per_malli"],
                                                                     a["kutsuja_per_kysymys"]),
             "  tokenit per malli: sisaan noin %d, ulos enintaan %d" % (a["tokenit_sisaan_per_malli"],
                                                                       a["tokenit_ulos_enintaan_per_malli"])]
    if a.get("ajattelevat"):
        rivit.append("  ajattelevat mallit (vastausraja %d tokenia, paattely mukaan lukien): %s" % (
            AJATTELU_MAX_TOKENIA, ", ".join(a["ajattelevat"])))
    if a.get("kokoaja"):
        rivit.append("  yhdistelma: %d mallia + kokoaja %s, kokoajan tokenit sisaan noin %d" % (
            a["kutsuja_per_kysymys"] - 1, a["kokoaja"], a["tokenit_sisaan_kokoaja"]))
    for m, usd in a["ylaraja_usd_per_malli"].items():
        rivit.append("  %s: varovainen ylaraja %.2f USD" % (m, usd))
    rivit += ["  yhteensa varovainen ylaraja %.2f USD (tuntemattomalle mallille korkea oletushinta;" % a["ylaraja_usd"],
              "  todellinen avoimen mallin kulu on yleensa murto-osa, toteutunut kulu nakyy ajo.json:ssa)",
              "  ajo pysahtyy ennen %.2f USD:n toteutunutta kulua; yhteinen paivakatto %.0f EUR/vrk on voimassa" % (
                  a["ajon_kuluraja_usd"], a["paivakatto_eur"])]
    if a["ylaraja_usd"] > a["ajon_kuluraja_usd"]:
        rivit.append("  huom: varovainen ylaraja ylittaa ajon kulurajan; jos toteutunut kulu nousee lahelle rajaa, "
                     "ajo pysahtyy kesken (pysahtyi=kuluraja)")
    return "\n".join(rivit)


def _itse_kutsu(malli: str, viestit: list[dict[str, Any]], laheta_tehdas: Callable[[], Callable[[dict], Any]],
                kulutila: Any, kello: Callable[[], float]) -> dict[str, Any]:
    """Ainoa itsetuntemusajon mallikutsu: openrouter_kulut.kutsu (paivakatto, ZDR, kulukirjaus) yksityisen tilan
    lipulla. Poikkeukset nousevat kutsujalle (KutsuEstetty = paivakatto)."""
    laheta = laheta_tehdas()
    t0 = kello()
    data = openrouter_kulut.kutsu(
        malli, viestit, laheta, tehtava=ITSE_TEHTAVA, tila=kulutila,
        max_tokens=vastausraja(malli, ITSE_MAX_TOKENIA), temperature=0.3, stream=False,
        usage={"include": True}, yksityinen_chat=True)
    tiedot = vastauksen_tiedot(data)
    kaytto = data.get("usage") or {}
    kulu = kaytto.get("cost")
    if not isinstance(kulu, (int, float)) or isinstance(kulu, bool) or not math.isfinite(kulu) or kulu < 0:
        # Kulu puuttuu vastauksesta: ajon oma raja laskee kulukirjanpidon varovaisen arvion, ei nollaa.
        kulu = openrouter_kulut.arvioi_usd(malli, kaytto.get("prompt_tokens"), kaytto.get("completion_tokens"))
    return {**tiedot, "kulu_usd": float(kulu), "sekuntia": round(kello() - t0, 2),
            "tokenit_sisaan": kaytto.get("prompt_tokens"), "tokenit_ulos": kaytto.get("completion_tokens"),
            "tarjoaja": data.get("provider")}


def _itse_yhdistelmarivi(mallit: list[str], kokoaja: str, k: dict[str, Any],
                         kysy: Callable[[str, list[dict[str, Any]]], dict[str, Any]], rivi: dict[str, Any]) -> int:
    """Yksi kysymys yhdistelmana: jokainen malli vastaa (perakkain), sitten kokoaja. Taydentaa rivin ja palauttaa
    epaonnistuneiden kutsujen maaran. KutsuEstetty nousee; jo tehtyjen kutsujen kulu on silloinkin rivin kulu_usd:ssa."""
    osat: list[dict[str, Any]] = []
    virheita = 0
    rivi.update(yhdistelma=len(mallit), kokoaja=kokoaja, osat=osat, kulu_usd=0.0)
    try:
        for m in mallit:
            try:
                osat.append({"malli": m, **kysy(m, itse_viestit(k))})
            except openrouter_kulut.KutsuEstetty:
                raise
            except Exception as e:  # noqa: BLE001 - vain tyyppi talteen, ei viestia
                virheita += 1
                osat.append({"malli": m, **virherivi(e)})
            rivi["kulu_usd"] = sum(o.get("kulu_usd", 0.0) for o in osat)
        vastaukset = [o["vastaus"] for o in osat if o.get("vastaus")]   # tyhja jasenvastaus ei mene kokoajalle
        rivi["vastauksia"] = len(vastaukset)
        jasenaika = max([o["sekuntia"] for o in osat if "sekuntia" in o] or [0.0])
        perakkain = sum(o.get("sekuntia", 0.0) for o in osat)
        if not vastaukset and any(o.get("tyhja") for o in osat):
            # jasenet vastasivat tyhjaa (ei poikkeusta): ei kokoajan kutsua, rivi = "ei vastausta"
            rivi.update(vastaus="", tyhja=True, finish_reason=None, sekuntia=round(jasenaika, 2),
                        sekuntia_perakkain=round(perakkain, 2))
            return virheita
        if not vastaukset:
            rivi.update(virhe="ei vastauksia", sekuntia=round(jasenaika, 2), sekuntia_perakkain=round(perakkain, 2))
            if all(o.get("aikaraja") for o in osat):
                rivi["aikaraja"] = True          # kaikki jasenet aikakatkaisuun: "aikaraja", ei "vaarin"
            return virheita
        try:
            lopullinen = kysy(kokoaja, itse_kokoajan_viestit(k, vastaukset))
        except openrouter_kulut.KutsuEstetty:
            raise
        except Exception as e:  # noqa: BLE001 - vain tyyppi talteen; ei korvaavaa vastausta, pisteytys = vaarin
            virheita += 1
            rivi.update(virherivi(e), sekuntia=round(jasenaika, 2), sekuntia_perakkain=round(perakkain, 2))
            return virheita
        rivi["kulu_usd"] += lopullinen["kulu_usd"]
        # sekuntia = arvio rinnakkaisesta ajosta (hitain vastaaja + kokoaja); perakkainen aika talteen erikseen
        # kokoajan tyhja vastaus: rivin vastaus on tyhja ja tyhja=True -> pisteytyksessa "ei vastausta"
        rivi.update(vastaus=lopullinen["vastaus"], tyhja=lopullinen["tyhja"], finish_reason=lopullinen["finish_reason"],
                    ajatteli=lopullinen["ajatteli"], tokenit_paattely=lopullinen["tokenit_paattely"],
                    sekuntia=round(jasenaika + lopullinen["sekuntia"], 2),
                    sekuntia_perakkain=round(perakkain + lopullinen["sekuntia"], 2),
                    kokoaja_kulu_usd=lopullinen["kulu_usd"], tokenit_sisaan=lopullinen["tokenit_sisaan"],
                    tokenit_ulos=lopullinen["tokenit_ulos"], tarjoaja=lopullinen["tarjoaja"])
        return virheita
    finally:
        rivi["kulu_usd"] = round(rivi["kulu_usd"], 6)


def aja_itsetuntemus(mallit: list[str], ulos: Path, laheta_tehdas: Callable[[], Callable[[dict], Any]], *,
                     toistot: int = 1, raja: float = ITSE_KULURAJA_USD, kulutila: Any = None,
                     kello: Callable[[], float] = time.monotonic, loki: Callable[[str], Any] = print,
                     kokoaja: str | None = None) -> dict[str, Any]:
    """Ajaa itsetuntemuksen kysymykset. Kirjoittaa vastaukset.jsonl ja ajo.json. Pysahtyy kuten aja().
    Kokoajan kanssa (yhdistelmakoe, ks. itse_yhdistelma) jokainen kysymys kysytaan kaikilta mallilistan malleilta ja
    kokoaja muodostaa lopullisen vastauksen (len(mallit) + 1 kutsua); rivin malli on itse_yhdistelman_nimi ja
    yhteenveto kirjoitetaan tiedostoon ajo-yhdistelma-N.json. Kuluraja tarkistetaan ennen jokaista kysymysta koko
    kysymyksen kutsujen varauksella."""
    kys = itsetuntemus.lue_kysymykset()["kysymykset"]
    itse_mallit(mallit)                                       # kaikki tarkistetaan ennen ensimmaista kutsua
    if kokoaja is not None:
        itse_yhdistelma(mallit, len(mallit), kokoaja)
    toistot = min(max(int(toistot), 1), ITSE_MAX_TOISTOT)
    per_kysymys = 1 if kokoaja is None else len(mallit) + 1
    ajettavat = list(mallit) if kokoaja is None else [itse_yhdistelman_nimi(mallit, kokoaja)]
    ulos.mkdir(mode=0o700, parents=True, exist_ok=True)
    kulut, virheet, suurin, pysahtyi = 0.0, 0, 0.0, ""
    kirjatut: list[dict[str, Any]] = []

    def kysy(malli: str, viestit: list[dict[str, Any]]) -> dict[str, Any]:
        return _itse_kutsu(malli, viestit, laheta_tehdas, kulutila, kello)

    with (ulos / "vastaukset.jsonl").open("a", encoding="utf-8") as f:
        for nimi in ajettavat:
            perakkain = 0
            for toisto in range(1, toistot + 1):
                for k in kys:
                    if perakkain >= 2 or pysahtyi:
                        break
                    if kulut + max(0.05 * per_kysymys, 2 * suurin) > raja:
                        pysahtyi = "kuluraja"
                        break
                    rivi: dict[str, Any] = {"malli": nimi, "kysymys": k["id"], "laji": k["laji"], "toisto": toisto}
                    if kokoaja is None:
                        t0 = kello()
                        try:
                            rivi.update(kysy(nimi, itse_viestit(k)))
                            kulu, uudet_virheet = rivi["kulu_usd"], 0
                        except openrouter_kulut.KutsuEstetty:
                            pysahtyi = "paivakatto"
                            break
                        except Exception as e:  # noqa: BLE001 - vain tyyppi talteen, ei viestia
                            rivi.update(virherivi(e), sekuntia=round(kello() - t0, 2))
                            kulu, uudet_virheet = 0.0, 1
                    else:
                        try:
                            uudet_virheet = _itse_yhdistelmarivi(mallit, kokoaja, k, kysy, rivi)
                        except openrouter_kulut.KutsuEstetty:
                            pysahtyi = "paivakatto"   # keskeytynyt kysymys: ei rivia, jo tehty kulu yhteenvetoon
                            kulut += rivi.get("kulu_usd", 0.0)
                            break
                        kulu = rivi["kulu_usd"]
                    kulut += kulu
                    suurin = max(suurin, kulu)
                    virheet += uudet_virheet
                    perakkain = perakkain + 1 if "virhe" in rivi else 0
                    if virheet >= MAX_VIRHEITA:
                        pysahtyi = "virheita"
                    f.write(json.dumps(rivi, ensure_ascii=False) + "\n")
                    f.flush()
                    kirjatut.append(rivi)
                    loki("%s %s/%d %s" % (nimi, k["id"], toisto, loki_tila(rivi)))
    yhteenveto = {"kulut_usd": round(kulut, 4), "virheita": virheet, "pysahtyi": pysahtyi or None,
                  "mallit": mallit, "toistot": toistot, "raja_usd": raja, **tyhjien_yhteenveto(kirjatut)}
    if kokoaja is not None:
        # yhdistelmassa rivin malli on yhdistelman nimi: listan ulkopuoliset haetaan jasenista ja kokoajasta
        osat = [o for r in kirjatut for o in r.get("osat", [])]
        kokoajat = [{**r, "malli": kokoaja} for r in kirjatut]
        yhteenveto["ajattelevat_listan_ulkopuolella"] = tyhjien_yhteenveto(osat + kokoajat)["ajattelevat_listan_ulkopuolella"]
        yhteenveto["jasenten_tyhjat"] = sum(1 for o in osat if o.get("tyhja"))
        yhteenveto["jasenten_aikarajat"] = sum(1 for o in osat if o.get("aikaraja"))
    tiedosto = "ajo.json"
    if kokoaja is not None:
        yhteenveto.update(yhdistelma=len(mallit), kokoaja=kokoaja, kutsuja_per_kysymys=per_kysymys)
        tiedosto = "ajo-yhdistelma-%d.json" % len(mallit)
    (ulos / tiedosto).write_text(json.dumps(yhteenveto, ensure_ascii=False, indent=1), encoding="utf-8")
    return yhteenveto


# --- Viikoittainen ajo tuotannon malleilla (systemd/aivo-itsetuntemus.*.mall, docs/aivo-itsetuntemus.md) ---------
# Mallit luetaan samoista asetuksista kuin palvelussa: AIVO_OPENROUTER_MALLIT (ensimmainen = paamalli) ja
# AIVO_YKSITYINEN_ROOLIT (rooli tarkastaja; puuttuessa paamalli). Tiivistelmassa on vain luvut, ei vastauksia.
ITSE_VIIKKO_RAJA_USD = 1.0
ITSE_VIIKKO_HISTORIA = "historia.jsonl"
ITSE_VIIKKO_HISTORIA_MAX = 260                # noin viisi vuotta viikkoajoja; vanhimmat rivit pois
ITSE_VIIKKO_TIIVISTELMA = "viimeisin.md"
ITSE_VIIKKO_JSON = "viimeisin.json"


def itse_tuotannon_mallit(env: Any) -> dict[str, str]:
    """{"paamalli": m, "tarkastaja": m} palvelun asetuksista. Virheellinen asetus -> YksityinenVirhe."""
    mallit = yksityinen_or.tarkista_mallit(env.get("AIVO_OPENROUTER_MALLIT") or env.get("AIVO_OPENROUTER_MALLI"))
    roolit = yhdistelma.jasenna_roolit(env.get("AIVO_YKSITYINEN_ROOLIT"), mallit)
    return {"paamalli": mallit[0], "tarkastaja": roolit.get("tarkastaja") or mallit[0]}


def _itse_viikko_rivi(aika: str, roolit: dict[str, str], yv: dict[str, Any], tulos: dict[str, dict[str, Any]],
                      kysymyksia: int, rivit: list[dict[str, Any]]) -> dict[str, Any]:
    """vastattu = rivit, joilla on vastaus (virherivi lasketaan pisteissa vaaraksi, mutta ei vastatuksi; tyhja vastaus
    (tyhja=True) ja aikakatkaisu (aikaraja=True) eivat ole vastattuja). Ajo on kesken, jos se pysahtyi, jos yksikin
    kutsu epaonnistui tai jos vastauksia puuttuu: silloin se ei ole vertailukohta."""
    mallit = {}
    for m, t in tulos.items():
        vastattu = sum(1 for r in rivit if r.get("malli") == m and "vastaus" in r and "virhe" not in r
                       and not r.get("tyhja") and not r.get("aikaraja"))
        mallit[m] = {"yhteensa": t["yhteensa"], "lajit": dict(t["lajit"]), "luokat": dict(t["luokat"]),
                     "vastattu": vastattu, "kulu_usd": t["kulu"], "mediaani_s": t["mediaani_s"]}
    for m in yv["mallit"]:
        mallit.setdefault(m, {"yhteensa": None, "lajit": {}, "luokat": {}, "vastattu": 0, "kulu_usd": 0.0,
                              "mediaani_s": None})
    kesken = bool(yv.get("pysahtyi")) or yv["virheita"] > 0 or any(v["vastattu"] < kysymyksia for v in mallit.values())
    return {"aika": aika, "roolit": dict(roolit), "kysymyksia": kysymyksia, "kulut_usd": yv["kulut_usd"],
            "raja_usd": yv["raja_usd"], "pysahtyi": yv.get("pysahtyi"), "virheita": yv["virheita"],
            "kesken": kesken, "mallit": mallit}


def _lue_historia(polku: Path) -> list[dict[str, Any]]:
    if not polku.exists():
        return []
    rivit = []
    for r in polku.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(r)
        except ValueError:
            continue                                       # rikkinainen rivi ei kaada ajoa
        if isinstance(d, dict) and isinstance(d.get("mallit"), dict):
            rivit.append(d)
    return rivit


def _edellinen(historia: list[dict[str, Any]], malli: str) -> dict[str, Any] | None:
    """Uusin aiempi taysi (ei kesken) ajo, jossa malli oli mukana."""
    for r in reversed(historia):
        if not r.get("kesken") and isinstance(r["mallit"].get(malli), dict):
            return r["mallit"][malli]
    return None


def _muutos(nyt: Any, ennen: Any) -> str:
    if not isinstance(nyt, (int, float)):
        return "-"
    if not isinstance(ennen, (int, float)):
        return "%.2f (uusi)" % nyt
    return "%.2f (%+.2f)" % (nyt, nyt - ennen)


def itse_viikko_teksti(rivi: dict[str, Any], historia: list[dict[str, Any]]) -> str:
    """Tiivistelma Markdownina: pisteet luokittain ja muutos edelliseen tayteen ajoon. Ei vastauksia."""
    roolit = rivi["roolit"]
    osat = ["# Aivon itsetuntemus: viikkoajo %s" % rivi["aika"], "",
            "Paamalli %s, tarkastaja %s. Kysymyksia %d. Kulut %.4f USD (raja %.2f USD)." % (
                roolit["paamalli"], roolit["tarkastaja"], rivi["kysymyksia"], rivi["kulut_usd"], rivi["raja_usd"])]
    if rivi["kesken"]:
        osat.append("HUOM: ajo jai kesken (pysahtyi: %s, virheita %d). Pisteet eivat ole vertailukelpoisia." % (
            rivi.get("pysahtyi") or "osa vastauksista puuttuu", rivi["virheita"]))
    osat += ["", "Suluissa muutos edelliseen tayteen ajoon. Pisteet 0-1; mekaaninen pisteytys on suuntaa antava.", "",
             "| malli | yhteensa | " + " | ".join(itsetuntemus.LAJIT) + " | vastattu | arvaus faktana |",
             "|---|---|" + "---|" * len(itsetuntemus.LAJIT) + "---|---|"]
    for m, t in rivi["mallit"].items():
        e = _edellinen(historia, m) or {}
        lajit = [_muutos(t["lajit"].get(l), (e.get("lajit") or {}).get(l)) for l in itsetuntemus.LAJIT]
        osat.append("| %s | %s | %s | %d/%d | %d |" % (m, _muutos(t["yhteensa"], e.get("yhteensa")), " | ".join(lajit),
                                                     t["vastattu"], rivi["kysymyksia"],
                                                     (t.get("luokat") or {}).get("arvaus_faktana", 0)))
    return "\n".join(osat) + "\n"


def _kirjoita_atomisesti(polku: Path, teksti: str) -> None:
    tmp = polku.with_name("." + polku.name + ".tmp")
    tmp.write_text(teksti, encoding="utf-8")
    tmp.replace(polku)


def itse_viikko(env: Any, ulos: Path, tiivistelma: Path, laheta_tehdas: Callable[[], Callable[[dict], Any]], *,
                raja: float = ITSE_VIIKKO_RAJA_USD, nyt: Any = None, kulutila: Any = None,
                loki: Callable[[str], Any] = print) -> dict[str, Any]:
    """Viikkoajo: tuotannon mallit (paamalli ja tarkastaja, sama malli kerran), oma ajokansio ulos/<aika>,
    ajon raja enintaan ITSE_VIIKKO_RAJA_USD. Tiivistelma: tiivistelma/viimeisin.md ja .json seka historia.jsonl."""
    import datetime as _dt
    if not isinstance(raja, (int, float)) or isinstance(raja, bool) or not 0 < raja <= ITSE_VIIKKO_RAJA_USD:
        raise VertailuVirhe("viikkoajon raja pitaa olla 0-%.2f USD" % ITSE_VIIKKO_RAJA_USD)
    roolit = itse_tuotannon_mallit(env)
    mallit = itse_mallit(list(dict.fromkeys(roolit.values())))
    nyt = nyt or _dt.datetime.now(_dt.timezone.utc)
    leima = nyt.strftime("%Y-%m-%dT%H%M%SZ")
    ajo = ulos / leima
    if ajo.exists():
        raise VertailuVirhe("ajokansio on jo olemassa")
    ulos.mkdir(mode=0o700, parents=True, exist_ok=True)
    tiivistelma.mkdir(mode=0o700, parents=True, exist_ok=True)
    yv = aja_itsetuntemus(mallit, ajo, laheta_tehdas, raja=float(raja), kulutila=kulutila, loki=loki)
    kysymyksia = len(itsetuntemus.lue_kysymykset()["kysymykset"])
    vastausrivit = itsetuntemus.lue_vastaukset(ajo)
    tulos = itsetuntemus.pisteet(vastausrivit)
    rivi = _itse_viikko_rivi(nyt.isoformat(timespec="seconds"), roolit, yv, tulos, kysymyksia, vastausrivit)
    hpolku = tiivistelma / ITSE_VIIKKO_HISTORIA
    historia = _lue_historia(hpolku)
    _kirjoita_atomisesti(tiivistelma / ITSE_VIIKKO_TIIVISTELMA, itse_viikko_teksti(rivi, historia))
    _kirjoita_atomisesti(tiivistelma / ITSE_VIIKKO_JSON, json.dumps(rivi, ensure_ascii=False, indent=1) + "\n")
    uusi = (historia + [rivi])[-ITSE_VIIKKO_HISTORIA_MAX:]
    _kirjoita_atomisesti(hpolku, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in uusi))
    return rivi


def main(argv: list[str] | None = None, env: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="aivo.mallivertailu")
    ap.add_argument("komento", choices=("aja", "paketti", "taulukko", "itse-arvio", "itse-aja", "itse-taulukko",
                                        "itse-viikko"))
    ap.add_argument("--ulos")
    ap.add_argument("--mallit")
    ap.add_argument("--toistot", type=int)
    ap.add_argument("--paattely", type=int, default=0,
                    help="aja: tuotannon paattelyraja (esim. 2000) malleille, joille tuotanto sen lahettaa; 0 = ei")
    ap.add_argument("--avain", default=yksityinen_or.OLETUS_AVAIN)
    ap.add_argument("--tuomari", help="JSON {nayte: pisteet 0-10}")
    ap.add_argument("--vahvista", action="store_true", help="itse-aja: tee oikeat kutsut (ilman tata vain kuluarvio)")
    ap.add_argument("--yhdistelma", type=int, help="itse-arvio/itse-aja: N mallia (3 tai 5) vastaa, kokoaja kokoaa")
    ap.add_argument("--kokoaja", help="itse-arvio/itse-aja: kokoajan malli (AVOIMET_MALLIT), vaatii --yhdistelma")
    ap.add_argument("--tiivistelma", help="itse-viikko: tiivistelman kansio (vain luvut)")
    ap.add_argument("--raja", type=float, default=ITSE_VIIKKO_RAJA_USD,
                    help="itse-viikko: ajon kuluraja USD, enintaan %.2f" % ITSE_VIIKKO_RAJA_USD)
    a = ap.parse_args(argv)
    if a.komento != "itse-arvio" and not a.ulos:
        ap.error("--ulos puuttuu")
    ulos = Path(a.ulos) if a.ulos else None
    try:
        if a.komento == "aja":
            avain = yksityinen_or.lue_avain(a.avain)
            mallit = ",".join(EHDOKKAAT) if a.mallit is None else a.mallit
            toistot = TOISTOT if a.toistot is None else a.toistot
            yv = aja([m.strip() for m in mallit.split(",") if m.strip()], ulos,
                     kuljetin_tehdas(avain), toistot=min(max(toistot, 1), TOISTOT),
                     paattely=max(0, min(a.paattely, yksityinen_or.PAATTELY_MAX_YLARAJA)))
            print(json.dumps(yv, ensure_ascii=False))
        elif a.komento == "paketti":
            print("naytteita", paketti(ulos))
        elif a.komento == "taulukko":
            tuomari = json.loads(Path(a.tuomari).read_text(encoding="utf-8")) if a.tuomari else None
            tulos = pisteet(ulos, tuomari)
            print(taulukko(tulos) + "\n\n" + sokkonaytteet(ulos, tulos))
        elif a.komento == "itse-viikko":
            if not a.tiivistelma or a.mallit or a.yhdistelma or a.kokoaja or a.toistot:
                ap.error("itse-viikko: anna --ulos ja --tiivistelma; mallit tulevat palvelun asetuksista")
            avain = yksityinen_or.lue_avain(a.avain)
            rivi = itse_viikko(os.environ if env is None else env, ulos, Path(a.tiivistelma),
                               kuljetin_tehdas(avain), raja=a.raja)
            print(json.dumps({k: rivi[k] for k in ("aika", "roolit", "kulut_usd", "pysahtyi", "virheita", "kesken")},
                             ensure_ascii=False))
            print(json.dumps({m: t["yhteensa"] for m, t in rivi["mallit"].items()}, ensure_ascii=False))
            return 1 if rivi["kesken"] else 0
        elif a.komento == "itse-taulukko":
            print(itsetuntemus.taulukko(itsetuntemus.pisteet(itsetuntemus.lue_vastaukset(ulos))))
        else:
            mallit = itse_mallit([m.strip() for m in (NYKYINEN if a.mallit is None else a.mallit).split(",") if m.strip()])
            n, kokoaja = itse_yhdistelma(mallit, a.yhdistelma, a.kokoaja)
            toistot = min(max(1 if a.toistot is None else a.toistot, 1), ITSE_MAX_TOISTOT)
            print(itse_arvioteksti(itse_kuluarvio(mallit, toistot, kokoaja=kokoaja)))
            if a.komento == "itse-aja":
                if not a.vahvista:
                    print("Ei ajettu. Oikea ajo: lisaa --vahvista.")
                    return 2
                avain = yksityinen_or.lue_avain(a.avain)
                yv = aja_itsetuntemus(mallit, ulos, kuljetin_tehdas(avain), toistot=toistot,
                                      kokoaja=kokoaja)
                print(json.dumps(yv, ensure_ascii=False))
    except (VertailuVirhe, itsetuntemus.ArviointiVirhe, yksityinen.YksityinenVirhe, OSError, ValueError, KeyError) as e:
        print("virhe:", type(e).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
