import argparse
import json
import os
import time
from collections import Counter
from typing import Dict, List, Any

from wifi_windows_scan import scan_wifi_fingerprint


OUTPUT_DIR = "multimodal_outputs"

WIFI_KEYS_FILE = os.path.join(OUTPUT_DIR, "wifi_keys.json")
ESP_LATEST_FILE = os.path.join(OUTPUT_DIR, "esp_latest.json")
PERSON1_RECORDS_FILE = os.path.join(OUTPUT_DIR, "person1_records.json")

FLOOR_RSSI = -100


def now_ms() -> int:
    return int(time.time() * 1000)


def load_json_file(path: str, default_value: Any) -> Any:
    if not os.path.exists(path):
        return default_value

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json_file(path: str, data: Any) -> None:
    folder = os.path.dirname(path)

    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def learn_wifi_keys(samples: int, top_n: int, delay_seconds: float) -> None:
    print("Learning Wi-Fi feature keys...")
    print(f"Taking {samples} scans.")
    print()

    key_counter = Counter()
    key_strength_sum = Counter()

    for i in range(samples):
        print(f"Wi-Fi learning scan {i + 1}/{samples}")

        fingerprint = scan_wifi_fingerprint()

        for key, rssi in fingerprint.items():
            key_counter[key] += 1
            key_strength_sum[key] += rssi

        if i != samples - 1:
            time.sleep(delay_seconds)

    ranked_keys = []

    for key, count in key_counter.items():
        avg_rssi = key_strength_sum[key] / count
        ranked_keys.append((key, count, avg_rssi))

    ranked_keys.sort(key=lambda item: (item[1], item[2]), reverse=True)

    selected_keys = [key for key, count, avg_rssi in ranked_keys[:top_n]]

    save_json_file(WIFI_KEYS_FILE, selected_keys)

    print()
    print(f"Saved {len(selected_keys)} Wi-Fi feature keys to {WIFI_KEYS_FILE}")
    print()

    for index, key in enumerate(selected_keys):
        print(f"{index}: {key}")


def load_wifi_keys() -> List[str]:
    keys = load_json_file(WIFI_KEYS_FILE, [])

    if not keys:
        raise RuntimeError(
            "No Wi-Fi keys found. Run this first:\n"
            "python multimodal_demo\\collect_multimodal_records.py learn-wifi-keys"
        )

    return keys


def wifi_fingerprint_to_vector(
    fingerprint: Dict[str, int],
    wifi_keys: List[str],
) -> List[float]:
    vector = []

    for key in wifi_keys:
        vector.append(float(fingerprint.get(key, FLOOR_RSSI)))

    return vector


def wifi_confidence(vector: List[float]) -> float:
    if not vector:
        return 0.0

    seen_count = sum(1 for value in vector if value > FLOOR_RSSI)

    return round(seen_count / len(vector), 4)


def load_latest_esp_readings() -> Dict[str, Dict[str, Any]]:
    return load_json_file(ESP_LATEST_FILE, {})


def build_ble_vector_from_esp(
    device_id: str,
    anchor_ids: List[str],
    max_age_ms: int,
) -> tuple[List[float], float]:
    latest = load_latest_esp_readings()
    timestamp = now_ms()

    vector = []
    confidences = []

    for anchor_id in anchor_ids:
        key = f"{device_id}|{anchor_id}"
        reading = latest.get(key)

        if reading is None:
            vector.append(float(FLOOR_RSSI))
            confidences.append(0.0)
            continue

        age_ms = timestamp - int(reading["server_timestamp_ms"])

        if age_ms > max_age_ms:
            vector.append(float(FLOOR_RSSI))
            confidences.append(0.0)
            continue

        vector.append(float(reading["rssi"]))
        confidences.append(float(reading["confidence"]))

    if not confidences:
        confidence = 0.0
    else:
        confidence = sum(confidences) / len(confidences)

    return vector, round(confidence, 4)


def append_person1_records(new_records: List[Dict[str, Any]]) -> None:
    existing_records = load_json_file(PERSON1_RECORDS_FILE, [])
    existing_records.extend(new_records)
    save_json_file(PERSON1_RECORDS_FILE, existing_records)


def collect_once(
    device_id: str,
    anchor_ids: List[str],
    max_esp_age_ms: int,
) -> None:
    timestamp_ms = now_ms()

    wifi_keys = load_wifi_keys()
    wifi_fingerprint = scan_wifi_fingerprint()
    wifi_vector = wifi_fingerprint_to_vector(wifi_fingerprint, wifi_keys)

    ble_vector, ble_conf = build_ble_vector_from_esp(
        device_id=device_id,
        anchor_ids=anchor_ids,
        max_age_ms=max_esp_age_ms,
    )

    wifi_record = {
        "timestamp_ms": timestamp_ms,
        "device_id": device_id,
        "source_type": "wifi",
        "signal_vector": wifi_vector,
        "confidence": wifi_confidence(wifi_vector),
    }

    ble_record = {
        "timestamp_ms": timestamp_ms,
        "device_id": device_id,
        "source_type": "ble",
        "signal_vector": ble_vector,
        "confidence": ble_conf,
    }

    append_person1_records([wifi_record, ble_record])

    print()
    print(f"Collected timestamp_ms={timestamp_ms}")
    print(f"Wi-Fi vector length: {len(wifi_vector)}, confidence={wifi_record['confidence']}")
    print(f"BLE vector: {ble_vector}, confidence={ble_conf}")
    print(f"Appended records to {PERSON1_RECORDS_FILE}")


def clear_records() -> None:
    save_json_file(PERSON1_RECORDS_FILE, [])
    print(f"Cleared {PERSON1_RECORDS_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect Wi-Fi + ESP/BLE records into Person 1 standardized JSON."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    learn_parser = subparsers.add_parser("learn-wifi-keys")
    learn_parser.add_argument("--samples", type=int, default=5)
    learn_parser.add_argument("--top-n", type=int, default=12)
    learn_parser.add_argument("--delay", type=float, default=3.0)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--device-id", default="person_1")
    collect_parser.add_argument("--count", type=int, default=10)
    collect_parser.add_argument("--interval", type=float, default=3.0)
    collect_parser.add_argument(
        "--anchors",
        default="esp_outside,esp_inside,esp_bathroom",
        help="Comma-separated ESP anchor IDs in fixed vector order.",
    )
    collect_parser.add_argument("--max-esp-age-ms", type=int, default=10000)

    subparsers.add_parser("clear-records")

    args = parser.parse_args()

    if args.command == "learn-wifi-keys":
        learn_wifi_keys(
            samples=args.samples,
            top_n=args.top_n,
            delay_seconds=args.delay,
        )

    elif args.command == "collect":
        anchor_ids = [anchor.strip() for anchor in args.anchors.split(",")]

        for i in range(args.count):
            print(f"\nCollection {i + 1}/{args.count}")

            collect_once(
                device_id=args.device_id,
                anchor_ids=anchor_ids,
                max_esp_age_ms=args.max_esp_age_ms,
            )

            if i != args.count - 1:
                time.sleep(args.interval)

    elif args.command == "clear-records":
        clear_records()


if __name__ == "__main__":
    main()