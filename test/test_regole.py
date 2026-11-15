import unittest

from regole import classifica_link, parole, regole_effettive, valuta


class TestClassifica(unittest.TestCase):
    def test_tipi(self):
        casi = {
            "https://www.youtube.com/watch?v=yMP3ZxUWOaQ": ("video", "yMP3ZxUWOaQ"),
            "  https://youtu.be/yMP3ZxUWOaQ?si=abc  ": ("video", "yMP3ZxUWOaQ"),
            "https://m.youtube.com/watch?v=yMP3ZxUWOaQ&list=PLabc123": ("video", "yMP3ZxUWOaQ"),
            "https://www.youtube.com/shorts/yMP3ZxUWOaQ": ("video", "yMP3ZxUWOaQ"),
            "https://www.youtube.com/playlist?list=PLabc123_-x": ("playlist", "PLabc123_-x"),
            "https://www.youtube.com/channel/UCoI9lb-zX8bzSndrEqm1y7g": ("canale", "UCoI9lb-zX8bzSndrEqm1y7g"),
            "UCoI9lb-zX8bzSndrEqm1y7g": ("canale", "UCoI9lb-zX8bzSndrEqm1y7g"),
            "https://www.youtube.com/@DrBlitz/videos": ("canale", None),
            "ciao": ("sconosciuto", None),
            "": ("sconosciuto", None),
        }
        for link, (tipo, ident) in casi.items():
            r = classifica_link(link)
            self.assertEqual((r["tipo"], r["id"]), (tipo, ident), link)

    def test_link_senza_schema(self):
        casi = {
            "youtube.com/watch?v=yMP3ZxUWOaQ": ("video", "yMP3ZxUWOaQ"),
            "www.youtube.com/@DrBlitz": ("canale", None),
            "youtu.be/yMP3ZxUWOaQ": ("video", "yMP3ZxUWOaQ"),
            "m.youtube.com/playlist?list=PLabc123": ("playlist", "PLabc123"),
            "@DrBlitz": ("sconosciuto", None),
            "esempio.it/watch?v=yMP3ZxUWOaQ": ("sconosciuto", None),
        }
        for link, (tipo, ident) in casi.items():
            r = classifica_link(link)
            self.assertEqual((r["tipo"], r["id"]), (tipo, ident), link)
        self.assertEqual(classifica_link("youtu.be/yMP3ZxUWOaQ")["url"],
                         "https://youtu.be/yMP3ZxUWOaQ")

    def test_input_strani(self):
        r = classifica_link("https://youtu.be/yMP3ZxUWOaQ/")
        self.assertEqual((r["tipo"], r["id"]), ("video", "yMP3ZxUWOaQ"))
        r = classifica_link("https://www.youtube.com/watch?list=PLxyz")
        self.assertEqual((r["tipo"], r["id"]), ("playlist", "PLxyz"))
        for testo in ("http://[", "   ", "https://", "https://esempio.it/pagina", "@@@", "\x00"):
            self.assertEqual(classifica_link(testo)["tipo"], "sconosciuto", testo)

    def test_url_ripulito(self):
        r = classifica_link("  https://youtu.be/yMP3ZxUWOaQ?si=abc  ")
        self.assertEqual(r["url"], "https://youtu.be/yMP3ZxUWOaQ?si=abc")


class TestParole(unittest.TestCase):
    def test_parole(self):
        self.assertEqual(parole(" Data Over Dogma, , GiveAway ,"), ["data over dogma", "giveaway"])
        self.assertEqual(parole(""), [])


class TestValuta(unittest.TestCase):
    R = {"min_s": 120, "max_s": 2700, "solo": [], "salta": ["giveaway"], "sponsor": True}

    def test_durata(self):
        self.assertEqual(valuta(self.R, "x", 3669), (False, "più lungo di 45 min"))
        self.assertEqual(valuta(self.R, "x", 60), (False, "più corto di 2 min"))
        self.assertEqual(valuta(self.R, "x", None), (True, ""))

    def test_titoli(self):
        self.assertEqual(valuta(self.R, "Two new books & a GIVEAWAY!", 600), (False, "titolo con: giveaway"))
        r = dict(self.R, solo=["data over dogma"], salta=[])
        self.assertEqual(valuta(r, "Chi erano? (Data Over Dogma)", 600), (True, ""))
        self.assertEqual(valuta(r, "Altro", 600), (False, "titolo senza: data over dogma"))

    def test_durata_sconosciuta_ma_titolo_giudicato(self):
        self.assertEqual(valuta(self.R, "giveaway", None), (False, "titolo con: giveaway"))

    def test_nessun_limite(self):
        r = dict(self.R, min_s=0, max_s=0, salta=[])
        self.assertEqual(valuta(r, "x", 99999), (True, ""))


class TestEffettive(unittest.TestCase):
    CFG = {"min_duration_minutes": 2, "max_duration_minutes": 120, "sponsorblock": True}

    def test_ricaduta_e_eccezioni(self):
        self.assertEqual(regole_effettive(self.CFG, {"slug": "a"})["max_s"], 7200)
        self.assertEqual(regole_effettive(self.CFG, {"slug": "a", "max_duration_minutes": 45})["max_s"], 2700)
        self.assertFalse(regole_effettive(self.CFG, {"slug": "a", "sponsorblock": False})["sponsor"])
        for speciale in ({"manual": True}, {"type": "playlist"}):
            r = regole_effettive(self.CFG, speciale)
            self.assertEqual((r["min_s"], r["max_s"]), (0, 0))

    def test_parole_dal_canale(self):
        r = regole_effettive(self.CFG, {"title_include": "A, b", "title_exclude": ""})
        self.assertEqual((r["solo"], r["salta"]), (["a", "b"], []))
