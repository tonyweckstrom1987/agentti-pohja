#!/usr/bin/env bash
# =============================================================================
# palautustesti-gcrypt.sh - kuukausittainen palautustesti Drive-varmuuskopiosta (rclone crypt, kohde gcrypt:).
#
# Kaytto (Proxmox-isannalla, root; systemd-ajastin kerran kuussa, katso systemd/palautustesti-gcrypt.*.mall):
#     palautustesti-gcrypt.sh              hakee uusimman pienen kontin arkiston, tarkistaa sen, siivoaa
#     palautustesti-gcrypt.sh --luettelo   nayttaa vain, minka arkiston testi valitsisi (ei latausta, ei ilmoituksia)
#
# Mita tehdaan:
#   1) listaa kohteesta kontin (KONTTI, oletus 114 = Uptime Kuma) arkistot ja valitsee uusimman
#      (aikaleima nimessa) - vain varsinainen arkisto, ei .log/.notes
#   2) kopioi sen VAIN omaan mktemp-kansioonsa (TMPJUURI/palautustesti.XXXXXX)
#   3) vertaa kokoa kohteen listaukseen ja tarkistaa arkiston eheyden:
#        .tar.zst  -> zstd -t, sitten zstd -dc | tar -t
#        .tar.gz   -> gzip -t, sitten tar -tzf
#        .vma.zst  -> zstd -t (ja `vma verify`, jos vma-ohjelma on asennettu)
#        .vma.gz   -> gzip -t (ja `vma verify`, jos vma-ohjelma on asennettu)
#   4) poistaa vain tuon oman kansionsa (rm -rf vasta kun polku on tarkistettu)
#   5) ilmoittaa: Uptime Kuma push (up/down); Telegram VAIN virheesta
# Mitaan olemassa olevaa arkistoa ei poisteta eika muuteta: kohteeseen vain luetaan.
#
# Asetukset ymparistomuuttujista tai tiedostosta ASETUS_TIEDOSTO (oletus /etc/palautustesti-gcrypt.env,
# omistaja = ajava kayttaja, oikeudet 600). Salaisuudet (rclone.conf, Kuma-osoite, Telegram-tunnus) eivat
# kuulu repoon eika niita tulosteta.
#   REMOTE        gcrypt:                 rclone crypt -kohde (hakemisto mukaan, jos arkistot eivat ole juuressa)
#   KONTTI        114                     kontin tunnus, jonka arkisto testataan
#   MAX_KOKO      2147483648              enimmaiskoko tavuina; isompaa ei ladata
#   TMPJUURI      /var/tmp                hakemisto, jonka alle oma tyokansio luodaan
#   BWLIMIT       8M                      rclone --bwlimit
#   LOKI          /var/log/palautustesti-gcrypt.log
#   LUKKO         /run/palautustesti-gcrypt.lock
#   KUMA_PUSH_URL (valinnainen)           Uptime Kuma push-osoite ilman kyselyosaa
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (valinnainen) virheilmoitus Telegramiin
#   RCLONE        rclone                  rclone-ohjelma
# rclone.conf: RCLONE_CONFIG tai $HOME/.config/rclone/rclone.conf, oltava 600.
# =============================================================================
set -u -o pipefail

TILA=testi
case "${1:-}" in
  "") ;;
  --luettelo) TILA=luettelo ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac

ASETUS_TIEDOSTO="${ASETUS_TIEDOSTO:-/etc/palautustesti-gcrypt.env}"
if [ -f "$ASETUS_TIEDOSTO" ]; then
  if [ "$(stat -c %u "$ASETUS_TIEDOSTO")" != "$(id -u)" ]; then
    echo "EPAONNISTUI: asetustiedoston omistaja ei ole ajava kayttaja" >&2; exit 1
  fi
  if [ -n "$(find "$ASETUS_TIEDOSTO" -perm /077 2>/dev/null)" ]; then
    echo "EPAONNISTUI: asetustiedosto on ryhman tai muiden kaytettavissa (pitaa olla 600)" >&2; exit 1
  fi
  # shellcheck disable=SC1090
  . "$ASETUS_TIEDOSTO"
fi

REMOTE="${REMOTE:-gcrypt:}"
KONTTI="${KONTTI:-114}"
MAX_KOKO="${MAX_KOKO:-2147483648}"
TMPJUURI="${TMPJUURI:-/var/tmp}"
BWLIMIT="${BWLIMIT:-8M}"
LOKI="${LOKI:-/var/log/palautustesti-gcrypt.log}"
LUKKO="${LUKKO:-/run/palautustesti-gcrypt.lock}"
KUMA_PUSH_URL="${KUMA_PUSH_URL:-}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
RCLONE="${RCLONE:-rclone}"

loki() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOKI" 2>/dev/null || true; printf '%s\n' "$*"; }

TMP=""
ONNISTUI=0
VIESTI="palautustesti epaonnistui"

ilmoita() {  # $1 = up|down, $2 = viesti. Kuma aina; Telegram vain down. Ei salaisuuksia lokiin.
  [ "$TILA" = testi ] || return 0
  if [ -n "$KUMA_PUSH_URL" ]; then
    curl -fsS -m 20 -G --data-urlencode "status=$1" --data-urlencode "msg=$2" "$KUMA_PUSH_URL" >/dev/null 2>&1 \
      || loki "VAROITUS: Uptime Kuma -pingi epaonnistui"
  fi
  if [ "$1" = down ] && [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    # Tunnus annetaan curlille stdinin kautta, jotta se ei nay prosessilistassa.
    printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" \
      | curl -fsS -m 20 -K - --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
          --data-urlencode "text=Palautustesti (gcrypt, kontti $KONTTI): $2" >/dev/null 2>&1 \
      || loki "VAROITUS: Telegram-ilmoitus epaonnistui"
  fi
}

poista_oma_kansio() {
  # Poistetaan vain itse luotu kansio: ei tyhja, ei symlinkki, hakemisto ja nimi TMPJUURI/palautustesti.*
  [ -n "$TMP" ] || return 0
  case "$TMP" in
    "$TMPJUURI"/palautustesti.*) [ -d "$TMP" ] && [ ! -L "$TMP" ] && rm -rf -- "$TMP" ;;
    *) loki "VAROITUS: tyokansio ei ole odotetussa polussa, ei poisteta" ;;
  esac
}

siivous() {
  local koodi=$?
  poista_oma_kansio
  if [ "$ONNISTUI" != 1 ]; then
    loki "EPAONNISTUI (tila=$TILA, koodi=$koodi): $VIESTI"
    ilmoita down "$VIESTI"
    [ "$koodi" -eq 0 ] && koodi=1
  fi
  exit "$koodi"
}
trap siivous EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

vika() { VIESTI="$*"; exit 1; }

[[ "$KONTTI" =~ ^[0-9]+$ ]] || vika "KONTTI ei kelpaa"
[[ "$MAX_KOKO" =~ ^[0-9]+$ ]] || vika "MAX_KOKO ei kelpaa"
command -v "$RCLONE" >/dev/null 2>&1 || vika "rclonea ei loydy"
command -v flock >/dev/null 2>&1 || vika "flock puuttuu"
RCONF="${RCLONE_CONFIG:-${HOME:-/root}/.config/rclone/rclone.conf}"
if [ -f "$RCONF" ] && [ -n "$(find "$RCONF" -perm /077 2>/dev/null)" ]; then
  vika "rclone.conf on ryhman tai muiden kaytettavissa (pitaa olla 600)"
fi
[ -d "$TMPJUURI" ] || vika "TMPJUURI-hakemistoa ei ole"

exec 9>"$LUKKO" || vika "lukkotiedostoa ei voi avata"
if ! flock -n 9; then loki "toinen ajo kaynnissa, ohitetaan"; ONNISTUI=1; exit 0; fi

# Kohteen on oltava crypt-tyyppinen: muuten luettaisiin salaamatonta dataa tai vaaraa kohdetta.
KOHDE_NIMI="${REMOTE%%:*}"
"$RCLONE" listremotes --long 2>/dev/null | grep -qE "^${KOHDE_NIMI}:[[:space:]]+crypt\$" \
  || vika "kohde $KOHDE_NIMI ei ole crypt-tyyppinen"

# 1) Uusin arkisto. lsf: "koko;polku" (crypt-kohde kertoo purkautuneen koon). Nimen aikaleima jarjestaa.
LISTA=$("$RCLONE" lsf -R --files-only --format sp --separator ';' "$REMOTE" 2>/dev/null) || vika "listaus kohteesta epaonnistui"
RIVI=$(printf '%s\n' "$LISTA" \
  | awk -F';' -v id="$KONTTI" '
      { n=split($2, osat, "/"); nimi=osat[n]
        if (nimi ~ ("^vzdump-(lxc|qemu)-" id "-[0-9]{4}_[0-9]{2}_[0-9]{2}-[0-9]{2}_[0-9]{2}_[0-9]{2}\\.(tar|vma)(\\.(zst|gz|lzo))?$"))
          print nimi ";" $1 ";" $2 }' \
  | sort -t';' -k1,1 | tail -n 1)
[ -n "$RIVI" ] || vika "kohteesta ei loytynyt kontin $KONTTI arkistoa"
NIMI=${RIVI%%;*}; REST=${RIVI#*;}; KOKO=${REST%%;*}; POLKU=${REST#*;}
[[ "$KOKO" =~ ^[0-9]+$ ]] || vika "arkiston koko ei ole luku"

if [ "$TILA" = luettelo ]; then
  echo "VALITTAISIIN $NIMI ($KOKO tavua) polusta $POLKU"
  ONNISTUI=1; exit 0
fi

[ "$KOKO" -gt 0 ] || vika "arkiston koko on 0"
[ "$KOKO" -le "$MAX_KOKO" ] || vika "arkisto $NIMI on liian suuri ($KOKO > $MAX_KOKO tavua), ei ladata"
VAPAA=$(df --output=avail -B1 "$TMPJUURI" | tail -n 1 | tr -d ' ')
[[ "$VAPAA" =~ ^[0-9]+$ ]] && [ "$VAPAA" -gt $((KOKO * 2)) ] || vika "tilaa ei ole tarpeeksi ($TMPJUURI)"

# 2) Kopioi omaan tyokansioon.
TMP=$(mktemp -d "$TMPJUURI/palautustesti.XXXXXX") || vika "tyokansion luonti epaonnistui"
loki "palautustesti: $NIMI ($KOKO tavua)"
case "$REMOTE" in *: | */) LAHDE="$REMOTE$POLKU" ;; *) LAHDE="$REMOTE/$POLKU" ;; esac
"$RCLONE" copyto --bwlimit "$BWLIMIT" --retries 3 --low-level-retries 5 "$LAHDE" "$TMP/$NIMI" >/dev/null 2>&1 \
  || vika "arkiston haku epaonnistui"

# 3a) Kokovertailu.
LADATTU=$(stat -c %s "$TMP/$NIMI")
[ "$LADATTU" = "$KOKO" ] || vika "koko ei tasmaa (ladattu $LADATTU, kohteessa $KOKO)"

# 3b) Eheys arkiston tyypin mukaan.
tarvitse() { command -v "$1" >/dev/null 2>&1 || vika "tarkistustyokalu puuttuu: $1"; }
case "$NIMI" in
  *.tar.zst) tarvitse zstd; tarvitse tar
    zstd -t -q "$TMP/$NIMI" || vika "zstd -t epaonnistui"
    zstd -dc "$TMP/$NIMI" | tar -t >/dev/null || vika "tar-rakenne ei ole ehja" ;;
  *.tar.gz) tarvitse gzip; tarvitse tar
    gzip -t "$TMP/$NIMI" || vika "gzip -t epaonnistui"
    tar -tzf "$TMP/$NIMI" >/dev/null || vika "tar-rakenne ei ole ehja" ;;
  *.vma.zst) tarvitse zstd
    zstd -t -q "$TMP/$NIMI" || vika "zstd -t epaonnistui"
    if command -v vma >/dev/null 2>&1; then
      zstd -dc "$TMP/$NIMI" > "$TMP/arkisto.vma" && vma verify "$TMP/arkisto.vma" >/dev/null 2>&1 || vika "vma verify epaonnistui"
    fi ;;
  *.vma.gz) tarvitse gzip
    gzip -t "$TMP/$NIMI" || vika "gzip -t epaonnistui"
    if command -v vma >/dev/null 2>&1; then
      gzip -dc "$TMP/$NIMI" > "$TMP/arkisto.vma" && vma verify "$TMP/arkisto.vma" >/dev/null 2>&1 || vika "vma verify epaonnistui"
    fi ;;
  *) vika "arkiston tyyppia ei tueta: $NIMI" ;;
esac

loki "palautustesti OK: $NIMI ($KOKO tavua), koko ja eheys tarkistettu"
ONNISTUI=1
poista_oma_kansio; TMP=""
ilmoita up "OK $NIMI"
exit 0
