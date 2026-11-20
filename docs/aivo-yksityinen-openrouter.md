# Aivon yksityinen tila OpenRouterin avoimella mallilla (A984)

Tämä ohje on Tonylle. Rajattu ulkoinen mallipalvelu (A728). Tonyn sha-sidottu kyllä tarvitaan ennen käyttöönottoa.

**Sääntömuutos (näkyvä):** aiempi sääntö "yksityisen chatin sisältö ei koskaan lähde OpenRouteriin" (A728) muuttuu Tonyn päätöksen A984 mukaan. `openrouter_zdr.rakenna_runko` sallii lipun `yksityinen_chat=True` vain, kun ZDR ja `data_collection=deny` on pakotettu ja malli on avoin malli listalta `AVOIMET_MALLIT`. Muuten tulee `ZdrVirhe`. Aivon yksityinen tila antaa lipun aina. Muuta (katot, avain, ZDR) ei lievennetä.

## Mitä muuttuu

- Yksityisellä tilalla on kaksi taustapäätä. Oletus on edelleen paikallinen mallipalvelin (loopback). Uusi vaihtoehto on OpenRouterin avoin malli: `--yksityinen-taustapaa openrouter`.
- Muisti, keskustelut, ydinohjeet ja ehdotukset pysyvät kontissa 122. OpenRouterille lähtee vain kulloinenkin kehote (ydinohjeet, muistista valitut otteet ja keskusteluhistoria).
- Jokainen kutsu kulkee `openrouter_kulut.kutsu`n kautta. Se tarkistaa yhteisen päiväkatkaisimen (10 €/vrk, sama kirjanpito ja sama katto kuin ajurilla, #309; yksityisellä tilalla ei ole omaa kulutilaa), rakentaa pyynnön `openrouter_zdr.rakenna_runko`lla (ZDR ja `data_collection=deny` pakotettuina) ja kirjaa kulun. Kuljetin kieltäytyy lähettämästä, jos ZDR puuttuu rungosta.
- Mallin pitää olla täsmälleen listalla `AVOIMET_MALLIT` tiedostossa `aivo/openrouter_zdr.py`. Lista on tarkka luettelo mallitunnisteista, ei etuliitteitä, ja jokaisen rivin kommentissa on mallin `hugging_face_id`. Suljetut mallit (esim. `qwen/qwen3.7-max`) ja kaikki liitteet (`:free`, `:batch`, `:online`, `:nitro` ja muut) hylätään. Oletusmallia ei ole.
- Avain luetaan tiedostosta, jonka omistaa palvelun käyttäjä ja jonka oikeudet ovat 0600. Ympäristömuuttujaa ei lueta. Avain luetaan joka kutsulla, joten sen voi vaihtaa ilman uudelleenkäynnistystä.
- Puuttuva tai väärin suojattu avain antaa selkeän virheen (HTTP 502 ja teksti "OpenRouter-avain puuttuu…"), eikä päiväkattovarausta kirjata.
- Työkalutila ei käytä tätä taustapäätä. Testit tarkistavat sen.
- Yksityisen alueen keskustelujen tiivistys käyttää samaa taustapäätä, kun `AIVO_YKSITYINEN_TAUSTAPAA=openrouter` (sama malli ja avain). Tiivistyksellä on lisäksi oma raja, 10 kutsua vuorokaudessa, ja sen kulut lasketaan samaan 10 €/vrk kattoon. Katso `docs/aivo-yksityinen.md`, kohta Keskustelujen tiivistys.
- Kehote kertoo mallille totuudenmukaisesti, että kehote kulkee OpenRouterin kautta nollasäilytyksellä. Sivun valinnan teksti kertoo taustapään: loopback-tilassa "Yksityinen (oma malli, ei verkkoa)" ja OpenRouter-tilassa "Yksityinen (avoin malli OpenRouterissa, ZDR: ei tallennusta)".

## Suositeltu malli ja kustannusarvio

Hinnat on haettu OpenRouterin julkisesta mallilistasta ja ZDR-päätepisteiden listasta 16.11.2026. Hinnat muuttuvat, joten tarkista ne ennen käyttöönottoa.

| Malli | Listahinta (sisään / ulos, $ per miljoona tokenia) | ZDR-päätepisteitä | ZDR-hintahaarukka |
|---|---|---|---|
| `qwen/qwen3-235b-a22b-2507` (suositus) | 0,14 / 0,80 | 6 | 0,07–0,20 / 0,44–0,83 |
| `meta-llama/llama-3.3-70b-instruct` | 0,71 / 0,71 | 9 | 0,20–0,72 / 0,52–1,20 |

Suomen kieltä ei ole mitattu. Valitse malli Tonyn oman kokeilun mukaan.

Oletukset: yksi OpenRouter-kutsu per Tonyn viesti, keskustelu on 20 viestiä, kurssi 0,92 €/$ (sama vakio kuin kulukirjanpidossa). Historia oli laskelmaa tehtäessä enintään 12 viestiä; nyt raja on 40 (katso alla). Tyypillinen kutsu on 6 000 tokenia sisään ja 350 ulos, raskas 14 000 sisään ja 700 ulos (muistiraja 16 000 merkkiä ja ydinohjeraja 20 000 merkkiä täynnä). Laskennassa käytetään ZDR-ylärajaa (0,20 / 0,825) Qwenille ja listahintaa Llamalle.

| Malli ja kuorma | Per keskustelu (20 viestiä) | Kuukausi, 20 viestiä/pv | Kuukausi, 50 viestiä/pv |
|---|---|---|---|
| Qwen3 235B, tyypillinen | 0,04 € | 1,10 € | 2,76 € |
| Qwen3 235B, raskas | 0,07 € | 2,15 € | 5,36 € |
| Llama 3.3 70B, tyypillinen | 0,12 € | 3,49 € | 8,72 € |
| Llama 3.3 70B, raskas | 0,23 € | 6,76 € | 16,90 € |

Historiaraja on nyt 40 viestiä (`yksityinen_or.HISTORIA_YLARAJA`, 19.11.2026). Taulukon 20 viestin keskustelussa koko keskustelu kulkee silloin mukana, ja syöte kasvaa keskustelun loppua kohti. Pahin yksittäinen kutsu (40 täyttä viestiä, muisti ja ydinohjeet täynnä) on noin 55 000–80 000 tokenia sisään, Qwenillä ZDR-ylärajalla noin 0,01–0,02 € kutsulta. Päiväkatto ei muutu.

Päiväkatto 10 €/vrk on huomattavasti suurempi kuin yksityisen tilan odotettu kulu (alle 0,6 €/vrk raskaimmassakin laskelmassa). Virheellinen tai aikakatkaistu kutsu kirjataan varovaisesti 0,50 $:n kuluna, joten toistuvat virheet näkyvät kirjanpidossa.

## Käyttöönotto (Tony tekee kontissa 122)

Ota ensin snapshot kontista 122. Käyttäjänimi `aivo-yksityinen` on sama kuin yllä olevassa ohjeessa.

1. **Kulukirjanpidon kansio.** Kirjanpito on yhteinen: koodi käyttää kansiota `/var/lib/claude-jono` (`openrouter_kulut.OLETUS_TILA`), eikä sitä voi vaihtaa palvelun asetuksista. Palvelun käyttäjän `aivo-yksityinen` pitää päästä kirjoittamaan sinne, ja kansion pitää olla sama kuin ajurilla. Katso avoimet kohdat.

2. **Avaintiedosto.** Kirjoita avain itse. Tämä komento kysyy sen piilotettuna eikä jätä sitä komentohistoriaan:

   ```
   sudo -u aivo-yksityinen sh -c 'umask 077; printf "Avain: "; stty -echo; read -r K; stty echo; printf "%s" "$K" > /var/lib/aivo-yksityinen/openrouter-avain'
   ls -l /var/lib/aivo-yksityinen/openrouter-avain
   ```

   Tiedoston pitää olla `-rw-------` ja omistajan `aivo-yksityinen`. Luo avain OpenRouterissa omalla, vain tätä käyttöä varten tehdyllä avaimella ja aseta sille tarvittaessa kulutuskatto OpenRouterin puolella.

3. **Palvelun asetukset.** Lisää `aivo-chat.service`-tiedoston `ExecStart`-riville: `--yksityinen-taustapaa openrouter --openrouter-malli qwen/qwen3-235b-a22b-2507` (toinen valitsin: `--openrouter-avain-tiedosto`). Useamman mallin lista annetaan valitsimella `--openrouter-mallit` tai muuttujalla `AIVO_OPENROUTER_MALLIT` (pilkuin erotettu, ensimmäinen on oletus); katso `aivo-yksityinen.md`, osio "Mallin valinta". Usean mallin yhdistelmä (tarkastus, rinnakkais tai reititys) valitaan valitsimella `--yksityinen-yhdistelma` ja roolit valitsimella `--yksityinen-roolit`; katso osio "Usean mallin yhdistelmät". Oletus on yksi malli. Lisää `/var/lib/claude-jono` kohtaan `ReadWritePaths`, koska `ProtectSystem=strict` estää kirjoituksen muuten. Kontin 122 pitää päästä HTTPS-yhteydellä osoitteeseen `openrouter.ai`. Palomuuria ei muuteta tämän muutoksen mukana, joten tarkista saanti itse.

4. **Uudelleenkäynnistys ja testi.**

   ```
   sudo systemctl daemon-reload
   sudo systemctl restart aivo-chat
   sudo systemctl status aivo-chat --no-pager
   ```

   Avaa Aivo, valitse Yksityinen ja lähetä viesti. Odotettu tulos: vastaus tulee muutamassa sekunnissa, ja kulutiedostoon ilmestyy rivi: `sudo tail -n 2 /var/lib/claude-jono/openrouter-kulut.jsonl` (rivillä on vain malli, aika ja luvut). Jos avain puuttuu, sivu näyttää virheen "OpenRouter-avain puuttuu".

5. **Takaisin paikalliseen malliin.** Poista `--yksityinen-taustapaa`-valitsin (oletus on `loopback`) ja käynnistä palvelu uudelleen.

## Avoimet kohdat

- Katto on yksi, koska koodissa on yksi kulutila. Jos Aivo ajetaan kontissa 122 eikä siellä ole samaa `/var/lib/claude-jono`-kansiota kuin ajurilla (esim. jaettu liitos), kontin kirjanpito on tosiasiassa erillinen ja katto kaksinkertainen. Tämä on käyttöönoton ehto: Tonyn pitää päättää, miten kansio jaetaan. Koodi ei tarjoa kiertotietä (ei omaa kulutilaa asetuksena).
- Mallin suomen kielen laatua ei ole mitattu.
