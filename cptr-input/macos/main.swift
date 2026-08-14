// cptr-input (macOS backend)
//
// Neutral JSON protocol (see ../protocol.md) -> CGEvent. Signed with a stable
// bundle id so the Accessibility grant survives tool upgrades. One connection
// per client, each on its own queue with a read timeout, so a stuck peer can
// never wedge the daemon.

import Darwin
import Foundation
import CoreGraphics
import ApplicationServices

let socketPath = ("~/.cptr/input.sock" as NSString).expandingTildeInPath
let EVENT_TAG: Int64 = 0x43505452  // "CPTR" — lets a verifier tap drop our own events

// --- helpers ----------------------------------------------------------------

func mainDisplayBounds() -> CGRect { CGDisplayBounds(CGMainDisplayID()) }

func point(_ nx: Double, _ ny: Double) -> CGPoint {
    let b = mainDisplayBounds()
    return CGPoint(x: b.origin.x + max(0, min(1, nx)) * b.size.width,
                   y: b.origin.y + max(0, min(1, ny)) * b.size.height)
}

func flags(_ mods: [String]) -> CGEventFlags {
    var f = CGEventFlags()
    for m in mods {
        switch m {
        case "cmd", "meta", "super": f.insert(.maskCommand)
        case "shift": f.insert(.maskShift)
        case "alt", "option": f.insert(.maskAlternate)
        case "ctrl", "control": f.insert(.maskControl)
        default: break
        }
    }
    return f
}

func tagAndPost(_ e: CGEvent?, _ f: CGEventFlags = []) {
    guard let e = e else { return }
    if !f.isEmpty { e.flags = f }
    e.setIntegerValueField(.eventSourceUserData, value: EVENT_TAG)
    e.post(tap: .cghidEventTap)
}

// W3C `code` -> macOS virtual keycode (kVK_*). Only what shortcuts/navigation
// need; printable text goes through the "text" op instead.
let KEYCODES: [String: CGKeyCode] = [
    "KeyA":0,"KeyS":1,"KeyD":2,"KeyF":3,"KeyH":4,"KeyG":5,"KeyZ":6,"KeyX":7,
    "KeyC":8,"KeyV":9,"KeyB":11,"KeyQ":12,"KeyW":13,"KeyE":14,"KeyR":15,"KeyY":16,
    "KeyT":17,"KeyO":31,"KeyU":32,"KeyI":34,"KeyP":35,"KeyL":37,"KeyJ":38,"KeyK":40,
    "KeyN":45,"KeyM":46,
    "Digit1":18,"Digit2":19,"Digit3":20,"Digit4":21,"Digit6":22,"Digit5":23,
    "Digit9":25,"Digit7":26,"Digit8":28,"Digit0":29,
    "Equal":24,"Minus":27,"BracketRight":30,"BracketLeft":33,"Quote":39,
    "Semicolon":41,"Backslash":42,"Comma":43,"Slash":44,"Period":47,"Backquote":50,
    "Enter":36,"Tab":48,"Space":49,"Backspace":51,"Escape":53,"Delete":117,
    "Home":115,"End":119,"PageUp":116,"PageDown":121,
    "ArrowLeft":123,"ArrowRight":124,"ArrowDown":125,"ArrowUp":126,
    "MetaLeft":55,"ShiftLeft":56,"CapsLock":57,"AltLeft":58,"ControlLeft":59,
    "ShiftRight":60,"AltRight":61,"ControlRight":62,
    "F1":122,"F2":120,"F3":99,"F4":118,"F5":96,"F6":97,"F7":98,"F8":100,
    "F9":101,"F10":109,"F11":103,"F12":111,
]

// track held buttons so a move becomes a drag
var heldButtons: Int = 0

func handle(_ cmd: [String: Any]) -> [String: Any] {
    let op = cmd["op"] as? String ?? ""
    switch op {
    case "ping", "trusted":
        return ["ok": true, "trusted": AXIsProcessTrusted(), "platform": "macos"]

    case "move":
        let p = point(cmd["x"] as? Double ?? 0, cmd["y"] as? Double ?? 0)
        let type: CGEventType = heldButtons & 1 != 0 ? .leftMouseDragged
                              : heldButtons & 2 != 0 ? .rightMouseDragged
                              : .mouseMoved
        tagAndPost(CGEvent(mouseEventSource: nil, mouseType: type,
                           mouseCursorPosition: p, mouseButton: .left))
        return ["ok": true]

    case "button":
        let name = cmd["button"] as? String ?? "left"
        let down = (cmd["action"] as? String ?? "down") == "down"
        let clicks = cmd["clicks"] as? Int ?? 1
        let p: CGPoint
        if let nx = cmd["x"] as? Double, let ny = cmd["y"] as? Double { p = point(nx, ny) }
        else { p = CGEvent(source: nil)?.location ?? .zero }
        let (type, btn): (CGEventType, CGMouseButton)
        switch name {
        case "right": type = down ? .rightMouseDown : .rightMouseUp; btn = .right
        case "middle": type = down ? .otherMouseDown : .otherMouseUp; btn = .center
        default: type = down ? .leftMouseDown : .leftMouseUp; btn = .left
        }
        let bit = name == "right" ? 2 : name == "middle" ? 4 : 1
        if down { heldButtons |= bit } else { heldButtons &= ~bit }
        let e = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: p, mouseButton: btn)
        if clicks > 0 { e?.setIntegerValueField(.mouseEventClickState, value: Int64(clicks)) }
        tagAndPost(e, flags(cmd["mods"] as? [String] ?? []))
        return ["ok": true]

    case "scroll":
        // macOS scrolls opposite to DOM deltas
        let dy = Int32(-(cmd["dy"] as? Double ?? 0))
        let dx = Int32(-(cmd["dx"] as? Double ?? 0))
        tagAndPost(CGEvent(scrollWheelEvent2Source: nil, units: .pixel,
                           wheelCount: 2, wheel1: dy, wheel2: dx, wheel3: 0),
                   flags(cmd["mods"] as? [String] ?? []))
        return ["ok": true]

    case "key":
        guard let code = cmd["code"] as? String, let kc = KEYCODES[code] else {
            return ["ok": false, "error": "unmapped key \(cmd["code"] ?? "")"]
        }
        let down = (cmd["action"] as? String ?? "down") == "down"
        tagAndPost(CGEvent(keyboardEventSource: nil, virtualKey: kc, keyDown: down),
                   flags(cmd["mods"] as? [String] ?? []))
        return ["ok": true]

    case "text":
        let text = cmd["text"] as? String ?? ""
        for ch in text {
            let u = Array(String(ch).utf16)
            for isDown in [true, false] {
                guard let e = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: isDown) else { continue }
                e.keyboardSetUnicodeString(stringLength: u.count, unicodeString: u)
                e.setIntegerValueField(.eventSourceUserData, value: EVENT_TAG)
                e.post(tap: .cghidEventTap)
            }
        }
        return ["ok": true]

    default:
        return ["ok": false, "error": "unknown op \(op)"]
    }
}

// --- unix socket server (concurrent, timeout per client) --------------------

func serve() {
    unlink(socketPath)
    try? FileManager.default.createDirectory(
        atPath: (socketPath as NSString).deletingLastPathComponent, withIntermediateDirectories: true)

    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { FileHandle.standardError.write("socket() failed\n".data(using: .utf8)!); exit(1) }

    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let cap = MemoryLayout.size(ofValue: addr.sun_path) - 1
    _ = withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
        socketPath.withCString { strncpy(UnsafeMutableRawPointer(ptr).assumingMemoryBound(to: CChar.self), $0, cap) }
    }
    let size = socklen_t(MemoryLayout<sockaddr_un>.size)
    guard withUnsafePointer(to: &addr, { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, size) } }) == 0
    else { FileHandle.standardError.write("bind() failed\n".data(using: .utf8)!); exit(1) }

    chmod(socketPath, 0o600)
    listen(fd, 16)
    FileHandle.standardError.write("cptr-input (macos) on \(socketPath) trusted=\(AXIsProcessTrusted())\n".data(using: .utf8)!)

    let q = DispatchQueue(label: "cptr-input.clients", attributes: .concurrent)
    while true {
        let client = accept(fd, nil, nil)
        if client < 0 { continue }
        var tv = timeval(tv_sec: 30, tv_usec: 0)
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        q.async {
            let h = FileHandle(fileDescriptor: client, closeOnDealloc: true)
            var buf = Data()
            while true {
                let chunk = h.availableData
                if chunk.isEmpty { break }
                buf.append(chunk)
                while let nl = buf.firstIndex(of: 0x0A) {
                    let line = buf.subdata(in: buf.startIndex..<nl)
                    buf.removeSubrange(buf.startIndex...nl)
                    guard !line.isEmpty else { continue }
                    let reply: [String: Any] = (try? JSONSerialization.jsonObject(with: line) as? [String: Any])
                        .flatMap { $0 }.map(handle) ?? ["ok": false, "error": "bad json"]
                    if let out = try? JSONSerialization.data(withJSONObject: reply) { h.write(out + Data([0x0A])) }
                }
            }
        }
    }
}

serve()
