import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

from wifi_windows_scan import scan_wifi_fingerprint


OUTPUT_DIR = "multimodal_outputs"

FINGERPRINTS_FILE = os.path.join(OUTPUT_DIR, "wifi_demo_fingerprints.json")
RAW_PERSON1_INPUT_FILE = os.path.join(OUTPUT_DIR, "wifi_raw_person1_input.json")
PERSON1_RECORDS_FILE = os.path.join(OUTPUT_DIR, "person1_wifi_records.json")
PREDICTIONS_FILE = os.path.join(OUTPUT_DIR, "wifi_zone_predictions.json")
WIFI_KEYS_FILE = os.path.join(OUTPUT_DIR, "wifi_demo_keys.json")

FLOOR_RSSI = -100


def now_ms() -> int:
    return int(time.time() * 1000)


def load_json(path: str, default_value: Any) -> Any:
    if not os.path.exists(path):
        return default_value

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def append_json_list(path: str, new_items: List[Dict[str, Any]]) -> None:
    existing_items = load_json(path, [])

    existing_items.extend(new_items)

    save_json(path, existing_items)


def fingerprint_distance(fp1: Dict[str, int], fp2: Dict[str, float]) -> float:
    all_keys = set(fp1.keys()) | set(fp2.keys())

    if not all_keys:
        return float("inf")

    squared_sum = 0.0

    for key in all_keys:
        rssi1 = fp1.get(key, FLOOR_RSSI)
        rssi2 = fp2.get(key, FLOOR_RSSI)

        difference = rssi1 - rssi2
        squared_sum += difference * difference

    return math.sqrt(squared_sum / len(all_keys))


def build_centroids(samples: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    zone_to_fingerprints = defaultdict(list)

    for sample in samples:
        zone_to_fingerprints[sample["zone"]].append(sample["fingerprint"])

    centroids = {}

    for zone, fingerprints in zone_to_fingerprints.items():
        all_keys = set()

        for fp in fingerprints:
            all_keys.update(fp.keys())

        centroid = {}

        for key in all_keys:
            values = []

            for fp in fingerprints:
                values.append(fp.get(key, FLOOR_RSSI))

            centroid[key] = sum(values) / len(values)

        centroids[zone] = centroid

    return centroids


def vector_distance(v1: List[float], v2: List[float]) -> float:
    if len(v1) != len(v2):
        raise ValueError("Vectors must have the same length")

    if len(v1) == 0:
        return float("inf")

    squared_sum = 0.0

    for a, b in zip(v1, v2):
        difference = a - b
        squared_sum += difference * difference

    return math.sqrt(squared_sum / len(v1))


def predict_zone(
    fingerprint: Dict[str, int],
    samples: List[Dict[str, Any]],
    wifi_keys: List[str],
) -> Dict[str, Any]:
    """
    Predict zone using the same fixed Wi-Fi feature vector
    that we output to Person 1.

    This is more stable than comparing raw dictionaries directly.
    """

    current_vector = fingerprint_to_signal_vector(fingerprint, wifi_keys)

    zone_to_vectors = defaultdict(list)

    for sample in samples:
        zone = sample["zone"]
        sample_vector = fingerprint_to_signal_vector(sample["fingerprint"], wifi_keys)
        zone_to_vectors[zone].append(sample_vector)

    zone_centroids = {}

    for zone, vectors in zone_to_vectors.items():
        centroid = []

        for i in range(len(wifi_keys)):
            values_at_index = [vector[i] for vector in vectors]
            centroid.append(sum(values_at_index) / len(values_at_index))

        zone_centroids[zone] = centroid

    distances = []

    for zone, centroid in zone_centroids.items():
        distance = vector_distance(current_vector, centroid)
        distances.append((zone, distance))

    distances.sort(key=lambda item: item[1])

    best_zone, best_distance = distances[0]

    if len(distances) == 1:
        confidence = 1.0
        second_zone = None
        second_distance = None
    else:
        second_zone, second_distance = distances[1]

        if second_distance <= 0:
            confidence = 1.0
        else:
            confidence = 1.0 - (best_distance / second_distance)

        confidence = max(0.0, min(1.0, confidence))

    return {
        "zone": best_zone,
        "confidence": round(confidence, 4),
        "best_distance": round(best_distance, 4),
        "second_zone": second_zone,
        "second_distance": None if second_distance is None else round(second_distance, 4),
        "all_distances": [
            {
                "zone": zone,
                "distance": round(distance, 4),
            }
            for zone, distance in distances
        ],
    }

def select_wifi_keys(samples: List[Dict[str, Any]], top_n: int) -> List[str]:
    key_counter = Counter()
    key_strength_sum = Counter()

    for sample in samples:
        fingerprint = sample["fingerprint"]

        for key, rssi in fingerprint.items():
            key_counter[key] += 1
            key_strength_sum[key] += rssi

    ranked_keys = []

    for key, count in key_counter.items():
        average_rssi = key_strength_sum[key] / count

        ranked_keys.append(
            {
                "key": key,
                "count": count,
                "average_rssi": average_rssi,
            }
        )

    ranked_keys.sort(
        key=lambda item: (item["count"], item["average_rssi"]),
        reverse=True,
    )

    return [item["key"] for item in ranked_keys[:top_n]]


def fingerprint_to_signal_vector(
    fingerprint: Dict[str, int],
    wifi_keys: List[str],
) -> List[float]:
    return [float(fingerprint.get(key, FLOOR_RSSI)) for key in wifi_keys]


def vector_confidence(signal_vector: List[float]) -> float:
    if not signal_vector:
        return 0.0

    seen_count = sum(1 for value in signal_vector if value > FLOOR_RSSI)

    return round(seen_count / len(signal_vector), 4)


def record_zone(zone: str, count: int, delay: float) -> None:
    samples = load_json(FINGERPRINTS_FILE, [])

    print()
    print(f"Recording {count} Wi-Fi samples for zone: {zone}")
    print("Stay in that zone. Move slightly between scans.")
    print()

    for i in range(count):
        print(f"Sample {i + 1}/{count}: scanning Wi-Fi...")

        fingerprint = scan_wifi_fingerprint()

        sample = {
            "zone": zone,
            "timestamp_ms": now_ms(),
            "fingerprint": fingerprint,
        }

        samples.append(sample)

        print(f"Recorded {len(fingerprint)} access points.")

        if i != count - 1:
            print(f"Waiting {delay} seconds...")
            time.sleep(delay)

    save_json(FINGERPRINTS_FILE, samples)

    wifi_keys = select_wifi_keys(samples, top_n=12)
    save_json(WIFI_KEYS_FILE, wifi_keys)

    print()
    print(f"Saved samples to {FINGERPRINTS_FILE}")
    print(f"Saved Wi-Fi feature keys to {WIFI_KEYS_FILE}")
    print()


def list_zones() -> None:
    samples = load_json(FINGERPRINTS_FILE, [])

    if not samples:
        print("No samples recorded yet.")
        return

    counts = Counter(sample["zone"] for sample in samples)

    print()
    print("Recorded samples:")
    for zone, count in counts.items():
        print(f"  {zone}: {count}")
    print()


def clear_demo() -> None:
    save_json(FINGERPRINTS_FILE, [])
    save_json(RAW_PERSON1_INPUT_FILE, [])
    save_json(PERSON1_RECORDS_FILE, [])
    save_json(PREDICTIONS_FILE, [])
    save_json(WIFI_KEYS_FILE, [])

    print("Cleared Wi-Fi demo files.")


def require_training_data() -> Tuple[List[Dict[str, Any]], List[str]]:
    samples = load_json(FINGERPRINTS_FILE, [])
    wifi_keys = load_json(WIFI_KEYS_FILE, [])

    if not samples:
        raise RuntimeError("No training samples yet. Record zones first.")

    zones = set(sample["zone"] for sample in samples)

    if len(zones) < 2:
        raise RuntimeError("You need samples from at least 2 zones before prediction.")

    if not wifi_keys:
        wifi_keys = select_wifi_keys(samples, top_n=12)
        save_json(WIFI_KEYS_FILE, wifi_keys)

    return samples, wifi_keys


def collect_prediction(device_id: str) -> None:
    samples, wifi_keys = require_training_data()

    timestamp_ms = now_ms()

    fingerprint = scan_wifi_fingerprint()

    prediction = predict_zone(fingerprint, samples, wifi_keys)

    signal_vector = fingerprint_to_signal_vector(fingerprint, wifi_keys)
    confidence = vector_confidence(signal_vector)

    raw_person1_input = {
        "timestamp_ms": timestamp_ms,
        "device_id": device_id,
        "raw_source_type": "windows_wifi_netsh",
        "wifi_fingerprint": fingerprint,
    }

    standardized_person1_record = {
        "timestamp_ms": timestamp_ms,
        "device_id": device_id,
        "source_type": "wifi",
        "signal_vector": signal_vector,
        "confidence": confidence,
    }

    demo_prediction_record = {
        "timestamp_ms": timestamp_ms,
        "device_id": device_id,
        "predicted_zone": prediction["zone"],
        "zone_confidence": prediction["confidence"],
        "best_distance": prediction["best_distance"],
        "second_zone": prediction["second_zone"],
        "second_distance": prediction["second_distance"],
    }

    append_json_list(RAW_PERSON1_INPUT_FILE, [raw_person1_input])
    append_json_list(PERSON1_RECORDS_FILE, [standardized_person1_record])
    append_json_list(PREDICTIONS_FILE, [demo_prediction_record])

    print()
    print(f"Timestamp: {timestamp_ms}")
    print(f"Saw {len(fingerprint)} Wi-Fi access points.")
    print(f"Predicted zone: {prediction['zone']}")
    print(f"Zone confidence: {prediction['confidence']}")
    print(f"Person 1 standardized signal_vector length: {len(signal_vector)}")
    print(f"Person 1 standardized confidence: {confidence}")

    if prediction["second_zone"] is not None:
        print(f"Runner-up: {prediction['second_zone']}")
        print()
        print("Distances to trained zones:")
        for item in prediction["all_distances"]:
            print(f"  {item['zone']}: {item['distance']}")


    

    print()
    print(f"Saved raw Person 1 input to: {RAW_PERSON1_INPUT_FILE}")
    print(f"Saved standardized Person 1 record to: {PERSON1_RECORDS_FILE}")
    print(f"Saved demo prediction to: {PREDICTIONS_FILE}")


def live_demo(device_id: str, count: int, delay: float) -> None:
    print()
    print("Starting live Wi-Fi zone demo.")
    print("Walk between outside_library, inside_library, and bathroom.")
    print()

    for i in range(count):
        print("=" * 70)
        print(f"Live prediction {i + 1}/{count}")
        collect_prediction(device_id)

        if i != count - 1:
            print(f"Waiting {delay} seconds...")
            time.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wi-Fi-only library/bathroom zone demo."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--zone", required=True)
    record_parser.add_argument("--count", type=int, default=8)
    record_parser.add_argument("--delay", type=float, default=4.0)

    predict_parser = subparsers.add_parser("predict")
    predict_parser.add_argument("--device-id", default="person_1")

    live_parser = subparsers.add_parser("live")
    live_parser.add_argument("--device-id", default="person_1")
    live_parser.add_argument("--count", type=int, default=20)
    live_parser.add_argument("--delay", type=float, default=4.0)

    subparsers.add_parser("list")
    subparsers.add_parser("clear")

    args = parser.parse_args()

    if args.command == "record":
        record_zone(
            zone=args.zone,
            count=args.count,
            delay=args.delay,
        )

    elif args.command == "predict":
        collect_prediction(device_id=args.device_id)

    elif args.command == "live":
        live_demo(
            device_id=args.device_id,
            count=args.count,
            delay=args.delay,
        )

    elif args.command == "list":
        list_zones()

    elif args.command == "clear":
        clear_demo()


if __name__ == "__main__":
    main()