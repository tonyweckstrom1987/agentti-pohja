"""Aamukooste: yksi Telegram-viesti Tonylle (enintaan 5 rivi\u00e4 + linkit), joka kootaan oikeista tiedoista.

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

from . import ajuri, kaistamittaus, kulutus, todennettavat, vaiteportti, varaus

TZ = ZoneInfo("Europe/Helsinki")
OLETUS_REPO = "/var/lib/claude-jono/docs"
OLETUS_YOJONO = "/var/lib/yojono"
DOCS_URL = "https://github.com/tonyweckstrom1987/agenttifirma-docs/blob/main/claude-jono"
IKKUNA_H = 24
RIVI_MAX = 300
TARVITAAN_MAX = 500   # tarvitaan-rivi voi sisaltaa todennuskysymykset
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
    for p in sorted(hak.glob("*.md")):
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
            ulos.append({"nimi": p.name, "tila": t.group(1), "aika": aika, "teksti": teksti})
    return ulos


def tonylle_uudet(repo, alku):
    """tonylle.md:n kohdat (`## <pvm>[Thh:mm | klo hh.mm] ...`), jotka ovat alkua uudempia. Pelkka paiva lasketaan uudeksi,
    jos paiva >= alun paiva. Palauttaa listan (aika, loppuotsikko)."""
    p = Path(repo) / ajuri.JONO / ajuri.TONYLLE
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
            ulos.append((aika, " ".join(m.group(8).split())[:60]))
    return ulos


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


def keraa(repo, perus, nyt, hae=None, yksikot=None):
    """Kokoaa kooste-aineiston. `hae` = GitHub-haku (testeissa tekaistu), `yksikot` = failed-yksikoiden lahde."""
    from agentti import aamupaketti
    alku = nyt - timedelta(hours=IKKUNA_H)
    tulokset = lue_tulokset(repo, alku, nyt)
    opit = []
    for t in tulokset:
        if t["tila"] == "VALMIS":
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
    try:   # todennettavat: eraantyneet avoimet rivit; puuttuva tiedosto ei kaada koostetta
        todennus = todennettavat.kysymykset(todennettavat.eraantyneet(repo, nyt))
    except Exception:
        todennus = None
    return {"nyt": nyt, "todennus": todennus, "kaistat": kaistat, "kulutus": kulutus_teksti, "varaus": varaus_rivi, "varaus_kysymys": varaus_kysymys, "tulokset": tulokset, "uudet_prt": prt, "opit": opit, "tonylle": tonylle_uudet(repo, alku),
            "pr": pr_ryhmat, "pr_virheita": virheet, "yojono": yojono_tila(perus, nyt),
            "yksikot": (yksikot or rikkinaiset_yksikot)(), "vaiteportti": vaiteportti.puutteet(repo)}


def _lyhenna(teksti, raja=RIVI_MAX):
    return teksti if len(teksti) <= raja else teksti[:raja - 3].rstrip() + "..."


def rivit(t):
    """Viisi rivi\u00e4 (tehty, tarvitaan, opittu, h\u00e4lytykset, kulut) tiedoista."""
    nimet = {"VALMIS": 0, "KESKEN": 0, "EPAONNISTUI": 0, "HYLATTY": 0}
    for r in t["tulokset"]:
        if r["tila"] in nimet:
            nimet[r["tila"]] += 1
    yj = t["yojono"]
    osat = [f"{len(t['tulokset'])} jonotulosta ({nimet['VALMIS']} valmis, {str(nimet['KESKEN']) + ' kesken, ' if nimet['KESKEN'] else ''}{nimet['EPAONNISTUI']} ep\u00e4onnistui, {nimet['HYLATTY']} hyl\u00e4tty)"]
    if t["uudet_prt"]:
        nayta = ", ".join(t["uudet_prt"][:4]) + (f" (+{len(t['uudet_prt']) - 4})" if len(t["uudet_prt"]) > 4 else "")
        osat.append("PR:t " + nayta)
    if yj:
        osat.append(f"y\u00f6jono {yj['valmis']} valmis")
    if t.get("kaistat"):
        osat.append(t["kaistat"])
    tehty = "Tehty: " + "; ".join(osat) + "."

    tarvitaan_osat = []
    if t["pr"] is None:
        tarvitaan_osat.append("PR-listaa ei saatu (GitHub-haku ep\u00e4onnistui)")
    else:
        n = len(t["pr"].get("tony", []))
        tarvitaan_osat.append(f"{n} PR:\u00e4\u00e4 mergattavaksi" if n else "ei mergattavia PR:j\u00e4")
        if t.get("pr_virheita"):
            tarvitaan_osat.append(f"PR-lista voi olla vajaa ({len(t['pr_virheita'])} hakuvirhett\u00e4, esim. {t['pr_virheita'][0]})")
        if t["pr"].get("automerge"):
            tarvitaan_osat.append(f"{len(t['pr']['automerge'])} hoituu automergell\u00e4")
    if t["tonylle"]:
        tarvitaan_osat.append(f"{len(t['tonylle'])} uutta kohtaa tonylle.md:ss\u00e4")
    if t.get("varaus_kysymys"):
        tarvitaan_osat.append(t["varaus_kysymys"])
    if t.get("todennus"):
        tarvitaan_osat.append(t["todennus"])
    tarvitaan = "Tarvitaan sinulta: " + ", ".join(tarvitaan_osat) + "."

    opittu = "Opittu: " + ("; ".join(o.rstrip(".") for o in t["opit"][:2]) if t["opit"] else "ei uusia oppeja viime vuorokaudessa") + "."

    halytykset = []
    if t["yksikot"] is None:
        halytykset.append("systemd-tilaa ei saatu")
    elif t["yksikot"]:
        halytykset.append("failed: " + ", ".join(t["yksikot"][:3]) + (f" (+{len(t['yksikot']) - 3})" if len(t["yksikot"]) > 3 else ""))
    if t.get("vaiteportti"):
        halytykset.append("v\u00e4iteportti: " + ", ".join(t["vaiteportti"][:3]) + " valmis ilman .tarkastus.md-tiedostoa")
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
    return [_lyhenna(r, KULU_RIVI_MAX if r is kulut else TARVITAAN_MAX if r is tarvitaan else RIVI_MAX) for r in (tehty, tarvitaan, opittu, hal, kulut)]


def linkit(t):
    ulos = []
    for r in (t["pr"] or {}).get("tony", [])[:MAX_MERGELINKKEJA]:
        ulos.append(f"Merge: {r['repo']} #{r['numero']} {r['otsikko'][:50]}\n{r['url']}")
    if t["pr"] and len(t["pr"].get("tony", [])) > MAX_MERGELINKKEJA:
        ulos.append(f"(+{len(t['pr']['tony']) - MAX_MERGELINKKEJA} muuta mergattavaa: aja python3 -m agentti.aamupaketti)")
    if t["tonylle"]:
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
    teksti = muotoile(keraa(a.repo, a.yojono, nyt))
    print(teksti)
    if a.laheta:
        from yojono.ajuri import laheta_telegram
        if not laheta_telegram(teksti, pakota=True):
            print("Telegram-l\u00e4hetys ep\u00e4onnistui tai ei asetettu".encode("ascii", "backslashreplace").decode(), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
