# Aivon oma haastattelu

Aivo on käytössä, kun se tekee ensimmäisen oman haastattelunsa (Tonyn määritelmä, A502, A616–A618). Aivo laatii kysymykset, ja pilvi-Claude esittää ne koulumatkalla.

## Kierros viikossa

1. **Laadinta.** `python3 -m aivo.kysymykset laadi` käy läpi kirjaston ja etsii ristiriidat ja ohuimmat kohdat. Se kirjoittaa listan tiedostoon `/var/lib/aivo/kysymykset-ulos/VVVV-KK-PP.md`. Sama aihe kysytään vain kerran (A140).
2. **Siirto docs-repoon.** Lista kuuluu docs-repon polkuun `claude-jono/aivon-kysymykset/VVVV-KK-PP.md`. Siirto on tarkoituksella erillinen vaihe, ja vain tämä yleinen lista saa lähteä isännältä (A329). Tony tai hänen hyväksymänsä siirtoskripti tuo tiedoston sinne.
3. **Haastattelu.** Pilvi-Claude lukee uusimman listan ensin ja esittää sen kysymykset. Henkilökohtaisista aiheista se kysyy ensin Tonylta, saako kysyä.
4. **Vastaukset.** Yleiset vastaukset menevät docs-repoon (`## K1`, `## K2` ... -otsikoiden alle), henkilökohtaiset Giteaan Tonyn kautta. Molemmat tuodaan isännälle hakemistoon `/var/lib/aivo/vastaukset/VVVV-KK-PP.md`.
5. **Muistin päivitys.** `lue` kirjaa vastaukset kirjaston merkintöihin (`## Haastattelu PVM` ja `paivitetty`-kentän uusi arvo). `tarkista` on takautuva testi (A577): jokainen luettu vastaus pitää olla kirjattu viikon sisällä, muuten komento palauttaa virhekoodin 1.

Ajastin: `systemd/aivo-kysymykset.{service,timer}.mall` (sunnuntaisin klo 18.00). Palvelussa ei ole verkkoa (`PrivateNetwork=yes`).

## Kirjaston muoto

Aivon kirjaston merkintä on Markdown-tiedosto, jonka alussa on front matter:

```
---
aihe: Python-testaus
luokka: yleinen
avain: paatos-testaus     (valinnainen: sama avain = sama asia)
kanta: unittest           (valinnainen: ristiriita, jos samalla avaimella on eri kanta)
paivitetty: 2026-10-30
---
Runko.
```

- **Ohut** merkintä: runko alle 200 merkkiä tai päivitys yli 180 päivää sitten.
- **Ristiriita:** sama `avain`, eri `kanta`.
- Tämä muoto on oletus. Jos Aivon todellinen kirjasto on erilainen, mukautetaan `lue_merkinta`.

## Tietosuoja

- Kysymyksiin pääsee vain aiheita, joilla on `luokka: yleinen`. Puuttuva tai muu luokka tarkoittaa henkilökohtaista aihetta, ja listassa näkyy vain "henkilökohtainen aihe N".
- Lisäksi aiheen nimi ja avain tarkistetaan arkaluonteisten sanojen varalta (terveys, perhe, raha, tunteet, unen ja sykkeen mittaukset, tunnukset). Osuma tekee aiheesta henkilökohtaisen, vaikka merkintä olisi luokiteltu yleiseksi.
- Kirjaston runkoa ei kopioida listaan. Mukaan tulee vain aiheen nimi.
- Valmis lista tarkistetaan vielä kerran ennen kirjoitusta.
- Tarkistus on sanalista eikä täydellinen: ihmisen nimeä tai muuta tunnistetta se ei tunnista. Siksi luokka on oletuksena henkilökohtainen.

## Pilvi-Clauden koulumatkahaastattelun ohje (lisättävä ohjeeseen)

> Hae ensin uusin tiedosto kansiosta `claude-jono/aivon-kysymykset/` ja esitä sen kysymykset sellaisenaan ennen omia kysymyksiäsi. Kohdista, joiden tunniste on `henkilokohtainen-aihe-N`, kysy ensin Tonylta, saako aiheesta kysyä. Älä arvaile aiheen sisältöä. Kirjaa vastaukset otsikoilla `## K1`, `## K2` ...

## Käytöstä poisto

Poista ajastin (`systemctl disable --now aivo-kysymykset.timer`). Kirjastoon jäävät vain `## Haastattelu`-kohdat, jotka voi poistaa käsin.
