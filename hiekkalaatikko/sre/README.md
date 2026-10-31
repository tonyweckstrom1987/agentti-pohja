# Agentti-SRE: vikojen injektointi ja korjausmittaus

Hiekkalaatikon ensimmainen varsinainen kaytto (Tonyn paatos 31.10.2026). Kertakayttokloonin sisaan injektoidaan tahallinen vika, agentti saa vain oireen ja paasyn konttiin, ja erillinen tarkistusskripti ratkaisee onnistuiko korjaus. Mitataan onnistumisprosentti, korjausaika ja kustannus mallikohtaisesti. Rivit kirjataan `kokeilut.jsonl`:iin (hl.py:n muoto), josta ne ovat eval-dataa mcp-model-routerille ja pohjaa yovahdin korjausehdotuksille.

**Tila: kirjoitettu ja testattu fixtureilla. Mitaan ei ole ajettu oikeassa kontissa eika isannalla.** Erityisesti vika 4 (tmpfs-mount) vaatii, etta kloonin LXC sallii `mount -t tmpfs`; jos ei, injektointi paattyy koodiin 3 ja ajo kirjautuu `keskeytetty` (ei mallin syyksi).

Kaikki tiedostot ovat ASCII-muodossa.

## Rakenne

```
hiekkalaatikko/
  aja-kokeilu.sh              ajaa YHDEN kokeilun (klooni, katot, snapshot, adapteri, tarkistus, kirjaus). Uusi: salainen alustus.
  kokeilut/sre-vNN-*.md       agentin saama kuvaus: VAIN oire + yleiset ohjeet (generoitu)
  kokeilut/sre.alustus.sh, sre.tarkistus.sh, sre.salainen.d   (generoitu; yhteiset kaikille sre-*-kokeiluille: kokeilun nimi
                              annetaan argumenttina ja valitsee vikaskriptin; salainen.d on linkki sre/viat-kansioon)
  sre/viat/sre_lib.py         yhteinen kirjasto (Ymp: juuri, komentojen ajo, isantanimi; yhteiset tarkistusapurit)
  sre/viat/v01..v06_*.py      vikakirjasto: kukin vika oma skriptinsa (injektoi | tarkista | oire)
  sre/luo_kokeilut.py         generoi kokeilut/sre-*-tiedostot vikakirjastosta (--tarkista vertaa)
  sre/matriisi.py             sama vika usealle mallille: kutsuu aja-kokeilu.sh:ta yksi ajo kerrallaan
  sre/yhteenveto.py           mallikohtainen yhteenveto kokeilut.jsonl:sta
  test/test_sre.py            testit (55 kpl, ei oikeaa konttia)
```

## Vikakirjasto

| # | kokeilu | vika (todellinen tausta) | korjaus on esim. | tarkistus |
|---|---|---|---|---|
| 1 | `sre-v01-polku` | `Environment=PATH=/opt/sre/bin` -> `runuser` ei loydy (30.10.) | PATH takaisin tai absoluuttinen polku | `systemctl start`, `Result=success`, onnistumismerkki; skriptin `runuser -u nobody` on yha paikallaan |
| 2 | `sre-v02-valilyonti` | `Environment=GIT_SSH_COMMAND=ssh -i ...` ilman lainausmerkkeja halkeaa valilyonnista | lainausmerkit arvon ymparille | start + merkki; skriptin tiukka `!= "$odotettu"`-vertailu ennallaan |
| 3 | `sre-v03-oikeus` | asetustiedosto root:root 0600, palvelu ajaa kayttajana `sre-palvelu` | `chown`/ryhma + 0640 | start + merkki; `User=sre-palvelu` ennallaan; tiedosto ei saa olla muille luettava/kirjoitettava |
| 4 | `sre-v04-levy` | datahakemisto on 1 Mt:n tmpfs, jonka `vanha-vedos.bin` tayttaa | vedos pois tai tilaa lisaa | start + merkki + `tulos.dat` olemassa; skriptin 200 kt kirjoitus ennallaan |
| 5 | `sre-v05-env` | `SRE_API_URL` puuttuu `EnvironmentFile`:sta, palvelu poistuu koodilla 78 | muuttuja takaisin (esimerkkiarvo on skriptin kommentissa) | start + merkki, jossa URL |
| 6 | `sre-v06-ajastin` | `sre-siivous.timer` on olemassa mutta ei enabled eika active | `systemctl enable --now` | `is-enabled`=enabled, `is-active`=active, palvelu ajettavissa |

Jokainen tarkistus on *kayttaytymiseen* perustuva (palvelu oikeasti kaynnistetaan) ja lisaksi tarkistaa, ettei tyokuormaa ole ontoistettu tai heikennetty (esim. skriptin tarkistus poistettu, `User=` vaihdettu rootiksi, tiedosto `0666`). Tarkistus poistaa onnistumismerkin ennen ajoa, joten agentti ei voi kirjoittaa sita kasin. Uuden vian lisays: kopioi lahin `vNN_*.py`, anna uusi `KOKEILU`-nimi, aja `python3 hiekkalaatikko/sre/luo_kokeilut.py` ja `python3 -m unittest test.test_sre` (testi vaatii, etta generoidut tiedostot ovat ajan tasalla).

## Ajokehys

Yhden ajon kulku (`aja-kokeilu.sh --kokeilu sre-v01-polku --adapteri litellm --malli M`):

1. varaus (kustannus) -> klooni mallipohjasta -> katot -> snapshot `ennen` -> kaynnistys
2. **alustus**: `kokeilut/sre.salainen.d`-tiedostot pusketaan kloonin `/root/.hl-salainen/`-hakemistoon, `alustus.sh` injektoi vian, hakemisto **poistetaan** ennen adapteria. Jos alustus epaonnistuu, adapteria ei ajeta ja rivi kirjautuu `keskeytetty` (ei lasketa mallin epaonnistumiseksi).
3. adapteri (`litellm`, malli `--malli`) saa kuvauksen: vain oire. Aikakatto (oletus 1800 s) ja kustannuskatto (oletus 0,50 USD) ovat `aja-kokeilu.sh`:n / `hl.py`:n; tama kokonaisuus ei muuta niita.
4. **tarkistus**: vikakirjasto pusketaan uudelleen isannalta, `tarkistus.sh` ajaa `tarkista`-komennon, hakemisto poistetaan. Poistumiskoodi ratkaisee lopputuloksen; agentin oma `TULOS:`-vaite ei vaikuta (`hl.py paata`).
5. snapshot `jalkeen`, klooni tuhotaan (vain jos nimi ja silta tasmaavat), rivi `kokeilut.jsonl`:iin.

Turva: vikaskriptit kieltaytyvat (koodi 78) ellei isantanimi ole `hl-9100`..`hl-9199`; ne eivat siis tee mitaan, jos ne ajetaan vahingossa tuotantokontissa. Tuotanto-CT:hin ei kosketa: kaikki `pct`-kutsut kulkevat `aja-kokeilu.sh`:n `ct()`-funktion (ID-vali 9100-9199) kautta. Ei salaisuuksia: vikojen ssh-"avainta" ei ole olemassakaan, vain polku.

**Rajoitus:** agentilla on root kloonissa, joten se voisi periaatteessa vaarentaa `systemctl`/`python3`-ohjelmia tai muuttaa vian jaljen. Tarkistus ajetaan kloonin sisalla, joten tama on tunnettu, hyvaksytty riski kertakayttokloonissa (kloonin tuhoaa `aja-kokeilu.sh`). Tulosten luotettavuutta parantaa `lokit/<id>/agentti.log`:n tarkastus otantana.

## Ajaminen (Tony, Proxmox-isannalla, vasta kun hiekkalaatikko on otettu kayttoon README:n mukaan)

```
# 1. kuiva-ajo: tulostaa suunnitelman ja kaikki pct-komennot, ei koske mihinkaan
python3 hiekkalaatikko/sre/matriisi.py --mallit qwen-coder,deepseek-flash --viat v01,v02 --kuiva
# 2. yksi oikea ajo (aseta LITELLM_API_KEY ja HL_LLM_URL ymparistoon itse, ks. ../README.md)
./hiekkalaatikko/aja-kokeilu.sh --kokeilu sre-v01-polku --adapteri litellm --malli deepseek-flash
# 3. matriisi (6 vikaa x mallit)
python3 hiekkalaatikko/sre/matriisi.py --mallit qwen-coder,deepseek-flash,deepseek-pro --viat v01,v02,v03
# 4. tulokset
python3 hiekkalaatikko/sre/yhteenveto.py /var/lib/hiekkalaatikko/kokeilut.jsonl --malli-vika
python3 hiekkalaatikko/hl.py eval-vienti /var/lib/hiekkalaatikko/kokeilut.jsonl > sre-eval.jsonl
```

Paivaraja: `aja-kokeilu.sh`:n oletus on 10 kokeilua/paiva (koodin kova ylaraja 20). Taysi matriisi 6 vikaa x 3 mallia = 18 ajoa ei mahdu oletukseen, ja `matriisi.py` hylkaa sen ennen ensimmaista ajoa. Jaa se osiin (esim. `--viat v01,v02,v03` ja loput seuraavana paivana) tai nosta `HL_KOKEILUA_PAIVA` enintaan arvoon 20 itse. Rajaa ei kierreta koodissa.

## Tulosten tulkinta

`yhteenveto.py` tulostaa mallikohtaisesti:

- **onnistumis-%** = onnistui / ratkaistut. `keskeytetty` (aikakatto, alustus epaonnistui, Ctrl-C) ei lasketa nimittajaan, koska se ei kerro mallin kyvysta; se nakyy omassa sarakkeessa. Jos keskeytettyja on paljon, tulos on epaluotettava.
- **korjausaika** = `kesto_s`-mediaani vain onnistuneista ajoista. Se on koko ajon kesto (klooni, alustus, agentti, tarkistus), joten siina on vakioylimaara noin minuutin luokkaa; vertaa malleja keskenaan, ala ota absoluuttisena.
- **hinta** = LiteLLM:n raportoima kustannus (`x-litellm-response-cost`). Jos kustannus ei ole tiedossa, rivin kulu on `null`, kirjanpito pitaa pessimistisen varauksen (koko katto) ja rivi lasketaan sarakkeeseen "kulu tuntematon" eika summaan.
- **malli x vika** (`--malli-vika`): nayttaa, mitka viat ovat vaikeita. Tyypillisesti vika 4 ja 5 vaativat enemman tutkimusta kuin 6.

Pieni otos: 1 ajo per vika ja malli on anekdootti. Raportoi aina ajomaara (n) ja jata johtopaatokset tekematta alle ~5 ajosta per solu. LLM-vastaukset vaihtelevat; toista ajot.

Eval-vienti (`hl.py eval-vienti`) antaa yhden rivin per ratkaistu ajo: `tehtava` = kokeilun nimi (`sre-v01-polku`), `malli`, `onnistui`, `kesto_s`, `kustannus_usd`. Jos mcp-model-routerin skeema on eri, anna esimerkki (avoin kysymys hiekkalaatikon README:ssa).

Yovahdin korjausehdotukset: onnistuneiden ajojen `lokit/<id>/agentti.log` sisaltaa agentin komennot ja korjauksen. Siita voi poimia "oire -> syy -> korjaus" -parit ehdotuspohjiksi. Ehdotukset ovat vain ehdotuksia: yovahti ei saa soveltaa niita itse.

## Ehdotus julkaistavaksi yhteenvedoksi ("annoin N mallille rikkinaisen palvelimen")

Julkaise vasta kun oikeita ajoja on riittavasti (suositus: vahintaan 5 per vika ja malli) ja Tony on tarkistanut luvut ja lokit (ei salaisuuksia, ei sisaisia osoitteita). Runko:

> **Annoin 3 mallille rikkinaisen palvelimen**
> Rakensin kertakayttoisen Linux-kontin, johon injektoin 6 tahallista vikaa, jotka ovat oikeasti sattuneet minulle (komento puuttuu PATHista, systemd-arvo halkeaa valilyonnista, vaarat tiedosto-oikeudet, levy taynna, puuttuva ymparistomuuttuja, ajastin joka ei kaynnisty). Malli sai vain oireen ("palvelu X ei toimi"), root-oikeuden ja aikarajan. Erillinen skripti kayttaa palvelua ja ratkaisee onnistumisen, ei mallin oma vaite.
> Tulokset: taulukko (onnistumis-%, mediaaniaika, hinta per korjaus), kolme havaintoa (mika vika oli vaikein, missa malli "huijasi" tai ontoisti palvelun, mika oli halvin korjaus) ja linkki avoimeen koodiin.
> Opit: miksi tarkistus ei saa luottaa mallin omaan vaitteeseen; mita yksinkertaiset eval-harnessit eivat nae.
