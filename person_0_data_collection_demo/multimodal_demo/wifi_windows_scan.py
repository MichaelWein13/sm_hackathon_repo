import argparse
import json
import re
import subprocess
from typing import Dict, List, Any


def quality_percent_to_rssi(signal_percent: int) -> int:
    return int((signal_percent / 2) - 100)


def run_netsh_scan() -> str:
    result = subprocess.run(
        ["netsh", "wlan", "show", "networks", "mode=bssid"],
        capture_output=True,
        text=True,
        errors="replace",
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return result.stdout


def parse_netsh_output(text: str) -> List[Dict[str, Any]]:
    access_points = []
    current_ssid = ""
    current_ap = None

    def finish_current_ap():
        nonlocal current_ap

        if current_ap is None:
            return

        if "signal_percent" not in current_ap:
            current_ap = None
            return

        if "channel" not in current_ap:
            current_ap["channel"] = "unknown"

        ssid = current_ap.get("ssid", "")
        bssid = current_ap.get("bssid", "")

        if bssid:
            current_ap["key"] = f"{ssid}@{bssid}"
        else:
            current_ap["key"] = f"{ssid}@ch{current_ap['channel']}"

        access_points.append(current_ap)
        current_ap = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        ssid_match = re.match(r"^SSID\s+\d+\s*:\s*(.*)$", line)
        if ssid_match:
            finish_current_ap()
            current_ssid = ssid_match.group(1).strip()
            continue

        bssid_match = re.match(r"^BSSID\s+\d+\s*:\s*(.*)$", line)
        if bssid_match:
            finish_current_ap()
            current_ap = {
                "ssid": current_ssid,
                "bssid": bssid_match.group(1).strip(),
            }
            continue

        if current_ap is not None:
            signal_match = re.match(r"^Signal\s*:\s*(\d+)%", line)
            if signal_match:
                signal_percent = int(signal_match.group(1))
                current_ap["signal_percent"] = signal_percent
                current_ap["rssi"] = quality_percent_to_rssi(signal_percent)
                continue

            channel_match = re.match(r"^Channel\s*:\s*(.*)$", line)
            if channel_match:
                current_ap["channel"] = channel_match.group(1).strip()
                continue

    finish_current_ap()
    access_points.sort(key=lambda ap: ap["rssi"], reverse=True)
    return access_points


def scan_wifi_access_points() -> List[Dict[str, Any]]:
    text = run_netsh_scan()
    return parse_netsh_output(text)

def scan_wifi_fingerprint():
    """
    Returns a dictionary like:
    {
        "eduroam@aa:bb:cc:dd:ee:ff": -62,
        "Technion-Guest@11:22:33:44:55:66": -71
    }
    """

    access_points = scan_wifi_access_points()

    fingerprint = {}

    for ap in access_points:
        fingerprint[ap["key"]] = ap["rssi"]

    return fingerprint

def scan_wifi_fingerprint() -> Dict[str, int]:
    access_points = scan_wifi_access_points()
    fingerprint = {}

    for ap in access_points:
        fingerprint[ap["key"]] = ap["rssi"]

    return fingerprint


def main() -> None:
    parser = argparse.ArgumentParser(description="Windows Wi-Fi scanner")
    parser.add_argument("--json", action="store_true", help="Print scan as JSON")
    args = parser.parse_args()

    access_points = scan_wifi_access_points()

    if args.json:
        print(json.dumps(access_points, indent=2))
        return

    print(f"Found {len(access_points)} access points")
    print()
    print(f"{'RSSI':>6}  {'SIGNAL':>7}  {'CH':>5}  {'SSID':<30}  BSSID")
    print("-" * 90)

    for ap in access_points:
        print(
            f"{ap['rssi']:>6}  "
            f"{str(ap['signal_percent']) + '%':>7}  "
            f"{ap['channel']:>5}  "
            f"{ap['ssid']:<30}  "
            f"{ap['bssid']}"
        )


if __name__ == "__main__":
    main()