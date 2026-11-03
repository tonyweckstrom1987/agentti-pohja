"""Mallien tyonjako (Tony 3.11.2026): jokainen tehtava halvimmalla mallilla, joka riittaa laatuun.

Reitit:
  claude      -> `claude -p --model opus|sonnet` (Claude-tilaus, tyokalut kaytossa, voi avata PR:n)
  openrouter  -> LiteLLM-alias (halpa malli OpenRouterin kautta), ei tyokaluja: vain tekstivastaus.

Valinta: (1) tehtavatiedoston rivi `Malli: opus|sonnet|openrouter:<alias>`; (2) puuttuessa tyypin mukaan
taulukosta TYYPPI_MALLI (yksi paikka); (3) oletus OLETUS_MALLI. Sallitut mallit ovat koodissa (koodi on laki):
tehtavatiedosto tai agentti ei voi valita mallia, jota tassa ei ole listattu.
"""
import re
from dataclasses import dataclass

CLAUDE_MALLIT = ("opus", "sonnet")
# Sallitut LiteLLM-aliakset (CLAUDE.md: qwen-coder, deepseek-flash, deepseek-pro). posti-zdr kuuluu posti-agentille, ei jonolle.
OPENROUTER_ALIAKSET = ("deepseek-flash", "deepseek-pro", "qwen-coder")
OLETUS_MALLI = "sonnet"
OPENROUTER_OLETUS = "openrouter:deepseek-flash"

# Tyyppi tunnistetaan tehtavan nimen osasta (pvm-NN-<nimi>.md). Ensimmainen osuma voittaa. YKSI PAIKKA tyyppien mallivalinnalle.
TYYPPI_MALLI = (
    # Opus: suunnittelu, arkkitehtuuri, riippumaton tarkastus, vaikeat viat, Digi-Tonyn paatokset
    ("arkkitehtuur", "opus"), ("suunnittelu", "opus"), ("tarkastaja", "opus"), ("tarkastus", "opus"),
    ("vaikea-vika", "opus"), ("juurisyy", "opus"), ("digitony", "opus"), ("digi-tony", "opus"), ("katselmointi", "opus"),
    # OpenRouter: suurivolyyminen rutiini, jossa virheen hinta on pieni ja tulos tarkistetaan
    ("tarkkailu", OPENROUTER_OLETUS), ("paivakatsaus", OPENROUTER_OLETUS), ("tiivistelma", OPENROUTER_OLETUS),
    ("luokittelu", OPENROUTER_OLETUS), ("kauppatiimi-analyysi", OPENROUTER_OLETUS), ("kauppa-analyysi", OPENROUTER_OLETUS),
    ("aamukoosteen-teksti", OPENROUTER_OLETUS), ("savy-esitarkistus", OPENROUTER_OLETUS), ("kaannos-esitarkistus", OPENROUTER_OLETUS),
)
# Tehtava, joka vaatii tyokaluja (git, PR, tiedostot), ei saa paatya tyypin perusteella tyokaluttomalle reitille.
TYOKALUMERKIT = re.compile(r"claudejono-pr|/opt/agentscope/tyo|\bPR\b|\bgit\b|klooni|kloon|haara\b|\bpytest\b|unittest", re.I)
MALLI_RIVI_RE = re.compile(r"^[ \t]*Malli[ \t]*:[ \t]*(.*?)[ \t]*$", re.I | re.M)
MALLI_ARVO_RE = re.compile(r"^(opus|sonnet|openrouter:[a-z0-9][a-z0-9._-]{0,40})$")


@dataclass(frozen=True)
class Malli:
    reitti: str        # "claude" | "openrouter"
    nimi: str          # claude: opus|sonnet; openrouter: LiteLLM-alias
    lahde: str         # "kentta" | "tyyppi" | "oletus" | "kentta-hylatty"

    @property
    def tunniste(self):
        return self.nimi if self.reitti == "claude" else "openrouter:" + self.nimi


def _tulkitse(arvo):
    """'opus' | 'sonnet' | 'openrouter:<alias>' -> (reitti, nimi) tai None, jos arvo ei ole sallittu."""
    arvo = (arvo or "").strip().lower()
    if not MALLI_ARVO_RE.match(arvo):
        return None
    if arvo in CLAUDE_MALLIT:
        return "claude", arvo
    alias = arvo.split(":", 1)[1]
    return ("openrouter", alias) if alias in OPENROUTER_ALIAKSET else None


def kentta(teksti):
    """Ensimmaisen `Malli:`-rivin arvo tai None. Vain rivin alusta, joten tekstin seassa oleva maininta ei laukaise."""
    m = MALLI_RIVI_RE.search(teksti or "")
    return m.group(1) if m else None


def tyyppi_malli(nimi, teksti=""):
    """Tyypin mukainen malli tehtavan nimesta (taulukko TYYPPI_MALLI) tai None."""
    nimi = (nimi or "").lower()
    for avain, malli in TYYPPI_MALLI:
        if avain in nimi:
            reitti = _tulkitse(malli)
            if reitti and reitti[0] == "openrouter" and TYOKALUMERKIT.search(teksti or ""):
                return None   # tarvitsee tyokaluja -> ei halvalle tekstireitille
            return reitti
    return None


def valitse(nimi, teksti=""):
    """Tehtavan malli. Virheellinen Malli-kentta ei kaada ajoa: hylataan (lahde 'kentta-hylatty') ja paatellaan tyypista."""
    arvo = kentta(teksti)
    hylatty = False
    if arvo is not None:
        r = _tulkitse(arvo)
        if r:
            return Malli(r[0], r[1], "kentta")
        hylatty = True
    r = tyyppi_malli(nimi, teksti)
    if r:
        return Malli(r[0], r[1], "kentta-hylatty" if hylatty else "tyyppi")
    return Malli("claude", OLETUS_MALLI, "kentta-hylatty" if hylatty else "oletus")


def suunnittelun_malli():
    """Jonon itsesuunnittelu on suunnittelutehtava -> Opus."""
    return Malli("claude", "opus", "tyyppi")
