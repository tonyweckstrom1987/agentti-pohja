# Infran kartoitus (31.10.2026, CT115:n nakymasta)

Lahteet: CT115:n `systemctl`, `getent`, `stat`, `ls` (vain luku), repot ja docs-repon `01-infra.md` (15.10.). Salaisuustiedostojen sisaltoa ei luettu, vain nimet ja oikeudet.
Merkinnat: **[nahty]** = tarkistettu CT115:ssa tana paivana, **[docs]** = vain docs-repon mukaan (voi olla vanhentunut), **[ei nae]** = ei nakyvissa taalta.

## 1. Kontit ja isanta
| Mika | Tila |
|---|---|
| CT110 open-webui .115, CT111 litellm .116:4000, CT112 postgres .117, CT113 gitea .118, CT114 uptime-kuma .119, CT116 holvi .121, CT117 posti-agentti .122, CT118 infra-automaatio .123 | [docs] 01-infra.md; CT115 ei nae naita. Resurssit ja onboot-asetukset ovat siella |
| CT115 agentscope .120 (2c/2G/16G) | [nahty] tama kone, Debian 13 (trixie) |
| Proxmox-isanta: kontit, vzdump 02:30, rclone 04:00, lampovalvonta (`/usr/local/bin/lampo-valvonta.sh`, `/etc/cron.d/lampo`), nftables, Tailscale, thin pool | [ei nae] vain [docs] |
| Suunniteltu 210-sandbox | [docs] ei rakennettu |

## 2. CT115: systemd-ajastimet ja palvelut [nahty] (`/etc/systemd/system`)
| Yksikko | Mita | Ajastus |
|---|---|---|
| claude-jono.service + .timer | pilvi-Clauden tehtavajono (tama rooli) | 5 min kaynnistyksesta, sitten 10 min edellisen ajon paattymisesta |
| docs-pull.service + .timer | `git pull --ff-only` kohteeseen `/opt/agenttifirma-docs` | 30 min |
| github-api.service | GitHub-apulainen, uvicorn 0.0.0.0:8800, `/opt/agentscope/bin/python3` | jatkuva |
| portti.service | Portti (User=portti), 192.168.1.120:8810 | jatkuva |
| yojono.service + yojono.timer, yojono-a.timer, yojono-c.timer | yojono-ajuri (`/opt/yojono-ajuri`) | 21:30, 01:00, 05:00 (Europe/Helsinki); drop-init `yojono.service.d/{loki,peilaus,virheilmoitus}.conf` |
| ideapankki.service + .timer | User=ideapankki, `/opt/agentit/ideapankki` | su 17:30 |
| paivakatsaus.service + .timer, paivakatsaus-komennot.*, loki-keraaja-paivakatsaus.* | User=paivakatsaus | ti-pe 07:30, ma 08:30; 2 min; 5 min |
| halytin.service + .timer | `/opt/agentscope/halytin.py` | 08:15 |
| trendit-paiva.*, trendit-viikko.* (+ `.service.d`) | `/opt/agentscope/trendit` | 04:30; su 18:00 |
| viikkoajo.*, viikkoraportti.* | viikkoajo.py; yojono-viikkoraportti | ma 08:00 (palvelimen aikavyohyke); su 19:00 |
| virheilmoitus@.service, virheilmoitus-yojono-tila.service | Telegram-ilmoitus epaonnistumisista (OnFailure) | tapahtumasta |
| postfix.service | paikallinen posti, kuuntelee vain 127.0.0.1:25 | jatkuva |
| `*.esimerkki`-tiedostot (ideapankki) | vanhat mallit | ei kaytossa |

Muuta: ajastimet on otettu kayttoon symlinkeilla `/etc/systemd/system/timers.target.wants/`. Tila: `systemctl is-system-running` = degraded, koska `trendit-paiva.service` on `failed` (31.10.). Cron: CT115:ssa vain `e2scrub_all` (ei omia cron-tehtavia).

## 3. Kayttajat ja ryhmat [nahty]
| Tili | uid | Koti / kuori | Huomio |
|---|---|---|---|
| claudejono | 996 | /var/lib/claudejono, bash | ryhmat: claudejono, systemd-journal; ainoa sudo: `(prbotti) NOPASSWD: /usr/local/sbin/claudejono-pr` (`sudo -l`) |
| prbotti | 995 | /nonexistent, nologin | lukee botin GitHub-tunnuksen (`/etc/claudejono-pr/`) |
| ideapankki | 999 | nologin | |
| portti | 997 | /home/portti, nologin | |
| paivakatsaus | 102 | nologin | ryhma ehdotusposti (988) |
| root | 0 | | claude-jono.service ajaa ajurin rootina (ei `User=`), agentti ajetaan `runuser`illa tilina claudejono |

## 4. Hakemistot ja oikeudet [nahty]
| Polku | Omistaja, oikeudet | Sisalto |
|---|---|---|
| /opt/agentscope | root 755 | agenttien ja palvelujen koodi (github_api.py, prompt.txt, viikkoajo.py, halytin.py, trendit/, paivakatsaus/ ...) |
| /opt/agentscope/tyo | claudejono 755 | claude-jonon tyoklooneja |
| /opt/agentti-pohja, /opt/yojono-ajuri, /opt/agenttifirma-docs, /opt/yojono, /opt/agentit/{ideapankki,portti} | root 755 | ajettava koodi; `yojono-ajuri.prev-*` 3 kpl vanhoja versioita |
| /var/lib/claude-jono (docs-klooni, lukko) | root 755 | |
| /var/lib/claudejono | claudejono 700 | Clauden kirjautuminen, `.claude`, `.local/bin/claude` |
| /var/lib/claudejono-pr, /etc/claudejono-pr | prbotti 700 | PR-tila ja botin tunnus (sisaltoa ei luettu) |
| /etc/claude-jono | root 700 | deploy key |
| /etc/yojono | root 755; tiedostot 600 | env, telegram.env, github-bot.token, peilaus_avain(.pub) |
| /etc/agentti | root | ideapankki.env, portti.env (+ `portti.env.save`), ryhmalle luettavia 640 |
| /etc/paivakatsaus.env | root 600 | |
| /var/lib/yojono, /var/lib/agentti | root | tilatiedostot, tapahtumaloki |
| /usr/local/sbin/claudejono-pr | root 755 | PR-wrapper; **ei ollut missaan repossa ennen taita PR:aa** |
| /etc/sudoers.d/claudejono | root 440 | sisaltoa ei voi lukea claudejonona |

## 5. Verkko [nahty]
Kuuntelee: 22 (ssh), 8800 (github-api, kaikki rajapinnat), 192.168.1.120:8810 (portti), 127.0.0.1:25 ja ::1:25 (postfix). Palomuuri (nftables) ja Tailscale on isannalla/muissa konteissa: [ei nae].

## 6. Asennetut paketit [nahty]
Manuaalisesti asennetut: perusjarjestelma + git, curl, sudo, python3 (3.13), nftables, openssh, postfix, cron, build-essential, tmux, vim-tiny. Node.js 22 kasin `/opt/node` (symlinkit `/usr/local/bin`). Clauden binaari kayttajan kotihakemistossa (`~claudejono/.local`), ei pakettina. Python-venv: `/opt/agentscope` itse (pyvenv.cfg).

## 7. Havainnot (korjattavaa, ei korjattu)
1. **Mallitiedostot elavat repossa pitkalle, mutta asennettu ero**: repon `claudejono/systemd/claude-jono.service.mall` ajaa rootina (`/root/.local/bin/claude`, `NoNewPrivileges=true`, 30 min), asennettu yksikko kayttaa `/var/lib/claudejono/.local/bin/claude`, ilman NoNewPrivileges ja 10 min. Tama rooli kopioi asennetun version totuudeksi; mallit kannattaa paivittaa tai poistaa.
2. Yksikon kommentti viittaa tiedostoon `asenna-kayttaja.sh`, jota ei ole repossa. Tama rooli korvaa sen (tilit, sudoers, hakemistot).
3. `/etc/agentti/portti.env.save` on salaisuustiedoston kopio (ryhmalukuoikeus portti). Harkitse poistoa kasin; Ansible ei poista mitaan.
4. `trendit-paiva.service` on failed -> koko jarjestelma `degraded`.
5. `viikkoajo.timer` ei nimea aikavyohyketta (muut nimeavat Europe/Helsinki): ajo seuraa palvelimen asetusta (nyt EET). CLAUDE.md:n "UTC"-maininta on vanhentunut tai eri.

## 8. Mita en nae: Tonyn ajettavat lukukomennot
Kaikki ovat vain lukua. Liita tulosteet tarvittaessa, mutta ALA liita salaisuuksien sisaltoa.

**Isanta (Proxmox):**
```
pct list                                     # kontit ja tilat
for c in 110 111 112 113 114 115 116 117 118; do echo "== $c"; pct config $c | grep -vi -E 'password|key'; done
pvesm status; lvs                            # tallennus ja thin pool
cat /etc/pve/jobs.cfg /etc/cron.d/lampo      # vzdump-ajo ja lampovalvonta
nft list ruleset                             # palomuuri
tailscale status
```
**CT115 (rootina; sisaltoa ei tarvitse liittaa):**
```
cat /etc/sudoers.d/claudejono                # vertaa roolin sudoers-tiedostoon
stat -c '%U:%G %a %n' /etc/claudejono-pr/* /etc/claude-jono/*
git -C /var/lib/claude-jono/docs remote -v; git -C /var/lib/claude-jono/docs config --get user.name
```
**Muut kontit (jokaisessa `pct enter <CT>`):**
```
systemctl list-units --type=service --state=running --no-pager
systemctl list-timers --all --no-pager
getent passwd | awk -F: '$3>=900 || $3<100'
ls -la /etc/systemd/system | head -50
ss -tlnp
```
Naista syntyvat roolit: ks. README.md "Seuraavaksi".
