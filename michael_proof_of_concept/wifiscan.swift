// wifiscan.swift — minimal CoreWLAN scanner to confirm WiFi RSSI works on macOS.
// Build:  swiftc wifiscan.swift -o wifiscan -framework CoreWLAN -framework CoreLocation -framework Foundation
// Run:    ./wifiscan
//
// On macOS 14.4+, the old `airport -s` is gone; CoreWLAN is the supported path.
// Scanning needs Location Services granted to your TERMINAL app, otherwise SSIDs
// come back empty (you'll still see RSSI/channel). See notes at the bottom.

import CoreWLAN
import CoreLocation
import Foundation

// --- Ask for Location permission (needed for SSIDs on recent macOS) ---
final class LocGate: NSObject, CLLocationManagerDelegate {
    let mgr = CLLocationManager()
    var settled = false
    func ask() {
        mgr.delegate = self
        mgr.requestWhenInUseAuthorization()
    }
    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        settled = true
    }
}

let gate = LocGate()
gate.ask()
// Pump the run loop briefly so the auth callback can fire (CLI tools need this).
let deadline = Date().addingTimeInterval(8)
while !gate.settled && Date() < deadline {
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.2))
}

// --- Scan ---
let client = CWWiFiClient.shared()
guard let iface = client.interface() else {
    FileHandle.standardError.write("No Wi-Fi interface found.\n".data(using: .utf8)!)
    exit(1)
}

let asJSON = CommandLine.arguments.contains("--json")

do {
    let networks = try iface.scanForNetworks(withSSID: nil)
    let sorted = networks.sorted { $0.rssiValue > $1.rssiValue }

    if asJSON {
        var arr: [[String: Any]] = []
        for n in sorted {
            arr.append([
                "ssid": n.ssid ?? "",
                "rssi": n.rssiValue,
                "ch": n.wlanChannel?.channelNumber ?? 0
            ])
        }
        let data = try JSONSerialization.data(withJSONObject: arr, options: [])
        print(String(data: data, encoding: .utf8) ?? "[]")
    } else {
        func pad(_ s: String, _ w: Int) -> String {
            s.count >= w ? s : s + String(repeating: " ", count: w - s.count)
        }
        print("Found \(sorted.count) networks\n")
        print(pad("RSSI", 7) + pad("CH", 5) + "SSID")
        print(String(repeating: "-", count: 40))
        for n in sorted {
            let ssid = (n.ssid?.isEmpty == false) ? n.ssid! : "<hidden/blocked>"
            let ch   = n.wlanChannel?.channelNumber ?? 0
            print(pad("\(n.rssiValue)", 7) + pad("\(ch)", 5) + ssid)
        }
    }
} catch {
    FileHandle.standardError.write("Scan failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}

// --- Notes ---
// 1. If SSID shows "<hidden/blocked>" for everything but RSSI/CH look real,
//    the SCAN itself works — you just lack Location permission. Fix:
//    System Settings > Privacy & Security > Location Services > ON, and enable
//    it for your terminal (Terminal.app / iTerm). Re-run.
// 2. BSSID (the AP's MAC) is redacted on recent macOS, so for fingerprinting
//    key each AP on (SSID + channel) instead of BSSID.
// 3. To feed a classifier later, swap the print loop for JSON:
//    each network -> {"ssid": ..., "rssi": ..., "ch": ...}
