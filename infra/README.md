# infra/ - infra koodina (Ansible)

Tavoite (Tonyn paatos 31.10.2026): kontit ja palvelut kuvataan tiedostoina, jotta levyrikon jalkeen ne voi rakentaa uudelleen ajamalla yhden komennon eika kasin paivien ajan.
Nyt valmiina: **kartoitus** ([KARTOITUS.md](KARTOITUS.md)) ja **ensimmainen rooli** `claudejono` (CT115).

Miksi taalla eika omassa repossa: rooli kuvaa tata ajuria ja sen asennusta (`claudejono/`, `DEPLOY.md`), ja se pysyy samassa versiossa koodin kanssa. Kun rooleja on 4+ ja ne koskevat muita kontteja (LiteLLM, kauppatiimi), kannattaa siirtaa omaan repoon `agenttifirma-infra` (historia sailyy `git subtree split -P infra`). Ehdotus, ei tehty.

## Mita rooli hallitsee (CT115)
| Osa | Tiedosto |
|---|---|
| ryhmat ja tilit `claudejono` (bash, koti, ryhma systemd-journal) ja `prbotti` (nologin) | `tasks/tilit.yml` |
| hakemistot ja oikeudet (`/var/lib/claudejono` 0700, `/etc/claudejono-pr` 0700 prbotti, `/etc/claude-jono` 0700, `/opt/agentscope/tyo` ...) | `tasks/hakemistot.yml`, `defaults/main.yml` |
| sudoers: `claudejono` saa ajaa vain `claudejono-pr`:n prbottina; kirjoitus `visudo -cf` -tarkistuksen jalkeen | `tasks/sudo.yml` |
| PR-wrapper `/usr/local/sbin/claudejono-pr` (root 0755) | `files/claudejono-pr` |
| `claude-jono.service`, `claude-jono.timer` + ajastin paalle | `files/`, `tasks/systemd.yml` |
| salaisuuksien olemassaolon tarkistus (ei luku) | `tasks/salaisuudet.yml` |

**Ei hallita (tarkoituksella):** salaisuudet (deploy key, botin tunnus, `telegram.env`, Clauden kirjautuminen), docs-klooni, `/opt/agentti-pohja`-koodi, Clauden binaari. Nama kirjoittaa Tony itse, ks. `DEPLOY.md` ja `claudejono/README.md`.

## Salaisuudet
Repoon ei tule yhtaan salaisuutta. Rooli ei lue eika kirjoita niita; se vain tarkistaa `stat`-kutsulla, ovatko `/etc/claude-jono/deploy_key` ja `/etc/claudejono-pr/token` olemassa, ja tulostaa varoituksen jos puuttuvat (rootina ajettaessa).
Kun roolit tarvitsevat salaisuuksia (LiteLLM-avaimet, Postgres-salasanat): vaihtoehdot ovat (a) Ansible Vault (salattu tiedosto repossa, vault-salasana Tonyn muistissa/ei repossa) tai (b) haku holvista (CT116) ajon aikana lookup-pluginilla. Paatos on tehtavan 31 asia; kunnes se on tehty, salaisuudet jaavat kasin kirjoitettaviksi ja rooli vain tarkistaa ne.

## Ajaminen
Asennus (kerran, CT115 tai Tonyn kone): `apt install ansible-core` (Debian-paketti; rooli kayttaa vain `ansible.builtin`-moduuleita, kokoelmia ei tarvita).

```
cd /opt/agentti-pohja/infra            # tai oma klooni
sudo ansible-playbook site.yml --check --diff     # 1. AINA ensin: nayttaa mita muuttuisi, ei muuta mitaan
sudo ansible-playbook site.yml                    # 2. vasta kun diff on ymmarretty (Tonyn kasin; tata ei ajeta agentilla)
```
Rajaus: `--tags sudo`, `--tags systemd`, `--tags tilit`, `--tags hakemistot`.

Ajo vaatii root-oikeuden (sudoers, `/etc/systemd`, kayttajat). Agentti (`claudejono`) ei aja tata: se voi ajaa vain `--check`in, ja silloin `/etc/sudoers.d/claudejono` nayttaa aina "changed", koska tiedosto ei ole sen luettavissa.

### Hiekkalaatikkotesti (ei root-oikeuksia, ei kosketa oikeaan jarjestelmaan)
```
infra/tests/hiekkalaatikko.sh
```
Ajaa roolin tilapaishakemistoon (`claudejono_fs_root`), tarkistaa ettei `--check` luo mitaan, ensimmainen ajo luo, toinen ajo antaa `changed=0` (idempotenssi) ja sudoers/wrapper/oikeudet ovat odotetut. Jos `ansible-playbook` ei ole PATHissa: `ANSIBLE=/polku/ansible-playbook infra/tests/hiekkalaatikko.sh`.

### Tila 31.10.2026 (mita on todella testattu)
- Hiekkalaatikkotesti: lapi (idempotentti, `changed=0` toisella ajolla). `ansible-lint` profiili `production`: 0 virhetta. Ansible-core 2.21.5.
- `--check --diff` oikeaa CT115:ta vastaan claudejono-tilina: kaikki tehtavat `ok` (tilit, hakemistot omistajineen ja oikeuksineen, wrapper, molemmat yksikot, ajastin enabled) **paitsi sudoers**: `changed`. Syy on se, ettei claudejono voi lukea tiedostoa (root 0440), joten vertailu ei onnistu. Rootina ajettaessa nakyy todellinen ero. Rooli kirjoittaa sudoers-tiedoston ymmartamani saannon (`sudo -l`) mukaan; alkuperaisessa voi olla eri kommentit (tiedosto on 284 tavua). Jos ero on vain kommentit, paivita `tasks/sudo.yml`n `content` vastaamaan: `sudo cat /etc/sudoers.d/claudejono`. **Ala aja ilman --check, ennen kuin olet katsonut taman diffin.**
- Yksikkojen `Mallitiedosto: ei asenna mitaan` -kommentti on tarkoituksella sailytetty, jotta diff on nolla; siivoa kommentti myohemmin omana muutoksena.

## Tonyn harjoitus: ajo `--check` ja diffin tulkinta
1. Aja rootina: `cd infra && ansible-playbook site.yml --check --diff`.
2. Katso loppuyhteenveto: `ok=` / `changed=` / `failed=`. Odotus: `changed=0` paitsi mahdollisesti sudoers.
3. Jos jokin on `changed`, lue diff: `-` rivit ovat koneella nyt, `+` rivit ovat tiedostossa (mita Ansible kirjoittaisi). Kysy itseltasi: kumpi on oikein? Jos kone on oikein, korjaa tiedosto repossa (PR). Jos repo on oikein, kone on "ajautunut" (drift) ja ajo korjaa sen.
4. Kokeile vaarennos: muuta `defaults/main.yml`ssa hakemiston `/etc/claudejono-pr` mode arvoksi "0750" ja aja `infra/tests/hiekkalaatikko.sh`: se epaonnistuu kohdassa 5 (odotus 700). Mieti, miksi testi nain suojaa vahingossa tehdyilta oikeusmuutoksilta. Palauta muutos.
5. Kirjaa, mita et ymmartanyt, uuteen issueen.

## Seuraavaksi (jarjestys)
1. **Yojono-rooli** (CT115): yojono.service/.timerit (3 ajastinta), drop-init, `/opt/yojono`-skriptit, `virheilmoitus@`. Sama malli: kopioi nykytila, `--check` nolla-diffiin ensin.
2. **Muut CT115:n palvelut**: github-api, portti, ideapankki, paivakatsaus, trendit, halytin, docs-pull (~12 yksikkoa; yksi rooli per palvelu tai yksi `ct115-palvelut` taulukkopohjainen rooli).
3. **LiteLLM (CT111)**: Python-asennus, systemd, konfiguraatio (`config.yaml`), avaimet holvista/Vaultista. Vaatii ensin kartoituksen kontissa (KARTOITUS.md kohta 8) ja snapshotin.
4. **Kauppatiimi**: paperikauppa-ajot ja niiden ajastimet; riippuu siita, missa ne ajetaan (CT115 vai oma kontti).
5. **Isanta**: kontit itse (`pct create`, resurssit, onboot, vzdump-job) esim. `community.general.proxmox`-kokoelmalla. Isannalle ei anneta CT115:n ajoa: Tony ajaa.
6. Sitten: inventaari usealle koneelle (SSH, avaimet), `ansible-lint` ja hiekkalaatikkotesti CI:hin (`.github/` on suojattu polku, joten Tony lisaa sen).

## Rajat
Rooli ei muuta kattoja, budjetteja eika oikeuksia laajemmiksi: sudoers sallii yhden komennon, ei `ALL`. Muutos sudoersiin tai wrapperiin on aina Tonyn katselmoima PR.
