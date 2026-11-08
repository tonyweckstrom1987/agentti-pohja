"""Tyopaikkaseuranta: hakee avoimia ilmoituksia julkisista, kirjautumattomista lahteista, suodattaa hakusanoilla,
poistaa jo nahdyt ja ilmoittaa uudet Telegramiin olemassa olevan ilmoita-polun kautta (ei uusia tunnuksia).

Lahteet (vain luvalliset): Greenhouse boards-api, Lever postings-api, Ashby posting-api (yritysten julkiset
urasivu-rajapinnat) seka yleinen RSS/Atom-feed. EI LinkedIn-scrapingia; Duunitori ja Tyomarkkinatorin /api/ kieltavat
automaattihaun robots.txt:ssa, joten niita ei kayteta. Katso docs/tyopaikat.md.

Kaytto:
    python3 -m agentti.tyopaikat --asetukset tyopaikat.json --tila /var/lib/agentti/tyopaikat.json [--laheta] [--kuiva]
Ilman --laheta tulostetaan vain osumat. Vain stdlib. Virheista tulostetaan vain tyypin nimi."""
import argparse
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

from . import ilmoita

OLETUS_HAKUSANAT = ["llmops", "mlops", "devops", "platform", "backend", "python", "healthtech", "terveysteknologia",
                    "harjoittelu", "harjoittelupaikka", "tyossaoppiminen", "intern", "internship", "trainee", "sre", "site reliability"]
AIKAKATKAISU = 20
VASTAUS_MAX = 8_000_000
MAX_VIESTISSA = 8
MAX_AJOSSA = 40
NAYTETYT_MAX = 5000
UA = "agentti-tyopaikat/1 (henkilokohtainen seuranta; ei massahakua)"
LAHTEET = ("greenhouse", "lever", "ashby", "rss")
SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def laskosta(teksti):
    """Pienet kirjaimet ja skandit ASCII:ksi, jotta hakusana 'tyossaoppiminen' tasmaa myos skandillisiin ilmoituksiin."""
    t = str(teksti).lower()
    for a, b in (("\u00e4", "a"), ("\u00f6", "o"), ("\u00e5", "a")):
        t = t.replace(a, b)
    return t


def _sana_re(sana):
    """Hakusana kokonaisena sanana (sanaraja kuten \\b, mutta toimii myos 'c++'-tyyppisilla hakusanoilla) ja re.escape:lla:
    'intern' ei tasmaa sanaan 'international'."""
    return re.compile(r"(?<!\w)" + re.escape(laskosta(sana)) + r"(?!\w)")


# Euroopan ulkopuoliset alueet: remote + jokin nama -> hylataan (Tonin paatos 1.11.2026).
EI_EUROOPPA = ("united states", "usa", "us", "u.s.", "u.s.a.", "north america", "america", "americas", "latam",
               "latin america", "canada", "mexico", "brazil", "apac", "asia", "india", "australia", "new zealand",
               "japan", "singapore", "china", "israel")
EI_EUROOPPA_RE = [re.compile(r"(?<![\w.])" + re.escape(a) + r"(?![\w])") for a in EI_EUROOPPA]


def _remote_ei_euroopassa(sij):
    """Sijainti (laskostettu) sisaltaa remote-sanan JA Euroopan ulkopuolisen alueen."""
    return bool(_sana_re("remote").search(sij)) and any(r.search(sij) for r in EI_EUROOPPA_RE)


def sopii(ilmoitus, hakusanat, paikat=()):
    """Otsikko/tiimi tasmaa vahintaan yhteen hakusanaan (kokonainen sana); jos paikat annettu, myos sijainti tasmaa
    yhteen. Sijainti, jossa on remote mutta myos Euroopan ulkopuolinen alue (esim. 'Remote - US'), hylataan."""
    teksti = laskosta(ilmoitus.get("otsikko", "") + " " + ilmoitus.get("tiimi", ""))
    if not any(_sana_re(h).search(teksti) for h in hakusanat):
        return False
    if paikat:
        sij = laskosta(ilmoitus.get("sijainti", ""))
        if _remote_ei_euroopassa(sij):
            return False
        return any(_sana_re(p).search(sij) for p in paikat)
    return True


# Korkeakouluvaatimus (Tonin paatos 8.11.2026, A904): Tony opiskelee ammatillista perustutkintoa, joten kohteet, joissa
# vaaditaan korkeakouluopiskelua, merkitaan "ei sovi" eika niita nosteta. Lauseet haetaan otsikosta, tiimista ja
# mahdollisesta kuvauskentasta (lukijat eivat nyt tayta kuvausta; silloin tarkin keino on asetusten ei_sovi-lista).
KORKEAKOULULAUSEET = ("bachelor student", "bachelors student", "master student", "masters student",
                      "university student", "korkeakouluopiskelija", "korkeakouluopiskelijat",
                      "ammattikorkeakouluopiskelija", "yliopisto-opiskelija", "yliopisto-opiskelijat")


def _vaatimusteksti(teksti):
    t = laskosta(teksti)
    for a in ("'", "\u2019", "\u2018"):
        t = t.replace(a, "")
    return t


def ei_sovi_syy(ilmoitus, ei_sovi=()):
    """Palauttaa syyn (str), jos ilmoitus ei sovi korkeakouluvaatimuksen takia, muuten None.
    ei_sovi: asetusten lista {'yritys': ..., 'otsikko': ..., 'syy': ...}; yritys ja otsikko ovat osamerkkijonoja,
    joiden molempien pitaa tasmata (puuttuva kentta tasmaa aina; saanto ilman kumpaakaan ohitetaan)."""
    for r in ei_sovi or ():
        if not isinstance(r, dict) or not (r.get("yritys") or r.get("otsikko")):
            continue
        if laskosta(r.get("yritys", "")) in laskosta(ilmoitus.get("yritys", "")) and \
                laskosta(r.get("otsikko", "")) in laskosta(ilmoitus.get("otsikko", "")):
            return str(r.get("syy") or "ei sovi")[:120]
    teksti = _vaatimusteksti(" ".join(str(ilmoitus.get(k, "")) for k in ("otsikko", "tiimi", "kuvaus")))
    if any(_sana_re(l).search(teksti) for l in KORKEAKOULULAUSEET):
        return "ei sovi: korkeakouluvaatimus"
    return None


def hae_json(osoite, avaa=None):
    avaa = avaa or _avaa
    return json.loads(avaa(osoite).decode("utf-8"))


def _avaa(osoite):
    if not osoite.startswith("https://"):
        raise ValueError("vain https")
    pyynto = urllib.request.Request(osoite, headers={"User-Agent": UA, "Accept": "application/json, application/xml"})
    with urllib.request.urlopen(pyynto, timeout=AIKAKATKAISU) as v:
        return v.read(VASTAUS_MAX)


def _ilm(lahde, nimi, tunnus, otsikko, osoite, tiimi="", sijainti=""):
    return {"id": "%s:%s:%s" % (lahde, nimi, tunnus), "yritys": nimi, "otsikko": str(otsikko or "").strip(),
            "osoite": str(osoite or ""), "tiimi": str(tiimi or ""), "sijainti": str(sijainti or "")}


def lue_greenhouse(nimi, data):
    return [_ilm("gh", nimi, j.get("id"), j.get("title"), j.get("absolute_url"), "",
                 (j.get("location") or {}).get("name", "")) for j in data.get("jobs", [])]


def lue_lever(nimi, data):
    return [_ilm("lv", nimi, j.get("id"), j.get("text"), j.get("hostedUrl"),
                 (j.get("categories") or {}).get("team", ""), (j.get("categories") or {}).get("location", ""))
            for j in data]


def lue_ashby(nimi, data):
    return [_ilm("ab", nimi, j.get("id"), j.get("title"), j.get("jobUrl"),
                 "%s %s" % (j.get("department", ""), j.get("team", "")), j.get("location", ""))
            for j in data.get("jobs", []) if j.get("isListed", True)]


def lue_rss(nimi, teksti):
    """RSS 2.0 (item) ja Atom (entry). DTD/entiteetteja ei kasitella: <!DOCTYPE hylataan."""
    if "<!DOCTYPE" in teksti.upper() or "<!ENTITY" in teksti.upper():
        raise ValueError("DOCTYPE ei sallittu")
    juuri = ET.fromstring(teksti)
    tulos = []
    for el in juuri.iter():
        nimi_el = el.tag.rsplit("}", 1)[-1]
        if nimi_el not in ("item", "entry"):
            continue
        kentat = {k.tag.rsplit("}", 1)[-1]: k for k in el}
        otsikko = (kentat["title"].text if "title" in kentat else "") or ""
        linkki = ""
        if "link" in kentat:
            linkki = kentat["link"].get("href") or (kentat["link"].text or "")
        tunnus = (kentat["guid"].text if "guid" in kentat else None) or (kentat["id"].text if "id" in kentat else None) or linkki
        if tunnus:
            tulos.append(_ilm("rss", nimi, tunnus, otsikko, linkki))
    return tulos


def hae_lahde(l, avaa=None):
    """l: {'tyyppi','nimi','tunnus'|'osoite'}. Nostaa poikkeuksen virheesta (kutsuja kasittelee)."""
    tyyppi, nimi = l.get("tyyppi"), str(l.get("nimi") or l.get("tunnus") or "?")
    avaa = avaa or _avaa
    if tyyppi == "rss":
        osoite = l.get("osoite", "")
        return lue_rss(nimi, avaa(osoite).decode("utf-8", "replace"))
    tunnus = str(l.get("tunnus", ""))
    if not SLUG_RE.match(tunnus):
        raise ValueError("tunnus")
    if tyyppi == "greenhouse":
        return lue_greenhouse(nimi, hae_json("https://boards-api.greenhouse.io/v1/boards/%s/jobs" % tunnus, avaa))
    if tyyppi == "lever":
        return lue_lever(nimi, hae_json("https://api.lever.co/v0/postings/%s?mode=json" % tunnus, avaa))
    if tyyppi == "ashby":
        return lue_ashby(nimi, hae_json("https://api.ashbyhq.com/posting-api/job-board/%s" % tunnus, avaa))
    raise ValueError("tuntematon tyyppi")


def lataa_nahdyt(polku):
    try:
        with open(polku, encoding="utf-8") as f:
            d = json.load(f)
        return [x for x in d if isinstance(x, str)]
    except (OSError, ValueError):
        return []


def tallenna_nahdyt(polku, jarjestys):
    with open(polku, "w", encoding="utf-8") as f:
        json.dump(jarjestys[-NAYTETYT_MAX:], f)


def muodosta_viestit(uudet):
    """Jakaa osumat eriin (ilmoita rajaa tekstin 1200 merkkiin; pidetaan alle). Palauttaa [(teksti, [idt])]."""
    erat, rivit, idt = [], [], []
    for u in uudet:
        rivi = "%s: %s\n%s" % (u["yritys"], u["otsikko"][:90], u["osoite"][:200])
        if rivit and (len(rivit) >= MAX_VIESTISSA or sum(len(r) + 2 for r in rivit) + len(rivi) > 1000):
            erat.append(("\n\n".join(rivit), idt))
            rivit, idt = [], []
        rivit.append(rivi)
        idt.append(u["id"])
    if rivit:
        erat.append(("\n\n".join(rivit), idt))
    return erat



def aja(asetukset, tila_polku, laheta=False, avaa=None, laheta_fn=None, ilmoita_args=None, hylatyt=None):
    """Palauttaa (osumat, virheet). Lahetetyt kirjataan tilaan era kerrallaan vasta onnistuneen lahetyksen jalkeen;
    epaonnistunut era yritetaan uudelleen seuraavalla ajolla. Ilman laheta ei kirjata mitaan.
    Yhdella ajolla lahetetaan enintaan MAX_AJOSSA; loput tulevat seuraavalla ajolla.
    Korkeakouluvaatimuksen takia hylatyt (ei_sovi_syy) eivat mene osumiin; ne lisataan listaan hylatyt (jos annettu)."""
    hakusanat = asetukset.get("hakusanat") or OLETUS_HAKUSANAT
    paikat = asetukset.get("paikat") or []
    ei_sovi = asetukset.get("ei_sovi") or []
    nahdyt = lataa_nahdyt(tila_polku)
    nahdyt_joukko = set(nahdyt)
    uudet, virheet, kasitelty = [], [], set()
    for l in asetukset.get("lahteet", []):
        try:
            ilmoitukset = hae_lahde(l, avaa)
        except Exception as e:  # vain tyyppi talteen: viesti voi sisaltaa osoitteen
            virheet.append("%s: %s" % (l.get("nimi") or l.get("tunnus") or "?", type(e).__name__))
            continue
        for i in ilmoitukset:
            if i["id"] in nahdyt_joukko or i["id"] in kasitelty or not i["otsikko"]:
                continue
            kasitelty.add(i["id"])
            if sopii(i, hakusanat, paikat):
                syy = ei_sovi_syy(i, ei_sovi)
                if syy:
                    if hylatyt is not None:
                        hylatyt.append((i, syy))
                    continue
                uudet.append(i)
    # ei-osuvia ei tarvitse kirjata: jos hakusanat muuttuvat, aiemmin hylatyt arvioidaan uudelleen
    osumat, jaljelle = uudet[:MAX_AJOSSA], len(uudet) - len(uudet[:MAX_AJOSSA])
    if not laheta:
        return osumat, virheet
    laheta_fn = laheta_fn or ilmoita.ilmoita
    erat = muodosta_viestit(osumat)
    for n, (teksti, idt) in enumerate(erat):
        if jaljelle and n == len(erat) - 1:
            teksti += "\n\n(+%d lisaa seuraavalla ajolla)" % jaljelle
        tulos = laheta_fn("valmis", "Uusia tyopaikkoja (%d/%d)" % (n + 1, len(erat)), teksti, **(ilmoita_args or {}))
        if tulos != "lahetetty":
            virheet.append("telegram: %s" % tulos)
            break
        nahdyt.extend(idt)
        tallenna_nahdyt(tila_polku, nahdyt)
    return osumat, virheet


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentti.tyopaikat")
    ap.add_argument("--asetukset", required=True)
    ap.add_argument("--tila", required=True)
    ap.add_argument("--laheta", action="store_true", help="laheta Telegramiin ja kirjaa nahdyiksi")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        with open(a.asetukset, encoding="utf-8") as f:
            asetukset = json.load(f)
    except (OSError, ValueError) as e:
        print("asetukset: %s" % type(e).__name__, file=sys.stderr)
        return 2
    hylatyt = []
    osumat, virheet = aja(asetukset, a.tila, laheta=a.laheta, hylatyt=hylatyt)
    for i in osumat:
        print("%s | %s | %s" % (i["yritys"], i["otsikko"], i["osoite"]))
    for i, syy in hylatyt:
        print("EI SOVI | %s | %s | %s" % (i["yritys"], i["otsikko"], syy))
    for v in virheet:
        print("virhe: %s" % v, file=sys.stderr)
    print("osumia: %d, ei sovi: %d, virheita: %d" % (len(osumat), len(hylatyt), len(virheet)))
    return 1 if virheet and not osumat else 0


if __name__ == "__main__":
    sys.exit(main())
