#!/usr/bin/env python3
"""Vika 3: tiedosto-oikeus vaarin (palvelukayttaja ei lue omaa asetustiedostoaan)."""
import sre_lib as L

TUNNUS = "tiedosto-oikeus"
KOKEILU = "sre-v03-oikeus"
OIRE = "Palvelu sre-lukija.service ei toimi: se epaonnistuu kaynnistettaessa."
YKSIKKO = "sre-lukija.service"
KAYTTAJA = "sre-palvelu"
ASETUS = "/etc/sre/lukija.conf"
MERKKI = "/var/lib/sre-lukija/lukija.ok"
SKRIPTI = "/opt/sre/lukija.sh"

SKRIPTI_TEKSTI = """#!/bin/bash
# Lukija: lukee asetustiedoston palvelukayttajana ja kirjaa sisallon tilahakemistoon.
set -eu
tila=$(< /etc/sre/lukija.conf)
printf '%s\\n' "$tila" > /var/lib/sre-lukija/lukija.ok
"""


def injektoi(y):
    y.kirjoita(SKRIPTI, SKRIPTI_TEKSTI, 0o755)
    y.kirjoita(ASETUS, "tila=ok\n", 0o600)
    y.kirjoita("/etc/systemd/system/" + YKSIKKO, L.yksikko_teksti("SRE-lukija", [
        "Type=oneshot", "User=" + KAYTTAJA, "StateDirectory=sre-lukija", "ExecStart=" + SKRIPTI]))
    rc, _ = y.aja("id", "-u", KAYTTAJA)
    if rc != 0:
        L.vaadi_ok(y, "useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", KAYTTAJA)
    # asetus jaa root-omisteiseksi ja 0600: palvelukayttaja ei lue sita
    L.vaadi_ok(y, "chown", "root:root", y.p(ASETUS))
    L.vaadi_ok(y, "systemctl", "daemon-reload")


def tarkista(y):
    puute = L.sisaltaa(y, "/etc/systemd/system/" + YKSIKKO, r"^User=" + KAYTTAJA + r"$") or L.sisaltaa(y, ASETUS, r"tila=ok")
    if puute:
        return False, puute
    mode = y.mode(ASETUS)
    if mode & 0o007:
        return False, "asetustiedoston oikeudet liian laajat (%o): ei saa olla muille luettavissa tai kirjoitettavissa" % mode
    return L.kaynnista_ja_varmista(y, YKSIKKO, MERKKI)


if __name__ == "__main__":
    raise SystemExit(L.kaynnista(__import__(__name__)))
