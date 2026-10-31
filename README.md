# agentti-pohja

> **Tapaustutkimus / Case study:** [Turvallisesti itseohjautuva agenttijono kotipalvelimella](docs/tapaustutkimus.md) (suomi + English)

Yhteinen runko ajastetuille tekoälyagenteille: päivittäinen tiivistäjä, uutisseuranta, raportti, mikä tahansa "kerää dataa, tiivistä mallilla, lähetä Telegramiin" -agentti. Vain Pythonin standardikirjasto, ei riippuvuuksia, ei `pip install` -vaihetta palvelimella.

Pohja koostuu osista, jotka jokainen agentti tarvitsee ja jotka on helppo tehdä väärin:

| Osa | Tiedosto | Mitä se takaa |
| --- | --- | --- |
| Kulukatto | `kulu.py`, `rajat.py` | Päivä- ja kuukausikatto sekä kutsuraja. Kulu varataan **ennen** kutsua, joten katto pitää myös virhetilanteessa. Rikkinäinen kirjanpito pysäyttää kutsut eikä nollaudu hiljaa. |
| LLM-asiakas | `llm.py` | OpenAI-yhteensopiva (esim. LiteLLM). Ei työkaluja, `max_tokens`-katto, ei uudelleenohjausten seurantaa (avain ei karkaa), virheet koodeina: `llm_aikakatkaisu`, `llm_http_429`, `llm_vastaus_katkaistu`... |
| "En tiedä" | `tulos.py`, `skeema.py` | Vastaus on `ok` tai `ei_tietoa` + syy. Molemmat ovat normaaleja lopputuloksia. Muu kuin skeemaan sopiva vastaus hylätään. |
| Epäluotettava data | `data.py` | Ulkoinen data annetaan mallille `<data>`-lohkossa, jota sen sisältö ei voi sulkea, ja mallilla ei ole työkaluja. Prompt injection voi korkeintaan vääristää tekstiä. |
| Lähetys | `telegram.py`, `ajo.py` | Vastaanottaja tulee vain ympäristömuuttujasta, ei datasta tai mallilta. Lähetys korkeintaan kerran päivässä, ja epäonnistunut lähetys ei kuluta päivän lähetystä. |
| Valvonta | `sydan.py`, `ajo.py` | Virhe antaa paluukoodin 1, ja sydämenlyönti (esim. Uptime Kuma push) pingataan vain onnistuneen ajon jälkeen. |
| Ajastus | `systemd/*.mall` | Kovennettu oneshot-palvelu ja ajastin: oma käyttäjä, `ProtectSystem=strict`, ei oikeuksia, vain yksi kirjoitettava hakemisto. |

## Periaatteet

1. **Koodi on laki.** Rahaa ja turvallisuutta koskevat rajat ovat `rajat.py`:ssä kovakoodattuina, eivät prompteissa. Agentti, malli tai komentorivi ei voi muuttaa niitä; muutos vaatii omistajan luvan ja oman commitin.
2. **"En tiedä" on kelvollinen vastaus.** Pakotettu arvaus on huonompi kuin rehellinen `ei_tietoa`.
3. **Oletus on kuiva-ajo.** Verkkoa ja rahaa käytetään vasta, kun `--llm` ja `--send` annetaan erikseen.
4. **Vika näkyy.** Virheessä lähtee varaviesti raakadatasta ja virhekoodista, ja ajo päättyy koodiin 1.

## Pikakokeilu

Vaatii Python 3.10+ (CI ajaa versiot 3.10-3.13; Linux tai macOS, koska kulukirjanpito käyttää `flock`-lukkoa). Muita riippuvuuksia ei ole.

```bash
python -m agentti                 # kuiva-ajo: kerää datan, ei verkkoa
python -m unittest discover -s . -t . -p 'test_*.py'
```

Kuiva-ajo ajaa oletuksena esimerkkiagentin (levytila ja kuorma) ja tulostaa tekstin, jonka ensimmäinen rivi on `[kuiva-ajo, LLM ei kaytossa]`. Tilatiedostot (kulukirjanpito, lähetysmerkit) tallentuvat hakemistoon `tila/`; sen voi vaihtaa valitsimella `--tila` tai ympäristömuuttujalla `AGENTTI_TILA`.

Mallin kanssa (`LLM_BASE_URL` ja `LLM_API_KEY` ympäristössä tai `--env`-tiedostossa):

```bash
python -m agentti --llm           # kutsuu mallia, kulukatto voimassa
python -m agentti --llm --send    # lisäksi Telegramiin (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
```

Muut valitsimet: `--agentti NIMI` valitsee agentin (oletus `esimerkki`) ja `--env TIEDOSTO` lukee `KEY=VALUE`-rivit; ympäristömuuttujat ohittavat tiedoston arvot. Sydämenlyönnin osoite annetaan muuttujalla `SYDAN_URL`. Jos `--llm` annetaan ilman `LLM_BASE_URL`- ja `LLM_API_KEY`-asetuksia, ajo päättyy koodiin 1 ja virhekoodiin `llm_asetus`.

Paluukoodit: `0` ok, `1` ajo epäonnistui (virhekoodi stderriin), `2` käyttövirhe (esim. tuntematon agentti tai env-tiedosto puuttuu).

## Uusi agentti

```bash
python luo.py uutiset
```

Tämä luo `agentti/agentit/uutiset.py` (kopio esimerkistä), `test/test_uutiset.py` (perustestit mallista `test/agentti.test.mall`) ja `systemd/agentti-uutiset.service` + `.timer`, ja tulostaa kustakin rivin `luotu: <polku>`. Komento ei ylikirjoita mitään: jos jokin kohdetiedosto on jo olemassa, se päättyy virheeseen ja koodiin 2. Nimen on oltava 2-31 merkkiä (pienet kirjaimet, numerot ja `_`, alkaa kirjaimella), eikä `esimerkki` kelpaa. Uuden agentin voi ajaa heti: `python -m agentti --agentti uutiset`.

Jos agentilla on määritys (`maaritys.esimerkki.json`), anna se mukaan: `python luo.py uutiset --maaritys oma.json`. Määritys tarkistetaan ensin (kulu- ja tokenkatto, vyöhyke, oikeustaso), ja vasta sen jälkeen luodaan lisäksi `maaritykset/uutiset.json`, ohjetiedoston runko ja `evalit/uutiset.json` (eval-runko, jossa etukäteen sovittu mittari on mukana). Virheellinen määritys ei luo mitään.

Muokkaa agentin moduulista nämä:

- `MALLI` on käytettävän mallin nimi
- `kerays()` hakee datan (tähän tulee agentin oma työ)
- `viestit(data)` rakentaa mallille viestit; käytä `data.data_lohko()` ja `data.SYSTEM_SAANNOT`
- `SKEEMA` kuvaa, mitä `sisalto`-kentässä saa olla
- `render(sisalto)` ja `render_kuiva(data)` tekevät Telegram-tekstin

Kirjoita jokaiselle uudelle agentille testit ennen käyttöönottoa. `luo.py` tekee valmiiksi neljä perustestiä (moduulin sopimus, viestit, kuiva-ajo ilman verkkoa, mallivirhe ei ole hiljainen); lisää omat testisi sen alle. Mallina onnistuneelle mallivastaukselle `test/test_ajo.py`.

## Itsenäinen työ: yöjono ja PR-kutsu

Pohjan päälle on rakennettu osat, joilla agentit tekevät tarkistettavaa työtä ilman että kukaan on paikalla. Jokainen osa on rajattu kovakoodatuin säännöin, ja ihminen katselmoi tulokset:

| Osa | Tiedosto | Mitä se tekee |
| --- | --- | --- |
| Yöjono | `yojono/` ([ohje](yojono/README.md)) | Ajaa jonotiedoston tehtävät peräkkäin päivä- ja kuukausikaton sisällä. Tyypit: `katselmointi` ja `ideointi` (vain raportti), `kehitys` (muutos haaraan ja PR, ks. [KEHITYS.md](yojono/KEHITYS.md)). |
| PR-kutsu | `agentti/pr_kutsu.py` | Pushaa `yo/<nimi>`-haaran botin forkkiin ja avaa PR:n. Sallittu repo-lista, ei force-pushia, suojatut polut (`.github/`, `tools/`, `systemd/`, `isanta/`, `rajat.py`, `config.py` ja salaisuuden nimiset tiedostot) estetty, enintään 40 tiedostoa ja 100 PR:ää päivässä. Tunnus annetaan gitille vain tilapäisen apuskriptin kautta. Ajuri synkkaa botin forkin pohjahaaran itse ennen kehitys-tehtävää (`git fetch`, vain fast-forward, ei force-pushia, vain main/master, ei muita haaroja). Jos fork-main on erkaantunut ylävirrasta tai push epäonnistuu, tehtävä ohitetaan ilman kulua ja syy kirjataan koodilla (`fork_ei_ff`, `fork_push_epaonnistui`, `fork_workflow_oikeus`). Jos ylävirran commitit muuttavat `.github/workflows/`-tiedostoja, botin tokenilla (`public_repo`, ei `workflow`-oikeutta) niitä ei voi työntää: silloin fork pitää synkata käsin (botin tilillä: Sync fork → Update branch). |
| Tapahtumaloki | `agentti/tapahtumaloki.py`, `loki_keraaja.py` | Vain lisäävä SQLite-loki tapahtumille ja palautteelle; salaisuuden näköiset merkkijonot poistetaan ennen tallennusta. |
| Julkaisukomento | `isanta/julkaise.sh` | Yksi isännän skripti kolmelle kohteelle (kauppatiimi CT119, infra-automaatio CT118, yojono-ajuri CT115): julkaisu, peruutus (vain koodi; tilan ja konfiguraation tiiviste ennen/jälkeen), `--kuiva`, `--kanaria`, `--katastrofi-tila` (tila talteen ennen `pct rollback`:ia). Vaatii `git archive` -paketin, jossa on commit-tunniste. Ks. [`isanta/JULKAISU.md`](isanta/JULKAISU.md). |
| Viikkoraportti | `agentti/viikkoraportti.py` | Lukee lokin ja tulostaa kulut agenteittain, virheet virhekoodeittain, ohitetut tehtävät syineen ja sääntöpohjaiset vihjeet (ei mallikutsua). |
| Automerge | `tools/automerge.py` | Yhdistää automaattisesti vain pienet dokumentti- ja testimuutokset sekä botin `yo/readme-*`-PR:t, joissa on pelkkiä `.md`-tiedostoja. Koodi, työnkulut ja suojatut polut jäävät aina ihmiselle. |
| Mutanttitestaus | `tools/mutantit.py` | Muuttaa koodia pienin virhein (AST-mutantit) ja tarkistaa, että moduulin testit huomaavat sen. Selviytyjä = testeistä puuttuu tarkistus. Paikallisesti `python tools/mutantit.py agentti/kulu.py`; pitkä ajo GitHubissa: Actions → Mutantit → Run workflow. Vastaavat mutantit sallitaan `tools/mutantit.sallitut.txt`-tiedostossa perusteluineen. |

Periaate on sama kuin pohjassa: koodi on laki. Raja ei ole kehotteessa vaan tarkistuksessa, ja jokainen automaattinen muutos on katselmoitavissa PR:nä.

## Koulun muistutukset

`agentti/koulu_muistutus.py` lukee `koulu.md`-tiedoston ja tulostaa tekstin: myöhässä olevat palautukset, tänään, huomenna ja seuraavat 7 päivää. Ei verkkoa, ei mallikutsuja eikä lähetystä.

Muokkaa `koulu.md`:ta (mallina `docs/koulu.esimerkki.md`, keksittyä dataa). Yksi rivi per tapahtuma:

```
PVM | kurssi | tapahtuma | tyyppi | tehty
2026-11-06 | Ohjelmointi | Välikoe | tentti
```

PVM on `VVVV-KK-PP`, tyyppi on `tentti`, `palautus` tai `kertaus`. Viides sarake on valinnainen: `x` merkitsee palautuksen tehdyksi, jolloin se ei näy myöhässä-listassa. Rivit, jotka alkavat `#`, ja tyhjät rivit ohitetaan. Myöhässä-listaan tulevat vain tekemättömät palautukset.

Ajo: `python3 -m agentti.koulu_muistutus --tiedosto koulu.md --pvm 2026-11-02` (ilman `--pvm`:ää käytetään tätä päivää). Virheellinen rivi antaa virheen rivinumerolla ja poistumiskoodin 1. Testit: `python3 -m unittest test.test_koulu_muistutus`.

## Keskustelu agenttien kanssa: portti Open WebUI:hin

`python -m agentti.portti --config portti.json --tila <hakemisto> --env <env> --host <ip> --port 8810`
julkaisee `maaritys.json`-agentit OpenAI-yhteensopivana rajapintana. Open WebUI:ssa: Admin -> Settings -> Connections ->
OpenAI API -> osoite `http://<ip>:8810/v1` ja avain `PORTTI_AVAIN`. Agentit ilmestyvat mallilistaan.

- Env-tiedostossa `LLM_BASE_URL`, `LLM_API_KEY` ja `PORTTI_AVAIN` (vahintaan 24 merkkia).
- Ohje luetaan aina palvelimen ohjetiedostosta: chatin system-viestit ja tyokalut ohitetaan.
- Oma paivabudjetti per agentti (`portti.esimerkki.json`), laki-ylaraja 3 USD.
- Tyokalut maarityksen `tyokalut`-listalta: `hae_netista` (vaatii `PORTTI_SEARXNG_URL` env-tiedostoon), `hae_sivu`, `ehdota_muutos`. Agentti ehdottaa, omistaja hyvaksyy: `python3 -m agentti.portti ehdotukset|nayta|hyvaksy|hylkaa [ID] --config ... --tila ...` (aja rootina).
- Kuuntelee oletuksena vain 127.0.0.1; anna LAN-osoite tietoisesti `--host`-valitsimella. Malli: `systemd/portti.service.mall`.

## Käyttöönotto palvelimella

Katso [DEPLOY.md](DEPLOY.md): käyttäjä, hakemistot, salaisuudet, ajastin ja käyttöönottojärjestys (kuiva-ajo → `--llm` → `--send` → ajastin).

## Rajaukset

Kovakoodatut rajat (`agentti/rajat.py`, ei muutettavissa ympäristöstä tai komentoriviltä):

| Raja | Arvo |
| --- | --- |
| Päiväkatto | 0,05 USD |
| Kuukausikatto | 1,00 USD |
| LLM-kutsuja päivässä | 3 |
| Varaus ennen kutsua | 0,015 USD |
| Vastauksen katto (`max_tokens`) | 2000 |
| LLM-aikakatkaisu | 120 s |
| Telegram-viestin enimmäispituus | 3500 merkkiä |

Päivä lasketaan Europe/Helsinki-ajan mukaan (`agentti/config.py`).

- Vain Linux/macOS (flock). Palvelimella ajettava `yojono/kehitys.sh` on Linux-skripti (käyttää GNU `timeout`-komentoa); sen testit ohitetaan macOS:ssä.
- Ei työkaluja malleille tarkoituksella. Jos agentti tarvitsee työkaluja, tee se omana, erikseen katselmoituna lisänä.
- Hinnasto (`HINNAT_USD_PER_MILJOONA`) on oletuksena tyhjä: tuntemattomalle mallille kirjanpitoon jää pessimistinen varaus (`KUTSU_ARVIO_USD`). Lisää mallin hinta vasta, kun olet tarkistanut sen palveluntarjoajalta.

## Lisenssi

MIT
