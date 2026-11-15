"""Test del server del pannello: sicurezza, API, sorveglianza. Nessuna rete esterna."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

import pannello
from pannello import ammessa, crea_server, da_notificare
from unittest import mock

from stato_giro import Blocco, in_corso

LINK = "https://www.youtube.com/@canale"

SITE = "https://esempio.test/podcast"


def scrivi_json(radice, rel, dati):
    p = os.path.join(radice, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dati, f)


def crea_file(radice, rel, contenuto=b"x"):
    p = os.path.join(radice, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(contenuto)
    return p


def progetto(tmp):
    scrivi_json(tmp, "channels.json", {
        "site_url": SITE, "max_episodes": 7, "audio_quality": "9",
        "min_duration_minutes": 2, "max_duration_minutes": 120,
        "max_repo_mb": 700, "channels": [{
            "id": "UCaaaaaaaaaaaaaaaaaaaaaa", "slug": "a", "title": "A",
            "description": "d", "category": "Science",
            "cover": SITE + "/cover-a.jpg"}]})
    os.makedirs(os.path.join(tmp, "state"))
    os.makedirs(os.path.join(tmp, "docs", "audio"))


def leggi_stato(radice, slug):
    with open(os.path.join(radice, "state", slug + ".json"), encoding="utf-8") as f:
        return json.load(f)


def canale(radice, slug):
    with open(os.path.join(radice, "channels.json"), encoding="utf-8") as f:
        return next(c for c in json.load(f)["channels"] if c["slug"] == slug)


class TestSorveglianza(unittest.TestCase):
    def test_sorveglianza(self):
        t = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        h = timedelta(hours=1)
        self.assertTrue(da_notificare(t, t - 2 * h, t - 37 * h, None))
        self.assertFalse(da_notificare(t, t - 0.2 * h, t - 37 * h, None))
        self.assertFalse(da_notificare(t, t - 2 * h, t - 35 * h, None))
        self.assertFalse(da_notificare(t, t - 2 * h, t - 60 * h, t - 3 * h))
        self.assertTrue(da_notificare(t, t - 2 * h, None, None))
        self.assertTrue(da_notificare(t, t - 2 * h, t - 60 * h, t - 25 * h))


class TestRiposo(unittest.TestCase):
    T0 = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)

    def test_ha_dormito(self):
        t, m = self.T0, 1000.0
        # cinque minuti reali e monotoni: nessuno stop
        self.assertFalse(pannello.ha_dormito(t, m, t + timedelta(minutes=5), m + 300))
        # fino a due minuti di scarto si tollera (orologio corretto, ritardi)
        self.assertFalse(pannello.ha_dormito(t, m, t + timedelta(minutes=5), m + 190))
        # un weekend in stop: dieci ore di parete, cinque minuti monotoni
        self.assertTrue(pannello.ha_dormito(t, m, t + timedelta(hours=10), m + 300))
        self.assertTrue(pannello.ha_dormito(t, m, t + timedelta(minutes=8), m + 300))

    def test_grazia_dopo_il_risveglio(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(pannello, "notifica") as notifica:
            t, m = self.T0, 1000.0
            memoria = {"avvio": t - timedelta(days=3), "ora": t, "mono": m}
            # il pannello e' acceso da giorni e nessun giro e' riuscito: avviso
            self.assertTrue(pannello.passo_sorveglianza(
                d, memoria, t + timedelta(minutes=5), m + 300))
            os.remove(os.path.join(d, "run", "sorveglianza.json"))
            notifica.reset_mock()
            # il Mac dorme per ore: al risveglio la grazia riparte, nessun avviso
            risveglio = t + timedelta(hours=12)
            self.assertFalse(pannello.passo_sorveglianza(
                d, memoria, risveglio, m + 600))
            self.assertEqual(memoria["avvio"], risveglio)
            notifica.assert_not_called()
            # trenta minuti dopo (senza altro stop) il controllo torna normale
            self.assertTrue(pannello.passo_sorveglianza(
                d, memoria, risveglio + timedelta(minutes=31), m + 600 + 31 * 60))
            notifica.assert_called_once()


class TestEsecutore(unittest.TestCase):
    def test_timeout_ferma_anche_i_figli(self):
        import signal
        import time
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "yt2podcast.py"), "w") as f:
                f.write("import subprocess, sys, time\n"
                        "c = subprocess.Popen([sys.executable, '-c', "
                        "'import time; time.sleep(60)'])\n"
                        "open('figlio.pid', 'w').write(str(c.pid))\n"
                        "time.sleep(60)\n")
            esegui = pannello._esecutore_predefinito(d)
            with self.assertRaises(pannello.subprocess.TimeoutExpired):
                esegui(["x"], 2)
            with open(os.path.join(d, "figlio.pid")) as f:
                pid = int(f.read())
            vivo = True
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    vivo = False
                    break
                time.sleep(0.1)
            if vivo:
                os.kill(pid, signal.SIGKILL)
            self.assertFalse(vivo, "il processo figlio e' rimasto in vita")

    def test_esecuzione_normale(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "yt2podcast.py"), "w") as f:
                f.write("import sys; print('{\"ok\": 1}'); sys.exit(1)\n")
            self.assertEqual(pannello._esecutore_predefinito(d)(["x"], 10),
                             (1, '{"ok": 1}\n'))


class TestAmmessa(unittest.TestCase):
    def test_ammessa(self):
        ok = {"Host": "127.0.0.1:8719", "Origin": "http://127.0.0.1:8719",
              "X-Podcast": "1", "Content-Type": "application/json"}
        self.assertTrue(ammessa("POST", ok, 8719))
        self.assertTrue(ammessa("GET", {"Host": "localhost:8719"}, 8719))
        self.assertFalse(ammessa("GET", {"Host": "evil.example:8719"}, 8719))
        self.assertFalse(ammessa("GET", {"Host": "127.0.0.1:1"}, 8719))
        for chiave, valore in (("Origin", "https://evil.example"),
                               ("X-Podcast", None),
                               ("Content-Type", "text/plain")):
            h = dict(ok)
            h.pop(chiave) if valore is None else h.__setitem__(chiave, valore)
            self.assertFalse(ammessa("POST", h, 8719), chiave)

    def test_richieste_ostili(self):
        ok = {"Host": "127.0.0.1:8719", "Origin": "http://127.0.0.1:8719",
              "X-Podcast": "1", "Content-Type": "application/json"}
        casi = [
            {"Origin": None}, {"Origin": "null"}, {"X-Podcast": "0"},
            {"Host": "127.0.0.1"}, {"Origin": "http://localhost"},
            {"Host": "evil:8719", "Origin": "http://evil:8719"},
            {"Host": "localhost:8719", "Origin": "http://127.0.0.1:8719"}]
        for modifica in casi:
            h = dict(ok)
            for k, v in modifica.items():
                h.pop(k) if v is None else h.__setitem__(k, v)
            self.assertFalse(ammessa("POST", h, 8719), modifica)
        img = dict(ok, **{"Content-Type": "image/png"})
        self.assertTrue(ammessa("POST", img, 8719, "/api/copertina"))
        for modifica in ({"X-Podcast": None}, {"Origin": "https://evil.example"}):
            h = dict(img)
            for k, v in modifica.items():
                h.pop(k) if v is None else h.__setitem__(k, v)
            self.assertFalse(ammessa("POST", h, 8719, "/api/copertina"), modifica)

    def test_nomi_senza_maiuscole(self):
        h = {"host": "localhost:9", "origin": "http://localhost:9",
             "x-podcast": "1", "content-type": "application/json"}
        self.assertTrue(ammessa("POST", h, 9))

    def test_copertina_immagine(self):
        h = {"Host": "127.0.0.1:9", "Origin": "http://127.0.0.1:9",
             "X-Podcast": "1", "Content-Type": "image/png"}
        self.assertTrue(ammessa("POST", h, 9, "/api/copertina"))
        self.assertFalse(ammessa("POST", h, 9, "/api/aggiorna"))
        h["Content-Type"] = "application/json"
        self.assertFalse(ammessa("POST", h, 9, "/api/copertina"))
        h["Content-Type"] = "image/png"
        h.pop("X-Podcast")
        self.assertFalse(ammessa("POST", h, 9, "/api/copertina"))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = os.path.realpath(tmp.name)
        progetto(self.d)
        self.lanciati = []
        self.eseguiti = []
        self.risposta_esecutore = (0, json.dumps({"titolo": "T"}))
        self.srv = crea_server(
            self.d, 0, lanciatore=lambda a: self.lanciati.append(list(a)),
            esecutore=self._esecutore)
        self.porta = self.srv.server_address[1]
        t = threading.Thread(target=self.srv.serve_forever,
                             kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        self.addCleanup(self._ferma)

    def _ferma(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _esecutore(self, args, timeout):
        self.eseguiti.append(list(args))
        return self.risposta_esecutore

    def richiesta(self, metodo, percorso, corpo=None, intestazioni=None):
        h = {"Host": f"127.0.0.1:{self.porta}"}
        if metodo != "GET":
            h.update({"Origin": f"http://127.0.0.1:{self.porta}",
                      "X-Podcast": "1", "Content-Type": "application/json"})
        h.update(intestazioni or {})
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        try:
            c.putrequest(metodo, percorso, skip_host=True)
            for k, v in h.items():
                c.putheader(k, v)
            if corpo is not None:
                c.putheader("Content-Length", str(len(corpo)))
            c.endheaders(corpo)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def get_stato(self, percorso):
        return self.richiesta("GET", percorso)[0]

    def get(self, percorso):
        s, b = self.richiesta("GET", percorso)
        self.assertEqual(s, 200, b)
        return json.loads(b)

    def post_grezzo(self, percorso, corpo, intestazioni=None):
        s, b = self.richiesta("POST", percorso, corpo, intestazioni)
        try:
            return s, json.loads(b)
        except ValueError:
            return s, b

    def post(self, percorso, dati, intestazioni=None):
        return self.post_grezzo(percorso, json.dumps(dati).encode(), intestazioni)


class TestSicurezza(Base):
    def test_post_estraneo_non_avvia(self):
        stato, corpo = self.post("/api/aggiorna", {},
                                 {"Origin": "https://evil.example"})
        self.assertEqual(stato, 403)
        self.assertEqual(corpo, {"errore": "Richiesta non ammessa."})
        self.assertEqual(self.lanciati, [])

    def test_host_estraneo(self):
        self.assertEqual(
            self.richiesta("GET", "/api/stato", None, {"Host": "evil.example"})[0], 403)

    def test_json_rotto(self):
        stato, _ = self.post_grezzo("/api/aggiungi", b"{non json")
        self.assertEqual(stato, 400)
        self.assertEqual(self.lanciati, [])

    def test_occupato_409(self):
        with Blocco(self.d):
            stato, corpo = self.post("/api/aggiorna", {})
        self.assertEqual(stato, 409)
        self.assertEqual(corpo, {"errore": "C'è già un lavoro in corso."})
        self.assertEqual(self.lanciati, [])

    def test_aggiorna_avvia(self):
        stato, corpo = self.post("/api/aggiorna", {})
        self.assertEqual((stato, corpo["avviato"]), (202, True))
        self.assertEqual(self.lanciati, [[]])

    def test_percorsi_fuori_cartella(self):
        self.assertEqual(self.get_stato("/copertine/..%2F..%2Fchannels.json"), 404)

    def test_miniature_ostile(self):
        crea_file(self.d, "x.jpg")
        self.assertEqual(self.get_stato("/miniature/a/..%2F..%2Fx.jpg"), 404)
        self.assertEqual(self.get_stato("/miniature/..%2F/x.jpg"), 404)

    def test_corpo_troppo_grande(self):
        stato, _ = self.post_grezzo("/api/aggiungi", b" " * (64 * 1024 + 1))
        self.assertEqual(stato, 413)
        self.assertEqual(self.lanciati, [])

    def test_rotta_ignota_e_metodo(self):
        self.assertEqual(self.get_stato("/api/boh"), 404)
        self.assertEqual(self.post("/api/boh", {})[0], 404)
        self.assertEqual(self.get_stato("/api/aggiorna"), 405)
        self.assertEqual(self.richiesta("PUT", "/api/stato", b"{}")[0], 405)

    def test_errore_interno_non_ferma(self):
        def rotto(radice):
            raise RuntimeError("boom")
        orig = pannello.costruisci_stato
        pannello.costruisci_stato = rotto
        self.addCleanup(setattr, pannello, "costruisci_stato", orig)
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            s, b = self.richiesta("GET", "/api/stato")
        self.assertEqual(s, 500)
        self.assertEqual(json.loads(b), {"errore": "Errore interno del pannello."})
        pannello.costruisci_stato = orig
        self.assertEqual(self.get_stato("/api/stato"), 200)


class TestLavori(Base):
    def test_aggiungi(self):
        self.post("/api/aggiungi", {"link": "https://youtu.be/abcdefghijk"})
        self.assertEqual(self.lanciati, [["--add", "https://youtu.be/abcdefghijk"]])

    def test_aggiungi_con_regole(self):
        self.post("/api/aggiungi", {"link": LINK, "regole": {"sponsorblock": False}})
        self.assertEqual(self.lanciati,
                         [["--add", LINK, "--regole", '{"sponsorblock": false}']])

    def test_link_non_valido(self):
        for corpo in ({}, {"link": ""}, {"link": 5}, {"link": "x" * 501}):
            self.assertEqual(self.post("/api/aggiungi", corpo)[0], 400)
        self.assertEqual(self.lanciati, [])

    def test_link_flag_o_sconosciuto_rifiutati(self):
        for rotta in ("/api/aggiungi", "/api/anteprima"):
            for link in ("--aggiorna-programma", "-x", "--regole", "ciao", " --remove a"):
                s, c = self.post(rotta, {"link": link})
                self.assertEqual(s, 400, link)
                self.assertEqual(c["errore"], "Non riconosco questo link. Incolla un "
                                 "link di YouTube: canale, playlist o video.")
        self.assertEqual(self.lanciati, [])
        self.assertEqual(self.eseguiti, [])

    def test_link_senza_schema_ripulito(self):
        self.post("/api/aggiungi", {"link": "  youtube.com/@canale "})
        self.assertEqual(self.lanciati, [["--add", "https://youtube.com/@canale"]])

    def test_regole_di_aggiungi_validate(self):
        for r in ({"altra": 1}, {"max_duration_minutes": "5"}, {"sponsorblock": 1}, 5):
            self.assertEqual(self.post("/api/aggiungi", {"link": LINK, "regole": r})[0],
                             400, r)
            self.assertEqual(self.post("/api/anteprima", {"link": LINK, "regole": r})[0],
                             400, r)
        self.assertEqual(self.lanciati, [])
        self.post("/api/aggiungi", {"link": LINK, "regole": {
            "title_include": "a, b", "max_duration_minutes": None, "title_exclude": []}})
        self.assertEqual(self.lanciati,
                         [["--add", LINK, "--regole", '{"title_include": ["a", "b"]}']])

    def test_concorrenza_un_solo_avvio(self):
        import time as _t
        self.srv.lanciatore = lambda a: (_t.sleep(0.05), self.lanciati.append(list(a)))
        esiti = []
        fili = [threading.Thread(target=lambda: esiti.append(self.post("/api/aggiorna", {})[0]))
                for _ in range(8)]
        for f in fili:
            f.start()
        for f in fili:
            f.join()
        self.assertEqual(sorted(esiti), [202] + [409] * 7)
        self.assertEqual(self.lanciati, [[]])

    def test_rimuovi(self):
        self.assertEqual(self.post("/api/rimuovi", {"slug": "zzz"})[0], 404)
        self.assertEqual(self.post("/api/rimuovi", {"slug": "../a"})[0], 404)
        self.assertEqual(self.lanciati, [])
        self.assertEqual(self.post("/api/rimuovi", {"slug": "a"})[0], 202)
        self.assertEqual(self.lanciati, [["--remove", "a"]])

    def test_singolo(self):
        self.assertEqual(self.post("/api/singolo", {"id": "corto"})[0], 400)
        self.assertEqual(self.lanciati, [])
        self.assertEqual(self.post("/api/singolo", {"id": "abcdefghijk"})[0], 202)
        self.assertEqual(self.lanciati,
                         [["--video", "https://www.youtube.com/watch?v=abcdefghijk"]])

    def test_aggiorna_programma(self):
        self.post("/api/aggiorna-programma", {})
        self.assertEqual(self.lanciati, [["--aggiorna-programma"]])

    def test_riprova(self):
        scrivi_json(self.d, "state/a.json", {
            "episodes": [], "skipped": ["R1aaaaaaaaa", "F1aaaaaaaaa"],
            "skipped_info": {"R1aaaaaaaaa": {"kind": "x"}, "F1aaaaaaaaa": {"kind": "y"}},
            "falliti": {"R1aaaaaaaaa": {"tentativi": 3}, "ALTRO": {"tentativi": 1}}})
        s, _ = self.post("/api/riprova", {"slug": "a", "id": "R1aaaaaaaaa"})
        self.assertEqual(s, 202)
        st = leggi_stato(self.d, "a")
        self.assertEqual(st["skipped"], ["F1aaaaaaaaa"])
        self.assertEqual(list(st["skipped_info"]), ["F1aaaaaaaaa"])
        # il video resta fra i falliti, con i tentativi azzerati
        self.assertEqual(st["falliti"]["R1aaaaaaaaa"]["tentativi"], 0)
        self.assertEqual(st["falliti"]["ALTRO"]["tentativi"], 1)
        self.assertEqual(self.lanciati, [["--only", "a"]])

    def test_riprova_di_un_fallito_definitivo_lo_tiene_visibile(self):
        scrivi_json(self.d, "state/a.json", {
            "episodes": [], "skipped": ["F1aaaaaaaaa", "G1aaaaaaaaa"],
            "skipped_info": {
                "F1aaaaaaaaa": {"title": "Titolo", "reason": "HTTP Error 403",
                                "kind": "fallito", "date": "2026-11-01T10:00:00+00:00"},
                "G1aaaaaaaaa": {"title": "G", "reason": "r", "kind": "regola"}}})
        s, _ = self.post("/api/riprova", {"slug": "a", "id": "F1aaaaaaaaa"})
        self.assertEqual(s, 202)
        st = leggi_stato(self.d, "a")
        self.assertEqual(st["skipped"], ["G1aaaaaaaaa"])
        self.assertEqual(list(st["skipped_info"]), ["G1aaaaaaaaa"])
        self.assertEqual(st["falliti"]["F1aaaaaaaaa"],
                         {"title": "Titolo", "reason": "HTTP Error 403",
                          "tentativi": 0, "date": "2026-11-01T10:00:00+00:00"})
        voci = {v["id"]: v for v in self.get("/api/show/a")["saltati"]}
        self.assertEqual(voci["F1aaaaaaaaa"]["kind"], "fallito")

    def test_riprova_rilascia_il_blocco_prima_di_lanciare(self):
        viste = []
        self.srv.lanciatore = lambda a: viste.append(in_corso(self.d))
        self.post("/api/riprova", {"slug": "a", "id": "R1aaaaaaaaa"})
        self.assertEqual(viste, [False])

    def test_riprova_con_blocco_preso_da_altri_409(self):
        scrivi_json(self.d, "state/a.json", {
            "episodes": [], "falliti": {"R1aaaaaaaaa": {"tentativi": 2}}})
        with Blocco(self.d), mock.patch.object(pannello, "in_corso",
                                               return_value=False):
            s, r = self.post("/api/riprova", {"slug": "a", "id": "R1aaaaaaaaa"})
        self.assertEqual((s, r["errore"]), (409, "C'è già un lavoro in corso."))
        self.assertEqual(leggi_stato(self.d, "a")["falliti"]["R1aaaaaaaaa"]["tentativi"], 2)
        self.assertEqual(self.lanciati, [])

    def test_riprova_senza_modifiche_non_scrive(self):
        s, _ = self.post("/api/riprova", {"slug": "a", "id": "R1aaaaaaaaa"})
        self.assertEqual(s, 202)
        self.assertFalse(os.path.exists(os.path.join(self.d, "state", "a.json")))
        self.assertEqual(self.lanciati, [["--only", "a"]])
        scrivi_json(self.d, "state/a.json", {"episodes": [], "skipped": ["X"]})
        p = os.path.join(self.d, "state", "a.json")
        os.utime(p, (1, 1))
        self.srv.ultimo_lancio = float("-inf")  # fuori dalla finestra di "occupato"
        s, _ = self.post("/api/riprova", {"slug": "a", "id": "R1aaaaaaaaa"})
        self.assertEqual(s, 202)
        self.assertEqual(self.lanciati, [["--only", "a"], ["--only", "a"]])
        self.assertEqual(os.path.getmtime(p), 1)
        self.assertEqual(leggi_stato(self.d, "a")["skipped"], ["X"])

    def test_riprova_id_non_valido(self):
        self.assertEqual(self.post("/api/riprova", {"slug": "a", "id": "!!"})[0], 400)
        self.assertEqual(self.lanciati, [])

    def test_apri_cartella(self):
        import unittest.mock as m
        with m.patch("pannello.subprocess.run") as run:
            s, _ = self.post("/api/apri-cartella", {})
        self.assertEqual(s, 200)
        run.assert_called_once()
        self.assertEqual(run.call_args[0][0], ["open", self.d])

    def test_copertina(self):
        s, _ = self.post_grezzo("/api/copertina?slug=a", b"\x89PNGdati",
                                {"Content-Type": "image/png"})
        self.assertEqual(s, 202)
        p = os.path.join(self.d, "run", "copertina-a.png")
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"\x89PNGdati")
        self.assertEqual(self.lanciati, [["--cover", "a", p]])

    def test_copertina_rifiuti(self):
        img = {"Content-Type": "image/png"}
        self.assertEqual(self.post_grezzo("/api/copertina?slug=zz", b"x", img)[0], 404)
        self.assertEqual(self.post_grezzo(
            "/api/copertina?slug=a", b"x", {"Content-Type": "image/gif"})[0], 415)
        self.assertEqual(self.post_grezzo(
            "/api/copertina?slug=a", b"x", {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.post_grezzo(
            "/api/copertina?slug=a", b" " * (10 * 1024 * 1024 + 1), img)[0], 413)
        self.assertEqual(self.lanciati, [])


class TestAnteprima(Base):
    def test_ok(self):
        s, c = self.post("/api/anteprima", {"link": LINK, "regole": {"sponsorblock": True}})
        self.assertEqual((s, c), (200, {"titolo": "T"}))
        self.assertEqual(self.eseguiti,
                         [["--preview", LINK, "--regole", '{"sponsorblock": true}']])
        self.assertEqual(self.lanciati, [])

    def test_errore_422(self):
        self.risposta_esecutore = (1, json.dumps({"errore": "x"}))
        self.assertEqual(self.post("/api/anteprima", {"link": LINK}),
                         (422, {"errore": "x"}))

    def test_altro_502(self):
        for r in ((2, "{}"), (0, "non json"), (1, "non json"), (None, "")):
            self.risposta_esecutore = r
            s, c = self.post("/api/anteprima", {"link": LINK})
            self.assertEqual(s, 502)
            self.assertEqual(c, {"errore": "L'anteprima non è riuscita. Riprova."})


class TestScritture(Base):
    def test_regole_rivaluta(self):
        scrivi_json(self.d, "state/a.json", {
            "episodes": [], "skipped": ["R1", "F1"],
            "skipped_info": {"R1": {"kind": "regola"}, "F1": {"kind": "fallito"}}})
        self.post("/api/regole", {"slug": "a", "max_duration_minutes": 45})
        self.assertEqual(leggi_stato(self.d, "a")["skipped"], ["F1"])
        self.assertEqual(list(leggi_stato(self.d, "a")["skipped_info"]), ["F1"])
        self.assertEqual(canale(self.d, "a")["max_duration_minutes"], 45)

    def test_regole_rimozione_chiavi(self):
        self.post("/api/regole", {"slug": "a", "max_duration_minutes": 45,
                                  "title_include": "uno, due", "sponsorblock": False})
        c = canale(self.d, "a")
        self.assertEqual(c["title_include"], ["uno", "due"])
        self.assertIs(c["sponsorblock"], False)
        self.post("/api/regole", {"slug": "a", "max_duration_minutes": None,
                                  "title_include": [], "sponsorblock": None})
        c = canale(self.d, "a")
        for k in ("max_duration_minutes", "title_include", "sponsorblock"):
            self.assertNotIn(k, c)
        self.post("/api/regole", {"slug": "a", "title_exclude": "x", "min_duration_minutes": 3})
        self.post("/api/regole", {"slug": "a", "title_exclude": ""})
        self.assertNotIn("title_exclude", canale(self.d, "a"))
        self.assertEqual(canale(self.d, "a")["min_duration_minutes"], 3)

    def test_regole_validazione(self):
        for corpo in ({"slug": "zzz"}, ):
            self.assertEqual(self.post("/api/regole", corpo)[0], 404)
        for extra in ({"min_duration_minutes": "5"}, {"max_duration_minutes": -1},
                      {"min_duration_minutes": True}, {"sponsorblock": "si"},
                      {"title_include": 3}, {"title_exclude": [1]},
                      {"sconosciuta": 1}):
            self.assertEqual(self.post("/api/regole", {"slug": "a", **extra})[0], 400, extra)
        self.assertNotIn("min_duration_minutes", canale(self.d, "a"))

    def test_regole_occupato(self):
        with Blocco(self.d):
            self.assertEqual(self.post("/api/regole",
                                       {"slug": "a", "sponsorblock": False})[0], 409)
        self.assertNotIn("sponsorblock", canale(self.d, "a"))

    def test_impostazioni(self):
        s, _ = self.post("/api/impostazioni", {
            "max_episodes": 10, "qualita": "alta", "loudnorm": False,
            "min_duration_minutes": 0, "chapters": True})
        self.assertEqual(s, 200)
        with open(os.path.join(self.d, "channels.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual((cfg["max_episodes"], cfg["audio_quality"], cfg["loudnorm"],
                          cfg["min_duration_minutes"], cfg["chapters"]),
                         (10, "5", False, 0, True))
        self.assertEqual(len(cfg["channels"]), 1)

    def test_impostazioni_non_valide(self):
        for corpo in ({"max_episodes": 0}, {"max_episodes": 51}, {"qualita": "x"},
                      {"loudnorm": 1}, {"max_duration_minutes": -2}, {"altro": 1}):
            self.assertEqual(self.post("/api/impostazioni", corpo)[0], 400, corpo)

    def test_scritture_col_blocco_vero_preso_da_un_giro_409(self):
        # il controllo "libero" del pannello passa (corsa con un giro che parte),
        # ma il blocco vero e' preso: nessun file cambia
        with open(os.path.join(self.d, "channels.json"), "rb") as f:
            prima = f.read()
        with Blocco(self.d), mock.patch.object(pannello, "in_corso",
                                               return_value=False):
            for percorso, corpo in (
                    ("/api/regole", {"slug": "a", "sponsorblock": False}),
                    ("/api/impostazioni", {"max_episodes": 9})):
                s, r = self.post(percorso, corpo)
                self.assertEqual((s, r["errore"]),
                                 (409, "C'è già un lavoro in corso."), percorso)
        with open(os.path.join(self.d, "channels.json"), "rb") as f:
            self.assertEqual(f.read(), prima)

    def test_impostazioni_occupato(self):
        with Blocco(self.d):
            self.assertEqual(self.post("/api/impostazioni", {"max_episodes": 9})[0], 409)


class TestLettura(Base):
    def test_stato(self):
        s = self.get("/api/stato")
        self.assertEqual(s["spazio"]["limite_sito_mb"], 900)
        self.assertEqual(s["spazio"]["soglia_mb"], 700)
        self.assertEqual(s["spazio"]["cronologia_mb"], 0)
        self.assertEqual(s["impostazioni"]["qualita"], "leggera")
        self.assertEqual([x["slug"] for x in s["show"]], ["a"])
        self.assertEqual(s["show"][0]["tipo"], "canale")
        self.assertEqual(s["show"][0]["feed_url"], SITE + "/a.xml")
        self.assertFalse(s["in_corso"])
        self.assertTrue(s["fermo_da_36_ore"])
        self.assertIn(s["prossimo_giro"], ("08:00", "20:00"))

    def test_stato_predefiniti_come_il_motore(self):
        scrivi_json(self.d, "channels.json", {"site_url": SITE, "channels": []})
        self.assertEqual(self.get("/api/stato")["impostazioni"]["max_episodes"], 40)

    def test_stato_ultimo_comando(self):
        self.assertIsNone(self.get("/api/stato")["ultimo_comando"])
        adesso = datetime.now(timezone.utc)
        d = {"azione": "aggiungi", "inizio": adesso.isoformat(),
             "fine": adesso.isoformat(), "messaggio": "Link sbagliato"}
        scrivi_json(self.d, "run/ultimo-comando.json", d)
        self.assertEqual(self.get("/api/stato")["ultimo_comando"], d)
        vecchio = dict(d, fine=(adesso - timedelta(minutes=11)).isoformat())
        scrivi_json(self.d, "run/ultimo-comando.json", vecchio)
        self.assertIsNone(self.get("/api/stato")["ultimo_comando"])
        scrivi_json(self.d, "run/ultimo-comando.json", dict(d, fine="boh"))
        self.assertIsNone(self.get("/api/stato")["ultimo_comando"])

    def test_stato_conteggi(self):
        crea_file(self.d, "docs/audio/a/X1.mp3", b"0" * 1024 * 1024)
        scrivi_json(self.d, "state/a.json", {"episodes": [{"id": "X1"}, {"id": "X2"}],
                                             "skipped": ["S"]})
        scrivi_json(self.d, "run/ultimo-riuscito.json",
                    {"fine": datetime.now(timezone.utc).isoformat()})
        sh = self.get("/api/stato")
        v = sh["show"][0]
        self.assertEqual((v["episodi"], v["mb"], v["nuovi_oggi"], v["saltati"]),
                         (2, 1, 1, 1))
        self.assertFalse(sh["fermo_da_36_ore"])
        self.assertEqual(sh["ultimo_riuscito"] is not None, True)

    def test_stato_saltati_come_show(self):
        scrivi_json(self.d, "state/a.json", {
            "episodes": [], "skipped": ["S1"],
            "falliti": {"F1": {"tentativi": 3}, "F2": {"tentativi": 1}}})
        self.assertEqual(self.get("/api/stato")["show"][0]["saltati"], 3)
        self.assertEqual(len(self.get("/api/show/a")["saltati"]), 3)

    def test_errori_sempre_json(self):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        c.request("TRACE", "/", headers={"Host": f"127.0.0.1:{self.porta}"})
        r = c.getresponse()
        corpo = r.read()
        c.close()
        self.assertEqual(r.status, 501)
        self.assertTrue(r.getheader("Content-Type").startswith("application/json"))
        self.assertIn("errore", json.loads(corpo))

    def test_file_rotti_durante_il_giro(self):
        p = os.path.join(self.d, "state", "a.json")
        with open(p, "w") as f:
            f.write('{"episodes": [')
        self.assertEqual(self.get("/api/stato")["show"][0]["episodi"], 0)
        self.assertEqual(self.get("/api/show/a")["episodi"], [])
        c = os.path.join(self.d, "channels.json")
        with open(c, "w") as f:
            f.write('{"channels": [')
        s, b = self.richiesta("GET", "/api/stato")
        self.assertEqual((s, json.loads(b)), (503, {"errore": "Riprova tra un attimo."}))
        self.assertEqual(self.get_stato("/api/show/a"), 503)

    def test_scritture_atomiche_senza_avanzi(self):
        self.post("/api/regole", {"slug": "a", "sponsorblock": False})
        # la guardia del blocco resta (e' ignorata da git); nessun avanzo
        self.assertEqual(sorted(os.listdir(self.d)),
                         [".in-corso.guardia", "channels.json", "docs", "state"])
        self.assertFalse(in_corso(self.d))

    def test_qualita_piu_vicina(self):
        scrivi_json(self.d, "channels.json", {
            "site_url": SITE, "audio_quality": "8", "channels": []})
        self.assertEqual(self.get("/api/stato")["impostazioni"]["qualita"], "media")

    def test_tipi(self):
        scrivi_json(self.d, "channels.json", {"site_url": SITE, "channels": [
            {"slug": "p", "title": "P", "type": "playlist"},
            {"slug": "m", "title": "M", "manual": True}]})
        tipi = {x["slug"]: x["tipo"] for x in self.get("/api/stato")["show"]}
        self.assertEqual(tipi, {"p": "playlist", "m": "singoli"})

    def test_show(self):
        crea_file(self.d, "docs/audio/a/E1aaaaaaaaa.mp3")
        crea_file(self.d, "docs/audio/a/E1aaaaaaaaa.jpg")
        scrivi_json(self.d, "state/a.json", {
            "episodes": [{"id": "E1aaaaaaaaa", "title": "t", "published": "p", "duration": 5,
                          "chapters": 2, "tagliati_s": 3},
                         {"id": "E2aaaaaaaaa", "title": "u", "published": "q", "duration": 6}],
            "skipped": ["OLD1", "R1"],
            "skipped_info": {"R1": {"title": "r", "reason": "troppo lungo",
                                    "kind": "regola", "date": "d"}},
            "falliti": {"F9": {"title": "f", "reason": "rete", "tentativi": 2,
                               "date": "d2"}}})
        v = self.get("/api/show/a")
        self.assertEqual(v["podcast_url"], "podcast://esempio.test/podcast/a.xml")
        self.assertEqual(v["feed_url"], SITE + "/a.xml")
        e1, e2 = v["episodi"]
        self.assertTrue(e1["image"] and e1["nuovo_oggi"])
        self.assertFalse(e2["image"] or e2["nuovo_oggi"])
        self.assertEqual((e1["chapters"], e1["tagliati_s"]), (2, 3))
        sal = {x["id"]: x for x in v["saltati"]}
        self.assertEqual(sal["OLD1"], {"id": "OLD1", "title": "",
                                       "reason": "motivo non registrato",
                                       "kind": "", "date": ""})
        self.assertEqual(sal["R1"]["reason"], "troppo lungo")
        self.assertEqual(sal["F9"]["kind"], "fallito")
        self.assertEqual(v["regole"], {})

    def test_show_regole(self):
        self.post("/api/regole", {"slug": "a", "max_duration_minutes": 30,
                                  "title_exclude": "x, y", "sponsorblock": False})
        self.assertEqual(self.get("/api/show/a")["regole"], {
            "max_duration_minutes": 30, "title_exclude": ["x", "y"],
            "sponsorblock": False})

    def test_show_saltati_vecchi(self):
        scrivi_json(self.d, "state/a.json", {"episodes": [], "skipped": ["OLD1"]})
        self.assertEqual(self.get("/api/show/a")["saltati"][0]["reason"],
                         "motivo non registrato")

    def test_show_ignoto(self):
        self.assertEqual(self.get_stato("/api/show/zzz"), 404)
        self.assertEqual(self.get_stato("/api/show/..%2Fa"), 404)

    def test_statici(self):
        self.assertEqual(self.get_stato("/"), 404)
        crea_file(self.d, "pannello/index.html", b"<p>ciao</p>")
        crea_file(self.d, "pannello/app.js", b"1")
        crea_file(self.d, "docs/cover-a.jpg", b"jpg")
        crea_file(self.d, "docs/audio/a/E1aaaaaaaaa.jpg", b"mini")
        s, b = self.richiesta("GET", "/")
        self.assertEqual((s, b), (200, b"<p>ciao</p>"))
        self.assertEqual(self.richiesta("GET", "/app.js")[0], 200)
        self.assertEqual(self.get_stato("/stile.css"), 404)
        self.assertEqual(self.richiesta("GET", "/copertine/a.jpg"), (200, b"jpg"))
        self.assertEqual(self.richiesta("GET", "/miniature/a/E1aaaaaaaaa.jpg"), (200, b"mini"))
        self.assertEqual(self.get_stato("/miniature/a/E2aaaaaaaaa.jpg"), 404)

    def test_qr(self):
        self.assertEqual(self.richiesta("GET", "/qr/a.svg")[0], 503)
        self.assertEqual(self.get_stato("/qr/zzz.svg"), 404)
        prog = crea_file(self.d, "run/qr",
                         b"#!/bin/sh\necho \"<svg>$1</svg>\"\n")
        os.chmod(prog, 0o755)
        s, b = self.richiesta("GET", "/qr/a.svg")
        self.assertEqual(s, 200)
        self.assertIn(b"podcast://esempio.test/podcast/a.xml", b)
        os.remove(prog)
        self.assertEqual(self.richiesta("GET", "/qr/a.svg")[0], 200)  # dalla cache


if __name__ == "__main__":
    unittest.main()
