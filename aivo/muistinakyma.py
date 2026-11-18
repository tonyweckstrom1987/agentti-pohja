"""Aivon muistin nakyma chat-sivulle (suunnitelma aivo-keskustelumuisti, kohdat 5.1 ja 5.3), vain luku.

Listaa yhden tilan tiivistelmat (aika, lyhyt sisalto, Tonyn lainaukset) HTML:ksi. Tiivistelma on DATAA: kaikki
tiedostosta luettu arvo kulkee puhdista_naytolle-funktion ja html.escape-funktion kautta. Moduuli ei kirjoita
mitaan, ei kutsu mallia eika verkkoa, eika tunne tilojen oletuspolkuja: kansio annetaan parametrina, ja kutsuja
(chat.Tila) valitsee kansion tilan mukaan. Tyokalutilan kansio ei koskaan ole yksityisen alueen kansio."""
import html
from pathlib import Path

from aivo import tiivistelma as tv

LYHYT = 200        # merkkia tulkintaa kohden
MAX_NAYTETTAVAT = 50


def _e(x):
    return html.escape(tv.puhdista_naytolle(str(x)), quote=True)


def _lyhenna(teksti):
    teksti = str(teksti)
    return teksti if len(teksti) <= LYHYT else teksti[:LYHYT] + "\u2026"


def keraa(kansio):
    """Palauttaa ([tiivistelma, ...], ohitettu_lkm), uusin ensin. Puuttuva kansio => tyhja. Rikkinainen tiedosto
    ohitetaan ja lasketaan (virheen sisaltoa ei tulosteta)."""
    tulos, ohitettu = [], 0
    if not Path(kansio).is_dir():
        return tulos, 0
    try:
        tunnukset = tv.luettele(kansio)
    except tv.TiivistelmaVirhe:
        return tulos, 1
    for tunnus in tunnukset:
        try:
            tulos.append((tunnus, tv.lue(kansio, tunnus)))
        except tv.TiivistelmaVirhe:
            ohitettu += 1
    tulos.sort(key=lambda x: str(x[1].get("tiivistetty", "")), reverse=True)
    return tulos[:MAX_NAYTETTAVAT], ohitettu


def sivu(kansio, otsikko):
    """HTML-sivu (vain luku). Otsikko on koodin oma vakio, ei datasta."""
    rivit, ohitettu = keraa(kansio)
    o = ["<!doctype html>", '<html lang="fi"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         "<title>Aivo \u2013 muisti</title>",
         "<style>body{font:16px/1.45 system-ui,sans-serif;max-width:46rem;margin:auto;padding:1rem}"
         "section{border:1px solid #888;border-radius:8px;padding:.6rem .9rem;margin:.8rem 0}"
         "q{display:block;margin:.2rem 0 .2rem 1rem}.pieni{font-size:.85rem;opacity:.7}</style></head><body>",
         '<p><a href="/">\u2190 Keskusteluun</a></p>',
         "<h1>Aivon muisti: %s</h1>" % html.escape(otsikko),
         '<p class="pieni">Vain luku. Tiivistelm\u00e4t ovat Aivon tulkintoja; Tonyn omat sanat n\u00e4kyv\u00e4t lainauksina.</p>']
    if not rivit:
        o.append("<p>Ei tiivistelmi\u00e4.</p>")
    for tunnus, t in rivit:
        o.append("<section><strong>%s</strong> <span class=\"pieni\">viestit %s\u2013%s, malli %s</span>"
                 % (_e(t.get("tiivistetty")), _e(t.get("alku")), _e(t.get("loppu")), _e(t.get("malli"))))
        for k in t.get("kohdat", []):
            if not isinstance(k, dict):
                continue
            korjattu = bool(k.get("tony_korjaus"))
            sisalto = k.get("tony_korjaus") if korjattu else k.get("tulkinta")
            o.append("<div><em>%s</em> (%s): %s%s"
                     % (_e(k.get("tyyppi")), _e(k.get("tila")), _e(_lyhenna(sisalto)),
                        " <span class=\"pieni\">(Tonyn korjaus)</span>" if korjattu else
                        " <span class=\"pieni\">(%s)</span>" % html.escape(tv.MERKINTA)))
            lain = k.get("tony_sanoi", [])
            for q in lain if isinstance(lain, list) else []:
                o.append("<q>%s</q>" % _e(q))
            o.append("</div>")
        o.append("</section>")
    if ohitettu:
        o.append('<p class="pieni">Ohitettu lukukelvottomia tiedostoja: %d.</p>' % ohitettu)
    o.append("</body></html>")
    return "\n".join(o)
