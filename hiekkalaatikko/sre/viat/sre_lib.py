"""SRE-vikakirjaston yhteinen kirjasto. Vain vakiokirjasto, ei verkkoa, ei salaisuuksia.

Jokainen vika (v0N_*.py) on oma skriptinsa ja tarjoaa kolme komentoa:
  injektoi  rikkoo palvelun (VAIN kertakayttoklooni: isantanimi hl-9100..hl-9199, muuten kieltaytyy)
  tarkista  ratkaisee onnistumisen kayttamalla palvelua: poistumiskoodi 0 = korjattu, 1 = ei korjattu
  oire      tulostaa oireen, jonka agentti saa (ei vikaa, ei ratkaisua)
Ymparistoa (juuri, komentojen ajo, isantanimi) kuvaa Ymp, jotta tarkistukset voi testata fixtureilla ilman oikeaa konttia.
"""
import os
import re
import socket
import subprocess
import sys

KLOONI_NIMI = re.compile(r"^hl-(9[1][0-9]{2})$")
TULOSTE_MAX = 4000
POLKU = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def oikea_ajo(komento, aika=60):
    try:
        r = subprocess.run(list(komento), capture_output=True, text=True, timeout=aika, env={"PATH": POLKU, "LC_ALL": "C"})
    except subprocess.TimeoutExpired:
        return 124, "aikakatko"
    except OSError as e:
        return 127, type(e).__name__
    return r.returncode, (r.stdout + r.stderr)[:TULOSTE_MAX]


class Ymp:
    def __init__(self, juuri="/", ajo=None, hostname=None):
        self.juuri = juuri
        self._ajo = ajo or oikea_ajo
        self._hostname = hostname

    def hostname(self):
        return self._hostname if self._hostname is not None else socket.gethostname()

    def turvallinen(self):
        """Vain hiekkalaatikon kertakayttoklooni (hl-9100..hl-9199). Tuotantokonttiin ei kosketa."""
        m = KLOONI_NIMI.match(self.hostname())
        return bool(m) and 9100 <= int(m.group(1)) <= 9199

    def p(self, polku):
        return os.path.join(self.juuri, polku.lstrip("/"))

    def kirjoita(self, polku, teksti, mode=0o644):
        t = self.p(polku)
        os.makedirs(os.path.dirname(t), exist_ok=True)
        with open(t, "w", encoding="ascii") as f:
            f.write(teksti)
        os.chmod(t, mode)

    def lue(self, polku):
        try:
            with open(self.p(polku), encoding="ascii", errors="replace") as f:
                return f.read(100000)
        except OSError:
            return None

    def on(self, polku):
        return os.path.exists(self.p(polku))

    def poista(self, polku):
        try:
            os.unlink(self.p(polku))
        except FileNotFoundError:
            pass

    def mode(self, polku):
        return os.stat(self.p(polku)).st_mode & 0o7777

    def aja(self, *komento, aika=60):
        return self._ajo(komento, aika)


# ---------- yhteiset tarkistusapurit ----------

def sisaltaa(y, polku, *kuviot):
    """Palauttaa puuttuvan kuvion kuvauksen tai None. Estaa 'korjauksen', jossa tyokuorma itse poistetaan tai heikennetaan."""
    teksti = y.lue(polku)
    if teksti is None:
        return "%s puuttuu" % polku
    for k in kuviot:
        if not re.search(k, teksti, re.M):
            return "%s: vaadittu rakenne puuttuu (%s)" % (polku, k)
    return None


def kaynnista_ja_varmista(y, yksikko, merkki):
    """Kaynnistaa oneshot-yksikon uudelleen ja vaatii, etta tyokuorma kirjasi onnistumismerkin. Palauttaa (ok, syy)."""
    y.poista(merkki)
    rc, ulos = y.aja("systemctl", "start", yksikko, aika=90)
    if rc != 0:
        return False, "%s ei kaynnisty (systemctl start rc=%d)" % (yksikko, rc)
    rc, tulos = y.aja("systemctl", "show", "-p", "Result", "--value", yksikko)
    if rc != 0 or tulos.strip() != "success":
        return False, "%s Result=%s" % (yksikko, tulos.strip()[:40])
    if not y.on(merkki):
        return False, "tyokuorma ei kirjannut onnistumista (%s)" % merkki
    return True, "ok"


def yksikko_teksti(kuvaus, palvelu_rivit, asennus=None):
    s = "[Unit]\nDescription=%s\n\n[Service]\n%s\n" % (kuvaus, "\n".join(palvelu_rivit))
    if asennus:
        s += "\n[Install]\n%s\n" % "\n".join(asennus)
    return s


# ---------- komentorivi ----------

def kaynnista(modul, argv=None, y=None, ulos=None):
    """Vikaskriptin main. Palauttaa poistumiskoodin: 0 ok, 1 ei korjattu, 2 kaytto, 78 ei turvallinen ymparisto, 3 injektointi epaonnistui."""
    ulos = ulos or sys.stdout
    argv = list(sys.argv[1:] if argv is None else argv)
    y = y or Ymp()
    if len(argv) != 1 or argv[0] not in ("injektoi", "tarkista", "oire"):
        print("kaytto: %s injektoi|tarkista|oire" % modul.TUNNUS, file=sys.stderr)
        return 2
    if argv[0] == "oire":
        print(modul.OIRE, file=ulos)
        return 0
    if not y.turvallinen():
        print("kieltaydyn: isantanimi ei ole hiekkalaatikon klooni (hl-9100..hl-9199)", file=sys.stderr)
        return 78
    if argv[0] == "injektoi":
        try:
            modul.injektoi(y)
        except (OSError, RuntimeError) as e:
            print("injektointi epaonnistui: %s" % type(e).__name__, file=sys.stderr)
            return 3
        print("injektoitu: %s" % modul.TUNNUS, file=ulos)
        return 0
    ok, syy = modul.tarkista(y)
    print(("OK: " if ok else "VIKA: ") + syy, file=ulos)
    return 0 if ok else 1


def vaadi_ok(y, *komento):
    rc, ulos = y.aja(*komento)
    if rc != 0:
        raise RuntimeError("komento epaonnistui: %s rc=%d" % (komento[0], rc))
    return ulos
