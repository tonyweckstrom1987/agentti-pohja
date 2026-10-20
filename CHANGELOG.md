# Changelog

Muoto perustuu [Keep a Changelogiin](https://keepachangelog.com/fi/1.0.0/).

## [Julkaisematon]

### Lisätty

- Sandbox-asiakas (`agentti/sandbox_kutsu.py`): lähettää repon tai luonnoshakemiston eristettyyn sandboxiin ssh:lla pakotetun komennon kautta. Vain `https://github.com/omistaja/repo`-osoitteet, ref-tarkistus, tar ilman symlinkkejä ja raskaita hakemistoja, kokoraja, aikaraja. Ei salaisuuksia: vain avaintiedoston polku.
- Automerge-koe 20.10.2026: tama rivi lisattiin PR:lla, jonka pitaisi yhdistya itsestaan.
- Automaattinen yhdistaminen pienille muutoksille (`.github/workflows/automerge.yml`, `tools/automerge.py`): kytkee GitHubin auto-mergen (squash) vain kun PR sisaltaa pelkkia dokumentteja (`*.md`) tai testeja (`test/*.py`), enintaan 20 tiedostoa ja 200 rivia, eika kosketa suojattuja polkuja (`.github/`, `tools/`, `isanta/`, `systemd/`, config, rajat, avaintiedostot). Yhdistyy vasta kun pakolliset tarkistukset ovat vihreat. Paatos tehdaan luotetusta paahaaran koodista (`pull_request_target`), PR:n koodia ei suoriteta. Kelpaamattomaksi muuttuneelta PR:lta auto-merge poistetaan. 12 testia.
- Snapshot-oikeus CT115:lle (`isanta/`, `agentti/snapshot_kutsu.py`): isannan pakotettu ssh-komento sallii vain `listaa` ja `snapshot ennen-<nimi>` kontille 115 (enintaan 20 snapshotia, 10 ottoa/vrk, ei poistoa eika rollbackia). Asennus isannalla: `isanta/asenna.sh` (idempotentti). Asiakas: `python3 -m agentti.snapshot_kutsu listaa | ota ennen-<nimi>`. 16 testia.
- Testit sandbox-asiakkaalle (`test/test_sandbox_kutsu.py`).
- `aja_git(repo, ref)` ja CLI `python -m agentti.sandbox_kutsu git <repo> [ref]`: testaa git-haaran sandboxissa lähettämällä vain haaran seurattavat tiedostot (`git archive`). Seuraamattomat tiedostot (`.env`, tilatiedostot) ja symlinkit eivät lähde. Tarkoitettu yo/-haarojen testaukseen ennen katselmointia.
- Yöjono (`yojono/`): jonotiedostosta ajettava, kulukattoinen tehtäväajuri (nyt vain koodikatselmointi). Varaa budjetin ennen tehtävää, lukee todellisen kulun, laskuttaa koko budjetin jos kulu on tuntematon, kirjaa tilan heti, lukko estää rinnakkaisajon, valinnainen Telegram-yhteenveto. Systemd-mallit ja käyttöönotto-ohje (`yojono/README.md`), 30 testiä.

## [0.1.0] - 2026-10-16

### Lisätty

- Kulukirjanpito (`kulu.py`): varaus ennen kutsua, päivä- ja kuukausikatto sekä kutsuraja, flock-lukko, vikatilassa pysäytys (fail closed).
- LLM-asiakas (`llm.py`): OpenAI-yhteensopiva, ei työkaluja, `max_tokens`-katto, ei uudelleenohjausten seurantaa, virheet koodeina.
- Tulos (`tulos.py`, `skeema.py`): `ok` tai `ei_tietoa` (ei tiedä on aina sallittu), vastaus hyväksytään vain skeemaa vasten.
- Telegram (`telegram.py`), sydämenlyönti (`sydan.py`), ulkoisen datan `<data>`-lohko (`data.py`).
- Ajon kulku (`ajo.py`) ja komentorivi (`python -m agentti`): oletuksena kuiva-ajo, `--llm`, `--send` (kerran päivässä).
- Esimerkkiagentti, `luo.py` (uusi agentti pohjasta), kovennetut systemd-pohjat, DEPLOY.md.
- Testit (yli 80) ja CI (Python 3.10-3.13).
