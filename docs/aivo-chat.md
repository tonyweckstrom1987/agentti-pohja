# Aivon oma keskustelusivu (A705–A706)

Yksityinen tila ja ydinohjeet: katso `docs/aivo-yksityinen.md`.

Tämä ohje on Tonylle. Chat on asennettu omaan konttiinsa (Tonyn päätös A976). Kontti on Debianin minimiasennus, jossa komentoja ajetaan root-käyttäjänä eikä `sudoa` ole; käyttäjänä ajo tehdään komennolla `runuser -u KÄYTTÄJÄ -- KOMENTO`. Sivu on pieni oma palvelu (`aivo/chat.py`, vain Pythonin vakiokirjasto). Claude on kirjoittanut koodin, ja Tony ottaa sen käyttöön itse: palvelu on uusi, se koskee Aivon oikeuksia ja siinä on salasana. Tony asettaa salasanan itse. Mitään salaisuutta ei ole repossa.

## Mitä sivu tekee

- Tony kirjautuu salasanallaan ja keskustelee Aivon kanssa. Sivu toimii puhelimen selaimessa (kapea asettelu, tumma ja vaalea teema).
- Vastaus tulee vain Claudelta suoraan (`claude -p`, ei työkaluja). LiteLLM:ää, OpenRouteria tai kilpailutusta ei käytetä (A329).
- Ennen jokaista vastausta Aivo lukee muistinsa: päätöskirjaston, kompassin, haastattelut ja faktat. Tiedostot luetaan kirjaston kansiosta tässä järjestyksessä (nimi sisältää `paatos`, `kompassi`, `haastattel`, `fakta`, sitten muut `.md`- ja `.txt`-tiedostot). Yläraja on noin 300 kt; ylimenevät tiedostot jäävät pois.
- Aivo merkitsee muistista otetut väitteet muodossa `[lähde: tiedosto]`. Palvelin tarkistaa viitteet: jos tiedostoa ei ole muistissa, viite saa merkinnän `EI LÖYDY MUISTISTA`.
- Keskustelut tallentuvat isännälle tiedostoihin (`/var/lib/aivo/chat/*.jsonl`, oikeudet 600). Mitään ei lähetetä GitHubiin.
- **Päätösehdotus (A997):** kun Tony sanoo selvästi päättävänsä jotain, Aivo voi lisätä vastauksensa viimeiselle riville merkinnän `[[PAATOS n]]` (n = Tonyn viestin numero). Palvelin poistaa merkinnän näkyvästä tekstistä ja palauttaa kentän `paatosehdotus`, jos n on saman keskustelun Tonyn viesti; muuten ehdotus hylätään hiljaa. Sivu näyttää vastauksen alla kortin "Kirjaanko päätökseksi: '…'?" (Tonyn viesti sanasta sanaan) ja napit kyllä / ei. Vain kyllä kutsuu `/api/paatos`-reittiä, ja kirjataan edelleen vain Tonyn oma viesti. Aivo ei kirjaa mitään itse. Kehote (`KEHOTE`) opastaa ehdotuksen vain työkalutilassa; yksityisessä tilassa merkintä käsitellään samoin, mutta sen ohjeet ovat isännän ydinohjeissa, joita tämä muutos ei koske.
- **Päätökset:** jokaisessa Tonyn viestissä on painike "Kirjaa päätökseksi". Painikkeen painallus liittää Tonyn viestin sanasta sanaan lainauksena kirjaston tiedostoon `aivo-paatoskirjasto.md` ja pushaa kirjaston Giteaan. Sivu näyttää "Kirjattu" vain, kun push onnistui (työkalutila) tai päätös kirjattiin yksityiseen muistiin (yksityinen tila). Jos push epäonnistuu, palvelin vastaa virhekoodilla 502 ja sivu kertoo, ettei päätöstä kirjattu Giteaan; commit jää silloin kirjaston paikalliseen kloonaan. Jos päätös on jo kirjattu, sivu näyttää "Ei muutosta". Aivon omia lauseita tai päätelmiä ei kirjata päätökseksi koskaan. Kirjaston etärepo ei saa osoittaa GitHubiin; muuten koodi kieltäytyy pushaamasta.
- **Tyhjä vastaus:** jos vastauksessa on pelkkä valinta-, ehdotus- tai päätösmerkintä, tekstikupla piilotetaan ja sivu näyttää vain painikkeet tai kortin. Jos merkintöjen poistamisen jälkeen ei jää mitään näytettävää, palvelin palauttaa ja tallentaa historiaan tekstin "(Aivo ei antanut vastausta. Kokeile uudelleen.)". Sivu näyttää "Virhe: …" vain, kun pyyntö epäonnistui (HTTP-tila ei ole 2xx).

## Turvarajat

- Palvelu kuuntelee vain osoitteessa `127.0.0.1`. Koodi kieltäytyy käynnistymästä muussa kuin loopback- tai Tailscale-osoitteessa.
- Tailnetiin sivu avataan komennolla `tailscale serve` (HTTPS, vain tailnetin laitteet). Älä käytä `tailscale funnel` -komentoa: se avaisi sivun internetiin.
- Evästeessä on `HttpOnly`, `Secure` ja `SameSite=Strict`. Muutospyynnöt vaativat oman otsakkeen ja saman alkuperän. Väärällä `Host`-otsakkeella sivu vastaa 421.
- Viisi väärää salasanaa lukitsee kirjautumisen viideksi minuutiksi. Istunnot ovat vain muistissa (palvelun uudelleenkäynnistys kirjaa ulos).
- Salasanasta tallennetaan vain scrypt-tiiviste tiedostoon, jonka oikeudet ovat 600. Lokiin ei kirjoiteta viestejä eikä salasanoja.
- Muistin tiedostot ovat dataa, eivät ohjeita. Kehote kieltää noudattamasta niiden sisältämiä käskyjä.

## Käyttöönotto (Tony ajaa, isännällä, jolla on Tailscale ja `claude`)

Ota ensin tilannekuva kontista. Tämä ohje ei muuta palomuureja, holvia eikä Tailscalen asetuksia, paitsi lisää yhden `tailscale serve` -säännön (vaihe 6).

1. **Käyttäjä ja kansiot** (sama `aivo`-käyttäjä kuin Garmin- ja Drive-ohjeissa):

   ```
   useradd --system --create-home --home-dir /var/lib/aivo --shell /usr/sbin/nologin aivo
   useradd --system --create-home --home-dir /var/lib/aivo-tyokalu --shell /usr/sbin/nologin aivo-tyokalu
   groupadd --system aivo-tyokalu-socket
   install -d -m 700 -o aivo -g aivo /var/lib/aivo/chat /etc/aivo
   ```

   `aivo` ajaa chat-palvelua ja `aivo-tyokalu` työkalutilan `claude`-kutsua (kohta 5b). Ryhmä `aivo-tyokalu-socket` annetaan vain chat-palvelun käyttäjälle (`SupplementaryGroups`). Jos käyttäjä tai ryhmä on jo olemassa, komento antaa virheen. Se ei haittaa.

2. **Kirjasto Gitean kautta.** Kloonaa Aivon kirjasto Giteasta tailnet-nimellä (esimerkiksi `gitea.<tailnet>.ts.net`; IP-osoitetta ei käytetä, koska sallittujen palvelimien lista vertaa nimeä). Tunnus ja avain ovat Tonyn. Git tallentaa ne tiedostoon `.git-credentials` (credential store), jotta push toimii ilman kyselyä:

   ```
   runuser -u aivo -- git config --global credential.helper store
   runuser -u aivo -- git clone https://gitea.<tailnet>.ts.net/aivo/aivo.git /var/lib/aivo/kirjasto
   chmod 600 /var/lib/aivo/.git-credentials
   ```

   Klooni kysyy tunnuksen ja avaimen; Tony kirjoittaa ne itse. Tarkista, että tiedoston tila on 600 (`stat -c '%a' /var/lib/aivo/.git-credentials`) ja ettei `origin` osoita GitHubiin: `runuser -u aivo -- git -C /var/lib/aivo/kirjasto remote -v`.

   **Git-identiteetti.** Ilman nimeä ja sähköpostiosoitetta `git commit` kaatuu, eikä päätös mene perille. Aseta ne kirjaston omaan asetukseen (kloonin jälkeen, ei riipu kotihakemiston ympäristömuuttujista) ja tarkista:

   ```
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto config user.name "Aivo"
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto config user.email "aivo@localhost"
   runuser -u aivo -- git -C /var/lib/aivo/kirjasto config --get-regexp '^user\.'
   ```

3. **Salasana.** Aja itse ja kirjoita salasana kahdesti (vähintään 12 merkkiä). Mitään ei tulostu:

   ```
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.chat aseta-salasana
   ```

   Onnistuessaan komento tulostaa: `Salasanan tiiviste tallennettu (600). Salasanaa ei tallennettu.` Tiedosto on `/etc/aivo/chat-salasana`. `PYTHONPATH` osoittaa agentti-pohjan juureen.

4. **Claude-kirjautuminen `aivo-tyokalu`-käyttäjälle.** Tämän ohjeen mukaisessa socket-mallissa vain `aivo-tyokalu` ajaa `claude`:a (kohta 5b), joten vain sen tarvitsee kirjautua. Tee kirjautuminen kerran: `runuser -u aivo-tyokalu -- env HOME=/var/lib/aivo-tyokalu claude`. Chat-palvelun käyttäjä `aivo` ei tarvitse omaa kirjautumista. Jos jätät socketin pois (`AIVO_TYOKALU_SOCKET` ei ole asetettu), `claude` ajetaan suoraan käyttäjänä `aivo`, ja silloin kirjautuminen tehdään sille samalla komennolla (`HOME=/var/lib/aivo`). Tarkista, että kirjautuminen toimii:

   ```
   echo "sano kyllä" | runuser -u aivo-tyokalu -- env HOME=/var/lib/aivo-tyokalu claude -p --tools "" --max-turns 1
   ```

5. **Palvelu (systemd).** Tallenna tiedostoon `/etc/systemd/system/aivo-chat.service`:

   ```
   [Unit]
   Description=Aivon keskustelusivu
   After=network-online.target

   [Service]
   User=aivo
   WorkingDirectory=/opt/agentti-pohja
   Environment=HOME=/var/lib/aivo PYTHONPATH=/opt/agentti-pohja
   Environment=AIVO_CLAUDE=/KOKO/POLKU/claude
   Environment=AIVO_TYOKALU_SOCKET=/run/aivo-tyokalu.sock
   Environment=AIVO_SALLITUT_ETAISANNAT=gitea.<tailnet>.ts.net
   ExecStart=/usr/bin/python3 -m aivo.chat palvele --host TAILNET-NIMI
   Restart=on-failure
   SupplementaryGroups=aivo-tyokalu-socket
   NoNewPrivileges=true
   ProtectSystem=strict
   ReadWritePaths=/var/lib/aivo
   PrivateTmp=true

   [Install]
   WantedBy=multi-user.target
   ```

   Vaihda `AIVO_CLAUDE` oikeaan polkuun (`command -v claude`) ja `TAILNET-NIMI` palvelun täydelliseen tailnet-nimeen (`tailscale status --self --json`, kenttä `DNSName`, ilman loppupistettä). `--host` vaatii täsmällisen nimen: jokerimerkintä (`.pääte`) hylätään käynnistyksessä, ja ilman `--host`-valitsinta sallitaan vain `localhost`. Tämä pienentää DNS-rebinding-riskiä, koska toisen tailnetin nimet eivät kelpaa.

   **`NoNewPrivileges=true` pysyy.** Jos käytät yksityisen tilan ohjeen mukaista työkalukäyttäjää (`aivo-tyokalu`), ei tarvita `sudoa` eikä sudoers-riviä. Työkalutilan `claude` ajetaan erillisenä systemd-palveluna omana käyttäjänään, ja chat-palvelu puhuu sille unix-socketin kautta. Ohje on kohdassa 5b. Jos et käytä työkalukäyttäjää, jätä `AIVO_TYOKALU_SOCKET` ja `SupplementaryGroups`-rivi pois, jolloin `claude` ajetaan suoraan chat-palvelun käyttäjänä (`aivo`).

   Kaksi ympäristömuuttujaa yksikössä:

   - `AIVO_TYOKALU_SOCKET` on työkalutilan socketin polku (kohta 5b). Ilman sitä työkalutilan `claude` ajetaan chat-palvelun omana käyttäjänä.
   - `AIVO_SALLITUT_ETAISANNAT` on pilkuilla erotettu lista palvelimista (tarkka tailnet-nimi), joille kirjasto saa pushata. **Ilman sitä push kieltäytyy**: päätöstä ei julkaista, tulos on `ei-etaa` ja sivu näyttää virheen eikä "Kirjattu". Arvon pitää vastata kirjaston `origin`-osoitteen palvelinta.

5b. **Työkalutila omana käyttäjänään (ei sudoa).** Käyttäjämalli:

   | Rooli | Käyttäjä | Oikeus socketille |
   |---|---|---|
   | Chat-palvelu (`aivo-chat.service`) | `aivo` (yksityisen tilan ohjeessa `aivo-yksityinen`) | kuuluu ryhmään `aivo-tyokalu-socket` (`SupplementaryGroups`), tila 0660 |
   | Socketin omistaja | `root`, ryhmä `aivo-tyokalu-socket` | omistaja ja ryhmä saavat lukea ja kirjoittaa, muut eivät mitään |
   | Työkalutilan kääre (`aivo-tyokalu@.service`) | `aivo-tyokalu` | ei ole ryhmässä eikä pääse socketille itse; systemd antaa sille yhteyden vain `StandardInput=socket`-kautta |

   Ryhmään kuuluu vain chat-palvelun käyttäjä. Näin chat-palvelun käyttäjä voi vaihtua (`aivo` tai `aivo-yksityinen`) ilman, että socketin oikeuksia tarvitsee avata muille. Ryhmä luodaan kerran: `groupadd --system aivo-tyokalu-socket` (ks. kohta 1). Kaksi tiedostoa. Komento on kiinteä yksikkötiedostossa: chat-palvelu voi lähettää socketille vain tekstiä, ei valitsimia eikä komentoja.

   `/etc/systemd/system/aivo-tyokalu.socket`:

   ```
   [Unit]
   Description=Aivon tyokalutilan Claude-socket

   [Socket]
   ListenStream=/run/aivo-tyokalu.sock
   Accept=yes
   MaxConnections=2
   SocketUser=root
   SocketGroup=aivo-tyokalu-socket
   SocketMode=0660

   [Install]
   WantedBy=sockets.target
   ```

   `/etc/systemd/system/aivo-tyokalu@.service`:

   ```
   [Unit]
   Description=Aivon tyokalutilan Claude (yksi kutsu)

   [Service]
   User=aivo-tyokalu
   Environment=HOME=/var/lib/aivo-tyokalu
   StandardInput=socket
   StandardOutput=socket
   StandardError=journal
   ExecStart=/usr/bin/python3 /KOKO/POLKU/aivo/tyokalu_kaares.py /KOKO/POLKU/claude -p --output-format text --max-turns 1 --tools ""
   RuntimeMaxSec=250
   NoNewPrivileges=yes
   ProtectSystem=strict
   ReadWritePaths=/var/lib/aivo-tyokalu
   PrivateTmp=yes
   PrivateDevices=yes
   ```

   `tyokalu_kaares.py` ajaa saman kiinteän `claude`-komennon ja lisää vastauksen perään päätösrivin `\x00AIVO-EXIT <koodi>`. Chat näyttää nollasta poikkeavasta koodista lyhyen virheen (`Työkalu epäonnistui (koodi 1), katso journalctl -u aivo-tyokalu@*`) tyhjän vastauksen sijaan. Puuttuva päätösrivi (katkennut yhteys, aikaraja) raportoidaan myös virheenä. `claude`:n stderr jää lokiin eikä kulje socketin yli. Kääreskripti ei saa olla `aivo-tyokalu`-käyttäjän kirjoitettavissa (omistajaksi root). Päivitys: vanha yksikkö ilman käärettä antaa nyt aina virheen, koska päätösrivi puuttuu.

   Komento on koko polulla (`command -v claude`). Sitten `systemctl daemon-reload && systemctl enable --now aivo-tyokalu.socket`. Chat-palvelun yksikössä (kohta 5) on jo rivi `Environment=AIVO_TYOKALU_SOCKET=/run/aivo-tyokalu.sock`. Vain `aivo-tyokalu-socket`-ryhmän jäsenet (chat-palvelun käyttäjä) pääsevät socketille (tila 0660, ei oikeuksia muille). Vanha `AIVO_TYOKALU_KAYTTAJA`-asetus on poistettu: koodi kieltäytyy käynnistämästä kutsua, jos se on asetettu, jotta eristys ei heikkene huomaamatta.

   Jos chat-palvelun käyttäjä lisätään ryhmään vasta nyt, käynnistä chat-palvelu uudelleen, jotta ryhmä tulee voimaan.

   Tarkistus (ryhmä annetaan `-g`-valitsimella, koska `runuser -u` ei muuten lisää lisäryhmää): `runuser -u aivo -g aivo-tyokalu-socket -- python3 -c "import socket;s=socket.socket(socket.AF_UNIX);s.connect('/run/aivo-tyokalu.sock');s.sendall('sano kyllä'.encode());s.shutdown(socket.SHUT_WR);print(s.recv(200))"` tulostaa vastauksen. Merkkijonon pitää olla `'sano kyllä'.encode()`: `b'sano kyllä'` on `SyntaxError`, koska bytes-literaalissa ei saa olla ASCII-merkkien ulkopuolisia merkkejä. Toisena käyttäjänä (`runuser -u nobody -- ...`) yhteys antaa `PermissionError`.

   Sitten:

   ```
   systemctl daemon-reload && systemctl enable --now aivo-chat
   ```

6. **Avaus tailnetiin** (HTTPS, vain tailnet). Komennon muoto riippuu Tailscalen versiosta; tarkista `tailscale serve --help`:

   ```
   tailscale serve --bg 8780
   tailscale serve status
   ```

   Tilan pitää näyttää osoite, jonka loppu on `.ts.net`, ja merkintä "tailnet only". Jos näet sanan "Funnel on", sammuta se: `tailscale funnel reset`.

## Todennus (Tonyn ajettavaksi, sen jälkeen riippumaton tarkastaja)

1. Avaa puhelimella (Tailscale päällä) tailnet-osoite, kirjaudu ja kysy "mitä tiedät minusta?". Vastauksessa pitää olla `[lähde: ...]`-viitteitä oikeisiin tiedostoihin.
2. Sulje puhelimen Tailscale (tai käytä mobiilidataa ilman Tailscalea) ja avaa sama osoite. Sivun ei pidä aueta.
3. Sano chatissa selvästi päättäväsi jotain (esimerkiksi "kyllä, tehdään X") ja tarkista, että vastauksen alle ilmestyy kortti "Kirjaanko päätökseksi". "Ei" sulkee kortin ilman kirjausta. Voit myös painaa yhden oman viestisi kohdalla "Kirjaa päätökseksi" ja tarkista, että `aivo-paatoskirjasto.md` sisältää viestin sanasta sanaan ja että muutos näkyy Giteassa (ei GitHubissa). Painikkeen pitää näyttää "Kirjattu" vasta, kun push on onnistunut. Jos push epäonnistuu (esimerkiksi `AIVO_SALLITUT_ETAISANNAT` puuttuu), painikkeen tekstin pitää olla "Ei kirjattu Giteaan: ...".
4. Isännällä: `ss -ltnp | grep 8780` näyttää vain `127.0.0.1:8780`.

## Keskustelumuisti: automaattinen tiivistys (Tony tekee kontissa 122)

Työkalutilan keskustelut tiivistetään kirjastoon, jotta Aivo muistaa ne 40 viestin jälkeenkin (A983 kohta A3). Tiivistys ajetaan komennolla `python3 -m aivo.tiivista --alue tyokalu`. Se tiivistää keskustelun, kun viimeisestä viestistä on kulunut vähintään 30 minuuttia, ja kutsuu Claudea samalla `aivo-tyokalu`-socketilla kuin chat. Päiväkatto on 10 tiivistystä vuorokaudessa (Europe/Helsinki). Katto on koodin vakio, eikä sitä voi muuttaa asetuksilla. Katon täyttyessä ajo lopettaa ja kirjoittaa syyn tiedostoon `/var/lib/aivo/indeksi/tiivista.log`. Yksityisen tilan keskusteluja ei lueta eikä lähetetä: komento hyväksyy vain alueen `tyokalu`.

Tiivistelmät tallentuvat kansioon `/var/lib/aivo/tiivistelmat` (0700, tiedostot 0600) ja luettelo tiedostoon `/var/lib/aivo/indeksi/indeksi.json`. Tonyn lainaukset on erotettu mallin tulkinnasta. Lainaus tarkistetaan sanasta sanaan Tonyn viestistä, ja virheellinen lainaus poistetaan. Uudet kohdat ovat aina tilassa `ehdotus`, ja vain Tonyn korjaus (`python3 -m aivo.tiivistelma`) vahvistaa ne. Chat liittää tiivistelmät työkalutilan kehotteeseen muun muistin rinnalle, enintään 60 kt, mutta vain kun Tony kytkee sen erikseen päälle: chatin käynnistysasetus `--tiivistelmat-kaytossa` (tai ympäristömuuttuja `AIVO_TIIVISTELMAT_KAYTOSSA=1`). Oletuksena kytkentä on pois, eikä kansion olemassaolo kytke sitä. Osuvimmat ja uusimmat tulevat ensin. Yksityinen tila ei saa niitä koskaan.

Ota ensin snapshot kontista 122. Ajastin ei asennu itsestään: tämä on ohje, jonka Tony ajaa.

1. **Yhteys.** Avaa isännällä (Proxmox) kontin pääte ja varmista, että olet kontissa 122:

   ```
   pct enter 122
   hostname
   ```

   Odotettu tulos: komento `hostname` tulostaa kontin 122 nimen (ei isännän nimeä).

2. **Päivitä koodi ja käynnistä chat uudelleen.** Tiivistelmien lukeminen kehotteeseen ei ole päällä oletuksena: lisää chatin käynnistykseen asetus `--tiivistelmat-kaytossa` (tai `AIVO_TIIVISTELMAT_KAYTOSSA=1` ympäristöön), kun olet tarkistanut tiivistelmät kohdassa 8. Chatin palvelukäyttäjä selviää komennolla:

   ```
   systemctl show aivo-chat -p User
   ```

   Odotettu tulos: rivi `User=aivo`. Jos rivillä on muu nimi, käytä sitä nimeä alla kohdassa `User=` ja `runuser -u`-komennoissa.

3. **Kuiva-ajo.** Se vain listaa tiivistettävät keskustelut eikä kutsu mallia:

   ```
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.tiivista --alue tyokalu --kuiva
   ```

   Odotettu tulos: rivit `tiivistettaisiin <keskustelun tunnus> viestit 0-N`, yksi kutakin tiivistämätöntä ja yli 30 minuuttia hiljaista keskustelua kohti. Jos keskusteluja ei ole, ei tule mitään.

4. **Yksikkö.** Tallenna tiedostoon `/etc/systemd/system/aivo-tiivista.service`:

   ```
   [Unit]
   Description=Aivon keskustelujen tiivistys (tyokalutila)

   [Service]
   Type=oneshot
   User=aivo
   WorkingDirectory=/opt/agentti-pohja
   Environment=PYTHONPATH=/opt/agentti-pohja
   Environment=AIVO_TYOKALU_SOCKET=/run/aivo-tyokalu.sock
   ExecStart=/usr/bin/python3 -m aivo.tiivista --alue tyokalu
   SupplementaryGroups=aivo-tyokalu-socket
   NoNewPrivileges=true
   ProtectSystem=strict
   ReadWritePaths=/var/lib/aivo
   PrivateTmp=true
   ```

   Käyttäjä ja ryhmä ovat samat kuin `aivo-chat.service`-yksikössä. Kaksi ajoa yhtä aikaa on turvallista: toinen ajo huomaa lukon ja päättyy.

5. **Ajastin** (15 minuutin välein). Tallenna tiedostoon `/etc/systemd/system/aivo-tiivista.timer`:

   ```
   [Unit]
   Description=Aivon tiivistys 15 minuutin valein

   [Timer]
   OnBootSec=5min
   OnUnitActiveSec=15min

   [Install]
   WantedBy=timers.target
   ```

6. **Käyttöönotto ja tarkistus.**

   ```
   systemctl daemon-reload && systemctl enable --now aivo-tiivista.timer
   systemctl list-timers aivo-tiivista.timer
   ```

   Odotettu tulos: ajastin näkyy listassa, ja `NEXT`-sarakkeessa on aika enintään 15 minuutin päässä.

7. **Ensimmäisen ajon tulos.** Ajon jälkeen:

   ```
   journalctl -u aivo-tiivista --since "20 min ago" --no-pager
   ls -l /var/lib/aivo/tiivistelmat
   ```

   Odotettu tulos: journaalissa rivit `tiivistetty: <tunnus> viestit 0-N, kohtia K, hylattyja lainauksia X, pudotettuja kohtia Y`. Hakemistossa on tiedostoja `<tunnus>-0-N.json` oikeuksilla `-rw-------`.

8. **Tiivistelmän katselu ja korjaus.** Tonyn korjaus kirjoittaa tiivistelmään kentän `tony_korjaus` ja vahvistaa kohdan:

   ```
   runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.tiivistelma --kansio /var/lib/aivo/tiivistelmat lista
   ```

   Odotettu tulos: tiivistelmien tunnukset, yksi riville. Komento `nayta <tunnus>` näyttää lainaukset ja Aivon tulkinnan erillään.

Jos päiväkatto täyttyy, loki sisältää rivin `lopetus: paivakatto 10/10 taynna, tiivistamatta N keskustelua`. Ajo jatkuu seuraavana vuorokautena. Jos tiivistys epäonnistuu, journaalissa on rivi `epaonnistui: ...` ja virheen tyyppi. Sama viestialue yritetään enintään kahdesti, joten rikkinäinen keskustelu ei kuluta koko päiväkattoa.

## Kalenteri työkalutilan kehotteeseen (Tony tekee kontissa 122)

Chat voi liittää työkalutilan kehotteeseen tiiviin listan Tonyn seuraavien 14 päivän tapahtumista (otsikko, aika Europe/Helsinki -aikavyöhykkeellä, paikka). Lista alkaa tämän päivän alusta ja päättyy 14 päivän kuluttua keskiyöhön. Kytkentä on oletuksena pois päältä. Yksityinen tila ei saa kalenteria koskaan. Kalenterin teksti merkitään kehotteessa dataksi, ei ohjeiksi. Osio on enintään 4000 tavua ja enintään 40 tapahtumaa. Jos osoitetiedostoa ei ole, haku epäonnistuu tai välimuisti puuttuu, chat toimii ilman kalenteria ja lokiin kirjataan vain virheen tyyppi. Osoite ei näy kehotteessa, lokissa eikä virheissä.

Ota ensin snapshot kontista 122.

1. **Osoitetiedosto.** Kirjoita Googlen salainen iCal-osoite itse tiedostoon `/etc/aivo/kalenteri-ical` (oikeudet 600, omistajana palvelun käyttäjä). Osoitetta ei anneta komentorivillä eikä ympäristömuuttujalla. Tarkistus, joka ei näytä sisältöä:

   ```
   stat -c '%U %a' /etc/aivo/kalenteri-ical
   ```

   Odotettu tulos: rivi `aivo 600`. Jos käyttäjä tai oikeudet ovat muut, kalenteri jää pois käytöstä.

2. **Kytke päälle.** Lisää chatin ympäristöön `AIVO_KALENTERI_KAYTOSSA=1` (tai käynnistysasetus `--kalenteri-kaytossa`) ja käynnistä chat uudelleen:

   ```
   systemctl edit aivo-chat
   systemctl restart aivo-chat
   ```

   Lisää muokkaukseen palveluosion alle rivi `Environment=AIVO_KALENTERI_KAYTOSSA=1`. Välimuistin kansio on oletuksena `/var/lib/aivo/kalenteri` (muutettavissa asetuksella `AIVO_KALENTERI_DATA`). Palvelun pitää voida kirjoittaa sinne (`ReadWritePaths`).

3. **Tarkistus.** Kysy työkalutilassa chatissa: "Mitä menoja minulla on tällä viikolla?".

   Odotettu tulos: Aivo kertoo kalenterin tapahtumat kellonaikoineen. Välimuistitiedosto on olemassa:

   ```
   ls -l /var/lib/aivo/kalenteri
   ```

   Odotettu tulos: tiedosto `kalenteri.ics` oikeuksilla `-rw-------`.

4. **Vianetsintä.** Jos Aivo ei tiedä menoja, katso lokista kalenteririvit:

   ```
   journalctl -u aivo-chat --since "10 min ago" --no-pager | grep -i kalenteri
   ```

   Odotettu tulos: rivi `kalenteriosio puuttuu (<virheen tyyppi>)` tai rivi `kalenterin haku epaonnistui: ...` ja HTTP-tila. Osoitetta ei ole riveillä.

5. **Poiskytkentä.** Poista asetus `AIVO_KALENTERI_KAYTOSSA=1` (tai aseta arvoksi `0`) ja käynnistä chat uudelleen. Välimuistitiedoston voi jättää paikalleen.

   Odotettu tulos: seuraavan vastauksen kehotteessa ei ole kalenteriosiota, eikä Aivo tiedä menoja.

## Ydinohjeiden ehdotukset chat-sivulla (A1018)

Yksityisen tilan ydinohjemuutoksen voi hyväksyä tai hylätä myös chat-sivulla. Päätteen komento (`python3 -m aivo.yksityinen hyvaksy TUNNUS`) toimii edelleen.

- Kun Aivon vastauksesta tallentuu ehdotus, palvelin palauttaa kentän `ehdotukset` (palvelimen itse tallentamat tunnukset). Sivu näyttää vastauksen alla laatikon, jossa on ero voimassa olevaan ohjeeseen sekä painikkeet "Hyväksy" ja "Hylkää".
- Linkki "Odottavat ehdotukset" yläpalkissa näyttää kaikki odottavat ehdotukset samanlaisina laatikoina. Linkki näkyy vain yksityisessä tilassa.
- "Hyväksy" lähettää vahvistuksen `kyllä <sha8>` sen ehdotuksen tiivisteellä, joka laatikossa näytettiin. Palvelin hyväksyy vain täsmälleen tämän muodon ja kutsuu sen jälkeen funktiota `yksityinen.hyvaksy_ehdotus` sellaisenaan: sha-sidonta ja järjestys (etäkohteen tarkistus, kirjoitus ja commit, palautus virheessä, ehdotuksen poisto, push) ovat samat kuin päätteessä. Jos ehdotuksen sisältö on muuttunut näyttämisen jälkeen, tiiviste ei täsmää ja palvelin vastaa 409.
- "Hylkää" siirtää ehdotuksen alikansioon `hylatyt/` (0700). Mitään ei poisteta, eikä aiempaa samannimistä hylättyä korvata.
- Mallin vastaus tai Tonyn chat-viesti ei voi hyväksyä mitään, vaikka siinä lukisi `kyllä <sha8>`. Vain hyväksyntäreitti hyväksyy.
- **Järjestelmämerkintä.** Kun hyväksyntä tai hylkäys onnistuu, palvelin lisää yksityiseen keskusteluun rivin roolilla `jarjestelma`, esimerkiksi `[Järjestelmä: Tony hyväksyi muutoksen ydinohjeeseen saannot.md (sha 1a2b3c4d). Se on nyt voimassa ja näkyy YDINOHJEET-osiossa.]` tai `[Järjestelmä: Tony hylkäsi ehdotuksen ydinohjeeseen saannot.md (tunnus …). Ydinohje ei muuttunut.]`. Reitti palauttaa saman tekstin kentässä `merkinta`, ja sivu näyttää sen keskustelussa omana rivinään. Epäonnistunut käsittely (esim. väärä sha, 400, 409 tai 500) ei lisää merkintää.
- Merkintä on vain tiedoksi. Mikään koodi ei lue sitä hyväksynnäksi, eikä se anna oikeuksia mihinkään. Hyväksyntä tulee edelleen vain hyväksyntäreitistä ja funktiosta `yksityinen.hyvaksy_ehdotus`. Merkinnän kirjoittaa vain palvelin. Jos mallin vastaus matkii merkintää (`[Järjestelmä:` tai `[jarjestelma:` isoin tai pienin kirjaimin), palvelin muuttaa alun muotoon `[Aivon teksti, ei järjestelmämerkintä:` ennen tallennusta ja näyttämistä.
- Mallille merkintä välitetään seuraavan Tonyn viestin alussa (`user`-rooli), koska kaikki mallipohjat eivät salli `system`-viestiä keskellä keskustelua tai kahta peräkkäistä `user`-viestiä. Yksityisen tilan tiivistys lukee merkinnät nimellä "Järjestelmä", eikä niitä hyväksytä Tonyn lainauksiksi.
- **Kehote (sääntö 3).** Kehote kertoo, että Tony hyväksyy tai hylkää ehdotukset chat-sivun painikkeilla eikä malli näe niitä. Malli ei saa pyytää hyväksyntää chatissa eikä väittää, että hyväksyntä annetaan kirjoittamalla. Kaikki YDINOHJEET-osion sisältö on jo hyväksyttyä ja voimassa. Kun Tony kysyy, päivittyikö jokin ohje, mallin pitää verrata YDINOHJEET-osioon eikä arvata. Kiellot säilyvät: malli ei väitä muuttaneensa ohjeita itse eikä keksi hyväksyntää. Sama sääntö on molemmissa taustapäissä (`yksityinen.YKSITYINEN_KEHOTE` ja `yksityinen_or.KEHOTE`).
- Kaikki dynaaminen sisältö lisätään sivulle `textContent`-ominaisuudella. Ero puhdistetaan kuten päätteen `nayta`-komennossa (ohjausmerkit näkyvät `\x1b`-tyylisinä merkintöinä).

Reitit (kirjautuminen, `Host`-otsake, `X-Aivo`-otsake ja sama alkuperä tarkistetaan kuten muillakin `/api`-reiteillä):

| Reitti | Kuvaus |
|---|---|
| `GET /api/ehdotukset?keskustelu=ID` | lista: tunnus, tiedosto, sha8 |
| `GET /api/ehdotus?keskustelu=ID&tunnus=T` | yksi ehdotus ja ero voimassa olevaan ohjeeseen |
| `POST /api/ehdotus/hyvaksy` `{keskustelu, tunnus, vahvistus}` | vahvistus täsmälleen `kyllä <sha8>` |
| `POST /api/ehdotus/hylkaa` `{keskustelu, tunnus}` | siirto kansioon `hylatyt/` |

Keskustelun pitää olla yksityisen tilan keskustelu. Työkalutilan keskustelulle ja palvelulle, jossa yksityinen tila ei ole käytössä, reitit vastaavat 404.

**Kirjoitusoikeus.** `docs/aivo-yksityinen.md` neuvoo asettamaan chat-palveluun `ReadOnlyPaths=/var/lib/aivo-yksityinen/ydinohjeet`. Tällä asetuksella hyväksyntä chat-sivulla ei onnistu: palvelin vastaa 500 (`tallennus epäonnistui (PermissionError)` tai vastaava tyyppi), ohje ei muutu ja ehdotus jää odottamaan. Asetuksen muuttaminen on Tonyn päätös, eikä tämä muutos koske siihen.

**Ehdotuksen pituus.** Ehdotus on ydinohjetiedoston koko uusi sisältö, ja vastauksen enimmäispituus on `MAX_TOKENIA` = 700 tokenia. Rajaa ei nosteta, koska ydinohjeiden yläraja (20 000 merkkiä, noin 7 000 tokenia) ei ehtisi i5-8400:lla vastausajan sisällä. Kehote pyytää siksi pitämään ehdotuksen enintään 1 200 merkissä ja ehdottamaan pidemmän tiedoston sijaan uutta lyhyttä tiedostoa. Jos vastaus katkeaa ennen sulkevaa `[[/EHDOTUS]]`-merkintää, mitään ei tallenneta ja vastauksessa näkyy `[ehdotusta ei tallennettu: vastaus katkesi]`. Jos sulkeva merkintä on, mutta tiedostonimi ei kelpaa, vain ehdotuslohko korvataan tekstillä `[ehdotusta ei tallennettu: virheellinen tiedostonimi]`, ja lohkon jälkeinen teksti näkyy ennallaan. Hyväksyntä- ja hylkäysreitti palauttavat tilan 400, jos tunnus ei ole merkkijono.

**Kehotteen taustapää.** Paikallisella mallilla kehote kertoo, että malli ajetaan Tonyn omalla koneella. OpenRouter-taustapäällä kehote kertoo, että käytössä on avoin malli OpenRouterin kautta nollasäilytyksellä (ZDR) ja että ulos lähtee vain kulloinenkin kehote (`aivo/yksityinen_or.py`, `KEHOTE`).

## Asennuksen tilatarkistus (vain luku)

Päivityksen jälkeen kontissa 122 yksi komento kertoo, onko asennus kunnossa. Anna argumenttina odotettu commit (`git rev-parse HEAD` agentti-pohjan kloonista, jonka PR on mergattu):

```
runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.tarkista_asennus ODOTETTU_COMMIT
```

Komento tarkistaa asennetun commitin, chat-palvelun ja tiivistysajastimen tilan (`systemctl is-active`), reitin `/terve` portissa 8780, Aivon moduulien olemassaolon ja käännettävyyden sekä salasanatiedoston oikeudet. Moduulit luetaan lähdetiedostoina polusta, jonka `--koodi` antaa (oletus `/opt/agentti-pohja`), ja ne käännetään `compile`-kutsulla. Moduuleja ei tuoda, joten niiden tuontiaikaista koodia ei ajeta. Tarkistus ei siis löydä ajonaikaisia vikoja. Salasanatiedostoon käytetään vain `os.stat`ia: sisältöä ei avata eikä tulosteta.

`/etc/aivo` on `aivo:aivo` ja tilassa 700. Jos komennon ajaa muu käyttäjä kuin hakemiston omistaja (esimerkiksi palvelun `User=` on eri kuin `aivo`), `os.stat` ei pääse tiedostoon ja rivi on `VIKA avain /etc/aivo/chat-salasana: ei oikeutta tarkistaa (PermissionError)`. Komento ei kaadu. Aja silloin komento hakemiston omistajana (`runuser -u <omistaja> -- ...`). Paluuarvo on 0 vain, jos kaikki rivit ovat OK. Asentamattomassa ympäristössä tulos on esimerkiksi:

```
VIKA commit: asennettu 7c01f795a607, odotettu 141ab0075ba5
VIKA palvelu aivo-chat: inactive
VIKA ajastin aivo-tiivista.timer: inactive
VIKA terve /terve: ei yhteytta (ConnectionRefusedError)
OK   moduulit: 3 moduulia
VIKA avain /etc/aivo/chat-salasana: puuttuu
```

Valinnat `--koodi` (oletus `/opt/agentti-pohja`) ja `--portti` (oletus 8780) vaihtavat polun ja portin.

## OpenRouter-kulut chat-sivulla

Yläpalkissa on pieni teksti, esimerkiksi "Tänään 0,42 € / 10 €". Luku on Aivon OpenRouter-kulujen summa kuluvalta vuorokaudelta (Europe/Helsinki), ja 10 € on päiväkatto (A963, A1005). Kun kulusta on käytetty 80 % katosta, teksti muuttuu varoitusväriseksi. Teksti päivittyy, kun sivu avataan ja kun vastaus on tullut. Tekstin painallus avaa keskusteluun erittelyn:

- tämän päivän sekä viimeisen 7 ja 30 vuorokauden summat (kuluva päivä mukaan lukien) euroina ja dollareina
- jakauma käyttötarkoituksittain (chat, tiivistys, muu) ja malleittain (enintään 20 mallia, loput rivillä "muut")
- käytetty osuus päiväkatosta ja tieto, onko katto täynnä.

Sivu näyttää kaiken `textContent`-ominaisuudella, ei HTML:nä.

**Reitti.** `GET /api/kulut` vaatii saman kirjautumisen, sallitun `Host`-otsakkeen, `X-Aivo`-otsakkeen ja saman alkuperän (`Origin`) kuin muut API-reitit. Ilman kirjautumista vastaus on 401. Reitti palauttaa vain summat. Se ei palauta keskustelujen sisältöä eikä kirjanpidon tehtävätunnisteita.

**Lähde.** Reitti lukee yhteistä kirjanpitoa `/var/lib/claude-jono/openrouter-kulut.jsonl` (`aivo/openrouter_kulut.py`) vain lukien. Se ei luo tiedostoa eikä lukkoa, eikä se muuta katkaisinta tai kattoa. Kirjanpidossa on jo kentät `malli` ja `tehtava`, joten uusia kenttiä ei tarvittu. Käyttötarkoitus päätellään tehtävästä: `aivo-yksityinen` on chat, tunniste, jossa on sana `tiivist`, on tiivistys, ja muut ovat muu. Avoimet varaukset lasketaan mukaan kuten katkaisimessa. Puuttuva tai tyhjä kirjanpito näkyy nollina. Jos kirjanpitoa ei voi lukea (esimerkiksi rikkinäinen rivi), sivu näyttää "kirjanpito ei luettavissa" varoitusvärillä. Silloin myös katkaisin estää OpenRouter-kutsut.

**Valuutta.** Kirjanpito on dollareina. Sivu näyttää sekä $- että €-luvut ja kertoo, että €-luku on arvio. Näytön muunnoskerroin on asetus: `--kulut-kerroin` tai ympäristömuuttuja `AIVO_KULUT_USD_EUR` (oletus 0,92, sallittu väli 0–10). Kerroin vaikuttaa vain näyttöön. Päiväkatto ja 80 %:n varoitusraja lasketaan aina katkaisimen omalla kiinteällä kertoimella (`openrouter_kulut.USD_EUR`), jotta varoitus vastaa katkaisimen todellista tilaa. Jos asetat eri kertoimen, €-summa ja varoitusraja voivat siksi poiketa toisistaan hieman.

**Oikeudet.** Chat-palvelun käyttäjän pitää pystyä lukemaan kirjanpito. Yksityisen tilan OpenRouter-taustapää kirjoittaa samaan tiedostoon, joten oikeus on jo olemassa, jos taustapää on käytössä.

## Testit

```
python3 -m unittest aivo.test_chat aivo.test_tiivista aivo.test_kalenteri_kytkenta aivo.test_yksityinen_hyvaksynta aivo.test_tarkista_asennus aivo.test_kulunakyma
```

Testit kattavat salasanan tiivisteen ja lukituksen, osoite- ja Host-rajat, CSRF-suojan, muistin lukemisen (ei linkkejä kirjaston ulkopuolelle), lähdeviitteiden tarkistuksen, päätöksen kirjaamisen sanasta sanaan, GitHub-pushin eston ja sen, että Claude-kutsu ei käytä työkaluja eikä välittäjiä.
