#!/usr/bin/env bash
# luo-pohja.sh - luo hiekkalaatikon mallipohjan (CT 9000), eristetyn sillan (vmbr2) ja sallintaproxyn (CT 9001).
# AJETAAN PROXMOX-ISANNALLA (root), Tony ajaa itse. Katso ensin: luo-pohja.sh --kuiva
#
# Kaytto: luo-pohja.sh [--kuiva]
#   --kuiva   tulostaa komennot eika aja mitaan (ei pct-kutsuja, ei tiedostokirjoituksia)
# Pakolliset ymparistomuuttujat (oikeassa ajossa; kuivassa ajossa korvataan <paikanpitajilla>):
#   HL_TALLENNUS      tallennustila konteille, esim. konttilevy
#   HL_DEBIAN_POHJA   Debian-kontin vztmpl, esim. local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst (pveam download ensin)
#   HL_PROXY_LAN_IP   proxyn LAN-osoite CIDR-muodossa, vapaa osoite (esim. 192.168.1.x/24)
#   HL_PROXY_LAN_GW   LAN-yhdyskaytava
# Valinnainen: HL_DNS (proxyn DNS, oletus 1.1.1.1), HL_PCT (testeja varten).
# Idempotentti: jo olemassa oleviin CT:hin ja siltaan ei koske, vaan ohittaa vaiheen. Mitaan ei poisteta.
# Tallessa ei ole salaisuuksia; tunnuksia ei tarvita.
set -euo pipefail
LC_ALL=C
export LC_ALL

PCT="${HL_PCT:-pct}"
SILTA=vmbr2
VERKKO=10.98.0
PROXY_IP="$VERKKO.2"
POHJA_IP="$VERKKO.3"
POHJA_ID=9000
PROXY_ID=9001
SILTA_TIEDOSTO="${HL_SILTA_TIEDOSTO:-/etc/network/interfaces.d/hiekkalaatikko}"
KUIVA=0

virhe() { echo "luo-pohja.sh: $*" >&2; exit 2; }
for a in "$@"; do
  case "$a" in
    --kuiva) KUIVA=1 ;;
    -h|--ohje) sed -n '2,15p' "$0"; exit 0 ;;
    *) virhe "tuntematon valinta: $a" ;;
  esac
done

if [ "$KUIVA" = 1 ]; then
  TALLENNUS="${HL_TALLENNUS:-<TALLENNUS>}"
  POHJA="${HL_DEBIAN_POHJA:-<DEBIAN_POHJA>}"
  LAN_IP="${HL_PROXY_LAN_IP:-<PROXY_LAN_IP/24>}"
  LAN_GW="${HL_PROXY_LAN_GW:-<LAN_GW>}"
else
  TALLENNUS="${HL_TALLENNUS:-}"; POHJA="${HL_DEBIAN_POHJA:-}"; LAN_IP="${HL_PROXY_LAN_IP:-}"; LAN_GW="${HL_PROXY_LAN_GW:-}"
  [ -n "$TALLENNUS" ] && [ -n "$POHJA" ] && [ -n "$LAN_IP" ] && [ -n "$LAN_GW" ] || virhe "aseta HL_TALLENNUS, HL_DEBIAN_POHJA, HL_PROXY_LAN_IP ja HL_PROXY_LAN_GW (ks. --ohje)"
fi
DNS="${HL_DNS:-1.1.1.1}"
case "$TALLENNUS$POHJA$LAN_IP$LAN_GW$DNS" in *[[:space:]\;\&\|\$\`\"\'\\]*) virhe "arvoissa ei saa olla valilyonteja tai kuoren erikoismerkkeja" ;; esac

ajaa() {
  if [ "$KUIVA" = 1 ]; then printf '+'; printf ' %q' "$@"; printf '\n'; else "$@"; fi
}
otsikko() { echo; echo "## $*"; }

TMP=""
if [ "$KUIVA" = 0 ]; then TMP="$(mktemp -d)"; trap 'rm -rf "${TMP:?}"' EXIT; fi

# --- tiedostosisallot ---
SILTA_SISALTO="# Hiekkalaatikon eristetty silta: ei fyysista porttia eika IP:ta isannalle (isanta ei reitita tahan verkkoon).
auto $SILTA
iface $SILTA inet manual
    bridge-ports none
    bridge-stp off
    bridge-fd 0"
SQUID="http_port $PROXY_IP:3128
acl sandbox src $VERKKO.0/24
acl privaatti dst 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16
acl allowed dstdomain .github.com .githubusercontent.com .pypi.org .pythonhosted.org .npmjs.org .nodejs.org .debian.org .crates.io proxy.golang.org
acl Safe_ports port 80 443
acl SSL_ports port 443
acl CONNECT method CONNECT
http_access deny !Safe_ports
http_access deny CONNECT !SSL_ports
http_access deny privaatti
http_access allow sandbox allowed
http_access deny all
forwarded_for delete
via off
access_log /var/log/squid/access.log squid"

kirjoita() {  # kirjoita <kohde-ct> <polku-kontissa> <sisalto>
  if [ "$KUIVA" = 1 ]; then
    echo "+ pct push $1 <tiedosto> $2   # sisalto:"; printf '%s\n' "$3" | sed 's/^/#   /'
  else
    printf '%s\n' "$3" >"$TMP/f"; "$PCT" push "$1" "$TMP/f" "$2"
  fi
}

olemassa() { [ "$KUIVA" = 0 ] && "$PCT" config "$1" >/dev/null 2>&1; }

otsikko "0. esitarkistukset (vain luku)"
if [ "$KUIVA" = 1 ]; then
  echo "+ pct config $POHJA_ID; pct config $PROXY_ID            # molempien pitaa puuttua (tai ne ohitetaan)"
  echo "+ grep -q 'interfaces.d' /etc/network/interfaces      # silta-asetus luetaan vain jos interfaces.d on mukana"
  echo "+ pve-firewall status                                  # Proxmox-palomuurin tila (tama skripti ei muuta sita)"
else
  grep -q 'interfaces\.d' /etc/network/interfaces || virhe "/etc/network/interfaces ei lue interfaces.d-hakemistoa; lisaa silta kasin"
  echo "Proxmox-palomuuri: $(pve-firewall status 2>&1 | head -1)"
  echo "(Jos 'disabled': silta ja proxy eristavat silti; per-CT-saannot ovat toinen kerros, katso README kohta 3.)"
fi

otsikko "1. eristetty silta $SILTA"
if [ "$KUIVA" = 0 ] && ip link show "$SILTA" >/dev/null 2>&1; then
  echo "$SILTA on jo olemassa: ohitetaan"
else
  if [ "$KUIVA" = 1 ]; then
    echo "+ kirjoita $SILTA_TIEDOSTO:"; printf '%s\n' "$SILTA_SISALTO" | sed 's/^/#   /'
  else
    [ ! -e "$SILTA_TIEDOSTO" ] || virhe "$SILTA_TIEDOSTO on jo olemassa mutta $SILTA ei ole ylhaalla; tarkista kasin"
    printf '%s\n' "$SILTA_SISALTO" >"$SILTA_TIEDOSTO"
  fi
  ajaa ifup "$SILTA"
  echo "# tarkistus: ip -br addr show $SILTA   (ei IPv4-osoitetta isannalla)"
fi

otsikko "2. sallintaproxy CT $PROXY_ID ($PROXY_IP vmbr2-puolella, LAN-puoli ulospaasyyn)"
if olemassa "$PROXY_ID"; then
  echo "CT $PROXY_ID on jo olemassa: ohitetaan"
else
  ajaa "$PCT" create "$PROXY_ID" "$POHJA" --hostname hl-proxy --unprivileged 1 --cores 1 --memory 512 --swap 0 \
    --rootfs "$TALLENNUS:4" --nameserver "$DNS" --onboot 1 --description "hiekkalaatikon sallintaproxy (squid)" \
    --net0 "name=eth0,bridge=$SILTA,ip=$PROXY_IP/24" --net1 "name=eth1,bridge=vmbr0,ip=$LAN_IP,gw=$LAN_GW"
  ajaa "$PCT" start "$PROXY_ID"
  ajaa sleep 5
  ajaa "$PCT" exec "$PROXY_ID" -- bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq squid'
  kirjoita "$PROXY_ID" /etc/squid/squid.conf "$SQUID"
  ajaa "$PCT" exec "$PROXY_ID" -- squid -k parse
  ajaa "$PCT" exec "$PROXY_ID" -- systemctl restart squid
fi

otsikko "3. mallipohja CT $POHJA_ID ($POHJA_IP, vain $SILTA, ei yhtaan LAN-korttia)"
if olemassa "$POHJA_ID"; then
  echo "CT $POHJA_ID on jo olemassa: ohitetaan"
else
  ajaa "$PCT" create "$POHJA_ID" "$POHJA" --hostname hl-pohja --unprivileged 1 --cores 2 --memory 2048 --swap 0 \
    --rootfs "$TALLENNUS:8" --onboot 0 --description "hiekkalaatikon mallipohja (ala kayta suoraan)" \
    --net0 "name=eth0,bridge=$SILTA,ip=$POHJA_IP/24"
  ajaa "$PCT" start "$POHJA_ID"
  ajaa sleep 5
  kirjoita "$POHJA_ID" /etc/apt/apt.conf.d/01proxy "Acquire::http::Proxy \"http://$PROXY_IP:3128\";"
  kirjoita "$POHJA_ID" /etc/environment "http_proxy=http://$PROXY_IP:3128
https_proxy=http://$PROXY_IP:3128
HTTP_PROXY=http://$PROXY_IP:3128
HTTPS_PROXY=http://$PROXY_IP:3128
no_proxy=localhost,127.0.0.1"
  ajaa "$PCT" exec "$POHJA_ID" -- bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-pip git curl ca-certificates build-essential iproute2 netcat-openbsd nmap strace procps'
  ajaa "$PCT" exec "$POHJA_ID" -- bash -c 'mkdir -p /root/kokeilu && history -c; rm -f /root/.bash_history'
  # tarkistus ennen mallipohjaksi muuttoa: proxyn kautta paasee githubiin, suoraan ei paase minnekaan
  ajaa "$PCT" exec "$POHJA_ID" -- bash -c 'curl -sS -o /dev/null -w "%{http_code}\n" --max-time 15 https://github.com'
  ajaa "$PCT" exec "$POHJA_ID" -- bash -c 'if curl -sS --noproxy "*" --max-time 5 -o /dev/null https://1.1.1.1; then echo VIRHE-suora-yhteys-toimii; exit 1; else echo ok-suora-yhteys-estetty; fi'
  ajaa "$PCT" stop "$POHJA_ID"
  ajaa "$PCT" template "$POHJA_ID"
fi
otsikko "valmis"
echo "Seuraavaksi: aja-kokeilu.sh --kuiva --kokeilu turva-01 --adapteri kuiva"
