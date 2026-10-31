#!/usr/bin/env python3
"""Vika 6: ajastin ei kaynnisty (timer ei ole enabled eika active)."""
import sre_lib as L

TUNNUS = "ajastin-ei-kaynnisty"
KOKEILU = "sre-v06-ajastin"
OIRE = ("Siivousajo (sre-siivous) ei ole koskaan ajettu ajastetusti. Sen pitaisi kaynnistya viiden minuutin valein, "
        "myos kontin uudelleenkaynnistyksen jalkeen.")
PALVELU = "sre-siivous.service"
AJASTIN = "sre-siivous.timer"
MERKKI = "/var/lib/sre/siivous.ok"
SKRIPTI = "/opt/sre/siivous.sh"

SKRIPTI_TEKSTI = """#!/bin/bash
# Siivous: kirjaa ajon aikaleiman.
set -eu
printf '%(%s)T\\n' -1 > /var/lib/sre/siivous.ok
"""


def injektoi(y):
    y.kirjoita(SKRIPTI, SKRIPTI_TEKSTI, 0o755)
    y.kirjoita("/etc/systemd/system/" + PALVELU, L.yksikko_teksti("SRE-siivous", ["Type=oneshot", "ExecStart=" + SKRIPTI]))
    y.kirjoita("/etc/systemd/system/" + AJASTIN, "[Unit]\nDescription=SRE-siivouksen ajastin\n\n[Timer]\n"
               "OnCalendar=*:0/5\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    y.kirjoita("/var/lib/sre/.luotu", "")
    L.vaadi_ok(y, "systemctl", "daemon-reload")
    y.aja("systemctl", "disable", "--now", AJASTIN)  # varmistetaan: ei enabled, ei active (paluukoodi ei ole tarkea)


def tarkista(y):
    puute = L.sisaltaa(y, "/etc/systemd/system/" + AJASTIN, r"^OnCalendar=\S+", r"^WantedBy=timers\.target$")
    if puute:
        return False, puute
    rc, ulos = y.aja("systemctl", "is-enabled", AJASTIN)
    if rc != 0 or ulos.strip() != "enabled":
        return False, "ajastin ei ole enabled (%s)" % (ulos.strip()[:30] or "?")
    rc, ulos = y.aja("systemctl", "is-active", AJASTIN)
    if rc != 0 or ulos.strip() != "active":
        return False, "ajastin ei ole active (%s)" % (ulos.strip()[:30] or "?")
    return L.kaynnista_ja_varmista(y, PALVELU, MERKKI)


if __name__ == "__main__":
    raise SystemExit(L.kaynnista(__import__(__name__)))
