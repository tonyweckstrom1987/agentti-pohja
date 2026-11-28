"""Vuotovartijan osumaprosentti: vastasiko vartijan arvio Tonyn omaa ratkaisua luovutuksissa ja paketeissa.

Kirjaus (yksi rivi per ratkaisu) tiedostoon <luovutukset>/vartija_osumat.jsonl (0600, kansio 0700):
  {"aika", "lahde", "tunnus", ["pala"], "tapahtuma": "ratkaisu", "vartija": esta|kysy|sallittu,
   "ratkaisu": hyvaksytty|hylatty|pala_poistettu, ["muutettu": true]}
  Ei tekstia, ei sha:ta eika palojen sisaltoa. lahde: "luovutus" tai "paketti" (myohemmin esim. "haku" samaan
  tiedostoon). Lisaksi estetty hyvaksyntayritys kirjataan riviksi tapahtuma="esto" (ilman ratkaisua).

Aiempi esto ja myohempi ratkaisu (muutettu=true kirjataan aina, kun samasta kohteesta on aiempi esto):
  * hyvaksytty: vartijan arvioksi kirjataan esta (alkuperainen esto piti, Tony hyvaksyi vasta muokatun tekstin).
  * hylatty tai pala_poistettu: vartijan arvioksi kirjataan vartijan oma arvio muokatusta tekstista. Jos vartija
    paasti muokatun tekstin lapi ja Tony silti hylkasi sen, se on ohilyonti eika osuma.

Osuma:
  * esta + hylatty tai pala_poistettu = osuma; esta + hyvaksytty (vain aiemman eston perimana) = osuma;
  * sallittu + hyvaksytty = osuma; sallittu + hylatty tai pala_poistettu = ohi (myos muutettu=true);
  * kysy: ei lasketa osumaprosenttiin, maara naytetaan erikseen.

Eston perinta lukee vain tiedoston viimeisen ESTO_IKKUNA_TAVUT-osan (ei koko tiedostoa jokaisella ratkaisulla).

Kirjauksen virhe ei koskaan estaa hyvaksyntaa eika hylkaysta: stderriin vain virheen tyyppi.
Vertailuraja (vahintaan 90 % ja 100 laskettua paatosta) on vain tulosteessa naytettava arvo, ei kytkin.

Kaytto: python3 -m aivo.vartija_osumat [--luovutukset HAKEMISTO] [--json]"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

TIEDOSTO = "vartija_osumat.jsonl"
VARTIJA_ARVOT = ("esta", "kysy", "sallittu")
RATKAISUT = ("hyvaksytty", "hylatty", "pala_poistettu")
LAHTEET = ("luovutus", "paketti")
RAJA_PROSENTTI = 90
RAJA_PAATOKSET = 100
# Eston perinnassa luetaan vain tiedoston loppu (rivi noin 150-200 tavua -> noin 5000 viimeisinta tapahtumaa).
ESTO_IKKUNA_TAVUT = 1024 * 1024
_VARTIJAN_TULOS = {"esta": "esta", "kysy": "kysy", "turvallinen": "sallittu"}


def vartija_tuloksesta(tulos):
    """vartija.TULOS_* (esta/kysy/turvallinen) -> esta/kysy/sallittu. Tuntematon -> kysy."""
    return _VARTIJAN_TULOS.get(tulos, "kysy")


def vartija_sallitusta(sallittu):
    """Paketin palan vartijan sallittu-arvo: False -> esta, None -> kysy, True -> sallittu (identiteettivertailu)."""
    if sallittu is True:
        return "sallittu"
    if sallittu is None:
        return "kysy"
    return "esta"


def _nyt():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kirjoita(hakemisto, rivi):
    p = Path(hakemisto) / TIEDOSTO
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(rivi, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _virhe(e):
    try:
        sys.stderr.write("vartija_osumat: kirjaus epaonnistui (%s)\n" % type(e).__name__)
    except Exception:
        pass


def lue(hakemisto):
    """Kaikki kelvolliset rivit tiedostosta (vanhimmasta uusimpaan). Puuttuva tiedosto -> []."""
    p = Path(hakemisto) / TIEDOSTO
    if not p.is_file():
        return []
    ulos = []
    for r in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(r)
        except ValueError:
            continue
        if isinstance(d, dict):
            ulos.append(d)
    return ulos


def _lue_loppu(hakemisto, max_tavut):
    """Tiedoston viimeiset enintaan max_tavut tavua kelvollisina riveina. Katkennut ensimmainen rivi ohitetaan."""
    p = Path(hakemisto) / TIEDOSTO
    try:
        with open(p, "rb") as f:
            koko = f.seek(0, os.SEEK_END)
            alku = max(0, koko - max_tavut)
            f.seek(alku)
            data = f.read()
    except FileNotFoundError:
        return []
    if alku > 0:
        data = data.split(b"\n", 1)[1] if b"\n" in data else b""
    ulos = []
    for r in data.decode("utf-8", errors="replace").splitlines():
        try:
            d = json.loads(r)
        except ValueError:
            continue
        if isinstance(d, dict):
            ulos.append(d)
    return ulos


def _oli_esto(hakemisto, lahde, tunnus, pala, max_tavut=None):
    """Onko samasta kohteesta esto tiedoston viimeisessa ESTO_IKKUNA_TAVUT-osassa. Luku on rajattu, jotta jokainen
    ratkaisu ei lue koko kasvavaa tiedostoa; ikkunaa vanhempi esto ei enaa periydy (rivi kirjataan ilman muutettu)."""
    for d in _lue_loppu(hakemisto, ESTO_IKKUNA_TAVUT if max_tavut is None else max_tavut):
        if d.get("tapahtuma") == "esto" and d.get("lahde") == lahde and d.get("tunnus") == tunnus and d.get("pala") == pala:
            return True
    return False


def kirjaa_esto(hakemisto, lahde, tunnus, pala=None, nyt=_nyt):
    """Estetty hyvaksyntayritys (vartija esta). Ei ole Tonyn ratkaisu, eika sita lasketa sellaisenaan."""
    try:
        rivi = {"aika": nyt(), "lahde": lahde, "tunnus": tunnus, "tapahtuma": "esto", "vartija": "esta"}
        if pala is not None:
            rivi["pala"] = pala
        _kirjoita(hakemisto, rivi)
    except Exception as e:      # kirjaus ei saa estaa mitaan
        _virhe(e)


def kirjaa_ratkaisu(hakemisto, lahde, tunnus, vartija, ratkaisu, pala=None, nyt=_nyt):
    """Yksi rivi Tonyn ratkaisusta. Jos samasta kohteesta on aiempi esto, rivi saa muutettu=true; vain ratkaisulla
    hyvaksytty vartijan arvioksi kirjataan esta (eston perinta). Hylkayksessa ja palan poistossa pidetaan vartijan
    oma arvio muokatusta tekstista, ettei ohilyonti nay osumana. Palauttaa True, jos kirjaus onnistui."""
    try:
        if vartija not in VARTIJA_ARVOT or ratkaisu not in RATKAISUT:
            raise ValueError("tuntematon arvo")
        rivi = {"aika": nyt(), "lahde": lahde, "tunnus": tunnus, "tapahtuma": "ratkaisu", "vartija": vartija,
                "ratkaisu": ratkaisu}
        if pala is not None:
            rivi["pala"] = pala
        if vartija != "esta" and _oli_esto(hakemisto, lahde, tunnus, pala):
            rivi["muutettu"] = True
            if ratkaisu == "hyvaksytty":
                rivi["vartija"] = "esta"
        _kirjoita(hakemisto, rivi)
        return True
    except Exception as e:      # kirjaus ei saa estaa hyvaksyntaa eika hylkaysta
        _virhe(e)
        return False


def luokittele(vartija, ratkaisu):
    """-> "osuma", "ohi", "kysy" tai None (tuntematon rivi)."""
    if vartija == "kysy" and ratkaisu in RATKAISUT:
        return "kysy"
    if vartija == "esta" and ratkaisu in RATKAISUT:
        return "osuma"
    if vartija == "sallittu":
        if ratkaisu == "hyvaksytty":
            return "osuma"
        if ratkaisu in ("hylatty", "pala_poistettu"):
            return "ohi"
    return None


def laske(rivit):
    """Yhteenveto ratkaisuriveista: maarat yhteensa ja lahteittain, osumaprosentti ja vertailurajan tila."""
    def tyhja():
        return {"osuma": 0, "ohi": 0, "kysy": 0}
    yht, lahteittain = tyhja(), {}
    for d in rivit:
        if d.get("tapahtuma") != "ratkaisu":
            continue
        k = luokittele(d.get("vartija"), d.get("ratkaisu"))
        if k is None:
            continue
        yht[k] += 1
        lahteittain.setdefault(str(d.get("lahde")), tyhja())[k] += 1

    def johda(m):
        n = m["osuma"] + m["ohi"]
        return dict(m, laskettu=n, prosentti=(round(100.0 * m["osuma"] / n, 1) if n else None))
    tulos = johda(yht)
    puuttuu = []
    if tulos["laskettu"] < RAJA_PAATOKSET:
        puuttuu.append("p\u00e4\u00e4t\u00f6ksi\u00e4 %d/%d" % (tulos["laskettu"], RAJA_PAATOKSET))
    if tulos["prosentti"] is None or tulos["prosentti"] < RAJA_PROSENTTI:
        puuttuu.append("osumaprosentti alle %d %%" % RAJA_PROSENTTI)
    tulos["raja"] = {"prosentti": RAJA_PROSENTTI, "paatokset": RAJA_PAATOKSET, "taytetty": not puuttuu, "puuttuu": puuttuu}
    tulos["lahteittain"] = {k: johda(v) for k, v in sorted(lahteittain.items())}
    return tulos


def muotoile(t):
    def pros(p):
        return "-" if p is None else ("%.1f %%" % p).replace(".", ",")
    rivit = ["Vuotovartijan osumaprosentti (luovutukset ja paketit)",
             "Lasketut p\u00e4\u00e4t\u00f6kset: %d (osuma %d, ohi %d)" % (t["laskettu"], t["osuma"], t["ohi"]),
             "Osumaprosentti: %s" % pros(t["prosentti"]),
             "Kysy (ei laskettu): %d" % t["kysy"]]
    for k, v in t["lahteittain"].items():
        rivit.append("  %s: lasketut %d, osuma %d, ohi %d, kysy %d, osumaprosentti %s"
                     % (k, v["laskettu"], v["osuma"], v["ohi"], v["kysy"], pros(v["prosentti"])))
    r = t["raja"]
    tila = "t\u00e4yttyy" if r["taytetty"] else "ei t\u00e4yty (%s)" % ", ".join(r["puuttuu"])
    rivit.append("Vertailuraja (v\u00e4hint\u00e4\u00e4n %d %% ja %d p\u00e4\u00e4t\u00f6st\u00e4): %s" % (r["prosentti"], r["paatokset"], tila))
    rivit.append("Raja on vain vertailuarvo: AIVO_LISAPALAT-kytkint\u00e4 ei muuteta automaattisesti.")
    return "\n".join(rivit)


def main(argv=None):
    from aivo import luovutus
    ap = argparse.ArgumentParser(description="Vuotovartijan osumaprosentti luovutuksista ja paketeista.")
    ap.add_argument("--luovutukset", default=os.environ.get("AIVO_LUOVUTUKSET") or luovutus.OLETUS_LUOVUTUKSET,
                    help="yksityinen luovutuskansio (oletus: AIVO_LUOVUTUKSET tai %s)" % luovutus.OLETUS_LUOVUTUKSET)
    ap.add_argument("--json", action="store_true", help="tulosta JSON")
    a = ap.parse_args(argv)
    try:
        t = laske(lue(a.luovutukset))
    except OSError as e:
        sys.stderr.write("vartija_osumat: tiedostoa ei voi lukea (%s)\n" % type(e).__name__)
        return 1
    print(json.dumps(t, ensure_ascii=False, indent=2) if a.json else muotoile(t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
