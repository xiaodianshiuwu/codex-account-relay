import AppKit
import Foundation

let outputDirectory = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "Assets")
try? FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

let size = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
guard let context = NSGraphicsContext.current else { exit(1) }
context.imageInterpolation = .high

let background = NSBezierPath(
    roundedRect: NSRect(x: 54, y: 54, width: 916, height: 916),
    xRadius: 220,
    yRadius: 220
)
let gradient = NSGradient(colors: [
    NSColor(red: 0.10, green: 0.36, blue: 0.86, alpha: 1),
    NSColor(red: 0.42, green: 0.28, blue: 0.92, alpha: 1),
])
gradient?.draw(in: background, angle: -55)

NSColor.white.withAlphaComponent(0.10).setFill()
NSBezierPath(ovalIn: NSRect(x: 150, y: 560, width: 720, height: 380)).fill()

if let symbol = NSImage(
    systemSymbolName: "arrow.triangle.2.circlepath",
    accessibilityDescription: nil
)?.withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: 430, weight: .bold)) {
    let symbolImage = NSImage(size: symbol.size)
    symbolImage.lockFocus()
    symbol.draw(in: NSRect(origin: .zero, size: symbol.size))
    NSColor.white.set()
    NSRect(origin: .zero, size: symbol.size).fill(using: .sourceAtop)
    symbolImage.unlockFocus()

    let target = NSRect(
        x: (1024 - symbol.size.width * 0.72) / 2,
        y: (1024 - symbol.size.height * 0.72) / 2,
        width: symbol.size.width * 0.72,
        height: symbol.size.height * 0.72
    )
    symbolImage.draw(in: target, from: .zero, operation: .sourceOver, fraction: 1.0)
}
image.unlockFocus()

guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    exit(1)
}
let pngURL = outputDirectory.appendingPathComponent("icon-1024.png")
try png.write(to: pngURL)
print(pngURL.path)
