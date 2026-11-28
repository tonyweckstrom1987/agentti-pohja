"""Aivon asennuksen tilatarkistus (vain luku). Stdlib.

Kaytto: python3 -m aivo.tarkista_asennus ODOTETTU_COMMIT [--koodi /opt/agentti-pohja]
         [--yksityinen-juuri /var/lib/aivo-yksityinen] [--tyokalu-juuri /var/lib/aivo]
         [--tausta-jono /var/lib/aivo-yksityinen/tyot] [--ohita-taustaajo] [--ohita-agentit]
Tuloste: yksi rivi per tarkistus (OK/VIKA/OHITETTU + lyhyt syy). Paluuarvo 0 vain, jos yhtaan VIKA-rivia ei ole.
Taustaajo (aivo-taustaajo.timer/.service), Syvenna-polku (chatin AIVO_TAUSTA_JONO tai --tausta-jono) ja
agenttipalvelu (aivo-agentit.service) tarkistetaan vain, jos yksikko on asennettu (systemctl show -p LoadState);
muuten tulostuu yksi OHITETTU-rivi. Taustajonosta vain os.lstat (ei listausta, tyo- tai vastaustiedostoja ei avata).
Taustaajon tila luetaan vain taustaajo.lue_tila-funktiolla, ja tulosteeseen menevat vain paiva, ajettu-luku ja raja.
Avaintiedoston sisaltoa ei koskaan avata: vain os.stat (olemassaolo ja oikeudet).
Tiivistyskansioista (molempien alueiden TIIVISTYS_ALIKANSIOT) vain os.lstat: kansioita ei listata, niiden tiedostoja
ei avata, eika mitaan luoda tai muuteta. Puuttuvasta kansiosta tulostetaan korjausvihje (install -d)."""
import argparse
import http.client
import os
import pwd
import stat
import subprocess
import re
import sys
from datetime import datetime

from aivo import taustaajo, tiivistelma, tyokalualue, yksityinen_alue

OLETUS_KOODI = "/opt/agentti-pohja"
OLETUS_PORTTI = 8780
CHAT_PALVELU = "aivo-chat"
TIIVISTYS_AJASTIN = "aivo-tiivista.timer"
TAUSTA_AJASTIN = "aivo-taustaajo.timer"
TAUSTA_PALVELU = "aivo-taustaajo.service"
AGENTTI_PALVELU = "aivo-agentit.service"
AVAINTIEDOSTOT = ("/etc/aivo/chat-salasana",)
MODUULIT = ("aivo.chat", "aivo.tiivista", "aivo.tiivistelma")
AIKARAJA = 10
OMISTAJA_AVOIN = "kayttajanimi on Tonyn avoin paatos (aivo vai aivo-yksityinen), ks. docs/aivo-chat.md"


def aja(komento):
    """Oletusajuri: (paluukoodi, stdout). Testit korvaavat taman vaarennetulla."""
    try:
        p = subprocess.run(komento, capture_output=True, text=True, timeout=AIKARAJA)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, type(e).__name__
    return p.returncode, p.stdout.strip()


def http_get(portti, polku):
    """Oletus-HTTP: (tila, runko)."""
    yhteys = http.client.HTTPConnection("127.0.0.1", portti, timeout=AIKARAJA)
    try:
        yhteys.request("GET", polku)
        r = yhteys.getresponse()
        return r.status, r.read(200).decode("utf-8", "replace")
    finally:
        yhteys.close()


def tarkista_commit(odotettu, koodi, ajuri):
    rc, ulos = ajuri(["git", "-C", koodi, "rev-parse", "HEAD"])
    if rc != 0:
        return False, "commitia ei saatu (%s)" % koodi
    if ulos == odotettu or (len(odotettu) >= 7 and ulos.startswith(odotettu)):
        return True, ulos[:12]
    return False, "asennettu %s, odotettu %s" % (ulos[:12], odotettu[:12])


def tarkista_palvelu(nimi, ajuri):
    rc, ulos = ajuri(["systemctl", "is-active", nimi])
    tila = ulos.splitlines()[0] if ulos else "tuntematon"
    return rc == 0 and tila == "active", tila


def tarkista_terve(portti, hae):
    try:
        tila, runko = hae(portti, "/terve")
    except (OSError, http.client.HTTPException) as e:
        return False, "ei yhteytta (%s)" % type(e).__name__
    if tila != 200:
        return False, "HTTP %s" % tila
    return True, "HTTP 200"


def tarkista_moduulit(koodi):
    """Moduulit tarkistetaan tiedostoina --koodi-polusta: tiedosto on olemassa ja kaantyy.

    Tuontia ei kayteta, koska (1) aivo on jo sys.modules-sanakirjassa, joten --koodi ei
    vaikuttaisi, ja (2) tuonti ajaisi moduulin tuontiaikaisen koodin. compile() ei aja koodia."""
    puuttuu = []
    for nimi in MODUULIT:
        polku = os.path.join(koodi, *nimi.split(".")) + ".py"
        try:
            with open(polku, "rb") as f:
                lahde = f.read()
            compile(lahde, polku, "exec", dont_inherit=True)
        except FileNotFoundError:
            puuttuu.append("%s (puuttuu)" % nimi)
        except Exception as e:   # vain tyyppi, viesti voi sisaltaa salaisuuksia
            puuttuu.append("%s (%s)" % (nimi, type(e).__name__))
    return (not puuttuu), ("; ".join(puuttuu) if puuttuu else "%d moduulia" % len(MODUULIT))


def tarkista_avain(polku):
    """Vain os.stat: olemassaolo, tavallinen tiedosto, ei ryhma-/muiden oikeuksia."""
    try:
        st = os.stat(polku)
    except FileNotFoundError:
        return False, "puuttuu"
    except PermissionError:
        return False, "ei oikeutta tarkistaa (PermissionError)"
    except OSError as e:
        return False, type(e).__name__
    if not stat.S_ISREG(st.st_mode):
        return False, "ei tavallinen tiedosto"
    tila = stat.S_IMODE(st.st_mode)
    if tila & 0o077:
        return False, "oikeudet %o (pitaa olla 600)" % tila
    return True, "oikeudet %o" % tila


def oletusalueet(yksityinen_juuri=yksityinen_alue.OLETUS_JUURI, tyokalu_juuri=tyokalualue.OLETUS_JUURI):
    """Alueet, joiden tiivistyskansiot tarkistetaan. Juuret ja alikansiot tulevat aluemoduuleista."""
    return (yksityinen_alue.yksityinen_alue(yksityinen_juuri), tyokalualue.tyokalualue(tyokalu_juuri))


def _juuren_omistaja(juuri):
    """(omistajan nimi tai None, syy). Vain os.lstat; nimi pwd:sta, tuntematon uid numerona."""
    try:
        st = os.lstat(juuri)
    except FileNotFoundError:
        return None, "alueen juuri %s puuttuu" % juuri
    except OSError as e:
        return None, "alueen juurta %s ei voi tarkistaa (%s)" % (juuri, type(e).__name__)
    try:
        return pwd.getpwuid(st.st_uid).pw_name, ""
    except KeyError:
        return str(st.st_uid), ""


def korjausvihje(polku, omistaja):
    """Komento, jonka Tony voi ajaa itse. Tarkistus ei aja sita. Tuntematon omistaja => <omistaja>."""
    o = omistaja or "<omistaja>"
    vihje = "korjaus: install -d -m 700 -o %s -g %s %s" % (o, o, polku)
    if omistaja is None:
        vihje += " (" + OMISTAJA_AVOIN + ")"
    return vihje


def tarkista_kansio(alue_, alikansio):
    """Alikansio on olemassa, oikea hakemisto (ei linkki) ja tila & 0o077 == 0. Vain os.lstat polkuun."""
    polku = alue_.polku(alikansio)
    try:
        st = os.lstat(polku)
    except FileNotFoundError:
        omistaja, syy = _juuren_omistaja(alue_.juuri)
        alku = "puuttuu (%s)" % syy if syy else "puuttuu"
        return False, "%s; %s" % (alku, korjausvihje(polku, omistaja))
    except PermissionError:
        return False, "ei oikeutta tarkistaa (PermissionError); aja alueen juuren omistajana"
    except OSError as e:
        return False, type(e).__name__
    if stat.S_ISLNK(st.st_mode):
        return False, "%s on linkki, ei hakemisto" % polku
    if not stat.S_ISDIR(st.st_mode):
        return False, "%s ei ole hakemisto" % polku
    tila = stat.S_IMODE(st.st_mode)
    if tila & 0o077:
        return False, "oikeudet %o (ryhmalla tai muilla ei saa olla oikeuksia); korjaus: chmod 700 %s" % (tila, polku)
    return True, "hakemisto, oikeudet %o (%s)" % (tila, polku)


# --- Taustaajo, Syvenna ja agenttipalvelu. (None, syy) = OHITETTU: ei vaikuta paluuarvoon. -------------------------

def _show(yksikko, ominaisuus, ajuri):
    """Yhden systemd-ominaisuuden arvo (vain luku). Virhe -> None."""
    rc, ulos = ajuri(["systemctl", "show", "-p", ominaisuus, "--value", yksikko])
    return ulos.strip() if rc == 0 else None


def asennettu(yksikko, ajuri):
    """LoadState: "loaded" -> (True, tila), "not-found" -> (None, tila) eli ei asennettu (OHITETTU), muu
    (masked, bad-setting, error, kysely epaonnistui) -> (False, tila) eli asennus on rikki (VIKA)."""
    tila = _show(yksikko, "LoadState", ajuri) or "tuntematon"
    if tila == "loaded":
        return True, tila
    return (None if tila == "not-found" else False), tila


def tarkista_kaytossa(yksikko, ajuri):
    """Yksikko on enabled ja active (ajastin tai pitkakestoinen palvelu); kumpikin saman ajurin kautta."""
    rc1, ulos1 = ajuri(["systemctl", "is-enabled", yksikko])
    rc2, ulos2 = ajuri(["systemctl", "is-active", yksikko])
    e = ulos1.splitlines()[0] if ulos1 else "tuntematon"
    a = ulos2.splitlines()[0] if ulos2 else "tuntematon"
    return (rc1 == 0 and e == "enabled" and rc2 == 0 and a == "active"), "%s, %s" % (e, a)


def tarkista_oneshot(yksikko, ajuri):
    """Ajastimen kaynnistama oneshot-palvelu: viimeisen ajon Result=success (myos ennen ensimmaista ajoa)."""
    tulos = _show(yksikko, "Result", ajuri)
    rc, ulos = ajuri(["systemctl", "is-active", yksikko])
    tila = ulos.splitlines()[0] if ulos else "tuntematon"
    if tulos is None:
        return False, "Result-arvoa ei saatu"
    return tulos == "success", "viimeisin ajo %s, nyt %s" % (tulos, tila)


def _palvelun_kayttaja(yksikko, ajuri):
    return _show(yksikko, "User", ajuri) or "root"


def chatin_tausta_jono(ajuri):
    """Chatin taustajonon polku systemd-asetuksesta: ExecStartin --tausta-jono voittaa AIVO_TAUSTA_JONO-ymparistomuuttujan
    (sama jarjestys kuin argparse). Ymparistoa ei tulosteta: siita poimitaan vain tama yksi arvo."""
    exe = _show(CHAT_PALVELU, "ExecStart", ajuri) or ""
    m = re.search(r"--tausta-jono(?:=|\s+)([^\s;]+)", exe)
    if m:
        return m.group(1)
    env = _show(CHAT_PALVELU, "Environment", ajuri) or ""
    m = re.search(r"(?:^|\s)\"?AIVO_TAUSTA_JONO=([^\s\"]*)", env)
    return m.group(1) if m else ""


def tarkista_syvenna(jono, ajuri):
    """Chatilla on sama taustajono kuin tarkistettavalla ajurilla, ja chat ajetaan samana kayttajana kuin taustaajo."""
    chatin = chatin_tausta_jono(ajuri)
    if not chatin:
        return False, "chatilla ei ole AIVO_TAUSTA_JONO- eika --tausta-jono-asetusta (Syvenna-painike pois kaytosta)"
    if os.path.normpath(chatin) != os.path.normpath(jono):
        return False, "chatin jono %s, tarkistettava jono %s" % (chatin, jono)
    k_chat = _palvelun_kayttaja(CHAT_PALVELU, ajuri)
    k_tausta = _palvelun_kayttaja(TAUSTA_PALVELU, ajuri)
    if k_chat != k_tausta:
        return False, "chatin kayttaja %s, taustaajon kayttaja %s (pitaa olla sama)" % (k_chat, k_tausta)
    return True, "jono %s, kayttaja %s" % (chatin, k_chat)


def tarkista_tausta_kansio(jono, omistaja):
    """Jono on olemassa, oikea hakemisto (ei linkki), tila 0700 ja omistaja on taustaajon kayttaja. Vain os.lstat."""
    try:
        st = os.lstat(jono)
    except FileNotFoundError:
        return False, "puuttuu; korjaus: install -d -m 700 -o %s -g %s %s" % (omistaja, omistaja, jono)
    except PermissionError:
        return False, "ei oikeutta tarkistaa (PermissionError); aja taustaajon kayttajana"
    except OSError as e:
        return False, type(e).__name__
    if stat.S_ISLNK(st.st_mode):
        return False, "on linkki, ei hakemisto"
    if not stat.S_ISDIR(st.st_mode):
        return False, "ei ole hakemisto"
    tila = stat.S_IMODE(st.st_mode)
    try:
        nimi = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        nimi = str(st.st_uid)
    if tila != 0o700:
        return False, "oikeudet %o (pitaa olla 700); korjaus: chmod 700 %s" % (tila, jono)
    if nimi != omistaja:
        return False, "omistaja %s (pitaa olla taustaajon kayttaja %s)" % (nimi, omistaja)
    return True, "hakemisto, oikeudet 700, omistaja %s" % nimi


def tarkista_tausta_tila(jono, nyt=None):
    """Vain taustaajo.lue_tila. Tulosteeseen paiva, ajettu ja raja; virheesta vain tyypin nimi (ei viestia)."""
    nyt = nyt or datetime.now(taustaajo.TZ)
    try:
        t = taustaajo.lue_tila(jono, nyt)
    except Exception as e:
        return False, "tila ei kelpaa (%s)" % type(e).__name__
    raja = t.get("paivaraja")
    return True, "paiva %s, ajettu %d, raja %s" % (t["paiva"], t["ajettu"], raja if raja is not None else "ei kirjattu")


def _ohitettu(syy):
    return lambda: (None, syy)


def tausta_tarkistukset(jono, ajuri, nyt=None):
    """Taustaajon ja Syvenna-polun tarkistukset, tai yksi OHITETTU-rivi, jos ajastinta ei ole asennettu."""
    if jono is None:
        yield "taustaajo", _ohitettu("ohitettu lipulla --ohita-taustaajo")
        return
    try:
        on, tila = asennettu(TAUSTA_AJASTIN, ajuri)
    except Exception as e:
        yield "taustaajo", (lambda e=e: (False, "poikkeus " + type(e).__name__))
        return
    if on is None:
        yield "taustaajo", _ohitettu("%s ei ole asennettu (LoadState %s)" % (TAUSTA_AJASTIN, tila))
        return
    if not on:
        yield "taustaajo", (lambda tila=tila: (False, "%s on rikki (LoadState %s)" % (TAUSTA_AJASTIN, tila)))
        return
    yield "ajastin " + TAUSTA_AJASTIN, lambda: tarkista_kaytossa(TAUSTA_AJASTIN, ajuri)
    yield "palvelu " + TAUSTA_PALVELU, lambda: tarkista_oneshot(TAUSTA_PALVELU, ajuri)
    yield "syvenna " + CHAT_PALVELU, lambda: tarkista_syvenna(jono, ajuri)
    yield "taustajono " + jono, lambda: tarkista_tausta_kansio(jono, _palvelun_kayttaja(TAUSTA_PALVELU, ajuri))
    yield "taustaajon tila", lambda: tarkista_tausta_tila(jono, nyt)


def agentti_tarkistukset(kaytossa, ajuri):
    if not kaytossa:
        yield "agenttipalvelu", _ohitettu("ohitettu lipulla --ohita-agentit")
        return
    try:
        on, tila = asennettu(AGENTTI_PALVELU, ajuri)
    except Exception as e:
        yield "agenttipalvelu", (lambda e=e: (False, "poikkeus " + type(e).__name__))
        return
    if on is None:
        yield "agenttipalvelu", _ohitettu("%s ei ole asennettu (LoadState %s)" % (AGENTTI_PALVELU, tila))
        return
    if not on:
        yield "agenttipalvelu", (lambda tila=tila: (False, "%s on rikki (LoadState %s)" % (AGENTTI_PALVELU, tila)))
        return
    yield "palvelu " + AGENTTI_PALVELU, lambda: tarkista_kaytossa(AGENTTI_PALVELU, ajuri)


def tarkistukset(odotettu, koodi, portti, ajuri, hae, avaimet, alueet=(), tausta_jono=None, agentit=False, nyt=None):
    yield "commit", lambda: tarkista_commit(odotettu, koodi, ajuri)
    yield "palvelu " + CHAT_PALVELU, lambda: tarkista_palvelu(CHAT_PALVELU, ajuri)
    yield "ajastin " + TIIVISTYS_AJASTIN, lambda: tarkista_palvelu(TIIVISTYS_AJASTIN, ajuri)
    yield "terve /terve", lambda: tarkista_terve(portti, hae)
    yield "moduulit", lambda: tarkista_moduulit(koodi)
    for a in avaimet:
        yield "avain " + a, (lambda a=a: tarkista_avain(a))
    for al in alueet:
        for k in tiivistelma.TIIVISTYS_ALIKANSIOT:
            yield "kansio %s/%s" % (al.nimi, k), (lambda al=al, k=k: tarkista_kansio(al, k))
    yield from tausta_tarkistukset(tausta_jono, ajuri, nyt)
    yield from agentti_tarkistukset(agentit, ajuri)


def main(argv=None, ajuri=aja, hae=http_get, avaimet=AVAINTIEDOSTOT, tulosta=print, alueet=None, nyt=None):
    ap = argparse.ArgumentParser(prog="aivo.tarkista_asennus", description=__doc__.split("\n")[0])
    ap.add_argument("odotettu_commit", help="odotettu commit (koko tai vahintaan 7 merkkia)")
    ap.add_argument("--koodi", default=OLETUS_KOODI)
    ap.add_argument("--portti", type=int, default=OLETUS_PORTTI)
    ap.add_argument("--yksityinen-juuri", default=yksityinen_alue.OLETUS_JUURI, help="yksityisen alueen juuri")
    ap.add_argument("--tyokalu-juuri", default=tyokalualue.OLETUS_JUURI, help="tyokalualueen juuri")
    ap.add_argument("--tausta-jono", default=taustaajo.OLETUS_JONO, help="taustaajon jono (oletus %(default)s)")
    ap.add_argument("--ohita-taustaajo", action="store_true", help="ala tarkista taustaajoa eika Syvenna-polkua")
    ap.add_argument("--ohita-agentit", action="store_true", help="ala tarkista agenttipalvelua")
    a = ap.parse_args(argv)
    if alueet is None:
        alueet = oletusalueet(a.yksityinen_juuri, a.tyokalu_juuri)
    kaikki_ok = True
    jono = None if a.ohita_taustaajo else a.tausta_jono
    for nimi, tarkistus in tarkistukset(a.odotettu_commit, a.koodi, a.portti, ajuri, hae, avaimet, alueet,
                                        tausta_jono=jono, agentit=not a.ohita_agentit, nyt=nyt):
        try:
            ok, syy = tarkistus()
        except Exception as e:
            ok, syy = False, "poikkeus " + type(e).__name__
        if ok is not None:
            kaikki_ok = kaikki_ok and ok
        tulosta("%-4s %s: %s" % ("OHITETTU" if ok is None else ("OK" if ok else "VIKA"), nimi, syy))
    return 0 if kaikki_ok else 1


if __name__ == "__main__":
    sys.exit(main())
