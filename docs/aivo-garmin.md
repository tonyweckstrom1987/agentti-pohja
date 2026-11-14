# Aivo: Garmin-datan haku isännälle

Ohje Tonylle. Kaikki komennot ajetaan Proxmox-isännällä rootina, ellei toisin sanota. Agentti tai pilvi-Claude ei aja näitä eikä koskaan syötä Garmin-salasanaa.

## Mitä tämä tekee

- `aivo/garmin.py` hakee joka aamu klo 6.00 eilisen ja tämän päivän tiedot: päivän yhteenveto, uni, stressi, Body Battery, askeleet, treenit, leposyke ja HRV.
- Data tallennetaan isännälle hakemistoon `/var/lib/aivo/garmin/` (700, käyttäjä `aivo`). Jokaiselle päivälle tulee oma tiedosto `garmin-VVVV-KK-PP.jsonl`, jossa on yksi rivi tietolajia kohden (600).
- Kirjautumistunniste on hakemistossa `/var/lib/aivo/garmin-kirjautuminen/` (700, tiedosto 600). Salasanaa ei tallenneta.
- Tilatiedosto `tila.json` sisältää vain tilan, ajan, päivämäärät, rivimäärät ja virhetyypit. Se viedään CT115:lle, jotta aamukooste voi pyytää uutta kirjautumista. Terveysarvoja siinä ei ole.
- Maanantaisin edellisen viikon tiivistelmä kirjoitetaan hakemistoon `/var/lib/aivo/kirjasto/garmin/`. Jos kirjasto on git-repo, se commitoidaan ja pushataan. GitHubiin osoittavaa etärepoa koodi ei hyväksy.
- Terveysdata ei mene GitHubiin: ei koodiin, ei PR:iin, ei docs-repoon eikä tulosraportteihin. Lokiin tulostuu vain rivimääriä ja virhetyyppejä.

## Kirjasto

`garminconnect` 0.3.20 (MIT, cyberjunky/python-garminconnect, julkaistu 23.10.2026, aktiivinen: viisi julkaisua syys–lokakuussa 2026). Kirjastolla on oma kirjautumisensa, eikä se enää käytä `garth`-kirjastoa. `garth` on vanhentunut: tekijä lopetti ylläpidon maaliskuussa 2026, koska Garmin muutti kirjautumista.

Riippuvuudet on lukittu tiedostoon `aivo/requirements-garmin.lock`: versiot ja sha256-tiivisteet, vain wheel-paketit (ei asennusskriptejä). Huomaa: `curl_cffi` sisältää valmiiksi käännetyn curl-kirjaston, joka matkii selaimen TLS-tunnistetta. Garminin kirjautuminen vaatii sitä.

## Asennus (kerran)

1. Tarkista Python. Version pitää olla vähintään 3.12.

   ```
   python3 --version
   ```

2. Luo käyttäjä ja hakemistot.

   ```
   useradd --system --home-dir /var/lib/aivo --shell /usr/sbin/nologin aivo
   install -d -m 700 -o aivo -g aivo /var/lib/aivo /var/lib/aivo/garmin /var/lib/aivo/garmin-kirjautuminen /var/lib/aivo/kirjasto
   ```

3. Hae koodi (kun PR on yhdistetty) ja asenna riippuvuudet omaan virtuaaliympäristöön.

   ```
   git clone https://github.com/tonyweckstrom1987/agentti-pohja.git /opt/aivo/agentti-pohja
   apt install python3-venv          # jos venv puuttuu
   python3 -m venv /opt/aivo/venv
   /opt/aivo/venv/bin/pip install --only-binary :all: --no-deps --require-hashes -r /opt/aivo/agentti-pohja/aivo/requirements-garmin.lock
   /opt/aivo/venv/bin/pip check
   ```

   `pip check` tulostaa `No broken requirements found.`. Jos tiiviste ei täsmää, pip pysähtyy eikä asenna mitään.

4. Luo CT115:lle hakemisto tilatiedostolle.

   ```
   pct exec 115 -- install -d -m 755 /var/lib/aivo-tila
   ```

5. Asenna ajastin, mutta älä vielä käynnistä sitä.

   ```
   cp /opt/aivo/agentti-pohja/systemd/aivo-garmin.service.mall /etc/systemd/system/aivo-garmin.service
   cp /opt/aivo/agentti-pohja/systemd/aivo-garmin.timer.mall /etc/systemd/system/aivo-garmin.timer
   systemctl daemon-reload
   ```

## Kirjautuminen

Aja itse päätteessä. Komento kysyy sähköpostin, salasanan (ei näy) ja tarvittaessa MFA-koodin.

```
cd /opt/aivo/agentti-pohja && runuser -u aivo -- /opt/aivo/venv/bin/python3 -m aivo.garmin kirjaudu
ls -l /var/lib/aivo/garmin-kirjautuminen/
```

Onnistuessa komento tulostaa `kirjaudu: ok, tunniste tallennettu (0o600)`, ja `ls -l` näyttää rivin `-rw------- 1 aivo aivo ... garmin_tokens.json`. Älä liitä tiedoston sisältöä mihinkään.

Kun tunniste vanhenee, haku kirjoittaa tilaksi `kirjaudu` ja aamukoosteen Tarvitaan-riville tulee pyyntö "Garmin: kirjaudu uudelleen isännällä". Aja silloin tämä sama komento uudelleen.

## Ensimmäinen haku ja tarkistus

```
systemctl start aivo-garmin.service
journalctl -u aivo-garmin.service -n 20 --no-pager
ls -l /var/lib/aivo /var/lib/aivo/garmin
wc -l /var/lib/aivo/garmin/*.jsonl
pct exec 115 -- cat /var/lib/aivo-tila/garmin-tila.json
```

Odotettu tulos: lokissa näkyy rivi `hae: tila=ok rivit={'VVVV-KK-PP': 8, ...}`, hakemistot ovat `drwx------ aivo aivo`, päivätiedostot ovat `-rw------- aivo aivo`, ja tilatiedostossa on vain tila, aika, päivät ja rivimäärät. Näitä tulosteita saa liittää Claudelle. Älä liitä `.jsonl`-tiedostojen sisältöä.

Kun kaikki on kunnossa, ota ajastin käyttöön:

```
systemctl enable --now aivo-garmin.timer
systemctl list-timers aivo-garmin.timer
```

## Viikkotiivistelmä Giteaan (valinnainen)

Ilman tätä vaihetta tiivistelmä jää vain isännälle. Jos haluat sen Giteaan:

1. Luo Giteaan **yksityinen** repo, esimerkiksi `aivo-kirjasto`.
2. Luo käyttäjälle `aivo` SSH-avain ja lisää julkinen avain repon käyttöönottoavaimeksi (kirjoitusoikeus vain tähän repoon).

   ```
   install -d -m 700 -o aivo -g aivo /var/lib/aivo/.ssh
   runuser -u aivo -- ssh-keygen -t ed25519 -N "" -f /var/lib/aivo/.ssh/id_ed25519 -C aivo-kirjasto
   cat /var/lib/aivo/.ssh/id_ed25519.pub
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto init -b main
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto config user.name aivo
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto config user.email aivo@localhost
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto remote add origin <gitea-ssh-osoite>/aivo-kirjasto.git
   ```

3. Tee ensimmäinen push käsin, koska SSH kysyy Gitean palvelinavaimen hyväksyntää. Tiivistelmää ei vielä ole, joten tee ensin tyhjä commit:

   ```
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto commit --allow-empty -m alku
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto push -u origin main
   ```

Sen jälkeen maanantain ajo pushaa tiivistelmän itse. Lokiin tulee rivi `viikko: VVVV-Www.md: push ok`.

## Poisto käytöstä

```
systemctl disable --now aivo-garmin.timer
```

Data ja tunniste jäävät paikalleen. Poista ne vain itse, jos haluat.

## Tunnetut rajoitukset

- Tiivistelmän kenttien nimet (`totalSteps`, `restingHeartRate`, `dailySleepDTO` ...) on otettu kirjaston esimerkeistä. Ne tarkistetaan ensimmäisestä oikeasta ajosta. Puuttuva kenttä näkyy viivana.
- Garmin muuttaa rajapintaansa ilman ilmoitusta. Jos haku alkaa epäonnistua, päivitä lukitustiedosto uudempaan `garminconnect`-versioon.
- Kolmannen osapuolen koodi ajetaan isännällä omana käyttäjänään ja systemd-suojauksin (`ProtectSystem=strict`, `NoNewPrivileges`), mutta ei erillisessä kontissa.
