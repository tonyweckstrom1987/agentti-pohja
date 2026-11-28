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

Kun tiivistelmät ovat päällä, chat myös hakee Tonyn viestin sanoilla työkalualueen hakuindeksistä (`/var/lib/aivo/indeksi/tiivistelmat.sqlite`) ja liittää osumat kehotteen ensimmäiseksi muistiosaksi lähdeviitteineen. Tiivistysajo päivittää indeksin. Tarkemmin ja käyttöönotto: `docs/aivo-yksityinen.md`, kohta Muistihaku.

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
   Description=Aivon tiivistys 15 minuutin valein (edellisen ajon paattymisesta)

   [Timer]
   OnActiveSec=1min
   OnBootSec=5min
   OnUnitInactiveSec=15min

   [Install]
   WantedBy=timers.target
   ```

   Palvelu on `Type=oneshot`, joten toistoväli lasketaan edellisen ajon päättymisestä (`OnUnitInactiveSec`). `OnUnitActiveSec` ei sovi tähän: kontissa 122 ajastin ajoi kerran, mutta seuraavaa ajoa ei ajastettu (`NEXT` `-`). `OnActiveSec=1min` käynnistää ensimmäisen ajon minuutin kuluttua ajastimen käynnistyksestä (`enable --now`, `restart`), joten toistovälillä on aina lähtöpiste. Testi `test/test_ajastin_toisto.py` hylkää repon ajastinmallin, jossa toisto on vain `OnUnitActiveSec`.

6. **Käyttöönotto ja tarkistus.**

   ```
   systemctl daemon-reload && systemctl enable --now aivo-tiivista.timer
   systemctl list-timers aivo-tiivista.timer --no-pager
   ```

   Odotettu: ajastin näkyy listassa, ja `NEXT`-sarakkeessa on aika enintään 15 minuutin päässä myös ensimmäisen ajon jälkeen. `NEXT` `-` tarkoittaa, ettei seuraavaa ajoa ole ajastettu: tarkista, että `[Timer]`-osiossa on `OnUnitInactiveSec` eikä `OnUnitActiveSec`.

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

## Agenttidata-kopio kehotteeseen (Tony tekee kontissa 122)

Aivo voi lukea agenttidata-kloonista (`/var/lib/aivo/agenttidata`, ohje `docs/agenttidata.md` askel 8) kolme tiedostoa: `kompassi.md`, `faktat.md` ja `pohdinta-vastaukset.md`. Ne tulevat kehotteeseen omana osionaan `AGENTTIDATA`, jonka otsikossa näkyvät lähde ja kopion aika. Asetus on oletuksena pois päältä.

- Mukana ovat molemmat tilat: työkalutila ja yksityinen tila (A1015).
- Pohdinta-vastauksista tulee vain loppu: enintään 60 viimeistä A-merkintää. Yksityisessä tilassa osuus on pienempi (kehotteen raja on 16 000 merkkiä), joten mukana on vain muutama tuorein merkintä (23.11.2026 datalla kuusi). Osion otsikko kertoo todellisen määrän.
- `tulokset/` ja `tehtavat/` eivät tule kehotteeseen.
- Osio mahtuu samaan muistirajaan kuin kirjasto. Kirjastolle jää loput, joten kehotteen kokonaiskoko ei kasva.
- Kopion aika on viimeisimmän haun aika (`.git/FETCH_HEAD`). Jos hakua ei ole tehty, aika on tiedostojen muutosaika. Jos kopio on yli 24 tuntia vanha, osioon tulee varoitus. Varoitus tulee myös, jos haku onnistui mutta työpuu ei päivittynyt (`FETCH_HEAD` ja `HEAD` eroavat, esim. `pull --ff-only` epäonnistui).
- Aivo lukee kopiota vain palvelun omana käyttäjänä. Mikään reitti ei kirjoita kopioon. Palvelu ei käynnisty, jos kopion kansio on kirjaston, keskustelujen, tiivistelmien tai yksityisten kansioiden kanssa päällekkäin.
- Vain tavalliset tiedostot kloonin sisällä luetaan: ei `.git`-kansiota eikä linkkejä kloonin ulkopuolelle.

1. **Kytke päälle.** Avaa drop-in:

   ```
   systemctl edit aivo-chat
   ```

   Lisää muokkaukseen palveluosion alle rivi `Environment=AIVO_AGENTTIDATA=/var/lib/aivo/agenttidata`. Käynnistä palvelu uudelleen ja tarkista, että asetus näkyy:

   ```
   systemctl restart aivo-chat
   systemctl show aivo-chat -p Environment --no-pager | tr ' ' '\n' | grep AIVO_AGENTTIDATA
   systemctl is-active aivo-chat
   ```

   Odotettu tulos: rivi `AIVO_AGENTTIDATA=/var/lib/aivo/agenttidata` ja `active`. `ReadWritePaths` ei muutu.

2. **Tarkistus.** Kysy chatissa (työkalutila): "Mikä on tuorein A-päätös?".

   Odotettu tulos: Aivo kertoo pohdinta-vastausten viimeisen A-merkinnän ja viittaa lähteeseen `agenttidata/pohdinta-vastaukset.md`. Vertaa:

   ```
   grep -oE '\*\*A[0-9]+' /var/lib/aivo/agenttidata/pohdinta-vastaukset.md | tail -1
   ```

3. **Poiskytkentä.** Poista rivi drop-inistä (`systemctl edit aivo-chat`) ja käynnistä chat uudelleen.

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

**Kehotteen taustapää.** Paikallisella mallilla kehote kertoo, että malli ajetaan Tonyn omalla koneella. OpenRouter-taustapäällä kehote kertoo, että käytössä on avoin malli OpenRouterin kautta nollasäilytyksellä (ZDR) ja että koko kehote (ydinohjeet, muisti, agenttidata ja keskustelu) lähtee jokaisella viestillä OpenRouterille. ZDR-tilassa sitä ei tallenneta eikä käytetä koulutukseen, ja muisti ja keskustelut tallennetaan Tonyn omalle palvelimelle (`aivo/yksityinen_or.py`, `KEHOTE`). Kehotteen koko seuraa mallin kontekstia (A1106), joten suurella mallilla kehote voi sisältää lähes koko muistin.

## Luovutus yksityisestä tilasta työkalutilaan

Tilojen välillä ei muuten ole siltaa. Luovutus on yksisuuntainen: yksityisessä tilassa sovittu teksti voidaan viedä työkalutilaan vasta, kun Tony on nähnyt sen ja hyväksynyt sen. Työkalutilasta yksityiseen ei kulje mitään. Säännöt sille, mitä luovutetaan ja miten teksti siivotaan, Tony ja Aivo sopivat ydinohjeisiin. Koodi rakentaa vain mekanismin (`aivo/luovutus.py`).

**Ehdotus.** Ehdotus syntyy kahdella tavalla:

- Aivon yksityisen tilan vastauksessa on lohko `[[LUOVUTUS]] teksti [[/LUOVUTUS]]`. Palvelin poistaa lohkon näkyvästä vastauksesta, tallentaa tekstin ehdotukseksi ja palauttaa tunnuksen kentässä `luovutukset`. Jos vastaus katkeaa ennen sulkevaa merkintää, mitään ei tallenneta.
- Jokaisen yksityisen tilan viestin (Tonyn ja Aivon) alla on painike "Luovuta työkalutilaan". Palvelin ottaa tekstin omasta keskusteluhistoriastaan viestin indeksin perusteella, ei selaimen lähettämänä.

Ehdotus tallentuu yksityiseen kansioon `/var/lib/aivo-yksityinen/luovutukset` (0700, tiedostot 0600) tunnuksen ja sha256-tiivisteen kanssa. Tonyn chat-viestiä ei koskaan tulkita lohkoksi. Työkalutilan vastauksen lohkoa ei tulkita lainkaan.

**Laatikko.** Sivu näyttää laatikon, jossa lukee "Tämä teksti lähtee Clauden pilveen" ja jossa on täsmälleen lähtevä teksti muokattavassa kentässä. Painikkeet ovat "Lähetä työkalutilaan" ja "Hylkää".

- Jos Tony muokkaa tekstiä, sivu tallentaa ensin muokkauksen (`/api/luovutus/muokkaa`). Muokattu teksti saa uuden sha:n, ja hyväksyntä sidotaan siihen. Vanhan sha:n vahvistus ei enää kelpaa (409).
- "Lähetä työkalutilaan" kysyy vahvistuksen ja lähettää `kyllä <sha8>`. Palvelin hyväksyy vain täsmälleen tämän muodon, kuten ydinohjeiden ehdotuksissa.
- "Hylkää" siirtää ehdotuksen alikansioon `hylatyt/`. Mitään ei poisteta.

**Hyväksynnän järjestys.** (1) Vahvistus tarkistetaan tallennetun tekstin tiivistettä vasten. (2) Yksityiseen lokiin `luovutetut.jsonl` (0600) kirjataan aika, tunnus, sha ja teksti ennen kuin mitään lähtee. (3) Teksti kopioidaan sellaisenaan kirjaston tiedostoon `saapuneet/<tunnus>.md`, ja kirjasto commitoidaan ja pushataan samalla funktiolla kuin päätöskirjaus (`julkaise_kirjastoon`, vain sallitut etäpalvelimet). (4) Jos julkaisu heittää virheen, tiedosto poistetaan kirjastosta, virhe kirjataan lokiin ja ehdotus jää odottamaan. (5) Onnistuneen julkaisun jälkeen ehdotus siirretään alikansioon `hyvaksytyt/`. Pushin epäonnistuminen ei peru commitia; sivu kertoo tuloksen.

**Työkalutila.** Työkalutilan kehotteessa on osio "SAAPUNEET LUOVUTUKSET": käsittelemättömät luovutukset sanasta sanaan, yhteensä enintään 20 000 tavua. Rajan yli jäävät mainitaan tunnuksella. Tiedosto, jonka tiiviste ei täsmää otsakeriviin (esim. käsin muutettu), jätetään pois ja mainitaan. Kansio `saapuneet/` ei tule kehotteeseen tavallisena muistina. Työkalutila lukee vain kirjaston `saapuneet/`-kansiota eikä koskaan yksityisiä kansioita.

Linkki "Saapuneet" näyttää työkalutilassa käsittelemättömät luovutukset ja painikkeen "Merkitse käsitellyksi". Painike siirtää tiedoston kansioon `saapuneet/kasitellyt/` ja julkaisee muutoksen. Aivo ei voi merkitä luovutusta käsitellyksi itse, koska työkalutilalla ei ole työkaluja (itsenäinen käsittely on erillinen päätös, A1001).

**Loki.** Linkki "Luovutetut" näyttää yksityisessä tilassa kaikki luovutetut tekstit (aika, tunnus, sha, tulos ja teksti) uusimmasta vanhimpaan.

Reitit (kirjautuminen, `Host`, `X-Aivo` ja sama alkuperä tarkistetaan kuten muillakin `/api`-reiteillä):

| Reitti | Tila | Kuvaus |
|---|---|---|
| `GET /api/luovutukset?keskustelu=ID` | yksityinen | odottavat ehdotukset: tunnus, sha8, lähde |
| `GET /api/luovutus?keskustelu=ID&tunnus=T` | yksityinen | yhden ehdotuksen teksti ja sha8 |
| `GET /api/luovutetut?keskustelu=ID` | yksityinen | loki |
| `POST /api/luovutus/uusi` `{keskustelu, indeksi}` | yksityinen | ehdotus keskustelun viestistä |
| `POST /api/luovutus/muokkaa` `{keskustelu, tunnus, teksti}` | yksityinen | uusi teksti ja uusi sha8 |
| `POST /api/luovutus/hyvaksy` `{keskustelu, tunnus, vahvistus, lisavahvistus?}` | yksityinen | vahvistus täsmälleen `kyllä <sha8>`; vuotovartijan tulos ”kysy” vaatii lisäksi `lisavahvistus` = `tarkistin kohdat <sha8>`, tulos ”estä” palauttaa aina 409 |
| `POST /api/luovutus/hylkaa` `{keskustelu, tunnus}` | yksityinen | siirto kansioon `hylatyt/` |
| `GET /api/saapuneet?keskustelu=ID` | työkalu | käsittelemättömät luovutukset |
| `POST /api/saapuneet/kasitelty` `{keskustelu, tunnus}` | työkalu | siirto kansioon `saapuneet/kasitellyt/` |

Väärän tilan keskustelulle reitit vastaavat 404. Tunnus on aina 12 heksamerkkiä, joten polkuja (`../`) ei hyväksytä. Kaikki teksti lisätään sivulle `textContent`- tai `value`-ominaisuudella.

**Käyttöönotto.** Kansio luodaan automaattisesti yksityisen datan viereen (`--luovutukset` tai `AIVO_LUOVUTUKSET` vaihtaa polun). Jos palvelu ei saa luoda sitä, luo se itse: `sudo install -d -m 700 -o aivo-yksityinen -g aivo-yksityinen /var/lib/aivo-yksityinen/luovutukset`. Chat-palvelun käyttäjällä pitää olla kirjoitusoikeus kirjastoon, kuten päätöskirjauksessakin.

### Vuotovartija (A1040, kohta 4)

Ennen kuin Tony hyväksyy luovutuksen, teksti tarkistetaan kahdella tavalla (`aivo/vartija.py`). Vartija ei koskaan muuta tekstiä. Tarkistuksista syntyy yksi kolmesta lopputuloksesta (alla), ja selvä salaisuus estää hyväksynnän (Tonyn päätös A1096, 25.11.2026).

- **Mekaaninen esitarkistus** tehdään aina palvelimella ilman verkkoa: sähköpostiosoitteet, puhelinnumerot, IPv4-osoitteet (myös nollilla täytetty muoto, esim. `010.001.002.003`) ja IPv6-osoitteet (myös `::`-lyhennetty muoto, vyöhyketunnus ja IPv4-upotettu muoto, esim. `::ffff:a.b.c.d`; luokittelu `ipaddress`-moduulilla), tailnet-nimet (`*.ts.net`, `tail…`), henkilötunnukset (myös pienillä kirjaimilla), IBAN-tilinumerot kaikista maista (maan pituus ja mod 97 -tarkiste, myös pienillä kirjaimilla), yksityisavainten otsikot (SSH, PKCS#8 ja PGP:n `PRIVATE KEY BLOCK`), SSH-julkisavaimet (`ssh-ed25519`/`ssh-rsa`), yleiset API-avainten muodot (`sk-`, `ghp_`, `gho_`, `github_pat_`, `xoxb-`, `AKIA`, `tskey-`, `AIza`, `sk_live_`, `rk_live_`, `glpat-`, `hf_` ja JWT eli kolme base64url-osaa `eyJ`-alulla), pitkä satunnainen arvo avainsanan (key, token, secret, salasana) jälkeen sekä Tonyn ylläpitämät listat. Listat ovat kansiossa `luovutukset/vartija/` (`--vartija-listat` tai `AIVO_VARTIJA_LISTAT` vaihtaa polun; luovutuspaketin koukku ja hyväksyntä käyttävät samaa polkua): `nimet.txt` (henkilönimet) ja `avainsanat.txt`, yksi rivi kerrallaan, `#`-rivit ovat kommentteja. Nimi osuu myös taivutettuna, kun alku on sama (Maija → Maijalle).
- **Vartijamalli** (rooli `vartija`) tarkistaa tekstin vain OpenRouter-taustapäällä. Malli on `AIVO_YKSITYINEN_ROOLIT`-asetuksen `vartija:<malli>` tai sallittujen mallien listan ensimmäinen. Kutsu kulkee samaa reittiä kuin yksityisen tilan vastaukset (`yksityinen_or._kysy_viestit`): avoin malli, ZDR-runko, yhteinen päiväkatto ja kulukirjaus. Malli palauttaa JSON-listan epäilyttävistä kohdista syineen (henkilö, paikka, tavoite, kokonaiskuva). Loopback-taustapäällä mallia ei käytetä, koska teksti ei muuten lähtisi palvelimelta.

**Mittaus synteettisellä aineistolla.** `python3 -m aivo.vartija_aineisto --ulos HAKEMISTO` kirjoittaa 110 keksittyä tekstiä (50 turvallista, 50 arkaluonteista ja 10 vaikeaa tapausta, 200–500 sanaa) totuustiedostoineen. Aineisto on toistettava siemenluvulla (oletus 413). Sähköpostit ovat vain verkkotunnuksissa example.com, example.org ja test.org, IP-osoitteet vain dokumentaatioalueilta, henkilötunnukset 1800-luvulta, ja henkilöt ja paikat ovat paikkamerkkejä. `python3 -m aivo.vartija_mittaus [--esimerkit]` ajaa mekaanisen tarkistuksen aineistoa vastaan ja tulostaa tyypeittäin osumat, ohitukset ja väärät hälytykset. Mittaus ei käytä verkkoa eikä mallia.

Tarkistus käynnistyy, kun ehdotus tallennetaan (lohko tai painike) ja kun Tony muokkaa tekstiä. Mallin tulos tallentuu erilliseen tiedostoon `luovutukset/vartija/<tunnus>.json` (0600), ja se sidotaan tekstin sha256-tiivisteeseen. Muokatun tekstin tarkistus tehdään uudelleen, eikä vanhaa tulosta näytetä eikä käytetä.

**Kolme lopputulosta** (`vartija.paatos`):

| Tulos | Milloin | Hyväksyntä |
|---|---|---|
| estä | mekaaninen osuma: SSH-yksityis- tai julkisavain, API-avain, pitkä satunnainen arvo avainsanan jälkeen, henkilötunnus, IBAN-tilinumero, tailnet-nimi tai yksityinen IP-osoite (RFC 1918 -alueet 10/8, 172.16/12 ja 192.168/16, Tailscalen CGNAT-alue 100.64/10 ja IPv6-alue fd7a:115c:a1e0::/48, link-local 169.254/16 sekä IPv6:n fc00::/7, fe80::/10 ja fec0::/10; myös lyhennettynä, kuten `fd7a:115c:a1e0::1`, ja IPv6:n sisään upotettuna) | ei mahdollinen; Tony muokkaa tekstiä, ja muokattu teksti tarkistetaan uudelleen. Estoa ei voi ohittaa. |
| kysy | muu mekaaninen osuma (sähköposti, puhelinnumero, muu IP-osoite kuten dokumentaatio-, loopback- tai julkinen osoite, nimi- tai avainsanalista), vartijamallin arvio ”suuri”, tai vartijamalli ei tarkistanut tekstiä (ei käytössä, kesken, virhe, kelvoton vastaus tai arvio puuttuu) | vaatii erillisen vahvistuksen, jossa merkityt kohdat näkyvät: `lisavahvistus` = `tarkistin kohdat <sha8>` |
| turvallinen | ei mekaanisia osumia ja vartijamallin arvio ”ei” tai ”pieni” | kuten ennen: `kyllä <sha8>` |

Vartijamallin arvio ”suuri” muuttuu estoksi vasta, kun synteettinen mittaus (tehtävä 25-45) näyttää väärien hälytysten osuuden ja Tony päättää. Loopback-taustapäällä vartijamallia ei käytetä, joten siellä jokainen luovutus on vähintään ”kysy”.

**Palvelin päättää.** Lopputulos lasketaan uudelleen hyväksyntähetkellä (`luovutus.hyvaksy`, luovutuslukon alla) samasta tekstistä, joka luovutetaan, ja mallin tulos kelpaa vain saman sha:n tekstille. Mekaaninen esto lasketaan hyväksynnässä aina itse, joten kutsujan antama tarkistus ei voi muuttaa estoa kysyksi tai turvalliseksi. Päätös käyttää kaikkia mekaanisia osumia, ei vain näytettäviä 50 ensimmäistä. Estetty tai vahvistamaton hyväksyntä palauttaa 409 ja kentän `vartija` = `{tulos, syyt}`. Syyt ovat tyyppejä (esim. ”API-avain”), eivät tekstin sisältöä. Lokiin ei kirjoiteta mitään, koska mitään ei luovutettu.

Hyväksyntälaatikko näyttää lopputuloksen ja syyt tyyppeinä sekä merkinnät korostettuina (`textContent` ja `<mark>`-elementit). ”Estä”-tuloksella lähetyspainike on pois käytöstä, kunnes tekstiä muokataan. ”Kysy”-tuloksella ensimmäisen vahvistuksen jälkeen avautuu toinen ikkuna, jossa merkityt kohdat näkyvät. Jos vartija ei vastaa, vastaus ei kelpaa tai mallia ei ole, laatikossa lukee "Vartija ei tarkistanut" ja syy, ja tulos on ”kysy”.

`GET /api/luovutus` palauttaa kentän `vartija`: `mekaaniset` (lista), `malli` (`tila`: `tarkistettu`, `kesken` tai `ei-tarkistanut`, `syy`, `merkinnat`, `arvio`, jos malli antoi sen), `nimilista` ja `avainsanoja` (listojen pituudet) sekä `tulos` (`turvallinen`, `kysy` tai `esta`) ja `syyt` (tyypit). `POST /api/luovutus/muokkaa` palauttaa saman kentän muokatusta tekstistä.

## Luovutuspaketti: monta palaa, yksi hyväksyntä

Iso työ voidaan luovuttaa pilkottuna pakettina (`aivo/luovutuspaketti.py`). Yksittäisen luovutuksen sha-sidonta ja `hyvaksy_ehdotus` pysyvät ennallaan.

**Merkintä.** Aivon yksityisen tilan vastauksessa:

```
[[LUOVUTUSPAKETTI nimi]]
[[PALA 1]] teksti [[/PALA]]
[[PALA 2]] teksti [[/PALA]]
[[/LUOVUTUSPAKETTI]]
```

Paketissa on 1–20 palaa (numerot 1–99, ei toistoja), yhteensä enintään 40 000 merkkiä. Nimi on 1–80 merkkiä kirjaimia, numeroita, välilyöntejä ja merkkejä `.,:()-`. Palvelin tallentaa paketin yksityiseen kansioon `<luovutukset>/paketit/` (0700, tiedostot 0600) ja palauttaa tunnuksen kentässä `paketit`. Katkennut tai tyhjä paketti ei tallennu. Paketti käsitellään ennen `[[LUOVUTUS]]`-lohkoja, joten paketin sisällä oleva teksti ei synny yksittäiseksi luovutukseksi.

**Sha.** Paketin sha256 lasketaan koko paketista: nimi, jatkettava paketti sekä jokaisen palan numero ja teksti. Lähetys vaatii vahvistuksen `kyllä <sha8>` tästä tiivisteestä.

**Vuotovartija.** Jokainen pala kulkee koukun `vartija_koukku` kautta, ja koukku käyttää yksittäisen luovutuksen mekaanista tarkistusta (`aivo/vartija.py`). Estotyyppi (sama lista kuin yllä) antaa palalle `sallittu=False`, ja merkintä kertoo tyypin. Muuten pala on ”kysy” (`sallittu=None`), koska vartijamalli ei tarkista paketin paloja. Estetty pala estää koko paketin lähetyksen, kunnes Tony poistaa palan. Lähetyshetkellä estotyypit lasketaan uudelleen samoista teksteistä Tonyn listojen kanssa, eikä palan vartija voi ohittaa niitä. Jos yksikin pala on ”kysy”, hyväksyntä vaatii lisävahvistuksen `tarkistin kohdat <sha8>` paketin sha:sta. Mikään lisäpala ei lähde automaattisesti, koska mikään pala ei saa tilaa `sallittu=True` ilman vartijamallia. Koukku ei koskaan palauta arvoa `True`, ja testi varmistaa sen (`aivo/test_luovutuspaketti.py`, KoukkuJaListatTesti). Koukku ja hyväksyntä lukevat Tonyn listat samasta paikasta kuin chat (`--vartija-listat` tai `AIVO_VARTIJA_LISTAT`, oletus `luovutukset/vartija/`).

**Laatikko.** Kaikki palat näkyvät allekkain vartijan merkintöineen. Painikkeet:

- "Poista pala n" poistaa yksittäisen palan. Paketti saa uuden sha:n, joten vanha vahvistus ei enää kelpaa (409). Pyynnössä on mukana Tonyn näkemä sha8, joten vanhentunut näkymä ei voi poistaa väärää palaa. Viimeistä palaa ei voi poistaa; silloin paketti hylätään.
- "Lähetä paketti" kysyy vahvistuksen ja lähettää `kyllä <sha8>` koko paketista.
- "Hylkää paketti" siirtää paketin kansioon `paketit/hylatyt/`. Mitään ei poisteta.

**Hyväksytty paketti.** Jokainen pala menee kirjaston kansioon `saapuneet/` omana tiedostonaan. Palan tunnus johdetaan paketin tunnuksesta ja palan numerosta, joten sama pala ei voi lähteä kahdesti. Tekstin ensimmäisellä rivillä ovat paketin tunnus ja palan numero:

```
<!-- luovutuspaketti tunnus=<paketti> pala=<n> nimi=<nimi> -->
```

Järjestys on sama kuin yksittäisessä luovutuksessa: lokirivit yksityiseen lokiin, tiedostot kirjastoon ja yksi julkaisu. Jos julkaisu epäonnistuu, kaikki paketin tiedostot poistetaan ja paketti jää odottamaan. Onnistuneen julkaisun jälkeen paketti siirtyy kansioon `paketit/hyvaksytyt/`.

**Paluu.** Asetus `AIVO_TULOS_REPO` (tai `--tulos-repo`) kertoo Aivon yksityisen tulosrepon polun tai kloonin. Palan n tulos on tiedosto `<tulosrepo>/<pakettitunnus>/pala-<n>.md`. Kun tulos ilmestyy, yksityisen tilan keskusteluun tulee merkintä `[Paketin X pala n valmis]` ja painike "Näytä tulos". Kukin tulos ilmoitetaan kerran (kirjanpito `paketit/paluut.json`). Symlinkkejä ei seurata, ja tulos saa olla enintään 50 000 tavua. Vain yksityinen tila lukee tulosrepoa. Palvelu ei käynnisty, jos tulosrepo on kirjaston, työkalutilan datan tai tiivistelmien sisällä tai niiden yläpuolella. Mallin vastauksessa oleva matkittu paluumerkintä neutraloidaan.

**Lisäpalat.** Hyväksyttyä pakettia voi jatkaa lohkolla `[[LISAPALA <pakettitunnus>]] teksti [[/LISAPALA]]`. Numerot jatkuvat paketin suurimmasta numerosta.

- `AIVO_LISAPALAT=kysy` (oletus): lisäpalat tallentuvat uudeksi paketiksi, joka vaatii oman hyväksynnän.
- `AIVO_LISAPALAT=automaattinen`: lisäpalat lähtevät ilman painallusta vain, jos vuotovartija on hyväksynyt jokaisen palan. Muuten ne jäävät odottamaan hyväksyntää. Automaattisesta lähetyksestä kirjataan yhteenveto Tonylle (`paketit/automaattiset.jsonl` ja keskustelu). Ilman vartijaa mikään ei lähde automaattisesti. Arvon vaihto on Tonyn päätös.

Reitit (kaikki vain yksityinen tila; työkalutilan keskustelulle 404):

| Reitti | Kuvaus |
|---|---|
| `GET /api/paketit?keskustelu=ID` | odottavat paketit: tunnus, nimi, sha8, palojen määrä |
| `GET /api/paketti?keskustelu=ID&tunnus=T` | paketin palat, vartijan merkinnät ja sha8 |
| `POST /api/paketti/poista-pala` `{keskustelu, tunnus, pala, sha8}` | palan poisto, uusi sha8 |
| `POST /api/paketti/hyvaksy` `{keskustelu, tunnus, vahvistus, lisavahvistus?}` | vahvistus täsmälleen `kyllä <sha8>`; ”kysy”-palat vaativat `lisavahvistus` = `tarkistin kohdat <sha8>`, estetty pala palauttaa aina 409 |
| `POST /api/paketti/hylkaa` `{keskustelu, tunnus}` | siirto kansioon `paketit/hylatyt/` |
| `GET /api/paluut?keskustelu=ID` | uudet tulokset ja merkinnät |
| `GET /api/paketti/tulos?keskustelu=ID&tunnus=T&pala=N` | hyväksytyn palan tulos tulosreposta |

## Palaute ja korjaukset yksityisessä tilassa

Yksityisen tilan jokaisen Aivon vastauksen alla on painikkeet **Hyvä** ja **Väärin**. Väärin-painike avaa luokkavalinnan ja tekstikentän korjaukselle (enintään 2 000 merkkiä). Luokat ovat kiinteät: oma rakenne ja toiminta, väärä tieto, muistin tai lähteiden käyttö, pyynnön tai ohjeen noudattaminen, kieli ja muoto sekä muu.

**Tallennus.** Palaute tallentuu yksityiseen kansioon `/var/lib/aivo-yksityinen/korjaukset` (0700) tiedostoon `korjaukset.jsonl` (0600). Rivit vain lisätään, mitään ei muuteta eikä poisteta. Vastauksen alku (200 merkkiä) otetaan palvelimen tallentamasta historiasta, ei selaimen lähettämänä. Palvelin ei kirjoita palautteen sisältöä lokiin, ja virheviesteissä on vain syy.

**Kehote.** Seuraavan viestin kehotteessa korjaukset ovat MUISTI-osiossa omana tiedostonaan `tonyn-korjaukset` (data, ei ohjeita). Osioon valitaan enintään 8 korjausta ja 3 000 merkkiä: ensin viestiin osuvimmat (sama sananalkuhaku kuin muistitiedostoissa), sitten uusimmat. Osion koko vähennetään muistin rajasta. Tonyn tekstistä neutraloidaan palvelimen merkintöjen ja lohkomerkintöjen matkinta.

**Toistuva korjaus → ehdotus.** Kun samassa luokassa on vähintään kaksi käsittelemätöntä korjausta eri vastauksiin, palvelin lisää osioon merkinnän `TOISTUVA KORJAUS` ja tiedostonimen `korjaukset-<luokka>.md`. Kehotteen sääntö 11 pyytää Aivoa silloin ehdottamaan sääntöä nykyisellä `[[EHDOTUS]]`-mekanismilla. Ehdotus näkyy tavallisena ydinohje-ehdotuksena, ja Tony hyväksyy (`kyllä <sha8>`) tai hylkää sen painikkeella. Mikään korjaus tai mallin teksti ei hyväksy ehdotusta. Kun ehdotus odottaa, samaa merkintää ei toisteta. Kun Tony hyväksyy tai hylkää tiedoston `korjaukset-<luokka>.md` ehdotuksen, luokan korjaukset merkitään käsitellyiksi. Ne näkyvät kehotteessa edelleen, mutta uusi merkintä tulee vasta kahdesta uudesta korjauksesta.

| Reitti | Tila | Kuvaus |
|---|---|---|
| `POST /api/palaute` `{keskustelu, indeksi, arvio: "hyva"}` | yksityinen | hyvä-arvio Aivon vastaukseen |
| `POST /api/palaute` `{keskustelu, indeksi, arvio: "vaarin", luokka, korjaus}` | yksityinen | korjaus Aivon vastaukseen |

Indeksin pitää osoittaa Aivon vastaukseen. Työkalutilan keskustelulle reitti vastaa 404.

**Käyttöönotto.** Kansio ja tiedosto luodaan käynnistyksessä yksityisen datan viereen (`--korjaukset` tai `AIVO_KORJAUKSET` vaihtaa polun). Käynnistys avaa tiedoston samoin liputuksin kuin tallennus, joten kirjoituseste näkyy heti palvelun käynnistyessä eikä vasta ensimmäisessä painalluksessa. Jos palvelu ei käynnisty ja journalissa on `korjaustiedostoa ei voi avata` tai `yksityista kansiota ei voi luoda`, luo kansio itse: `sudo install -d -m 700 -o aivo-yksityinen -g aivo-yksityinen /var/lib/aivo-yksityinen/korjaukset` ja tarkista, että yksikön `ReadWritePaths` kattaa sen.

## Asennuksen tilatarkistus (vain luku)

Päivityksen jälkeen kontissa 122 yksi komento kertoo, onko asennus kunnossa. Anna argumenttina odotettu commit (`git rev-parse HEAD` agentti-pohjan kloonista, jonka PR on mergattu):

```
runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.tarkista_asennus ODOTETTU_COMMIT
```

Komento tarkistaa asennetun commitin, chat-palvelun ja tiivistysajastimen tilan (`systemctl is-active`), reitin `/terve` portissa 8780, Aivon moduulien olemassaolon ja käännettävyyden sekä salasanatiedoston oikeudet. Moduulit luetaan lähdetiedostoina polusta, jonka `--koodi` antaa (oletus `/opt/agentti-pohja`), ja ne käännetään `compile`-kutsulla. Moduuleja ei tuoda, joten niiden tuontiaikaista koodia ei ajeta. Tarkistus ei siis löydä ajonaikaisia vikoja. Salasanatiedostoon käytetään vain `os.stat`ia: sisältöä ei avata eikä tulosteta.

`/etc/aivo` on `aivo:aivo` ja tilassa 700. Jos komennon ajaa muu käyttäjä kuin hakemiston omistaja (esimerkiksi palvelun `User=` on eri kuin `aivo`), `os.stat` ei pääse tiedostoon ja rivi on `VIKA avain /etc/aivo/chat-salasana: ei oikeutta tarkistaa (PermissionError)`. Komento ei kaadu. Aja silloin komento hakemiston omistajana (`runuser -u <omistaja> -- ...`). Paluuarvo on 0 vain, jos yhtään VIKA-riviä ei ole. OHITETTU-rivit eivät muuta paluuarvoa. Asentamattomassa ympäristössä tulos on esimerkiksi:

```
VIKA commit: asennettu 7c01f795a607, odotettu 141ab0075ba5
VIKA palvelu aivo-chat: inactive
VIKA ajastin aivo-tiivista.timer: inactive
VIKA terve /terve: ei yhteytta (ConnectionRefusedError)
OK   moduulit: 3 moduulia
VIKA avain /etc/aivo/chat-salasana: puuttuu
```

Valinnat `--koodi` (oletus `/opt/agentti-pohja`) ja `--portti` (oletus 8780) vaihtavat polun ja portin.

**Päivitys: aja tarkistus sekä ennen päivitystä että sen jälkeen.** Komento tarkistaa myös tiivistyksen kansiot `chat`, `tiivistelmat` ja `indeksi` kummankin alueen juuressa (`--yksityinen-juuri`, oletus `/var/lib/aivo-yksityinen`, ja `--tyokalu-juuri`, oletus `/var/lib/aivo`). Kansion pitää olla hakemisto, eikä ryhmällä tai muilla saa olla siihen oikeuksia. Puuttuvasta kansiosta tulostetaan korjauskomento `install -d -m 700 -o <omistaja> -g <omistaja> <polku>`. Omistaja luetaan alueen juuresta. Jos juurikin puuttuu, komennossa lukee `<omistaja>`, koska käyttäjänimi on vielä avoin päätös. Tarkistus ei luo eikä muuta mitään, eikä se avaa kansioiden tiedostoja. Jos yksityisen alueen juuri kuuluu eri käyttäjälle kuin komennon ajaja, rivi on `ei oikeutta tarkistaa (PermissionError)`.

**Taustaajo, Syvennä ja agenttipalvelu.** Komento tarkistaa myös nämä, jos ne on asennettu. Asennus tunnistetaan komennolla `systemctl show -p LoadState`. Jos yksikköä ei ole (`not-found`), tulostuu yksi OHITETTU-rivi. Jos yksikkö on olemassa mutta rikki (esim. `masked`), rivi on VIKA. Tarkistukset:

- `ajastin aivo-taustaajo.timer`: `systemctl is-enabled` on `enabled` ja `is-active` on `active`.
- `palvelu aivo-taustaajo.service`: viimeisen ajon `Result` on `success`. Arvo on `success` myös ennen ensimmäistä ajoa.
- `syvenna aivo-chat`: chatin asetuksissa on sama jono kuin `--tausta-jono` (oletus `/var/lib/aivo-yksityinen/tyot`), ja chat ja taustaajo ajetaan samana käyttäjänä. Jono luetaan chatin `ExecStart`-rivin valinnasta `--tausta-jono` tai ympäristömuuttujasta `AIVO_TAUSTA_JONO`. Ympäristön muita arvoja ei tulosteta.
- `taustajono <polku>`: kansio on olemassa, se ei ole linkki, sen oikeudet ovat 700 ja omistaja on taustaajon käyttäjä. Tarkistus käyttää vain `os.lstat`ia. Jonoa ei listata, eikä työ- tai vastaustiedostoja avata.
- `taustaajon tila`: tila luetaan taustaajon omalla funktiolla `lue_tila`. Rivillä näkyvät vain päivä, tänään ajettujen töiden määrä ja ajurin viimeksi käyttämä päiväraja. Rikkinäisestä tilatiedostosta tulostuu vain virheen tyyppi, ei sisältöä.
- `palvelu aivo-agentit.service`: `is-enabled` on `enabled` ja `is-active` on `active`.

Valinnat `--ohita-taustaajo` ja `--ohita-agentit` ohittavat tarkistukset, ja rivillä lukee silloin OHITETTU. Jono ja tilatiedosto kuuluvat taustaajon käyttäjälle. Jos chatin käyttäjä ei ole `aivo`, aja komento chatin käyttäjänä:

```
U=$(systemctl show -p User --value aivo-chat); echo "CHAT=$U"
runuser -u "$U" -- env PYTHONPATH=/opt/agentti-pohja python3 -m aivo.tarkista_asennus ODOTETTU_COMMIT
```

Esimerkki, kun taustaajo toimii mutta agenttipalvelu on kaatunut:

```
OK   ajastin aivo-taustaajo.timer: enabled, active
OK   palvelu aivo-taustaajo.service: viimeisin ajo success, nyt inactive
OK   syvenna aivo-chat: jono /var/lib/aivo-yksityinen/tyot, kayttaja aivo
OK   taustajono /var/lib/aivo-yksityinen/tyot: hakemisto, oikeudet 700, omistaja aivo
OK   taustaajon tila: paiva 2026-12-07, ajettu 2, raja 5
VIKA palvelu aivo-agentit.service: enabled, failed
```

Tarkistus ei tee malli- eikä verkkokutsuja. Se ajaa vain lukukomentoja `systemctl show`, `is-enabled` ja `is-active`.

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

## Pitkä vastaus ja katkennut yhteys

Yksityisen tilan vastaus voi kestää useita minuutteja (vastaus, itsetarkistus ja korjaus). Selaimen pyyntö kulkee `tailscale serve` -välityspalvelimen kautta (siksi palvelu näkee yhteyden osoitteesta `127.0.0.1`). Jos välityspalvelin tai puhelimen selain katkaisee yhteyden ennen vastausta, vastaus ei katoa:

- Palvelin tallentaa vastauksen keskusteluun ennen kuin se lähetetään selaimelle, ja vastauslukko vapautuu normaalisti.
- Katkos kirjataan journaliin yhdellä rivillä ilman tracebackia, esimerkiksi `aivo-chat: yhteys katkesi ennen vastausta (POST '/api/viesti', tila 200, BrokenPipeError)`. Rivillä ei ole viestien sisältöä.
- Selain näyttää 45 sekunnin jälkeen tekstin "Aivo vastaa vielä…" ja hakee keskustelun tilaa kolmen sekunnin välein reitiltä `GET /api/viimeisin?keskustelu=…` (vain luku: viimeisin Tonyn viesti, sen jälkeinen vastaus ja tieto, onko vastaus kesken). Haku alkaa heti, jos yhteys katkeaa aiemmin. Kun vastaus löytyy, se näytetään merkinnällä "Yhteys katkesi kesken; vastaus haettiin keskustelusta." Haku lopetetaan 15 minuutin jälkeen tai kun 20 peräkkäistä hakua epäonnistuu (verkko poikki). Jos keskustelun pituutta ei saatu ennen lähetystä, haettua vastausta ei näytetä, jotta aiemman samanlaisen viestin vastaus ei näy uutena.
- Uutta viestiä ei voi lähettää haun aikana, joten "edellinen vastaus on kesken" -virhettä ei tule.
- Haettuna vastauksena näytetään teksti ja malli. Valintanapit ja päätösehdotuksen kortti tulevat vain suorassa vastauksessa; ydinohje-ehdotukset näkyvät edelleen odottavien listassa.

Aikarajoja tai kutsurajoja ei muutettu. `tailscale serve` -välityspalvelimen omaa aikarajaa ei ole dokumentoitu. Sen voi mitata kontissa 122 esimerkiksi hitaalla testipalvelimella toisessa portissa.

## Testit

```
python3 -m unittest aivo.test_chat aivo.test_tiivista aivo.test_kalenteri_kytkenta aivo.test_yksityinen_hyvaksynta aivo.test_luovutus aivo.test_vartija aivo.test_vartija_paatos aivo.test_tarkista_asennus aivo.test_kulunakyma
```

Testit kattavat salasanan tiivisteen ja lukituksen, osoite- ja Host-rajat, CSRF-suojan, muistin lukemisen (ei linkkejä kirjaston ulkopuolelle), lähdeviitteiden tarkistuksen, päätöksen kirjaamisen sanasta sanaan, GitHub-pushin eston ja sen, että Claude-kutsu ei käytä työkaluja eikä välittäjiä.
