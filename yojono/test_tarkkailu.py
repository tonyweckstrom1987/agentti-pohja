"""tarkkailu.sh: oikea alaprosessi, tekoagentti ja tekokeraaja (ei verkkoa, ei oikeaa claudea)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKRIPTI = Path(__file__).resolve().parent / "tarkkailu.sh"


def kirjoita(polku, teksti, suoritettava=False):
    polku.parent.mkdir(parents=True, exist_ok=True)
    polku.write_text(teksti)
    if suoritettava:
        polku.chmod(polku.stat().st_mode | stat.S_IXUSR)


class Pohja(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.perus = self.t / "perus"
        self.env_tiedosto = self.t / "env"
        kirjoita(self.env_tiedosto, "KOE=1\n")
        self.agentti = self.t / "claude"

    def agentti_skripti(self, runko):
        kirjoita(self.agentti, "#!/bin/sh\n" + runko + "\n", True)

    def aja(self, repo="agentti-pohja", budjetti="1.00", teema="infra", raportti="2026-10-24-tarkkailu-t-agentti-pohja", **extra):
        env = dict(os.environ, TARKKAILU_PERUS=str(self.perus), TARKKAILU_TYO=str(self.t / "tyo"), TARKKAILU_ENV=str(self.env_tiedosto),
                   TARKKAILU_CLAUDE=str(self.agentti), TARKKAILU_PYTHON=sys.executable,
                   TARKKAILU_KUVAKOMENTO='echo kuva > "$0/github.md"; echo x > "$0/yojono.md"',
                   YOJONO_TEEMA=teema, YOJONO_RAPORTTI=raportti, YOJONO_TUNNUS="t")
        env.update(extra)
        return subprocess.run(["sh", str(SKRIPTI), repo, budjetti], capture_output=True, text=True, env=env, timeout=60)

    def raportti(self, nimi="2026-10-24-tarkkailu-t-agentti-pohja"):
        return (self.perus / "raportit" / (nimi + ".md")).read_text()


class TarkkailuTesti(Pohja):
    def test_agentti_ajetaan_vain_luku_ja_raportti_syntyy(self):
        kayttoloki = self.t / "kutsut"
        self.agentti_skripti(f'echo "$@" > {kayttoloki}; pwd >> {kayttoloki}; ls >> {kayttoloki}\n'
                             "echo '{\"total_cost_usd\": 0.21, \"result\": \"## Havainnot - a ## Ehdotukset 1. PARAS\"}'")
        r = self.aja()
        self.assertEqual(r.returncode, 0, r.stderr)
        teksti = self.raportti()
        self.assertIn("# Tarkkailu: infra", teksti)
        self.assertIn("## Ehdotukset 1. PARAS", teksti)
        self.assertIn("Tilannekuvan tiedostoja: 2", teksti)
        kutsut = kayttoloki.read_text()
        self.assertIn("--allowedTools Read,Grep,Glob", kutsut)
        self.assertIn("--max-budget-usd 1.00", kutsut)
        self.assertNotIn("Bash(", kutsut.split("--disallowedTools")[0])
        self.assertIn("github.md", kutsut)  # agentti ajetaan tilannekuvan hakemistossa
        self.assertIn("DATAA", kutsut)
        self.assertEqual(json.loads((self.perus / "raportit" / "2026-10-24-tarkkailu-t-agentti-pohja.json").read_text())["total_cost_usd"], 0.21)

    def test_kehote_kieltaa_mergattujen_ehdottamisen(self):
        kayttoloki = self.t / "kutsut"
        self.agentti_skripti(f'echo "$@" > {kayttoloki}\necho \'{{"total_cost_usd": 0, "result": "ok"}}\'')
        r = self.aja()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ala ehdota asiaa, joka nakyy jo mergatuissa PR:issa.", kayttoloki.read_text())

    def test_roolit_ja_tarkennus_kehotteessa(self):
        kayttoloki = self.t / "kutsut"
        self.agentti_skripti(f'echo "$@" > {kayttoloki}\necho \'{{"total_cost_usd": 0, "result": "ok"}}\'')
        for rooli, sana in (("infra", "Proxmox"), ("automaatio", "yojono, botti"), ("laatu", "testit, katselmointi"), ("kasvu", "portfolioprojektit")):
            r = self.aja(teema=rooli + " erityisesti levyn kasvu")
            self.assertEqual(r.returncode, 0, r.stderr)
            kutsut = kayttoloki.read_text()
            self.assertIn(sana, kutsut)
            self.assertIn("erityisesti levyn kasvu", kutsut)

    def test_malli_on_oletuksena_haiku_ja_vaihdettavissa(self):
        kayttoloki = self.t / "kutsut"
        self.agentti_skripti(f'echo "$@" > {kayttoloki}\necho \'{{"total_cost_usd": 0, "result": "ok"}}\'')
        self.assertEqual(self.aja().returncode, 0)
        self.assertIn("--model haiku", kayttoloki.read_text())
        self.assertEqual(self.aja(TARKKAILU_MALLI="deepseek-pro").returncode, 0)
        self.assertIn("--model deepseek-pro", kayttoloki.read_text())

    def test_virheelliset_syotteet(self):
        self.agentti_skripti("echo '{}'")
        for kw in ({"teema": "tuntematon"}, {"teema": ""}, {"teema": "x" * 301}, {"repo": "../x"}, {"repo": ""},
                   {"raportti": "a/b"}, {"raportti": ""}):
            with self.subTest(kw=kw):
                r = self.aja(**kw)
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_agentin_tulos_puuttuu_antaa_virheen(self):
        self.agentti_skripti("echo 'ei jsonia'")
        r = self.aja()
        self.assertEqual(r.returncode, 1)
        self.assertIn("ei agentin tulosta", self.raportti())

    def test_kuiva_ajo_ei_kutsu_agenttia(self):
        r = self.aja(TARKKAILU_KUIVA="1", TARKKAILU_CLAUDE="/ei/ole")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("kuiva PARAS", self.raportti())

    def test_keraajan_virhe_ei_kaada(self):
        self.agentti_skripti('echo \'{"total_cost_usd": 0, "result": "ok"}\'')
        r = self.aja(TARKKAILU_KUVAKOMENTO="exit 1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Tilannekuvan tiedostoja: 0", self.raportti())

    def _koko_loki(self):
        kayttoloki = self.t / "koot"
        self.agentti_skripti(f'wc -c *.md > {kayttoloki}\necho \'{{"total_cost_usd": 0, "result": "ok"}}\'')
        return kayttoloki

    def _koot(self, loki):
        return {r.split()[1]: int(r.split()[0]) for r in loki.read_text().splitlines() if r.strip() and not r.strip().endswith("total")}

    def test_iso_tiedosto_katkaistaan_ja_pieni_jaa_ennalleen(self):
        loki = self._koko_loki()
        kuva = 'python3 -c "print((\'rivi\\n\') * 5000, end=\'\')" > "$0/github.md"; echo pieni > "$0/yojono.md"'
        r = self.aja(TARKKAILU_KUVAKOMENTO=kuva, TARKKAILU_TIEDOSTO_MAX="1000", TARKKAILU_KUVA_MAX="100000")
        self.assertEqual(r.returncode, 0, r.stderr)
        koot = self._koot(loki)
        self.assertLessEqual(koot["github.md"], 1000 + 60)
        self.assertGreater(koot["github.md"], 500)
        self.assertEqual(koot["yojono.md"], len("pieni\n"))

    def test_katkaisu_nakyy_agentille_tiedostossa_eika_katkaise_riveja(self):
        self.agentti_skripti(f'cat github.md > {self.t / "sisalto"}\necho \'{{"total_cost_usd": 0, "result": "ok"}}\'')
        kuva = 'python3 -c "print((\'abcdefghij\\n\') * 500, end=\'\')" > "$0/github.md"'
        r = self.aja(TARKKAILU_KUVAKOMENTO=kuva, TARKKAILU_TIEDOSTO_MAX="105")
        self.assertEqual(r.returncode, 0, r.stderr)
        teksti = (self.t / "sisalto").read_text()
        self.assertTrue(teksti.endswith("(katkaistu: tilannekuva ylitti rajan)\n"), teksti[-80:])
        self.assertEqual(teksti.count("abcdefghij\n"), 9)           # 105 tavua -> 9 taytta rivia (99 tavua), ei puolikasta rivia

    def test_kokonaisraja_rajaa_koko_hakemiston(self):
        loki = self._koko_loki()
        kuva = 'for n in a b c d; do python3 -c "print((\'x\' * 99 + \'\\n\') * 8, end=\'\')" > "$0/$n.md"; done'
        r = self.aja(TARKKAILU_KUVAKOMENTO=kuva, TARKKAILU_TIEDOSTO_MAX="10000", TARKKAILU_KUVA_MAX="1000")
        self.assertEqual(r.returncode, 0, r.stderr)
        koot = self._koot(loki)
        self.assertEqual(set(koot), {"a.md", "b.md", "c.md", "d.md"})
        self.assertLessEqual(sum(koot.values()), 1000 + 4 * 60)    # raja + katkaisumerkit
        self.assertLess(sum(koot.values()), 4 * 800)

    def test_oletusrajat_eivat_koske_tavalliseen_tilannekuvaan(self):
        loki = self._koko_loki()
        r = self.aja()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._koot(loki), {"github.md": len("kuva\n"), "yojono.md": len("x\n")})

    def test_kehote_neuvoo_lukemaan_tiedostot_kerran(self):
        kayttoloki = self.t / "kutsut"
        self.agentti_skripti(f'echo "$@" > {kayttoloki}\necho \'{{"total_cost_usd": 0, "result": "ok"}}\'')
        self.assertEqual(self.aja().returncode, 0)
        self.assertIn("Lue jokainen tiedosto kerran", kayttoloki.read_text())

    def test_skripti_on_ascii_ja_ei_anna_agentille_kirjoitusoikeutta(self):
        teksti = SKRIPTI.read_bytes()
        teksti.decode("ascii")
        s = teksti.decode()
        self.assertNotIn("acceptEdits", s)
        self.assertIn('"Bash" "Edit" "Write"', s)


if __name__ == "__main__":
    unittest.main()
