#!/bin/bash
# tarkista-ja-pushaa.sh: ajaa repon testit ILMAN putkea ja pushaa vain, jos paluuarvo on 0.
# Syy: "testit | tail" peittaa paluuarvon (virheloki 5.11.2026), joten testin tulos luetaan suoraan $?-arvosta.
# Kaytto (repon sisalla): tarkista-ja-pushaa.sh [-n] [git push -argumentit...]
#   -n  vain testit, ei pushia (kuiva-ajo)
# Testikomento: 1) .tarkistus-tiedosto (ensimmainen ei-tyhja, ei-#-rivi ajetaan bash -o pipefail -tilassa), 2) Node: package.json + npm test,
#   3) Python: python3 -m unittest discover. Jos mikaan ei tasmaa, ei pushata (poistumiskoodi 2).
# Viimeinen tulostettu rivi on aina "TESTIT: OK <n>" tai "TESTIT: KAATUI".
set -euo pipefail

vain_testit=0
if [ "${1:-}" = "-n" ]; then vain_testit=1; shift; fi

juuri=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "tarkista-ja-pushaa: ei git-repo" >&2; echo "TESTIT: KAATUI"; exit 2; }
cd "$juuri"

loki=$(mktemp)
trap 'rm -f "$loki"' EXIT

komento=()
if [ -f .tarkistus ]; then
  rivi=$(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' .tarkistus | head -n 1 || true)
  [ -n "$rivi" ] && komento=(bash -o pipefail -c "$rivi")
fi
if [ "${#komento[@]}" -eq 0 ] && [ -f package.json ] && grep -q '"test"' package.json; then
  komento=(npm test)
fi
if [ "${#komento[@]}" -eq 0 ] && { [ -d test ] || [ -d tests ] || [ -d testit ] || compgen -G 'test_*.py' >/dev/null; }; then
  komento=(python3 -m unittest discover)
fi
if [ "${#komento[@]}" -eq 0 ]; then
  echo "tarkista-ja-pushaa: testikomentoa ei loytynyt (lisaa .tarkistus-tiedosto). Ei pushata." >&2
  echo "TESTIT: KAATUI"
  exit 2
fi

echo "tarkista-ja-pushaa: ajetaan: ${komento[*]}"
# Ei putkea: tulos ohjataan tiedostoon ja paluuarvo luetaan heti.
rc=0
"${komento[@]}" >"$loki" 2>&1 || rc=$?
tail -n 40 "$loki"

# Testien lukumaara: unittest "Ran N tests", TAP "# pass N", Jest/vitest "Tests: N passed". Muuten "?".
n=$(sed -n -E -e 's/^Ran ([0-9]+) tests?.*/\1/p' -e 's/^# pass ([0-9]+).*/\1/p' -e 's/^Tests:.* ([0-9]+) passed.*/\1/p' "$loki" | tail -n 1)
n=${n:-?}

if [ "$rc" -ne 0 ] || [ "$n" = "0" ]; then
  echo "tarkista-ja-pushaa: testit eivat mene lapi (paluuarvo $rc, testeja $n). Ei pushata." >&2
  echo "TESTIT: KAATUI"
  exit 1
fi

echo "TESTIT: OK $n"
if [ "$vain_testit" -eq 1 ]; then exit 0; fi
git push "$@"
