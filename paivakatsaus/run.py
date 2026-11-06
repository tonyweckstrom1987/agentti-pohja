#!/usr/bin/env python3
"""Paivakatsauksen ajo: profiili -> collectors -> llm -> render -> proposals -> send (vain stdlib).

  python3 -B run.py                 # dry-run (oletus): ei mallia, ei kuluja, ei lahetysta, ei lukkoa; tulostaa varaviestin
  python3 -B run.py --llm           # oikea malli (kuluu kulukattoa), viesti tulostetaan; ei lahetysta, ei ehdotustiedostoja
  python3 -B run.py --send          # sisaltaa --llm; luo ehdotukset, lahettaa Telegramiin ja kirjoittaa lukon

Virhe collectorissa tai mallissa -> varaviesti ("Katsaus ilman tiivistysta"). Kulukatto -> lyhyt ilmoitus "kulukatto" ja lopetus;
rikkinainen kirjanpito -> ilmoitus "kirjanpito rikki" (ei lukitse paivaa). Poistumiskoodit: 0 ok, 1 virhe, 2 kulukatto,
3 viesti lahetettiin mutta lukkoa ei voitu kirjoittaa (tuplalahetyksen riski), 64 kayttovirhe (argparse; EI sama kuin 2).
Loki paivakatsaus.log: aika, tila, tokenit, kulu, virhekoodi. Ei promptin eika vastauksen sisaltoa; sk-, Bearer ja token=
-kuviot suodatetaan. Asetukset vain config.lue_asetukset():n kautta (ymparistomuuttujat); arvoja ei tulosteta.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import collectors
import config
import llm
import posti
import profiili
import proposals
import render
import send

OK, VIRHE, KULUKATTO, LAHETETTY_ILMAN_LUKKOA, KAYTTOVIRHE = 0, 1, 2, 3, 64

_SUODATIN = (
    (re.compile(r"(?i)\bbearer\s+\S+"), "Bearer [suodatettu]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"), "[suodatettu]"),
    (re.compile(r"(?i)\b(token|api[_-]?key|secret|password)=\S+"), r"\1=[suodatettu]"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{20,}"), "[suodatettu]"),
)


def suodata_loki(teksti):
    """Poistaa lokirivilta avainten ja tokenien kuviot (sk-..., Bearer ..., token=..., Telegram-token)."""
    for kuvio, korvaus in _SUODATIN:
        teksti = kuvio.sub(korvaus, teksti)
    return teksti


def kirjaa_loki(polku, rivi):
    """Lisaa yhden suodatetun ASCII-rivin lokiin. Ei seuraa symlinkkia. Epaonnistuminen ei kaada ajoa."""
    data = (suodata_loki(rivi).replace("\n", " ").replace("\r", " ") + "\n").encode("ascii", errors="replace")
    try:
        fd = os.open(polku, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def _num(x, muoto="{:.4f}"):
    return "-" if x is None else muoto.format(x)


def _lokirivi(aika, tila, kaytto, kulu, koodit, muistiinpanot, ehdotuksia, vaihe):
    p, k = (kulu or {}).get("paiva_usd"), (kulu or {}).get("kuukausi_usd")
    kutsut = (kulu or {}).get("paiva_kutsut")
    tok = f"{kaytto.get('prompt_tokens') if kaytto.get('prompt_tokens') is not None else '-'}/{kaytto.get('completion_tokens') if kaytto.get('completion_tokens') is not None else '-'}"
    kesto = kaytto.get("kesto_s")
    lisa = (f" kesto_s={kesto:.2f}" if kesto is not None else "") + (f" http={kaytto['http']}" if kaytto.get("http") is not None else "") \
        + (f" finish={kaytto['finish']}" if kaytto.get("finish") else "") \
        + (f" skeema_syy={kaytto['skeema_syy']}" if re.fullmatch(r"[A-Za-z0-9_.\[\],:]{1,80}", str(kaytto.get("skeema_syy") or "")) else "")
    return (f"{aika.isoformat(timespec='seconds')} tila={tila} vaihe={vaihe} tokenit={tok}{lisa} kutsun_kulu_usd={_num(kaytto.get('usd'), '{:.6f}')} "
            f"kulu_paiva_usd={_num(p)} kulu_kk_usd={_num(k)} llm_kutsuja_pv={kutsut if kutsut is not None else '-'} "
            f"ehdotuksia={ehdotuksia} virhekoodi={','.join(koodit) if koodit else '-'}"
            + (f" huom={','.join(muistiinpanot)}" if muistiinpanot else ""))


_KOODI_RE = re.compile(r"[A-Za-z0-9_]{1,60}")


def _yhteenveto(paluu, vaihe, koodit):
    """Yksi kiintea stderr-rivi (journal): paluukoodi, vaihe ja virhekoodit. Vain kiinteat koodit; muut korvataan
    sanalla 'muu', joten rivi ei voi sisaltaa poikkeusviestia, mallin tekstia tai tunnuksia."""
    puhtaat = [k if isinstance(k, str) and _KOODI_RE.fullmatch(k) else "muu" for k in koodit]
    return suodata_loki(f"Ajo paattyi: paluu={paluu} vaihe={vaihe} virhekoodi={','.join(puhtaat) if puhtaat else '-'}")


def ajo(kayta_llm=False, laheta=False, *, env=None, nyt=None, polut=None, http_llm=None, http_send=None, keraa=None,
        tuloste=None, virhetuloste=None, viive=send.VIIVE):
    """Palauttaa poistumiskoodin: 0 ok, 1 virhe, 2 kulukatto. Oletus on dry-run."""
    kayta_llm = kayta_llm or laheta
    env = os.environ if env is None else env
    aika = (nyt or datetime.now(timezone.utc)).astimezone(ZoneInfo(config.PAIVA_TZ))    # lokin aikaleima PAIVA_TZ:ssa
    paiva = config.paiva(aika)                                                           # YKSI paivalaskenta (jarjestelman tz ei vaikuta)
    arkisto = Path((polut or {}).get("arkisto", config.ARKISTO))
    p = {"ehdotukset": config.EHDOTUKSET, "loki": config.LOKI, "kulu": arkisto / "kulu.json",
         "ideat": collectors.IDEAT_DIR, "trendit": collectors.TRENDIT_OUT}
    p.update({k: v for k, v in (polut or {}).items() if k != "arkisto"})
    tila = "send" if laheta else "llm" if kayta_llm else "dry-run"
    ulos, err = tuloste or sys.stdout, virhetuloste or sys.stderr
    koodit, muistiinpanot, kaytto = [], [], {}
    vaihe, ehdotuksia, kulu = "alku", 0, None
    koodi, kirjanpitovirhe, ilman_lukkoa = OK, False, False

    def viimeistele(paluu):
        rivi = _lokirivi(aika, tila, kaytto, kulu or render.lue_tila(p["kulu"], aika), koodit, muistiinpanot, ehdotuksia, vaihe)
        if not kirjaa_loki(p["loki"], rivi):
            print("Lokia ei voitu kirjoittaa", file=err)
        print(_yhteenveto(paluu, vaihe, koodit), file=err)                   # journaliin: syy nakyy ilman lokin lukuoikeutta
        return paluu

    try:
        # 1) lukko ensin: jos tanaan on jo lahetetty, mallia ei kutsuta
        if laheta:
            vaihe = "lukko"
            try:
                if send.onko_lahetetty(arkisto, aika):
                    muistiinpanot.append("jo_lahetetty")
                    print("Tanaan on jo lahetetty; ei tehda mitaan", file=err)
                    return viimeistele(OK)
            except send.SendEstetty:
                koodit.append("lukko_virhe")
                print("Lukkoa ei voi tarkistaa; ei lahetysta", file=err)
                return viimeistele(VIRHE)
        # 2) asetukset (vain mallille ja lahetykselle)
        asetukset = None
        if kayta_llm:
            vaihe = "asetukset"
            try:
                asetukset = config.lue_asetukset(env)
            except config.AsetusVirhe as e:
                koodit.append("asetus")
                print(f"Asetusvirhe: {e}", file=err)                    # viesti nimeaa vain muuttujat
                return viimeistele(VIRHE)
        # 3) profiili
        vaihe = "profiili"
        profiili_teksti = None
        try:
            profiili_teksti, varoitukset = profiili.lataa_profiili(env=env, nyt=paiva)
            if varoitukset:
                muistiinpanot.append(f"profiilivaroituksia={len(varoitukset)}")
        except (profiili.ProfiiliVirhe, config.AsetusVirhe):
            if kayta_llm:
                koodit.append("profiili_virhe")                          # mallia ei kayteta ilman profiilia
            else:
                muistiinpanot.append("profiili_ohitettu")
        # 4) collectors
        vaihe = "collectors"
        try:
            lahteet = (keraa or collectors.keraa_kaikki)(paiva, p["ideat"], p["trendit"])
        except Exception:
            lahteet = []
            koodit.append("collector_virhe")
        if any(t.get("tila") == "virhe" for t in lahteet):
            koodit.append("collector_virhe")
        # 5) malli
        vaihe = "llm"
        tulos = None
        if not kayta_llm:
            muistiinpanot.append("ilman_llm")
        elif "collector_virhe" in koodit or "profiili_virhe" in koodit:
            muistiinpanot.append("llm_ohitettu")
        elif not any(t.get("tila") == "ok" for t in lahteet):
            muistiinpanot.append("ei_aineistoa")
        else:
            try:
                tulos = llm.kysy(profiili_teksti, lahteet, asetukset=asetukset, http=http_llm, kulu_polku=p["kulu"],
                                 nyt=aika, kaytto=kaytto)
                if tulos is None:
                    koodit.append(kaytto.get("virhekoodi") or "llm_skeema")
            except llm.LlmKirjanpitoVirhe:                          # ennen LlmEstetty:a (aliluokka): kirjanpito rikki != kulukatto
                koodit.append("kirjanpito_rikki")
                kirjanpitovirhe = True
            except llm.LlmEstetty:
                koodit.append("kulukatto")
                koodi = KULUKATTO
            except llm.LlmVirhe as e:
                koodit.append(e.koodi)                              # llm_aikakatkaisu, llm_http_<koodi>, llm_yhteys, ...
        kulu = render.lue_tila(p["kulu"], aika)
        # 6a) kirjanpito rikki: lyhyt ilmoitus omalla lukollaan (ei lukitse paivan varsinaista katsausta), virhe 1
        if kirjanpitovirhe:
            vaihe = "kirjanpito"
            viesti = f"{render.otsikko(paiva)}: kirjanpito rikki (LLM-kutsua ei tehty)"
            ilman_lukkoa = _toimita(viesti, laheta, asetukset, http_send, arkisto, aika, viive, koodit, ulos, err, lukko_nimi="ilmoitus")
            return viimeistele(LAHETETTY_ILMAN_LUKKOA if ilman_lukkoa else VIRHE)
        # 6b) kulukatto: lyhyt ilmoitus ja lopetus
        if koodi == KULUKATTO:
            vaihe = "kulukatto"
            viesti = f"{render.otsikko(paiva)}: kulukatto (LLM-kutsua ei tehty)"
            ilman_lukkoa = _toimita(viesti, laheta, asetukset, http_send, arkisto, aika, viive, koodit, ulos, err)
            return viimeistele(LAHETETTY_ILMAN_LUKKOA if ilman_lukkoa else KULUKATTO)
        # 7) ehdotukset (oikeat numerot; dry-run vain suunnittelee)
        vaihe = "ehdotukset"
        ehdotukset = None
        mallin_ehdotukset = list(tulos["ehdotukset"]) if tulos and tulos.get("ehdotukset") else []
        posti_luetut, posti_ei_lukuoikeutta = posti.lue_posti_tila(aika, p.get("posti"))                 # vain luku, virheet ohitetaan
        posti_tekstit = posti.poista_kasitellyt(posti_luetut, p["ehdotukset"])
        if mallin_ehdotukset or posti_tekstit:
            try:
                if laheta:
                    ehdotukset = proposals.luo_ehdotukset(mallin_ehdotukset + posti_tekstit, nyt=aika, hakemisto=p["ehdotukset"])
                else:
                    ehdotukset = proposals.suunnittele(mallin_ehdotukset + posti_tekstit, nyt=aika, hakemisto=p["ehdotukset"])
            except (proposals.ProposalVirhe, OSError):
                koodit.append("ehdotukset_virhe")
                ehdotukset = []
        ehdotuksia = len(ehdotukset or [])
        # 8) viesti ja toimitus
        vaihe = "viesti"
        viesti = render.renderoi(tulos, lahteet, kulu, paiva, ehdotukset_numeroin=ehdotukset, postivaroitus=posti_ei_lukuoikeutta,
                                virhe=render.virherivit(koodit, kaytto))
        vaihe = "laheta" if laheta else "tulosta"
        ilman_lukkoa = _toimita(viesti, laheta, asetukset, http_send, arkisto, aika, viive, koodit, ulos, err)
        return viimeistele(LAHETETTY_ILMAN_LUKKOA if ilman_lukkoa else (VIRHE if koodit else OK))
    except Exception as e:
        koodit.append("odottamaton_" + type(e).__name__)
        print(f"Ajo epaonnistui: {type(e).__name__}", file=err)
        return viimeistele(VIRHE)


def _toimita(viesti, laheta, asetukset, http_send, arkisto, aika, viive, koodit, ulos, err, lukko_nimi=None):
    """Tulostaa viestin (dry-run) tai lahettaa sen. Lisaa virhekoodin koodit-listaan, jos lahetys epaonnistuu.
    Palauttaa True, jos viesti LAHETETTIIN mutta lukkoa ei voitu kirjoittaa (oma virhekoodi ja poistumiskoodi 3)."""
    if not laheta:
        print(viesti, file=ulos)
        return False
    try:
        tulos = send.laheta(viesti, asetukset=asetukset, http=http_send, arkisto=arkisto, nyt=aika, viive=viive, lukko_nimi=lukko_nimi)
        print(f"Lahetys: {tulos}", file=err)
    except send.SendLukkoVirhe as e:                                # ENNEN SendVirhe:ta: viesti on perilla, lukko puuttuu
        koodit.append("lukko_kirjoitus_epaonnistui")
        print(f"VAROITUS: {e}. Luo lukko kasin (touch arkisto/PVM.sent) ennen uutta ajoa, muuten viesti lahtee uudelleen", file=err)
        return True
    except send.SendEstetty as e:
        koodit.append("laheta_estetty")
        print(f"Ei lahetetty: {e}", file=err)
    except send.SendVirhe as e:
        koodit.append("laheta_virhe")
        print(f"Lahetys epaonnistui: {e}", file=err)
    return False


class _Jasennin(argparse.ArgumentParser):
    """Kayttovirhe palauttaa koodin 64 (EX_USAGE), jottei se sekoitu kulukattoon (2)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(KAYTTOVIRHE, f"{self.prog}: virhe: {message}\n")


def main(argv=None):
    ap = _Jasennin(description="Paivakatsaus (oletus: dry-run)")
    ap.add_argument("--llm", action="store_true", help="kayta oikeaa mallia (ei lahetysta)")
    ap.add_argument("--send", action="store_true", help="laheta Telegramiin (sisaltaa --llm)")
    args = ap.parse_args(argv)
    return ajo(kayta_llm=args.llm, laheta=args.send)


if __name__ == "__main__":
    sys.exit(main())
