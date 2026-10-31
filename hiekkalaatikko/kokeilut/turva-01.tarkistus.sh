#!/usr/bin/env bash
# Ajetaan kloonissa. Hyvaksyy vain, jos raportti on olemassa ja siina on vahintaan yksi KOVENNUS-rivi.
set -eu
test -s /root/kokeilu/RAPORTTI.md
grep -q '^KOVENNUS: ' /root/kokeilu/RAPORTTI.md
