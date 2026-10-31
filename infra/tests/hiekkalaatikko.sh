#!/bin/sh
# Hiekkalaatikkotesti: ajaa roolin tilapaishakemistoon (ei kosketa oikeaa jarjestelmaa, ei vaadi root-oikeutta).
# Kayttajat, omistajat ja systemd ovat pois paalta. Tarkistaa: (1) 1. ajo luo, (2) 2. ajo ei muuta mitaan (idempotenssi),
# (3) --check ei muuta, (4) tiedostojen sisalto ja oikeudet.
# Kaytto: infra/tests/hiekkalaatikko.sh   (ANSIBLE=polku/ansible-playbook, jos ei ole PATHissa)
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
AP=${ANSIBLE:-ansible-playbook}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT INT TERM
ME=$(id -un); MYG=$(id -gn)
cd "$HERE"
export ANSIBLE_LOCAL_TEMP="$T/.tmp" ANSIBLE_REMOTE_TMP="$T/.tmp"
EV="claudejono_fs_root=$T claudejono_manage_users=false claudejono_manage_systemd=false claudejono_root_owner=$ME claudejono_root_group=$MYG claudejono_user=$ME claudejono_group=$MYG claudejono_prbotti_user=$ME claudejono_prbotti_group=$MYG"
run() { $AP site.yml -e "$EV" "$@"; }

echo "== 1. --check ei saa luoda mitaan"
run --check --diff >"$T/c1.log"
[ -z "$(find "$T" -path "$T/.tmp" -prune -o -path "$T/*.log" -prune -o -type f -print)" ] || { echo "VIRHE: --check loi tiedostoja"; exit 1; }
echo "ok"

echo "== 2. ensimmainen ajo luo"
run >"$T/a1.log"; grep -E "ok=|changed=" "$T/a1.log" | tail -1

echo "== 3. toinen ajo: changed=0 (idempotentti)"
run >"$T/a2.log"; L=$(grep -E "changed=" "$T/a2.log" | tail -1); echo "$L"
echo "$L" | grep -q "changed=0 " || { echo "VIRHE: ei idempotentti"; cat "$T/a2.log"; exit 1; }

echo "== 4. --check --diff luotua tilaa vastaan: changed=0"
run --check --diff >"$T/c2.log"; L=$(grep -E "changed=" "$T/c2.log" | tail -1); echo "$L"
echo "$L" | grep -q "changed=0 " || { echo "VIRHE: check nayttaa muutoksia"; exit 1; }

echo "== 5. sisalto ja oikeudet"
[ "$(stat -c %a "$T/etc/sudoers.d/claudejono")" = 440 ] || { echo "VIRHE: sudoers-oikeudet"; exit 1; }
grep -q 'NOPASSWD: /usr/local/sbin/claudejono-pr$' "$T/etc/sudoers.d/claudejono" || { echo "VIRHE: sudoers-saanto"; exit 1; }
[ "$(grep -c NOPASSWD "$T/etc/sudoers.d/claudejono")" = 1 ] || { echo "VIRHE: sudoersissa pitaa olla yksi saanto"; exit 1; }
cmp -s roles/claudejono/files/claudejono-pr "$T/usr/local/sbin/claudejono-pr" || { echo "VIRHE: wrapper"; exit 1; }
[ "$(stat -c %a "$T/etc/claudejono-pr")" = 700 ] || { echo "VIRHE: etc/claudejono-pr 0700"; exit 1; }
[ ! -e "$T/etc/claudejono-pr/token" ] || { echo "VIRHE: rooli ei saa luoda tunnusta"; exit 1; }
echo "ok"
echo "KAIKKI OK"
