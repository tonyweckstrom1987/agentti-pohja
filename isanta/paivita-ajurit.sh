#!/usr/bin/env bash
# =============================================================================
# paivita-ajurit.sh - paivittaa CT115:n ajurit GitHubin mainista yhdella komennolla (aja rootina CT115:ssa).
#
# Kohteet (molemmat samaan commitiin):
#   /opt/agentti-pohja  git-klooni: Claude-jonon kaistat, tarkastaja, aamukooste, agentti-moduulit
#   /opt/yojono-ajuri   manifestikopio (ei .git): yojono-ajuri; sama muoto kuin isanta/julkaise.sh tekee
#
# KAYTTO
#   paivita-ajurit.sh [--kuiva]      hakee mainin, ajaa testit, mutta ei vaihda mitaan (--kuiva)
#   paivita-ajurit.sh                varmuuskopio, testit, vaihto, savutesti, tarvittaessa automaattinen paluu
#   paivita-ajurit.sh --peruuta      palauttaa viimeisimman varmuuskopion (.prev-*) molempiin kohteisiin
#   Valinnaiset: --odota SEK (jonon odotusaika, oletus 1800), --salli-likainen (ohittaa paikallisten muutosten esto)
#
# Periaate: koodi vaihdetaan sisarhakemisto + mv -menetelmalla (polut ennallaan). Tila ja konfiguraatio
# (/var/lib/*, /etc/*) eivat ole kohteissa eika muutu. Ei salaisuuksia: skripti ei lue .env-tiedostoja eika tulosta
# ymparistomuuttujia. Poistumiskoodi: 0 = OK tai ajan tasalla, 1 = epaonnistui (tilanne ennallaan tai palautettu),
# 2 = esitarkistus esti (mitaan ei muutettu), 3 = palautus jai kesken tai epaonnistui (osa kohteista ei ole palautunut:
# loppurivi kertoo tarkan tilan kohde kohteelta, tarkista kasin).
# Uuden koodin testit JA savutesti (import, `yojono tila`) ajetaan EI rootina: testit valiaikaiskopiossa, savutesti vaihdetussa
# kohteessa, molemmat palvelukayttajana (runuser, env -i).
# Loppurivi on aina "OK: ..." tai "EPAONNISTUI: ...". Loki: /var/log/paivita-ajurit.log
# =============================================================================
set -u -o pipefail
export LC_ALL=C.UTF-8

LAHDE=${PAIVITA_LAHDE:-https://github.com/tonyweckstrom1987/agentti-pohja}
HAARA=${PAIVITA_HAARA:-main}
POHJA=${PAIVITA_POHJA:-/opt/agentti-pohja}
YOJONO=${PAIVITA_YOJONO:-/opt/yojono-ajuri}
LOKI=${PAIVITA_LOKI:-/var/log/paivita-ajurit.log}
LUKKO=${PAIVITA_LUKKO:-/run/lock/paivita-ajurit.lock}
SYSTEMCTL=${PAIVITA_SYSTEMCTL:-systemctl}
PYTHON=${PAIVITA_PYTHON:-python3}
ODOTA_S=${PAIVITA_ODOTA_S:-1800}
SEURANTA_S=${PAIVITA_SEURANTA_S:-5}
VAPAA_MB=${PAIVITA_VAPAA_MB:-300}
SALLI_EI_ROOT=${PAIVITA_SALLI_EI_ROOT:-0}      # vain testeja varten
# Kayttaja, jona uuden mainin testit ajetaan (rootina ei koskaan). Oletus rootina ajettaessa: claudejono.
TESTIKAYTTAJA=${PAIVITA_TESTIKAYTTAJA:-}
[ -n "$TESTIKAYTTAJA" ] || { [ "$(id -u)" = 0 ] && TESTIKAYTTAJA=claudejono; }
ASETUKSET=${PAIVITA_ASETUKSET:-/etc/claude-jono/asetukset.env}   # systemd EnvironmentFile; luetaan vain avain CLAUDEJONO_KOEPAIVA
POIS_YOJONO=".github"                          # sama kuin julkaisu/yojono-ajuri.conf POIS
# Testit ajetaan puretussa uudessa kopiossa ennen vaihtoa. Koko sarja kaatuu CT115:ssa ymparistoeroihin (root, node
# puuttuu), joten oletuksena ajetaan sama moduulijoukko kuin isanta/julkaisu/yojono-ajuri.conf.
TESTI_POHJA=${PAIVITA_TESTI_POHJA:-test.test_pr_kutsu yojono.test_ajuri test.test_viikkoraportti claudejono.test_kaistat claudejono.test_ajuri claudejono.test_tarkastaja claudejono.test_aamukooste tools.test_kayttoonotto_ero}
TESTI_YOJONO=${PAIVITA_TESTI_YOJONO:-test.test_pr_kutsu yojono.test_ajuri test.test_viikkoraportti}
SAVU_POHJA=${PAIVITA_SAVU_POHJA:-claudejono.ajuri claudejono.tarkastaja claudejono.aamukooste agentti.pr_kutsu}
SAVU_YOJONO=${PAIVITA_SAVU_YOJONO:-yojono.ajuri agentti.pr_kutsu agentti.viikkoraportti}
TERVEYS_YOJONO=${PAIVITA_TERVEYS_YOJONO:-yojono tila}   # python3 -B -m <tama>; vain luku, tyhja = ei tarkistusta

KUIVA=0; PERUUTA=0; SALLI_LIKAINEN=0
TS=${PAIVITA_TS:-$(date +%Y%m%d-%H%M%S)}
T=""                                   # valiaikaishakemisto (testilokit, manifesti)
TESTIHAK=""                            # testikopiot (omistaja: TESTIKAYTTAJA); erillaan $T:sta, jotta kayttaja paasee sisaan
PALAUTETTU=(); EI_PALAUTETTU=()        # palautuksen tila kohde kohteelta (paluu_vanhaan, peruuta)
KLOONI=""                              # puhdas klooni uuteen committiin
UUSI_COMMIT=""
AJASTIMET_PYSAYTETTY=()
KOHTEET=()                             # paivitettavat: pohja yojono
declare -A UUSI PREV VAIHE             # VAIHE: "" | siirretty | vaihdettu
LOPETETTU=0

# ---- aputoiminnot -----------------------------------------------------------------
info()  { local m; m="[$(date +%H:%M:%S)] $*"; echo "$m"; echo "$m" >> "$LOKI" 2>/dev/null || true; }
rivi_loppu() { echo "$1"; echo "[$(date +%H:%M:%S)] $1" >> "$LOKI" 2>/dev/null || true; }
koodi_kansio() { case "$1" in pohja) echo "$POHJA";; yojono) echo "$YOJONO";; esac; }
nimi() { case "$1" in pohja) echo agentti-pohja;; yojono) echo yojono-ajuri;; esac; }
lyhyt() { echo "${1:0:7}"; }

kasittele_ajastimet() {                 # $1 = stop | start
  local u
  for u in "${AJASTIMET_PYSAYTETTY[@]:-}"; do
    [ -n "$u" ] || continue
    "$SYSTEMCTL" "$1" "$u" >/dev/null 2>&1 || info "VAROITUS: $SYSTEMCTL $1 $u epaonnistui"
  done
}
palauta_ajastimet() {
  if [ "${#AJASTIMET_PYSAYTETTY[@]}" -gt 0 ] && [ -n "${AJASTIMET_PYSAYTETTY[0]:-}" ]; then
    kasittele_ajastimet start; info "ajastimet palautettu (${#AJASTIMET_PYSAYTETTY[@]} kpl)"; AJASTIMET_PYSAYTETTY=()
  fi
}
aktiiviset_ajastimet() { "$SYSTEMCTL" list-units --type=timer --state=active --no-legend --plain --no-pager 'claude-*' 'yojono*' 2>/dev/null | awk 'NF{print $1}'; }
kaynnissa_palvelut()  { "$SYSTEMCTL" list-units --type=service --state=activating,active,deactivating,reloading --no-legend --plain --no-pager 'claude-*' 'yojono*' 2>/dev/null | awk 'NF{print $1}'; }

siivoa() {                              # staging-kansiot ja valiaikaiset; ei kosketa vanhoihin eika prev-kansioihin
  local k
  for k in pohja yojono; do
    [ -n "${UUSI[$k]:-}" ] && [ -d "${UUSI[$k]}" ] && [ "${VAIHE[$k]:-}" != vaihdettu ] && rm -rf -- "${UUSI[$k]}"
  done
  [ -n "$KLOONI" ] && [ -d "$KLOONI" ] && rm -rf -- "$KLOONI"
  [ -n "$TESTIHAK" ] && [ -d "$TESTIHAK" ] && rm -rf -- "$TESTIHAK"
  [ -n "$T" ] && [ -d "$T" ] && rm -rf -- "$T"
  return 0
}

paluu_vanhaan() {                       # palauttaa jokaisen jo siirretyn kohteen vanhan koodin; tila kirjataan kohde kohteelta
  local k d kok rc=0
  PALAUTETTU=(); EI_PALAUTETTU=()
  for k in "${KOHTEET[@]}"; do
    d=$(koodi_kansio "$k"); kok=0                 # kok nollataan joka kohteelle: yhden kohteen virhe ei leimaa toista
    case "${VAIHE[$k]:-}" in
      vaihdettu)
        if mv -T -- "$d" "$d.viallinen-$TS"; then
          mv -T -- "${PREV[$k]}" "$d" || { kok=1; EI_PALAUTETTU+=("$(nimi "$k") (kohde puuttuu; vanha koodi: ${PREV[$k]}, viallinen uusi: $d.viallinen-$TS)"); }
        else
          kok=1; EI_PALAUTETTU+=("$(nimi "$k") (uutta ei saatu pois paikaltaan; vanha koodi: ${PREV[$k]})")
        fi ;;
      siirretty)                                  # vanha oli jo siirretty pois, uutta ei ehditty asettaa
        mv -T -- "${PREV[$k]}" "$d" || { kok=1; EI_PALAUTETTU+=("$(nimi "$k") (kohde puuttuu; vanha koodi: ${PREV[$k]})"); } ;;
      *) continue ;;
    esac
    if [ "$kok" = 0 ]; then
      VAIHE[$k]=""; PALAUTETTU+=("$(nimi "$k")")
      info "palautettu: $(nimi "$k") <- ${PREV[$k]##*/}"
    else
      rc=1; info "VIRHE: palautus epaonnistui: ${EI_PALAUTETTU[-1]}"
    fi
  done
  return "$rc"
}
palautuksen_tila() {                    # yksi rivi: mika palautui ja mika ei
  local p="${PALAUTETTU[*]:-}" e="" x
  for x in "${EI_PALAUTETTU[@]:-}"; do [ -n "$x" ] && e+="${e:+; }$x"; done
  echo "palautettu: ${p:-ei mitaan}; EI palautettu: ${e:-ei mitaan}"
}

# Koepaivan asetus (A905): tulostaa vain avaimen CLAUDEJONO_KOEPAIVA arvon, ei muita rivejä eikä kirjoita mitaan.
# Viimeinen asetus voittaa (kuten systemd). Arvo tulostetaan vain, jos se on ISO-paivamaara; muuten ei lainkaan.
koepaiva_rivi() {
  local rivi arvo="" luettu=0
  if [ -r "$ASETUKSET" ]; then
    while IFS= read -r rivi || [ -n "$rivi" ]; do
      rivi=${rivi#"${rivi%%[![:space:]]*}"}; rivi=${rivi#export }
      case "$rivi" in CLAUDEJONO_KOEPAIVA=*) arvo=${rivi#CLAUDEJONO_KOEPAIVA=}; luettu=1 ;; esac
    done < "$ASETUKSET"
  else
    echo "koepaiva: EI LUETTAVISSA"; return 0
  fi
  arvo=${arvo%"${arvo##*[![:space:]]}"}; arvo=${arvo#[\"\']}; arvo=${arvo%[\"\']}
  if [ "$luettu" = 0 ] || [ -z "$arvo" ]; then echo "koepaiva: PUUTTUU"
  elif [[ "$arvo" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then echo "koepaiva: $arvo"
  else echo "koepaiva: VIRHEELLINEN (ei ISO-paivamaara)"; fi
}

lopeta() {                              # $1 = koodi, $2 = loppurivi (ilman etuliitetta)
  [ "$LOPETETTU" = 1 ] && return; LOPETETTU=1
  siivoa; palauta_ajastimet
  echo; koepaiva_rivi
  if [ "$1" = 0 ]; then rivi_loppu "OK: $2"; else rivi_loppu "$(printf 'EP\xc3\x84ONNISTUI: %s' "$2")"; fi
  exit "$1"
}
vika_esi() { lopeta 2 "$*"; }           # esitarkistus esti, mitaan ei muutettu
vika_vaihdossa() {                      # virhe vaihdon aikana tai jalkeen: palautus vanhaan
  local viesti=$*
  info "VIRHE: $viesti"
  if paluu_vanhaan; then
    if savutesti_kaikki palautus; then lopeta 1 "$viesti; vanha koodi palautettu ja savutesti OK"
    else lopeta 1 "$viesti; vanha koodi palautettu, mutta savutesti EI mennyt lapi: tarkista kasin"; fi
  else lopeta 3 "$viesti; PALAUTUS EPAONNISTUI ($(palautuksen_tila)); tarkista kasin"; fi
}
keskeytys() { info "keskeytys vastaanotettu"; if [ "$vaihto_alkanut" = 1 ]; then vika_vaihdossa "keskeytetty"; else lopeta 1 "keskeytetty ennen vaihtoa, mitaan ei muutettu"; fi; }
vaihto_alkanut=0
poistuessa() {                          # odottamaton poistuminen (esim. set -u): siivous ja paluu vanhaan
  [ "$LOPETETTU" = 1 ] && return
  info "odottamaton poistuminen"
  local rc=0
  if [ "$vaihto_alkanut" = 1 ] && ! paluu_vanhaan; then
    rc=3; info "VIRHE: odottamattoman poistumisen palautus jai kesken ($(palautuksen_tila)); tarkista kasin"
  fi
  siivoa; palauta_ajastimet
  [ "$rc" = 0 ] || exit "$rc"
}

# ---- tarkistukset ------------------------------------------------------------------
# Rootina ajettava luku: safe.directory vain taman kohteen tarkalle polulle (ei jokerimerkkia, ei gitin asetuksiin).
# Virheita ei niella: esitarkistukset() pysayttaa ajon, jos rev-parse tai status epaonnistuu.
pohja_git() { git -c "safe.directory=$POHJA" -C "$POHJA" "$@"; }
git_vika() { info "VIRHE: $*"; vika_esi "${1%% epaonnistui*} epaonnistui, ajoa ei jatketa; mitaan ei muutettu"; }
nykyinen_commit() {
  case "$1" in
    pohja) pohja_git rev-parse HEAD ;;
    yojono) sed -n 's/^commit=//p' "$YOJONO/.julkaisu-tieto" 2>/dev/null | head -1 ;;
  esac
}
esitarkistukset() {
  [ "$SALLI_EI_ROOT" = 1 ] || [ "$(id -u)" = 0 ] || vika_esi "aja rootina (sudo)"
  command -v git >/dev/null && command -v "$PYTHON" >/dev/null && command -v sha256sum >/dev/null && command -v flock >/dev/null \
    || vika_esi "tyokalu puuttuu (git, python3, sha256sum tai flock)"
  if [ -n "$TESTIKAYTTAJA" ]; then
    id -u -- "$TESTIKAYTTAJA" >/dev/null 2>&1 || vika_esi "testikayttajaa $TESTIKAYTTAJA ei ole (testit ajetaan ei-rootina)"
    [ "$(id -u -- "$TESTIKAYTTAJA")" != 0 ] || vika_esi "testikayttaja ei saa olla root"
    command -v runuser >/dev/null || vika_esi "runuser puuttuu (testit ajetaan ei-rootina)"
  fi
  [ -d "$POHJA/.git" ] || vika_esi "$POHJA ei ole git-klooni"
  local pohja_head
  pohja_head=$(pohja_git rev-parse HEAD 2>&1) && [ "${#pohja_head}" = 40 ] \
    || git_vika "git rev-parse epaonnistui kohteessa $POHJA (esim. dubious ownership): ${pohja_head:0:200}"
  [ -f "$YOJONO/.julkaisu-manifesti" ] || vika_esi "$YOJONO:ssa ei ole .julkaisu-manifestia (ensiasennus tehdaan isanta/julkaise.sh:lla)"
  if [ "$PERUUTA" = 0 ]; then
    local k d
    for k in pohja yojono; do
      d=$(koodi_kansio "$k")
      [ -e "$d.uusi" ] && vika_esi "edellisen ajon jaannos $d.uusi: tarkista ja poista kasin"
    done
    if [ "$SALLI_LIKAINEN" = 0 ]; then
      local pohja_tila
      pohja_tila=$(pohja_git status --porcelain --untracked-files=no 2>&1) \
        || git_vika "git status epaonnistui kohteessa $POHJA (esim. dubious ownership): ${pohja_tila:0:200}"
      [ -z "$pohja_tila" ] \
        || vika_esi "$POHJA:ssa on paikallisia muutoksia (git status); vanha kopio sailyisi .prev-kansiossa, ohita vain tarkoituksella: --salli-likainen"
      ( cd "$YOJONO" && sha256sum -c --quiet .julkaisu-manifesti >/dev/null 2>&1 ) \
        || vika_esi "$YOJONO poikkeaa manifestistaan (paikallisia muutoksia?); ohita vain tarkoituksella: --salli-likainen"
    fi
    local vapaa; vapaa=$(df -Pm "$(dirname "$POHJA")" | awk 'NR==2{print $4}')
    [ "${vapaa:-0}" -ge "$VAPAA_MB" ] || vika_esi "levytilaa vain ${vapaa:-?} Mt (vaaditaan $VAPAA_MB Mt)"
  fi
}

hae_uusi() {                            # kloonaa lahde, kiinnittaa commitin, tekee staging-kopiot
  KLOONI=$(mktemp -d "$(dirname "$POHJA")/.paivita-kloonaus.XXXXXX") || vika_esi "valiaikaiskansiota ei voi luoda"
  info "haetaan $HAARA lahteesta (git clone)"
  git clone --quiet --no-tags --branch "$HAARA" -- "$LAHDE" "$KLOONI/k" >/dev/null 2>&1 \
    || vika_esi "git clone epaonnistui (verkko tai lahde); mitaan ei muutettu"
  UUSI_COMMIT=$(git -C "$KLOONI/k" rev-parse HEAD 2>/dev/null) && [ "${#UUSI_COMMIT}" = 40 ] \
    || vika_esi "uuden commitin selvitys epaonnistui"
  info "uusi commit ${UUSI_COMMIT:0:12}"
  local k d nyt
  for k in pohja yojono; do
    d=$(koodi_kansio "$k"); nyt=$(nykyinen_commit "$k")
    if [ "$nyt" = "$UUSI_COMMIT" ]; then info "$(nimi "$k"): ajan tasalla ($(lyhyt "$nyt"))"
    else KOHTEET+=("$k"); info "$(nimi "$k"): $(lyhyt "${nyt:-tuntematon}") -> $(lyhyt "$UUSI_COMMIT")"; fi
  done
  [ "${#KOHTEET[@]}" -gt 0 ] || lopeta 0 "ajan tasalla, ei muutoksia (${UUSI_COMMIT:0:7})"
  for k in "${KOHTEET[@]}"; do
    d=$(koodi_kansio "$k"); UUSI[$k]="$d.uusi"; PREV[$k]="$d.prev-$TS"; VAIHE[$k]=""
    [ -e "${PREV[$k]}" ] && vika_esi "${PREV[$k]} on jo olemassa (kaksi ajoa samalla sekunnilla?)"
  done
}

tee_staging() {
  local k
  for k in "${KOHTEET[@]}"; do
    case "$k" in
      pohja)   # puhdas klooni (sama muoto kuin nykyinen /opt/agentti-pohja, jotta git pull ja ero-tyokalu toimivat)
        git clone --quiet --no-tags --local --branch "$HAARA" -- "$KLOONI/k" "${UUSI[$k]}" >/dev/null 2>&1 \
          && git -C "${UUSI[$k]}" remote set-url origin "$LAHDE" \
          && [ "$(git -C "${UUSI[$k]}" rev-parse HEAD)" = "$UUSI_COMMIT" ] \
          || vika_esi "agentti-pohjan staging-kopio epaonnistui" ;;
      yojono)  # manifestikopio: git archive, ilman .git:ia ja POIS-kansioita
        mkdir -p "${UUSI[$k]}" && git -C "$KLOONI/k" -c core.autocrlf=false -c core.eol=lf archive --format=tar "$UUSI_COMMIT" \
          | tar -x -C "${UUSI[$k]}" --no-same-owner --no-same-permissions || vika_esi "yojono-ajurin staging-kopio epaonnistui"
        local p; for p in $POIS_YOJONO; do rm -rf -- "${UUSI[$k]:?}/$p"; done
        local huono; huono=$(find "${UUSI[$k]}" -type f \( -name '*.env' -o -name '.env*' -o -name '*.pem' -o -name '*.key' -o -name 'id_rsa*' -o -name 'id_ed25519*' \) | head -3)
        [ -z "$huono" ] || vika_esi "uudessa koodissa on salaisuuden nakoisia tiedostoja (nimet: $(sed "s#${UUSI[$k]}/##" <<< "$huono" | tr '\n' ' '))"
        ( cd "${UUSI[$k]}" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum ) > "$T/manifesti" \
          && [ -s "$T/manifesti" ] || vika_esi "manifestin teko epaonnistui"
        cp "$T/manifesti" "${UUSI[$k]}/.julkaisu-manifesti"
        { echo "kohde=yojono-ajuri"; echo "commit=$UUSI_COMMIT"; echo "aika=$(date '+%F %T')"; echo "julkaisija=paivita-ajurit.sh"; } > "${UUSI[$k]}/.julkaisu-tieto" ;;
    esac
  done
}

aja_testit() {                          # set -o pipefail: tee/tail ei peita testikomennon virhetta
  # Uuden koodin testit eivat aja rootina: koodi kopioidaan valiaikaishakemistoon, jonka omistaa TESTIKAYTTAJA, ja testit
  # ajetaan hanen oikeuksillaan puhtaalla ymparistolla (env -i). Savutesti (savutesti_kohde) ajaa vaihdon jalkeen saman
  # uuden koodin myos palvelukayttajana (aja_kayttajana), joten root ei suorita uuden mainin koodia missaan vaiheessa.
  local k d mods rc kopio etuliite=()
  [ -n "$TESTIKAYTTAJA" ] && etuliite=(runuser -u "$TESTIKAYTTAJA" --)
  TESTIHAK=$(mktemp -d "${TMPDIR:-/tmp}/paivita-testi.XXXXXX") || vika_esi "testihakemistoa ei voi luoda"
  chmod 755 "$TESTIHAK"
  for k in "${KOHTEET[@]}"; do
    d=${UUSI[$k]}; [ "$k" = pohja ] && mods=$TESTI_POHJA || mods=$TESTI_YOJONO
    kopio="$TESTIHAK/$k"
    cp -a -- "$d" "$kopio" || vika_esi "testikopion teko epaonnistui ($(nimi "$k")); mitaan ei vaihdettu"
    if [ -n "$TESTIKAYTTAJA" ]; then
      chown -R -h -- "$TESTIKAYTTAJA": "$kopio" || vika_esi "testikopion omistajan vaihto epaonnistui ($(nimi "$k")); mitaan ei vaihdettu"
    fi
    info "testit: $(nimi "$k") ($mods) kayttajana ${TESTIKAYTTAJA:-$(id -un)}"
    ( cd "$kopio" && ${etuliite[@]+"${etuliite[@]}"} env -i HOME="$kopio" PATH="$PATH" LC_ALL=C.UTF-8 \
        $PYTHON -B -m unittest $mods ) 2>&1 | tee "$T/testi-$k.log" | tail -n 4
    rc=${PIPESTATUS[0]}
    if [ "$rc" != 0 ]; then
      grep -E '^(FAIL|ERROR):' "$T/testi-$k.log" | head -5
      lopeta 1 "testit kaatuivat ($(nimi "$k"), exit $rc); mitaan ei vaihdettu"
    fi
  done
}

odota_hiljaisuutta() {
  local n alku=$SECONDS vapaa
  mapfile -t AJASTIMET_PYSAYTETTY < <(aktiiviset_ajastimet)
  [ "${AJASTIMET_PYSAYTETTY[0]:-}" = "" ] && AJASTIMET_PYSAYTETTY=()
  info "pysaytetaan ${#AJASTIMET_PYSAYTETTY[@]} ajastinta vaihdon ajaksi"
  kasittele_ajastimet stop
  while :; do
    vapaa=$(kaynnissa_palvelut | tr '\n' ' ')
    [ -z "${vapaa// /}" ] && break
    if [ $((SECONDS - alku)) -ge "$ODOTA_S" ]; then vika_esi "palvelut ovat yha kaynnissa ${ODOTA_S} s jalkeen (${vapaa}); mitaan ei vaihdettu, ajastimet palautetaan"; fi
    info "odotetaan palveluita: $vapaa"
    sleep "$SEURANTA_S"
  done
}

# Ajaa komennon hakemistossa $1 palvelukayttajana (TESTIKAYTTAJA, runuser + env -i), ei rootina: savutesti suorittaa
# vaihdetun uuden koodin (import ja `yojono tila`), joten sita ei saa ajaa root-oikeuksin. Ilman TESTIKAYTTAJAa
# (vain testit, ei-root-ajo) komento ajetaan nykyisena kayttajana.
aja_kayttajana() {                      # $1 = hakemisto, loput = komento
  local hakem=$1 koti; shift
  if [ -n "$TESTIKAYTTAJA" ]; then
    koti=$(getent passwd "$TESTIKAYTTAJA" | cut -d: -f6); [ -d "${koti:-}" ] || koti=/nonexistent
    ( cd "$hakem" && runuser -u "$TESTIKAYTTAJA" -- env -i HOME="$koti" PATH="$PATH" LC_ALL=C.UTF-8 "$@" )
  else
    ( cd "$hakem" && "$@" )
  fi
}

savutesti_kohde() {                     # $1 = kohde
  local k=$1 d mods
  d=$(koodi_kansio "$k")
  [ "$k" = pohja ] && mods=$SAVU_POHJA || mods=$SAVU_YOJONO
  local m code=""; for m in $mods; do code+="import $m;"; done
  aja_kayttajana "$d" $PYTHON -B -c "$code" >/dev/null 2>&1 || { info "savutesti (tuonti) kaatui: $(nimi "$k")"; return 1; }
  if [ "$k" = yojono ] && [ -n "$TERVEYS_YOJONO" ]; then
    aja_kayttajana "$d" timeout 60 $PYTHON -B -m $TERVEYS_YOJONO >/dev/null 2>&1 || { info "savutesti (terveyskomento) kaatui: $(nimi "$k")"; return 1; }
  fi
  if [ "$k" = yojono ]; then
    ( cd "$d" && sha256sum -c --quiet .julkaisu-manifesti >/dev/null 2>&1 ) || { info "manifesti ei tasmaa: $(nimi "$k")"; return 1; }
  fi
  return 0
}
savutesti_kaikki() {                    # $1 = "palautus" -> kaikki kohteet; muuten vain paivitetyt
  local k lista=("${KOHTEET[@]}")
  [ "${1:-}" = palautus ] && lista=(pohja yojono)
  for k in "${lista[@]}"; do savutesti_kohde "$k" || return 1; done
  info "savutesti OK"; return 0
}

vaihda() {
  local k d
  vaihto_alkanut=1
  for k in "${KOHTEET[@]}"; do
    d=$(koodi_kansio "$k")
    mv -T -- "$d" "${PREV[$k]}" || vika_vaihdossa "vanhan siirto epaonnistui ($(nimi "$k"))"
    VAIHE[$k]=siirretty
    mv -T -- "${UUSI[$k]}" "$d" || vika_vaihdossa "uuden asetus epaonnistui ($(nimi "$k"))"
    VAIHE[$k]=vaihdettu
    info "vaihdettu: $(nimi "$k") (varmuuskopio ${PREV[$k]})"
  done
}

ilmoita_yksikot() {                     # tiedoksi: systemd-mallit eivat asennu itsestaan
  local vanha=$1
  [ -n "$vanha" ] && git -C "$KLOONI/k" cat-file -e "$vanha^{commit}" 2>/dev/null || return 0
  git -C "$KLOONI/k" diff --quiet "$vanha" "$UUSI_COMMIT" -- systemd claudejono/systemd 2>/dev/null \
    || info "HUOM: systemd-yksikkomallit muuttuivat (git diff --stat ${vanha:0:7} ${UUSI_COMMIT:0:7} -- systemd claudejono/systemd); skripti ei asenna yksikoita, asenna tarvittaessa kasin"
}

peruuta() {                             # --peruuta: viimeisin .prev-* takaisin
  local k d p
  mapfile -t AJASTIMET_PYSAYTETTY < <(aktiiviset_ajastimet); [ "${AJASTIMET_PYSAYTETTY[0]:-}" = "" ] && AJASTIMET_PYSAYTETTY=()
  # Palautetaan vain viimeisimman ajon varmuuskopiot (sama aikaleima), jottei vanhempi .prev-* tule vahingossa takaisin.
  local uusin; uusin=$(for k in pohja yojono; do ls -d "$(koodi_kansio "$k")".prev-* 2>/dev/null; done | sed 's/.*\.prev-//' | LC_ALL=C sort | tail -1)
  [ -n "$uusin" ] || vika_esi "ei varmuuskopiota (.prev-*)"
  KOHTEET=()
  for k in pohja yojono; do
    d=$(koodi_kansio "$k"); UUSI[$k]=""; VAIHE[$k]=""
    if [ -d "$d.prev-$uusin" ]; then PREV[$k]="$d.prev-$uusin"; KOHTEET+=("$k"); fi
  done
  [ "$KUIVA" = 1 ] && lopeta 0 "kuiva-ajo: palautettaisiin .prev-$uusin (${KOHTEET[*]}), mitaan ei muutettu"
  kasittele_ajastimet stop
  vaihto_alkanut=1
  local alku=$SECONDS busy
  while busy=$(kaynnissa_palvelut | tr '\n' ' '); [ -n "${busy// /}" ]; do
    [ $((SECONDS - alku)) -ge "$ODOTA_S" ] && vika_esi "palvelut kaynnissa (${busy}); mitaan ei palautettu"
    sleep "$SEURANTA_S"
  done
  # Palautus kohde kohteelta. Ensimmaisen virheen jalkeen ei yriteta enaa muita: tila pysyy mahdollisimman selvana.
  local puuttuu=0 virhe=""
  PALAUTETTU=(); EI_PALAUTETTU=()
  for k in "${KOHTEET[@]}"; do
    d=$(koodi_kansio "$k")
    if [ -n "$virhe" ]; then EI_PALAUTETTU+=("$(nimi "$k") (ei yritetty, ennallaan)"); continue; fi
    if ! mv -T -- "$d" "$d.peruttu-$TS"; then
      virhe=1; EI_PALAUTETTU+=("$(nimi "$k") (nykyisen siirto epaonnistui, ennallaan)"); info "VIRHE: peruutus epaonnistui: $(nimi "$k")"; continue
    fi
    if mv -T -- "${PREV[$k]}" "$d"; then
      PALAUTETTU+=("$(nimi "$k")")
      info "palautettu: $(nimi "$k") <- ${PREV[$k]##*/} (nykyinen talteen: $(basename "$d").peruttu-$TS)"
    else
      virhe=1
      if mv -T -- "$d.peruttu-$TS" "$d"; then
        EI_PALAUTETTU+=("$(nimi "$k") (varmuuskopion asetus epaonnistui; nykyinen koodi takaisin paikallaan, ${PREV[$k]##*/} jai)")
      else
        puuttuu=1; EI_PALAUTETTU+=("$(nimi "$k") (KOHDE PUUTTUU; nykyinen koodi: $d.peruttu-$TS, vanha koodi: ${PREV[$k]})")
      fi
      info "VIRHE: peruutus epaonnistui: ${EI_PALAUTETTU[-1]}"
    fi
  done
  vaihto_alkanut=0
  if [ -n "$virhe" ]; then
    if [ "${#PALAUTETTU[@]}" -gt 0 ] || [ "$puuttuu" = 1 ]; then
      lopeta 3 "osittainen palautus, tarkista kasin: $(palautuksen_tila)"
    fi
    lopeta 1 "peruutus epaonnistui, mitaan ei palautettu ($(palautuksen_tila))"
  fi
  savutesti_kaikki palautus || lopeta 1 "palautettu, mutta savutesti EI mennyt lapi: tarkista kasin"
  lopeta 0 "peruutettu: agentti-pohja $(lyhyt "$(nykyinen_commit pohja)"), yojono-ajuri $(lyhyt "$(nykyinen_commit yojono)")"
}

paa() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --kuiva) KUIVA=1 ;;
      --peruuta) PERUUTA=1 ;;
      --salli-likainen) SALLI_LIKAINEN=1 ;;
      --odota) shift; [[ "${1:-}" =~ ^[0-9]+$ ]] || { echo "--odota vaatii sekunnit" >&2; exit 2; }; ODOTA_S=$1 ;;
      -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *) echo "tuntematon valinta: $1" >&2; exit 2 ;;
    esac; shift
  done
  trap 'keskeytys' INT TERM HUP
  trap 'poistuessa' EXIT
  mkdir -p "$(dirname "$LUKKO")" 2>/dev/null
  exec 9>"$LUKKO" || { echo "lukkotiedostoa ei voi avata" >&2; exit 2; }
  flock -n 9 || { echo "toinen paivitys on kesken (lukko)"; exit 2; }
  T=$(mktemp -d) || { echo "valiaikaiskansio" >&2; exit 2; }
  info "paivita-ajurit alkaa ($( [ "$KUIVA" = 1 ] && echo kuiva || { [ "$PERUUTA" = 1 ] && echo peruutus || echo ajo; } ), $TS)"
  esitarkistukset
  [ "$PERUUTA" = 1 ] && peruuta
  hae_uusi
  tee_staging
  aja_testit
  if [ "$KUIVA" = 1 ]; then
    lopeta 0 "kuiva-ajo: testit lapi, vaihdettaisiin $(for k in "${KOHTEET[@]}"; do printf '%s ' "$(nimi "$k")"; done)-> $(lyhyt "$UUSI_COMMIT"); mitaan ei muutettu"
  fi
  local vanha_pohja; vanha_pohja=$(nykyinen_commit pohja)
  local vanhat; vanhat=$(for k in "${KOHTEET[@]}"; do printf '%s %s, ' "$(nimi "$k")" "$(lyhyt "$(nykyinen_commit "$k")")"; done)
  odota_hiljaisuutta
  vaihda
  savutesti_kaikki || vika_vaihdossa "savutesti epaonnistui uudella koodilla"
  ilmoita_yksikot "$vanha_pohja"
  lopeta 0 "${vanhat%, } -> ${UUSI_COMMIT:0:7}; varmuuskopiot .prev-$TS; peruutus: paivita-ajurit.sh --peruuta"
}
paa "$@"
exit $?
