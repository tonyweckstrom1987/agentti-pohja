"""Aamukooste: yksi Telegram-viesti Tonylle (enintaan 10 rivi\u00e4, p\u00e4\u00e4t\u00f6kset ensin, + linkit), joka kootaan oikeista tiedoista.

Lahteet (kaikki vain luku):
  - docs-repon claude-jono/tulokset/ (edellinen vuorokausi), tonylle.md:n uudet kohdat ja opit
  - avoimet PR:t (GitHubin julkinen API, agentti.aamupaketti.keraa; ei tunnuksia)
  - yojonon tila (tila.json, jono.json: kulut ja katot vain luettuna) ja systemd:n failed-yksikot

Kaytto: python3 -m claudejono.aamukooste [--laheta] [--nyt ISO-AIKA] [--repo POLKU] [--yojono POLKU]
Ilman --laheta vain tulostaa (kuiva-ajo). Lahetys kayttaa YOJONO_TELEGRAM_TOKEN ja YOJONO_TELEGRAM_CHAT -ymparistoa.
Teksti kirjoitetaan Tonylle suomeksi; koodi on ASCII (\\u-merkinnat)."""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import aivo_arvio, ajuri, kaistamittaus, kulutus, vaiteportti, valmis, varaus

TZ = ZoneInfo("Europe/Helsinki")
OLETUS_REPO = "/var/lib/claude-jono/docs"
OLETUS_YOJONO = "/var/lib/yojono"
DOCS_URL = "https://github.com/tonyweckstrom1987/agenttifirma-docs/blob/main/claude-jono"
IKKUNA_H = 24
RIVI_MAX = 300
RIVI_YHT_MAX = 10   # A546: enintaan 10 rivi\u00e4 (aiemmin 5)
SAANTOPAIVITYKSET = "saantopaivitykset.md"   # Aivon itse tekem\u00e4t s\u00e4\u00e4nt\u00f6p\u00e4ivitykset (A529): otsikot `## <pvm> ...`
PAATOS_IKKUNA_PV = 7   # A490: avoin p\u00e4\u00e4t\u00f6s muistutetaan p\u00e4ivitt\u00e4in t\u00e4m\u00e4n ajan, ellei otsikossa ole suljettu-merkint\u00e4\u00e4
PAATOS_RE = re.compile(r"(?i)aamulla ensin|p\u00e4\u00e4tett\u00e4v\u00e4|paatettava|kyll\u00e4/ei|kysymys")   # vain n\u00e4m\u00e4 otsikot ovat p\u00e4\u00e4t\u00f6spyynt\u00f6j\u00e4 (tonylle.md sis\u00e4lt\u00e4\u00e4 my\u00f6s tiedotteita)
SULJETTU_SANAT = r"(?:aiempi|tehty|hoidettu|vastattu|p\u00e4\u00e4tetty|paatetty|suljettu)"
SULJETTU_LOPUSSA_RE = re.compile(r"(?i)(?:^|[\s\[(:\-\u2013\u2014])" + SULJETTU_SANAT + r"[\s.!)\]]*$")      # otsikon lopussa (myos suluissa)
SULJETTU_ALUSSA_RE = re.compile(r"(?i)^[\[(]\s*" + SULJETTU_SANAT + r"\s*[\])]|\b" + SULJETTU_SANAT + r"\s*:")     # [TEHTY] alussa tai TEHTY: (kaksoispiste = tilamerkinta)
KIELTO_RE = re.compile(r"(?i)\b(ei|ole|viela|viel\u00e4|ilman)\s+(?:\w+\s+)?" + SULJETTU_SANAT + r"[\s.!)\]]*$")   # "ei ole tehty"


def suljettu(otsikko):
    """Otsikko on suljettu vain, jos sulkusana on tilamerkint\u00e4 otsikon alussa ([TEHTY], TEHTY:) tai lopussa
    (\u2026 VASTATTU). Sana kesken otsikkoa ('aiempi korjaus rikki'), kielteinen muoto ('ei ole tehty') ja
    kysymysmerkkiin p\u00e4\u00e4ttyv\u00e4 kysymys ('onko X tehty?') j\u00e4\u00e4v\u00e4t avoimiksi."""
    o = " ".join((otsikko or "").split())
    if SULJETTU_ALUSSA_RE.search(o):
        return True
    return bool(SULJETTU_LOPUSSA_RE.search(o)) and not KIELTO_RE.search(o)


KULU_RIVI_MAX = 700   # kulutusrivi (mallit + viikko + Tonyn varaus) on pidempi kuin muut rivit
MAX_MERGELINKKEJA = 4
BUDJETTI_HALYTYS = 0.8   # nayttorajaus: kuukausikulu >= 80 % katosta nostetaan halytyksiin (ei muuta kattoa)

TYHJA_OPPI_RE = re.compile(r"(?i)^(t[a\u00e4]m[a\u00e4] ajo ei|ei uutta|ei uusia|ei opittu|ei mit[a\u00e4][a\u00e4]n)")
PR_RE = re.compile(r"github\.com/tonyweckstrom1987/([A-Za-z0-9._-]+)/pull/(\d+)")
AIKA_RE = re.compile(r"^- Aika:\s*(\S+)", re.M)
TILA_RE = re.compile(r"^- Tila:\s*(\S+)", re.M)
OTSIKKO_RE = re.compile(
    r"^##[ \t]+(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})\S*|[ \t]+klo[ \t]+(\d{1,2})[.:](\d{2}))?(.*)$", re.M)


def _suomi_pvm(dt):
    return f"{dt.day}.{dt.month}.{dt.year}"


def lue_tulokset(repo, alku, loppu):
    """Tulokset, joiden `- Aika:` on valilla [alku, loppu]. Palauttaa listan {nimi, tila, aika, teksti}."""
    hak = Path(repo) / ajuri.JONO / "tulokset"
    ulos = []
    if not hak.is_dir():
        return ulos
    for p in valmis.tulostiedostot(hak):   # sama suodatin kuin virhemittarilla: *.tarkastus.md ei ole jonotulos
        try:
            teksti = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        a, t = AIKA_RE.search(teksti[:600]), TILA_RE.search(teksti[:600])
        if not a or not t:
            continue
        try:
            aika = datetime.fromisoformat(a.group(1))
        except ValueError:
            continue
        if aika.tzinfo is None:
            aika = aika.replace(tzinfo=TZ)
        if alku <= aika <= loppu:
            # A515-A516: VALMIS vaatii riippumattoman tarkastajan merkinnan (valmis.py); muuten KESKEN.
            tila = valmis.tehokas_tila(t.group(1), teksti, valmis.lue_tarkastus(hak, p.name))
            ulos.append({"nimi": p.name, "tila": tila, "ajurin_tila": t.group(1), "aika": aika, "teksti": teksti})
    return ulos


def tonylle_uudet(repo, alku, tiedosto=None, koko=False):
    """tonylle.md:n kohdat (`## <pvm>[Thh:mm | klo hh.mm] ...`), jotka ovat alkua uudempia. Pelkka paiva lasketaan uudeksi,
    jos paiva >= alun paiva. Palauttaa listan (aika, loppuotsikko)."""
    p = Path(repo) / ajuri.JONO / (tiedosto or ajuri.TONYLLE)
    try:
        teksti = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ulos = []
    for m in OTSIKKO_RE.finditer(teksti):
        v, kk, pv = int(m.group(1)), int(m.group(2)), int(m.group(3))
        tunti, min_ = m.group(4) or m.group(6), m.group(5) or m.group(7)
        try:
            if tunti is None:
                aika = datetime(v, kk, pv, 23, 59, tzinfo=TZ)   # kellonaika puuttuu: katsotaan paivan loppuun asti
                uusi = aika.date() >= alku.astimezone(TZ).date()
            else:
                aika = datetime(v, kk, pv, int(tunti), int(min_), tzinfo=TZ)
                uusi = aika >= alku
        except ValueError:
            continue
        if uusi:
            otsikko = " ".join(m.group(8).split())
            ulos.append((aika, otsikko if koko else otsikko[:60]))
    return ulos


def avoimet_paatokset(repo, nyt):
    """tonylle.md:n kohdat viimeiselt\u00e4 PAATOS_IKKUNA_PV p\u00e4iv\u00e4lt\u00e4, joiden otsikossa ei ole suljettu-merkint\u00e4\u00e4 (TEHTY, HOIDETTU, VASTATTU, P\u00c4\u00c4TETTY).
    Vastauksen merkkaa Tony tai pilvi-Claude otsikkoon; ilman merkint\u00e4\u00e4 kohta muistutetaan joka aamu (A490)."""
    # sulkumerkinta tarkistetaan koko otsikosta (nayttoon leikataan 60 merkkiin vasta sen jalkeen)
    return [(a, o[:60]) for a, o in tonylle_uudet(repo, nyt - timedelta(days=PAATOS_IKKUNA_PV), koko=True)
            if PAATOS_RE.search(o) and not suljettu(o)]


def yojono_tila(perus, nyt):
    """Yojonon tila vain luettuna: {valmis, epaonnistui[id], kulu_tanaan, paivakatto, kuukausikulu, kuukausikatto}. Puuttuva tila -> None."""
    try:
        tila = json.loads((Path(perus) / "tila.json").read_text(encoding="utf-8"))
        jono = json.loads((Path(perus) / "jono.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    from yojono import ajuri as yj
    pvm = yj.ajopaiva(nyt)
    raja = yj.ajopaiva(nyt - timedelta(hours=IKKUNA_H))
    tehtavat = tila.get("tehtavat", {}) if isinstance(tila.get("tehtavat"), dict) else {}
    viime = {i: t for i, t in tehtavat.items() if isinstance(t, dict) and str(t.get("pvm", "")) >= raja}
    kulut = tila.get("kulut", {}) if isinstance(tila.get("kulut"), dict) else {}
    kk = pvm[:7]
    return {"valmis": sum(1 for t in viime.values() if t.get("tila") == "valmis"),
            "epaonnistui": sorted(i for i, t in viime.items() if t.get("tila") == "epaonnistui"),
            "kulu_tanaan": float(kulut.get(pvm, 0) or 0),
            "paivakatto": jono.get("paivakatto"),
            "kuukausikulu": sum(float(v) for k, v in kulut.items() if str(k).startswith(kk) and isinstance(v, (int, float))),
            "kuukausikatto": jono.get("kuukausikatto")}


def rikkinaiset_yksikot(suorita=subprocess.run):
    """systemd:n failed-yksikot (nimet). None, jos kysely ei onnistu."""
    try:
        r = suorita(["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [rivi.split()[0] for rivi in r.stdout.splitlines() if rivi.strip()]


OLETUS_POHJA = "/opt/agentti-pohja"
OLETUS_YOJONO_AJURI = "/opt/yojono-ajuri"


def kayttoonotto_ero_rivi(pohja=OLETUS_POHJA, yojono_ajuri=OLETUS_YOJONO_AJURI, hae_vertailu=None, suorita=subprocess.run):
    """Teksti, jos mainissa on ajureihin viela ottamattomia yhdistettyja muutoksia; None, jos kaikki ovat ajan tasalla.

    Vertailu on tools/kayttoonotto_ero.py:n (GitHub compare). agentti-pohjan commit luetaan gitista, yojono-ajurin
    (ei .git-hakemistoa) tiedostosta .julkaisu-tieto. Verkkovirhe palauttaa tekstin 'ei selvinnyt', ei poikkeusta."""
    try:
        from tools import kayttoonotto_ero as ero
    except ImportError:
        return None
    hae = hae_vertailu or ero.hae_vertailu
    kohteet = []   # (nimi, jaljessa, omia, pr)
    try:
        commitit = {"agentti-pohja": ero.paikallinen_commit(pohja, suorita=suorita)}
        try:
            tieto = (Path(yojono_ajuri) / ".julkaisu-tieto").read_text(encoding="utf-8")
            commitit["y\u00f6jono-ajuri"] = next(r[7:].strip() for r in tieto.splitlines() if r.startswith("commit="))
        except (OSError, StopIteration):
            commitit["y\u00f6jono-ajuri"] = None
        for nimi, commit in commitit.items():
            if commit is None:
                return "ajurien k\u00e4ytt\u00f6\u00f6noton tila ei selvinnyt (" + nimi + ")"
            kohteet.append((nimi,) + ero.tiivista(hae("agentti-pohja", commit, "main")))
    except Exception as virhe:   # verkko- tai gitvirhe ei kaada koostetta; vain tyyppi ja EroVirheen lyhyt syy nakyviin
        syy = getattr(virhe, "lyhyt", None) if isinstance(virhe, ero.EroVirhe) else None
        return "ajurien k\u00e4ytt\u00f6\u00f6noton tila ei selvinnyt (" + type(virhe).__name__ + (": " + syy if syy else "") + ")"
    jaljessa = [k for k in kohteet if k[1] or k[2]]
    if not jaljessa:
        return None
    pr = []
    for k in jaljessa:
        pr += [n for n in k[3] if n not in pr]
    osat = ", ".join(f"{k[0]} {k[1]} committia" for k in jaljessa)
    return f"ajurit j\u00e4ljess\u00e4 mainista: {osat}" + (" (PR " + ", ".join(f"#{n}" for n in pr[:5]) + (" ..." if len(pr) > 5 else "") + ")" if pr else "") \
        + "; p\u00e4ivit\u00e4 komennolla paivita-ajurit.sh"


def keraa(repo, perus, nyt, hae=None, yksikot=None, ero=None):
    """Kokoaa kooste-aineiston. `hae` = GitHub-haku (testeissa tekaistu), `yksikot` = failed-yksikoiden lahde,
    `ero` = kayttoonoton eron lahde (kutsuttava, palauttaa tekstin tai None; oletuksena ei hae mitaan)."""
    from agentti import aamupaketti
    alku = nyt - timedelta(hours=IKKUNA_H)
    tulokset = lue_tulokset(repo, alku, nyt)
    opit = []
    for t in tulokset:
        if t["ajurin_tila"] == "VALMIS":   # opit tulevat ajurin VALMIS-tuloksista, vaikka tarkastus puuttuu
            opit += [o for o in ajuri.suodata_opit(ajuri.poimi_uusi_oppi(t["teksti"]))[0] if not TYHJA_OPPI_RE.match(o)]
    prt = []
    for t in tulokset:
        for repo_nimi, nro in PR_RE.findall(t["teksti"]):
            viite = f"{repo_nimi}#{nro}"
            if viite not in prt:
                prt.append(viite)
    virheet = []
    perus_hae = hae or aamupaketti._oletushaku

    def laskeva_hae(url):   # aamupaketti.keraa nielee hakuvirheet: lasketaan ne, ettei vajaa lista nayta tyhjalta onnistumiselta
        try:
            return perus_hae(url)
        except Exception as virhe:
            virheet.append(type(virhe).__name__)
            raise

    try:
        pr_ryhmat = aamupaketti.keraa(laskeva_hae)
    except Exception as virhe:   # GitHub-haku ei saa kaataa koostetta
        print(f"aamukooste: PR-haku epaonnistui ({type(virhe).__name__})", file=sys.stderr)
        pr_ryhmat = None
    try:   # kaistakoe: yksi osa Tehty-riville; puuttuva tai rikkinainen loki ei kaada koostetta
        kaistat = kaistamittaus.rivi(Path(repo).parent, nyt, perus)
    except Exception:
        kaistat = None
    kulutus_teksti = varaus_rivi = varaus_kysymys = None
    try:   # mallikohtainen kulutus ja Tonyn varaus; puuttuva loki ei kaada koostetta
        tila = Path(repo).parent
        kulutus_teksti = kulutus.kulutusrivi(tila, nyt)
        varaus_rivi, varaus_kysymys = varaus.aamukooste_osa(tila, os.environ, nyt)
    except Exception:
        pass
    try:
        aivo_valmis = aivo_arvio.valmius_taynna(repo)
    except Exception:
        aivo_valmis = False
    from . import tarkastaja
    try:   # tarkastajan ja PR-avaajan viat nakyviin; puuttuva loki ei kaada koostetta
        tarkastaja_virheet = tarkastaja.virheet_lokista(Path(repo).parent, alku)
    except Exception:
        tarkastaja_virheet = []
    try:   # ajurien jaljessaolo mainista: vain pyydettaessa, virhe ei kaada koostetta
        ero_teksti = ero() if ero else None
    except Exception:
        ero_teksti = None
    return {"nyt": nyt, "aivo_valmis": aivo_valmis, "tarkastaja_virheet": tarkastaja_virheet, "kaistat": kaistat, "kulutus": kulutus_teksti, "varaus": varaus_rivi, "varaus_kysymys": varaus_kysymys, "tulokset": tulokset, "uudet_prt": prt, "opit": opit, "tonylle": tonylle_uudet(repo, alku), "paatokset": avoimet_paatokset(repo, nyt), "saannot": tonylle_uudet(repo, alku, SAANTOPAIVITYKSET),
            "pr": pr_ryhmat, "pr_virheita": virheet, "yojono": yojono_tila(perus, nyt),
            "yksikot": (yksikot or rikkinaiset_yksikot)(), "vaiteportti": vaiteportti.puutteet(repo), "ero": ero_teksti}


def _lyhenna(teksti, raja=RIVI_MAX):
    return teksti if len(teksti) <= raja else teksti[:raja - 3].rstrip() + "..."


def rivit(t):
    """Enint\u00e4\u00e4n 10 rivi\u00e4, p\u00e4\u00e4t\u00f6kset ensin: P\u00e4\u00e4t\u00f6kset, Tehty, Kesken, S\u00e4\u00e4nn\u00f6t, Opittu, H\u00e4lytykset, Kulut."""
    nimet = {"VALMIS": 0, "KESKEN": 0, "EPAONNISTUI": 0, "HYLATTY": 0}
    for r in t["tulokset"]:
        if r["tila"] in nimet:
            nimet[r["tila"]] += 1
    yj = t["yojono"]

    paatos_osat = []
    if t["pr"] is None:
        paatos_osat.append("PR-listaa ei saatu (GitHub-haku ep\u00e4onnistui)")
    else:
        from agentti.aamupaketti import puuttuu_rivi
        n = len(t["pr"].get("tony", []))
        puuttuu = puuttuu_rivi(t["pr"].get("puuttuu"))
        if n:
            paatos_osat.append(f"{n} PR:\u00e4\u00e4 mergattavaksi (kyll\u00e4/ei)")
        elif not puuttuu and not t.get("pr_virheita"):
            paatos_osat.append("ei mergattavia PR:j\u00e4")   # nolla vain kun kaikki haut onnistuivat
        if puuttuu:   # lahde + virhetyyppi + HTTP-tila: tieto puuttuu, ei aitoa nollaa
            paatos_osat.append(puuttuu)
        elif t.get("pr_virheita"):
            paatos_osat.append(f"PR-lista voi olla vajaa ({len(t['pr_virheita'])} hakuvirhett\u00e4, esim. {t['pr_virheita'][0]})")
        if t["pr"].get("automerge"):
            paatos_osat.append(f"{len(t['pr']['automerge'])} hoituu automergell\u00e4")
    if t.get("aivo_valmis"):   # Tony 7.11.2026: kerro HETI, kun kaikki aivo-valmius.md:n kohdat t\u00e4yttyv\u00e4t
        paatos_osat.insert(0, "Aivo on valmis OpenRouteriin ja itsekehitykseen: kaikki aivo-valmius.md:n kohdat t\u00e4yttyv\u00e4t")
    avoimet = t.get("paatokset") or []
    muut = [k for k in (t.get("tonylle") or []) if k not in avoimet]   # ei hiljaa katoavia kohtia: avainsanaan osumattomat vain laskurina
    if muut:
        paatos_osat.append(f"+{len(muut)} muuta kohtaa tonylle.md:ss\u00e4")
    # kalibrointipyynt\u00f6 ennen vapaamittaisia otsikoita: _lyhenna leikkaa rivin lopusta, joten pyynt\u00f6 ei saa j\u00e4\u00e4d otsikoiden taakse
    if t.get("varaus_kysymys"):
        paatos_osat.append(t["varaus_kysymys"])
    if t.get("ero"):   # ajurit jaljessa mainista
        paatos_osat.append(t["ero"])
    if avoimet:
        paatos_osat.append(f"{len(avoimet)} avointa kohtaa tonylle.md:ss\u00e4 (muistutus p\u00e4ivitt\u00e4in): " + "; ".join(f"{a.day}.{a.month}. {o}".strip() for a, o in avoimet[:2]))
    paatokset = "P\u00e4\u00e4t\u00f6kset sinulle: " + ", ".join(paatos_osat) + "."

    ilman = sum(1 for r in t["tulokset"] if r["tila"] == "KESKEN" and r.get("ajurin_tila") == "VALMIS")
    ilman_osa = f" (heist\u00e4 {ilman} ilman tarkastajan merkint\u00e4\u00e4)" if ilman else ""
    kesken_osa = f"{nimet['KESKEN']} kesken{ilman_osa}, " if nimet["KESKEN"] else ""
    osat = [f"{len(t['tulokset'])} jonotulosta ({nimet['VALMIS']} valmis, {kesken_osa}{nimet['EPAONNISTUI']} ep\u00e4onnistui, {nimet['HYLATTY']} hyl\u00e4tty)"]
    if t["uudet_prt"]:
        nayta = ", ".join(t["uudet_prt"][:4]) + (f" (+{len(t['uudet_prt']) - 4})" if len(t["uudet_prt"]) > 4 else "")
        osat.append("PR:t " + nayta)
    if yj:
        osat.append(f"y\u00f6jono {yj['valmis']} valmis")
    if t.get("kaistat"):
        osat.append(t["kaistat"])
    tehty = "Tehty: " + "; ".join(osat) + "."

    kesken_nimet = [r["nimi"].removesuffix(".md") for r in t["tulokset"] if r["tila"] == "KESKEN"]
    kesken = ("Kesken (ei tarkastajan vahvistusta): " + ", ".join(kesken_nimet[:3]) + (f" (+{len(kesken_nimet) - 3})" if len(kesken_nimet) > 3 else "") + "."
              if kesken_nimet else "Kesken: ei mit\u00e4\u00e4n.")

    sn = t.get("saannot") or []
    saannot = ("S\u00e4\u00e4nt\u00f6p\u00e4ivitykset (Aivo): " + "; ".join(f"{a.day}.{a.month}. {o}".strip() for a, o in sn[:3]) + (f" (+{len(sn) - 3})" if len(sn) > 3 else "") + "."
               if sn else "S\u00e4\u00e4nt\u00f6p\u00e4ivitykset (Aivo): ei.")

    opittu = "Opittu: " + ("; ".join(o.rstrip(".") for o in t["opit"][:2]) if t["opit"] else "ei uusia oppeja viime vuorokaudessa") + "."

    halytykset = []
    if t["yksikot"] is None:
        halytykset.append("systemd-tilaa ei saatu")
    elif t["yksikot"]:
        halytykset.append("failed: " + ", ".join(t["yksikot"][:3]) + (f" (+{len(t['yksikot']) - 3})" if len(t["yksikot"]) > 3 else ""))
    if t.get("vaiteportti"):
        halytykset.append("v\u00e4iteportti: " + ", ".join(t["vaiteportti"][:3]) + " valmis ilman .tarkastus.md-tiedostoa")
    if t.get("tarkastaja_virheet"):
        tv = t["tarkastaja_virheet"]
        kohde = tv[-1].get("haara") or tv[-1].get("pr") or tv[-1].get("repo") or "?"
        halytykset.append(f"PR-tarkastaja/-avaaja: {len(tv)} virhett\u00e4, viimeisin {kohde}: " + _lyhenna(str(tv[-1].get("syy", "")), 80))
    if nimet["EPAONNISTUI"]:
        halytykset.append(f"{nimet['EPAONNISTUI']} jonoteht\u00e4v\u00e4\u00e4 ep\u00e4onnistui")
    if yj and yj["epaonnistui"]:
        halytykset.append("y\u00f6jono ep\u00e4onnistui: " + ", ".join(yj["epaonnistui"][:3]))
    if yj and isinstance(yj["kuukausikatto"], (int, float)) and yj["kuukausikatto"] > 0 \
            and yj["kuukausikulu"] >= BUDJETTI_HALYTYS * yj["kuukausikatto"]:
        halytykset.append(f"kuukausibudjetista k\u00e4ytetty {round(100 * yj['kuukausikulu'] / yj['kuukausikatto'])} %")
    hal = "H\u00e4lytykset: " + ("; ".join(halytykset) if halytykset else "ei") + "."

    if yj:
        paiva = f"{yj['kulu_tanaan']:.2f}" + (f" / {yj['paivakatto']:.2f}" if isinstance(yj["paivakatto"], (int, float)) else "")
        kk = f"{yj['kuukausikulu']:.2f}" + (f" / {yj['kuukausikatto']:.2f}" if isinstance(yj["kuukausikatto"], (int, float)) else "")
        kulut = f"Kulut: y\u00f6jono t\u00e4n\u00e4\u00e4n {paiva} USD, kuukausi {kk} USD."
    else:
        kulut = "Kulut: y\u00f6jonon tilaa ei saatu luettua."
    kulut = " ".join(x.rstrip(".") + "." for x in (kulut, t.get("kulutus"), t.get("varaus")) if x)
    ulos = [_lyhenna(r, KULU_RIVI_MAX if r is kulut else RIVI_MAX) for r in (paatokset, tehty, kesken, saannot, opittu, hal, kulut)]
    assert len(ulos) <= RIVI_YHT_MAX
    return ulos


def linkit(t):
    ulos = []
    for r in (t["pr"] or {}).get("tony", [])[:MAX_MERGELINKKEJA]:
        ulos.append(f"Merge: {r['repo']} #{r['numero']} {r['otsikko'][:50]}\n{r['url']}")
    if t["pr"] and len(t["pr"].get("tony", [])) > MAX_MERGELINKKEJA:
        ulos.append(f"(+{len(t['pr']['tony']) - MAX_MERGELINKKEJA} muuta mergattavaa: aja python3 -m agentti.aamupaketti)")
    if t["tonylle"] or t.get("paatokset"):
        ulos.append(f"tonylle.md: {DOCS_URL}/tonylle.md")
    ulos.append(f"Tulokset: {DOCS_URL}/tulokset")
    return ulos


def muotoile(t):
    return "\n".join(rivit(t)) + "\n\nLinkit:\n" + "\n".join(linkit(t))


def main(argv=None):
    p = argparse.ArgumentParser(description="Aamukooste Telegramiin (kuiva-ajo ilman --laheta)")
    p.add_argument("--laheta", action="store_true")
    p.add_argument("--nyt", help="ISO-aika (testaus)")
    p.add_argument("--repo", default=os.environ.get("CLAUDEJONO_REPO", OLETUS_REPO))
    p.add_argument("--yojono", default=os.environ.get("YOJONO_PERUS", OLETUS_YOJONO))
    a = p.parse_args(argv)
    nyt = datetime.fromisoformat(a.nyt) if a.nyt else datetime.now(TZ)
    if nyt.tzinfo is None:
        nyt = nyt.replace(tzinfo=TZ)
    teksti = muotoile(keraa(a.repo, a.yojono, nyt, ero=kayttoonotto_ero_rivi))
    print(teksti)
    if a.laheta:
        from yojono.ajuri import laheta_telegram
        if not laheta_telegram(teksti, pakota=True):
            print("Telegram-l\u00e4hetys ep\u00e4onnistui tai ei asetettu".encode("ascii", "backslashreplace").decode(), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
