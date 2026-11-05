"""Kysyntamittari: viikon luvut Tonyn julkisista tyokaluista (valitavoite 5, Tonyn paatos 2.11.2026).

Laskee npm-lataukset seka GitHubin tahdet, forkit, issuet ja PR:t siten, etta omat tunnukset ja botit eivat kasvata
ulkoisia lukuja (botin oma forkki ei ole kysyntaa). Epaonnistunut haku on "?", ei koskaan 0: tuntematon ei saa
nayttaa silta, ettei kukaan kayta. Yhteydenotot Tony kertoo itse, niita ei lueta mistaan.

Kaytto:
    python3 -m agentti.kysynta [--tila polku | --ei-tilaa] [--halyta [--kuiva]]
Tulostaa markdown-taulukon. Paivan luvut tallentuvat tilatiedostoon (oletus TILA_OLETUS, --tila vaihtaa, --ei-tilaa jattaa
tallentamatta) ja taulukko nayttaa muutoksen edelliseen eri paivan mittaukseen. Tahdet ovat repon stargazers_count eli
kokonaismaara (omat mukana); forkit, issuet ja PR:t ovat ulkoisia.
--halyta: tilaan tallennetaan ulkoisten issueiden ja PR:ien numerot, ja uudet (edellisen onnistuneen haun jalkeen ilmestyneet)
ilmoitetaan agentti.ilmoita-moduulilla. Ensimmainen ajo ilman tilaa kirjaa lahtotason eika halyta. Epaonnistunut haku ei ole
uusi eika tyhjenna tilaa. Jos ilmoitus ei lahde, uudet jaavat nakematta ja halytys toistuu seuraavalla ajolla. --kuiva tulostaa
halytyksen lahettamatta eika kirjoita tilaa. Haku nakee uusimmat SIVUKOKO issuea/PR:aa; harvempi ajo kuin kerran paivassa
voi ohittaa tulvan alle jaaneet.
Vain luku, ei tunnuksia, vain stdlib. GitHubin kirjautumaton raja on 60 pyyntoa tunnissa. Pyyntoja kuluu koosteilla:
1 repolista (tahdet ja forkkien maara kaikille repoille), 1 hakupyynto (kaikki ulkopuoliset issuet ja PR:t kaikissa repoissa)
ja 1 forkkilista vain niille repoille, joilla on forkkeja. Repomaarasta riippumaton osa on siis 2 pyyntoa. Sama osoite haetaan
yhden ajon aikana vain kerran. Jaljella oleva raja luetaan X-RateLimit-Remaining-otsakkeesta: kun jaljella on korkeintaan
VARARAJA pyyntoa, loput haut jatetaan tekematta (RajaLoppu) ja luvut ovat "?".
Virheista nakyy vain tyypin nimi ja HTTP-tila."""
import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .pr_kutsu import REPOT

# Kaikki kysyntamittarin oletustiedostot (taman moduulin ja kysynta_ajon) ovat yhdessa hakemistossa. claudejono saa kirjoittaa
# tanne; /var/lib/claude-jono ei. Isanta voi siirtaa koko hakemiston ymparistomuuttujalla KYSYNTA_TILAHAKEMISTO.
TILAHAKEMISTO = os.environ.get("KYSYNTA_TILAHAKEMISTO") or "/opt/agentscope/tyo/kysynta-tila"
TILA_OLETUS = os.path.join(TILAHAKEMISTO, "kysynta.json")
OMISTAJA = "tonyweckstrom1987"
OMAT_TUNNUKSET = {OMISTAJA, "agenttifirma-bot"}
NPM_PAKETIT = {
    "mcp-doctor": "@tonyweckstrom/mcp-doctor",
    "mcp-model-router": "mcp-model-router",
    "evalkit": "@tonyweckstrom/evalkit",
    "llm-cassette": "@tonyweckstrom/llm-cassette",
}
AIKAKATKAISU = 20
SIVUKOKO = 100
GITHUB = "https://api.github.com/"
REPOLISTA_URL = GITHUB + "users/%s/repos?type=owner&per_page=%d" % (OMISTAJA, SIVUKOKO)
# Kaikki muiden kuin Tonyn ja botin avaamat issuet ja PR:t Tonyn repoissa yhdella pyynnolla (hakurajapinnalla on oma raja).
HAKU_URL = GITHUB + "search/issues?" + urllib.parse.urlencode(
    {"q": "user:%s -author:%s -author:agenttifirma-bot" % (OMISTAJA, OMISTAJA), "sort": "created", "order": "desc", "per_page": SIVUKOKO})
VARARAJA = 5    # GitHub-pyyntoja jatetaan tekematta, kun jaljella on korkeintaan tama monta
HISTORIA_MAX = 120
UA = "agenttifirma-kysynta/1"
SARAKKEET = ("npm_viikko", "tahdet", "forkit", "issuet", "prt")


class RajaLoppu(Exception):
    """GitHubin tuntiraja on lahes taynna: haku jatetaan tekematta, ettei loppu raja kulu eika tulos jaa puolittaiseksi."""


RAJA = {}   # resurssi ("core", "search") -> viimeksi nahty X-RateLimit-Remaining


def _raja_talteen(otsakkeet):
    try:
        RAJA[otsakkeet.get("X-RateLimit-Resource") or "core"] = int(otsakkeet.get("X-RateLimit-Remaining"))
    except (AttributeError, TypeError, ValueError):
        pass


def hae_json(osoite):
    resurssi = None
    if osoite.startswith(GITHUB):
        resurssi = "search" if "/search/" in osoite else "core"
        if RAJA.get(resurssi, VARARAJA + 1) <= VARARAJA:
            raise RajaLoppu()
    pyynto = urllib.request.Request(osoite, headers={"Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(pyynto, timeout=AIKAKATKAISU) as v:
            if resurssi:
                _raja_talteen(v.headers)
            return json.loads(v.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if resurssi:
            _raja_talteen(e.headers)
        raise


def muistiin(hae):
    """Sama osoite haetaan yhden ajon aikana vain kerran (viikkoluvut ja halytys jakavat repolistan ja haun). Virheita ei muisteta."""
    if getattr(hae, "muistissa", False):
        return hae
    muisti = {}

    def muistava(osoite):
        if osoite not in muisti:
            muisti[osoite] = hae(osoite)
        return muisti[osoite]
    muistava.muistissa = True
    return muistava


def _virhe(e):
    """Virheen tyyppi ja mahdollinen HTTP-tila (esim. "HTTPError 401"). Vastauksen sisaltoa tai osoitetta ei oteta mukaan."""
    koodi = getattr(e, "code", None) if isinstance(e, urllib.error.HTTPError) else None
    return type(e).__name__ + (" %d" % koodi if isinstance(koodi, int) else "")


def ulkoinen(kayttaja):
    """Onko GitHub-kayttaja joku muu kuin Tony, hanen bottinsa tai mika tahansa automaatio."""
    if not isinstance(kayttaja, dict) or not kayttaja.get("login"):
        return False
    tunnus = kayttaja["login"]
    return kayttaja.get("type") != "Bot" and not tunnus.endswith("[bot]") and tunnus.lower() not in {o.lower() for o in OMAT_TUNNUKSET}


def _laske_ulkoiset(lista):
    """Taysi sivu tarkoittaa, etta rivit saattoivat jaada lukematta: luku on silloin alaraja, joka merkitaan +:lla."""
    maara = sum(1 for k in lista if ulkoinen(k))
    return "%d+" % maara if len(lista) >= SIVUKOKO else maara


def repolista(hae=hae_json):
    """Tonyn julkiset repot yhdella pyynnolla: {nimi: rivi}. Rivilla on tahdet, forkkien maara ja fork/archived-liput."""
    return {r["name"]: r for r in hae(REPOLISTA_URL) if isinstance(r, dict) and isinstance(r.get("name"), str)}


def ulkoiset_kohteet(hae=hae_json):
    """Kaikki ulkopuoliset issuet ja PR:t yhdella hakupyynnolla. Palauttaa (lista, taysi). Listan alkio on
    {"repo", "numero", "laji" ("issue" tai "pr"), "tunnus"}; taysi tarkoittaa, etta osa tuloksista jai saamatta (luvut ovat alarajoja).
    Otsikoita tai tekstia ei oteta mukaan."""
    vastaus = hae(HAKU_URL)
    rivit = vastaus["items"]
    kokonais = vastaus.get("total_count")
    taysi = bool(vastaus.get("incomplete_results")) or (kokonais > len(rivit) if type(kokonais) is int else len(rivit) >= SIVUKOKO)
    tulos = []
    for r in rivit:
        if not isinstance(r, dict) or not ulkoinen(r.get("user")) or type(r.get("number")) is not int:
            continue
        osat = str(r.get("repository_url", "")).rstrip("/").split("/")
        if len(osat) < 2 or osat[-2].lower() != OMISTAJA.lower() or not osat[-1]:
            continue
        tulos.append({"repo": osat[-1], "numero": r["number"], "laji": "pr" if "pull_request" in r else "issue", "tunnus": r["user"]["login"]})
    return tulos, taysi


def _int(arvo):
    if isinstance(arvo, bool) or not isinstance(arvo, int):
        raise ValueError("ei lukua")
    return arvo


def mittaa_github(repot, hae=hae_json):
    """Repojen GitHub-luvut koosteilla (ks. moduulin ohje). Palauttaa (luvut repoittain, virherivit repoittain {repo: [..]}).
    Kukin luku on omassa try-lohkossaan, joten yksi epaonnistunut haku ei vie muita."""
    hae = muistiin(hae)
    virheet = {r: [] for r in repot}
    try:
        rivit = repolista(hae)
    except Exception as e:
        rivit, lista_virhe = {}, _virhe(e)
    else:
        lista_virhe = None
    try:
        kohteet, taysi = ulkoiset_kohteet(hae)
        haku_virhe = None
    except Exception as e:
        kohteet, taysi, haku_virhe = [], False, _virhe(e)
    tulos = {}
    raja_loppu = isinstance(haku_virhe, str) and haku_virhe == RajaLoppu.__name__   # raja tuli vastaan: ei yritysta enaa
    for repo in repot:
        luvut = {"tahdet": "?", "forkit": "?", "issuet": "?", "prt": "?"}
        rivi = rivit.get(repo)
        if lista_virhe or rivi is None:   # jaettu haku kaatui tai repoa ei ole listalla: ei yhtaan lukua repolistasta
            syy = lista_virhe or "ei repolistassa"
            virheet[repo] += ["tahdet: " + syy, "forkit: " + syy]
        else:
            # Repon oma stargazers_count-kentta: stargazers-osoite vaatii kirjautumisen (401). Luku on kokonaismaara, omat tahdet mukana.
            try:
                luvut["tahdet"] = _int(rivi.get("stargazers_count"))
            except Exception as e:
                virheet[repo].append("tahdet: " + _virhe(e))
            try:
                if _int(rivi.get("forks_count")) == 0:
                    luvut["forkit"] = 0
                elif raja_loppu:
                    raise RajaLoppu()
                else:   # forkkilista vain, kun forkkeja on: botin oma forkki ei ole kysyntaa, joten kokonaismaara ei kelpaa sellaisenaan
                    luvut["forkit"] = _laske_ulkoiset([f.get("owner") for f in hae("%srepos/%s/%s/forks?per_page=%d" % (GITHUB, OMISTAJA, repo, SIVUKOKO))])
            except Exception as e:
                raja_loppu = raja_loppu or isinstance(e, RajaLoppu)
                virheet[repo].append("forkit: " + _virhe(e))
        if haku_virhe:
            virheet[repo].append("issuet: " + haku_virhe)
        else:
            numerot = {}
            for avain, laji in (("issuet", "issue"), ("prt", "pr")):
                nrot = sorted(k["numero"] for k in kohteet if k["repo"] == repo and k["laji"] == laji)
                luvut[avain] = "%d+" % len(nrot) if taysi else len(nrot)
                numerot[avain] = nrot
            luvut["numerot"] = numerot  # vain onnistuneesta haustasta: puuttuva avain tarkoittaa "ei tietoa", ei "ei mitaan"
        tulos[repo] = luvut
    return tulos, virheet


def npm_viikko(paketti, hae=hae_json):
    """Viimeisen viikon lataukset. Julkaisematon paketti tai virhe: "?". Palauttaa (luku, virhe tai None)."""
    if paketti is None:
        return "-", None
    try:
        vastaus = hae("https://api.npmjs.org/downloads/point/last-week/" + paketti)
        luku = vastaus.get("downloads")
        if isinstance(luku, bool) or not isinstance(luku, int):
            return "?", "npm: ei lukua"
        return luku, None
    except Exception as e:
        return "?", "npm: " + _virhe(e)


def mittaa(repot=REPOT, hae=hae_json):
    """Palauttaa (luvut repoittain, virherivit). Virherivilla on vain virheen tyyppi."""
    hae = muistiin(hae)
    tulos, repon_virheet = mittaa_github(repot, hae)
    virheet = []
    for repo in repot:
        luku, virhe = npm_viikko(NPM_PAKETIT.get(repo), hae)
        tulos[repo]["npm_viikko"] = luku
        virheet += ["%s %s" % (repo, v) for v in repon_virheet[repo] + ([virhe] if virhe else [])]
    return tulos, virheet


def _muutos(nyt, ennen):
    """Muutos vain kahden tunnetun, tarkan luvun valilla; "?" ja alarajat ("5+") eivat anna muutosta."""
    if type(nyt) is int and type(ennen) is int and nyt != ennen:
        return " (%+d)" % (nyt - ennen)
    return ""


def muotoile(luvut, virheet, edellinen=None, edellinen_pvm=None):
    rivit = ["# Kysynt\u00e4mittari", "", "Ulkoiset luvut: omat tunnukset ja botit eiv\u00e4t ole mukana. ? = haku ep\u00e4onnistui. - = ei npm-pakettia. Luku + = v\u00e4hint\u00e4\u00e4n."]
    if edellinen_pvm:
        rivit.append("Suluissa muutos mittaukseen %s." % edellinen_pvm)
    rivit += ["", "| Repo | npm/vko | T\u00e4hdet | Forkit | Issuet | PR:t |", "|---|---|---|---|---|---|"]
    edellinen = edellinen or {}
    for repo in sorted(luvut):
        vanha = edellinen.get(repo, {})
        solut = ["%s%s" % (luvut[repo].get(s, "?"), _muutos(luvut[repo].get(s), vanha.get(s))) for s in SARAKKEET]
        rivit.append("| %s | %s |" % (repo, " | ".join(solut)))
    rivit += ["", "npm laskee my\u00f6s CI-ajot ja peilit, joten lataus ei ole sama kuin k\u00e4ytt\u00e4j\u00e4."]
    if virheet:
        rivit += ["", "## Ep\u00e4onnistuneet haut"] + ["- " + v for v in virheet]
    return "\n".join(rivit) + "\n"


def viimeiset_numerot(tila):
    """Repoittain viimeisin tunnettu {issuet: [...], prt: [...]} koko historiasta (myos tanaan tallennettu)."""
    tulos = {}
    for pvm in sorted(tila):
        rivit = tila[pvm] if isinstance(tila[pvm], dict) else {}
        for repo, luvut in rivit.items():
            n = luvut.get("numerot") if isinstance(luvut, dict) else None
            if isinstance(n, dict):
                tulos[repo] = n
    return tulos


def _numerolista(n, laji):
    lista = n.get(laji) if isinstance(n, dict) else None
    return lista if isinstance(lista, list) else None


def uudet_ulkoiset(edellinen, luvut):
    """Vertaa tilaan tallennettuja numeroita uuteen hakuun. Palauttaa jarjestetyn listan (repo, laji, numero), laji "issue" tai "pr".
    Repo, jolla ei ole aiempaa tallennettua numerolistaa (ensimmainen ajo, aiempi haku epaonnistui) kirjataan lahtotasoksi eika halyta.
    Epaonnistunut haku (ei "numerot"-avainta) ei tuota mitaan."""
    uudet = []
    for repo in sorted(luvut):
        nyt = luvut[repo].get("numerot")
        for avain, laji in (("issuet", "issue"), ("prt", "pr")):
            vanha, uusi = _numerolista((edellinen or {}).get(repo), avain), _numerolista(nyt, avain)
            if vanha is None or uusi is None:
                continue
            uudet += [(repo, laji, n) for n in uusi if n not in vanha]
    return uudet


def halytysviesti(uudet, enint=10):
    """(otsikko, teksti) ilmoitukselle. Vain repo, laji ja numero seka niista johdettu osoite: otsikoita ja kirjoittajien tekstia ei kopioida."""
    rivit = ["%s %s #%d https://github.com/%s/%s/%s/%d" % (r, "issue" if l == "issue" else "PR", n, OMISTAJA, r,
                                                          "issues" if l == "issue" else "pull", n) for r, l, n in uudet[:enint]]
    if len(uudet) > enint:
        rivit.append("... ja %d muuta" % (len(uudet) - enint))
    return "Ulkopuolinen avasi %d uutta issuea tai PR:aa" % len(uudet), "\n".join(rivit)


def lue_tila(polku):
    try:
        data = json.loads(Path(polku).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def kirjoita_tila(polku, tila, pvm, luvut):
    """Saman paivan uusi ajo korvaa mittauksen. Vanhimmat karsitaan. Kirjoitus on atominen, ettei kaatuminen riko historiaa."""
    uusi = dict(tila)
    uusi[pvm] = luvut
    for vanha in sorted(uusi)[:-HISTORIA_MAX]:
        del uusi[vanha]
    kohde = Path(polku)
    kohde.parent.mkdir(parents=True, exist_ok=True)
    kahva, tmp = tempfile.mkstemp(dir=str(kohde.parent), prefix=".kysynta-")
    try:
        with os.fdopen(kahva, "w", encoding="utf-8") as f:
            json.dump(uusi, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, kohde)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv=None, hae=hae_json, tanaan=None, laheta=None):
    ap = argparse.ArgumentParser(prog="agentti.kysynta")
    ap.add_argument("--tila", default=TILA_OLETUS, help="JSON-historia, johon paivan luvut tallennetaan (oletus %s)" % TILA_OLETUS)
    ap.add_argument("--ei-tilaa", action="store_true", help="ala lue tai kirjoita tilaa")
    ap.add_argument("--halyta", action="store_true", help="ilmoita uudet ulkopuoliset issuet ja PR:t (agentti.ilmoita); vaatii tilan")
    ap.add_argument("--kuiva", action="store_true", help="vain --halyta: tulosta halytys, ala laheta ala kirjoita tilaa")
    a = ap.parse_args(argv)
    if a.halyta and a.ei_tilaa:
        ap.error("--halyta tarvitsee tilan (ei yhdessa --ei-tilaa kanssa)")
    if a.kuiva and not a.halyta:
        ap.error("--kuiva kuuluu --halyta-lipun kanssa")
    pvm = tanaan or time.strftime("%Y-%m-%d")
    luvut, virheet = mittaa(hae=hae)
    tila = {} if a.ei_tilaa else lue_tila(a.tila)
    aiemmat = sorted(p for p in tila if p < pvm)
    edellinen_pvm = aiemmat[-1] if aiemmat else None
    print(muotoile(luvut, virheet, tila.get(edellinen_pvm), edellinen_pvm), end="")
    tallenna = dict(luvut)
    if a.halyta:
        edellinen = viimeiset_numerot(tila)
        uudet = uudet_ulkoiset(edellinen, luvut)
        if uudet:
            otsikko, teksti = halytysviesti(uudet)
            if a.kuiva:
                print("\n## Halytys (kuiva-ajo, ei lahetetty)\n%s\n%s" % (otsikko, teksti))
            else:
                if laheta is None:
                    from .ilmoita import ilmoita as laheta
                tulos = laheta("paatos", otsikko, teksti)
                print("\nHalytys: %d uutta, ilmoitus %s" % (len(uudet), tulos))
                if tulos != "lahetetty":
                    # Ei kirjata nahdyksi: seuraava ajo yrittaa uudelleen.
                    for repo in {r for r, _, _ in uudet}:
                        tallenna[repo] = {a_: b for a_, b in luvut[repo].items() if a_ != "numerot"}
        # Epaonnistunut tai poisjaanyt haku ei tyhjenna tilaa: edellinen tunnettu numerolista siirtyy tanaan tallennettavaan.
        for repo in tallenna:
            if "numerot" not in tallenna[repo] and repo in edellinen:
                tallenna[repo] = dict(tallenna[repo], numerot=edellinen[repo])
    if not a.ei_tilaa and not a.kuiva:
        kirjoita_tila(a.tila, tila, pvm, tallenna)
    # Kaikkien hakujen epaonnistuminen on verkko- tai rajaongelma, ei tyhja tulos: exit 1 nakyy valvonnassa.
    tunnetut = any(v not in ("?", "-") for rivi in luvut.values() for v in rivi.values())
    return 0 if tunnetut else 1


if __name__ == "__main__":
    sys.exit(main())
