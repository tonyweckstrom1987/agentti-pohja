"""Aivon keskustelumuisti, osa 5: FTS5-haku tiivistelmiin (suunnitelma kohdat 3.4 ja 8).

Moduuli on tilaneutraali: se saa `Alue`-olion eika tunne yhtaan oletuspolkua. Jokaisella alueella on oma indeksi
(SQLite-tiedosto alueen `indeksi`-kansiossa), joten alueiden sisallot eivat voi sekoittua. Vain stdlib, ei mallikutsuja,
ei verkkoa, ei alaprosesseja. Hakua ei ole kytketty chattiin eika kehotteeseen.

  * Tiivistelman tekstit (Tonyn lainaukset, tulkinta ja korjaus) pilkotaan 800-1 500 merkin paloihin sanarajoilla.
  * Sanat pienennetaan ja katkaistaan 6 merkkiin (karkea suomen vartalo), seka indeksissa etta kyselyssa.
  * Kysely rakennetaan itse: jokainen sana lainausmerkeissa, joten FTS5:n erikoismerkit eivat koskaan paase syntaksiksi.
  * Tulos sisaltaa lahdeviitteen (tiivistelman tunnus). Tulos on DATAA, ei ohjeita.

Kaytto:
  python3 -m aivo.haku rakenna --alue tyokalu|yksityinen [--juuri KANSIO]
  python3 -m aivo.haku hae --alue tyokalu|yksityinen [--juuri KANSIO] [--k 5] KYSELY...
"""
import argparse
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from aivo import tiivistelma
from aivo.alue import AlueVirhe, tarkista_hakemisto

INDEKSI_NIMI = "tiivistelmat.sqlite"
KAYTETYT_ALIKANSIOT = ("tiivistelmat", "indeksi")   # rakennus lukee ja kirjoittaa vain nama; muut jaavat tarkistamatta
PALA_TAVOITE = 1000          # merkkia; pilkonta sanarajalla
PALA_MIN = 800
PALA_MAX = 1500
VARTALO = 6                  # sanan katkaisu (suunnitelma 3.1)
MAX_SANOJA_KYSELYSSA = 20
MAX_KYSELY = 500
MAX_K = 50
SANA_RE = re.compile(r"\w+", re.UNICODE)


class HakuVirhe(Exception):
    """Odotettu virhe; viesti ei sisalla tiivistelmien sisaltoa eika polkuja."""


def sanat(teksti):
    """Pienet kirjaimet, vain sana-merkit, katkaisu VARTALO merkkiin. Duplikaatit sailyvat (BM25 hyodyntaa niita)."""
    return [s[:VARTALO] for s in SANA_RE.findall(str(teksti).lower())]


def pilko(teksti, tavoite=PALA_TAVOITE, maksimi=PALA_MAX):
    """Jakaa tekstin paloihin sanarajoilla: pala paattyy kun tavoite on saavutettu, eika yksikaan sana halkea.
    Yli maksimin pitka yksittainen 'sana' leikataan maksimiin. Viimeinen pala voi olla lyhyt (alle PALA_MIN), mutta
    liitetaan edelliseen, jos yhteispituus mahtuu maksimiin."""
    paloja, nyk = [], ""
    for sana in str(teksti).split():
        while len(sana) > maksimi:
            if nyk:
                paloja.append(nyk)
                nyk = ""
            paloja.append(sana[:maksimi])
            sana = sana[maksimi:]
        uusi = (nyk + " " + sana) if nyk else sana
        if len(uusi) > maksimi:
            paloja.append(nyk)
            nyk = sana
        elif len(uusi) >= tavoite:
            paloja.append(uusi)
            nyk = ""
        else:
            nyk = uusi
    if nyk:
        if paloja and len(paloja[-1]) + 1 + len(nyk) <= maksimi and len(nyk) < PALA_MIN:
            paloja[-1] += " " + nyk
        else:
            paloja.append(nyk)
    return paloja


def tiivistelman_teksti(t):
    """Tiivistelman haettava teksti: Tonyn lainaukset, tulkinta ja Tonyn korjaus. Kentat on tarkistettu jo tallennettaessa;
    tassa luetaan puolustavasti (vaara tyyppi ohitetaan)."""
    osat = []
    for k in t.get("kohdat", []):
        if not isinstance(k, dict):
            continue
        for q in k.get("tony_sanoi", []) or []:
            if isinstance(q, str):
                osat.append(q)
        for kentta in ("tony_korjaus", "tulkinta"):
            if isinstance(k.get(kentta), str):
                osat.append(k[kentta])
    return " . ".join(osat)


def indeksin_polku(alue):
    return alue.polku("indeksi") / INDEKSI_NIMI


def _fts_kysely(teksti):
    """Rakentaa FTS5-kyselyn itse: jokainen vartalo omana lainattuna merkkijonona, OR-yhdistettyna. Vartalot ovat vain
    sana-merkkeja, joten lainausmerkit ja operaattorit (AND, NOT, NEAR, *, :, ^) eivat voi toimia syntaksina."""
    s = []
    for x in sanat(str(teksti)[:MAX_KYSELY]):
        if x not in s:
            s.append(x)
        if len(s) >= MAX_SANOJA_KYSELYSSA:
            break
    return " OR ".join('"%s"' % x for x in s)


def rakenna(alue):
    """Rakentaa alueen indeksin tyhjasta uudelleen alueen tiivistelmista (atomisesti: valiaikainen tiedosto ja os.replace).
    Palauttaa (tiivistelmia, paloja). Lukuvirhe yksittaisessa tiivistelmassa ohitetaan, muu virhe keskeyttaa."""
    try:
        alue.valmista(alikansiot=KAYTETYT_ALIKANSIOT)
        indeksikansio = tarkista_hakemisto(alue.polku("indeksi"))
        tkansio = alue.polku("tiivistelmat")
    except AlueVirhe as e:
        raise HakuVirhe("alue ei ole kayttokelpoinen (%s)" % type(e).__name__)
    fd, tmp = tempfile.mkstemp(dir=indeksikansio, prefix=".tmp.")
    os.close(fd)
    os.chmod(tmp, 0o600)
    tiiv = paloja = 0
    try:
        db = sqlite3.connect(tmp)
        try:
            db.execute("create virtual table pala using fts5(teksti, tunnus unindexed, nro unindexed, "
                       "tokenize='unicode61 remove_diacritics 0')")
            for tunnus in tiivistelma.luettele(tkansio):
                try:
                    t = tiivistelma.lue(tkansio, tunnus)
                except tiivistelma.TiivistelmaVirhe:
                    continue
                tiiv += 1
                for n, p in enumerate(pilko(tiivistelman_teksti(t))):
                    vartalot = " ".join(sanat(p))
                    if vartalot:
                        db.execute("insert into pala(teksti, tunnus, nro) values (?,?,?)", (vartalot, tunnus, n))
                        paloja += 1
            db.commit()
        finally:
            db.close()
        os.replace(tmp, indeksin_polku(alue))
    except (sqlite3.Error, OSError, tiivistelma.TiivistelmaVirhe) as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise HakuVirhe("indeksin rakennus epaonnistui (%s)" % type(e).__name__)
    return tiiv, paloja


def hae(alue, kysely, k=5):
    """Palauttaa enintaan k tulosta [{'tunnus', 'pisteet', 'pala'}, ...] parhaasta alkaen. Yksi tulos per tiivistelma
    (paras pala). Puuttuva tai tyhja indeksi tai tyhja kysely => []. Hakee vain talta alueelta."""
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise HakuVirhe("k pitaa olla kokonaisluku valilla 1-%d" % MAX_K)
    fts = _fts_kysely(kysely)
    polku = indeksin_polku(alue)
    if not fts or not os.path.isfile(polku) or os.path.islink(polku):
        return []
    try:
        db = sqlite3.connect("file:%s?mode=ro" % str(polku).replace("?", "%3f").replace("#", "%23"), uri=True)
        try:
            rivit = db.execute("select tunnus, nro, bm25(pala) as p from pala where pala match ? order by p limit ?",
                               (fts, k * 20)).fetchall()
        finally:
            db.close()
    except sqlite3.Error as e:
        raise HakuVirhe("haku epaonnistui (%s)" % type(e).__name__)
    tulos, nahty = [], set()
    for tunnus, nro, p in rivit:
        if tunnus in nahty:
            continue
        nahty.add(tunnus)
        tulos.append({"tunnus": tunnus, "pisteet": -p, "pala": nro})
        if len(tulos) >= k:
            break
    return tulos


# --- CLI ---------------------------------------------------------------------------------------------

def _alue_nimesta(nimi, juuri):
    """Alueen valinta nimella. Tuonnit tehdaan vasta tassa, jotta moduulin muu osa pysyy tilaneutraalina."""
    if nimi == "tyokalu":
        from aivo import tyokalualue as m
        return m.tyokalualue(juuri) if juuri else m.tyokalualue()
    from aivo import yksityinen_alue as m
    return m.yksityinen_alue(juuri) if juuri else m.yksityinen_alue()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m aivo.haku", description=__doc__.split("\n")[0])
    s = ap.add_subparsers(dest="komento", required=True)
    for nimi in ("rakenna", "hae"):
        p = s.add_parser(nimi)
        p.add_argument("--alue", required=True, choices=("tyokalu", "yksityinen"))
        p.add_argument("--juuri", help="alueen juurikansio (oletus: alueen oma)")
        if nimi == "hae":
            p.add_argument("--k", type=int, default=5)
            p.add_argument("kysely", nargs="+")
    a = ap.parse_args(argv)
    try:
        alue = _alue_nimesta(a.alue, a.juuri)
        if a.komento == "rakenna":
            tiiv, paloja = rakenna(alue)
            print("indeksi: %d tiivistelmaa, %d palaa" % (tiiv, paloja))
        else:
            for t in hae(alue, " ".join(a.kysely), a.k):
                print("%s  pala %d  pisteet %.3f" % (t["tunnus"], t["pala"], t["pisteet"]))
    except (HakuVirhe, AlueVirhe) as e:
        print("virhe: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
