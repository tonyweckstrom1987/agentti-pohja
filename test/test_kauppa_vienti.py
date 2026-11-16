"""Testit isanta/kauppa-vienti.cron ja isanta/kauppa-vienti.sh (Tonyn paatos 9.11.2026, A915). Ei verkkoa, ei isantaa."""
import hashlib
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
CRON = JUURI / "isanta" / "kauppa-vienti.cron"
SKRIPTI = JUURI / "isanta" / "kauppa-vienti.sh"
OHJE = JUURI / "isanta" / "KAUPPA-VIENTI.md"
# Docs-repon isanta-kasin/kauppa-vienti.sh (versio 2). Muutos tehdaan tarkoituksella: paivita tiiviste ja docs-repo yhdessa.
SKRIPTI_SHA256 = "dd9fde05847a66d6cb07c25da204f0c7421d3e95e368841624bf404df43975b4"
DOCS_KOPIOT = (
    "/opt/agenttifirma-docs/claude-jono/isanta-kasin/kauppa-vienti.sh",
    "/var/lib/claude-jono/docs/claude-jono/isanta-kasin/kauppa-vienti.sh",
)
KOMENTO = "sh /usr/local/sbin/kauppa-vienti.sh >>/var/log/kauppa-vienti.log 2>&1"


def cron_rivit():
    return [r for r in CRON.read_text().splitlines() if r.strip() and not r.lstrip().startswith("#")]


class CronRivi(unittest.TestCase):
    def test_yksi_ajastusrivi(self):
        self.assertEqual(len(cron_rivit()), 1)

    def test_muoto_kuusi_kenttaa_ja_komento(self):
        osat = cron_rivit()[0].split(None, 6)
        self.assertEqual(len(osat), 7, "5 aikakenttaa + kayttaja + komento")
        minuutti, tunti, kk_paiva, kuu, vk_paiva, kayttaja, komento = osat
        self.assertEqual((minuutti, tunti, kk_paiva, kuu, vk_paiva), ("47", "10", "*", "*", "*"))
        self.assertEqual(kayttaja, "root")
        self.assertEqual(komento, KOMENTO)

    def test_aikakentat_ovat_kelvollisia(self):
        minuutti, tunti = cron_rivit()[0].split()[:2]
        self.assertTrue(0 <= int(minuutti) <= 59 and 0 <= int(tunti) <= 23)

    def test_ei_osu_lauantain_kello_10_ajoon(self):
        osat = cron_rivit()[0].split()
        self.assertNotEqual((osat[0], osat[1]), ("0", "10"))
        self.assertEqual(osat[4], "*")

    def test_ei_ip_osoitteita_julkisessa_repossa(self):
        for polku in (CRON, SKRIPTI, OHJE):
            self.assertIsNone(re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", polku.read_text()), polku.name)

    def test_cron_ei_pura_ymparistomuuttujia(self):
        # cron.d-tiedostossa SHELL/PATH-rivit muuttaisivat muiden rivien ymparistoa: tama tiedosto sisaltaa vain yhden rivin
        for r in CRON.read_text().splitlines():
            self.assertFalse(re.match(r"^[A-Z_]+=", r), r)


class Skripti(unittest.TestCase):
    def test_tiiviste_vastaa_vahvistettua_versiota(self):
        self.assertEqual(hashlib.sha256(SKRIPTI.read_bytes()).hexdigest(), SKRIPTI_SHA256)

    def test_identtinen_docs_repon_kanssa_kun_saatavilla(self):
        loydetty = [p for p in DOCS_KOPIOT if os.path.isfile(p)]
        if not loydetty:
            self.skipTest("docs-repon kopiota ei talla koneella")
        for p in loydetty:
            self.assertEqual(Path(p).read_bytes(), SKRIPTI.read_bytes(), p)

    def test_suoritusoikeus_ja_syntaksi(self):
        self.assertTrue(os.access(SKRIPTI, os.X_OK))
        sh = shutil.which("sh")
        if not sh:
            self.skipTest("sh puuttuu")
        self.assertEqual(subprocess.run([sh, "-n", str(SKRIPTI)]).returncode, 0)

    def test_cron_viittaa_skriptin_asennuspolkuun(self):
        self.assertIn("/usr/local/sbin/kauppa-vienti.sh", cron_rivit()[0])


class Ohje(unittest.TestCase):
    def test_asennuskomento_viittaa_olemassa_oleviin_tiedostoihin(self):
        teksti = OHJE.read_text()
        komento = [r for r in teksti.splitlines() if r.startswith("install ")]
        self.assertEqual(len(komento), 1)
        for lahde in re.findall(r"(isanta/kauppa-vienti\.\w+)", komento[0]):
            self.assertTrue((JUURI / lahde).is_file(), lahde)
        self.assertIn("/etc/cron.d/kauppa-vienti", komento[0])
        self.assertIn("-m 644", komento[0])

    def test_ohje_kertoo_siirtymavaroituksen(self):
        self.assertIn("KAUPPA_MAX_IKA_H", OHJE.read_text())
        self.assertIn("28,5", OHJE.read_text())

    def test_ohjeen_tiiviste_tasmaa(self):
        self.assertIn(SKRIPTI_SHA256[:12], OHJE.read_text())


if __name__ == "__main__":
    unittest.main()
