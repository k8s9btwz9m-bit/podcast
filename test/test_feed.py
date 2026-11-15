import os
import tempfile
import unittest
from xml.etree import ElementTree as ET

import yt2podcast
from aiuti import (SITE, installa, progetto_finto, leggi_cfg, canale,
                   crea_file)

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


class TestFeed(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = progetto_finto(self._t.name)
        installa(self)
        self.cfg = leggi_cfg(self.d)
        self.ch = canale(self.d, "a")

    def _item(self):
        radice = ET.parse(os.path.join(self.d, "docs", "a.xml")).getroot()
        return radice.find("channel/item")

    def test_feed_titoli_difficili_e_immagine(self):
        ep = {"id": "V1", "title": 'A & B <"c"> 😂',
              "published": "2026-11-14T10:00:00+00:00",
              "description": "d & e", "file": "V1.mp3", "duration": 60,
              "image": "V1.jpg"}
        crea_file(self.d, "docs/audio/a/V1.mp3")
        crea_file(self.d, "docs/audio/a/V1.jpg")
        yt2podcast.build_feed(self.cfg, self.ch, [ep])
        item = self._item()
        self.assertEqual(item.find("title").text, 'A & B <"c"> 😂')
        self.assertEqual(item.find("guid").text, "yt-V1")
        img = item.find(ITUNES + "image")
        self.assertTrue(img.get("href").endswith("/audio/a/V1.jpg"))

    def test_episodio_vecchio_senza_immagine(self):
        ep = {"id": "V1", "title": "t", "published": "2026-11-14T10:00:00+00:00",
              "description": "d", "file": "V1.mp3", "duration": 60}
        crea_file(self.d, "docs/audio/a/V1.mp3")
        yt2podcast.build_feed(self.cfg, self.ch, [ep])
        item = self._item()
        self.assertIsNone(item.find(ITUNES + "image"))
        self.assertEqual(item.find("guid").text, "yt-V1")
        self.assertEqual(item.find("enclosure").get("url"),
                         f"{SITE}/audio/a/V1.mp3")

    def test_immagine_dichiarata_ma_assente(self):
        ep = {"id": "V1", "title": "t", "published": "2026-11-14T10:00:00+00:00",
              "description": "d", "file": "V1.mp3", "duration": 60,
              "image": "V1.jpg"}
        crea_file(self.d, "docs/audio/a/V1.mp3")
        yt2podcast.build_feed(self.cfg, self.ch, [ep])
        self.assertIsNone(self._item().find(ITUNES + "image"))


if __name__ == "__main__":
    unittest.main()
