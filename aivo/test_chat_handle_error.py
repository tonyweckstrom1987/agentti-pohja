"""Chat-palvelimen kasittelijavirhe ei saa vuotaa poikkeuksen viestia stderriin (journal)."""
import http.client
import io
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr

from aivo import chat

SALAINEN = "SALAINEN-" + "merkkijono-7f3a"     # koottu osista: ei kirjaimellisena lahdekoodissa


def ip(*osat):
    return ".".join(str(o) for o in osat)


class Myrkkytila:
    """Mika tahansa tilan kentan luku nostaa poikkeuksen, jonka viestissa on salaisuus."""

    def __getattr__(self, nimi):
        raise RuntimeError("vuoto %s" % SALAINEN)


class HandleErrorTesti(unittest.TestCase):
    def test_vain_tyypin_nimi_stderriin(self):
        srv = chat.kaynnista(ip(127, 0, 0, 1), 0, Myrkkytila())
        self.assertIsInstance(srv, chat.AivoPalvelin)
        portti = srv.server_address[1]
        saie = threading.Thread(target=srv.serve_forever, daemon=True)
        virta = io.StringIO()
        with redirect_stderr(virta):
            saie.start()
            try:
                c = http.client.HTTPConnection(ip(127, 0, 0, 1), portti, timeout=5)
                try:
                    c.request("GET", "/terve")
                    c.getresponse().read()
                except (http.client.HTTPException, OSError):
                    pass                         # palvelin sulkee yhteyden vastaamatta
                finally:
                    c.close()
                loppu = time.monotonic() + 5
                while "kasittelijavirhe" not in virta.getvalue() and time.monotonic() < loppu:
                    time.sleep(0.02)
            finally:
                srv.shutdown()
                srv.server_close()
        teksti = virta.getvalue()
        self.assertIn("chat: kasittelijavirhe RuntimeError", teksti)
        self.assertNotIn(SALAINEN, teksti)
        self.assertNotIn("Traceback", teksti)
        self.assertNotIn(ip(127, 0, 0, 1), teksti)

    def test_handle_error_ilman_poikkeusta(self):
        srv = chat.AivoPalvelin.__new__(chat.AivoPalvelin)   # ei sokettia: vain metodin kaytos
        virta = io.StringIO()
        with redirect_stderr(virta):
            srv.handle_error(None, ("x", 0))
        self.assertEqual(virta.getvalue(), "chat: kasittelijavirhe tuntematon\n")


if __name__ == "__main__":
    unittest.main()
