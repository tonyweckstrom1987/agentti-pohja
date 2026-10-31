#!/usr/bin/env bash
# Ajetaan kloonissa. Tarkistaa tulostiedoston muodon (ei tuloksen hyvyytta).
set -eu
python3 - <<'PY'
import json
d = json.load(open("/root/kokeilu/tulokset.json"))
avaimet = ["data_lahde", "jakso_alku", "jakso_loppu", "kauppoja", "tuotto_pros", "osta_ja_pida_pros", "max_laskuvaihe_pros", "kulut_pros", "huomiot"]
puuttuu = [k for k in avaimet if k not in d]
assert not puuttuu, puuttuu
assert isinstance(d["huomiot"], list)
PY
test -s /root/kokeilu/RAPORTTI.md
