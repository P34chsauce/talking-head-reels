// Find the largest face in each image with Apple's Vision framework (on-device, no model download).
//   face <image>...   ->   one JSON line per image: {"x":..,"y":..,"w":..} (face centre and width as
//   fractions of the image, origin top-left) or null when there's no face.
// Built once by lib/reels/media.py into models/bin/face.
import Foundation
import Vision

for path in CommandLine.arguments.dropFirst() {
    let request = VNDetectFaceRectanglesRequest()
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:])
    var line = "null"
    if (try? handler.perform([request])) != nil,
       let face = request.results?.max(by: { $0.boundingBox.width < $1.boundingBox.width }) {
        let b = face.boundingBox  // normalised, origin bottom-left
        line = String(format: "{\"x\":%.4f,\"y\":%.4f,\"w\":%.4f}", b.midX, 1 - b.midY, b.width)
    }
    print(line)
}
