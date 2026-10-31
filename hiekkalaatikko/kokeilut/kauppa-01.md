# kauppa-01: kauppatiimin strategian backtest eristetysti

Tavoite: aja yksi kauppatiimin strategia historiadataa vasten, tuota mittarit ja kirjaa, mita ei saatu toimimaan.
Vain paperirahaa: ei tunnuksia, ei porssirajapintoja, ei oikeita tilauksia. Jos jokin pyytaa avainta, pysahdy ja raportoi.

## Saannot
- Verkko: vain proxyn sallimat osoitteet (GitHub, PyPI, npm, Debian). Porssien rajapinnat ovat tarkoituksella estetty.
- Datahaku omalla koodilla: julkinen CSV/dataset GitHubista tai PyPI-paketti, jonka lisenssi sallii kayton. Kirjaa datan lahde ja haun aika.
- Strategiakoodi: `git clone https://github.com/agenttifirma-bot/kauppa-sandbox` (julkinen), jos se on saatavilla. Muuten kirjoita
  yksinkertainen vertailustrategia (esim. SMA-risteytys) ja sano raportissa, ettei oikeaa strategiaa saatu.
- Ala katso lookahead-vinoumaa vahatteleva tulos hyvaksi: signaali saa kayttaa vain sita tietoa, joka oli saatavilla ennen palkkia.
- Kaikki kulut mukaan (oletus 0,1 % per kauppa) ja ero "osta ja pida" -vertailuun.

## Tulos
`/root/kokeilu/tulokset.json` avaimilla: `data_lahde`, `jakso_alku`, `jakso_loppu`, `kauppoja`, `tuotto_pros`, `osta_ja_pida_pros`,
`max_laskuvaihe_pros`, `kulut_pros`, `huomiot` (lista, mm. rajoitteet ja epailyttavat kohdat).
Lisaksi `/root/kokeilu/RAPORTTI.md`: mita tehtiin, mika ei toiminut. "Data ei riita" on hyvaksytty lopputulos (silloin tulokset.json:ssa `kauppoja: 0` ja huomio).
Viimeinen rivi: `TULOS: onnistui` tai `TULOS: epaonnistui`.
