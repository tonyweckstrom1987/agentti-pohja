"""Aivon yksityisen tilan OpenRouter-taustapaa (Tonyn paatos A984; rajattu ulkoinen mallipalvelu, A728).

Vaihtoehto loopback-mallipalvelimelle (`yksityinen.kysy_paikalliselta`). Muisti, keskustelut ja ydinohjeet pysyvat
palvelun omissa kansioissa; OpenRouterille lahtee vain kulloinenkin kehote (ydinohjeet, valitut muistiotteet ja
keskusteluhistoria) ja ZDR on pakotettu.

Saannot (koodi on laki):
  * Yksi yhteinen kulukirjanpito ja yksi paivakatto: ei omaa kulutilaa. Oletus on `openrouter_kulut.OLETUS_TILA`,
    joten yksityisen tilan kutsut kuluttavat samaa 10 EUR/vrk kattoa kuin ajurin kutsut (A963, #309). `kulutila`-parametri
    on vain testeja varten eika sita voi asettaa palvelun asetuksista.
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
    on oma (TIIVISTYS_TEHTAVA), jotta tiivistyksen osuus nakyy kulukirjanpidossa."""
from __future__ import annotations

import json
import os
import re
import stat
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from . import openrouter_kulut, yhdistelma, yksityinen
from .yksityinen import YKSITYINEN_KEHOTE, YksityinenVirhe

OLETUS_AVAIN = "/var/lib/aivo-yksityinen/openrouter-avain"
TEHTAVA = "aivo-yksityinen"
VASTAUS_AIKARAJA_S = 120
MAX_VASTAUS_TAVUA = 2_000_000
MAX_TOKENIA = yksityinen.MAX_TOKENIA
# Historia on taustapaakohtainen. Paikallinen malli (yksityinen.HISTORIA_YLARAJA = 12) kasittelee kehotteen hitaasti
# 900 s aikarajan sisalla; OpenRouterin malli kasittelee pitkan kehotteen sekunneissa, joten raja on sama 40 kuin
# tyokalutilassa (chat.HISTORIA_YLARAJA). Pahin tapaus: 40 viestia (20 x 4 000 merkkia + 20 x 700 tokenia),
# muisti 16 000, ydinohjeet 20 000 ja kehote noin 4 000 merkkia = noin 160 000 merkkia, alle 80 000 tokenia
# varovaisellakin 2 merkkia/token-arviolla. Suositusmallin qwen3-235b-a22b-2507 konteksti on 262 144 tokenia.
HISTORIA_YLARAJA = 40

TIIVISTYS_TEHTAVA = "aivo-yksityinen-tiivistys"
# Tiivistys palauttaa JSON:n (enintaan tiivistelma.MAX_KOHDAT kohtaa, tulkinta enintaan 300 merkkia). 700 tokenia
# (paikallisen mallin raja) katkaisee pitkan vastauksen kesken; 1 500 riittaa ja maksaa avoimella mallilla senttien osia.
TIIVISTYS_MAX_TOKENIA = 1_500

AVOIMET_MALLIT = openrouter_kulut.AVOIMET_MALLIT      # lista on saantomoduulissa (A984); uudelleenvienti
_VANHA_LAUSE = ("sinua ajetaan Tonyn omalla\nkoneella, eik\u00e4 t\u00e4m\u00e4 keskustelu l\u00e4hde millek\u00e4\u00e4n ulkoiselle palvelulle.")
_UUSI_LAUSE = ("sinua ajetaan avoimella mallilla\nOpenRouterin kautta nollas\u00e4ilytyksell\u00e4 (ZDR). Muisti ja keskustelut s\u00e4ilyv\u00e4t Tonyn omalla palvelimella;\n"
               "ulos l\u00e4htee vain kulloinenkin kehote.")
KEHOTE = YKSITYINEN_KEHOTE.replace(_VANHA_LAUSE, _UUSI_LAUSE)
_AVAIN_RE = re.compile(r"^[\x21-\x7e]{16,300}$")


avoin_malli = openrouter_kulut.avoin_malli


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


def tee_kuljetin(avain: str, avaa: Callable[..., Any] | None = None,
                 aikaraja_s: float | None = None) -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    """Kuljetin `openrouter_kulut.kutsu`lle: lahettaa rungon OpenRouteriin. Kieltaytyy, ellei ZDR ole rungossa.
    `aikaraja_s` voi vain lyhentaa VASTAUS_AIKARAJA_S:aa (yhdistelman kokonaisaikaraja)."""
    aikaraja = VASTAUS_AIKARAJA_S if aikaraja_s is None else max(1.0, min(float(aikaraja_s), VASTAUS_AIKARAJA_S))
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
            raise YksityinenVirhe("mallipalvelu hylkasi pyynnon (HTTP %s)" % e.code) from None
        except (OSError, ValueError) as e:
            raise YksityinenVirhe("mallipalvelu ei vastannut (%s)" % type(e).__name__) from None
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
    **lisa: Any,
) -> str:
    """Yksi kutsu valmiilla viestilistalla. Ainoa reitti, jota kysy_openrouterilta ja yhdistelmat kayttavat."""
    tarkista_malli(malli)
    avain = lue_avain(avain_tiedosto)                     # ennen varausta: puuttuva avain ei jata varausta
    laheta = tee_kuljetin(avain, avaa, aikaraja_s)
    tokenit = MAX_TOKENIA if max_tokens is None else max(1, min(int(max_tokens), MAX_TOKENIA))
    try:
        data = openrouter_kulut.kutsu(
            malli, viestit, laheta, tehtava=TEHTAVA, nyt=nyt, tila=kulutila, ilmoita=ilmoita,
            max_tokens=tokenit, temperature=0.3, stream=False, usage={"include": True},
            **{**lisa, "yksityinen_chat": True})
    except openrouter_kulut.KutsuEstetty:
        raise yhdistelma.Katkaisu("OpenRouterin p\u00e4iv\u00e4katto on t\u00e4ynn\u00e4 tai kulukirjanpito ei kelpaa; kutsua ei tehty") from None
    except openrouter_kulut.ZdrVirhe:
        raise YksityinenVirhe("ZDR-s\u00e4\u00e4nt\u00f6 est\u00e4\u00e4 pyynn\u00f6n; kutsua ei tehty") from None
    try:
        teksti = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise YksityinenVirhe("OpenRouterin vastauksessa ei ollut tekstia") from None
    if not isinstance(teksti, str) or not teksti.strip():
        raise YksityinenVirhe("OpenRouter antoi tyhjan vastauksen")
    return teksti.strip()


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
    **lisa: Any,
) -> str:
    """Yksityisen tilan vastaus OpenRouterin avoimelta mallilta. Nostaa aina YksityinenVirhe (turvallinen viesti)."""
    tarkista_malli(malli)
    viestit = yksityinen.muodosta_viestit(muisti, historia, ohjeet, KEHOTE, HISTORIA_YLARAJA)
    return _kysy_viestit(malli, viestit, avain_tiedosto=avain_tiedosto, kulutila=kulutila, avaa=avaa, nyt=nyt,
                         ilmoita=ilmoita, **lisa)


def kysy_yhdistelmalla(
    muisti: str,
    historia: list[dict[str, Any]],
    ohjeet: str = "",
    *,
    malli: str,
    yhdistelma_nimi: str = yhdistelma.OLETUS,
    roolit: Mapping[str, str] | None = None,
    kello: Callable[[], float] | None = None,
    **kw: Any,
) -> "yhdistelma.Tulos":
    """Usean mallin yhdistelma (yhdistelma.aja). Jokainen osakutsu kulkee _kysy_viestit-funktion kautta."""
    tarkista_malli(malli)
    for m in (roolit or {}).values():
        tarkista_malli(m)
    viestit = yksityinen.muodosta_viestit(muisti, historia, ohjeet, KEHOTE, HISTORIA_YLARAJA)

    def kysy(m: str, v: list[dict[str, Any]], aikaraja_s: float | None = None, max_tokens: int | None = None) -> str:
        return _kysy_viestit(m, v, aikaraja_s=aikaraja_s, max_tokens=max_tokens, **kw)
    return yhdistelma.aja(yhdistelma_nimi, roolit or {}, malli, viestit, kysy,
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
) -> str:
    """Yksityisen alueen keskusteluosan tiivistys OpenRouterin avoimella mallilla (ZDR pakotettu).
    `ohje` on jarjestelmaviesti ja `data` keskusteluosa. Virheet: YksityinenVirhe (viestissa ei sisaltoa eika avainta);
    katon tayttyminen: PaivakattoTaynna."""
    tarkista_malli(malli)
    avain = lue_avain(avain_tiedosto)                     # ennen varausta: puuttuva avain ei jata varausta
    laheta = tee_kuljetin(avain, avaa)
    viestit = [{"role": "system", "content": ohje}, {"role": "user", "content": data}]
    try:
        vastaus = openrouter_kulut.kutsu(
            malli, viestit, laheta, tehtava=TIIVISTYS_TEHTAVA, nyt=nyt, tila=kulutila,
            max_tokens=TIIVISTYS_MAX_TOKENIA, temperature=0.2, stream=False, usage={"include": True},
            yksityinen_chat=True)
    except openrouter_kulut.KutsuEstetty:
        raise PaivakattoTaynna("OpenRouterin p\u00e4iv\u00e4katto on t\u00e4ynn\u00e4 tai kulukirjanpito ei kelpaa; kutsua ei tehty") from None
    except openrouter_kulut.ZdrVirhe:
        raise YksityinenVirhe("ZDR-s\u00e4\u00e4nt\u00f6 est\u00e4\u00e4 pyynn\u00f6n; kutsua ei tehty") from None
    try:
        teksti = vastaus["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise YksityinenVirhe("OpenRouterin vastauksessa ei ollut tekstia") from None
    if not isinstance(teksti, str) or not teksti.strip():
        raise YksityinenVirhe("OpenRouter antoi tyhjan vastauksen")
    return teksti.strip()
