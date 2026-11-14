"""OpenRouter-kulujen kirjaus malleittain ja paivakatkaisin (Tonyn paatos 14.11.2026, A963; aivo-valmius.md ruutu 3).

Kaikki Aivon OpenRouter-kutsut kulkevat funktion `kutsu` kautta: se kysyy ensin `saako_kutsua`, rakentaa rungon
`openrouter_zdr.rakenna_runko`lla, tekee kutsun annetulla kuljettimella ja kirjaa kulun. Muu aivo-koodi ei saa tehda
OpenRouter-kutsuja (testi tarkistaa).

Saannot:
  * Katto on koodin vakio PAIVAKATTO_EUR (10 EUR/vrk, Europe/Helsinki). Ei ymparistomuuttujaa eika parametria:
    agentit eivat muuta omia suojakaiteitaan. Muutos vaatii Tonyn luvan ja koodimuutoksen.
  * Fail closed: lukuvirhe, rikkinainen rivi, kirjoituskelvoton hakemisto tai aikavyohykkeeton `nyt` estaa kutsun.
  * Tuntematon kulu lasketaan varovasti: tunnetuilla tokeneilla mallin hinnasta (tuntematon malli = korkea oletushinta),
    ilman tokeneita kiintea VARO_USD_KUTSU. Myos kuljetinvirhe kirjataan tuntemattomana kulutuksena (aikakatkaisu voi laskuttaa).
  * Valuutta: OpenRouter raportoi USD:na; muunnos on kiintea vakio USD_EUR (ei verkkohakua).
  * Katkaisin tarkistaa kulun ennen kutsua, joten yksittainen kutsu voi ylittaa katon sen verran kuin kutsu maksaa.
  * Kirjaus on isannan tilahakemistossa (oletus /var/lib/claude-jono, kulutus.jsonl:n vieressa), ei GitHubissa (A728).
    Rivilla on vain malli, aika, tehtavan tunniste ja luvut; ei viestien sisaltoa.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from . import openrouter_zdr

TZ = ZoneInfo("Europe/Helsinki")
PAIVAKATTO_EUR = Decimal(10)
USD_EUR = Decimal("0.92")           # kiintea muuntokerroin (hieman korkea = varovainen); paivitys vain koodimuutoksella
VARO_USD_KUTSU = Decimal("0.50")    # kulu, kun ei kulua eika tokeneita
OLETUS_TILA = "/var/lib/claude-jono"
KULUT = "openrouter-kulut.jsonl"
TAPAHTUMAT = "openrouter-katto.jsonl"
VIIKKO_TARKISTA_PAIVIA = 3          # katto taynna vahintaan nain monena paivana 7:sta -> "tarkista kaytto" (A963)

# USD / miljoona tokenia (sisaan, ulos). Tuntematon malli: OLETUSHINTA (korkea, varovainen).
HINNAT_USD_MTOK: dict[str, tuple[Decimal, Decimal]] = {}
OLETUSHINTA_USD_MTOK = (Decimal(15), Decimal(75))

_TUNNISTE_RE = re.compile(r"[^A-Za-z0-9._:/-]")


class KulutVirhe(Exception):
    """Kirjanpito ei ole luettavissa tai kirjoitettavissa (fail closed)."""


class KutsuEstetty(Exception):
    """Katkaisin esti kutsun (katto taynna tai kirjanpito rikki). Viestissa ei ole salaisuuksia."""


def _tunniste(arvo: Any, raja: int = 60) -> str:
    return _TUNNISTE_RE.sub("_", str(arvo))[:raja]


def _tila(tila: Any) -> Path:
    return Path(tila if tila is not None else OLETUS_TILA)


def _paiva(aika: datetime):
    if not isinstance(aika, datetime) or aika.tzinfo is None:
        raise KulutVirhe("aika ilman aikavyohykketta")
    return aika.astimezone(TZ).date()


def _luku(arvo: Any) -> Decimal | None:
    """Ei-negatiivinen aarellinen luku Decimalina tai None (puuttuu). Muu -> KulutVirhe."""
    if arvo is None:
        return None
    if isinstance(arvo, bool) or not isinstance(arvo, (int, float)) or not math.isfinite(arvo) or arvo < 0:
        raise KulutVirhe("virheellinen luku")
    return Decimal(repr(arvo))


def arvioi_usd(malli: str, tokenit_in: Any, tokenit_out: Any) -> Decimal:
    """Varovainen USD-arvio tuntemattomalle kululle."""
    try:
        t_in, t_out = _luku(tokenit_in), _luku(tokenit_out)
    except KulutVirhe:
        return VARO_USD_KUTSU
    if t_in is None and t_out is None:
        return VARO_USD_KUTSU
    h_in, h_out = HINNAT_USD_MTOK.get(malli, OLETUSHINTA_USD_MTOK)
    arvio = ((t_in or 0) * h_in + (t_out or 0) * h_out) / Decimal(1_000_000)
    return max(arvio, VARO_USD_KUTSU) if (t_in is None or t_out is None) else arvio


def rivin_usd(rivi: Mapping[str, Any]) -> Decimal:
    """Rivin kulu USD:na. Tuntematon kulu (usd puuttuu) arvioidaan varovasti. Virheellinen rivi -> KulutVirhe."""
    if not isinstance(rivi, Mapping):
        raise KulutVirhe("rivi ei ole kuvaus")
    usd = _luku(rivi.get("usd"))
    if usd is not None:
        return usd
    return arvioi_usd(str(rivi.get("malli", "")), rivi.get("tokenit_in"), rivi.get("tokenit_out"))


def _lue_rivit(tila: Path, tiedosto: str) -> list[dict[str, Any]]:
    """Kaikki rivit; puuttuva tiedosto = ei rivej\u00e4; mik\u00e4 tahansa rikkin\u00e4inen rivi tai lukuvirhe -> KulutVirhe."""
    try:
        teksti = (tila / tiedosto).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise KulutVirhe("kirjanpito ei ole luettavissa") from None
    ulos = []
    for rivi in teksti.splitlines():
        if not rivi.strip():
            continue
        try:
            d = json.loads(rivi)
            aika = datetime.fromisoformat(d["aika"])
            if not isinstance(d, dict) or aika.tzinfo is None:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise KulutVirhe("rikkinainen rivi") from None
        d["_paiva"] = aika.astimezone(TZ).date()
        ulos.append(d)
    return ulos


def _lisaa_rivi(tila: Path, tiedosto: str, rivi: Mapping[str, Any]) -> None:
    try:
        fd = os.open(tila / tiedosto, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(rivi, sort_keys=True) + "\n")
    except OSError:
        raise KulutVirhe("kirjanpitoon ei voi kirjoittaa") from None


def _eur(usd: Decimal) -> Decimal:
    return usd * USD_EUR


def kulut_paivalta(tila: Any, nyt: datetime) -> Decimal:
    """Kuluvan vuorokauden (Helsinki) kulut EUR. KulutVirhe, jos kirjanpito ei kelpaa."""
    paiva = _paiva(nyt)
    return _eur(sum((rivin_usd(r) for r in _lue_rivit(_tila(tila), KULUT) if r["_paiva"] == paiva), Decimal(0)))


def kirjaa(malli: str, tehtava: str, vastaus: Any, nyt: datetime, tila: Any = None) -> dict[str, Any]:
    """Kirjaa yhden OpenRouter-vastauksen kulun. `vastaus` = vastauksen JSON (dict) tai None (kuljetinvirhe).
    usage.cost (USD) kirjataan sellaisenaan; jos se puuttuu, kirjataan tokenit ja kulu merkitaan tuntemattomaksi.
    KulutVirhe, jos kirjoitus ei onnistu."""
    _paiva(nyt)
    u = vastaus.get("usage") if isinstance(vastaus, Mapping) and isinstance(vastaus.get("usage"), Mapping) else {}
    rivi: dict[str, Any] = {"aika": nyt.astimezone(TZ).isoformat(timespec="seconds"), "malli": _tunniste(malli), "tehtava": _tunniste(tehtava)}
    for avain, nimi in (("prompt_tokens", "tokenit_in"), ("completion_tokens", "tokenit_out")):
        try:
            if _luku(u.get(avain)) is not None:
                rivi[nimi] = int(u[avain])
        except KulutVirhe:
            pass
    try:
        kulu = _luku(u.get("cost"))
    except KulutVirhe:
        kulu = None
    if kulu is not None:
        rivi["usd"] = float(kulu)
    else:
        rivi["kulu_tuntematon"] = True
    if vastaus is None:
        rivi["virhe"] = True
    _lisaa_rivi(_tila(tila), KULUT, rivi)
    return rivi


def _ilmoita_katto(tila: Path, nyt: datetime, eur: Decimal, ilmoita: Callable[[str], Any] | None) -> None:
    """Kirjaa kattotapahtuman kerran vuorokaudessa ja ilmoittaa Tonylle (kerran vuorokaudessa, uusinta kunnes onnistuu).
    Virhe ei saa muuttaa katkaisimen tulosta."""
    try:
        paiva = _paiva(nyt)
        rivit = _lue_rivit(tila, TAPAHTUMAT)
        tanaan = [r.get("tyyppi") for r in rivit if r["_paiva"] == paiva]
        aika = nyt.astimezone(TZ).isoformat(timespec="seconds")
        if "katto" not in tanaan:
            _lisaa_rivi(tila, TAPAHTUMAT, {"aika": aika, "tyyppi": "katto", "eur": float(round(eur, 2))})
        if "ilmoitus" not in tanaan and ilmoita is not None:
            teksti = ("Aivon OpenRouter-p\u00e4iv\u00e4katto (%s \u20ac) t\u00e4yttyi: kulut t\u00e4n\u00e4\u00e4n %s \u20ac. Kutsut on estetty vuorokauden vaihteeseen."
                      % (PAIVAKATTO_EUR, round(eur, 2)))
            if ilmoita(teksti) is True:
                _lisaa_rivi(tila, TAPAHTUMAT, {"aika": aika, "tyyppi": "ilmoitus"})
    except Exception as virhe:  # noqa: BLE001 - ilmoitus ei saa kaataa katkaisinta
        print("openrouter_kulut: katto-ilmoitus epaonnistui (%s)" % type(virhe).__name__, file=sys.stderr)


def saako_kutsua(nyt: datetime, tila: Any = None, ilmoita: Callable[[str], Any] | None = None) -> bool:
    """Epatosi, kun kuluvan vuorokauden kulut ovat >= PAIVAKATTO_EUR tai kirjanpitoa ei voi lukea/kirjoittaa (fail closed)."""
    try:
        t = _tila(tila)
        if not t.is_dir() or not os.access(t, os.W_OK):
            return False
        eur = kulut_paivalta(t, nyt)
    except Exception:  # noqa: BLE001 - mik\u00e4 tahansa virhe est\u00e4\u00e4 kutsun
        return False
    if eur >= PAIVAKATTO_EUR:
        _ilmoita_katto(t, nyt, eur, ilmoita)
        return False
    return True


def kutsu(
    malli: str,
    viestit: Iterable[Mapping[str, Any]],
    laheta: Callable[[dict[str, Any]], Mapping[str, Any]],
    *,
    tehtava: str = "",
    nyt: Callable[[], datetime] | None = None,
    tila: Any = None,
    ilmoita: Callable[[str], Any] | None = None,
    **lisa: Any,
) -> Mapping[str, Any]:
    """Ainoa reitti OpenRouter-kutsuun: katkaisin -> ZDR-runko -> `laheta(runko)` -> kirjaus.
    `laheta` on kuljetin (tekee verkkokutsun ja palauttaa vastauksen JSON:n); testeissa feikki.
    Katkaisin estaa: KutsuEstetty. Kuljetinvirhe kirjataan (varovainen kulu) ja nostetaan edelleen."""
    aika = (nyt or (lambda: datetime.now(TZ)))()
    if not saako_kutsua(aika, tila, ilmoita):
        raise KutsuEstetty("paivakatto tayna tai kirjanpito ei kelpaa")
    runko = openrouter_zdr.rakenna_runko(malli, viestit, **lisa)
    try:
        vastaus = laheta(runko)
    except BaseException:
        try:
            kirjaa(malli, tehtava, None, aika, tila)
        except KulutVirhe:
            pass
        raise
    kirjaa(malli, tehtava, vastaus, aika, tila)
    return vastaus


def viikkoraportti(tila: Any, nyt: datetime) -> str:
    """Viikkoraportin osa: kulut per paiva ja malli (7 vrk, Helsinki) EUR. Jos katto taynna >= 3 paivana: 'tarkista kaytto' (A963)."""
    try:
        paiva = _paiva(nyt)
        rivit = _lue_rivit(_tila(tila), KULUT)
        paivat = [paiva - timedelta(days=i) for i in range(6, -1, -1)]
        taulu: dict[Any, dict[str, Decimal]] = {p: {} for p in paivat}
        for r in rivit:
            if r["_paiva"] in taulu:
                m = str(r.get("malli", "?"))
                taulu[r["_paiva"]][m] = taulu[r["_paiva"]].get(m, Decimal(0)) + _eur(rivin_usd(r))
    except Exception:  # noqa: BLE001
        return "OpenRouter-kulut: kirjanpitoa ei voitu lukea, tarkista k\u00e4ytt\u00f6."
    rivit_ulos = ["OpenRouter-kulut (\u20ac, " + str(PAIVAKATTO_EUR) + " \u20ac/vrk katto):"]
    katto_paivia = 0
    for p in paivat:
        yht = sum(taulu[p].values(), Decimal(0))
        if yht >= PAIVAKATTO_EUR:
            katto_paivia += 1
        osat = "; ".join("%s %.2f" % (m, v) for m, v in sorted(taulu[p].items())) or "ei kuluja"
        rivit_ulos.append("- %d.%d. yhteens\u00e4 %.2f (%s)" % (p.day, p.month, yht, osat))
    if katto_paivia >= VIIKKO_TARKISTA_PAIVIA:
        rivit_ulos.append("Tarkista k\u00e4ytt\u00f6: katto t\u00e4yttyi %d p\u00e4iv\u00e4n\u00e4 7:st\u00e4." % katto_paivia)
    return "\n".join(rivit_ulos)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["viikko"]:
        print(viikkoraportti(argv[1] if len(argv) > 1 else None, datetime.now(TZ)))
        return 0
    print("kaytto: python3 -m aivo.openrouter_kulut viikko [TILAHAKEMISTO]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
