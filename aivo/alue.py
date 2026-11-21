"""Aivon muistialue: hakemistopuu, jonka alta kaikki tilan polut johdetaan (suunnitelma aivo-keskustelumuisti, kohta 4).

Tama moduuli on tilaneutraali: siina ei ole yhtaan oletuspolkua eika viittausta yksityiseen tai tyokalutilaan.
Tilakohtaiset polut ovat omissa moduuleissaan (`yksityinen_alue`, `tyokalualue`), jotta erottelun voi todentaa
rakenteellisesti (ks. test_alue_erottelu.py). Ei mallikutsuja, ei verkkoa, ei alaprosesseja.
"""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

ALIKANSIO_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


class AlueVirhe(Exception):
    """Muistialueen tarkistus epaonnistui. Viesti ei sisalla polkuja eika sisaltoa."""


def tarkista_hakemisto(polku, luo=True, korjaa=True):
    """Kansion pitaa olla oikea kansio (ei linkki), prosessin kayttajan oma ja 0700. Liian laajat oikeudet yritetaan
    korjata; jos se ei onnistu, kieltaydytaan. korjaa=False: vain tarkistus (lukupolku ei muuta oikeuksia; liian
    laajat oikeudet => AlueVirhe). Palauttaa Path-olion."""
    p = Path(polku)
    if luo:
        try:
            p.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as e:
            raise AlueVirhe("kansiota ei voi luoda (%s)" % type(e).__name__)
    try:
        st = os.lstat(p)
    except OSError as e:
        raise AlueVirhe("kansio puuttuu (%s)" % type(e).__name__)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise AlueVirhe("alueen kansion pitaa olla oikea kansio, ei linkki")
    if st.st_uid != os.geteuid():
        raise AlueVirhe("alueen kansio ei kuulu palvelun kayttajalle")
    if stat.S_IMODE(st.st_mode) & 0o077:
        if not korjaa:
            raise AlueVirhe("alueen kansion oikeudet ovat liian laajat")
        try:
            os.chmod(p, 0o700)
        except OSError:
            raise AlueVirhe("alueen kansion oikeudet ovat liian laajat")
    return p


GIT_KANSIO = ".git"


def _git_kansio_ok(st):
    """Sallittu git-metakansio: oikea kansio (ei linkki) ja prosessin kayttajan oma."""
    return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode) and st.st_uid == os.geteuid()


def tarkista_tiedostot(kansio, salli_git=False):
    """Kansion suorat tiedostot: ei linkkeja, ei muita kuin tavallisia tiedostoja ja ei toisen omistajan tiedostoja.
    Liian laajat oikeudet korjataan 0600:ksi; jos korjaus ei onnistu, kieltaydytaan.
    salli_git=True (vain git-kirjastokansio): suora `.git`-alikansio sallitaan, jos se on oikea kansio ja oma. Sen
    sisaltoa ei tarkisteta eika sen oikeuksia muuteta; muut alikansiot hylataan edelleen."""
    try:
        nimet = sorted(os.listdir(kansio))
    except OSError as e:
        raise AlueVirhe("kansiota ei voi lukea (%s)" % type(e).__name__)
    for nimi in nimet:
        p = Path(kansio) / nimi
        try:
            st = os.lstat(p)
        except OSError as e:
            raise AlueVirhe("tiedostoa ei voi tarkistaa (%s)" % type(e).__name__)
        if salli_git and nimi == GIT_KANSIO and _git_kansio_ok(st):
            continue
        if not stat.S_ISREG(st.st_mode):
            raise AlueVirhe("alueen kansiossa on muu kuin tavallinen tiedosto (linkki tai kansio)")
        if st.st_uid != os.geteuid():
            raise AlueVirhe("alueen tiedosto ei kuulu palvelun kayttajalle")
        if stat.S_IMODE(st.st_mode) & 0o077:
            try:
                os.chmod(p, 0o600)
            except OSError:
                raise AlueVirhe("alueen tiedoston oikeudet ovat liian laajat")


@dataclass(frozen=True)
class Alue:
    """Yksi muistialue: nimi, juurikansio ja sallittu alikansioiden joukko. Polut johdetaan vain taalta."""
    nimi: str
    juuri: Path
    alikansiot: tuple
    git_alikansiot: tuple = ()      # alikansiot, jotka saavat olla git-klooneja (sallittu `.git`-kansio)

    def __post_init__(self):
        object.__setattr__(self, "git_alikansiot", tuple(self.git_alikansiot))
        if not set(self.git_alikansiot) <= set(self.alikansiot):
            raise AlueVirhe("git-alikansion pitaa kuulua alueen alikansioihin")
        object.__setattr__(self, "juuri", Path(self.juuri))
        object.__setattr__(self, "alikansiot", tuple(self.alikansiot))
        if not self.juuri.is_absolute():
            raise AlueVirhe("alueen juuren pitaa olla absoluuttinen polku")
        if len(set(self.alikansiot)) != len(self.alikansiot):
            raise AlueVirhe("alikansiot eivat saa toistua")
        for a in self.alikansiot:
            if not isinstance(a, str) or not ALIKANSIO_RE.match(a):
                raise AlueVirhe("virheellinen alikansion nimi")

    def polku(self, alikansio):
        """Alikansion polku. Vain ennalta maaratyt alikansiot kelpaavat (ei '..', ei vierasta nimea)."""
        if alikansio not in self.alikansiot:
            raise AlueVirhe("alikansio ei kuulu alueeseen %s" % self.nimi)
        return self.juuri / alikansio

    def sisaltaa(self, polku):
        """Onko polku (symlinkit avattuna) alueen juuren alla tai sama kuin juuri."""
        try:
            Path(os.path.realpath(polku)).relative_to(os.path.realpath(self.juuri))
        except ValueError:
            return False
        return True

    def valmista(self, luo=True, alikansiot=None):
        """Luo ja tarkistaa juuren seka alikansiot (omistaja, 0700, ei linkkeja) ja niiden tiedostot (0600).
        alikansiot=None: kaikki alueen alikansiot. Muuten vain annetut (osajoukko; vieras nimi => AlueVirhe), jotta
        kayttaja voi rajata tarkistuksen kansioihin, joita se oikeasti lukee tai kirjoittaa.
        Palauttaa {alikansio: Path}."""
        if alikansiot is None:
            valitut = self.alikansiot
        else:
            valitut = tuple(alikansiot)
            if not valitut or any(a not in self.alikansiot for a in valitut):
                raise AlueVirhe("alikansio ei kuulu alueeseen %s" % self.nimi)
        tarkista_hakemisto(self.juuri, luo=luo)
        tulos = {}
        for a in valitut:
            p = tarkista_hakemisto(self.polku(a), luo=luo)
            tarkista_tiedostot(p, salli_git=a in self.git_alikansiot)
            tulos[a] = p
        return tulos


def tarkista_erottelu(a, b):
    """Kaksi aluetta eivat saa olla sama kansio eivatka toistensa sisalla (symlinkit avattuna)."""
    ra, rb = Path(os.path.realpath(a.juuri)), Path(os.path.realpath(b.juuri))
    for x, y in ((ra, rb), (rb, ra)):
        try:
            x.relative_to(y)
        except ValueError:
            continue
        raise AlueVirhe("alueet %s ja %s ovat paallekkaiset" % (a.nimi, b.nimi))
