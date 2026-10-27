import json
import unittest

from agentti import skeema, tulos

S = {"type": "object", "required": ["a"], "properties": {
    "a": {"type": "string", "maxLength": 5},
    "n": {"type": "integer"},
    "l": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
    "e": {"type": "string", "enum": ["x", "y"]}}}


class SkeemaTesti(unittest.TestCase):
    def virhe(self, arvo):
        with self.assertRaises(skeema.SkeemaVirhe):
            skeema.tarkista(arvo, S)

    def test_kelvollinen(self):
        skeema.tarkista({"a": "abc", "n": 3, "l": ["q"], "e": "x"}, S)

    def test_puuttuva_pakollinen(self):
        self.virhe({})

    def test_ylimaarainen_kentta(self):
        self.virhe({"a": "x", "muu": 1})

    def test_vaara_tyyppi(self):
        self.virhe({"a": 5})
        self.virhe({"a": "x", "n": "3"})

    def test_bool_ei_ole_kokonaisluku(self):
        self.virhe({"a": "x", "n": True})

    def test_enum_pituus_ja_lista(self):
        self.virhe({"a": "x", "e": "z"})
        self.virhe({"a": "liianpitka"})
        self.virhe({"a": "x", "l": ["a", "b", "c"]})
        self.virhe({"a": "x", "l": [1]})

    def test_tuntematon_tyyppi_skeemassa(self):
        with self.assertRaises(skeema.SkeemaVirhe):
            skeema.tarkista(1, {"type": "foo"})


class SkeemaTarkkaTesti(unittest.TestCase):
    def kelpaa(self, arvo, skeema_):
        skeema.tarkista(arvo, skeema_)

    def hylkaa(self, arvo, skeema_):
        with self.assertRaises(skeema.SkeemaVirhe):
            skeema.tarkista(arvo, skeema_)

    def test_number_hyvaksyy_int_ja_float_mutta_ei_boolia(self):
        self.kelpaa(1, {"type": "number"})
        self.kelpaa(1.5, {"type": "number"})
        self.hylkaa(True, {"type": "number"})
        self.hylkaa(False, {"type": "number"})
        self.hylkaa("1", {"type": "number"})

    def test_integer_ei_hyvaksy_floatia(self):
        self.kelpaa(3, {"type": "integer"})
        self.hylkaa(3.5, {"type": "integer"})

    def test_boolean_hyvaksyy_vain_boolit(self):
        self.kelpaa(True, {"type": "boolean"})
        self.kelpaa(False, {"type": "boolean"})
        self.hylkaa(1, {"type": "boolean"})
        self.hylkaa("true", {"type": "boolean"})

    def test_tyhja_merkkijono_kelpaa_ilman_minlengthia(self):
        self.kelpaa("", {"type": "string"})
        self.hylkaa("", {"type": "string", "minLength": 1})
        self.kelpaa("a", {"type": "string", "minLength": 1})

    def test_maxlength_raja_on_tarkka(self):
        self.kelpaa("abcde", {"type": "string", "maxLength": 5})
        self.hylkaa("abcdef", {"type": "string", "maxLength": 5})

    def test_lista_oletusraja_on_miljoona_alkiota(self):
        self.kelpaa([0] * 1000000, {"type": "array"})
        self.hylkaa([0] * 1000001, {"type": "array"})

    def test_maxitems_raja_on_tarkka_ja_virhepolku_nakyy(self):
        s_ = {"type": "array", "maxItems": 2, "items": {"type": "integer"}}
        self.kelpaa([1, 2], s_)
        with self.assertRaisesRegex(skeema.SkeemaVirhe, r"^\$\[1\]: odotettiin integer$"):
            skeema.tarkista([1, "x"], s_)
        with self.assertRaisesRegex(skeema.SkeemaVirhe, r"^\$: liian monta alkiota$"):
            skeema.tarkista([1, 2, 3], s_)

    def test_additional_properties_sallii_ylimaaraiset(self):
        self.kelpaa({"x": 1}, {"type": "object", "additionalProperties": True})
        self.hylkaa({"x": 1}, {"type": "object"})


SIS = {"type": "object", "required": ["t"], "properties": {"t": {"type": "string"}}}


class TulosTesti(unittest.TestCase):
    def test_ok(self):
        t = tulos.lue(json.dumps({"tila": "ok", "sisalto": {"t": "hei"}}), SIS)
        self.assertEqual((t.tila, t.sisalto["t"]), ("ok", "hei"))

    def test_ei_tietoa_on_sallittu(self):
        t = tulos.lue(json.dumps({"tila": "ei_tietoa", "syy": "data puuttuu"}), SIS)
        self.assertEqual((t.tila, t.syy, t.sisalto), ("ei_tietoa", "data puuttuu", None))

    def test_ei_tietoa_vaatii_syyn(self):
        for v in ({"tila": "ei_tietoa"}, {"tila": "ei_tietoa", "syy": "  "}):
            with self.assertRaises(tulos.TulosVirhe) as e:
                tulos.lue(json.dumps(v), SIS)
            self.assertEqual(e.exception.koodi, "llm_skeema")

    def test_ok_vaatii_sisallon(self):
        with self.assertRaises(tulos.TulosVirhe):
            tulos.lue(json.dumps({"tila": "ok"}), SIS)

    def test_vaara_sisalto(self):
        with self.assertRaises(tulos.TulosVirhe) as e:
            tulos.lue(json.dumps({"tila": "ok", "sisalto": {"t": 1}}), SIS)
        self.assertEqual(e.exception.koodi, "llm_skeema")

    def test_ei_json(self):
        with self.assertRaises(tulos.TulosVirhe) as e:
            tulos.lue("Tassa on vastaus.", SIS)
        self.assertEqual(e.exception.koodi, "llm_vastaus_ei_json")

    def test_koodiaita_poistetaan(self):
        teksti = "```json\n" + json.dumps({"tila": "ok", "sisalto": {"t": "a"}}) + "\n```"
        self.assertEqual(tulos.lue(teksti, SIS).sisalto["t"], "a")

    def test_tuntematon_tila(self):
        with self.assertRaises(tulos.TulosVirhe):
            tulos.lue(json.dumps({"tila": "ehka", "sisalto": {"t": "a"}}), SIS)


class TulosTarkkaTesti(unittest.TestCase):
    def test_syyn_enimmaispituus_on_500(self):
        def lue(n):
            return tulos.lue(json.dumps({"tila": "ei_tietoa", "syy": "x" * n}), SIS)
        self.assertEqual(lue(500).syy, "x" * 500)
        with self.assertRaises(tulos.TulosVirhe) as e:
            lue(501)
        self.assertEqual(e.exception.koodi, "llm_skeema")


if __name__ == "__main__":
    unittest.main()
