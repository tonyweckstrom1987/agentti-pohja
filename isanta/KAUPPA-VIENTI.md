# Kauppadatan vienti (isännän cron)

Isännän root kopioi kauppatiimin datan (vain luku) kontista CT119 konttiin CT115. Kopio on tuotannon päiväajon jälkeinen, ja `agentti/tilannekuva.py` tarkistaa sen tuoreuden. Tonyn päätös 9.11.2026 (A915): ajastus on versionhallinnassa, joten aikamuutokset kulkevat pull request -pyyntönä.

| Tiedosto | Asennuskohde isännällä | Oikeudet |
|---|---|---|
| `isanta/kauppa-vienti.sh` | `/usr/local/sbin/kauppa-vienti.sh` | root:root 0755 |
| `isanta/kauppa-vienti.cron` | `/etc/cron.d/kauppa-vienti` | root:root 0644 |

Skripti on sisällöltään sama kuin aiempi käsin asennettu versio 2 (muuttumaton). Ajoaika on **10.47** (isännän paikallinen aika), aiemmin 06.17. Cron-tiedoston kentät: minuutti, tunti, kuukaudenpäivä, kuukausi, viikonpäivä, käyttäjä, komento.

## Asennus (yksi komento, isännällä rootina)

Aja tämän repon tuoreessa kloonissa (`main`, PR yhdistetty):

```
install -m 755 -o root -g root isanta/kauppa-vienti.sh /usr/local/sbin/kauppa-vienti.sh && install -m 644 -o root -g root isanta/kauppa-vienti.cron /etc/cron.d/kauppa-vienti
```

Komento korvaa saman nimisen vanhan cron-tiedoston, joten kaksoisajastusta ei synny. Mitään ei käynnistetä uudelleen: cron lukee `/etc/cron.d`-hakemiston itse.

Käsiajo heti asennuksen jälkeen (Tonyn päätös 9.11.2026, A925). Sama komento kuin cron-rivillä, jotta kopio päivittyy eikä varoitus "kopio yli 26 h vanha" tule siirtymäpäivänä:

```
sh /usr/local/sbin/kauppa-vienti.sh >>/var/log/kauppa-vienti.log 2>&1
```

Todennus CT115:ssä (PAIVITETTY-tiedoston aikaleima):

```
cat /var/lib/claude-jono/kauppa/PAIVITETTY
```

Odotettu: yksi rivi, jonka aikaleima on käsiajon hetki (muoto `2026-11-09T11:02:41+02:00`, ero nykyhetkeen alle minuutti). Jos aikaleima on vanha, katso `tail -3 /var/log/kauppa-vienti.log` isännällä.

Tarkistus asennuksen jälkeen:

```
sha256sum isanta/kauppa-vienti.sh /usr/local/sbin/kauppa-vienti.sh && cat /etc/cron.d/kauppa-vienti
```

Odotettu: kaksi samaa tiivistettä (`dd9fde05847a…`) ja cron-rivi `47 10 * * * root sh /usr/local/sbin/kauppa-vienti.sh >>/var/log/kauppa-vienti.log 2>&1`. Seuraavana päivänä: `tail -3 /var/log/kauppa-vienti.log`, viimeinen rivi päättyy tekstiin `kauppa-vienti ok`.

## Siirtymän varoitus ja käsiajo

`KAUPPA_MAX_IKA_H` on 26 h. Vanha ajo oli 06.17 ja uusi 10.47, joten siirtymävälistä tulee 28,5 h, jos kopiota ei päivitetä ennen uutta ajoaikaa. Siksi aja vientiskripti käsin heti asennuksen jälkeen ja ennen kello 10.47 (komento yllä, Tonyn päätös A925). Silloin kopio päivittyy ennen kuin ikä ylittää rajan, eikä varoitusta tule.

Jos käsiajo jää tekemättä, tilannekuva näyttää yhden `VAROITUS: kauppadatan kopio on vanhentunut` -rivin. Se on odotettu eikä kerro viasta, ja se poistuu seuraavasta ajosta (10.47).

Raja-arvoa ei muuteta tässä. Pysyvä rajan nosto on kattoarvon kaltainen muutos ja jää Tonyn päätettäväksi.

## Ajoaikaa muutettaessa

Muuta vain `isanta/kauppa-vienti.cron`, avaa PR ja aja yllä oleva asennuskomento. Valitse aika, joka ei osu lauantain kauppa-ajoon (klo 10.00) eikä tuotannon päiväajoon. Testit tarkistavat rivin muodon.
