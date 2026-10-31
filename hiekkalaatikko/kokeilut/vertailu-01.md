# vertailu-01: sama pieni koodaustehtava eri malleille

Aja sama kuvaus kahdella mallilla (`--malli`). Kirjataan onnistuminen, hinta ja aika; ratkaisee objektiivinen testi, ei mallin oma vaite.

## Tehtava
Kirjoita tiedosto `/root/kokeilu/tehtava.py`, jossa on funktio `kesto_sekunneiksi(teksti: str) -> int`.
- Syote on kestoteksti, esim. `"1h30m"`, `"45s"`, `"2d"`, `"1d2h3m4s"`. Yksikot: `d`=86400, `h`=3600, `m`=60, `s`=1.
- Yksikot saavat esiintya vain suuruusjarjestyksessa (d, h, m, s) ja kukin enintaan kerran. Valilyonnit alussa ja lopussa sallitaan.
- Virheellinen syote (tyhja, tuntematon yksikko, luku ilman yksikkoa, vaara jarjestys, negatiivinen luku) nostaa `ValueError`.
- Vain vakiokirjasto. Testit ovat tiedostossa `/root/kokeilu/test_tehtava.py`; aja `python3 -m unittest -v` hakemistossa `/root/kokeilu`.
Ala muokkaa testitiedostoa. Viimeinen rivi: `TULOS: onnistui` tai `TULOS: epaonnistui`.
