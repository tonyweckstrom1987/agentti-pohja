# Aivo: lukuoikeus Google Driveen (A694)

Tonyn päätös 5.11.2026: Aivo saa lukea koko Driven. Täydet oikeudet vasta, kun hyöty on osoitettu (välimalli A530). Tämä ohje on Tonille. Agentti tai pilvi-Claude ei luo tunnusta eikä koske salaisuuksiin.

## Mitä tämä tekee

- `aivo/drive.py` käyttää OAuth-oikeutta **drive.readonly**. Muuta oikeutta (`drive`, `drive.file`) koodi ei hyväksy: tunnustiedosto hylätään, jos scope on jokin muu, eikä kirjautuminen tallenna tunnusta, jos Google myöntäisi muuta kuin tasan drive.readonly.
- Tunnus on vain isännällä: `/etc/aivo/drive-tunnus.json`, tila 600, omistaja `aivo`. Koodi kieltäytyy lukemasta sitä, jos muut voivat lukea sen. Tunnusta ei ole repoissa, lokeissa eikä raporteissa.
- Aivo hakee Drivesta ensin vain metatiedot (nimi, tyyppi, muutospäivä) ja lukee sisällön yksi tiedosto kerrallaan, kun se tiivistää. Koko Drivesta ei tehdä kopioita.
- Tiivistelmä kirjoitetaan Aivon muistiin (`/var/lib/aivo/kirjasto/drive/`). Jos kirjasto on Gitea-repo, se commitoidaan ja pushataan. GitHubiin osoittavaa etärepoa koodi ei hyväksy. Tiivistelmässä on aina lähde: tiedostonimi ja muutospäivä.
- Driven sisältö on dataa, ei ohjeita. Tiivistyskehote kieltää noudattamasta sisällön käskyjä, ja sisällön erotinmerkki poistetaan tekstistä.
- Data menee vain Claudelle suoraan (`claude -p` isännällä, A329). Koodi ei käytä LiteLLM:ää eikä OpenRouteria; testi vartioi tätä.

## Komennot (käyttäjänä `aivo`, isännällä)

```
python3 -m aivo.drive listaa -n 10       # uusimmat tiedostot (ei sisältöä)
python3 -m aivo.drive tiivista <id>      # tiivistelmä Aivon muistiin
python3 -m aivo.drive kirjoitustesti     # odotettu: "kirjoitus hylatty (HTTP 403)"
python3 -m aivo.drive vaikutus paatos "<lahde>" "<mitä muuttui>"
python3 -m aivo.drive vaikutus kysymys "<lahde>" "<mitä muuttui>"
python3 -m aivo.drive vaikutus yhteensa  # mittari
```

## Mittari

Täysien oikeuksien perusteeksi lasketaan, montako kertaa Driven tieto muutti Aivon päätöstä tai kysymystä. Aivo kirjaa tapauksen komennolla `vaikutus` ja mainitsee lähteen (tiedostonimi, päivä). Tiedosto: `/var/lib/aivo/drive/vaikutus.jsonl` (600). Aamukoosteessa tai kuukausikatsauksessa luku esitetään Tonille; päätös oikeuksien laajentamisesta on aina Tonin.

## Tunnuksen luonti, yksi askel kerrallaan

Tehdään omalla Google-tililläsi selaimessa. Google muuttaa käyttöliittymän nimiä välillä, joten valikkojen sanat voivat poiketa vähän. Linkit vievät oikeaan paikkaan.

1. **Projekti.** Avaa https://console.cloud.google.com/projectcreate. Nimeksi `aivo-drive`, paina Luo (Create). Odotettu tulos: sivun yläreunan projektivalitsimessa lukee `aivo-drive`. Jos ei lue, valitse se.
2. **Drive-rajapinta päälle.** Avaa https://console.cloud.google.com/apis/library/drive.googleapis.com ja paina Ota käyttöön (Enable). Odotettu tulos: sivulla lukee API enabled / Rajapinta käytössä.
3. **Suostumusnäkymä.** Avaa https://console.cloud.google.com/auth/overview ja paina Aloita (Get started). Sovelluksen nimeksi `Aivo`, tukisähköpostiksi oma osoitteesi, kohderyhmäksi Ulkoinen (External), yhteystiedoksi oma osoitteesi. Odotettu tulos: näkymä näyttää sovelluksen tiedot ilman punaisia virheitä.
4. **Oikeus drive.readonly.** Avaa https://console.cloud.google.com/auth/scopes, paina Lisää tai poista oikeuksia (Add or remove scopes) ja valitse **vain** `.../auth/drive.readonly`. Tallenna. Odotettu tulos: kohdassa "Rajoitetut oikeudet" (Restricted scopes) on täsmälleen yksi rivi, drive.readonly.
5. **Julkaisun tila.** Avaa https://console.cloud.google.com/auth/audience. Jos tila on Testaus (Testing), Googlen kirjautumistunnus vanhenee 7 päivässä. Paina Julkaise sovellus (Publish app) / Tuotantoon. Sovellus on vahvistamaton ja vain sinun käytössäsi, joten Google näyttää varoituksen kirjautuessa. Se on odotettu. Odotettu tulos: tila on Tuotannossa (In production).
6. **OAuth-asiakas.** Avaa https://console.cloud.google.com/auth/clients, paina Luo asiakas (Create client), tyypiksi Työpöytäsovellus (Desktop app), nimeksi `aivo-isanta`. Odotettu tulos: ruutu näyttää asiakastunnuksen (Client ID) ja salaisuuden (Client secret). Pidä ruutu auki. Älä liitä näitä mihinkään keskusteluun tai repoon.
7. **Kansio isännälle** (isännällä rootina):
   ```
   install -d -m 700 -o aivo -g aivo /etc/aivo
   ```
   Odotettu tulos: komento ei tulosta mitään.
8. **Kirjautuminen** (isännällä):
   ```
   sudo -u aivo python3 -m aivo.drive kirjaudu
   ```
   Komento kysyy `client_id`- ja `client_secret`-arvot (kirjoitus ei näy ruudulla), tulostaa sitten lupasivun osoitteen. Avaa osoite selaimessa, valitse tilisi ja hyväksy. Odotettu tulos: selain yrittää avata osoitteen `127.0.0.1:8765` ja näyttää virheen. Se on odotettu. Kopioi osoiterivin koko osoite ja liitä se komennolle. Lopuksi komento tulostaa: `Tunnus tallennettu (scope drive.readonly).`
9. **Tarkistus**:
   ```
   stat -c '%a %U' /etc/aivo/drive-tunnus.json
   sudo -u aivo python3 -m aivo.drive listaa -n 10
   sudo -u aivo python3 -m aivo.drive kirjoitustesti
   ```
   Odotettu tulos: `600 aivo`; kymmenen riviä (päivä, nimi, tyyppi, id); `kirjoitus hylatty (HTTP 403), odotettu`.

Peruutus: https://myaccount.google.com/permissions → Aivo → Poista käyttöoikeus. Sen jälkeen tunnus lakkaa toimimasta heti.

## Rajaukset ja tunnetut puutteet

- Koodi on testattu vain feikkivastauksilla. Oikeaa Google-kutsua ja oikeaa `claude -p` -kutsua ei ole ajettu, koska tunnusta ei vielä ole. Todennus tapahtuu vasta askeleen 9 jälkeen.
- Binäärit (kuvat, PDF, Office-tiedostot) ohitetaan. Vain Google-dokumentit, -taulukot, -esitykset ja tekstitiedostot luetaan, enintään 200 kt kerrallaan.
- Aikataulutettua hakua ei ole. Aivo kutsuu komentoja itse; ajastin lisätään, kun hyöty on osoitettu.
