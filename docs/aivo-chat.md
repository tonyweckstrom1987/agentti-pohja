# Aivon oma keskustelusivu (A705–A706)

Yksityinen tila ja ydinohjeet: katso `docs/aivo-yksityinen.md`.

Tämä ohje on Tonylle. Sivu on pieni oma palvelu (`aivo/chat.py`, vain Pythonin vakiokirjasto). Claude on kirjoittanut koodin, ja Tony ottaa sen käyttöön itse: palvelu on uusi, se koskee Aivon oikeuksia ja siinä on salasana. Tony asettaa salasanan itse. Mitään salaisuutta ei ole repossa.

## Mitä sivu tekee

- Tony kirjautuu salasanallaan ja keskustelee Aivon kanssa. Sivu toimii puhelimen selaimessa (kapea asettelu, tumma ja vaalea teema).
- Vastaus tulee vain Claudelta suoraan (`claude -p`, ei työkaluja). LiteLLM:ää, OpenRouteria tai kilpailutusta ei käytetä (A329).
- Ennen jokaista vastausta Aivo lukee muistinsa: päätöskirjaston, kompassin, haastattelut ja faktat. Tiedostot luetaan kirjaston kansiosta tässä järjestyksessä (nimi sisältää `paatos`, `kompassi`, `haastattel`, `fakta`, sitten muut `.md`- ja `.txt`-tiedostot). Yläraja on noin 300 kt; ylimenevät tiedostot jäävät pois.
- Aivo merkitsee muistista otetut väitteet muodossa `[lähde: tiedosto]`. Palvelin tarkistaa viitteet: jos tiedostoa ei ole muistissa, viite saa merkinnän `EI LÖYDY MUISTISTA`.
- Keskustelut tallentuvat isännälle tiedostoihin (`/var/lib/aivo/chat/*.jsonl`, oikeudet 600). Mitään ei lähetetä GitHubiin.
- **Päätökset:** jokaisessa Tonyn viestissä on painike "Kirjaa päätökseksi". Painikkeen painallus liittää Tonyn viestin sanasta sanaan lainauksena kirjaston tiedostoon `aivo-paatoskirjasto.md` ja pushaa kirjaston Giteaan. Aivon omia lauseita tai päätelmiä ei kirjata päätökseksi koskaan. Kirjaston etärepo ei saa osoittaa GitHubiin; muuten koodi kieltäytyy pushaamasta.

## Turvarajat

- Palvelu kuuntelee vain osoitteessa `127.0.0.1`. Koodi kieltäytyy käynnistymästä muussa kuin loopback- tai Tailscale-osoitteessa.
- Tailnetiin sivu avataan komennolla `tailscale serve` (HTTPS, vain tailnetin laitteet). Älä käytä `tailscale funnel` -komentoa: se avaisi sivun internetiin.
- Evästeessä on `HttpOnly`, `Secure` ja `SameSite=Strict`. Muutospyynnöt vaativat oman otsakkeen ja saman alkuperän. Väärällä `Host`-otsakkeella sivu vastaa 421.
- Viisi väärää salasanaa lukitsee kirjautumisen viideksi minuutiksi. Istunnot ovat vain muistissa (palvelun uudelleenkäynnistys kirjaa ulos).
- Salasanasta tallennetaan vain scrypt-tiiviste tiedostoon, jonka oikeudet ovat 600. Lokiin ei kirjoiteta viestejä eikä salasanoja.
- Muistin tiedostot ovat dataa, eivät ohjeita. Kehote kieltää noudattamasta niiden sisältämiä käskyjä.

## Käyttöönotto (Tony ajaa, isännällä, jolla on Tailscale ja `claude`)

Ota ensin tilannekuva, jos palvelu asennetaan kontin sisään. Tämä ohje ei muuta palomuureja, holvia eikä Tailscalen asetuksia, paitsi lisää yhden `tailscale serve` -säännön (vaihe 6).

1. **Käyttäjä ja kansiot** (sama `aivo`-käyttäjä kuin Garmin- ja Drive-ohjeissa):

   ```
   sudo useradd --system --create-home --home-dir /var/lib/aivo --shell /usr/sbin/nologin aivo
   sudo install -d -m 700 -o aivo -g aivo /var/lib/aivo/chat /etc/aivo
   sudo chown aivo:aivo /etc/aivo && sudo chmod 700 /etc/aivo
   ```

   Jos käyttäjä on jo olemassa, `useradd` antaa virheen. Se ei haittaa.

2. **Kirjasto Gitean kautta.** Kloonaa Aivon kirjasto Giteasta (`GITEA` on Gitean osoite tailnetissä; tunnus ja avain ovat Tonyn):

   ```
   sudo -u aivo git clone https://GITEA/aivo/aivo.git /var/lib/aivo/kirjasto
   ```

   Tarkista, ettei `origin` osoita GitHubiin: `sudo -u aivo git -C /var/lib/aivo/kirjasto remote -v`.

3. **Salasana.** Aja itse ja kirjoita salasana kahdesti (vähintään 12 merkkiä). Mitään ei tulostu:

   ```
   sudo -u aivo python3 -m aivo.chat aseta-salasana
   ```

   Aja komento koodin hakemistossa (`PYTHONPATH` osoittaa agentti-pohjan juureen).

4. **Claude-kirjautuminen `aivo`-käyttäjälle.** Aivon `claude` tarvitsee oman kirjautumisen (`sudo -u aivo -H claude`, tee kirjautuminen kerran). Tarkista, että tämä toimii:

   ```
   echo "sano kyllä" | sudo -u aivo -H claude -p --tools "" --max-turns 1
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
   ExecStart=/usr/bin/python3 -m aivo.chat palvele --host TAILNET-NIMI
   Restart=on-failure
   NoNewPrivileges=true
   ProtectSystem=strict
   ReadWritePaths=/var/lib/aivo
   PrivateTmp=true

   [Install]
   WantedBy=multi-user.target
   ```

   Vaihda `AIVO_CLAUDE` oikeaan polkuun (`command -v claude`) ja `TAILNET-NIMI` palvelun täydelliseen tailnet-nimeen (`tailscale status --self --json`, kenttä `DNSName`, ilman loppupistettä). `--host` vaatii täsmällisen nimen: jokerimerkintä (`.pääte`) hylätään käynnistyksessä, ja ilman `--host`-valitsinta sallitaan vain `localhost`. Tämä pienentää DNS-rebinding-riskiä, koska toisen tailnetin nimet eivät kelpaa.

   **`NoNewPrivileges=true` pysyy.** Jos käytät yksityisen tilan ohjeen mukaista työkalukäyttäjää (`aivo-tyokalu`), ei tarvita `sudoa` eikä sudoers-riviä. Työkalutilan `claude` ajetaan erillisenä systemd-palveluna omana käyttäjänään, ja chat-palvelu puhuu sille unix-socketin kautta. Ohje on kohdassa 5b. Jos et käytä työkalukäyttäjää, jätä `AIVO_TYOKALU_SOCKET` asettamatta, jolloin `claude` ajetaan suoraan `aivo-yksityinen`-käyttäjänä.

5b. **Työkalutila omana käyttäjänään (ei sudoa).** Kaksi tiedostoa. Komento on kiinteä yksikkötiedostossa: chat-palvelu voi lähettää socketille vain tekstiä, ei valitsimia eikä komentoja.

   `/etc/systemd/system/aivo-tyokalu.socket`:

   ```
   [Unit]
   Description=Aivon tyokalutilan Claude-socket

   [Socket]
   ListenStream=/run/aivo-tyokalu.sock
   Accept=yes
   MaxConnections=2
   SocketUser=aivo-yksityinen
   SocketGroup=aivo-yksityinen
   SocketMode=0600

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

   Komento on koko polulla (`command -v claude`). Sitten `sudo systemctl daemon-reload && sudo systemctl enable --now aivo-tyokalu.socket`, ja chat-palvelun yksikköön rivi `Environment=AIVO_TYOKALU_SOCKET=/run/aivo-tyokalu.sock`. Vain `aivo-yksityinen` pääsee socketille (tila 0600). Vanha `AIVO_TYOKALU_KAYTTAJA`-asetus on poistettu: koodi kieltäytyy käynnistämästä kutsua, jos se on asetettu, jotta eristys ei heikkene huomaamatta.

   Tarkistus: `sudo -u aivo-yksityinen python3 -c "import socket;s=socket.socket(socket.AF_UNIX);s.connect('/run/aivo-tyokalu.sock');s.sendall(b'sano kyllä');s.shutdown(socket.SHUT_WR);print(s.recv(200))"` tulostaa vastauksen. Toisena käyttäjänä (`sudo -u nobody ...`) yhteys antaa `PermissionError`.

   Sitten:

   ```
   sudo systemctl daemon-reload && sudo systemctl enable --now aivo-chat
   ```

6. **Avaus tailnetiin** (HTTPS, vain tailnet). Komennon muoto riippuu Tailscalen versiosta; tarkista `tailscale serve --help`:

   ```
   sudo tailscale serve --bg 8780
   tailscale serve status
   ```

   Tilan pitää näyttää osoite, jonka loppu on `.ts.net`, ja merkintä "tailnet only". Jos näet sanan "Funnel on", sammuta se: `tailscale funnel reset`.

## Todennus (Tonyn ajettavaksi, sen jälkeen riippumaton tarkastaja)

Tätä ei ole vielä ajettu oikeassa ympäristössä: palvelinta ei ole asennettu isännälle eikä Tailscalea ole tässä kontissa.

1. Avaa puhelimella (Tailscale päällä) tailnet-osoite, kirjaudu ja kysy "mitä tiedät minusta?". Vastauksessa pitää olla `[lähde: ...]`-viitteitä oikeisiin tiedostoihin.
2. Sulje puhelimen Tailscale (tai käytä mobiilidataa ilman Tailscalea) ja avaa sama osoite. Sivun ei pidä aueta.
3. Paina yhden oman viestisi kohdalla "Kirjaa päätökseksi" ja tarkista, että `aivo-paatoskirjasto.md` sisältää viestin sanasta sanaan ja että muutos näkyy Giteassa (ei GitHubissa).
4. Isännällä: `ss -ltnp | grep 8780` näyttää vain `127.0.0.1:8780`.

## Testit

```
python3 -m unittest aivo.test_chat
```

Testit kattavat salasanan tiivisteen ja lukituksen, osoite- ja Host-rajat, CSRF-suojan, muistin lukemisen (ei linkkejä kirjaston ulkopuolelle), lähdeviitteiden tarkistuksen, päätöksen kirjaamisen sanasta sanaan, GitHub-pushin eston ja sen, että Claude-kutsu ei käytä työkaluja eikä välittäjiä.
