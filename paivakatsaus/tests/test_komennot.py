"""Telegram-komentokasittelija (komennot.py): /hyvaksy N ja /lista. Ei oikeita Telegram- tai LLM-kutsuja:
http on mock (Bot API -muoto) tai paikallinen 127.0.0.1-feikkipalvelin. Tilapaishakemistot."""
import fcntl
import http.server
import json
import os
import re
import threading
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ymparisto import Ymparisto, TOKEN, CHAT, NYT, LLM_AVAIN
import config
import komennot
import proposals
import send

ROOT = Path(__file__).resolve().parent.parent
VIERAS = "999111"


def paivitys(uid, teksti, chat=CHAT, frm=CHAT, tyyppi="private", avain="message", **lisa):
    m = {"message_id": uid, "date": 1, "chat": {"id": chat if not isinstance(chat, str) else int(chat), "type": tyyppi},
         "from": {"id": frm if not isinstance(frm, str) else int(frm), "is_bot": False}, "text": teksti}
    m.update(lisa)
    return {"update_id": uid, avain: m}


_EI = object()


class Palvelin:
    """Bot API -mock: getUpdates noudattaa offsetia (palauttaa update_id >= offset), sendMessage tallentaa viestit."""
    def __init__(self, paivitykset=(), hae_virhe=None, laheta_virhe=None, hae_vastaus=_EI, ei_limitia=False):
        self.paivitykset = list(paivitykset)
        self.ei_limitia = ei_limitia
        self.hae_virhe, self.laheta_virhe, self.hae_vastaus = hae_virhe, laheta_virhe, hae_vastaus
        self.haut, self.viestit, self.kutsut = [], [], []

    def __call__(self, url, runko):
        self.kutsut.append((url, runko))
        if url.endswith("/getUpdates"):
            self.haut.append(runko)
            if self.hae_virhe:
                raise self.hae_virhe
            if self.hae_vastaus is not _EI:
                return self.hae_vastaus
            o = runko["offset"]
            if o < 0:
                tulos = self.paivitykset[-1:]
            else:
                tulos = [p for p in self.paivitykset if p["update_id"] >= o]
            return {"ok": True, "result": tulos if self.ei_limitia else tulos[:runko["limit"]]}
        if url.endswith("/sendMessage"):
            if self.laheta_virhe:
                raise self.laheta_virhe
            self.viestit.append(runko)
            return {"ok": True, "result": {}}
        raise AssertionError("odottamaton osoite")


class Pohja(Ymparisto, unittest.TestCase):
    def setUp(self):
        super().setUp()
        proposals.luo_ehdotukset(["Ehdotus A", "Ehdotus B", "Ehdotus C", "Ehdotus D"], nyt=NYT, hakemisto=self.ehd)    # numerot 1-4

    def offset_polku(self):
        return self.arkisto / "update_offset.txt"

    def aseta_offset(self, n):
        self.offset_polku().write_text(f"{n}\n", encoding="utf-8")

    def offset(self):
        return int(self.offset_polku().read_text(encoding="utf-8"))

    def aja(self, palvelin, nyt=NYT):
        self.palvelin = palvelin
        return komennot.ajo(env=self.env, nyt=nyt, arkisto=self.arkisto, hakemisto=self.ehd, loki=self.loki, http=palvelin, viive=0)

    def tilat(self):
        return {e["n"]: e["tila"] for e in proposals.lue_kaikki(self.ehd)[0]}

    def loki_rivit(self):
        return [r for r in self.loki_teksti().splitlines() if " komennot " in r]


class HyvaksyTesti(Pohja):
    def test_hyvaksy_3_merkitsee_vain_oikean_ja_vastaa_omistajalle(self):
        self.aseta_offset(100)
        p = Palvelin([paivitys(100, "/hyvaksy 3")])
        self.assertEqual(self.aja(p), 0)
        self.assertEqual(self.tilat(), {1: "PENDING", 2: "PENDING", 3: "APPROVED", 4: "PENDING"})
        self.assertEqual(len(p.viestit), 1)
        self.assertEqual(p.viestit[0]["chat_id"], CHAT)
        self.assertIn("Hyvaksytty: ehdotus 3", p.viestit[0]["text"])
        self.assertEqual(self.offset(), 101)

    def test_hyvaksy_kahdesti_ei_tuplaa(self):
        self.aseta_offset(100)
        p = Palvelin([paivitys(100, "/hyvaksy 3"), paivitys(101, "/hyvaksy 3")])
        self.assertEqual(self.aja(p), 0)
        self.assertEqual(self.tilat()[3], "APPROVED")
        self.assertIn("Hyvaksytty", p.viestit[0]["text"])
        self.assertIn("jo hyvaksytty", p.viestit[1]["text"])
        self.assertEqual(self.loki_teksti().count("approve n=3"), 1)                      # hyvaksynta kirjattiin kerran
        self.assertEqual(self.aja(p), 0)                                                    # uusi ajo: offset 102, ei mitaan uutta
        self.assertEqual(len(p.viestit), 2)
        self.assertEqual(self.loki_teksti().count("approve n=3"), 1)

    def test_nelinumeroinen_numero_toimii(self):
        (self.ehd / "numerolaskuri.txt").write_text("99\n", encoding="utf-8")
        proposals.luo_ehdotukset(["Sata"], nyt=NYT, hakemisto=self.ehd)
        self.aseta_offset(1)
        p = Palvelin([paivitys(1, "/hyvaksy 100")])
        self.aja(p)
        self.assertEqual(self.tilat()[100], "APPROVED")

    def test_tuntematon_numero_antaa_selkean_virheen(self):
        self.aseta_offset(1)
        p = Palvelin([paivitys(1, "/hyvaksy 77")])
        self.aja(p)
        self.assertIn("77", p.viestit[0]["text"])
        self.assertIn("Ei avointa ehdotusta", p.viestit[0]["text"])
        self.assertEqual(set(self.tilat().values()), {"PENDING"})

    def test_vanhentunut_ehdotus_antaa_selkean_virheen(self):
        self.aseta_offset(1)
        myohemmin = NYT + timedelta(days=9)
        p = Palvelin([paivitys(1, "/hyvaksy 2")])
        self.aja(p, nyt=myohemmin)
        self.assertIn("vanhentunut", p.viestit[0]["text"])
        self.assertEqual(self.tilat()[2], "EXPIRED")

    def test_hyvaksy_ei_suorita_mitaan(self):
        lahde = (ROOT / "komennot.py").read_text(encoding="utf-8")
        for kielletty in ("subprocess", "os.system", "os.exec", "eval(", "exec(", "__import__", "shlex", "pty"):
            self.assertNotIn(kielletty, lahde)


class SuodatusTesti(Pohja):
    def assert_ei_vaikutusta(self, paivitykset, offset_etenee=True):
        self.aseta_offset(1)
        ennen = self.tilat()
        p = Palvelin(paivitykset)
        self.assertEqual(self.aja(p), 0)
        self.assertEqual(self.tilat(), ennen)
        self.assertEqual(p.viestit, [])                                                   # vieraille ei vastata
        self.assertNotIn("approve", self.loki_teksti())
        if offset_etenee:
            self.assertEqual(self.offset(), max(x["update_id"] for x in paivitykset) + 1)

    def test_vieras_chat(self):
        self.assert_ei_vaikutusta([paivitys(1, "/hyvaksy 3", chat=VIERAS, frm=CHAT)])

    def test_vieras_from_id(self):
        self.assert_ei_vaikutusta([paivitys(1, "/hyvaksy 3", chat=CHAT, frm=VIERAS)])

    def test_molemmat_vieraita(self):
        self.assert_ei_vaikutusta([paivitys(1, "/hyvaksy 3", chat=VIERAS, frm=VIERAS), paivitys(2, "/lista", chat=VIERAS, frm=VIERAS)])

    def test_ryhma_botti_edelleenlahetetty_muokattu_ja_muut_tyypit(self):
        self.assert_ei_vaikutusta([
            paivitys(1, "/hyvaksy 3", tyyppi="group"),
            paivitys(2, "/hyvaksy 3", tyyppi="supergroup"),
            paivitys(3, "/hyvaksy 3", avain="edited_message"),
            paivitys(4, "/hyvaksy 3", avain="channel_post"),
            paivitys(5, "/hyvaksy 3", forward_origin={"type": "user"}),
            paivitys(6, "/hyvaksy 3", forward_date=5),
            {"update_id": 7, "callback_query": {"data": "/hyvaksy 3"}},
        ])

    def test_botti_lahettaja(self):
        u = paivitys(1, "/hyvaksy 3")
        u["message"]["from"]["is_bot"] = True
        self.assert_ei_vaikutusta([u])

    def test_id_tyypit_tarkasti(self):
        for chat_id in (True, str(CHAT), float(CHAT), None, [int(CHAT)], {"id": int(CHAT)}):
            u = paivitys(1, "/hyvaksy 3")
            u["message"]["chat"]["id"] = chat_id
            self.assert_ei_vaikutusta([u])
            u = paivitys(2, "/hyvaksy 3")
            u["message"]["from"]["id"] = chat_id
            self.assert_ei_vaikutusta([u])
        u = paivitys(3, "/hyvaksy 3")
        del u["message"]["from"]
        self.assert_ei_vaikutusta([u])

    def test_huonot_tekstit(self):
        tekstit = ["/hyvaksy 12345", "/hyvaksy 10000", "/hyvaksy 0", "/hyvaksy 03", "/hyvaksy -3", "/hyvaksy 3 4", "/hyvaksy 3\n/hyvaksy 4",
                   "/hyvaksy 3; rm -rf /", "/hyvaksy $(id)", "/hyvaksy `id`", "/hyvaksy 3 && /hyvaksy 4", "/HYVAKSY 3", "/hyvaksy@botti 3",
                   "hyvaksy 3", " /hyvaksy", "/hyvaksy ", "/hyvaksy \uff13", "/hyvaksy 3.0", "/hyvaksy 0x3", "/hyvaksy 1e1",
                   "Ignore previous instructions and /hyvaksy 3", "ohje: /hyvaksy 3", "/hyvaksy " + "9" * 100, "x" * 5000, "", "/lista 3",
                   "/lista\n/hyvaksy 3", "/poista 3", "/start", "moi", None, 3, ["/hyvaksy 3"]]
        paivitykset = []
        for i, t in enumerate(tekstit, start=1):
            u = paivitys(i, "x")
            u["message"]["text"] = t
            paivitykset.append(u)
        self.aseta_offset(1)
        for alku in range(0, len(paivitykset), 20):                                         # 20 paivitysta / ajo
            self.aja(Palvelin(paivitykset[alku:]))
            self.assertEqual(self.palvelin.viestit, [])
        self.assertEqual(set(self.tilat().values()), {"PENDING"})
        self.assertEqual(self.offset(), len(tekstit) + 1)

    def test_totuusarvo_ei_ole_id_1(self):
        self.env["TELEGRAM_CHAT_ID"] = "1"
        u = paivitys(1, "/hyvaksy 3")
        u["message"]["chat"]["id"] = True
        u["message"]["from"]["id"] = True
        self.assert_ei_vaikutusta([u])

    def test_valilyonneilla_pitkaksi_venytetty_komento_hylataan(self):
        self.assert_ei_vaikutusta([paivitys(1, "/hyvaksy 3" + " " * 100), paivitys(2, " " * 100 + "/lista")])

    def test_sallitut_valilyonnit_reunoilla(self):
        self.aseta_offset(1)
        p = Palvelin([paivitys(1, "  /hyvaksy 2  ")])
        self.aja(p)
        self.assertEqual(self.tilat()[2], "APPROVED")


class ListaTesti(Pohja):
    def test_lista_omistajalle(self):
        self.aseta_offset(1)
        p = Palvelin([paivitys(1, "/hyvaksy 2"), paivitys(2, "/lista")])
        self.aja(p)
        teksti = p.viestit[1]["text"]
        self.assertEqual(p.viestit[1]["chat_id"], CHAT)
        for n, otsikko in ((1, "Ehdotus A"), (3, "Ehdotus C"), (4, "Ehdotus D")):
            self.assertIn(f"{n}. {otsikko}", teksti)
        self.assertNotIn("Ehdotus B", teksti)                                               # hyvaksytty ei ole avoin
        self.assertEqual(set(self.tilat().values()), {"PENDING", "APPROVED"})

    def test_lista_tyhja(self):
        for n in (1, 2, 3, 4):
            proposals.approve(n, nyt=NYT, hakemisto=self.ehd, loki=self.loki)
        self.aseta_offset(1)
        p = Palvelin([paivitys(1, "/lista")])
        self.aja(p)
        self.assertIn("Ei avoimia", p.viestit[0]["text"])

    def test_lista_vieraalle_ei_vastausta(self):
        self.aseta_offset(1)
        p = Palvelin([paivitys(1, "/lista", chat=VIERAS, frm=VIERAS)])
        self.aja(p)
        self.assertEqual(p.viestit, [])

    def test_lista_rajattu_ja_ei_linkkeja(self):
        (self.ehd / "numerolaskuri.txt").write_text("4\n", encoding="utf-8")
        proposals.luo_ehdotukset([f"Pitka ehdotus {i} " + "x" * 100 for i in range(12)], nyt=NYT, hakemisto=self.ehd)
        self.aseta_offset(1)
        p = Palvelin([paivitys(1, "/lista")])
        self.aja(p)
        teksti = p.viestit[0]["text"]
        self.assertLessEqual(len(teksti), config.VIESTI_MAX_MERKKEJA)
        self.assertIn("muuta", teksti)


class OffsetTesti(Pohja):
    def test_ensiajo_alustaa_offsetin_eika_kasittele_vanhoja(self):
        p = Palvelin([paivitys(50, "/hyvaksy 3"), paivitys(51, "/hyvaksy 4")])
        self.assertEqual(self.aja(p), 0)
        self.assertEqual(p.haut[0]["offset"], -1)
        self.assertEqual(self.offset(), 52)
        self.assertEqual(p.viestit, [])
        self.assertEqual(set(self.tilat().values()), {"PENDING"})
        self.assertIn("tapahtuma=alustus", self.loki_rivit()[-1])

    def test_ensiajo_ilman_paivityksia_kirjoittaa_nollan(self):
        self.assertEqual(self.aja(Palvelin([])), 0)
        self.assertEqual(self.offset(), 0)

    def test_pyynnon_muoto(self):
        self.aseta_offset(77)
        p = Palvelin([])
        self.aja(p)
        url, runko = p.kutsut[0]
        self.assertTrue(url.startswith("https://api.telegram.org/bot"), url[:30])
        self.assertTrue(url.endswith("/getUpdates"))
        self.assertEqual((runko["offset"], runko["limit"], runko["timeout"]), (77, 20, 0))
        self.assertEqual(runko["allowed_updates"], ["message"])

    def test_offset_etenee_vain_kasitellyn_yli(self):
        self.aseta_offset(10)
        p = Palvelin([paivitys(i, "moi") for i in (10, 11, 15)])
        self.aja(p)
        self.assertEqual(self.offset(), 16)
        self.aja(p)
        self.assertEqual(self.offset(), 16)                                                  # tyhja haku ei muuta

    def test_yli_20_paivitysta_kasitellaan_20_kerrallaan(self):
        self.aseta_offset(1)
        p = Palvelin([paivitys(i, "moi") for i in range(1, 46)], ei_limitia=True)                # palvelin ei noudata limitia
        self.aja(p)
        self.assertEqual(self.offset(), 21)

    def test_offset_tallennetaan_ennen_toimenpidetta_ja_kestaa_keskeytyksen(self):
        self.aseta_offset(100)
        p = Palvelin([paivitys(100, "/hyvaksy 3"), paivitys(101, "/hyvaksy 4")], laheta_virhe=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.aja(p)
        self.assertEqual(self.offset(), 102)                                                 # tallennettu ennen vastausta
        self.assertEqual(self.tilat()[3], "APPROVED")                                         # merkinta ehti tehda, vastaus ei lahtenyt
        self.assertEqual(self.tilat()[4], "PENDING")
        p2 = Palvelin([paivitys(100, "/hyvaksy 3"), paivitys(101, "/hyvaksy 4")])
        self.aja(p2)                                                                          # sama komento ei suoritu uudelleen
        self.assertEqual(p2.viestit, [])
        self.assertEqual(self.tilat()[4], "PENDING")
        self.assertEqual(self.loki_teksti().count("approve n=3"), 1)

    def test_haun_epaonnistuminen_ei_siirra_offsetia(self):
        self.aseta_offset(100)
        p = Palvelin([paivitys(100, "/hyvaksy 3")], hae_virhe=OSError("Verkko " + TOKEN))
        self.assertEqual(self.aja(p), 0)                                                      # ohimenevä: ei failed-tilaa
        self.assertEqual(self.offset(), 100)
        self.assertEqual(self.tilat()[3], "PENDING")
        self.assertIn("tapahtuma=haku_virhe_ohimeneva", self.loki_rivit()[-1])

    def test_offsetin_kirjoitus_epaonnistuu_fail_closed(self):
        self.aseta_offset(100)
        p = Palvelin([paivitys(100, "/hyvaksy 3")])
        with mock.patch.object(komennot, "_kirjoita_offset", side_effect=OSError("levy tayna")):
            self.assertEqual(self.aja(p), 1)
        self.assertEqual(self.tilat()[3], "PENDING")                                         # ei kasitelty ilman tallennettua offsetia
        self.assertEqual(p.viestit, [])
        self.assertEqual(self.offset(), 100)
        self.assertIn("tapahtuma=offset_virhe", self.loki_rivit()[-1])

    def test_rikkinainen_offset_fail_closed(self):
        for sisalto in ("abc", "-5", "1 2", "", "99999999999999999999", "1\n2\n", "0x10", "\uff11"):
            self.offset_polku().write_text(sisalto, encoding="utf-8")
            p = Palvelin([paivitys(1, "/hyvaksy 3")])
            self.assertEqual(self.aja(p), 1, msg=repr(sisalto))
            self.assertEqual(p.kutsut, [], msg=repr(sisalto))
            self.assertEqual(self.tilat()[3], "PENDING")
        self.assertIn("tapahtuma=offset_virhe", self.loki_rivit()[-1])

    def test_offset_symlink_hylataan(self):
        kohde = self.r / "muu.txt"
        kohde.write_text("1\n", encoding="utf-8")
        os.symlink(kohde, self.offset_polku())
        p = Palvelin([paivitys(1, "/hyvaksy 3")])
        self.assertEqual(self.aja(p), 1)
        self.assertEqual(p.kutsut, [])
        self.assertEqual(kohde.read_text(encoding="utf-8"), "1\n")

    def test_offset_kirjoitus_on_atominen(self):
        self.aseta_offset(5)
        self.aja(Palvelin([paivitys(5, "moi")]))
        self.assertEqual(sorted(p.name for p in self.arkisto.iterdir() if p.name.startswith("update_offset")), ["update_offset.txt"])
        self.assertEqual(self.offset_polku().read_text(encoding="utf-8"), "6\n")

    def test_vaara_vastausmuoto_fail_closed(self):
        for vastaus in ({"ok": False, "result": []}, {"ok": True}, {"ok": True, "result": "x"}, [], "x", None,
                        {"ok": True, "result": ["x"]}, {"ok": True, "result": [{"message": {}}]},
                        {"ok": True, "result": [{"update_id": True}]}, {"ok": True, "result": [{"update_id": -1}]},
                        {"ok": True, "result": [{"update_id": "7"}]}, {"ok": True, "result": [{"update_id": 1.5}]}):
            self.aseta_offset(100)
            p = Palvelin(hae_vastaus=vastaus)
            self.assertEqual(self.aja(p), 1, msg=repr(vastaus))
            self.assertEqual(self.offset(), 100, msg=repr(vastaus))
            self.assertEqual(p.viestit, [])

    def test_lukko_estaa_rinnakkaisajon(self):
        self.aseta_offset(1)
        fd = os.open(self.arkisto / "komennot.lock", os.O_WRONLY | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            p = Palvelin([paivitys(1, "/hyvaksy 3")])
            self.assertEqual(self.aja(p), 0)
            self.assertEqual(p.kutsut, [])
            self.assertIn("tapahtuma=lukko_varattu", self.loki_rivit()[-1])
        finally:
            os.close(fd)


class VirheetJaLokiTesti(Pohja):
    def kaikki_ulostulo(self, funktio):
        import contextlib
        import io
        ulos, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(ulos), contextlib.redirect_stderr(err):
            koodi = funktio()
        return koodi, ulos.getvalue() + err.getvalue()

    def assert_ei_vuotoa(self, ulostulo):
        for salainen in (TOKEN, TOKEN.split(":")[1], LLM_AVAIN, "api.telegram.org", "/bot"):
            self.assertNotIn(salainen, ulostulo)
            self.assertNotIn(salainen, self.loki_teksti())

    def test_haun_virhe_ei_paljasta_tokenia(self):
        self.aseta_offset(1)
        for virhe in (urllib.error.URLError("https://api.telegram.org/bot" + TOKEN + "/getUpdates"), OSError(TOKEN), TimeoutError(TOKEN),
                      urllib.error.HTTPError("https://api.telegram.org/bot" + TOKEN, 401, "Unauthorized", {}, None), ValueError(TOKEN)):
            koodi, ulos = self.kaikki_ulostulo(lambda: self.aja(Palvelin([], hae_virhe=virhe)))
            self.assertEqual(koodi, 1 if isinstance(virhe, ValueError) or getattr(virhe, "code", 0) == 401 else 0)   # ohimenevä: 0, pysyvä: 1
            self.assert_ei_vuotoa(ulos)

    def test_lahetyksen_virhe_ei_paljasta_tokenia_ja_jatkaa(self):
        self.aseta_offset(1)
        virhe = urllib.error.HTTPError("https://api.telegram.org/bot" + TOKEN + "/sendMessage", 400, "Bad Request " + TOKEN, {}, None)
        p = Palvelin([paivitys(1, "/hyvaksy 3"), paivitys(2, "/hyvaksy 4")], laheta_virhe=virhe)
        koodi, ulos = self.kaikki_ulostulo(lambda: self.aja(p))
        self.assertEqual(koodi, 1)
        self.assert_ei_vuotoa(ulos)
        self.assertEqual((self.tilat()[3], self.tilat()[4]), ("APPROVED", "APPROVED"))      # molemmat kasiteltiin vaikka vastaus epaonnistui
        self.assertEqual(self.offset(), 3)
        self.assertIn("tapahtuma=lahetys_virhe", self.loki_rivit()[-1])

    def test_odottamaton_virhe_kirjaa_vain_tyypin_nimen(self):
        self.aseta_offset(1)
        with mock.patch.object(komennot, "_kasittele", side_effect=RuntimeError(TOKEN)):
            koodi, ulos = self.kaikki_ulostulo(lambda: self.aja(Palvelin([paivitys(1, "/lista")])))
        self.assertEqual(koodi, 1)
        self.assert_ei_vuotoa(ulos)
        self.assertIn("tapahtuma=odottamaton_RuntimeError", self.loki_rivit()[-1])

    def test_asetusvirhe_nimeaa_vain_muuttujat(self):
        env = dict(self.env)
        del env["TELEGRAM_TOKEN"]
        self.env = env
        p = Palvelin([])
        koodi, ulos = self.kaikki_ulostulo(lambda: self.aja(p))
        self.assertEqual(koodi, 1)
        self.assertEqual(p.kutsut, [])
        self.assertIn("tapahtuma=asetus_virhe", self.loki_rivit()[-1])
        self.assert_ei_vuotoa(ulos)

    def test_lokirivi_vain_tapahtuma_ja_kesto(self):
        self.aseta_offset(1)
        self.aja(Palvelin([paivitys(1, "/hyvaksy 3"), paivitys(2, "/lista")]))
        self.aja(Palvelin([]))
        rivit = self.loki_rivit()
        self.assertEqual(len(rivit), 2)
        for r in rivit:
            self.assertRegex(r, r"^\S+ komennot tapahtuma=[a-z_A-Z]+ kesto_s=\d+\.\d\d$")
        for kielletty in ("Ehdotus", "hyvaksy", CHAT, TOKEN):
            self.assertNotIn(kielletty, "\n".join(rivit))

    def test_main_hylkaa_argumentit(self):
        koodi, _ = self.kaikki_ulostulo(lambda: komennot.main(["--foo"]))
        self.assertEqual(koodi, 64)


class _Feikki:
    def __init__(self, tila="ok", viive=0.0, ohjaus=None):
        self.pyynnot = []
        f = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                f.pyynnot.append("GET " + self.path)                                         # myos GET-seuranta (302 voi muuttua GET:ksi)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                f.pyynnot.append(self.path)
                if viive:
                    threading.Event().wait(viive)
                if ohjaus:
                    self.send_response(302)
                    self.send_header("Location", ohjaus)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps({"ok": True, "result": []}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        class S(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, *a):
                pass

        self.httpd = S(("127.0.0.1", 0), H)
        self.portti = self.httpd.server_address[1]
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True).start()

    @property
    def api(self):
        return f"http://127.0.0.1:{self.portti}"

    def sulje(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class SiirtoTesti(Pohja):
    def setUp(self):
        super().setUp()
        self.palvelimet = []
        self.addCleanup(lambda: [f.sulje() for f in self.palvelimet])

    def feikki(self, **kw):
        f = _Feikki(**kw)
        self.palvelimet.append(f)
        return f

    def test_oletus_on_vain_https_api_telegram_org(self):
        self.assertEqual(send.TELEGRAM_API, "https://api.telegram.org")
        for url in ("http://api.telegram.org/bot1/getUpdates", "https://evil.test/bot1/getUpdates", "https://api.telegram.org.evil.test/bot1/x",
                    "file:///etc/passwd", "ftp://api.telegram.org/bot1/x", "https://api.telegram.org@evil.test/bot1/x"):
            with self.assertRaises(komennot.KomentoVirhe):
                komennot.post(url, {})

    def test_uudelleenohjausta_ei_seurata(self):
        kohde = self.feikki()
        lahde = self.feikki(ohjaus=kohde.api + "/bot1/getUpdates")
        with mock.patch.object(send, "TELEGRAM_API", lahde.api):
            with self.assertRaises(urllib.error.HTTPError):
                komennot.post(lahde.api + "/bot1/getUpdates", {"a": 1})
        self.assertEqual(len(lahde.pyynnot), 1)
        self.assertEqual(kohde.pyynnot, [])

    def test_aikakatkaisu_on_kaytossa_eika_paljasta_tokenia(self):
        hidas = self.feikki(viive=1.5)
        self.aseta_offset(1)
        with mock.patch.object(send, "TELEGRAM_API", hidas.api), mock.patch.object(komennot, "AIKAKATKAISU", 0.3):
            koodi = komennot.ajo(env=self.env, nyt=NYT, arkisto=self.arkisto, hakemisto=self.ehd, loki=self.loki, viive=0)
        self.assertEqual(koodi, 0)                                                            # aikakatkaisu on ohimenevä
        self.assertNotIn(TOKEN, self.loki_teksti())
        self.assertIn("tapahtuma=haku_virhe_ohimeneva", self.loki_rivit()[-1])

    def test_aito_siirto_toimii_paikallista_palvelinta_vasten(self):
        f = self.feikki()
        self.aseta_offset(1)
        with mock.patch.object(send, "TELEGRAM_API", f.api):
            koodi = komennot.ajo(env=self.env, nyt=NYT, arkisto=self.arkisto, hakemisto=self.ehd, loki=self.loki, viive=0)
        self.assertEqual(koodi, 0)
        self.assertEqual(f.pyynnot, [f"/bot{TOKEN}/getUpdates"])


class YksikkoTesti(unittest.TestCase):
    def _lue(self, nimi):
        return (ROOT / "systemd" / nimi).read_text(encoding="utf-8")

    def _direktiivit(self, teksti):
        return [r.strip() for r in teksti.splitlines() if "=" in r and not r.lstrip().startswith("#")]

    def test_palvelu_on_yhta_kovennettu_kuin_paivakatsaus(self):
        vanha, uusi = self._direktiivit(self._lue("paivakatsaus.service")), self._direktiivit(self._lue("paivakatsaus-komennot.service"))
        sallitut_erot = ("Description=", "ExecStart=", "TimeoutStartSec=")
        vanha_ydin = {d for d in vanha if not d.startswith(sallitut_erot)}
        uusi_ydin = {d for d in uusi if not d.startswith(sallitut_erot)}
        self.assertEqual(uusi_ydin, vanha_ydin)
        for d in ("NoNewPrivileges=true", "ProtectSystem=strict", "CapabilityBoundingSet=", "MemoryDenyWriteExecute=yes", "User=paivakatsaus"):
            self.assertIn(d, uusi)

    def test_palvelun_kirjoitusoikeudet_vain_arkisto_ehdotukset_loki(self):
        rw = [d for d in self._direktiivit(self._lue("paivakatsaus-komennot.service")) if d.startswith("ReadWritePaths=")]
        self.assertEqual(len(rw), 1)
        self.assertEqual(set(rw[0].split("=", 1)[1].split()),
                         {"/opt/agentscope/paivakatsaus/arkisto", "/opt/agentscope/paivakatsaus/ehdotukset", "/opt/agentscope/paivakatsaus/paivakatsaus.log"})

    def test_palvelu_ajaa_vain_komennot_py_ilman_lippuja(self):
        teksti = self._lue("paivakatsaus-komennot.service")
        self.assertIn("Type=oneshot", teksti)
        self.assertRegex(teksti, r"(?m)^ExecStart=/usr/bin/python3 -B /opt/agentscope/paivakatsaus/komennot\.py$")
        self.assertRegex(teksti, r"(?m)^EnvironmentFile=/etc/paivakatsaus\.env$")
        timeout = int(re.search(r"(?m)^TimeoutStartSec=(\d+)$", teksti).group(1))
        self.assertTrue(60 <= timeout <= 300)

    def test_ajastin_2_min_valein_ilman_catchup(self):
        t = self._lue("paivakatsaus-komennot.timer")
        self.assertRegex(t, r"(?m)^OnUnitActiveSec=2min$")
        self.assertRegex(t, r"(?m)^OnBootSec=\d+min$")
        self.assertNotIn("Persistent=true", t)
        self.assertRegex(t, r"(?m)^Unit=paivakatsaus-komennot\.service$")
        self.assertIn("WantedBy=timers.target", t)


class JournaaliJaOhimenevaTesti(Pohja):
    """Virhetyyppi näkyy journalissa (stderr) ja ohimenevä verkkovirhe ei jätä palvelua failed-tilaan."""

    def aja_stderr(self, virhe):
        import contextlib
        import io
        self.aseta_offset(100)
        p = Palvelin([paivitys(100, "/hyvaksy 3")], hae_virhe=virhe)
        puskuri = io.StringIO()
        with contextlib.redirect_stderr(puskuri):
            koodi = komennot.ajo(env=self.env, nyt=NYT, arkisto=self.arkisto, hakemisto=self.ehd, loki=self.loki, http=p, viive=0)
        return koodi, puskuri.getvalue()

    def http_virhe(self, koodi):
        return urllib.error.HTTPError("https://api.telegram.org/bot" + TOKEN + "/getUpdates", koodi, "viesti " + TOKEN, {}, None)

    def test_ohimeneva_verkkovirhe_koodi_0_ja_tyyppi_journaliin(self):
        for virhe, tyyppi in ((urllib.error.URLError("nimi " + TOKEN), "URLError"), (TimeoutError("hidas " + TOKEN), "TimeoutError"),
                              (ConnectionResetError("katkesi"), "ConnectionResetError")):
            koodi, err = self.aja_stderr(virhe)
            self.assertEqual(koodi, 0, tyyppi)
            self.assertIn("tapahtuma=haku_virhe_ohimeneva", err)
            self.assertIn("tyyppi=" + tyyppi, err)
            self.assertNotIn(TOKEN, err)
            self.assertEqual(self.offset(), 100)

    def test_http_5xx_ja_429_ovat_ohimenevia_ja_tila_nakyy(self):
        for tila in (429, 500, 502, 503):
            koodi, err = self.aja_stderr(self.http_virhe(tila))
            self.assertEqual(koodi, 0, tila)
            self.assertIn("tyyppi=HTTPError", err)
            self.assertIn(f"http={tila}", err)
            self.assertNotIn(TOKEN, err)

    def test_pysyva_http_virhe_palauttaa_1_ja_tila_nakyy(self):
        for tila in (400, 401, 404, 409):
            koodi, err = self.aja_stderr(self.http_virhe(tila))
            self.assertEqual(koodi, 1, tila)
            self.assertIn("tapahtuma=haku_virhe ", err + " ")
            self.assertNotIn("_ohimeneva", err)
            self.assertIn(f"http={tila}", err)
            self.assertNotIn(TOKEN, err)

    def test_pysyva_ei_verkkovirhe_palauttaa_1(self):
        koodi, err = self.aja_stderr(ValueError("rikki " + TOKEN))
        self.assertEqual(koodi, 1)
        self.assertIn("tyyppi=ValueError", err)
        self.assertNotIn(TOKEN, err)

    def test_rikkinainen_vastaus_on_pysyva(self):
        self.aseta_offset(100)
        p = Palvelin([], hae_vastaus={"ok": False})
        self.assertEqual(self.aja(p), 1)
        self.assertEqual(self.offset(), 100)


if __name__ == "__main__":
    unittest.main()
