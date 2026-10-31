#!/usr/bin/env bash
# Generoitu: sre/luo_kokeilut.py. Ajetaan kloonissa; vikatiedostot ovat vain talla hetkella /root/.hl-salainen/:ssa.
set -eu
kokeilu="${1:-}"
case "$kokeilu" in
  sre-v[0-9][0-9]-*) ;;
  *) echo "kokeilun nimi puuttuu tai ei ole sre-vNN-*" >&2; exit 2 ;;
esac
n="${kokeilu#sre-v}"
n="${n%%-*}"
cd /root/.hl-salainen
set -- "v${n}_"*.py
[ -f "$1" ] || { echo "vikaskriptia ei loydy" >&2; exit 2; }
python3 "$1" tarkista
