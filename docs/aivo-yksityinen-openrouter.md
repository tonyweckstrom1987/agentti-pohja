# Aivon yksityinen tila OpenRouterin avoimella mallilla (A984)

Tämä ohje on Tonylle. Rajattu ulkoinen mallipalvelu (A728). Tonyn sha-sidottu kyllä tarvitaan ennen käyttöönottoa.

**Sääntömuutos (näkyvä):** aiempi sääntö "yksityisen chatin sisältö ei koskaan lähde OpenRouteriin" (A728) muuttuu Tonyn päätöksen A984 mukaan. `openrouter_zdr.rakenna_runko` sallii lipun `yksityinen_chat=True` vain, kun ZDR ja `data_collection=deny` on pakotettu ja malli on avoin malli listalta `AVOIMET_MALLIT`. Muuten tulee `ZdrVirhe`. Aivon yksityinen tila antaa lipun aina. Muuta (katot, avain, ZDR) ei lievennetä.

## Mitä muuttuu

- Yksityisellä tilalla on kaksi taustapäätä. Oletus on edelleen paikallinen mallipalvelin (loopback). Uusi vaihtoehto on OpenRouterin avoin malli: `--yksityinen-taustapaa openrouter`.
- Muisti, keskustelut, ydinohjeet ja ehdotukset tallennetaan kontissa 122. OpenRouterille lähtee jokaisella viestillä koko kehote: ydinohjeet, muisti (kirjasto, agenttidata, tiivistelmät ja muistihaun osumat) ja keskusteluhistoria. Kehotteen koko seuraa mallin kontekstia (A1106), mutta muistiosio on enintään `AIVO_YKSITYINEN_MUISTI_MAX` merkkiä (oletus 40 000, A1123). Muistihaku liittää kysymykseen osuvat kohdat ensimmäisinä, joten koko muistia ei lähetetä joka viestissä. Itsetarkistus lähettää samat tiedot tarkastajamallille samalla rajalla.
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

### Muistin enimmäiskoko ja kustannus (A1123, 26.11.2026)

Kontekstiraja (A1106) täytti mallin `qwen/qwen3.8-2.4t-a95b` (konteksti 262 144 tokenia) kehotteen noin 210 000 merkkiin eli noin 84 000 tokeniin. Yksi viesti maksoi noin 0,18 USD ja itsetarkistuksen kanssa 0,20–0,27 USD (kontin 122 loki 26.11. klo 13.33–13.37).

| Kehote | Merkkejä | Tokeneita (2,5 merkkiä/token) | Hinta viestiltä (sisään 2,0 ja ulos 6,0 USD/Mtok, vastaus 1 500 tokenia) |
|---|---|---|---|
| Ennen: kontekstiraja täynnä | 210 000 | 84 000 | noin 0,18 USD |
| Jälkeen: muisti 40 000, lyhyt historia | 50 000–65 000 | 20 000–26 000 | noin 0,05–0,06 USD |

Itsetarkistuksen tarkastaja saa lähteiksi enintään muistin enimmäiskoon, kehotteen alun ja keskustelun viimeiset viestit (enintään noin 38 000 merkkiä). Tavallisessa keskustelussa se on noin 50 000–60 000 merkkiä eli noin 0,05 USD. Korjauskutsu (vain epävarmoissa vastauksissa) maksaa saman verran kuin itse viesti. Arvio on ylähinnoilla. Toteutunut kulu näkyy journalissa ja kulunäkymässä.

Asetus: `AIVO_YKSITYINEN_MUISTI_MAX=40000` (tai valitsin `--yksityinen-muisti-max`). Sallittu alue on 4 000–1 000 000 merkkiä. Asetus voi vain pienentää kehotetta: suurikaan arvo ei ylitä kontekstirajaa. Päiväkatto, kiinteä varaus ja kutsuraja eivät muutu.

Huomio: raja koskee muistia, ei keskusteluhistoriaa. Historian raja seuraa edelleen mallin kontekstia, joten pitkä keskustelu voi kasvattaa kehotetta. Uusi keskustelu pitää kehotteen pienenä.

### Välimuisti (prompt caching)

OpenRouterin dokumentaation mukaan (https://openrouter.ai/docs/features/prompt-caching, luettu 26.11.2026):

- Välimuistista luetut syötetokenit ilmoitetaan kentässä `usage.prompt_tokens_details.cached_tokens` ja kirjoitetut kentässä `usage.prompt_tokens_details.cache_write_tokens`. Jälkimmäinen tulee vain malleilta, joilla on erillinen välimuistiin kirjoittamisen hinta.
- Osa tarjoajista käyttää välimuistia automaattisesti (esim. DeepSeek, Moonshot, OpenAI, Grok, Z.AI, Gemini). Alibaban Qwen-mallit vaativat erillisen `cache_control`-merkinnän viestin osaan. Muita avoimien mallien tarjoajia ei mainita.
- Välimuistista luettu syöte maksaa tarjoajasta riippuen 0,1–0,5 kertaa normaalin syötehinnan.
- OpenRouter ohjaa saman keskustelun samalle tarjoajalle (sticky routing), jotta välimuisti osuu. Avain on ensimmäisen järjestelmäviestin ja ensimmäisen käyttäjäviestin tiiviste.

Mitä Aivo tekee:

- Kehotteen alku pysyy samana viestistä toiseen: säännöt (`KEHOTE`) ja ydinohjeet ovat järjestelmäviestin alussa, ja vasta niiden jälkeen tulee muisti. Muistihaun osumat ja korjaukset vaihtuvat kysymyksen mukaan, joten välimuisti voi osua vain kehotteen alkuun (noin 5 000–25 000 merkkiä).
- Journalin rivi `yksityinen_or: toteutunut …` näyttää kentät `cached_tokens` ja `cache_write_tokens`, kun OpenRouter ilmoittaa ne. Niistä näkee, käyttääkö valittu tarjoaja välimuistia.
- `cache_control`-merkintää ei lisätä. Se muuttaisi pyynnön runkoa (`openrouter_zdr.rakenna_runko`), ja välimuistiin kirjoittaminen maksaa Alibaballa 1,25-kertaisesti. Jos Tony haluaa sen, se on erillinen päätös.

### Vastauksen pituusraja ja aikaraja (A1095, 25.11.2026)

- OpenRouter-taustapään vastausraja on 8 000 tokenia (`yksityinen_or.MAX_TOKENIA`), noin 20 000–24 000 merkkiä suomea. Paikallisen loopback-mallin raja (`yksityinen.MAX_TOKENIA`, 700) ei muutu, koska i5-8400 ei ehtisi tuottaa 8 000 tokenia aikarajassa. Taustatyö (`kysy_taustatyo`) käyttää samaa 8 000 tokenin rajaa. Mallivertailulla on oma vakio `mallivertailu.MAX_TOKENIA = 700`, jotta tulokset pysyvät vertailukelpoisina.
- Yhden mallin chat-kutsun aikaraja on 360 s (`yksityinen_or.CHAT_AIKARAJA_S`). Pyyntö on `stream=False`, joten palvelin ei lähetä mitään ennen kuin vastaus on valmis. Hidas avoin malli tuottaa noin 25 tokenia sekunnissa: 8 000 tokenia vie noin 320 s, ja kehotteen käsittely vie lisäksi 10–30 s. Selain ei katkaise odotusta, eikä chat-palvelimessa ole omaa pyyntökohtaista aikarajaa. Yhdistelmät (kokonaisaikaraja 240 s), tiivistys ja agentit käyttävät edelleen omia rajojaan (`VASTAUS_AIKARAJA_S` 120 s).
- Jos vastaus katkeaa pituusrajaan (`finish_reason` on `length`), vastauksen loppuun lisätään rivi `[vastaus katkesi pituusrajaan, kirjoita: jatka]`. Lokiin (stderr) kirjataan vain tieto katkeamisesta, ei vastauksen sisältöä.
- Ajatteleva malli voi käyttää koko rajan päättelyyn, jolloin `content` on tyhjä ja `finish_reason` on `length`. Silloin chat, yhdistelmän osakutsu, tiivistys ja taustatyö nostavat virheen `VastausKatkesi`, ja chat näyttää ilmoituksen ”Vastaus katkesi pituusrajaan ennen kuin malli ehti kirjoittaa vastauksen …” tyhjän viestin sijaan. Taustaajo merkitsee työn epäonnistuneeksi syyllä ”vastaus katkesi pituusrajaan ennen tekstiä”. Taustatyön katkennut mutta ei-tyhjä vastaus saa loppuun merkinnän `[vastaus katkesi pituusrajaan]`.
- Kulut: päiväkattoa varten tehtävä varaus on kiinteä `openrouter_kulut.VARAUS_USD` (0,50 $), eikä se riipu `max_tokens`-arvosta. 8 000 tokenia ulos maksaa taulukon malleilla enintään noin 0,01 $ (Llama 1,20 $ / miljoona tokenia), joten varaus kattaa sen. Päiväkatto (10 €/vrk) ei muutu.

### Päättelyn yläraja ja uusinta (26.11.2026)

Ajatteleva malli käyttää samaa `max_tokens`-rajaa päättelyyn ja vastaukseen. Kontin 122 lokissa 26.11. klo 19.43 malli `qwen/qwen3.8-2.4t-a95b` käytti koko 8 000 tokenin rajan päättelyyn: `prompt_tokens=17684, completion_tokens=8000, cost=0.0834 USD` ja `vastaus katkesi pituusrajaan ennen tekstia`. Tony ei saanut vastausta, mutta päättely laskutettiin.

Mitä Aivo tekee:

- Kutsuun lisätään `reasoning: {"max_tokens": N, "exclude": true}`. N on asetus `AIVO_YKSITYINEN_PAATTELY_MAX` (valitsin `--yksityinen-paattely-max`, oletus 2 000, sallittu 0–6 000). Arvo 0 tarkoittaa, että reasoning-parametria ei lähetetä lainkaan. Muu arvo estää palvelun käynnistymisen.
- Raja on aina pienempi kuin kutsun `max_tokens`. Vastaukselle jää vähintään 2 000 tokenia, tai puolet rajasta, jos koko raja on alle 4 000 tokenia. Itsetarkistuksen tarkastajan raja on siksi 750 tokenia (1 500 / 2). Korjauskutsu, yhdistelmän osakutsut, vartija, taustaajo (`kysy_taustatyo`) ja tiivistys (`kysy_tiivistys`, 750 tokenia) käyttävät samaa laskentaa. Taustaajo ja tiivistys lukevat saman asetuksen `AIVO_YKSITYINEN_PAATTELY_MAX` omassa `main()`-funktiossaan, eivätkä ne tee uusintaa.
- `exclude` jättää päättelyn tekstin pois vastauksesta. Aivo ei käytä sitä, joten se olisi vain turhaa siirrettävää.
- Parametri lähetetään vain malleille, jotka päättelevät oletuksena ja joiden OpenRouter-mallitiedoissa `supported_parameters` sisältää `reasoning`. Taulukko `yksityinen_or.PAATTELY_TUKI` on osajoukko joukosta `yksityinen_or.OLETUKSENA_PAATTELEVAT`, ja sama joukko on mallivertailun `AJATTELEVAT_MALLIT`. Muut mallit kutsutaan ilman parametria, eikä niille tehdä uusintaa.
- Valinnaisen päättelyn mallit (esimerkiksi `deepseek/deepseek-chat-v3.1` ja `mistralai/mistral-small-2603`) eivät saa parametria. OpenRouter päättelee `enabled`-tilan kentistä, joten `reasoning.max_tokens` todennäköisesti kytkisi niillä päättelyn päälle. Se kasvattaisi kuluja ja voisi katkaista yhdistelmän lyhyet osakutsut (200 ja 500 tokenia) ja vartijan (800 tokenia).
- Jos vastaus katkeaa silti ennen tekstiä, yhden mallin chat tekee yhden uusintakutsun. Valinnaisen päättelyn malleilla uusinta on ilman päättelyä (`effort: "none"` tai `enabled: false`), ja journaliin kirjataan rivi `yksityinen_or: uusinta kerran ilman paattelya …`. Pakollisen päättelyn malleilla uusinta on `effort: "low"`, ja rivi on `yksityinen_or: uusinta kerran pienemmalla paattelytasolla …`. Rivit eivät sisällä viestien sisältöä.
- Pakollisen päättelyn effort-mallilla (`qwen/qwen3.8-2.4t-a95b`) uusinta tehdään vain, jos taso low on pienempi kuin ensimmäisen kutsun todennäköinen taso (`yksityinen_or.todennakoinen_taso`). Oletusraja 2 000 / 8 000 = 25 % vastaa lähinnä tasoa low, joten uusinta olisi sama pyyntö. Silloin uusintaa ei tehdä, ja journaliin kirjataan rivi `yksityinen_or: uusintaa ei tehty: ensimmainen kutsu oli todennakoisesti jo tasolla low …`. Jos asetus on esimerkiksi 4 000 (50 %, medium) tai 6 000 (75 %, lähinnä xhigh), uusinta tehdään tasolla low. Uusinta kulkee saman päiväkaton kautta, ja se lasketaan kutsurajaan (4 per viesti). Jos katto on täynnä tai kutsuraja ei salli, uusintaa ei tehdä. Yhdistelmän osakutsut ja itsetarkistus eivät tee uusintaa, koska niillä on oma kutsulaskurinsa.
- Rivi `yksityinen_or: kehote …` kertoo myös päättelyn rajan (`paattely enintaan N`). Rivi `yksityinen_or: toteutunut …` kertoo `reasoning_tokens=N`, jos OpenRouter ilmoittaa kentän `usage.completion_tokens_details.reasoning_tokens`. Siitä näkee, pitääkö raja käytännössä.

OpenRouterin tiedot (https://openrouter.ai/docs/use-cases/reasoning-tokens ja `GET https://openrouter.ai/api/v1/models`, luettu 26.11.2026):

| Malli | `reasoning` tuettu | Päättely pakollinen | Effort-tasot | Uusinta |
|---|---|---|---|---|
| `qwen/qwen3.8-2.4t-a95b` | kyllä | kyllä | xhigh, medium, low (oletus xhigh) | `effort: "low"` |
| `qwen/qwen3.5-397b-a17b` | kyllä | ei | ei kerrota | `enabled: false` |
| `deepseek/deepseek-chat-v3.1`, `mistralai/mistral-small-2603` | kyllä, mutta päättely ei ole oletuksena päällä | ei | – | ei uusintaa, ei parametria |
| `meta-llama/llama-4-maverick` | ei | – | – | ei uusintaa, ei parametria |

Dokumentaation mukaan malli, joka tukee vain effort-tasoja, käyttää `reasoning.max_tokens`-arvoa tason valintaan. Tasojen osuudet `max_tokens`-rajasta ovat noin 95 % (max, xhigh), 80 % (high), 50 % (medium), 20 % (low) ja 10 % (minimal). Tarkkaa muunnossääntöä sivu ei kerro (luettu 27.11.2026). Aivo arvioi tason lähimpänä osuutena, ja tasapelissä se valitsee pienemmän tason. Näillä malleilla raja on siis vain taso, ei tarkka tokenmäärä: 2 000 / 8 000 = 25 % vastaa lähinnä tasoa low. Qwen-malleilla `reasoning.max_tokens` välitetään `thinking_budget`-arvona ”joillakin malleilla”. Se, että jokainen taulukon Qwen-malli noudattaa rajaa, on arvaus. Uusinta kattaa tapauksen, jossa raja ei pidä.

Kustannusesimerkki lokin luvuilla (`qwen/qwen3.8-2.4t-a95b`, sisään 2,0 USD ja ulos 6,0 USD miljoonaa tokenia kohden, kehote 17 684 tokenia eli noin 0,035 USD):

| Tilanne | Ulos (tokenia) | Hinta |
|---|---|---|
| Ennen: koko raja päättelyyn, ei vastausta | 8 000 | 0,0834 USD (toteutunut) |
| Jälkeen: päättely enintään 2 000 ja vastaus 800 | 2 800 | noin 0,052 USD |
| Jälkeen: päättely ei pidä, uusinta effort low (noin 1 600 + 800) | 8 000 + 2 400 | noin 0,083 + 0,050 = 0,133 USD |
| 27.11. alkaen oletusrajalla: päättely ei pidä, uusintaa ei tehdä (taso olisi sama) | 8 000 | 0,083 USD |

Kehote on suurin yksittäinen kulu (noin 0,035 USD). Päättelyn raja pienentää ulostulon osuutta noin 0,048 USD:stä enintään noin 0,012 USD:hen. Kolmas rivi oli pahin tapaus ennen 27.11. Oletusrajalla uusinta olisi lähetetty samalla tasolla low, joten se todennäköisesti maksoi toisen kerran ilman parempaa tulosta. Nyt pahin tapaus on 0,083 USD ilman vastausta. Uusinta tehdään vain, jos asetus on niin suuri, että ensimmäinen kutsu oli todennäköisesti tasoa low suurempi.

Malleilla, jotka eivät päättele oletuksena, kulu ei muutu: niiden kutsuun ei lisätä `reasoning`-kenttää, eikä niille tehdä uusintaa. Taustaajossa ja tiivistyksessä raja voi vain pienentää ulostulon määrää, koska niissä ei ole uusintaa. Toteutuneet päättelytokenit näkyvät journalin `reasoning_tokens`-kentästä, kun OpenRouter ilmoittaa ne.

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
