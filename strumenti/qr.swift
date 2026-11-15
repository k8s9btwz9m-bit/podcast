// Codice QR come SVG: un <rect> di lato 1 per ogni modulo scuro, correzione M.
// Uso: run/qr "TESTO"   (compilato con: swiftc -O strumenti/qr.swift -o run/qr)
import CoreImage
import Foundation

func esci(_ messaggio: String) -> Never {
    FileHandle.standardError.write((messaggio + "\n").data(using: .utf8)!)
    exit(1)
}

guard CommandLine.arguments.count >= 2, !CommandLine.arguments[1].isEmpty else {
    esci("Uso: qr \"TESTO\"")
}
let testo = CommandLine.arguments[1]

guard let filtro = CIFilter(name: "CIQRCodeGenerator"),
      let dati = testo.data(using: .utf8) else {
    esci("Generatore di codici QR non disponibile.")
}
filtro.setValue(dati, forKey: "inputMessage")
filtro.setValue("M", forKey: "inputCorrectionLevel")
guard let immagine = filtro.outputImage,
      let cg = CIContext().createCGImage(immagine, from: immagine.extent) else {
    esci("Non sono riuscito a generare il codice QR.")
}

let w = cg.width, h = cg.height
var pixel = [UInt8](repeating: 0, count: w * h * 4)
guard let contesto = CGContext(
    data: &pixel, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
    space: CGColorSpaceCreateDeviceRGB(),
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    esci("Non sono riuscito a leggere i moduli del codice QR.")
}
contesto.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))

// Zona di rispetto di 4 moduli per lato, bianca, dentro l'SVG stesso.
let margine = 4
let lato = w + 2 * margine
var rettangoli = "<rect x=\"0\" y=\"0\" width=\"\(lato)\" height=\"\(h + 2 * margine)\" fill=\"#fff\"/>"
for y in 0..<h {
    for x in 0..<w where pixel[(y * w + x) * 4] < 128 {
        rettangoli += "<rect x=\"\(x + margine)\" y=\"\(y + margine)\" width=\"1\" height=\"1\"/>"
    }
}
print("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 \(lato) \(h + 2 * margine)\" shape-rendering=\"crispEdges\" role=\"img\" aria-label=\"Codice QR\">\(rettangoli)</svg>")
