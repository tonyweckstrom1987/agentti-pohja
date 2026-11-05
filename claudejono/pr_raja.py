"""PR-raja suunnittelijalle (Tonyn ehdotus 5, A4: enintaan 10 keskenerainta).

Suunnittelija laskee avoimet PR:t repoittain. Kun repossa on yli RAJA avointa PR:aa, suunnittelija ei kirjoita siihen uusia
kehitystehtavia vaan purkutehtavia: konfliktissa olevan PR:n rebase mainin paalle, vanhentuneiden sulkuehdotus Tonylle
(ei sulkemista itse) ja tarkastajalle menevien jarjestys.

Tama moduuli vain lukee (julkinen GitHub-API ilman tunnusta) ja kirjoittaa tehtavatekstia. Se ei muuta PR:ia, katto- eika
oikeusarvoja eika pr_kutsun rajoja. Lukuvirhe ei estaa suunnittelua: silloin PR-rajaa ei sovelleta (vain purkutehtavia
tulee lisaa, ei vahemman turvaa).
"""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OMISTAJA = "tonyweckstrom1987"
RAJA = 10                   # yli taman (>= 11) repo menee purkutilaan; tasan 10 on viela sallittu
VANHENTUNUT_PV = 14         # ei paivitetty tahan aikaan -> sulkuehdotus Tonylle
MAX_PURKUTEHTAVIA = 3       # konfliktien rebase-tehtavia enintaan tama per repo per suunnittelukierros
VALIMUISTI = "pr-raja-tila.json"
VALIMUISTI_S = 1800         # julkinen API sallii 60 pyyntoa/h: tulos pidetaan 30 min
PR_REPOT = ("agentti-pohja", "mcp-model-router", "ai-pr-reviewer-action", "llm-stopcheck", "ideapankki", "evalkit", "kauppatiimi",
            "mcp-doctor", "llm-cassette")   # sama lista kuin pr_kutsu.REPOT; tarkistaa test_pr_raja
MAX_TIEDOT = 30             # julkinen API: 60 pyyntoa/h ilman tunnusta; tiedot haetaan vain rajan ylittavista repoista


def _oletushaku(url):
    pyynto = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "claudejono-pr-raja"})
    with urllib.request.urlopen(pyynto, timeout=20) as v:
        return json.loads(v.read().decode("utf-8"))


def _aika(teksti):
    try:
        return datetime.fromisoformat(str(teksti).replace("Z", "+00:00"))
    except ValueError:
        return None


def hae_avoimet(repo, hae=None):
    """Repon avoimet PR:t (listaus, max 100). Lukuvirhe nostaa poikkeuksen; kutsuja paattaa jatkosta."""
    hae = hae or _oletushaku
    data = hae("https://api.github.com/repos/%s/%s/pulls?state=open&per_page=100" % (OMISTAJA, repo))
    if not isinstance(data, list):
        raise ValueError("odottamaton vastaus")
    return [p for p in data if isinstance(p, dict) and isinstance(p.get("number"), int)]


def rikasta(repo, prt, hae=None, enintaan=MAX_TIEDOT):
    """Lisaa jokaiseen PR:aan mergeable_state ja koko (yksittaishaku). Epaonnistunut haku jattaa kentat tyhjiksi."""
    hae = hae or _oletushaku
    ulos = []
    for i, p in enumerate(prt):
        p = dict(p)
        if i < enintaan:
            try:
                d = hae("https://api.github.com/repos/%s/%s/pulls/%d" % (OMISTAJA, repo, p["number"]))
                if isinstance(d, dict):
                    for k in ("mergeable_state", "additions", "deletions", "changed_files"):
                        p[k] = d.get(k)
            except Exception:
                pass
        ulos.append(p)
    return ulos


def luokka(p, nyt):
    """'konflikti' | 'vanhentunut' | 'tarkastajalle'. Konflikti ensin: se estaa tarkastuksen ja yhdistamisen."""
    if p.get("mergeable_state") == "dirty":
        return "konflikti"
    paivitetty = _aika(p.get("updated_at"))
    if paivitetty is not None and nyt - paivitetty > timedelta(days=VANHENTUNUT_PV):
        return "vanhentunut"
    return "tarkastajalle"


def _koko(p):
    return (p.get("additions") or 0) + (p.get("deletions") or 0)


def tarkastusjarjestys(prt):
    """Tarkastajalle menevat: ei-luonnokset ensin, pienin muutos ensin (nopea purku), tasatilanteessa pienin numero."""
    return sorted(prt, key=lambda p: (bool(p.get("draft")), _koko(p), p["number"]))


def tilanne(repo, prt, nyt=None, hae=None):
    """Repon PR-tilanne. 'yli' on tosi vain kun avoimia on enemman kuin RAJA; luokittelu tehdaan vain silloin."""
    nyt = nyt or datetime.now(timezone.utc)
    t = {"repo": repo, "maara": len(prt), "yli": len(prt) > RAJA, "konflikti": [], "vanhentunut": [], "tarkastajalle": []}
    if not t["yli"]:
        return t
    for p in rikasta(repo, prt, hae):
        t[luokka(p, nyt)].append(p)
    t["tarkastajalle"] = tarkastusjarjestys(t["tarkastajalle"])
    return t


def keraa(repot, nyt=None, hae=None):
    """{repo: tilanne} vain rajan ylittaville repoille. Repo, jonka luku epaonnistuu, jaa pois (ei estoa lukuvirheesta)."""
    ulos = {}
    for repo in repot:
        try:
            t = tilanne(repo, hae_avoimet(repo, hae), nyt, hae)
        except Exception:
            continue
        if t["yli"]:
            ulos[repo] = t
    return ulos


def _nimi(p):
    return "#%d %s" % (p["number"], str(p.get("title", ""))[:70])


def purkutehtavat(tilat):
    """Purkutehtavat rajan ylittaville repoille: [{'nimi','sisalto'}]. Nimi sisaltaa PR-numeron, joten sama PR ei toistu."""
    ulos = []
    for repo, t in sorted(tilat.items()):
        for p in t["konflikti"][:MAX_PURKUTEHTAVIA]:
            haara = ((p.get("head") or {}).get("ref")) or "(haara selviaa PR:sta)"
            ulos.append({"nimi": "purku-%s-rebase-pr-%d" % (re.sub(r"[^a-z0-9-]", "-", repo.lower()), p["number"]), "sisalto": (
                "# Purku: rebase PR %s repossa %s\n\n"
                "Pisteet: hyoty 4, varmuus 3, vaiva 1 h\nSuunta: jatkoa (yllapito: PR-raja, repossa %d avointa PR:aa, raja %d)\n\n"
                "Hyoty: konflikti estaa tarkastuksen ja yhdistamisen; Tony saa PR:n takaisin tarkastettavaksi.\n"
                "Mittari: yllapito\n\n"
                "Tavoite: rebase PR:n haara (%s) mainin paalle samaan haaraan ja ratkaise konfliktit. "
                "Jos PR:n haara ei ala yo/-etuliitteella tai se ei ole botin oma, lopeta ja kirjoita raporttiin syy. "
                "Force-with-lease sallitaan vain agentin omaan yo/-haaraan, ei koskaan mainiin eika muiden haaraan. "
                "Jos pr_kutsu ei salli haaran paivitysta, ala kierra sita: kirjoita ehdotus raporttiin ja lopeta. "
                "Ala laajenna PR:n sisaltoa, ala muuta katto- tai oikeusarvoja, ala yhdista mitaan.\n\n"
                "Valmis kun: haara on mainin paalla ja testit menevat lapi, tai raportti kertoo, miksi se ei onnistunut."
                % (_nimi(p), repo, t["maara"], RAJA, haara))})
    return ulos


def tonylle_rivit(tilat):
    """Yksi tiivis rivi per repo: sulkuehdotus (Tony paattaa) ja tarkastusjarjestys. Ei sulje mitaan."""
    rivit = []
    for repo, t in sorted(tilat.items()):
        osat = ["PR-raja: %s %d avointa (raja %d), uusia kehitystehtavia ei kirjoiteta; konfliktissa %d."
                % (repo, t["maara"], RAJA, len(t["konflikti"]))]
        if t["vanhentunut"]:
            osat.append("Sulkuehdotus (ei paivitetty yli %d pv, Tony paattaa): %s."
                        % (VANHENTUNUT_PV, "; ".join(_nimi(p) for p in t["vanhentunut"][:8])))
        if t["tarkastajalle"]:
            osat.append("Tarkastusjarjestys (pienin ensin): %s." % ", ".join("#%d" % p["number"] for p in t["tarkastajalle"][:10]))
        rivit.append(" ".join(osat))
    return "\n".join(rivit)


def kehotelisa(tilat):
    """Teksti suunnittelijan kehotteeseen. Tyhja, jos mikaan repo ei ylita rajaa."""
    if not tilat:
        return ""
    rivit = ["\n\nPR-RAJA (Tonyn ehdotus 5, A4): seuraavissa repoissa on yli %d avointa PR:aa. Ala kirjoita niihin UUSIA kehitystehtavia "
             "(ajuri hylkaa ne). Purkutehtavat konfliktien rebasesta ajuri lisaa itse; sulkuehdotukset ja tarkastusjarjestyksen se kirjaa "
             "kenttaan tonylle. Voit ehdottaa muita repoja koskevia tehtavia normaalisti." % RAJA]
    for repo, t in sorted(tilat.items()):
        rivit.append("- %s: %d avointa, %d konfliktissa, %d vanhentunutta" % (repo, t["maara"], len(t["konflikti"]), len(t["vanhentunut"])))
    return "\n".join(rivit) + "\n"


def suodata_kehitys(tehtavat, tilat):
    """(sallitut, hylatyt): poistaa planerin tehtavat, jotka mainitsevat rajan ylittavan repon. Ajurin omat purkutehtavat lisataan suodatuksen jalkeen, joten ne eivat kulje taman kautta."""
    if not tilat:
        return list(tehtavat), []
    kuviot = {repo: re.compile(r"(?<![\w-])" + re.escape(repo) + r"(?![\w-])", re.IGNORECASE) for repo in tilat}
    ok, huono = [], []
    for t in tehtavat:
        teksti = str(t.get("nimi", "")) + "\n" + str(t.get("sisalto", ""))
        osuma = next((r for r, k in kuviot.items() if k.search(teksti)), None)
        if osuma:
            huono.append((t, "PR-raja: repossa %s yli %d avointa PR:aa" % (osuma, RAJA)))
        else:
            ok.append(t)
    return ok, huono


def keraa_valimuistilla(repot, tyohak, nyt=None, hae=None):
    """keraa(), mutta tulos pidetaan VALIMUISTI_S sekunnin ajan tiedostossa (tyohak/pr-raja-tila.json). Kirjoitusvirhe ei haittaa."""
    polku = Path(tyohak) / VALIMUISTI
    nyt = nyt or datetime.now(timezone.utc)
    try:
        d = json.loads(polku.read_text(encoding="utf-8"))
        if 0 <= nyt.timestamp() - float(d["aika"]) < VALIMUISTI_S and d.get("repot") == list(repot):
            return d["tilat"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    tilat = keraa(repot, nyt, hae)
    try:
        polku.write_text(json.dumps({"aika": nyt.timestamp(), "repot": list(repot), "tilat": tilat}), encoding="utf-8")
    except OSError:
        pass
    return tilat
