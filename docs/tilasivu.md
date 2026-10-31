# Tilasivu

Yksi sivu, josta nakee puhelimella onko kaikki kunnossa. `python3 -m agentti.tilasivu [--ei-telegram] [--ei-pr] [kohde]` kirjoittaa kohteeseen (oletus `/var/lib/tilasivu`):

- `index.html` (staattinen, ei ulkoisia resursseja; pieni skripti punaistaa bannerin, jos sivu on yli 45 min vanha eli generaattori seisoo)
- `tila.md` (sama sisalto tekstina, esim. GitHub-sovellukselle)
- `tila.json` (kone luettava)
- `tilasivu-tila.json` (generaattorin oma muisti: viimeisin onnistunut ajo yksikoittain, PR-valimuisti, jo ilmoitetut punaiset)

## Osiot ja varit
- Palvelut: `systemctl show` per yksikko. Punainen: failed / ajastin ei kaytossa / viimeisin onnistunut ajo yli 2 x raja. Keltainen: yli 1 x raja, ajo kestaa liian kauan, tilaa ei saatu. Harmaa: yksikko ei asennettu (ei vaikuta kokonaistilaan). Rajat ovat `PALVELUT`-taulukossa (`tilasivu.py`). Kauppatiimin yksikkoa ei talla hetkella ole: rivi on harmaa kunnes `kauppatiimi.service` asennetaan (muuta nimi taulukkoon tarvittaessa).
- Jonot: claude-jono: tekemattomat tehtavat ja viimeisimman tuloksen ika (keltainen yli 2 h, punainen yli 4 h kun tehtavia odottaa). yojono: tanaan tehdyt, epaonnistuneet ja koskaan ajamattomat.
- Kulut: yksi lahde, `tila.json` (`kulut`). Katot luetaan `jono.json`:sta vain naytettavaksi (generaattori ei muuta niita). Keltainen 80 %:sta, punainen kun katto tayttyy. Huomautus, jos tehtavien kulujen summa eroaa kirjatusta paivakulusta.
- PR:t: aamupaketin logiikka (`aamupaketti.keraa`), Tonyn mergattavat. Haetaan enintaan kerran tunnissa (GitHub-API ilman tunnuksia: 60 kutsua/h).
- Tonyn tehtavat: `claude-jono/tonylle.md`:n kahden viimeisen osion rivit, jotka alkavat sanoilla PAATETTAVA, Mergepaatos, Odottaa, Katselmoitavana, AAMULLA tai Tonyn (heuristiikka, tiedosto ei ole rakenteinen); RATKAISTU/PAATETTY-rivit jatetaan pois.

## Telegram
Punainen (palvelu, claude-jono-jono tai kulukatto) lahettaa yhden viestin (`agentti.ilmoita`, tila `virhe`) vain kun punaisten asioiden joukkoon tulee uusi asia. Saman vian toisto ei lahetä uudelleen. Toipumisesta ei viestia. Epaonnistunut lahetys kirjataan lahettamattomaksi ja yritetaan seuraavalla ajolla. Keltainen ei laheta mitaan.

## Kayttoonotto (Tony, kerran)
1. `install -d -m 755 /var/lib/tilasivu`
2. Luo yksikot alla olevista malleista tiedostoihin `/etc/systemd/system/tilasivu.service` ja `tilasivu.timer`; `systemctl daemon-reload`
3. Kuivakoe: `python3 -m agentti.tilasivu --ei-telegram /tmp/tilasivu && cat /tmp/tilasivu/tila.md`
4. `systemctl enable --now tilasivu.timer`

Peruutus: `systemctl disable --now tilasivu.timer`.

## Nakyminen puhelimella (ehdotus, ei toteutettu)
Ei julkista internetia. Sivulla ei ole salaisuuksia (siivous `tilannekuva.SALAISUUDET`), mutta PR-otsikot, tehtavien nimet ja kulut ovat yksityista.
1. Suositus: `tila.md` yksityiseen GitHub-repoon (esim. `agenttifirma-tila`, oma deploy-avain) ja luku GitHub-sovelluksella. Toimii missa tahansa, ei uutta verkkopalvelua. Kirjoitus vain kun tila tai punaiset muuttuvat tai tunnin valein, jottei commit-historia tayty (ei toteutettu).
2. Tailscale: CT115:ssa ei nyt ole Tailscalea (vain LAN 192.168.1.120). Jos Tony asentaa sen: `python3 -m http.server 8810 --directory /var/lib/tilasivu --bind <tailscale-ip>` omana palveluna. Verkko- ja Tailscale-muutokset ovat Tonyn paatos.
3. Vain LAN: sama kuin 2, mutta sidottu 192.168.1.120:een; toimii vain kotiverkossa.
Generaattorin oma kuolema nakyy vain vanhenevana sivuna (banneri punaistuu); Telegramia se ei laheta. Taydentava vahti: Uptime Kuma push-monitori (CT114), jota ajastettu ajo pingaa.

## Yksikkomallit (kopioi kasin; systemd/-polku on pr_kutsun suojaama)

`/etc/systemd/system/tilasivu.service`:
```
# Mallitiedosto: ei asenna mitaan. Asennus docs/tilasivu.md:n mukaan (Tonyn paatos).
[Unit]
Description=Tilasivu: yksi sivu kaikesta (index.html, tila.md, tila.json)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/agentti-pohja
Environment=TILASIVU_KOHDE=/var/lib/tilasivu
Environment=YOJONO_PERUS=/var/lib/yojono
Environment=CLAUDEJONO_REPO=/var/lib/claude-jono/docs
# Telegram-tunnukset luetaan agentti.ilmoita-moduulissa tiedostosta /etc/yojono/telegram.env (ei ymparistosta)
ExecStart=/usr/bin/python3 -B -m agentti.tilasivu
TimeoutStartSec=300
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
# /var/lib/yojono: ilmoita.py kirjaa lahetysrajan sinne; /var/lib/tilasivu: sivu ja edellinen tila
ReadWritePaths=/var/lib/tilasivu /var/lib/yojono
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
LockPersonality=true
```

`/etc/systemd/system/tilasivu.timer`:
```
# Mallitiedosto: ei asenna mitaan.
[Unit]
Description=Tilasivu 15 min valein

[Timer]
OnBootSec=2min
OnUnitInactiveSec=15min
Persistent=true
Unit=tilasivu.service

[Install]
WantedBy=timers.target
```
