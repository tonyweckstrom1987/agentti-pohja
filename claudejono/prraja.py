"""Avointen agentti-PR:ien raja repoa kohden (Tonyn paatos A843, 7.11.2026).

Kussakin repossa saa olla enintaan RAJA_AVOIMIA_PR avointa ei-rajattua PR:aa. Rajatut PR:t (Tonyn yhdistettavat polut:
kulukatot, oikeudet, salaisuudet, raha, tyonkulut) odottavat Tonya, joten ne lasketaan erikseen eivatka kuluta rajaa.
Kun raja taytyy, suunnittelija ei lisaa jonoon tehtavia, jotka avaisivat repoon uuden PR:n: se valitsee viimeistelytehtavan
(korjaa hylatty PR, pura ristiriita, sulje korvattu).

Tama moduuli EI muuta katto-, oikeus- tai polkurajoja eika esta pr_kutsu.py:n omia tarkistuksia. Se vain kiristaa sita,
mika paasee jonoon. Luokittelu tulee agentti.aamupaketti.keraa:sta (sama rajattujen luokitus kuin automergessa).
"""
import re

RAJA_AVOIMIA_PR = 5

# Tehtava, joka viimeistelee olemassa olevia PR:ia, ei avaa uutta.
VIIMEISTELY_RE = re.compile(
    r"\bviimeistele\w*|\bristiriid\w*|\bristiriit\w*|\bkonflikti\w*|\bkorjaa\w*\s+(?:\w+\s+){0,3}hyl\w*"
    r"|\bsulje\w*|\bsulkulista\w*|\bkorvatu\w*|\bpurku\w*|\brebase\w*|\bp(?:\u00e4|a)ivit(?:\u00e4|a)\w*\s+(?:\w+\s+){0,2}pr\b",
    re.IGNORECASE)


def laske(ryhmat):
    """{repo: ei-rajattujen avointen PR:ien maara}. ryhmat on aamupaketti.keraa():n tulos; 'tony' (rajatut) ei kuluta rajaa."""
    luvut = {}
    for ryhma in ("automerge", "muut"):
        for rivi in ryhmat.get(ryhma, []):
            luvut[rivi["repo"]] = luvut.get(rivi["repo"], 0) + 1
    return luvut


def rajatut_luvut(ryhmat):
    """{repo: rajattujen (Tonya odottavien) PR:ien maara}, raportointia varten."""
    luvut = {}
    for rivi in ryhmat.get("tony", []):
        luvut[rivi["repo"]] = luvut.get(rivi["repo"], 0) + 1
    return luvut


def taynna(ryhmat, raja=RAJA_AVOIMIA_PR):
    """Niiden repojen joukko, joissa on vahintaan `raja` avointa ei-rajattua PR:aa."""
    return {repo for repo, n in laske(ryhmat).items() if n >= raja}


def hae_taynna(keraa=None, raja=RAJA_AVOIMIA_PR):
    """Taynna olevat repot GitHubin julkisesta API:sta. Lukuvirhe palauttaa tyhjan joukon (suunnittelua ei lukita verkkovirheen takia);
    pr_kutsu.py:n paivaraja ja Tonyn katselmointi pysyvat voimassa."""
    if keraa is None:
        from agentti import aamupaketti
        keraa = aamupaketti.keraa
    try:
        return taynna(keraa(), raja)
    except Exception as e:
        print("prraja: lukuvirhe " + type(e).__name__)
        return set()


def avaa_uuden_pr(sisalto, repot):
    """Repo, johon tehtava avaisi uuden PR:n (mainittu repo on taynna eika tehtava ole viimeistelya), tai None."""
    sisalto = sisalto or ""
    if VIIMEISTELY_RE.search(sisalto):
        return None
    for repo in sorted(repot):
        if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(repo) + r"(?![A-Za-z0-9_-])", sisalto):
            return repo
    return None


def suodata(tehtavat, taynna_repot):
    """(sallitut, lykatyt) : lykatyt = [(tehtava, repo)]. Jarjestys sailyy."""
    sallitut, lykatyt = [], []
    for t in tehtavat:
        repo = avaa_uuden_pr(t.get("sisalto", ""), taynna_repot) if taynna_repot else None
        if repo:
            lykatyt.append((t, repo))
        else:
            sallitut.append(t)
    return sallitut, lykatyt


def lykkaysrivit(lykatyt):
    return "\n".join("- PR-raja (%d/repo) taynna repossa %s: tehtava lykatty, valitse viimeistelytehtava: %s"
                     % (RAJA_AVOIMIA_PR, repo, (t.get("nimi") or "")[:60]) for t, repo in lykatyt)[:900]
