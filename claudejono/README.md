# Claude-jono

Pilvi-Claude ei pääse Tailscale-verkkoon, mutta molemmat pääsevät GitHubiin. Siksi tehtävät kulkevat docs-repon (`agenttifirma-docs`) kautta:

1. Pilvi-Claude kirjoittaa tehtävän `claude-jono/tehtavat/YYYY-MM-DD-NN-nimi.md`.
2. CT115:n ajastin (10 min edellisen ajon päättymisestä) ajaa `python3 -m claudejono ajo`: hakee repon, ottaa vanhimman tehtävän ilman tulosta, ajaa `claude -p` (tilaus), kirjoittaa `claude-jono/tulokset/<sama nimi>.md` ja pushaa vain sen. Yhteenveto Telegramiin, jos `/etc/yojono/telegram.env` on olemassa.
3. Pilvi-Claude lukee tulokset ja kirjoittaa seuraavat tehtävät (ajastettu tehtävä 3×/vrk).
4. **Itsesuunnittelu** (Tony hyväksyi 30.10.2026): kun jono on tyhjä, CT115:n Claude ehdottaa jatkotehtävät (`suunnittelu.md`). Ajuri nimeää ne `<pvm>-NN-auto-<nimi>.md`, rajaa enintään 2 / ajo ja 30 / vrk (`AUTO_MAX_PAIVA`, koodissa) ja kirjaa Tonyn päätettävät `claude-jono/tonylle.md`:hen. Pois päältä: `Environment=CLAUDEJONO_ITSESUUNNITTELU=0`. Tyhjän kierroksen jälkeen suunnittelu pitää tauon (oletus 3 h, `CLAUDEJONO_SUUNNITTELU_TAUKO_S`), ellei uusi tulos ole tullut.

Rajat (Tony hyväksyi 30.10.2026, doc 71): PR-taso, ei mergeä, ei katto-/oikeusmuutoksia, ei salaisuuksia, ei poistoja. Kehote: `kehote.md`. Ajuri committaa vain tulostiedostot, `-auto-`-tehtävät ja `tonylle.md`:n (testattu).

## Itsekehittyvä silmukka (Tonyn linjaus 2.11.2026 klo 16.10)

Kolme osaa, joiden koodi on `silmukka.py` ja `laatu.py`. Ne eivät muuta kulukattoja, oikeuksia eivätkä polkurajoja (`AUTO_MAX_PAIVA`, `AUTO_PER_AJO`, `sallittu_polku` ovat ennallaan).

1. **Jono ei tyhjene.** Kun avoimia tehtäviä on alle 5 (`silmukka.AVOIMIA_ALARAJA`), ajuri kutsuu itsesuunnittelua jo ennen kuin jono on tyhjä, ja ajaa silti vuorossa olevan tehtävän. Suunnittelun kaatuminen ei estä tehtävää. Suunnittelija kirjoittaa pisteytetyt tehtävät (`Pisteet:`- ja `Suunta:`-rivi). **Porttitarkistin** hylkää tehtävän, jos rivit puuttuvat. Tarkistin ohjaa tehtävän Tonyn sunnuntaiehdotuksiin (`tonylle.md`, rivi alkaa `Sunnuntaiehdotus (syy)`), jos se koskee julkaisua, rahaa, oikeuksia, kulukattoja, ajastuksia tai uutta suuntaa. Tarkistin on karkea sanalista, ja kieltolauseet ("ei saa muuttaa kattoja") ohitetaan. Se on lisävartija, ei ainoa raja: tehtävän suorittajaa sitovat silti kehotteen rajat ja ajurin polkuraja.
2. **Viikoittainen optimointikierros.** Pohja `toistuvat/optimointi.md` (tehtävä 43). Ajuri luo siitä tehtävän `<pvm>-NN-auto-optimointi.md`, kun edellisestä on 7 päivää eikä edellinen odota ajoa. Luonti ei tarvitse uutta ajastinta: se tapahtuu `claude-jono.service`-ajon alussa. Tehtävä kuuluu itse keksittyjen tehtävien päivärajaan.
3. **Päivittäinen laatuotos.** `python3 -m claudejono laatu` valitsee 2–3 satunnaista eilistä tulosta. Eri `claude -p` -istunto tarkistaa ne lähteitä vasten työkaluilla `Read`, `Grep` ja `Glob` (`--tools`, `--no-session-persistence`). Tulos kirjataan tiedostoon `$CLAUDEJONO_TYOHAK/laatu-tila.json` (ei docs-repoon). Vakava virhe vaatii todisteen (lähteen polku ja lainaus), muuten se lasketaan lieväksi. `python3 -m claudejono mittari` tulostaa aamukoosteen mittaririvin: otoksen tulos, hyväksytyt ja hylätyt PR:t, uudet virhelokimerkinnät ja puuttuvat "Todennettu käytössä" -rivit.
4. **Pysäytysehto.** Jos laatuotos löytää vakavan virheen kahtena kalenteripäivänä peräkkäin, itsesuunnittelu ja toistuvat tehtävät pysähtyvät ja Telegramiin lähtee `HALYTYS`-viesti. Pysäytys poistuu vain komennolla `python3 -m claudejono vapauta`. Jo jonossa olevat tehtävät ajetaan edelleen.

**Käyttöönotto (Tony, ajastinmuutos):** kopioi `systemd/claude-laatu.service.mall` ja `claude-laatu.timer.mall` hakemistoon `/etc/systemd/system/` ilman `.mall`-päätettä, aja `systemctl daemon-reload` ja `systemctl enable --now claude-laatu.timer`. Kokeile ensin: `systemctl start claude-laatu.service` ja `journalctl -u claude-laatu -n 20 --no-pager`. Aamukoosteen (PR #160) valmistuttua mittaririvin voi liittää koosteeseen kutsulla `claudejono.laatu.mittaririvi(tyohak, repo, nyt)`.

## Käyttöönotto CT115:ssä (Tony, kerran)

1. `claude` → `/status`: kirjautuminen tilauksella (ei API-avainta).
2. Deploy key: `install -d -m 700 /etc/claude-jono && ssh-keygen -t ed25519 -N '' -f /etc/claude-jono/deploy_key -C claude-jono-ct115`. Lisää `deploy_key.pub` GitHubissa: agenttifirma-docs → Settings → Deploy keys → **Allow write access**.
3. Klooni: `install -d /var/lib/claude-jono && GIT_SSH_COMMAND='ssh -i /etc/claude-jono/deploy_key -o IdentitiesOnly=yes' git clone git@github.com:tonyweckstrom1987/agenttifirma-docs.git /var/lib/claude-jono/docs` ja `git -C /var/lib/claude-jono/docs config user.name claude-jono-ct115`, `git -C /var/lib/claude-jono/docs config core.sshCommand 'ssh -i /etc/claude-jono/deploy_key -o IdentitiesOnly=yes'` + `user.email` (botin osoite).
4. Koodi: `git clone https://github.com/tonyweckstrom1987/agentti-pohja /opt/agentti-pohja` (tai päivitä).
5. Lupatila: aja kerran käsin `cd /opt/agentscope && claude -p "aja: git -C /opt/agentti-pohja log -1 --oneline" --output-format json --max-turns 3` ja katso, pysähtyykö komento lupakyselyyn. Jos pysähtyy, lisää palveluun `Environment=CLAUDEJONO_LUPATILA=<tila>` (esim. `auto`, jos versio tukee sitä headless-ajossa) ja testaa uudelleen.
6. Kuivakoe: `cd /opt/agentti-pohja && CLAUDEJONO_REPO=/var/lib/claude-jono/docs GIT_SSH_COMMAND='ssh -i /etc/claude-jono/deploy_key -o IdentitiesOnly=yes' python3 -m claudejono seuraava`.
7. Asenna: kopioi `systemd/*.mall` → `/etc/systemd/system/` ilman `.mall`-päätettä, `systemctl daemon-reload`, `systemctl start claude-jono.service` (yksi ajo, katso `journalctl -u claude-jono`), sitten `systemctl enable --now claude-jono.timer`.

## Oma käyttäjä (Tonyn päätös 30.10.2026)

Ajuri jää rootiksi (git, deploy key, Telegram), mutta agentti ajetaan käyttäjänä `claudejono` (`CLAUDEJONO_KAYTTAJA`, `runuser`). Agentti kirjoittaa vain `/opt/agentscope/tyo`:hon ja kotiinsa; se ei lue deploy keytä, botin tunnusta eikä voi muuttaa yöjonon kattoja. PR:t avataan `sudo -u prbotti /usr/local/sbin/claudejono-pr`:llä: `prbotti` yksin lukee tunnuksen, ja haarat haetaan sen omaan tilapäisrepoon, jotta agentin `.git/config` ei aja komentoja prbottina.

1. `sh /opt/agentti-pohja/claudejono/asenna-kayttaja.sh` (tulostaa tarkistuksen).
2. `runuser -l claudejono -c 'curl -fsSL https://claude.ai/install.sh | bash'` ja `runuser -l claudejono -c '~/.local/bin/claude'` → `/login` tilauksella.
3. Päivitä palvelu mallista (`CLAUDEJONO_KAYTTAJA`, `CLAUDEJONO_CLAUDE`), `systemctl daemon-reload`.

Peruutus: `systemctl disable --now claude-jono.timer` ja poista deploy key GitHubista.

## Rinnakkaiset kaistat (Tonyn päätös 3.11.2026: aloitus 2, valmius 3)

Kaksi tehtävää voi olla ajossa yhtä aikaa. Kolmas kaista on tuettu, mutta otetaan käyttöön vasta ensimmäisen viikon mittausten jälkeen.

**Miten se toimii**
- `python3 -m claudejono ajo --kaista N` (N = 1–3). Oletus on kaista 1. Kaistojen määrä tulee yhdestä asetuksesta `CLAUDEJONO_KAISTOJA` (oletus 1; palvelumallissa 2). Koodin ylärajaa 3 ei voi ohittaa asetuksella, ja kaista asetuksen yli hylätään ennen kuin mitään tehdään.
- Jokaisella kaistalla on oma lukko `lukko-N` docs-kloonin vanhemmassa hakemistossa: sama kaista ei ala kahdesti. Aiempi yhteinen `lukko` jää pois.
- **Tehtävän varaus:** ajuri luo tiedoston `varattu/<tehtävä>` (`O_EXCL`) docs-kloonin vanhempaan hakemistoon (ei gitiin). Kilpailijoista vain yksi onnistuu. Varaus vapautuu tuloksen kirjauksen jälkeen tai virheessä. Jos ajuri kuolee kesken, varaus vanhenee, kun Clauden aikaraja (`CLAUDEJONO_AIKARAJA_S`, oletus 45 min) + 15 min on kulunut tai varaajan prosessi ei enää ole olemassa.
- **Docs-repon kirjoitukset:** kaikki kloonin git-toimet (`pull --rebase`, valinta ja varaus, tuloksen `add`/`commit`/`push`) tehdään yhteisen lyhyen `git-lukon` sisällä. Lukkoa ei pidetä Clauden ajon aikana. Jos push silti törmää (esim. pilvi-Clauden push), ajuri tekee `pull --rebase` ja yrittää uudelleen enintään 3 kertaa; rebase-konflikti perutaan (`rebase --abort`), jotta klooni ei jää kesken.
- **Kaistakiinnitys (`Kaista: N`):** jos tehtävätiedostossa on rivi `Kaista: N` (rivi alkaa sanalla `Kaista:`, perässä yksi luku 1–3, mitään muuta ei rivillä), tehtävän ottaa vain kaista N. Kaista N ottaa ensin omat kiinnitetyt tehtävänsä (vanhin ensin) ja vasta sitten vapaat. Muut kaistat ohittavat kiinnitetyn tehtävän. Rivi etsitään tiedoston 20 000 ensimmäisestä tavusta, ja ensimmäinen rivi ratkaisee. Puuttuva, virheellinen tai kaistojen määrän (`CLAUDEJONO_KAISTOJA`) ylittävä arvo tulkitaan vapaaksi tehtäväksi, jotta tehtävä ei jää odottamaan kaistaa, jota ei ole käynnissä. Jos jäljellä on vain toisten kaistojen tehtäviä, kaista ei aloita itsesuunnittelua. Kiinnitys ei muuta kaistojen määrää, päiväkattoja eikä ajastimia.
- **Itsesuunnittelu:** vain yksi kaista suunnittelee kerrallaan (`varattu/_suunnittelu`), ja vapaa kaista suunnittelee, vaikka muut kaistat ovat töissä, kun varaamattomia avoimia tehtäviä on alle 2 × kaistat. Suunnittelu ei luo tehtävää, joka on jo jonossa tai tehty (nimi- ja sisältövertailu). Päiväkatto (`AUTO_MAX_PAIVA`) ja ajokohtainen raja ennallaan.
- **Työhakemistot:** kaista 1 käyttää hakemistoa `/opt/agentscope/tyo`, kaista 2 hakemistoa `/opt/agentscope/tyo/k2` ja kaista 3 hakemistoa `/opt/agentscope/tyo/k3`. Ajuri luo hakemiston ja antaa sen agenttikäyttäjälle, ja kehote kertoo agentille oman polun. Polut pysyvät `/opt/agentscope/tyo`:n alla, joten `claudejono-pr`:n polkuraja ei muutu.

**Käyttöönotto (Tony, kerran; ei tehdä automaattisesti)**

Tee tämä, kun mikään ajo ei ole käynnissä (`systemctl is-active claude-jono.service` → `inactive`), koska vanhan ajurin `lukko` ja uuden `lukko-1` eivät tunne toisiaan.
```
cd /opt/agentti-pohja && git pull
cd claudejono/systemd
cp claude-jono@.service.mall /etc/systemd/system/claude-jono@.service
cp claude-jono@.timer.mall /etc/systemd/system/claude-jono@.timer
cp claude-kaistamittaus.service.mall /etc/systemd/system/claude-kaistamittaus.service
cp claude-kaistamittaus.timer.mall /etc/systemd/system/claude-kaistamittaus.timer
systemctl daemon-reload
systemctl disable --now claude-jono.timer
systemctl enable --now claude-jono@1.timer claude-jono@2.timer claude-kaistamittaus.timer
systemctl list-timers 'claude-*'
```
Kolmas kaista (vasta mittausdatan jälkeen): vaihda `Environment=CLAUDEJONO_KAISTOJA=2` arvoon 3 tiedostossa `/etc/systemd/system/claude-jono@.service`, `systemctl daemon-reload` ja `systemctl enable --now claude-jono@3.timer`.
Peruutus: `systemctl disable --now claude-jono@2.timer claude-jono@3.timer`; kaista 1 jatkaa yksin. Täysi paluu vanhaan: `systemctl disable --now claude-jono@1.timer` ja `systemctl enable --now claude-jono.timer`.

Muistin lisäys (CT115, `pct set`) tehdään erikseen Tonyn toimesta hostilta; ajuri ei koske siihen. Kaksi rinnakkaista `claude -p` -ajoa kuluttaa muistia ja tilauksen käyttörajaa kaksinkertaisesti, ja mittaus kertoo, kumpi rajoittaa.

**Mittaus (viikko)**
- Ajuri kirjaa jokaisesta ajosta rivin tiedostoon `kaistaloki.jsonl`: kaista, kesto, onnistuiko, rajaan osuminen (arvaus virheilmoituksen tekstistä), kierrokset, Clauden ilmoittama API-arvo (arvio, ei lasku) ja git-lukon odotus.
- `claude-kaistamittaus.timer` kirjaa 15 min välein `/proc/loadavg`- ja muistiluvut tiedostoon `kuorma.jsonl`.
- Raportti: `python3 -m claudejono.kaistamittaus raportti --kirjoita /var/lib/claude-jono/viikko/kaistakoe-2026-11.md` (taulukot kaistoittain ja vuorokausittain, rinnakkaisuus, kuorma, muisti, yöjonon kulut ja ohjeellinen päätelmä). Tiedosto kirjoitetaan docs-kloonin ulkopuolelle samasta syystä kuin viikkoehdotus: ajuri saa committaa vain sallitut polut. Jos raportin haluaa docs-repoon, polkulistan laajennus on Tonyn päätös.
- Aamukoosteen ensimmäiselle riville tulee kaistaosa (`kaistat k1 5, k2 4, rinnakkain 80 min, rajaosumia 0, …`), kun `kaistaloki.jsonl` on olemassa. Rivejä on edelleen viisi.
- Tilauksen käyttörajasta ei ole suoraa lukua. Mitataan osumat ja kulutuksen arvio; nolla osumaa ei todista, ettei rajaa lähestytty.

## Mallien työnjako, kulutusmittari ja Tonyn varaus (Tonyn suunta 3.11.2026)

Jokainen tehtävä ajetaan halvimmalla mallilla, joka riittää laatuun.

**Mallin valinta** (`malli.py`, taulukko `TYYPPI_MALLI` on ainoa paikka):
1. Tehtävätiedoston rivi `Malli: opus|sonnet|openrouter:<alias>` (rivin alusta; tekstin seassa oleva maininta ei laukaise).
2. Puuttuessa tehtävän nimen tyyppi: suunnittelu, arkkitehtuuri, tarkastus ja vaikeat viat → Opus; tarkkailu, päiväkatsaus, tiivistelmä, luokittelu ja kauppa-analyysi → OpenRouter (vain jos tehtävä ei tarvitse työkaluja, kuten PR:ää tai gitiä).
3. Muuten Sonnet.
Sallitut mallit ovat koodissa. Tuntematon arvo hylätään ja malli päätellään tyypistä. OpenRouter-aliakset: `deepseek-flash`, `deepseek-pro`, `qwen-coder`. Jonon itsesuunnittelu ajetaan Opuksella.

**Reitit.** Claude-reitti ajaa `claude -p --model opus|sonnet`. OpenRouter-reitti kutsuu LiteLLM:ää ajurin omassa prosessissa (`litellm_ajo.py`), ilman työkaluja: se tuottaa vain tekstiraportin. Avain `CLAUDEJONO_LITELLM_KEY` luetaan ajurin ympäristöstä (Tony kirjoittaa tiedoston `/etc/claude-jono/litellm.env`, ks. palvelumallit), eikä se kulkeudu agentin ympäristöön. Samaan tiedostoon tulee myös osoite `CLAUDEJONO_LITELLM_URL=http://<LiteLLM-kontin osoite>:4000/v1` (pakollinen, ei oletusta, jottei sisäverkon osoite päädy repoon). Jos avainta tai osoitetta ei ole, OpenRouter-tehtävät jäävät jonoon eivätkä estä muita.

**Kulutusmittari.** Jokaisesta ajosta tulee rivi tiedostoon `/var/lib/claude-jono/kulutus.jsonl`: malli, tokenit (syöte, tuloste, välimuisti), kesto ja kustannusarvio. Claude-ajoissa kustannus on `total_cost_usd` (API-hintainen arvo; tilauksessa se ei ole lasku). OpenRouter-ajoissa kustannus tulee LiteLLM:n vastausotsakkeesta. Aamukooste näyttää eilisen kulutuksen malleittain ja viikon kertymän.

**Tonyn varaus.** Agenttien osuus Claude-tilauksen viikkorajasta on enintään 60 % (`CLAUDEJONO_AGENTTIEN_OSUUS_PROS`, yksi asetus). Prosenttia ei voi lukea koneellisesti, joten se kalibroidaan: Tony ilmoittaa asetussivun luvun "Tällä viikolla x % käytetty" 2–3 kertaa viikossa komennolla `python3 -m claudejono.varaus kalibroi <luku>`; aamukooste kysyy lukua, kun viimeisin on yli 48 h vanha. Mittari laskee prosenttimuutoksen ja agenttien API-arvon suhteen. Arvio on varovainen: koko muutos katsotaan agenttien käytöksi, joten osuus yliarvioidaan. Viikko vaihtuu lauantaina klo 21.59 (Europe/Helsinki), koska tilauksen viikkoraja nollautuu silloin; hetken voi muuttaa asetuksella `CLAUDEJONO_VIIKKO_VAIHTUU` (muoto `la 21:59`, virheellinen arvo antaa oletuksen). Viikon yli ei verrata. Menneen ilmoituksen voi kirjata: `python3 -m claudejono.varaus kalibroi 37 --aika 2026-11-03T16:11`.

**Käyttörajan pysäytys ja krediitit.** Kun Claude-reitti palauttaa viikkorajan täyttymisen (esim. "usage limit reached"), kaikki Claude-kaistat pysähtyvät seuraavaan viikon vaihtoon asti, koska käyttökrediitit ovat päällä ja uusi yritys voisi kuluttaa maksullisia krediittejä. Ohimenevä virhe (esim. ylikuormitus) pysäyttää vain tunniksi. Sama pysäytys tulee, kun jarruarvio ylittää rajan. Aamukooste kertoo pysäytyksen ja vaihtohetken. OpenRouter-reittiä pysäytys ei koske.
- alle 80 % osuudesta: normaali; 80 % tai yli: yksi Claude-kaista; osuuden täyttyessä: Claude-kaistat pysähtyvät; Tonyn ilmoittama kokonaiskäyttö 90 % tai yli: pysähtyminen.
- Ilman kalibrointia ei hidasteta, mutta aamukooste kysyy lukua. Tilan vaihtuessa Tony saa Telegram-ilmoituksen. OpenRouter-reittiä portti ei koske.
- **Käyttörajavirhe** pysäyttää kaikki Claude-kaistat tauoksi (`CLAUDEJONO_RAJA_TAUKO_S`, oletus 3600 s). Tehtävä jää jonoon (ei tulosta) enintään kolmeksi yritykseksi, minkä jälkeen se kirjataan epäonnistuneeksi. Uutta ajoa ei yritetä silmukassa.

**Laatu malleittain.** `kulutus.kirjaa_laatu(tila, malli, otos, virheita)` on laatuotoksen (tehtävä 44) kirjauspiste; ilman sitä virheprosentti lasketaan ajojen epäonnistumisista. `kulutus.siirtoehdotus` antaa viikkokatsaukseen ehdotuksen, jos OpenRouter-mallin virheprosentti on vähintään 15 prosenttiyksikköä Sonnetin yli (vähintään 10 ajoa). Se ei muuta mitään itse.

## Korjaaja: kaatuminen käynnistää diagnoosin (Tonyn toive 31.10.2026)

Valvottu autonomia: agentti **diagnosoi ja ehdottaa, Tony hyväksyy**. Ei uudelleenkäynnistyksiä, ei asetusmuutoksia, ei mergeä.

Ketju: valvottu yksikkö kaatuu → `OnFailure=korjaaja@%n.service` → `python3 -m claudejono.korjaaja <yksikkö>` (root) →
1. tarkistukset: nimi, valvottujen lista (`claude-jono`, `yojono`, `trendit-paiva`, `trendit-viikko`, `kauppatiimi-*`), silmukan esto,
2. toistoraja: sama yksikkö enintään 1 diagnoosi / 6 h; päiväkatto 8 diagnoosia / vrk yhteensä (kustannuskatto). Raja kirjataan *ennen* ajoa (`/var/lib/korjaaja/tila.json`), joten kaatuva diagnoosi ei kierrä sitä,
3. ajuri kerää rootina `journalctl -u <yksikkö> -n 80`, puhdistaa (ohjausmerkit pois, rivi max 400 merkkiä, yht. 16 kt, `<loki>`-tagit neutraloitu) ja antaa tekstinä. Agentti ei saa journal-oikeuksia tätä varten. Loki on **dataa**: kehote kieltää noudattamasta sen sisältämiä ohjeita,
4. Claude ajetaan käyttäjänä `claudejono` (runuser, kuten claude-jono), aikaraja 10 min (prosessiryhmä tapetaan), max 20 kierrosta,
5. tulos: `claude-jono/korjaukset/<pvm>-<yksikkö>.md` docs-repoon (ajuri committaa vain tämän polkumuodon, `ajuri.sallittu_polku` ei muutu) + Telegram-tiivistelmä. Selvä koodivika: agentti avaa korjaus-PR:n normaalisti `claudejono-pr`:llä.

Silmukan esto kolmessa kerroksessa: `korjaaja@.service`:llä ei ole `OnFailure=`-riviä; koodi hylkää `korjaaja*`-yksiköt; valvottujen lista ei sisällä niitä.

### Asennus (Tony, kerran; ei tehdä automaattisesti)
```
cp claudejono/systemd/korjaaja@.service.mall /etc/systemd/system/korjaaja@.service
for u in claude-jono yojono trendit-paiva trendit-viikko; do
  install -d /etc/systemd/system/$u.service.d
  cp claudejono/systemd/korjaaja-onfailure.conf.mall /etc/systemd/system/$u.service.d/korjaaja.conf
done
systemctl daemon-reload
```
Huom: `yojono.service.d` ja `trendit-*.service.d` sisältävät jo `OnFailure=virheilmoitus@%N.service`; useampi `OnFailure=` kertyy listaksi, molemmat laukeavat. Kuivakoe: `systemctl start korjaaja@trendit-paiva.service.service` (trendit-paiva on nyt `failed`).
Peruutus: poista drop-in-tiedostot ja `systemctl daemon-reload`.

Huom turvallisuudesta: `asenna-kayttaja.sh` lisää `claudejono`n ryhmään `systemd-journal` (diagnostiikkaan). Korjaaja ei sitä tarvitse; ryhmän voi poistaa Tonyn päätöksellä (ehdotus, ei muutettu tässä PR:ssä).

### Muut kontit (suunnitelma)
CT116/CT117 jne. eivät näy CT115:n systemd:lle. Vaihtoehto: kunkin CT:n oma `OnFailure`-yksikkö kirjoittaa lokin (rajattu, puhdistettu) docs-repon `claude-jono/korjaukset/syote/`-kansioon tai kutsuu tätä samaa moduulia SSH:n yli vain luku -avaimella. Päätös Tonylle; tässä PR:ssä vain CT115.

### Seuraava porras (Tonyn päätettäväksi, EI toteutettu)
Sallittu lista turvallisia toimia, esim. `systemctl reset-failed <yksikkö>` + **yksi** uudelleenkäynnistys, vain valvottujen listan yksiköille, ajurin (ei agentin) suorittamana ja vain kun agentin diagnoosi on luokiteltu tyyppiin "ohimenevä" (esim. verkkovirhe). Edellytys mittareille: vähintään ~10 diagnoosia, joista Tony on merkinnyt osuneiksi ≥ 80 % (lisää `korjaukset/`-tiedostoon rivi "Osuiko: kyllä/ei"), eikä yhtään väärää toimintaehdotusta. Toimi kirjataan lokiin ja rajataan 1 / yksikkö / vrk. Tämä on oikeusmuutos, joten se vaatii Tonyn nimenomaisen luvan erikseen.

## Jonon vahti: pysähtymisen hälytys (Tonyn päätös 4.11.2026)

20 tunnin katkos jäi huomaamatta, joten pysähtyminen havaitaan kahta tietä: ajurin oma vahti ja Uptime Kuma ulkopuolelta.

`python3 -m claudejono.vahti` ajetaan ajastimella 15 min välein (root, koska se käynnistää korjaajan):
1. Katsoo `kaistaloki.jsonl`:n viimeisen **onnistuneen** tehtävä- tai suunnitteluajon. Alle 2 h: `terve`.
2. Muuten tarkistetaan tarkoituksellinen tauko: pysäytystiedosto `/var/lib/claude-jono/pysayta` (Tony luo komennolla `touch`), kaikki `claude-jono@N.timer` pois päältä, käyttörajan tauko, kulutusjarru (varauksen `kaistoja` = 0) tai tyhjä jono (ei avoimia tehtäviä, ja itsesuunnittelu on pois tai sen tauko on käynnissä). Silloin tila on `tauolla`, eikä hälytetä.
3. Muuten `pysahtynyt`: Telegram-viesti (alkaa sanalla `HALYTYS`, joten `CLAUDEJONO_ILMOITUKSET=kooste` ei pidätä sitä) ja `systemctl start --no-block korjaaja@claude-jono.service.service`. Toistoeste 2 h (`vahti-tila.json`, kirjataan ennen toimia). Korjaajan omat rajat (1 diagnoosi / 6 h / yksikkö, 8 / vrk) pysyvät ennallaan.
4. Jos kaistalokia ei ole tai siinä ei ole yhtään onnistunutta ajoa, tila on `ei_dataa`: ei hälytystä eikä pingiä.

**Uptime Kuma:** vahti pingaa push-osoitetta vain, kun tila on `terve` tai `tauolla`. Pysähtyneenä pingiä ei lähde, ja Kuma hälyttää, kun heartbeat-aika ylittyy. Sama koskee tilannetta, jossa koko CT115 tai vahdin ajastin on alhaalla. Osoite luetaan muuttujasta `CLAUDEJONO_KUMA_PUSH_URL`; sitä ei tulosteta, ei kirjata eikä viedä repoon. Ping-virheestä näytetään vain poikkeuksen tyyppi.

### Asennus (Tony, kerran; ei tehdä automaattisesti)
1. Kuman monitori (vaatii kirjautumisen CT114:ään): Add New Monitor → tyyppi **Push** → nimi `claude-jono` → Heartbeat Interval **1800** s (2 ajoväliä) → Retries 1 → ilmoitus Telegramiin. Kopioi monitorin Push URL.
2. Osoite asetustiedostoon (tiedoston omistaja root, oikeudet 600), esimerkiksi editorilla: rivi `CLAUDEJONO_KUMA_PUSH_URL=<Push URL>`. Älä liitä osoitetta keskusteluun tai repoon.
3. Ajastin:
```
cd /opt/agentti-pohja/claudejono/systemd
cp claude-vahti.service.mall /etc/systemd/system/claude-vahti.service
cp claude-vahti.timer.mall /etc/systemd/system/claude-vahti.timer
systemctl daemon-reload && systemctl enable --now claude-vahti.timer
```
4. Kuiva-ajo (ei viestiä, ei korjaajaa, ei pingiä): `cd /opt/agentti-pohja && python3 -B -m claudejono.vahti --kuiva`. Oikea ajo: `systemctl start claude-vahti.service`; Kuman monitorin pitäisi muuttua vihreäksi.
5. Tarkoituksellinen pysäytys: `touch /var/lib/claude-jono/pysayta`; jatko: `rm /var/lib/claude-jono/pysayta`.
Peruutus: `systemctl disable --now claude-vahti.timer` ja poista yksikkötiedostot.

## Aamukooste ja ilmoitusten rajaus

**Aamukooste** (`python3 -m claudejono.aamukooste`) kokoaa yhden Telegram-viestin: enintään viisi riviä (tehty, tarvitaan sinulta, opittu, hälytykset, kulut) ja linkit. Lähteet ovat edellisen vuorokauden tulokset, `tonylle.md`:n uudet kohdat, avoimet PR:t (GitHubin julkinen API, ei tunnuksia), yöjonon tila (`tila.json`, `jono.json`, vain luku) ja systemd:n failed-yksiköt. Napit ovat GitHub-linkkejä: mergattavien PR:ien sivut (merge-painike on PR-sivulla). Jos GitHub-haku epäonnistuu (esim. 60 haun tuntiraja), rivi kertoo sen eikä näytä tyhjää listaa onnistumisena.

Kuiva-ajo (ei lähetä): `cd /opt/agentti-pohja && python3 -B -m claudejono.aamukooste`. Lähetys vain lipulla `--laheta`. Testibotti: aseta `YOJONO_TELEGRAM_TOKEN` ja `YOJONO_TELEGRAM_CHAT` testibotin arvoiksi tilapäisessä ympäristötiedostossa ennen ensimmäistä lähetystä.

**Ilmoitusten rajaus** (`CLAUDEJONO_ILMOITUKSET`): oletus `heti` säilyttää nykyisen käytöksen (kaikki viestit lähtevät heti). Arvolla `kooste` heti lähtevät vain viestit, jotka alkavat sanalla `HALYTYS` tai `PAATOS` (kaatumishälytys, yöjonon hälytysrivi, päätöstä vaativat asiat, `agentti.ilmoita virhe|paatos`). Muut tulos- ja raporttiviestit jäävät pois ja näkyvät aamukoosteessa. Tuntematon arvo tulkitaan arvoksi `heti`. Rajaus on yhdessä paikassa (`yojono.ajuri.laheta_telegram`), ja aamukooste ohittaa sen itse (`pakota=True`). Muut lähettäjät (`agentti.aamupaketti --laheta`, `agentti.ajo`) eivät noudata asetusta.

**Viikkoehdotus** (`python3 -m claudejono.viikkoehdotus`) kirjoittaa tiedoston `<vvvv-vv>-ehdotus.md` (hyöty × varmuus / vaiva). Ehdokkaat tulevat jonon tehtävistä, joilla ei ole tulosta ja joilla on `Pisteet:`-rivi, viimeisen 7 vuorokauden epäonnistuneista tai hylätyistä tehtävistä sekä valinnaisesta tiedostosta `claude-jono/ehdokkaat.md` (rivi: `- nimi | hyöty | varmuus | vaiva h | tavoite`). Pisteitä ei keksitä: välitavoite, jolle ei ole pisteytettyä ehdokasta, listataan erikseen aukkona. Oletushakemisto on `/var/lib/claude-jono/viikko` eli docs-kloonin ulkopuolella, koska ajuri saa committaa vain sallitut polut (`sallittu_polku`). Jos tiedoston haluaa docs-repoon, polkulistan laajennus on Tonyn päätös.

### Asennus (Tony)

```
install -d /var/lib/claude-jono/viikko
cd /opt/agentti-pohja/claudejono/systemd
for n in claude-aamukooste claude-viikkoehdotus; do
  cp $n.service.mall /etc/systemd/system/$n.service
  cp $n.timer.mall /etc/systemd/system/$n.timer
done
systemctl daemon-reload
systemctl start claude-aamukooste.service     # yksi testilähetys (testibotilla tai omaan chattiin)
systemctl enable --now claude-aamukooste.timer claude-viikkoehdotus.timer
systemctl list-timers 'claude-*'
```

Ajastus on Helsingin aikaa (`OnCalendar=... Europe/Helsinki`), joten kesäaika ei siirrä kellonaikaa vaikka palvelimen aikavyöhyke on UTC. Kääntäminen päälle: lisää `Environment=CLAUDEJONO_ILMOITUKSET=kooste` tiedostoon `claude-jono.service` (ja yojono-palveluun), `daemon-reload`. Peruutus: poista rivi tai aseta arvoksi `heti`.

## Riippumaton tarkastaja ja automerge v2 (Tonyn päätös 4.11.2026)

Kaikki PR:t yhdistyvät ilman Tonya, kun CI on vihreä ja riippumaton tarkastaja on hyväksynyt saman head-commitin. Poikkeus
on rajatut: oikea raha, salaisuudet, oikeudet ja kulukatot, työnkulut (`.github`), `tools/automerge.py`, palomuuri ja
julkaisu Tonyn nimissä. Ne yhdistää aina Tony. Säännöt ovat tiedostossa `tools/automerge.py`, ja agentit eivät voi muuttaa
niitä (`agentti/pr_kutsu.py`, `AGENTILTA_KIELLETYT_RE`).

Ketju:
1. `claude-tarkastaja.timer` ajaa 10 minuutin välein komennon `python3 -m claudejono.tarkastaja kierros` rootina.
2. Ohjelma ohittaa PR:t, jotka eivät muutenkaan kelpaa automergeen, joten tarkastusta ei kuluteta niihin.
3. Malli (oletuksena Opus, tilauksesta) arvioi diffin erillisenä käyttäjänä `tarkastaja` ilman työkaluja.
4. Ohjelma kirjoittaa PR:ään kommentin, jonka lopussa on merkki `riippumaton-tarkastus v1 sha=... paatos=...`.
5. Kommentti käynnistää Automerge-työnkulun. Se kytkee auto-mergen vain paatos=HYVAKSYTTY-merkinnälle ja vain samaan commitiin (`--match-head-commit`).

PR-avaaja: sama kierros avaa ensin botin tunnuksella PR:n jokaiselle Tonyn repon haaralle `korjaus/<nimi>`, jolla ei ole vielä PR:ää (ei myöskään suljettua). Näihin haaroihin pushaavat vain Tony ja pilvi-Claude. Enintään 10 avausta vuorokaudessa. Pois päältä: `CLAUDEJONO_PR_AVAAJA=0`.

Rajat: 40 tarkastusta vuorokaudessa, 6 per PR ja diff enintään 150 kt. Isompi diff on Tonylle. Kaikki epäselvä on Tonylle.

Käyttöönotto (Tony, kerran, CT115):
```
useradd --system --create-home --home-dir /var/lib/tarkastaja --shell /bin/bash tarkastaja
runuser -u tarkastaja -- bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
runuser -u tarkastaja -- /var/lib/tarkastaja/.local/bin/claude
```
Viimeinen komento avaa Clauden. Kirjaudu `/login`-komennolla samalla tilillä kuin claudejono ja poistu `/exit`-komennolla. Sen jälkeen:
```
cd /opt/agentti-pohja/claudejono/systemd
cp claude-tarkastaja.service.mall /etc/systemd/system/claude-tarkastaja.service
cp claude-tarkastaja.timer.mall /etc/systemd/system/claude-tarkastaja.timer
systemctl daemon-reload
cd /opt/agentti-pohja && python3 -m claudejono.tarkastaja kierros --kuiva
systemctl enable --now claude-tarkastaja.timer
```
Kuiva-ajo listaa tarkastettavat PR:t ajamatta mallia. Loki on tiedostossa `/var/lib/claude-jono/tarkastukset.jsonl`. Jos PR ei kelpaa tarkastukseen rakennesyystä, lokiin tulee yksi rivi per head-commit: `{"repo", "pr", "tulos": "OHITETTU", "syyt": [...], "sha", "aika"}`. Rivi ei kuluta tarkastusta eikä nosta hälytystä. Jos syyt muuttuvat ilman uutta committia, uutta riviä ei tule.
Peruutus: `systemctl disable --now claude-tarkastaja.timer`. Ilman tarkastajaa mikään ei yhdisty automaattisesti.
