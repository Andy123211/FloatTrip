import AppKit
import Vision
import Foundation

let input = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "assets/github-qr.png"
guard let image = NSImage(contentsOfFile: input), let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(1) }
let request = VNDetectBarcodesRequest()
request.symbologies = [.qr]
try VNImageRequestHandler(cgImage: cgImage).perform([request])
let values = (request.results ?? []).compactMap { $0.payloadStringValue }
let expected = "https://github.com/shouzhuoshouzhuo/FloatTrip"
guard values.contains(expected) else {
  fputs("QR verification failed: \(values)\n", stderr)
  exit(1)
}
print("QR verification passed: \(expected)")
