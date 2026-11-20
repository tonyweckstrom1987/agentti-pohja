"""Seuranta: tilanne lahteista, ei epasuorista merkeista (virheloki 6.11., 7.11. ja 9.11.; pohdinta A917).

Yksi komento, joka lukee totuuden lahteesta: tulostiedostoista, GitHubin PR-sivuilta ja docs-repon tiedostoista.
Viisi osiota:
  1. Viimeisen N tunnin tulokset, joiden tila on EPAONNISTUI tai KESKEN, syy ja ehdotus.
  2. Avoimet PR:t: karki, CI nykykarjesta, tarkastajan paatos NYKYKARJESTA (vanhan karjen paatosta ei kayteta)
     ja paatoskommentin loydokset (rivit "- "), yhdistettavyys ja ristiriidat. Rajatut ensin.
  3. Julkisten repojen avointen PR:ien otsikko, kuvaus ja lisatyt rivit isannan tailnet-osoitteen varalta.
     Osoite luetaan docs-repon 01-infra.md:sta eika sita koskaan tulosteta: vain "osumia N, repo #nro".
  4. tonylle.md:n avointen kohtien maara (raja 20, A886).
  5. KIIRE-tehtavat: ajettu vai jonossa.
Jos lahdetta ei voi lukea, rivilla lukee EI TIETOA (ei nollaa).

Kaytto:
    python3 -I tools/seuranta.py --juuri /polku/docs-repon-juureen [--ilman-github] [--tunnit 24]
                                 [--tunnus-tiedosto POLKU] [--omistaja tonyweckstrom1987]
Lukutunnus (vain luku riittaa): ymparistomuuttuja SEURANTA_TUNNUS tai GITHUB_TOKEN, muuten --tunnus-tiedosto tai
/etc/agentscope/github-luku.token jos se on luettavissa. Ilman tunnusta luetaan julkista dataa (kutsuraja 60/h).
Paluukoodit: 0 = kaikki lahteet luettu, ei tailnet-osumia; 3 = tailnet-osoite julkisessa PR:ssa (vakava);
4 = jokin lahde jai lukematta (EI TIETOA), joten "ei osumia" ei ole todennettu; 1 = kayttovirhe.
Vain stdlib, Python 3.10. Virheista tulostetaan vain tyyppi ja HTTP-tila; tunnusta ei tulosteta."""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

EI_TIETOA = "EI TIETOA"
TONYLLE_RAJA = 20
OLETUS_TUNNUS_TIEDOSTO = "/etc/agentscope/github-luku.token"
OLETUS_OMISTAJA = "tonyweckstrom1987"
BOTTI = "agenttifirma-bot"
# Sama merkki kuin tools/automerge.py:ssa (testi varmistaa, etta kuviot pysyvat samoina).
TARKASTUS_RE = re.compile(r"<!-- riippumaton-tarkastus v1 sha=([0-9a-f]{40}) paatos=(HYVAKSYTTY|HYLATTY|RAJATTU|EN_TIEDA) -->")
# Tailnet-osoitteet: CGNAT-alue (100.64/10) ja tailnetin nimet (paate ts.net).
TAILNET_RE = re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b",
                        re.IGNORECASE)
# Tulosteeseen ei paase IP-osoitteita eika tailnet-nimia, vaikka katselmointikommentti tai otsikko lainaisi niita.
PEITA_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net\b", re.IGNORECASE)
SIVUKOKO = 100
SIVUJA_MAX = 10
LOYDOKSIA_MAX = 6


def peita(teksti):
    return PEITA_RE.sub("[osoite]", teksti)


class Lukuvirhe(Exception):
    """Lahdetta ei saatu luettua. Viesti on vain virheen tyyppi ja HTTP-tila, ei sisaltoa."""


# ---------------------------------------------------------------- GitHub

def http_hae(url, tunnus=None):
    """(tila, json) GitHubista. Nostaa Lukuvirhe, jossa on vain tyyppi ja HTTP-tila."""
    otsakkeet = {"Accept": "application/vnd.github+json", "User-Agent": "agentti-seuranta"}
    if tunnus:
        otsakkeet["Authorization"] = "Bearer " + tunnus
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=otsakkeet), timeout=30) as v:
            return v.status, json.loads(v.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raja = " (kutsuraja)" if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0" else ""
        raise Lukuvirhe("HTTP %d%s" % (e.code, raja))
    except Exception as e:                                          # vain tyypin nimi
        raise Lukuvirhe(type(e).__name__)


class GitHub:
    def __init__(self, tunnus=None, hae=None):
        self.tunnus = tunnus
        self._hae = hae or http_hae
        self.osittainen = False          # sivutusraja tayttyi: lista voi olla vajaa

    def get(self, polku):
        url = polku if polku.startswith("https://") else "https://api.github.com" + polku
        return self._hae(url, self.tunnus)[1]

    def sivut(self, polku):
        """Kaikki sivut listana. Jos SIVUJA_MAX tayttyy, self.osittainen = True."""
        tulos = []
        erotin = "&" if "?" in polku else "?"
        for sivu in range(1, SIVUJA_MAX + 1):
            osa = self.get("%s%sper_page=%d&page=%d" % (polku, erotin, SIVUKOKO, sivu))
            if not isinstance(osa, list):
                raise Lukuvirhe("odottamaton vastaus")
            tulos.extend(osa)
            if len(osa) < SIVUKOKO:
                return tulos
        self.osittainen = True
        return tulos


def lue_tunnus(ymparisto, tiedosto):
    for nimi in ("SEURANTA_TUNNUS", "GITHUB_TOKEN"):
        arvo = (ymparisto.get(nimi) or "").strip()
        if arvo:
            return arvo
    if tiedosto:
        try:
            return Path(tiedosto).read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


# ---------------------------------------------------------------- 1. tulokset

def lue_tulos(polku):
    """{'nimi', 'tila', 'aika', 'syy'} tulostiedostosta. Puuttuva kentta on None."""
    teksti = polku.read_text(encoding="utf-8", errors="replace")
    tila = re.search(r"^- Tila: *(\S+)", teksti, re.MULTILINE)
    aika = re.search(r"^- Aika: *(\S+)", teksti, re.MULTILINE)
    syy = re.search(r"subtype: *([A-Za-z0-9_]+)", teksti) or re.search(r"koodina `([A-Za-z0-9_]+)`", teksti)
    return {"nimi": polku.stem, "tila": tila.group(1) if tila else None, "aika": aika.group(1) if aika else None,
            "syy": syy.group(1) if syy else None}


def jasenna_aika(teksti):
    try:
        t = datetime.datetime.fromisoformat(teksti)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def osio_tulokset(juuri, nyt, tunnit):
    kansio = Path(juuri) / "claude-jono" / "tulokset"
    rivit = ["1. Tulokset viimeisen %d h ajalta (EPAONNISTUI tai KESKEN)" % tunnit]
    if not kansio.is_dir():
        return rivit + ["   %s: kansiota claude-jono/tulokset ei voitu lukea" % EI_TIETOA], False
    raja = nyt - datetime.timedelta(hours=tunnit)
    osumia, tuntematon, ikkunassa = [], [], 0
    for p in sorted(kansio.glob("*.md")):
        try:
            t = lue_tulos(p)
        except OSError:
            tuntematon.append(p.stem)
            continue
        aika = jasenna_aika(t["aika"])
        if aika is None:
            tuntematon.append(t["nimi"])        # aikaa ei voi lukea: ei vaiteta, kerrotaan
            continue
        if aika < raja:
            continue
        ikkunassa += 1
        if t["tila"] in ("EPAONNISTUI", "KESKEN"):
            osumia.append(t)
    for t in osumia:
        syy = t["syy"] or "ei tuloksessa"
        ehdotus = "pilko tehtava pienempiin osiin" if t["syy"] == "error_max_turns" else "lue tulos ja ratkaise syy ennen uudelleenajoa"
        rivit.append("   %s %s: syy: %s -> %s" % (t["tila"], t["nimi"], syy, ehdotus))
    if not osumia:
        rivit.append("   Ei yhtaan (%d tulosta ikkunassa)." % ikkunassa)
    ok = True
    if tuntematon:
        ok = False
        rivit.append("   %s: %d tuloksen aikaa tai tiedostoa ei voitu lukea: %s" % (EI_TIETOA, len(tuntematon), ", ".join(tuntematon[:5])))
    return rivit, ok


# ---------------------------------------------------------------- 2.-3. PR:t

def lataa_automerge():
    """tools/automerge.py samasta hakemistosta (rajattujen polkujen luokittelu) tai None."""
    polku = Path(__file__).resolve().parent / "automerge.py"
    try:
        spec = importlib.util.spec_from_file_location("seuranta_automerge", polku)
        moduuli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(moduuli)
        return moduuli
    except Exception:
        return None


def tarkastajan_paatos(kommentit, sha):
    """(paatos, kommentin runko) tarkastajan viimeisimmasta paatoksesta TALLE karjelle, muuten (None, None).
    Vanhan karjen paatosta ei kayteta. Tasan yksi merkki per kommentti ja kirjoittajana botti (kuten automerge.py)."""
    tulos = (None, None)
    for k in kommentit:
        if ((k.get("user") or {}).get("login") or "") != BOTTI:
            continue
        runko = k.get("body") or ""
        merkit = TARKASTUS_RE.findall(runko)
        if len(merkit) == 1 and merkit[0][0] == sha:
            tulos = (merkit[0][1], runko)
    return tulos


def loydokset(runko):
    """Katselmoinnin loydokset: rivit, jotka alkavat merkeilla '- '."""
    return [r.strip()[2:].strip() for r in (runko or "").splitlines() if r.startswith("- ") and r[2:].strip()]


def ci_tila(gh, repo, sha):
    """'vihrea', 'punainen', 'kesken', 'ei tarkistuksia' tai EI TIETOA. Lukee check-runit ja yhdistetyn tilan."""
    try:
        ajot = gh.get("/repos/%s/commits/%s/check-runs?per_page=100" % (repo, sha)).get("check_runs", [])
        yhd = gh.get("/repos/%s/commits/%s/status" % (repo, sha))
    except (Lukuvirhe, AttributeError) as e:
        return "%s (%s)" % (EI_TIETOA, e if isinstance(e, Lukuvirhe) else "odottamaton vastaus")
    punaiset = ("failure", "cancelled", "timed_out", "action_required", "startup_failure")
    if any(a.get("conclusion") in punaiset for a in ajot) or yhd.get("state") in ("failure", "error"):
        return "punainen"
    if any(a.get("status") != "completed" for a in ajot) or yhd.get("state") == "pending" and yhd.get("statuses"):
        return "kesken"
    if not ajot and not yhd.get("statuses"):
        return "ei tarkistuksia"
    return "vihrea"


def yhdistettavyys(pr):
    tila = pr.get("mergeable_state")
    if tila is None or tila == "unknown":
        return EI_TIETOA + " (GitHub ei ole laskenut viela)"
    if tila == "dirty":
        return "RISTIRIITA (dirty)"
    return tila


def pr_tiedot(gh, repo, listaus, automerge):
    """Yhden PR:n tiedot ja virheet. Ei nosta poikkeusta: lukematon kentta saa arvon EI_TIETOA."""
    nro = listaus["number"]
    sha = (listaus.get("head") or {}).get("sha") or ""
    t = {"repo": repo, "nro": nro, "sha": sha, "otsikko": listaus.get("title") or "", "kuvaus": listaus.get("body") or "",
         "tiedostot": None, "osittainen": False}
    try:
        t["pr"] = gh.get("/repos/%s/pulls/%d" % (repo, nro))
    except Lukuvirhe as e:
        t["pr"] = {}
        t["pr_virhe"] = str(e)
    try:
        kommentit = gh.sivut("/repos/%s/issues/%d/comments" % (repo, nro))
        t["paatos"], runko = tarkastajan_paatos(kommentit, sha)
        t["loydokset"] = loydokset(runko) if runko else []
        t["vanhat"] = sorted({m[0][:7] + " " + m[1] for k in kommentit
                              if ((k.get("user") or {}).get("login") or "") == BOTTI
                              for m in TARKASTUS_RE.findall(k.get("body") or "") if m[0] != sha})
    except Lukuvirhe as e:
        t["paatos"], t["loydokset"], t["vanhat"], t["paatos_virhe"] = None, [], [], str(e)
    try:
        tiedostot = gh.sivut("/repos/%s/pulls/%d/files" % (repo, nro))
        t["tiedostot"] = tiedostot
        t["osittainen"] = any(("patch" not in f and f.get("status") != "removed" and (f.get("changes") or 0) > 0) for f in tiedostot)
    except Lukuvirhe as e:
        t["tiedostot_virhe"] = str(e)
    t["ci"] = ci_tila(gh, repo, sha) if sha else EI_TIETOA
    t["rajattu"] = t.get("paatos") == "RAJATTU"
    if automerge and t["tiedostot"]:
        nimi = repo.split("/", 1)[1]
        for f in t["tiedostot"]:
            if automerge.rajattu(nimi, f.get("filename")) or automerge.rajattu(nimi, f.get("previous_filename") or "x"):
                t["rajattu"] = True
    return t


def osio_prt(tiedot, yritetty, taysi):
    rivit = ["2. Avoimet pull request -pyynnot (rajatut ensin)"]
    if not yritetty:
        return rivit + ["   %s: GitHubia ei luettu" % EI_TIETOA], False
    ok = taysi
    if not taysi:
        rivit.append("   %s: repolista tai pyyntolista jai vajaaksi, alla vain luetut" % EI_TIETOA)
    if not tiedot:
        return rivit + ["   Ei avoimia pyyntoja luetuissa repoissa."], ok
    for t in sorted(tiedot, key=lambda x: (not x["rajattu"], x["repo"], x["nro"])):
        if t["paatos"]:
            paatos = "%s (nykykarjesta)" % t["paatos"]
        elif "paatos_virhe" in t:
            paatos, ok = "%s (%s)" % (EI_TIETOA, t["paatos_virhe"]), False
        else:
            paatos = "ei paatosta nykykarjesta"
            if t["vanhat"]:
                paatos += " (vain vanhoista karjista: " + ", ".join(t["vanhat"][:3]) + ")"
        yhd = yhdistettavyys(t["pr"]) if t["pr"] else "%s (%s)" % (EI_TIETOA, t.get("pr_virhe", "?"))
        if EI_TIETOA in yhd or EI_TIETOA in t["ci"]:
            ok = False
        rivit.append("   %s%s #%d %s karki %s | CI %s | tarkastaja: %s | tila: %s" % (
            "[RAJATTU] " if t["rajattu"] else "", t["repo"], t["nro"], peita(t["otsikko"][:60].replace("\n", " ")),
            t["sha"][:7] or "?", t["ci"], paatos, yhd))
        for l in t["loydokset"][:LOYDOKSIA_MAX]:
            rivit.append("       - " + peita(l[:160]))
        if len(t["loydokset"]) > LOYDOKSIA_MAX:
            rivit.append("       ... ja %d muuta loydosta kommentissa" % (len(t["loydokset"]) - LOYDOKSIA_MAX))
    return rivit, ok


def lue_osoitteet(juuri):
    """(osoitteet, virhe). Osoitteet luetaan 01-infra.md:sta eika niita tulosteta."""
    try:
        teksti = (Path(juuri) / "01-infra.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set(), "01-infra.md:ta ei voitu lukea"
    loydetyt = {m.group(0).lower() for m in TAILNET_RE.finditer(teksti)}
    if not loydetyt:
        return set(), "01-infra.md:sta ei loytynyt tailnet-osoitetta"
    return loydetyt, None


def osumia_pr(t, osoitteet):
    """Montako kenttaa (otsikko, kuvaus, lisatty tiedosto) sisaltaa jonkin osoitteen. Lisattyja rivej tarkastellaan."""
    teksti = [t["otsikko"], t["kuvaus"]]
    for f in t["tiedostot"] or []:
        teksti.extend(r[1:] for r in (f.get("patch") or "").splitlines() if r.startswith("+"))
        teksti.append(f.get("filename") or "")
    return sum(1 for rivi in teksti if any(o in rivi.lower() for o in osoitteet))


def osio_skannaus(tiedot, julkiset, osoitteet, osoite_virhe, yritetty, taysi):
    rivit = ["3. Tailnet-osoitteen skannaus julkisten repojen avoimista pyynnoista (osoitetta ei tulosteta)"]
    if not yritetty:
        return rivit + ["   %s: GitHubia ei luettu, skannausta ei tehty" % EI_TIETOA], False, 0
    if osoite_virhe:
        return rivit + ["   %s: %s, skannausta ei tehty" % (EI_TIETOA, osoite_virhe)], False, 0
    osumat, vajaa = [], []
    skannattu = 0
    for t in tiedot:
        if t["repo"] not in julkiset:
            continue
        if t["tiedostot"] is None:
            vajaa.append("%s #%d" % (t["repo"], t["nro"]))
            continue
        skannattu += 1
        if t["osittainen"]:
            vajaa.append("%s #%d" % (t["repo"], t["nro"]))
        n = osumia_pr(t, osoitteet)
        if n:
            osumat.append((t, n))
    ok = not vajaa and taysi
    if not taysi:
        rivit.append("   %s: repo- tai pyyntolista jai vajaaksi, kaikkia julkisia pyyntoja ei skannattu" % EI_TIETOA)
    for t, n in osumat:
        rivit.append("   OSUMIA %d, %s #%d" % (n, t["repo"], t["nro"]))
    if not osumat:
        if ok:
            rivit.append("   Osumia 0 (%d julkisen repon pyyntoa skannattu)." % skannattu)
        else:
            rivit.append("   Luetuissa ei osumia (%d pyyntoa), mutta tulosta ei ole todennettu." % skannattu)
    if vajaa:
        rivit.append("   %s: skannaus vajaa (diff puuttuu tai on liian iso): %s" % (EI_TIETOA, ", ".join(vajaa[:8])))
    return rivit, ok, len(osumat)


# ---------------------------------------------------------------- 4.-5. tiedostot

def osio_tonylle(juuri):
    rivit = ["4. tonylle.md: avoimet kohdat"]
    try:
        teksti = (Path(juuri) / "claude-jono" / "tonylle.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rivit + ["   %s: tonylle.md:ta ei voitu lukea" % EI_TIETOA], False
    n = len(re.findall(r"^\d+\. ", teksti, re.MULTILINE))
    rivit.append("   %d numeroitua kohtaa (raja %d)%s" % (n, TONYLLE_RAJA, " - YLI RAJAN, arkistoi hoidetut" if n > TONYLLE_RAJA else ""))
    return rivit, True


def osio_kiire(juuri):
    rivit = ["5. KIIRE-tehtavat"]
    tehtavat = Path(juuri) / "claude-jono" / "tehtavat"
    tulokset = Path(juuri) / "claude-jono" / "tulokset"
    if not tehtavat.is_dir() or not tulokset.is_dir():
        return rivit + ["   %s: tehtavat- tai tulokset-kansiota ei voitu lukea" % EI_TIETOA], False
    kiireet = [p for p in sorted(tehtavat.glob("*.md")) if "kiire" in p.name.lower()]
    jonossa, ajettu = [], 0
    for p in kiireet:
        if (tulokset / p.name).is_file():
            ajettu += 1
        else:
            jonossa.append(p.stem)
    for nimi in jonossa:
        rivit.append("   JONOSSA " + nimi)
    rivit.append("   Ajettu %d, jonossa %d (KIIRE-tehtavia yhteensa %d)." % (ajettu, len(jonossa), len(kiireet)))
    return rivit, True


# ---------------------------------------------------------------- paaohjelma

def hae_prt(gh, omistaja):
    """(PR-tiedot, julkisten repojen nimet, ok). Repolista: omistajan ei-forkit, ei-arkistoidut."""
    try:
        repot = gh.sivut("/users/%s/repos?type=owner" % omistaja)
    except Lukuvirhe:
        return [], set(), False
    repot = [r for r in repot if not r.get("fork") and not r.get("archived")]
    julkiset = {r["full_name"] for r in repot if not r.get("private")}
    automerge = lataa_automerge()
    tiedot, ok = [], not gh.osittainen
    for r in repot:
        try:
            auki = gh.sivut("/repos/%s/pulls?state=open" % r["full_name"])
        except Lukuvirhe:
            ok = False
            continue
        for pr in auki:
            tiedot.append(pr_tiedot(gh, r["full_name"], pr, automerge))
    return tiedot, julkiset, ok


def aja(argv=None, ymparisto=None, nyt=None, hae=None, ulos=None):
    ap = argparse.ArgumentParser(prog="seuranta.py", description="Tilanne lahteista.")
    ap.add_argument("--juuri", required=True, help="docs-repon juuri")
    ap.add_argument("--ilman-github", action="store_true", help="vain tiedostolahteet")
    ap.add_argument("--tunnus-tiedosto", default=OLETUS_TUNNUS_TIEDOSTO)
    ap.add_argument("--tunnit", type=int, default=24)
    ap.add_argument("--omistaja", default=OLETUS_OMISTAJA)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 1
    ulos = ulos or sys.stdout
    ymparisto = os.environ if ymparisto is None else ymparisto
    nyt = nyt or datetime.datetime.now(datetime.timezone.utc)
    if not Path(a.juuri).is_dir() or a.tunnit < 1:
        print("kaytto: --juuri pitaa olla hakemisto ja --tunnit >= 1", file=sys.stderr)
        return 1

    osiot, kaikki_ok, osumat = [], True, 0
    r, ok = osio_tulokset(a.juuri, nyt, a.tunnit)
    osiot.append(r)
    kaikki_ok &= ok

    yritetty, taysi, tiedot, julkiset, otsikko = False, False, [], set(), "Lahde: GitHub ei luettu (--ilman-github)"
    if not a.ilman_github:
        yritetty = True
        tunnus = lue_tunnus(ymparisto, a.tunnus_tiedosto)
        gh = GitHub(tunnus, hae)
        tiedot, julkiset, taysi = hae_prt(gh, a.omistaja)
        otsikko = "Lahde: GitHub %s%s" % ("lukutunnuksella" if tunnus else "ILMAN tunnusta (kutsuraja 60/h)",
                                           "" if taysi else " - luku jai vajaaksi")
    r, ok = osio_prt(tiedot, yritetty, taysi)
    osiot.append(r)
    kaikki_ok &= ok
    osoitteet, osoite_virhe = lue_osoitteet(a.juuri)
    r, ok, osumat = osio_skannaus(tiedot, julkiset, osoitteet, osoite_virhe, yritetty, taysi)
    osiot.append(r)
    kaikki_ok &= ok
    for osio in (osio_tonylle(a.juuri), osio_kiire(a.juuri)):
        osiot.append(osio[0])
        kaikki_ok &= osio[1]

    print("Seuranta %s" % nyt.strftime("%Y-%m-%d %H:%M UTC"), file=ulos)
    print(otsikko, file=ulos)
    for osio in osiot:
        print("\n".join(osio), file=ulos)
    if osumat:
        print("\nVAKAVA: tailnet-osoite julkisessa pyynnossa. Korjaa ensin ja kerro Tonylle heti.", file=ulos)
        return 3
    if not kaikki_ok:
        print("\nHUOM: jokin lahde jai lukematta (%s). Ala kirjoita \"vakavaa ei ole\"; hanki lahde ja aja uudelleen." % EI_TIETOA,
              file=ulos)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(aja())
