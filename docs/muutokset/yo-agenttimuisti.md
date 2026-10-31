# yo/agenttimuisti: opit ajosta toiseen

claude-jonon ajot alkoivat tyhjasta. Nyt ajuri lukee `claude-jono/opit.md` (docs-repo) kehotteeseen `<opit>`-lohkona ja poimii raportin `## Uusi oppi` -osion (0-2 riviä).

- `claudejono/ajuri.py`: `lue_opit`, `poimi_uusi_oppi`, `suodata_opit` (kieltolista), `paivita_opit` (max 60 riviä, vanhimmat tiivistetaan), `julkaise_opit`.
- `claudejono/kehote.md`: lue opit ensin; lisaa `## Uusi oppi`.
- Testit: poiminta, kieltolista, koon rajaus, kehotteen rakenne, ajo.

## Riski: prompt injection opien kautta (tehtava 32)
Opit ovat dataa mutta menevat seuraavan ajon kehotteeseen: haitallinen tai harhautunut rivi voisi yrittaa laajentaa oikeuksia. Torjunta: (1) kehote sanoo opit dataksi, (2) ajuri hylkaa rivit, jotka osuvat kieltolistaan (salaisuudet, sudo, oikeudet, ohitus/kiertaminen, katot, "ignore"), ovat yli 240 merkkia tai sisaltavat `<`/`` ` ``, (3) enintaan 2 uutta riviä/ajo, tiedosto max 60 riviä. Kieltolista on karkea eika korvaa Tonyn katselmointia; muuten PR-taso ja CT115:n asetukset rajaavat.

## Avoin: commit-polku
`julkaise` sallii committiin vain `claude-jono/tulokset/`. opit.md ei siis ole viela sallittu. Polkurajaa ei muutettu: `OPIT_JULKAISU_SALLITTU = False`, joten ajuri lukee opit.md:n mutta ei kirjoita sita. Hyvaksynnan jalkeen vaihdetaan lippu True (julkaise_opit committaa vain tasan opit.md:n).
