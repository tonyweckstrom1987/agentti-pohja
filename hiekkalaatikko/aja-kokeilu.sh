#!/usr/bin/env bash
# aja-kokeilu.sh - ajaa YHDEN kokeilun hiekkalaatikossa. AJETAAN PROXMOX-ISANNALLA (root), Tony ajaa itse.
#
# Kaytto: aja-kokeilu.sh [--kuiva] [--sailyta] --kokeilu NIMI --adapteri NIMI [--malli MALLI]
#   --kuiva     tulostaa komennot eika aja mitaan (ei pct-kutsuja, ei kirjoituksia)
#   --sailyta   ei tuhoa kloonia lopuksi (vain vianetsintaan; Tony tuhoaa sen itse: pct destroy <ID> --purge 1)
#
# Vaiheet: varaus -> klooni (pohjasta 9000) -> katot -> palomuuri -> snapshot ennen -> kaynnistys -> kuvaus sisaan
#          -> adapteri aikakatolla -> tarkistus -> lokien keruu -> pysayta -> snapshot jalkeen -> tuhoa -> kirjaa.
# Ymparistomuuttujat (ei salaisuuksia tiedostoihin; adapterin tunnukset tulevat ymparistosta):
#   HL_TILA (oletus /var/lib/hiekkalaatikko)  HL_PCT (oletus pct, testeja varten)  HL_FW_HAKEMISTO (oletus /etc/pve/firewall)
#   HL_KOKEILU_KATTO_USD  HL_PAIVA_KATTO_USD  HL_KOKEILUA_PAIVA  HL_AIKAKATTO_S   (vain pienennys koodin ylarajoista, ks. hl.py)
set -euo pipefail
LC_ALL=C
export LC_ALL

HAKEMISTO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HL="$HAKEMISTO/hl.py"
PCT="${HL_PCT:-pct}"
TILA="${HL_TILA:-/var/lib/hiekkalaatikko}"
FW_HAKEMISTO="${HL_FW_HAKEMISTO:-/etc/pve/firewall}"
POHJA_ID=9000
PROXY_ID=9001
SILTA=vmbr2
VERKKO=10.98.0
PROXY_IP="$VERKKO.2"
PROXY_PORTTI=3128
ALKU_ID=9100
LOPPU_ID=9199
CORES=2
MUISTI_MB=2048
KOKEILU_USD="${HL_KOKEILU_KATTO_USD:-0.50}"
PAIVA_USD="${HL_PAIVA_KATTO_USD:-3.00}"
KOKEILUA_PAIVA="${HL_KOKEILUA_PAIVA:-10}"
AIKA_S="${HL_AIKAKATTO_S:-1800}"

KUIVA=0
SAILYTA=0
KOKEILU=""
ADAPTERI=""
MALLI=""

virhe() { echo "aja-kokeilu.sh: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --kuiva) KUIVA=1 ;;
    --sailyta) SAILYTA=1 ;;
    --kokeilu) [ $# -ge 2 ] || virhe "--kokeilu vaatii arvon"; KOKEILU="$2"; shift ;;
    --adapteri) [ $# -ge 2 ] || virhe "--adapteri vaatii arvon"; ADAPTERI="$2"; shift ;;
    --malli) [ $# -ge 2 ] || virhe "--malli vaatii arvon"; MALLI="$2"; shift ;;
    -h|--ohje) sed -n '2,12p' "$0"; exit 0 ;;
    *) virhe "tuntematon valinta: $1" ;;
  esac
  shift
done

[ -n "$KOKEILU" ] || virhe "--kokeilu puuttuu"
[ -n "$ADAPTERI" ] || virhe "--adapteri puuttuu"
python3 -I "$HL" nimi-sallittu "$KOKEILU" || virhe "kokeilun nimi ei kelpaa (a-z, 0-9, viiva)"
python3 -I "$HL" nimi-sallittu "$ADAPTERI" || virhe "adapterin nimi ei kelpaa (a-z, 0-9, viiva)"
if [ -n "$MALLI" ]; then
  case "$MALLI" in *[!A-Za-z0-9._/:-]*|-*) virhe "mallin nimi sisaltaa kiellettyja merkkeja" ;; esac
fi
KUVAUS="$HAKEMISTO/kokeilut/$KOKEILU.md"
[ -f "$KUVAUS" ] || virhe "kokeilun kuvausta ei loydy: kokeilut/$KOKEILU.md"
ADAPTERI_TIEDOSTO=""
for paate in sh py; do
  if [ -f "$HAKEMISTO/adapterit/$ADAPTERI.$paate" ]; then ADAPTERI_TIEDOSTO="$HAKEMISTO/adapterit/$ADAPTERI.$paate"; fi
done
[ -n "$ADAPTERI_TIEDOSTO" ] || virhe "adapteria ei loydy: adapterit/$ADAPTERI.sh|py"
python3 -I "$HL" rajat --kokeilu-usd "$KOKEILU_USD" --paiva-usd "$PAIVA_USD" \
  --kokeilua-paiva "$KOKEILUA_PAIVA" --aika-s "$AIKA_S" || virhe "katto ylittaa koodin ylarajan"

# --- CT-ID:n validointi: KAIKKI kokeilukloonia koskevat pct-kutsut kulkevat taman kautta ---
validoi_id() {
  python3 -I "$HL" ct-sallittu "$1" || { echo "aja-kokeilu.sh: CT $1 ei kuulu varatulle valille $ALKU_ID-$LOPPU_ID; keskeytetaan" >&2; exit 4; }
}

ajaa() {  # suoritus tai kuiva-tulostus
  if [ "$KUIVA" = 1 ]; then
    printf '+'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

ct() {  # ct <pct-alikomento> <ID> [argumentit...]  (vain varattu vali)
  local ali="$1" id="$2"; shift 2
  validoi_id "$id"
  ajaa "$PCT" "$ali" "$id" "$@"
}

if [ "$KUIVA" = 1 ]; then
  CT_ID=$ALKU_ID
  echo "# kuiva-ajo: vapaa ID valitaan vasta oikeassa ajossa (nyt oletus $CT_ID)"
else
  mkdir -p "$TILA/lokit"
  chmod 700 "$TILA" "$TILA/lokit"
  exec 9>"$TILA/ajo.lukko"
  flock -n 9 || virhe "toinen kokeilu on kaynnissa (lukko $TILA/ajo.lukko)"
  KAYTOSSA="$("$PCT" list | awk 'NR>1 {print $1}')" || virhe "pct list epaonnistui"
  CT_ID=""
  i=$ALKU_ID
  while [ "$i" -le "$LOPPU_ID" ]; do
    if ! printf '%s\n' "$KAYTOSSA" | grep -qx "$i"; then CT_ID=$i; break; fi
    i=$((i + 1))
  done
  [ -n "$CT_ID" ] || virhe "ei vapaata CT-ID:ta valilla $ALKU_ID-$LOPPU_ID (siivoa vanhat kloonit kasin)"
fi
validoi_id "$CT_ID"
IP="$VERKKO.$((10 + CT_ID - ALKU_ID))"
ALKU_AIKA=$(date -u +%Y%m%dT%H%M%SZ)
KID="$ALKU_AIKA-$KOKEILU-$ADAPTERI"
LOKIT="$TILA/lokit/$KID"
NIMI="hl-$CT_ID"

echo "# kokeilu $KID, CT $CT_ID, ip $IP, aikakatto ${AIKA_S}s, kustannuskatto ${KOKEILU_USD} USD (paiva ${PAIVA_USD})"

VARAUS=""
KESKEYTETTY=0
ADAPTERI_AJETTU=0
KLOONATTU=0
ALKU_EPOCH=$(date +%s)
ADAPTERI_RC=1
TARKISTUS_RC=""
LOPPU_EPOCH=""

siivoa() {
  local rc=$?
  trap - EXIT INT TERM
  set +e
  if [ "$KUIVA" = 1 ]; then exit "$rc"; fi
  LOPPU_EPOCH=$(date +%s)
  if [ "$KLOONATTU" = 1 ]; then
    if "$PCT" status "$CT_ID" 2>/dev/null | grep -q running; then ct stop "$CT_ID" --timeout 20 >/dev/null 2>&1; fi
    # tuhoaminen vain jos kontti on varmasti taman ajon klooni: oikea nimi ja hiekkalaatikkosilta, ei isannan polkuja
    local conf
    conf="$("$PCT" config "$CT_ID" 2>/dev/null)"
    if [ "$SAILYTA" = 1 ]; then
      echo "# --sailyta: CT $CT_ID jatetaan (tuhoa kasin: $PCT destroy $CT_ID --purge 1)"
    elif printf '%s\n' "$conf" | grep -qx "hostname: $NIMI" && printf '%s\n' "$conf" | grep -q "bridge=$SILTA" \
         && ! printf '%s\n' "$conf" | grep -qE '^(mp[0-9]+|dev[0-9]+|lxc\.)'; then
      if ct destroy "$CT_ID" --purge 1 >/dev/null; then echo "# klooni $CT_ID tuhottu"; else rc=5; fi
      rm -f "${FW_HAKEMISTO:?}/${CT_ID:?}.fw"
    else
      echo "aja-kokeilu.sh: CT $CT_ID ei tasmaa odotettuun kloonin kuvaukseen; EI tuhota. Tarkista kasin." >&2
      rc=5
    fi
  fi
  if [ -n "$VARAUS" ]; then
    mkdir -p "$LOKIT"
    [ -f "$LOKIT/rajarikkeet.json" ] || echo '[]' >"$LOKIT/rajarikkeet.json"
    [ -f "$LOKIT/yhteenveto.txt" ] || : >"$LOKIT/yhteenveto.txt"
    local args
    args=(--tila "$TILA" --varaus "$VARAUS" --id "$KID" --aika "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --kokeilu "$KOKEILU"
          --adapteri "$ADAPTERI" --malli-pyydetty "$MALLI" --kesto "$((LOPPU_EPOCH - ALKU_EPOCH))"
          --adapteri-rc "$ADAPTERI_RC" --yhteenveto "$LOKIT/yhteenveto.txt" --rajarikkeet "$LOKIT/rajarikkeet.json" --lokit "$LOKIT")
    if [ -n "$TARKISTUS_RC" ]; then args+=(--tarkistus-rc "$TARKISTUS_RC"); fi
    if [ "$KESKEYTETTY" = 1 ] || [ "$ADAPTERI_AJETTU" = 0 ]; then args+=(--keskeytetty); fi
    python3 -I "$HL" paata "${args[@]}" || { echo "aja-kokeilu.sh: lokirivin kirjaus epaonnistui" >&2; rc=6; }
  fi
  exit "$rc"
}
trap siivoa EXIT
trap 'KESKEYTETTY=1; exit 130' INT TERM

if [ "$KUIVA" = 0 ]; then
  VARAUS="$(python3 -I "$HL" varaa --tila "$TILA" --kokeilu-usd "$KOKEILU_USD" --paiva-usd "$PAIVA_USD" \
    --kokeilua-paiva "$KOKEILUA_PAIVA" --aika-s "$AIKA_S")" || virhe "kustannusvarausta ei voitu tehda (katto tai kirjanpito); ajoa ei aloiteta"
  mkdir -p "$LOKIT"
  chmod 700 "$LOKIT"
else
  echo "+ python3 -I hl.py varaa --kokeilu-usd $KOKEILU_USD --paiva-usd $PAIVA_USD --aika-s $AIKA_S   # varaus ennen ajoa"
fi

# 1. klooni ja katot
ajaa "$PCT" clone "$POHJA_ID" "$CT_ID" --hostname "$NIMI" --description "hiekkalaatikko-kokeilu $KID"
KLOONATTU=1
ct set "$CT_ID" --cores "$CORES" --memory "$MUISTI_MB" --swap 0 --onboot 0 \
  --net0 "name=eth0,bridge=$SILTA,ip=$IP/24,firewall=1"

# 2. Proxmox-palomuuri kloonille: ulos vain proxyyn
FW="[OPTIONS]
enable: 1
policy_in: DROP
policy_out: DROP

[RULES]
OUT ACCEPT -dest $PROXY_IP -p tcp -dport $PROXY_PORTTI"
if [ "$KUIVA" = 1 ]; then
  echo "+ kirjoita $FW_HAKEMISTO/$CT_ID.fw:"; printf '%s\n' "$FW" | sed 's/^/#   /'
else
  printf '%s\n' "$FW" >"$FW_HAKEMISTO/$CT_ID.fw"
fi

# 3. snapshot ennen, kaynnistys
ct snapshot "$CT_ID" ennen
ct start "$CT_ID"
if [ "$KUIVA" = 0 ]; then
  for _ in $(seq 1 30); do
    if "$PCT" status "$CT_ID" | grep -q running; then break; fi
    sleep 1
  done
  "$PCT" status "$CT_ID" | grep -q running || { echo "aja-kokeilu.sh: kontti ei kaynnistynyt" >&2; exit 1; }
fi

# 4. kokeilun kuvaus sisaan (lisatiedostot kokeilut/NIMI.d/ mukaan)
ct exec "$CT_ID" -- mkdir -p /root/kokeilu
ct push "$CT_ID" "$KUVAUS" /root/kokeilu/KUVAUS.md
if [ -d "$HAKEMISTO/kokeilut/$KOKEILU.d" ]; then
  for f in "$HAKEMISTO/kokeilut/$KOKEILU.d"/*; do
    if [ -f "$f" ]; then ct push "$CT_ID" "$f" "/root/kokeilu/$(basename "$f")"; fi
  done
fi

# 4b. valinnainen salainen alustus (esim. SRE-vika): kokeilut/NIMI.alustus.sh ajetaan kloonissa, ja kokeilut/NIMI.salainen.d/:n
# tiedostot ovat kontissa vain taman ajan (/root/.hl-salainen). Ne poistetaan ENNEN adapteria, joten agentti ei nae vikaskriptia.
# Oma tiedosto (NIMI.*) voittaa; muuten ryhman yhteinen (RYHMA.*, esim. sre.alustus.sh kaikille sre-*-kokeiluille). Nimi annetaan argumenttina.
RYHMA="${KOKEILU%%-*}"
ALUSTUS_SH=""; TARKISTUS_SH=""; SALAINEN=""
for k in "$KOKEILU" "$RYHMA"; do
  if [ -z "$ALUSTUS_SH" ] && [ -f "$HAKEMISTO/kokeilut/$k.alustus.sh" ]; then ALUSTUS_SH="$HAKEMISTO/kokeilut/$k.alustus.sh"; fi
  if [ -z "$TARKISTUS_SH" ] && [ -f "$HAKEMISTO/kokeilut/$k.tarkistus.sh" ]; then TARKISTUS_SH="$HAKEMISTO/kokeilut/$k.tarkistus.sh"; fi
  if [ -z "$SALAINEN" ] && [ -d "$HAKEMISTO/kokeilut/$k.salainen.d" ]; then SALAINEN="$HAKEMISTO/kokeilut/$k.salainen.d"; fi
done
salainen_sisaan() {
  ct exec "$CT_ID" -- mkdir -p /root/.hl-salainen
  if [ -n "$SALAINEN" ]; then
    for f in "$SALAINEN"/*; do
      if [ -f "$f" ]; then ct push "$CT_ID" "$f" "/root/.hl-salainen/$(basename "$f")"; fi
    done
  fi
}
salainen_pois() { ct exec "$CT_ID" -- rm -rf /root/.hl-salainen; }
if [ -n "$ALUSTUS_SH" ]; then
  salainen_sisaan
  ct push "$CT_ID" "$ALUSTUS_SH" /root/.hl-salainen/alustus.sh
  ALUSTUS_RC=0
  if [ "$KUIVA" = 1 ]; then
    echo "+ $PCT exec $CT_ID -- timeout 120 bash /root/.hl-salainen/alustus.sh $KOKEILU"
  else
    "$PCT" exec "$CT_ID" -- timeout 120 bash /root/.hl-salainen/alustus.sh "$KOKEILU" >"$LOKIT/alustus.stdout" 2>&1 || ALUSTUS_RC=$?
  fi
  salainen_pois
  # alustus epaonnistui -> adapteria ei ajeta (ADAPTERI_AJETTU=0 -> kirjataan keskeytetty, ei mallin syyksi)
  [ "$ALUSTUS_RC" = 0 ] || { echo "aja-kokeilu.sh: alustus epaonnistui (rc=$ALUSTUS_RC), ks. $LOKIT/alustus.stdout" >&2; exit 1; }
fi

# 5. adapteri aikakatolla (isannan puolella; kontissa ei ole LLM-tunnuksia eika LLM-reittia)
ADAPTERI_KOMENTO=("$ADAPTERI_TIEDOSTO")
case "$ADAPTERI_TIEDOSTO" in *.py) ADAPTERI_KOMENTO=(python3 -I "$ADAPTERI_TIEDOSTO") ;; esac
if [ "$KUIVA" = 1 ]; then
  echo "+ HL_CT=$CT_ID HL_KOKEILU=$KOKEILU HL_KUVAUS=$KUVAUS HL_TULOS=$LOKIT HL_AIKAKATTO_S=$AIKA_S HL_KUSTANNUSKATTO_USD=$KOKEILU_USD HL_MALLI=${MALLI:-<oletus>} timeout -k 20 $AIKA_S ${ADAPTERI_KOMENTO[*]}"
else
  ADAPTERI_AJETTU=1
  set +e
  HL_CT="$CT_ID" HL_PCT="$PCT" HL_KOKEILU="$KOKEILU" HL_KUVAUS="$KUVAUS" HL_TULOS="$LOKIT" \
    HL_AIKAKATTO_S="$AIKA_S" HL_KUSTANNUSKATTO_USD="$KOKEILU_USD" HL_MALLI="$MALLI" \
    timeout -k 20 "$AIKA_S" "${ADAPTERI_KOMENTO[@]}" >"$LOKIT/adapteri.stdout" 2>"$LOKIT/adapteri.stderr"
  ADAPTERI_RC=$?
  set -e
  if [ "$ADAPTERI_RC" = 124 ] || [ "$ADAPTERI_RC" = 137 ]; then KESKEYTETTY=1; fi
  tail -n 1 "$LOKIT/adapteri.stdout" >"$LOKIT/yhteenveto.txt" || true
fi

# 6. objektiivinen tarkistus (jos kokeilulla on sellainen)
if [ -n "$TARKISTUS_SH" ] && [ "$KESKEYTETTY" = 0 ]; then
  # tarkistustiedostot ja kokeilun omat lisatiedostot kirjoitetaan uudelleen isannalta: agentin muokkaus ei vaikuta tarkistukseen
  if [ -d "$HAKEMISTO/kokeilut/$KOKEILU.d" ]; then
    for f in "$HAKEMISTO/kokeilut/$KOKEILU.d"/*; do
      if [ -f "$f" ]; then ct push "$CT_ID" "$f" "/root/kokeilu/$(basename "$f")"; fi
    done
  fi
  ct push "$CT_ID" "$TARKISTUS_SH" /root/kokeilu/tarkistus.sh
  if [ -n "$SALAINEN" ]; then salainen_sisaan; fi
  if [ "$KUIVA" = 1 ]; then
    echo "+ $PCT exec $CT_ID -- timeout 120 bash /root/kokeilu/tarkistus.sh $KOKEILU"
  else
    set +e
    timeout 150 "$PCT" exec "$CT_ID" -- timeout 120 bash /root/kokeilu/tarkistus.sh "$KOKEILU" >"$LOKIT/tarkistus.stdout" 2>&1
    TARKISTUS_RC=$?
    set -e
  fi
  if [ -n "$SALAINEN" ]; then salainen_pois; fi
fi

# 7. lokien keruu: tyokansio (max 50 Mt), proxyn lokista estetyt pyynnot (-> rajarikkeet)
if [ "$KUIVA" = 1 ]; then
  echo "+ $PCT exec $CT_ID -- tar -C /root/kokeilu -czf - .  > $LOKIT/tyokansio.tar.gz   # max 50 Mt"
  echo "+ $PCT exec $PROXY_ID -- grep -F $IP /var/log/squid/access.log  > $LOKIT/proxy.log   # estetyt yhteydet -> rajarikkeet"
else
  set +e
  "$PCT" exec "$CT_ID" -- tar -C /root/kokeilu -czf - . 2>/dev/null | head -c 52428800 >"$LOKIT/tyokansio.tar.gz"
  "$PCT" exec "$PROXY_ID" -- grep -F "$IP" /var/log/squid/access.log 2>/dev/null | tail -n 20000 >"$LOKIT/proxy.log"
  set -e
  python3 -I "$HL" rajarikkeet --loki "$LOKIT/proxy.log" --ip "$IP" --alku "$ALKU_EPOCH" --loppu "$(date +%s)" >"$LOKIT/rajarikkeet.json" \
    || echo '[]' >"$LOKIT/rajarikkeet.json"
  chmod -R go-rwx "$LOKIT"
fi

# 8. pysayta, snapshot jalkeen; siivoa() tuhoaa kloonin (ellei --sailyta) ja kirjaa rivin
ct stop "$CT_ID" --timeout 30
ct snapshot "$CT_ID" jalkeen
if [ "$KUIVA" = 1 ]; then
  echo "+ $PCT config $CT_ID   # varmistus ennen tuhoamista: hostname $NIMI, bridge=$SILTA, ei mp/dev/lxc-rivia"
  ct destroy "$CT_ID" --purge 1
  echo "+ rm -f $FW_HAKEMISTO/$CT_ID.fw"
  echo "+ python3 -I hl.py paata ...   # kustannuksen tasaus + rivi $TILA/kokeilut.jsonl"
fi
exit 0
