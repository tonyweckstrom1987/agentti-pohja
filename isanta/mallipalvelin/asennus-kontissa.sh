#!/usr/bin/env bash
# Aja AIVON KESKUSTELUPALVELUN KONTIN SISALLA (root). Asentaa Ollaman samaan konttiin, jossa Aivo ajetaan.
# Ollama kuuntelee vain osoitteessa 127.0.0.1, joten mikaan toinen kone (LAN, tailnet) ei paase siihen,
# eika asennus avaa palomuurin portteja tai muuta verkkoasetuksia.
# Kaytto: asennus-kontissa.sh ASETUSTIEDOSTO SHA256_TIIVISTE
set -euo pipefail
asetukset="${1:?kayta: asennus-kontissa.sh ASETUSTIEDOSTO SHA256}"
tiiviste="${2:?anna tarkistussumma (Ollaman julkaisun sha256sum.txt)}"
# shellcheck disable=SC1090
. "$asetukset"
for v in OLLAMA_VERSIO OLLAMA_PORTTI OLLAMA_MUISTI_MB; do
  [ -n "${!v:-}" ] || { echo "puuttuu asetus: $v" >&2; exit 2; }
done
case "$OLLAMA_PORTTI$OLLAMA_MUISTI_MB" in *[!0-9]*) echo "portti ja muisti ovat numeroita" >&2; exit 2;; esac
apt-get update -qq && apt-get install -y -qq curl zstd ca-certificates
tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/ollama.tar.zst" \
  "https://github.com/ollama/ollama/releases/download/${OLLAMA_VERSIO}/ollama-linux-amd64.tar.zst"
echo "$tiiviste  $tmp/ollama.tar.zst" | sha256sum -c -
tar -C /usr/local --zstd -xf "$tmp/ollama.tar.zst"
rm -rf "$tmp"
id ollama >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -m -d /var/lib/ollama ollama
cat > /etc/systemd/system/ollama.service <<UNIT
[Unit]
Description=Ollama (paikallinen mallipalvelin, vain loopback)
After=network-online.target
Wants=network-online.target

[Service]
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Environment=OLLAMA_HOST=127.0.0.1:${OLLAMA_PORTTI}
Environment=OLLAMA_KEEP_ALIVE=10m
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_NOPRUNE=1
MemoryMax=${OLLAMA_MUISTI_MB}M
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/ollama
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now ollama
echo "valmis. Tarkista: ss -ltn | grep ${OLLAMA_PORTTI}  (kuuntelun pitaa olla vain 127.0.0.1:${OLLAMA_PORTTI})"
