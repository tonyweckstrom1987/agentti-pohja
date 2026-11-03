"""Talonmies, vaihe 1: EHDOTUSTILA. Lukee palvelimen tilan ja kirjoittaa ehdotuksia. EI TEE MUUTOKSIA.

Vaiheistus ja tasot: docs/talonmies.md. Tama moduuli ei sisalla yhtaan toimenpidetta (ei restart, ei apt install,
ei poistoa, ei snapshotia). Ainoat ulkoiset komennot ovat alla olevat kiinteat vain luku -komennot (KOMENNOT);
mitaan ei rakenneta syotteesta. Kirjoitus tapahtuu vain annettuun tilahakemistoon.

Kaytto:
  python3 -m agentti.talonmies ajo <tila>                      ehdotusajo; kirjoittaa raportin ja kirjanpidon
  python3 -m agentti.talonmies paata <tila> <tunniste> kylla|ei [huomio]   Tonyn paatos ehdotukseen
  python3 -m agentti.talonmies mittari <tila> [--raja 0.8] [--min 10]      osumatarkkuus toimenpiteittain
  python3 -m agentti.talonmies snapshot-siivous <tiedosto>                 siivousehdotus pct listsnapshot -tulosteesta
Valitsin ajo-komennolle: --halyta lahettaa sisallottoman Telegram-halytyksen, kun thin pool ylittaa halytysrajan.
Pysaytystiedosto <tila>/PYSAYTA: kun se on olemassa, ajo ei tee mitaan.

Koodi on ASCII-muotoista; ihmiselle menevat suomenkieliset tekstit kirjoitetaan \\u-koodein.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from .lukko import kirjoita_atomisesti, lukittu

# Kiinteat vain luku -komennot. Mikaan niista ei muuta jarjestelman tilaa.
KOMENNOT = {
    "epaonnistuneet": ["systemctl", "--failed", "--no-legend", "--no-pager"],
    "levy": ["df", "-P", "/"],
    "paivitykset": ["apt", "list", "--upgradable"],
}
KOMENTO_AIKARAJA = 30
PYSAYTUS = "PYSAYTA"
EHDOTUKSET = "ehdotukset.jsonl"
PAATOKSET = "paatokset.jsonl"
RAPORTIT = "raportit"
TASOT = ("A", "B", "C")
LEVY_RAJA = 80            # prosenttia (docs/talonmies.md)
LEVY_HALYTYS = 90
VARMUUSKOPIO_IKA_H = 36
PALAUTUSTESTI_PAIVIA = 31
KOHINA = ("dev-mqueue.mount", "run-lock.mount", "tmp.mount")   # LXC:ssa aina failed
TUNNISTE_RE = re.compile(r"^\d{8}-[a-z0-9-]{3,40}-[0-9a-f]{6}$")
HUOMIO_MAX = 300
# Telegram-halytyksen otsikko on kiintea: ei lukuja, ei tunnisteita, ei dataa.
HALYTYSOTSIKKO = {"thinpool-halytys": "Talonmies: konttilevyn thin pool ylitti h\u00e4lytysrajan",
                  "thinpool-kiireellinen": "Talonmies: KIIREELLINEN konttilevyn thin pool l\u00e4hes t\u00e4ynn\u00e4"}
# Konttilevyn thin pool (Tonyn raja-arvot 3.11.2026): kooste > 60 %, halytys > 75 %, kiireellinen > 85 %.
# Raja koskee seka dataa etta metadataa (korkeampi ratkaisee). Rajat ovat ehdotusten luokitus, eivat oikeuksia.
THIN_KOOSTE = 60
THIN_HALYTYS = 75
THIN_KIIREELLINEN = 85
THIN_LVS_OSIO = "## Thin pool (lvs)"
THIN_PVESM_OSIO = "## Tallennustila (pvesm status)"
SNAPSHOT_RAJA = 5            # siivousehdotus kontille, jolla on enemman snapshotteja
SNAPSHOT_PERUS = "perus-toimii"
SNAPSHOT_UUSIMPIA = 2
SNAPSHOT_NIMI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
SNAPSHOT_RIVI_RE = re.compile(r"^[\s`|]*->\s+(\S+)(?:\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?")
KUUKAUDET = {m: i + 1 for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}


class TalonmiesVirhe(Exception):
    pass


def tunniste(pvm, toimenpide, kohde):
    h = hashlib.sha256(("%s|%s|%s" % (pvm, toimenpide, kohde)).encode("utf-8")).hexdigest()[:6]
    return "%s-%s-%s" % (pvm.replace("-", ""), toimenpide, h)


def ehdotus(pvm, toimenpide, kohde, syy, ehdotettu, taso="C"):
    assert taso in TASOT
    return {"tunniste": tunniste(pvm, toimenpide, kohde), "pvm": pvm, "taso": taso, "toimenpide": toimenpide,
            "kohde": kohde, "syy": syy, "ehdotettu": ehdotettu}


# ---- Analyysi (puhtaita funktioita: teksti sisaan, ehdotukset ulos) ----

def ehdota_kaatuneet(teksti, pvm):
    """Kaatunut palvelu: A-tason ehdotus 'kaynnista kerran'. Toinen kaatuminen on halytys (C), sen seuraa myohempi vaihe."""
    tulos = []
    for rivi in (teksti or "").splitlines():
        osat = [o for o in rivi.split() if o not in ("\u25cf", "*")]
        if not osat or not osat[0].endswith((".service", ".timer", ".socket", ".mount")):
            continue
        if any(k in osat[0] for k in KOHINA):
            continue
        yksikko = osat[0]
        tulos.append(ehdotus(
            pvm, "kaatunut-palvelu", yksikko,
            "Palvelu %s on tilassa failed." % yksikko,
            "K\u00e4ynnist\u00e4 uudelleen kerran, tarkista tila 60 s kuluttua. Jos kaatuu uudelleen, h\u00e4lytys Telegramiin "
            "(ei uutta yrityst\u00e4).", taso="A"))
    return tulos


def jasenna_levy(teksti):
    """df -P -tuloste -> [(liitospiste, kaytto_prosenttia)]."""
    tulos = []
    for rivi in (teksti or "").splitlines()[1:]:
        k = rivi.split()
        if len(k) >= 6 and k[4].endswith("%") and k[4][:-1].isdigit():
            tulos.append((k[5], int(k[4][:-1])))
    return tulos


def ehdota_levy(teksti, pvm, raja=LEVY_RAJA):
    tulos = []
    for piste, pros in jasenna_levy(teksti):
        if pros < raja:
            continue
        taso = "A" if pros < LEVY_HALYTYS else "C"
        ehd = ("Siivoa vanhat lokit (journal vacuum) ja v\u00e4liaikaistiedostot (/tmp, vanhemmat kuin 7 vrk) "
               "vain listatulla siivousskriptill\u00e4. Dataa ei poisteta." if taso == "A" else
               "Levy yli %d %%: h\u00e4lytys Tonylle. Siivous ei riit\u00e4 tai vaatii p\u00e4\u00e4t\u00f6ksen." % LEVY_HALYTYS)
        tulos.append(ehdotus(pvm, "levy-taynna", piste, "Levy %s on %d %% t\u00e4ynn\u00e4 (raja %d %%)." % (piste, pros, raja), ehd, taso))
    return tulos


def _ika_tunteina(kuukausi, paiva, kello, nyt):
    """ls -lt -rivin 'Nov  1 03:00' -> ika tunteina (vuosi paatellaan: ei tulevaisuutta)."""
    try:
        h, m = (int(x) for x in kello.split(":"))
        d = datetime(nyt.year, KUUKAUDET[kuukausi], int(paiva), h, m, tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None
    if d > nyt + timedelta(hours=1):
        d = d.replace(year=nyt.year - 1)
    return (nyt - d).total_seconds() / 3600.0


def uusin_varmuuskopio_ika(isanta_teksti, nyt):
    """Etsii isannan tilatulosteen kohdasta 'Viimeisimmat varmuuskopiot' uusimman vzdump-tiedoston i\u00e4n tunteina."""
    osio = (isanta_teksti or "").split("## Viimeisimmat varmuuskopiot", 1)
    if len(osio) < 2:
        return None
    osio = osio[1].split("\n## ", 1)[0]
    ikaa = []
    for rivi in osio.splitlines():
        k = rivi.split()
        if len(k) >= 9 and "vzdump" in k[-1] and not k[-1].endswith((".log", ".notes")):
            ika = _ika_tunteina(k[5], k[6], k[7], nyt)
            if ika is not None:
                ikaa.append(ika)
    return min(ikaa) if ikaa else None


def ehdota_varmuuskopiot(isanta_teksti, pvm, nyt, viimeisin_palautustesti=None):
    tulos = []
    if isanta_teksti is None:
        tulos.append(ehdotus(pvm, "varmuuskopio-tuoreus", "isanta", "Is\u00e4nn\u00e4n tilaa ei saatu luettua, tuoreutta ei voitu tarkistaa.",
                             "Tarkista snapshot-yhteys (python3 -m agentti.snapshot_kutsu tila)."))
    else:
        ika = uusin_varmuuskopio_ika(isanta_teksti, nyt)
        if ika is None:
            tulos.append(ehdotus(pvm, "varmuuskopio-tuoreus", "isanta",
                                 "Is\u00e4nn\u00e4n dump-hakemistosta ei l\u00f6ytynyt varmuuskopiotiedostoja.",
                                 "H\u00e4lytys Tonylle: tarkista vzdump-ajastus ja salattu Drive-kopio."))
        elif ika > VARMUUSKOPIO_IKA_H:
            tulos.append(ehdotus(pvm, "varmuuskopio-tuoreus", "isanta",
                                 "Uusin varmuuskopio on %.0f h vanha (raja %d h)." % (ika, VARMUUSKOPIO_IKA_H),
                                 "H\u00e4lytys Tonylle. Varmuuskopiota ei k\u00e4ynnistet\u00e4 itse."))
    if viimeisin_palautustesti is None or (nyt.date() - viimeisin_palautustesti).days > PALAUTUSTESTI_PAIVIA:
        tulos.append(ehdotus(pvm, "palautustesti", "kuukausittain",
                             "Palautustestist\u00e4 ei ole kirjausta %d vuorokauteen." % PALAUTUSTESTI_PAIVIA,
                             "Ehdotus Tonylle: palautustesti uuteen v\u00e4liaikaiseen kontti-ID:hen (vanhaa ei kosketa). "
                             "Kirjaa suoritus tiedostoon palautustesti.txt (YYYY-MM-DD)."))
    return tulos


def ehdota_paivitykset(teksti, pvm):
    rivit = [r for r in (teksti or "").splitlines() if "[upgradable from" in r]
    if not rivit:
        return []
    turva = [r.split("/", 1)[0] for r in rivit if "/" in r and "security" in r.split()[0].split("/", 1)[1]]
    tulos = []
    if turva:
        tulos.append(ehdotus(pvm, "tietoturvapaivitys", "ct115",
                             "%d tietoturvap\u00e4ivityst\u00e4 odottaa (%s)." % (len(turva), ", ".join(sorted(turva)[:8])),
                             "Snapshot ensin, p\u00e4ivit\u00e4 vain listatut paketit, terveystarkistus, automaattinen peruutus "
                             "ep\u00e4onnistuessa (taso B).", taso="B"))
    muut = len(rivit) - len(turva)
    if muut:
        tulos.append(ehdotus(pvm, "muut-paivitykset", "ct115", "%d tavallista p\u00e4ivityst\u00e4 odottaa." % muut,
                             "Ei toimenpidett\u00e4 automaattisesti. Tony p\u00e4\u00e4tt\u00e4\u00e4 sopivan hetken (taso C)."))
    return tulos


# ---- Thin pool ----

def _osio(teksti, otsikko):
    osat = (teksti or "").split(otsikko, 1)
    return osat[1].split("\n## ", 1)[0] if len(osat) == 2 else None


def _pros(arvo):
    try:
        v = float(arvo.replace(",", "."))
    except ValueError:
        return None
    return v if 0 <= v <= 100 else None


def jasenna_thinpool(isanta_teksti):
    """Isannan tilatulosteesta thin poolit: [(nimi, data%, metadata% tai None)].

    Ensisijainen lahde on osio '## Thin pool (lvs)' (rivit lv,vg,attr,pool,data,metadata). Jos sita ei ole, varalla
    on 'pvesm status' (lvmthin-rivin %-sarake = data; metadata ei ole saatavilla)."""
    tulos = []
    lvs = _osio(isanta_teksti, THIN_LVS_OSIO)
    if lvs is not None:
        for rivi in lvs.splitlines():
            k = [x.strip() for x in rivi.split(",")]
            if len(k) == 6 and k[2][:1] == "t":
                data, meta = _pros(k[4]), _pros(k[5])
                if data is not None and meta is not None:
                    tulos.append(("%s/%s" % (k[1], k[0]), data, meta))
        return tulos
    pvesm = _osio(isanta_teksti, THIN_PVESM_OSIO)
    for rivi in (pvesm or "").splitlines():
        k = rivi.split()
        if len(k) >= 7 and k[1] == "lvmthin" and k[6].endswith("%"):
            data = _pros(k[6][:-1])
            if data is not None:
                tulos.append((k[0], data, None))
    return tulos


def thinpool_taso(data, meta):
    """'ok' | 'kooste' | 'halytys' | 'kiireellinen' (korkeampi arvo ratkaisee, raja on tiukasti ylittava)."""
    suurin = max(data, meta if meta is not None else 0)
    if suurin > THIN_KIIREELLINEN:
        return "kiireellinen"
    if suurin > THIN_HALYTYS:
        return "halytys"
    if suurin > THIN_KOOSTE:
        return "kooste"
    return "ok"


def _suomi_pros(v):
    return ("%.2f" % v).replace(".", ",") + " %"


def thinpool_rivi(nimi, data, meta):
    teksti = "Thin pool %s: data %s, metadata %s" % (
        nimi, _suomi_pros(data), _suomi_pros(meta) if meta is not None else "ei luettavissa")
    return teksti + " (taso: %s)." % thinpool_taso(data, meta)


def ehdota_thinpool(poolit, pvm):
    """Ylitys -> ehdotus (taso C, vain Tonylle). Toimenpiteen nimi kertoo kanavan: kooste/halytys/kiireellinen."""
    tulos = []
    for nimi, data, meta in poolit:
        taso = thinpool_taso(data, meta)
        if taso == "ok":
            continue
        kuvaus = {"kooste": "Rivi aamukoosteeseen.",
                  "halytys": "H\u00e4lytys Telegramiin (sis\u00e4llt\u00f6n).",
                  "kiireellinen": "KIIREELLINEN: h\u00e4lytys Telegramiin (sis\u00e4llt\u00f6n), kontit pys\u00e4htyv\u00e4t jos pool t\u00e4yttyy."}[taso]
        tulos.append(ehdotus(
            pvm, "thinpool-" + taso, nimi,
            thinpool_rivi(nimi, data, meta) + " Rajat: kooste %d %%, h\u00e4lytys %d %%, kiireellinen %d %%." % (
                THIN_KOOSTE, THIN_HALYTYS, THIN_KIIREELLINEN),
            kuvaus + " Tarkista snapshotit (python3 -m agentti.talonmies snapshot-siivous). "
            "Poisto ja LVM-asetukset ovat Tonyn p\u00e4\u00e4t\u00f6s.", taso="C"))
    return tulos


# ---- Snapshot-siivousehdotus (vain teksti, ei poistoja) ----

def jasenna_snapshotit(teksti):
    """Teksti, jossa osiot '## CT<id>' + `pct listsnapshot <id>` -tuloste -> {id: [(nimi, aika, jarjestys)]}.

    Vain nimet, jotka tayttavat Proxmoxin nimisaannon, hyvaksytaan (data ei paase komentoihin sellaisenaan)."""
    tulos, ct = {}, None
    for rivi in (teksti or "").splitlines():
        m = re.match(r"^##\s*CT(\d{3,6})\b", rivi)
        if m:
            ct = m.group(1)
            tulos.setdefault(ct, [])
            continue
        m = SNAPSHOT_RIVI_RE.match(rivi)
        if ct is None or not m or m.group(1) == "current" or not SNAPSHOT_NIMI_RE.match(m.group(1)):
            continue
        tulos[ct].append((m.group(1), m.group(2) or "", len(tulos[ct])))
    return tulos


def ehdota_snapshot_siivous(snapshotit):
    """Kontit, joilla on yli SNAPSHOT_RAJA snapshotia: sailyta perus-toimii + kaksi uusinta, loput ehdotetaan poistettaviksi.

    Palauttaa [{ct, yhteensa, sailyta, poista, varoitus}]. perus-toimii puuttuu -> ei poistoehdotusta (varoitus)."""
    tulos = []
    for ct in sorted(snapshotit, key=int):
        lista = snapshotit[ct]
        if len(lista) <= SNAPSHOT_RAJA:
            continue
        vanhimmasta = [x[0] for x in sorted(lista, key=lambda x: (x[1], x[2]))]
        if SNAPSHOT_PERUS not in vanhimmasta:
            tulos.append({"ct": ct, "yhteensa": len(lista), "sailyta": [], "poista": [],
                          "varoitus": "snapshotia %s ei ole: ei poistoehdotusta, p\u00e4\u00e4t\u00e4 k\u00e4sin" % SNAPSHOT_PERUS})
            continue
        muut = [n for n in vanhimmasta if n != SNAPSHOT_PERUS]
        sailyta = [SNAPSHOT_PERUS] + muut[-SNAPSHOT_UUSIMPIA:]
        tulos.append({"ct": ct, "yhteensa": len(lista), "sailyta": sailyta,
                      "poista": [n for n in vanhimmasta if n not in sailyta], "varoitus": ""})
    return tulos


def snapshot_siivousteksti(ehdotukset, snapshotit):
    rivit = ["# Snapshot-siivousehdotus", "",
             "Mit\u00e4\u00e4n ei ole poistettu. Poisto on Tonyn p\u00e4\u00e4t\u00f6s: ajettavat komennot ovat alla, "
             "yksi kerrallaan Proxmox-is\u00e4nn\u00e4ll\u00e4.", ""]
    if not ehdotukset:
        rivit.append("Yht\u00e4\u00e4n kontteja, joilla olisi yli %d snapshotia, ei l\u00f6ytynyt (kontteja luettu: %d)." % (
            SNAPSHOT_RAJA, len(snapshotit)))
    for e in ehdotukset:
        rivit += ["## CT%s: %d snapshotia" % (e["ct"], e["yhteensa"]), ""]
        if e["varoitus"]:
            rivit += ["- Varoitus: " + e["varoitus"], ""]
            continue
        rivit.append("S\u00e4ilytet\u00e4\u00e4n: " + ", ".join(e["sailyta"]) + ". Poistettavaksi ehdotetaan %d (vanhimmasta):" % len(e["poista"]))
        rivit += ["", "```"] + ["pct delsnapshot %s %s" % (e["ct"], n) for n in e["poista"]] + ["```", ""]
    rivit += ["Tarkistus poistojen j\u00e4lkeen: `lvs --noheadings -o lv_name,data_percent,metadata_percent konttilevy/konttilevy`.", ""]
    return "\n".join(rivit)


# ---- Keruu (vain luku) ----

def aja_komento(nimi, suorita=subprocess.run):
    if nimi not in KOMENNOT:
        raise TalonmiesVirhe("tuntematon komento")
    try:
        r = suorita(KOMENNOT[nimi], capture_output=True, text=True, timeout=KOMENTO_AIKARAJA,
                    env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TalonmiesVirhe(type(e).__name__)
    return r.stdout or ""


def lue_palautustesti(tila):
    try:
        with open(os.path.join(tila, "palautustesti.txt"), encoding="utf-8") as f:
            return datetime.strptime(f.read().strip()[:10], "%Y-%m-%d").date()
    except (OSError, ValueError):
        return None


def keraa(tila, nyt=None, suorita=subprocess.run, hae_isanta=None):
    """Palauttaa ehdotukset, huomautukset (mita ei saatu luettua) ja tilannerivit (myos kun kaikki on kunnossa). Ei muuta jarjestelmaa."""
    nyt = nyt or datetime.now(timezone.utc)
    pvm = nyt.strftime("%Y-%m-%d")
    ehd, huom, tilanne = [], [], []
    for nimi, analyysi in (("epaonnistuneet", ehdota_kaatuneet), ("levy", ehdota_levy), ("paivitykset", ehdota_paivitykset)):
        try:
            ehd += analyysi(aja_komento(nimi, suorita), pvm)
        except TalonmiesVirhe as e:
            huom.append("%s: ei luettavissa (%s)" % (nimi, e))
    isanta = None
    try:
        if hae_isanta is None:
            from . import snapshot_kutsu
            hae_isanta = snapshot_kutsu.tila
        isanta = hae_isanta()
    except Exception as e:      # vain tyyppi, viesti voi sisaltaa salaisuuksia
        huom.append("is\u00e4nn\u00e4n tila: ei luettavissa (%s)" % type(e).__name__)
    ehd += ehdota_varmuuskopiot(isanta, pvm, nyt, lue_palautustesti(tila))
    if isanta is not None:
        poolit = jasenna_thinpool(isanta)
        if not poolit:
            huom.append("thin pool: ei luettavissa isannan tilasta (lis\u00e4\u00e4 lvs-osio, ks. docs/talonmies-asennus.md)")
        tilanne += [thinpool_rivi(*p) for p in poolit]
        if any(p[2] is None for p in poolit):
            huom.append("thin pool: metadataa ei saatu luettua (vain pvesm status), lis\u00e4\u00e4 lvs-osio")
        ehd += ehdota_thinpool(poolit, pvm)
        for ct, teksti in re.findall(r"## CT(\d+) epaonnistuneet palvelut\n(.*?)(?=\n## |\Z)", isanta, re.S):
            for e in ehdota_kaatuneet(teksti, pvm):
                e["kohde"] = "ct%s:%s" % (ct, e["kohde"])
                e["tunniste"] = tunniste(pvm, e["toimenpide"], e["kohde"])
                ehd.append(e)
    else:
        tilanne.append("Thin pool: is\u00e4nn\u00e4n tilaa ei saatu luettua.")
    return ehd, huom, tilanne


# ---- Raportti ja kirjanpito ----

def raportti(ehd, huom, pvm, tilanne=()):
    rivit = ["# Talonmies, ehdotusraportti %s" % pvm, "",
             "Ehdotustila: mit\u00e4\u00e4n ei ole tehty. Vastaa komennolla "
             "`python3 -m agentti.talonmies paata <tila> <tunniste> kylla|ei`.", ""]
    if tilanne:
        rivit += ["## Tilanne", ""] + ["- " + r for r in tilanne] + [""]
    if not ehd:
        rivit.append("Ei ehdotuksia: kaikki tarkistetut asiat ovat kunnossa.")
    for taso, otsikko in (("A", "Taso A (saisi tehd\u00e4 itse)"), ("B", "Taso B (snapshot ja peruutus)"),
                          ("C", "Taso C (vain Tonylle)")):
        osa = [e for e in ehd if e["taso"] == taso]
        if osa:
            rivit += ["## " + otsikko, ""]
            for e in osa:
                rivit += ["- `%s` %s: %s Ehdotus: %s" % (e["tunniste"], e["toimenpide"], e["syy"], e["ehdotettu"])]
            rivit.append("")
    if huom:
        rivit += ["## Huomautukset", ""] + ["- " + h for h in huom] + [""]
    return "\n".join(rivit) + "\n"


def _lue_jsonl(polku):
    rivit = []
    try:
        with open(polku, encoding="utf-8") as f:
            for r in f:
                try:
                    rivit.append(json.loads(r))
                except ValueError:
                    continue
    except OSError:
        pass
    return rivit


def _liita(polku, rivi):
    with lukittu(polku + ".lock"):
        with open(polku, "a", encoding="utf-8") as f:
            f.write(json.dumps(rivi, ensure_ascii=True, sort_keys=True) + "\n")


def ajo(tila, nyt=None, suorita=subprocess.run, hae_isanta=None, halyta=None):
    """Ehdotusajo. Palauttaa (raporttiteksti, ehdotukset). Pysaytystiedosto estaa kaiken.

    halyta(otsikko): valinnainen; kutsutaan kerran per uusi thinpool-halytys/kiireellinen -ehdotus (sisallotun viestin otsikolla)."""
    nyt = nyt or datetime.now(timezone.utc)
    if os.path.exists(os.path.join(tila, PYSAYTUS)):
        return "Talonmies pys\u00e4ytetty (%s on olemassa). Ei tehty mit\u00e4\u00e4n.\n" % PYSAYTUS, []
    os.makedirs(os.path.join(tila, RAPORTIT), exist_ok=True)
    ehd, huom, tilanne = keraa(tila, nyt, suorita, hae_isanta)
    pvm = nyt.strftime("%Y-%m-%d")
    teksti = raportti(ehd, huom, pvm, tilanne)
    kirjoita_atomisesti(os.path.join(tila, RAPORTIT, pvm + ".md"), teksti)
    vanhat = {r.get("tunniste") for r in _lue_jsonl(os.path.join(tila, EHDOTUKSET))}
    for e in ehd:
        if e["tunniste"] not in vanhat:      # saman paivan uusinta ei tuplaa kirjanpitoa
            _liita(os.path.join(tila, EHDOTUKSET), e)
            if halyta and e["toimenpide"] in ("thinpool-halytys", "thinpool-kiireellinen"):
                halyta(HALYTYSOTSIKKO[e["toimenpide"]])
    return teksti, ehd


def paata(tila, tunn, vastaus, huomio="", nyt=None):
    """Tonyn kylla/ei yhteen ehdotukseen. Myohempi paatos korvaa aiemman (mittari lukee viimeisimman)."""
    if not TUNNISTE_RE.match(tunn or ""):
        raise TalonmiesVirhe("virheellinen tunniste")
    if vastaus not in ("kylla", "ei"):
        raise TalonmiesVirhe("vastauksen pit\u00e4\u00e4 olla kylla tai ei")
    tunnetut = {r.get("tunniste"): r for r in _lue_jsonl(os.path.join(tila, EHDOTUKSET))}
    if tunn not in tunnetut:
        raise TalonmiesVirhe("tunnistetta ei l\u00f6ydy")
    nyt = nyt or datetime.now(timezone.utc)
    huomio = "".join(c for c in str(huomio) if c >= " ")[:HUOMIO_MAX]
    _liita(os.path.join(tila, PAATOKSET), {"tunniste": tunn, "toimenpide": tunnetut[tunn]["toimenpide"],
                                           "vastaus": vastaus, "huomio": huomio, "aika": nyt.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return tunnetut[tunn]["toimenpide"]


def mittari(tila, raja=None, vahintaan=10):
    """Hyvaksymisosuus toimenpiteittain: kylla / (kylla + ei) ehdotuksista, joihin Tony on vastannut.

    Raja ja vahimmaismaara ovat Tonyn paatettavia: raja=None -> ei suositusta. Vastaamattomat ehdotukset
    raportoidaan erikseen (ne eivat nosta eivatka laske osuutta)."""
    ehd = _lue_jsonl(os.path.join(tila, EHDOTUKSET))
    viimeisin = {}
    for p in _lue_jsonl(os.path.join(tila, PAATOKSET)):
        viimeisin[p.get("tunniste")] = p.get("vastaus")
    rivit = {}
    for e in ehd:
        r = rivit.setdefault(e["toimenpide"], {"ehdotettu": 0, "kylla": 0, "ei": 0})
        r["ehdotettu"] += 1
        v = viimeisin.get(e["tunniste"])
        if v in ("kylla", "ei"):
            r[v] += 1
    for nimi, r in rivit.items():
        paatetty = r["kylla"] + r["ei"]
        r["vastaamatta"] = r["ehdotettu"] - paatetty
        r["osuus"] = round(r["kylla"] / paatetty, 3) if paatetty else None
        r["kypsa"] = bool(raja is not None and paatetty >= vahintaan and r["osuus"] is not None and r["osuus"] >= raja)
    return rivit


def _telegram_halytys(otsikko):
    """Lahettaa kiinteaotsikkoisen halytyksen ilmoita-moduulin kautta (tunnukset sen omasta tiedostosta, nopeusraja mukana)."""
    from . import ilmoita
    ilmoita.ilmoita("virhe", otsikko, "Katso talonmiehen aamuraportti.")


def main(argv=None):
    a = sys.argv[1:] if argv is None else list(argv)
    try:
        if len(a) in (2, 3) and a[0] == "ajo" and (len(a) == 2 or a[2] == "--halyta"):
            teksti, _ = ajo(a[1], halyta=_telegram_halytys if len(a) == 3 else None)
            print(teksti)
            return 0
        if len(a) == 2 and a[0] == "snapshot-siivous":
            try:
                with open(a[1], encoding="utf-8") as f:
                    snapshotit = jasenna_snapshotit(f.read())
            except OSError as e:
                raise TalonmiesVirhe("tiedostoa ei voi lukea (%s)" % type(e).__name__)
            print(snapshot_siivousteksti(ehdota_snapshot_siivous(snapshotit), snapshotit))
            return 0
        if len(a) >= 4 and a[0] == "paata":
            nimi = paata(a[1], a[2], a[3], " ".join(a[4:]))
            print("kirjattu: %s %s" % (nimi, a[3]))
            return 0
        if len(a) >= 2 and a[0] == "mittari":
            raja, min_ = None, 10
            lisat = a[2:]
            while lisat:
                if lisat[0] == "--raja" and len(lisat) > 1:
                    raja = float(lisat[1])
                elif lisat[0] == "--min" and len(lisat) > 1:
                    min_ = int(lisat[1])
                else:
                    raise TalonmiesVirhe("tuntematon valitsin")
                lisat = lisat[2:]
            print(json.dumps(mittari(a[1], raja, min_), indent=2, sort_keys=True))
            return 0
    except (TalonmiesVirhe, ValueError) as e:
        print("VIRHE: %s" % (e if isinstance(e, TalonmiesVirhe) else type(e).__name__))
        return 1
    print("k\u00e4ytt\u00f6: python3 -m agentti.talonmies ajo <tila> [--halyta] | snapshot-siivous <tiedosto> | paata <tila> <tunniste> kylla|ei [huomio] | mittari <tila>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
