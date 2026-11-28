"""Aivon yksityisen tilan OpenRouter-taustapaa (Tonyn paatos A984; rajattu ulkoinen mallipalvelu, A728).

Vaihtoehto loopback-mallipalvelimelle (`yksityinen.kysy_paikalliselta`). Muisti, keskustelut ja ydinohjeet pysyvat
palvelun omissa kansioissa; OpenRouterille lahtee jokaisella viestilla koko kehote (ydinohjeet, valitut muistiotteet ja
keskusteluhistoria) ja ZDR on pakotettu.

Saannot (koodi on laki):
  * Ei omaa kulutilaa: oletus on `openrouter_kulut.OLETUS_TILA`, ja paivakatto on sama 10 EUR/vrk (A963, #309).
    Kirjanpito on sen koneen tai kontin oma kansio, jossa koodi ajetaan: Aivon kirjanpito kontissa 122 ja ajurin
    kirjanpito kontissa 115 ovat erilliset, joten katto ei nae toisen kulutusta. `kulutila`-parametri on vain testeja
    varten eika sita voi asettaa palvelun asetuksista.
  * Pyynto rakennetaan aina lipulla `yksityinen_chat=True` (A984): saantomoduuli hyvaksyy sen vain avoimella mallilla.
  * Kaikki kutsut kulkevat `openrouter_kulut.kutsu`n kautta: paivakatkaisin (10 EUR/vrk) tarkistetaan ennen kutsua,
    pyynto rakennetaan `openrouter_zdr.rakenna_runko`lla (ZDR ja data_collection=deny) ja kulu kirjataan.
    Tama moduuli ei rakenna `provider`-rungon osaa eika tuo katon arvoja ulos muutettavaksi.
  * Kuljetin kieltaytyy lahettamasta, jos runko ei sisalla `provider.zdr == True` ja `provider.data_collection == "deny"`
    (toinen lukko ZDR:lle).
  * Malli on asetus eika oletusta ole. Vain tarkan listan AVOIMET_MALLIT mallit kelpaavat; suljetut mallit
    ja kaikki liitteet (`:free`, `:batch`, `:online` ym.) hylataan jo asetuksessa.
  * Malleja voi olla lista (AIVO_OPENROUTER_MALLIT, pilkuin erotettu; tarkista_mallit). Jokaisen pitaa lapaista
    avoin_malli(), muuten palvelu ei kaynnisty. Ensimmainen on oletus. Kaikilla malleilla on sama kulukirjanpito ja
    sama paivakatto.
  * Avain luetaan tiedostosta, jonka omistaa palvelun kayttaja ja jonka oikeudet ovat 0600 tai tiukemmat (ei linkki).
    Avainta ei lueta ymparistomuuttujasta, eika sita kirjoiteta lokiin, virheviesteihin eika muualle.
    Virheista nakyy vain tyyppi ja HTTP-tila.
  * Avain luetaan jokaisella kutsulla (Tony voi vaihtaa tiedoston kayttamatta palvelun uudelleenkaynnistysta) ja ennen
    paivakattovarausta: puuttuva avain ei jata varausta eika anna arvoituksellista virhetta.
  * Usean mallin yhdistelmat (aivo/yhdistelma.py, AIVO_YKSITYINEN_YHDISTELMA): jokainen osakutsu kulkee taman
    moduulin `_kysy_viestit`-funktion kautta, joka tarkistaa mallin (avoin_malli) ja kutsuu `openrouter_kulut.kutsu`a
    lipulla `yksityinen_chat=True`. Paivakatto nostaa yhdistelma.Katkaisu, joka lopettaa seuraavat osakutsut.
  * Tyokalutila ei kayta tata moduulia (testi tarkistaa).
  * Yksityisen alueen keskustelujen tiivistys (aivo/tiivista_yksityinen.py) kayttaa OpenRouteria vain funktion
    `kysy_tiivistys` kautta: sama avain, sama avoimen mallin tarkistus, sama `openrouter_kulut.kutsu` (ZDR,
    data_collection=deny, yhteinen 10 EUR/vrk katto ja kirjaus) ja lippu `yksityinen_chat=True`. Tehtavan tunniste
    on oma (TIIVISTYS_TEHTAVA), jotta tiivistyksen osuus nakyy kulukirjanpidossa.
  * Taustaajo (aivo/taustaajo.py) kayttaa OpenRouteria vain funktion `kysy_taustatyo` kautta: sama avain, sama avoimen
    mallin tarkistus, sama `openrouter_kulut.kutsu` (ZDR, data_collection=deny, yhteinen 10 EUR/vrk katto ja kirjaus),
    lippu `yksityinen_chat=True` ja sama MAX_TOKENIA kuin chatissa. Vain yhden kutsun aikaraja on pidempi
    (TAUSTA_AIKARAJA_S), koska ajattelija saa vastata hitaasti. Tehtavan tunniste on oma (TAUSTA_TEHTAVA).
  * Itsetarkistus (aivo/itsetarkistus.py, AIVO_YKSITYINEN_ITSETARKISTUS): tarkastajan ja korjauksen kutsut kulkevat
    funktion `tarkista_vastaus` kautta `_kysy_viestit`-reittia (avoin_malli, ZDR, yhteinen 10 EUR/vrk katto ja kirjaus,
    lippu `yksityinen_chat=True`). Tehtavan tunniste on oma (TARKISTUS_TEHTAVA), jotta osuus nakyy kulukirjanpidossa.
    Paivakatto nayttaa vastauksen ilman tarkistusta; kutsuraja 4 per viesti koskee yhdistelman ja tarkistuksen summaa.
    Tarkastajan lahteiden raja lasketaan tarkastajamallin kontekstista samalla laskennalla kuin kehotteen raja
    (tarkastajan_lahderaja). Korjauskutsun lisasyotteelle (itsetarkistus.KORJAUS_LISAVARAUS) varataan tila ennen
    muistin ja historian tayttoa (chat: muistin_merkkiraja, tarkista_vastaus: historian_merkkiraja).
  * Vastauksen pituusraja (A1095): OpenRouter-taustapaan MAX_TOKENIA = 8 000 on erillaan paikallisen loopback-mallin
    rajasta (yksityinen.MAX_TOKENIA = 700, i5-8400 ei ehtisi 8 000 tokenia). Yhden mallin chat-kutsun aikaraja on
    CHAT_AIKARAJA_S. Jos vastaus katkeaa pituusrajaan (finish_reason == "length"), chat lisaa loppuun
    KATKENNUT_HUOMAUTUS ja kirjaa stderriin vain tiedon katkeamisesta (ei sisaltoa). Paivakatto ja varaus eivat muutu.
  * Ajatteleva malli (paattely ja vastaus jakavat max_tokens-rajan): jos raja taytty paattelyssa, content on tyhja ja
    finish_reason "length". Silloin kaikki reitit (chat, yhdistelman osakutsu, tiivistys, taustaajo) nostavat
    VastausKatkesi-virheen (YksityinenVirhe), jonka viesti kertoo katkeamisesta; tyhjaa viestia ei palauteta.
    Taustaajon katkennut mutta ei-tyhja vastaus saa loppuun TAUSTA_KATKENNUT_HUOMAUTUS-merkinnan.
  * Kehotteen koko (A1106, aivo/kontekstiraja.py): muisti ja historia tayttavat mallin kontekstin, josta varataan
    vastaukselle MAX_TOKENIA (pienella mallilla enintaan neljannes) ja 10 % marginaali. Yhdistelmassa raja on
    pienimman mukana olevan mallin konteksti, ja roolisyotteelle varataan YHDISTELMA_LISAVARAUS. Kehotteen arvioitu
    ylin hinta pidetaan enintaan kiintean varauksen (openrouter_kulut.VARAUS_USD) suuruisena. Jokaisesta kutsusta
    stderriin (journal) kehotteen koko merkkeina ja tokeneina, arvioitu hinta ja toteutuneet tokenit (ei sisaltoa).
    Paikallisen taustapaan rajat (yksityinen.MUISTI_YLARAJA ym.) eivat muutu.
  * Muistin enimmaiskoko (A1123): muistiosio on enintaan MUISTI_MAX_OLETUS (40 000) merkkia tai asetuksen
    AIVO_YKSITYINEN_MUISTI_MAX arvo (MUISTI_MAX_ALARAJA-MUISTI_MAX_YLARAJA). muistin_merkkiraja = min(kontekstiraja,
    asetus), joten asetus voi vain pienentaa kehotetta. Tarkastajan lahteet rajataan samaan kokoon (tarkastajan_lahderaja).
    Toteutuneen kutsun lokiin kirjataan myos usage.prompt_tokens_details.cached_tokens (tarjoajan valimuisti).
  * Paattelyn ylaraja (PAATTELY_MAX_OLETUS 2 000, asetus AIVO_YKSITYINEN_PAATTELY_MAX 0-6 000): _kysy_viestit lisaa
    kutsuun reasoning.max_tokens (ja exclude), jos malli on taulukossa PAATTELY_TUKI. Raja on aina pienempi kuin
    max_tokens, joten vastaukselle jaa tilaa. Muut mallit kutsutaan ilman parametria. Sama raja koskee chattia,
    yhdistelman osakutsuja, vartijaa seka itsetarkistuksen tarkastajaa ja korjausta. Yhden mallin chat tekee enintaan
    yhden uusinnan ilman paattelya (uusinnan_parametri), jos vastaus katkeaa silti ennen tekstia; uusinta kulkee saman
    openrouter_kulut.kutsu-reitin kautta (paivakatto) ja lasketaan kutsurajaan (Vastausteksti.kutsuja ->
    itsetarkistuksen `kaytetyt`). Taustaajo (kysy_taustatyo) ja tiivistys (kysy_tiivistys) saavat saman rajan ilman
    uusintaa. Raja lahetetaan vain malleille, jotka paattelevat oletuksena (OLETUKSENA_PAATTELEVAT, yhteinen
    mallivertailun kanssa); valinnaisen paattelyn malleilla parametri kytkisi paattelyn paalle. Toteutuneen kutsun
    lokiin kirjataan usage.completion_tokens_details.reasoning_tokens, jos OpenRouter ilmoittaa sen.
  * Epaonnistunut kutsu (_kysy_viestit): stderriin (journal) yksi rivi `yksityinen_or: virhe tehtava .., malli ..,
    luokka .., http .., koodi .., kesto .. s` (loki_virhe). Virheen vapaata tekstia ei kirjata, koska se voi sisaltaa
    kehotteen osia; OpenRouterin error.code tai -type kirjataan vain siistittyna lyhyena tunnisteena. Virheluokat
    (HttpVirhe, YhteysVirhe, PalveluVirhe, TyhjaVastaus, Aikakatkaisu, VastausKatkesi) ovat YksityinenVirheen
    aliluokkia, ja niiden viestit ovat samat kuin ennen."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from . import itsetarkistus, kontekstiraja, openrouter_kulut, yhdistelma, yksityinen
from .yksityinen import YKSITYINEN_KEHOTE, YksityinenVirhe

OLETUS_AVAIN = "/var/lib/aivo-yksityinen/openrouter-avain"
TEHTAVA = "aivo-yksityinen"
VASTAUS_AIKARAJA_S = 120
MAX_VASTAUS_TAVUA = 2_000_000
# OpenRouter-taustapaan vastausraja (Tonyn paatos A1095): noin 20 000-24 000 merkkia suomea. Paikallisen
# loopback-mallin raja on yksityinen.MAX_TOKENIA (700) eika muutu. Kulu: avoimella mallilla 8 000 tokenia ulos maksaa
# ZDR-ylarajahinnalla (0,825 USD/Mtok) noin 0,007 USD, selvasti alle kiintean varauksen (openrouter_kulut.VARAUS_USD 0,50 USD).
MAX_TOKENIA = 8_000
# Yhden mallin chat-kutsun aikaraja. stream=False: palvelin ei laheta mitaan ennen kuin koko vastaus on valmis.
# Hitaat avoimet mallit (esim. deepseek-r1) tuottavat OpenRouterissa noin 25-40 tokenia/s; 8 000 / 25 = 320 s ja
# kehotteen kasittely noin 10-30 s, joten 360 s riittaa pahimmassakin tavallisessa tapauksessa. Selain (fetch ilman
# AbortControlleria) ja ThreadingHTTPServer eivat katkaise odotusta. Yhdistelmat, tiivistys ja agentit kayttavat
# edelleen VASTAUS_AIKARAJA_S:aa (120 s) tai omaa rajaansa. Mallivertailu kayttaa samaa rajaa kuin chat
# (mallivertailu.VERTAILU_AIKARAJA_S), koska sen ajattelevat mallit saavat saman 8 000 tokenin vastausrajan.
CHAT_AIKARAJA_S = 360
KATKENNUT_HUOMAUTUS = "[vastaus katkesi pituusrajaan, kirjoita: jatka]"
TAUSTA_KATKENNUT_HUOMAUTUS = "[vastaus katkesi pituusrajaan]"
KATKESI_TYHJANA = ("Vastaus katkesi pituusrajaan ennen kuin malli ehti kirjoittaa vastauksen (ajatteleva malli "
                   "k\u00e4ytti rajan p\u00e4\u00e4ttelyyn). Kokeile uudelleen tai valitse toinen malli.")
# Historia on taustapaakohtainen. Paikallinen malli (yksityinen.HISTORIA_YLARAJA = 12) kasittelee kehotteen hitaasti
# 900 s aikarajan sisalla; OpenRouterin malli kasittelee pitkan kehotteen sekunneissa. Kehotteen koko seuraa mallin
# kontekstia (Tonyn paatos A1106, aivo/kontekstiraja.py): muisti ja historia tayttavat kontekstin, josta on varattu
# vastaus (MAX_TOKENIA) ja 10 % marginaali. Viestimaara HISTORIA_YLARAJA on vain ylaraja; varsinainen raja on
# historian merkkimaara (kontekstiraja.historiaraja). Uusin Tonyn viesti sailyy aina kokonaan, vanhemmat lyhennetaan
# (yksityinen.lyhenna_historia).
# HISTORIA_MERKIT_YLARAJA (80 000) on kiintea raja tyokalutilalle (chat.HISTORIA_MERKIT_YLARAJA) ja mallivertailulle;
# yksityisen tilan OpenRouter-kutsut kayttavat mallikohtaista rajaa.
HISTORIA_YLARAJA = 200
HISTORIA_MERKIT_YLARAJA = 80_000
# Yhdistelman roolisyote (luonnos, tai rinnakkaisen kaksi vastausta, yhdistelma.VASTAUS_MAX_MERKKIA kukin) lisataan
# perusviestien peraan, joten sille varataan tila kehotteesta. Roolin ohjeet ovat alle 500 merkkia.
YHDISTELMA_LISAVARAUS = 2 * yhdistelma.VASTAUS_MAX_MERKKIA + 1_000
# Kehotteen arvioitu ylin hinta enintaan kulukatkaisimen kiintea varaus (luetaan, ei muuteta).
VARAUS_USD_ARVIO = float(openrouter_kulut.VARAUS_USD)
_VARAUS_USD = VARAUS_USD_ARVIO

# Yksityisen tilan muistiosion enimmaiskoko merkkeina (Tonyn paatos A1123). Kontekstiraja (A1106) taytti ison mallin
# kehotteen noin 210 000 merkkiin (noin 0,18 USD viestilta); muistihaku (#449) liittaa kysymykseen osuvat kohdat erikseen,
# joten koko muistia ei tarvitse lahettaa joka viestissa. Asetus (AIVO_YKSITYINEN_MUISTI_MAX) voi vain pienentaa
# kehotetta: muistin_merkkiraja palauttaa min(kontekstiraja, asetus). Sama raja koskee tarkastajan lahteita.
MUISTI_MAX_OLETUS = 40_000
MUISTI_MAX_ALARAJA = 4_000
MUISTI_MAX_YLARAJA = 1_000_000

# Paattelyn ylaraja (reasoning). Ajatteleva malli jakaa max_tokens-rajan paattelyn ja vastauksen kesken; kontin 122
# loki 26.11.2026: qwen3.8-2.4t-a95b kaytti koko 8 000 tokenin rajan paattelyyn (completion_tokens=8000,
# cost=0.0834 USD), eika vastausta tullut. Siksi kutsuun lisataan reasoning.max_tokens (asetus
# AIVO_YKSITYINEN_PAATTELY_MAX, oletus 2 000, sallittu 0-6 000; 0 = reasoning-parametria ei laheteta).
# Raja on aina pienempi kuin max_tokens: vastaukselle jaa vahintaan min(PAATTELY_VASTAUSVARA, max_tokens // 2) tokenia.
PAATTELY_MAX_OLETUS = 2_000
PAATTELY_MAX_YLARAJA = 6_000
PAATTELY_VASTAUSVARA = 2_000
# Oletuksena paattelevat mallit (yhteinen lahde: mallivertailu.AJATTELEVAT_MALLIT on sama joukko). Lahde: OpenRouter,
# docs/use-cases/reasoning-tokens, luettu 25.11.2026; ajo 25.11.: qwen3.5-397b-a17b sai 800 tokenin rajalla 33/33
# tyhjaa vastausta. Mukana paattelymallit (r1, thinking, gpt-oss) ja Qwen3-hybridiperheet, jotka ajattelevat
# oletuksena. Taman joukon ulkopuolinen malli ei saa reasoning-kenttaa, vaikka se tukisi parametria: OpenRouter
# paattelee enabled-tilan kentista, joten valinnaisen paattelyn mallilla (esim. deepseek/deepseek-chat-v3.1,
# mistralai/mistral-small-2603) reasoning.max_tokens kytkisi paattelyn paalle ja kasvattaisi kuluja.
OLETUKSENA_PAATTELEVAT = frozenset({
    "qwen/qwen3.5-397b-a17b",
    "qwen/qwen3-235b-a22b-thinking-2507",
    "deepseek/deepseek-r1-0528",
    "qwen/qwen3.8-2.4t-a95b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "qwen/qwen3.6-35b-a3b",
    "qwen/qwen3.5-122b-a10b",
    "qwen/qwen3.5-27b",
    "qwen/qwen3.5-35b-a3b",
    "qwen/qwen3.5-9b",
    "qwen/qwen3-14b",
    "qwen/qwen3-32b",
    "qwen/qwen3-30b-a3b",
    "openai/gpt-oss-120b",
    "minimax/minimax-m3",
})
# Lahde: OpenRouter, docs/use-cases/reasoning-tokens ja mallilista GET /api/v1/models (kentat
# supported_parameters ja reasoning), luettu 26.11.2026. Mukana vain AVOIMET_MALLIT, joiden supported_parameters
# sisaltaa "reasoning" JA jotka ovat joukossa OLETUKSENA_PAATTELEVAT (raja saa vain pienentaa paattelya, ei kytkea
# sita paalle). Arvo: (pakollinen, tuetut effort-tasot tai None = mallitieto ei kerro tasoja).
#   * Dokumentaation mukaan malli, joka tukee vain effort-tasoja, muuntaa reasoning.max_tokens-arvon tasoksi
#     (osuus max_tokens-rajasta; low noin 20 %, medium 50 %, xhigh 95 %). Naille malleille raja on siis vain
#     taso, ei tarkka tokenmaara. qwen3.8-2.4t-a95b:n oletus on xhigh.
#   * Qwen-malleille reasoning.max_tokens valitetaan thinking_budget-arvona "joillakin malleilla". Etta jokainen
#     listan Qwen-malli noudattaa rajaa, on ARVAUS; uusinta (alla) kattaa tapauksen, jossa raja ei pida.
#   * Listan ulkopuolinen malli (esim. meta-llama/llama-4-maverick, ei "reasoning"-parametria, tai valinnaisen
#     paattelyn deepseek-chat-v3.1 ja mistral-small-2603) kutsutaan ilman reasoning-parametria eika sille tehda uusintaa.
PAATTELY_TUKI: dict[str, tuple[bool, tuple[str, ...] | None]] = {
    "deepseek/deepseek-r1-0528": (True, None),
    "qwen/qwen3-14b": (False, None),
    "qwen/qwen3-235b-a22b-thinking-2507": (True, None),
    "qwen/qwen3-30b-a3b": (False, None),
    "qwen/qwen3-32b": (False, None),
    "qwen/qwen3.5-122b-a10b": (False, None),
    "qwen/qwen3.5-27b": (False, None),
    "qwen/qwen3.5-35b-a3b": (False, None),
    "qwen/qwen3.5-397b-a17b": (False, None),
    "qwen/qwen3.5-9b": (False, None),
    "qwen/qwen3.6-27b": (False, None),
    "qwen/qwen3.6-35b-a3b": (False, None),
    "qwen/qwen3.8-2.4t-a95b": (True, ("xhigh", "medium", "low")),
    "qwen/qwen3.8-27b": (False, ("xhigh", "medium", "low", "none")),
}


def _paattelee_ja_tukee(malli: str) -> bool:
    """reasoning-kentta vain mallille, joka paattelee oletuksena ja tukee parametria (molemmat ehdot)."""
    return malli in PAATTELY_TUKI and malli in OLETUKSENA_PAATTELEVAT


TIIVISTYS_TEHTAVA = "aivo-yksityinen-tiivistys"
# Tiivistys palauttaa JSON:n (enintaan tiivistelma.MAX_KOHDAT kohtaa, tulkinta enintaan 300 merkkia). 700 tokenia
# (paikallisen mallin raja) katkaisee pitkan vastauksen kesken; 1 500 riittaa ja maksaa avoimella mallilla senttien osia.
TIIVISTYS_MAX_TOKENIA = 1_500

TAUSTA_TEHTAVA = "aivo-yksityinen-tausta"

TARKISTUS_TEHTAVA = "aivo-yksityinen-tarkistus"
# Taustaajossa yksi kutsu saa kestaa enintaan 10 min (ajattelija voi miettia minuutteja). Keskustelu ei odota sita.
TAUSTA_AIKARAJA_S = 600

AVOIMET_MALLIT = openrouter_kulut.AVOIMET_MALLIT      # lista on saantomoduulissa (A984); uudelleenvienti
_VANHA_LAUSE = ("sinua ajetaan Tonyn omalla\nkoneella, eik\u00e4 t\u00e4m\u00e4 keskustelu l\u00e4hde millek\u00e4\u00e4n ulkoiselle palvelulle.")
_UUSI_LAUSE = ("sinua ajetaan avoimella mallilla\nOpenRouterin kautta nollas\u00e4ilytyksell\u00e4 (ZDR). Koko t\u00e4m\u00e4 kehote (ydinohjeet, muisti, agenttidata\n"
               "ja keskustelu) l\u00e4htee jokaisella viestill\u00e4 OpenRouterille. ZDR-tilassa sit\u00e4 ei tallenneta eik\u00e4 k\u00e4ytet\u00e4\n"
               "koulutukseen. Muisti ja keskustelut tallennetaan Tonyn omalle palvelimelle.")
KEHOTE = YKSITYINEN_KEHOTE.replace(_VANHA_LAUSE, _UUSI_LAUSE)
_AVAIN_RE = re.compile(r"^[\x21-\x7e]{16,300}$")


avoin_malli = openrouter_kulut.avoin_malli


class VastausKatkesi(YksityinenVirhe):
    """Vastaus katkesi pituusrajaan ennen yhtaan vastaustekstia (finish_reason "length", tyhja content)."""
    syy = "vastaus katkesi pituusrajaan"


class Aikakatkaisu(YksityinenVirhe):
    """Mallipalvelu ei vastannut kuljettimen aikarajassa (TimeoutError, myos URLError(reason=TimeoutError) yhteyden
    avauksessa). Aliluokka, jotta mallivertailu erottaa aikakatkaisun muista virheista; muut kutsujat nakevat sen
    YksityinenVirheena kuten ennen (sama viesti)."""
    syy = "aikaraja: mallipalvelu ei vastannut ajoissa"


# Virheen luokittelu journaliin ja itsetarkistuksen merkintaan. Aliluokkien viestit ovat samat kuin ennen, joten
# kutsujat, jotka nayttavat str(e):n, eivat muutu. Attribuutit: `syy` (lyhyt teksti, ei sisaltoa), `http`
# (HTTP-tila tai None), `koodi` (OpenRouterin error.code tai -type, siistitty, tai None) ja `tilapainen`
# (uusintayritys voi auttaa: HTTP 408/429/5xx, katkennut yhteys tai palvelun virhe vastauksen rungossa).
_KOODI_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")


def _siisti_koodi(arvo: Any) -> str | None:
    if isinstance(arvo, bool):
        return None
    if isinstance(arvo, int):
        return str(arvo)
    if isinstance(arvo, str) and _KOODI_RE.fullmatch(arvo):
        return arvo
    return None


def _virheen_koodi(data: Any) -> str | None:
    """OpenRouterin virherungon {"error": {"code": .., "type": ..}} koodi tai tyyppi. Ei viestia eika metadataa."""
    virhe = data.get("error") if isinstance(data, Mapping) else None
    if not isinstance(virhe, Mapping):
        return None
    return _siisti_koodi(virhe.get("code")) or _siisti_koodi(virhe.get("type"))


class HttpVirhe(YksityinenVirhe):
    """Mallipalvelu vastasi HTTP-virheella. Viesti on sama kuin ennen ("mallipalvelu hylkasi pyynnon (HTTP n)")."""

    def __init__(self, http: int, koodi: str | None = None) -> None:
        super().__init__("mallipalvelu hylkasi pyynnon (HTTP %s)" % http)
        self.http = http
        self.koodi = koodi
        self.tilapainen = http in (408, 429) or http >= 500
        self.syy = "palvelu ei vastannut (HTTP %s)" % http if self.tilapainen else "palvelu hylk\u00e4si pyynn\u00f6n (HTTP %s)" % http


class YhteysVirhe(YksityinenVirhe):
    """Yhteys katkesi tai vastaus ei ollut JSONia (muu OSError tai ValueError kuin aikakatkaisu)."""
    tilapainen = True
    syy = "yhteys mallipalveluun katkesi"


class PalveluVirhe(YksityinenVirhe):
    """HTTP 200, mutta rungossa on virhe eika vastausta (OpenRouter: palveluntarjoaja epaonnistui vastauksen aikana)."""
    tilapainen = True

    def __init__(self, viesti: str, koodi: str | None = None) -> None:
        super().__init__(viesti)
        self.koodi = koodi
        self.syy = "palvelu palautti virheen" + (" (koodi %s)" % koodi if koodi else "")


class TyhjaVastaus(YksityinenVirhe):
    """Vastauksessa ei ollut tekstia (tyhja tai puuttuva content ilman pituusrajaa)."""
    syy = "vastaus oli tyhj\u00e4"


def vastauksen_teksti(data: Any) -> str:
    """Vastauksen teksti (strip). Tyhja content + finish_reason "length" -> VastausKatkesi; muu tyhja tai puuttuva
    -> YksityinenVirhe. Viesteissa ei ole vastauksen sisaltoa."""
    try:
        teksti = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        teksti = None
        if not katkesi_pituusrajaan(data):
            koodi = _virheen_koodi(data)
            if koodi is not None or (isinstance(data, Mapping) and isinstance(data.get("error"), Mapping)):
                raise PalveluVirhe("OpenRouterin vastauksessa ei ollut tekstia", koodi) from None
            raise TyhjaVastaus("OpenRouterin vastauksessa ei ollut tekstia") from None
    if teksti is None or (isinstance(teksti, str) and not teksti.strip()):
        if katkesi_pituusrajaan(data):
            raise VastausKatkesi(KATKESI_TYHJANA)
        valinta = _valinta(data)
        if isinstance(valinta.get("error"), Mapping):
            raise PalveluVirhe("OpenRouter antoi tyhjan vastauksen", _virheen_koodi(valinta))
    if not isinstance(teksti, str) or not teksti.strip():
        raise TyhjaVastaus("OpenRouter antoi tyhjan vastauksen")
    return teksti.strip()


def _valinta(data: Any) -> Mapping[str, Any]:
    try:
        v = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    return v if isinstance(v, Mapping) else {}


def tarkista_malli(malli: Any) -> str:
    if not avoin_malli(malli):
        raise YksityinenVirhe("OpenRouter-malli puuttuu tai ei ole sallittu avoin malli (AVOIMET_MALLIT)")
    return malli


MALLIT_YLARAJA = 10


def tarkista_mallit(mallit: Any) -> tuple[str, ...]:
    """Mallilista asetuksesta: pilkuin erotettu merkkijono tai lista. Jokaisen pitaa lapaista avoin_malli();
    muuten YksityinenVirhe (palvelu ei kaynnisty). Ensimmainen on oletus. Tyhja kohta, kaksoiskappale ja
    liian pitka lista hylataan. Yksi malli (vanha AIVO_OPENROUTER_MALLI) on yhden mallin lista."""
    if isinstance(mallit, str):
        osat = [m.strip() for m in mallit.split(",")]
    elif isinstance(mallit, (list, tuple)):
        osat = [m.strip() if isinstance(m, str) else m for m in mallit]
    else:
        osat = []
    if not osat or osat == [""]:
        raise YksityinenVirhe("OpenRouter-malli puuttuu tai ei ole sallittu avoin malli (AVOIMET_MALLIT)")
    if len(osat) > MALLIT_YLARAJA:
        raise YksityinenVirhe("OpenRouter-malleja on liikaa (enintaan 10, MALLIT_YLARAJA)")
    for m in osat:
        tarkista_malli(m)
    if len(set(osat)) != len(osat):
        raise YksityinenVirhe("OpenRouter-mallilistassa on sama malli kahdesti")
    return tuple(osat)


def lue_avain(polku: Any) -> str:
    """Lukee avaimen tiedostosta. Virheviestissa ei ole avainta eika sen osia."""
    p = Path(str(polku or ""))
    try:
        st = os.lstat(p)
    except OSError:
        raise YksityinenVirhe("OpenRouter-avain puuttuu: avaintiedostoa ei ole tai sita ei voi lukea") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise YksityinenVirhe("OpenRouter-avaintiedoston pitaa olla tavallinen tiedosto, ei linkki")
    if st.st_uid != os.geteuid():
        raise YksityinenVirhe("OpenRouter-avaintiedosto ei kuulu palvelun kayttajalle")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise YksityinenVirhe("OpenRouter-avaintiedoston oikeudet ovat liian laajat (pitaa olla 0600)")
    try:
        avain = p.read_bytes()[:1000].decode("ascii").strip()
    except (OSError, ValueError):
        raise YksityinenVirhe("OpenRouter-avaintiedostoa ei voi lukea") from None
    if not avain:
        raise YksityinenVirhe("OpenRouter-avain puuttuu: avaintiedosto on tyhja")
    if not _AVAIN_RE.fullmatch(avain):
        raise YksityinenVirhe("OpenRouter-avaintiedoston sisalto ei ole avaimen nakoinen")
    return avain


def _zdr_kunnossa(runko: Any) -> bool:
    prov = runko.get("provider") if isinstance(runko, Mapping) else None
    return isinstance(prov, Mapping) and prov.get("zdr") is True and prov.get("data_collection") == "deny"


def _http_virheen_koodi(e: Any) -> str | None:
    """HTTP-virherungon error.code tai -type (enintaan 64 kt luetaan). Viestia ei lueta talteen."""
    try:
        return _virheen_koodi(json.loads(e.read(65_536)))
    except Exception:  # noqa: BLE001 - runko puuttuu tai ei ole JSONia: koodia ei ole
        return None


def tee_kuljetin(avain: str, avaa: Callable[..., Any] | None = None,
                 aikaraja_s: float | None = None,
                 ylaraja_s: float = VASTAUS_AIKARAJA_S) -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    """Kuljetin `openrouter_kulut.kutsu`lle: lahettaa rungon OpenRouteriin. Kieltaytyy, ellei ZDR ole rungossa.
    `aikaraja_s` voi vain lyhentaa ylarajaa (yhdistelman kokonaisaikaraja). `ylaraja_s` on VASTAUS_AIKARAJA_S,
    paitsi taustaajossa (kysy_taustatyo, TAUSTA_AIKARAJA_S)."""
    aikaraja = ylaraja_s if aikaraja_s is None else max(1.0, min(float(aikaraja_s), ylaraja_s))
    if avaa is None:
        avaa = urllib.request.build_opener(urllib.request.ProxyHandler({})).open     # ei valityspalvelinta

    def laheta(runko: dict[str, Any]) -> Mapping[str, Any]:
        if not _zdr_kunnossa(runko):
            raise YksityinenVirhe("ZDR puuttuu pyynnosta; ei lahetetty")
        pyynto = urllib.request.Request(
            openrouter_kulut.OSOITE, data=json.dumps(runko).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + avain})
        try:
            with avaa(pyynto, timeout=aikaraja) as r:
                data = json.loads(r.read(MAX_VASTAUS_TAVUA))
        except urllib.error.HTTPError as e:
            koodi = _http_virheen_koodi(e)
            e.close()                               # vapauttaa virhevastauksen socketin
            raise HttpVirhe(e.code, koodi) from None
        except (OSError, ValueError) as e:
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                raise Aikakatkaisu("mallipalvelu ei vastannut (%s)" % type(e).__name__) from None
            raise YhteysVirhe("mallipalvelu ei vastannut (%s)" % type(e).__name__) from None
        if not isinstance(data, Mapping):
            raise YksityinenVirhe("OpenRouter antoi odottamattoman vastauksen")
        return data
    return laheta


def _kysy_viestit(
    malli: str,
    viestit: list[dict[str, Any]],
    *,
    avain_tiedosto: str = OLETUS_AVAIN,
    kulutila: str | None = None,
    avaa: Callable[..., Any] | None = None,
    nyt: Callable[[], datetime] | None = None,
    ilmoita: Callable[[str], Any] | None = None,
    aikaraja_s: float | None = None,
    max_tokens: int | None = None,
    ylaraja_s: float = VASTAUS_AIKARAJA_S,
    huomauta_katkeamisesta: bool = False,
    tehtava: str = TEHTAVA,
    paattely_max: int = PAATTELY_MAX_OLETUS,
    uusinta: Callable[[], bool] | None = None,
    **lisa: Any,
) -> str:
    """Yksi kutsu valmiilla viestilistalla. Ainoa reitti, jota kysy_openrouterilta ja yhdistelmat kayttavat.
    `huomauta_katkeamisesta`: vain yhden mallin chat (kysy_openrouterilta). Yhdistelman osakutsujen (reititin,
    luokittelu) vastauksia jasennetaan, joten niihin ei lisata huomautusta.
    `paattely_max`: paattelyn ylaraja (paattelyraja, paattelyparametri); koskee kaikkia taman funktion kutsuja.
    `uusinta`: jos vastaus katkeaa paattelyyn ennen tekstia, yksi uusintakutsu uusinnan_parametri-asetuksella, mutta
    vain jos `uusinta()` palauttaa True (kutsija laskee kutsurajan). Uusinta kulkee saman openrouter_kulut.kutsu-reitin
    kautta, joten paivakatto tarkistetaan ennen sita. None = ei uusintaa."""
    tarkista_malli(malli)
    avain = lue_avain(avain_tiedosto)                     # ennen varausta: puuttuva avain ei jata varausta
    laheta = tee_kuljetin(avain, avaa, aikaraja_s, ylaraja_s=ylaraja_s)
    tokenit = MAX_TOKENIA if max_tokens is None else max(1, min(int(max_tokens), MAX_TOKENIA))
    tokenit = min(tokenit, kontekstiraja.vastausvaraus(malli, MAX_TOKENIA))   # pieni konteksti: vastaus ei vie kaikkea
    paattely = paattelyparametri(malli, paattelyraja(malli, tokenit, paattely_max))
    loki_kehote(malli, viestit, tokenit, paattely["max_tokens"] if paattely else 0)
    alku = time.monotonic()

    def kutsu(reasoning: dict[str, Any] | None) -> Mapping[str, Any]:
        try:
            vastaus = openrouter_kulut.kutsu(
                malli, viestit, laheta, tehtava=tehtava, nyt=nyt, tila=kulutila, ilmoita=ilmoita,
                max_tokens=tokenit, temperature=0.3, stream=False, usage={"include": True},
                **{**lisa, **({"reasoning": reasoning} if reasoning else {}), "yksityinen_chat": True})
        except openrouter_kulut.KutsuEstetty:
            raise yhdistelma.Katkaisu("OpenRouterin p\u00e4iv\u00e4katto on t\u00e4ynn\u00e4 tai kulukirjanpito ei kelpaa; kutsua ei tehty") from None
        except openrouter_kulut.ZdrVirhe:
            raise YksityinenVirhe("ZDR-s\u00e4\u00e4nt\u00f6 est\u00e4\u00e4 pyynn\u00f6n; kutsua ei tehty") from None
        loki_toteutunut(malli, vastaus)
        return vastaus

    try:
        data = kutsu(paattely)
        try:
            teksti = vastauksen_teksti(data)
        except VastausKatkesi:
            print("yksityinen_or: vastaus katkesi pituusrajaan ennen tekstia (max_tokens=%d, paattely enintaan %d)"
                  % (tokenit, paattely["max_tokens"] if paattely else 0), file=sys.stderr)
            uusi = uusinnan_parametri(malli) if paattely else None
            if uusi is None or uusinta is None:
                raise
            if not uusinta():
                print("yksityinen_or: uusintaa ei tehty (kutsuraja)", file=sys.stderr)
                raise
            print("yksityinen_or: uusinta kerran ilman paattelya (reasoning %s), malli %s"
                  % (",".join("%s=%s" % kv for kv in sorted(uusi.items()) if kv[0] != "exclude"), str(malli)[:100]),
                  file=sys.stderr)
            data = kutsu(uusi)
            try:
                teksti = vastauksen_teksti(data)
            except VastausKatkesi:
                print("yksityinen_or: uusintakin katkesi pituusrajaan ennen tekstia", file=sys.stderr)
                raise
    except YksityinenVirhe as e:
        loki_virhe(tehtava, malli, e, time.monotonic() - alku)
        raise
    if huomauta_katkeamisesta and katkesi_pituusrajaan(data):
        print("yksityinen_or: vastaus katkesi pituusrajaan (max_tokens=%d)" % tokenit, file=sys.stderr)
        return teksti + "\n\n" + KATKENNUT_HUOMAUTUS
    return teksti


def loki_kehote(malli: str, viestit: list[dict[str, Any]], max_tokens: int, paattely: int = 0) -> dict[str, Any]:
    """Kirjaa stderriin (journal) kehotteen koon ja arvioidun hinnan ennen kutsua. Ei sisaltoa: vain luvut ja malli.
    `paattely`: kutsun reasoning.max_tokens (0 = reasoning-parametria ei lahetetty)."""
    merkit = kontekstiraja.viestien_merkit(viestit)
    tiedot = {"merkit": merkit, "tokenit_arvio": kontekstiraja.arvioi_tokenit(merkit),
              "syoteraja_tokenit": kontekstiraja.syote_tokenit(malli, MAX_TOKENIA, _VARAUS_USD),
              "konteksti": kontekstiraja.konteksti(malli), "max_tokens": int(max_tokens), "paattely": int(paattely)}
    tiedot["usd_arvio"] = kontekstiraja.arvioi_usd(malli, tiedot["tokenit_arvio"], tiedot["max_tokens"])
    print("yksityinen_or: kehote %(merkit)d merkkia, arvio %(tokenit_arvio)d tokenia (syoteraja %(syoteraja_tokenit)d, "
          "konteksti %(konteksti)d, vastaus enintaan %(max_tokens)d, paattely enintaan %(paattely)d), arvioitu hinta "
          "enintaan %(usd_arvio).4f USD" % tiedot + ", malli " + str(malli)[:100], file=sys.stderr)
    return tiedot


def loki_virhe(tehtava: str, malli: str, e: BaseException, kesto_s: float) -> None:
    """Kirjaa stderriin (journal) yhden rivin epaonnistuneesta mallikutsusta: tehtava, malli, poikkeuksen luokka,
    HTTP-tila ja OpenRouterin virhekoodi (jos saatavilla) seka kesto. Ei virheen tekstia, koska se voi sisaltaa
    kehotteen osia."""
    http = getattr(e, "http", None)
    koodi = _siisti_koodi(getattr(e, "koodi", None))
    print("yksityinen_or: virhe tehtava %s, malli %s, luokka %s, http %s, koodi %s, kesto %.1f s"
          % (str(tehtava)[:60], str(malli)[:100], type(e).__name__,
             http if isinstance(http, int) and not isinstance(http, bool) else "-", koodi or "-",
             max(0.0, float(kesto_s))), file=sys.stderr)


def loki_toteutunut(malli: str, data: Any) -> None:
    """Kirjaa OpenRouterin ilmoittamat toteutuneet prompt- ja completion-tokenit ja kulun (ei sisaltoa)."""
    u = data.get("usage") if isinstance(data, Mapping) else None
    if not isinstance(u, Mapping):
        return
    osat = []
    for avain in ("prompt_tokens", "completion_tokens"):
        if isinstance(u.get(avain), int) and not isinstance(u.get(avain), bool):
            osat.append("%s=%d" % (avain, u[avain]))
    # tarjoajan valimuisti (prompt caching): OpenRouter ilmoittaa valimuistista luetut syotetokenit kentassa
    # usage.prompt_tokens_details.cached_tokens (ja kirjoitetut kentassa cache_write_tokens, jos tarjoaja kertoo ne)
    tiedot = u.get("prompt_tokens_details")
    if isinstance(tiedot, Mapping):
        for avain in ("cached_tokens", "cache_write_tokens"):
            arvo = tiedot.get(avain)
            if isinstance(arvo, int) and not isinstance(arvo, bool):
                osat.append("%s=%d" % (avain, arvo))
    # paattelytokenit (usage.completion_tokens_details.reasoning_tokens): osa completion_tokens-maarasta; vain luku
    tiedot = u.get("completion_tokens_details")
    if isinstance(tiedot, Mapping):
        arvo = tiedot.get("reasoning_tokens")
        if isinstance(arvo, int) and not isinstance(arvo, bool):
            osat.append("reasoning_tokens=%d" % arvo)
    if isinstance(u.get("cost"), (int, float)) and not isinstance(u.get("cost"), bool):
        osat.append("cost=%.4f USD" % u["cost"])
    if osat:
        print("yksityinen_or: toteutunut " + ", ".join(osat) + ", malli " + str(malli)[:100], file=sys.stderr)


def historian_merkkiraja(mallit: Any, muisti: str, ohjeet: str, historia: list[dict[str, Any]],
                         lisavaraus_merkit: int = 0) -> int:
    """Historian merkkiraja OpenRouter-kutsulle: mallin konteksti - jarjestelmaviesti (kehote, ydinohjeet, muisti)
    - viestien rakenne - yhdistelman lisavaraus. Uusin Tonyn viesti sailyy lyhenna_historia-funktiossa aina."""
    jarjestelma = len(KEHOTE) + len(ohjeet or "(ei ydinohjeita)\n") + len(muisti or "") + kontekstiraja.JARJESTELMA_LISA
    viesteja = min(len(historia), HISTORIA_YLARAJA)
    # jokaiseen viestiin voi tulla rakenne ja lyhennysmerkinta ("[... vanha viesti lyhennetty: n merkkia pois]")
    per_viesti = kontekstiraja.VIESTI_LISA + len(yksityinen.LYHENNETTY % 10 ** 7)
    return max(0, kontekstiraja.historiaraja(mallit, MAX_TOKENIA, jarjestelma, lisavaraus_merkit, _VARAUS_USD)
               - viesteja * per_viesti)


def tarkista_muisti_max(arvo: Any) -> int:
    """AIVO_YKSITYINEN_MUISTI_MAX: puuttuva tai tyhja -> MUISTI_MAX_OLETUS. Kokonaisluku valilla
    MUISTI_MAX_ALARAJA-MUISTI_MAX_YLARAJA; muu arvo nostaa YksityinenVirhe (palvelu ei kaynnisty)."""
    if arvo is None or (isinstance(arvo, str) and not arvo.strip()):
        return MUISTI_MAX_OLETUS
    if isinstance(arvo, bool):
        raise YksityinenVirhe("yksityisen tilan muistin enimm\u00e4iskoko ei ole kokonaisluku")
    try:
        n = int(str(arvo).strip())
    except ValueError:
        raise YksityinenVirhe("yksityisen tilan muistin enimm\u00e4iskoko ei ole kokonaisluku") from None
    if not MUISTI_MAX_ALARAJA <= n <= MUISTI_MAX_YLARAJA:
        raise YksityinenVirhe("yksityisen tilan muistin enimm\u00e4iskoko pit\u00e4\u00e4 olla %d-%d merkki\u00e4"
                              % (MUISTI_MAX_ALARAJA, MUISTI_MAX_YLARAJA))
    return n


def tarkista_paattely_max(arvo: Any) -> int:
    """AIVO_YKSITYINEN_PAATTELY_MAX: puuttuva tai tyhja -> PAATTELY_MAX_OLETUS. Kokonaisluku 0-PAATTELY_MAX_YLARAJA
    (0 = reasoning-parametria ei laheteta); muu arvo nostaa YksityinenVirhe (palvelu ei kaynnisty)."""
    if arvo is None or (isinstance(arvo, str) and not arvo.strip()):
        return PAATTELY_MAX_OLETUS
    if isinstance(arvo, bool):
        raise YksityinenVirhe("yksityisen tilan p\u00e4\u00e4ttelyn yl\u00e4raja ei ole kokonaisluku")
    try:
        n = int(str(arvo).strip())
    except ValueError:
        raise YksityinenVirhe("yksityisen tilan p\u00e4\u00e4ttelyn yl\u00e4raja ei ole kokonaisluku") from None
    if not 0 <= n <= PAATTELY_MAX_YLARAJA:
        raise YksityinenVirhe("yksityisen tilan p\u00e4\u00e4ttelyn yl\u00e4raja pit\u00e4\u00e4 olla 0-%d tokenia"
                              % PAATTELY_MAX_YLARAJA)
    return n


def paattelyraja(malli: str, max_tokens: int, paattely_max: int = PAATTELY_MAX_OLETUS) -> int:
    """Paattelyn tokenraja kutsulle: 0, jos asetus on 0 tai malli ei paattele oletuksena ja tue reasoning-parametria
    (PAATTELY_TUKI ja OLETUKSENA_PAATTELEVAT).
    Muuten min(asetus, max_tokens - vastausvara), jossa vastausvara = max(1, min(PAATTELY_VASTAUSVARA, max_tokens // 2)).
    Tulos on aina pienempi kuin max_tokens."""
    asetus = max(0, min(int(paattely_max), PAATTELY_MAX_YLARAJA))
    if asetus == 0 or not _paattelee_ja_tukee(malli):
        return 0
    tokenit = max(0, int(max_tokens))
    vara = max(1, min(PAATTELY_VASTAUSVARA, tokenit // 2))
    return max(0, min(asetus, tokenit - vara))


def paattelyparametri(malli: str, raja: int) -> dict[str, Any] | None:
    """reasoning-kentta: {"max_tokens": raja, "exclude": True} tai None (raja 0 tai malli ei tue parametria).
    exclude: paattelyn tekstia ei palauteta (Chat Completions; sita ei kayteta, eika se kuulu vastaukseen)."""
    if raja < 1 or not _paattelee_ja_tukee(malli):
        return None
    return {"max_tokens": int(raja), "exclude": True}


def uusinnan_parametri(malli: str) -> dict[str, Any] | None:
    """Uusintakutsun reasoning-kentta, kun vastaus katkesi paattelyyn: paattely pois, jos malli sallii
    (effort "none" tai enabled False), pakollisella mallilla effort "low", jos malli kertoo tukevansa sita.
    None = uusinnasta ei olisi apua (malli ei tue parametria, tai paattely on pakollinen ilman tasoja)."""
    if not _paattelee_ja_tukee(malli):
        return None
    pakollinen, tasot = PAATTELY_TUKI[malli]
    if not pakollinen:
        if tasot is not None and "none" in tasot:
            return {"effort": "none", "exclude": True}
        return {"enabled": False}
    if tasot is not None and "low" in tasot:
        return {"effort": "low", "exclude": True}
    return None


class Vastausteksti(str):
    """Chatin vastaus (str) ja taman viestin mallikutsujen maara (`kutsuja`, uusinta mukaan lukien). Chat antaa
    maaran itsetarkistukselle, jotta kutsuraja yhdistelma.MAX_KUTSUT_VIESTI koskee summaa."""
    kutsuja: int = 1

    def __new__(cls, teksti: str, kutsuja: int = 1) -> "Vastausteksti":
        olio = super().__new__(cls, teksti)
        olio.kutsuja = int(kutsuja)
        return olio


def muistin_kontekstiraja(mallit: Any, ohjeet: str, historia: list[dict[str, Any]], lisavaraus_merkit: int = 0) -> int:
    """Muistiosion merkkiraja mallin kontekstin mukaan (A1106) ilman muistin enimmaiskokoa."""
    viimeiset = historia[-HISTORIA_YLARAJA:]
    per_viesti = kontekstiraja.VIESTI_LISA + len(yksityinen.LYHENNETTY % 10 ** 7)     # sama kuin historian_merkkiraja
    tarve = sum(len(str(v.get("teksti", ""))) + per_viesti for v in viimeiset)
    # uusin Tonyn viesti ja sita uudemmat sailyvat aina kokonaan (lyhenna_historia): niille tila ensin
    tony = [i for i, v in enumerate(viimeiset) if v.get("rooli") == "tony"]
    suojattu = sum(len(str(v.get("teksti", ""))) + per_viesti for v in viimeiset[tony[-1]:]) if tony else 0
    suojattu += (tony[-1] if tony else 0) * per_viesti      # vanhemmista jaa ainakin rakenne ja lyhennysmerkinta
    kiintea = len(KEHOTE) + len(ohjeet or "(ei ydinohjeita)\n")
    return kontekstiraja.muistiraja(mallit, MAX_TOKENIA, kiintea, tarve, lisavaraus_merkit, _VARAUS_USD, suojattu)


def muistin_merkkiraja(mallit: Any, ohjeet: str, historia: list[dict[str, Any]], lisavaraus_merkit: int = 0,
                       muisti_max: int = MUISTI_MAX_OLETUS) -> int:
    """Muistiosion merkkiraja (kirjasto, agenttidata, korjaukset, hakuosumat ja tiivistelmat yhteensa)
    OpenRouter-taustapaalla: min(kontekstiraja, muisti_max). Historialle jaa aina vahintaan
    kontekstiraja.HISTORIA_VAHIMMAISOSUUS jaljella olevasta tilasta. Asetus voi vain pienentaa rajaa."""
    return min(muistin_kontekstiraja(mallit, ohjeet, historia, lisavaraus_merkit), max(0, int(muisti_max)))


def yhdistelman_mallit(malli: str, roolit: Mapping[str, str] | None) -> list[str]:
    return [malli] + [m for m in (roolit or {}).values() if m != malli]


def yhdistelman_lisavaraus(yhdistelma_nimi: str) -> int:
    return 0 if yhdistelma_nimi == yhdistelma.OLETUS else YHDISTELMA_LISAVARAUS


def katkesi_pituusrajaan(data: Any) -> bool:
    """True, jos OpenRouterin vastauksen ensimmainen valinta paattyi pituusrajaan (finish_reason == "length")."""
    try:
        return data["choices"][0].get("finish_reason") == "length"
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def kysy_openrouterilta(
    muisti: str,
    historia: list[dict[str, Any]],
    ohjeet: str = "",
    *,
    malli: str,
    avain_tiedosto: str = OLETUS_AVAIN,
    kulutila: str | None = None,
    avaa: Callable[..., Any] | None = None,
    nyt: Callable[[], datetime] | None = None,
    ilmoita: Callable[[str], Any] | None = None,
    paattely_max: int = PAATTELY_MAX_OLETUS,
    **lisa: Any,
) -> "Vastausteksti":
    """Yksityisen tilan vastaus OpenRouterin avoimelta mallilta. Nostaa aina YksityinenVirhe (turvallinen viesti).
    Jos vastaus katkeaa paattelyyn ennen tekstia, tehdaan enintaan yksi uusinta (_kysy_viestit), kun kutsuraja
    yhdistelma.MAX_KUTSUT_VIESTI sallii. Paluuarvon `kutsuja` kertoo tehtyjen kutsujen maaran (itsetarkistukselle)."""
    tarkista_malli(malli)
    viestit = yksityinen.muodosta_viestit(muisti, historia, ohjeet, KEHOTE, HISTORIA_YLARAJA,
                                         historian_merkkiraja(malli, muisti, ohjeet, historia))
    kutsuja = [1]

    def uusinta() -> bool:
        if kutsuja[0] >= 2 or kutsuja[0] + 1 > yhdistelma.MAX_KUTSUT_VIESTI:     # kerran ja vain kerran
            return False
        kutsuja[0] += 1
        return True
    teksti = _kysy_viestit(malli, viestit, avain_tiedosto=avain_tiedosto, kulutila=kulutila, avaa=avaa, nyt=nyt,
                           ilmoita=ilmoita, ylaraja_s=CHAT_AIKARAJA_S, huomauta_katkeamisesta=True,
                           paattely_max=paattely_max, uusinta=uusinta, **lisa)
    return Vastausteksti(teksti, kutsuja[0])


def kysy_yhdistelmalla(
    muisti: str,
    historia: list[dict[str, Any]],
    ohjeet: str = "",
    *,
    malli: str,
    yhdistelma_nimi: str = yhdistelma.OLETUS,
    roolit: Mapping[str, str] | None = None,
    kello: Callable[[], float] | None = None,
    ajattelija_aika_s: float = yhdistelma.AJATTELIJA_AIKA_OLETUS_S,
    **kw: Any,
) -> "yhdistelma.Tulos":
    """Usean mallin yhdistelma (yhdistelma.aja). Jokainen osakutsu kulkee _kysy_viestit-funktion kautta."""
    tarkista_malli(malli)
    for m in (roolit or {}).values():
        tarkista_malli(m)
    raja = historian_merkkiraja(yhdistelman_mallit(malli, roolit), muisti, ohjeet, historia,
                                yhdistelman_lisavaraus(yhdistelma_nimi))
    viestit = yksityinen.muodosta_viestit(muisti, historia, ohjeet, KEHOTE, HISTORIA_YLARAJA, raja)

    def kysy(m: str, v: list[dict[str, Any]], aikaraja_s: float | None = None, max_tokens: int | None = None) -> str:
        return _kysy_viestit(m, v, aikaraja_s=aikaraja_s, max_tokens=max_tokens, **kw)
    return yhdistelma.aja(yhdistelma_nimi, roolit or {}, malli, viestit, kysy, ajattelija_aika_s=ajattelija_aika_s,
                          **({"kello": kello} if kello is not None else {}))


class PaivakattoTaynna(YksityinenVirhe):
    """Yhteinen 10 EUR/vrk katto on taynna tai kirjanpito ei kelpaa: kutsua ei tehty eika kulua syntynyt."""


def tiivistyksen_malli(malli: str) -> str:
    """Tiivistelman `malli`-kentan arvo (enintaan 100 merkkia; tiivistelma.py tarkistaa)."""
    return ("zdr:" + tarkista_malli(malli))[:100]


def kysy_tiivistys(
    ohje: str,
    data: str,
    *,
    malli: str,
    avain_tiedosto: str = OLETUS_AVAIN,
    kulutila: str | None = None,
    avaa: Callable[..., Any] | None = None,
    nyt: Callable[[], datetime] | None = None,
    paattely_max: int = PAATTELY_MAX_OLETUS,
) -> str:
    """Yksityisen alueen keskusteluosan tiivistys OpenRouterin avoimella mallilla (ZDR pakotettu).
    `ohje` on jarjestelmaviesti ja `data` keskusteluosa. Virheet: YksityinenVirhe (viestissa ei sisaltoa eika avainta);
    katon tayttyminen: PaivakattoTaynna.
    `paattely_max`: sama paattelyn ylaraja kuin chatissa (paattelyraja); TIIVISTYS_MAX_TOKENIA 1 500 -> paattely
    enintaan 750, jolloin JSON-vastaukselle jaa tilaa. Ei uusintaa: tiivistys ajetaan uudelleen seuraavalla ajolla."""
    tarkista_malli(malli)
    avain = lue_avain(avain_tiedosto)                     # ennen varausta: puuttuva avain ei jata varausta
    laheta = tee_kuljetin(avain, avaa)
    viestit = [{"role": "system", "content": ohje}, {"role": "user", "content": data}]
    paattely = paattelyparametri(malli, paattelyraja(malli, TIIVISTYS_MAX_TOKENIA, paattely_max))
    try:
        vastaus = openrouter_kulut.kutsu(
            malli, viestit, laheta, tehtava=TIIVISTYS_TEHTAVA, nyt=nyt, tila=kulutila,
            max_tokens=TIIVISTYS_MAX_TOKENIA, temperature=0.2, stream=False, usage={"include": True},
            yksityinen_chat=True, **({"reasoning": paattely} if paattely else {}))
    except openrouter_kulut.KutsuEstetty:
        raise PaivakattoTaynna("OpenRouterin p\u00e4iv\u00e4katto on t\u00e4ynn\u00e4 tai kulukirjanpito ei kelpaa; kutsua ei tehty") from None
    except openrouter_kulut.ZdrVirhe:
        raise YksityinenVirhe("ZDR-s\u00e4\u00e4nt\u00f6 est\u00e4\u00e4 pyynn\u00f6n; kutsua ei tehty") from None
    return vastauksen_teksti(vastaus)


def kysy_taustatyo(
    malli: str,
    viestit: list[dict[str, Any]],
    *,
    aikaraja_s: float,
    avain_tiedosto: str = OLETUS_AVAIN,
    kulutila: str | None = None,
    avaa: Callable[..., Any] | None = None,
    nyt: Callable[[], datetime] | None = None,
    paattely_max: int = PAATTELY_MAX_OLETUS,
) -> str:
    """Taustaajon yksi mallikutsu (ajattelija tai keskustelija) OpenRouterin avoimella mallilla, ZDR pakotettu.
    `aikaraja_s` voi vain lyhentaa TAUSTA_AIKARAJA_S:aa. Virheet: YksityinenVirhe (viestissa ei sisaltoa eika avainta);
    katon tayttyminen: PaivakattoTaynna.
    `paattely_max`: sama paattelyn ylaraja kuin chatissa (AIVO_YKSITYINEN_PAATTELY_MAX, taustaajo.main lukee sen);
    reasoning-kentta vain oletuksena paattelevalle mallille. Ei uusintaa: taustaajolla on oma paivaraja."""
    tarkista_malli(malli)
    avain = lue_avain(avain_tiedosto)                     # ennen varausta: puuttuva avain ei jata varausta
    laheta = tee_kuljetin(avain, avaa, aikaraja_s, ylaraja_s=TAUSTA_AIKARAJA_S)
    paattely = paattelyparametri(malli, paattelyraja(malli, MAX_TOKENIA, paattely_max))
    try:
        vastaus = openrouter_kulut.kutsu(
            malli, viestit, laheta, tehtava=TAUSTA_TEHTAVA, nyt=nyt, tila=kulutila,
            max_tokens=MAX_TOKENIA, temperature=0.3, stream=False, usage={"include": True},
            yksityinen_chat=True, **({"reasoning": paattely} if paattely else {}))
    except openrouter_kulut.KutsuEstetty:
        raise PaivakattoTaynna("OpenRouterin p\u00e4iv\u00e4katto on t\u00e4ynn\u00e4 tai kulukirjanpito ei kelpaa; kutsua ei tehty") from None
    except openrouter_kulut.ZdrVirhe:
        raise YksityinenVirhe("ZDR-s\u00e4\u00e4nt\u00f6 est\u00e4\u00e4 pyynn\u00f6n; kutsua ei tehty") from None
    teksti = vastauksen_teksti(vastaus)                    # tyhja + "length" -> VastausKatkesi
    if katkesi_pituusrajaan(vastaus):
        print("yksityinen_or: taustatyon vastaus katkesi pituusrajaan (max_tokens=%d)" % MAX_TOKENIA, file=sys.stderr)
        return teksti + "\n\n" + TAUSTA_KATKENNUT_HUOMAUTUS
    return teksti

# Tarkastajan lahteiden kiintea osa muistin lisaksi: keskustelun viimeiset viestit (itsetarkistus.lahteet), otsikot
# ja rajamerkit. Kehotteen alku (KEHOTE ja ydinohjeet) lisataan kutsussa ohjeiden todellisen pituuden mukaan.
LAHTEET_KESKUSTELU_MAX = (itsetarkistus.VIIMEINEN_VIESTI_MAX_MERKKIA
                          + (itsetarkistus.HISTORIA_VIESTEJA - 1) * (itsetarkistus.HISTORIA_VIESTI_MAX_MERKKIA + 8)
                          + 2_000)


def tarkastajan_lahderaja(tarkastaja: str, muisti_max: int | None = None, ohjeet: str = "") -> int:
    """Tarkastajan lahteiden merkkiraja: tarkastajamallin syoteraja (kontekstiraja.syote_merkit, vastaukselle
    itsetarkistus.TARKISTUS_MAX_TOKENIA, sama kiintea varaus kuin chatissa) - tarkastajan ohje, vastaus ja rakenne.
    `muisti_max` (A1123) rajaa lahteet samaan muistin enimmaiskokoon: muisti + kehotteen alku + keskustelun
    viimeiset viestit. Asetus voi vain pienentaa rajaa."""
    syote = kontekstiraja.syote_merkit(tarkastaja, itsetarkistus.TARKISTUS_MAX_TOKENIA, _VARAUS_USD)
    kiintea = (itsetarkistus.TARKASTAJA_OHJE_MAX_MERKKIA + itsetarkistus.VASTAUS_MAX_MERKKIA + 2 * kontekstiraja.VIESTI_LISA
               + kontekstiraja.JARJESTELMA_LISA)
    raja = max(0, syote - kiintea)
    if muisti_max is not None:
        alku = len(KEHOTE) + len(ohjeet or "(ei ydinohjeita)\n") + kontekstiraja.JARJESTELMA_LISA
        raja = min(raja, max(0, int(muisti_max)) + alku + LAHTEET_KESKUSTELU_MAX)
    return raja


def korjauksen_raja(vastaaja: str) -> int:
    """Korjauskehotteen (perusviestit + korjauksen lisasyote) merkkiraja vastaajamallin kontekstista."""
    return kontekstiraja.syote_merkit(vastaaja, MAX_TOKENIA, _VARAUS_USD)


def tarkista_vastaus(
    muisti: str,
    historia: list[dict[str, Any]],
    ohjeet: str,
    vastaus: str,
    *,
    vastaaja: str,
    tarkastaja: str | None = None,
    kaytetyt: int = 1,
    muisti_max: int | None = None,
    avain_tiedosto: str = OLETUS_AVAIN,
    kulutila: str | None = None,
    avaa: Callable[..., Any] | None = None,
    nyt: Callable[[], datetime] | None = None,
    paattely_max: int = PAATTELY_MAX_OLETUS,
) -> "itsetarkistus.Tulos":
    """Itsetarkistus (itsetarkistus.aja) samalla kehotteella, jolla vastaus syntyi. `tarkastaja` puuttuu ->
    vastaajan malli. Molemmat mallit tarkistetaan avoin_malli()-funktiolla ennen kutsua. Kutsun virheet eivat nouse:
    ne nakyvat merkintana ja alkuperainen vastaus sailyy."""
    tarkista_malli(vastaaja)
    tarkastaja = tarkista_malli(tarkastaja or vastaaja)
    # Historiaraja vastaajan mallin mukaan (korjaus menee vastaajalle) ja korjauksen lisasyotteelle varattu tila.
    # Tarkastaja ei saa koko kehotetta vaan oman rajansa (tarkastajan_lahderaja), viitatut lahteet ensin.
    viestit = yksityinen.muodosta_viestit(muisti, historia, ohjeet, KEHOTE, HISTORIA_YLARAJA,
                                         historian_merkkiraja(vastaaja, muisti, ohjeet, historia,
                                                              itsetarkistus.KORJAUS_LISAVARAUS))

    def kysy(m: str, v: list[dict[str, Any]], max_tokens: int | None = None, aikaraja_s: float | None = None) -> str:
        ylaraja = VASTAUS_AIKARAJA_S if max_tokens is not None else CHAT_AIKARAJA_S     # korjaus: kuten chat
        return _kysy_viestit(m, v, avain_tiedosto=avain_tiedosto, kulutila=kulutila, avaa=avaa, nyt=nyt,
                             max_tokens=max_tokens, ylaraja_s=ylaraja, aikaraja_s=aikaraja_s,
                             tehtava=TARKISTUS_TEHTAVA, paattely_max=paattely_max)    # ei uusintaa: kutsuraja itsetarkistuksessa
    return itsetarkistus.aja(vastaus, viestit, kysy, tarkastaja=tarkastaja, vastaaja=vastaaja, kaytetyt=kaytetyt,
                             lahteet_raja=tarkastajan_lahderaja(tarkastaja, muisti_max, ohjeet),
                             korjaus_raja=korjauksen_raja(vastaaja))
