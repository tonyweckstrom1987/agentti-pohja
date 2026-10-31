"""hiekkalaatikko/: hl.py (CT-valin tarkistus, kustannusvaraus, loki), litellm-adapteri ja isantaskriptit.

Isantaskriptit ajetaan vaaralla pct:lla (kirjaa kutsut tiedostoon), joten mitaan oikeaa kontia, verkkoa tai isantaa ei kosketa.
Linux + bash + timeout + flock vaaditaan."""
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

JUURI = Path(__file__).resolve().parent.parent
HL_HAKEMISTO = JUURI / "hiekkalaatikko"
LINUX = sys.platform.startswith("linux") and all(shutil.which(x) for x in ("bash", "timeout", "flock"))


def lataa(nimi, polku):
    spec = importlib.util.spec_from_file_location(nimi, polku)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


hl = lataa("hl_testi", HL_HAKEMISTO / "hl.py")
litellm = lataa("hl_litellm_testi", HL_HAKEMISTO / "adapterit" / "litellm.py")


def rivi(**yli):
    r = {"id": "a", "aika": "2026-10-31T10:00:00Z", "kokeilu": "turva-01", "adapteri": "kuiva", "malli": "m",
         "kesto_s": 10, "kustannus_usd": 0.01, "lopputulos": "onnistui", "rajarikkeet": [], "lokit": "/x"}
    r.update(yli)
    return r


class CtValiTesti(unittest.TestCase):
    def test_sallitut_rajat(self):
        for n in (9100, 9101, 9199, "9100", "9199"):
            self.assertTrue(hl.ct_id_sallittu(n), n)

    def test_kielletyt(self):
        tuotanto = [110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 211]
        muut = [0, 1, 100, 9000, 9001, 9099, 9200, 99999, -9100, "9099", "9200", "", " 9100", "9100 ", "9100;ls", "09100",
                "9100\n", "9e3", "9100.0", "-9100", None, 9100.0, True, [9100], "\uff19\uff11\uff10\uff10"]
        for n in tuotanto + muut:
            self.assertFalse(hl.ct_id_sallittu(n), repr(n))

    def test_nimet(self):
        self.assertTrue(hl.nimi_sallittu("turva-01"))
        for n in ("", "-a", "A", "a b", "a/b", "../x", "a;b", "a" * 42, None, "a\n"):
            self.assertFalse(hl.nimi_sallittu(n), repr(n))


class VarausTesti(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def test_paivakatto_ja_tasaus(self):
        t = lambda: 1_800_000_000
        a = hl.varaa(self.d, 0.5, 1.0, 10, 600, kello=t)
        b = hl.varaa(self.d, 0.5, 1.0, 10, 600, kello=lambda: 1_800_000_001)
        with self.assertRaises(hl.Virhe) as e:
            hl.varaa(self.d, 0.5, 1.0, 10, 600, kello=lambda: 1_800_000_002)
        self.assertEqual(str(e.exception), "kulu_paiva")
        hl.tasaa(self.d, a, 0.0)  # todellinen kulu vapauttaa varauksen
        hl.varaa(self.d, 0.5, 1.0, 10, 600, kello=lambda: 1_800_000_003)
        self.assertNotEqual(a, b)

    def test_tasaus_none_jattaa_pessimistisen_varauksen(self):
        a = hl.varaa(self.d, 0.5, 1.0, 10, 600, kello=lambda: 1_800_000_000)
        hl.tasaa(self.d, a, None)
        hl.varaa(self.d, 0.5, 1.0, 10, 600, kello=lambda: 1_800_000_001)
        with self.assertRaises(hl.Virhe):
            hl.varaa(self.d, 0.1, 1.0, 10, 600, kello=lambda: 1_800_000_002)

    def test_kokeilumaara(self):
        for i in range(2):
            hl.varaa(self.d, 0.01, 5.0, 2, 600, kello=lambda: 1_800_000_000 + i)
        with self.assertRaises(hl.Virhe) as e:
            hl.varaa(self.d, 0.01, 5.0, 2, 600, kello=lambda: 1_800_000_005)
        self.assertEqual(str(e.exception), "kokeilut_paiva")

    def test_uusi_paiva_nollaa_paivalaskurin(self):
        hl.varaa(self.d, 1.0, 1.0, 10, 600, kello=lambda: 1_800_000_000)
        hl.varaa(self.d, 1.0, 1.0, 10, 600, kello=lambda: 1_800_000_000 + 86400)

    def test_rikkinainen_kirjanpito_pysayttaa(self):
        for sisalto in ("{ei json", "[]", '{"varaukset": {"a": {"paiva": "x"}}}', '{"varaukset": {"a": 1}}'):
            with open(os.path.join(self.d, "kulu.json"), "w") as f:
                f.write(sisalto)
            with self.assertRaises(hl.Virhe, msg=sisalto):
                hl.varaa(self.d, 0.1, 1.0, 10, 600)

    def test_koodin_ylarajoja_ei_voi_ylittaa(self):
        for args in ((hl.ENINT_KOKEILU_USD + 0.01, 1, 5, 60), (0.1, hl.ENINT_PAIVA_USD + 0.01, 5, 60),
                     (0.1, 1, hl.ENINT_KOKEILUA_PAIVA + 1, 60), (0.1, 1, 5, hl.ENINT_AIKA_S + 1),
                     (0, 1, 5, 60), (0.1, 1, 5, 0), (-1, 1, 5, 60)):
            with self.assertRaises(hl.Virhe, msg=args):
                hl.varaa(self.d, *args)
        self.assertFalse(os.path.exists(os.path.join(self.d, "kulu.json")))

    def test_tasaus_hylkaa_huonon_kulun(self):
        a = hl.varaa(self.d, 0.5, 1.0, 10, 600)
        for huono in (-1, float("nan"), "0.1", True):
            with self.assertRaises(hl.Virhe):
                hl.tasaa(self.d, a, huono)
        with self.assertRaises(hl.Virhe):
            hl.tasaa(self.d, "ei-ole", 0.1)


class LokiTesti(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.p = os.path.join(self.d, "kokeilut.jsonl")

    def test_kirjaus_ja_luku(self):
        hl.kirjaa_rivi(self.p, rivi(id="1"))
        hl.kirjaa_rivi(self.p, rivi(id="2", lopputulos="epaonnistui", kustannus_usd=None))
        rs = hl.lue_rivit(self.p)
        self.assertEqual([r["id"] for r in rs], ["1", "2"])
        with open(self.p) as f:
            self.assertEqual(len(f.read().splitlines()), 2)  # yksi rivi per kokeilu

    def test_huonot_rivit_hylataan(self):
        huonot = [rivi(lopputulos="ok"), rivi(kesto_s=-1), rivi(kustannus_usd=-0.1), rivi(kustannus_usd="1"), rivi(aika="eilen"),
                  rivi(kokeilu="../x"), rivi(rajarikkeet="x"), rivi(rajarikkeet=[{"tyyppi": "a"}]), rivi(malli=""),
                  dict(rivi(), ylimaarainen=1), {k: v for k, v in rivi().items() if k != "lokit"}, [], rivi(kesto_s=True)]
        for r in huonot:
            with self.assertRaises(hl.Virhe, msg=r):
                hl.kirjaa_rivi(self.p, r)
        self.assertFalse(os.path.exists(self.p))

    def test_skandit_eivat_rikko_asciita(self):
        hl.kirjaa_rivi(self.p, rivi(malli="m\u00e4"))
        with open(self.p, "rb") as f:
            f.read().decode("ascii")

    def test_rikkinainen_rivi_nostaa_virheen(self):
        with open(self.p, "w") as f:
            f.write("ei json\n")
        with self.assertRaises(hl.Virhe):
            hl.lue_rivit(self.p)

    def test_taulu_ja_eval(self):
        rs = [rivi(id="1", malli="a", kesto_s=10, kustannus_usd=0.02),
              rivi(id="2", malli="a", kesto_s=30, kustannus_usd=0.04, lopputulos="epaonnistui"),
              rivi(id="3", malli="a", lopputulos="keskeytetty", kustannus_usd=None,
                   rajarikkeet=[{"tyyppi": "estetty_verkkoyhteys", "maara": 3, "kohteet": ["x"]}]),
              rivi(id="4", malli="b", kustannus_usd=None)]
        t = hl.taulu(rs)
        self.assertIn("| turva-01 | kuiva | a | 3 | 1/2 | 1 | 10 | 0.0300 | 3 |", t)
        self.assertIn("| turva-01 | kuiva | b | 1 | 1/1 | 0 | 10 | ? | 0 |", t)
        e = [json.loads(x) for x in hl.eval_vienti(rs).splitlines()]
        self.assertEqual([x["ajo_id"] for x in e], ["1", "2", "4"])  # keskeytetty pois
        self.assertEqual([x["onnistui"] for x in e], [True, False, True])
        self.assertEqual(hl.eval_vienti([]), "")


class PaatosTesti(unittest.TestCase):
    def test_taulukko(self):
        ok = {"lopputulos": "onnistui", "kustannus_usd": 0.05, "malli": "m"}
        self.assertEqual(hl.paata(0, ok, None, False), ("onnistui", 0.05, "m"))
        self.assertEqual(hl.paata(0, ok, 1, False)[0], "epaonnistui")  # tarkistus voittaa agentin vaitteen
        self.assertEqual(hl.paata(0, {"lopputulos": "epaonnistui"}, 0, False)[0], "onnistui")  # tarkistus voittaa myos toisin pain
        self.assertEqual(hl.paata(1, ok, 0, False)[0], "epaonnistui")  # adapterin kaatuminen
        self.assertEqual(hl.paata(124, ok, None, True)[0], "keskeytetty")
        self.assertEqual(hl.paata(0, {}, None, False), ("epaonnistui", None, None))
        for huono in (-1, "1", True, float("nan")):
            self.assertIsNone(hl.paata(0, {"kustannus_usd": huono}, None, False)[1])

    def test_yhteenveto(self):
        self.assertEqual(hl.lue_yhteenveto('loki\n{"a": 1}\n\n'), {"a": 1})
        self.assertEqual(hl.lue_yhteenveto("ei json"), {})
        self.assertEqual(hl.lue_yhteenveto("[1]"), {})
        self.assertEqual(hl.lue_yhteenveto(""), {})


class RajarikkeTesti(unittest.TestCase):
    LOKI = ("1000.5 10 10.98.0.10 TCP_DENIED/403 3900 CONNECT evil.example:443 - HIER_NONE/- text/html\n"
            "1001.0 10 10.98.0.10 TCP_DENIED/403 3900 CONNECT evil.example:443 - HIER_NONE/- text/html\n"
            "1002.0 10 10.98.0.10 TCP_DENIED/403 3900 GET http://x.example/salainen?avain=1 - HIER_NONE/- text/html\n"
            "1003.0 10 10.98.0.10 TCP_TUNNEL/200 3900 CONNECT github.com:443 - HIER_DIRECT/1.2.3.4 -\n"
            "1004.0 10 10.98.0.11 TCP_DENIED/403 3900 CONNECT toinen.example:443 - HIER_NONE/- text/html\n"
            "900.0 10 10.98.0.10 TCP_DENIED/403 3900 CONNECT vanha.example:443 - HIER_NONE/- text/html\n"
            "roskaa\n")

    def test_vain_oma_ip_ja_aikaikkuna_ja_denied(self):
        r = hl.rajarikkeet_lokista(self.LOKI, "10.98.0.10", 1000, 1005)
        self.assertEqual(r, [{"tyyppi": "estetty_verkkoyhteys", "maara": 3, "kohteet": ["evil.example:443", "x.example"]}])

    def test_polku_ja_kysely_eivat_paady_lokiin(self):
        self.assertNotIn("salainen", json.dumps(hl.rajarikkeet_lokista(self.LOKI, "10.98.0.10", 1000, 1005)))

    def test_ei_rikkeita(self):
        self.assertEqual(hl.rajarikkeet_lokista("", "10.98.0.10", 0, 10), [])


class LiteLLMTesti(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        kuv = os.path.join(self.d, "k.md")
        with open(kuv, "w") as f:
            f.write("tee jotain")
        self.env = {"HL_LLM_URL": "http://x", "LITELLM_API_KEY": "avain-ei-tulosteeseen", "HL_MALLI": "m", "HL_CT": "9100", "HL_PCT": "pct",
                   "HL_KUVAUS": kuv, "HL_TULOS": self.d, "HL_KUSTANNUSKATTO_USD": "0.10", "HL_AIKAKATTO_S": "600"}

    def vastaukset(self, *viestit, kulu=0.01):
        it = iter(viestit)
        kutsut = []

        def kutsu(url, avain, malli, viestit_):
            kutsut.append(list(viestit_))
            return {"choices": [{"message": next(it)}]}, kulu
        return kutsu, kutsut

    def test_onnistunut_ajo(self):
        tyokalu = {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "kuori", "arguments": json.dumps({"komento": "id"})}}]}
        loppu = {"role": "assistant", "content": "valmis\nTULOS: onnistui"}
        kutsu, kutsut = self.vastaukset(tyokalu, loppu)
        ajetut = []
        y, rc = litellm.aja(self.env, kutsu=kutsu, kuori=lambda *a: ajetut.append(a) or "poistumiskoodi: 0\nuid=0")
        self.assertEqual((y["lopputulos"], y["kustannus_usd"], y["malli"], rc), ("onnistui", 0.02, "m", 0))
        self.assertEqual(ajetut, [("pct", "9100", "id")])
        with open(os.path.join(self.d, "agentti.log")) as f:
            self.assertIn("uid=0", f.read())
        self.assertEqual(kutsut[1][-1]["role"], "tool")

    def test_tuntematon_kulu_antaa_none(self):
        kutsu, _ = self.vastaukset({"role": "assistant", "content": "TULOS: onnistui"}, kulu=None)
        y, _ = litellm.aja(self.env, kutsu=kutsu)
        self.assertIsNone(y["kustannus_usd"])

    def test_kustannuskatto_pysayttaa(self):
        tyokalu = {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "kuori", "arguments": json.dumps({"komento": "ls"})}}]}
        kutsu, kutsut = self.vastaukset(*([tyokalu] * 50), kulu=0.04)
        y, _ = litellm.aja(self.env, kutsu=kutsu, kuori=lambda *a: "x")
        self.assertEqual(len(kutsut), 3)  # 0,04 * 3 >= 0,10
        self.assertEqual(y["lopputulos"], "epaonnistui")

    def test_aikakatto_pysayttaa_ennen_kutsua(self):
        kutsu, kutsut = self.vastaukset()
        y, _ = litellm.aja(self.env, kutsu=kutsu, kello=iter([0, 10_000]).__next__)
        self.assertEqual(kutsut, [])

    def test_puuttuva_ymparisto(self):
        del self.env["LITELLM_API_KEY"]
        y, rc = litellm.aja(self.env)
        self.assertEqual((y["lopputulos"], rc), ("epaonnistui", 64))

    def test_virheellinen_tyokalukutsu_ei_kaada(self):
        huono = {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "function": {"name": "kuori", "arguments": "ei json"}}]}
        kutsu, _ = self.vastaukset(huono, {"role": "assistant", "content": "TULOS: epaonnistui"})
        y, _ = litellm.aja(self.env, kutsu=kutsu, kuori=lambda *a: self.fail("ei pitaisi ajaa"))
        self.assertEqual(y["lopputulos"], "epaonnistui")

    def test_shell_ei_peri_isannan_ymparistoa(self):
        saatu = {}

        def ajo(komento, **kw):
            saatu.update(kw)
            return subprocess.CompletedProcess(komento, 0, "ulos", "")
        os.environ["LITELLM_API_KEY"] = "ei-pida-vuotaa"
        self.addCleanup(os.environ.pop, "LITELLM_API_KEY", None)
        litellm.aja_kuoressa("pct", 9100, "id", ajo=ajo)
        self.assertNotIn("LITELLM_API_KEY", saatu["env"])
        self.assertEqual(list(saatu["env"]), ["PATH"])


FAKE_PCT = r'''#!/usr/bin/env bash
echo "$*" >> "$SIM/pct.log"
cmd="$1"; shift
case "$cmd" in
  list) echo "VMID Status Lock Name"; cat "$SIM/lista" 2>/dev/null || true ;;
  clone) touch "$SIM/klooni-$2" ;;
  config) echo "hostname: ${FAKE_HOSTNAME:-hl-$1}"; echo "net0: name=eth0,bridge=${FAKE_SILTA:-vmbr2},ip=10.98.0.10/24"; ${FAKE_LISARIVI:+echo "$FAKE_LISARIVI"} ;;
  status) if [ -f "$SIM/ajossa-$1" ]; then echo "status: running"; else echo "status: stopped"; fi ;;
  start) touch "$SIM/ajossa-$1" ;;
  stop) rm -f "$SIM/ajossa-$1" ;;
  destroy) echo "tuhottu $1" >> "$SIM/tuhotut"; rm -f "$SIM/klooni-$1" ;;
  exec)
    id="$1"; shift; shift
    if [ "$id" = 9001 ]; then
      echo "$(date +%s).5 10 $3 TCP_DENIED/403 3900 CONNECT paha.example:443 - HIER_NONE/- text/html"
    elif [ "$1" = tar ]; then echo arkisto
    elif [ "$1" = timeout ]; then exit "${FAKE_TARKISTUS_RC:-0}"
    fi ;;
  *) ;;
esac
exit 0
'''


@unittest.skipUnless(LINUX, "vaatii Linuxin, bashin, timeoutin ja flockin")
class IsantaSkriptiTesti(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        self.kopio = self.d / "hl"
        shutil.copytree(HL_HAKEMISTO, self.kopio, ignore=shutil.ignore_patterns("__pycache__"))
        self.sim = self.d / "sim"
        self.sim.mkdir()
        self.fw = self.d / "fw"
        self.fw.mkdir()
        self.tila = self.d / "tila"
        pct = self.d / "pct"
        pct.write_text(FAKE_PCT)
        pct.chmod(0o755)
        self.env = {"PATH": os.environ["PATH"], "SIM": str(self.sim), "HL_PCT": str(pct), "HL_TILA": str(self.tila),
                    "HL_FW_HAKEMISTO": str(self.fw)}

    def aja(self, *args, env=None, script="aja-kokeilu.sh"):
        e = dict(self.env)
        e.update(env or {})
        return subprocess.run(["bash", str(self.kopio / script)] + list(args), capture_output=True, text=True, env=e, timeout=120)

    def pct_loki(self):
        p = self.sim / "pct.log"
        return p.read_text().splitlines() if p.exists() else []

    def rivit(self):
        p = self.tila / "kokeilut.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    # --- kuiva-ajo ---
    def test_kuiva_ei_koske_mihinkaan(self):
        r = self.aja("--kuiva", "--kokeilu", "turva-01", "--adapteri", "kuiva")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.sim / "pct.log").exists())
        self.assertFalse(self.tila.exists())
        self.assertEqual(list(self.fw.iterdir()), [])
        for odotettu in ("pct clone 9000 9100", "pct snapshot 9100 ennen", "pct snapshot 9100 jalkeen", "pct destroy 9100",
                         "policy_out: DROP", "-dest 10.98.0.2 -p tcp -dport 3128", "timeout -k 20 1800"):
            self.assertIn(odotettu, r.stdout)

    def test_kuivassa_kaikki_pct_id_t_ovat_sallitulta_valilta(self):
        for kok in ("turva-01", "kauppa-01", "vertailu-01"):
            r = self.aja("--kuiva", "--kokeilu", kok, "--adapteri", "litellm", "--malli", "x/y:1")
            self.assertEqual(r.returncode, 0, r.stderr)
            for m in re.finditer(r"\+ pct (\w+) (\d+)", r.stdout):
                ali, n = m.group(1), int(m.group(2))
                if ali == "clone":
                    self.assertEqual(n, 9000)  # lahde: mallipohja
                elif ali == "exec" and n == 9001:
                    pass  # proxyn loki, vain lukua
                else:
                    self.assertTrue(hl.ct_id_sallittu(n), m.group(0))
            self.assertNotRegex(r.stdout, r"destroy (?!9100)")

    def test_kuiva_virheet(self):
        huonot = [
            (["--kokeilu", "turva-01"], {}),
            (["--adapteri", "kuiva"], {}),
            (["--kokeilu", "../x", "--adapteri", "kuiva"], {}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva; rm"], {}),
            (["--kokeilu", "ei-ole", "--adapteri", "kuiva"], {}),
            (["--kokeilu", "turva-01", "--adapteri", "ei-ole"], {}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva", "--malli", "-x"], {}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva", "--malli", "a b"], {}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva", "--ct", "115"], {}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva"], {"HL_KOKEILU_KATTO_USD": "1.01"}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva"], {"HL_PAIVA_KATTO_USD": "5.5"}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva"], {"HL_AIKAKATTO_S": "3601"}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva"], {"HL_KOKEILUA_PAIVA": "21"}),
            (["--kokeilu", "turva-01", "--adapteri", "kuiva"], {"HL_AIKAKATTO_S": "abc"}),
        ]
        for args, env in huonot:
            r = self.aja("--kuiva", *args, env=env)
            self.assertNotEqual(r.returncode, 0, (args, env))
        self.assertFalse((self.sim / "pct.log").exists())

    # --- oikea ajo vaaralla pct:lla ---
    def test_taysi_ajo_kloonaa_ajaa_kirjaa_ja_tuhoaa(self):
        (self.sim / "lista").write_text("9100 running x\n115 running y\n")  # 9100 on varattu -> valitaan 9101
        r = self.aja("--kokeilu", "turva-01", "--adapteri", "kuiva")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        loki = self.pct_loki()
        self.assertIn("clone 9000 9101 --hostname hl-9101 --description hiekkalaatikko-kokeilu " + r.stdout.split("kokeilu ")[1].split(",")[0], loki)
        self.assertIn("snapshot 9101 ennen", loki)
        self.assertIn("snapshot 9101 jalkeen", loki)
        self.assertLess(loki.index("snapshot 9101 ennen"), loki.index("start 9101"))
        self.assertLess(loki.index("snapshot 9101 jalkeen"), loki.index("destroy 9101 --purge 1"))
        self.assertEqual((self.sim / "tuhotut").read_text().split(), ["tuhottu", "9101"])
        self.assertFalse((self.fw / "9101.fw").exists())
        # yksikaan pct-kutsu ei koske tuotantokonttiin eika varattuun 9100:aan
        for k in loki:
            t = k.split()
            if t[0] != "list":
                self.assertIn(int(t[1]), (9000, 9001) + tuple(range(9101, 9200)), k)
        rs = self.rivit()
        self.assertEqual(len(rs), 1)
        r0 = rs[0]
        self.assertEqual((r0["kokeilu"], r0["adapteri"], r0["malli"], r0["lopputulos"], r0["kustannus_usd"]),
                         ("turva-01", "kuiva", "kuiva", "onnistui", 0))
        self.assertEqual(r0["rajarikkeet"][0]["kohteet"], ["paha.example:443"])
        self.assertTrue(os.path.isdir(r0["lokit"]))
        self.assertTrue(os.path.isfile(os.path.join(r0["lokit"], "tyokansio.tar.gz")))
        self.assertEqual(oct(os.stat(r0["lokit"]).st_mode & 0o777), "0o700")
        # kulut: tasattu todelliseen 0 USD:hen -> varaus vapautui
        hl.varaa(str(self.tila), 0.5, 3.0, 10, 600)

    def test_fw_tiedosto_kirjoitetaan_ennen_kaynnistysta(self):
        # sailyta: fw-tiedosto jaa nahtavaksi
        r = self.aja("--sailyta", "--kokeilu", "turva-01", "--adapteri", "kuiva")
        self.assertEqual(r.returncode, 0, r.stderr)
        fw = (self.fw / "9100.fw").read_text()
        self.assertIn("policy_out: DROP", fw)
        self.assertIn("OUT ACCEPT -dest 10.98.0.2 -p tcp -dport 3128", fw)
        self.assertNotIn("tuhottu", (self.sim / "tuhotut").read_text() if (self.sim / "tuhotut").exists() else "")
        self.assertEqual(len([k for k in self.pct_loki() if k.startswith("destroy")]), 0)

    def test_tarkistus_voittaa_agentin_vaitteen(self):
        r = self.aja("--kokeilu", "turva-01", "--adapteri", "kuiva", env={"FAKE_TARKISTUS_RC": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.rivit()[0]["lopputulos"], "epaonnistui")

    def test_aikakatto_keskeyttaa_ja_tuhoaa(self):
        (self.kopio / "adapterit" / "hidas.sh").write_text("#!/usr/bin/env bash\nexec sleep 60\n")
        (self.kopio / "adapterit" / "hidas.sh").chmod(0o755)
        t0 = time.time()
        r = self.aja("--kokeilu", "vertailu-01", "--adapteri", "hidas", env={"HL_AIKAKATTO_S": "1"})
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.rivit()[0]["lopputulos"], "keskeytetty")
        self.assertTrue((self.sim / "tuhotut").exists())

    def test_adapterin_kaatuminen_on_epaonnistui(self):
        r = self.aja("--kokeilu", "turva-01", "--adapteri", "claude")  # ei toteutettu: exit 78
        self.assertEqual(r.returncode, 0, r.stderr)
        rs = self.rivit()
        self.assertEqual((rs[0]["lopputulos"], rs[0]["kustannus_usd"]), ("epaonnistui", None))
        # kulu tuntematon -> pessimistinen varaus (0,50) jai kirjanpitoon
        with self.assertRaises(hl.Virhe):
            hl.varaa(str(self.tila), 0.5, 0.9, 10, 600)

    def test_paivakatto_estaa_ajon_ennen_kloonausta(self):
        r1 = self.aja("--kokeilu", "turva-01", "--adapteri", "claude", env={"HL_PAIVA_KATTO_USD": "0.6"})
        self.assertEqual(r1.returncode, 0)
        self.sim.joinpath("pct.log").unlink()
        r2 = self.aja("--kokeilu", "turva-01", "--adapteri", "claude", env={"HL_PAIVA_KATTO_USD": "0.6"})
        self.assertNotEqual(r2.returncode, 0)
        self.assertNotIn("kustannusvarausta", "")  # selite tulee stderriin
        self.assertIn("kustannusvarausta ei voitu tehda", r2.stderr)
        self.assertFalse(any(k.startswith(("clone", "start", "destroy")) for k in self.pct_loki()))
        self.assertEqual(len(self.rivit()), 1)

    def test_ei_vapaata_id_ta(self):
        (self.sim / "lista").write_text("".join("%d running x\n" % n for n in range(9100, 9200)))
        r = self.aja("--kokeilu", "turva-01", "--adapteri", "kuiva")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ei vapaata CT-ID:ta", r.stderr)
        self.assertFalse(any(k.startswith(("clone", "destroy")) for k in self.pct_loki()))

    def test_tuhoamista_ei_tehda_jos_kontti_ei_tasmaa(self):
        for env in ({"FAKE_HOSTNAME": "tuotanto"}, {"FAKE_SILTA": "vmbr0"}, {"FAKE_LISARIVI": "mp0: /root,mp=/host"},
                    {"FAKE_LISARIVI": "lxc.mount.entry: /dev dev none bind"}):
            with self.subTest(env=env):
                shutil.rmtree(self.sim)
                self.sim.mkdir()
                r = self.aja("--kokeilu", "turva-01", "--adapteri", "kuiva", env=env)
                self.assertEqual(r.returncode, 5, r.stderr)
                self.assertFalse((self.sim / "tuhotut").exists())
                self.assertIn("EI tuhota", r.stderr)

    def test_toinen_ajo_samanaikaisesti_estyy(self):
        (self.kopio / "adapterit" / "hidas.sh").write_text("#!/usr/bin/env bash\nexec sleep 5\n")
        (self.kopio / "adapterit" / "hidas.sh").chmod(0o755)
        e = dict(self.env)
        p = subprocess.Popen(["bash", str(self.kopio / "aja-kokeilu.sh"), "--kokeilu", "vertailu-01", "--adapteri", "hidas"],
                             env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                if (self.sim / "pct.log").exists() and any(k.startswith("start") for k in self.pct_loki()):
                    break
                time.sleep(0.1)
            r = self.aja("--kokeilu", "turva-01", "--adapteri", "kuiva")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("toinen kokeilu on kaynnissa", r.stderr)
        finally:
            p.terminate()
            p.wait(timeout=30)

    # --- luo-pohja.sh ---
    def test_luo_pohja_kuiva(self):
        r = self.aja("--kuiva", script="luo-pohja.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.sim / "pct.log").exists())
        for odotettu in ("vmbr2", "pct create 9001", "pct create 9000", "pct template 9000", "http_access deny privaatti",
                         "http_access allow sandbox allowed", "bridge-ports none"):
            self.assertIn(odotettu, r.stdout)
        self.assertNotIn("vmbr0,ip=10.98", r.stdout)
        # mallipohjalla ei ole LAN-korttia: vain net0 vmbr2:lla
        pohja = r.stdout.split("pct create 9000")[1].split("\n")[0]
        self.assertNotIn("net1", pohja)
        self.assertNotIn("destroy", r.stdout)

    def test_luo_pohja_vaatii_ymparistomuuttujat_ilman_kuivaa(self):
        r = self.aja(script="luo-pohja.sh")
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.sim / "pct.log").exists())

    def test_luo_pohja_hylkaa_kuoren_merkit(self):
        r = self.aja("--kuiva", script="luo-pohja.sh", env={"HL_TALLENNUS": "a;rm -rf x"})
        self.assertEqual(r.returncode, 2)


@unittest.skipUnless(LINUX, "vaatii bashin")
class SkriptiLintTesti(unittest.TestCase):
    SKRIPTIT = sorted(list(HL_HAKEMISTO.glob("*.sh")) + list((HL_HAKEMISTO / "adapterit").glob("*.sh")) + list((HL_HAKEMISTO / "kokeilut").glob("*.sh")))

    def test_skriptit_loytyvat(self):
        self.assertGreaterEqual(len(self.SKRIPTIT), 6)

    def test_bash_n_ja_perusasetukset(self):
        for s in self.SKRIPTIT:
            with self.subTest(s=s.name):
                self.assertEqual(subprocess.run(["bash", "-n", str(s)], capture_output=True).returncode, 0)
                teksti = s.read_text()
                self.assertTrue(teksti.startswith("#!/usr/bin/env bash\n"))
                self.assertIn("set -eu", teksti.replace("set -euo pipefail", "set -eu"))
                self.assertTrue(os.stat(s).st_mode & stat.S_IXUSR, "ei suoritettava")

    def test_shellcheck_jos_asennettu(self):
        sc = shutil.which("shellcheck")
        if not sc:
            self.skipTest("shellcheck ei asennettu (CI:ssa ajetaan)")
        for s in self.SKRIPTIT:
            with self.subTest(s=s.name):
                r = subprocess.run([sc, "-S", "warning", str(s)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout)

    def test_kaikki_tiedostot_ascii_ja_ei_salaisuuksia(self):
        salaisuus = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_|[0-9]{8,}:[A-Za-z0-9_-]{30,}|BEGIN [A-Z ]*PRIVATE KEY)")
        for p in HL_HAKEMISTO.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                b = p.read_bytes()
                with self.subTest(p=str(p.relative_to(HL_HAKEMISTO))):
                    b.decode("ascii")
                    self.assertIsNone(salaisuus.search(b.decode("ascii")))

    def test_pct_kutsut_kulkevat_ct_funktion_kautta(self):
        """Muuttavat pct-alikomennot kloonille saa kutsua vain ct()-funktiolla (joka validoi ID:n)."""
        teksti = (HL_HAKEMISTO / "aja-kokeilu.sh").read_text()
        funktio = teksti.split("\nct() {")[1].split("\n}\n")[0]
        self.assertIn('validoi_id "$id"', funktio)
        muuttavat = re.compile(r'"\$PCT"\s+(set|start|stop|snapshot|push|destroy|rollback|delete|resize|move|migrate|restore)\b')
        self.assertEqual(muuttavat.findall(teksti), [])
        # lisaksi tuhoaminen vain yhdessa paikassa ja vain sen jalkeen kun kuvaus on tarkistettu
        self.assertEqual(len(re.findall(r"\bct destroy\b", teksti)), 2)  # siivoa() + kuivan ajon esitys
        koodi = [x for x in teksti.splitlines() if not x.lstrip().startswith("#") and "echo" not in x]
        self.assertEqual([x for x in koodi if re.search(r'"\$PCT"\s+destroy|\bpct destroy', x)], [])

    def test_aja_kokeilu_ei_lue_salaisuustiedostoja(self):
        for s in self.SKRIPTIT:
            teksti = s.read_text()
            self.assertNotRegex(teksti, r"api_key|\.env\b|/etc/holvi|id_rsa|authorized_keys")


if __name__ == "__main__":
    unittest.main()
