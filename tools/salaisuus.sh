#!/bin/sh
# salaisuus.sh: tallentaa yhden salaisuuden env-tiedostoon ilman etta arvo nakyy ruudulla, historiassa tai Clauden nakyvissa.
# Kaytto (palvelimella, itse ajettuna): salaisuus.sh <env-tiedosto> <MUUTTUJA> [omistaja:ryhma [oikeudet]]
# Esim.:  salaisuus.sh /etc/agentti/portti.env PORTTI_AVAIN root:portti 640
# Arvo kysytaan piilotettuna (read -s), kirjoitetaan atomisesti (tmp + mv), vanha rivi korvataan, oikeudet asetetaan.
# Tulosteeseen ei koskaan tule arvoa, vain pituus. Claude tai terminal-Claude EI aja tata puolestasi: arvon kirjoitat sina.
set -eu
LC_ALL=C
export LC_ALL
TIEDOSTO="${1:-}"
MUUTTUJA="${2:-}"
OMISTAJA="${3:-root:root}"
OIKEUDET="${4:-600}"
case "$MUUTTUJA" in
  ""|[0-9]*|*[!A-Za-z0-9_]*) echo "salaisuus.sh: virheellinen muuttujan nimi" >&2; exit 2 ;;
esac
case "$TIEDOSTO" in
  ""|*[[:space:]]*) echo "salaisuus.sh: virheellinen tiedostopolku" >&2; exit 2 ;;
esac
case "$OIKEUDET" in
  [0-7][0-7][0-7]) ;;
  *) echo "salaisuus.sh: oikeudet muodossa 600/640" >&2; exit 2 ;;
esac
if [ -n "${SALAISUUS_ARVO:-}" ]; then
  echo "salaisuus.sh: ymparistomuuttujaa SALAISUUS_ARVO ei kayteta; arvo kysytaan piilotettuna" >&2; exit 2
fi
printf 'Anna arvo muuttujalle %s (ei nay ruudulla): ' "$MUUTTUJA" >&2
stty -echo 2>/dev/null || true
IFS= read -r ARVO || ARVO=""
stty echo 2>/dev/null || true
printf '\n' >&2
if [ -z "$ARVO" ]; then echo "salaisuus.sh: tyhja arvo, ei muutettu" >&2; exit 1; fi
case "$ARVO" in *[!\ -~]*) echo "salaisuus.sh: arvossa on ei-tulostettavia merkkeja, ei muutettu" >&2; exit 1 ;; esac
HAKEMISTO=$(dirname "$TIEDOSTO")
mkdir -p "$HAKEMISTO"
TMP=$(mktemp "$HAKEMISTO/.salaisuus.XXXXXX")
trap 'rm -f "$TMP"' EXIT INT TERM
chmod 600 "$TMP"
if [ -f "$TIEDOSTO" ]; then
  grep -v "^${MUUTTUJA}=" "$TIEDOSTO" > "$TMP" || true
fi
printf '%s=%s\n' "$MUUTTUJA" "$ARVO" >> "$TMP"
chown "$OMISTAJA" "$TMP" 2>/dev/null || true
chmod "$OIKEUDET" "$TMP"
mv "$TMP" "$TIEDOSTO"
trap - EXIT INT TERM
echo "tallennettu: $MUUTTUJA -> $TIEDOSTO (${#ARVO} merkkia, oikeudet $OIKEUDET, omistaja $OMISTAJA)" >&2
