# Aivon yksityinen tila ja ydinohjeet (A729–A731)

Tämä ohje on Tonylle. Claude on kirjoittanut koodin (`aivo/yksityinen.py`, `aivo/chat.py`, `aivo/mittaa_malli.py`), ja Tony ottaa sen käyttöön itse. Koodi ei muuta palomuureja, holvia eikä Tailscalea. Mitään salaisuutta ei ole repossa.

## Mitä muuttuu

- Chat-sivulla on valinta **Yksityinen / Työkalu**. Oletus on yksityinen. Työkalutilaan vaihtaminen kysyy varmistuksen, koska viestit kulkevat silloin Clauden pilvipalveluun.
- **Yksityinen tila** puhuu vain paikalliselle mallipalvelimelle (llama.cpp:n `llama-server` tai vastaava, OpenAI-yhteensopiva). Koodi hyväksyy vain loopback-IP-osoitteen. Muuhun osoitteeseen se ei lähetä mitään, eikä palvelu käynnisty sellaisella asetuksella. Claudea, LiteLLM:ää ja OpenRouteria ei käytetä.
- Yksityisellä tilalla on **oma muisti** (`--yksityinen-muisti`) ja omat keskustelut (`--yksityinen-data`). Työkalutilan kirjasto ei sekoitu niihin, eikä yksityinen muisti mene työkalutilan kehotteeseen.
- Yksityiset kansiot kuuluvat palvelun käyttäjälle ja ovat 0700. Jos kansio kuuluu jollekin toiselle tai on linkki, palvelu ei käynnisty.
- **Ydinohjeet** luetaan vain yksityisessä tilassa. Työkalutila (pilvi-Claude) ei saa niitä.
- Aivo voi **ehdottaa** ydinohjeiden muutosta vastauksessaan lohkolla `[[EHDOTUS tiedosto.md]] … [[/EHDOTUS]]`. Lohko poistuu vastauksesta ja tallentuu ehdotukseksi (0600). Mikään ei muutu ennen Tonyn hyväksyntää (A530).

## Faktat raudasta ja malleista

**Tärkeä rajaus:** tässä kehityskontissa ei ole mallipalvelinta eikä isännän rautaa, joten alla olevat nopeudet ovat laskettuja arvioita, **eivät mittauksia**. Suomen laatua ei ole mitattu lainkaan. Mittaus tehdään isännällä komennolla `python3 -m aivo.mittaa_malli` (ks. alla).

Nykyinen rauta: i5-8400 (6 ydintä, AVX2, ei AVX-512), 32 Gt, ei näytönohjainta.

Tekstin tuottaminen on muistikaistan rajoittama: jokainen sana vaatii koko (aktiivisen) mallin lukemisen muistista. Kaksikanavainen DDR4-2666 antaa teoriassa noin 42 Gt/s, käytännössä noin 25 Gt/s. Nopeus on suunnilleen kaista jaettuna mallin koolla:

| Malli (4-bittinen kvantisointi) | Koko muistissa | Arvioitu nopeus | Huomio |
|---|---|---|---|
| noin 4 mrd parametria | 2,5 Gt | 8–10 tok/s | nopea, suomi todennäköisesti heikkoa |
| noin 8 mrd | 5 Gt | 4–5 tok/s | järkevä ensimmäinen kokeilu |
| noin 12–14 mrd | 8–9 Gt | 2,5–3 tok/s | parempi suomi, hidas |
| harva (MoE) malli, noin 30 mrd, 3 mrd aktiivista | noin 18 Gt | 8–10 tok/s | nopea suhteessa kokoonsa, mutta vie 18 Gt keskusmuistia |

Muita rajoja:

- **Kehotteen käsittely on hidasta**: prosessorilla se on karkeasti 30–60 tok/s 8 mrd mallilla. Siksi yksityisen tilan muistiraja on 16 000 merkkiä (noin 5 000 tokenia, 1–3 min ensimmäiseen sanaan), eikä koko kirjastoa (300 kt) voi syöttää. Koodi valitsee muistista kysymykseen osuvimmat tiedostot sanaosumilla. Se on yksinkertainen ratkaisu, ei hakua merkityksen mukaan.
- Vastaus tulee kokonaisena (ei suoratoistoa), joten pitkä vastaus voi viedä useita minuutteja. Aikaraja on 15 min.
- Isännällä pyörivät kontit käyttävät samaa muistia. Tarkista `free -g` ennen kuin varaat mallille 8–18 Gt.
- Tämä kehitysympäristö (2 vCPU, 6 Gt) on liian pieni mallin ajamiseen.

**Suomen laatu** on avoin kysymys: pienet mallit (alle noin 8 mrd) tekevät tyypillisesti taivutus- ja yhdyssanavirheitä. Ehdokkaita ovat Gemma-, Qwen- ja Mistral-perheiden 8–14 mrd mallit sekä harvat mallit. En voi varmistaa tästä ympäristöstä, mitkä ovat tällä hetkellä parhaat, joten tarkista ajantasainen tilanne ennen latausta.

**Uusi kone (A697):** näytönohjain muuttaisi kuvan eniten, koska näytönohjaimen muistikaista on kymmenkertainen. Karkea arvio: 12 Gt:n näytönohjaimella 8–14 mrd malli pyörii noin 30–60 tok/s, 24 Gt:n näytönohjaimella voi ajaa 30 mrd -luokan mallia noin 20–40 tok/s. Kehotteen käsittely nopeutuu vielä enemmän. Hinnat ja saatavuus pitää tarkistaa erikseen.

**Ristiriita, joka vaatii päätöksen:** A697 sanoo, että lisäteho vuokrataan palvelimelta. Yksityisen tilan vaatimus on, että data on vain isännällä ja Giteassa. Vuokrattu palvelin ei ole isäntä, joten mallia ei pidä ajaa vuokrapalvelimella, jos keskustelut kulkevat sille. Yksityinen tila kuuluu siis omalle koneelle (nykyiselle tai uudelle).

### Mittaus isännällä

Käynnistä mallipalvelin vain loopbackissa, esimerkiksi (polku ja mallitiedosto Tonyn valinnan mukaan):

```
llama-server -m /var/lib/aivo-yksityinen/malli/MALLI.gguf --host 127.0.0.1 --port 8081 -c 8192 -t 6
python3 -m aivo.mittaa_malli
```

Skripti kysyy viisi yleistä kysymystä (git, testaus, tietoturva, taivutus, sähköposti), tulostaa vastaukset ja nopeuden (tok/s). Tony arvioi suomen laadun itse. Skripti hylkää muut kuin loopback-osoitteet.

## Käyttöönotto (Tony ajaa isännällä)

Ota ensin snapshot, jos asennat konttiin. Käyttäjänimet ovat ehdotus.

1. **Käyttäjät.** `aivo-yksityinen` ajaa chat-palvelua ja mallia. `aivo-tyokalu` ajaa työkalutilan `claude`-kutsun. Ne ovat eri käyttäjiä, jotta työkalutila ei pääse lukemaan yksityisiä kansioita tiedosto-oikeuksilla.

   ```
   sudo useradd --system --create-home --home-dir /var/lib/aivo-yksityinen --shell /usr/sbin/nologin aivo-yksityinen
   sudo useradd --system --create-home --home-dir /var/lib/aivo-tyokalu --shell /usr/sbin/nologin aivo-tyokalu
   sudo install -d -m 700 -o aivo-yksityinen -g aivo-yksityinen /var/lib/aivo-yksityinen/{chat,muisti,ehdotukset,malli}
   ```

2. **Työkalutilan käyttäjä.** Claude-kirjautuminen kuuluu `aivo-tyokalu`-käyttäjälle (`sudo -u aivo-tyokalu -H claude`). Chat-palvelu kutsuu sitä komennolla `sudo -n -u aivo-tyokalu -- claude …`, kun ympäristömuuttuja `AIVO_TYOKALU_KAYTTAJA=aivo-tyokalu` on asetettu. Tämä vaatii sudoers-rivin, joka sallii juuri tämän yhden komennon ja vain palvelun käyttäjälle. Sudoers on oikeusmuutos, joten Tony kirjoittaa sen itse (`sudo visudo -f /etc/sudoers.d/aivo-tyokalu`).

3. **Mallipalvelin** (`/etc/systemd/system/aivo-malli.service`). Rivit `IPAddressDeny=any` ja `IPAddressAllow=127.0.0.0/8` estävät mallipalvelinta puhumasta mihinkään loopbackin ulkopuolelle:

   ```
   [Service]
   User=aivo-yksityinen
   ExecStart=/usr/local/bin/llama-server -m /var/lib/aivo-yksityinen/malli/MALLI.gguf --host 127.0.0.1 --port 8081 -c 8192 -t 6
   IPAddressDeny=any
   IPAddressAllow=127.0.0.0/8
   NoNewPrivileges=yes
   ProtectSystem=strict
   ProtectHome=yes
   ```

4. **Chat-palvelu.** Lisää olemassa olevan `aivo-chat.service`-tiedoston `ExecStart`-riville `--yksityinen-data /var/lib/aivo-yksityinen/chat --yksityinen-muisti /var/lib/aivo-yksityinen/muisti --ehdotukset /var/lib/aivo-yksityinen/ehdotukset --ydinohjeet /var/lib/aivo-yksityinen/ydinohjeet`, aseta `User=aivo-yksityinen` ja lisää `ReadOnlyPaths=/var/lib/aivo-yksityinen/ydinohjeet`. Palvelu voi silloin lukea ohjeita mutta ei kirjoittaa niitä; vain `hyvaksy`-komento kirjoittaa.

5. **Varmuuskopio.** `/var/lib/aivo-yksityinen` saa mennä Driveen vain salattuna (rclone crypt, A715–A718). Älä lisää sitä salaamattomaan varmuuskopioon.

6. **Todennus** (ei vielä tehty, odottaa Tonya ja riippumatonta tarkastajaa):
   - `sudo -u aivo-tyokalu ls /var/lib/aivo-yksityinen/chat` → `Permission denied`.
   - Sivulla oletus on "Yksityinen"; lähetä viesti ja tarkista, ettei `claude`-prosessia käynnisty: `pgrep -a claude`.
   - `sudo -u aivo-yksityinen ss -tnp` keskustelun aikana: vain yhteys osoitteeseen 127.0.0.1:8081.

## Ydinohjeet: yksityinen Gitea-repo

Gitea on jo suljettu (rekisteröinti pois, kirjautuminen vaaditaan, käyttäjät vain `tony` ja `aivo`). Tonyn tehtävät:

1. Luo Giteaan **yksityinen** repo `aivo/ydinohjeet`. Älä lisää yhteistyökumppaneita äläkä organisaatiota. Vain `tony` ja `aivo` ovat käyttäjinä.
2. Kloonaa se: `sudo -u aivo-yksityinen git clone https://GITEA/aivo/ydinohjeet.git /var/lib/aivo-yksityinen/ydinohjeet`. Tarkista, ettei `origin` osoita GitHubiin.
3. Kirjoita tai kopioi ydinohjeet itse tiedostoiksi (`*.md`) repoon. Claude ei lue, kopioi eikä kirjoita niiden sisältöä (pilvi-Claude ei pääse Giteaan). Ehdotus siirrettävistä kompassin osioista on tiedostossa `claude-jono/suunnitelmat/aivo-ydinohjeet-ehdotus.md` docs-repossa. **Mitään ei ole siirretty.**
4. **Huomio historiasta:** jos poistat osion docs-repon kompassista, se säilyy docs-repon git-historiassa. Jos osion pitää olla pilvi-Claudelle näkymätön myös historiassa, historia pitää kirjoittaa uusiksi (iso, peruuttamaton toimenpide; ota ensin varmuuskopio).

### Muutosten kulku (A530)

1. Aivo ehdottaa yksityisessä tilassa `[[EHDOTUS saannot.md]] … [[/EHDOTUS]]`. Ehdotus tallentuu kansioon `ehdotukset` (tunnus, tiedosto, sha-tiiviste).
2. Tony katsoo ehdotukset: `sudo -u aivo-yksityinen python3 -m aivo.yksityinen ehdotukset`, ja eron: `… nayta TUNNUS`.
3. Tony hyväksyy ajamalla `… hyvaksy TUNNUS` päätteessä ja kirjoittamalla tarkalleen `kyllä XXXXXXXX` (sha:n 8 ensimmäistä merkkiä). Vahvistus on sidottu juuri nähtyyn sisältöön: jos ehdotuksen teksti muuttuu, tiiviste ei täsmää. Ilman päätettä komento ei hyväksy mitään.
4. Hyväksytty muutos kirjoitetaan ydinohjeisiin ja commitoidaan; pushaus tehdään vain, jos `origin` ei ole GitHub.

## Rajat ja avoimet asiat

- Yksityinen tila ja työkalutila ovat samassa prosessissa. Erottelu perustuu siihen, että työkalutilan Claude-kutsu ajetaan toisena käyttäjänä (kohta 2) eikä saa työkaluja (`--tools ""`). Jos `AIVO_TYOKALU_KAYTTAJA`-asetusta ei käytetä, erottelu on vain koodin tasolla, ei tiedosto-oikeuksilla.
- Ensimmäinen vastaus yksityisessä tilassa on hidas (ks. nopeudet).
- Aivon vastauksen lähdeviitteet tarkistetaan kuten työkalutilassa.
