# GitHubin lukutunnus isännällä

Lukutunnus nostaa julkisten repojen hakujen rajan 60 hausta tunnissa 5000 hakuun tunnissa.
Tunnus on vain luku -oikeuksilla. Sillä ei voi kirjoittaa, avata PR:ää eikä yhdistää mitään.

Koodi lukee tunnuksen tiedostosta `/etc/agentscope/github-luku.token`. Polun voi vaihtaa
ympäristömuuttujalla `GITHUB_LUKU_TUNNUS_POLKU`. Jos tiedostoa ei ole, haut toimivat kuten ennen
(ilman tunnusta). Lukijat: aamupaketti, aamukooste, tilannekuva, PR-opit ja laatuotoksen PR-luvut.

## 1. Luo tunnus selaimessa

Osoite: https://github.com/settings/personal-access-tokens/new

- Token name: `agentscope-luku`
- Expiration: 1 year
- Repository access: Public repositories (read-only)
- Permissions: ei lisäoikeuksia

Kopioi tunnus leikepöydälle. Älä liitä sitä chattiin, tiedostoon tai komentoriville.

## 2. Luo hakemisto (isännällä, rootina)

    install -d -m 755 /etc/agentscope

Odotettu tuloste: ei mitään.

## 3. Tallenna tunnus ilman komentohistoriaa

    read -rs -p "Tunnus: " T; echo; (umask 077; printf '%s' "$T" > /etc/agentscope/github-luku.token); unset T

Liitä tunnus kehotteeseen ja paina Enter. Tunnus ei näy ruudulla eikä mene historiaan,
koska se ei ole komentorivin osa. Odotettu tuloste: tyhjä rivi.

## 4. Aseta omistaja ja oikeudet

    chown root:claudejono /etc/agentscope/github-luku.token && chmod 640 /etc/agentscope/github-luku.token

Jos vain yksi palvelukäyttäjä lukee tunnusta, voit käyttää `chown <käyttäjä> ...` ja `chmod 600`.
Odotettu tuloste: ei mitään.

## 5. Tarkista oikeudet (tunnusta ei tulosteta)

    stat -c '%a %U:%G %s' /etc/agentscope/github-luku.token

Odotettu tuloste: `640 root:claudejono` ja tiedoston koko noin 93 tavua (fine-grained-tunnus).

## 6. Todenna

    cd /opt/agentti-pohja && python3 -m agentti.github_luku

Odotettu tuloste:

    tunnus: luettu
    rate_limit: limit 5000, jaljella 49xx

Jos rivillä lukee `tunnus: ei kaytossa` ja `limit 60`, tiedostoa ei voitu lukea
(oikeudet, polku tai muoto). Aja komento palvelukäyttäjänä: `sudo -u claudejono python3 -m agentti.github_luku`.
Rajapinnan `/rate_limit`-haku ei kuluta kiintiötä.

## Vaihto ja peruutus

Vaihda tunnus ajamalla vaiheet 3 ja 4 uudelleen. Peruuta tunnus GitHubin asetuksissa
(Settings > Developer settings > Fine-grained tokens). Tunnus vanhenee vuoden kuluttua.
