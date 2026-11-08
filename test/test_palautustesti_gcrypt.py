"""Testit isanta/palautustesti-gcrypt.sh: rclone-, curl- ja zstd-valejaljitelmat (ei verkkoa, ei oikeaa Drivea)."""
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

LINUX = sys.platform.startswith("linux") and all(shutil.which(x) for x in ("bash", "flock", "gzip", "tar"))
JUURI = Path(__file__).resolve().parent.parent
SKRIPTI = JUURI / "isanta" / "palautustesti-gcrypt.sh"

# listremotes -> crypt; lsf -> $VALE_LISTA; copyto lahde kohde -> kopioi $VALE_ETA/<polku>. Kaikki kutsut kirjataan.
RCLONE = """#!/bin/bash
echo "$*" >> "$VALE_LOKI"
case "$1" in
  listremotes) echo "${VALE_TYYPPI-gcrypt: crypt}"; exit 0 ;;
  lsf) cat "$VALE_LISTA"; exit ${VALE_LSF_KOODI:-0} ;;
  copyto)
    lahde="${@: -2:1}"; kohde="${@: -1}"
    cp "$VALE_ETA/${lahde#*:}" "$kohde"; exit $? ;;
esac
exit 0
"""
# curl: kirjaa argumentit ja stdinin (jos -K -)
CURL = """#!/bin/bash
echo "ARGS: $*" >> "$CURL_LOKI"
case " $* " in *" -K - "*) cat >> "$CURL_LOKI.stdin" ;; esac
exit ${CURL_KOODI:-0}
"""
# zstd-valejaljitelma: testiarkistot ovat gzip-paketteja .zst-nimella
ZSTD = """#!/bin/bash
case "$1" in
  -t) gzip -t "${@: -1}" ;;
  -dc) gzip -dc "${@: -1}" ;;
  *) exit 2 ;;
esac
"""
TOKEN = "TESTITUNNUS123"


def tee_arkisto(polku, sisalto=b"data"):
    polku.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(polku, "w:gz") as t:
        tiedosto = polku.parent / "_sisalto"
        tiedosto.write_bytes(sisalto)
        t.add(tiedosto, arcname="rootfs/data.txt")
        tiedosto.unlink()


def suoritettava(polku, teksti):
    polku.write_text(teksti)
    polku.chmod(polku.stat().st_mode | stat.S_IXUSR)


@unittest.skipUnless(LINUX, "vaatii Linuxin, flockin, gzipin ja tarin")
class Pohja(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.bin = t / "bin"; self.bin.mkdir()
        suoritettava(self.bin / "rclone", RCLONE)
        suoritettava(self.bin / "curl", CURL)
        suoritettava(self.bin / "zstd", ZSTD)
        self.eta = t / "eta"; self.eta.mkdir()
        self.tmpjuuri = t / "tmpjuuri"; self.tmpjuuri.mkdir()
        self.vieras = self.tmpjuuri / "palautustesti.vieras"; self.vieras.mkdir()
        (self.vieras / "tiedosto").write_text("ei saa kadota")
        self.lisaa("dump/vzdump-lxc-114-2026_10_01-02_30_00.tar.zst")
        self.lisaa("dump/vzdump-lxc-114-2026_11_05-02_30_00.tar.zst")      # uusin kontille 114
        self.lisaa("dump/vzdump-lxc-114-2026_11_06-02_30_00.log", raaka=True)  # ei arkisto
        self.lisaa("dump/vzdump-lxc-111-2026_11_06-02_30_00.tar.zst")      # toinen kontti, uudempi
        self.vale_loki = t / "rclone.log"; self.curl_loki = t / "curl.log"
        self.env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}", "REMOTE": "gcrypt:", "KONTTI": "114",
            "RCLONE": "rclone", "LOKI": str(t / "ajo.log"), "LUKKO": str(t / "lukko"),
            "TMPJUURI": str(self.tmpjuuri), "ASETUS_TIEDOSTO": str(t / "ei-ole.env"),
            "RCLONE_CONFIG": str(t / "ei-ole.conf"), "VALE_LOKI": str(self.vale_loki),
            "VALE_LISTA": str(t / "lista.txt"), "VALE_ETA": str(self.eta), "CURL_LOKI": str(self.curl_loki),
            "KUMA_PUSH_URL": "https://kuma.example/api/push/avain", "TELEGRAM_BOT_TOKEN": TOKEN,
            "TELEGRAM_CHAT_ID": "42", "HOME": str(t),
        }
        self.kirjoita_lista()

    def tearDown(self):
        self.tmp.cleanup()

    def lisaa(self, suhteellinen, raaka=False, sisalto=b"data"):
        p = self.eta / suhteellinen
        if raaka:
            p.parent.mkdir(parents=True, exist_ok=True); p.write_text("loki")
        else:
            tee_arkisto(p, sisalto)

    def kirjoita_lista(self, koot=None):
        """Valekohteen listaus muodossa koko;polku; `koot` korvaa kokoja polun mukaan."""
        rivit = []
        for p in sorted(self.eta.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(self.eta))
                rivit.append(f"{(koot or {}).get(rel, p.stat().st_size)};{rel}")
        Path(self.env["VALE_LISTA"]).write_text("\n".join(rivit) + "\n")

    def aja(self, *args, **env):
        e = dict(self.env); e.update(env)
        return subprocess.run(["bash", str(SKRIPTI), *args], env=e, capture_output=True, text=True, timeout=60)

    def rclone_kutsut(self):
        return self.vale_loki.read_text().splitlines() if self.vale_loki.exists() else []

    def curl_kutsut(self):
        return self.curl_loki.read_text().splitlines() if self.curl_loki.exists() else []


class Onnistuminen(Pohja):
    def test_valitsee_kontin_uusimman_arkiston_ja_ilmoittaa_kumaan(self):
        r = self.aja()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("vzdump-lxc-114-2026_11_05-02_30_00.tar.zst", r.stdout)
        copyto = [x for x in self.rclone_kutsut() if x.startswith("copyto")]
        self.assertEqual(len(copyto), 1)
        self.assertIn("dump/vzdump-lxc-114-2026_11_05-02_30_00.tar.zst", copyto[0])
        kutsut = self.curl_kutsut()
        self.assertEqual(len(kutsut), 1, kutsut)
        self.assertIn("status=up", kutsut[0])
        self.assertFalse(any("telegram" in k for k in kutsut), "onnistuminen ei saa halyttaa Telegramiin")

    def test_vain_luku_kohteeseen(self):
        self.aja()
        for rivi in self.rclone_kutsut():
            self.assertIn(rivi.split()[0], ("listremotes", "lsf", "copyto"), rivi)
            self.assertNotRegex(rivi, r"^(delete|deletefile|purge|sync|move|moveto|copy|rmdirs|cleanup)\b")

    def test_siivoaa_vain_oman_kansionsa(self):
        self.aja()
        self.assertEqual(sorted(p.name for p in self.tmpjuuri.iterdir()), ["palautustesti.vieras"])
        self.assertEqual((self.vieras / "tiedosto").read_text(), "ei saa kadota")

    def test_juuressa_olevat_arkistot_ilman_alihakemistoa(self):
        shutil.rmtree(self.eta / "dump")
        self.lisaa("vzdump-lxc-114-2026_11_05-02_30_00.tar.zst"); self.kirjoita_lista()
        r = self.aja()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_luettelo_ei_lataa_eika_ilmoita(self):
        r = self.aja("--luettelo")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VALITTAISIIN vzdump-lxc-114-2026_11_05", r.stdout)
        self.assertFalse([x for x in self.rclone_kutsut() if x.startswith("copyto")])
        self.assertEqual(self.curl_kutsut(), [])


class Virheet(Pohja):
    def assert_epaonnistui(self, r, osa):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(osa, r.stdout)
        kutsut = self.curl_kutsut()
        self.assertTrue(any("status=down" in k for k in kutsut), kutsut)
        self.assertTrue(any("api.telegram.org" not in k and "-K -" in k for k in kutsut), "Telegram-kutsu puuttuu")
        self.assertEqual(sorted(p.name for p in self.tmpjuuri.iterdir()), ["palautustesti.vieras"])   # siivous myos virheessa

    def test_koko_ei_tasmaa(self):
        self.kirjoita_lista({"dump/vzdump-lxc-114-2026_11_05-02_30_00.tar.zst": 999999})
        self.assert_epaonnistui(self.aja(), "koko ei tasmaa")

    def test_rikkinainen_arkisto(self):
        p = self.eta / "dump/vzdump-lxc-114-2026_11_05-02_30_00.tar.zst"
        p.write_bytes(b"ei ole pakattu arkisto"); self.kirjoita_lista()
        self.assert_epaonnistui(self.aja(), "zstd -t epaonnistui")

    def test_kontin_arkistoa_ei_loydy(self):
        self.assert_epaonnistui(self.aja(KONTTI="999"), "ei loytynyt kontin 999 arkistoa")

    def test_ei_crypt_kohde(self):
        r = self.aja(VALE_TYYPPI="gcrypt: drive")
        self.assert_epaonnistui(r, "ei ole crypt-tyyppinen")
        self.assertFalse([x for x in self.rclone_kutsut() if x.startswith("copyto")])

    def test_listaus_epaonnistuu(self):
        self.assert_epaonnistui(self.aja(VALE_LSF_KOODI="3"), "listaus kohteesta epaonnistui")

    def test_liian_suuri_ei_ladata(self):
        r = self.aja(MAX_KOKO="10")
        self.assert_epaonnistui(r, "liian suuri")
        self.assertFalse([x for x in self.rclone_kutsut() if x.startswith("copyto")])

    def test_tyhja_arkisto_hylataan(self):
        self.kirjoita_lista({"dump/vzdump-lxc-114-2026_11_05-02_30_00.tar.zst": 0})
        self.assert_epaonnistui(self.aja(), "koko on 0")

    def test_rclone_conf_liian_avoin(self):
        conf = Path(self.env["RCLONE_CONFIG"]); conf.write_text("[x]\n"); conf.chmod(0o644)
        self.assert_epaonnistui(self.aja(), "600")

    def test_telegram_tunnus_ei_nay_argumenteissa_eika_tulosteessa(self):
        self.kirjoita_lista({"dump/vzdump-lxc-114-2026_11_05-02_30_00.tar.zst": 1})
        r = self.aja()
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertFalse(any(TOKEN in k for k in self.curl_kutsut()))
        self.assertIn(TOKEN, Path(str(self.curl_loki) + ".stdin").read_text())   # tunnus kulkee vain stdinin kautta

    def test_ilman_telegramia_kuma_down_riittaa(self):
        self.kirjoita_lista({"dump/vzdump-lxc-114-2026_11_05-02_30_00.tar.zst": 1})
        r = self.aja(TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.curl_kutsut()), 1)

    def test_kuma_pingin_virhe_ei_kaada_onnistunutta_ajoa(self):
        r = self.aja(CURL_KOODI="22")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VAROITUS", r.stdout)


class Rakenne(unittest.TestCase):
    def test_ajastinmalli_kuukausittain_ja_ascii(self):
        t = (JUURI / "systemd" / "palautustesti-gcrypt.timer.mall").read_text()
        self.assertIn("OnCalendar=*-*-01 ", t)
        self.assertIn("Persistent=true", t)
        (JUURI / "systemd" / "palautustesti-gcrypt.service.mall").read_bytes().decode("ascii")
        t.encode("ascii")

    def test_skripti_ei_sisalla_poistoja_kohteeseen(self):
        s = SKRIPTI.read_text()
        for kielletty in ("rclone delete", '"$RCLONE" delete', '"$RCLONE" sync', '"$RCLONE" purge', '"$RCLONE" move'):
            self.assertNotIn(kielletty, s)
        koodi = [r for r in s.splitlines() if not r.lstrip().startswith("#")]
        self.assertEqual(sum("rm -rf" in r for r in koodi), 1)   # ainoa poisto on poista_oma_kansio


if __name__ == "__main__":
    unittest.main()
