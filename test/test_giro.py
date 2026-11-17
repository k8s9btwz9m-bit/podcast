import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import stato_giro
import yt2podcast
from aiuti import (installa, progetto_finto, leggi_stato, leggi_cfg, canale,
                   finto_feed, finto_download, finto_download_ok, finto_elenco,
                   cattura_notifiche, finto_git, finto_peso_sito,
                   aggiungi_playlist_finta, crea_file)


YTDLP_JSON_VERO = yt2podcast.ytdlp_json


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


class TestGiro(Base):
    def test_giro_riuscito_scrive_riepilogo(self):
        finto_feed(["V1"]); finto_download_ok()
        r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "riuscito")
        self.assertEqual(r["show"][0]["nuovi"], 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.d, "run", "ultimo-riuscito.json")))
        self.assertIsNone(stato_giro.leggi_avanzamento(self.d))

    def test_tutti_falliti_notifica(self):
        finto_feed(["V1", "V2"])
        finto_download({v: (None, "HTTP Error 403: Forbidden", "fallito")
                        for v in ("V1", "V2")})
        with cattura_notifiche() as n:
            r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "fallito"); self.assertEqual(len(n), 1)
        self.assertEqual(r["errori"][0]["codice"], "youtube")
        self.assertEqual(len(r["errori"]), 1)

    def test_video_in_attesa_non_e_un_fallimento(self):
        finto_feed(["V1"])
        finto_download({"V1": (None, yt2podcast.MOTIVO_ATTESA, "attesa")})
        with cattura_notifiche() as n:
            for _ in range(2):
                r = yt2podcast.giro(self.cfg)
                self.assertEqual(r["esito"], "riuscito")
                self.assertEqual((r["show"][0]["tentati"], r["show"][0]["falliti"]),
                                 (0, 0))
                self.assertEqual(r["errori"], [])
                st = leggi_stato(self.d, "a")
                self.assertEqual(st["falliti"]["V1"]["tentativi"], 0)
                self.assertEqual(st["falliti"]["V1"]["reason"], yt2podcast.MOTIVO_ATTESA)
                self.assertNotIn("V1", st.get("skipped", []))
        self.assertEqual(n, [])

    def test_video_in_attesa_in_playlist(self):
        blocco = aggiungi_playlist_finta(self.d, "pl", ["P1"])
        finto_download({"P1": (None, yt2podcast.MOTIVO_ATTESA, "attesa")})
        for _ in range(4):   # oltre MAX_TENTATIVI: resta in attesa, mai saltato
            r = yt2podcast.process_playlist(self.cfg, blocco)
            self.assertEqual((r["tentati"], r["falliti"]), (0, 0))
        st = leggi_stato(self.d, "pl")
        self.assertEqual(st["falliti"]["P1"]["tentativi"], 0)
        self.assertNotIn("P1", st.get("skipped", []))

    def test_errori_in_ordine_di_priorita(self):
        show = [{"saltati_info": [
            {"kind": "fallito", "reason": "HTTP Error 403: Forbidden"}],
            "elenco_fallito": False}]
        errori = yt2podcast._errori_del_giro(
            show, ["boh", "git push fallito", "spazio esaurito: sito oltre 900 MB",
                   "<urlopen error [Errno 8] nodename nor servname provided>"],
            [{"codice": "comando", "messaggio": "m", "rimedio": ""}])
        self.assertEqual([e["codice"] for e in errori],
                         ["rete", "git", "spazio", "youtube", "comando",
                          "sconosciuto"])

    def test_elenco_offline_e_un_errore_di_rete(self):
        err = "ERROR: yt-dlp non riesce a leggere l'elenco dei video.\n" \
              "ERROR: [youtube] x: Unable to download webpage: <urlopen error " \
              "[Errno 8] nodename nor servname provided, or not known>"
        with mock.patch.object(yt2podcast, "fetch_channel_feed",
                               side_effect=yt2podcast.urllib.error.URLError(
                                   "[Errno 8] nodename nor servname provided")), \
                mock.patch.object(yt2podcast, "elenco_ytdlp",
                                  side_effect=yt2podcast.Errore(err)):
            r = yt2podcast.giro(self.cfg)
        self.assertEqual([e["codice"] for e in r["errori"]], ["rete"])

    def test_uno_fallito_niente_notifica(self):
        finto_feed(["V1", "V2"])
        finto_download({"V1": (None, "HTTP Error 403: Forbidden", "fallito")})
        with cattura_notifiche() as n:
            r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "problemi"); self.assertEqual(n, [])
        self.assertEqual([s["id"] for s in r["saltati"]], ["V1"])

    def test_push_fallito_e_giro_fallito(self):
        self.cfg["git_auto_push"] = True
        with finto_git(push_fallisce=True), cattura_notifiche() as n:
            r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "fallito"); self.assertEqual(r["errori"][0]["codice"], "git")
        self.assertEqual(len(n), 1)

    def test_senza_git_con_push_automatico_il_giro_fallisce(self):
        # niente .git: pubblicare e' impossibile e non va detto "riuscito"
        self.cfg["git_auto_push"] = True
        self.assertFalse(os.path.isdir(yt2podcast.GIT_DIR))
        with cattura_notifiche() as n:
            r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "fallito")
        self.assertEqual(r["errori"][0]["codice"], "git")
        self.assertEqual(len(n), 1)

    def test_senza_origin_con_push_automatico_il_giro_fallisce(self):
        self.cfg["git_auto_push"] = True
        os.makedirs(yt2podcast.GIT_DIR)

        def falso(*args, check=True):
            if args[:2] == ("remote", "get-url"):
                raise RuntimeError("git remote get-url fallito")
            return ""
        with mock.patch.object(yt2podcast, "git", side_effect=falso):
            with self.assertRaises(yt2podcast.Errore) as c:
                yt2podcast.git_publish(self.cfg)
        self.assertEqual(str(c.exception),
                         "La cartella non è collegata a GitHub: non posso pubblicare.")

    def test_senza_git_e_senza_push_automatico_non_cambia_nulla(self):
        finto_feed(["V1"]); finto_download_ok()
        r = yt2podcast.giro(self.cfg)   # git_auto_push false: non pubblica
        self.assertEqual(r["esito"], "riuscito")

    def test_azzeramento_cronologia_aggiunge_origin_subito_dopo_init(self):
        shutil_rm = mock.patch.object(yt2podcast.shutil, "rmtree")
        with finto_git() as eseguiti, shutil_rm, \
                mock.patch.object(yt2podcast, "git_dir_size", return_value=0):
            yt2podcast.git_reset_history("https://github.com/x/y.git", "main",
                                         {"user.name": "N", "user.email": ""})
        comandi = [a[0] for a in eseguiti]
        self.assertEqual(comandi, ["init", "remote", "config", "add", "commit",
                                   "push"])
        self.assertEqual(eseguiti[1],
                         ("remote", "add", "origin", "https://github.com/x/y.git"))

    def test_push_riuscito(self):
        self.cfg["git_auto_push"] = True
        with finto_git() as git_eseguiti:
            r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "riuscito")
        self.assertIn(("push", "-u", "origin", "main"), git_eseguiti)

    def test_sito_pieno_non_scarica(self):
        finto_feed(["V1"]); chiamati = finto_download_ok()
        with finto_peso_sito(905), cattura_notifiche() as n:
            r = yt2podcast.giro(self.cfg)
        self.assertEqual(chiamati, [])
        s = leggi_stato(self.d, "a")
        self.assertIn("spazio esaurito", s["falliti"]["V1"]["reason"])
        self.assertEqual(s["falliti"]["V1"].get("tentativi", 0), 0)
        self.assertNotIn("V1", s["skipped"])
        self.assertEqual(r["esito"], "fallito"); self.assertEqual(len(n), 1)
        self.assertEqual(r["errori"][0]["codice"], "spazio")

    def test_sito_pieno_playlist(self):
        aggiungi_playlist_finta(self.d, "pl", ["P1"])
        cfg = leggi_cfg(self.d)
        chiamati = finto_download_ok()
        with finto_peso_sito(900):
            yt2podcast.giro(cfg, solo="pl")
        self.assertEqual(chiamati, [])
        self.assertIn("spazio esaurito",
                      leggi_stato(self.d, "pl")["falliti"]["P1"]["reason"])

    def test_peso_sito_mb(self):
        crea_file(self.d, "docs/audio/a/x.mp3", b"x" * (3 * 1024 * 1024))
        self.assertEqual(yt2podcast.peso_sito_mb(), 3)

    def test_eccezione_non_attesa_chiude_il_giro(self):
        finto_feed(["V1"])
        with mock.patch.object(yt2podcast, "download_audio",
                               side_effect=OSError("No space left on device")):
            with cattura_notifiche() as n:
                r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "fallito"); self.assertEqual(len(n), 1)
        self.assertEqual(r["errori"][0]["codice"], "disco")
        self.assertIsNone(stato_giro.leggi_avanzamento(self.d))
        self.assertTrue(os.path.exists(os.path.join(self.d, "run", "ultimo-giro.json")))

    def test_avanzamento_a_ogni_show_e_download(self):
        finto_feed(["V1"])
        viste = []

        def falso(video_id, dest, cfg, regole=None):
            viste.append(stato_giro.leggi_avanzamento(self.d))
            crea_file(self.d, f"docs/audio/a/{video_id}.mp3")
            return {"file": f"{video_id}.mp3"}, "", ""
        with mock.patch.object(yt2podcast, "download_audio", side_effect=falso):
            yt2podcast.giro(self.cfg)
        a = viste[0]
        self.assertEqual((a["show"], a["show_n"], a["show_tot"]), ("a", 1, 1))
        self.assertEqual((a["video_n"], a["video_tot"]), (1, 1))
        self.assertTrue(a["testo"])

    def test_senza_giro_l_avanzamento_non_scrive(self):
        finto_feed(["V1"]); finto_download_ok()
        yt2podcast.process_channel(self.cfg, canale(self.d, "a"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "run")))

    def test_solo_elabora_un_show_e_lista_gli_altri(self):
        aggiungi_playlist_finta(self.d, "pl", ["P1"])
        cfg = leggi_cfg(self.d)
        finto_download_ok()
        with mock.patch.object(yt2podcast, "process_channel") as pc:
            r = yt2podcast.giro(cfg, solo="pl")
        pc.assert_not_called()
        self.assertEqual([s["slug"] for s in r["show"]], ["a", "pl"])
        self.assertIn("Playlist pl", leggi(self.d, "docs/index.html"))
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.giro(cfg, solo="zzz")

    def test_solo_i_giri_completi_aggiornano_il_riuscito(self):
        riuscito = os.path.join(self.d, "run", "ultimo-riuscito.json")
        finto_feed(["V1"]); finto_download_ok()
        yt2podcast.giro(self.cfg, solo="a")
        self.assertFalse(os.path.exists(riuscito))
        yt2podcast.giro(self.cfg, azione="rimuovi", elabora=False)
        self.assertFalse(os.path.exists(riuscito))
        yt2podcast.giro(self.cfg)
        self.assertTrue(os.path.exists(riuscito))

    def test_senza_elaborare_ricostruisce_e_pubblica(self):
        self.cfg["git_auto_push"] = True
        with mock.patch.object(yt2podcast, "process_channel") as pc, \
                finto_git() as git_eseguiti:
            r = yt2podcast.giro(self.cfg, azione="rimuovi", elabora=False)
        pc.assert_not_called()
        self.assertEqual(r["azione"], "rimuovi")
        self.assertIn("<strong>A</strong>", leggi(self.d, "docs/index.html"))
        self.assertIn(("push", "-u", "origin", "main"), git_eseguiti)

    def test_playlist_elenco_dimezzato_non_rimuove_nulla(self):
        blocco = aggiungi_playlist_finta(self.d, "pl", list("ABCDEFGH"))
        finto_download_ok()
        yt2podcast.process_playlist(self.cfg, blocco)
        st = leggi_stato(self.d, "pl")
        self.assertEqual(st["ultimo_elenco"], 8)
        # l'elenco arriva tagliato a 3 voci (meno della meta' di 8)
        finto_elenco(list(zip("FGH", ["t"] * 3, [600] * 3)))
        chiamati = finto_download_ok()
        r = yt2podcast.process_playlist(self.cfg, blocco)
        self.assertTrue(r["elenco_fallito"])
        self.assertIn("incompleto", r["elenco_errore"])
        self.assertEqual(chiamati, [])
        st = leggi_stato(self.d, "pl")
        self.assertEqual(len(st["episodes"]), 8)
        self.assertEqual(st["ultimo_elenco"], 8)   # l'elenco cattivo non conta

    def test_playlist_elenco_un_po_piu_corto_va_bene(self):
        blocco = aggiungi_playlist_finta(self.d, "pl", list("ABCDEFGH"))
        finto_download_ok()
        yt2podcast.process_playlist(self.cfg, blocco)
        finto_elenco(list(zip("EFGH", ["t"] * 4, [600] * 4)))   # esattamente la meta'
        r = yt2podcast.process_playlist(self.cfg, blocco)
        self.assertFalse(r["elenco_fallito"])
        self.assertEqual(leggi_stato(self.d, "pl")["ultimo_elenco"], 4)

    def test_playlist_corta_non_fa_scattare_la_guardia(self):
        blocco = aggiungi_playlist_finta(self.d, "pl", list("ABC"))   # meno di 4
        finto_download_ok()
        yt2podcast.process_playlist(self.cfg, blocco)
        finto_elenco([("C", "t", 600)])
        self.assertFalse(yt2podcast.process_playlist(self.cfg, blocco)["elenco_fallito"])

    def test_playlist_con_elenco_fallito_non_rimuove_nulla(self):
        aggiungi_playlist_finta(self.d, "pl", ["P1"])
        cfg = leggi_cfg(self.d)
        finto_download_ok()
        ch = next(c for c in cfg["channels"] if c["slug"] == "pl")
        yt2podcast.process_playlist(cfg, ch)
        self.assertEqual(len(leggi_stato(self.d, "pl")["episodes"]), 1)
        with mock.patch.object(yt2podcast, "elenco_ytdlp",
                               side_effect=yt2podcast.Errore("boom")):
            r = yt2podcast.process_playlist(cfg, ch)
        self.assertEqual(len(leggi_stato(self.d, "pl")["episodes"]), 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.d, "docs", "audio", "pl", "P1.mp3")))
        self.assertEqual((r["tentati"], r["falliti"], r["count"]), (0, 0, 1))
        self.assertTrue(r["elenco_fallito"])

    def test_playlist_con_elenco_fallito_nel_giro_e_un_problema_non_un_fallimento(self):
        aggiungi_playlist_finta(self.d, "pl", ["P1"])
        cfg = leggi_cfg(self.d)
        finto_feed(["V1"]); finto_download_ok()
        with mock.patch.object(yt2podcast, "elenco_ytdlp",
                               side_effect=yt2podcast.Errore("HTTP Error 403")):
            r = yt2podcast.giro(cfg)
        self.assertEqual(r["esito"], "problemi")
        self.assertEqual(r["errori"][0]["codice"], "youtube")
        self.assertEqual([s["elenco_fallito"] for s in r["show"]], [False, True])

    def test_tutti_gli_elenchi_falliti_e_fallito(self):
        aggiungi_playlist_finta(self.d, "pl", ["P1"])
        cfg = leggi_cfg(self.d)
        err = yt2podcast.urllib.error.URLError("HTTP Error 403: Forbidden")
        with mock.patch.object(yt2podcast, "fetch_channel_feed", side_effect=err), \
                mock.patch.object(yt2podcast, "elenco_ytdlp",
                                  side_effect=yt2podcast.Errore("HTTP Error 403")), \
                cattura_notifiche() as n:
            r = yt2podcast.giro(cfg)
        self.assertEqual(r["esito"], "fallito"); self.assertEqual(len(n), 1)
        self.assertEqual(r["errori"][0]["codice"], "youtube")
        self.assertEqual(len(r["errori"]), 1)
        self.assertIn("Playlist pl", leggi(self.d, "docs/index.html"))

    def test_un_show_che_solleva_non_ferma_gli_altri(self):
        aggiungi_playlist_finta(self.d, "pl", ["P1"])
        cfg = leggi_cfg(self.d)
        finto_download_ok()
        with mock.patch.object(yt2podcast, "process_channel",
                               side_effect=ValueError("boom")), \
                cattura_notifiche() as n:
            r = yt2podcast.giro(cfg)
        self.assertEqual(r["esito"], "problemi"); self.assertEqual(n, [])
        self.assertTrue(r["completato"])
        self.assertEqual([s["errore_show"] for s in r["show"]], [True, False])
        self.assertEqual(r["show"][1]["nuovi"], 1)
        self.assertEqual(r["errori"][0]["codice"], "sconosciuto")
        self.assertIn("Playlist pl", leggi(self.d, "docs/index.html"))

    def test_tutti_gli_show_che_sollevano_e_fallito(self):
        with mock.patch.object(yt2podcast, "process_channel",
                               side_effect=ValueError("boom")), \
                cattura_notifiche() as n:
            r = yt2podcast.giro(self.cfg)
        self.assertEqual(r["esito"], "fallito"); self.assertEqual(len(n), 1)

    def test_video_singolo_con_sito_pieno(self):
        meta = {"id": "VVVVVVVVVVV", "title": "t", "duration": 600}
        chiamati = finto_download_ok()
        with mock.patch.object(yt2podcast, "video_metadata", return_value=meta), \
                finto_peso_sito(900):
            with self.assertRaises(yt2podcast.Errore) as c:
                yt2podcast.add_video("https://youtu.be/VVVVVVVVVVV", self.cfg)
        self.assertIn("spazio esaurito: sito oltre 900 MB", str(c.exception))
        self.assertEqual(chiamati, [])
        self.assertFalse(os.path.exists(os.path.join(self.d, "state", "youtube.json")))

    def test_video_singolo_fallito_dice_il_motivo(self):
        meta = {"id": "VVVVVVVVVVV", "title": "t", "duration": 600}
        finto_download({"VVVVVVVVVVV": (None, "HTTP Error 403: Forbidden", "fallito")})
        with mock.patch.object(yt2podcast, "video_metadata", return_value=meta):
            with self.assertRaises(yt2podcast.Errore) as c:
                yt2podcast.add_video("https://youtu.be/VVVVVVVVVVV", self.cfg)
        self.assertEqual(str(c.exception),
                         "Download fallito: HTTP Error 403: Forbidden")

    def test_canale_con_ytdlp_fallito_e_nessun_video(self):
        with mock.patch.object(yt2podcast, "fetch_channel_feed",
                               side_effect=yt2podcast.urllib.error.URLError("x")), \
                mock.patch.object(yt2podcast, "elenco_ytdlp",
                                  side_effect=yt2podcast.Errore("boom")):
            r = yt2podcast.process_channel(self.cfg, canale(self.d, "a"))
        self.assertTrue(r["elenco_fallito"])
        self.assertEqual((r["tentati"], r["falliti"]), (0, 0))


class TestSaveJson(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.p = os.path.join(self._t.name, "sotto", "x.json")

    def test_contenuto_identico_al_vecchio_formato(self):
        dati = {"titolo": "Perché è così", "n": [1, 2], "v": {"a": None}}
        yt2podcast.save_json(self.p, dati)
        atteso = json.dumps(dati, ensure_ascii=False, indent=2) + "\n"
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(f.read(), atteso)
        self.assertEqual(os.listdir(os.path.dirname(self.p)), ["x.json"])

    def test_errore_a_meta_lascia_intatto_il_file(self):
        yt2podcast.save_json(self.p, {"vecchio": 1})
        with open(self.p, encoding="utf-8") as f:
            prima = f.read()
        with mock.patch.object(yt2podcast.json, "dump",
                               side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                yt2podcast.save_json(self.p, {"nuovo": 2})
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(f.read(), prima)
        self.assertEqual(os.listdir(os.path.dirname(self.p)), ["x.json"])


class TestIndice(Base):
    def test_indice_ha_link_podcast(self):
        yt2podcast.build_index(self.cfg, [{"title": "A", "feed_url": "https://k8s9btwz9m-bit.github.io/podcast/a.xml", "count": 1}])
        h = leggi(self.d, "docs/index.html")
        self.assertIn('href="podcast://k8s9btwz9m-bit.github.io/podcast/a.xml"', h)
        self.assertIn("Apri in Podcast</a>", h)
        self.assertIn("<code>https://k8s9btwz9m-bit.github.io/podcast/a.xml</code>", h)


class TestRobustezza(Base):
    def test_rimuovi_rifiuta_slug_non_valido(self):
        for brutto in ("../x", "a/b", "A", "", "a b", "a\n", ".."):
            with self.assertRaises(yt2podcast.Errore, msg=brutto):
                yt2podcast.rimuovi(brutto)
        self.assertEqual(len(leggi_cfg(self.d)["channels"]), 1)

    def test_elenco_ytdlp_codice_diverso_da_zero(self):
        fallito = subprocess.CompletedProcess([], 1, '{"id": "X"}\n',
                                              "WARNING\nERROR: boom\n")
        with mock.patch.object(yt2podcast.subprocess, "run", return_value=fallito):
            with self.assertRaises(yt2podcast.Errore) as c:
                yt2podcast.elenco_ytdlp("url", 5)
        self.assertIn("boom", str(c.exception))

    def test_elenco_ytdlp_pretende_dati_completi(self):
        # senza questo yt-dlp tace e restituisce una playlist tagliata
        ok = subprocess.CompletedProcess([], 0, '{"id": "X"}\n', "")
        with mock.patch.object(yt2podcast.subprocess, "run", return_value=ok) as run:
            yt2podcast.elenco_ytdlp("url", 5)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--extractor-args") + 1],
                         "youtube:raise_incomplete_data")

    def test_timeout_ytdlp_e_un_errore(self):
        scaduto = subprocess.TimeoutExpired("yt-dlp", 120)
        with mock.patch.object(yt2podcast.subprocess, "run",
                               side_effect=scaduto) as run:
            with self.assertRaises(yt2podcast.Errore):
                yt2podcast.elenco_ytdlp("url", 5)
            self.assertEqual(run.call_args.kwargs["timeout"], 120)
            with self.assertRaises(yt2podcast.Errore):
                YTDLP_JSON_VERO(["url"])
            self.assertEqual(run.call_args.kwargs["timeout"], 120)
            with self.assertRaises(yt2podcast.Errore):
                yt2podcast.video_metadata("url")
            self.assertEqual(run.call_args.kwargs["timeout"], 120)


class TestCli(Base):
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

    def test_preview_con_qualsiasi_eccezione_stampa_json(self):
        with mock.patch.object(yt2podcast, "anteprima",
                               side_effect=KeyError("x")):
            codice, out = self._main("--preview", "https://youtu.be/abcdefghijk")
        self.assertEqual(codice, 1)
        self.assertIn("errore", json.loads(out))

    def test_preview_passa_le_regole_e_non_prende_il_blocco(self):
        with mock.patch.object(yt2podcast, "anteprima",
                               return_value={"tipo": "canale"}) as an, \
                mock.patch.object(stato_giro, "Blocco") as bl:
            codice, out = self._main("--preview", "LINK", "--regole",
                                     '{"max_duration_minutes": 30}')
        self.assertEqual(codice, 0)
        bl.assert_not_called()
        self.assertEqual(an.call_args.args[2], {"max_duration_minutes": 30})

    def test_regole_non_valide(self):
        codice, out = self._main("--add", "LINK", "--regole", "{no")
        self.assertEqual(codice, 1)
        codice, out = self._main("--preview", "LINK", "--regole", "{no")
        self.assertEqual(codice, 1)
        self.assertIn("errore", json.loads(out))

    def test_codici_di_uscita(self):
        finto_feed(["V1"]); finto_download_ok()
        self.assertEqual(self._main()[0], 0)
        finto_feed(["V9"])
        finto_download({"V9": (None, "HTTP Error 403", "fallito")})
        self.assertEqual(self._main()[0], 2)

    def test_occupato_codice_3_e_notifica(self):
        with cattura_notifiche() as n, \
                mock.patch.object(stato_giro, "Blocco") as bl:
            bl.return_value.__enter__.side_effect = stato_giro.Occupato("x")
            codice, _ = self._main("--senza-attesa")
        self.assertEqual(codice, 3)
        self.assertEqual(n, [("Nazg", "C'è già un aggiornamento in corso: riprova tra poco.")])
        self.assertEqual(bl.call_args.kwargs["attesa_s"], 0)

    def test_attesa_predefinita_30_minuti(self):
        with mock.patch.object(stato_giro, "Blocco") as bl:
            self._main()
        self.assertEqual(bl.call_args.kwargs["attesa_s"], 1800)

    def test_add_prosegue_con_il_giro_sul_nuovo_show(self):
        with mock.patch.object(yt2podcast, "aggiungi", return_value="nuovo") as ag, \
                mock.patch.object(yt2podcast, "giro",
                                  return_value={"esito": "riuscito"}) as gi:
            codice, _ = self._main("--add", "LINK", "--regole",
                                   '{"sponsorblock": false}')
        self.assertEqual(codice, 0)
        ag.assert_called_once_with("LINK", {"sponsorblock": False})
        self.assertEqual(gi.call_args.kwargs["solo"], "nuovo")

    def test_video_prosegue_con_il_giro(self):
        with mock.patch.object(yt2podcast, "add_video", return_value="youtube") as av, \
                mock.patch.object(yt2podcast, "giro",
                                  return_value={"esito": "riuscito"}) as gi:
            codice, _ = self._main("--video", "https://youtu.be/abcdefghijk")
        self.assertEqual(codice, 0)
        self.assertEqual(av.call_args.args[0], "https://youtu.be/abcdefghijk")
        self.assertEqual(gi.call_args.kwargs["solo"], "youtube")

    def test_remove_e_cover_ricostruiscono_senza_elaborare(self):
        img = crea_file(self._t.name, "nuova.png")
        with mock.patch.object(yt2podcast, "rimuovi") as rm, \
                mock.patch.object(yt2podcast, "cambia_copertina") as cc, \
                mock.patch.object(yt2podcast, "giro",
                                  return_value={"esito": "riuscito"}) as gi:
            self.assertEqual(self._main("--remove", "a")[0], 0)
            rm.assert_called_once_with("a")
            self.assertFalse(gi.call_args.kwargs["elabora"])
            self.assertEqual(self._main("--cover", "a", img)[0], 0)
            cc.assert_called_once_with("a", img)
            self.assertFalse(gi.call_args.kwargs["elabora"])

    def test_aggiorna_programma(self):
        with mock.patch.object(yt2podcast.subprocess, "run",
                               return_value=subprocess.CompletedProcess(
                                   [], 1, "", "uv: errore")) as run, \
                mock.patch.object(yt2podcast, "giro",
                                  return_value={"esito": "problemi"}) as gi:
            codice, _ = self._main("--aggiorna-programma")
        self.assertEqual(codice, 0)
        self.assertEqual(run.call_args.args[0], ["uv", "tool", "upgrade", "yt-dlp"])
        extra = gi.call_args.kwargs["errori_extra"]
        self.assertEqual(extra[0]["codice"], "aggiornamento")

    def test_aggiorna_programma_ok_non_aggiunge_errori(self):
        with mock.patch.object(yt2podcast.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "", "")), \
                mock.patch.object(yt2podcast, "giro",
                                  return_value={"esito": "riuscito"}) as gi:
            self._main("--aggiorna-programma")
        self.assertEqual(gi.call_args.kwargs["errori_extra"], [])

    def test_comando_fallito_scrive_il_riepilogo(self):
        with mock.patch.object(yt2podcast, "aggiungi",
                               side_effect=yt2podcast.Errore("Link sbagliato")):
            codice, _ = self._main("--add", "LINK")
        self.assertEqual(codice, 1)
        c = json.loads(leggi(self.d, "run/ultimo-comando.json"))
        self.assertEqual((c["azione"], c["messaggio"]), ("aggiungi", "Link sbagliato"))
        self.assertTrue(c["inizio"] and c["fine"])
        # l'ultimo giro non viene toccato
        self.assertFalse(os.path.exists(
            os.path.join(self.d, "run", "ultimo-giro.json")))

    def test_comando_fallito_lascia_intatto_l_ultimo_giro(self):
        stato_giro.chiudi_giro(self.d, {"esito": "riuscito", "fine": "F1"})
        stato_giro.scrivi_avanzamento(self.d, {"testo": "x"})
        with mock.patch.object(yt2podcast, "aggiungi",
                               side_effect=yt2podcast.Errore("Link sbagliato")):
            self._main("--add", "LINK")
        self.assertEqual(json.loads(leggi(self.d, "run/ultimo-giro.json"))["fine"], "F1")
        self.assertIsNone(stato_giro.leggi_avanzamento(self.d))

    def test_programmi_mancanti_scrivono_ultimo_comando(self):
        out = io.StringIO()
        with mock.patch.object(yt2podcast.sys, "argv", ["y", "--only", "a"]), \
                mock.patch.object(yt2podcast, "check_requirements",
                                  side_effect=yt2podcast.Errore(
                                      "ERRORE: programmi mancanti: yt-dlp, git")), \
                redirect_stdout(out):
            self.assertEqual(yt2podcast._main(), 1)
        c = json.loads(leggi(self.d, "run/ultimo-comando.json"))
        self.assertEqual(c["azione"], "aggiorna")
        self.assertIn("yt-dlp, git", c["messaggio"])

    def test_comando_con_eccezione_imprevista(self):
        with mock.patch.object(yt2podcast, "rimuovi", side_effect=KeyError("k")):
            with self.assertRaises(KeyError):
                with mock.patch.object(yt2podcast.sys, "argv",
                                       ["y", "--remove", "a"]), \
                        mock.patch.object(yt2podcast, "check_requirements"):
                    yt2podcast.main()
        c = json.loads(leggi(self.d, "run/ultimo-comando.json"))
        self.assertEqual(c["azione"], "rimuovi")
        self.assertTrue(c["messaggio"].startswith("Errore imprevisto"))

    def test_solo_sconosciuto_scrive_il_riepilogo(self):
        self.assertEqual(self._main("--only", "zzz")[0], 1)
        c = json.loads(leggi(self.d, "run/ultimo-comando.json"))
        self.assertEqual(c["azione"], "aggiorna")
        self.assertIn("zzz", c["messaggio"])

    def test_occupato_non_scrive_riepilogo(self):
        with mock.patch.object(stato_giro, "Blocco") as bl:
            bl.return_value.__enter__.side_effect = stato_giro.Occupato("x")
            self._main("--add", "LINK")
        self.assertFalse(os.path.exists(os.path.join(self.d, "run")))

    def test_azioni_dei_comandi_riusciti(self):
        with mock.patch.object(yt2podcast, "aggiungi", return_value="a"), \
                mock.patch.object(yt2podcast, "giro",
                                  return_value={"esito": "riuscito"}) as gi:
            self._main("--add", "LINK")
        self.assertEqual(gi.call_args.kwargs["azione"], "aggiungi")

    def _video(self, scarica):
        meta = {"id": "VVVVVVVVVVV", "title": "Il mio video", "duration": 600}
        with mock.patch.object(yt2podcast, "video_metadata", return_value=meta), \
                mock.patch.object(yt2podcast, "download_audio", side_effect=scarica):
            return self._main("--video", "https://youtu.be/VVVVVVVVVVV")

    def test_video_scrive_avanzamento_e_lo_toglie(self):
        viste = []

        def scarica(video_id, dest, cfg, regole=None):
            viste.append(stato_giro.leggi_avanzamento(self.d))
            crea_file(self.d, f"docs/audio/youtube/{video_id}.mp3")
            return {"file": f"{video_id}.mp3"}, "", ""
        self.assertEqual(self._video(scarica)[0], 0)
        self.assertIn("Il mio video", viste[0]["testo"])
        self.assertEqual(viste[0]["azione"], "video")
        self.assertIsNone(stato_giro.leggi_avanzamento(self.d))

    def test_video_fallito_toglie_l_avanzamento(self):
        viste = []

        def scarica(video_id, dest, cfg, regole=None):
            viste.append(stato_giro.leggi_avanzamento(self.d))
            return None, "HTTP Error 403", "fallito"
        self.assertEqual(self._video(scarica)[0], 1)
        self.assertIn("Il mio video", viste[0]["testo"])
        self.assertIsNone(stato_giro.leggi_avanzamento(self.d))

    def test_inizio_del_riepilogo_e_quello_del_comando(self):
        n = iter(range(10, 1000))
        with mock.patch.object(
                yt2podcast, "_ora",
                side_effect=lambda: f"2026-01-01T10:00:{next(n) % 60:02d}+00:00"):
            def scarica(video_id, dest, cfg, regole=None):
                crea_file(self.d, f"docs/audio/youtube/{video_id}.mp3")
                return {"file": f"{video_id}.mp3"}, "", ""
            self._video(scarica)
        r = json.loads(leggi(self.d, "run/ultimo-giro.json"))
        self.assertEqual(r["inizio"], "2026-01-01T10:00:10+00:00")
        self.assertLess(r["inizio"], r["fine"])

    def test_remove_e_cover_mostrano_cosa_fanno(self):
        testi = []
        img = crea_file(self._t.name, "nuova.png")

        def vedi(*a):
            testi.append(stato_giro.leggi_avanzamento(self.d)["testo"])
        with mock.patch.object(yt2podcast, "rimuovi", side_effect=vedi), \
                mock.patch.object(yt2podcast, "cambia_copertina", side_effect=vedi):
            self._main("--remove", "a")
            self._main("--cover", "a", img)
        self.assertEqual(testi, ["Rimuovo A…", "Preparo la copertina…"])
        self.assertIsNone(stato_giro.leggi_avanzamento(self.d))

    def test_errore_esce_con_1(self):
        codice, _ = self._main("--only", "zzz")
        self.assertEqual(codice, 1)


class TestScript(unittest.TestCase):
    def test_script_senza_blocco_ne_git(self):
        radice = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for nome in ("aggiorna-auto.sh", "Aggiorna podcast.command",
                     "Aggiungi video.command", "da-shortcut.sh"):
            with open(os.path.join(radice, nome), encoding="utf-8") as f:
                testo = f.read()
            self.assertIsNone(re.search(r"\bgit\s+(add|commit|push|diff|fetch)", testo), nome)
            self.assertNotIn("in-corso.lock", testo, nome)
            r = subprocess.run(["bash", "-n", os.path.join(radice, nome)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, nome + r.stderr)


if __name__ == "__main__":
    unittest.main()
