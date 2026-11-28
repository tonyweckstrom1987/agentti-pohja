"""Lahdeviitteen sisaltotarkistus: onko [lahde: X] -viitetta edeltavan vaitteen tunniste tiedostossa X.

chat.tarkista_lahteet tarkistaa vain, etta viitattu tiedosto on muistissa. Tama moduuli tarkistaa lisaksi sisallon
mekaanisesti, ilman mallikutsua:

  * Vaite = teksti edellisesta lahdeviitteesta tai rivinvaihdosta talle viitteelle (enintaan VAITE_MAX_MERKKIA).
    Perakkaiset viitteet ([lahde: X] [lahde: Y]) jakavat saman vaitteen.
  * Tunnisteet: A-paatosnumerot (A1116), paivamaarat (26.11., 26.11.2026, 2026-11-26) ja vahintaan kolminumeroiset
    luvut. Lyhyita lukuja ei tarkisteta (liian yleisia: vaarat halytykset).
  * Viite on kunnossa, jos jokainen tunniste esiintyy jossakin viitatuista tiedostoista.
  * Vuosi: vuodellinen paivamaara (26.11.2025) esiintyy viitatussa tiedostossa, jos siella on sama paiva samalla
    vuodella tai ilman vuotta (26.11.); toisen vuoden sama paiva (26.11.2026) ei kelpaa. Ehdokkaaksi kelpaa vain
    tiedosto, jossa paiva on samalla vuodella. Vuodeton paivamaara (26.11.) osuu mihin tahansa vuoteen.
  * Jos ei: tiedostot, joissa kaikki tunnisteet esiintyvat, ovat ehdokkaita. Tasan yksi ehdokas, yksi viite ja
    jokaisessa paivamaarassa vuosi -> viite korjataan ehdokkaaseen ja korjaus kerrotaan tekstissa. Jos vaitteen
    paivamaarasta puuttuu vuosi, korjausta ei tehda automaattisesti, vaan viite merkitaan. Useita ehdokkaita tai ryhmaviite -> viite
    merkitaan vaaraksi ja ehdokkaat luetellaan (enintaan 3). Ei ehdokkaita -> merkinta vain, jos A-numero tai
    paivamaara puuttuu viitatusta tiedostosta (pelkka luku voi olla laskettu, joten siita ei merkita).

Muisti jasennetaan chatin muodosta: osat erotetaan RAJA-rivilla, ja osan alussa on "### TIEDOSTO: nimi". Osia ilman
otsikkoa (esim. hakuosumat, kalenteri) ei kayteta. Viitteita tiedostoihin, joita ei loydy otsikoista, ei kosketa
(chat.tarkista_lahteet merkitsee ne). Moduuli ei tee verkko- eika mallikutsuja.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

RAJA = "=====AIVO-MUISTI-RAJA====="              # sama kuin chat.RAJA (testi vertaa)
LAHDE_RE = re.compile(r"\[l\u00e4hde: ([^\]\n]{1,200})\]")   # sama kuin chat.LAHDE_RE (testi vertaa)
_TIEDOSTO_RE = re.compile(r"\A\s*### TIEDOSTO: ([^\n]+)\n?")
VAITE_MAX_MERKKIA = 400
EHDOKKAITA_MAX = 3
_VALI_RE = re.compile(r"\A[\s,;.:]*\Z")          # perakkaisten viitteiden valissa vain valimerkkeja

_PAATOS_RE = re.compile(r"(?<![0-9A-Za-z\u00c0-\u00ff])A\s?(\d{2,5})(?!\d)")
_PVM_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_PVM_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})?(?!\d)")
_LUKU_RE = re.compile(r"(?<![\d.,:])(\d{3,})(?![\d])")


def jasenna_muisti(muisti: Any) -> Dict[str, str]:
    """Muistiteksti -> {tiedostonimi: sisalto}. Saman nimen osat yhdistetaan."""
    tulos: Dict[str, str] = {}
    if not isinstance(muisti, str):
        return tulos
    for osa in muisti.split(RAJA):
        m = _TIEDOSTO_RE.match(osa)
        if not m:
            continue
        nimi = m.group(1).strip()
        if nimi:
            tulos[nimi] = tulos.get(nimi, "") + osa[m.end():]
    return tulos


def tunnisteet(vaite: str) -> List[Tuple[str, str, Any]]:
    """Vaitteen tunnisteet listana (laji, nayttoteksti, avain). Lajit: 'paatos', 'pvm', 'luku'.
    Paivamaaran avain on (paiva, kuukausi, vuosi tai None)."""
    tulos: List[Tuple[str, str, Any]] = []
    varattu: List[Tuple[int, int]] = []

    def lisaa(laji, teksti, avain, m):
        varattu.append(m.span())
        if all(t[2] != avain or t[0] != laji for t in tulos):
            tulos.append((laji, teksti, avain))

    for m in _PAATOS_RE.finditer(vaite):
        lisaa("paatos", "A" + m.group(1), m.group(1), m)
    for m in _PVM_ISO_RE.finditer(vaite):
        lisaa("pvm", m.group(0), (int(m.group(3)), int(m.group(2)), int(m.group(1))), m)
    for m in _PVM_RE.finditer(vaite):
        if any(a <= m.start() < b for a, b in varattu):
            continue
        p, k = int(m.group(1)), int(m.group(2))
        if 1 <= p <= 31 and 1 <= k <= 12:
            lisaa("pvm", m.group(0), (p, k, int(m.group(3)) if m.group(3) else None), m)
    for m in _LUKU_RE.finditer(vaite):
        if any(a <= m.start() < b for a, b in varattu):
            continue
        lisaa("luku", m.group(1), m.group(1), m)
    return tulos


def _esiintyy(tunniste: Tuple[str, str, Any], sisalto: str, tarkka: bool = False) -> bool:
    """Esiintyyko tunniste sisallossa. tarkka=True (ehdokkaat): vuodellisen paivamaaran pitaa olla tiedostossa
    samalla vuodella; vuodeton maininta (26.11.) ei riita."""
    laji, _, avain = tunniste
    if laji == "paatos":
        return re.search(r"(?<![0-9A-Za-z\u00c0-\u00ff])A\s?%s(?!\d)" % avain, sisalto) is not None
    if laji == "pvm":
        p, k, v = avain
        if v is None:
            if re.search(r"(?<![\d.])0?%d\.0?%d\.(?!\d\.)" % (p, k), sisalto):
                return True
            return re.search(r"(?<!\d)\d{4}-%02d-%02d(?!\d)" % (k, p), sisalto) is not None
        if re.search(r"(?<![\d.])0?%d\.0?%d\.\s?%04d(?!\d)" % (p, k, v), sisalto):
            return True
        if re.search(r"(?<!\d)%04d-%02d-%02d(?!\d)" % (v, k, p), sisalto):
            return True
        if tarkka:
            return False
        return re.search(r"(?<![\d.])0?%d\.0?%d\.(?!\d\.|\s?\d{4}(?!\d))" % (p, k), sisalto) is not None
    return re.search(r"(?<![\d.,:])%s(?!\d)" % avain, sisalto) is not None


def _ryhmat(vastaus: str) -> List[List[re.Match]]:
    """Perakkaiset viitteet (vain valimerkkeja valissa) samaan ryhmaan."""
    ryhmat: List[List[re.Match]] = []
    for m in LAHDE_RE.finditer(vastaus):
        if ryhmat and _VALI_RE.match(vastaus[ryhmat[-1][-1].end():m.start()]):
            ryhmat[-1].append(m)
        else:
            ryhmat.append([m])
    return ryhmat


def _vaite(vastaus: str, alku: int, edellinen_loppu: int) -> str:
    raja = max(edellinen_loppu, vastaus.rfind("\n", 0, alku) + 1, alku - VAITE_MAX_MERKKIA)
    return vastaus[raja:alku]


def _luettelo(nimet: Sequence[str]) -> str:
    return ", ".join(nimet[:EHDOKKAITA_MAX]) + (" ym." if len(nimet) > EHDOKKAITA_MAX else "")


def tarkista(vastaus: Any, muisti: Any,
             tiedostot: Optional[Sequence[str]] = None) -> Tuple[Any, List[Dict[str, Any]]]:
    """Tarkistaa viitteiden sisallon. Palauttaa (teksti, havainnot). Havainto: {"viite", "tunnisteet", "tila",
    "ehdokkaat"}; tila on "korjattu" (viite vaihdettu ainoaan ehdokkaaseen) tai "vaara" (merkitty tekstiin).
    `tiedostot` rajaa ehdokkaat kehotteen tiedostoihin (None -> kaikki muistin otsikot)."""
    if not isinstance(vastaus, str) or "[l\u00e4hde:" not in vastaus:
        return vastaus, []
    sisallot = jasenna_muisti(muisti)
    if tiedostot is not None:
        sallitut = set(tiedostot)
        sisallot = {n: s for n, s in sisallot.items() if n in sallitut}
    if not sisallot:
        return vastaus, []
    havainnot: List[Dict[str, Any]] = []
    korvaukset: List[Tuple[int, int, str]] = []
    edellinen_loppu = 0
    for ryhma in _ryhmat(vastaus):
        alku, loppu = ryhma[0].start(), ryhma[-1].end()
        vaite = _vaite(vastaus, alku, edellinen_loppu)
        edellinen_loppu = loppu
        nimet = [m.group(1).strip() for m in ryhma]
        if any(n not in sisallot for n in nimet):
            continue                     # tuntematon tai otsikoton viite: ei sisaltotarkistusta
        tunn = tunnisteet(vaite)
        if not tunn:
            continue
        puuttuvat = [t for t in tunn if not any(_esiintyy(t, sisallot[n]) for n in nimet)]
        if not puuttuvat:
            continue
        ehdokkaat = [n for n in sisallot if n not in nimet and all(_esiintyy(t, sisallot[n], True) for t in tunn)]
        naytto = ", ".join(t[1] for t in puuttuvat)
        if not ehdokkaat and not any(t[0] in ("paatos", "pvm") for t in puuttuvat):
            continue                     # pelkka luku: voi olla laskettu, ei merkintaa
        vuodeton = any(t[0] == "pvm" and t[2][2] is None for t in tunn)   # vuosi puuttuu: vain merkinta
        if len(ehdokkaat) == 1 and len(ryhma) == 1 and not vuodeton:
            oikea = ehdokkaat[0]
            korvaukset.append((alku, loppu, "[l\u00e4hde: %s] (palvelin korjasi l\u00e4hteen: %s ei ole tiedostossa %s)"
                               % (oikea, naytto, nimet[0])))
            havainnot.append({"viite": nimet[0], "tunnisteet": [t[1] for t in puuttuvat], "tila": "korjattu",
                              "ehdokkaat": [oikea]})
            continue
        if ehdokkaat:
            lisa = "l\u00e4hde v\u00e4\u00e4r\u00e4: %s ei ole tiedostossa, oikea todenn\u00e4k\u00f6isesti %s" % (
                naytto, _luettelo(ehdokkaat))
        else:
            lisa = "l\u00e4hde v\u00e4\u00e4r\u00e4: %s ei ole tiedostossa eik\u00e4 muualla muistissa" % naytto
        viimeinen = ryhma[-1]
        korvaukset.append((viimeinen.end(), viimeinen.end(), " (%s)" % lisa))
        havainnot.append({"viite": ", ".join(nimet), "tunnisteet": [t[1] for t in puuttuvat], "tila": "vaara",
                          "ehdokkaat": ehdokkaat[:EHDOKKAITA_MAX]})
    for a, b, uusi in reversed(korvaukset):
        vastaus = vastaus[:a] + uusi + vastaus[b:]
    return vastaus, havainnot
