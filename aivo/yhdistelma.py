"""Yksityisen tilan usean mallin yhdistelmat (AIVO_YKSITYINEN_YHDISTELMA): yksi, tarkastus, rinnakkais, reititys.

Moduuli on pelkka ohjaus: se ei tee verkkokutsuja eika tuo OpenRouter-moduuleja (rakennetestit). Jokainen mallikutsu
tehdaan parametrina annetulla `kysy(malli, viestit, aikaraja_s, max_tokens)`-funktiolla. Tuotannossa se on
`yksityinen_or._kysy_viestit`, joka kulkee `openrouter_kulut.kutsu`n kautta (paivakatto, ZDR-runko, kulukirjaus) ja
tarkistaa mallin `avoin_malli()`-funktiolla.

Saannot (koodi on laki; muutos vaatii Tonyn luvan):
  * MAX_KUTSUT_VIESTI = 4 mallikutsua per viesti. Laskuri estaa viidennen kutsun ennen kuin `kysy` kutsutaan.
  * MAX_MALLEJA = 3 eri mallia per yhdistelma.
  * AIKARAJA_S = kokonaisaikaraja per viesti. Jokainen osakutsu saa aikarajaksi enintaan jaljella olevan ajan; kun aika
    on lopussa, uutta kutsua ei aloiteta.
  * Jos osakutsu epaonnistuu, palataan yhden mallin vastaukseen (ensimmainen onnistunut vastaus) ja palautetaan
    `merkinta`. Jos yhtaan vastausta ei ole, nostetaan virhe kuten yhden mallin tilassa.
  * Paivakatto (Katkaisu) lopettaa kaikki seuraavat kutsut heti.
  * Ehdotus-, valinta-, luovutus- ja paatosmerkinnat ([[EHDOTUS ..]], [[VALINNAT: ..]], [[LUOVUTUS]], [[PAATOS n]])
    tulevat lopulliseen vastaukseen vain ensisijaisen vastaajan luonnoksesta sanasta sanaan. Tarkastajan tai kokoajan
    kirjoittamat merkinnat poistetaan: ne eivat voi lisata, muuttaa tai pudottaa ehdotuksia."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .yksityinen import YksityinenVirhe

YHDISTELMAT = ("yksi", "tarkastus", "rinnakkais", "reititys")
OLETUS = "yksi"
# Roolit yhdistelmittain. Puuttuva rooli kayttaa keskustelun mallia (valitsimen malli tai listan ensimmainen).
ROOLIT = {
    "yksi": (),
    "tarkastus": ("vastaaja", "tarkastaja"),
    "rinnakkais": ("vastaaja", "vastaaja2", "kokoaja"),
    "reititys": ("reititin", "kevyt", "vaativa"),
}
KAIKKI_ROOLIT = frozenset(r for rr in ROOLIT.values() for r in rr)
MAX_KUTSUT_VIESTI = 4
MAX_MALLEJA = 3
AIKARAJA_S = 240
REITITIN_MAX_TOKENIA = 200
VASTAUS_MAX_MERKKIA = 12_000       # luonnos tai rinnakkainen vastaus seuraavan mallin syotteessa

MERKINTA_RE = re.compile(
    r"\[\[EHDOTUS [^\]\n]*\]\].*?\[\[/EHDOTUS\]\]"
    r"|\[\[LUOVUTUS\]\].*?\[\[/LUOVUTUS\]\]"
    r"|\[\[VALINNAT:[^\n]*?\]\]"
    r"|\[\[PAATOS [0-9]{1,6}\]\]", re.S)
KATKENNUT_RE = re.compile(r"\[\[(?:EHDOTUS [^\]\n]*|LUOVUTUS)\]\](?:(?!\[\[/(?:EHDOTUS|LUOVUTUS)\]\]).)*\Z", re.S)
IRRALLINEN_RE = re.compile(r"\[\[/?(?:EHDOTUS|LUOVUTUS|VALINNAT|PAATOS)[^\]\n]*\]\]")

TARKASTAJA_OHJE = (
    "\n\nROOLISI T\u00c4SS\u00c4 VASTAUKSESSA: tarkastaja. Tonyn viimeisen viestin per\u00e4ss\u00e4 on toisen mallin luonnos vastaukseksi "
    "Tonyn edelliseen viestiin. Kirjoita lopullinen vastaus Tonylle: korjaa asiavirheet, keksityt l\u00e4hteet, "
    "puutteet ja kieli. S\u00e4ilyt\u00e4 oikea sis\u00e4lt\u00f6 ja [l\u00e4hde: ...]-merkinn\u00e4t. Kirjoita vain lopullinen vastaus, "
    "\u00e4l\u00e4 arviota luonnoksesta. \u00c4l\u00e4 kirjoita [[ ]]-merkint\u00f6j\u00e4: palvelin siirt\u00e4\u00e4 luonnoksen merkinn\u00e4t itse.")
KOKOAJA_OHJE = (
    "\n\nROOLISI T\u00c4SS\u00c4 VASTAUKSESSA: kokoaja. Tonyn viimeisen viestin per\u00e4ss\u00e4 on kahden mallin vastaukset (A ja B) "
    "Tonyn edelliseen viestiin. Kirjoita niist\u00e4 yksi paras vastaus Tonylle: ota oikeat ja hy\u00f6dylliset kohdat, "
    "j\u00e4t\u00e4 virheet ja keksityt l\u00e4hteet pois. Kirjoita vain lopullinen vastaus. \u00c4l\u00e4 kirjoita [[ ]]-merkint\u00f6j\u00e4: "
    "palvelin siirt\u00e4\u00e4 vastauksen A merkinn\u00e4t itse.")
REITITIN_OHJE = (
    "Luokittele Tonyn viesti. Vastaa yhdell\u00e4 sanalla: kevyt (lyhyt kysymys, tervehdys, yksinkertainen tieto) "
    "tai vaativa (suunnittelu, p\u00e4\u00e4t\u00f6s, monivaiheinen p\u00e4\u00e4ttely, pitk\u00e4 teksti). \u00c4l\u00e4 vastaa viestiin.")
PALVELIN_ALKU = "[Palvelimen viesti, ei Tonyn]\n"
REITITYS_RE = re.compile(r"\b(kevyt|vaativa)\b", re.I)


class Katkaisu(YksityinenVirhe):
    """Paivakatto tai kirjanpito esti kutsun: seuraavia osakutsuja ei tehda."""


class KutsurajaTaynna(YksityinenVirhe):
    pass


@dataclass
class Tulos:
    teksti: str
    malli: str                                   # lopullisen vastauksen kirjoittanut malli
    yhdistelma: str
    mallit: list[str] = field(default_factory=list)       # kutsutut mallit kutsujarjestyksessa (myos epaonnistuneet)
    kutsuja: int = 0
    luonnos: str | None = None                   # tarkastajan tai kokoajan syote (Nayta luonnos)
    merkinta: str | None = None                  # palattiin yhden mallin vastaukseen


def tarkista_yhdistelma(arvo: Any) -> str:
    a = OLETUS if arvo is None or arvo == "" else arvo
    if a not in YHDISTELMAT:
        raise YksityinenVirhe("tuntematon yhdistelm\u00e4 (sallitut: yksi, tarkastus, rinnakkais, reititys)")
    return a


def jasenna_roolit(arvo: Any, sallitut_mallit: Sequence[str]) -> dict[str, str]:
    """AIVO_YKSITYINEN_ROOLIT: 'vastaaja:malli1,tarkastaja:malli2'. Jokaisen mallin pitaa olla sallittujen listalla
    (joka on jo tarkistettu avoin_malli()-funktiolla). Tuntematon tai toistuva rooli estaa kaynnistyksen."""
    if arvo is None or (isinstance(arvo, str) and not arvo.strip()):
        return {}
    if not isinstance(arvo, str):
        raise YksityinenVirhe("roolit pit\u00e4\u00e4 antaa muodossa rooli:malli,rooli:malli")
    roolit: dict[str, str] = {}
    for osa in arvo.split(","):
        rooli, sep, malli = osa.strip().partition(":")
        rooli, malli = rooli.strip(), malli.strip()
        if not sep or rooli not in KAIKKI_ROOLIT:
            raise YksityinenVirhe("tuntematon rooli asetuksessa AIVO_YKSITYINEN_ROOLIT")
        if rooli in roolit:
            raise YksityinenVirhe("sama rooli kahdesti asetuksessa AIVO_YKSITYINEN_ROOLIT")
        if malli not in sallitut_mallit:
            raise YksityinenVirhe("roolin malli ei ole sallittujen mallien listalla (AIVO_OPENROUTER_MALLIT)")
        roolit[rooli] = malli
    return roolit


def roolien_mallit(yhdistelma: str, roolit: Mapping[str, str], oletusmalli: str) -> dict[str, str]:
    m = {r: roolit.get(r) or oletusmalli for r in ROOLIT[yhdistelma]}
    if len(set(m.values())) > MAX_MALLEJA:
        raise YksityinenVirhe("yhdistelm\u00e4ss\u00e4 saa olla enint\u00e4\u00e4n 3 eri mallia")
    return m


def merkinnat(teksti: str) -> list[str]:
    """Luonnoksen merkinnat sanasta sanaan esiintymisjarjestyksessa (myos katkennut avoin lohko lopussa)."""
    loydetyt = [m.group(0) for m in MERKINTA_RE.finditer(teksti)]
    loppu = KATKENNUT_RE.search(MERKINTA_RE.sub("", teksti))
    if loppu:
        loydetyt.append(loppu.group(0))
    return loydetyt


def poista_merkinnat(teksti: str) -> str:
    t = MERKINTA_RE.sub("", teksti)
    t = KATKENNUT_RE.sub("", t)
    return IRRALLINEN_RE.sub("", t).strip()


def siirra_merkinnat(luonnos: str, lopullinen: str) -> str:
    """Lopullinen teksti ilman omia merkintojaan + luonnoksen merkinnat lopussa. VALINNAT on aina viimeisena
    (chat.erota_valinnat lukee sen vain vastauksen lopusta), katkennut lohko sita ennen."""
    mm = merkinnat(luonnos)
    if not mm:
        return poista_merkinnat(lopullinen)
    valinnat = [m for m in mm if m.startswith("[[VALINNAT:")]
    muut = [m for m in mm if not m.startswith("[[VALINNAT:")]
    osat = [poista_merkinnat(lopullinen)] + muut + valinnat[-1:]
    return "\n\n".join(o for o in osat if o)


def _lisaa_rooli(perusviestit: Sequence[Mapping[str, Any]], ohje: str, sisalto: str) -> list[dict[str, Any]]:
    viestit = [dict(v) for v in perusviestit]
    if viestit and viestit[0].get("role") == "system":
        viestit[0]["content"] = str(viestit[0].get("content", "")) + ohje
    else:
        viestit.insert(0, {"role": "system", "content": ohje.strip()})
    if viestit and viestit[-1].get("role") == "user":     # vuorottelu sailyy: syote Tonyn viestin peraan
        viestit[-1]["content"] = str(viestit[-1].get("content", "")) + "\n\n" + PALVELIN_ALKU + sisalto
    else:
        viestit.append({"role": "user", "content": PALVELIN_ALKU + sisalto})
    return viestit


class _Ajo:
    def __init__(self, kysy: Callable[..., str], kello: Callable[[], float], aikaraja_s: float):
        self.kysy = kysy
        self.kello = kello
        self.loppu = kello() + aikaraja_s
        self.kutsuja = 0
        self.mallit: list[str] = []
        self.katkaistu = False
        self._lukko = threading.Lock()

    def jaljella(self) -> float:
        return self.loppu - self.kello()

    def kutsu(self, malli: str, viestit: list[dict[str, Any]], max_tokens: int | None = None) -> str:
        with self._lukko:
            if self.katkaistu:
                raise Katkaisu("p\u00e4iv\u00e4katto t\u00e4ynn\u00e4; seuraavaa kutsua ei tehty")
            if self.kutsuja >= MAX_KUTSUT_VIESTI:
                raise KutsurajaTaynna("kutsuraja (4 per viesti) t\u00e4ynn\u00e4")
            jaljella = self.jaljella()
            if jaljella <= 1:
                raise YksityinenVirhe("aikaraja t\u00e4ynn\u00e4; kutsua ei tehty")
            self.kutsuja += 1
            self.mallit.append(malli)
        try:
            return self.kysy(malli, viestit, aikaraja_s=jaljella, max_tokens=max_tokens)
        except Katkaisu:
            self.katkaistu = True
            raise


def _syy(e: BaseException) -> str:
    if isinstance(e, Katkaisu):
        return "p\u00e4iv\u00e4katto"
    if isinstance(e, KutsurajaTaynna):
        return "kutsuraja"
    return "osakutsu ep\u00e4onnistui"


def _yhden_mallin_merkinta(e: BaseException) -> str:
    return "Yhdistelm\u00e4 keskeytyi (%s): n\u00e4ytet\u00e4\u00e4n yhden mallin vastaus." % _syy(e)


def aja(yhdistelma: str, roolit: Mapping[str, str], oletusmalli: str, perusviestit: Sequence[Mapping[str, Any]],
        kysy: Callable[..., str], *, kello: Callable[[], float] = time.monotonic, aikaraja_s: float = AIKARAJA_S) -> Tulos:
    """Ajaa yhdistelman. `perusviestit` on yhden mallin tilan viestilista (system + historia, viimeisena Tonyn viesti).
    Nostaa YksityinenVirhe, jos yhtaan vastausta ei saatu."""
    yhdistelma = tarkista_yhdistelma(yhdistelma)
    rm = roolien_mallit(yhdistelma, roolit, oletusmalli)
    ajo = _Ajo(kysy, kello, min(aikaraja_s, AIKARAJA_S))
    perus = [dict(v) for v in perusviestit]

    def tulos(teksti, malli, **kw):
        return Tulos(teksti=teksti, malli=malli, yhdistelma=yhdistelma, mallit=list(ajo.mallit), kutsuja=ajo.kutsuja, **kw)

    if yhdistelma == "yksi":
        return tulos(ajo.kutsu(oletusmalli, perus), oletusmalli)

    if yhdistelma == "tarkastus":
        luonnos = ajo.kutsu(rm["vastaaja"], perus)
        try:
            lopullinen = ajo.kutsu(rm["tarkastaja"], _lisaa_rooli(
                perus, TARKASTAJA_OHJE, "LUONNOS:\n" + poista_merkinnat(luonnos)[:VASTAUS_MAX_MERKKIA]))
        except YksityinenVirhe as e:
            return tulos(luonnos, rm["vastaaja"], merkinta=_yhden_mallin_merkinta(e))
        return tulos(siirra_merkinnat(luonnos, lopullinen), rm["tarkastaja"], luonnos=luonnos)

    if yhdistelma == "rinnakkais":
        vastaukset: dict[str, Any] = {}

        def aja_vastaaja(rooli):
            try:
                vastaukset[rooli] = ajo.kutsu(rm[rooli], perus)
            except BaseException as e:  # noqa: BLE001 - virhe kasitellaan alla
                vastaukset[rooli] = e
        saikeet = [threading.Thread(target=aja_vastaaja, args=(r,), daemon=True) for r in ("vastaaja", "vastaaja2")]
        for s in saikeet:
            s.start()
        for s in saikeet:
            s.join(max(0.0, ajo.jaljella()))
        a, b = vastaukset.get("vastaaja"), vastaukset.get("vastaaja2")
        ok = [(r, v) for r, v in (("vastaaja", a), ("vastaaja2", b)) if isinstance(v, str)]
        if not ok:
            virhe = a if isinstance(a, BaseException) else b
            if isinstance(virhe, YksityinenVirhe):
                raise virhe
            raise YksityinenVirhe("aikaraja t\u00e4ynn\u00e4; vastausta ei saatu")
        if len(ok) == 1:
            virhe = next((v for v in (a, b) if isinstance(v, BaseException)), YksityinenVirhe("aikaraja"))
            return tulos(ok[0][1], rm[ok[0][0]], merkinta=_yhden_mallin_merkinta(virhe))
        try:
            lopullinen = ajo.kutsu(rm["kokoaja"], _lisaa_rooli(
                perus, KOKOAJA_OHJE, "VASTAUS A:\n%s\n\nVASTAUS B:\n%s" % (
                    poista_merkinnat(a)[:VASTAUS_MAX_MERKKIA], poista_merkinnat(b)[:VASTAUS_MAX_MERKKIA])))
        except YksityinenVirhe as e:
            return tulos(a, rm["vastaaja"], merkinta=_yhden_mallin_merkinta(e))
        return tulos(siirra_merkinnat(a, lopullinen), rm["kokoaja"],
                     luonnos="VASTAUS A (%s):\n%s\n\nVASTAUS B (%s):\n%s" % (rm["vastaaja"], a, rm["vastaaja2"], b))

    # reititys: halpa reititin valitsee mallin; reitittimen virhe tai epaselva vastaus -> kevyt malli
    tony = perus[-1].get("content", "") if perus and perus[-1].get("role") == "user" else ""
    merkinta = None
    try:
        luokka = ajo.kutsu(rm["reititin"], [{"role": "system", "content": REITITIN_OHJE},
                                            {"role": "user", "content": str(tony)[:4000]}],
                           max_tokens=REITITIN_MAX_TOKENIA)
        m = REITITYS_RE.search(poista_merkinnat(luokka))
        if not m:
            raise YksityinenVirhe("reititin ei antanut luokkaa")
        malli = rm[m.group(1).lower()]
    except Katkaisu:
        raise
    except YksityinenVirhe as e:
        malli, merkinta = rm["kevyt"], _yhden_mallin_merkinta(e)
    return tulos(ajo.kutsu(malli, perus), malli, merkinta=merkinta)
