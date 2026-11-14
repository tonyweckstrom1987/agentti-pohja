#!/bin/sh
# Kauppatiimin data vain luku -kopiona CT119 -> CT115 viikkokatselmointia varten.
# Ajetaan Proxmox-isannalla rootina (cron). Ei salaisuuksia: .env ja llm.env eivat kopioidu.
# Tonyn paatos 1.11.2026. Versio 2 (2.11.2026, riippumattoman tarkastuksen korjaukset):
# - PATH asetetaan itse (cronin oletus-PATH ei sisalla /usr/sbin, jossa pct on)
# - lukitus (flock), siivous virheissakin (trap), symlinkit ohitetaan, atominen vaihto CT115:lla
# - tyhja vienti (ei yhtaan .db:ta) ei korvaa vanhaa kopiota
# - tuloste lokiin /var/log/kauppa-vienti.log (cron-rivi ohjaa sen sinne)
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
LAHDE=119
KOHDE=115
TMP=/tmp/kauppa-vienti
KOHDEHAK=/var/lib/claude-jono/kauppa
LUKKO=/run/kauppa-vienti.lock

exec 9>"$LUKKO"
flock -n 9 || { echo "$(date -Is) kauppa-vienti: edellinen ajo kesken, ohitetaan"; exit 0; }

siivoa() {
  rm -rf "$TMP"
  pct exec $LAHDE -- rm -rf /tmp/kauppa-vienti /tmp/kauppa-vienti.tar.gz 2>/dev/null || true
  pct exec $KOHDE -- rm -f /tmp/kauppa-vienti.tar.gz 2>/dev/null || true
}
trap siivoa EXIT

echo "$(date -Is) kauppa-vienti alkaa"
rm -rf "$TMP" && mkdir -p "$TMP"

# Tietokannat sqliten omalla backupilla (johdonmukainen kopio kesken ajon), muut tiedostot sellaisenaan.
# Symlinkit ohitetaan, jottei kauppa-kayttajan koodi voi ohjata rootia kopioimaan muuta (esim. llm.env).
pct exec $LAHDE -- timeout 300 python3 - <<'PY'
import os, shutil, sqlite3, sys
ulos = "/tmp/kauppa-vienti"
shutil.rmtree(ulos, ignore_errors=True)
os.makedirs(ulos)
SALLITUT = ("/srv/kauppa/data/", "/srv/kauppa/havainnot/")
def kelpaa(p):
    return os.path.isfile(p) and not os.path.islink(p) and os.path.realpath(p).startswith(SALLITUT)
dbt = 0
for nimi in ("salkku_flash.db", "salkku_pro.db", "salkku.db", "kokeilut.db", "markkina.db", "historia.db", "lisadata.db"):
    p = "/srv/kauppa/data/" + nimi
    if kelpaa(p):
        src = sqlite3.connect("file:" + p + "?mode=ro", uri=True)
        dst = sqlite3.connect(os.path.join(ulos, nimi))
        src.backup(dst)
        dst.close(); src.close()
        dbt += 1
for p in ("/srv/kauppa/data/tapahtumat.jsonl", "/srv/kauppa/havainnot/havainnot.md",
          "/srv/kauppa/havainnot/havainnot.jsonl"):
    if kelpaa(p):
        shutil.copy2(p, ulos, follow_symlinks=False)
if dbt == 0:
    sys.exit("yhtaan tietokantaa ei loytynyt, vientia ei tehda")
shutil.make_archive("/tmp/kauppa-vienti", "gztar", ulos)
print("tietokantoja", dbt)
PY

pct pull $LAHDE /tmp/kauppa-vienti.tar.gz "$TMP/vienti.tar.gz"
pct push $KOHDE "$TMP/vienti.tar.gz" /tmp/kauppa-vienti.tar.gz
# Pura ensin viereen ja vaihda vasta onnistuneen purun jalkeen (lukija ei nae puolikasta hakemistoa).
pct exec $KOHDE -- sh -c "set -e; rm -rf $KOHDEHAK.uusi; mkdir -p $KOHDEHAK.uusi; \
  tar --no-same-owner --no-same-permissions -xzf /tmp/kauppa-vienti.tar.gz -C $KOHDEHAK.uusi; \
  date -Is > $KOHDEHAK.uusi/PAIVITETTY; \
  chown -R root:claudejono $KOHDEHAK.uusi; chmod -R u=rwX,g=rX,o= $KOHDEHAK.uusi; \
  rm -rf $KOHDEHAK.vanha; if [ -d $KOHDEHAK ]; then mv $KOHDEHAK $KOHDEHAK.vanha; fi; \
  mv $KOHDEHAK.uusi $KOHDEHAK; rm -rf $KOHDEHAK.vanha"
echo "$(date -Is) kauppa-vienti ok"
