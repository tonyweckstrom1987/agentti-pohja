#!/usr/bin/env bash
# Clauden adapteri: EI VIELA TOTEUTETTU. Poistuu koodilla 78 (EX_CONFIG), jolloin kokeilu kirjautuu epaonnistuneeksi ilman kulua.
# Syy: Clauden tyokalut (Bash jne.) ajaisivat isannalla, ei kloonissa. Toteutus vaatii shell-tyokalun, joka ohjaa komennot
# `pct exec` -kutsuun (esim. MCP-palvelin) ja Clauden omat tyokalut pois paalta. Ks. README, avoimet kysymykset.
set -eu
echo "claude-adapteri ei ole viela toteutettu" >&2
exit 78
