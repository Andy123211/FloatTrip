import AppKit
import CoreImage
import Foundation

let target = "https://github.com/shouzhuoshouzhuo/FloatTrip"
let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "assets/github-qr.png"
guard let filter = CIFilter(name: "CIQRCodeGenerator") else { exit(1) }
filter.setValue(Data(target.utf8), forKey: "inputMessage")
filter.setValue("M", forKey: "inputCorrectionLevel")
guard let qr = filter.outputImage else { exit(1) }
guard let color = CIFilter(name: "CIFalseColor") else { exit(1) }
color.setValue(qr, forKey: kCIInputImageKey)
color.setValue(CIColor(red: 0.063, green: 0.106, blue: 0.176, alpha: 1), forKey: "inputColor0")
color.setValue(CIColor(red: 1, green: 1, blue: 1, alpha: 1), forKey: "inputColor1")
guard let image = color.outputImage?.transformed(by: CGAffineTransform(scaleX: 14, y: 14)) else { exit(1) }
let context = CIContext(options: [.useSoftwareRenderer: true])
guard let cgImage = context.createCGImage(image, from: image.extent) else { exit(1) }
let bitmap = NSBitmapImageRep(cgImage: cgImage)
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
