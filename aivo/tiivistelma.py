"""Aivon keskustelumuisti, osa 2: tiivistelman tietomalli, tiukka jasennys ja CLI (suunnitelma kohdat 2.2, 5.1, 5.3).

Tama moduuli EI kutsu mallia eika verkkoa (vain stdlib; AST-testi valvoo). Se vain
  * maarittelee tiivistelman JSON-muodon (Tonyn lainaukset erillaan mallin tulkinnasta),
  * jasentaa ja tarkistaa mallin antaman JSON:n tiukasti (tuntematon kentta, vaara tyyppi tai pituus => hylatty),
  * tarkistaa, etta jokainen lainaus loytyy sanasta sanaan lahdekeskustelun Tonyn viesteista,
  * tallentaa tiivistelmat (kansio 0700, tiedostot 0600) ja antaa Tonille komennot lista/nayta/poista/korjaa.

Tiivistelma on DATAA, ei ohjeita: mikaan tassa ei suorita tai tulkitse sisaltoa kaskyna.
Lahdekeskustelu on aivo/chat.py:n jsonl (rivi: {"aika","rooli","teksti","lahteet"}; rooli "tony", "aivo" tai
"jarjestelma" = palvelimen merkinta yksityisessa tilassa, ei koskaan Tonyn sanoja).

Kaytto: python3 -m aivo.tiivistelma --kansio KANSIO lista | nayta TUNNUS | poista TUNNUS | korjaa TUNNUS KOHTA TEKSTI
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

VERSIO = 1
TYYPIT = ("ohje", "mieltymys", "avoin", "fakta")
TILAT = ("ehdotus", "voimassa", "vanhentunut", "tulkinta-vain")
MAX_LAINAUS = 500
MAX_TULKINTA = 300
MAX_KOHDAT = 20
MAX_LAINAUKSIA = 10          # lainauksia per kohta
MAX_VIESTEJA = 50            # viesti-indeksia per kohta
MAX_KORJAUS = 500
MAX_TIEDOSTO = 200_000       # tavua; isompaa tiivistelmaa ei lueta
RAJA = "=====AIVO-MUISTI-RAJA====="
KID_RE = r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}"
TUNNUS_RE = re.compile(r"^(%s)-([0-9]{1,6})-([0-9]{1,6})$" % KID_RE)
KID_TARKISTUS = re.compile(r"^%s$" % KID_RE)
POISTETUT = "poistetut.jsonl"
MERKINTA = "Aivon tulkinta, ei Tonyn sanoja"

YLAKENTAT = ("versio", "keskustelu", "alku", "loppu", "ensimmainen_aika", "viimeinen_aika", "tiivistetty", "malli",
             "alue_sha", "kohdat")
KOHTAKENTAT_PAKOLLISET = ("id", "tyyppi", "tony_sanoi", "tulkinta", "viestit", "tila")
# Vain korjaa() kirjoittaa nama kentat. Mallin syote ei saa sisaltaa niita (jasenna_kohta hylkaa ne tuntemattomina).
TONYN_KENTAT = ("tony_korjaus", "tulkinta_korvattu", "alkuperainen_tulkinta")


class TiivistelmaVirhe(Exception):
    """Odotettu virhe; viesti on aina turvallinen tulostaa (ei sisalla tiivistelman sisaltoa)."""


def _nyt():
    return datetime.now(timezone.utc)


# --- Puhdistus ---------------------------------------------------------------------------------------

def _ohjausmerkki(c):
    return c not in "\n\t" and unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cs", "Co")


def puhdista_kehotteeseen(teksti, raja=None):
    """Poistaa NUL- ja ohjausmerkit, muuttaa rivinvaihdot ja sarkaimet valilyonneiksi seka poistaa RAJA-merkkijonon
    ja lohkomerkit [[ ]] toistaen, kunnes tulos ei enaa muutu (poisto voi muuten koota uuden RAJA-merkkijonon)."""
    t = "".join(" " if c in "\n\t" else c for c in str(teksti) if c in "\n\t" or not _ohjausmerkki(c))
    edellinen = None
    while t != edellinen:
        edellinen = t
        t = t.replace(RAJA, "").replace("[[", "").replace("]]", "")
    t = t.strip()
    return t[:raja] if raja else t


def puhdista_naytolle(teksti):
    """Ohjaus- ja muotoilumerkit (myos rivinvaihto ja sarkain) nakyviksi \\xNN/\\uNNNN-merkinnoiksi, jottei
    terminaalissa voi piilottaa sisaltoa eika vaarentaa uutta rivia (esim. 'Tony sanoi: ...')."""
    return "".join(("\\x%02x" % ord(c) if ord(c) < 256 else "\\u%04x" % ord(c)) if (_ohjausmerkki(c) or c in "\n\t") else c
                   for c in teksti)


# --- Lahdekeskustelu ---------------------------------------------------------------------------------

def lue_keskustelu(polku):
    """Lukee aivo/chat.py:n jsonl:n; palauttaa [{'rooli', 'teksti'}, ...]. Rikkinainen rivi => TiivistelmaVirhe."""
    try:
        rivit = Path(polku).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise TiivistelmaVirhe("keskustelua ei voi lukea (%s)" % type(e).__name__)
    viestit = []
    for r in rivit:
        if not r.strip():
            continue
        try:
            d = json.loads(r)
        except ValueError:
            raise TiivistelmaVirhe("keskustelun rivi ei ole JSON:ia")
        if (not isinstance(d, dict) or d.get("rooli") not in ("tony", "aivo", "jarjestelma") or not isinstance(d.get("teksti"), str)
                or not isinstance(d.get("aika"), str)):
            raise TiivistelmaVirhe("keskustelun rivin muoto on virheellinen")
        viestit.append({"rooli": d["rooli"], "teksti": d["teksti"], "aika": d["aika"]})
    return viestit


def alueen_sha(viestit, alku, loppu):
    """SHA-256 viestialueesta [alku, loppu] (rooli ja teksti); sama alue => sama tiiviste."""
    h = hashlib.sha256()
    for v in viestit[alku:loppu + 1]:
        h.update(json.dumps([v["rooli"], v["teksti"]], ensure_ascii=False).encode("utf-8") + b"\n")
    return h.hexdigest()


# --- Tiukka jasennys ---------------------------------------------------------------------------------

def _on_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _tarkista_kentat(d, pakolliset, valinnaiset, nimi):
    if not isinstance(d, dict):
        raise TiivistelmaVirhe("%s: pitaa olla objekti" % nimi)
    tuntematon = set(d) - set(pakolliset) - set(valinnaiset)
    if tuntematon:
        raise TiivistelmaVirhe("%s: tuntematon kentta (%d kpl)" % (nimi, len(tuntematon)))
    puuttuu = [k for k in pakolliset if k not in d]
    if puuttuu:
        raise TiivistelmaVirhe("%s: pakollinen kentta puuttuu: %s" % (nimi, ", ".join(puuttuu)))


def _teksti(x, nimi, raja, tyhja_ok=False):
    if not isinstance(x, str):
        raise TiivistelmaVirhe("%s: pitaa olla merkkijono" % nimi)
    if not tyhja_ok and not x.strip():
        raise TiivistelmaVirhe("%s: ei saa olla tyhja" % nimi)
    if len(x) > raja:
        raise TiivistelmaVirhe("%s: liian pitka (enintaan %d merkkia)" % (nimi, raja))
    return x


def _lainaus_ok(lainaus, viestit, indeksit):
    """Lainaus on osamerkkijono jonkin viitatun TONYN viestin tekstista (Aivon viesti ei koskaan kelpaa lahteeksi)."""
    return any(viestit[i]["rooli"] == "tony" and lainaus in viestit[i]["teksti"] for i in indeksit)


def jasenna_kohta(d, viestit, nimi="kohta"):
    """Tarkistaa yhden kohdan. Palauttaa puhdistetun kopion; virheessa TiivistelmaVirhe."""
    _tarkista_kentat(d, KOHTAKENTAT_PAKOLLISET, (), nimi)
    if not _on_int(d["id"]) or d["id"] < 1:
        raise TiivistelmaVirhe("%s: id pitaa olla positiivinen kokonaisluku" % nimi)
    if d["tyyppi"] not in TYYPIT:
        raise TiivistelmaVirhe("%s: tuntematon tyyppi" % nimi)
    if d["tila"] not in TILAT:
        raise TiivistelmaVirhe("%s: tuntematon tila" % nimi)
    ind = d["viestit"]
    if (not isinstance(ind, list) or not ind or len(ind) > MAX_VIESTEJA or not all(_on_int(i) for i in ind)
            or any(not 0 <= i < len(viestit) for i in ind)):
        raise TiivistelmaVirhe("%s: viestit-indeksit ovat virheellisia" % nimi)
    lain = d["tony_sanoi"]
    if not isinstance(lain, list) or len(lain) > MAX_LAINAUKSIA:
        raise TiivistelmaVirhe("%s: tony_sanoi pitaa olla enintaan %d lainauksen lista" % (nimi, MAX_LAINAUKSIA))
    for q in lain:
        _teksti(q, nimi + ".tony_sanoi", MAX_LAINAUS)
        if any(_ohjausmerkki(c) for c in q) or RAJA in q:
            raise TiivistelmaVirhe("%s: lainaus sisaltaa kiellettyja merkkeja" % nimi)
        if not _lainaus_ok(q, viestit, ind):
            raise TiivistelmaVirhe("%s: lainaus ei loydy sanasta sanaan Tonyn viestista" % nimi)
    if bool(lain) == (d["tila"] == "tulkinta-vain"):
        raise TiivistelmaVirhe("%s: tila tulkinta-vain kuuluu tasan kohdalle, jolla ei ole lainausta" % nimi)
    ulos = {"id": d["id"], "tyyppi": d["tyyppi"], "tony_sanoi": list(lain),
            "tulkinta": puhdista_kehotteeseen(_teksti(d["tulkinta"], nimi + ".tulkinta", MAX_TULKINTA), MAX_TULKINTA),
            "viestit": list(ind), "tila": d["tila"]}
    if not ulos["tulkinta"]:
        raise TiivistelmaVirhe("%s: tulkinta jai tyhjaksi puhdistuksessa" % nimi)
    return ulos


def jasenna(raaka, viestit):
    """Jasentaa tiivistelman (JSON-teksti tai dict) ja tarkistaa sen lahdeviesteja vasten. Palauttaa puhdistetun dictin."""
    if isinstance(raaka, (str, bytes)):
        if len(raaka) > MAX_TIEDOSTO:
            raise TiivistelmaVirhe("tiivistelma on liian suuri")
        try:
            d = json.loads(raaka)
        except ValueError:
            raise TiivistelmaVirhe("tiivistelma ei ole JSON:ia")
    else:
        d = raaka
    _tarkista_kentat(d, YLAKENTAT, (), "tiivistelma")
    if d["versio"] != VERSIO or not _on_int(d["versio"]):
        raise TiivistelmaVirhe("tuntematon versio")
    if not isinstance(d["keskustelu"], str) or not KID_TARKISTUS.fullmatch(d["keskustelu"]):
        raise TiivistelmaVirhe("keskustelun tunniste on virheellinen")
    a, b = d["alku"], d["loppu"]
    if not (_on_int(a) and _on_int(b)) or not 0 <= a <= b < len(viestit):
        raise TiivistelmaVirhe("viestialue on virheellinen")
    for k in ("ensimmainen_aika", "viimeinen_aika", "tiivistetty"):
        if any(_ohjausmerkki(c) or c in "\n\t" for c in _teksti(d[k], k, 40)):
            raise TiivistelmaVirhe("%s sisaltaa kiellettyja merkkeja" % k)
    _teksti(d["malli"], "malli", 100)
    if any(_ohjausmerkki(c) for c in d["malli"]):
        raise TiivistelmaVirhe("malli sisaltaa kiellettyja merkkeja")
    if d["alue_sha"] != alueen_sha(viestit, a, b):
        raise TiivistelmaVirhe("viestialueen sha ei tasmaa lahdekeskusteluun")
    kohdat = d["kohdat"]
    if not isinstance(kohdat, list) or len(kohdat) > MAX_KOHDAT:
        raise TiivistelmaVirhe("kohtia saa olla enintaan %d" % MAX_KOHDAT)
    ulos_kohdat = [jasenna_kohta(k, viestit, "kohta %d" % (n + 1)) for n, k in enumerate(kohdat)]
    if len({k["id"] for k in ulos_kohdat}) != len(ulos_kohdat):
        raise TiivistelmaVirhe("kohtien id:t eivat ole yksilollisia")
    for k in ulos_kohdat:
        if any(not a <= i <= b for i in k["viestit"]):
            raise TiivistelmaVirhe("kohta viittaa viestiin tiivistetyn alueen ulkopuolelta")
    ulos = {k: d[k] for k in YLAKENTAT if k != "kohdat"}
    ulos["kohdat"] = ulos_kohdat
    return ulos


# --- Tallennus ---------------------------------------------------------------------------------------

def tunnus_tiivistelmalle(t):
    return "%s-%d-%d" % (t["keskustelu"], t["alku"], t["loppu"])


def _kansio(kansio, luo=False):
    p = Path(kansio)
    if luo:
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
    if p.is_symlink() or not p.is_dir():
        raise TiivistelmaVirhe("kansio puuttuu tai on linkki")
    return p


def _polku(kansio, tunnus):
    if not isinstance(tunnus, str) or not TUNNUS_RE.fullmatch(tunnus):
        raise TiivistelmaVirhe("virheellinen tiivistelman tunnus")
    return Path(kansio) / (tunnus + ".json")


def _kirjoita_600(polku, teksti):
    fd, tmp = tempfile.mkstemp(dir=polku.parent, prefix=".tmp.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(teksti)
        os.replace(tmp, polku)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _poistettu(kansio, tunnus):
    p = Path(kansio) / POISTETUT
    if p.is_symlink():
        raise TiivistelmaVirhe("poistetut.jsonl on linkki")
    try:
        rivit = p.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False                              # ei lokia => mitaan ei ole poistettu
    except (OSError, ValueError) as e:            # turvallinen epaonnistuminen: ei tiedeta, onko poistettu => esto
        raise TiivistelmaVirhe("poistettujen lokia ei voi lukea (%s)" % type(e).__name__)
    try:
        return any(json.loads(r)["tunnus"] == tunnus for r in rivit if r.strip())
    except (ValueError, KeyError, TypeError):
        raise TiivistelmaVirhe("poistettujen loki on rikkinainen")


def tallenna(kansio, raaka, viestit):
    """Jasentaa ja tallentaa tiivistelman; palauttaa tunnuksen. Vaara tai poistettu tiivistelma hylataan."""
    t = jasenna(raaka, viestit)
    tunnus = tunnus_tiivistelmalle(t)
    p = _kansio(kansio, luo=True)
    if _poistettu(p, tunnus):
        raise TiivistelmaVirhe("tama viestialue on poistettu eika sita luoda uudelleen")
    polku = _polku(p, tunnus)
    if os.path.lexists(polku):                    # uusintatiivistys ei saa hukata Tonyn korjauksia hiljaa
        try:
            vanha = lue(p, tunnus)
        except TiivistelmaVirhe:
            raise TiivistelmaVirhe("olemassa olevaa tiivistelmaa ei voi lukea, ei ylikirjoiteta")
        if any(isinstance(k, dict) and any(f in k for f in TONYN_KENTAT) for k in vanha["kohdat"]):
            raise TiivistelmaVirhe("tiivistelmassa on Tonyn korjauksia, ei ylikirjoiteta")
    _kirjoita_600(polku, json.dumps(t, ensure_ascii=False, indent=1) + "\n")
    return tunnus


def lue(kansio, tunnus):
    """Lukee tallennetun tiivistelman ja tarkistaa muodon (ilman lahdekeskustelua: lainauksia ei voi todentaa)."""
    p = _polku(_kansio(kansio), tunnus)
    try:
        if p.is_symlink() or p.stat().st_size > MAX_TIEDOSTO:
            raise TiivistelmaVirhe("tiedosto on linkki tai liian suuri")
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TiivistelmaVirhe("tiivistelmaa ei ole")
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise TiivistelmaVirhe("tiivistelmaa ei voi lukea (%s)" % type(e).__name__)
    _tarkista_kentat(d, YLAKENTAT, (), "tiivistelma")
    if not isinstance(d["kohdat"], list):
        raise TiivistelmaVirhe("kohdat puuttuu")
    return d


def luettele(kansio):
    if not os.path.lexists(kansio):
        return []
    try:
        nimet = sorted(f.name[:-5] for f in _kansio(kansio).iterdir() if f.name.endswith(".json"))
    except OSError as e:
        raise TiivistelmaVirhe("kansiota ei voi lukea (%s)" % type(e).__name__)
    return [n for n in nimet if TUNNUS_RE.fullmatch(n)]


def katettu_loppu(kansio, kid):
    """Suurin viestin indeksi, jonka tiivistelma on keskustelulle olemassa TAI jonka Tony on poistanut (-1: ei mitaan).
    Poistettua aluetta ei tiivisteta uudelleen. Lukuvirhe => TiivistelmaVirhe (turvallinen epaonnistuminen)."""
    if not isinstance(kid, str) or not KID_TARKISTUS.fullmatch(kid):
        raise TiivistelmaVirhe("keskustelun tunniste on virheellinen")
    suurin = -1
    for n in luettele(kansio):
        m = TUNNUS_RE.fullmatch(n)
        if m and m.group(1) == kid:
            suurin = max(suurin, int(m.group(3)))
    loki = Path(kansio) / POISTETUT
    if loki.is_symlink():
        raise TiivistelmaVirhe("poistetut.jsonl on linkki")
    try:
        rivit = loki.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return suurin
    except (OSError, ValueError) as e:
        raise TiivistelmaVirhe("poistettujen lokia ei voi lukea (%s)" % type(e).__name__)
    for r in rivit:
        if not r.strip():
            continue
        try:
            d = json.loads(r)
            if "keskustelu" in d and "loppu" in d:
                rkid, rloppu = d["keskustelu"], int(d["loppu"])
            else:                                 # vanha tai kasin kirjoitettu rivi: johdetaan tunnuksesta
                m = TUNNUS_RE.fullmatch(d["tunnus"])
                rkid, rloppu = m.group(1), int(m.group(3))
            if rkid == kid:
                suurin = max(suurin, rloppu)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise TiivistelmaVirhe("poistettujen loki on rikkinainen")
    return suurin


def poista(kansio, tunnus):
    """Poistaa tiivistelman ja jattaa merkinnan ilman sisaltoa (tunnus, keskustelu, alku, loppu, sha, aika), ettei sama
    kohta synny uudelleen. katettu_loppu lukee keskustelun ja lopun tasta rivista (tai tunnuksesta, jos kentat puuttuvat)."""
    t = lue(kansio, tunnus)
    p = _polku(kansio, tunnus)
    rivi = json.dumps({"tunnus": tunnus, "keskustelu": t["keskustelu"], "alku": t["alku"], "loppu": t["loppu"],
                       "alue_sha": t["alue_sha"], "poistettu": _nyt().isoformat(timespec="seconds")})
    loki = Path(kansio) / POISTETUT
    if loki.is_symlink():
        raise TiivistelmaVirhe("poistetut.jsonl on linkki")
    vanha = loki.read_text(encoding="utf-8") if loki.exists() else ""
    _kirjoita_600(loki, vanha + rivi + "\n")
    p.unlink()


def korjaa(kansio, tunnus, kohta_id, teksti):
    """Tonyn korjaus: tallentuu kenttaan tony_korjaus, alkuperainen tulkinta sailyy merkittyna korvatuksi."""
    t = lue(kansio, tunnus)
    teksti = puhdista_kehotteeseen(_teksti(teksti, "korjaus", MAX_KORJAUS), MAX_KORJAUS)
    if not teksti:
        raise TiivistelmaVirhe("korjaus jai tyhjaksi puhdistuksessa")
    for k in t["kohdat"]:
        if isinstance(k, dict) and k.get("id") == kohta_id:
            k.setdefault("alkuperainen_tulkinta", k.get("tulkinta"))
            k["tony_korjaus"] = teksti
            k["tulkinta_korvattu"] = True
            if k.get("tila") == "ehdotus":            # Tonyn oma korjaus vahvistaa kohdan
                k["tila"] = "voimassa"
            _kirjoita_600(_polku(kansio, tunnus), json.dumps(t, ensure_ascii=False, indent=1) + "\n")
            return
    raise TiivistelmaVirhe("kohtaa ei ole")


# --- CLI (ei mallikutsua) ----------------------------------------------------------------------------

def muotoile(t):
    n = lambda x: puhdista_naytolle(str(x))      # kaikki tiedostosta luettu arvo kulkee puhdistuksen kautta
    rivit = ["%s  %s  viestit %s-%s  malli: %s" % (n(t["keskustelu"]), n(t["tiivistetty"]), n(t["alku"]), n(t["loppu"]),
                                                    n(t["malli"]))]
    for k in t["kohdat"]:
        rivit.append("[%s] %s, tila: %s, viestit %s" % (n(k.get("id")), n(k.get("tyyppi")), n(k.get("tila")),
                                                         n(k.get("viestit"))))
        for q in k.get("tony_sanoi", []):
            rivit.append("  Tony sanoi: \"%s\"" % puhdista_naytolle(str(q)))
        if k.get("tony_korjaus"):
            rivit.append("  Tonyn korjaus: %s" % puhdista_naytolle(k["tony_korjaus"]))
            rivit.append("  (korvattu) %s: %s" % (MERKINTA, puhdista_naytolle(str(k.get("alkuperainen_tulkinta", "")))))
        else:
            rivit.append("  %s: %s" % (MERKINTA, puhdista_naytolle(str(k.get("tulkinta")))))
    return "\n".join(rivit)


def main(argv=None, syote=input, paate=None):
    ap = argparse.ArgumentParser(prog="python3 -m aivo.tiivistelma", description=__doc__.split("\n")[0])
    ap.add_argument("--kansio", required=True, help="tiivistelmakansio (valitaan tilan mukaan; ei oletusta)")
    s = ap.add_subparsers(dest="komento", required=True)
    s.add_parser("lista")
    for nimi in ("nayta", "poista"):
        s.add_parser(nimi).add_argument("tunnus")
    k = s.add_parser("korjaa")
    k.add_argument("tunnus")
    k.add_argument("kohta", type=int)
    k.add_argument("teksti")
    a = ap.parse_args(argv)
    try:
        if a.komento == "lista":
            for n in luettele(a.kansio):
                print(n)
        elif a.komento == "nayta":
            print(muotoile(lue(a.kansio, a.tunnus)))
        else:
            if not (sys.stdin.isatty() if paate is None else paate):
                raise TiivistelmaVirhe("muutos vaatii paatteen (Tony vahvistaa itse)")
            if a.komento == "poista":
                if syote("Kirjoita tarkalleen 'poista %s' vahvistaaksesi: " % a.tunnus) != "poista " + a.tunnus:
                    raise TiivistelmaVirhe("vahvistus ei tasmannyt, mitaan ei poistettu")
                poista(a.kansio, a.tunnus)
                print("poistettu:", a.tunnus)
            else:
                korjaa(a.kansio, a.tunnus, a.kohta, a.teksti)
                print("korjattu:", a.tunnus, "kohta", a.kohta)
        return 0
    except TiivistelmaVirhe as e:
        print("virhe:", e, file=sys.stderr)
        return 1
    except Exception as e:                      # viesti voi sisaltaa tiivistelman sisaltoa: vain tyyppi
        print("virhe:", type(e).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
