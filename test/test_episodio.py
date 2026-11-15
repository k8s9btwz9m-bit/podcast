"""Eliminare un solo episodio: motore, riga di comando, opzioni sconosciute."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import yt2podcast
from aiuti import (installa, progetto_finto, leggi_stato, leggi_cfg, canale,
                   scrivi_stato, finto_feed, finto_download_ok, finto_git,
                   aggiungi_playlist_finta, crea_file)

V1, V2, V3 = "AAAAAAAAAA1", "AAAAAAAAAA2", "AAAAAAAAAA3"


def ep(ident, titolo, giorno):
    return {"id": ident, "title": titolo, "file": f"{ident}.mp3",
            "image": f"{ident}.jpg", "published": f"2026-01-{giorno:02d}T10:00:00+00:00",
            "description": "", "duration": 600}


def leggi(radice, rel):
    with open(os.path.join(radice, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = progetto_finto(self._t.name)
        installa(self)
        self.cfg = leggi_cfg(self.d)

    def con_episodi(self, slug, ids=(V1, V2)):
        eps = [ep(i, f"Titolo {i}", 20 - n) for n, i in enumerate(ids)]
        for e in eps:
            for chiave in ("file", "image"):
                crea_file(self.d, f"docs/audio/{slug}/{e[chiave]}")
        scrivi_stato(self.d, slug, {"episodes": eps, "purged": ["ZZZZZZZZZZZ"]})
        return eps

    def audio(self, slug, nome):
        return os.path.join(self.d, "docs", "audio", slug, nome)


class TestRimuoviSingolo(Base):
    def test_cancella_file_stato_e_ricorda(self):
        self.con_episodi("a")
        yt2podcast.rimuovi_singolo("a", V1)
        st = leggi_stato(self.d, "a")
        self.assertEqual([e["id"] for e in st["episodes"]], [V2])
        self.assertEqual(st["purged"], sorted([V1, "ZZZZZZZZZZZ"]))
        self.assertFalse(os.path.exists(self.audio("a", V1 + ".mp3")))
        self.assertFalse(os.path.exists(self.audio("a", V1 + ".jpg")))
        self.assertTrue(os.path.exists(self.audio("a", V2 + ".mp3")))

    def test_errori(self):
        self.con_episodi("a")
        for slug, ident, msg in (("zzz", V1, "Show sconosciuto: zzz"),
                                 ("a", V3, "Episodio non trovato: " + V3)):
            with self.assertRaises(yt2podcast.Errore) as c:
                yt2podcast.rimuovi_singolo(slug, ident)
            self.assertEqual(str(c.exception), msg)
        for slug, ident in (("../x", V1), ("A", V1), ("a", "corto"),
                            ("a", "../../etc/pa"), ("a", ""), ("a", None)):
            with self.assertRaises(yt2podcast.Errore):
                yt2podcast.rimuovi_singolo(slug, ident)
        self.assertEqual(len(leggi_stato(self.d, "a")["episodes"]), 2)

    def test_episodio_di_un_altro_show_non_si_trova(self):
        aggiungi_playlist_finta(self.d, "pl", [V1])
        self.con_episodi("a", (V1,))
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.rimuovi_singolo("pl", V1)

    def test_canale_non_riscarica(self):
        self.con_episodi("a")
        yt2podcast.rimuovi_singolo("a", V1)
        finto_feed([V1, V2]); chiamati = finto_download_ok()
        yt2podcast.process_channel(self.cfg, canale(self.d, "a"))
        self.assertEqual(chiamati, [])
        self.assertEqual([e["id"] for e in leggi_stato(self.d, "a")["episodes"]], [V2])

    def test_playlist_non_riscarica_e_conserva_purged(self):
        blocco = aggiungi_playlist_finta(self.d, "pl", [V1, V2])
        self.con_episodi("pl")
        yt2podcast.rimuovi_singolo("pl", V1)
        chiamati = finto_download_ok()
        yt2podcast.process_playlist(self.cfg, blocco)
        self.assertEqual(chiamati, [])
        st = leggi_stato(self.d, "pl")
        self.assertEqual([e["id"] for e in st["episodes"]], [V2])
        self.assertIn(V1, st["purged"])

    def test_show_manuale_e_riaggiunta_deliberata(self):
        self.con_episodi("a")
        cfg = leggi_cfg(self.d)
        yt2podcast.ensure_manual_channel(cfg)
        yt2podcast.save_json(yt2podcast.CONFIG_FILE, cfg)
        self.con_episodi("youtube")
        yt2podcast.rimuovi_singolo("youtube", V1)
        self.assertIn(V1, leggi_stato(self.d, "youtube")["purged"])
        meta = {"id": V1, "title": "Di nuovo", "duration": 600}
        finto_download_ok()
        with mock.patch.object(yt2podcast, "video_metadata", return_value=meta):
            yt2podcast.add_video("https://youtu.be/" + V1, leggi_cfg(self.d))
        st = leggi_stato(self.d, "youtube")
        self.assertIn(V1, [e["id"] for e in st["episodes"]])
        self.assertNotIn(V1, st["purged"])
        self.assertIn("ZZZZZZZZZZZ", st["purged"])


class TestCliEpisodio(Base):
    def _main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(yt2podcast.sys, "argv", ["yt2podcast.py", *argv]), \
                mock.patch.object(yt2podcast, "check_requirements"), \
                redirect_stdout(out):
            try:
                yt2podcast.main()
                codice = 0
            except SystemExit as e:
                codice = e.code
        return codice, out.getvalue()

    def test_remove_episode_rigenera_il_feed_senza_l_episodio(self):
        self.con_episodi("a")
        yt2podcast.build_feed(self.cfg, canale(self.d, "a"),
                              leggi_stato(self.d, "a")["episodes"])
        self.assertIn(V1, leggi(self.d, "docs/a.xml"))
        codice, _ = self._main("--remove-episode", "a", V1, "--senza-attesa")
        self.assertEqual(codice, 0)
        xml = leggi(self.d, "docs/a.xml")
        self.assertNotIn(V1, xml)
        self.assertIn(V2, xml)
        self.assertIn(V1, leggi_stato(self.d, "a")["purged"])

    def test_azione_e_avanzamento(self):
        self.con_episodi("a")
        viste = []
        with mock.patch.object(yt2podcast, "_avanza",
                               side_effect=lambda **k: viste.append(k)), \
                mock.patch.object(yt2podcast, "giro",
                                  return_value={"esito": "riuscito"}) as gi:
            self.assertEqual(self._main("--remove-episode", "a", V1)[0], 0)
        self.assertEqual(gi.call_args.kwargs["azione"], "rimuovi-episodio")
        self.assertFalse(gi.call_args.kwargs["elabora"])
        self.assertEqual(viste[0], {"testo": f"Elimino Titolo {V1}…", "show": "a"})

    def test_errore_esce_con_uno(self):
        self.con_episodi("a")
        self.assertEqual(self._main("--remove-episode", "a", V3)[0], 1)
        self.assertEqual(self._main("--remove-episode", "zzz", V1)[0], 1)

    def test_id_che_inizia_con_trattino(self):
        ident = "-AAAAAAAAA1"
        self.con_episodi("a", (ident, V2))
        with mock.patch.object(yt2podcast, "giro",
                               return_value={"esito": "riuscito"}):
            self.assertEqual(self._main("--remove-episode", "a", ident)[0], 0)
        self.assertEqual([e["id"] for e in leggi_stato(self.d, "a")["episodes"]], [V2])


class TestOpzioniSconosciute(Base):
    _main = TestCliEpisodio._main

    def test_rifiuta_prima_di_fare_qualsiasi_cosa(self):
        with mock.patch.object(yt2podcast, "giro") as gi, \
                mock.patch.object(yt2podcast.stato_giro, "Blocco") as bl:
            for brutta in ("--prova", "--remove-episod", "-x"):
                codice, _ = self._main(brutta)
                self.assertEqual(codice, 1, brutta)
            self.assertEqual(self._main("--remove", "a", "--prova")[0], 1)
        gi.assert_not_called()
        bl.assert_not_called()

    def test_messaggio(self):
        with mock.patch.object(yt2podcast, "log") as lg:
            self._main("--prova")
        lg.assert_called_once_with("Opzione sconosciuta: --prova")

    def test_help_stampa_il_manuale(self):
        with mock.patch.object(yt2podcast, "giro") as gi:
            for h in ("-h", "--help"):
                codice, out = self._main(h)
                self.assertEqual(codice, 0)
                self.assertIn("--remove-episode", out)
        gi.assert_not_called()

    def test_i_valori_non_sono_opzioni(self):
        self.assertIsNone(yt2podcast._opzione_sconosciuta(
            ["--remove-episode", "a", "-AAAAAAAAAA", "--senza-attesa"]))
        self.assertIsNone(yt2podcast._opzione_sconosciuta(
            ["--cover", "a", "-strano.png", "--regole", "-1"]))
        self.assertIsNone(yt2podcast._opzione_sconosciuta([]))
        self.assertEqual(yt2podcast._opzione_sconosciuta(
            ["--only", "a", "--boh"]), "--boh")


if __name__ == "__main__":
    unittest.main()
