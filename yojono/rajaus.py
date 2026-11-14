#!/usr/bin/env python3
"""Katselmoinnin rajaus: vain muutokset edellisen onnistuneen katselmoinnin jalkeen.

Kaytto (katselmoi.sh kutsuu): rajaus.py <klooni> <ulos-hakemisto> <perus> <repo> [raja_tavua]

Pohja valitaan nain:
1. <perus>/rajaus/<repo>.commit (edellisen onnistuneen ajon HEAD), jos commit on kloonissa;
2. muuten viimeisin raportti <perus>/raportit/<pvm>-<repo>.md: pohja on viimeinen commit ennen sen paivamaaraa;
3. muuten viimeiset 7 paivaa.
Jos diff on yli rajan (oletus 150000 tavua), mukaan otetaan suurimmat tiedostomuutokset rajan sisalle ja loput kirjataan pois jaaneiksi.

Kirjoittaa <ulos>/muutokset.md (tiedostolista, rajaus), <ulos>/diff.patch ja <ulos>/otsikko.md (raportin alkuun) ja tulostaa avain=arvo-rivit.
Vain stdlib. Ei salaisuuksia: lukee vain git-kloonia ja raporttien nimia."""
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OLETUS_RAJA = 150_000
OLETUS_PAIVAT = 7
OTSIKON_PISIMMAT = 10  # raportin alkuun enintaan nain monta pois jaanytta tiedostoa nimelta
TYHJA_PUU = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git:n tyhja puu: kaikki tiedostot nakyvat lisayksina
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def git(klooni, *args):
    r = subprocess.run(["git", "-C", str(klooni), *args], capture_output=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} epaonnistui (koodi {r.returncode})")
    return r.stdout


def commit_olemassa(klooni, sha):
    r = subprocess.run(["git", "-C", str(klooni), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, check=False)
    return r.returncode == 0


def viimeisin_raportin_pvm(perus, repo):
    """Viimeisin onnistunut (md olemassa) raportti <pvm>-<repo>.md: palauttaa date-olion tai None."""
    kansio = Path(perus) / "raportit"
    pvmt = []
    if kansio.is_dir():
        for p in kansio.glob(f"????-??-??-{repo}.md"):
            try:
                pvmt.append(datetime.strptime(p.name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc))
            except ValueError:
                continue
    return max(pvmt) if pvmt else None


def valitse_pohja(klooni, perus, repo, nyt=None, paivat=OLETUS_PAIVAT):
    """Palauttaa (pohja_sha tai TYHJA_PUU, selite)."""
    nyt = nyt or datetime.now(timezone.utc)
    tallennettu = Path(perus) / "rajaus" / f"{repo}.commit"
    if tallennettu.is_file():
        sha = tallennettu.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha) and commit_olemassa(klooni, sha):
            return sha, f"edellinen onnistunut katselmointi (commit {sha[:8]})"
    pvm = viimeisin_raportin_pvm(perus, repo)
    if pvm is not None:
        raja, selite = pvm, f"edellisen raportin p\u00e4iv\u00e4 {pvm:%Y-%m-%d} (commit-tietoa ei ole)"
    else:
        raja, selite = nyt - timedelta(days=paivat), f"ei edellist\u00e4 katselmointia: viimeiset {paivat} p\u00e4iv\u00e4\u00e4"
    sha = git(klooni, "rev-list", "-1", f"--before={raja.strftime('%Y-%m-%dT%H:%M:%S')}+0000", "HEAD").decode().strip()
    if not sha:
        return TYHJA_PUU, selite + "; aikarajaa vanhempaa committia ei ole, koko historia on mukana"
    return sha, selite


def tiedostomuutokset(klooni, pohja):
    """Lista (tiedosto, patch-tavut, patch-bytes) muuttuneista tiedostoista. Nimet NUL-erotettuina (turvallinen erikoismerkeille)."""
    nimet = git(klooni, "diff", "--name-only", "-z", "--no-renames", pohja, "HEAD").split(b"\0")
    tulos = []
    for n in nimet:
        if not n:
            continue
        patch = git(klooni, "diff", "--no-renames", pohja, "HEAD", "--", n.decode("utf-8", "surrogateescape"))
        tulos.append((n.decode("utf-8", "replace"), len(patch), patch))
    return tulos


def valitse_mukaan(muutokset, raja):
    """Suurimmat ensin rajan sisalle; mikaan yksittainen yli rajan oleva ei mahdu. Palauttaa (mukana, pois) alkuperaisessa jarjestyksessa."""
    jarjestys = sorted(range(len(muutokset)), key=lambda i: (-muutokset[i][1], muutokset[i][0]))
    mukana, summa = set(), 0
    for i in jarjestys:
        koko = muutokset[i][1]
        if summa + koko <= raja:
            mukana.add(i)
            summa += koko
    return ([m for i, m in enumerate(muutokset) if i in mukana], [m for i, m in enumerate(muutokset) if i not in mukana], summa)


def rajaa(klooni, ulos, perus, repo, raja=OLETUS_RAJA, nyt=None):
    if not REPO_RE.match(repo):
        raise ValueError("virheellinen repo")
    ulos = Path(ulos)
    ulos.mkdir(parents=True, exist_ok=True)
    pohja, selite = valitse_pohja(klooni, perus, repo, nyt)
    head = git(klooni, "rev-parse", "HEAD").decode().strip()
    if pohja == TYHJA_PUU:  # tyhja puu ei aina ole objektitietokannassa: luodaan se (kertakayttoklooni)
        subprocess.run(["git", "-C", str(klooni), "hash-object", "-t", "tree", "-w", "--stdin"], input=b"", capture_output=True, check=False)
    muutokset = tiedostomuutokset(klooni, pohja)
    kaikki = sum(m[1] for m in muutokset)
    mukana, pois, mukana_koko = valitse_mukaan(muutokset, raja) if kaikki > raja else (muutokset, [], kaikki)
    (ulos / "diff.patch").write_bytes(b"".join(m[2] for m in mukana))
    rivit = [f"# Katselmoinnin rajaus", "", f"- Pohja: {selite}", f"- Vertailu: {pohja[:8] if pohja != TYHJA_PUU else 'tyhj\u00e4 puu'}..{head[:8]}",
             f"- Muuttuneita tiedostoja: {len(muutokset)}; diffin koko {kaikki} tavua, katselmoitavaa {mukana_koko} tavua (raja {raja})", ""]
    rivit.append("## Muuttuneet tiedostot")
    for n, koko, _ in sorted(muutokset, key=lambda m: m[0]):
        rivit.append(f"- {n} ({koko} tavua){' - J\u00c4I POIS: ei mahtunut rajaan' if any(n == p[0] for p in pois) else ''}")
    (ulos / "muutokset.md").write_text("\n".join(rivit) + "\n", encoding="utf-8")
    otsikko = [f"> Rajaus: katselmoitu vain muutokset ({selite}); {len(mukana)}/{len(muutokset)} tiedostoa, {mukana_koko} tavua diffi\u00e4."]
    if pois:
        otsikko.append(f"> Diff oli {kaikki} tavua (raja {raja}). Katselmoimatta j\u00e4i {len(pois)} tiedostoa: " + ", ".join(f"{p[0]} ({p[1]} t)" for p in sorted(pois, key=lambda p: -p[1])[:OTSIKON_PISIMMAT]) + (f" ja {len(pois) - OTSIKON_PISIMMAT} muuta (koko lista: .katselmointi-rajaus/muutokset.md)" if len(pois) > OTSIKON_PISIMMAT else "") + ".")
    (ulos / "otsikko.md").write_text("\n".join(otsikko) + "\n\n", encoding="utf-8")
    return {"pohja": pohja, "head": head, "muuttuneita": len(muutokset), "mukana": len(mukana), "pois": len(pois),
            "diff_tavua": kaikki, "mukana_tavua": mukana_koko}


def _nyt_ymparistosta():
    """KATSELMOI_NYT (ISO 8601, esim. 2026-11-05T12:00:00+00:00) kiinnittaa kellon testeille; puuttuessa None (oikea kello).
    Vaikuttaa vain siihen, mista hetkesta oletusajanjakso lasketaan; budjettiin ja rajoihin se ei koske."""
    arvo = os.environ.get("KATSELMOI_NYT")
    if not arvo:
        return None
    t = datetime.fromisoformat(arvo)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def main(argv):
    if len(argv) not in (5, 6):
        print("kaytto: rajaus.py <klooni> <ulos> <perus> <repo> [raja_tavua]", file=sys.stderr)
        return 2
    try:
        raja = int(argv[5]) if len(argv) == 6 else OLETUS_RAJA
        t = rajaa(argv[1], argv[2], argv[3], argv[4], raja, nyt=_nyt_ymparistosta())
    except (RuntimeError, ValueError, OSError) as virhe:
        print(f"rajaus.py: {type(virhe).__name__}", file=sys.stderr)  # vain tyyppi
        return 1
    for k, v in t.items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
