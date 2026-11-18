# Aivon yksityinen tila OpenRouterin avoimella mallilla (A984)

Tämä ohje on Tonylle. Rajattu ulkoinen mallipalvelu (A728). Tonyn sha-sidottu kyllä tarvitaan ennen käyttöönottoa.

**Sääntömuutos (näkyvä):** aiempi sääntö "yksityisen chatin sisältö ei koskaan lähde OpenRouteriin" (A728) muuttuu Tonyn päätöksen A984 mukaan. `openrouter_zdr.rakenna_runko` sallii lipun `yksityinen_chat=True` vain, kun ZDR ja `data_collection=deny` on pakotettu ja malli on avoin malli listalta `AVOIMET_MALLIT`. Muuten tulee `ZdrVirhe`. Aivon yksityinen tila antaa lipun aina. Muuta (katot, avain, ZDR) ei lievennetä.

## Mitä muuttuu

- Yksityisellä tilalla on kaksi taustapäätä. Oletus on edelleen paikallinen mallipalvelin (loopback). Uusi vaihtoehto on OpenRouterin avoin malli: `--yksityinen-taustapaa openrouter`.
- Muisti, keskustelut, ydinohjeet ja ehdotukset pysyvät kontissa 122. OpenRouterille lähtee vain kulloinenkin kehote (ydinohjeet, muistista valitut otteet ja keskusteluhistoria).
- Jokainen kutsu kulkee `openrouter_kulut.kutsu`n kautta. Se tarkistaa päiväkatkaisimen (10 €/vrk), rakentaa pyynnön `openrouter_zdr.rakenna_runko`lla (ZDR ja `data_collection=deny` pakotettuina) ja kirjaa kulun. Kirjanpito on sen koneen tai kontin omassa kansiossa `/var/lib/claude-jono`, jossa koodi ajetaan. Aivon kirjanpito on siis kontissa 122 ja ajurin kirjanpito kontissa 115: ne ovat erilliset (ks. kohta Katto ja kirjanpito). Yksityisellä tilalla ei ole omaa kulutilaa asetuksena. Kuljetin kieltäytyy lähettämästä, jos ZDR puuttuu rungosta.
- Mallin pitää kuulua avoimiin perheisiin (`AVOIMET_MALLIT` tiedostossa `aivo/openrouter_zdr.py`). Suljetut mallit, `:free`-versiot ja `:online`-liitteet hylätään. Oletusmallia ei ole.
- Avain luetaan tiedostosta, jonka omistaa palvelun käyttäjä ja jonka oikeudet ovat 0600. Ympäristömuuttujaa ei lueta. Avain luetaan joka kutsulla, joten sen voi vaihtaa ilman uudelleenkäynnistystä.
- Puuttuva tai väärin suojattu avain antaa selkeän virheen (HTTP 502 ja teksti "OpenRouter-avain puuttuu…"), eikä päiväkattovarausta kirjata.
- Työkalutila ei käytä tätä taustapäätä. Testit tarkistavat sen.
- Kehote kertoo mallille totuudenmukaisesti, että kehote kulkee OpenRouterin kautta nollasäilytyksellä. Sivun valinnan teksti kertoo taustapään: loopback-tilassa "Yksityinen (oma malli, ei verkkoa)" ja OpenRouter-tilassa "Yksityinen (avoin malli OpenRouterissa, ZDR: ei tallennusta)".

## Suositeltu malli ja kustannusarvio

Hinnat on haettu OpenRouterin julkisesta mallilistasta ja ZDR-päätepisteiden listasta 16.11.2026. Hinnat muuttuvat, joten tarkista ne ennen käyttöönottoa.

| Malli | Listahinta (sisään / ulos, $ per miljoona tokenia) | ZDR-päätepisteitä | ZDR-hintahaarukka |
|---|---|---|---|
| `qwen/qwen3-235b-a22b-2507` (suositus) | 0,14 / 0,80 | 6 | 0,07–0,20 / 0,44–0,83 |
| `meta-llama/llama-3.3-70b-instruct` | 0,71 / 0,71 | 9 | 0,20–0,72 / 0,52–1,20 |

Suomen kieltä ei ole mitattu. Valitse malli Tonyn oman kokeilun mukaan.

Oletukset: yksi OpenRouter-kutsu per Tonyn viesti, keskustelu on 20 viestiä, kurssi 0,92 €/$ (sama vakio kuin kulukirjanpidossa). Historia on enintään 12 viestiä. Tyypillinen kutsu on 6 000 tokenia sisään ja 350 ulos, raskas 14 000 sisään ja 700 ulos (muistiraja 16 000 merkkiä ja ydinohjeraja 20 000 merkkiä täynnä). Laskennassa käytetään ZDR-ylärajaa (0,20 / 0,825) Qwenille ja listahintaa Llamalle.

| Malli ja kuorma | Per keskustelu (20 viestiä) | Kuukausi, 20 viestiä/pv | Kuukausi, 50 viestiä/pv |
|---|---|---|---|
| Qwen3 235B, tyypillinen | 0,04 € | 1,10 € | 2,76 € |
| Qwen3 235B, raskas | 0,07 € | 2,15 € | 5,36 € |
| Llama 3.3 70B, tyypillinen | 0,12 € | 3,49 € | 8,72 € |
| Llama 3.3 70B, raskas | 0,23 € | 6,76 € | 16,90 € |

Päiväkatto 10 €/vrk on huomattavasti suurempi kuin yksityisen tilan odotettu kulu (alle 0,6 €/vrk raskaimmassakin laskelmassa). Virheellinen tai aikakatkaistu kutsu kirjataan varovaisesti 0,50 $:n kuluna, joten toistuvat virheet näkyvät kirjanpidossa.

## Katto ja kirjanpito

- Koodissa on yksi kiinteä kirjanpitokansio: `/var/lib/claude-jono` (`openrouter_kulut.OLETUS_TILA`). Sitä ei voi vaihtaa asetuksella. Kansion pitää olla olemassa, ja palvelun käyttäjän pitää päästä kirjoittamaan siihen. Muuten kutsu estyy (fail closed) eikä mitään lähde OpenRouteriin.
- Kontissa 122 ei ole tätä kansiota valmiina. Ohje luo sen kontin 122 omaksi kansioksi, jonka omistaa `aivo-yksityinen`. Se ei ole ajurin kansio kontissa 115 eikä jaettu liitos.
- Päiväkatto on 10 €/vrk, ja se on konttikohtainen: kontilla 115 (ajuri) ja kontilla 122 (Aivo) on kummallakin oma kirjanpito ja oma 10 € katto. Katto ei näe toisen kontin kulutusta, joten OpenRouterin kokonaiskulu voi olla enintään noin 20 €/vrk (10 € + 10 €). Koodin arvo `PAIVAKATTO_EUR = 10` ei muutu tässä ohjeessa.
- Tonyn päätös 17.11.2026 (A1005): Aivon kontille riittää päiväkatto. OpenRouter-avaimen kuukausiraja ei ole pakollinen askel, eikä ohje vaadi sitä.
- Yhteistä kattoa ajurin ja Aivon välille varten tarvittaisiin koodimuutos tai jaettu kansio. Se on kattoa koskeva muutos, jota ei tehdä ilman Tonyn lupaa.
- Ohje muuttaa palvelun `ReadWritePaths`-asetusta vain yhden kansion verran: lisättävä kansio on `/var/lib/claude-jono`. Syy: `ProtectSystem=strict` estää kirjoituksen kaikkialle muualle, ja kirjanpito on koodissa kiinteästi tässä kansiossa. Kansio on `aivo-yksityinen`-käyttäjän oma (tila 700), eikä muita kansioita avata.

## Käyttöönotto (Tony tekee kontissa 122)

Tee yksi komento kerrallaan ja tarkista Odotettu-rivi ennen seuraavaa. Avainta ei näytetä eikä kirjoiteta mihinkään muualle kuin avaintiedostoon.

**A. Yhteys**

1. Avaa PowerShell Windowsissa ja yhdistä Proxmox-isäntään:

   ```
   ssh root@PROXMOX-ISANTA
   ```

   Korvaa `PROXMOX-ISANTA` Proxmox-isännän osoitteella (se ei ole tässä repossa).

   Odotettu: isännän komentokehote (esimerkiksi `root@agenttifirma:~#`).

2. Ota snapshot kontista 122 ennen muutoksia:

   ```
   pct snapshot 122 ennen-or-yksityinen
   ```

   Odotettu: komento päättyy ilman virheilmoitusta.

3. Siirry konttiin 122:

   ```
   pct enter 122
   ```

   Odotettu: kehote vaihtuu kontin kehotteeksi (`root@...:~#`). Seuraavat komennot ajetaan tässä kontissa.

**B. Kulukirjanpidon kansio kontissa 122**

4. Tarkista, onko kansio jo olemassa:

   ```
   ls -ld /var/lib/claude-jono
   ```

   Odotettu: `No such file or directory`. Jos kansio on jo olemassa, älä poista sitä; ilmoita Tonylle (pilvi-Claudelle) sen omistaja ja sisältö ennen jatkamista.

5. Luo kansio kontin 122 omaksi kirjanpidoksi:

   ```
   install -d -m 700 -o aivo-yksityinen -g aivo-yksityinen /var/lib/claude-jono
   ```

   Odotettu: ei tulostetta.

6. Tarkista oikeudet:

   ```
   ls -ld /var/lib/claude-jono
   ```

   Odotettu: `drwx------ 2 aivo-yksityinen aivo-yksityinen ... /var/lib/claude-jono`.

**C. Avaintiedosto**

7. Kirjoita avain itse. Komento kysyy sen piilotettuna eikä jätä sitä komentohistoriaan. Luo avain OpenRouterissa omalla, vain tätä käyttöä varten tehdyllä avaimella:

   ```
   runuser -u aivo-yksityinen -- sh -c 'umask 077; printf "Avain: "; stty -echo; read -r K; stty echo; printf "%s" "$K" > /var/lib/aivo-yksityinen/openrouter-avain'
   ```

   Odotettu: kehote `Avain: `, jonka jälkeen liität avaimen (ruudulla ei näy mitään) ja painat Enter.

8. Tarkista oikeudet näyttämättä sisältöä:

   ```
   ls -l /var/lib/aivo-yksityinen/openrouter-avain
   ```

   Odotettu: `-rw------- 1 aivo-yksityinen aivo-yksityinen ...`.

**D. Palvelun asetukset**

9. Avaa palvelutiedosto muokattavaksi:

   ```
   nano /etc/systemd/system/aivo-chat.service
   ```

   Odotettu: tiedosto aukeaa. Lisää `ExecStart`-riville `--yksityinen-taustapaa openrouter --openrouter-malli qwen/qwen3-235b-a22b-2507` (tarvittaessa myös `--openrouter-avain-tiedosto`). Lisää `ReadWritePaths`-riville kansio `/var/lib/claude-jono` nykyisten kansioiden rinnalle, koska `ProtectSystem=strict` estää kirjoituksen muuten. Tallenna (Ctrl+O, Enter) ja sulje (Ctrl+X).

10. Tarkista, että kontti pääsee OpenRouteriin (vain yhteys, ei avainta):

    ```
    curl -s -o /dev/null -w '%{http_code}\n' https://openrouter.ai/api/v1/models
    ```

    Odotettu: `200`. Muu tulos tai virhe tarkoittaa, ettei yhteys toimi; palomuuria ei muuteta tämän ohjeen mukana.

**E. Käynnistys ja testi**

11. Lataa asetukset:

    ```
    systemctl daemon-reload
    ```

    Odotettu: ei tulostetta.

12. Käynnistä palvelu uudelleen:

    ```
    systemctl restart aivo-chat
    ```

    Odotettu: ei tulostetta.

13. Tarkista tila:

    ```
    systemctl status aivo-chat --no-pager
    ```

    Odotettu: `Active: active (running)`.

14. Avaa Aivo selaimessa, valitse Yksityinen ja lähetä lyhyt viesti. Odotettu: vastaus tulee muutamassa sekunnissa. Jos avain puuttuu tai on väärin suojattu, sivu näyttää virheen "OpenRouter-avain puuttuu".

15. Tarkista kirjanpito kontissa 122:

    ```
    tail -n 2 /var/lib/claude-jono/openrouter-kulut.jsonl
    ```

    Odotettu: 1–2 riviä, joilla on malli, aika ja luvut (ei viestien sisältöä). Jos tiedostoa ei ole, kutsu ei kulkenut katkaisimen kautta; tarkista kohdat 5–6 ja `ReadWritePaths`.

**F. Takaisin paikalliseen malliin**

16. Poista `--yksityinen-taustapaa`-valitsin (oletus on `loopback`) palvelutiedostosta ja aja `systemctl daemon-reload` ja `systemctl restart aivo-chat`.

## Avoimet kohdat

- Yhteinen katto ajurin ja Aivon välillä puuttuu, ja katto on konttikohtainen (ks. kohta Katto ja kirjanpito). Jos Tony haluaa yhteisen kirjanpidon, se vaatii koodi- tai jakomuutoksen, ja siihen tarvitaan hänen lupansa erikseen.
- Mallin suomen kielen laatua ei ole mitattu.
