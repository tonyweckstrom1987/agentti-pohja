"""PR-laatumittari: kuinka moni yojonon (botin) PR on mergattu, suljettu mergaamatta tai auki (julkinen GitHub-API, vain luku).

Kasi laadun arviointi korvautuu luvulla: mergausprosentti repoittain ja tehtavan otsikon (aihe) mukaan. Kaytto: python3 -m agentti.pr_laatu [PAIVIA]
Aamupaketti liittaa tiivistelman riviin 'Yojono-PR:t'."""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from . import github_luku
from .pr_kutsu import REPOT

OMISTAJA = "tonyweckstrom1987"
BOTTI = "agenttifirma-bot"
OLETUS_PAIVIA = 30
# Paivakaton noston ehdot (Tony 28.10.: nostetaan, kun laatu on riittava ja virheita vahan). Aloitusarvot; Tony saataa tarvittaessa.
NOSTO_MIN_PAATETTYJA = 20      # mergattuja + suljettuja ikkunassa
NOSTO_MIN_MERGE_PROS = 75
NOSTO_MAX_KULU_PER_MERGE = 4.0  # USD; kulu = koko yojonon kulu ikkunassa (myos tarkkailu/katselmointi), jaettuna mergattujen PR:ien maaralla
OLETUS_PERUS = "/var/lib/yojono"
PVM_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AIHE_RE = re.compile(r"\[?yojono\]?:?\s*(testit|readme|riippuvuudet|ominaisuus)", re.I)  # 'yojono: testit', '[yojono] testit', '[yojono] yojono: testit'


def _oletushaku(url):
    pyynto = github_luku.pyynto(url, "agenttifirma-pr-laatu")
    with urllib.request.urlopen(pyynto, timeout=20) as v:
        return json.loads(v.read().decode("utf-8"))


def _aika(teksti):
    try:
        return datetime.fromisoformat(str(teksti).replace("Z", "+00:00"))
    except ValueError:
        return None


def on_botti(login):
    """Onko PR:n kirjoittaja botti: kayttajatunnus tai GitHub Appin `<tunnus>[bot]`-muoto (kirjainkoolla ei ole merkitysta)."""
    nimi = str(login or "").lower()
    return nimi in (BOTTI, BOTTI + "[bot]")


def aihe(otsikko):
    """Tehtavan aihe PR:n otsikosta ('yojono: testit llm-stopcheck' -> 'testit'), muuten 'muu'."""
    m = AIHE_RE.search(str(otsikko))
    return m.group(1).lower() if m else "muu"


def _virhe_tiiviisti(e):
    """Poikkeuksen tyyppi ja mahdollinen HTTP-tila (ei viestia: se voi sisaltaa salaisuuksia)."""
    tila = getattr(e, "code", None)
    return type(e).__name__ + (" %s" % tila if isinstance(tila, int) else "")


def keraa(hae=None, paivia=OLETUS_PAIVIA, nyt=None):
    """Palauttaa {'yht': tila, 'repoittain': {repo: tila}, 'aiheittain': {aihe: tila}}, tila = {'mergattu', 'suljettu', 'auki'}.
    Vain botin tekemat PR:t, jotka on avattu viimeisen `paivia` paivan aikana."""
    hae = hae or _oletushaku
    nyt = nyt or datetime.now(timezone.utc)
    raja = nyt - timedelta(days=paivia)
    uusi = lambda: {"mergattu": 0, "suljettu": 0, "auki": 0}
    tulos = {"yht": uusi(), "repoittain": {}, "aiheittain": {}, "virheet": []}
    for repo in REPOT:
        try:
            prt = hae("https://api.github.com/repos/%s/%s/pulls?state=all&sort=created&direction=desc&per_page=50" % (OMISTAJA, repo))
        except Exception as e:
            # Hakuvirhe (esim. tuntirajan 403) kirjataan: luvut ovat talloin alarajoja eika hiljaisia nollia. Vain tyyppi ja HTTP-tila.
            tulos["virheet"].append("%s: %s" % (repo, _virhe_tiiviisti(e)))
            continue
        for p in prt:
            luotu = _aika(p.get("created_at"))
            if luotu is None or luotu < raja:
                continue
            if not on_botti((p.get("user") or {}).get("login")):
                continue
            tila = "auki" if p.get("state") == "open" else ("mergattu" if p.get("merged_at") else "suljettu")
            for kohde in (tulos["yht"], tulos["repoittain"].setdefault(repo, uusi()), tulos["aiheittain"].setdefault(aihe(p.get("title")), uusi())):
                kohde[tila] += 1
    return tulos


def lue_kulut(perus=None):
    """tila.json:n 'kulut' ({ajopaiva: USD}); tyhja sanakirja, jos tiedostoa ei saa luettua."""
    polku = os.path.join(perus or os.environ.get("YOJONO_PERUS", OLETUS_PERUS), "tila.json")
    try:
        with open(polku, encoding="utf-8") as f:
            kulut = json.load(f).get("kulut")
    except (OSError, ValueError, AttributeError):
        return {}
    return kulut if isinstance(kulut, dict) else {}


def kulu_ajalta(kulut, paivia=OLETUS_PAIVIA, nyt=None):
    """Kulujen summa viimeiselta `paivia` ajopaivalta (avain ISO-paiva >= nyt - paivia)."""
    nyt = nyt or datetime.now(timezone.utc)
    raja = (nyt - timedelta(days=paivia)).date().isoformat()
    return sum(float(v) for k, v in (kulut or {}).items() if PVM_RE.match(str(k)) and str(k) >= raja and isinstance(v, (int, float)))


def nostoehdot(tulos, kulu, paivia=OLETUS_PAIVIA):
    """(tayttyy, rivi): tayttyyko paivakaton noston ehdot (riittavasti PR:ia, mergausprosentti, kulu per merge)."""
    y = tulos["yht"]
    paatetyt = y["mergattu"] + y["suljettu"]
    osuus = round(100 * y["mergattu"] / paatetyt) if paatetyt else 0
    kpm = kulu / y["mergattu"] if y["mergattu"] else None
    syyt = []
    if paatetyt < NOSTO_MIN_PAATETTYJA:
        syyt.append("PR:ia liian vahan")
    if osuus < NOSTO_MIN_MERGE_PROS:
        syyt.append("mergausprosentti alle rajan")
    if kpm is None or kpm > NOSTO_MAX_KULU_PER_MERGE:
        syyt.append("kulu per merge yli rajan")
    rivi = "Paivakaton nosto (%d vrk): paatettyja %d/%d, mergausprosentti %d %% (vah. %d), kulu per merge %s (enint. %.1f USD) -> %s" % (
        paivia, paatetyt, NOSTO_MIN_PAATETTYJA, osuus, NOSTO_MIN_MERGE_PROS, "-" if kpm is None else "%.1f USD" % kpm,
        NOSTO_MAX_KULU_PER_MERGE, "EHDOT TAYTTYVAT: ehdota Tonylle nostoa" if not syyt else "ei viela: " + ", ".join(syyt))
    return not syyt, rivi


def _osuus(t):
    paatetyt = t["mergattu"] + t["suljettu"]
    return "%d %%" % round(100 * t["mergattu"] / paatetyt) if paatetyt else "-"


def muotoile(tulos, paivia=OLETUS_PAIVIA):
    y = tulos["yht"]
    rivit = ["Yojono-PR:t %d vrk: %d mergattu, %d suljettu, %d auki (mergausprosentti %s)" % (paivia, y["mergattu"], y["suljettu"], y["auki"], _osuus(y))]
    for otsikko, avain in (("repoittain", "repoittain"), ("aiheittain", "aiheittain")):
        osat = ["%s %d/%d" % (nimi, t["mergattu"], t["mergattu"] + t["suljettu"] + t["auki"]) for nimi, t in sorted(tulos[avain].items())]
        if osat:
            rivit.append("  %s (mergattu/kaikki): %s" % (otsikko, ", ".join(osat)))
    virheet = tulos.get("virheet") or []
    if virheet:
        rivit.append("  VAROITUS: %d hakuvirhetta, luvut ovat alarajoja (%s)" % (len(virheet), "; ".join(virheet)))
    return "\n".join(rivit)


def main(argv=None, hae=None):
    a = sys.argv[1:] if argv is None else argv
    if len(a) > 1 or (a and not a[0].isdigit()):
        print("kaytto: python3 -m agentti.pr_laatu [PAIVIA]", file=sys.stderr)
        return 2
    paivia = int(a[0]) if a else OLETUS_PAIVIA
    print(muotoile(keraa(hae, paivia), paivia))
    return 0


if __name__ == "__main__":
    sys.exit(main())
