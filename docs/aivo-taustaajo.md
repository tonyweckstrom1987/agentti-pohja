# Aivon taustaajo (yksityinen tila)

Taustaajo tuo hitaan ajattelijamallin vastauksen keskusteluun myöhemmin. Keskustelu ei odota ajattelijaa. Koodi on tiedostoissa `aivo/taustaajo.py` (ajuri) ja `aivo/syvennys.py` (chatin Syvennä-painike). Tämä muutos ei asenna mitään: ylläpitäjä asentaa yksiköt osion Asennus kontissa 122 mukaan.

## Kulku

1. Tony painaa yksityisen tilan vastauksen alla painiketta **Syvennä**. Chat luo ehdotuksen, jossa on Tonyn kysymys palvelimen tallentamasta historiasta. Tony voi muokata tekstiä ja käynnistää työn. Vasta käynnistys kirjoittaa työn jonoon funktiolla `taustaajo.kirjoita_tyo(jono, keskustelu, tehtava)`. Työssä on keskustelun tunnus, tehtävä ja tehtävän sha-256. Chat kertoo, että syvempi analyysi on tulossa (`taustaajo.TULOSSA_TEKSTI`). Katso osio Syvennä-painike.
2. Ajastin käynnistää ajurin (`python3 -m aivo.taustaajo`). Ajuri ottaa työt vanhin ensin. Ajattelija vastaa englanniksi, ja keskustelija muotoilee vastauksen suomeksi.
3. Valmis vastaus lisätään keskustelun jsonl-tiedostoon rivinä, jonka rooli on `aivo` ja jossa on kenttä `tausta`. Teksti alkaa merkinnällä `[Syvempi analyysi valmis]`.
4. Chat-sivu kysyy reittiä `GET /api/tausta?keskustelu=ID&alkaen=N` 30 sekunnin välein, kun yksityinen keskustelu on auki ja välilehti näkyvissä. Uudet taustaviestit näkyvät keskustelussa.
5. Jos työ epäonnistuu, keskusteluun tulee merkintä `[Syvempi analyysi epäonnistui]` ja syy: aikaraja, päiväraja, kulukatto, mallivirhe, keskeytynyt ajo tai virheellinen työ.

Työtiedostoja ei poisteta. Ne siirretään jonon alikansioihin `kesken/`, `valmiit/` ja `epaonnistuneet/`.
Jos siirto ei onnistu (esimerkiksi levy on täynnä), lokiin tulee rivi `siirto epaonnistui: <tunnus> (<virheen tyyppi>, <kansio>)`, ja ajo jatkuu seuraavaan työhön. Jos työtä ei saada siirrettyä `kesken/`-kansioon, sitä ei ajeta eikä päiväpaikkaa varata. Työ jää jonoon, ja seuraava ajo yrittää sitä uudelleen. Epäonnistumismerkintä lisätään keskusteluun vasta, kun työ on siirretty `epaonnistuneet/`-kansioon. Näin sama merkintä ei toistu keskustelussa joka ajossa, jos siirto epäonnistuu pysyvästi.

## Kansiot

| Polku | Oikeudet | Sisältö |
|---|---|---|
| `/var/lib/aivo-yksityinen/tyot/` | 0700, palvelun käyttäjä | odottavat työt `<tunnus>.json` (0600) |
| `.../tyot/kesken/`, `valmiit/`, `epaonnistuneet/` | 0700 | työn tila |
| `.../tyot/tila.json` | 0600 | päivälaskuri (Europe/Helsinki) |
| `.../tyot/taustaajo.log` | 0600 | loki: vain tunnus, tulos ja virheen tyyppi |

Ajuri kieltäytyy toimimasta, jos jono tai keskustelukansio on linkki, kuuluu toiselle käyttäjälle tai on laajemmin kuin 0700. Työkalutilan käyttäjä (`aivo-tyokalu`) ei siksi pääse jonoon, eikä työkalutilan koodi tunne jonon polkua.

## Rajat

- Enintään `AIVO_TAUSTA_PAIVARAJA` työtä vuorokaudessa (oletus 5). Asetus ei voi ylittää koodin ylärajaa 20. Työ varataan laskuriin ennen ensimmäistä mallikutsua.
- Työn kokonaisaikaraja `AIVO_TAUSTA_AIKARAJA_S` (oletus 600 s, yläraja 900 s). Kukin kutsu saa enintään jäljellä olevan ajan. Yksittäisen kutsun yläraja on `yksityinen_or.TAUSTA_AIKARAJA_S` (600 s). Chatin oma aikaraja (`yksityinen_or.CHAT_AIKARAJA_S`, 360 s) on erillinen.
- Enintään kaksi mallikutsua työtä kohden.
- Kutsut kulkevat vain funktion `yksityinen_or.kysy_taustatyo` kautta. Malli tarkistetaan funktiolla `avoin_malli()`, ZDR ja `data_collection=deny` pakotetaan, ja yhteinen päiväkatto (10 €/vrk, A1005) tarkistetaan ennen kutsua. Kulut kirjataan tehtävätunnuksella `aivo-yksityinen-tausta`. Vastauksen enimmäispituus on sama kuin OpenRouter-chatissa (`yksityinen_or.MAX_TOKENIA`, 8 000 tokenia, A1095).
- Taustavastaus ei voi tehdä ehdotuksia eikä päätöksiä, koska kaikki `[[ ]]`-merkinnät poistetaan.
- Uusia verkkokohteita ei ole.

## Roolit ja mallit

Ajuri lukee samat asetukset kuin chat:

- `AIVO_OPENROUTER_MALLIT` (tai `AIVO_OPENROUTER_MALLI`): sallitut avoimet mallit.
- `AIVO_YKSITYINEN_ROOLIT`: ajuri lukee vain roolit `keskustelija` ja `ajattelija`. Muut roolit se ohittaa. Puuttuva rooli käyttää listan ensimmäistä mallia. Roolin mallin pitää olla sallittujen listalla.
- `AIVO_OPENROUTER_AVAIN`: sama avaintiedosto kuin chatilla (0600, palvelun käyttäjän oma).
- `AIVO_YKSITYINEN_PAATTELY_MAX`: ajattelevan mallin päättelyn yläraja tokeneina (oletus 2 000, sallittu 0–6 000, 0 = reasoning-parametria ei lähetetä). Sama asetus kuin chatilla. Virheellinen arvo lopettaa ajon virheeseen. Taustaajo ei tee uusintaa, jos vastaus katkeaa päättelyyn. Katso `aivo-yksityinen-openrouter.md`, osio ”Päättelyn yläraja ja uusinta”.

Ajuri toimii vain OpenRouter-taustapäällä. Jos `AIVO_YKSITYINEN_TAUSTAPAA=loopback`, se päättyy virheeseen.

## Syvennä-painike (chat)

- Painike näkyy yksityisen tilan vastauksen alla, kun chatilla on asetus `AIVO_TAUSTA_JONO` (tai valitsin `--tausta-jono`). Oletus on tyhjä, jolloin painiketta ja reittejä `/api/syvenna/*` ei ole. Painike vaatii OpenRouter-taustapään, koska ajuri ei aja töitä paikallisella mallilla.
- Painallus (`POST /api/syvenna/uusi`) luo vain ehdotuksen kansioon `/var/lib/aivo-yksityinen/syvennykset/` (0700). Kortissa näkyvät teksti, ajattelijamalli, tunnus, sha ja päivän käyttö. Tony voi muokata tekstiä, jolloin sha vaihtuu.
- Työ kirjoitetaan jonoon vain reitillä `POST /api/syvenna/kaynnista`, jonka vahvistus on täsmälleen `kyllä <sha8>`. Väärä tai vanha sha hylätään. Ehdotus siirretään ensin kansioon `kaynnistetyt/`, joten sama ehdotus ei voi kirjoittaa kahta työtä. Keskusteluun tulee merkintä `[Järjestelmä: Tony käynnisti syventämisen …]`.
- Painike on pois käytöstä, ja syy näkyy sen vieressä, kun
  - päiväraja on täynnä: tänään ajetut (`tila.json`) + jonossa odottavat + `kesken/`-kansion työt ≥ `AIVO_TAUSTA_PAIVARAJA`
  - yhteinen päiväkatto (10 €/vrk) on täynnä tai kulukirjanpitoa ei voi lukea
  - jonon tilaa ei voi lukea.

  Este tarkistetaan palvelimella uudelleen ennen jonoon kirjoitusta. Ajuri tarkistaa päivärajan ja katon silti itse.
  Laskurin raja on sama kuin ajurilla: ajuri kirjoittaa käyttämänsä päivärajan jonon `tila.json`-tiedostoon (kenttä `paivaraja`), ja chat lukee sen sieltä. Jos ajuri ei ole vielä ajanut, chat käyttää omaa `AIVO_TAUSTA_PAIVARAJA`-asetustaan.
- Malli voi ehdottaa syventämistä merkinnällä `[[SYVENNA]]` vastauksen lopussa. Chat poistaa merkinnän, ja painikkeen tekstinä on "Syvennä (Aivo ehdottaa)". Merkintä ei luo ehdotusta eikä työtä.
- Ajattelijamalli valitaan asetuksella `AIVO_YKSITYINEN_ROOLIT` (rooli `ajattelija`), kuten osiossa Roolit ja mallit. Chat ja ajuri lukevat saman asetuksen. Mallin pitää olla listalla `AIVO_OPENROUTER_MALLIT`, jonka mallit tarkistetaan listaa `AVOIMET_MALLIT` vasten. Chat näyttää mallin kortissa.
- Rajat ovat ennallaan (A1076): 5 työtä vuorokaudessa, työn aikaraja 10 min, kutsun aikaraja 10 min ja yhteinen päiväkatto 10 €/vrk.

## Yksiköt

Käyttäjän pitää olla sama kuin `aivo-chat.service`-palvelun `User=`, koska jono, keskustelut ja avain tarkistetaan palvelun käyttäjän omiksi. Alla oleva yksikkö käyttää esimerkkinä käyttäjää `aivo`. Asennusohje kirjoittaa oikean käyttäjän muuttujasta `$U`.

**Palvelu** `/etc/systemd/system/aivo-taustaajo.service`:

```
[Unit]
Description=Aivon taustaajo (yksityinen tila, OpenRouter ZDR)

[Service]
Type=oneshot
User=aivo
WorkingDirectory=/opt/agentti-pohja
Environment=PYTHONPATH=/opt/agentti-pohja
Environment=AIVO_YKSITYINEN_TAUSTAPAA=openrouter
Environment=AIVO_OPENROUTER_MALLIT=qwen/qwen3-235b-a22b-2507
Environment=AIVO_YKSITYINEN_ROOLIT=keskustelija:qwen/qwen3-235b-a22b-2507,ajattelija:qwen/qwen3-235b-a22b-2507
Environment=AIVO_TAUSTA_PAIVARAJA=5
Environment=AIVO_TAUSTA_AIKARAJA_S=600
ExecStart=/usr/bin/python3 -m aivo.taustaajo
TimeoutStartSec=310min
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/aivo-yksityinen /var/lib/claude-jono
PrivateTmp=true
```

`TimeoutStartSec` kattaa pisimmän mahdollisen ajon: koodin ylärajat `PAIVARAJA_MAX` (20 työtä) × `AIKARAJA_MAX_S` (15 min) = 300 min ja 10 minuutin pelivara. Näin systemd ei katkaise maksettua kutsua kesken, vaikka asetuksia nostettaisiin ylärajaan asti. Arvo ei salli enempää töitä tai kuluja: päiväraja, työn aikaraja ja kulukatto ovat koodissa. Testi `YksikkoAikarajaTesti` tarkistaa, että arvo riittää.

Mallit ovat samat kuin `aivo-chat.service`-palvelussa. `/var/lib/claude-jono` on mukana, koska kulukirjanpito ja päiväkatto ovat siellä. Ilman sitä jokainen kutsu estyy (fail closed). Kaksi ajoa yhtä aikaa on turvallista: toinen huomaa lukon (`taustaajo.lukko`) ja päättyy. Yksikössä ei ole tilapäistä tiedostojärjestelmää eikä setgid-oikeuksia, joten se ei tarvitse sticky-bittiä eikä setgid-chmodia, eikä `RestrictSUIDSGID` estä mitään.

**Ajastin** `/etc/systemd/system/aivo-taustaajo.timer`:

```
[Unit]
Description=Aivon taustaajo minuutin valein

[Timer]
OnActiveSec=1min
OnBootSec=2min
OnUnitInactiveSec=1min

[Install]
WantedBy=timers.target
```

`OnActiveSec` antaa ajastimelle seuraavan ajan heti käyttöönotossa. Pelkkä `OnUnitInactiveSec` ei laukea ennen kuin palvelu on ajettu kerran, ja `OnBootSec` on jo ohitettu, kun ajastin otetaan käyttöön kesken käytön. Sen jälkeen ajo alkaa minuutti edellisen ajon päättymisestä.

## Asennus kontissa 122

Komennot ajetaan kontissa 122 pääkäyttäjänä (root), ellei toisin mainita. Toisen käyttäjän komennot ajetaan muodossa `runuser -u <käyttäjä> -- <komento>`. Tarkistukset luetaan suoraan komentoriviltä tai lokista. `systemd-run --pipe` ei toimi `pct enter` -istunnossa (virheloki 25.11.), joten sitä ei käytetä. Jokaisen kohdan jälkeen on Odotettu-rivi. Jos tulos poikkeaa, pysähdy äläkä jatka.

### 0. Snapshot ja koodi

Isännällä:

```
pct snapshot 122 ennen-aivo-syvenna
pct listsnapshot 122
```

Kontissa (repo päivitetty mainiin, johon tämä muutos on yhdistetty):

```
git -C /opt/agentti-pohja log -1 --oneline
grep -c "def paivan_kaytto" /opt/agentti-pohja/aivo/taustaajo.py
```

Odotettu: listassa on `ennen-aivo-syvenna`, ja `grep` tulostaa `1`.

### 1. Chatin käyttäjä ja nykyiset asetukset

```
U=$(systemctl show -p User --value aivo-chat); echo "CHAT=$U"
systemctl cat aivo-chat | grep -E '^(User|ReadWritePaths|ExecStart|Environment)='
```

Kirjoita rivit muistiin peruutusta varten. Odotettu: rivi `CHAT=aivo` tai `CHAT=aivo-yksityinen` (ei tyhjä). `ReadWritePaths` sisältää kansion `/var/lib/aivo-yksityinen` (tai sen alikansiot) ja kansion `/var/lib/claude-jono`. Yksityinen tila käyttää OpenRouteria (`--yksityinen-taustapaa openrouter` tai `AIVO_YKSITYINEN_TAUSTAPAA=openrouter`). Käytä alla samaa mallilistaa kuin chat. Muuttuja `U` on voimassa vain tässä istunnossa: aja kohdat 1–6 samassa istunnossa.

### 2. Jono

```
install -d -m 0700 -o "$U" -g "$U" /var/lib/aivo-yksityinen/tyot
stat -c '%a %U' /var/lib/aivo-yksityinen/tyot
```

Odotettu: `700 <chatin käyttäjä>`. Kansiolle ei anneta setgid- eikä sticky-bittiä. Ajuri ja chat kieltäytyvät käyttämästä jonoa, jonka oikeudet ovat laajemmat kuin 0700.

### 3. Kuiva-ajo

```
runuser -u "$U" -- env PYTHONPATH=/opt/agentti-pohja AIVO_OPENROUTER_MALLIT=qwen/qwen3-235b-a22b-2507 python3 -m aivo.taustaajo --kuiva; echo "paluu=$?"
```

Odotettu: ei `ajettaisiin`-rivejä (jono on tyhjä) ja `paluu=0`. Kuiva-ajo ei tee mallikutsuja. Jos tulos on `virhe: TaustaVirhe`, tarkista kohdan 2 oikeudet ja keskustelukansion `/var/lib/aivo-yksityinen/chat` omistaja (`stat -c '%a %U'`, odotettu `700 <chatin käyttäjä>`).

### 4. Yksiköt

Kopioi palvelu ja ajastin osiosta Yksiköt tiedostoihin `/etc/systemd/system/aivo-taustaajo.service` ja `/etc/systemd/system/aivo-taustaajo.timer` tekstieditorilla (esim. `nano`). Vaihda palveluun rivi `User=aivo` chatin käyttäjäksi ja mallit samoiksi kuin chatissa. Tarkista:

```
grep -E '^(User|Environment=AIVO_YKSITYINEN_ROOLIT)=' /etc/systemd/system/aivo-taustaajo.service
systemd-analyze verify /etc/systemd/system/aivo-taustaajo.service /etc/systemd/system/aivo-taustaajo.timer
```

Odotettu: rivi `User=` on sama kuin kohdan 1 `CHAT=`-arvo, ja roolirivillä on sama ajattelijamalli kuin chatissa. `systemd-analyze verify` ei tulosta yhtään riviä, jossa mainitaan `aivo-taustaajo`.

### 5. Ajastin käyntiin ja NEXT-aika

```
systemctl daemon-reload
systemctl enable --now aivo-taustaajo.timer
systemctl list-timers aivo-taustaajo.timer --all --no-pager
```

Odotettu: sarakkeessa `NEXT` on kellonaika (ei `n/a`), ja `LEFT` on alle kaksi minuuttia. Odota kaksi minuuttia ja tarkista ensimmäinen ajo:

```
systemctl show aivo-taustaajo.service -p Result -p ExecMainStatus --no-pager
journalctl -u aivo-taustaajo --since "5 min ago" --no-pager
systemctl list-timers aivo-taustaajo.timer --all --no-pager
```

Odotettu: `Result=success` ja `ExecMainStatus=0`. Lokissa ei ole rivejä, jotka alkavat `virhe:`. `NEXT` on taas kellonaika noin minuutin päässä.

### 6. Chat: Syvennä-painike käyttöön

```
systemctl edit aivo-chat
```

Lisää muokkaukseen palveluosion alle. Päiväraja ja roolit ovat samat kuin ajurilla:

```
[Service]
Environment=AIVO_TAUSTA_JONO=/var/lib/aivo-yksityinen/tyot
Environment=AIVO_TAUSTA_PAIVARAJA=5
Environment=AIVO_YKSITYINEN_ROOLIT=keskustelija:qwen/qwen3-235b-a22b-2507,ajattelija:qwen/qwen3-235b-a22b-2507
```

Jos chatilla on jo `AIVO_YKSITYINEN_ROOLIT` tai `--yksityinen-roolit`, älä lisää rooliriviä, vaan käytä samaa arvoa ajurissa. Käynnistä uudelleen ja tarkista:

```
systemctl restart aivo-chat
systemctl is-active aivo-chat
systemctl show aivo-chat -p Environment --no-pager | tr ' ' '\n' | grep -E '^AIVO_(TAUSTA|YKSITYINEN_ROOLIT)'
ls -ld /var/lib/aivo-yksityinen/syvennykset
```

Odotettu: `active`, rivit `AIVO_TAUSTA_JONO=/var/lib/aivo-yksityinen/tyot` ja `AIVO_TAUSTA_PAIVARAJA=5`, ja kansio `syvennykset` on `drwx------` chatin käyttäjälle. Jos palvelu ei käynnisty, `journalctl -u aivo-chat -n 20 --no-pager` näyttää syyn (esim. `syventäminen vaatii yksityisen tilan ja OpenRouter-taustapään`).

### 7. Käytöstesti selaimessa (yksityinen tila)

1. Kysy jotain, mihin kannattaa syvempi vastaus. Vastauksen alle tulee painike **Syvennä**.
2. Paina sitä. Kortissa näkyvät kysymys, ajattelijamalli ja päivän käyttö (esim. `0/5`). Tarkista, että jono on yhä tyhjä: `ls /var/lib/aivo-yksityinen/tyot/*.json` → `No such file or directory`.
3. Paina **Käynnistä syventäminen** ja vahvista. Odotettu: teksti "Syvempi analyysi on tulossa…", ja `ls /var/lib/aivo-yksityinen/tyot/*.json` näyttää yhden tiedoston (tai ei mitään, jos ajuri ehti jo ottaa sen).
4. Odota enintään 10 minuuttia. Odotettu: keskusteluun tulee kerran viesti, joka alkaa `[Syvempi analyysi valmis]`. Loki: `journalctl -u aivo-taustaajo --since "15 min ago" --no-pager | grep -E 'valmis|epaonnistui'` → rivi `valmis: <tunnus>, kutsuja 2`.
5. Kulut: `python3 -m aivo.openrouter_kulut viikko` (hakemistossa `/opt/agentti-pohja`) näyttää tehtävän `aivo-yksityinen-tausta`.

### Peruutus

```
systemctl disable --now aivo-taustaajo.timer
systemctl revert aivo-chat
systemctl restart aivo-chat
```

Odotettu: `systemctl list-timers aivo-taustaajo.timer --all --no-pager` ei näytä ajastinta, ja vastausten alla ei ole Syvennä-painiketta. Jono, ehdotukset ja yksikkötiedostot jäävät paikalleen. Niiden poisto on oma päätöksensä. Koko kontin voi palauttaa isännällä komennolla `pct rollback 122 ennen-aivo-syvenna`, mutta se kumoaa myös muut snapshotin jälkeiset muutokset.
