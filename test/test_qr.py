import os
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

RADICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QR = os.path.join(RADICE, "run", "qr")
SORGENTE = os.path.join(RADICE, "strumenti", "qr.swift")


@unittest.skipUnless(shutil.which("swiftc"), "swiftc non disponibile")
class TestQr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(QR):
            os.makedirs(os.path.dirname(QR), exist_ok=True)
            subprocess.run(["swiftc", "-O", SORGENTE, "-o", QR], check=True)

    def test_qr_svg(self):
        svg = subprocess.run([QR, "podcast://k8s9btwz9m-bit.github.io/podcast/a.xml"],
                             capture_output=True, text=True, check=True).stdout
        radice = ET.fromstring(svg)
        self.assertTrue(radice.get("viewBox").startswith("0 0 "))
        self.assertEqual(radice.get("shape-rendering"), "crispEdges")
        self.assertGreater(svg.count("<rect"), 200)

    def test_zona_di_rispetto_nel_svg(self):
        svg = subprocess.run([QR, "podcast://x.example/a.xml"],
                             capture_output=True, text=True, check=True).stdout
        radice = ET.fromstring(svg)
        rect = [e for e in radice if e.tag.endswith("rect")]
        sfondo, scuri = rect[0], rect[1:]
        self.assertEqual(sfondo.get("fill"), "#fff")
        lato = int(radice.get("viewBox").split()[2])
        self.assertEqual(sfondo.get("width"), str(lato))
        self.assertGreater(len(scuri), 200)
        for r in scuri:
            self.assertGreaterEqual(int(r.get("x")), 4)
            self.assertGreaterEqual(int(r.get("y")), 4)
            self.assertLessEqual(int(r.get("x")), lato - 5)
            self.assertLessEqual(int(r.get("y")), lato - 5)

    def test_testi_diversi_svg_diversi(self):
        a = subprocess.run([QR, "podcast://x.example/a.xml"],
                           capture_output=True, text=True, check=True).stdout
        b = subprocess.run([QR, "podcast://x.example/b.xml"],
                           capture_output=True, text=True, check=True).stdout
        self.assertNotEqual(a, b)

    def test_senza_argomento_esce_con_errore(self):
        r = subprocess.run([QR], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stderr.strip())


if __name__ == "__main__":
    unittest.main()
