# Aivon yksityinen tila ja ydinohjeet (A729–A731)

Tämä ohje on Tonylle. Claude on kirjoittanut koodin (`aivo/yksityinen.py`, `aivo/chat.py`, `aivo/mittaa_malli.py`), ja Tony ottaa sen käyttöön itse. Koodi ei muuta palomuureja, holvia eikä Tailscalea. Mitään salaisuutta ei ole repossa.

## Mitä muuttuu

- Chat-sivulla on valinta **Yksityinen / Työkalu**. Oletus on yksityinen. Työkalutilaan vaihtaminen kysyy varmistuksen, koska viestit kulkevat silloin Clauden pilvipalveluun.
- **Yksityinen tila** puhuu vain paikalliselle mallipalvelimelle (llama.cpp:n `llama-server` tai vastaava, OpenAI-yhteensopiva). Koodi hyväksyy vain loopback-IP-osoitteen. Muuhun osoitteeseen se ei lähetä mitään, eikä palvelu käynnisty sellaisella asetuksella. Claudea ja LiteLLM:ää ei käytetä. Vaihtoehtoinen taustapää on OpenRouterin avoin malli (ks. `aivo-yksityinen-openrouter.md`, Tonyn päätös A984).
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

**Käyttäjät kahdessa ohjeessa.** Chat-palvelun perusasennus on ohjeessa `aivo-chat.md`, ja siinä chat-palvelun käyttäjä on `aivo` (kohdat 1 ja 5). Tämä ohje olettaa, että se asennus on tehty ensin. Tämän ohjeen kohta 1 luo lisäksi käyttäjän `aivo-yksityinen`, ja kohta 4 vaihtaa chat-palvelun käyttäjäksi `aivo-yksityinen`. Jos `aivo-chat.md`:n asennus on tekemättä, tee se ensin äläkä aja tämän ohjeen komentoja sen tilalla. Tarkista tilanne ennen aloitusta ja kohdan 4 jälkeen: `systemctl show aivo-chat -p User`.

1. **Käyttäjät.** `aivo-yksityinen` ajaa chat-palvelua ja mallia. `aivo-tyokalu` ajaa työkalutilan `claude`-kutsun. Ne ovat eri käyttäjiä, jotta työkalutila ei pääse lukemaan yksityisiä kansioita tiedosto-oikeuksilla.

   ```
   sudo useradd --system --create-home --home-dir /var/lib/aivo-yksityinen --shell /usr/sbin/nologin aivo-yksityinen
   sudo useradd --system --create-home --home-dir /var/lib/aivo-tyokalu --shell /usr/sbin/nologin aivo-tyokalu
   sudo install -d -m 700 -o aivo-yksityinen -g aivo-yksityinen /var/lib/aivo-yksityinen/{chat,muisti,ehdotukset,malli}
   ```

2. **Työkalutilan käyttäjä.** Claude-kirjautuminen kuuluu `aivo-tyokalu`-käyttäjälle (`sudo -u aivo-tyokalu -H claude`). Chat-palvelu kutsuu sitä unix-socketin kautta (`AIVO_TYOKALU_SOCKET=/run/aivo-tyokalu.sock`), jonka toisessa päässä systemd ajaa `claude`-komennon käyttäjänä `aivo-tyokalu` kiinteillä valitsimilla. Sudoa ei tarvita, eikä `NoNewPrivileges` jouda pois. Yksiköt ovat tiedostossa `aivo-chat.md` (kohta 5b); Tony luo ne itse.

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

4. **Chat-palvelu.** Lisää olemassa olevan `aivo-chat.service`-tiedoston `ExecStart`-riville `--yksityinen-data /var/lib/aivo-yksityinen/chat --yksityinen-muisti /var/lib/aivo-yksityinen/muisti --ehdotukset /var/lib/aivo-yksityinen/ehdotukset --ydinohjeet /var/lib/aivo-yksityinen/ydinohjeet`, aseta `User=aivo-yksityinen` (säilytä `SupplementaryGroups=aivo-tyokalu-socket`, jotta työkalutilan socket pysyy käytettävänä) ja lisää `ReadOnlyPaths=/var/lib/aivo-yksityinen/ydinohjeet`. Palvelu voi silloin lukea ohjeita mutta ei kirjoittaa niitä; vain `hyvaksy`-komento kirjoittaa.

5. **Varmuuskopio.** `/var/lib/aivo-yksityinen` saa mennä Driveen vain salattuna (rclone crypt, A715–A718). Älä lisää sitä salaamattomaan varmuuskopioon.

6. **Todennus** (ei vielä tehty, odottaa Tonya ja riippumatonta tarkastajaa):
   - `sudo -u aivo-tyokalu ls /var/lib/aivo-yksityinen/chat` → `Permission denied`.
   - Sivulla oletus on "Yksityinen"; lähetä viesti ja tarkista, ettei `claude`-prosessia käynnisty: `pgrep -a claude`.
   - `sudo -u aivo-yksityinen ss -tnp` keskustelun aikana: vain yhteys osoitteeseen 127.0.0.1:8081.

## Ydinohjeet: yksityinen Gitea-repo

Gitea on jo suljettu (rekisteröinti pois, kirjautuminen vaaditaan, käyttäjät vain `tony` ja `aivo`). Tonyn tehtävät:

1. Luo Giteaan **yksityinen** repo `aivo/ydinohjeet`. Älä lisää yhteistyökumppaneita äläkä organisaatiota. Vain `tony` ja `aivo` ovat käyttäjinä.
2. Kloonaa se rajatulla umaskilla: `sudo -u aivo-yksityinen sh -c 'umask 077 && git clone https://GITEA/aivo/ydinohjeet.git /var/lib/aivo-yksityinen/ydinohjeet'`. Tarkista oikeudet: `stat -c '%a %U' /var/lib/aivo-yksityinen/ydinohjeet` → `700 aivo-yksityinen`. Palvelu kieltäytyy lukemasta ydinohjeita, jos kansio ei kuulu sen käyttäjälle tai on muiden luettavissa. Tarkista, ettei `origin` osoita GitHubiin.
   Aseta sallitut push-palvelimet ympäristömuuttujaan `AIVO_SALLITUT_ETAISANNAT` (esim. `gitea.lan`; tarkka isäntänimi tai IP, pilkulla eroteltuna) tai `hyvaksy`-komennon valitsimella `--sallitut-etaisannat`. Tyhjällä listalla hyväksyntä kieltäytyy, jos kirjastolla on `origin`.
3. Kirjoita tai kopioi ydinohjeet itse tiedostoiksi (`*.md`) repoon. Claude ei lue, kopioi eikä kirjoita niiden sisältöä (pilvi-Claude ei pääse Giteaan). Ehdotus siirrettävistä kompassin osioista on tiedostossa `claude-jono/suunnitelmat/aivo-ydinohjeet-ehdotus.md` docs-repossa. **Mitään ei ole siirretty.**
4. **Huomio historiasta:** jos poistat osion docs-repon kompassista, se säilyy docs-repon git-historiassa. Jos osion pitää olla pilvi-Claudelle näkymätön myös historiassa, historia pitää kirjoittaa uusiksi (iso, peruuttamaton toimenpide; ota ensin varmuuskopio).

### Muutosten kulku (A530)

1. Aivo ehdottaa yksityisessä tilassa `[[EHDOTUS saannot.md]] … [[/EHDOTUS]]`. Ehdotus tallentuu kansioon `ehdotukset` (tunnus, tiedosto, sha-tiiviste).
2. Tony katsoo ehdotukset: `sudo -u aivo-yksityinen python3 -m aivo.yksityinen ehdotukset`, ja eron: `… nayta TUNNUS`.
3. Tony hyväksyy ajamalla `… hyvaksy TUNNUS` päätteessä ja kirjoittamalla tarkalleen `kyllä XXXXXXXX` (sha:n 8 ensimmäistä merkkiä). Vahvistus on sidottu juuri nähtyyn sisältöön: jos ehdotuksen teksti muuttuu, tiiviste ei täsmää. Ilman päätettä komento ei hyväksy mitään.
   Vaihtoehtoisesti Tony hyväksyy tai hylkää ehdotuksen chat-sivulla (A1018, katso `docs/aivo-chat.md`, kohta Ydinohjeiden ehdotukset chat-sivulla). Sivun hyväksyntä kutsuu samaa `hyvaksy_ehdotus`-funktiota samalla sha-sidonnalla. Hylkäys siirtää ehdotuksen alikansioon `hylatyt/` (0700) eikä poista sitä; päätteessä sama tehdään komennolla `… hylkaa TUNNUS`.
4. Ennen mitään muutosta tarkistetaan kaikki `origin`in push-osoitteet (`git remote get-url --push --all`): jokaisen isännän on oltava sallittujen listalla. Sen jälkeen tiedosto kirjoitetaan ja commitoidaan. Jos commit epäonnistuu (esim. git-identiteetti puuttuu), tiedosto palautetaan, ehdotus säilyy ja komento ilmoittaa virheen. Ehdotus poistetaan vasta onnistuneen commitin jälkeen. Push nimeää kohteen (`git push origin HEAD:refs/heads/<haara>`), joten `remote.pushDefault` ja `branch.*.pushRemote` eivät ohjaa sitä muualle. Pushin epäonnistuminen ei peru commitia; tulos on `push-epaonnistui`.
5. Ehdotuksen ero tulostetaan terminaaliin puhdistettuna: ohjaus- ja muotoilumerkit (esim. ESC, CR) näkyvät `\\x1b`-tyylisinä merkintöinä, jotta ne eivät voi piilottaa sisältöä ennen hyväksyntää. Jos ydinohjeita ei mahdu kehotteen ylärajaan, kehotteeseen tulee näkyvä huomautus pois jääneistä tiedostoista.

## Päätösten kirjaus

Yksityisen tilan päätös kirjataan vain yksityiseen muistiin (tiedosto 0600) eikä koskaan commitoida tai pushata, vaikka muisti olisi git-repossa. Työkalutilan päätös julkaistaan kirjaston `origin`iin vain, jos sen palvelin on `AIVO_SALLITUT_ETAISANNAT`-listalla; jos julkaisu heittää poikkeuksen, päätöstiedosto palautetaan ennalleen. Jos push epäonnistuu (tulos `push-epaonnistui`), commit jää kirjaston paikalliseen kloonaan eikä sitä peruta. Chat vastaa silloin virhekoodilla 502 eikä näytä "Kirjattu". Selain saa Tonyn viestin paikan palvelimelta, joten päätöspainike kirjaa aina juuri sen viestin.

## Keskustelujen tiivistys (komento)

Komento `python3 -m aivo.tiivista --alue yksityinen` tiivistää yksityisen alueen keskustelut. Keskustelu tiivistetään vasta, kun sen viimeisestä viestistä on 30 minuuttia. Pitkä keskustelu pilkotaan osiin, ja jokainen osa tallentuu omaksi tiivistelmäkseen kansioon `tiivistelmat`. Valinta `--kuiva` listaa tiivistettävät alueet ilman mallikutsua.

Mallin lähde valitaan samalla asetuksella kuin chatissa: `AIVO_YKSITYINEN_TAUSTAPAA` (tai `--taustapaa`), oletus `loopback`.

- **loopback**: paikallinen malli samassa loopback-osoitteessa kuin chatissa (`--malli-url` tai `AIVO_MALLI_URL`, oletus `http://127.0.0.1:8081`). Muuta osoitetta ei hyväksytä. Kulukattoa ei ole, koska paikallinen malli ei maksa mitään.
- **openrouter**: OpenRouterin avoin malli nollasäilytyksellä (ZDR). Malli on sama kuin chatissa (`AIVO_OPENROUTER_MALLI`), ja avain luetaan samasta tiedostosta (`AIVO_OPENROUTER_AVAIN`, oletus `/var/lib/aivo-yksityinen/openrouter-avain`). Kutsu kulkee vain funktion `yksityinen_or.kysy_tiivistys` ja sitä kautta `openrouter_kulut.kutsu`n läpi. Pyynnössä on aina `provider.zdr=true` ja `data_collection=deny`, suljettu malli hylätään ennen kutsua, ja kulut kirjataan tiedostoon `/var/lib/claude-jono/openrouter-kulut.jsonl` tehtävän tunnisteella `aivo-yksityinen-tiivistys`. Muita verkkokohteita ei ole.

Kulurajat OpenRouter-taustapäällä:

- Enintään 10 tiivistyskutsua vuorokaudessa (Europe/Helsinki), kuten työkalutilassa. Kutsu varataan ennen lähetystä. Kun raja täyttyy, ajo lopettaa ja kirjaa rivin `lopetus: paivakatto 10/10 taynna`.
- Kulut lasketaan samaan konttikohtaiseen 10 €/vrk päiväkattoon kuin chatin kutsut (A1005). Jos katto on täynnä, kutsua ei tehdä, ajo lopettaa (`lopetus: kulukatto taynna`) eikä yrityskertoja kulu.

Keskustelun sisältö ei päädy lokiin (`indeksi/tiivista.log`), kulukirjanpitoon eikä virheviesteihin. Lokissa on vain keskustelun tunnus, viestien numerot ja virheen tyyppi.

### Tiivistelmät yksityisen tilan kehotteeseen

Chat liittää yksityisen alueen tiivistelmät yksityisen tilan kehotteeseen vain, kun palvelulla on asetus `AIVO_YKSITYISET_TIIVISTELMAT_KAYTOSSA=1` (tai valitsin `--yksityiset-tiivistelmat-kaytossa`). Oletuksena ominaisuus on pois päältä. Kansio on oletuksena `--yksityinen-data`-kansion viereinen `tiivistelmat` (eli `/var/lib/aivo-yksityinen/tiivistelmat`), ja sen voi vaihtaa asetuksella `AIVO_YKSITYISET_TIIVISTELMAT`.

- Mekanismi ja yläraja ovat samat kuin työkalutilassa (`aivo/tiivistelmat_kehote.py`, enintään 60 kt). Kysymykseen osuvat ja uusimmat tiivistelmät tulevat ensin.
- Erottelu: työkalutila lukee vain kansion `AIVO_TIIVISTELMAT` (oletus `/var/lib/aivo/tiivistelmat`), ja yksityinen tila lukee vain yksityisen alueen kansion. Palvelu ei käynnisty, jos molemmat asetukset osoittavat samaan kansioon.
- Paikallisella mallilla 60 kt pidentää kehotteen käsittelyä selvästi (katso Keskusteluhistoria mallille). Kytkin on tarkoitettu ensisijaisesti OpenRouter-taustapäälle.

Käyttöönotto: lisää `aivo-chat.service`-tiedostoon rivi `Environment=AIVO_YKSITYISET_TIIVISTELMAT_KAYTOSSA=1` ja käynnistä palvelu uudelleen (`systemctl daemon-reload && systemctl restart aivo-chat`).

### Tiivistyksen ajastin (ohje, ei asennettu)

Repossa ei ole tälle valmista yksikköä, eikä tämä muutos asenna mitään. Tony luo tiedostot itse, jos haluaa ajastuksen. Malli on sama kuin työkalutilan `aivo-tiivista.timer` (`docs/aivo-chat.md`). Alla on kaksi vaihtoehtoa: OpenRouter-taustapää (kontti, jossa chat käyttää OpenRouteria) ja paikallinen malli.

Ajastimen käyttäjän pitää olla sama kuin `aivo-chat.service`-palvelun `User=` (kontissa `aivo`). Syy: yksityiset kansiot ja avaintiedosto tarkistetaan palvelun käyttäjän omiksi (0700 ja 0600). Tarkista käyttäjä: `systemctl show -p User aivo-chat`.

1. **Kuiva-ajo** (ei mallikutsua):

   ```
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja AIVO_YKSITYINEN_TAUSTAPAA=openrouter python3 -m aivo.tiivista --alue yksityinen --kuiva
   ```

   Odotettu tulos: rivit `tiivistettaisiin <keskustelun tunnus> viestit 0-N` tai ei mitään, jos tiivistettävää ei ole.

2. **Yksikkö** `/etc/systemd/system/aivo-tiivista-yksityinen.service` (OpenRouter-taustapää):

   ```
   [Unit]
   Description=Aivon keskustelujen tiivistys (yksityinen tila, OpenRouter ZDR)

   [Service]
   Type=oneshot
   User=aivo
   WorkingDirectory=/opt/agentti-pohja
   Environment=PYTHONPATH=/opt/agentti-pohja
   Environment=AIVO_YKSITYINEN_TAUSTAPAA=openrouter
   Environment=AIVO_OPENROUTER_MALLI=qwen/qwen3-235b-a22b-2507
   ExecStart=/usr/bin/python3 -m aivo.tiivista --alue yksityinen
   NoNewPrivileges=true
   ProtectSystem=strict
   ProtectHome=yes
   ReadWritePaths=/var/lib/aivo-yksityinen /var/lib/claude-jono
   PrivateTmp=true
   ```

   Käytä samaa mallia kuin `aivo-chat.service`-tiedostossa (`--openrouter-malli` tai `AIVO_OPENROUTER_MALLI`). `/var/lib/claude-jono` on mukana `ReadWritePaths`-rivillä, koska kulukirjanpito ja päiväkatto ovat siellä. Ilman sitä jokainen kutsu estyy (fail closed). Kaksi ajoa yhtä aikaa on turvallista: toinen ajo huomaa lukon (`tiivista.lukko`) ja päättyy.

   **Paikallinen malli:** jätä pois rivit `AIVO_YKSITYINEN_TAUSTAPAA` ja `AIVO_OPENROUTER_MALLI` sekä `/var/lib/claude-jono`, lisää `[Unit]`-osioon `After=aivo-malli.service` ja `[Service]`-osioon rivit `IPAddressDeny=any` ja `IPAddressAllow=127.0.0.0/8`. Ne rajaavat ajon loopbackiin kuten mallipalvelimen. Käyttäjä on se, joka omistaa `/var/lib/aivo-yksityinen`-kansion.

3. **Ajastin** `/etc/systemd/system/aivo-tiivista-yksityinen.timer` (15 minuutin välein):

   ```
   [Unit]
   Description=Aivon yksityisen tilan tiivistys 15 minuutin valein

   [Timer]
   OnBootSec=10min
   OnUnitActiveSec=15min

   [Install]
   WantedBy=timers.target
   ```

4. **Käyttöönotto ja tarkistus:**

   ```
   systemctl daemon-reload && systemctl enable --now aivo-tiivista-yksityinen.timer
   systemctl list-timers aivo-tiivista-yksityinen.timer
   journalctl -u aivo-tiivista-yksityinen --since "20 min ago" --no-pager
   ```

   Odotettu tulos: ajastin näkyy listassa, ja journaalissa on rivejä `tiivistetty: …` tai ei rivejä, jos tiivistettävää ei ollut. Rivi `epaonnistui: … (TiivistaVirhe)` tai `virhe: …` kertoo yleensä, ettei malli vastannut tai avainta ei voitu lukea. Kulut näkyvät viikkoraportissa: `python3 -m aivo.openrouter_kulut viikko`.

## Keskusteluhistoria mallille

Yksityisen tilan kutsuun liitetään keskustelun viimeiset viestit. Raja riippuu taustapäästä:

| Taustapää | Viestejä enintään | Vakio | Peruste |
|---|---|---|---|
| Paikallinen malli (loopback) | 12 | `yksityinen.HISTORIA_YLARAJA` | i5-8400 käsittelee pitkän kehotteen hitaasti, ja vastauksen aikaraja on 900 s. Pidempi historia ylittäisi aikarajan. |
| OpenRouter | 40 | `yksityinen_or.HISTORIA_YLARAJA` | Etämalli käsittelee pitkän kehotteen sekunneissa. Raja on sama kuin työkalutilassa (`chat.HISTORIA_YLARAJA`). |

OpenRouter-taustapään pahin tapaus: 20 Tonyn viestiä (enintään 4 000 merkkiä kukin), 20 vastausta (enintään 700 tokenia kukin), muisti 16 000 merkkiä, ydinohjeet 20 000 merkkiä ja kehote noin 3 000 merkkiä. Yhteensä noin 160 000 merkkiä eli alle 80 000 tokenia, vaikka arvioitaisiin varovasti 2 merkkiä tokenia kohti. Suositusmallin `qwen/qwen3-235b-a22b-2507` konteksti on 262 144 tokenia, joten kehote mahtuu. Jos vaihdat mallin, tarkista, että sen konteksti on vähintään 100 000 tokenia.

Kehotteen sääntö 2 kertoo mallille, että keskustelun aiemmat viestit ovat käytettävissä, ja sääntö 6 kieltää lupaamasta taustatyötä tai arvioimasta kestoa: yksityisessä tilassa ei ole työkaluja, verkkoa eikä taustaprosesseja.

## Mallin valinta (OpenRouter-taustapää)

OpenRouter-taustapäässä mallin voi valita keskustelukohtaisesti. Sallitut mallit annetaan palvelun asetuksena:

- `AIVO_OPENROUTER_MALLIT` (tai `--openrouter-mallit`) on pilkuin erotettu lista, esimerkiksi `qwen/qwen3-235b-a22b-2507,meta-llama/llama-3.3-70b-instruct`. Ensimmäinen on oletus.
- Jokaisen mallin pitää läpäistä `avoin_malli()`. Jos yksikin on suljettu malli, `:free`-versio, tyhjä kohta tai sama malli kahdesti, palvelu ei käynnisty. Listassa voi olla enintään 10 mallia.
- Vanha `AIVO_OPENROUTER_MALLI` (tai `--openrouter-malli`) toimii edelleen yhden mallin listana. Jos molemmat on annettu, `AIVO_OPENROUTER_MALLIT` voittaa.

Käyttöliittymä:

- Yksityisessä tilassa otsikkorivillä näkyy mallivalitsin, kun listassa on vähintään kaksi mallia. Yhden mallin listalla ja paikallisella taustapäällä valitsinta ei näytetä.
- Valinta tallentuu keskustelun metatietoihin (tiedosto `<keskustelu>.meta.json` keskustelun vieressä, oikeudet 600). Valinnan voi vaihtaa kesken keskustelun: seuraava viesti käyttää uutta mallia ja tallentaa sen.
- Jokaisen vastauksen alla on pieni teksti "Malli: …", ja keskustelutiedoston Aivon rivillä on kenttä `malli`.
- Työkalutilassa valitsinta ei ole, eikä työkalutila hyväksy kenttää `malli` (400).

Palvelin hyväksyy vain listalla olevan mallin. Selaimen lähettämä muu arvo (myös avoin malli, joka ei ole listalla) saa vastauksen 400, eikä viestiä silloin tallenneta. Kaikki mallit kulkevat saman `openrouter_kulut.kutsu`n kautta: kulukirjanpito, päiväkatto ja ZDR-pakotus ovat kaikille malleille yhteiset.

Rajapinta: `GET /api/mallit` palauttaa sallitut mallit (`{"mallit": [...]}`; paikallisella taustapäällä tyhjä lista). `POST /api/uusi` ja `POST /api/viesti` hyväksyvät valinnaisen kentän `malli`, ja vastaus kertoo käytetyn mallin kentässä `malli`.

## Usean mallin yhdistelmät (OpenRouter-taustapää)

Yksityisen tilan vastauksen voi tuottaa useamman mallin yhteistyönä. Yhdistelmä valitaan asetuksella, ja sen voi vaihtaa keskustelukohtaisesti mallivalitsimesta. Oletus on `yksi`, eli toiminta on sama kuin ennen.

Mallivertailu (19-50) ja yhdistelmävertailu (19-54) ovat vielä ajamatta, joten parasta yhdistelmää ei ole mitattu. Siksi kaikki kolme yhdistelmää on toteutettu asetuksina, ja oletus on yksi malli.

| Yhdistelmä | Kutsut järjestyksessä | Roolit |
|---|---|---|
| `yksi` | keskustelun malli | – |
| `tarkastus` | vastaaja kirjoittaa luonnoksen, tarkastaja kirjoittaa lopullisen vastauksen | `vastaaja`, `tarkastaja` |
| `rinnakkais` | kaksi vastaajaa samanaikaisesti, kokoaja yhdistää | `vastaaja`, `vastaaja2`, `kokoaja` |
| `reititys` | reititin luokittelee viestin (kevyt tai vaativa), valittu malli vastaa | `reititin`, `kevyt`, `vaativa` |

Asetukset:

- `AIVO_YKSITYINEN_YHDISTELMA` (tai `--yksityinen-yhdistelma`): oletusyhdistelmä uusille keskusteluille. Tuntematon arvo estää käynnistyksen. Paikallisella taustapäällä vain `yksi` kelpaa.
- `AIVO_YKSITYINEN_ROOLIT` (tai `--yksityinen-roolit`): esimerkiksi `vastaaja:qwen/qwen3-235b-a22b-2507,tarkastaja:meta-llama/llama-3.3-70b-instruct`. Jokaisen roolin mallin pitää olla `AIVO_OPENROUTER_MALLIT`-listalla, joten se on jo läpäissyt `avoin_malli()`-tarkistuksen. Tuntematon rooli, sama rooli kahdesti tai listalta puuttuva malli estää käynnistyksen. Puuttuva rooli käyttää keskustelun mallia.

Rajat (koodin vakioita, `aivo/yhdistelma.py`):

- Enintään 4 mallikutsua per viesti (`MAX_KUTSUT_VIESTI`). Laskuri estää ylimääräisen kutsun ennen kuin mallia kutsutaan.
- Enintään 3 eri mallia per yhdistelmä (`MAX_MALLEJA`).
- Kokonaisaikaraja 240 s per viesti (`AIKARAJA_S`). Jokainen osakutsu saa aikarajaksi enintään jäljellä olevan ajan, eikä uutta kutsua aloiteta, kun aika on lopussa.
- Jokainen osakutsu kulkee `yksityinen_or._kysy_viestit`-funktion ja `openrouter_kulut.kutsu`n kautta: ZDR on pakotettu, kulu kirjataan ja päiväkatto (A1005) koskee jokaista kutsua. Kun katto täyttyy, seuraavia kutsuja ei tehdä.
- Jos osakutsu epäonnistuu, palataan yhden mallin vastaukseen (tarkastuksessa luonnos, rinnakkaisessa ensimmäinen onnistunut vastaus, reitityksessä kevyt malli), ja vastauksen alla näkyy merkintä. Jos yhtään vastausta ei saatu, näytetään virhe kuten yhden mallin tilassa.

Merkinnät: ehdotus-, valinta-, luovutus- ja päätösmerkinnät (`[[EHDOTUS …]]`, `[[VALINNAT: …]]`, `[[LUOVUTUS]]`, `[[PAATOS n]]`) tulevat lopulliseen vastaukseen vain ensisijaisen vastaajan luonnoksesta sanasta sanaan. Tarkastaja ja kokoaja eivät näe niitä syötteessään, ja niiden kirjoittamat merkinnät poistetaan. Näin tarkastaja ei voi lisätä, muuttaa eikä pudottaa ehdotuksia. `[[VALINNAT: …]]` siirretään aina vastauksen loppuun.

Käyttöliittymä:

- Mallivalitsimessa ovat mallit ja niiden perässä vaihtoehdot "Yhdistelmä: tarkastus" ja niin edelleen. Mallin valinta tarkoittaa yhdistelmää `yksi`. Valinta tallentuu keskustelun metatietoihin (`yhdistelma`-kenttä).
- Yhdistelmän vastauksen alla on pieni teksti "Mallit: a, b · kutsuja: 2". Mahdollinen paluumerkintä näkyy sen alla.
- Linkki "Näytä luonnos" näyttää tarkastajan saaman luonnoksen tai rinnakkaiset vastaukset A ja B. Teksti asetetaan `textContent`-ominaisuudella. Luonnosta ei tallenneta keskustelutiedostoon.

Rajapinta: `GET /api/mallit` palauttaa OpenRouter-taustapäällä myös kentät `yhdistelmat` ja `yhdistelma` (oletus). `POST /api/uusi` ja `POST /api/viesti` hyväksyvät kentän `yhdistelma`. Muu arvo saa vastauksen 400, eikä viestiä silloin tallenneta. Työkalutila ei hyväksy kenttää (400). Yhdistelmän vastaus sisältää kentät `yhdistelma`, `mallit`, `kutsuja` sekä tarvittaessa `luonnos` ja `merkinta`. Keskustelutiedoston Aivon rivillä ovat `yhdistelma`, `mallit`, `kutsuja` ja `merkinta`.

Kulut: tarkastus ja reititys tekevät 2 kutsua ja rinnakkais 3 kutsua viestiä kohden. Tarkastajan ja kokoajan syöte sisältää luonnoksen, joten se on yhden mallin kutsua pidempi.

## Rajat ja avoimet asiat

- Yksityinen tila ja työkalutila ovat samassa prosessissa. Erottelu perustuu siihen, että työkalutilan Claude-kutsu ajetaan toisena käyttäjänä (kohta 2, socket) eikä saa työkaluja (`--tools ""`). Jos `AIVO_TYOKALU_SOCKET`-asetusta ei käytetä, erottelu on vain koodin tasolla, ei tiedosto-oikeuksilla.
- Ensimmäinen vastaus yksityisessä tilassa on hidas (ks. nopeudet).
- Aivon vastauksen lähdeviitteet tarkistetaan kuten työkalutilassa.
