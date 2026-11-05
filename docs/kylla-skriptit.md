# Kyllä-skriptit: isäntä ajaa Tonyn hyväksymän skriptin

Tony hyväksyy isäntäkomennot yhdellä viestillä komentobotissa sen sijaan, että kopioisi ne PowerShelliin.
Koodi: `isanta/kylla.py` (vain stdlib, ajetaan Proxmox-isännällä rootina). Testit: `test/test_kylla.py`.

## Kulku

1. Pilvi-Claude tai Aivo kirjoittaa skriptin docs-repoon: `claude-jono/isanta-kasin/ajettavat/NN-nimi.sh`.
   Jonoagentit eivät voi kirjoittaa sinne: ajurin `vapaa_docs_polku` hylkää koko `isanta-kasin/`-polun (testi `TestDocsPolku`).
2. Ajastin (5 min) hakee docs-repon (`git pull --ff-only`) ja lukee hakemiston `ajettavat/`.
   Uudesta tai muuttuneesta skriptistä komentobotti lähettää: `Aja NN: <kuvaus> (kirjoittaja pilvi-claude, sha256 1a2b3c4d)? Vastaa: kyllä NN`.
   Samalla skriptistä otetaan rootin oma kopio (`/var/lib/kylla/tila/odottavat/NN.sh`).
3. Tony vastaa `kyllä NN` (tai `kylla NN`, tai varmuuden vuoksi `kyllä NN 1a2b3c4d`). Ennen ajoa tarkistetaan:
   - viestin `chat.id` ja `from.id` ovat Tonyn chat-id (muut ohitetaan hiljaa)
   - vastaus on kysymystä uudempi (vanha vastaus ei aja uutta versiota)
   - docs-repon skriptin sha256 on sama kuin kysymyksessä näytetty, ja rootin kopion sha256 on sama
   - jos sha on annettu, sen pitää täsmätä
   Muuttunut skripti vaatii uuden kyllän. Sama sisältö ajetaan vain kerran.
4. Ajo: `/bin/bash <kopio>`, oma prosessiryhmä, aikaraja 10 min (koko ryhmä tapetaan), yksi ajo kerrallaan (lukko `/run/kylla.lock`).
5. Tuloste suodatetaan (yksityiset ja julkiset IP-osoitteet, `.ts.net`-nimet, Telegram-, GitHub- ja OpenAI-muotoiset tunnukset,
   avaimet, `password=`/`token:`-arvot, pitkät satunnaiset merkkijonot) ja kirjoitetaan `/var/lib/kylla/tulosteet/NN.txt`.
   Botti vastaa yhdellä rivillä: `NN: onnistui, paluukoodi 0.` / `NN: epäonnistui, paluukoodi 3.` / `NN: aikaraja (10 min) ylittyi`.
6. Loki: `/var/log/kylla.log`, yksi JSON-rivi per tapahtuma (kysytty, hylätty, ajo_alkaa, ajo_valmis). Ei tulostetta eikä tunnuksia.

## Skriptin muoto

```bash
#!/bin/bash
# kirjoittaja: pilvi-claude
# kuvaus: Asentaa X:n uuden palvelutiedoston (yksi rivi, näkyy kysymyksessä)
# muuttaa: /etc/systemd/system/x.service
# peruminen: cp /root/x.service.bak /etc/systemd/system/x.service && systemctl daemon-reload
# odotettu tuloste: viimeinen rivi "x ok"
set -euo pipefail
...
```

Pakolliset: nimi `NN-nimi.sh` (NN = 01–99), `# kirjoittaja:` (`pilvi-claude` tai `aivo`) ja `# kuvaus:` 20 ensimmäisellä rivillä,
enintään 64 kt, tavallinen tiedosto. Hylätty skripti ei aja mitään; Tony saa hylkäyksen syyn.
Jos skripti muuttaa oikeuksia tai palveluja, sen ensimmäinen toimi on snapshot (kompassin oikeusmuutosten sääntö).

Rajoitus: `kirjoittaja`-rivi on skriptin oma ilmoitus, ei todennus. Varsinainen raja on se, kuka voi kirjoittaa
docs-repon `ajettavat/`-hakemistoon, ja Tonyn kyllä, joka on sidottu näytettyyn sha:han.
Samoin tarkistus "skripti ei mainitse ajajan polkuja" on lisäsuoja eikä pitävä: tiedostonimen voi kirjoittaa paloina.
Tony näkee kysymyksessä vain kuvauksen. Siksi ennen kyllää kannattaa avata skripti GitHubista ja verrata sha:ta.

## Palvelujen uudelleenkäynnistys ilman kyllää (5b)

`/etc/kylla/palvelut.txt` (root, 0644, Tony kirjoittaa ja hyväksyy kerran), rivi per palvelu:

    115 claude-aamukooste.service
    115 github-api.service

- Vain listan palvelut ja vain, kun `systemctl is-failed` sanoo palvelun kaatuneeksi. Kauppatiimin palvelut (nimessä `kauppa`) hylätään aina.
- Jokainen uudelleenkäynnistys kirjataan tiedostoon `/var/lib/kylla/palvelut/uudelleenkaynnistykset.jsonl` (aamukoostetta varten).
- Kolmas käynnistys samana vuorokautena (UTC) pysäyttää palvelun automaattisen käynnistyksen, lähettää hälytyksen ja kirjaa
  juurisyyn selvityksen tiedostoon `juurisyyt.jsonl`. Pysäytys pysyy, kunnes Tony poistaa rivin tiedostosta `palvelut.json` (`pysaytetyt`).
- Ilman listatiedostoa mitään ei käynnistetä.

## Käyttöönotto (Tony, isännällä rootina, noin 15 min)

Ennen aloitusta: yhdistä tämä PR ja komentobotin PR (#196 tai sen korvaaja). Ota snapshot ennen oikeusmuutosta:
tämä lisää isännälle uuden root-palvelun.

1. Komentobotti: luo BotFatherilla **erillinen** botti (eri tunnus kuin ilmoitusbotilla ja `telegram_luku`:lla,
   koska kaksi getUpdates-lukijaa samalla tunnuksella vie toistensa viestit).
2. Tunnukset (kirjoita itse, älä liitä chattiin):

        install -d -m 700 /etc/kylla /usr/local/lib/kylla /var/lib/kylla
        nano /etc/kylla/komentobotti.env      # KOMENTOBOTTI_TOKEN=...
        nano /etc/kylla/chat-id               # Tonyn chat-id, pelkkä numero
        chmod 600 /etc/kylla/komentobotti.env /etc/kylla/chat-id

3. Docs-repon lukukopio isännälle (vain luku -avain, esim. GitHubin deploy key ilman kirjoitusoikeutta):

        git clone <docs-repon osoite> /var/lib/kylla/docs

4. Ajaja ja ajastimet (lähde CT115:n kloonista, kuten `asenna.sh`):

        pct pull 115 /opt/agentti-pohja/isanta/kylla.py /usr/local/lib/kylla/kylla.py
        chmod 700 /usr/local/lib/kylla/kylla.py
        for n in kylla-kierros kylla-palvelut; do
          for p in service timer; do pct pull 115 /opt/agentti-pohja/isanta/kylla-systemd/$n.$p.mall /etc/systemd/system/$n.$p; done
        done
        systemctl daemon-reload

5. Kuiva-ajo (ei aja eikä lähetä mitään):

        python3 -I -B /usr/local/lib/kylla/kylla.py tarkista --ajettavat /var/lib/kylla/docs/claude-jono/isanta-kasin/ajettavat

   Odotettu: `ok NN ...` -rivit, tyhjä tuloste (tyhjä hakemisto) tai `ajettavat-hakemistoa ei voi lukea` (hakemistoa ei vielä ole).
6. Ensimmäinen kierros käsin ja tarkistus:

        systemctl start kylla-kierros.service && journalctl -u kylla-kierros -n 20 --no-pager && tail -3 /var/log/kylla.log

   Testiksi pilvi-Claude voi kirjoittaa skriptin `01-testi.sh`, joka ajaa pelkän `uptime`-komennon.
7. Käyttöön: `systemctl enable --now kylla-kierros.timer`. Palvelulista (5b) vasta kun `/etc/kylla/palvelut.txt` on kirjoitettu:
   `systemctl enable --now kylla-palvelut.timer`.

Peruminen: `systemctl disable --now kylla-kierros.timer kylla-palvelut.timer`. Tila ja lokit jäävät talteen.

## Tonyn päätettävät (ei tehty tässä PR:ssä)

- `agentti/pr_kutsu.py`: `AGENTILTA_KIELLETYT_RE`:hen pitää lisätä `isanta/kylla\.py` ja `isanta/kylla-systemd/`, jotta jonoagentti
  ei voi muuttaa ajajaa PR:llä. Tiedosto on itse agentilta kielletty, joten muutos on Tonyn tehtävä.
- Tulosteiden vienti docs-repoon (`isanta-kasin/tulosteet/NN.txt`): isännällä on vain lukuoikeus. Vaihtoehdot: (a) CT115:n ajuri
  hakee tulosteen snapshot-kutsun tapaan pakotetulla SSH-komennolla, (b) isännälle kirjoitusoikeus vain tulosteet/-polkuun.
- Aamukooste lukee CT115:llä; `uudelleenkaynnistykset.jsonl` ja `juurisyyt.jsonl` ovat isännällä. Sama siirtotie kuin edellä.
- `juurisyyt.jsonl`-rivi pitää viedä jonoon tehtäväksi (A439); nyt kirjataan vain isännälle ja hälytetään.
