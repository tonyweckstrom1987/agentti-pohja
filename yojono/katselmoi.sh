#!/bin/sh
set -eu
REPO="$1"
BUDJETTI="${2:-1.50}"
BASE=/var/lib/yojono
set -a; . /etc/yojono/env; set +a
export CLAUDE_CONFIG_DIR="$BASE/claude"
mkdir -p "$BASE/tyo" "$BASE/raportit"
cd "$BASE/tyo"
rm -rf "$REPO"
git clone --depth 1 "https://github.com/tonyweckstrom1987/$REPO" "$REPO"
cd "$REPO"
PVM=$(date +%F)
PROMPT='Tee koodikatselmointi tämän repon koodille. Tehtävä on vain lukea: älä muuta tiedostoja. Etsi oikeita bugeja, turvallisuusriskejä ja virheenkäsittelyn aukkoja. Jokaisesta löydöksestä kirjoita: vakavuus (korkea, keskitaso, matala), tiedosto ja rivi, lyhyt perustelu ja miten ongelma toistetaan. Raportoi vain se, minkä olet todennut lukemalla koodin. Jos et ole varma, kirjoita epävarma ja miksi. Jos löydöksiä ei ole, sano se suoraan. Enintään 10 löydöstä, tärkeimmät ensin. Tarkista lisäksi erikseen: käyttääkö koodi MCP initialize-kättelyä, Mcp-Session-Id-otsaketta tai Sampling-ominaisuutta (MCP-spesifikaatio 2026-07-28 poistaa tai vanhentaa nämä) ja kerro mitkä kohdat se koskee. Kirjoita suomeksi. Kirjoita koko raportti yhteen viimeiseen viestiin vasta, kun olet lukenut koodin: älä kirjoita raportin osia työkalukutsujen väliin, koska vain viimeinen viesti tallentuu. Aloita raportti otsikkorivillä, joka alkaa merkeillä "# Koodikatselmointi:".'
/root/.local/bin/claude -p "$PROMPT" --model sonnet --max-budget-usd "$BUDJETTI" --allowedTools "Read,Grep,Glob" --output-format json > "$BASE/raportit/$PVM-$REPO.json" || true
/opt/agentscope/bin/python -c "import sys,json; d=json.load(open(sys.argv[1])); r=d.get('result'); (r is None) and (print('EI RAPORTTIA:', d.get('subtype'), d.get('errors'), 'kulu USD:', d.get('total_cost_usd')) or sys.exit(1)); open(sys.argv[2],'w').write(d['result']); print('kulu USD:', d.get('total_cost_usd'))" "$BASE/raportit/$PVM-$REPO.json" "$BASE/raportit/$PVM-$REPO.md"
echo "raportti: $BASE/raportit/$PVM-$REPO.md"
