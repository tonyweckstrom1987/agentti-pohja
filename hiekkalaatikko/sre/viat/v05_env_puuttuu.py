#!/usr/bin/env python3
"""Vika 5: palvelu kaatuu puuttuvaan ymparistomuuttujaan (asetustiedostosta puuttuu SRE_API_URL)."""
import sre_lib as L

TUNNUS = "env-puuttuu"
KOKEILU = "sre-v05-env"
OIRE = "Palvelu sre-api.service ei toimi: se lopettaa heti kaynnistyksen jalkeen virhekoodilla."
YKSIKKO = "sre-api.service"
YMPARISTO = "/etc/sre/ymparisto.conf"
MERKKI = "/var/lib/sre/api.ok"
SKRIPTI = "/opt/sre/api.sh"

SKRIPTI_TEKSTI = """#!/bin/bash
# API-asiakas. Vaatii ymparistomuuttujat SRE_API_URL (esim. http://127.0.0.1:8080) ja SRE_TILA.
set -eu
: "${SRE_TILA:?SRE_TILA puuttuu}"
if [ -z "${SRE_API_URL:-}" ]; then
  echo "SRE_API_URL puuttuu ymparistosta" >&2
  exit 78
fi
case "$SRE_API_URL" in
  http://*|https://*) ;;
  *) echo "SRE_API_URL ei ole URL" >&2; exit 78 ;;
esac
printf '%s\\n' "$SRE_API_URL" > /var/lib/sre/api.ok
"""


def injektoi(y):
    y.kirjoita(SKRIPTI, SKRIPTI_TEKSTI, 0o755)
    y.kirjoita(YMPARISTO, "SRE_TILA=testi\n")
    y.kirjoita("/etc/systemd/system/" + YKSIKKO, L.yksikko_teksti("SRE-api", [
        "Type=oneshot", "EnvironmentFile=" + YMPARISTO, "ExecStart=" + SKRIPTI]))
    y.kirjoita("/var/lib/sre/.luotu", "")
    L.vaadi_ok(y, "systemctl", "daemon-reload")


def tarkista(y):
    puute = L.sisaltaa(y, SKRIPTI, r"SRE_API_URL", r"SRE_TILA")
    if puute:
        return False, puute
    ok, syy = L.kaynnista_ja_varmista(y, YKSIKKO, MERKKI)
    if not ok:
        return ok, syy
    arvo = (y.lue(MERKKI) or "").strip()
    if not arvo.startswith(("http://", "https://")):
        return False, "api.ok ei sisalla URLia"
    return True, "ok"


if __name__ == "__main__":
    raise SystemExit(L.kaynnista(__import__(__name__)))
