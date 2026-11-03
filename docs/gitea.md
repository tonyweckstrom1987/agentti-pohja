# Gitea (CT113): GitHub-peilit ja yksityisen työn koti

Tila 3.11.2026. Tämä on ohje Tonylle. Ohje on kirjoitettu CT115:ssä. Gitean asetuksia ei ole muutettu eikä Giteaan ole kirjoitettu mitään; vain lukukutsut on ajettu (ks. kohta Todennettu käytössä). Kaikki kirjoittavat ajot tekee Tony itse.

## Lähtötiedot

- Gitea on kontissa CT113 (2 ydintä, 2 Gt, 8 Gt levy, yksi ylläpitäjätunnus). Tietokanta `gitea` on Postgresissa CT112:ssa. Lähde: docs-repon `01-infra.md`.
- Gitean versio on 1.23.6 (luettu 3.11.2026 rajapinnasta `<gitea>/api/v1/version`). Peilit ja siirrot (migraatiot) ovat käytössä. Tarkista versio tarvittaessa isännältä:

  ```
  pct exec 113 -- gitea --version
  ```
- Gitea on tarkoitus avata Tailscale-osoitteen kautta (sääntö: Tailscale per palvelukontti, `tailscale serve`). Kontin tarkkaa Tailscale-nimeä ei löytynyt dokumenteista; tässä ohjeessa sen paikalla on `GITEA`.
- Peilit ovat vain luku -kopioita. Gitea hakee muutokset GitHubista tunnin välein. Peileihin ei kirjoiteta Giteasta päin.

## a) Pull-peilit kaikista julkisista repoista

Peilien luontiin on suunniteltu skripti `tools/gitea_peilit.py` (vain vakiokirjasto). Se ei ole tässä repossa: `tools/` on PR-kutsun suojattu polku, eikä agentti voinut lisätä sitä. Ennen kuin Tony tuo skriptin käsin tai hyväksyy toisen paikan, peilin voi luoda yksitellen Gitean käyttöliittymässä (Uusi siirto, GitHub, valinta "Tämä kopio on peili"). Skripti tekisi näin:

1. Listaa käyttäjän `tonyweckstrom1987` julkiset repot GitHubin rajapinnasta. Tunnusta ei tarvita. Forkit jätetään pois, ellei anna valitsinta `--forkit`.
2. Tarkistaa, onko repo jo Giteassa, ja ohittaa sen. Skriptin voi siis ajaa uudelleen.
3. Luo puuttuvat peilit kutsulla `POST /api/v1/repos/migrate` (`mirror: true`, `mirror_interval: 1h0m0s`).
4. Peilit luodaan yksityisinä. Julkisuussääntö pysyy ennallaan: GitHub on julkinen näyteikkuna, Gitea on koti.

Turvallisuus:

- Kuiva-ajo on oletus. Se ei kirjoita Giteaan mitään. Peilit luodaan vasta valitsimella `--aja`.
- Gitea-token luetaan vain ympäristömuuttujasta `GITEA_TOKEN`. Skripti ei tulosta sitä, eikä sitä anneta komentoriville.
- Virheistä tulostetaan vain tyyppi ja HTTP-tila.
- Tokenin oikeudeksi riittää `write:repository`. Luo se Giteassa: Asetukset, Sovellukset. Älä anna sille ylläpitäjän oikeuksia.

Yksityiset repot (esimerkiksi docs-repo) vaativat GitHub-tokenin, jonka Gitea tallentaisi peilin asetuksiin. Tämä on Tonyn päätös, eikä skripti tee sitä. Vaihtoehto on siirtää yksityinen repo kokonaan Giteaan (kohta b), jolloin GitHub-tokenia ei tarvita.

### Tonyn ajo-ohje (7 komentoa)

Aja koneella, jolla on yhteys Giteaan (Tailscale päällä) ja jossa skripti on saatavilla (vasta kun Tony on tuonut sen):

```
pct exec 113 -- gitea --version                      # 1. isännällä: versio talteen
pct exec 113 -- df -h /                              # 2. isännällä: vapaa tila
read -rs GITEA_TOKEN && export GITEA_TOKEN           # 3. liitä token (ei näy ruudulla)
python3 tools/gitea_peilit.py --gitea https://GITEA  # 4. kuiva-ajo, lukee vain GitHubia
python3 tools/gitea_peilit.py --gitea https://GITEA --aja   # 5. luo peilit
unset GITEA_TOKEN                                    # 6. poista token ympäristöstä
pvesh get /cluster/backup --output-format json-pretty       # 7. isännällä: vzdump-työn sisältö
```

Komennot 1, 2 ja 7 ajetaan Proxmox-isännällä, komennot 3–6 koneella, jolla Gitea näkyy. Tarkista kuiva-ajon tuloste ennen komentoa 5.

## b) Yksityisen repon siirto Giteaan

Siirto ja käyttö:

1. Luo Giteaan tyhjä yksityinen repo (esimerkiksi `kauppatiimi-yksityinen`). Älä käytä peiliä, koska peilistä ei voi työntää.
2. Työnnä sisältö omalta koneelta: `git remote add gitea https://GITEA/OMISTAJA/<repo>.git`, sitten `git push gitea --all` ja `git push gitea --tags`.
3. Kun Gitea-kopio on todennettu (`git ls-remote gitea` näyttää samat haarat), poista GitHub-repo vasta Tonyn omalla päätöksellä. Agentit eivät poista mitään.
4. Siirrä tämän jälkeen repon `origin` osoittamaan Giteaan: `git remote set-url origin https://GITEA/OMISTAJA/<repo>.git`.

Miten claude-jono ja prbotti käyttäisivät Giteaa (ehdotus, ei käytössä):

- Gitealle luodaan oma botti-käyttäjä (`agenttifirma-bot`, tavallinen käyttäjä, ei ylläpitäjä). Sillä on oma token, jonka oikeus on vain `write:repository`.
- Ei forkkeja. Botti lisätään yksittäisen repon yhteistyökumppaniksi kirjoitusoikeudella ja se työntää `yo/`-haaroja suoraan samaan repoon. Päähaara suojataan Giteassa: suora työntö kielletty, yhdistäminen vain Tonylle. Näin botti ei voi muuttaa päähaaraa, vaikka koodin tarkistus pettäisi.
- Pull request luodaan kutsulla `POST /api/v1/repos/{omistaja}/{repo}/pulls`.
- Osoite on Tailscale-osoite, ei julkinen. CT115:n pitää nähdä Gitea tailnetissä.
- Nykyinen `agentti/pr_kutsu.py` osaa vain GitHubin ja sen `REPOT`-lista on kovakoodattu. Giteatuki vaatii koodimuutoksen, ja se kuuluu "Koodi on laki" -alueeseen (repolista, polkurajaukset, päiväkatto). Siksi muutosta ei ole tehty tässä PR:ssä. Tony päättää erikseen, tehdäänkö se.

## Yksityiset repot agenttialustalle ja Digi-Tonylle

Tonyn päätös 3.11.2026: arka sisältö kuuluu vain Giteaan. Tähän kuuluvat agenttialustan sisältö sekä Digi-Tonyn päätösmuisti ja profiili. Näitä ei viedä GitHubiin, ei myöskään yksityiseen GitHub-repoon eikä peiliksi. Yleinen tehtäväkoordinaatio ilman arkaa tietoa pysyy GitHubin yksityisessä docs-repossa, jotta pilvi-Claude toimii.

### Periaatteet

- Kaksi erillistä yksityistä repoa: yksi agenttialustalle (esimerkiksi `agenttialusta`) ja yksi Digi-Tonylle (esimerkiksi `digi-tony`). Erillisinä niille voi antaa eri käyttöoikeudet.
- Repot luodaan tyhjinä ja yksityisinä Giteassa. Peiliä ei käytetä (peiliin ei voi työntää), eikä Gitealle aseteta työntöpeiliä (push mirror) GitHubiin.
- Pääsy: vain Tailscale. Gitean osoite ei ole julkinen.
- Botti (`agenttifirma-bot`, tavallinen käyttäjä) saa kirjoitusoikeuden vain agenttialusta-repoon. Digi-Tonin repoon se ei saa oikeutta ennen kuin Tony erikseen päättää niin.
- Päähaara suojataan: suora työntö kielletty, yhdistäminen vain Tonylle.
- Salaisuuksia (avaimia, tunnuksia, `.env`-tiedostoja) ei talleteta näihin repoihin. Ne kuuluvat holviin.
- Pilvi-Claude ei näe näitä repoja. Siirrä sille vain yhteenveto ilman arkaa sisältöä.

### Tonyn ajo-ohje

Aja koneella, jolla Tailscale on päällä ja Digi-Tonin tai agenttialustan tiedostot ovat kloonattuina. Korvaa `GITEA` Gitean osoitteella ja `OMISTAJA` Gitean käyttäjätunnuksellasi.

1. Luo selaimessa Giteassa tyhjä yksityinen repo (Uusi repo, Näkyvyys: yksityinen, ei README:tä, ei lisenssiä).
2. Tarkista, että repo on yksityinen. Kirjautumatta (anonyymisti) listan pitää olla tyhjä:

   ```
   curl -s -D - -o /dev/null "https://GITEA/api/v1/repos/search?limit=1"
   ```

   Odotettu tuloste sisältää rivin `X-Total-Count: 0`. Jos luku on suurempi kuin 0, jokin repo on julkinen; tarkista näkyvyys.
3. Lisää Gitea toiseksi etäpalvelimeksi ja työnnä. Työntö kysyy käyttäjätunnusta ja salasanaa; salasanaksi annetaan Gitean henkilökohtainen token (oikeus `write:repository`), jota ei kirjoiteta komentoriville:

   ```
   git remote add gitea https://GITEA/OMISTAJA/digi-tony.git
   git push gitea --all
   ```

4. Varmista, että kopio on perillä, ja että GitHub-etäpalvelinta ei ole:

   ```
   git ls-remote gitea
   git remote -v
   ```

   Ensimmäinen listaa samat haarat kuin paikallinen repo. Toisen pitää näyttää vain `gitea`-osoitteet, ei `github.com`-osoitteita. Jos GitHub-osoite näkyy, poista se: `git remote remove origin` (vain kun olet varma, että Gitea-kopio on perillä).
5. Suojaa päähaara Giteassa: repon Asetukset, Haarat, Lisää suojaussääntö, haara `main`, työntö pois päältä.
6. Lisää botti yhteistyökumppaniksi (Asetukset, Yhteistyökumppanit) vain agenttialusta-repoon.

Ota snapshot ennen kohtia 5 ja 6, jos muutat samalla muita asetuksia. Varmuuskopion kattavuus on kohdassa d.

## c) Gitea Actions (act_runner) yksityisille testeille

Vaatimukset:

- Actions on otettava käyttöön Gitean asetuksissa (`app.ini`, osio `[actions]`, `ENABLED = true`). Tarkista, että versio tukee sitä; versio selviää kohdan a komennosta 1.
- Ajaja (`act_runner`) rekisteröidään Gitealle rekisteröintitunnuksella, jonka Tony luo itse Gitean ylläpitonäkymässä. Tunnusta ei kirjoiteta mihinkään tiedostoon, joka päätyy repoon.
- Ajajalle luodaan oma kontti (esimerkiksi CT119, unprivileged), ei CT113:een. Docker kontin sisällä on vastoin arkkitehtuurisääntöä (natiiviasennukset), joten käytä ajajan `host`-tunnistetta: työt ajetaan suoraan ajajakontissa tavallisena käyttäjänä.

Riskit:

- Työn koodi ajetaan ajajalla. Kuka tahansa, joka voi työntää repoon työnkulun, voi ajaa koodia ajajakontissa. Siksi Actions kytketään päälle vain yksityisille repoille, joihin kirjoittaa vain Tony ja botti.
- Ei avaimia: ajajakonttiin ei laiteta holvin tunnuksia, LiteLLM-avaimia eikä GitHub-tunnuksia. Giteaan ei tallenneta tuotantosalaisuuksia (Actions-salaisuudet).
- Verkko: ajajakontilla ei ole Tailscalea eikä pääsyä muihin kontteihin. Palomuuri sallii liikenteen vain Giteaan (rekisteröinti ja työt) ja ulos pakettien latausta varten. Palomuurisäännöt on Tonyn muutettavissa, eivät agenttien.
- Testit eivät saa käyttää oikeaa rahaa tai kauppa-API:a. Kauppatiimin testit ajetaan vain vanhoilla aineistoilla.
- Ota snapshot ennen ajajan asennusta ja sen jälkeen.

## d) Varmuuskopio ja levytila

Varmuuskopiot (docs-repon `01-infra.md`): yöllinen vzdump-työ "yo-varmuuskopio" klo 02:30 kattaa kaikki kontit (keep-last=3). Rclone-kopio lähtee klo 04:00 salattuna Google Driveen. Tämä pitäisi kattaa sekä CT113 (Gitean tiedostot, repot) että CT112 (Gitean tietokanta). Älä silti luota pelkkään dokumenttiin. Varmista asia komennolla 7:

- Työn `vmid`-kentässä pitää olla joko `all` tai 112 ja 113. Jos kentässä on luettelo, jossa 113 puuttuu, lisää se.
- Gitean repot ovat CT113:ssa ja metatiedot (käyttäjät, tiketit, PR:t) CT112:n kannassa. Kummankin pitää olla samassa ajossa, jotta palautettu tila on yhtenäinen.
- Palautusta ei ole vielä kokeiltu oikeasti (docs: vain tarkistussumma). Kokeile palautusta kerran uuteen tunnukseen ennen kuin Giteaan siirretään ainoa kopio yksityisestä työstä.

Levytila-arvio (GitHubin `size`-kentästä, 2.11.2026, rajapinta `users/tonyweckstrom1987/repos`):

| Mitta | Arvo |
|---|---|
| Julkisia repoja (ei forkkeja) | 21 |
| GitHubin ilmoittama koko yhteensä | noin 7,5 Mt |
| Suurimmat | tonyweckstrom1987.github.io 3,1 Mt, software-developer-portfolio 1,6 Mt, agentti-pohja 0,8 Mt |
| Arvio Giteassa (kerroin 3: tietokannan merkinnät, pakkaamattomat objektit, kasvu) | noin 25 Mt |
| CT113:n levy | 8 Gt |

`size` on likiarvo (ei sisällä esimerkiksi LFS-tiedostoja), mutta peilit mahtuvat reilusti: alle 1 % kontin levystä. Vzdump-arkisto kasvaa saman verran ja kolmen arkiston säilytys alle 100 Mt. Levytilaa kannattaa seurata muusta syystä: ohuen levypoolin (thin pool) snapshotit täyttävät sitä, ja `01-infra.md` mainitsee ylivarauksen. Tarkista vapaa tila komennolla 2 ja poolin käyttö ennen uusia snapshotteja.

## Todennettu käytössä

Todennettu käytössä: osittain. Ohjeen lukukomennot on ajettu CT115:stä Gitean rajapintaa vasten 3.11.2026 (vain GET, ei kirjautumista):

- `GET /api/v1/version` palautti `{"version":"1.23.6"}`.
- `GET /api/v1/repos/search?limit=50` palautti HTTP 200 ja `X-Total-Count: 0`: anonyymisti ei näy yhtään repoa.
- `GET /api/v1/user` ilman tunnusta palautti HTTP 401.
- `GET /api/v1/settings/repository` kertoi, että peilit ja siirrot eivät ole pois käytöstä (`mirrors_disabled: false`, `migrations_disabled: false`).

Ei vielä todennettu (vaatii Tonin tokenin, ei ajettu): repojen luonti, `git push gitea`, haaran suojaus, botin oikeudet, peilien luonti ja palautus varmuuskopiosta. Rivi päivitetään muotoon "kyllä", kun Tony on ajanut kohdat 1–4 ja ilmoittanut tuloksen.

## Avoimet kysymykset Tonylle

1. Gitean Tailscale-osoite (versio on jo selvitetty: 1.23.6).
2. Peilataanko myös yksityinen docs-repo (vaatii GitHub-tokenin Giteaan) vai siirretäänkö se kokonaan Giteaan?
3. Tehdäänkö botille Giteatuki `pr_kutsu.py`:hin (muutos "Koodi on laki" -alueelle, vaatii erillisen luvan)?
4. Rakennetaanko act_runner omaan konttiin heti vai vasta, kun ensimmäinen yksityinen testi tarvitsee sitä?
