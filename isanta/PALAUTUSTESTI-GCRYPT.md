# Palautustesti: Drive-varmuuskopio (gcrypt), kerran kuussa

Tonyn päätös A837 (7.11.2026): Drive-varmuuskopiosta tehdään automaattinen palautustesti kuukausittain. Testi kohdistuu nykyiseen salattuun kopioon (`gcrypt:`), ei `varmuuskopio-drive.sh`:n kohteeseen.

## Mitä testi tekee
1. Listaa `gcrypt:`-kohteesta kontin (oletus 114, Uptime Kuma) uusimman arkiston.
2. Kopioi sen omaan väliaikaiskansioonsa (`/var/tmp/palautustesti.XXXXXX`).
3. Vertaa kokoa kohteen listaukseen ja tarkistaa eheyden: `.tar.zst` → `zstd -t` ja `tar -t`, `.vma.zst` → `zstd -t` (sekä `vma verify`, jos `vma` on asennettu).
4. Poistaa vain oman väliaikaiskansionsa.
5. Ilmoittaa Uptime Kumaan (push, `up` tai `down`). Telegramiin menee viesti vain virheestä.

Kohteeseen vain luetaan. Skripti ei poista eikä muuta yhtään arkistoa.

## Käyttöönotto (Tony, isännällä, root)
Ota ensin snapshot tai varmista, ettei muuta ole kesken. Skripti ei muuta mitään muuta kuin `/var/tmp`-kansiotaan ja lokiaan.

1. Kopioi tiedostot:
   - `isanta/palautustesti-gcrypt.sh` → `/usr/local/sbin/palautustesti-gcrypt.sh` (`chmod 755`)
   - `systemd/palautustesti-gcrypt.service.mall` → `/etc/systemd/system/palautustesti-gcrypt.service`
   - `systemd/palautustesti-gcrypt.timer.mall` → `/etc/systemd/system/palautustesti-gcrypt.timer`
2. Luo asetustiedosto `/etc/palautustesti-gcrypt.env` (omistaja root, `chmod 600`). Esimerkki (arvot omasi, ei repoon):
   ```
   KUMA_PUSH_URL=<Kuma push -osoite ilman kyselyosaa>
   TELEGRAM_BOT_TOKEN=<tunnus>
   TELEGRAM_CHAT_ID=<tunnus>
   # valinnaiset: REMOTE=gcrypt:  KONTTI=114  MAX_KOKO=2147483648
   ```
   `rclone.conf` luetaan isännän omasta tiedostosta (`RCLONE_CONFIG` tai `~/.config/rclone/rclone.conf`, oikeudet 600).
3. Tarkista valinta ilman latausta: `/usr/local/sbin/palautustesti-gcrypt.sh --luettelo`
4. Ota ajastin käyttöön: `systemctl daemon-reload && systemctl enable --now palautustesti-gcrypt.timer`
5. Ensimmäinen oikea ajo käsin: `systemctl start palautustesti-gcrypt.service` ja `journalctl -u palautustesti-gcrypt.service -n 20`.

Luo Kumaan oma push-monitori, jonka heartbeat-väli on noin 35 päivää, jotta ajon puuttuminen hälyttää.

## Rajat
- Vaatii `zstd`-, `tar`-, `flock`- ja `rclone`-ohjelmat. Puuttuva työkalu on virhe, ei hiljainen ohitus.
- Kohteen on oltava `crypt`-tyyppinen.
- Yli `MAX_KOKO`:n (oletus 2 GiB) arkistoa ei ladata.
- Testi todentaa arkiston eheyden, ei sitä, että kontti käynnistyy. Täysi palautus väliaikaiseen konttiin on edelleen käsin tehtävä testi.
