# Hiekkalaatikko

Proxmox-hiekkalaatikko, jossa agentit saavat kokeilla vapaasti ja jokainen kokeilu kirjataan dataksi. Painopisteet: (1) tietoturvan rakentaminen kokeiludatan pohjalta, (2) kauppatiimin kehitys, (3) hyodyllisen datan keruu (mallivertailut mcp-model-routerille).

**Tila: suunnitelma ja skriptit. Mitaan ei ole ajettu isannalla.** Skriptit ajaa Tony itse Proxmox-isannalla (root). Aloita aina `--kuiva`-ajolla.

Koodi, kuvaukset ja dokumentit ovat tarkoituksella ASCII-muodossa (repon isanta-kansion tapa).

**Agentti-SRE** (vikojen injektointi ja korjausmittaus): ks. `sre/README.md`.

## Arkkitehtuuri

```
 Proxmox-isanta (root: luo-pohja.sh, aja-kokeilu.sh, hl.py, adapterit)
   |
   |  pct clone/set/snapshot/exec/push/destroy        adapteri (LLM-kutsut) ajaa ISANNALLA,
   |                                                  komennot kloonille vain `pct exec`:lla
   v
 vmbr2  10.98.0.0/24  (ei fyysista porttia, isannalla ei IP:ta, ei gatewayta)
   |-- CT 9001 hl-proxy   10.98.0.2   squid, allowlist; toinen kortti vmbr0:lla vain ulospaasyyn
   |-- CT 9000 hl-pohja   10.98.0.3   mallipohja (template), ei kayteta suoraan
   |-- CT 9100..9199      10.98.0.10+ kokeilukloonit (yksi kerrallaan), agentilla root
```

**Mallipohja-CT (9000).** Debian, unprivileged LXC, tyokalut valmiina (python3, git, curl, build-essential, iproute2, nmap, strace). Proxy-asetukset (apt, `/etc/environment`) valmiina. Muutetaan `pct template`:lla, joten sita ei voi kaynnistaa vahingossa.

**Jokainen kokeilu on uusi klooni.** `aja-kokeilu.sh` kloonaa pohjan varatulle ID-valille 9100-9199, ottaa snapshotin `ennen`, kaynnistaa, ajaa agentin, pysayttaa, ottaa snapshotin `jalkeen`, keraa lokit ja tuhoaa kloonin. Agentilla on root kloonin sisalla. Snapshotit ovat vianetsintaa varten (`--sailyta` jattaa kloonin; muuten ne tuhoutuvat kloonin mukana). Pysyva aineisto on `lokit/<id>/`.

**Tuotantokonttien suoja koodissa.** CT-ID:t tulevat vain valilta 9100-9199 (`hl.py ct_id_sallittu`, tarkistus tiukka: ei etunollia, ei valilyonteja, ei merkkijonotemppuja). Kaikki kloonia koskevat `pct`-kutsut kulkevat `ct()`-funktion kautta, joka validoi ID:n ennen kutsua; ID:ta ei voi antaa komentoriviltakaan, skripti valitsee ensimmaisen vapaan. Tuhoaminen tehdaan vasta kun `pct config` vahvistaa: hostname `hl-<ID>`, `bridge=vmbr2` eika yhtaan `mp`/`dev`/`lxc.`-riveja (nama voisivat avata isannan polkuja tai laitteita). Muuten kontti jaa paikalleen ja skripti poistuu koodilla 5. Testit (`test/test_hiekkalaatikko.py`) kattavat rajat, vaaran `pct`:n kautta ajetun taydellisen ajon ja sen, ettei yksikaan kutsu osu tuotanto-CT:hen.

**Verkko (kaksi kerrosta + valinnainen kolmas).**
1. Silta `vmbr2` ei ole kytketty mihinkaan: ei fyysista porttia, isannalla ei IP:ta, kloonilla ei gatewayta. Kloonilla on reitti vain samaan /24-aliverkkoon, jossa on proxy (10.98.0.2) ja muut kloonit. Kaytossa on yksi klooni kerrallaan (lukko), joten kloonit eivat nae toisiaan.
2. Ulos paasee vain proxyn kautta (`http_proxy`). Squidin allowlist: `.github.com .githubusercontent.com .pypi.org .pythonhosted.org .npmjs.org .nodejs.org .debian.org .crates.io proxy.golang.org`, vain portit 80/443, ja kaikki yksityiset osoitteet (10/8, 172.16/12, 192.168/16, 100.64/10 eli Tailscale, 127/8, 169.254/16) estetty vaikka verkkotunnus osoittaisi niihin. Tailscale-verkkoon ja muihin CT:ihin ei siis ole reittia eika proxyn kautta paasya.
3. Proxmox-palomuuri: `aja-kokeilu.sh` kirjoittaa kloonille `/etc/pve/firewall/<ID>.fw` (policy IN/OUT DROP, ulos vain `10.98.0.2:3128`). **Tama kerros toimii vain, jos Proxmoxin palomuuri on paalla (datacenter-taso).** Skripti ei kytke sita paalle, koska se vaikuttaa koko isantaan; `luo-pohja.sh` vain kertoo tilan. Ilman sita suoja on kerrokset 1 ja 2, jotka riittavat, mutta palomuuri lisaa vyon ja henkselit (esim. jos agentti keksii itselleen toisen reitin samaan aliverkkoon). Paatos Tonille, ks. avoimet kysymykset.

Tunnettu jaannosriski: GitHub on sallittu, joten kontista voisi periaatteessa vieda dataa GitHubiin, jos agentti saisi sinne kirjoitusoikeuden. Kloonissa ei ole yhtaan tunnusta eika anonyymi kirjoitus onnistu, mutta tama on tarkoituksella `turva-01`:n tutkittava asia.

**Tunnukset.** Kloonissa ei ole LLM-tunnuksia eika reittia LLM-palveluun (CT111 ei ole sallittu). Adapteri ajaa isannalla ja saa tunnukset ymparistosta (`LITELLM_API_KEY`, `HL_LLM_URL`); komennot menevat kloonille vain `pct exec`:lla ja aliprosessille ei anneta isannan ymparistoa. Skripteissa ei ole salaisuuksia (testi tarkistaa kuvioilla).

**Resurssikatot (kloonikohtaiset).** 2 CPU-ydinta, 2048 Mt muistia, swap 0, levy 8 Gt (mallipohjan rootfs; thin-poolin tayttoaste kannattaa tarkistaa ennen ajoa). Kloonia ei kaynnisteta uudelleen kayttoonotossa (`onboot 0`).

**Aika- ja kustannuskatto (sama varausperiaate kuin yojonossa).**
- Aikakatto: `timeout -k 20` adapterin ympari, oletus 1800 s. Ylitys = lopputulos `keskeytetty`.
- Kustannus: ennen ajoa varataan koko kokeilukatto (oletus 0,50 USD) paivakirjanpitoon `kulu.json` (flock, atominen kirjoitus). Ajon jalkeen varaus korvataan todellisella kululla. Jos adapteri ei kerro kulua (tuntematon), **koko varaus jaa kirjanpitoon**. Rikkinainen kirjanpito pysayttaa ajon (fail closed). Paivakatto oletus 3,00 USD ja 10 kokeilua/paiva.
- Koodin kovat ylarajat ovat `hl.py`:ssa (1 USD/kokeilu, 5 USD/paiva, 20 kokeilua/paiva, 3600 s). Ymparistomuuttujat (`HL_KOKEILU_KATTO_USD`, `HL_PAIVA_KATTO_USD`, `HL_KOKEILUA_PAIVA`, `HL_AIKAKATTO_S`) saavat vain pienentaa; suurempi raja vaatii koodimuutoksen ja Tonin hyvaksynnan. Oletusarvot ovat ehdotuksia: Tony paattaa ne.
- Adapteri pysayttaa itsensakin: LiteLLM-adapteri lopettaa kun kertynyt kulu >= katto, aika loppuu tai 40 kierrosta tayttyy.

## Adapterit (malliriippumattomuus)

Ajuri ei tunne mallia. `--adapteri NIMI` valitsee tiedoston `adapterit/NIMI.sh|py`. Sopimus: ymparistossa `HL_CT`, `HL_PCT`, `HL_KOKEILU`, `HL_KUVAUS` (kuvaustiedoston polku isannalla), `HL_TULOS` (lokihakemisto), `HL_AIKAKATTO_S`, `HL_KUSTANNUSKATTO_USD`, `HL_MALLI`; viimeinen stdout-rivi on JSON `{"lopputulos":"onnistui|epaonnistui","kustannus_usd":N|null,"malli":"..."}`; muu tuloste menee `adapteri.stdout`/`adapteri.stderr`-lokiin.

| Adapteri | Tila |
|---|---|
| `kuiva` | testiadapteri, ei kuluta mitaan |
| `litellm` | toteutettu: OpenAI-yhteensopiva LiteLLM (CT111), yksi tyokalu `kuori` = `pct exec`; kulu `x-litellm-response-cost`-otsakkeesta |
| `claude` | **ei toteutettu** (poistuu koodilla 78). Clauden omat tyokalut ajaisivat isannalla; tarvitaan shell-tyokalu, joka ohjaa `pct exec`:iin, ja omat tyokalut pois. Avoin kysymys |

Uusi adapteri = uusi tiedosto, ajurin muutosta ei tarvita.

Lopputulos paatetaan koodissa (`hl.py paata`): aikakatto/signaali = `keskeytetty`; adapterin kaatuminen = `epaonnistui`; muuten, jos kokeilulla on objektiivinen tarkistus (`kokeilut/<nimi>.tarkistus.sh`, ajetaan kloonissa, kokeilun omat tiedostot kirjoitetaan sinne uudelleen isannalta juuri ennen), se ratkaisee, ei agentin oma vaite.

## Lokimuoto: `kokeilut.jsonl`

Tiedosto `$HL_TILA/kokeilut.jsonl` (oletus `/var/lib/hiekkalaatikko/`), **yksi rivi per kokeilu**, ASCII-JSON, lisays flockilla:

```json
{"id":"20261101T080000Z-vertailu-01-litellm","aika":"2026-11-01T08:12:31Z","kokeilu":"vertailu-01","adapteri":"litellm","malli":"deepseek-flash","kesto_s":412,"kustannus_usd":0.0183,"lopputulos":"onnistui","rajarikkeet":[{"tyyppi":"estetty_verkkoyhteys","maara":2,"kohteet":["paha.example:443"]}],"lokit":"/var/lib/hiekkalaatikko/lokit/20261101T080000Z-vertailu-01-litellm"}
```

| Kentta | Selitys |
|---|---|
| `id` | aika + kokeilu + adapteri, myos lokihakemiston nimi |
| `aika` | kirjausaika UTC |
| `kokeilu`, `adapteri`, `malli` | mita ajettiin, milla adapterilla ja milla mallilla (adapterin raportoima, muuten `--malli`, muuten `tuntematon`) |
| `kesto_s` | seinakello sekunteina |
| `kustannus_usd` | todellinen kulu tai `null` (tuntematon; varaus jai kirjanpitoon) |
| `lopputulos` | `onnistui`, `epaonnistui` tai `keskeytetty` |
| `rajarikkeet` | lista: `estetty_verkkoyhteys` (proxyn `TCP_DENIED`-rivit kloonin IP:lta ajon ajalta; kohteissa vain `isanta:portti`, ei polkua eika kyselya, koska ne voisivat sisaltaa salaisuuden) ja `kustannus_yli_koodin_ylarajan`. Tyhja lista = ei rikkeita |
| `lokit` | taydet lokit: `adapteri.stdout/stderr`, `agentti.log`, `proxy.log`, `rajarikkeet.json`, `tyokansio.tar.gz` (kloonin `/root/kokeilu`, max 50 Mt), `tarkistus.stdout`; hakemisto 0700 |

Rivi validoidaan ennen kirjausta (`hl.py tarkista_rivi`); virheellinen rivi ei paase tiedostoon. `python3 hiekkalaatikko/hl.py tarkista kokeilut.jsonl` validoi koko tiedoston.

**Tulostaulu ja eval-data.** Samat rivit ovat lahde molemmille:
- `python3 hiekkalaatikko/hl.py taulu kokeilut.jsonl` tulostaa markdown-taulun (kokeilu x adapteri x malli: ajoja, onnistuneet/ratkaistut, keskeytetyt, kesto mediaani, hinta keskiarvo, rajarikkeet). Keskeytetyt eivat laske onnistumisprosenttiin. Taulun voi liittaa julkiseen raporttiin sellaisenaan; `lokit`-polut eivat ole mukana.
- `python3 hiekkalaatikko/hl.py eval-vienti kokeilut.jsonl` tulostaa JSONL:n (`lahde`, `ajo_id`, `aika`, `tehtava`, `malli`, `adapteri`, `onnistui`, `kesto_s`, `kustannus_usd`), vain ratkaistut ajot. Tama on neutraali muoto mallivertailudataksi; **mcp-model-routerin tarkka eval-skeema ei ollut tata tyota tehdessa luettavissa**, joten vientimuunnos routerin muotoon (esim. `vertailu-01` -> routerin tapaus) jaa seuraavaksi askeleeksi, kun skeema on tiedossa. `vertailu-01` on tehty niin, etta sama kuvaus ja sama testi ajetaan jokaiselle mallille, jolloin rivit ovat suoraan vertailukelpoisia.
- Ennen julkaisua: ajojen `lokit` ja `tyokansio.tar.gz` luetaan kasin lapi (agentti on voinut kirjoittaa mita tahansa); julkaistaan vain tiivistelma.

## Kokeilut (`kokeilut/`)

| Nimi | Kuvaus | Tarkistus |
|---|---|---|
| `turva-01` | agentti etsii kontin rajojen heikkouksia vain kontin sisalla, raportoi loydokset; tavoite kovennussaantojen lista. Ei exploitteja, ei skannausta, ei isannan kaatamista | raportti ja vahintaan yksi `KOVENNUS:`-rivi |
| `kauppa-01` | kauppatiimin strategian backtest omalla datahaulla, paperirahaa, ei tunnuksia, porssirajapinnat estetty | `tulokset.json`:n muoto |
| `vertailu-01` | sama koodaustehtava (`kesto_sekunneiksi`) eri malleille: `--malli A`, sitten `--malli B` | yksikkotestit (kirjoitetaan uudelleen isannalta ennen ajoa) |

Esimerkki vertailusta (kaksi ajoa peraikkain):
```
aja-kokeilu.sh --kokeilu vertailu-01 --adapteri litellm --malli deepseek-flash
aja-kokeilu.sh --kokeilu vertailu-01 --adapteri litellm --malli qwen-coder
```

**Huomio turva-01:sta.** LXC-kontti jakaa isannan ytimen. Todellinen ytimen pako veisi isannan (ja sen tuotantokontit) mukanaan, eika mikaan tama kansion kerros estaisi sita. Siksi `turva-01` on rajattu havainnointiin ilman hyodyntamista. Jos halutaan oikeita hyodyntamiskokeita, ne kuuluvat VM:aan (qemu), ei LXC:hen; tama on avoin kysymys.

## Tonin kayttoonotto (numeroitu, jokaiselle tarkistus)

Tehdaan isannalla (`ssh root@100.87.110.22`). Ennen isoja muutoksia snapshot-kaytanto on voimassa; tama kokonaisuus ei kuitenkaan muuta olemassa olevia kontteja.

1. **Hae PR ja tarkista, ettei skripteissa ole mitaan odottamatonta.**
   `git -C /opt/agentti-pohja log --oneline -3 && bash -n hiekkalaatikko/*.sh && echo syntaksi-ok`
2. **Varmista, etta varatut ID:t ja silta ovat vapaat.**
   `pct config 9000; pct config 9001; pct list | awk '$1>=9000 && $1<=9199'; ip link show vmbr2`
   Odotus: ensimmaiset kaksi sanovat, ettei konfiguraatiota ole, kolmas ei listaa mitaan, `ip link` sanoo `does not exist`.
3. **Lataa Debian-pohja ja valitse tallennustila.** `pveam update && pveam available | grep debian-12` ja `pveam download local <tiedosto>`; `pvesm status` (valitse thin-pool, jossa on yli 40 Gt vapaata).
   Tarkistus: `pveam list local | grep debian-12`.
4. **Kuiva-ajo luo-pohjalle.**
   `HL_TALLENNUS=konttilevy HL_DEBIAN_POHJA=local:vztmpl/<tiedosto> HL_PROXY_LAN_IP=192.168.1.<vapaa>/24 HL_PROXY_LAN_GW=192.168.1.1 ./hiekkalaatikko/luo-pohja.sh --kuiva`
   Tarkistus: tuloste on luettavissa, siina ei ole yhtaan komentoa muuhun kuin ID:hin 9000/9001 ja siltaan vmbr2; mitaan ei ajettu (`pct list` ennallaan).
5. **Paata Proxmox-palomuurista** (valinnainen kerros 3). `pve-firewall status`. Jos `disabled`, ala kytke sita paalle tama takia ilman erillista harkintaa; kerrokset 1 ja 2 toimivat ilman.
6. **Ota snapshotit tai varmuuskopio tarvittaessa.** Tama ei muuta olemassa olevia kontteja, mutta silta-asetus (`/etc/network/interfaces.d/hiekkalaatikko`) ja `ifup vmbr2` koskevat isannan verkkoa: tarkista ensin `cat /etc/network/interfaces | grep source` (pitaa lukea `interfaces.d`). Tarkistus: `ip -br addr > /root/ennen-vmbr2.txt`.
7. **Aja luo-pohja.sh oikeasti** samoilla muuttujilla ilman `--kuiva`-lippua.
   Tarkistus: `pct list | grep -E '^(9000|9001)'` (9000 on `template`), `ip -br addr show vmbr2` (ei IPv4-osoitetta), `pct exec 9001 -- squid -k parse`. Skripti ajaa itse kaksi verkkokoetta: proxyn kautta github vastaa (`200`) ja suora yhteys 1.1.1.1:een estyy (`ok-suora-yhteys-estetty`). Jos jompikumpi epaonnistuu, **pysahdy** ja liita tuloste.
8. **Kuiva-ajo kokeilulle.**
   `./hiekkalaatikko/aja-kokeilu.sh --kuiva --kokeilu turva-01 --adapteri kuiva`
   Tarkistus: kaikki `pct`-rivit koskevat vain 9000 (lahde), 9100 ja 9001 (lokin luku); palomuuritiedosto nayttaa `policy_out: DROP`.
9. **Ensimmainen oikea ajo testiadapterilla (ei kuluta mitaan).**
   `./hiekkalaatikko/aja-kokeilu.sh --kokeilu turva-01 --adapteri kuiva`
   Tarkistus: `cat /var/lib/hiekkalaatikko/kokeilut.jsonl`, `pct list | awk '$1>=9100'` (tyhja, klooni tuhottu), `python3 hiekkalaatikko/hl.py taulu /var/lib/hiekkalaatikko/kokeilut.jsonl`.
10. **Verkkokoe kloonin sisalta** (`--sailyta`): `./hiekkalaatikko/aja-kokeilu.sh --sailyta --kokeilu turva-01 --adapteri kuiva`, sitten `pct start 9100 && pct exec 9100 -- bash -c 'curl -sS -m 8 -o /dev/null -w "%{http_code}\n" https://github.com; curl -sS -m 8 -o /dev/null https://example.com; curl -sS -m 5 --noproxy "*" http://192.168.1.116:4000 || echo estetty'`. Odotus: github 200, example.com estetty (403), suora LAN-yhteys estetty. Tuhoa lopuksi: `pct stop 9100; pct destroy 9100 --purge 1; rm /etc/pve/firewall/9100.fw`.
11. **LiteLLM-ajo:** aseta `HL_LLM_URL` ja `LITELLM_API_KEY` ymparistoon itse (esim. `read -rs LITELLM_API_KEY; export LITELLM_API_KEY`; avainta ei kirjoiteta tiedostoon eika tulosteta), sitten `./hiekkalaatikko/aja-kokeilu.sh --kokeilu vertailu-01 --adapteri litellm --malli deepseek-flash`. Tarkistus: rivi `kokeilut.jsonl`:ssa, `kustannus_usd` ei ole `null`.
12. **Vasta sitten** `turva-01` ja `kauppa-01` LiteLLM-adapterilla.

Peruutus: kokeilut ovat itsenaisia ja kloonit tuhoutuvat ajon lopussa. Koko hiekkalaatikon voi poistaa kasin: `pct destroy 9001 --purge 1; pct destroy 9000 --purge 1` ja poistamalla `/etc/network/interfaces.d/hiekkalaatikko` + `ifdown vmbr2` (huom: ei automatisoitu tarkoituksella).

## Testit

`python3 -m unittest test.test_hiekkalaatikko` (48 testia, vakiokirjasto). Ajaa `hl.py`:n yksikkotestit, LiteLLM-adapterin vaaralla palvelimella, isantaskriptit vaaralla `pct`:lla (kuiva-ajo ei koske mihinkaan, taysi ajo, aikakatto, tuhoamisen esto, paivakatto, lukko) ja skriptien lint-tarkistukset (`bash -n`, ASCII, salaisuuskuviot, `ct()`-kutsupolku; `shellcheck` ajetaan jos se on asennettu).

## Avoimet kysymykset Tonille

1. Kustannus- ja aikakattojen oletukset (0,50 USD/kokeilu, 3 USD/paiva, 10 kokeilua/paiva, 1800 s) ovat ehdotuksia. Hyvaksytko vai muutetaanko?
2. Kytketaanko Proxmox-palomuuri paalle (datacenter-taso) per-CT-saantoja varten, vai riittavatko silta + proxy?
3. Clauden adapteri: toteutetaanko shell-tyokalu `pct exec`:in ylle (MCP), vai ajetaanko Claude-kokeilut vasta kun kloonissa on turvallinen oma reitti?
4. `turva-01` jakaa isannan ytimen. Halutaanko hyodyntamiskokeille erillinen VM-pohja?
5. mcp-model-routerin eval-skeema: anna esimerkkitiedosto, niin `eval-vienti` muunnetaan sen muotoon.
6. `kauppa-01` olettaa julkista kauppa-sandbox-repoa ja julkista datalahdetta GitHubissa tai PyPI:ssa; jos kauppatiimi haluaa muun datalahteen, se on lisattava proxyn allowlistiin erikseen (Tonin paatos).
