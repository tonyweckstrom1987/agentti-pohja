#!/bin/sh
set -eu
REPO="$1"
BUDJETTI="${2:-1.50}"
BASE="${KATSELMOI_PERUS:-/var/lib/yojono}"
AJURI="${YOJONO_AJURI:-/opt/yojono-ajuri}"
PYTHON="${KATSELMOI_PYTHON:-/opt/agentscope/bin/python}"
# Rajaus: katselmoidaan vain muutokset edellisen onnistuneen katselmoinnin jalkeen (yojono/rajaus.py); budjetti ei muutu.
set -a; . "${KATSELMOI_ENV:-/etc/yojono/env}"; set +a
export CLAUDE_CONFIG_DIR="$BASE/claude"
mkdir -p "$BASE/tyo" "$BASE/raportit"
cd "$BASE/tyo"
rm -rf "$REPO"
git clone --no-tags "${KATSELMOI_URL_ETUOSA:-https://github.com/tonyweckstrom1987}/$REPO" "$REPO"
cd "$REPO"
PVM=$(date +%F)
# rajaustiedostot klooniin (tyohakemisto, jonka Read-oikeus on jo katselmoijalla); klooni poistetaan seuraavan ajon alussa
RAJAUS="$PWD/.katselmointi-rajaus"
LOKI="$BASE/tyo/$REPO.rajaus.log"
rm -rf "$RAJAUS"
"$PYTHON" -B "$AJURI/yojono/rajaus.py" "$PWD" "$RAJAUS" "$BASE" "$REPO" > "$LOKI" 2>&1 || { cat "$LOKI" >&2; echo "rajaus epaonnistui" >&2; exit 1; }
MUUTTUNEITA=$(sed -n 's/^muuttuneita=//p' "$LOKI")
HEAD_SHA=$(git rev-parse HEAD)
RAP="$BASE/raportit/$PVM-$REPO"
tallenna_commit() { mkdir -p "$BASE/rajaus"; echo "$HEAD_SHA" > "$BASE/rajaus/$REPO.commit"; }
if [ "$MUUTTUNEITA" = "0" ]; then
  # ei muutoksia edellisen katselmoinnin jalkeen: ei claude-kutsua, kulu 0
  { cat "$RAJAUS/otsikko.md"; echo "Ei muutoksia edellisen onnistuneen katselmoinnin jälkeen (HEAD ${HEAD_SHA}). Koodia ei katselmoitu uudelleen, eikä uusia löydöksiä ole."; } > "$RAP.md"
  printf '{"subtype":"success","is_error":false,"total_cost_usd":0,"result":"ei muutoksia"}\n' > "$RAP.json"
  tallenna_commit
  echo "kulu USD: 0 (ei muutoksia)"; echo "raportti: $RAP.md"; exit 0
fi
PROMPT='Tee koodikatselmointi vain tämän repon MUUTOKSILLE edellisen katselmoinnin jälkeen, älä koko repoa. Muutokset: lue ensin '"$RAJAUS"'/muutokset.md (tiedostolista ja mitä jäi pois rajan takia) ja sitten '"$RAJAUS"'/diff.patch (git diff). Voit lukea muuttuneiden tiedostojen koko sisällön ymmärtääksesi kontekstin, mutta raportoi vain löydökset, jotka liittyvät diffin muutoksiin. Tehtävä on vain lukea: älä muuta tiedostoja. Etsi oikeita bugeja, turvallisuusriskejä ja virheenkäsittelyn aukkoja. Jokaisesta löydöksestä kirjoita: vakavuus (korkea, keskitaso, matala), tiedosto ja rivi, lyhyt perustelu ja miten ongelma toistetaan. Raportoi vain se, minkä olet todennut lukemalla koodin. Jos et ole varma, kirjoita epävarma ja miksi. Jos löydöksiä ei ole, sano se suoraan. Enintään 10 löydöstä, tärkeimmät ensin. Tarkista lisäksi erikseen: käyttääkö muuttunut koodi MCP initialize-kättelyä, Mcp-Session-Id-otsaketta tai Sampling-ominaisuutta (MCP-spesifikaatio 2026-07-28 poistaa tai vanhentaa nämä) ja kerro mitkä kohdat se koskee. Kirjoita suomeksi.'
"${KATSELMOI_CLAUDE:-/root/.local/bin/claude}" -p "$PROMPT" --model sonnet --max-budget-usd "$BUDJETTI" --allowedTools "Read,Grep,Glob" --output-format json > "$RAP.json" || true
"$PYTHON" -c "import sys,json; d=json.load(open(sys.argv[1])); r=d.get('result'); (r is None) and (print('EI RAPORTTIA:', d.get('subtype'), d.get('errors'), 'kulu USD:', d.get('total_cost_usd')) or sys.exit(1)); open(sys.argv[2],'w').write(d['result']); print('kulu USD:', d.get('total_cost_usd'))" "$RAP.json" "$RAP.md"
# rajauksen yhteenveto raportin alkuun (koodi, ei mallin kirjoittama) ja onnistunut ajo pohjaksi seuraavalle
cat "$RAJAUS/otsikko.md" "$RAP.md" > "$RAP.md.uusi" && mv "$RAP.md.uusi" "$RAP.md"
tallenna_commit
echo "raportti: $BASE/raportit/$PVM-$REPO.md"
