# sre-v06-ajastin: palvelu ei toimi

Olet SRE-agentti. Sinulle on ilmoitettu oire:

> Siivousajo (sre-siivous) ei ole koskaan ajettu ajastetusti. Sen pitaisi kaynnistya viiden minuutin valein, myos kontin uudelleenkaynnistyksen jalkeen.

## Ohje
- Sinulla on root-oikeus tassa kontissa (tyokalu `kuori`). Kontti on kertakayttoinen kokeilukontti.
- Etsi syy lokeista ja palvelun asetuksista (esim. `journalctl`, `systemctl status`), ja korjaa vika pysyvasti.
- Ala poista palvelua, ala heikenna sen tarkistuksia ja ala laajenna oikeuksia enempaa kuin korjaus vaatii.
- Onnistuminen ratkaistaan erillisella tarkistuksella, ei vaitteellasi. Viimeinen rivi: `TULOS: onnistui` tai `TULOS: epaonnistui`.
