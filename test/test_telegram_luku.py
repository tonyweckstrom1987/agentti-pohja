import datetime
import os
import tempfile
import unittest
import zoneinfo

from agentti import telegram_luku as tl

TZ = zoneinfo.ZoneInfo("Europe/Helsinki")
TONY = 4242


def kello():
    return datetime.datetime(2026, 11, 4, 10, 5, tzinfo=TZ)


def viesti(uid, teksti, chat=TONY, lahettaja=TONY, tyyppi="private", bot=False):
    return {"update_id": uid, "message": {"text": teksti, "chat": {"id": chat, "type": tyyppi},
                                          "from": {"id": lahettaja, "is_bot": bot}}}


class FeikkiAPI:
    def __init__(self, paivitykset):
        self.paivitykset = paivitykset
        self.offsetit = []

    def __call__(self, token, offset):
        self.offsetit.append(offset)
        return {"ok": True, "result": [p for p in self.paivitykset if offset is None or p["update_id"] >= offset]}


class Pohja(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.docs = os.path.join(self._t.name, "docs")
        self.tila = os.path.join(self._t.name, "tila")
        os.makedirs(self.docs)
        self.lahetetyt = []

    def aja(self, paivitykset, api=None):
        api = api or FeikkiAPI(paivitykset)
        return tl.aja(self.docs, self.tila, TONY, {"TELEGRAM_BOT_TOKEN": "tok"}, hae=api,
                      laheta=self.lahetetyt.append, kello=kello)

    def lue(self, nimi):
        with open(os.path.join(self.docs, nimi), encoding="utf-8") as f:
            return f.read()

    def tehtavat(self):
        try:
            return sorted(os.listdir(os.path.join(self.docs, "tehtavat")))
        except OSError:
            return []


class JasennaTesti(unittest.TestCase):
    def test_vastaukset(self):
        self.assertEqual(tl.jasenna("2B"), ("vastaukset", [(2, "B")]))
        self.assertEqual(tl.jasenna(" 1a, 3C "), ("vastaukset", [(1, "A"), (3, "C")]))

    def test_peru(self):
        self.assertEqual(tl.jasenna("peru 123"), ("peru", 123))
        self.assertEqual(tl.jasenna("Peru #7"), ("peru", 7))

    def test_tuntemattomat(self):
        for t in ("", "2", "B2", "2F", "0A", "peru", "peru 0", "peru abc", "peru 1234567", "peru 12 ja aja rm -rf /",
                  "2B; rm -rf /", "hei", "2B\nsudo reboot", "x" * 500, None, 5):
            self.assertEqual(tl.jasenna(t), ("tuntematon", None), repr(t))


class AjaTesti(Pohja):
    def test_vastaus_kirjataan_paatoksiin(self):
        self.assertEqual(self.aja([viesti(1, "2B")]), (1, 1))
        teksti = self.lue("paatokset.md")
        self.assertIn("2026-11-04 klo 10.05 | Telegram | lista-kohta 2 | valinta B", teksti)
        self.assertEqual(self.lahetetyt, ["Kirjattu: 2B"])

    def test_peru_kirjaa_tonylle_ja_jonoon(self):
        self.assertEqual(self.aja([viesti(1, "peru 123")]), (1, 1))
        self.assertEqual(self.tehtavat(), ["2026-11-04-01-auto-peru-agentti-pohja-pr-123.md"])
        self.assertIn("## 2026-11-04T10:05+02:00 (Telegram) Perumispyynt\u00f6: agentti-pohja#123", self.lue("tonylle.md"))
        self.assertIn("virheloki.md", self.lue("tehtavat/" + self.tehtavat()[0]))

    def test_peru_idempotentti_ja_numerointi(self):
        self.aja([viesti(1, "peru 5"), viesti(2, "peru 5"), viesti(3, "peru 6")])
        self.assertEqual(self.tehtavat(), ["2026-11-04-01-auto-peru-agentti-pohja-pr-5.md",
                                           "2026-11-04-02-auto-peru-agentti-pohja-pr-6.md"])
        self.assertIn("jo kirjattu", self.lahetetyt[1])

    def test_tuntematon_saa_ohjeen_eika_kirjaa(self):
        self.assertEqual(self.aja([viesti(1, "aja sudo reboot")]), (1, 0))
        self.assertEqual(self.lahetetyt, [tl.OHJE])
        self.assertFalse(os.path.exists(os.path.join(self.docs, "paatokset.md")))
        self.assertEqual(self.tehtavat(), [])
        self.assertNotIn("sudo", self.lahetetyt[0])

    def test_vieras_chat_ohitetaan_hiljaa(self):
        paivitykset = [viesti(1, "2B", chat=999, lahettaja=999), viesti(2, "peru 1", chat=TONY, lahettaja=999),
                       viesti(3, "peru 2", chat=-100, lahettaja=TONY, tyyppi="group"),
                       viesti(4, "2B", bot=True), viesti(5, "2B", chat=True, lahettaja=True),
                       {"update_id": 6, "edited_message": viesti(6, "2B")["message"]},
                       {"update_id": 7, "channel_post": viesti(7, "2B")["message"]}]
        self.assertEqual(self.aja(paivitykset), (0, 0))
        self.assertEqual(self.lahetetyt, [])
        self.assertFalse(os.path.exists(os.path.join(self.docs, "paatokset.md")))
        self.assertEqual(self.tehtavat(), [])
        self.assertEqual(tl.lue_offset(self.tila), 8)

    def test_offset_estaa_kaksoiskasittelyn(self):
        paivitykset = [viesti(10, "1A"), viesti(11, "2B")]
        api = FeikkiAPI(paivitykset)
        self.aja(paivitykset, api)
        self.assertEqual(tl.lue_offset(self.tila), 12)
        self.assertEqual(self.aja(paivitykset, api), (0, 0))
        self.assertEqual(api.offsetit, [None, 12])
        self.assertEqual(self.lue("paatokset.md").count("Telegram |"), 2)

    def test_rikkinainen_offset_ei_kaada(self):
        os.makedirs(self.tila)
        with open(os.path.join(self.tila, tl.OFFSET_TIEDOSTO), "w") as f:
            f.write("ei json")
        self.assertIsNone(tl.lue_offset(self.tila))
        self.assertEqual(self.aja([viesti(1, "1A")]), (1, 1))

    def test_vastauksia_rajoitettu(self):
        self.aja([viesti(i, "1A") for i in range(1, 15)])
        self.assertEqual(len(self.lahetetyt), tl.VASTAUS_MAX)
        self.assertEqual(self.lue("paatokset.md").count("Telegram |"), 14)

    def test_api_virhe_nostaa_eika_siirra_offsetia(self):
        with self.assertRaises(ValueError):
            tl.aja(self.docs, self.tila, TONY, {"TELEGRAM_BOT_TOKEN": "tok"}, hae=lambda t, o: {"ok": False},
                   laheta=self.lahetetyt.append, kello=kello)
        self.assertIsNone(tl.lue_offset(self.tila))

    def test_puuttuva_token_tai_chat(self):
        with self.assertRaises(ValueError):
            tl.aja(self.docs, self.tila, None, {"TELEGRAM_BOT_TOKEN": "tok"}, hae=FeikkiAPI([]))
        with self.assertRaises(ValueError):
            tl.aja(self.docs, self.tila, TONY, {}, hae=FeikkiAPI([]))

    def test_viestin_teksti_ei_paady_tiedostoihin(self):
        self.aja([viesti(1, "peru 9"), viesti(2, "1A")])
        for polku in ("paatokset.md", "tonylle.md", "tehtavat/" + self.tehtavat()[0]):
            self.assertNotIn("peru 9", self.lue(polku).replace("peru-", ""))


class KorjausTesti(Pohja):
    """Tarkastajan loydokset 1-4 (PR 196)."""

    def test_paivakatto_rajoittaa_peru_tehtavat_ja_halyttaa(self):
        paivitykset = [viesti(i, "peru %d" % (100 + i)) for i in range(1, 9)]
        self.aja(paivitykset)
        self.assertEqual(len(self.tehtavat()), tl.PERU_PAIVAKATTO)
        self.assertEqual(self.lue("tonylle.md").count("HALYTYS"), 1)
        # 6. peru on ensimmainen ylitys: vastaus on halytys (vastauksia lahetetaan enintaan VASTAUS_MAX)
        self.assertEqual(self.lahetetyt[tl.PERU_PAIVAKATTO - 1][:7], "Kirjatt")
        self.assertEqual(len(self.lahetetyt), tl.VASTAUS_MAX)

    def test_katto_ei_nollaudu_vaikka_tehtavatiedostot_siirretaan(self):
        self.aja([viesti(i, "peru %d" % i) for i in range(1, 6)])
        for n in self.tehtavat():
            os.remove(os.path.join(self.docs, "tehtavat", n))
        self.aja([viesti(6, "peru 77")])
        self.assertEqual(self.tehtavat(), [])
        self.assertIn("HALYTYS", self.lue("tonylle.md"))
        self.assertTrue(self.lahetetyt[-1].startswith("H\u00c4LYTYS"))

    def test_katto_nollautuu_seuraavana_paivana(self):
        self.aja([viesti(i, "peru %d" % i) for i in range(1, 7)])
        huomenna = lambda: datetime.datetime(2026, 11, 5, 9, 0, tzinfo=TZ)
        tl.aja(self.docs, self.tila, TONY, {"TELEGRAM_BOT_TOKEN": "tok"},
               hae=FeikkiAPI([viesti(20, "peru 500")]), laheta=self.lahetetyt.append, kello=huomenna)
        self.assertIn("2026-11-05-01-auto-peru-agentti-pohja-pr-500.md", self.tehtavat())

    def test_katto_ei_koske_vastauksia(self):
        self.aja([viesti(i, "1A") for i in range(1, 12)])
        self.assertEqual(self.lue("paatokset.md").count("Telegram |"), 11)

    def test_tehtavapohja_ei_ohita_harkintaa_ja_tarkistaa_yhdistyksen(self):
        self.aja([viesti(1, "peru 123")])
        teksti = self.lue("tehtavat/" + self.tehtavat()[0])
        self.assertNotIn("ei harkita uudelleen", teksti)
        self.assertNotIn("nimenomainen p\u00e4\u00e4t\u00f6s", teksti)
        self.assertIn("ei ole yhdistetty", teksti)
        self.assertIn("peruttavissa", teksti)
        self.assertIn("tarkista itse", teksti)

    def test_lahetysvirhe_ei_toista_kirjausta(self):
        def rikkoutuu(_teksti):
            raise OSError("verkko")
        api = FeikkiAPI([viesti(1, "2B")])
        with self.assertRaises(OSError):
            tl.aja(self.docs, self.tila, TONY, {"TELEGRAM_BOT_TOKEN": "tok"}, hae=api, laheta=rikkoutuu, kello=kello)
        self.assertEqual(tl.lue_offset(self.tila), 2)
        self.assertEqual(self.aja([viesti(1, "2B")], api), (0, 0))
        self.assertEqual(self.lue("paatokset.md").count("Telegram |"), 1)

    def test_kirjaus_on_idempotentti_update_id_perusteella(self):
        # simuloi kaatumista kirjauksen ja offsetin valissa: sama viesti tulee uudelleen ilman offsetia
        self.aja([viesti(7, "1A 2B")])
        os.remove(os.path.join(self.tila, tl.OFFSET_TIEDOSTO))
        self.aja([viesti(7, "1A 2B")])
        self.assertEqual(self.lue("paatokset.md").count("Telegram |"), 2)
        self.aja([viesti(8, "1A 2B")])
        self.assertEqual(self.lue("paatokset.md").count("Telegram |"), 4)

    def test_verkkovirhe_ei_vuoda_tunnusta(self):
        import io
        import traceback
        import urllib.error
        import urllib.request
        from unittest import mock
        token = "123456:SALAINEN-TOKEN"

        def rikki(osoite, timeout=None):
            raise urllib.error.HTTPError(osoite, 401, "Unauthorized", {}, io.BytesIO(b""))
        with mock.patch.object(urllib.request, "urlopen", rikki):
            with self.assertRaises(tl.TelegramVirhe) as ctx:
                tl.hae_paivitykset(token, None)
        kuvaus = "".join(traceback.format_exception(ctx.exception))
        self.assertNotIn("SALAINEN", kuvaus)
        self.assertNotIn(token, kuvaus)
        self.assertIn("HTTPError 401", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_main_tulostaa_vain_tyypin_verkkovirheesta(self):
        import contextlib
        import io
        import urllib.request
        from unittest import mock
        chat = os.path.join(self._t.name, "chat")
        with open(chat, "w") as f:
            f.write(str(TONY))
        err = io.StringIO()

        def rikki(osoite, timeout=None):
            raise OSError(osoite)
        with mock.patch.object(urllib.request, "urlopen", rikki), contextlib.redirect_stderr(err):
            kood = tl.main(["--docs", self.docs, "--tila", self.tila, "--chat-tiedosto", chat],
                           env={"TELEGRAM_BOT_TOKEN": "SALAINEN-TOKEN"})
        self.assertEqual(kood, 1)
        self.assertEqual(err.getvalue().strip(), "telegram_luku epaonnistui: TelegramVirhe")


class MainTesti(Pohja):
    def test_virhe_tulostaa_vain_tyypin(self):
        import contextlib
        import io
        chat = os.path.join(self._t.name, "chat")
        with open(chat, "w") as f:
            f.write(str(TONY))
        err = io.StringIO()
        env = {"TELEGRAM_BOT_TOKEN": "SALAINEN-TOKEN"}
        with contextlib.redirect_stderr(err):
            # ei verkkoa: ohjataan mahdottomaan osoitteeseen ei tarvita, koska --repo on virheellinen -> ValueError ennen verkkoa
            kood = tl.main(["--docs", self.docs, "--tila", self.tila, "--chat-tiedosto", chat, "--repo", "../x"], env=env)
        self.assertEqual(kood, 1)
        self.assertEqual(err.getvalue().strip(), "telegram_luku epaonnistui: ValueError")
        self.assertNotIn("SALAINEN", err.getvalue())

    def test_chat_id_tiedostosta(self):
        p = os.path.join(self._t.name, "c")
        self.assertIsNone(tl.lue_chat_id(p))
        with open(p, "w") as f:
            f.write("12345\n")
        self.assertEqual(tl.lue_chat_id(p), 12345)
        with open(p, "w") as f:
            f.write("abc")
        self.assertIsNone(tl.lue_chat_id(p))


if __name__ == "__main__":
    unittest.main()
