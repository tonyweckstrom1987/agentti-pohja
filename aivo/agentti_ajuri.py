"""Eristettyjen agenttien ajuri (osa 3): jono, tyokansiot ja tulokset kontissa 122.

Palvelu (`aivo-agentit.service`, kayttaja `aivo-agentti`) lukee jonoa ja ajaa tehtavat yksi kerrallaan. Jokainen
tehtava ajetaan omana aliprosessinaan (`python3 -m aivo.agentti_ajuri lapsi`), joka kutsuu funktiota
`aivo.agentti.malli.aja`. Ajuri lopettaa aliprosessin, jos se ylittaa ajan PROSESSIN_AIKARAJA_S (agenttiytimen
oma raja malli.AIKARAJA_S ja pieni vara). Uusia kulu-, budjetti- tai askelrajoja ei ole: kulut kulkevat
funktion `openrouter_kulut.kutsu` kautta yhteiseen kirjanpitoon (malli-moduuli), ja askel- ja aikarajat ovat
agenttiytimen vakioita.

Kansiot (perus = /var/lib/aivo-agentit; asennusohje docs/aivo-agentit.md):
  jono/<tunnus>.json        tehtavapyynnot (2770, ryhma aivo-agentit). Kirjoittaja tekee ensin valiaikaisen nimen
                            (alkaa pisteella) ja nimeaa sen lopuksi; ajuri lukee vain nimet [0-9a-f]{12}.json.
                            Pyynnon oikeudet 0640 (ryhma periytyy setgid-kansiosta), jotta ajuri voi lukea sen.
  kasitellyt/<tunnus>.json  ajuri siirtaa pyynnon tanne ennen ajoa (0700). Mitaan ei poisteta automaattisesti.
  tyo/<tunnus>/             tehtavan tyokansio (0700), liitteet kopioidaan tanne.
  tulokset/<tunnus>.json    tulos (tiedosto 0640, kansio 2750, ryhma aivo-agentit).
  tulokset/<tunnus>/        kopio tyokansion tiedostoista chatin vain luettavaa nakymaa varten (kansiot 2750,
                            tiedostot 0640). Kopio tehdaan ennen tulosta, joten valmis tulos tarkoittaa valmista kopiota.

Pyynnon muoto (JSON-olio, muut kentat hylataan):
  {"versio": 1, "teksti": "...", "liitteet": [{"nimi": "a.md", "sisalto": "..."}], "keskustelu": "abc"}
  `liitteet` ja `keskustelu` ovat valinnaisia. Liitteille patevat tyokalun `kirjoita` polku-, paate- ja kokosaannot.

Lokiin (stdout -> journald) kirjoitetaan vain tunnus, tila, luvut ja virheen tyyppi, ei koskaan tehtavan sisaltoa.

Verkkohaku (A1101, valinnainen): jos palvelulla on --haku-osoite (tai AIVO_HAKU_OSOITE), lapsiprosessi saa
hakuasetuksen ja agentin tekija tyokalut hae ja sivu (aivo.verkkohaku hakuvalittajan kautta). Avain on tiedostossa
--haku-avain (oletus <perus>/haku-avain, 0600, palvelun kayttajan oma; Tony luo). Hakuloki (aika, tunnus, tila)
kirjoitetaan kansioon <perus>/hakuloki (0700, 30 paivaa). Lausekkeet ja osoitteet nakyvat tuloksen kentassa verkko.

Paattelyn ylaraja: sama ymparistomuuttuja ja tarkistus kuin chatissa (AIVO_YKSITYINEN_PAATTELY_MAX, oletus 2 000,
sallittu 0-6 000). Lapsiprosessi perii ympariston ja valittaa arvon funktiolle malli.aja. Palvelu ja tarkista
hylkaavat kelvottoman arvon heti kaynnistyksessa (asetusvirhe), jottei jokainen tehtava paaty virheeseen.
"""
import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .agentti import malli, polut, tyokalut

OLETUS_PERUS = "/var/lib/aivo-agentit"
PAATTELY_MUUTTUJA = "AIVO_YKSITYINEN_PAATTELY_MAX"   # sama kuin chatissa, taustaajossa ja tiivistyksessa
JONO = "jono"
KASITELLYT = "kasitellyt"
TYO = "tyo"
TULOKSET = "tulokset"
LUKKO = "ajuri.lukko"
HAKUAVAIN = "haku-avain"
HAKULOKI = "hakuloki"

PROSESSIN_AIKARAJA_S = malli.AIKARAJA_S + 60   # agenttiytimen aikaraja + vara aliprosessin kaynnistykseen
ODOTUS_S = 10                                   # jonon tarkistusvali, kun jono on tyhja
VERSIO = 1
TUNNUS_RE = re.compile(r"[0-9a-f]{12}")
JONONIMI_RE = re.compile(r"([0-9a-f]{12})\.json")
KESKUSTELU_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
RAJA_SYOTE_TAVUT = 4 * 1024 * 1024    # pyyntotiedoston koko (syotteen kelpoisuus, ei kuluraja)
RAJA_TEKSTI = 20000                   # tehtavan teksti, merkkia
RAJA_LIITTEET = 50
TILAT = (malli.VALMIS, malli.KESKEN, malli.KESKEYTETTY, malli.VIRHE)
SYY_AIKARAJA = "aikaraja: aliprosessi lopetettu"
SYY_PYSAHTYI = "ajuri pysahtyi kesken tehtavan"
KENTAT = ("versio", "tunnus", "keskustelu", "tila", "syy", "raportti", "tarkastus", "tiedostot", "kutsuja",
          "askelia", "kesto_s", "kulu_eur", "kulu_tuntematon", "mallit", "rajat", "loki", "verkko", "valmistui")


class SyoteVirhe(Exception):
    """Pyynto ei kelpaa. Viesti on turvallinen tallentaa tulokseen (ei pyynnon sisaltoa)."""


class LukkoVarattu(Exception):
    """Toinen ajuri on jo kaynnissa samalla peruskansiolla."""


def loki(teksti):
    print("aivo-agentit: " + teksti, flush=True)


# ---------------------------------------------------------------------------------------------- kansiot

def tarkista_kansiot(perus, uid=None):
    """Tarkistaa kansioiden omistajat ja oikeudet. Palauttaa listan virheita (tyhja = kunnossa)."""
    uid = os.geteuid() if uid is None else uid
    virheet = []

    def tila(nimi):
        p = os.path.join(perus, nimi) if nimi else perus
        try:
            st = os.lstat(p)
        except OSError:
            virheet.append("%s: puuttuu" % (nimi or "peruskansio"))
            return None
        if not stat.S_ISDIR(st.st_mode):
            virheet.append("%s: ei ole kansio (tai on linkki)" % (nimi or "peruskansio"))
            return None
        if st.st_uid != uid:
            virheet.append("%s: omistaja ei ole palvelun kayttaja" % (nimi or "peruskansio"))
        return st

    p = tila("")
    if p is not None and stat.S_IMODE(p.st_mode) & 0o027:
        virheet.append("peruskansio: oikeudet liian laajat (pitaa olla 0750)")
    for nimi in (TYO, KASITELLYT):
        st = tila(nimi)
        if st is not None and stat.S_IMODE(st.st_mode) & 0o077:
            virheet.append("%s: oikeudet liian laajat (pitaa olla 0700)" % nimi)
    j = tila(JONO)
    if j is not None:
        m = stat.S_IMODE(j.st_mode)
        if m & 0o007 or (m & 0o070) != 0o070 or not m & stat.S_ISGID:
            virheet.append("jono: oikeuksien pitaa olla 2770")
        if p is not None and j.st_gid != p.st_gid:
            virheet.append("jono: ryhma eri kuin peruskansiolla")
    t = tila(TULOKSET)
    if t is not None:
        m = stat.S_IMODE(t.st_mode)
        if m & 0o027 or not m & stat.S_ISGID:
            virheet.append("tulokset: oikeuksien pitaa olla 2750")
        if p is not None and t.st_gid != p.st_gid:
            virheet.append("tulokset: ryhma eri kuin peruskansiolla")
    return virheet


def _palvelun_uid():
    return os.geteuid()


def tarkista_kulukirjanpito(tila=None):
    """Kulukirjanpidon kayttoedellytykset palvelun kayttajana (sama ehto kuin openrouter_kulut-moduulin varauksessa:
    kansio kirjoitettavissa, lisaksi kolme tiedostoa luettavissa ja kirjoitettavissa). Palauttaa listan virheita.
    Ei muuta kirjanpitoa eika sen saantoja; tarkistus vain kertoo esteen ennen ensimmaista tehtavaa."""
    ok = malli.openrouter_kulut
    tila = ok.OLETUS_TILA if tila is None else tila
    if not os.path.isdir(tila) or not os.access(tila, os.W_OK):
        return ["kulukirjanpito: kansioon %s ei voi kirjoittaa (ohje kohta 3)" % tila]
    virheet = []
    for nimi in (ok.KULUT, ok.TAPAHTUMAT, ok.LUKKO):
        p = os.path.join(tila, nimi)
        if not os.path.isfile(p) or os.path.islink(p) or not os.access(p, os.R_OK | os.W_OK):
            virheet.append("kulukirjanpito: %s puuttuu tai ei ole luettavissa ja kirjoitettavissa" % nimi)
        elif os.lstat(p).st_uid == _palvelun_uid():
            # kirjanpito kuuluu chatin kayttajalle; ajuri kirjoittaa siihen vain ryhman kautta (ohje kohta 3)
            virheet.append("kulukirjanpito: %s kuuluu palvelun kayttajalle (pitaa kuulua chatin kayttajalle)" % nimi)
        else:
            # Avataan samoin liputuksin kuin openrouter_kulut (O_CREAT). os.access ei nae esim. fs.protected_regular-
            # estoa sticky-kansiossa (tmpfs mode=1777), joka esti kutsut 25.11.2026. Mitaan ei kirjoiteta.
            liput = (os.O_RDWR if nimi == ok.LUKKO else os.O_WRONLY | os.O_APPEND) | os.O_CREAT
            try:
                os.close(os.open(p, liput, 0o644))
            except OSError as e:
                virheet.append("kulukirjanpito: %s ei aukea kirjoitettavaksi (%s)" % (nimi, type(e).__name__))
    return virheet


class Lukko:
    """Yksi ajuri kerrallaan (flock). Lukko vapautuu, kun prosessi paattyy."""

    def __init__(self, perus):
        self.fd = os.open(os.path.join(perus, LUKKO), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise LukkoVarattu("toinen ajuri on jo kaynnissa") from None

    def vapauta(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


# ------------------------------------------------------------------------------------------------- jono

def jonossa(perus):
    """Kelvolliset pyynnot vanhimmasta uusimpaan: lista (tunnus, nimi). Linkit ja muut nimet ohitetaan."""
    loydetyt = []
    with os.scandir(os.path.join(perus, JONO)) as it:
        for e in it:
            m = JONONIMI_RE.fullmatch(e.name)
            if not m:
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                loydetyt.append((st.st_mtime_ns, e.name, m.group(1)))
    loydetyt.sort()
    return [(tunnus, nimi) for _, nimi, tunnus in loydetyt]


def kelvottomat_jononimet(perus):
    """Lukumaara jonon nimista, joita ajuri ei lue (vain lukumaara lokiin, ei nimia)."""
    n = 0
    with os.scandir(os.path.join(perus, JONO)) as it:
        for e in it:
            if not JONONIMI_RE.fullmatch(e.name) and not e.name.startswith("."):
                n += 1
    return n


def lue_syote(polku):
    """Lukee ja tarkistaa pyynnon. Palauttaa (teksti, liitteet, keskustelu). Virhe: SyoteVirhe."""
    try:
        fd = os.open(polku, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)   # FIFO ei saa jumittaa ajuria
    except OSError:
        raise SyoteVirhe("pyyntoa ei voi avata (pitaa olla 0640, ryhma aivo-agentit)") from None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise SyoteVirhe("pyynto ei ole tavallinen tiedosto")
        raaka = f.read(RAJA_SYOTE_TAVUT + 1)
    if len(raaka) > RAJA_SYOTE_TAVUT:
        raise SyoteVirhe("pyynto on liian suuri")
    try:
        data = json.loads(raaka.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise SyoteVirhe("pyynto ei ole UTF-8-JSONia") from None
    return tarkista_syote(data)


def tarkista_syote(data):
    if not isinstance(data, dict):
        raise SyoteVirhe("pyynnon pitaa olla JSON-olio")
    if set(data) - {"versio", "teksti", "liitteet", "keskustelu"}:
        raise SyoteVirhe("pyynnossa on tuntemattomia kenttia")
    if data.get("versio") != VERSIO or isinstance(data.get("versio"), bool):
        raise SyoteVirhe("pyynnon versio ei kelpaa (pitaa olla %d)" % VERSIO)
    teksti = data.get("teksti")
    if not isinstance(teksti, str) or not teksti.strip():
        raise SyoteVirhe("tehtavan teksti puuttuu")
    if len(teksti) > RAJA_TEKSTI:
        raise SyoteVirhe("tehtavan teksti on liian pitka (enintaan %d merkkia)" % RAJA_TEKSTI)
    keskustelu = data.get("keskustelu", "")
    if not isinstance(keskustelu, str) or (keskustelu and not KESKUSTELU_RE.fullmatch(keskustelu)):
        raise SyoteVirhe("keskustelun tunniste ei kelpaa")
    liitteet = data.get("liitteet", [])
    if not isinstance(liitteet, list):
        raise SyoteVirhe("liitteet pitaa olla lista")
    if len(liitteet) > RAJA_LIITTEET:
        raise SyoteVirhe("liitteita on liikaa (enintaan %d)" % RAJA_LIITTEET)
    nahdyt = set()
    ulos = []
    for i, l in enumerate(liitteet, 1):
        if not isinstance(l, dict) or set(l) != {"nimi", "sisalto"}:
            raise SyoteVirhe("liite %d: pitaa olla {\"nimi\", \"sisalto\"}" % i)
        if not isinstance(l["nimi"], str) or not isinstance(l["sisalto"], str):
            raise SyoteVirhe("liite %d: nimi ja sisalto pitaa olla tekstia" % i)
        try:
            osat = polut.jasenna_polku(l["nimi"])
            polut.tarkista_paate(osat)
        except polut.PolkuVirhe as e:
            raise SyoteVirhe("liite %d: %s" % (i, e)) from None
        avain = "/".join(osat)
        if avain == malli.SUUNNITELMA_TIEDOSTO:
            raise SyoteVirhe("liite %d: nimi on varattu" % i)
        if avain in nahdyt:
            raise SyoteVirhe("liite %d: sama nimi kahdesti" % i)
        nahdyt.add(avain)
        ulos.append((avain, l["sisalto"]))
    return teksti, ulos, keskustelu


# ---------------------------------------------------------------------------------------------- tulokset

def tiedostolista(juuri):
    """Tyokansion tavalliset tiedostot: [{"polku", "koko"}], enintaan polut.RAJA_TIEDOSTOT. Linkkeja ei seurata."""
    ulos = []
    for hak, alihak, nimet in os.walk(juuri, followlinks=False):
        alihak.sort()
        for n in sorted(nimet):
            p = os.path.join(hak, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and not polut.VALIAIKAINEN.fullmatch(n):
                ulos.append({"polku": os.path.relpath(p, juuri), "koko": st.st_size})
    ulos.sort(key=lambda x: x["polku"])
    return ulos[:polut.RAJA_TIEDOSTOT]


def kopioi_tiedostot(perus, tunnus, juuri, lista):
    """Kopioi tiedostolistan tiedostot kansioon tulokset/<tunnus>/ (kansiot 2750, tiedostot 0640), jotta ryhma
    aivo-agentit (chat) voi lukea ne. Tyokansio (0700) ei muutu. Linkkeja ei seurata. Palauttaa kopioidut polut."""
    if not TUNNUS_RE.fullmatch(tunnus):
        raise ValueError("tunnus")
    kohde = os.path.join(perus, TULOKSET, tunnus)
    # Ei chmodia setgid-bitilla: yksikon RestrictSUIDSGID=yes estaa sen (EPERM). Uusi kansio perii setgid-bitin ja
    # ryhman kansiosta tulokset/ (2750). UMask=0077 kavennettaisiin ryhman oikeudet, joten umask on taman ajan 027.
    vanha_umask = os.umask(0o027)
    try:
        return _kopioi(kohde, juuri, lista)
    finally:
        os.umask(vanha_umask)


def _kopioi(kohde, juuri, lista):
    os.mkdir(kohde, 0o750)
    kopioidut = []
    for t in lista:
        osat = t["polku"].split("/")
        hak = kohde
        for osa in osat[:-1]:
            hak = os.path.join(hak, osa)
            if not os.path.lexists(hak):
                os.mkdir(hak, 0o750)
        try:
            lfd = os.open(os.path.join(juuri, *osat), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            continue
        with os.fdopen(lfd, "rb") as lf:
            if not stat.S_ISREG(os.fstat(lf.fileno()).st_mode):
                continue
            data = lf.read(polut.RAJA_TIEDOSTO + 1)
        if len(data) > polut.RAJA_TIEDOSTO:
            continue
        kfd = os.open(os.path.join(hak, osat[-1]), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        with os.fdopen(kfd, "wb") as kf:
            os.fchmod(kf.fileno(), 0o640)
            kf.write(data)
        kopioidut.append(t["polku"])
    return kopioidut


def perustulos(tunnus, keskustelu, tila, syy, mallit, kutsuja=0, kulu_tuntematon=False):
    """Tulos ilman agentin ajoa (pyynto hylatty, aikaraja tai keskeytys)."""
    return {"versio": VERSIO, "tunnus": tunnus, "keskustelu": keskustelu, "tila": tila, "syy": syy,
            "raportti": "", "tarkastus": {}, "tiedostot": [], "kutsuja": kutsuja, "askelia": 0, "kesto_s": 0.0,
            "kulu_eur": 0.0, "kulu_tuntematon": kulu_tuntematon, "mallit": dict(mallit or {}),
            "rajat": dict(malli.RAJAT), "loki": [], "verkko": {}, "valmistui": ""}


def kirjoita_tulos(perus, tunnus, tulos):
    """Kirjoittaa tulokset/<tunnus>.json atomisesti (0640; ryhma periytyy setgid-kansiosta)."""
    if not TUNNUS_RE.fullmatch(tunnus):
        raise ValueError("tunnus")
    tulos = dict(tulos)
    tulos["valmistui"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = (json.dumps({k: tulos.get(k) for k in KENTAT}, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    kansio = os.path.join(perus, TULOKSET)
    valiaikainen = os.path.join(kansio, ".%s.tmp" % tunnus)
    fd = os.open(valiaikainen, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640)
    try:
        os.fchmod(fd, 0o640)            # UMask=0077 ei saa kaventaa ryhman lukuoikeutta
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.unlink(valiaikainen)
        except OSError:
            pass
        raise
    os.replace(valiaikainen, os.path.join(kansio, tunnus + ".json"))


def palauta_keskeneraiset(perus, mallit):
    """Kasitellyt pyynnot ilman tulosta (ajuri pysahtyi kesken) saavat tuloksen KESKEYTETTY. Palauttaa lukumaaran."""
    n = 0
    tulokset = os.path.join(perus, TULOKSET)
    with os.scandir(os.path.join(perus, KASITELLYT)) as it:
        nimet = sorted(e.name for e in it)
    for nimi in nimet:
        m = JONONIMI_RE.fullmatch(nimi)
        if not m or os.path.lexists(os.path.join(tulokset, nimi)):
            continue
        kirjoita_tulos(perus, m.group(1), perustulos(m.group(1), "", malli.KESKEYTETTY, SYY_PYSAHTYI, mallit,
                                                     kulu_tuntematon=True))
        loki("tunnus=%s tila=%s syy=pysahtyi" % (m.group(1), malli.KESKEYTETTY))
        n += 1
    return n


# ------------------------------------------------------------------------------------------------- ajo

def lapsikomento(juuri, roolit, haku_osoite=""):
    k = [sys.executable, "-m", "aivo.agentti_ajuri", "lapsi", "--juuri", juuri, "--roolit", roolit]
    if haku_osoite:
        k += ["--haku-osoite", haku_osoite]
    return k


def hakuasetus(juuri, haku_osoite, haku_avain=None):
    """Lapsen hakuasetus malli.aja-funktiolle tai None. Perus paatellaan tyokansiosta (<perus>/tyo/<tunnus>)."""
    if not haku_osoite:
        return None
    perus = os.path.dirname(os.path.dirname(os.path.abspath(juuri)))
    return {"osoite": haku_osoite, "avain": haku_avain or os.path.join(perus, HAKUAVAIN),
            "loki": os.path.join(perus, HAKULOKI), "tunnus": os.path.basename(os.path.abspath(juuri))}


def _roolit_teksti(mallit):
    return ",".join("%s:%s" % (r, mallit[r]) for r in malli.ROOLIT)


def aja_aliprosessina(komento, teksti, aikaraja_s):
    """Ajaa yhden tehtavan aliprosessina. Palauttaa (tulos-sanakirja tai None, syy)."""
    try:
        p = subprocess.run(komento, input=json.dumps({"teksti": teksti}).encode("utf-8"), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=aikaraja_s,
                           cwd=str(Path(__file__).resolve().parent.parent), check=False)
    except subprocess.TimeoutExpired:
        return None, SYY_AIKARAJA
    except OSError as e:
        return None, "aliprosessia ei voi kaynnistaa (%s)" % type(e).__name__
    if p.returncode != 0:
        return None, "aliprosessi paattyi koodilla %d" % p.returncode
    try:
        d = json.loads(p.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "aliprosessin tulos ei kelpaa"
    if not isinstance(d, dict) or d.get("tila") not in TILAT:
        return None, "aliprosessin tulos ei kelpaa"
    return d, ""


def aja_seuraava(perus, roolit, komento=None, aikaraja_s=PROSESSIN_AIKARAJA_S, kello=time.monotonic, haku_osoite=""):
    """Ajaa jonon vanhimman pyynnon. Palauttaa tulos-sanakirjan tai None, jos jono on tyhja.

    `komento` ja `aikaraja_s` ovat testeja varten; aikaraja voi vain pienentya (enintaan PROSESSIN_AIKARAJA_S).
    """
    mallit = malli.tarkista_roolit(roolit)
    aikaraja_s = min(aikaraja_s, PROSESSIN_AIKARAJA_S)
    jono = jonossa(perus)
    if not jono:
        return None
    tunnus, nimi = jono[0]
    lahde = os.path.join(perus, JONO, nimi)
    kohde = os.path.join(perus, KASITELLYT, nimi)
    juuri = os.path.join(perus, TYO, tunnus)
    if any(os.path.lexists(p) for p in (kohde, juuri, os.path.join(perus, TULOKSET, nimi),
                                        os.path.join(perus, TULOKSET, tunnus))):
        os.rename(lahde, os.path.join(perus, KASITELLYT, "%s.toisto-%d" % (tunnus, time.time_ns())))
        loki("tunnus=%s tila=%s syy=tunnus-jo-kaytetty" % (tunnus, malli.VIRHE))
        return {"tunnus": tunnus, "tila": malli.VIRHE, "syy": "tunnus on jo kaytetty", "kirjoitettu": False}
    try:
        os.rename(lahde, kohde)         # pyynto otetaan kasittelyyn; sama tiedostojarjestelma, atominen
    except FileNotFoundError:           # kirjoittaja poisti tai nimesi pyynnon valilla: ohitetaan
        return {"tunnus": tunnus, "tila": malli.VIRHE, "syy": "pyynto katosi jonosta", "kirjoitettu": False}
    alku = kello()
    keskustelu = ""
    try:
        teksti, liitteet, keskustelu = lue_syote(kohde)
        os.mkdir(juuri, 0o700)
        for i, (polku, sisalto) in enumerate(liitteet, 1):
            t = tyokalut.suorita(juuri, "kirjoita", {"polku": polku, "sisalto": sisalto})
            if not t.ok:
                raise SyoteVirhe("liite %d: %s" % (i, t.teksti))
    except SyoteVirhe as e:
        tulos = perustulos(tunnus, keskustelu, malli.VIRHE, "pyynto: " + str(e), mallit)
    except OSError as e:
        tulos = perustulos(tunnus, keskustelu, malli.VIRHE, "tyokansiota ei voi luoda (%s)" % type(e).__name__,
                           mallit)
    else:
        d, syy = aja_aliprosessina(komento or lapsikomento(juuri, _roolit_teksti(mallit), haku_osoite), teksti,
                                   aikaraja_s)
        if d is None:
            tila = malli.KESKEYTETTY if syy == SYY_AIKARAJA else malli.VIRHE
            tulos = perustulos(tunnus, keskustelu, tila, syy, mallit, kulu_tuntematon=True)
        else:
            tulos = perustulos(tunnus, keskustelu, d["tila"], "", mallit)
            tulos.update({k: d[k] for k in KENTAT if k in d and k not in ("versio", "tunnus", "keskustelu",
                                                                         "tiedostot", "valmistui")})
        tulos["tiedostot"] = tiedostolista(juuri) if os.path.isdir(juuri) else []
        try:
            kopioi_tiedostot(perus, tunnus, juuri, tulos["tiedostot"])
        except OSError as e:            # tulos kirjoitetaan silti; tiedostonakyma kertoo, ettei tiedostoa voi nayttaa
            loki("tunnus=%s tiedostojen kopio epaonnistui (%s)" % (tunnus, type(e).__name__))
    tulos["kesto_s"] = round(kello() - alku, 1)
    try:
        kirjoita_tulos(perus, tunnus, tulos)
    except OSError as e:                # esim. levy taynna: ajuri pysahtyy, palvelu kaynnistyy uudelleen
        loki("tunnus=%s tuloksen kirjoitus epaonnistui (%s)" % (tunnus, type(e).__name__))
        raise
    loki("tunnus=%s tila=%s kutsuja=%s kulu_eur=%s kesto_s=%s" % (
        tunnus, tulos["tila"], tulos.get("kutsuja"), tulos.get("kulu_eur"), tulos["kesto_s"]))
    tulos["kirjoitettu"] = True
    return tulos


def palvelu(perus, roolit, odotus_s=ODOTUS_S, kierroksia=None, kulutila=None, haku_osoite=""):
    """Pitkaikainen silmukka: yksi tehtava kerrallaan. `kierroksia` ja `kulutila` ovat testeja varten."""
    mallit = malli.tarkista_roolit(roolit)
    paattely_max_ymparistosta()          # kelvoton arvo -> AgenttiVirhe (main: asetusvirhe, koodi 2)
    virheet = tarkista_kansiot(perus) + tarkista_kulukirjanpito(kulutila)
    if virheet:
        for v in virheet:
            loki("kansiovirhe: " + v)
        return 1
    lukko = Lukko(perus)
    try:
        palauta_keskeneraiset(perus, mallit)
        nahty_kelvottomia = 0
        n = 0
        while kierroksia is None or n < kierroksia:
            n += 1
            kelvottomat = kelvottomat_jononimet(perus)
            if kelvottomat != nahty_kelvottomia:
                if kelvottomat:
                    loki("jonossa %d tiedostoa, joiden nimi ei kelpaa (ohitetaan)" % kelvottomat)
                nahty_kelvottomia = kelvottomat
            if aja_seuraava(perus, mallit, haku_osoite=haku_osoite) is None:
                time.sleep(odotus_s)
        return 0
    finally:
        lukko.vapauta()


def paattely_max_ymparistosta(env=None):
    """AIVO_YKSITYINEN_PAATTELY_MAX ymparistosta tarkistettuna (malli.tarkista_paattely_max). Virhe: AgenttiVirhe."""
    return malli.tarkista_paattely_max((os.environ if env is None else env).get(PAATTELY_MUUTTUJA))


def lapsi(juuri, roolit, syote=None, haku_osoite="", **kw):
    """Aliprosessin runko: lukee {"teksti"} syotteesta, ajaa agentin ja palauttaa tuloksen sanakirjana.
    Paattelyn ylaraja luetaan ymparistosta (PAATTELY_MUUTTUJA), ellei `paattely_max` ole annettu."""
    kw.setdefault("paattely_max", os.environ.get(PAATTELY_MUUTTUJA))
    data = json.load(syote if syote is not None else sys.stdin)
    teksti = data["teksti"] if isinstance(data, dict) and isinstance(data.get("teksti"), str) else ""
    if not teksti:
        return perustulos("", "", malli.VIRHE, "tehtavan teksti puuttuu", {})
    return malli.aja(teksti, juuri, roolit, haku=hakuasetus(juuri, haku_osoite), **kw).sanakirja()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m aivo.agentti_ajuri", description=__doc__.splitlines()[0])
    ali = ap.add_subparsers(dest="komento", required=True)
    p = ali.add_parser("palvelu", help="lue jonoa ja aja tehtavat yksi kerrallaan")
    p.add_argument("--perus", default=OLETUS_PERUS)
    p.add_argument("--roolit", required=True, help="malli tai suunnittelija:m,tekija:m,tarkastaja:m")
    p.add_argument("--haku-osoite", default=os.environ.get("AIVO_HAKU_OSOITE", ""),
                   help="hakuvalittajan osoite http://isanta:portti; tyhja = ei verkkotyokaluja (oletus)")
    t = ali.add_parser("tarkista", help="tarkista kansioiden oikeudet ja rooliasetus")
    t.add_argument("--perus", default=OLETUS_PERUS)
    t.add_argument("--roolit")
    c = ali.add_parser("lapsi", help="sisainen: yhden tehtavan aliprosessi")
    c.add_argument("--juuri", required=True)
    c.add_argument("--roolit", required=True)
    c.add_argument("--haku-osoite", default="")
    a = ap.parse_args(argv)
    try:
        if a.komento == "lapsi":
            print(json.dumps(lapsi(a.juuri, a.roolit, haku_osoite=a.haku_osoite), ensure_ascii=True))
            return 0
        if a.komento == "tarkista":
            virheet = tarkista_kansiot(a.perus) + tarkista_kulukirjanpito()
            try:
                paattely_max_ymparistosta()
            except malli.AgenttiVirhe as e:
                virheet.append("%s: %s" % (PAATTELY_MUUTTUJA, e))
            if a.roolit:
                try:
                    malli.tarkista_roolit(a.roolit)
                except malli.AgenttiVirhe as e:
                    virheet.append("roolit: %s" % e)
            for v in virheet:
                print("VIRHE " + v)
            print("OK" if not virheet else "VIRHEITA %d" % len(virheet))
            return 0 if not virheet else 1
        return palvelu(a.perus, a.roolit, haku_osoite=a.haku_osoite)
    except malli.AgenttiVirhe as e:
        loki("asetusvirhe: %s" % e)
        return 2
    except LukkoVarattu as e:
        loki(str(e))
        return 3
    except Exception as e:     # vain tyypin nimi, ei viestia (voi sisaltaa polkuja tai sisaltoa)
        loki("virhe (%s)" % type(e).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
