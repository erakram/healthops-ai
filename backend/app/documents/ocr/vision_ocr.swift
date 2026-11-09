// On-device OCR with Apple's Vision framework (the engine behind Live Text).
// Usage: vision-ocr <image.png>
// Prints JSON: [{"text": "...", "x0": .., "y0": .., "x1": .., "y1": ..}] with one entry per word,
// coordinates as fractions of the image, origin at the top left.
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count == 2, let url = URL(string: "file://" + args[1]) else {
    FileHandle.standardError.write("usage: vision-ocr <image.png>\n".data(using: .utf8)!)
    exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false  // codes and IDs must come through as printed
let handler = VNImageRequestHandler(url: url, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write("ocr failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}
var words: [[String: Any]] = []
for observation in request.results ?? [] {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let text = candidate.string
    var index = text.startIndex
    for part in text.split(separator: " ", omittingEmptySubsequences: true) {
        guard let range = text.range(of: part, range: index..<text.endIndex) else { continue }
        index = range.upperBound
        let box = (try? candidate.boundingBox(for: range))?.boundingBox ?? observation.boundingBox
        words.append(["text": String(part), "x0": box.minX, "y0": 1 - box.maxY, "x1": box.maxX, "y1": 1 - box.minY])
    }
}
let data = try JSONSerialization.data(withJSONObject: words)
FileHandle.standardOutput.write(data)
