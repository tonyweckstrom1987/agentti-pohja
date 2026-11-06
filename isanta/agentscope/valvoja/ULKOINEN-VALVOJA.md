# Ulkoinen valvoja (CT115)

Tarkoitus: jos koko palvelin sammuu (esim. sähkökatko), Aivo ja Uptime Kuma eivät voi kertoa siitä, koska ne ovat samalla koneella. CT115 lähettää siksi viiden minuutin välein tyhjän HTTP-pingin ulkopuoliseen palveluun. Kun ping loppuu, palvelu lähettää sähköpostin.

## 1. Palveluiden vertailu (haettu 5.11.2026)

Luvut ovat kolmannen osapuolen vertailusivuilta. Tarkista ne palvelun omalta hintasivulta ennen valintaa.

| Palvelu | Ilmainen kiintiö | Hälytyskanavat (ilmainen) | Huomio |
|---|---|---|---|
| Healthchecks.io (Hobbyist) | 20 tarkistusta, 100 lokimerkintää/tarkistus | sähköposti, Telegram, webhook, Slack (Telegram vahvistettu vain sivustovertailuista) | Avoin lähdekoodi, voi ajaa itse. Suositus: tehtävään sopii parhaiten. |
| Cronitor (Hacker) | 5 monitoria | sähköposti, Slack | Lähteet ristiriitaisia ominaisuuksista. |
| Better Stack | 10 heartbeatia (useimmat lähteet) | sähköposti ym. | Lähteet ristiriitaisia, luottokortti mahdollisesti vaaditaan. |

Tietosuoja: ping on pelkkä HTTP-pyyntö ilman sisältöä. Palvelu näkee vain osoitteen, ajan ja lähettäjän IP-osoitteen. Pingin mukana ei lähde palvelimen dataa.

Lähteet:
- https://healthchecks.io/pricing
- https://cronitor.io/pricing
- https://betterstack.com/community/comparisons/better-stack-vs-healthchecks-io/
- https://freetier.co/directory/products/healthchecksio

Suositus: Healthchecks.io, koska ilmainen kiintiö riittää (tarvitaan 1 tarkistus) ja Telegram-hälytys on mahdollinen.

## 2. Tunnuksen ja tarkistuksen luonti (Tony tekee itse)

1. Luo tunnus valittuun palveluun.
2. Luo tarkistus: nimi `CT115 ulkoinen valvoja`, jakso 5 minuuttia, toleranssiaika (grace) 10 minuuttia.
3. Lisää hälytyskanavaksi sähköposti (ja halutessasi Telegram).
4. Kopioi tarkistuksen ping-osoite. Älä liitä sitä keskusteluun, repoon tai lokeihin.

## 3. Osoitteen asetus CT115:llä (root)

```
install -d -m 700 /etc/claude-jono
umask 077
read -r -s -p "Ping-osoite: " U; echo "$U" > /etc/claude-jono/healthcheck.url; unset U
chmod 600 /etc/claude-jono/healthcheck.url
```

## 4. Yksiköiden asennus (root, ottaa ensin snapshotin hostilta: `pct snapshot 115 ennen-valvoja`)

```
cp ulkoinen-valvoja.service ulkoinen-valvoja.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ulkoinen-valvoja.timer
systemctl list-timers ulkoinen-valvoja.timer
```

Ilman tiedostoa `/etc/claude-jono/healthcheck.url` palvelu ohitetaan (ConditionPathExists), joten asennus on turvallinen myös ennen osoitteen asetusta.

## 5. Testaus

1. Ajo käsin: `systemctl start ulkoinen-valvoja.service; systemctl status ulkoinen-valvoja.service`. Odotus: `status=0/SUCCESS`, ja palvelun sivulla tarkistus muuttuu vihreäksi.
2. Hälytystesti: `systemctl stop ulkoinen-valvoja.timer`. Odota jakso + toleranssi (noin 15 min). Sähköposti saapuu.
3. Palautus: `systemctl start ulkoinen-valvoja.timer`. Palvelu lähettää toipumisviestin.
4. Kun testi on tehty, vaihda rivi tiedostossa todennettavat.md tilaan "todennettu".

## 6. Rajat

- Osoite ei ole repossa. Virhetilanteessa curl tulostaa vain HTTP-tilan, ei osoitetta.
- Ajastin ei muuta palomuureja, holvia eikä Tailscalea. Se tekee vain lähtevän HTTPS-pyynnön.
