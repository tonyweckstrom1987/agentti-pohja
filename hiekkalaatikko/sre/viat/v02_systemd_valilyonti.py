#!/usr/bin/env python3
"""Vika 2: systemd-arvo halkeaa valilyonnista (GIT_SSH_COMMAND ilman lainausmerkkeja)."""
import sre_lib as L

TUNNUS = "systemd-valilyonti"
KOKEILU = "sre-v02-valilyonti"
OIRE = "Palvelu sre-synkka.service ei toimi: se kuolee heti kaynnistyksen jalkeen ja valittaa ymparistoasetuksesta."
YKSIKKO = "sre-synkka.service"
MERKKI = "/var/lib/sre/synkka.ok"
SKRIPTI = "/opt/sre/synkka.sh"

SKRIPTI_TEKSTI = """#!/bin/bash
# Synkka: vaatii tasmalleen oikean GIT_SSH_COMMAND-arvon (tiukka isannan avaintarkistus).
set -eu
odotettu="ssh -i /opt/sre/avain -o StrictHostKeyChecking=yes"
if [ "${GIT_SSH_COMMAND:-}" != "$odotettu" ]; then
  echo "GIT_SSH_COMMAND vaara: '${GIT_SSH_COMMAND:-}'" >&2
  exit 1
fi
printf '%(%s)T\\n' -1 > /var/lib/sre/synkka.ok
"""


def injektoi(y):
    y.kirjoita(SKRIPTI, SKRIPTI_TEKSTI, 0o755)
    y.kirjoita("/etc/systemd/system/" + YKSIKKO, L.yksikko_teksti("SRE-synkka", [
        "Type=oneshot", "Environment=GIT_SSH_COMMAND=ssh -i /opt/sre/avain -o StrictHostKeyChecking=yes",
        "ExecStart=" + SKRIPTI]))
    y.kirjoita("/var/lib/sre/.luotu", "")
    L.vaadi_ok(y, "systemctl", "daemon-reload")


def tarkista(y):
    puute = L.sisaltaa(y, SKRIPTI, r'odotettu="ssh -i /opt/sre/avain -o StrictHostKeyChecking=yes"', r'!=\s*"\$odotettu"')
    if puute:
        return False, puute
    return L.kaynnista_ja_varmista(y, YKSIKKO, MERKKI)


if __name__ == "__main__":
    raise SystemExit(L.kaynnista(__import__(__name__)))
