"""ZDR-pakotus OpenRouter-pyyntoihin (kompassi A831, A728).

Puhdas funktio: rakentaa pyynnon rungon ja asettaa aina `provider.zdr = true`
(nollassailytys) ja `provider.data_collection = "deny"`. Ei verkkokutsua, ei avainten lukua. Mallia tai avainta
ei kovakoodata: ne tulevat kutsujalta.

Saannot:
  * Kutsuja ei voi ohittaa zdr:aa (zdr=False, provider.zdr != True tai
    ylimaarainen `zdr`-avain) -> ZdrVirhe.
  * Samassa kohdassa pakotetaan `provider.data_collection = "deny"` (A912): palveluntarjoaja
    ei saa kayttaa dataa koulutukseen. Ohitusyritys (`"allow"`, iso avain, ylimaarainen
    avain) -> ZdrVirhe. Taman moduulin kautta kulkevat kaikki henkilotietoa sisaltavat
    pyynnot; muut pyynnot eivat kulje taman funktion kautta, joten niihin ei koske mikaan.
  * `yksityinen_chat=True` (muutettu avoimesti Tonyn paatoksella A984; aiemmin A728: ei koskaan): sallitaan vain, kun
    (a) ZDR ja data_collection=deny on pakotettu (rakenne ei salli muuta: ohitusyritys -> ZdrVirhe) ja (b) malli on
    avoin malli tarkalta listalta AVOIMET_MALLIT. Muuten ZdrVirhe. Arvon pitaa olla tasmalleen True tai False. Lippu ei
    poista muita saantoja: katto, avain ja ZDR pysyvat ennallaan. Lippu kuuluu antaa aina yksityisen tilan kutsuissa
    (aivo/yksityinen_or.py), jotta saantoa ei voi kiertaa jattamalla se pois silta, missa sita tarvitaan.
  * Tata moduulia ei ole kytketty mihinkaan ajoon.
  * `OSOITE` on ainoa kiintea tieto: rajapinnan osoite on vain tassa tiedostossa (staattinen vartija
    test_openrouter_zdr.py tarkistaa, ettei se esiinny muualla aivo/-kansiossa). Moduuli ei itse kayta sita.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


OSOITE = "https://openrouter.ai/api/v1/chat/completions"

# Avoimet mallit (A984): TARKKA lista OpenRouterin mallitunnisteita, ei etuliitteita. Kommentissa on mallin
# `hugging_face_id` OpenRouterin mallilistasta (/api/v1/models, haettu 19.11.2026): kentta kertoo, etta painot on
# julkaistu. Listalla on vain malleja, joilla on seka `hugging_face_id` etta ZDR-endpoint (/api/v1/endpoints/zdr).
# Miksi tarkka lista eika perhesaanto: sama perhe sisaltaa seka avoimia etta suljettuja malleja, eika nimesta voi
# paatella kumpi on kyseessa (esim. qwen/qwen3.8-27b on avoin, qwen/qwen3.8-max-0902 ja qwen/qwen3.7-flash suljettuja,
# mutta "-flash" ei aina tarkoita suljettua). Etuliite `qwen/qwen3` paasti lapi 14 suljettua mallia. Tarkka lista
# hylkaa myos tulevat suljetut variantit ilman erillista saantoa. Uuden mallin tai perheen lisays vaatii
# koodimuutoksen ja Tonyn luvan.
AVOIMET_MALLIT = (
    "deepseek/deepseek-chat",                      # deepseek-ai/DeepSeek-V3
    "deepseek/deepseek-chat-v3-0324",              # deepseek-ai/DeepSeek-V3-0324
    "deepseek/deepseek-chat-v3.1",                 # deepseek-ai/DeepSeek-V3.1
    "deepseek/deepseek-r1-0528",                   # deepseek-ai/DeepSeek-R1-0528
    "google/gemma-2-27b-it",                       # google/gemma-2-27b-it
    "google/gemma-3-12b-it",                       # google/gemma-3-12b-it
    "google/gemma-3-27b-it",                       # google/gemma-3-27b-it
    "google/gemma-3-4b-it",                        # google/gemma-3-4b-it
    "meta-llama/llama-3.1-70b-instruct",           # meta-llama/Meta-Llama-3.1-70B-Instruct
    "meta-llama/llama-3.1-8b-instruct",            # meta-llama/Meta-Llama-3.1-8B-Instruct
    "meta-llama/llama-3.2-1b-instruct",            # meta-llama/Llama-3.2-1B-Instruct
    "meta-llama/llama-3.2-3b-instruct",            # meta-llama/Llama-3.2-3B-Instruct
    "meta-llama/llama-3.3-70b-instruct",           # meta-llama/Llama-3.3-70B-Instruct
    "meta-llama/llama-4-maverick",                 # meta-llama/Llama-4-Maverick-17B-128E-Instruct
    "meta-llama/llama-4-scout",                    # meta-llama/Llama-4-Scout-17B-16E-Instruct
    "mistralai/mistral-nemo",                      # mistralai/Mistral-Nemo-Instruct-2407
    "mistralai/mistral-small-24b-instruct-2501",   # mistralai/Mistral-Small-24B-Instruct-2501
    "mistralai/mistral-small-2603",                # mistralai/Mistral-Small-4-119B-2603
    "mistralai/mistral-small-3.1-24b-instruct",    # mistralai/Mistral-Small-3.1-24B-Instruct-2503
    "mistralai/mistral-small-3.2-24b-instruct",    # mistralai/Mistral-Small-3.2-24B-Instruct-2506
    "mistralai/mixtral-8x22b-instruct",            # mistralai/Mixtral-8x22B-Instruct-v0.1
    "qwen/qwen2.5-vl-72b-instruct",                # Qwen/Qwen2.5-VL-72B-Instruct
    "qwen/qwen3-14b",                              # Qwen/Qwen3-14B
    "qwen/qwen3-235b-a22b-2507",                   # Qwen/Qwen3-235B-A22B-Instruct-2507
    "qwen/qwen3-235b-a22b-thinking-2507",          # Qwen/Qwen3-235B-A22B-Thinking-2507
    "qwen/qwen3-30b-a3b",                          # Qwen/Qwen3-30B-A3B
    "qwen/qwen3-30b-a3b-instruct-2507",            # Qwen/Qwen3-30B-A3B-Instruct-2507
    "qwen/qwen3-32b",                              # Qwen/Qwen3-32B
    "qwen/qwen3-coder",                            # Qwen/Qwen3-Coder-480B-A35B-Instruct
    "qwen/qwen3-coder-30b-a3b-instruct",           # Qwen/Qwen3-Coder-30B-A3B-Instruct
    "qwen/qwen3-coder-next",                       # Qwen/Qwen3-Coder-Next
    "qwen/qwen3-next-80b-a3b-instruct",            # Qwen/Qwen3-Next-80B-A3B-Instruct
    "qwen/qwen3-vl-235b-a22b-instruct",            # Qwen/Qwen3-VL-235B-A22B-Instruct
    "qwen/qwen3-vl-30b-a3b-instruct",              # Qwen/Qwen3-VL-30B-A3B-Instruct
    "qwen/qwen3-vl-8b-instruct",                   # Qwen/Qwen3-VL-8B-Instruct
    "qwen/qwen3.5-122b-a10b",                      # Qwen/Qwen3.5-122B-A10B
    "qwen/qwen3.5-27b",                            # Qwen/Qwen3.5-27B
    "qwen/qwen3.5-35b-a3b",                        # Qwen/Qwen3.5-35B-A3B
    "qwen/qwen3.5-397b-a17b",                      # Qwen/Qwen3.5-397B-A17B
    "qwen/qwen3.5-9b",                             # Qwen/Qwen3.5-9B
    "qwen/qwen3.6-27b",                            # Qwen/Qwen3.6-27B
    "qwen/qwen3.6-35b-a3b",                        # Qwen/Qwen3.6-35B-A3B
    "qwen/qwen3.8-2.4t-a95b",                      # Qwen/Qwen3.8-2.4T-A95B
    "qwen/qwen3.8-27b",                            # Qwen/Qwen3.8-27B
)
_MALLI_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")
# OpenRouterin malliliitteet (docs: guides/routing/model-variants, luettu 19.11.2026). Tarkka lista hylkaa jo kaikki
# liitteet; tama lista on lisavarmistus ja dokumentaatio. Liitteita voi ketjuttaa (esim. ":nitro:exacto"), joten
# tarkistus etsii liitetta mista kohdasta tahansa.
#   :free     oma endpoint-joukko ja omat ehdot; tietojen kayttoa ei ole dokumentoitu
#   :batch    eraajo Batch API:n kautta omilla endpointeilla; ZDR-endpointteja ei ole listalla
#   :thinking, :extended  vanhentuneet katalogivariantit, jotka vaihtavat endpointin
#   :online   lisaa verkkohaun (ulkoinen kutsu)
#   :nitro, :floor, :exacto  muuttavat reititysta ja sallivat priority- ja flex-tason endpointit
KIELLETYT_LIITTEET = (":free", ":batch", ":thinking", ":extended", ":online", ":nitro", ":floor", ":exacto")
_SALLITUT = frozenset(AVOIMET_MALLIT)


def avoin_malli(malli: Any) -> bool:
    """Tosi vain, jos malli on tasmalleen listalla AVOIMET_MALLIT (ei etuliitetta, ei liitetta)."""
    if not isinstance(malli, str) or not _MALLI_RE.fullmatch(malli):
        return False
    if any(l in malli for l in KIELLETYT_LIITTEET):
        return False
    return malli in _SALLITUT


class ZdrVirhe(ValueError):
    """Pyynto hylatty, koska ZDR-saantoa yritettiin rikkoa."""


def _on_zdr_avain(avain: Any) -> bool:
    """Tosi, jos avain on 'zdr' kirjainkoosta ja ymparoivista valeista riippumatta."""
    return str(avain).strip().lower() == "zdr"


def _on_dc_avain(avain: Any) -> bool:
    """Tosi, jos avain on 'data_collection' kirjainkoosta ja ymparoivista valeista riippumatta."""
    return str(avain).strip().lower() == "data_collection"


# Avaimet, jotka funktio omistaa: kutsuja ei voi antaa niita lisaparametreina.
_VARATUT = frozenset({"model", "messages", "provider"})


def _viestit(viestit: Any) -> list[dict[str, Any]]:
    """Kopioi viestilistan; vaara muoto -> ZdrVirhe (viestin sisaltoa ei nayteta)."""
    if isinstance(viestit, (str, bytes, Mapping)):
        raise ZdrVirhe("viestit pitaa olla lista kuvauksia")
    try:
        tulos = list(viestit)
    except TypeError:
        raise ZdrVirhe("viestit pitaa olla lista kuvauksia") from None
    if not all(isinstance(v, Mapping) for v in tulos):
        raise ZdrVirhe("viestit pitaa olla lista kuvauksia")
    return [dict(v) for v in tulos]


def rakenna_runko(
    malli: str,
    viestit: Iterable[Mapping[str, Any]],
    *,
    yksityinen_chat: bool = False,
    provider: Mapping[str, Any] | None = None,
    **lisa: Any,
) -> dict[str, Any]:
    """Palauttaa OpenRouter chat completions -pyynnon rungon, jossa provider.zdr on True."""
    if yksityinen_chat is not False and yksityinen_chat is not True:
        raise ZdrVirhe("yksityinen_chat pitaa olla True tai False")
    if not isinstance(malli, str) or not malli:
        raise ZdrVirhe("malli puuttuu")
    if yksityinen_chat is True and not avoin_malli(malli):
        raise ZdrVirhe("yksityinen chat sallitaan vain avoimella mallilla (A984)")
    if any(_on_zdr_avain(k) for k in lisa):
        raise ZdrVirhe("zdr ei ole kutsujan asetettavissa")
    if any(_on_dc_avain(k) for k in lisa):
        raise ZdrVirhe("data_collection ei ole kutsujan asetettavissa")
    varatut = _VARATUT.intersection(lisa)
    if varatut:
        raise ZdrVirhe("varattu avain lisaparametreissa")
    prov: dict[str, Any] = {}
    if provider is not None:
        if not isinstance(provider, Mapping):
            raise ZdrVirhe("provider ei ole kuvaus")
        prov = dict(provider)
        # Kaikki zdr-kirjoitusasut ('ZDR', 'Zdr', ' zdr ') kasitellaan samoin:
        # vain tarkka 'zdr': True sallitaan, muuten pyynto hylataan.
        for k, v in prov.items():
            if _on_zdr_avain(k) and not (k == "zdr" and v is True):
                raise ZdrVirhe("provider.zdr pitaa olla True")
            if _on_dc_avain(k) and not (k == "data_collection" and v == "deny"):
                raise ZdrVirhe('provider.data_collection pitaa olla "deny"')
    prov["zdr"] = True
    prov["data_collection"] = "deny"
    runko: dict[str, Any] = dict(lisa)
    runko["model"] = malli
    runko["messages"] = _viestit(viestit)
    runko["provider"] = prov
    return runko
