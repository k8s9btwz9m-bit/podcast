import os
import subprocess
import tempfile
import unittest
from unittest import mock

import yt2podcast

DATI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati")


def _dato(nome):
    with open(os.path.join(DATI, nome), encoding="utf-8") as f:
        return f.read()


class TestComando(unittest.TestCase):
    def test_comando_completo(self):
        c = yt2podcast.comando_download(
            "ID", "/tmp/x", {"mono": True, "audio_quality": "9"},
            {"min_s": 120, "max_s": 2700}, True)
        self.assertIn("--sponsorblock-remove", c)
        self.assertEqual(c[c.index("--sponsorblock-remove") + 1],
                         "sponsor,selfpromo,interaction,intro,outro")
        self.assertIn("--embed-chapters", c)
        pp = c[c.index("--postprocessor-args") + 1]
        self.assertIn("-ac 1", pp)
        self.assertIn("loudnorm=I=-16:TP=-1.5:LRA=11", pp)
        self.assertEqual(c.count("--postprocessor-args"), 1)
        self.assertEqual(c[c.index("--match-filter") + 1],
                         "!is_live & duration > 120 & duration < 2700")
        self.assertIn("--write-thumbnail", c)
        self.assertEqual(c[c.index("--convert-thumbnails") + 1], "jpg")
        self.assertIn("--no-simulate", c)
        self.assertEqual(c[c.index("--print") + 1], "after_move:%(duration)s")
        # --print da solo attiva la modalita' silenziosa e il filtro non parla piu'
        self.assertIn("--no-quiet", c)

    def test_comando_spento(self):
        c = yt2podcast.comando_download(
            "ID", "/tmp/x", {"loudnorm": False, "chapters": False}, None, False)
        self.assertNotIn("--sponsorblock-remove", c)
        self.assertNotIn("--embed-chapters", c)
        self.assertNotIn("--postprocessor-args", c)


class TestDownloadAudio(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.dest = os.path.join(self._t.name, "audio")
        self.chiamate = []
        self.crea_jpg = True
        self.scrivi_mp3 = True
        self.esiti = []   # (returncode, stdout, stderr) per ogni chiamata yt-dlp
        for nome, val in (("get_duration", 540),):
            p = mock.patch.object(yt2podcast, nome, return_value=val)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(yt2podcast.subprocess, "run", side_effect=self._run)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = {"audio_quality": "9", "mono": True}
        self.regole = {"min_s": 0, "max_s": 0, "solo": [], "salta": [],
                       "sponsor": True}

    def _run(self, cmd, *a, **k):
        self.chiamate.append(cmd)
        if cmd[0] == "yt-dlp":
            rc, out, err = self.esiti.pop(0)
            if rc == 0 and self.scrivi_mp3:
                self._scrivi_file(cmd)
            return subprocess.CompletedProcess(cmd, rc, out, err)
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, '[CHAPTER]\n[CHAPTER]\n', "")
        if cmd[0] == "ffmpeg":
            open(cmd[-1], "wb").close()
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"comando inatteso: {cmd}")

    def _scrivi_file(self, cmd):
        vid = cmd[-1].split("=")[-1]
        open(os.path.join(self.dest, f"{vid}.mp3"), "wb").close()
        if self.crea_jpg:
            open(os.path.join(self.dest, f"{vid}.jpg"), "wb").close()

    def ytdlp(self):
        return [c for c in self.chiamate if c[0] == "yt-dlp"]

    def test_ripiego_sponsorblock(self):
        self.esiti = [(1, "", "ERROR: SponsorBlock: API unreachable"),
                      (0, "600\n", "")]
        info, motivo, tipo = yt2podcast.download_audio(
            "V1", self.dest, self.cfg, self.regole)
        self.assertEqual(tipo, "")
        self.assertEqual(len(self.ytdlp()), 2)
        self.assertIn("--sponsorblock-remove", self.ytdlp()[0])
        self.assertNotIn("--sponsorblock-remove", self.ytdlp()[1])
        self.assertEqual(info["tagliati_s"], 0)   # sponsor non attivo nel riuscito

    def test_fallimento_senza_sponsorblock_non_ritenta(self):
        self.esiti = [(1, "", "ERROR: Video unavailable")]
        info, motivo, tipo = yt2podcast.download_audio(
            "V1", self.dest, self.cfg, self.regole)
        self.assertIsNone(info)
        self.assertEqual(tipo, "fallito")
        self.assertEqual(len(self.ytdlp()), 1)

    def test_filtro_di_durata_da_output_reale(self):
        # output vero di yt-dlp (catturato) per un video fuori dai limiti
        self.esiti = [(0, _dato("ytdlp-filtro-scartato.stdout.txt"),
                       _dato("ytdlp-filtro-scartato.stderr.txt"))]
        self.scrivi_mp3 = False
        info, motivo, tipo = yt2podcast.download_audio(
            "sPQMrf_mdUk", self.dest, self.cfg, self.regole)
        self.assertIsNone(info)
        self.assertEqual(tipo, "regola")
        self.assertEqual(motivo, "fuori dai limiti di durata")
        self.assertEqual(len(self.ytdlp()), 1)

    def test_video_non_ancora_disponibile_e_attesa(self):
        self.scrivi_mp3 = False
        for testo in ("ERROR: [youtube] abc: This live event will begin in 3 days.",
                      "ERROR: [youtube] abc: Premieres in 2 hours",
                      "ERROR: [youtube] abc: This live event has ended"):
            self.esiti = [(1, "", testo)]
            info, motivo, tipo = yt2podcast.download_audio(
                "V1", self.dest, self.cfg, self.regole)
            self.assertIsNone(info)
            self.assertEqual(tipo, "attesa", testo)
            self.assertEqual(
                motivo, "non ancora disponibile (diretta o première programmata)")

    def test_riuscito_da_output_reale(self):
        # l'output vero di un download riuscito: l'ultima riga e' la durata
        self.esiti = [(0, _dato("ytdlp-riuscito.stdout.txt"),
                       _dato("ytdlp-riuscito.stderr.txt"))]
        info, _, tipo = yt2podcast.download_audio(
            "ny2fhvJGjiU", self.dest, self.cfg, self.regole)
        self.assertEqual(tipo, "")
        self.assertEqual(info["file"], "ny2fhvJGjiU.mp3")
        self.assertEqual(info["tagliati_s"], 766 - 540)

    def test_tagliati_s(self):
        self.esiti = [(0, "titolo\n600.0\n", "")]
        info, _, tipo = yt2podcast.download_audio(
            "V1", self.dest, self.cfg, self.regole)
        self.assertEqual(tipo, "")
        self.assertEqual(info["tagliati_s"], 60)     # 600 - 540
        self.assertEqual(info["file"], "V1.mp3")
        self.assertEqual(info["image"], "V1.jpg")
        self.assertEqual(info["chapters"], 2)

    def test_tagliati_zero_con_durata_reale_ignota(self):
        self.esiti = [(0, "600\n", "")]
        with mock.patch.object(yt2podcast, "get_duration", return_value=0):
            info, _, tipo = yt2podcast.download_audio(
                "V1", self.dest, self.cfg, self.regole)
        self.assertEqual(tipo, "")
        self.assertEqual(info["tagliati_s"], 0)      # ffprobe fallito: non 600

    def test_tagliati_zero_senza_sponsor(self):
        self.esiti = [(0, "600\n", "")]
        regole = dict(self.regole, sponsor=False)
        info, _, _ = yt2podcast.download_audio("V1", self.dest, self.cfg, regole)
        self.assertEqual(info["tagliati_s"], 0)

    def test_miniatura_mancante(self):
        self.crea_jpg = False
        self.esiti = [(0, "600\n", "")]
        info, _, tipo = yt2podcast.download_audio(
            "V1", self.dest, self.cfg, self.regole)
        self.assertEqual(tipo, "")
        self.assertIsNone(info["image"])
        self.assertEqual(info["file"], "V1.mp3")


class TestRimozione(unittest.TestCase):
    def test_rimuove_mp3_e_jpg(self):
        with tempfile.TemporaryDirectory() as d:
            from aiuti import crea_file
            crea_file(d, "V1.mp3"); crea_file(d, "V1.jpg")
            yt2podcast.rimuovi_episodio(d, {"id": "V1", "file": "V1.mp3",
                                            "image": "V1.jpg"})
            self.assertEqual(os.listdir(d), [])
            # episodio vecchio senza image, file gia' assente: nessun errore
            yt2podcast.rimuovi_episodio(d, {"id": "V2", "file": "V2.mp3"})


if __name__ == "__main__":
    unittest.main()
