"""Isannan tilaraportti docs-repoon (ajastin tunneittain). Vain luku: ei muuta mitaan muuta kuin raporttitiedoston.

Kokoaa yhteen tiedostoon (claude-jono/raportit-palvelin/tila.md):
  - systemd:n failed-yksikot CT115:ssa ja muissa ajossa olevissa konteissa (isannan pakotettu vain luku -komento `tila`)
  - jonon tila (viimeisen vuorokauden tulokset, yojonon epaonnistuneet)
  - levyn ja thin poolin (pvesm status) kayttoprosentit
  - viimeisimmat 20 virherivia claude-jono@*-, claude-tarkastaja- ja docs-pull-lokeista
  - viimeisimman varmuuskopion ika
Kaikki teksti suodatetaan ENNEN kirjoitusta: infran osoitteet (automerge.OSOITE_RE, kaikki IPv4-osoitteet,
tailnetin IPv6 ja nimet), sahkopostit, MAC-osoitteet ja salaisuuden nakoiset merkkijonot poistetaan.

Kaytto: python3 -m claudejono.tilaraportti [--ulos HAKEMISTO] [--julkaise] [--repo POLKU] [--nyt ISO-AIKA]
Ilman --ulos tulostaa vain (kuiva-ajo). --ulos kirjoittaa <HAKEMISTO>/claude-jono/raportit-palvelin/tila.md
(docs-ulos-kansiorakenne). --julkaise committaa ja pushaa tiedoston docs-kloonista ajurin julkaise-funktiolla
(git-lukon sisalla); ilman muutosta ei kirjoiteta eika committaa mitaan. Isannan osoite tulee SNAPSHOT_HOST-ymparistosta."""
import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from tools.automerge import OSOITE_RE

TZ_NIMI = "Europe/Helsinki"
OLETUS_REPO = "/var/lib/claude-jono/docs"
OLETUS_YOJONO = "/var/lib/yojono"
KOHDE_SUHT = "claude-jono/raportit-palvelin/tila.md"
VIRHERIVEJA = 20
RIVI_MAX = 220
OSIO_MAX_RIVEJA = 40
LOKIYKSIKOT = ("claude-jono@*", "claude-tarkastaja.service", "docs-pull.service")
KOHINA = ("dev-mqueue.mount", "run-lock.mount", "tmp.mount")
PAIVAMAARA_RIVI = "P\u00e4ivitetty: "

# Lisasuodattimet OSOITE_RE:n (sisaiset osoitteet) lisaksi: mikaan osoitteen nakoinen ei saa paatya docs-repoon.
LISAKUVIOT = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),                    # mika tahansa IPv4
    re.compile(r"(?i)\bfd7a:[0-9a-f:]+"),                                        # tailnetin IPv6
    re.compile(r"(?i)\b(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}\b"),               # muu IPv6
    re.compile(r"(?i)\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b"),                       # MAC
    re.compile(r"[\w.+-]+@(?![\w-]+\.(?:service|timer|mount|socket|target|path|slice|scope)\b)[\w-]+(?:\.[\w-]+)+"),                                 # sahkoposti
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|bearer)\s*[:=]\s*\S{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
POISTETTU = "[poistettu]"


def suodata(teksti):
    """Poistaa osoitteet, tunnukset ja henkilotiedot (OSOITE_RE ensin, sitten lisakuviot). Rivit katkaistaan RIVI_MAX:iin."""
    ulos = []
    for rivi in str(teksti).splitlines():
        rivi = OSOITE_RE.sub(POISTETTU, rivi)
        for k in LISAKUVIOT:
            rivi = k.sub(POISTETTU, rivi)
        ulos.append(rivi if len(rivi) <= RIVI_MAX else rivi[:RIVI_MAX - 3] + "...")
    return "\n".join(ulos)


def _komento(kom, aikaraja=30, suorita=subprocess.run):
    """Palauttaa (koodi, stdout) tai None, jos komentoa ei voi ajaa. Virheiden tekstia ei palauteta."""
    try:
        r = suorita(kom, capture_output=True, text=True, timeout=aikaraja, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode, (r.stdout or "")


def osiot(isanta_teksti):
    """Jakaa isannan `tila`-tulosteen otsikoiden (`## ...`) mukaan: {otsikko: teksti}."""
    ulos, nimi, rivit = {}, None, []
    for rivi in isanta_teksti.splitlines():
        if rivi.startswith("## "):
            if nimi is not None:
                ulos[nimi] = "\n".join(rivit).strip()
            nimi, rivit = rivi[3:].strip(), []
        elif nimi is not None:
            rivit.append(rivi)
    if nimi is not None:
        ulos[nimi] = "\n".join(rivit).strip()
    return ulos


def failed_yksikot(isanta_osiot, paikallinen):
    """Kontti -> lista failed-yksikoista. paikallinen = CT115:n `systemctl --failed` -teksti (tai None)."""
    tulos = {}
    if paikallinen is None:
        tulos["CT115"] = None
    else:
        tulos["CT115"] = [r.split()[0] for r in paikallinen.splitlines() if r.strip() and not any(k in r for k in KOHINA)]
    for otsikko, teksti in isanta_osiot.items():
        m = re.match(r"CT(\d+) epaonnistuneet palvelut$", otsikko)
        if not m:
            continue
        kt = "CT" + m.group(1)
        if teksti.startswith("("):
            tulos.setdefault(kt, None if "ei saatavilla" in teksti else [])
            continue
        rivit = [r.lstrip("\u25cf* ").split()[0] for r in teksti.splitlines() if r.strip() and not any(k in r for k in KOHINA)]
        tulos[kt] = rivit
    return tulos


def levyt(isanta_osiot):
    """Levy- ja thin pool -prosentit: [(nimi, prosentti)]. Lahteet: pvesm status (viimeinen sarake %) ja df -h (Use%)."""
    ulos = []
    for rivi in isanta_osiot.get("Tallennustila (pvesm status)", "").splitlines()[1:]:
        k = rivi.split()
        if len(k) >= 7 and k[-1].endswith("%"):
            ulos.append((f"tallennus {k[0]} ({k[1]})", k[-1]))
    for rivi in isanta_osiot.get("Isannan levy", "").splitlines()[1:]:
        k = rivi.split()
        if len(k) >= 6 and k[4].endswith("%"):
            ulos.append((f"levy {k[5]}", k[4]))
    return ulos


KUUKAUDET = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def varmuuskopion_ika_h(isanta_osiot, nyt):
    """Uusimman varmuuskopion ika tunteina `ls -lt` -rivilta (kk pv hh:mm tai kk pv vuosi). None, jos ei loydy."""
    paras = None
    for rivi in isanta_osiot.get("Viimeisimmat varmuuskopiot", "").splitlines():
        k = rivi.split()
        if len(k) < 9 or k[5] not in KUUKAUDET or not k[6].isdigit():
            continue
        try:
            if ":" in k[7]:
                h, m = (int(x) for x in k[7].split(":"))
                aika = datetime(nyt.year, KUUKAUDET[k[5]], int(k[6]), h, m, tzinfo=nyt.tzinfo)
                if aika > nyt:
                    aika = aika.replace(year=nyt.year - 1)
            else:
                aika = datetime(int(k[7]), KUUKAUDET[k[5]], int(k[6]), tzinfo=nyt.tzinfo)
        except ValueError:
            continue
        if paras is None or aika > paras:
            paras = aika
    return None if paras is None else max(0.0, (nyt - paras).total_seconds() / 3600)


def virherivit(suorita=subprocess.run):
    """Viimeisimmat VIRHERIVIT-rivia (priorisuus err tai pahempi) jononyksikoiden lokeista. None, jos journalctl ei toimi."""
    kom = ["journalctl", "--no-pager", "-o", "short-iso", "-p", "err", "-n", str(VIRHERIVEJA), "-q"]
    for y in LOKIYKSIKOT:
        kom += ["-u", y]
    r = _komento(kom, 30, suorita)
    if r is None or r[0] != 0:
        return None
    return [x for x in r[1].splitlines() if x.strip()][-VIRHERIVEJA:]


def jonon_tila(repo, perus, nyt):
    """Viimeisen vuorokauden tulosten tilat ja yojonon epaonnistuneet. Puuttuva lahde -> None kyseiselle osalle."""
    from claudejono import aamukooste
    ulos = {"tulokset": None, "yojono": None}
    try:
        laskuri = {}
        for t in aamukooste.lue_tulokset(repo, nyt - timedelta(hours=24), nyt):
            laskuri[t["tila"]] = laskuri.get(t["tila"], 0) + 1
        ulos["tulokset"] = laskuri
    except Exception as e:
        print("tilaraportti: tulokset epaonnistui (%s)" % type(e).__name__, file=sys.stderr)
    try:
        ulos["yojono"] = aamukooste.yojono_tila(perus, nyt)
    except Exception as e:
        print("tilaraportti: yojono epaonnistui (%s)" % type(e).__name__, file=sys.stderr)
    return ulos


def keraa(repo, perus, nyt, hae_isanta=None, paikallinen=None, hae_virheet=None):
    """Ker\u00e4\u00e4 aineiston. Lahteet voi korvata (testit). Yksi epaonnistunut lahde ei kaada muita."""
    isanta_teksti, isanta_virhe = "", None
    try:
        if hae_isanta is None:
            from agentti import snapshot_kutsu
            hae_isanta = snapshot_kutsu.tila
        isanta_teksti = hae_isanta()
    except Exception as e:   # vain tyyppi: viesti voi sisaltaa osoitteita
        isanta_virhe = type(e).__name__
    os_ = osiot(isanta_teksti)
    if paikallinen is None:
        r = _komento(["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"])
        paikallinen = r[1] if r and r[0] == 0 else None
    return {"nyt": nyt, "isanta_virhe": isanta_virhe, "osiot": os_, "failed": failed_yksikot(os_, paikallinen),
            "levyt": levyt(os_), "kopion_ika_h": varmuuskopion_ika_h(os_, nyt),
            "virheet": (hae_virheet or virherivit)(), "jono": jonon_tila(repo, perus, nyt)}


def muotoile(t):
    """Tilaraportti markdown-tekstina (suodattamaton; suodata() ajetaan kirjoituksessa)."""
    r = ["# Is\u00e4nn\u00e4n tilaraportti", "", PAIVAMAARA_RIVI + t["nyt"].strftime("%Y-%m-%d %H:%M %Z"), "",
         "Kone kirjoittaa t\u00e4m\u00e4n tunneittain. Osoitteet ja tunnukset on suodatettu pois.", ""]
    r.append("## Ep\u00e4onnistuneet yksik\u00f6t (failed)")
    if t["isanta_virhe"]:
        r.append("- Is\u00e4nn\u00e4n tilaa ei saatu (%s): vain CT115 alla." % t["isanta_virhe"])
    for kt in sorted(t["failed"], key=lambda x: int(x[2:])):
        y = t["failed"][kt]
        r.append("- %s: %s" % (kt, "ei saatavilla" if y is None else (", ".join(y[:8]) + (" (+%d)" % (len(y) - 8) if len(y) > 8 else "") if y else "ei")))
    r += ["", "## Jonon tila"]
    j = t["jono"]
    if j["tulokset"] is None:
        r.append("- Tuloksia ei voitu lukea.")
    else:
        r.append("- Tulokset 24 h: " + (", ".join("%s %d" % (k, v) for k, v in sorted(j["tulokset"].items())) or "ei yht\u00e4\u00e4n"))
    yj = j["yojono"]
    if yj:
        r.append("- Y\u00f6jono 24 h: %d valmis, ep\u00e4onnistui: %s" % (yj["valmis"], ", ".join(yj["epaonnistui"][:5]) or "ei"))
    else:
        r.append("- Y\u00f6jonon tilaa ei saatu luettua.")
    r += ["", "## Levy ja thin pool"]
    r += ["- %s: %s" % l for l in t["levyt"]] or ["- ei lukuja"]
    if not t["levyt"]:
        r[-1] = "- ei lukuja (is\u00e4nn\u00e4n tila puuttuu)"
    r += ["", "## Varmuuskopiot"]
    ika = t["kopion_ika_h"]
    r.append("- Uusin varmuuskopio: " + ("ei l\u00f6ytynyt" if ika is None else "%.0f h sitten" % ika))
    r += ["", "## Viimeisimm\u00e4t %d virhett\u00e4 (claude-jono@*, claude-tarkastaja, docs-pull)" % VIRHERIVEJA]
    if t["virheet"] is None:
        r.append("- Lokia ei saatu luettua.")
    elif not t["virheet"]:
        r.append("- ei virheit\u00e4")
    else:
        r += ["    " + x for x in t["virheet"]]
    return "\n".join(r) + "\n"


def raportti(t):
    return suodata(muotoile(t)) + "\n"


def _sisalto_ilman_aikaa(teksti):
    return "\n".join(r for r in teksti.splitlines() if not r.startswith(PAIVAMAARA_RIVI))


def kirjoita(teksti, ulos):
    """Kirjoittaa tiedoston vain jos sisalto (ilman aikaleimaa) muuttui. Palauttaa polun tai None (ei muutosta)."""
    kohde = Path(ulos) / KOHDE_SUHT
    try:
        vanha = kohde.read_text(encoding="utf-8")
    except OSError:
        vanha = None
    if vanha is not None and _sisalto_ilman_aikaa(vanha) == _sisalto_ilman_aikaa(teksti):
        return None
    if kohde.is_symlink():
        raise OSError("kohde on symlinkki")
    kohde.parent.mkdir(parents=True, exist_ok=True)
    kohde.write_text(teksti, encoding="utf-8")
    return kohde


def main(argv=None):
    p = argparse.ArgumentParser(description="Isannan tilaraportti docs-repoon (kuiva-ajo ilman --ulos)")
    p.add_argument("--ulos", help="kirjoita <hakemisto>/" + KOHDE_SUHT)
    p.add_argument("--julkaise", action="store_true", help="committaa ja pushaa (vaatii --ulos = docs-klooni)")
    p.add_argument("--repo", default=os.environ.get("CLAUDEJONO_REPO", OLETUS_REPO))
    p.add_argument("--yojono", default=os.environ.get("YOJONO_PERUS", OLETUS_YOJONO))
    p.add_argument("--nyt", help="ISO-aika (testaus)")
    a = p.parse_args(argv)
    from zoneinfo import ZoneInfo
    nyt = datetime.fromisoformat(a.nyt) if a.nyt else datetime.now(ZoneInfo(TZ_NIMI))
    if nyt.tzinfo is None:
        nyt = nyt.replace(tzinfo=ZoneInfo(TZ_NIMI))
    teksti = raportti(keraa(a.repo, a.yojono, nyt))
    if not a.ulos:
        print(teksti)
        return 0
    kohde = kirjoita(teksti, a.ulos)
    print("tilaraportti: " + ("ei muutosta" if kohde is None else "kirjoitettu " + KOHDE_SUHT))
    if a.julkaise and kohde is not None:
        from claudejono import ajuri
        if not ajuri.julkaise(a.ulos, [kohde], "claude-jono: is\u00e4nn\u00e4n tilaraportti"):
            print("tilaraportti: push epaonnistui", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
