#!/usr/bin/env python3
"""Vika 1: komento puuttuu lapsen PATHista (todellinen vika 30.10.: runuser ei loytynyt)."""
import sre_lib as L

TUNNUS = "polku-puuttuu"
KOKEILU = "sre-v01-polku"
OIRE = "Palvelu sre-raportti.service ei toimi: se epaonnistuu joka kerta kun sen kaynnistaa (systemctl start sre-raportti)."
YKSIKKO = "sre-raportti.service"
MERKKI = "/var/lib/sre/raportti.ok"
SKRIPTI = "/opt/sre/raportti.sh"

SKRIPTI_TEKSTI = """#!/bin/bash
# Raportti: ajaa tyon rajoitetulla kayttajalla ja kirjaa onnistumisen.
set -eu
runuser -u nobody -- /bin/true
printf '%(%s)T\\n' -1 > /var/lib/sre/raportti.ok
"""


def injektoi(y):
    y.kirjoita(SKRIPTI, SKRIPTI_TEKSTI, 0o755)
    y.kirjoita("/etc/systemd/system/" + YKSIKKO, L.yksikko_teksti("SRE-raportti", [
        "Type=oneshot", "Environment=PATH=/opt/sre/bin", "ExecStart=" + SKRIPTI]))
    y.kirjoita("/var/lib/sre/.luotu", "")
    L.vaadi_ok(y, "systemctl", "daemon-reload")


def tarkista(y):
    puute = L.sisaltaa(y, SKRIPTI, r"runuser\s+-u\s+nobody")
    if puute:
        return False, puute
    return L.kaynnista_ja_varmista(y, YKSIKKO, MERKKI)


if __name__ == "__main__":
    raise SystemExit(L.kaynnista(__import__(__name__)))
