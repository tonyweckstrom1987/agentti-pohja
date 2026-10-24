import json
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from agentti import snapshot_kutsu as sk
from isanta import snapshot_isanta as si

LISTAUS = (
    "`-> serti-v1                    2026-10-11 08:55:07     SERTI-valmentaja v1 toimii\n"
    " `-> ennen-ideapankki          2026-10-17 18:38:12     no-description\n"
    "  `-> current                                      You are here!\n"
)


def pct_feikki(listaus=LISTAUS, koodi=0, virhe=""):
    def f(komento, capture_output=True, text=True, timeout=None):
        f.kutsut.append(komento)
        return subprocess.CompletedProcess(komento, koodi, listaus, virhe)
    f.kutsut = []
    return f


class IsantaTesti(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.loki = os.path.join(self.tmp.name, "loki")

    def test_sallitut_komennot(self):
        self.assertEqual(si.jasenna("listaa"), ("listaa", None))
        self.assertEqual(si.jasenna("snapshot ennen-halytin"), ("snapshot", "ennen-halytin"))

    def test_tila_on_sallittu_vain_ilman_argumentteja(self):
        self.assertEqual(si.jasenna("tila"), ("tila", None))
        for k in ("tila 111", "tila; id", "tila $(id)", "tilaa"):
            with self.subTest(k=k):
                with self.assertRaises(si.Virhe):
                    si.jasenna(k)

    def test_tila_ajaa_vain_kiintean_listan_eika_muuta(self):
        kutsut = []
        PCT_LIST = "VMID Status Lock Name\n111 running litellm\n113 stopped gitea\n115 running agentscope\n"

        def f(komento, capture_output=True, text=True, timeout=None):
            kutsut.append(komento)
            ulos = PCT_LIST if komento == ["pct", "list"] else "ok"
            return subprocess.CompletedProcess(komento, 0, ulos, "")
        t = si.aja("tila", suorita=f, loki=self.loki)
        self.assertTrue(t["ok"])
        self.assertIn("111 running litellm", t["tila"])
        self.assertIn("## CT111 epaonnistuneet palvelut", t["tila"])
        self.assertIn("## CT115 epaonnistuneet palvelut", t["tila"])
        self.assertNotIn("CT113", t["tila"])  # pysaytettyyn konttiin ei kosketa
        for k in kutsut:
            self.assertNotIn(k[:2], (["pct", "snapshot"], ["pct", "rollback"], ["pct", "delsnapshot"], ["pct", "stop"], ["pct", "start"]))
        self.assertIn(["pct", "exec", "111", "--", "systemctl", "list-units", "--failed", "--no-legend", "--no-pager"], kutsut)
        self.assertFalse(os.path.exists(self.loki))  # tila ei kirjaa snapshot-ottoja

    def test_tila_suodattaa_lxc_mount_kohinan(self):
        kohina = "* tmp.mount loaded failed failed Temporary Directory\n* dev-mqueue.mount loaded failed failed x\n* run-lock.mount loaded failed failed y"

        def f(komento, capture_output=True, text=True, timeout=None):
            if komento == ["pct", "list"]:
                return subprocess.CompletedProcess(komento, 0, "VMID Status Lock Name\n117 running a\n119 running b\n", "")
            if "systemctl" not in komento:
                return subprocess.CompletedProcess(komento, 0, "ok", "")
            ulos = kohina + "\n* oma.service loaded failed failed Oma" if "119" in komento else kohina
            return subprocess.CompletedProcess(komento, 0, ulos, "")
        t = si.aja("tila", suorita=f, loki=self.loki)["tila"]
        self.assertIn("(ei epaonnistuneita palveluita)", t)
        self.assertIn("oma.service", t)
        self.assertNotIn("tmp.mount", t)

    def test_tila_yksittainen_virhe_ei_kaada(self):
        def f(komento, capture_output=True, text=True, timeout=None):
            if komento[0] == "pvesm":
                raise OSError("puuttuu")
            return subprocess.CompletedProcess(komento, 0, "VMID Status Lock Name\n", "")
        t = si.aja("tila", suorita=f, loki=self.loki)
        self.assertIn("(ei saatavilla)", t["tila"])

    def test_kielletyt_komennot(self):
        for k in ("", "rollback ennen-xy", "snapshot", "snapshot ennen-a b", "snapshot muu-nimi",
                  "snapshot ennen-", "snapshot ennen-A", "snapshot ennen-x; id", "snapshot $(id)",
                  "snapshot ennen-" + "a" * 60, "delsnapshot ennen-xy", "listaa; id", "listaa 115"):
            with self.subTest(k=k):
                with self.assertRaises(si.Virhe):
                    si.jasenna(k)

    def test_nimet_jattaa_currentin_pois(self):
        self.assertEqual(si.nimet(LISTAUS), ["serti-v1", "ennen-ideapankki"])

    def test_listaa(self):
        f = pct_feikki()
        t = si.aja("listaa", suorita=f, loki=self.loki)
        self.assertEqual(t["snapshotit"], ["serti-v1", "ennen-ideapankki"])
        self.assertEqual(f.kutsut, [["pct", "listsnapshot", "115"]])

    def test_snapshot_kayttaa_vain_ct115(self):
        f = pct_feikki()
        t = si.aja("snapshot ennen-uusi", suorita=f, loki=self.loki)
        self.assertEqual(t, {"ok": True, "snapshot": "ennen-uusi"})
        self.assertEqual(f.kutsut[-1], ["pct", "snapshot", "115", "ennen-uusi", "--description", "CT115-pyynto"])

    def test_olemassa_oleva_nimi_hylataan(self):
        with self.assertRaises(si.Virhe):
            si.aja("snapshot ennen-ideapankki", suorita=pct_feikki(), loki=self.loki)

    def test_snapshotien_enimmaismaara(self):
        paljon = "".join("`-> ennen-x%d  2026-10-11  d\n" % i for i in range(si.MAX_SNAPSHOTIA))
        f = pct_feikki(listaus=paljon)
        with self.assertRaises(si.Virhe):
            si.aja("snapshot ennen-uusi", suorita=f, loki=self.loki)
        self.assertEqual(len(f.kutsut), 1)            # vain listaus, ei ottoa

    def test_paivan_raja(self):
        nyt = time.localtime()
        paiva = time.strftime("%Y-%m-%d", nyt)
        with open(self.loki, "w") as f:
            f.write("%s snapshot ok snapshot ennen-a\n" % paiva * si.MAX_PAIVASSA)
        with self.assertRaises(si.Virhe):
            si.aja("snapshot ennen-uusi", suorita=pct_feikki(), loki=self.loki, nyt=nyt)

    def test_virheelliset_otot_eivat_kuluta_paivan_rajaa(self):
        nyt = time.localtime()
        paiva = time.strftime("%Y-%m-%d", nyt)
        with open(self.loki, "w") as f:
            f.write("%s snapshot virhe snapshot ennen-a\n" % paiva * 50)
        t = si.aja("snapshot ennen-uusi", suorita=pct_feikki(), loki=self.loki, nyt=nyt)
        self.assertTrue(t["ok"])

    def test_pct_virhe_nakyy(self):
        with self.assertRaises(si.Virhe) as e:
            si.aja("listaa", suorita=pct_feikki(koodi=1, virhe="boom"), loki=self.loki)
        self.assertIn("boom", str(e.exception))

    def test_main_kirjaa_ja_palauttaa_koodit(self):
        loki = self.loki
        lukko = os.path.join(self.tmp.name, "lukko")
        with mock.patch.object(si, "LOKI", loki), mock.patch.object(si, "LUKKO", lukko), \
                mock.patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "rm -rf /"}), \
                mock.patch("builtins.print") as pr:
            koodi = si.main()
        self.assertEqual(koodi, 1)
        self.assertFalse(json.loads(pr.call_args[0][0])["ok"])
        self.assertIn("muu virhe", open(loki).read())


def vastaus(tulos, koodi=0):
    def feikki(komento, capture_output=True, timeout=None):
        feikki.kutsut.append(komento)
        return subprocess.CompletedProcess(komento, koodi, json.dumps(tulos).encode(), b"")
    feikki.kutsut = []
    return feikki


class AsiakasTesti(unittest.TestCase):
    def test_ota_lahettaa_oikean_komennon(self):
        f = vastaus({"ok": True, "snapshot": "ennen-xy"})
        with mock.patch.object(sk.subprocess, "run", f):
            t = sk.ota("ennen-xy")
        self.assertTrue(t["ok"])
        self.assertEqual(f.kutsut[0][-1], "snapshot ennen-xy")
        self.assertIn("BatchMode=yes", f.kutsut[0])

    def test_huono_nimi_ei_lahde_ssh_lle(self):
        f = vastaus({"ok": True})
        with mock.patch.object(sk.subprocess, "run", f):
            for n in ("x", "ennen-x; id", "ennen-", "ENNEN-x", "ennen-a b"):
                with self.subTest(n=n):
                    with self.assertRaises(sk.SnapshotVirhe):
                        sk.ota(n)
        self.assertEqual(f.kutsut, [])

    def test_ei_json(self):
        def f(komento, capture_output=True, timeout=None):
            return subprocess.CompletedProcess(komento, 255, b"", b"Permission denied")
        with mock.patch.object(sk.subprocess, "run", f):
            with self.assertRaises(sk.SnapshotVirhe) as e:
                sk.listaa()
        self.assertIn("Permission denied", str(e.exception))

    def test_aikakatko(self):
        def f(komento, capture_output=True, timeout=None):
            raise subprocess.TimeoutExpired(komento, timeout)
        with mock.patch.object(sk.subprocess, "run", f):
            with self.assertRaises(sk.SnapshotVirhe):
                sk.listaa()

    def test_cli(self):
        with mock.patch.object(sk.subprocess, "run", vastaus({"ok": True, "snapshot": "ennen-xy"})):
            with mock.patch("builtins.print") as pr:
                self.assertEqual(sk.main(["ota", "ennen-xy"]), 0)
        self.assertIn("ennen-xy", pr.call_args[0][0])
        with mock.patch.object(sk.subprocess, "run", vastaus({"ok": False, "virhe": "raja"})):
            with mock.patch("builtins.print") as pr:
                self.assertEqual(sk.main(["listaa"]), 1)
        self.assertIn("raja", pr.call_args[0][0])
        with mock.patch("builtins.print"):
            self.assertEqual(sk.main(["muuta"]), 2)


if __name__ == "__main__":
    unittest.main()
