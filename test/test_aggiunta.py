import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import yt2podcast
from aiuti import (installa, progetto_finto, scrivi_stato, leggi_stato,
                   leggi_cfg, canale, finto_elenco, finto_elenco_playlist,
                   finto_download_ok, finto_ytdlp_json, aggiungi_playlist_finta,
                   canale_playlist_nuovo, crea_file, ID_CANALE)

UC_NUOVO = "UCRuS9fWBnGbCzKdgcJp_owQ"
LINK_CANALE = f"https://www.youtube.com/channel/{UC_NUOVO}"


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = progetto_finto(self._t.name)
        installa(self)
        self.cfg = leggi_cfg(self.d)
        # copertine e rete: mai davvero
        self.scaricati = []

        def scarica(url, dest):
            self.scaricati.append(url)
            open(dest, "wb").write(b"img")
        self.quadrati = []

        def quadra(path, lato=720):
            self.quadrati.append((os.path.basename(path), lato))
            return True
        for nome, f in (("scarica_file", scarica), ("quadra_immagine", quadra)):
            p = mock.patch.object(yt2podcast, nome, side_effect=f)
            p.start()
            self.addCleanup(p.stop)


class TestAnteprima(Base):
    def test_anteprima_canale_con_regola(self):
        finto_elenco([("A", "Lungo", 3669), ("B", "Corto ok", 1031),
                      ("C", "Giveaway time", 900)])
        a = yt2podcast.anteprima(LINK_CANALE, self.cfg,
                                 {"max_duration_minutes": 45,
                                  "title_exclude": ["giveaway"]})
        self.assertEqual([(v["id"], v["entra"], v["motivo"]) for v in a["video"]],
                         [("A", False, "più lungo di 45 min"), ("B", True, ""),
                          ("C", False, "titolo con: giveaway")])
        self.assertEqual(a["stima_mb"], 9)          # 1031 s ≈ 17,2 min × 0,5
        self.assertEqual((a["tipo"], a["id"], a["gia_presente"]),
                         ("canale", UC_NUOVO, False))

    def test_anteprima_canale_gia_presente_e_ultimi_dieci(self):
        finto_elenco([(f"V{i}", "t", 600) for i in range(30)])
        a = yt2podcast.anteprima(ID_CANALE, self.cfg)
        self.assertTrue(a["gia_presente"])
        self.assertEqual(len(a["video"]), 10)

    def test_anteprima_playlist_ultimi_cinquanta_tutti_dentro(self):
        finto_elenco_playlist([f"V{i}" for i in range(60)])
        a = yt2podcast.anteprima("https://www.youtube.com/playlist?list=PLxyz",
                                 self.cfg, {"max_duration_minutes": 1})
        self.assertEqual(a["tipo"], "playlist")
        self.assertEqual([v["id"] for v in a["video"]],
                         [f"V{i}" for i in range(10, 60)])
        self.assertTrue(all(v["entra"] for v in a["video"]))
        self.assertEqual(a["stima_mb"], 250)         # 50 × 10 min × 0,5
        self.assertEqual(a["title"], "Playlist finta")

    def test_anteprima_video(self):
        meta = {"id": "yMP3ZxUWOaQ", "title": "Un video", "duration": 600}
        with mock.patch.object(yt2podcast, "video_metadata", return_value=meta):
            a = yt2podcast.anteprima("youtu.be/yMP3ZxUWOaQ", self.cfg)
        self.assertEqual(a["tipo"], "video")
        self.assertEqual(a["video"], [{"id": "yMP3ZxUWOaQ", "title": "Un video",
                                       "duration": 600, "entra": True, "motivo": ""}])
        self.assertEqual(a["stima_mb"], 5)
        self.assertFalse(a["gia_presente"])

    def test_anteprima_link_sconosciuto(self):
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.anteprima("ciao", self.cfg)


class TestInfoSorgente(Base):
    def test_handle_risolto_in_channel_id(self):
        finto_ytdlp_json({"channel_id": UC_NUOVO, "channel": "Dr Blitz",
                          "thumbnails": [{"id": "0", "url": "https://x/banner.jpg"},
                                         {"id": "avatar_uncropped",
                                          "url": "https://x/avatar.jpg"}]})
        i = yt2podcast.info_sorgente("www.youtube.com/@DrBlitz")
        self.assertEqual((i["tipo"], i["id"], i["title"], i["avatar_url"]),
                         ("canale", UC_NUOVO, "Dr Blitz", "https://x/avatar.jpg"))

    def test_playlist_privata(self):
        finto_ytdlp_json(yt2podcast.Errore("ERRORE: yt-dlp"))
        with self.assertRaises(yt2podcast.Errore) as c:
            yt2podcast.info_sorgente("https://www.youtube.com/playlist?list=PLxyz")
        self.assertEqual(str(c.exception),
                         'Questa playlist è privata o non esiste. '
                         'Rendila pubblica o "non in elenco" e riprova.')

    def test_link_sconosciuto(self):
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.info_sorgente("ciao")


class TestAggiungi(Base):
    def test_canale_con_regole_e_copertina(self):
        slug = yt2podcast.aggiungi(LINK_CANALE, {
            "max_duration_minutes": 45, "title_exclude": ["giveaway"],
            "min_duration_minutes": None, "title_include": [], "sponsorblock": False,
            "altro": "ignorato"})
        self.assertEqual(slug, "canale-finto")
        c = canale(self.d, slug)
        self.assertEqual((c["id"], c["title"]), (UC_NUOVO, "Canale finto"))
        self.assertEqual(c["max_duration_minutes"], 45)
        self.assertEqual(c["title_exclude"], ["giveaway"])
        self.assertIs(c["sponsorblock"], False)
        for k in ("min_duration_minutes", "title_include", "altro"):
            self.assertNotIn(k, c)
        self.assertEqual(self.scaricati, ["https://img.esempio/avatar.jpg"])
        self.assertIn(("cover-canale-finto.jpg", 1400),
                      [(n.replace(".nuova", ""), l) for n, l in self.quadrati])
        self.assertTrue(os.path.exists(
            os.path.join(self.d, "docs", "cover-canale-finto.jpg")))

    def test_copertina_non_scaricabile_non_blocca(self):
        with mock.patch.object(yt2podcast, "scarica_file",
                               side_effect=OSError("rete")):
            slug = yt2podcast.aggiungi(LINK_CANALE)
        self.assertEqual(canale(self.d, slug)["id"], UC_NUOVO)
        self.assertFalse(os.path.exists(
            os.path.join(self.d, "docs", f"cover-{slug}.jpg")))

    def test_gia_presente(self):
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.aggiungi(ID_CANALE)

    def test_playlist(self):
        finto_elenco_playlist(["A", "B"])
        slug = yt2podcast.aggiungi("https://www.youtube.com/playlist?list=PLxyz")
        c = canale(self.d, slug)
        self.assertEqual((slug, c["type"], c["playlist_id"], c["max_episodes"]),
                         ("playlist-finta", "playlist", "PLxyz", 50))
        self.assertEqual(self.scaricati, ["https://i.ytimg.com/vi/A/hqdefault.jpg"])
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.aggiungi("https://www.youtube.com/playlist?list=PLxyz")

    def test_video(self):
        meta = {"id": "yMP3ZxUWOaQ", "title": "Un video", "duration": 600,
                "uploader": "Tizio"}
        finto_download_ok()
        with mock.patch.object(yt2podcast, "video_metadata", return_value=meta):
            slug = yt2podcast.aggiungi("youtube.com/watch?v=yMP3ZxUWOaQ")
            self.assertEqual(slug, "youtube")
            self.assertEqual([e["id"] for e in leggi_stato(self.d, "youtube")["episodes"]],
                             ["yMP3ZxUWOaQ"])
            with self.assertRaises(yt2podcast.Errore):
                yt2podcast.aggiungi("https://youtu.be/yMP3ZxUWOaQ")

    def test_sconosciuto(self):
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.aggiungi("ciao")


class TestPlaylist(Base):
    def test_playlist_rispecchia(self):
        aggiungi_playlist_finta(self.d, "pl", ["A", "B"]); finto_download_ok()
        yt2podcast.process_playlist(self.cfg, canale(self.d, "pl"))
        finto_elenco_playlist(["B", "C"])
        r = yt2podcast.process_playlist(self.cfg, canale(self.d, "pl"))
        ids = [e["id"] for e in leggi_stato(self.d, "pl")["episodes"]]
        self.assertEqual(ids, ["C", "B"])           # C visto per ultimo, quindi in cima
        self.assertFalse(os.path.exists(os.path.join(self.d, "docs/audio/pl/A.mp3")))
        self.assertEqual(r["rimossi"], 1)
        self.assertEqual((r["nuovi"], r["count"]), (1, 2))

    def test_playlist_grande_primo_import(self):
        finto_elenco_playlist([f"V{i}" for i in range(60)]); finto_download_ok()
        yt2podcast.process_playlist(self.cfg, canale_playlist_nuovo(self.d))
        eps = leggi_stato(self.d, "pl")["episodes"]
        self.assertEqual({e["id"] for e in eps}, {f"V{i}" for i in range(10, 60)})
        # l'ultimo della playlist e' il piu' nuovo, e l'ordine si conserva
        self.assertEqual([e["id"] for e in eps], [f"V{i}" for i in range(59, 9, -1)])

    def test_playlist_senza_regole_di_durata_e_titolo(self):
        blocco = canale_playlist_nuovo(self.d)
        blocco["title_exclude"] = ["video"]
        finto_elenco_playlist(["A"])
        visti = []

        def falso(video_id, dest, cfg, regole=None):
            visti.append(regole)
            crea_file(dest, f"{video_id}.mp3")
            return {"file": f"{video_id}.mp3"}, "", ""
        with mock.patch.object(yt2podcast, "download_audio", side_effect=falso):
            yt2podcast.process_playlist(self.cfg, blocco)
        self.assertEqual((visti[0]["min_s"], visti[0]["max_s"]), (0, 0))
        self.assertEqual(len(leggi_stato(self.d, "pl")["episodes"]), 1)

    def test_playlist_fallito_si_ritenta_tre_volte(self):
        blocco = canale_playlist_nuovo(self.d)
        finto_elenco_playlist(["A"])
        from aiuti import finto_download
        finto_download({"A": (None, "HTTP Error 403", "fallito")})
        for giro in (1, 2):
            yt2podcast.process_playlist(self.cfg, blocco)
            self.assertEqual(leggi_stato(self.d, "pl")["falliti"]["A"]["tentativi"], giro)
        yt2podcast.process_playlist(self.cfg, blocco)
        s = leggi_stato(self.d, "pl")
        self.assertIn("A", s["skipped"])
        self.assertNotIn("A", s["falliti"])

    def test_playlist_vuota_non_cancella(self):
        blocco = canale_playlist_nuovo(self.d)
        finto_elenco_playlist(["A"]); finto_download_ok()
        yt2podcast.process_playlist(self.cfg, blocco)
        finto_elenco_playlist([])
        self.assertTrue(yt2podcast.process_playlist(self.cfg, blocco)["elenco_fallito"])
        self.assertEqual(len(leggi_stato(self.d, "pl")["episodes"]), 1)


class TestRimuoviECopertina(Base):
    def test_rimuovi_cancella_tutto(self):
        for p in ("state/a.json", "docs/a.xml", "docs/cover-a.jpg", "docs/audio/a/V1.mp3"):
            crea_file(self.d, p)
        yt2podcast.rimuovi("a")
        for p in ("state/a.json", "docs/a.xml", "docs/cover-a.jpg", "docs/audio/a"):
            self.assertFalse(os.path.exists(os.path.join(self.d, p)), p)
        self.assertNotIn("a", [c["slug"] for c in leggi_cfg(self.d)["channels"]])
        with self.assertRaises(yt2podcast.Errore): yt2podcast.rimuovi("a")

    def test_cambia_copertina(self):
        img = crea_file(self._t.name, "nuova.png")
        yt2podcast.cambia_copertina("a", img)
        self.assertTrue(os.path.exists(os.path.join(self.d, "docs", "cover-a.jpg")))
        self.assertEqual(self.quadrati[-1][1], 1400)
        self.assertEqual([f for f in os.listdir(os.path.join(self.d, "docs"))
                          if "nuova" in f], [])

    def test_cambia_copertina_errori(self):
        img = crea_file(self._t.name, "nuova.png")
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.cambia_copertina("zzz", img)
        with self.assertRaises(yt2podcast.Errore):
            yt2podcast.cambia_copertina("a", os.path.join(self._t.name, "manca.png"))
        with mock.patch.object(yt2podcast, "quadra_immagine", return_value=False):
            with self.assertRaises(yt2podcast.Errore):
                yt2podcast.cambia_copertina("a", img)
        self.assertFalse(os.path.exists(os.path.join(self.d, "docs", "cover-a.jpg")))


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

    def test_preview_stampa_solo_json(self):
        finto_elenco([("A", "Uno", 600)])
        codice, out = self._main("--preview", LINK_CANALE)
        self.assertEqual(codice, 0)
        self.assertEqual(json.loads(out)["video"][0]["id"], "A")

    def test_preview_errore(self):
        codice, out = self._main("--preview", "ciao")
        self.assertEqual(codice, 1)
        self.assertIn("errore", json.loads(out))

    def test_only_elabora_uno_solo_ma_l_indice_ha_tutti(self):
        aggiungi_playlist_finta(self.d, "pl", ["A"])
        finto_download_ok()
        scrivi_stato(self.d, "a", {"episodes": [{"id": "X", "title": "x",
                                                 "published": "2026-01-01T00:00:00+00:00",
                                                 "file": "X.mp3"}]})
        with mock.patch.object(yt2podcast, "process_channel") as pc:
            codice, out = self._main("--only", "pl")
        self.assertEqual(codice, 0)
        pc.assert_not_called()
        indice = open(os.path.join(self.d, "docs", "index.html"), encoding="utf-8").read()
        self.assertIn("Playlist pl", indice)
        self.assertIn("<strong>A</strong> — 1 episodi", indice)
        codice, out = self._main("--only", "zzz")
        self.assertEqual(codice, 1)


if __name__ == "__main__":
    unittest.main()
