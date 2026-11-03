# Talonmies, vaihe 1: ajastus (Tonyn asennus)

Tämä on ohje Tonylle. Mitään tästä ei aja automaatio. Vaihe 1 on pelkkä ehdotustila: ajo lukee tilan eikä muuta mitään.
Ei sudoers-, ssh-avain- eikä kulukattomuutoksia.

## Tarvittavat oikeudet
Ajo lukee `systemctl --failed`, `df` ja `apt list --upgradable` sekä isännän tilan komennolla `python3 -m agentti.snapshot_kutsu tila`. Viimeinen vaatii CT115:n snapshot-avaimen. Jos ajoa ei tehdä käyttäjänä, jolla avain on, raportin huomautuksiin tulee `isännän tila: ei luettavissa (SnapshotVirhe)`, ja varmuuskopioiden tuoreutta ei tarkisteta. Valitse silloin käyttäjä, jolla avain on (esimerkiksi sama käyttäjä, joka ajaa nykyisiä ajastimia).

## Kokeilu käsin
```
python3 -m agentti.talonmies ajo /var/lib/yojono/talonmies
```
Raportti: `/var/lib/yojono/talonmies/raportit/PVM.md`.

## Päätös ehdotukseen
```
python3 -m agentti.talonmies paata /var/lib/yojono/talonmies <tunniste> kylla
python3 -m agentti.talonmies mittari /var/lib/yojono/talonmies
```

## Thin pool -valvonta (konttilevy)
Ajo luokittelee konttilevyn thin poolin täyttöasteen (data ja metadata, korkeampi ratkaisee): yli 60 % tuo rivin aamukoosteeseen, yli 75 % hälytyksen ja yli 85 % kiireellisen hälytyksen. Raportin kohdassa `Tilanne` on poolin rivi aina, myös kun kaikki on kunnossa.

Ilman muutoksia isännän tilatulosteessa ajo osaa lukea vain datan `pvesm status` -rivistä (`lvmthin`), ja raportin huomautuksissa lukee, ettei metadataa saatu luettua. Metadata täyttyy erikseen ja voi pysäyttää kontit yhtä hyvin kuin data, joten lisää isännän `tila`-tulosteeseen yksi vain luku -rivi. Tämä on Tonyn muutos isännän skriptiin (`isanta/snapshot_isanta.py` on PR-työkalun suojaama, eikä automaatio muuta sitä). Lisää `TILA_KOMENNOT`-listaan rivi:
```
("Thin pool (lvs)", ["lvs", "--noheadings", "--nosuffix", "--separator", ",", "-o", "lv_name,vg_name,lv_attr,pool_lv,data_percent,metadata_percent", "konttilevy/konttilevy"]),
```
Rivi lukee vain yhden poolin eikä muuta LVM-asetuksia. Tulosteen odotettu muoto (yksi rivi): `konttilevy,konttilevy,twi-aotz--,,10.44,0.87`. Päivitä skripti samalla tavalla kuin muut isännän muutokset (`isanta/CT118-asennus.md`, kohta 1) ja ota CT115:stä snapshot ensin.

Hälytys Telegramiin (sisällötön, kiinteä otsikko ilman lukuja) lähtee vain, kun ajo käynnistetään valitsimella:
```
python3 -m agentti.talonmies ajo /var/lib/yojono/talonmies --halyta
```
Se käyttää `agentti.ilmoita`-moduulia, joten tunnukset ovat sen omassa tunnustiedostossa ja tunnin nopeusraja on voimassa. Yksi ylitys hälyttää kerran vuorokaudessa.

## Snapshot-siivousehdotus
Siivousehdotus kertoo kontit, joilla on yli 5 snapshotia, ja ehdottaa säilytettäväksi `perus-toimii`-snapshotin ja kaksi uusinta. Mitään ei poisteta: ohjelma tulostaa vain `pct delsnapshot` -komennot, ja poisto on Tonyn päätös. Jos `perus-toimii` puuttuu kontilta, poistoja ei ehdoteta.

Isännällä (vain luku):
```
for ct in $(pct list | awk 'NR>1 {print $1}'); do echo "## CT$ct"; pct listsnapshot $ct; done > /tmp/snapshotit.txt
```
```
pct push 115 /tmp/snapshotit.txt /tmp/snapshotit.txt
```
CT115:ssä:
```
python3 -m agentti.talonmies snapshot-siivous /tmp/snapshotit.txt
```

## Pysäytys
```
touch /var/lib/yojono/talonmies/PYSAYTA
```
Poista tiedosto, kun ajo saa jatkua.

## Ajastus (vaiheen 1 jälkeen, kun Tony on tyytyväinen raporttiin)
Päivittäinen systemd-ajastin, esimerkiksi klo 05:30 UTC ennen aamukoostetta. Aamukoosteeseen liittäminen on oma pieni muutoksensa.
