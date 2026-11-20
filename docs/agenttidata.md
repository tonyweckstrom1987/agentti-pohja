# Agenttidata: siivottu kopio agenttien tuloksista Giteaan

Tämä on ohje Tonylle. Se perustuu suunnitelmaan `claude-jono/suunnitelmat/aivo-tietolahde-1-tulokset.md` (vaihtoehto a, Tonyn päätös A1003). Koodi on valmis, mutta mitään ei ole asennettu, ajettu palvelimella eikä yhdistetty Giteaan. Tunnukset luot sinä itse. Ohje ei tulosta eikä pyydä yksityisiä avaimia.

## Mitä tämä tekee

Kontin 115 vienti (`python3 -m agentti.docs_vienti`) kopioi docs-repon kopiosta `/opt/agenttifirma-docs` vain nimetyt tiedostot Gitean yksityiseen repoon `tony/agenttidata`. Aivo lukee repon vain luku -avaimella.

| Mukaan | Ei mukaan |
|---|---|
| `claude-jono/tulokset/*.md` | kaikki muu docs-repossa (koulu, matka, ura, kauppa, `isanta-kasin`, `tonylle.md`, ajurin tilatiedostot) |
| `claude-jono/tehtavat/*.md` | alikansiot, muut tiedostopäätteet, piilotiedostot, symboliset linkit |
| `claude-jono/pohdinta-vastaukset.md` | `claude-jono/kompassi.md` (vain valinnalla `--kompassi`; sinun päätöksesi) |

Kohderepossa polut ovat `tulokset/`, `tehtavat/` ja `pohdinta-vastaukset.md`.

Siivous ennen kopiota:

- Tiedosto, jossa on avaimen muotoinen merkkijono (`sk-…`, `ghp_…`, `github_pat_…`, Telegram-tunnus tai yksityisen avaimen otsake), jätetään kokonaan pois. Lokiin tulee vain tiedoston nimi.
- Muissa tiedostoissa poistetaan `avain: arvo` -parit (`token`, `secret`, `password` ja muut), IPv4-osoitteet ja `*.ts.net`-nimet.
- Yli 200 kt:n tiedosto jätetään pois. Jos `pohdinta-vastaukset.md` kasvaa tämän yli, se katoaa kopiosta ja tulosteessa lukee `ohitettu`. Silloin raja pitää päättää uudelleen.
- Siivous ei ole täydellinen: se tunnistaa vain tunnetut muodot. Tonyn nimeä tai muuta henkilötietoa se ei poista.

Push tehdään vain osoitteeseen `ssh://git@<GITEA-IP>[:portti]/tony/agenttidata.git`, jossa `<GITEA-IP>` on Gitean yksi tarkka IPv4-osoite (infrataulukossa kontti 113). Osoite ja erillinen isäntäasetus `DOCS_VIENTI_ISANTA` annetaan asetustiedostossa, eikä niitä kirjoiteta repoon. Isäntäasetuksen pitää olla yksi sisäverkon tai tailnetin osoite (ei julkinen, ei loopback), ja osoitteen isännän pitää olla täsmälleen sama. Muut yksityisverkon osoitteet, nimet, muut polut ja muut käyttäjät estetään ennen kuin gitiä ajetaan. Lisäksi systemd-yksikkö sallii ulospäin vain tämän yhden osoitteen (`IPAddressAllow`).

Palvelu ajetaan omana käyttäjänään `docs-vienti`, ei rootina. Push kohdistetaan suoraan tähän osoitteeseen, joten työkloonin `origin`-asetus ei voi ohjata dataa muualle. Jos mikään ei muuttunut, committia ei tehdä. Vienti ei koskaan kirjoita Aivon kirjastoon (`aivo/aivo`).

Aivon yksityisessä tilassa lähdettä ei saa käyttää ennen kuin PR #322 on yhdistetty (suunnitelman riski 7).

## Tonyn askeleet

Aja askeleet järjestyksessä. Jokaisen komennon jälkeen on `Odotettu`-rivi. Jos tuloste poikkeaa, älä jatka vaan kerro.

Tässä `<ISÄNNÄN-IP>` ja `<GITEA-IP>` ovat infrataulukon osoitteet (isäntä ja kontti 113), `<AIVO>` on Aivon chatin kontin numero (suunnitelmassa 122; varmista askeleessa 1) ja `<SSH-PORTTI>` Gitean SSH-portti.

### Askel 1. Yhteys isännälle

PowerShellissä:

```
ssh root@<ISÄNNÄN-IP>
```

Odotettu:
```
Proxmox-isännän kehote, esim. root@agenttifirma:~#
```

Isännällä:

```
pct list
```

Odotettu:
```
kontit 113 (Gitea) ja 115 (agentscope) käynnissä; merkitse ylös Aivon chatin kontti (122 vai muu)
```

### Askel 2. Snapshotit

```
pct snapshot 115 ennen-agenttidata
pct snapshot <AIVO> ennen-agenttidata
```

Odotettu:
```
komennot palaavat ilman virhettä; pct listsnapshot 115 näyttää rivin ennen-agenttidata
```

### Askel 3. Yltävätkö kontit Giteaan

Gitean SSH-portti:

```
pct exec 113 -- ss -ltn
```

Odotettu:
```
rivi, jossa kuuntelee SSH-portti (22 tai 2222); merkitse se ylös
```

Kontti 115:

```
pct enter 115
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/<GITEA-IP>/<SSH-PORTTI>' && echo yhteys-ok || echo ei-yhteytta
exit
```

Odotettu:
```
yhteys-ok
```

Aivon kontti:

```
pct enter <AIVO>
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/<GITEA-IP>/<SSH-PORTTI>' && echo yhteys-ok || echo ei-yhteytta
exit
```

Odotettu:
```
yhteys-ok
```

Jos jompikumpi antaa `ei-yhteytta`: pysähdy. Älä muuta palomuuria. Kerro, niin suunnitellaan vienti isännän kautta.

### Askel 4. Repo Giteaan

Selaimessa (tailnet päällä): kirjaudu käyttäjänä `tony`, Uusi repo, nimi `agenttidata`, yksityinen, ilman README-tiedostoa ja ilman `.gitignore`-tiedostoa. Älä anna käyttäjälle `aivo` oikeuksia repoon.

Odotettu:
```
tyhjä yksityinen repo tony/agenttidata, jossa on vain klooniohje
```

### Askel 5. Vientiavain kontissa 115 (kirjoitus)

Yksityinen avain pysyy kontissa ja kuuluu vain palvelun omalle käyttäjälle. Tulosta vain julkinen osa.

```
pct enter 115
useradd --system --no-create-home --home-dir /var/lib/docs-vienti --shell /usr/sbin/nologin docs-vienti
install -d -m 700 -o docs-vienti -g docs-vienti /etc/docs-vienti /var/lib/docs-vienti
sudo -u docs-vienti ssh-keygen -t ed25519 -N '' -C docs-vienti-ct115 -f /etc/docs-vienti/avain
chmod 600 /etc/docs-vienti/avain
cat /etc/docs-vienti/avain.pub
test -r /opt/agenttifirma-docs/claude-jono && sudo -u docs-vienti test -r /opt/agenttifirma-docs/claude-jono && echo lahde-luettavissa
exit
```

Odotettu:
```
yksi rivi, joka alkaa merkeillä ssh-ed25519 ja päättyy sanaan docs-vienti-ct115
sen jälkeen rivi lahde-luettavissa
```

Jos viimeistä riviä ei tule, käyttäjä `docs-vienti` ei voi lukea docs-kopiota. Älä silloin avaa oikeuksia laajalle: kerro, niin sovitaan lukuoikeus vain tälle käyttäjälle.

Liitä rivi Giteassa: tony/agenttidata, Asetukset, Deploy-avaimet, Lisää avain. Nimi `docs-vienti`, ruksi "Salli kirjoitus".

### Askel 6. Lukuavain Aivolle (vain luku)

```
pct enter <AIVO>
install -d -m 700 -o aivo -g aivo /var/lib/aivo/.ssh
sudo -u aivo ssh-keygen -t ed25519 -N '' -C aivo-luku-agenttidata -f /var/lib/aivo/.ssh/agenttidata
cat /var/lib/aivo/.ssh/agenttidata.pub
exit
```

Odotettu:
```
yksi rivi, joka alkaa merkeillä ssh-ed25519 ja päättyy sanaan aivo-luku-agenttidata
```

Liitä rivi samaan repoon Deploy-avaimiin, nimi `aivo-luku`, ilman valintaa "Salli kirjoitus".

### Askel 7. Koodi ja ajastin kontissa 115

Koodin pitää olla `/opt/agentti-pohja`ssa. Kun tämä PR on yhdistetty, päivitä ajurit ohjeen mukaan:

```
pct enter 115
/opt/agentti-pohja/isanta/paivita-ajurit.sh --kuiva
```

Odotettu:
```
kuiva-ajo kertoo, että päivitys onnistuisi; jos ei, kerro ennen jatkoa
```

Ajon jälkeen (ilman `--kuiva`) tarkista, että moduuli löytyy:

```
test -f /opt/agentti-pohja/agentti/docs_vienti.py && echo moduuli-ok
```

Odotettu:
```
moduuli-ok
```

Kirjoita Gitean osoite asetustiedostoon (ei salaisuus; vain osoite). Ilman tätä vienti ei käynnisty. Jos portti on 22, jätä `:<SSH-PORTTI>` pois.

```
printf 'DOCS_VIENTI_OSOITE=ssh://git@<GITEA-IP>:<SSH-PORTTI>/tony/agenttidata.git\nDOCS_VIENTI_ISANTA=<GITEA-IP>\n' > /etc/docs-vienti/asetukset.env
chown docs-vienti:docs-vienti /etc/docs-vienti/asetukset.env
chmod 600 /etc/docs-vienti/asetukset.env
```

Kuiva-ajo (ei committia, ei pushia; lukee kuitenkin etärepon tilan, joten askeleen 5 avain pitää olla lisättynä):

```
cd /opt/agentti-pohja
sudo -u docs-vienti env $(cat /etc/docs-vienti/asetukset.env) python3 -B -m agentti.docs_vienti --kuiva
```

Odotettu:
```
docs-vienti: kuiva-ajo: N tiedostoa muuttuisi (kopioitu N); ei committia
```

Asenna yksiköt ja aja kerran käsin:

```
cp /opt/agentti-pohja/systemd/docs-vienti.service.mall /etc/systemd/system/docs-vienti.service
# korvaa OIKEA-IP Gitean osoitteella (sama kuin DOCS_VIENTI_ISANTA)
sed -i 's/<GITEA-IP>/OIKEA-IP/' /etc/systemd/system/docs-vienti.service
grep -n 'IPAddressAllow\|^User=' /etc/systemd/system/docs-vienti.service
cp /opt/agentti-pohja/systemd/docs-vienti.timer.mall /etc/systemd/system/docs-vienti.timer
systemctl daemon-reload
systemctl start docs-vienti.service
journalctl -u docs-vienti -n 5 --no-pager
```

Odotettu:
```
rivi "docs-vienti: pushattu: N tiedostoa muuttui (kopioitu N)"
```

Kun se onnistui, ota ajastin käyttöön:

```
systemctl enable --now docs-vienti.timer
systemctl list-timers docs-vienti.timer --no-pager
exit
```

Odotettu:
```
ajastin näkyy listassa ja seuraava ajo on :07, :22, :37 tai :52
```

Ajastin ajaa viennin 15 minuutin välein. Yksikössä on `Wants=` ja `After=` docs-pull.service, joten docs-pull käynnistyy ensin ja vienti odottaa sen valmistumista. Jos kontissa ei ole yksikköä `docs-pull.service`, systemd ohittaa `Wants=`-rivin ja vienti käyttää viimeksi haettua kopiota.

### Askel 8. Aivon luku ja tarkistus (kontti `<AIVO>`)

```
pct enter <AIVO>
sudo -u aivo env GIT_SSH_COMMAND='ssh -i /var/lib/aivo/.ssh/agenttidata -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/var/lib/aivo/.ssh/known_hosts_agenttidata' git clone ssh://git@<GITEA-IP>:<SSH-PORTTI>/tony/agenttidata.git /var/lib/aivo/agenttidata
sudo -u aivo git -C /var/lib/aivo/agenttidata log -1 --format=%cd
sudo -u aivo env GIT_SSH_COMMAND='ssh -i /var/lib/aivo/.ssh/agenttidata -o IdentitiesOnly=yes -o UserKnownHostsFile=/var/lib/aivo/.ssh/known_hosts_agenttidata' git -C /var/lib/aivo/agenttidata push origin HEAD:refs/heads/koe
```

Odotettu (kaksi ensimmäistä):
```
kloonaus onnistuu ja log tulostaa viimeisimmän viennin ajan
```

Odotettu (push):
```
push hylätään (read-only tai permission denied), koska Aivon avain on vain luku
```

Jos portti on 22, jätä `:<SSH-PORTTI>` pois. Jos Gitean SSH-osoite on toisenlainen, käytä repon sivulla näkyvää SSH-osoitetta (silloin vienti ei välttämättä hyväksy sitä, ja osoite pitää lisätä koodin sallittujen listaan erillisellä PR:llä).

Päivitys Aivon kontissa (valinnainen, kun kloonaus onnistui):

```
cp /opt/agentti-pohja/systemd/agenttidata-pull.service.mall /etc/systemd/system/agenttidata-pull.service
cp /opt/agentti-pohja/systemd/agenttidata-pull.timer.mall /etc/systemd/system/agenttidata-pull.timer
```

Aivon kontissa ei välttämättä ole `/opt/agentti-pohja`. Tällöin kopioi mallitiedostot käsin kontille. Sitten:

```
systemctl daemon-reload
systemctl enable --now agenttidata-pull.timer
systemctl start agenttidata-pull.service
journalctl -u agenttidata-pull -n 5 --no-pager
```

Odotettu:
```
palvelu päättyy ilman virhettä (Already up to date tai päivitetyt tiedostot)
```

### Askel 9. Tarkistus lähteestä

Tarkista kopiosta, ettei salaisuuden näköistä tai osoitteita jäänyt:

```
pct enter <AIVO>
grep -rIlE 'sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|([0-9]{1,3}\.){3}[0-9]{1,3}|ts\.net' /var/lib/aivo/agenttidata --exclude-dir=.git
exit
```

Odotettu:
```
ei tulosta mitään (ei osumia)
```

Jos tulostuu tiedostonimiä, älä käytä lähdettä Aivolle ennen kuin asia on selvitetty. Kerro tiedostonimi (ei sisältöä). Historiaan jäänyt sisältö poistuu vain kirjoittamalla historia uusiksi, ja se on sinun päätöksesi.

## Palautus

- Ajastin pois: `systemctl disable --now docs-vienti.timer` (kontti 115) ja `agenttidata-pull.timer` (Aivon kontti).
- Avaimen peruutus: poista deploy-avain Gitean repon asetuksista. Se katkaisee yhteyden heti.
- Snapshot: `pct rollback 115 ennen-agenttidata` (sinun päätöksesi; palauttaa koko kontin).

## Testit

```
python3 -m unittest test.test_docs_vienti
```

Testit kattavat sallitut polut, siivouksen, väärän osoitteen estämisen, `origin`-asetuksen ohituksen ja sen, ettei muuttumaton sisältö tee committia. Ne käyttävät paikallista tyhjää repoa eivätkä käytä verkkoa.
