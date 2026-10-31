#!/usr/bin/env python3
"""Vika 4: levy taynna (tmpfs-raja). Datahakemisto on 1 Mt:n tmpfs, jonka vanha vedos tayttaa."""
import sre_lib as L

TUNNUS = "levy-taynna"
KOKEILU = "sre-v04-levy"
OIRE = "Palvelu sre-data.service ei toimi: se epaonnistuu kaynnistettaessa."
YKSIKKO = "sre-data.service"
HAKEMISTO = "/var/lib/sre-data"
MERKKI = "/var/lib/sre/data.ok"
SKRIPTI = "/opt/sre/data.sh"
TULOS_KOKO = 204800

SKRIPTI_TEKSTI = """#!/bin/bash
# Data: kirjoittaa 200 kt tulostiedoston datahakemistoon.
set -eu
head -c 204800 /dev/zero > /var/lib/sre-data/tulos.dat
printf '%(%s)T\\n' -1 > /var/lib/sre/data.ok
"""


def injektoi(y):
    y.kirjoita(SKRIPTI, SKRIPTI_TEKSTI, 0o755)
    y.kirjoita("/etc/systemd/system/" + YKSIKKO, L.yksikko_teksti("SRE-data", ["Type=oneshot", "ExecStart=" + SKRIPTI]))
    y.kirjoita("/var/lib/sre/.luotu", "")
    L.vaadi_ok(y, "mkdir", "-p", y.p(HAKEMISTO))
    L.vaadi_ok(y, "mount", "-t", "tmpfs", "-o", "size=1m", "tmpfs", y.p(HAKEMISTO))
    # taytto: dd paattyy odotetusti virheeseen (levy taynna), joten paluukoodia ei vaadita
    y.aja("dd", "if=/dev/zero", "of=" + y.p(HAKEMISTO) + "/vanha-vedos.bin", "bs=1k", "count=2048")
    L.vaadi_ok(y, "systemctl", "daemon-reload")


def tarkista(y):
    puute = L.sisaltaa(y, SKRIPTI, r"head -c 204800 /dev/zero > /var/lib/sre-data/tulos\.dat")
    if puute:
        return False, puute
    ok, syy = L.kaynnista_ja_varmista(y, YKSIKKO, MERKKI)
    if not ok:
        return ok, syy
    if not y.on(HAKEMISTO + "/tulos.dat"):
        return False, "tulostiedostoa ei ole"
    return True, "ok"


if __name__ == "__main__":
    raise SystemExit(L.kaynnista(__import__(__name__)))
