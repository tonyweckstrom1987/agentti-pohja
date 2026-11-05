"""Todennettavat: docs-repon claude-jono/todennettavat.md -taulukon lukija aamukoostetta varten.

Taulukon sarakkeet: Mekanismi | Luotu | Ensimmainen ajo | Miten todennetaan | Tila.
Avoin rivi = Tila alkaa sanalla "avoin" tai "odottaa". Rivi eraantyy, kun Ensimmainen ajo -sarakkeen
paivamaara (p.k. [klo hh.mm]) on mennyt. Rivi ilman luettavaa paivamaaraa ei eraannu (ajankohta puuttuu).
Vuosi otetaan annetusta ajasta. Vain luku; puuttuva tai rikkinainen tiedosto antaa tyhjan listan."""
import re
from datetime import datetime
from pathlib import Path

TIEDOSTO = "todennettavat.md"
AVOIN_RE = re.compile(r"(?i)^(avoin|odottaa)")
AJAT_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(?:\s*klo\s*(\d{1,2})[.:](\d{2}))?")


def _ajankohta(teksti, nyt):
    """Ensimmainen ajankohta tekstista (aikavyohyke nyt:n mukaan) tai None. Kellonajan puuttuessa paivan loppu."""
    m = AJAT_RE.search(teksti)
    if not m:
        return None
    pv, kk = int(m.group(1)), int(m.group(2))
    tunti, min_ = (int(m.group(3)), int(m.group(4))) if m.group(3) else (23, 59)
    try:
        return datetime(nyt.year, kk, pv, tunti, min_, tzinfo=nyt.tzinfo)
    except ValueError:
        return None


def rivit(teksti):
    """Taulukon datarivit: lista {mekanismi, luotu, ajo, miten, tila}."""
    ulos = []
    for rivi in teksti.splitlines():
        rivi = rivi.strip()
        if not rivi.startswith("|"):
            continue
        solut = [s.strip() for s in rivi.strip("|").split("|")]
        if len(solut) < 5 or set("".join(solut)) <= set("-: "):
            continue
        if solut[0].lower() == "mekanismi":
            continue
        ulos.append(dict(zip(("mekanismi", "luotu", "ajo", "miten", "tila"), solut[:5])))
    return ulos


def eraantyneet(repo, nyt, jono="claude-jono"):
    """Avoimet rivit, joiden ensimmainen ajo on mennyt (nyt >= ajankohta), ajan mukaan jarjestettyna."""
    try:
        teksti = (Path(repo) / jono / TIEDOSTO).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ulos = []
    for r in rivit(teksti):
        if not AVOIN_RE.match(r["tila"]):
            continue
        aika = _ajankohta(r["ajo"], nyt)
        if aika is not None and aika <= nyt:
            ulos.append((aika, r))
    return [r for _, r in sorted(ulos, key=lambda x: x[0])]


def kysymykset(rivit_, raja=3, nimi_max=50):
    """'Toimiko <mekanismi>? kylla / ei / en tieda' -teksti (enintaan `raja` kysymysta, loput lukuna) tai None."""
    if not rivit_:
        return None
    nimet = [re.sub(r"\s*\(.*?\)\s*$", "", r["mekanismi"])[:nimi_max] for r in rivit_[:raja]]
    teksti = " ".join(f"Toimiko {n}?" for n in nimet)
    if len(rivit_) > raja:
        teksti += f" (+{len(rivit_) - raja} muuta, katso todennettavat.md)"
    return teksti + " Vastaa: kyll\u00e4 / ei / en tied\u00e4"
