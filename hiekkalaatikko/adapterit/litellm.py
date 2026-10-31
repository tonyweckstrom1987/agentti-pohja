#!/usr/bin/env python3
"""LiteLLM-adapteri (CT111, OpenAI-yhteensopiva). Ajetaan isannalla; kloonissa ei ole LLM-tunnuksia eika reittia.

Agentti saa yhden tyokalun, `kuori`, joka ajaa komennon KLOONISSA (`pct exec <CT> -- bash -lc ...`). Isannan tiedostoihin
ja ymparistoon agentti ei paase. Tunnukset tulevat ymparistosta: HL_LLM_URL (esim. http://<litellm>:4000) ja LITELLM_API_KEY.
Mallin nimi: HL_MALLI. Sopimus: ks. kuiva.sh.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

KIERROKSIA_MAX = 40
TULOSTE_MAX = 6000
MAX_TOKENS = 2000
KUORI_AIKA_S = 120
TYOKALU = [{"type": "function", "function": {
    "name": "kuori", "description": "Aja bash-komento hiekkalaatikkokontissa (root). Palauttaa poistumiskoodin ja tulosteen.",
    "parameters": {"type": "object", "properties": {"komento": {"type": "string"}}, "required": ["komento"]}}}]


def leikkaa(teksti, raja=TULOSTE_MAX):
    return teksti if len(teksti) <= raja else teksti[:raja] + "\n[katkaistu]"


def aja_kuoressa(pct, ct, komento, aika_s=KUORI_AIKA_S, ajo=subprocess.run):
    """Komento kloonissa. Isannan ymparistoa (avaimet) ei anneta aliprosessille."""
    try:
        r = ajo([pct, "exec", str(ct), "--", "timeout", str(aika_s), "bash", "-lc", komento],
                capture_output=True, text=True, timeout=aika_s + 15, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    except subprocess.TimeoutExpired:
        return "poistumiskoodi: aikakatko\n"
    return "poistumiskoodi: %d\n%s%s" % (r.returncode, r.stdout, r.stderr)


def kutsu_mallia(url, avain, malli, viestit, avaa=urllib.request.urlopen):
    runko = json.dumps({"model": malli, "messages": viestit, "tools": TYOKALU, "max_tokens": MAX_TOKENS}).encode()
    pyynto = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=runko, method="POST",
                                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + avain})
    with avaa(pyynto, timeout=120) as v:
        data = json.loads(v.read(4_000_000))
        kulu = v.headers.get("x-litellm-response-cost")
    try:
        kulu = float(kulu) if kulu is not None else None
    except ValueError:
        kulu = None
    return data, kulu


def aja(env, kutsu=kutsu_mallia, kuori=aja_kuoressa, kello=time.monotonic):
    url, avain, malli = env.get("HL_LLM_URL", ""), env.get("LITELLM_API_KEY", ""), env.get("HL_MALLI", "")
    if not (url and avain and malli):
        print("litellm-adapteri: HL_LLM_URL, LITELLM_API_KEY ja HL_MALLI vaaditaan", file=sys.stderr)
        return {"lopputulos": "epaonnistui", "kustannus_usd": None, "malli": malli or "tuntematon"}, 64
    katto = float(env.get("HL_KUSTANNUSKATTO_USD", "0"))
    loppuaika = kello() + float(env.get("HL_AIKAKATTO_S", "600")) - 30
    with open(env["HL_KUVAUS"], encoding="utf-8") as f:
        kuvaus = f.read(20000)
    viestit = [
        {"role": "system", "content": "Olet agentti eristetyssa hiekkalaatikkokontissa (root). Tyokalu `kuori` ajaa komennon kontissa. "
                                      "Tee annettu kokeilu. Lopuksi kirjoita viimeiselle riville TULOS: onnistui tai TULOS: epaonnistui."},
        {"role": "user", "content": kuvaus}]
    yhteensa, tuntematon, tulos, loki = 0.0, False, "epaonnistui", []
    for _ in range(KIERROKSIA_MAX):
        if kello() > loppuaika or yhteensa >= katto:
            loki.append("keskeytys: aika tai kustannuskatto")
            break
        data, kulu = kutsu(url, avain, malli, viestit)
        if kulu is None:
            tuntematon = True
        else:
            yhteensa += kulu
        viesti = data["choices"][0]["message"]
        viestit.append(viesti)
        kutsut = viesti.get("tool_calls") or []
        if not kutsut:
            sisalto = (viesti.get("content") or "").strip()
            loki.append("agentti: " + leikkaa(sisalto, 2000))
            if sisalto.splitlines() and sisalto.splitlines()[-1].strip() == "TULOS: onnistui":
                tulos = "onnistui"
            break
        for k in kutsut:
            try:
                komento = json.loads(k["function"]["arguments"])["komento"]
                ulos = leikkaa(kuori(env.get("HL_PCT", "pct"), env["HL_CT"], str(komento)))
            except (ValueError, KeyError, TypeError):
                komento, ulos = "?", "virheellinen tyokalukutsu"
            loki.append("$ %s\n%s" % (leikkaa(str(komento), 500), ulos))
            viestit.append({"role": "tool", "tool_call_id": k.get("id", ""), "content": ulos})
    with open(os.path.join(env["HL_TULOS"], "agentti.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(loki))
    # tuntematon kulu -> None: ajuri jattaa pessimistisen varauksen (koko katto)
    return {"lopputulos": tulos, "kustannus_usd": None if tuntematon else round(yhteensa, 6), "malli": malli}, 0


if __name__ == "__main__":
    yhteenveto, rc = aja(os.environ)
    print(json.dumps(yhteenveto))
    sys.exit(rc)
