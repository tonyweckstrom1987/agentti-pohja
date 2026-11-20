# Yksityisen tilan mallivertailu (aivo/mallivertailu.py)

Tämä ohje on Tonylle. Työkalu vertaa yksityisen tilan malliehdokkaita synteettisellä, suomenkielisellä testisarjalla. Se ei muuta palvelun asetuksia eikä `AVOIMET_MALLIT`-listaa.

## Mitä mitataan

Testisarjassa on 20 keksittyä tehtävää (`aivo/testidata/mallivertailu_tehtavat.json`):

| Osa-alue | Tehtäviä | Pisteytys |
|---|---|---|
| Päätösapu ja suunnittelu | 5 | tuomari; laskutehtävissä lisäksi mekaaninen tarkistus |
| Ohjeiden noudattaminen (`[[EHDOTUS …]]`, `[[VALINNAT: …]]`) | 4 | mekaaninen ja tuomari |
| Rehellisyys (ei taustalupauksia, ei keksittyjä lähteitä) | 4 | mekaaninen ja tuomari |
| Saman keskustelun muistaminen yli 20 viestin takaa | 3 | mekaaninen ja tuomari |
| Huolellinen suomi | 4 | mekaaninen ja tuomari |

Järjestelmäkehote on sama kuin tuotannossa (`yksityinen_or.KEHOTE`). Ydinohjeet ja muisti ovat keksittyjä.

Huomio: tuotanto lähettää mallille vain 12 viimeistä viestiä (`yksityinen.HISTORIA_YLARAJA`). Muistitehtävät lähettävät koko historian, jotta mallin kyky voidaan mitata. Tuotannossa tieto 20 viestin takaa ei näy mallille lainkaan, valitaanpa mikä malli tahansa.

## Turvarajat

- Jokainen kutsu kulkee `openrouter_kulut.kutsu`n kautta: yhteinen päiväkatto, ZDR ja `data_collection=deny` pakotettuina sekä kulukirjaus. Kuljetin on tuotannon `yksityinen_or.tee_kuljetin`.
- Vain `EHDOKKAAT`-listan mallit kelpaavat. `AVOIMET_MALLIT`-listan mallit ajetaan lipulla `yksityinen_chat=True` kuten tuotannossa. Listan ulkopuoliset avoimen painoston mallit ajetaan ilman lippua, koska data on synteettistä. ZDR ja `deny` ovat silti pakotettuja.
- Kuluraja on 3 $ koko ajolle (OpenRouterin ilmoittama `usage.cost`). Malli ohitetaan kahden peräkkäisen virheen jälkeen, ja koko ajo pysähtyy kuuden virheen jälkeen. Syy: kirjanpito kirjaa epäonnistuneen kutsun varovaisena 0,50 $:n kuluna.

## Ajo kontissa 122

Työkalua ei ole kytketty palveluun. Se ajetaan käsin.

**Käyttäjä ja avain.** Kontin 122 asennus ajaa palvelua käyttäjänä `aivo` (ei `aivo-yksityinen`). Avain on tiedostossa `/var/lib/aivo-yksityinen/openrouter-avain` (0600, omistaja `aivo`). Työkalu lukee sen samalla funktiolla kuin tuotanto (`yksityinen_or.lue_avain`) eikä tulosta sitä.

**Valmistelu.** Ota ensin snapshot kontista 122. Luo sitten tuloskansio, jota vain `aivo` voi lukea:

```
install -d -m 700 -o aivo -g aivo /var/lib/aivo-yksityinen/mallivertailu
```

Työkalu luo kansion itsekin oikeuksilla 0700, jos sitä ei ole. Komento `install -d` varmistaa omistajan ja oikeudet myös silloin, kun kansio on jo olemassa.

**Ajo** (root-käyttäjänä kontissa 122):

```
runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -B -m aivo.mallivertailu aja --ulos /var/lib/aivo-yksityinen/mallivertailu
runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -B -m aivo.mallivertailu paketti --ulos /var/lib/aivo-yksityinen/mallivertailu
```

`-B` estää `__pycache__`-kansioiden kirjoittamisen asennushakemistoon.

**Mitä ajo tulostaa.** Komento `aja` tulostaa yhden rivin jokaisesta kutsusta muodossa `malli tehtävä/toisto ok` tai virheen kohdalla `malli tehtävä/toisto Virhetyyppi` (vain tyypin nimi). Lopuksi se tulostaa yhteenvedon JSON-rivinä, esimerkiksi:

```
{"kulut_usd": 1.2345, "virheita": 0, "pysahtyi": null, "mallit": [...], "toistot": 2, "raja_usd": 3.0}
```

Kenttä `pysahtyi` on `null`, kun ajo meni loppuun. Muut arvot ovat `kuluraja`, `paivakatto` (yhteinen päiväkatto täyttyi) ja `virheita`. Kansioon syntyvät tiedostot `vastaukset.jsonl` (yksi rivi per kutsu) ja `ajo.json` (yhteenveto). Komento `paketti` tulostaa rivin `naytteita N`.

**Kesto.** Ajo tekee enintään 9 × 20 × 2 = 360 kutsua peräkkäin. Kutsun aikaraja on 120 sekuntia. Arvio on noin 1–2 tuntia, jos vastaus kestää keskimäärin 10–20 sekuntia. Tätä ei ole mitattu, koska työkalua ei ole vielä ajettu oikeaa rajapintaa vastaan. Pahimmassa tapauksessa (jokainen kutsu aikarajaan) ajo kestäisi kauemmin, mutta virheraja pysäyttää sen kuuden virheen jälkeen. Aja komento esimerkiksi `tmux`- tai `screen`-istunnossa, jotta yhteyden katkeaminen ei keskeytä sitä.

**Kulut.** Arvioitu yläraja on noin 1,6 $ (ZDR-endpointtien kalleimmilla hinnoilla ja 700 tokenin vastauksilla). Kova raja on 3 $. Kutsut kirjataan samaan kulukirjanpitoon ja samaan päiväkattoon kuin tuotannon chat. Jos päiväkatto täyttyy, ajo pysähtyy, ja myös chat on silloin katossa saman päivän loppuun.

Komento `paketti` kirjoittaa sokkoutetun tuomarisyötteen `tuomari_syote.jsonl` ilman mallien nimiä ja avaimen `tuomari_avain.json` erikseen.

Tuomari (Opus) saa vain tiedoston `tuomari_syote.jsonl` ja palauttaa tiedoston `tuomari_pisteet.json` muodossa `{"n0000": 7, ...}` (0–10). Sen jälkeen:

```
runuser -u aivo -- env PYTHONPATH=/opt/agentti-pohja python3 -B -m aivo.mallivertailu taulukko --ulos /var/lib/aivo-yksityinen/mallivertailu --tuomari /var/lib/aivo-yksityinen/mallivertailu/tuomari_pisteet.json
```

Tuloste sisältää taulukon (kokonaispisteet, osa-alueet, $ / 100 viestiä, vastausajan mediaani) ja kolmen parhaan mallin vastaukset kolmeen samaan tehtävään kirjaimin A–C. Kirjainten avain on tiedostossa `sokko_avain.json`.
