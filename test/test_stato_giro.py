import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from stato_giro import (Blocco, Occupato, in_corso, scrivi_avanzamento,
                        leggi_avanzamento, esito_giro, chiudi_giro,
                        traduci_errore, notifica, scrivi_ultimo_comando)
import contextlib
import io


class TestStatoGiro(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        self.addCleanup(self._t.cleanup)

    def test_esito(self):
        self.assertEqual(esito_giro(True, 5, 0), "riuscito")
        self.assertEqual(esito_giro(True, 5, 2), "problemi")
        self.assertEqual(esito_giro(True, 3, 3), "fallito")
        self.assertEqual(esito_giro(False, 0, 0), "fallito")
        self.assertEqual(esito_giro(True, 0, 0), "riuscito")

    def test_blocco_orfano_rimosso(self):
        os.makedirs(os.path.join(self.d, ".in-corso.lock"))
        with open(os.path.join(self.d, ".in-corso.lock", "pid"), "w") as f:
            f.write("999999")
        with Blocco(self.d, attesa_s=0):
            self.assertTrue(in_corso(self.d))
        self.assertFalse(os.path.exists(os.path.join(self.d, ".in-corso.lock")))

    def test_blocco_senza_pid_orfano(self):
        for contenuto in (None, "", "abc"):
            lock = os.path.join(self.d, ".in-corso.lock")
            os.makedirs(lock)
            if contenuto is not None:
                with open(os.path.join(lock, "pid"), "w") as f:
                    f.write(contenuto)
            self.assertFalse(in_corso(self.d))
            with Blocco(self.d, attesa_s=0):
                pass
            self.assertFalse(os.path.exists(lock))

    def test_blocco_occupato(self):
        with Blocco(self.d):
            self.assertTrue(in_corso(self.d))
            with self.assertRaises(Occupato):
                with Blocco(self.d, attesa_s=0):
                    pass
        self.assertFalse(in_corso(self.d))

    def test_pid_enorme_orfano(self):
        lock = os.path.join(self.d, ".in-corso.lock")
        os.makedirs(lock)
        with open(os.path.join(lock, "pid"), "w") as f:
            f.write("99999999999999999999")
        self.assertFalse(in_corso(self.d))
        with Blocco(self.d, attesa_s=0):
            self.assertTrue(in_corso(self.d))

    def test_blocco_mai_senza_pid(self):
        reale = os.rename
        viste = []

        def spia(src, dst):
            viste.append(os.path.exists(os.path.join(src, "pid")))
            return reale(src, dst)
        with mock.patch("stato_giro.os.rename", side_effect=spia):
            with Blocco(self.d, attesa_s=0):
                pass
        self.assertEqual(viste, [True])

    def test_blocco_vivo_non_rubato(self):
        with Blocco(self.d):
            lock = os.path.join(self.d, ".in-corso.lock")
            with self.assertRaises(Occupato):
                with Blocco(self.d, attesa_s=0):
                    pass
            with open(os.path.join(lock, "pid")) as f:
                self.assertEqual(f.read(), str(os.getpid()))
            self.assertEqual(sorted(os.listdir(self.d)),
                             [".in-corso.guardia", ".in-corso.lock"])

    def test_avanzi_acquisizione_morta_puliti(self):
        avanzo = os.path.join(self.d, ".in-corso.lock.99999999")
        os.makedirs(avanzo)
        with Blocco(self.d, attesa_s=0):
            pass
        self.assertEqual(os.listdir(self.d), [".in-corso.guardia"])

    def test_contesa_mutua_esclusione(self):
        lock = os.path.join(self.d, ".in-corso.lock")
        os.makedirs(lock)
        with open(os.path.join(lock, "pid"), "w") as f:
            f.write("999999")
        stato = {"dentro": 0, "massimo": 0, "fatti": 0, "errori": []}
        mutex = threading.Lock()

        def lavoro():
            try:
                with Blocco(self.d, attesa_s=20, passo_s=0.01):
                    with mutex:
                        stato["dentro"] += 1
                        stato["massimo"] = max(stato["massimo"], stato["dentro"])
                    time.sleep(0.01)
                    with mutex:
                        stato["dentro"] -= 1
                        stato["fatti"] += 1
            except Exception as e:
                stato["errori"].append(e)
        fili = [threading.Thread(target=lavoro) for _ in range(8)]
        for t in fili:
            t.start()
        for t in fili:
            t.join()
        self.assertEqual(stato["errori"], [])
        self.assertEqual(stato["fatti"], 8)
        self.assertEqual(stato["massimo"], 1)
        self.assertFalse(os.path.exists(lock))

    def test_avanzamento(self):
        self.assertIsNone(leggi_avanzamento(self.d))
        scrivi_avanzamento(self.d, {"testo": "x", "video_n": 2})
        self.assertEqual(leggi_avanzamento(self.d), {"testo": "x", "video_n": 2})
        with open(os.path.join(self.d, "run", "avanzamento.json"), "w") as f:
            f.write("{rotto")
        self.assertIsNone(leggi_avanzamento(self.d))

    def test_chiudi_giro(self):
        scrivi_avanzamento(self.d, {"testo": "x"})
        chiudi_giro(self.d, {"esito": "fallito", "fine": "2026-11-15T08:00:00+00:00"})
        self.assertIsNone(leggi_avanzamento(self.d))
        self.assertTrue(os.path.exists(os.path.join(self.d, "run", "ultimo-giro.json")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "run", "ultimo-riuscito.json")))
        chiudi_giro(self.d, {"esito": "problemi", "fine": "2026-11-15T20:00:00+00:00"})
        self.assertTrue(os.path.exists(os.path.join(self.d, "run", "ultimo-riuscito.json")))

    def test_chiudi_giro_senza_aggiornare_il_riuscito(self):
        # un comando che non scarica nulla non azzera la sorveglianza
        chiudi_giro(self.d, {"esito": "riuscito", "fine": "2026-11-15T08:00:00+00:00"},
                    aggiorna_riuscito=False)
        self.assertTrue(os.path.exists(os.path.join(self.d, "run", "ultimo-giro.json")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "run", "ultimo-riuscito.json")))

    def test_ultimo_comando(self):
        scrivi_avanzamento(self.d, {"testo": "x"})
        scrivi_ultimo_comando(self.d, "aggiungi", "I", "F", "Link sbagliato")
        with open(os.path.join(self.d, "run", "ultimo-comando.json"),
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"azione": "aggiungi", "inizio": "I",
                                            "fine": "F", "messaggio": "Link sbagliato"})
        self.assertIsNone(leggi_avanzamento(self.d))
        self.assertFalse(os.path.exists(os.path.join(self.d, "run", "ultimo-giro.json")))

    def test_attesa_del_blocco_lo_dice_una_volta(self):
        out = io.StringIO()
        with Blocco(self.d):
            with contextlib.redirect_stdout(out), self.assertRaises(Occupato):
                with Blocco(self.d, attesa_s=0.1, passo_s=0.01):
                    pass
        testo = out.getvalue()
        self.assertEqual(testo.count("aspetto che finisca"), 1)
        self.assertIn("fino a 1 minuti", testo)

    def test_senza_attesa_il_blocco_tace(self):
        out = io.StringIO()
        with Blocco(self.d):
            with contextlib.redirect_stdout(out), self.assertRaises(Occupato):
                with Blocco(self.d, attesa_s=0):
                    pass
        self.assertEqual(out.getvalue(), "")

    def test_chiudi_giro_senza_cartella_run(self):
        chiudi_giro(self.d, {"esito": "riuscito", "fine": "2026-11-15T08:00:00+00:00"})
        self.assertTrue(os.path.exists(os.path.join(self.d, "run", "ultimo-giro.json")))

    def test_traduci(self):
        self.assertEqual(traduci_errore("ERROR: [youtube] abc: Sign in to confirm you’re not a bot")["codice"], "youtube")
        self.assertEqual(traduci_errore("HTTP Error 403: Forbidden")["codice"], "youtube")
        self.assertEqual(traduci_errore("HTTP Error 429: Too Many Requests")["codice"], "youtube")
        self.assertEqual(traduci_errore("fatal: unable to access: Could not resolve host: github.com")["codice"], "rete")
        self.assertEqual(traduci_errore("URLError: timed out")["codice"], "rete")
        self.assertEqual(traduci_errore("git push fallito: ! [rejected] main -> main (fetch first)")["codice"], "git")
        self.assertEqual(traduci_errore("OSError: [Errno 28] No space left on device")["codice"], "disco")
        self.assertEqual(traduci_errore("boh")["codice"], "sconosciuto")
        r = traduci_errore("boh")
        self.assertTrue(r["messaggio"] and r["rimedio"])
        self.assertEqual(traduci_errore("HTTP Error 403")["messaggio"],
                         "YouTube ha rifiutato dei download")
        self.assertEqual(traduci_errore("HTTP Error 403")["rimedio"],
                         "Di solito basta aggiornare il programma che scarica i video.")

    def test_traduci_rete_prima_di_youtube(self):
        # offline: l'elenco RSS e le righe di yt-dlp (che portano "[youtube]")
        for testo in (
                "<urlopen error [Errno 8] nodename nor servname provided, or not known>",
                "ERROR: [youtube] abc: Unable to download webpage: <urlopen error "
                "[Errno 8] nodename nor servname provided, or not known>",
                "ERROR: [youtube] abc: Unable to download API page: Network is down",
                "ERROR: [youtube] abc: Unable to download webpage: [Errno 65] No route to host",
                "ERROR: [youtube] abc: Temporary failure in name resolution",
                "ERROR: [youtube] abc: Connection refused",
                "ERROR: [youtube] abc: Connection reset by peer"):
            self.assertEqual(traduci_errore(testo)["codice"], "rete", testo)

    def test_traduci_youtube_solo_con_segnali_precisi(self):
        for testo in ("ERROR: [youtube] abc: Sign in to confirm you're not a bot",
                      "ERROR: [youtube] abc: Video unavailable",
                      "ERROR: [youtube] abc: Private video. Sign in",
                      "ERROR: [youtube] abc: Requested format is not available",
                      "ERROR: [youtube] abc: Unable to extract initial data",
                      "WARNING: [youtube] nsig extraction failed",
                      "ERROR: join this channel: members-only content"):
            self.assertEqual(traduci_errore(testo)["codice"], "youtube", testo)
        # una riga con la sola sigla [youtube] non basta
        self.assertEqual(traduci_errore("ERROR: [youtube] abc: boh")["codice"],
                         "sconosciuto")

    def test_traduci_git_stretto(self):
        for testo in ("git push fallito", "git commit fallito",
                      "remote: Authentication failed", "! [rejected] main -> main",
                      "hint: non-fast-forward", "hint: fetch first"):
            self.assertEqual(traduci_errore(testo)["codice"], "git", testo)
        self.assertEqual(traduci_errore("file rejected by filter")["codice"],
                         "sconosciuto")

    def test_notifica(self):
        with mock.patch("stato_giro.subprocess.run") as run:
            notifica("Podcast", "Fatto")
            self.assertTrue(run.called)
            args = run.call_args[0][0]
            self.assertIn("Podcast", args)
            self.assertIn("Fatto", args)
        with mock.patch("stato_giro.subprocess.run", side_effect=OSError("no")):
            notifica("Podcast", "Fatto")


if __name__ == "__main__":
    unittest.main()
