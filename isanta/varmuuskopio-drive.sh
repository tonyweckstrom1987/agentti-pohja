#!/usr/bin/env bash
# =============================================================================
# varmuuskopio-drive.sh - vzdump-varmuuskopiot salattuna Google Driveen (rclone crypt).
#
# Kaytto (Proxmox-isannalla, root; cron klo 3.30 vzdumpin jalkeen):
#     varmuuskopio-drive.sh              ajaa kopioinnin (copy + sailytys Driven listauksen mukaan)
#     varmuuskopio-drive.sh --luettelo   listaa paikalliset tiedostot: kopioidaan / ohitetaan.
#                                        Ei kayta rclonea eika tunnuksia, toimii ennen asennusta
#     varmuuskopio-drive.sh --kuiva      rclone --dry-run: ei muuta mitaan, ei pingia
#     varmuuskopio-drive.sh --palautustesti
#                                        hakee pienimman uusimman dumpin Drivesta,
#                                        purkaa salauksen ja vertaa sha256-tiivistetta
#
# Asetukset ymparistomuuttujista tai tiedostosta ASETUS_TIEDOSTO (oletus
# /etc/varmuuskopio-drive.env, oltava ei-ryhma/muu-kirjoitettava). Salaisuudet (rclone.conf,
# Uptime Kuman push-osoite) eivat kuulu repoon. Skripti ei tulosta niita.
#   DUMP_DIR      /var/lib/vz/dump        paikallinen vzdump-hakemisto
#   REMOTE        drive-crypt:proxmox     rclone crypt -kohde
#   BWLIMIT       8M                      rclone --bwlimit
#   SAILYTA       3                       uusinta kopiota per kontti/VM (>=1)
#   MAX_POISTO    20                      rclone --max-delete (suoja massapoistoa vastaan)
#   LOKI          /var/log/varmuuskopio-drive.log
#   LUKKO         /run/varmuuskopio-drive.lock
#   KUMA_PUSH_URL (valinnainen)           Uptime Kuma push-osoite ilman kyselyosaa
#   RCLONE        rclone                  rclone-ohjelma
#   VAADITUT_KONTIT 111 112 113 114 115 119 (kaikki kontit)
#                                   valilyonnilla erotetut kontti-/VM-tunnukset, joiden dump on oltava
#                                         lahteessa (113 = Gitea). Puuttuva tunnus: kopiointi tehdaan,
#                                         mutta ajo paattyy virheeseen ja Kuma saa tilan down
# rclone.conf (sisaltaa crypt-avaimen ja Driven luvan) on oltava vain omistajan luettavissa (600).
# Skripti kieltaytyy ajosta, jos ryhma tai muut voivat lukea sen. Polku: RCLONE_CONFIG tai
# $HOME/.config/rclone/rclone.conf.
# =============================================================================
set -u -o pipefail

TILA=sync
case "${1:-}" in
  "") ;;
  --kuiva) TILA=kuiva ;;
  --palautustesti) TILA=palautus ;;
  --luettelo) TILA=luettelo ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac

ASETUS_TIEDOSTO="${ASETUS_TIEDOSTO:-/etc/varmuuskopio-drive.env}"
if [ -f "$ASETUS_TIEDOSTO" ]; then
  # shellcheck disable=SC1090
  if [ "$(stat -c %u "$ASETUS_TIEDOSTO")" != "$(id -u)" ]; then
    echo "EPAONNISTUI: $ASETUS_TIEDOSTO omistaja ei ole ajava kayttaja (rootina ajettaessa omistajan pitaa olla root)" >&2; exit 1
  fi
  if [ -n "$(find "$ASETUS_TIEDOSTO" -perm /077 2>/dev/null)" ]; then
    echo "EPAONNISTUI: $ASETUS_TIEDOSTO on ryhman tai muiden luettavissa tai kirjoitettavissa (pitaa olla 600)" >&2; exit 1
  fi
  . "$ASETUS_TIEDOSTO"
fi

DUMP_DIR="${DUMP_DIR:-/var/lib/vz/dump}"
REMOTE="${REMOTE:-drive-crypt:proxmox}"
BWLIMIT="${BWLIMIT:-8M}"
SAILYTA="${SAILYTA:-3}"
MAX_POISTO="${MAX_POISTO:-20}"
LOKI="${LOKI:-/var/log/varmuuskopio-drive.log}"
LUKKO="${LUKKO:-/run/varmuuskopio-drive.lock}"
KUMA_PUSH_URL="${KUMA_PUSH_URL:-}"
RCLONE="${RCLONE:-rclone}"
VAADITUT_KONTIT="${VAADITUT_KONTIT-111 112 113 114 115 119}"

loki() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOKI" 2>/dev/null || true; printf '%s\n' "$*"; }

TMP=""
ONNISTUI=0
ping_kuma() {  # $1 = up|down, $2 = viesti; vain tila, ei salaisuuksia lokiin
  [ -n "$KUMA_PUSH_URL" ] || return 0
  # sync pingaa aina; palautustesti vain epaonnistuessaan (Kuma-monitori seuraa paivittaista synkkaa)
  [ "$TILA" = sync ] || { [ "$TILA" = palautus ] && [ "$1" = down ]; } || return 0
  curl -fsS -m 20 -G --data-urlencode "status=$1" --data-urlencode "msg=$2" "$KUMA_PUSH_URL" >/dev/null 2>&1 \
    || loki "VAROITUS: Uptime Kuma -pingi epaonnistui"
}
siivous() {
  local koodi=$?
  [ -n "$TMP" ] && rm -rf "$TMP"
  if [ "$ONNISTUI" != 1 ]; then
    loki "EPAONNISTUI (tila=$TILA, koodi=$koodi)"
    ping_kuma down "epaonnistui"
    [ "$koodi" -eq 0 ] && koodi=1
  fi
  exit "$koodi"
}
trap siivous EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$SAILYTA" =~ ^[0-9]+$ ]] && [ "$SAILYTA" -ge 1 ] || { loki "SAILYTA ei kelpaa: $SAILYTA"; exit 1; }
[[ "$MAX_POISTO" =~ ^[0-9]+$ ]] || { loki "MAX_POISTO ei kelpaa"; exit 1; }
if [ "$TILA" != luettelo ]; then
  command -v "$RCLONE" >/dev/null 2>&1 || { loki "rclonea ei loydy"; exit 1; }
  RCONF="${RCLONE_CONFIG:-${HOME:-/root}/.config/rclone/rclone.conf}"
  if [ -f "$RCONF" ] && [ -n "$(find "$RCONF" -perm /077 2>/dev/null)" ]; then
    loki "rclone.conf on ryhman tai muiden luettavissa (pitaa olla 600): $RCONF"; exit 1
  fi
fi
command -v flock >/dev/null 2>&1 || { loki "flock puuttuu"; exit 1; }
[ -d "$DUMP_DIR" ] || { loki "dump-hakemistoa ei ole: $DUMP_DIR"; exit 1; }

exec 9>"$LUKKO" || { loki "lukkotiedostoa ei voi avata"; exit 1; }
if ! flock -n 9; then loki "toinen ajo kaynnissa, ohitetaan"; ONNISTUI=1; exit 0; fi

TMP=$(mktemp -d) || exit 1

# Dump-tiedostot: vzdump-<lxc|qemu>-<id>-<aika>.<paate>. Ryhmitys tunnuksen mukaan.
mapfile -t TIEDOSTOT < <(find "$DUMP_DIR" -maxdepth 1 -type f -name 'vzdump-*' -printf '%f\n' | sort)
PERUSNIMET=$(printf '%s\n' "${TIEDOSTOT[@]:-}" \
  | sed -nE 's/^(vzdump-(lxc|qemu)-([0-9]+)-[0-9]{4}_[0-9]{2}_[0-9]{2}-[0-9]{2}_[0-9]{2}_[0-9]{2})\..*$/\3 \1/p' \
  | sort -u)
# Tyhja lahde: ei kopioitavaa (todennakoisesti vika). Kieltaydytaan.
[ -n "$PERUSNIMET" ] || { loki "dump-hakemistossa ei ole vzdump-tiedostoja, ei synkronoida tyhjaa"; exit 1; }

# Vaaditut tunnukset (esim. Gitea = 113) on oltava mukana, muuten kopio on hiljaa vajaa.
PUUTTUVAT=""
for id in $VAADITUT_KONTIT; do
  [[ "$id" =~ ^[0-9]+$ ]] || { loki "VAADITUT_KONTIT ei kelpaa: $id"; exit 1; }
  # Vain varsinainen arkisto kelpaa (pelkka .log epaonnistuneesta ajosta ei riita).
  grep -qE "^vzdump-(lxc|qemu)-$id-[0-9_-]+\.(tar|vma)(\.(zst|gz|lzo))?\$" <<< "$(printf '%s\n' "${TIEDOSTOT[@]:-}")" \
    || PUUTTUVAT="$PUUTTUVAT $id"
done

# Vanhat: per tunnus kaikki paitsi SAILYTA uusinta (aikaleima jarjestaa nimet).
echo "$PERUSNIMET" | sort -k1,1 -k2,2r \
  | awk -v n="$SAILYTA" '{ if ($1 != e) { e=$1; k=0 } k++; if (k>n) print "/" $2 ".*" }' > "$TMP/vanhat.txt"
VANHOJA=$(wc -l < "$TMP/vanhat.txt")

if [ "$TILA" = luettelo ]; then
  # Ei rclonea, ei verkkoa, ei tunnuksia: nayttaa vain mita kopioitaisiin ja mita ei.
  while read -r _ pn; do echo "$pn"; done < <(echo "$PERUSNIMET" | sort -k1,1 -k2,2r \
    | awk -v n="$SAILYTA" '{ if ($1 != e) { e=$1; k=0 } k++; if (k>n) print $1, $2 }') | sort > "$TMP/vanhat-nimet.txt"
  for f in "${TIEDOSTOT[@]}"; do
    pn="${f%%.*}"
    case "$f" in *.tmp|*.partial) echo "OHITETAAN (keskeneraeinen) $f"; continue ;; esac
    if grep -qxF "$pn" "$TMP/vanhat-nimet.txt"; then echo "OHITETAAN (yli $SAILYTA uusinta) $f"
    else echo "KOPIOIDAAN $f ($(stat -c %s "$DUMP_DIR/$f") tavua)"; fi
  done
  if [ -n "$PUUTTUVAT" ]; then echo "PUUTTUU vaadittu dump, tunnus:$PUUTTUVAT"; exit 1; fi
  ONNISTUI=1; exit 0
fi

BASE=("$RCLONE" --bwlimit "$BWLIMIT" --log-file "$LOKI" --log-level NOTICE --stats 0 --retries 3 --low-level-retries 5)

# Salaus ennen siirtoa: kohteen on oltava rclone crypt -tyyppinen. Pelkka Drive-kohde veisi
# dumpit selkokielisina, joten silloin ei siirreta mitaan. (listremotes ei tulosta salaisuuksia.)
KOHDE_NIMI="${REMOTE%%:*}"
if ! grep -qE "^${KOHDE_NIMI}:[[:space:]]+crypt\$" <<< "$("$RCLONE" listremotes --long 2>/dev/null)"; then
  loki "kohde $KOHDE_NIMI ei ole crypt-tyyppinen, ei siirreta eika haeta salaamatonta dataa"; exit 1
fi

if [ "$TILA" = palautus ]; then
  # Uusimmista dumpeista pienin (nopea testi). Haetaan, puretaan crypt ja verrataan sha256.
  UUSIMMAT=$(echo "$PERUSNIMET" | sort -k1,1 -k2,2r | awk '$1!=e{e=$1; print $2}')
  VALINTA=""; KOKO=""
  while read -r pn; do
    for f in "$DUMP_DIR/$pn".*; do
      [ -f "$f" ] || continue
      case "$f" in *.log|*.notes|*.tmp|*.partial) continue ;; esac
      k=$(stat -c %s "$f"); if [ -z "$KOKO" ] || [ "$k" -lt "$KOKO" ]; then KOKO=$k; VALINTA=$f; fi
    done
  done <<< "$UUSIMMAT"
  [ -n "$VALINTA" ] || { loki "palautustestiin ei loytynyt dump-tiedostoa"; exit 1; }
  NIMI=$(basename "$VALINTA")
  loki "palautustesti: $NIMI ($KOKO tavua)"
  mkdir "$TMP/palautus"
  "${BASE[@]}" copyto "$REMOTE/$NIMI" "$TMP/palautus/$NIMI" || { loki "palautustesti: haku Drivesta epaonnistui"; exit 1; }
  A=$(sha256sum < "$VALINTA" | cut -d' ' -f1); B=$(sha256sum < "$TMP/palautus/$NIMI" | cut -d' ' -f1)
  if [ "$A" = "$B" ]; then loki "palautustesti OK: sha256 tasmaa ($A)"; ONNISTUI=1; exit 0; fi
  loki "palautustesti EPAONNISTUI: sha256 ei tasmaa"; exit 1
fi

KUIVA=()
[ "$TILA" = kuiva ] && KUIVA=(--dry-run)
loki "alku tila=$TILA lahde=$DUMP_DIR kohde=$REMOTE sailyta=$SAILYTA vanhoja_ryhmia=$VANHOJA"

# 1) Kopioi (ei sync): Drivesta ei koskaan poisteta sita, mika on kadonnut paikallisesti.
#    Vanhat (yli SAILYTA) ja keskeneraiset jaavat pois siirrosta.
"${BASE[@]}" "${KUIVA[@]}" copy "$DUMP_DIR" "$REMOTE" \
  --exclude-from "$TMP/vanhat.txt" --exclude '*.tmp' --exclude '*.partial' \
  --transfers 2 --checkers 4 || { loki "kopiointi epaonnistui"; exit 1; }

# 2) Sailytys Driven oman listauksen mukaan: per tunnus SAILYTA uusinta jaa, vain sita vanhemmat
#    poistetaan. Paikallinen katoaminen ei siis poista Driven kopioita.
"${BASE[@]}" lsf "$REMOTE" --files-only > "$TMP/eta.txt" || { loki "Driven listaus epaonnistui"; exit 1; }
sed -nE 's/^(vzdump-(lxc|qemu)-([0-9]+)-[0-9]{4}_[0-9]{2}_[0-9]{2}-[0-9]{2}_[0-9]{2}_[0-9]{2})\..*$/\3 \1/p' "$TMP/eta.txt" \
  | sort -u | sort -k1,1 -k2,2r \
  | awk -v n="$SAILYTA" '{ if ($1 != e) { e=$1; k=0 } k++; if (k>n) print "/" $2 ".*" }' > "$TMP/eta-vanhat.txt"
ETA_VANHOJA=$(wc -l < "$TMP/eta-vanhat.txt")
if [ "$ETA_VANHOJA" -gt 0 ]; then
  "${BASE[@]}" "${KUIVA[@]}" delete "$REMOTE" --include-from "$TMP/eta-vanhat.txt" --max-delete "$MAX_POISTO" \
    || { loki "vanhojen poisto epaonnistui"; exit 1; }
fi

if [ -n "$PUUTTUVAT" ]; then
  loki "VAADITTU dump puuttuu lahteesta, tunnus:$PUUTTUVAT (muut siirrettiin)"; exit 1
fi

ONNISTUI=1
loki "valmis tila=$TILA"
ping_kuma up "OK"
exit 0
