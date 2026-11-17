// usage: render2 chars.txt out.tsv fontName threshold [convert=1]
// Renders each char (converted Hant->Hans unless convert=0) so that the ink box of the
// reference char maps onto the 15x14 cell (cols 1..15, rows 0..13); all chars share that transform.
import Foundation
import CoreText
import CoreGraphics

let a = CommandLine.arguments
let chars = try! String(contentsOfFile: a[1], encoding: .utf8).split(separator: "\n").map(String.init)
let fontName = a[3]
let thr = Double(a[4])!
let convert = a.count > 5 ? a[5] != "0" : true
let S = 256
let font = CTFontCreateWithName(fontName as CFString, 200, nil)
let overrides: [String: String] = ["鵰": "雕", "蘠": "蔷", "錧": "馆", "玆": "兹"]

func renderHi(_ s: String) -> [UInt8] {
    let cs = CGColorSpaceCreateDeviceGray()
    let ctx = CGContext(data: nil, width: S, height: S, bitsPerComponent: 8, bytesPerRow: S, space: cs, bitmapInfo: CGImageAlphaInfo.none.rawValue)!
    ctx.setFillColor(gray: 0, alpha: 1); ctx.fill(CGRect(x: 0, y: 0, width: S, height: S))
    ctx.setFillColor(gray: 1, alpha: 1)
    let attr = NSAttributedString(string: s, attributes: [kCTFontAttributeName as NSAttributedString.Key: font, kCTForegroundColorFromContextAttributeName as NSAttributedString.Key: true])
    let line = CTLineCreateWithAttributedString(attr)
    ctx.textPosition = CGPoint(x: 28, y: 50)
    CTLineDraw(line, ctx)
    let p = ctx.data!.assumingMemoryBound(to: UInt8.self)
    var out = [UInt8](repeating: 0, count: S*S)
    // flip so row 0 = top
    for y in 0..<S { for x in 0..<S { out[y*S+x] = p[y*S + x] } }
    return out
}
func inkBox(_ img: [UInt8]) -> (Int, Int, Int, Int) {
    var x0 = S, y0 = S, x1 = -1, y1 = -1
    for y in 0..<S { for x in 0..<S where img[y*S+x] > 127 { x0 = min(x0,x); x1 = max(x1,x); y0 = min(y0,y); y1 = max(y1,y) } }
    return (x0, y0, x1, y1)
}
// reference box from several full-size characters (union)
var rx0 = S, ry0 = S, rx1 = 0, ry1 = 0
for r in ["國", "图", "圆", "回"] {
    let b = inkBox(renderHi(r))
    rx0 = min(rx0, b.0); ry0 = min(ry0, b.1); rx1 = max(rx1, b.2); ry1 = max(ry1, b.3)
}
let cw = Double(rx1 - rx0 + 1) / 15.0, ch = Double(ry1 - ry0 + 1) / 14.0
var out = ""
for t in chars {
    var s = t
    if convert { s = overrides[t] ?? (t.applyingTransform(StringTransform("Hant-Hans"), reverse: false) ?? t) }
    let img = renderHi(s)
    var bytes = [UInt8](repeating: 0, count: 28)
    for r in 0..<14 {
        var v: UInt16 = 0
        for c in 0..<15 {
            let xa = Double(rx0) + Double(c) * cw, xb = xa + cw
            let ya = Double(ry0) + Double(r) * ch, yb = ya + ch
            var sum = 0.0, area = 0.0
            var y = Int(ya)
            while Double(y) < yb {
                let wy = min(Double(y+1), yb) - max(Double(y), ya)
                var x = Int(xa)
                while Double(x) < xb {
                    let wx = min(Double(x+1), xb) - max(Double(x), xa)
                    if x >= 0 && x < S && y >= 0 && y < S { sum += Double(img[y*S+x]) / 255.0 * wx * wy }
                    area += wx * wy
                    x += 1
                }
                y += 1
            }
            if sum / area >= thr { v |= UInt16(1) << UInt16(14 - c) }  // col c -> bit for column c+1
        }
        bytes[r*2] = UInt8(v >> 8); bytes[r*2+1] = UInt8(v & 0xff)
    }
    out += "\(t)\t\(s)\t" + bytes.map { String(format: "%02x", $0) }.joined() + "\n"
}
try! out.write(toFile: a[2], atomically: true, encoding: .utf8)
