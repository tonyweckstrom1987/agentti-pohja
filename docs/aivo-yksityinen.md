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

   **Huomautus:** Tällä rivillä hyväksyntä chat-sivulla ei toimi (katso `docs/aivo-chat.md`, Kirjoitusoikeus). Asetus on Tonyn avoin päätös.

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

## Luovutus työkalutilaan

Tony voi antaa jokaiselle yksityisen tilan vastaukselle palautteen (Hyvä tai Väärin ja korjaus). Korjaukset tallentuvat kansioon `/var/lib/aivo-yksityinen/korjaukset` (0700), tulevat seuraavan viestin kehotteeseen datana ja toistuessaan johtavat ydinohje-ehdotukseen, jonka Tony hyväksyy itse. Tarkemmin: `docs/aivo-chat.md`, kohta Palaute ja korjaukset yksityisessä tilassa.

Yksityisessä tilassa sovitun tekstin voi luovuttaa työkalutilaan vain Tonyn hyväksynnällä. Aivo voi ehdottaa luovutusta lohkolla `[[LUOVUTUS]] … [[/LUOVUTUS]]`, ja Tony voi luovuttaa minkä tahansa viestin painikkeella. Ehdotukset ja loki luovutetuista teksteistä ovat kansiossa `/var/lib/aivo-yksityinen/luovutukset` (0700). Hyväksytty teksti kopioidaan kirjaston kansioon `saapuneet/`. Kehote käskee noudattamaan ydinohjeiden luovutussääntöjä; jos niitä ei ole, Aivo ei ehdota luovutusta ennen kuin Tony pyytää. Tarkemmin: `docs/aivo-chat.md`, kohta Luovutus yksityisestä tilasta työkalutilaan.

## Keskustelujen tiivistys (komento)

Komento `python3 -m aivo.tiivista --alue yksityinen` tiivistää yksityisen alueen keskustelut. Keskustelu tiivistetään vasta, kun sen viimeisestä viestistä on 30 minuuttia. Pitkä keskustelu pilkotaan osiin, ja jokainen osa tallentuu omaksi tiivistelmäkseen kansioon `tiivistelmat`. Valinta `--kuiva` listaa tiivistettävät alueet ilman mallikutsua.

Mallin lähde valitaan samalla asetuksella kuin chatissa: `AIVO_YKSITYINEN_TAUSTAPAA` (tai `--taustapaa`), oletus `loopback`.

- **loopback**: paikallinen malli samassa loopback-osoitteessa kuin chatissa (`--malli-url` tai `AIVO_MALLI_URL`, oletus `http://127.0.0.1:8081`). Muuta osoitetta ei hyväksytä. Kulukattoa ei ole, koska paikallinen malli ei maksa mitään.
- **openrouter**: OpenRouterin avoin malli nollasäilytyksellä (ZDR). Malli on sama kuin chatissa (`AIVO_OPENROUTER_MALLI`), ja avain luetaan samasta tiedostosta (`AIVO_OPENROUTER_AVAIN`, oletus `/var/lib/aivo-yksityinen/openrouter-avain`). Kutsu kulkee vain funktion `yksityinen_or.kysy_tiivistys` ja sitä kautta `openrouter_kulut.kutsu`n läpi. Pyynnössä on aina `provider.zdr=true` ja `data_collection=deny`, suljettu malli hylätään ennen kutsua, ja kulut kirjataan tiedostoon `/var/lib/claude-jono/openrouter-kulut.jsonl` tehtävän tunnisteella `aivo-yksityinen-tiivistys`. Muita verkkokohteita ei ole.

Kulurajat OpenRouter-taustapäällä:

- Enintään 10 tiivistyskutsua vuorokaudessa (Europe/Helsinki), kuten työkalutilassa. Kutsu varataan ennen lähetystä. Kun raja täyttyy, ajo lopettaa ja kirjaa rivin `lopetus: paivakatto 10/10 taynna`.
- Kulut lasketaan samaan konttikohtaiseen 10 €/vrk päiväkattoon kuin chatin kutsut (A1005). Jos katto on täynnä, kutsua ei tehdä, ajo lopettaa (`lopetus: kulukatto taynna`) eikä yrityskertoja kulu.

Tiivistys tarkistaa vain kansiot, joita se käyttää: `chat` (luku) sekä `tiivistelmat` ja `indeksi` (kirjoitus). Niiden pitää olla palvelun käyttäjän omia, oikeuksiltaan 0700 eikä niissä saa olla linkkejä, alikansioita eikä toisen käyttäjän tiedostoja. Muuten ajo pysähtyy virheeseen `alueen tarkistus epaonnistui (AlueVirhe)`. Muita yksityisen alueen kansioita tiivistys ei avaa eikä tarkista. Siksi hylättyjen ehdotusten kansio `ehdotukset/hylatyt/` ja ydinohjeiden git-repo (`ydinohjeet/.git`) eivät estä tiivistystä.

Keskustelun sisältö ei päädy lokiin (`indeksi/tiivista.log`), kulukirjanpitoon eikä virheviesteihin. Lokissa on vain keskustelun tunnus, viestien numerot ja virheen tyyppi.

### Tiivistelmät yksityisen tilan kehotteeseen

Chat liittää yksityisen alueen tiivistelmät yksityisen tilan kehotteeseen vain, kun palvelulla on asetus `AIVO_YKSITYISET_TIIVISTELMAT_KAYTOSSA=1` (tai valitsin `--yksityiset-tiivistelmat-kaytossa`). Oletuksena ominaisuus on pois päältä. Kansio on oletuksena `--yksityinen-data`-kansion viereinen `tiivistelmat` (eli `/var/lib/aivo-yksityinen/tiivistelmat`), ja sen voi vaihtaa asetuksella `AIVO_YKSITYISET_TIIVISTELMAT`.

- Mekanismi ja yläraja ovat samat kuin työkalutilassa (`aivo/tiivistelmat_kehote.py`, enintään 60 kt). Kysymykseen osuvat ja uusimmat tiivistelmät tulevat ensin.
- Erottelu: työkalutila lukee vain kansion `AIVO_TIIVISTELMAT` (oletus `/var/lib/aivo/tiivistelmat`), ja yksityinen tila lukee vain yksityisen alueen kansion. Palvelu ei käynnisty, jos molemmat asetukset osoittavat samaan kansioon.
- Paikallisella mallilla 60 kt pidentää kehotteen käsittelyä selvästi (katso Keskusteluhistoria mallille). Kytkin on tarkoitettu ensisijaisesti OpenRouter-taustapäälle.

Käyttöönotto: lisää `aivo-chat.service`-tiedostoon rivi `Environment=AIVO_YKSITYISET_TIIVISTELMAT_KAYTOSSA=1` ja käynnistä palvelu uudelleen (`systemctl daemon-reload && systemctl restart aivo-chat`).

### Muistihaku: kysymykseen liittyvät kohdat kehotteen alkuun

Kun tiivistelmät on kytketty päälle (yllä), chat hakee jokaisella viestillä Tonyn viestin sanoilla alueen hakuindeksistä (`aivo/haku.py`, SQLite FTS5). Osumat tulevat kehotteeseen ensimmäiseksi muistiosaksi (otsikko `HAKUOSUMAT`). Jokaisen osuman otsikossa on lähdeviite muodossa `[lähde: tiivistelmat/<tunnus>.json]`, ja sama viite kelpaa vastauksen lähteeksi.

- **Alueet eivät sekoitu.** Yksityinen tila hakee vain yksityisen alueen indeksistä (`/var/lib/aivo-yksityinen/indeksi/tiivistelmat.sqlite`) ja työkalutila vain työkalualueen indeksistä (`/var/lib/aivo/indeksi/tiivistelmat.sqlite`). Indeksin paikka johdetaan tiivistelmäkansiosta (sen sisarkansio `indeksi`). Jos kansion nimi ei ole `tiivistelmat`, hakua ei tehdä. Osuman teksti luetaan aina saman alueen tiivistelmäkansiosta, joten väärään paikkaan kopioitu indeksi ei voi tuoda toisen alueen sisältöä.
- **Ei verkkoa eikä mallikutsuja.** Haku on paikallinen SQLite-kysely, joka avataan vain luku -tilassa.
- **Koko.** Enintään 5 osumaa (yksi tiivistelmää kohti) ja enintään 24 000 tavua. Paikallisella mallilla raja on neljännes muistirajasta (4 000). Osuman kohdat järjestetään kysymykseen osuvimmasta alkaen, ja kohta, joka ei mahdu, jää pois. Osumat kuuluvat tiivistelmien 60 kt:n rajaan, eikä samaa tiivistelmää toisteta tiivistelmien osiossa. Paikallisella taustapäällä kirjaston osuus pienenee hakuosion verran, joten muistiraja (16 000 merkkiä) ei kasva. OpenRouter-taustapäällä hakuosumat lasketaan mallin kontekstin mukaiseen muistirajaan (`aivo/kontekstiraja.py`), eivät sen päälle: osumille enintään neljännes rajasta, ja agenttidata, Tonyn korjaukset ja kirjasto saavat sen, mitä jää jäljelle.
- **Virheet.** Tyhjä tai pelkistä välimerkeistä koostuva viesti, puuttuva tai rikkinäinen indeksi tai liian laajat oikeudet indeksikansiossa tarkoittavat, ettei osumia tule. Keskustelu jatkuu normaalisti.
- **Indeksin päivitys.** Tiivistysajo (`python3 -m aivo.tiivista --alue yksityinen` tai `--alue tyokalu`) rakentaa indeksin uudelleen ajon lopussa, jos se tallensi tiivistelmiä, jos indeksiä ei vielä ole tai jos tiivistelmäkansio on muuttunut indeksin jälkeen (esimerkiksi `aivo.tiivistelma korjaa` tai `poista`). Journaliin tulee rivi `hakuindeksi: N tiivistelmaa, M palaa`.
- **Journal.** Chat kirjaa jokaisesta viestistä rivin `muistihaku yksityinen: N osumaa` tai `muistihaku tyokalu: N osumaa`. Rivillä ei ole tunnuksia eikä sisältöä.

Käyttöönotto kontissa 122 (Tony ajaa kontin päätteessä, `pct enter 122`; ota ensin snapshot). Jos jokin komento ei tulosta odotettua riviä, pysähdy.

1. Palvelun käyttäjä ja kytkin:

   ```
   systemctl show -p User -p Environment aivo-chat
   ```

   Odotettu tulos: rivi `User=aivo` ja `Environment=`-rivillä `AIVO_YKSITYISET_TIIVISTELMAT_KAYTOSSA=1`. Jos käyttäjä on eri, käytä sitä nimeä alla. Jos kytkin puuttuu, tee ensin yllä oleva kohta Tiivistelmät yksityisen tilan kehotteeseen: ilman kytkintä hakua ei tehdä.

2. FTS5 on käytettävissä:

   ```
   runuser -u aivo -- python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)'); print('fts5 ok')"
   ```

   Odotettu tulos: `fts5 ok`.

3. Päivitä koodi `/opt/agentti-pohja`-kansioon samalla tavalla kuin edellisissä Aivon käyttöönotoissa ja tarkista, että muutos on mukana:

   ```
   grep -c hakuosio /opt/agentti-pohja/aivo/chat.py
   ```

   Odotettu tulos: luku 2 tai suurempi. Luku 0 tarkoittaa, että koodi on vanha.

4. Rakenna indeksit kerran käsin (muuten ne syntyvät seuraavassa tiivistysajossa):

   ```
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.haku rakenna --alue yksityinen
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.haku rakenna --alue tyokalu
   ```

   Odotettu tulos: kummastakin rivi `indeksi: N tiivistelmaa, M palaa`. Rivin `virhe: alue ei ole kayttokelpoinen (AlueVirhe)` syy on yleensä kansion omistaja tai oikeudet (`stat -c '%a %U' /var/lib/aivo-yksityinen/indeksi` → `700 aivo`). Jos N on 0, tiivistelmiä ei vielä ole, eikä haku voi löytää mitään.

5. Koehaku sanalla, jonka tiedät esiintyvän jossain tiivistelmässä (tulostaa vain tunnukset, ei sisältöä):

   ```
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.haku hae --alue yksityinen muutto
   ```

   Vaihda sana `muutto` omaan sanaasi. Odotettu tulos: vähintään yksi rivi `<tunnus>  pala N  pisteet …`. Tyhjä tuloste tarkoittaa, ettei sanaa löytynyt tai ettei indeksiä ole (kohta 4). Kokeile toista sanaa ennen kuin jatkat.

6. Käynnistä chat uudelleen:

   ```
   systemctl restart aivo-chat && systemctl is-active aivo-chat
   ```

   Odotettu tulos: `active`.

7. Lähetä selaimessa yksityisessä tilassa viesti, jossa on kohdan 5 sana. Aja sen jälkeen:

   ```
   journalctl -u aivo-chat --since "5 min ago" --no-pager | grep muistihaku
   ```

   Odotettu tulos: rivi `muistihaku yksityinen: 1 osumaa` (tai suurempi luku). Rivi `0 osumaa` tarkoittaa, että haku ajettiin mutta ei löytänyt mitään. Ei riviä lainkaan: kytkin puuttuu (kohta 1) tai koodi on vanha (kohta 3).

### Tiivistyksen ajastin (ohje, ei asennettu)

Repossa ei ole tälle valmista yksikköä, eikä tämä muutos asenna mitään. Tony luo tiedostot itse, jos haluaa ajastuksen. Malli on sama kuin työkalutilan `aivo-tiivista.timer` (`docs/aivo-chat.md`). Alla on kaksi vaihtoehtoa: OpenRouter-taustapää (kontti, jossa chat käyttää OpenRouteria) ja paikallinen malli.

Ajastimen käyttäjän pitää olla sama kuin `aivo-chat.service`-palvelun `User=` (kontissa `aivo`). Syy: yksityiset kansiot ja avaintiedosto tarkistetaan palvelun käyttäjän omiksi (0700 ja 0600). Tarkista käyttäjä: `systemctl show -p User aivo-chat`.

1. **Kuiva-ajo** (ei mallikutsua). Kuiva-ajo ei luo kansioita. Tuoreessa asennuksessa kansioita `tiivistelmat` ja `indeksi` ei vielä ole, joten luo ne ensin palvelun käyttäjälle (kontissa `aivo`, vaihda tarvittaessa):

   ```
   install -d -m 700 -o aivo -g aivo /var/lib/aivo-yksityinen/tiivistelmat /var/lib/aivo-yksityinen/indeksi
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja AIVO_YKSITYINEN_TAUSTAPAA=openrouter python3 -m aivo.tiivista --alue yksityinen --kuiva
   ```

   Odotettu tulos: rivit `tiivistettaisiin <keskustelun tunnus> viestit 0-N` tai ei mitään, jos tiivistettävää ei ole. Jos kansiot puuttuvat, ajo päättyy paluuarvoon 1 ja virheeseen `kuiva-ajo ei luo kansioita; puuttuu: tiivistelmat, indeksi`, eikä mitään luoda. Kansiota `siirrot` tiivistys ei tarvitse. Varsinainen ajo (ilman `--kuiva`) luo puuttuvat käyttämänsä kansiot itse (0700).

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
   Description=Aivon yksityisen tilan tiivistys 15 minuutin valein (edellisen ajon paattymisesta)

   [Timer]
   OnActiveSec=1min
   OnBootSec=10min
   OnUnitInactiveSec=15min

   [Install]
   WantedBy=timers.target
   ```

   Palvelu on `Type=oneshot`, joten toistoväli lasketaan edellisen ajon päättymisestä (`OnUnitInactiveSec`). `OnUnitActiveSec` ei sovi tähän: kontissa 122 ajastin ajoi kerran, mutta seuraavaa ajoa ei ajastettu (`NEXT` `-`). `OnActiveSec=1min` käynnistää ensimmäisen ajon minuutin kuluttua ajastimen käynnistyksestä (`enable --now`, `restart`), joten toistovälillä on aina lähtöpiste. Testi `test/test_ajastin_toisto.py` hylkää repon ajastinmallin, jossa toisto on vain `OnUnitActiveSec`.

4. **Käyttöönotto ja tarkistus:**

   ```
   systemctl daemon-reload && systemctl enable --now aivo-tiivista-yksityinen.timer
   systemctl list-timers aivo-tiivista-yksityinen.timer --no-pager
   journalctl -u aivo-tiivista-yksityinen --since "20 min ago" --no-pager
   ```

   Odotettu: `list-timers` näyttää ajastimen, ja `NEXT`-sarakkeessa on aika enintään 15 minuutin päässä myös ensimmäisen ajon jälkeen. `NEXT` `-` tarkoittaa, ettei seuraavaa ajoa ole ajastettu: tarkista, että `[Timer]`-osiossa on `OnUnitInactiveSec` eikä `OnUnitActiveSec`. Journaalissa on rivejä `tiivistetty: …` tai ei rivejä, jos tiivistettävää ei ollut. Rivi `epaonnistui: … (TiivistaVirhe)` tai `virhe: …` kertoo yleensä, ettei malli vastannut tai avainta ei voitu lukea. Kulut näkyvät viikkoraportissa: `python3 -m aivo.openrouter_kulut viikko`.

## Keskusteluhistoria mallille

Yksityisen tilan kutsuun liitetään keskustelun viimeiset viestit. Raja riippuu taustapäästä:

| Taustapää | Viestejä enintään | Vakio | Peruste |
|---|---|---|---|
| Paikallinen malli (loopback) | 12 | `yksityinen.HISTORIA_YLARAJA` | i5-8400 käsittelee pitkän kehotteen hitaasti, ja vastauksen aikaraja on 900 s. Pidempi historia ylittäisi aikarajan. |
| OpenRouter | 200 | `yksityinen_or.HISTORIA_YLARAJA` | Etämalli käsittelee pitkän kehotteen sekunneissa. Varsinainen raja on mallin konteksti (ks. alla). |

Yhden viestin raja on 16 000 merkkiä (`chat.VIESTI_YLARAJA`). Selain näyttää merkkilaskurin ja estää liian pitkän viestin lähettämisen. Se ei katkaise tekstiä, ja palvelin hylkää liian pitkän viestin virheellä. Historian viestien yhteenlaskettu pituus on rajattu: paikallisessa taustapäässä 24 000 merkkiä ja työkalutilassa 80 000 merkkiä. Uusin viesti säilyy aina kokonaan. Vanhemmista viesteistä säilyy alku, ja loppuun tulee merkintä lyhennyksestä (`yksityinen.lyhenna_historia`).

### Kehotteen koko OpenRouter-taustapäällä (A1106)

OpenRouter-taustapäällä muisti, agenttidata ja keskusteluhistoria täyttävät kehotteen mallin kontekstiin asti (`aivo/kontekstiraja.py`). Paikallisen taustapään rajat (muisti 16 000 merkkiä, historia 12 viestiä ja 24 000 merkkiä, vastaus 700 tokenia) eivät muutu.

- **Konteksti** on mallikohtainen vakio taulukossa `kontekstiraja.KONTEKSTIT`. Lähde on OpenRouterin julkiset rajapinnat `/api/v1/models` ja `/api/v1/endpoints/zdr` (haettu 25.11.2026). Arvo on konteksti, jota vähintään puolet mallin ZDR-endpointeista tukee, enintään 262 144 tokenia. Tuntematon malli saa varovaisen oletuksen 32 768 tokenia.
- **Varaukset**: vastaukselle varataan `yksityinen_or.MAX_TOKENIA` (8 000) tokenia, pienellä mallilla enintään neljännes kontekstista. Lisäksi varataan 10 %:n turvamarginaali. Yhdistelmässä raja lasketaan pienimmän mukana olevan mallin mukaan, ja roolisyötteelle (luonnokset) varataan noin 25 000 merkkiä.
- **Merkit ja tokenit**: arvio on 2,5 merkkiä tokenia kohti. Suomen teksti on noin 3 merkkiä tokenia kohti, mutta koodi ja luettelot pilkkoutuvat tiheämmin. Arvio on tarkoituksella alakanttiin, koska kontekstin ylitys kaataisi koko vastauksen.
- **Hinta**: kehotteen arvioitu ylin hinta pidetään enintään kulukatkaisimen kiinteän varauksen (0,50 USD) suuruisena. Päiväkatto (10 €/vrk) ja varaus eivät muutu.
- **Muistin enimmäiskoko (A1123)**: muistiosio on enintään `AIVO_YKSITYINEN_MUISTI_MAX` merkkiä (valitsin `--yksityinen-muisti-max`, oletus 40 000, sallittu 4 000–1 000 000). Raja on pienempi kahdesta: mallin kontekstin mukainen raja ja asetus (`yksityinen_or.muistin_merkkiraja`). Asetus voi vain pienentää kehotetta. Muu arvo estää palvelun käynnistymisen. Ydinohjeet ja säännöt eivät kuulu tähän rajaan, vaan niillä on oma 20 000 merkin raja. Sama raja koskee itsetarkistusta (katso Itsetarkistus).
- **Päättelyn yläraja**: ajattelevan mallin päättely on enintään `AIVO_YKSITYINEN_PAATTELY_MAX` tokenia (valitsin `--yksityinen-paattely-max`, oletus 2 000, sallittu 0–6 000, 0 = reasoning-parametria ei lähetetä). Raja on aina pienempi kuin vastauksen `max_tokens`. Jos vastaus katkeaa silti ennen tekstiä, chat tekee yhden uusinnan ilman päättelyä tai pienemmällä tasolla. Uusintaa ei tehdä, jos se olisi sama pyyntö (effort-malli oletusrajalla). Katso `aivo-yksityinen-openrouter.md`, osio "Päättelyn yläraja ja uusinta".
- **Täyttöjärjestys (A1123)**: ensin muistihaun osumat (enintään neljännes muistirajasta), sitten Tonyn korjaukset (enintään 3 000 merkkiä), agenttidata (tuoreimmat päätökset ensin, enintään puolet muistirajasta), kirjasto (kysymykseen osuvat tiedostot ensin, sitten uusin muutosaika ensin) ja lopuksi tiivistelmät (enintään 60 kt; niille varataan oma osuus, enintään 10 % muistirajasta, ennen kirjastoa). Ennen A1123:a agenttidata oli ennen korjauksia. Korjaukset siirrettiin päätösten edelle, koska ne oikaisevat Aivon aiempia virheitä ja ovat lyhyitä. Pienellä rajalla iso agenttidata ei saa syrjäyttää niitä. Muistihaun osumat ovat ensimmäisinä, koska ne liittyvät juuri tähän kysymykseen. Historialle jää aina vähintään 25 % jäljellä olevasta tilasta, ja uusin Tonyn viesti mahtuu aina kokonaan, joten muisti ei syrjäytä keskustelua kokonaan. Jos uusin viesti ei pienellä mallilla mahdu edes yksin, muistia ei liitetä lainkaan.
- **Näkyvyys**: jokaisesta viestistä kirjataan journaliin rivi `yksityinen muisti: N merkkia, raja R (kontekstiraja K, asetus A)`. Jokaisesta kutsusta kirjataan rivi `yksityinen_or: kehote N merkkia, arvio T tokenia …, arvioitu hinta enintaan X USD` ja vastauksen jälkeen `yksityinen_or: toteutunut prompt_tokens=…, completion_tokens=…, cached_tokens=…`. Kenttä `cached_tokens` näkyy vain, jos OpenRouter ilmoittaa sen (katso `docs/aivo-yksityinen-openrouter.md`, kohta Välimuisti). Riveillä ei ole sisältöä. Kulunäkymä (`GET /api/kulut`) näyttää chatin kehotteen keskimääräisen ja suurimman koon tokeneina, keskimääräisen kulun kutsua kohti ja mallien kehoterajat.

Esimerkki: mallilla `qwen/qwen3-235b-a22b-2507` kehotteeseen mahtuu noin 228 000 tokenia eli noin 570 000 merkkiä. Täysi kehote maksaa arviolta enintään noin 0,04 USD.

Kustannusesimerkki (A1123): mallilla `qwen/qwen3.8-2.4t-a95b` (ZDR-ylähinta 2,0 USD sisään ja 6,0 USD ulos miljoonalta tokenilta) kontekstiraja täytti kehotteen noin 210 000 merkkiin eli noin 84 000 tokeniin. Viesti maksoi silloin noin 0,18 USD. Kun muisti on enintään 40 000 merkkiä, kehote on tavallisessa keskustelussa noin 50 000–65 000 merkkiä (kehote 5 000, ydinohjeet enintään 20 000, muisti 40 000 ja lyhyt historia). Se on noin 20 000–26 000 tokenia, ja viesti maksaa noin 0,05–0,06 USD, kun vastaus on 1 500 tokenia. Pitkä keskustelu kasvattaa kehotetta edelleen, koska historian raja seuraa yhä mallin kontekstia.

Kehotteen sääntö 2 kertoo mallille, että keskustelun aiemmat viestit ovat käytettävissä, ja sääntö 6 kieltää lupaamasta taustatyötä tai arvioimasta kestoa: Aivolla ei ole verkkoa, eikä se voi ajaa ohjelmia itse. Agenttitehtävää se voi ehdottaa säännön 8 mukaisesti. Säännöt 9–11 koskevat omaa rakennetta ja kuluja (9), `[[SYVENNA]]`-vihjettä (10) ja `tonyn-korjaukset`-tiedostoa (11). Testi `aivo/test_yksityinen_saantonumerot.py` varmistaa, että säännöt on numeroitu 1..N ilman aukkoja ja kaksoisnumeroita kummassakin taustapäässä.

## Paikallisen vartijamallin kehote ja arviointi (valmiina, ei kytketty)

Pieni paikallinen malli (3–4B, llama-server loopbackissa) arvioi vain kontekstiriskin. Muodolliset tiedot (sähköpostit, puhelinnumerot, IP-osoitteet, henkilötunnukset, IBAN, avaimet) hoitaa mekaaninen tarkistus, ja ne näkyvät mallille paikkamerkkeinä.

- `aivo/vartija_kehote.py`: englanninkielinen kehote, kuusi keksittyä suomenkielistä esimerkkiä (myös kiertoilmaus, kokonaiskuva ja injektio), vastaus `{"riski": "ei"|"pieni"|"suuri", "syy": enintään 15 sanaa}`. Pyyntö käyttää llama-serverin `response_format`-kenttää JSON-skeemalla ja lämpötilaa 0. Vastaus tarkistetaan silti: kelvoton JSON, ylimääräinen avain, tuntematon riski tai liian pitkä syy antaa tilan `ei-tarkistanut`.
- Kutsu menee vain osoitteeseen, jonka `yksityinen.sallittu_malli_osoite` hyväksyy. Välityspalvelinasetukset ohitetaan, eikä uudelleenohjauksia seurata.
- `aivo/vartija_arvio.py`: arviointiajuri. Se tarkistaa ensin `/health`-osoitteen. Jos malli ei vastaa, ajuri kertoo sen ja lopettaa paluukoodilla 2 kutsumatta mitään. Muuten se lähettää aineiston tekstit ja tulostaa osuvuuden, väärät hälytykset, suurten riskien ohitukset ja sekaannustaulun.

Ajo kontissa, kun mallipalvelu on käynnissä:

```
python3 -m aivo.vartija_arvio                       # oletusaineisto aivo/testidata/vartija_kehote_arvio.json
python3 -m aivo.vartija_arvio --kahdesti --tulos /tmp/vartija-arvio.json
python3 -m aivo.vartija_arvio --aineisto muu.json --tyypeista
```

`--kahdesti` ajaa jokaisen tekstin toiseen kertaan käännetyllä esimerkkijärjestyksellä ja laskee erimieliset. `--tyypeista` johtaa odotuksen totuustiedon `tyypit`-listasta kaksiarvoisesti (tyhjä = ei riskiä). Kytkentä vuotovartijaan on oma muutoksensa.

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
| `tiimi` | kevyt luokittelee viestin (arkinen tai vaativa); arkiseen vastaa keskustelija, vaativassa keskustelija antaa tehtävän ajattelijalle ja muotoilee vastauksen | `keskustelija`, `ajattelija`, `kevyt` |

Asetukset:

- `AIVO_YKSITYINEN_YHDISTELMA` (tai `--yksityinen-yhdistelma`): oletusyhdistelmä uusille keskusteluille. Tuntematon arvo estää käynnistyksen. Paikallisella taustapäällä vain `yksi` kelpaa.
- `AIVO_YKSITYINEN_ROOLIT` (tai `--yksityinen-roolit`): esimerkiksi `vastaaja:qwen/qwen3-235b-a22b-2507,tarkastaja:meta-llama/llama-3.3-70b-instruct`. Jokaisen roolin mallin pitää olla `AIVO_OPENROUTER_MALLIT`-listalla, joten se on jo läpäissyt `avoin_malli()`-tarkistuksen. Tuntematon rooli, sama rooli kahdesti tai listalta puuttuva malli estää käynnistyksen. Puuttuva rooli käyttää keskustelun mallia.

- `AIVO_YKSITYINEN_AJATTELIJA_AIKA` (tai `--yksityinen-ajattelija-aika`): tiimin ajattelijan aikaraja sekunteina, kokonaisluku 10–3600, oletus 300. Muu arvo estää käynnistyksen. Asetus voi vain lyhentää ajattelijan kutsua (katso tiimin kuvaus alla).

### Tiimi (A1040, A1041)

Tiimissä on kolme roolia, eikä Tonyn tarvitse vaihtaa malleja itse.

1. Kevyt malli luokittelee Tonyn viestin sanalla `arkinen` tai `vaativa`. Vaativia ovat esimerkiksi tehtävän pilkkominen, rakennusohje, suunnitelma ja iso päätös. Epäselvä luokka tai virhe tarkoittaa arkista viestiä, ja vastauksen alla näkyy merkintä.
2. Arkiseen viestiin vastaa keskustelija yksin samalla kehotteella kuin yhden mallin tilassa (2 kutsua).
3. Vaativassa viestissä (4 kutsua):
   - Keskustelija kirjoittaa ajattelijalle tiiviin englanninkielisen tehtävän ja ottaa siihen mukaan tarvittavan kontekstin.
   - Ajattelija näkee vain oman ohjeensa ja tehtävän. Muisti, ydinohjeet ja keskusteluhistoria eivät mene sille sellaisenaan.
   - Keskustelija muotoilee ajattelijan vastauksen suomeksi Tonylle.

Ajattelijan aikaraja on pienin seuraavista: asetus (oletus 300 s), jäljellä oleva kokonaisaika miinus keskustelijan varaus (`AJATTELIJA_VARAUS_S`, 60 s) ja osakutsun yläraja (`yksityinen_or.VASTAUS_AIKARAJA_S`, 120 s; yhden mallin chatin oma raja `CHAT_AIKARAJA_S` ei koske yhdistelmiä). Kokonaisaikaraja 240 s on siis aina voimassa. Jos ajattelija ei ehdi tai sen kutsu epäonnistuu, keskustelija vastaa itse. Vastauksen loppuun lisätään silloin lause "Syvempi analyysi ei ehtinyt aikarajassa, joten vastasin itse." (tai "ei onnistunut"), ja sama teksti näkyy merkintänä. Taustalla jatkuva ajo on erillinen tehtävä.

Merkinnät tiimissä tulevat vain keskustelijan lopullisesta vastauksesta. Tehtävänannosta ja ajattelijan vastauksesta poistetaan merkinnät, ja jäljelle jääneet `[[`- ja `]]`-merkit rikotaan, ennen kuin teksti menee seuraavalle mallille. Ajattelija ei siis voi lisätä ehdotuksia, valintoja, luovutuksia eikä päätöksiä. Jos muotoilukutsu epäonnistuu, näytetään ajattelijan vastaus ilman merkintöjä. "Näytä luonnos" näyttää tehtävänannon ja ajattelijan vastauksen.

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

Kulut: tarkastus ja reititys tekevät 2 kutsua ja rinnakkais 3 kutsua viestiä kohden. Tiimi tekee arkisessa viestissä 2 ja vaativassa 4 kutsua. Tarkastajan ja kokoajan syöte sisältää luonnoksen, joten se on yhden mallin kutsua pidempi.

## Itsetarkistus (OpenRouter-taustapää)

Asetus `AIVO_YKSITYINEN_ITSETARKISTUS=paalla` (tai `--yksityinen-itsetarkistus paalla`) ottaa käyttöön vastauksen itsetarkistuksen. Oletus on `pois`. Muu arvo estää käynnistyksen, ja asetus vaatii yksityisen tilan ja OpenRouter-taustapään. Koodi on tiedostossa `aivo/itsetarkistus.py`, ja kutsut kulkevat funktion `yksityinen_or.tarkista_vastaus` kautta.

1. Tarkistus tehdään vain, jos vastauksessa on lukuja, päivämääriä, A-päätösviittauksia (esim. A984) tai väitteitä Aivon rakenteesta. Muut vastaukset eivät maksa mitään.
2. Tarkastajamalli saa vastauksen ja sen lähteet: kehotteen järjestelmäosan (ydinohjeet, muisti, tiivistelmät, agenttidata) ja keskustelun viimeiset viestit. Lähteiden järjestys on: keskustelun viimeiset viestit, muistiosat, joihin vastaus viittaa (`[lähde: ...]`), ja muistihaun osumat, kehotteen alku (säännöt ja ydinohjeet) ja lopuksi muu muisti. Lähteiden enimmäispituus lasketaan tarkastajamallin kontekstista samalla tavalla kuin kehotteen koko (A1106, `yksityinen_or.tarkastajan_lahderaja`), ja se on enintään muistin enimmäiskoko lisättynä kehotteen alulla ja keskustelun viimeisillä viesteillä (A1123). Korjauskutsu käyttää samaa, jo rajattua muistia. Näin pitkässäkin kehotteessa tarkastaja näkee ne kohdat, joihin vastaus nojaa. Tarkastaja palauttaa JSON-listan ristiriidoista (`kohta`, `syy`). Malli on roolin `tarkastaja` malli (`AIVO_YKSITYINEN_ROOLIT`), muuten keskustelun malli. Mallin pitää läpäistä `avoin_malli()`-tarkistus.
3. Jos ristiriitoja on, vastaaja saa korjata vastauksen kerran. Korjattua vastausta ei tarkisteta uudelleen. Vastauksen alla näkyy merkintä, esimerkiksi "tarkistus: 1 kohta epävarma; vastaus korjattiin kerran", ja kohdat luettelona. "Näytä alkuperäinen vastaus" näyttää korjaamattoman vastauksen, ja se tallentuu myös keskustelutiedostoon (`tarkistus.alkuperainen`). Tarkistus ei siis muuta vastausta hiljaa.
4. Merkinnät (`[[EHDOTUS ..]]`, `[[VALINNAT: ..]]` ym.) tulevat vain alkuperäisestä vastauksesta. Tarkastajan teksti on dataa: siitä poistetaan merkinnät ja ohjausmerkit.

Rajat:

- Kutsut kulkevat samaa ZDR-reittiä (`openrouter_kulut.kutsu`, lippu `yksityinen_chat=True`) ja kuluttavat yhteistä päiväkattoa. Kulukirjanpidossa tehtävä on `aivo-yksityinen-tarkistus`, ja kulunäkymä laskee sen chatiksi.
- Jos päiväkatto on täynnä, vastaus näytetään ilman tarkistusta ja merkintä kertoo sen. Kelvoton tarkastajan vastaus tai verkkovirhe näkyy merkintänä "tarkistus: ei onnistunut".
- Kutsuraja 4 per viesti (`yhdistelma.MAX_KUTSUT_VIESTI`) koskee yhdistelmän ja tarkistuksen summaa. Jos raja on täynnä, tarkistus ohitetaan. Jos tilaa on vain yhdelle kutsulle, korjausta ei tehdä.
- Tarkastajan kutsussa vastausraja on 1 500 tokenia ja aikaraja 120 s. Korjauksessa ne ovat samat kuin chatissa.
- Tarkistuksella on oma kokonaisaikaraja 240 s (tarkastaja ja korjaus yhteensä). Jokainen kutsu saa enintään jäljellä olevan ajan, ja kun aika loppuu, korjausta ei aloiteta. Tarkistus voi siis pidentää viestin vastausaikaa enintään noin 240 s.
- `[[AGENTTI]]`-lohkot tulevat vain alkuperäisestä vastauksesta, kuten muutkin merkinnät.
- Korjauskutsun lisäsyötteelle (ohje, vastaus enintään 12 000 merkkiä ja havainnot, `itsetarkistus.KORJAUS_LISAVARAUS`) varataan tila ennen kuin muisti ja historia täyttävät kehotteen. Jos korjauskehote ei silti mahdu vastaajamallin kontekstiin, korjausta ei tehdä, ja merkintä kertoo sen.

## Rajat ja avoimet asiat

- Yksityinen tila ja työkalutila ovat samassa prosessissa. Erottelu perustuu siihen, että työkalutilan Claude-kutsu ajetaan toisena käyttäjänä (kohta 2, socket) eikä saa työkaluja (`--tools ""`). Jos `AIVO_TYOKALU_SOCKET`-asetusta ei käytetä, erottelu on vain koodin tasolla, ei tiedosto-oikeuksilla.
- Ensimmäinen vastaus yksityisessä tilassa on hidas (ks. nopeudet).
- Aivon vastauksen lähdeviitteet tarkistetaan kuten työkalutilassa.
