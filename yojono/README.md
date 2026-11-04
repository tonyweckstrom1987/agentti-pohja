# Yöjono

Aikataulutettu ajuri, joka ajaa jonotiedoston tehtävät peräkkäin kulukaton sisällä ja kertoo tuloksen. Tarkoitus: kone tekee hyödyllistä, tarkistettavaa työtä (nyt koodikatselmointeja), vaikka kukaan ei ole paikalla. Ajuri itse ei merge, ei pushaa eikä ota mitään käyttöön. Tyypit `katselmointi` ja `ideointi` tuottavat vain raportin; tyyppi `kehitys` tekee muutoksen kertakäyttökloonissa ja avaa PR:n `agentti/pr_kutsu.py`:llä (ks. `KEHITYS.md`). Ihminen katselmoi tuotoksen; automaattisesti yhdistyvät vain tools/automerge.py:n sallimat dokumentti- ja testimuutokset sekä botin `yo/readme-*`-PR:t, joissa on vain `.md`-tiedostoja.

Vain standardikirjasto, Python 3.10+. Testit: `python -m unittest discover -s . -t . -p 'test_*.py'` (repon juuresta).

## Mitä se tekee

1. Lukee `jono.json`-tiedoston ja tarkistaa jokaisen kentän (id, repo-nimi, budjetti).
2. Ohittaa valmiit tehtävät. Epäonnistunutta yritetään uudelleen vain myöhempänä päivänä ja enintään kaksi kertaa.
3. Varaa ennen jokaista tehtävää sen koko budjetin päiväkatosta. Jos jäljellä on vähemmän, tehtävä ohitetaan (pienempi voi mahtua).
4. Kutsuu tehtävätyypin skriptiä (`katselmointi` = `katselmoi.sh <repo> <budjetti>`), ilman shelliä, aikarajalla (30 min / tehtävä, 3 h / yö).
5. Lukee todellisen kulun skriptin tuottamasta JSON:sta (`total_cost_usd`), myös epäonnistuneesta ajosta. Jos ajo alkoi mutta kulua ei saada selville (aikakatkaisu, rikkinäinen JSON), kirjataan koko budjetti: katto ei saa alittua arvauksella.
6. Kirjaa tilan `tila.json`:iin heti jokaisen tehtävän jälkeen (atominen kirjoitus), joten katkennut ajo ei hukkaa kulukirjanpitoa.
7. Tulostaa yhteenvedon ja lähettää sen Telegramiin, jos `YOJONO_TELEGRAM_TOKEN` ja `YOJONO_TELEGRAM_CHAT` on asetettu. Telegram-tunnuksia ei anneta alaprosessille.

Lukko estää kaksi ajoa yhtä aikaa.

## Komennot

    python3 -B -m yojono kuiva   # näyttää mitä ajettaisiin, ei kutsu mitään, ei kirjoita tilaa
    python3 -B -m yojono ajo     # ajaa jonon
    python3 -B -m yojono tila    # tulostaa tila.json:n

Ympäristö: `YOJONO_PERUS` (oletus `/var/lib/yojono`), `YOJONO_KATSELMOI` (oletus `/opt/yojono/katselmoi.sh`).

## Jonotiedosto

Katso `jono.esimerkki.json`. `paivakatto` on USD:tä (0-50), tehtävän `budjetti` ei saa ylittää sitä. Valinnainen `kuukausikatto` (USD, vähintään päiväkatto, enintään 2000) pysäyttää ajon, kun kuukauden kulut + tehtävän budjetti ylittäisivät sen; kulut lasketaan tila.json:n päiväkirjanpidosta. `repo` on GitHub-repon nimi (vain kirjaimet, numerot, `_`, `.`, `-`). Tehtävätyypit: `kehitys` (`kehitys.sh`, `YOJONO_KEHITYS`; vaatii `aihe`-kentän `readme`/`testit`/`riippuvuudet`, repo PR-sallittujen listalta, katso `KEHITYS.md`), `katselmointi` (`katselmoi.sh`, ympäristö `YOJONO_KATSELMOI`) ja `ideointi` (`ideoi.sh`, `YOJONO_IDEOI`). Jokainen skripti kutsutaan `<skripti> <repo> <budjetti>` ja tuottaa `raportit/`-hakemistoon JSON:n (`total_cost_usd`) ja Markdown-raportin: `katselmointi` nimellä `<pvm>-<repo>`, muut nimellä `<pvm>-<tyyppi>-<repo>`. Ideointitehtävässä `repo` on aihealueen nimi (esim. `agenttifirma`). Ideointitehtävällä voi olla valinnainen `teema` (yksirivinen teksti, enintään 300 merkkiä): ajuri antaa sen skriptille ympäristömuuttujassa `YOJONO_TEEMA` ja raportin nimeksi `YOJONO_RAPORTTI` (`<pvm>-ideointi-<tehtävän id>-<repo>`), jolloin samaan repoon voi tehdä useita teemallisia ideointeja samana päivänä ilman että raportit ylikirjoittavat toisensa. `ideoi.sh` lisää teeman kehotteeseen ja kirjoittaa raportin annetulla nimellä. Repo `agenttifirma` ei ole GitHubissa: `ideoi.sh` lukee sen paikallisesta dokumenttikopiosta (`YOJONO_DOCS`, oletus `/opt/agenttifirma-docs`) ilman kloonia, eikä aja, jos kopiossa on `yksityinen-talous`-hakemisto. Muut repot kloonataan kuten ennen.

Valinnainen `toisto_pv` (kokonaisluku 1-365) tekee tehtävästä toistuvan: valmis tehtävä ajetaan uudelleen, kun edellisestä valmiista ajosta on vähintään niin monta päivää. Ilman sitä valmis tehtävä ei aja koskaan uudelleen.

Huom: ajurin päiväkatto laskee kalenteripäivän. LiteLLM-avaimen oma budjetti nollautuu omassa tahdissaan ja on kova yläraja (se pysäyttää kutsut, vaikka ajuri erehtyisi). Pidä ajurin `paivakatto` enintään avaimen budjetin suuruisena.

## Virhekoodit

Epäonnistunut tai ohitettu tehtävä saa vakaan koodin. Koodi näkyy `tila.json`:ssa (`koodi`), Telegram-yhteenvedossa (`syy: <koodi>: ...`), tapahtumalokissa (`virhekoodi`) ja viikkoraportissa. Vanhat lokirivit ilman koodia näkyvät koodina `tuntematon`.

| Koodi | Merkitys |
|---|---|
| `aikakatkaisu` | tehtävä ylitti aikarajan (kulu laskutetaan koko budjettina) |
| `malli_virhe` | claude/malli palautti virheen (`is_error`, muu `subtype` kuin `success`) tai ei tunnistanut mallia |
| `budjetti_loppui` | claude-kutsun budjetti (`--max-budget-usd`) loppui ennen raporttia (`error_max_budget_usd`); kulu voi ylittää budjetin, ja se kirjataan todellisena |
| `raporttia_ei_syntynyt` | skripti ei tuottanut raporttia (md puuttuu tai ei päivittynyt) |
| `raportti_tyhja` | raportti syntyi, mutta on tyhjä tai liian lyhyt (alle 200 tavua) |
| `skripti_ei_kaynnisty` | tehtäväskriptiä ei voitu käynnistää |
| `pr_kutsu_epaonnistui` | PR:n avaus epäonnistui muusta syystä kuin forkin synkkauksesta |
| `testit_punaiset` | kehitys: testit punaisella agentin muutosten jälkeen |
| `testit_aikakatkaisu` | kehitys: testisarja ei valmistunut aikarajassa (`YOJONO_TESTI_AIKARAJA`, oletus 900 s), tulos ei tiedossa |
| `fork_jaljessa` | fork jäi jälkeen ylävirrasta synkkauksen jälkeen |
| `fork_ei_ff` | fork-main eroaa ylävirrasta (ei fast-forward): mitään ei tehty |
| `fork_push_epaonnistui` | forkin synkkauksen push epäonnistui |
| `fork_workflow_oikeus` | synkkaus vaatisi `workflow`-oikeuden, jota botin tunnuksella ei ole (ylävirran commitit muuttavat `.github/workflows/`-tiedostoja) |
| `fork_tarkistus_epaonnistui` | forkin tilaa ei saatu luettua (verkko, tunnus, haara) |
| `tuntematon` | syytä ei voi päätellä |

Luokittelun järjestys: claude-tuloksen JSON (`budjetti_loppui`, `malli_virhe`) ensin, sitten skriptin viestistä (`fork_*`, `pr_kutsu_epaonnistui`, `testit_punaiset`, tuntematon malli) ja viimeisenä raportin puuttuminen.

## Forkin synkkaus ennen kehitys-tehtävää

`kehitys`-tehtävä avaa PR:n botin forkista, ja jos fork on jäljessä ylävirrasta, uusi haara vie forkkiin myös ylävirran commitit. Siksi ajuri synkkaa forkin pohjahaaran (`main`, `ai-pr-reviewer-action`: `master`) ennen mallikutsua (`agentti/pr_kutsu.py`, `synkkaa_fork`):

1. `git ls-remote` (julkinen luku) ylävirrasta ja forkista: samat = valmis.
2. Muuten `git fetch` molemmat väliaikaiseen bare-repoon ja `git merge-base --is-ancestor`: jos fork on ylävirran esivanhempi, push ylävirran tila forkin pohjahaaraan botin tunnuksella. Refspec on ilman `+`, ei force-pushia, ei muita haaroja. Sama tunnus kuin PR:ssä, ei uusia oikeuksia.
3. Jos fork-main on erkaantunut ylävirrasta, ei tehdä mitään: tehtävä ohitetaan ilman kulua ja yritystä, ja syy kirjataan (`fork_ei_ff`). Jos push epäonnistuu, sama (`fork_push_epaonnistui`, workflow-oikeuden puutteessa `fork_workflow_oikeus`).
4. Synkkaus tehdään kerran per repo ja ajo. Ohitukset kirjataan tapahtumalokiin tuloksella `ohitettu`, ja vähintään kolme ohitusta samassa ajossa nostaa `HALYTYS`-rivin yhteenvetoon.

Botin tunnus on klassinen `ghp_`-tunnus, jolla on vain `public_repo`-oikeus. GitHub hylkää pushin, jos se muuttaa `.github/workflows/`-tiedostoja ilman `workflow`-oikeutta, joten tällaisen ylävirran commitin synkkaus ei onnistu itsestään (`fork_workflow_oikeus`): synkkaa fork silloin käsin tai anna tunnukselle `workflow`-oikeus omalla päätöksellä.

## Käyttöönotto (ihminen tekee, ei automaattista)

Esimerkki CT115:ssä. Ota snapshot ensin (`pct snapshot 115 ennen-yojono-ajuri`, isännällä).

    git clone https://github.com/tonyweckstrom1987/agentti-pohja /opt/yojono-ajuri
    cp /opt/yojono-ajuri/yojono/jono.esimerkki.json /var/lib/yojono/jono.json
    cd /opt/yojono-ajuri && YOJONO_PERUS=/var/lib/yojono python3 -B -m yojono kuiva

Kun kuiva-ajo näyttää odotetut tehtävät, kokeile käsin yhdellä tehtävällä (pienennä jono ensin) ja tarkista raportti ja `tila`. Telegram on valinnainen: luo `/etc/yojono/telegram.env` (chmod 600) riveillä `YOJONO_TELEGRAM_TOKEN=...` ja `YOJONO_TELEGRAM_CHAT=...`. Älä lisää tiedostoa versionhallintaan.

Ajastus: kopioi `systemd/yojono.service.mall` ja `systemd/yojono.timer.mall` nimillä `yojono.service` ja `yojono.timer` hakemistoon `/etc/systemd/system/`, sitten `systemctl daemon-reload && systemctl enable --now yojono.timer`. Seuraava ajo: `systemctl list-timers 'yojono*' --no-pager`. Pysäytys: `systemctl disable --now yojono.timer`.

## Rajat

- Ei merge-, push- eikä käyttöönottotyyppejä. Uusi tehtävätyyppi vaatii oman suorittajan ja testit.
- Ajuri ei näe salaisuuksia: katselmoi.sh lukee ne itse omasta tiedostostaan.

## Mallikutsun ohitus (kulun pienennys)

- `kehitys.sh`: jos saman repon + aiheen + teeman edellinen onnistunut ajo ei tuottanut muutoksia ja repon HEAD on sama, mallikutsua ei tehda (raportti: "ohitettu", kulu 0). Tila: `<perus>/kehitys-tyhjat.tsv`. Raja `KEHITYS_OHITUS_S` (oletus 604800 s = 7 vrk).
- `tarkkailu.sh`: jos GitHub-tilannekuva (`github.md`) on identtinen saman roolin viimeksi analysoidun kanssa, mallikutsua ei tehda. Tila: `<perus>/tarkkailu-kuvat.tsv`. Raja `TARKKAILU_OHITUS_S` (oletus 172800 s = 2 vrk).
- Vain onnistunut agenttiajo (`is_error` false, `subtype` success) kirjataan; budjetti- ja mallivirheet eivat ohita seuraavaa yritysta. `YOJONO_PAKOTA=1` ohittaa ohituksen; kuiva-ajo ei ohita eika kirjaa.

## Pysyva jono ja kolme ajoa (ikkuna 21-07)

- `jono.pysyva.json`: valmis pysyva jono (20 toistuvaa tehtavaa: tarkkailu, kehitys testit/readme/riippuvuudet, katselmointi, ideointi); kallein tehtava (katselmointi-agentti-pohja, 2,50 USD) on ensimmaisena, jotta se mahtuu ennen halvempien kulutusta paivakattoon, paivakatto 5 USD, kuukausikatto 100 USD. Jarjestys = prioriteetti; `toisto_pv` ajaa tehtavan uudelleen, ja mallikutsun ohitus (kehitys.sh, tarkkailu.sh) estaa turhan kulun, kun repo tai GitHub-tilannekuva ei ole muuttunut.
- Ajopaiva: kulukatto (`paivakatto`) ja `toisto_pv` lasketaan ajopaivalle, joka vaihtuu klo 09 Helsingin aikaa, joten yoikkunan ajot A, B ja C jakavat yhden katon (`ajuri.ajopaiva`).
- Ajastimet: `yojono.timer` 01:00 (nykyinen), `systemd/yojono-a.timer.mall` 21:30 ja `systemd/yojono-c.timer.mall` 05:00. Ajurin lukko estaa paallekkaiset ajot.
- Kayttoonotto (Tonyn kasin, isanta/CT115): varmuuskopio nykyisesta `/var/lib/yojono/jono.json`, kopioi `jono.pysyva.json` sen tilalle, asenna molemmat ajastimet `/etc/systemd/system/` ilman `.mall`-paatetta, `systemctl daemon-reload`, `systemctl enable --now yojono-a.timer yojono-c.timer`. Peruutus: `systemctl disable --now yojono-a.timer yojono-c.timer` ja vanha jono.json takaisin.

