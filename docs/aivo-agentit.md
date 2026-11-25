# Aivo: eristettyjen agenttien ajuri (kontti 122)

Ajuri ajaa yksityisen tilan agenttitehtävät omana käyttäjänään `aivo-agentti`. Agentti ei näe muistia, keskusteluja eikä ydinohjeita: se saa vain tehtävän tekstin ja pyynnön mukana tulevat liitteet. Agenttiydin ja mallikytkentä ovat paketissa `aivo/agentti/`. Ajuri on moduuli `aivo/agentti_ajuri.py`, ja yksikkömalli on tiedostossa `systemd/aivo-agentit.service.mall`.

Kohdat 1–9 asentavat ajurin. Kohta 10 kytkee chatin: Aivo voi ehdottaa agenttitehtävää yksityisessä tilassa, ja tehtävä käynnistyy vain Tonyn painalluksella. Ilman kohtaa 10 tehtävän voi lisätä jonoon käsin (kohta 8).

## Miten ajuri toimii

- Palvelu `aivo-agentit.service` lukee kansiota `/var/lib/aivo-agentit/jono/` kymmenen sekunnin välein ja ajaa vanhimman pyynnön. Tehtäviä ajetaan yksi kerrallaan. Lukko (`ajuri.lukko`) estää toisen ajurin käynnistymisen.
- Ajuri siirtää pyynnön kansioon `kasitellyt/`, luo työkansion `tyo/<tunnus>/` (0700) ja kopioi liitteet sinne samoilla polku-, pääte- ja kokosäännöillä kuin agentin `kirjoita`-työkalu.
- Tehtävä ajetaan aliprosessina (`python3 -m aivo.agentti_ajuri lapsi`). Ajuri lopettaa aliprosessin, jos se kestää yli 21 minuuttia: agenttiytimen 20 minuutin aikaraja ja minuutin vara. Tulos on silloin `keskeytetty`.
- Tulos kirjoitetaan tiedostoon `tulokset/<tunnus>.json` (0640, ryhmä `aivo-agentit`).
- Ennen tulosta ajuri kopioi työkansion tiedostot kansioon `tulokset/<tunnus>/` (kansiot 2750, tiedostot 0640), jotta chat voi näyttää ne vain luettavina. Työkansio pysyy 0700:na. Linkkejä ei seurata, eikä yli 200 kt:n tiedostoja kopioida.
- Ajuri ei poista mitään. Pyynnöt, työkansiot ja tulokset jäävät talteen, ja niiden siivous on oma päätöksensä.
- Jos ajuri pysähtyy kesken tehtävän, se kirjoittaa seuraavalla käynnistyksellä tulokseksi `keskeytetty` ja syyksi `ajuri pysahtyi kesken tehtavan`.
- Lokiin (journald) tulee vain tunnus, tila, luvut ja virheen tyyppi, ei tehtävän sisältöä.

### Rajat

Ajuri ei lisää uusia kulu-, budjetti- tai askelrajoja (Tonyn päätös A1072). Voimassa ovat agenttiytimen rajat: 30 mallikutsua tehtävää kohden ja 20 minuuttia. Kaikki kutsut kulkevat funktion `openrouter_kulut.kutsu` kautta ZDR-pakotuksella, ja ne kirjataan samaan kulukirjanpitoon kuin chatin kutsut. Yhteinen 10 €:n päiväkatto pysäyttää myös agentit.

### Pyynnön muoto

Pyyntö on JSON-tiedosto `jono/<tunnus>.json`, jossa tunnus on 12 pientä heksamerkkiä (esim. `secrets.token_hex(6)`). Kirjoittaja tekee ensin väliaikaisen tiedoston, jonka nimi alkaa pisteellä, ja nimeää sen lopuksi. Ajuri lukee vain kelvolliset nimet. Pyynnön oikeuksien pitää olla 0640; ryhmä `aivo-agentit` periytyy kansiosta. Jos kirjoittajan palvelussa on `UMask=0077`, kirjoittaja asettaa oikeudet itse, muuten ajuri ei voi lukea pyyntöä.

```json
{"versio": 1, "teksti": "Tehtävän kuvaus", "liitteet": [{"nimi": "aineisto/a.md", "sisalto": "..."}], "keskustelu": "abc123"}
```

`liitteet` ja `keskustelu` ovat valinnaisia. Tuntematon kenttä, väärä versio, liian pitkä teksti (yli 20 000 merkkiä), yli 50 liitettä tai kelvoton liitteen nimi antavat tuloksen `virhe` ilman mallikutsuja.

### Tuloksen muoto

Kentät järjestyksessä: `versio`, `tunnus`, `keskustelu`, `tila` (`valmis`, `kesken`, `keskeytetty` tai `virhe`), `syy`, `raportti`, `tarkastus` (`arvio` ja `perustelu`), `tiedostot` (polku ja koko), `kutsuja`, `askelia`, `kesto_s`, `kulu_eur`, `kulu_tuntematon`, `mallit`, `rajat`, `loki` ja `valmistui` (UTC).

### Käyttäjät, ryhmät ja kansiot

| Kohde | Omistaja ja ryhmä | Oikeudet | Kuka käyttää |
|---|---|---|---|
| `/var/lib/aivo-agentit` | `aivo-agentti:aivo-agentit` | 0750 | ajuri; ryhmä pääsee alikansioihin |
| `jono/` | `aivo-agentti:aivo-agentit` | 2770 | kirjoittaja (myöhemmin chat) kirjoittaa, ajuri lukee ja siirtää |
| `kasitellyt/`, `tyo/` | `aivo-agentti:aivo-agentti` | 0700 | vain ajuri |
| `tulokset/` | `aivo-agentti:aivo-agentit` | 2750 | ajuri kirjoittaa, ryhmä lukee |
| `openrouter-avain` | `aivo-agentti:aivo-agentti` | 0600 | vain ajuri; chatin avainta ei hyväksytä |
| `/var/lib/claude-jono` | ennallaan | ennallaan | chat; ajuri ei näe kansiota (ks. alla) |
| kulukirjanpidon kolme tiedostoa | chatin käyttäjä, ryhmä `aivo-kulut` | 0660 | chat ja ajuri |

Kulukirjanpidon moduuli tarkistaa, että kirjanpidon kansioon voi kirjoittaa. Siksi yksikkö ei anna kirjoitusoikeutta oikeaan kansioon `/var/lib/claude-jono`. Sen sijaan palvelu näkee kansion paikalla oman tilapäisen kansion (`TemporaryFileSystem`), johon on liitetty vain kolme kulutiedostoa (`BindPaths`). Palvelu ei näe kansion muita tiedostoja eikä voi luoda, poistaa tai korvata tiedostoja oikeassa kansiossa. Tilapäiseen kansioon kirjoitettu katoaa, kun palvelu pysähtyy, eikä se näy muille. Kansion omistajaa ja oikeuksia ei muuteta, eikä chatin käyttäjää lisätä ryhmään `aivo-kulut`.

Ryhmän kirjoitusoikeus kolmeen tiedostoon sallii edelleen tiedostojen sisällön muuttamisen, myös tyhjentämisen. Moduuli tarvitsee lukkotiedostoon luku- ja kirjoitusoikeuden ja kirjanpitoon lisäysoikeuden. Tältä osin suoja perustuu koodiin: ajuri kirjoittaa kirjanpitoon vain funktion `openrouter_kulut.kutsu` kautta (rivien lisäys), eikä agentilla ole työkalua oman työkansionsa ulkopuolelle.

Liitos osoittaa siihen tiedostoon, joka oli paikallaan palvelun käynnistyessä. Kulutiedostoja ei siksi saa korvata uusilla (esimerkiksi siirtämällä uuden tiedoston vanhan päälle) palvelun ollessa käynnissä. Jos niin käy, käynnistä palvelu uudelleen. Kohta 7 tarkistaa, että palvelu ja chat käyttävät samoja tiedostoja.

Ajuri tarkistaa käynnistyessään, että kulutiedostot eivät kuulu sen omalle käyttäjälle. Väärin tehty asennus, jossa ajuri omistaa kirjanpidon, pysäyttää palvelun.

`aivo-agentti` ei kuulu ryhmiin `aivo-yksityinen`, `aivo-tyokalu` eikä `aivo-tyokalu-socket`. Lisäksi yksikön `InaccessiblePaths` piilottaa kansiot `/var/lib/aivo-yksityinen`, `/var/lib/aivo` ja `/var/lib/aivo-tyokalu`.

### Verkko

`IPAddressAllow` ei sovellu, koska OpenRouterin osoitteet vaihtuvat (CDN). Verkkoa rajaavat nyt kaksi kerrosta: agentilla ei ole verkkotyökalua (rakennetesti), ja ainoa ulospäin menevä kutsu on `openrouter_kulut.kutsu`, jossa ZDR on pakotettu. Nimipohjainen välityspalvelin kolmanneksi kerrokseksi on oma päätöksensä, eikä sitä tehdä tässä.

## Käyttöönotto

Komennot ajetaan kontissa 122 pääkäyttäjänä (root), ellei toisin mainita. Kontissa ei ole oikeuksien korotustyökalua, joten toisen käyttäjän komennot ajetaan muodossa `runuser -u <käyttäjä> -- <komento>`. Jokaisen tarkistuksen jälkeen on Odotettu-rivi. Jos tulos poikkeaa, pysähdy äläkä jatka seuraavaan kohtaan.

### 0. Snapshot

Isännällä:

```
pct snapshot 122 ennen-aivo-agentit
pct listsnapshot 122
pct config 122 | grep -E '^(unprivileged|features)'
```

Odotettu: listassa on `ennen-aivo-agentit`. Jos kontti on `unprivileged: 1`, rivillä `features` pitää olla `nesting=1`, koska yksikön eristysasetukset tarvitsevat sitä. Jos se puuttuu, älä lisää sitä tämän ohjeen perusteella, vaan kirjaa tilanne ja pyydä päätös.

### 1. Lähtötilanne

```
systemctl show -p User,WorkingDirectory aivo-chat
ls -l /var/lib/claude-jono/openrouter-kulut.jsonl /var/lib/claude-jono/openrouter-katto.jsonl /var/lib/claude-jono/openrouter-kulut.lock
stat -c '%A %U:%G %n' /var/lib/claude-jono
```

Kirjoita tulokset muistiin peruutusta varten. Odotettu: `User=` on chatin käyttäjä (`aivo-yksityinen` tai `aivo`), ja `WorkingDirectory=` on repon polku (tässä ohjeessa `/opt/agentti-pohja`). Kulutiedostoja voi puuttua; ne luodaan kohdassa 3.

### 2. Käyttäjä, ryhmät ja kansiot

```
groupadd --system aivo-agentit
groupadd --system aivo-kulut
useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --user-group aivo-agentti
usermod -aG aivo-agentit,aivo-kulut aivo-agentti
install -d -m 0750 -o aivo-agentti -g aivo-agentit /var/lib/aivo-agentit
install -d -m 2770 -o aivo-agentti -g aivo-agentit /var/lib/aivo-agentit/jono
install -d -m 2750 -o aivo-agentti -g aivo-agentit /var/lib/aivo-agentit/tulokset
install -d -m 0700 -o aivo-agentti -g aivo-agentti /var/lib/aivo-agentit/tyo /var/lib/aivo-agentit/kasitellyt
id aivo-agentti
ls -la /var/lib/aivo-agentit
```

Odotettu: `id` näyttää ryhmät `aivo-agentti`, `aivo-agentit` ja `aivo-kulut`, mutta ei ryhmiä `aivo-yksityinen`, `aivo-tyokalu` tai `aivo-tyokalu-socket`. Listauksessa on `drwxrws---` (jono), `drwxr-s---` (tulokset) ja `drwx------` (tyo, kasitellyt).

### 3. Kulukirjanpidon ryhmäoikeus

Agentit ja chat käyttävät samaa kirjanpitoa ja samaa päiväkattoa. Ajuri saa ryhmän `aivo-kulut` kautta luku- ja kirjoitusoikeuden vain kolmeen kulutiedostoon. Kansion `/var/lib/claude-jono` oikeuksia ei muuteta. Tiedostot luodaan valmiiksi chatin käyttäjän omistamina, jotta kumpikaan palvelu ei luo niitä omilla oikeuksillaan, ja yksikön liitokset löytävät ne. `CHAT` on kohdan 1 käyttäjä.

```
CHAT=$(systemctl show -p User --value aivo-chat); [ -n "$CHAT" ] && echo "CHAT=$CHAT" || echo "CHAT PUUTTUU: pysähdy"
cd /var/lib/claude-jono
for f in openrouter-kulut.jsonl openrouter-katto.jsonl openrouter-kulut.lock; do
  [ -e "$f" ] || install -m 0660 -o "$CHAT" -g aivo-kulut /dev/null "$f"
  chgrp aivo-kulut "$f" && chmod 0660 "$f"
done
stat -c '%A %U:%G %n' /var/lib/claude-jono openrouter-kulut.jsonl openrouter-katto.jsonl openrouter-kulut.lock
runuser -u aivo-agentti -- test -w /var/lib/claude-jono && echo "KANSIO KIRJOITETTAVISSA: pysähdy" || echo KANSIO-SUOJATTU
```

Odotettu: rivi `CHAT=<käyttäjä>`. Kansion rivi on sama kuin kohdassa 1. Kaikilla kolmella tiedostolla on `-rw-rw---- <chat-käyttäjä>:aivo-kulut`. Viimeinen rivi on `KANSIO-SUOJATTU`. Chat omistaa tiedostot, joten sen pääsy ei muutu eikä sitä tarvitse käynnistää uudelleen. Ajurin kirjoitusoikeus tarkistetaan kohdassa 5 samoilla liitoksilla kuin palvelussa.

### 4. Koodi ja avaintiedosto

Päivitä repo kuten chatin päivityksessä, kun muutos on mainissa. Tarkista, että ajuri on mukana ja ajurin käyttäjä pääsee lukemaan koodin:

```
runuser -u aivo-agentti -- env PYTHONPATH=/opt/agentti-pohja /usr/bin/python3 -c "import aivo.agentti_ajuri; print('TUONTI-OK')"
```

Odotettu: `TUONTI-OK`.

Kirjoita oma OpenRouter-avain itse. Avain ei saa olla sama tiedosto kuin chatin avain, ja sen kannattaa olla oma avain, jotta agenttien kulut näkyvät OpenRouterissa erikseen. Ohje ei tulosta avainta.

```
install -m 0600 -o aivo-agentti -g aivo-agentti /dev/null /var/lib/aivo-agentit/openrouter-avain
nano /var/lib/aivo-agentit/openrouter-avain
stat -c '%a %U %s' /var/lib/aivo-agentit/openrouter-avain
```

Odotettu: `600 aivo-agentti` ja koko yli 0.

### 5. Ennakkotarkistus

Tarkistus ajetaan kertaluonteisena palveluna samoilla kulukansion liitoksilla kuin yksikössä, koska ajurin käyttäjä ei pääse oikeaan kansioon `/var/lib/claude-jono`.

```
systemd-run --wait --collect --unit=aivo-agentit-tarkistus -p User=aivo-agentti -p Group=aivo-agentti \
  -p "SupplementaryGroups=aivo-agentit aivo-kulut" -p WorkingDirectory=/opt/agentti-pohja \
  -p "Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/agentti-pohja" \
  -p "TemporaryFileSystem=/var/lib/claude-jono:mode=0777,size=1M" \
  -p "BindPaths=/var/lib/claude-jono/openrouter-kulut.jsonl /var/lib/claude-jono/openrouter-katto.jsonl /var/lib/claude-jono/openrouter-kulut.lock" \
  /usr/bin/python3 -m aivo.agentti_ajuri tarkista --roolit qwen/qwen3-235b-a22b-2507
journalctl -u aivo-agentit-tarkistus -n 20 --no-pager
```

`--pipe` ei toimi `pct enter` -istunnossa (paluukoodi 208/STDIN), joten tuloste luetaan lokista. Odotettu: `Main processes terminated with: code=exited/status=0`, ja lokin viimeinen rivi on `OK`. Tarkistus kattaa kansiot, kulukirjanpidon ja mallin. Jos riveillä on `VIRHE`, korjaa oikeudet kohdan 2 tai 3 mukaan. Palvelu tekee saman tarkistuksen käynnistyessään, ja virheen sattuessa se ei aja tehtäviä. Käytä samaa avointa mallia kuin chatissa; mallin pitää olla listalla `AVOIMET_MALLIT`.

### 6. Asennus

```
cp /opt/agentti-pohja/systemd/aivo-agentit.service.mall /etc/systemd/system/aivo-agentit.service
systemd-analyze verify /etc/systemd/system/aivo-agentit.service
systemctl daemon-reload
systemctl enable --now aivo-agentit
```

Jos repo ei ole polussa `/opt/agentti-pohja` tai Python ei ole polussa `/usr/bin/python3`, muuta rivit `WorkingDirectory`, `Environment` ja `ExecStart` ennen `daemon-reload`-komentoa. Malli vaihdetaan `ExecStart`-rivin valitsimella `--roolit`. Yhden mallin sijaan voi antaa kaikki kolme roolia: `--roolit suunnittelija:m1,tekija:m2,tarkastaja:m3`.

Odotettu: `systemd-analyze verify` ei tulosta yhtään riviä, jossa mainitaan `aivo-agentit.service`. Ajastinta ei ole, joten NEXT-aikaa ei tarkisteta: palvelu on käynnissä jatkuvasti.

### 7. Tarkistus

```
systemctl is-active aivo-agentit
journalctl -u aivo-agentit -n 20 --no-pager
systemctl show -p User,SupplementaryGroups,InaccessiblePaths,ReadWritePaths,TemporaryFileSystem,BindPaths aivo-agentit
PID=$(systemctl show -p MainPID --value aivo-agentit)
nsenter -t "$PID" -m ls -A /var/lib/claude-jono
cd /var/lib/claude-jono
for f in openrouter-kulut.jsonl openrouter-katto.jsonl openrouter-kulut.lock; do
  [ "$(stat -c %d:%i "$f")" = "$(nsenter -t "$PID" -m stat -c %d:%i "/var/lib/claude-jono/$f")" ] && echo "SAMA $f" || echo "ERI $f"
done
runuser -u aivo-agentti -- ls /var/lib/aivo-yksityinen/chat
runuser -u aivo-tyokalu -- ls /var/lib/aivo-agentit/tyo
systemd-analyze security aivo-agentit --no-pager | tail -n 1
```

Odotettu:
- `active`.
- Lokissa ei ole rivejä `kansiovirhe`, `asetusvirhe` eikä `status=226/NAMESPACE`.
- `User=aivo-agentti` ja `SupplementaryGroups=aivo-agentit aivo-kulut`. `ReadWritePaths=/var/lib/aivo-agentit` (ei kansiota `/var/lib/claude-jono`), `TemporaryFileSystem=/var/lib/claude-jono:...` ja `BindPaths=` kolmella kulutiedostolla.
- `nsenter`-listaus näyttää palvelun näkymästä vain tiedostot `openrouter-katto.jsonl`, `openrouter-kulut.jsonl` ja `openrouter-kulut.lock`.
- Kolme riviä `SAMA <tiedosto>`. Rivi `ERI` tarkoittaa, että tiedosto on korvattu palvelun käynnistymisen jälkeen: käynnistä palvelu uudelleen ja tarkista uudelleen.
- Kumpikin `ls` antaa `Permission denied`. Jos kansiota `/var/lib/aivo-yksityinen/chat` tai käyttäjää `aivo-tyokalu` ei ole, tulos on `No such file or directory` tai `unknown user`, mikä on myös hyväksyttävä.
- `systemd-analyze security` antaa pisteet ja arvion. Kirjaa ne raporttiin. Pienempi on parempi; arvio `OK` tai `MEDIUM` on odotettu.

Jos palvelu kaatuu tilaan `226/NAMESPACE`, kontti ei tue jotain eristysasetusta. Älä poista asetuksia itse, vaan kirjaa lokin rivi ja pyydä korjausta.

### 8. Käytöstesti

Pieni tehtävä oikealla mallilla. Kulu on muutamia senttejä, ja se kirjautuu yhteiseen päiväkattoon.

```
T=$(python3 -c "import secrets; print(secrets.token_hex(6))")
echo '{"versio": 1, "teksti": "Kirjoita tiedosto tervehdys.md, jossa on yksi rivi: hei."}' | tee /var/lib/aivo-agentit/jono/.$T.tmp >/dev/null
chmod 0640 /var/lib/aivo-agentit/jono/.$T.tmp
mv /var/lib/aivo-agentit/jono/.$T.tmp /var/lib/aivo-agentit/jono/$T.json
```

Odota noin minuutti ja tarkista:

```
journalctl -u aivo-agentit -n 5 --no-pager
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d['tila'], d['kutsuja'], d['kulu_eur'], [f['polku'] for f in d['tiedostot']])" /var/lib/aivo-agentit/tulokset/$T.json
stat -c '%a %G' /var/lib/aivo-agentit/tulokset/$T.json
tail -n 4 /var/lib/claude-jono/openrouter-kulut.jsonl
```

Odotettu:
- Lokissa on rivi `aivo-agentit: tunnus=<T> tila=valmis kutsuja=<n> ...` eikä tehtävän tekstiä.
- Tulosrivi on esimerkiksi `valmis 4 0.002 ['_suunnitelma.md', 'tervehdys.md']`. Tila `kesken` tarkoittaa, että tarkastaja ei hyväksynyt raporttia. Se on toimiva ajo mutta heikko vastaus.
- `640 aivo-agentit`.
- Kulukirjanpidossa on rivejä tehtävätunnisteilla `aivo-agentti:suunnittelija`, `aivo-agentti:tekija` ja `aivo-agentti:tarkastaja`.

Jos tulos on `virhe` ja syy mainitsee avaimen, tarkista kohta 4. Jos syy on `paivakatto tai kulukirjanpito`, tarkista kohta 3 tai päivän kulut.

### 9. Peruutus

```
systemctl disable --now aivo-agentit
rm /etc/systemd/system/aivo-agentit.service
systemctl daemon-reload
```

Palauta kulutiedostojen ryhmä ja oikeudet kohdassa 1 kirjatuiksi (esim. `chgrp <vanha ryhmä> <tiedosto>` ja `chmod 0644 <tiedosto>`). Kansioon `/var/lib/claude-jono` ja chatin käyttäjän ryhmiin ei ole koskettu, joten niitä ei palauteta.

```
stat -c '%A %U:%G %n' /var/lib/claude-jono /var/lib/claude-jono/openrouter-kulut.jsonl /var/lib/claude-jono/openrouter-katto.jsonl /var/lib/claude-jono/openrouter-kulut.lock
```

Odotettu: rivit vastaavat kohdassa 1 kirjattuja. Kokeile chatin yksityistä tilaa yhdellä viestillä: vastauksen pitää tulla kuten ennenkin.

Käyttäjä, ryhmät ja `/var/lib/aivo-agentit` jäävät paikalleen, koska niissä voi olla tuloksia. Niiden poisto on oma päätöksensä. Koko kontin voi palauttaa isännällä komennolla `pct rollback 122 ennen-aivo-agentit`, mutta se kumoaa myös kaikki muut snapshotin jälkeiset muutokset.

### 10. Chat-kytkentä: agenttitehtävä yksityisestä chatista (RAJATTU)

Tämä kohta on uusi datavirta yksityisestä chatista agentille. Se otetaan käyttöön vasta Tonyn sha-sidotulla hyväksynnällä. Ota ensin snapshot isännällä: `pct snapshot 122 ennen-aivo-agentit-chat`.

Miten se toimii:
- Yksityisen tilan vastauksessa voi olla lohko `[[AGENTTI]] tehtävänkuvaus [[/AGENTTI]]`. Chat poistaa lohkon tekstistä ja tallentaa ehdotuksen kansioon `/var/lib/aivo-yksityinen/agenttitehtavat/` (0700). Mitään ei käynnisty.
- Kortti näyttää tehtävän tekstin, arvioidun kulun (aiempien ajojen mediaani tai "ei arviota"), päiväkatosta jäljellä olevan summan sekä askel- ja aikarajan. Tekstiä voi muokata. Muokkaus antaa uuden sha:n.
- Käynnistä-painike lähettää vahvistuksen `kyllä <sha8>`. Chat kirjoittaa jonoon vain kortin tekstin ja keskustelun tunnisteen. Muistia, keskustelua, ydinohjeita tai liitteitä ei lähetetä. Jos päiväkatto on täynnä tai kirjanpitoa ei voi lukea, painike on pois käytöstä ja palvelin hylkää käynnistyksen.
- Tila päivittyy kortissa 10 sekunnin välein: jonossa, käynnissä ja lopuksi tulos. Raportti ja tiedostot näytetään tekstinä (`textContent`), vain siinä keskustelussa, jossa tehtävä syntyi. Raportti ei mene Aivon keskusteluhistoriaan. Historiaan tulee vain järjestelmämerkintä käynnistyksestä ja lopputilasta.
- Loki `agenttitehtavat/agenttiloki.jsonl` sisältää vain ajan, tunnuksen ja tilan (käynnistetty, hylätty, lopputila).
- Kulurajoja ei muuteta. Agentti käyttää samaa päiväkattoa ja samoja ajurin rajoja kuin ennen.

Asennus (Tony, kontti 122). Kirjoita ensin nykyiset arvot muistiin peruutusta varten:

```
systemctl cat aivo-chat | grep -E '^(User|SupplementaryGroups|ReadWritePaths|ExecStart|Environment)='
```

Lisää chatille ryhmä, kirjoitusoikeus vain jonoon ja asetus `AIVO_AGENTIT` täydennystiedostolla:

```
systemctl edit aivo-chat
```

Lisää muokkaukseen palveluosion alle:

```
[Service]
SupplementaryGroups=aivo-agentit
ReadWritePaths=/var/lib/aivo-agentit/jono
Environment=AIVO_AGENTIT=/var/lib/aivo-agentit
```

`SupplementaryGroups` lisää ryhmän aiempien rinnalle (esim. `aivo-tyokalu-socket` säilyy). Käynnistä uudelleen ja tarkista:

```
systemctl restart aivo-chat
systemctl show aivo-chat -p SupplementaryGroups -p ReadWritePaths --no-pager
systemctl is-active aivo-chat
PID=$(systemctl show -p MainPID --value aivo-chat)
grep -E '^Groups:' /proc/$PID/status
getent group aivo-agentit
```

Odotettu: `SupplementaryGroups` sisältää `aivo-agentit`, `ReadWritePaths` sisältää `/var/lib/aivo-agentit/jono`, ja palvelu on `active`. `Groups:`-rivillä on ryhmän `aivo-agentit` numero (sama kuin `getent`-rivillä). Chatin käyttäjä ei saa kirjoitusoikeutta työkansioihin eikä tuloksiin: `tyo/` ja `kasitellyt/` ovat 0700, ja `tulokset/` on ryhmälle vain luettava.

Käytöstesti selaimessa (yksityinen tila):
1. Kirjoita: "Ehdota agenttitehtävä, joka kirjoittaa tiedoston tervehdys.md, jossa on yksi rivi: hei." Vastauksen alle tulee kortti, jossa näkyvät teksti, arvioitu kulu ja päiväkatosta jäljellä oleva summa.
2. Tarkista, että jono on yhä tyhjä: `ls -A /var/lib/aivo-agentit/jono` ei tulosta mitään.
3. Paina Käynnistä ja vahvista. Kortti näyttää tilan `jonossa` ja sitten `käynnissä`. Noin minuutin päästä näkyy raportti ja painike `Näytä tervehdys.md`.
4. Tarkista loki: `cat /var/lib/aivo-yksityinen/agenttitehtavat/agenttiloki.jsonl`. Odotettu: kaksi riviä, joissa on vain `aika`, `tunnus` ja `tila` (`käynnistetty` ja `valmis` tai `kesken`).

Peruutus: `systemctl revert aivo-chat` ja `systemctl restart aivo-chat`. Ehdotukset ja loki jäävät kansioon `/var/lib/aivo-yksityinen/agenttitehtavat/`. Niiden poisto on oma päätöksensä.

## Testit

`python3 -m unittest aivo.test_agentti_ajuri` (kansioiden oikeudet ja polut, jonosta luku, yksi tehtävä kerrallaan, aikaraja, tulostiedoston muoto, tiedostokopio, yksikkömalli ja rakennetesti) ja `python3 -m unittest aivo.test_agenttitehtava` (ehdotus ei käynnisty ilman painallusta, väärennetty lohko tai tunnus hylätään, katon täyttyminen näkyy kortissa, tulos näytetään turvallisesti ja vain aloittaneessa keskustelussa). Testit eivät käytä verkkoa.
