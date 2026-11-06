"""Yhteinen testiymparisto run.py:n testeille (ei oma testimoduuli): tilapaishakemistot, mock-malli ja mock-Telegram.
Ei verkkoa, ei oikeita tunnuksia. Kaytto: class T(Ymparisto, unittest.TestCase)."""
import contextlib
import io
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import run  # noqa: E402

TOKEN = "123456:TESTITOKEN-EI-OIKEA-ABCDEFGHIJKLMNOP"
CHAT = "424242"
LLM_AVAIN = "TESTIAVAIN-EI-OIKEA-98765"
NYT = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)

PROFIILI = """---
versio: 1
paivitetty: 2026-10-13
---
## tavoitteet
- PROFIILIN-TAVOITE-RIVI
## prioriteetit
1. Koulu
## projektit
- trendiagentti: toimii
## koulu
tyyppi: ohjelmistokehittajakoulutus
## tyyli
- suomeksi, tiiviisti
## rajat
- ei kryptoa
"""

HYVA = {"top3": [{"asia": f"Asia {i}", "miksi": f"Miksi {i}"} for i in range(3)], "uutta": ["Uusi 1"], "ehdotukset": ["Tee X", "Tee Y"]}


def llm_vastaus(sisalto=HYVA, finish="stop", prompt=1000, completion=200):
    return {"choices": [{"message": {"content": sisalto if isinstance(sisalto, str) else json.dumps(sisalto)}, "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


class Mock:
    """Korvaa http-funktion: jono vastauksia (tai poikkeuksia) tai fn(n). Tallentaa kutsut."""
    def __init__(self, *vastaukset, fn=None):
        self.vastaukset, self.fn, self.kutsut = list(vastaukset), fn, []

    def __call__(self, *args):
        self.kutsut.append(args)
        if self.fn:
            return self.fn(len(self.kutsut))
        v = self.vastaukset.pop(0)
        if isinstance(v, BaseException):
            raise v
        return v


class Ymparisto:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.r = Path(self.tmp.name)
        self.ideat, self.out, self.ehd = r / "ideat", r / "trendit" / "out", r / "ehdotukset"
        self.profiilit, self.arkisto, self.loki = r / "profiili", r / "arkisto", r / "paivakatsaus.log"
        for d in (self.ideat, self.out, self.ehd, self.profiilit, self.arkisto):
            d.mkdir(parents=True)
        self.profiili_polku = self.profiilit / "tony_profiili.md"
        self.profiili_polku.write_text(PROFIILI, encoding="utf-8")
        self.paikka = mock.patch.object(config, "SALLITUT_POLUT", [str(d) for d in (self.ideat, self.out, self.ehd, self.profiilit)])
        self.paikka.start()
        self.lukupaikka = mock.patch.object(config, "SALLITUT_LUKUPOLUT", [str(r / "posti")])
        self.lukupaikka.start()
        self.env = {"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": CHAT, "PROFIILI_POLKU": str(self.profiili_polku),
                    "LLM_BASE_URL": "http://litellm.test:4000/v1", "LLM_API_KEY": LLM_AVAIN}
        self.posti = r / "posti"                                  # ei luoda: oletuksena puuttuva postilaatikko
        self.polut = {"arkisto": self.arkisto, "ehdotukset": self.ehd, "loki": self.loki, "ideat": self.ideat, "trendit": self.out,
                      "posti": self.posti}
        self.sleep = mock.patch.object(run.send.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.lukupaikka.stop()
        self.paikka.stop()
        self.tmp.cleanup()

    def idea(self, nimi, teksti):
        (self.ideat / nimi).write_text(teksti, encoding="utf-8")

    def normaali_data(self):
        self.idea("2026-10-13-1200.md", "# Ehdotukset\n\nHyva idea: tee mcp-gateway")

    def ajo(self, kayta_llm=False, laheta=False, llm=None, tg=None, **kw):
        """Palauttaa poistumiskoodin; self.ulos/self.err ovat talteen otetut tulosteet."""
        self.llm = llm if llm is not None else Mock(llm_vastaus())
        self.tg = tg if tg is not None else Mock({"ok": True})
        ulos, err = io.StringIO(), io.StringIO()
        kw.setdefault("env", self.env)
        kw.setdefault("nyt", NYT)
        koodi = run.ajo(kayta_llm=kayta_llm, laheta=laheta, polut=self.polut, http_llm=self.llm, http_send=self.tg,
                        tuloste=ulos, virhetuloste=err, viive=0, **kw)
        self.ulos, self.err = ulos.getvalue(), err.getvalue()
        return koodi

    def loki_teksti(self):
        return self.loki.read_text(encoding="utf-8") if self.loki.exists() else ""

    def tila_ehdotus(self, nimi):
        for rivi in (self.ehd / nimi).read_text(encoding="utf-8").splitlines():
            if rivi.startswith("tila: "):
                return rivi[6:]

    def kuva(self):
        return {str(p.relative_to(self.r)): p.read_bytes() for p in sorted(self.r.rglob("*")) if p.is_file() and not p.is_symlink()}

    def lukot(self):
        return sorted(p.name for p in self.arkisto.glob("*.sent"))
