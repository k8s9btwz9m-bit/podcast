import tempfile
import unittest

import yt2podcast
from aiuti import (installa, progetto_finto, scrivi_stato, leggi_stato,
                   leggi_cfg, canale, finto_feed, finto_download)


class TestCanale(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = progetto_finto(self._t.name)
        installa(self)
        self.cfg = leggi_cfg(self.d)
        self.ch = canale(self.d, "a")

    def test_formato_vecchio(self):
        scrivi_stato(self.d, "a", {"episodes": [], "skipped": ["OLD1"]})
        yt2podcast.process_channel(self.cfg, self.ch)
        s = leggi_stato(self.d, "a")
        self.assertIn("OLD1", s["skipped"])
        self.assertEqual(s["skipped_info"], {})
        self.assertEqual(s["falliti"], {})

    def test_stato_mancante(self):
        r = yt2podcast.process_channel(self.cfg, self.ch)
        s = leggi_stato(self.d, "a")
        self.assertEqual(s["skipped_info"], {})
        self.assertEqual(s["falliti"], {})
        self.assertEqual(r["tentati"], 0)

    def test_fallito_si_ritenta_tre_volte(self):
        finto_feed(["V1"])
        finto_download({"V1": (None, "HTTP Error 403: Forbidden", "fallito")})
        for giro in (1, 2):
            yt2podcast.process_channel(self.cfg, self.ch)
            s = leggi_stato(self.d, "a")
            self.assertEqual(s["falliti"]["V1"]["tentativi"], giro)
            self.assertNotIn("V1", s["skipped"])
        yt2podcast.process_channel(self.cfg, self.ch)
        s = leggi_stato(self.d, "a")
        self.assertIn("V1", s["skipped"])
        self.assertEqual(s["skipped_info"]["V1"]["kind"], "fallito")
        self.assertNotIn("V1", s["falliti"])

    def test_regola_titolo_non_scarica(self):
        self.ch["title_exclude"] = ["giveaway"]
        finto_feed([("V2", "Two new books & a GIVEAWAY!")])
        chiamati = finto_download({})
        r = yt2podcast.process_channel(self.cfg, self.ch)
        self.assertEqual(chiamati, [])
        self.assertEqual(leggi_stato(self.d, "a")["skipped_info"]["V2"]["reason"],
                         "titolo con: giveaway")
        self.assertEqual(r["saltati"], 1)

    def test_filtro_yt_dlp_va_fra_i_saltati(self):
        finto_feed(["V3"])
        finto_download({"V3": (None, "does not pass filter", "regola")})
        r = yt2podcast.process_channel(self.cfg, self.ch)
        s = leggi_stato(self.d, "a")
        self.assertIn("V3", s["skipped"])
        self.assertEqual(s["skipped_info"]["V3"]["kind"], "regola")
        self.assertEqual(s["skipped_info"]["V3"]["reason"],
                         "fuori dai limiti di durata")
        self.assertEqual((r["saltati"], r["falliti"], r["tentati"], r["nuovi"]),
                         (1, 0, 1, 0))

    def test_contatori_e_successo(self):
        finto_feed(["V4"])
        finto_download({})
        r = yt2podcast.process_channel(self.cfg, self.ch)
        self.assertEqual((r["nuovi"], r["tentati"], r["count"], r["slug"]),
                         (1, 1, 1, "a"))
        self.assertEqual(leggi_stato(self.d, "a")["episodes"][0]["id"], "V4")

    def test_canale_senza_id_solleva_errore(self):
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.add_channel("non-un-id")


if __name__ == "__main__":
    unittest.main()
