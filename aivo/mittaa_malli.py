"""Mittaa paikallisen mallipalvelimen nopeuden ja tulostaa vastaukset suomen laadun arviointia varten.

Tony ajaa isannalla: python3 -m aivo.mittaa_malli [--url http://127.0.0.1:8081]
Kysymykset ovat yleisia (ei Tonyn henkilokohtaista dataa). Vain loopback-osoite sallitaan."""
import argparse
import json
import sys
import time
import urllib.request

from aivo import yksityinen

KYSYMYKSET = (
    "Selit\u00e4 lyhyesti, mit\u00e4 git rebase tekee ja milloin sit\u00e4 kannattaa v\u00e4ltt\u00e4\u00e4.",
    "Kirjoita kolmen lauseen yhteenveto siit\u00e4, miksi testit kannattaa ajaa ennen kuin muutos yhdistet\u00e4\u00e4n.",
    "Mit\u00e4 tarkoittaa DNS-rebinding ja miten silt\u00e4 suojaudutaan verkkopalvelimessa?",
    "Taivuta sana 'yhdyssana' monikon genetiiviss\u00e4 ja anna lauseesta esimerkki.",
    "Kirjoita kohtelias s\u00e4hk\u00f6posti opettajalle, jossa pyyd\u00e4n lis\u00e4aikaa palautukseen.",
)


def mittaa(url, avaa=None):
    if not yksityinen.sallittu_malli_osoite(url):
        raise yksityinen.YksityinenVirhe("osoite ei ole loopback")
    avaa = avaa or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    tulokset = []
    for k in KYSYMYKSET:
        runko = json.dumps({"messages": [{"role": "system", "content": "Vastaa suomeksi tiiviisti."},
                                         {"role": "user", "content": k}],
                            "max_tokens": 300, "temperature": 0.3}).encode()
        t0 = time.monotonic()
        with avaa(urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=runko,
                                         headers={"Content-Type": "application/json"}), timeout=900) as r:
            d = json.loads(r.read())
        dt = time.monotonic() - t0
        n = (d.get("usage") or {}).get("completion_tokens") or 0
        tulokset.append({"kysymys": k, "vastaus": d["choices"][0]["message"]["content"].strip(),
                         "tokenia": n, "sekuntia": round(dt, 1), "tok_per_s": round(n / dt, 2) if dt > 0 else 0})
    return tulokset


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aivo.mittaa_malli")
    ap.add_argument("--url", default=yksityinen.OLETUS_MALLI_URL)
    a = ap.parse_args(argv)
    try:
        tulokset = mittaa(a.url)
    except (yksityinen.YksityinenVirhe, OSError, ValueError, KeyError) as e:
        print("virhe:", type(e).__name__, file=sys.stderr)
        return 1
    for t in tulokset:
        print("## %s\n%s\n[%d tokenia, %.1f s, %.2f tok/s]\n" % (t["kysymys"], t["vastaus"], t["tokenia"], t["sekuntia"], t["tok_per_s"]))
    keskiarvo = sum(t["tok_per_s"] for t in tulokset) / len(tulokset)
    print("Keskinopeus: %.2f tok/s. Arvioi vastausten suomi itse (1-5) ja kirjaa malli + pisteet muistiin." % keskiarvo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
