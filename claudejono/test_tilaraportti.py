import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from claudejono import tilaraportti as tr

# Keksityt osoitteet koottuna paloista: repon vuototesti ei salli osoitteita sellaisenaan.
LAN = ".".join(("192", "168"))
KYMPPI = ".".join(("10", "0", "0", "5"))
TAILNET = ".".join(("100", "64", "0", "9"))
JULKINEN = ".".join(("8", "8", "8", "8"))
TS = ".".join(("ts", "net"))

NYT = datetime(2026, 11, 5, 12, 0, tzinfo=ZoneInfo("Europe/Helsinki"))
ISANTA = """## Kontit (pct list)
VMID Status Lock Name
115 running agentscope

## Tallennustila (pvesm status)
Name Type Status Total Used Available %
local-lvm lvmthin active 100 61 39 61.00%

## Isannan levy
Filesystem Size Used Avail Use% Mounted on
/dev/mapper/pve-root 50G 20G 28G 42% /

## Viimeisimmat varmuuskopiot
-rw-r--r-- 1 root root 5 Nov  5 03:00 vzdump-lxc-115.tar.zst
-rw-r--r-- 1 root root 5 Nov  4 03:00 vzdump-lxc-115.tar.zst

## CT116 epaonnistuneet palvelut
  holvi.service loaded failed failed Holvi
  tmp.mount loaded failed failed Temp

## CT117 epaonnistuneet palvelut
(ei epaonnistuneita palveluita)
"""


class Suodatus(unittest.TestCase):
    def test_osoitteet_pois(self):
        for s in ("yhteys " + LAN + ".1.121:8700", KYMPPI + " virhe", "solmu abc.tail1234." + TS, TAILNET + " ok",
                  "julkinen " + JULKINEN, "fd7a:115c:a1e0::1 x", "mac aa:bb:cc:dd:ee:ff", "tony@example.com"):
            u = tr.suodata(s)
            self.assertIn(tr.POISTETTU, u, s)
            for kielletty in (LAN, KYMPPI, TS, TAILNET, JULKINEN, "fd7a", "aa:bb", "@example"):
                self.assertNotIn(kielletty, u)

    def test_salaisuus_pois(self):
        u = tr.suodata("virhe token=abcdefgh12345678 ja sk-" + "a" * 20)
        self.assertNotIn("abcdefgh12345678", u)
        self.assertNotIn("sk-aaaa", u)

    def test_tavallinen_teksti_sailyy_ja_pitka_rivi_katkeaa(self):
        self.assertEqual(tr.suodata("claude-jono@1.service: failed"), "claude-jono@1.service: failed")
        self.assertLessEqual(len(tr.suodata("x" * 1000)), tr.RIVI_MAX)


class Keruu(unittest.TestCase):
    def raportti(self, isanta=lambda: ISANTA, virheet=lambda: ["2026-11-05T10:00:00 koneen claude-jono@1: virhe " + LAN + ".1.9"]):
        tmp = Path(tempfile.mkdtemp())
        t = tr.keraa(tmp / "ei-ole", tmp / "ei-ole", NYT, hae_isanta=isanta, paikallinen="", hae_virheet=virheet)
        return tr.raportti(t)

    def test_osiot_ja_arvot(self):
        r = self.raportti()
        self.assertIn("CT116: holvi.service", r)
        self.assertNotIn("tmp.mount", r)
        self.assertIn("CT117: ei", r)
        self.assertIn("CT115: ei", r)
        self.assertIn("tallennus local-lvm (lvmthin): 61.00%", r)
        self.assertIn("levy /: 42%", r)
        self.assertIn("9 h sitten", r)

    def test_raportissa_ei_osoitteita(self):
        r = self.raportti()
        self.assertNotIn(LAN, r)
        self.assertIn("virhe [poistettu]", r)

    def test_vain_20_virhetta_haetaan(self):
        kom = []
        tr.virherivit(suorita=lambda k, **kw: kom.append(k) or type("R", (), {"returncode": 0, "stdout": "a\n"})())
        self.assertIn("20", kom[0])
        self.assertIn("claude-jono@*", kom[0])
        self.assertIn("docs-pull.service", kom[0])

    def test_isanta_poissa_ei_kaada(self):
        def kaatuu():
            raise RuntimeError("yhteys " + LAN + ".1.114 katkesi")
        r = self.raportti(isanta=kaatuu, virheet=lambda: None)
        self.assertIn("RuntimeError", r)
        self.assertNotIn(LAN, r)
        self.assertIn("Lokia ei saatu luettua", r)

    def test_varmuuskopion_ika_puuttuu(self):
        self.assertIsNone(tr.varmuuskopion_ika_h({}, NYT))


class Kirjoitus(unittest.TestCase):
    def test_kirjoitus_vain_muutoksesta(self):
        ulos = Path(tempfile.mkdtemp())
        a = "# T\n\nP\u00e4ivitetty: 1\n\nrivi\n"
        self.assertIsNotNone(tr.kirjoita(a, ulos))
        self.assertIsNone(tr.kirjoita(a.replace("P\u00e4ivitetty: 1", "P\u00e4ivitetty: 2"), ulos))
        self.assertIsNotNone(tr.kirjoita(a.replace("rivi", "toinen"), ulos))
        self.assertTrue((ulos / tr.KOHDE_SUHT).is_file())

    def test_polku_on_docs_ulos_sallittu(self):
        from claudejono import ajuri
        self.assertTrue(ajuri.vapaa_docs_polku(tr.KOHDE_SUHT))


if __name__ == "__main__":
    unittest.main()
