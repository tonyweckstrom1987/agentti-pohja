# Mallipalvelin Aivon yksityiselle tilalle

Tämä kansio valmistelee paikallisen mallipalvelimen (Ollama) samaan konttiin, jossa Aivon keskustelupalvelu ajetaan. Ollama kuuntelee vain osoitteessa 127.0.0.1, joten yksityinen data ei kulje verkossa. **PR ei asenna mitään. Käyttöönotto on erillinen vaihe, jonka Tony tekee itse.**

Päätös (Tony, 8.11.2026, A907): vaihtoehto (a), malli samassa kontissa kuin Aivo. Omaa konttia ei luoda, eikä palomuuria muuteta.

## Sisältö
- `asetukset.esimerkki`: kolme muuttujaa (Ollaman versio, portti, muistiraja). Oikeat arvot vain paikalliseen `asetukset.env`-tiedostoon, jota ei viedä repoon.
- `asennus-kontissa.sh`: asennus Aivon kontin sisällä. Tarkistaa latauksen tarkistussumman ja asettaa palvelulle `OLLAMA_HOST=127.0.0.1:<portti>` sekä muistirajan.
- `mallit.json`: kolme ehdokasmallia (kaksi 8B-mallia ja yksi 4B-vertailumalli).
- `tapaukset.json`: keksityt testitapaukset mittauksiin.
- `mittaa.py`: mittausskripti.

## Suojaukset
- Ollama kuuntelee vain osoitteessa 127.0.0.1. Portti on 8081, sama kuin Aivon yksityisen tilan oletus (`OLETUS_MALLI_URL` tiedostossa `aivo/yksityinen.py`). Aivon koodiin ei tarvita muutosta.
- Aivon yksityinen tila hyväksyy vain `http` ja loopback-IP:n. Tämä tarkistus pysyy ennallaan.
- `mittaa.py` käyttää samaa sääntöä: vain `http` ja loopback-IP, ei nimiä eikä tunnuksia osoitteessa. Kontin LAN- tai tailnet-osoite hylätään.
- Skriptit eivät kosketa palomuuriin, verkkoasetuksiin, Tailscaleen eivätkä isäntään.
- Palvelun käyttäjä `ollama` on vähäoikeuksinen ja palvelulla on `NoNewPrivileges` sekä `ProtectSystem=strict`.
- Ollama tarvitsee ulospäin yhteyden vain mallien lataamiseen (`ollama pull`). Keskusteluja se ei lähetä minnekään.

## Muistitarve
- 8B-malli vie arviolta 6,5 Gt (`mallit.json`; arvio, ei mitattu). Lisäksi tarvitaan tilaa kehotteen välimuistille, Aivon keskustelupalvelulle ja järjestelmälle.
- Suositus: kontin muistiksi vähintään 8 Gt, jos ajetaan 8B-mallia. 4B-mallille (4 Gt) riittää pienempi. Tämä on arvio, joka tarkistetaan mittauksella.
- `OLLAMA_MUISTI_MB` (oletus 7424) rajaa vain Ollama-palvelun. Sen pitää olla pienempi kuin kontin muisti.
- **Kontin muistin nosto on Tonyn tehtävä isännällä. Tämä PR ei muuta muistia.**

Tarkista kontin muisti Aivon kontin sisällä (vain lukukomento, ei muuta mitään):
```
free -m
```
Rivin `Mem:` ensimmäinen luku (`total`) on kontille annettu muisti megatavuina. Komento on ajettu CT115:ssä, ja se näyttää kontin rajan (6144), ei isännän koko muistia.

## Käyttöönotto (Tony, vasta PR:n yhdistämisen jälkeen)
1. Ota snapshot Aivon kontista ennen asennusta. Tarkista, että kontilla on riittävästi muistia (ks. yllä) ja levytilaa (`df -h /`).
2. Kopioi `asetukset.esimerkki` nimelle `asetukset.env` ja täytä `OLLAMA_VERSIO`. Ota tarkistussumma Ollaman julkaisun `sha256sum.txt`-tiedostosta.
3. Aja Aivon kontissa: `bash asennus-kontissa.sh asetukset.env <sha256>`.
4. Tarkista kuuntelu: `ss -ltn | grep 8081`. Paikallisen osoitteen pitää olla `127.0.0.1:8081`, ei `0.0.0.0` eikä `*`.
5. Lataa mallit: `ollama pull qwen3:8b` jne. (Tunnisteet on tarkistettava, katso `mallit.json`.)
6. Aivon yksityinen tila käyttää osoitetta `http://127.0.0.1:8081`.

## Avoin kohta: mallin nimi
Aivon yksityinen tila (#240) kutsuu osoitetta `/v1/chat/completions` eikä lähetä pyynnössä `model`-kenttää. llama.cpp:n palvelin sietää sen. Ollaman rajapinta tarvitsee mallin nimen, ja puuttuva kenttä aiheuttaa todennäköisesti virheen. Tätä ei ole voitu kokeilla, koska Ollamaa ei ole asennettu. Ennen käyttöönottoa tämä pitää kokeilla (`curl` ilman `model`-kenttää). Jos se epäonnistuu, #240:een tarvitaan pieni lisäys: asetus mallin nimelle. Se ei löysennä osoitetarkistusta.

## Mittaus
```
python3 -I -m isanta.mallipalvelin.mittaa qwen3:8b llama3.1:8b gemma3:4b
```
Oletusosoite on `http://127.0.0.1:8081`. `MALLIPALVELIN_URL` voi vaihtaa sen, mutta vain loopback-IP hyväksytään. Skripti tulostaa taulukon (ohjeet, yksityisyys, tokenia sekunnissa) ja perään JSON-tulokset. Tapaukset ovat keksittyjä. Suomen kielen laatua skripti ei arvioi: sen arvioi Tony lukemalla vastaukset.

## Testit
```
python3 -m unittest test.test_mallipalvelin
AIVO_YKSITYINEN_PY=<polku #240:n aivo/yksityinen.py:hyn> python3 -m unittest test.test_mallipalvelin
```
Jälkimmäinen ajaa myös yhteistestin, joka vertaa osoitesääntöä #240:n `sallittu_malli_osoite`-funktioon. Ilman ympäristömuuttujaa yhteistesti ohitetaan.
