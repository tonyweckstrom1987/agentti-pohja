#!/usr/bin/env bash
# Testiadapteri: ei kutsu mallia, ei kuluta mitaan. Kirjoittaa kontin tyokansioon merkin ja palauttaa yhteenvedon.
# Adapterisopimus (kaikille adaptereille): ymparistossa HL_CT, HL_PCT, HL_KOKEILU, HL_KUVAUS, HL_TULOS, HL_AIKAKATTO_S,
# HL_KUSTANNUSKATTO_USD, HL_MALLI. Viimeinen stdout-rivi on JSON: {"lopputulos":"onnistui|epaonnistui","kustannus_usd":N|null,"malli":"..."}.
set -eu
"${HL_PCT:-pct}" exec "$HL_CT" -- bash -c 'echo kuiva-adapteri > /root/kokeilu/kuiva.txt'
echo "kuiva adapteri valmis" >"$HL_TULOS/agentti.log"
echo '{"lopputulos":"onnistui","kustannus_usd":0,"malli":"kuiva"}'
