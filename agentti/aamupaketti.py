"""Aamupaketti: yhteenveto avoimista PR:ista (vain luku, julkinen GitHub-API, ei tunnuksia).

Jakaa PR:t kolmeen ryhmaan: Tonyn mergattavat (muuttavat suojattuja polkuja), automerge hoitaa (vain sallittuja polkuja)
ja muut (luonnos, CI kesken tai punainen). Kaytto: python3 -m agentti.aamupaketti [--laheta]
--laheta kayttaa ymparistoa YOJONO_TELEGRAM_TOKEN ja YOJONO_TELEGRAM_CHAT (kuten yojonon yhteenveto)."""
import json
import os
import posixpath
import subprocess
import sys
import urllib.request

from . import pr_laatu, telegram
from .pr_kutsu import REPOT, suojattu as _kriittinen_polku
from .yksikot import suodata_kohina

OMISTAJA = "tonyweckstrom1987"
MAX_PR_REPOA_KOHDEN = 15
MAX_TIEDOSTOJA_HAKUUN = 100
OTSIKKO_MAX = 70
KRIITTISET_NIMET = ("riskivahti.py", "salkku.py", "kirjanpito.py")
KRIITTISET_PAATTEET = (".service", ".timer")
RISKI_RIVEJA_KORKEA = 300
RISKI_RIVEJA_KESKI = 60
RISKI_TIEDOSTOJA_KESKI = 5
NAYTA_TIEDOSTOJA = 4


def _matala_tiedosto(polku):
    p = polku.lower()
    return p.endswith((".md", ".txt")) or p.startswith(("docs/", "test/")) or posixpath.basename(p).startswith("test_")


def _kriittinen(polku):
    nimi = posixpath.basename(polku.lower())
    return _kriittinen_polku(polku) or nimi.endswith(KRIITTISET_PAATTEET) or nimi in KRIITTISET_NIMET


def arvioi_riski(tiedostot):
    """(taso, syy): 'korkea', 'keskitaso' tai 'matala'. Karkea ja deterministinen arvio katselmoinnin kohdistamiseen, ei turvatakuu:
    korkea = kriittinen polku (suojatut, *.service/*.timer, riskivahti/salkku/kirjanpito), poisto tai nimeaminen, yli 300 rivia tai tiedostolista katkaistu;
    keskitaso = yli 60 rivia, yli 5 tiedostoa tai muu kuin dokumentti/testi; muuten matala."""
    nimet = [n for t in tiedostot for n in (t.get("filename"), t.get("previous_filename")) if n]
    rivit = sum(int(t.get("additions") or 0) + int(t.get("deletions") or 0) for t in tiedostot)
    if len(tiedostot) >= MAX_TIEDOSTOJA_HAKUUN:
        return "korkea", "yli %d tiedostoa" % (MAX_TIEDOSTOJA_HAKUUN - 1)
    for n in nimet:
        if _kriittinen(n):
            return "korkea", "kriittinen polku %s" % n
    if any(t.get("status") in ("removed", "renamed") for t in tiedostot):
        return "korkea", "poisto tai nimeaminen"
    if rivit > RISKI_RIVEJA_KORKEA:
        return "korkea", "%d rivia" % rivit
    if rivit > RISKI_RIVEJA_KESKI:
        return "keskitaso", "%d rivia" % rivit
    if len(tiedostot) > RISKI_TIEDOSTOJA_KESKI:
        return "keskitaso", "%d tiedostoa" % len(tiedostot)
    if not all(_matala_tiedosto(n) for n in nimet):
        return "keskitaso", "koodia"
    return "matala", ""


def yhteenveto(tiedostot):
    """Tonyn mergattavan PR:n yhteenveto: tiedostot (kriittiset ensin), rivimaarat ja riski."""
    nimet = []
    for t in tiedostot:
        n = t.get("filename")
        if n and n not in nimet:
            nimet.append(n)
    nimet.sort(key=lambda n: not _kriittinen(n))
    riski, syy = arvioi_riski(tiedostot)
    return {"tiedostot": nimet, "lisatty": sum(int(t.get("additions") or 0) for t in tiedostot),
            "poistettu": sum(int(t.get("deletions") or 0) for t in tiedostot), "riski": riski, "riskisyy": syy}


def _suojattu(polku):
    """True jos polku kuuluu automergen suojalistalle tai ei ole sallittu (Tony katselmoi)."""
    from tools import automerge
    ok, _ = automerge.polku_ok(polku)
    return not ok


def _oletushaku(url):
    pyynto = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "agenttifirma-aamupaketti"})
    with urllib.request.urlopen(pyynto, timeout=20) as v:
        return json.loads(v.read().decode("utf-8"))


def _ci(hae, repo, sha):
    """'vihrea', 'punainen' tai 'kesken' check-runeista."""
    try:
        runit = hae("https://api.github.com/repos/%s/%s/commits/%s/check-runs?per_page=50" % (OMISTAJA, repo, sha)).get("check_runs", [])
    except Exception:
        return "kesken"
    if not runit or any(r.get("status") != "completed" for r in runit):
        return "kesken"
    huonot = [r for r in runit if r.get("conclusion") not in ("success", "skipped", "neutral")]
    return "punainen" if huonot else "vihrea"


def keraa(hae=None):
    """Palauttaa {'tony': [...], 'automerge': [...], 'muut': [...]}, kukin rivi dict (repo, numero, otsikko, url, ci, syy)."""
    hae = hae or _oletushaku
    ryhmat = {"tony": [], "automerge": [], "muut": []}
    for repo in REPOT:
        try:
            prt = hae("https://api.github.com/repos/%s/%s/pulls?state=open&per_page=%d" % (OMISTAJA, repo, MAX_PR_REPOA_KOHDEN))
        except Exception:
            continue
        for p in prt[:MAX_PR_REPOA_KOHDEN]:
            nro = p.get("number")
            rivi = {"repo": repo, "numero": nro, "otsikko": " ".join(str(p.get("title", "")).split())[:OTSIKKO_MAX],
                    "url": p.get("html_url") or "https://github.com/%s/%s/pull/%s" % (OMISTAJA, repo, nro), "ci": "", "syy": ""}
            if p.get("draft"):
                rivi["syy"] = "luonnos"
                ryhmat["muut"].append(rivi)
                continue
            rivi["ci"] = _ci(hae, repo, (p.get("head") or {}).get("sha", ""))
            try:
                tiedostot = hae("https://api.github.com/repos/%s/%s/pulls/%s/files?per_page=%d" % (OMISTAJA, repo, nro, MAX_TIEDOSTOJA_HAKUUN))
            except Exception:
                tiedostot = None
            if tiedostot is None:
                rivi["syy"] = "tiedostoja ei saatu"
                ryhmat["muut"].append(rivi)
                continue
            if rivi["ci"] == "punainen":
                rivi["syy"] = "CI punainen"
                ryhmat["muut"].append(rivi)
                continue
            suojattu = any(_suojattu(n) for t in tiedostot for n in (t.get("filename"), t.get("previous_filename")) if n)
            if suojattu:
                rivi.update(yhteenveto(tiedostot))
                ryhmat["tony"].append(rivi)
            elif rivi["ci"] == "vihrea":
                ryhmat["automerge"].append(rivi)
            else:
                rivi["syy"] = "CI kesken"
                ryhmat["muut"].append(rivi)
    return ryhmat


def _rivi(r):
    lisa = " [%s]" % r["syy"] if r["syy"] else (" (CI %s)" % r["ci"] if r["ci"] else "")
    if r.get("riski"):
        lisa += " [riski: %s%s]" % (r["riski"], ", " + r["riskisyy"] if r.get("riskisyy") else "")
        nimet = r.get("tiedostot") or []
        nayta = ", ".join(nimet[:NAYTA_TIEDOSTOJA]) + (" (+%d)" % (len(nimet) - NAYTA_TIEDOSTOJA) if len(nimet) > NAYTA_TIEDOSTOJA else "")
        return "- %s #%s %s%s\n  tiedostot: %s | +%d/-%d\n  %s" % (r["repo"], r["numero"], r["otsikko"], lisa, nayta, r["lisatty"], r["poistettu"], r["url"])
    return "- %s #%s %s%s\n  %s" % (r["repo"], r["numero"], r["otsikko"], lisa, r["url"])


def muotoile(ryhmat):
    osat = ["Aamupaketti: avoimet PR:t"]
    for avain, otsikko in (("tony", "Sinun mergattavat (suojatut polut)"), ("automerge", "Automerge hoitaa (CI vihrea)"), ("muut", "Muut (luonnos, CI kesken tai punainen)")):
        rivit = ryhmat.get(avain) or []
        osat.append("\n%s: %d" % (otsikko, len(rivit)))
        osat.extend(_rivi(r) for r in rivit)
    if not any(ryhmat.values()):
        osat.append("\nEi avoimia PR:ita.")
    return "\n".join(osat)


def _systemctl(*args):
    """Vain luku -systemctl-kutsu; palauttaa stdoutin, virheesta nostaa poikkeuksen."""
    return subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=15, check=True).stdout


def failed_yksikot(suorita=None):
    """Failed-yksikot: lista (nimi, aikaleima) tai tyypin nimi (str) jos tarkistus ei onnistunut. Kohinamountit suodatetaan.
    Aikaleima = StateChangeTimestamp (milloin yksikko meni failed-tilaan), muuten ActiveEnterTimestamp."""
    suorita = suorita or _systemctl
    try:
        ulos = suodata_kohina(suorita("list-units", "--failed", "--no-legend", "--no-pager", "--plain"))
        yksikot = []
        for rivi in ulos.splitlines():
            kentat = [k for k in rivi.split() if k not in ("*", "\u25cf")]
            if kentat:
                yksikot.append(kentat[0])
        tulos = []
        for nimi in yksikot:
            arvot = {}
            for r in suorita("show", "-p", "StateChangeTimestamp", "-p", "ActiveEnterTimestamp", nimi).splitlines():
                avain, _, arvo = r.partition("=")
                arvot[avain.strip()] = arvo.strip()
            tulos.append((nimi, arvot.get("StateChangeTimestamp") or arvot.get("ActiveEnterTimestamp") or "?"))
        return tulos
    except Exception as e:
        return type(e).__name__


def muotoile_failed(tulos):
    """Osio tekstina; tyhja lista = tyhja merkkijono (ei ilmoitusmelua)."""
    if isinstance(tulos, str):
        return "Epaonnistuneet palvelut:\n- failed-tarkistus ei onnistunut: %s" % tulos
    if not tulos:
        return ""
    return "\n".join(["Epaonnistuneet palvelut: %d" % len(tulos)] + ["- %s (failed alkaen %s)" % t for t in tulos])


def main(argv=None, hae=None, env=None, suorita=None):
    a = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if any(x not in ("--laheta",) for x in a):
        print("kaytto: python3 -m agentti.aamupaketti [--laheta]", file=sys.stderr)
        return 2
    teksti = muotoile(keraa(hae))
    osio = muotoile_failed(failed_yksikot(suorita))
    if osio:
        teksti += "\n\n" + osio
    try:   # laatumittari on lisa: virhe ei esta aamupakettia
        laatu = pr_laatu.keraa(hae)
        teksti += "\n\n" + pr_laatu.muotoile(laatu)
        kulut = pr_laatu.lue_kulut(env.get("YOJONO_PERUS"))
        if kulut:   # nostoehdot vaativat kulutiedon; ilman sita rivia ei nayteta
            teksti += "\n" + pr_laatu.nostoehdot(laatu, pr_laatu.kulu_ajalta(kulut))[1]
    except Exception:
        pass
    print(teksti)
    if "--laheta" in a:
        ok = telegram.laheta(teksti, {"TELEGRAM_BOT_TOKEN": env.get("YOJONO_TELEGRAM_TOKEN"), "TELEGRAM_CHAT_ID": env.get("YOJONO_TELEGRAM_CHAT")})
        if not ok:
            print("Telegram-lahetys epaonnistui tai ei asetettu", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
