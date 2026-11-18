"""Aivon yksityisen tilan OpenRouter-taustapaa (Tonyn paatos A984; rajattu ulkoinen mallipalvelu, A728).

Vaihtoehto loopback-mallipalvelimelle (`yksityinen.kysy_paikalliselta`). Muisti, keskustelut ja ydinohjeet pysyvat
palvelun omissa kansioissa; OpenRouterille lahtee vain kulloinenkin kehote (ydinohjeet, valitut muistiotteet ja
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
  * Malli on asetus eika oletusta ole. Vain avoimien mallien perheet (AVOIMET_MALLIT) kelpaavat; suljetut mallit,
    `:free`-versiot ja `:online`-tyyppiset liitteet hylataan jo asetuksessa.
  * Avain luetaan tiedostosta, jonka omistaa palvelun kayttaja ja jonka oikeudet ovat 0600 tai tiukemmat (ei linkki).
    Avainta ei lueta ymparistomuuttujasta, eika sita kirjoiteta lokiin, virheviesteihin eika muualle.
    Virheista nakyy vain tyyppi ja HTTP-tila.
  * Avain luetaan jokaisella kutsulla (Tony voi vaihtaa tiedoston kayttamatta palvelun uudelleenkaynnistysta) ja ennen
    paivakattovarausta: puuttuva avain ei jata varausta eika anna arvoituksellista virhetta.
  * Tyokalutila ei kayta tata moduulia (testi tarkistaa)."""
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

from . import openrouter_kulut, yksityinen
from .yksityinen import YKSITYINEN_KEHOTE, YksityinenVirhe

OLETUS_AVAIN = "/var/lib/aivo-yksityinen/openrouter-avain"
TEHTAVA = "aivo-yksityinen"
VASTAUS_AIKARAJA_S = 120
MAX_VASTAUS_TAVUA = 2_000_000
MAX_TOKENIA = yksityinen.MAX_TOKENIA

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
    if not _AVAIN_RE.match(avain):
        raise YksityinenVirhe("OpenRouter-avaintiedoston sisalto ei ole avaimen nakoinen")
    return avain


def _zdr_kunnossa(runko: Any) -> bool:
    prov = runko.get("provider") if isinstance(runko, Mapping) else None
    return isinstance(prov, Mapping) and prov.get("zdr") is True and prov.get("data_collection") == "deny"


def tee_kuljetin(avain: str, avaa: Callable[..., Any] | None = None) -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    """Kuljetin `openrouter_kulut.kutsu`lle: lahettaa rungon OpenRouteriin. Kieltaytyy, ellei ZDR ole rungossa."""
    if avaa is None:
        avaa = urllib.request.build_opener(urllib.request.ProxyHandler({})).open     # ei valityspalvelinta

    def laheta(runko: dict[str, Any]) -> Mapping[str, Any]:
        if not _zdr_kunnossa(runko):
            raise YksityinenVirhe("ZDR puuttuu pyynnosta; ei lahetetty")
        pyynto = urllib.request.Request(
            openrouter_kulut.OSOITE, data=json.dumps(runko).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + avain})
        try:
            with avaa(pyynto, timeout=VASTAUS_AIKARAJA_S) as r:
                data = json.loads(r.read(MAX_VASTAUS_TAVUA))
        except urllib.error.HTTPError as e:
            raise YksityinenVirhe("mallipalvelu hylkasi pyynnon (HTTP %s)" % e.code) from None
        except (OSError, ValueError) as e:
            raise YksityinenVirhe("mallipalvelu ei vastannut (%s)" % type(e).__name__) from None
        if not isinstance(data, Mapping):
            raise YksityinenVirhe("OpenRouter antoi odottamattoman vastauksen")
        return data
    return laheta


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
    avain = lue_avain(avain_tiedosto)                     # ennen varausta: puuttuva avain ei jata varausta
    laheta = tee_kuljetin(avain, avaa)
    viestit = yksityinen.muodosta_viestit(muisti, historia, ohjeet, KEHOTE)
    try:
        data = openrouter_kulut.kutsu(
            malli, viestit, laheta, tehtava=TEHTAVA, nyt=nyt, tila=kulutila, ilmoita=ilmoita,
            max_tokens=MAX_TOKENIA, temperature=0.3, stream=False, usage={"include": True},
            **{**lisa, "yksityinen_chat": True})
    except openrouter_kulut.KutsuEstetty:
        raise YksityinenVirhe("OpenRouterin p\u00e4iv\u00e4katto on t\u00e4ynn\u00e4 tai kulukirjanpito ei kelpaa; kutsua ei tehty") from None
    except openrouter_kulut.ZdrVirhe:
        raise YksityinenVirhe("ZDR-s\u00e4\u00e4nt\u00f6 est\u00e4\u00e4 pyynn\u00f6n; kutsua ei tehty") from None
    try:
        teksti = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise YksityinenVirhe("OpenRouterin vastauksessa ei ollut tekstia") from None
    if not isinstance(teksti, str) or not teksti.strip():
        raise YksityinenVirhe("OpenRouter antoi tyhjan vastauksen")
    return teksti.strip()
