"""
Person 0 Clean Wi-Fi Zone Detector v4

Purpose:
- Train on real Wi-Fi fingerprints in named zones, e.g. inside_library/outside_library.
- Build a stable fixed feature vector from Wi-Fi BSSIDs.
- Classify the current zone for demo/debugging.
- Write Person 2-compatible records exactly in this format:
  {
    "timestamp_ms": 1710000000000,
    "device_id": "person_1",
    "source_type": "wifi",
    "signal_vector": [-62.0, -71.0, -100.0]
  }

Dependencies: Python standard library only.
Platform: Windows, because it uses `netsh wlan show networks mode=bssid`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from statistics import median, pstdev
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ----------------------------- Paths ---------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
PERSON0_DIR = SCRIPT_PATH.parent
REPO_ROOT = PERSON0_DIR.parent

DATA_DIR = PERSON0_DIR / "data"
OUTPUTS_DIR = PERSON0_DIR / "outputs"

TRAINING_FILE = DATA_DIR / "training_samples.json"
MODEL_FILE = DATA_DIR / "wifi_model.json"
CLASSIFIER_LOG_FILE = OUTPUTS_DIR / "classifier_log.json"
LOCAL_PERSON2_OUTPUT_FILE = OUTPUTS_DIR / "person2_input.json"
DEFAULT_REPO_PERSON2_OUTPUT_FILE = (
    REPO_ROOT / "person_1_cleaning_data" / "converted_data" / "person2_input.json"
)

MISSING_RSSI = -100.0
SOURCE_TYPE = "wifi"


# ----------------------------- JSON helpers --------------------------------

def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default_value: Any) -> Any:
    if not path.exists():
        return default_value
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"WARNING: {path} was not valid JSON. Using default value.")
        return default_value


def save_json(path: Path, data: Any) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def append_json_list(path: Path, items: List[Dict[str, Any]]) -> None:
    existing = load_json(path, [])
    if not isinstance(existing, list):
        existing = []
    existing.extend(items)
    save_json(path, existing)


def now_ms() -> int:
    return int(time.time() * 1000)


# ----------------------------- Wi-Fi scanning -------------------------------

def quality_percent_to_rssi(signal_percent: int) -> int:
    """
    Windows netsh reports Wi-Fi quality as a percentage, not true RSSI.
    This common approximation maps 0..100% to about -100..-50 dBm.
    """
    return int((signal_percent / 2) - 100)


def run_netsh_scan() -> str:
    result = subprocess.run(
        ["netsh", "wlan", "show", "networks", "mode=bssid"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Wi-Fi scan failed. Make sure Wi-Fi is enabled and you are running on Windows.\n"
            + result.stderr
        )
    return result.stdout


def parse_netsh_output(text: str) -> List[Dict[str, Any]]:
    """
    Returns one dictionary per visible BSSID/access point.
    The most important stable feature is the BSSID, not just the SSID.
    """
    access_points: List[Dict[str, Any]] = []
    current_ssid = ""
    current_ap: Optional[Dict[str, Any]] = None

    def finish_current_ap() -> None:
        nonlocal current_ap
        if current_ap is None:
            return
        if "signal_percent" not in current_ap:
            current_ap = None
            return
        if "channel" not in current_ap:
            current_ap["channel"] = "unknown"

        bssid = str(current_ap.get("bssid", "")).lower().strip()
        ssid = str(current_ap.get("ssid", "")).strip()

        if bssid:
            key = bssid
        else:
            # Fallback only. Normally Windows gives a BSSID.
            key = f"{ssid}@ch{current_ap['channel']}".lower()

        current_ap["key"] = key
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
                "bssid": bssid_match.group(1).strip().lower(),
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


def scan_access_points() -> List[Dict[str, Any]]:
    return parse_netsh_output(run_netsh_scan())


def scan_fingerprint_once() -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    Returns:
      fingerprint: {bssid_or_key: rssi}
      names:       {bssid_or_key: ssid}
    """
    access_points = scan_access_points()
    fingerprint: Dict[str, float] = {}
    names: Dict[str, str] = {}

    for ap in access_points:
        key = str(ap["key"])
        fingerprint[key] = float(ap["rssi"])
        names[key] = str(ap.get("ssid", ""))

    return fingerprint, names


def aggregate_scans(
    scans_per_sample: int,
    scan_delay: float,
    min_visible_aps: int = 10,
    max_scan_attempts: int = 0,
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    A single Wi-Fi scan is noisy. This combines several scans into one stable sample.

    Important safety fix in v4:
    Sometimes Windows/netsh returns a broken scan with only 0-1 APs. Those samples
    should never be classified or written to Person 2, because almost every missing
    feature becomes -100 and the distance calculation becomes meaningless.
    """
    if scans_per_sample <= 0:
        scans_per_sample = 1
    if min_visible_aps < 0:
        min_visible_aps = 0
    if max_scan_attempts <= 0:
        max_scan_attempts = max(scans_per_sample * 4, scans_per_sample)

    values_by_key: Dict[str, List[float]] = defaultdict(list)
    names: Dict[str, str] = {}
    valid_scans = 0
    attempts = 0
    rejected_counts: List[int] = []

    while valid_scans < scans_per_sample and attempts < max_scan_attempts:
        attempts += 1
        fingerprint, scan_names = scan_fingerprint_once()
        visible_count = len(fingerprint)

        if visible_count < min_visible_aps:
            rejected_counts.append(visible_count)
            print(
                f"  ignored low-quality Wi-Fi scan {attempts}/{max_scan_attempts}: "
                f"only {visible_count} APs visible (< {min_visible_aps})"
            )
            time.sleep(scan_delay)
            continue

        valid_scans += 1
        for key, rssi in fingerprint.items():
            values_by_key[key].append(rssi)
        names.update(scan_names)

        if valid_scans < scans_per_sample:
            time.sleep(scan_delay)

    if valid_scans == 0:
        raise RuntimeError(
            "Could not get a valid Wi-Fi sample. Windows returned too few APs every time. "
            "Run `python person_0_clean_wifi\\person0_wifi.py scan` and check that it sees many APs. "
            "You can temporarily lower --min-visible-aps, but do not trust samples with only 1 AP."
        )

    if valid_scans < scans_per_sample:
        print(
            f"  WARNING: only {valid_scans}/{scans_per_sample} valid scans were collected "
            f"after {attempts} attempts."
        )

    aggregated = {
        key: float(median(values))
        for key, values in values_by_key.items()
    }
    return aggregated, names


# ----------------------------- Model training -------------------------------

def unique_zones(samples: List[Dict[str, Any]]) -> List[str]:
    return sorted({str(sample["zone"]) for sample in samples})


def vectorize(fingerprint: Dict[str, float], feature_keys: List[str]) -> List[float]:
    return [float(fingerprint.get(key, MISSING_RSSI)) for key in feature_keys]


def mean(values: Iterable[float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return sum(values_list) / len(values_list)


def radio_group_key(key: str) -> str:
    """
    Many enterprise APs broadcast several SSIDs from the same physical radio.
    Their BSSIDs often differ only in the last hex digit, e.g. ...:4d:20,
    ...:4d:21, ...:4d:2a. If we let all of them into the vector, one
    physical AP can dominate the distance calculation. This helper lets the
    model cap how many features come from one radio group.
    """
    parts = key.lower().split(":")
    if len(parts) == 6 and all(parts):
        return ":".join(parts[:5]) + ":" + parts[5][0] + "*"
    return key.lower()


def feature_statistics(
    key: str,
    samples: List[Dict[str, Any]],
    zones: List[str],
) -> Dict[str, Any]:
    """
    Score a BSSID by how useful it is for telling zones apart.

    Important change from the first version:
    - We do NOT mainly reward strongest/most common APs.
    - We reward APs whose mean RSSI or visibility is different between zones.
    - We penalize unstable/noisy APs.
    """
    zone_means: Dict[str, float] = {}
    zone_seen_fractions: Dict[str, float] = {}
    zone_stds: Dict[str, float] = {}

    for zone in zones:
        zone_samples = [sample for sample in samples if sample["zone"] == zone]
        values = []
        seen_count = 0

        for sample in zone_samples:
            fp = sample.get("fingerprint", {})
            if key in fp:
                seen_count += 1
            values.append(float(fp.get(key, MISSING_RSSI)))

        zone_means[zone] = mean(values)
        zone_seen_fractions[zone] = seen_count / max(1, len(zone_samples))
        zone_stds[zone] = pstdev(values) if len(values) > 1 else 0.0

    means = list(zone_means.values())
    seen_fractions = list(zone_seen_fractions.values())
    stds = list(zone_stds.values())

    rssi_separation = max(means) - min(means) if means else 0.0
    visibility_separation = max(seen_fractions) - min(seen_fractions) if seen_fractions else 0.0
    instability = mean(stds) if stds else 0.0
    max_seen_fraction = max(seen_fractions) if seen_fractions else 0.0

    # Main idea: a feature is good if it changes between zones.
    # Weak APs that are about -90 in both places get a low score.
    # APs that appear strongly in one zone but disappear in another get a high score.
    score = (rssi_separation / (1.0 + 0.15 * instability))
    score += 18.0 * visibility_separation
    score += 2.0 * max_seen_fraction

    return {
        "key": key,
        "radio_group": radio_group_key(key),
        "score": float(score),
        "rssi_separation": float(rssi_separation),
        "visibility_separation": float(visibility_separation),
        "instability": float(instability),
        "zone_means": {zone: round(value, 2) for zone, value in zone_means.items()},
        "zone_seen_fractions": {zone: round(value, 3) for zone, value in zone_seen_fractions.items()},
    }


def score_feature_key(
    key: str,
    samples: List[Dict[str, Any]],
    zones: List[str],
) -> float:
    return float(feature_statistics(key, samples, zones)["score"])


def build_model(top_n: int, min_samples_per_zone: int, max_per_radio_group: int) -> Dict[str, Any]:
    samples = load_json(TRAINING_FILE, [])
    if not isinstance(samples, list) or not samples:
        raise RuntimeError("No training samples found. Run `train` first.")

    zones = unique_zones(samples)
    if len(zones) < 2:
        raise RuntimeError("Train at least two zones, for example inside_library and outside_library.")

    counts = Counter(sample["zone"] for sample in samples)
    weak_zones = [zone for zone in zones if counts[zone] < min_samples_per_zone]
    if weak_zones:
        raise RuntimeError(
            "Not enough samples for these zones: "
            + ", ".join(f"{zone}={counts[zone]}" for zone in weak_zones)
            + f". Need at least {min_samples_per_zone} each."
        )

    all_keys = sorted({key for sample in samples for key in sample.get("fingerprint", {}).keys()})
    stats_by_key = {key: feature_statistics(key, samples, zones) for key in all_keys}
    ranked_keys = sorted(
        all_keys,
        key=lambda key: stats_by_key[key]["score"],
        reverse=True,
    )

    # Do not let one enterprise AP/radio group dominate the vector just because
    # it broadcasts many SSIDs. This was the main failure mode in the first
    # version on the Technion Wi-Fi environment.
    feature_keys: List[str] = []
    group_counts: Counter[str] = Counter()
    for key in ranked_keys:
        group = radio_group_key(key)
        if max_per_radio_group > 0 and group_counts[group] >= max_per_radio_group:
            continue
        feature_keys.append(key)
        group_counts[group] += 1
        if len(feature_keys) >= top_n:
            break

    # If the cap was too strict for this building, fill the remainder.
    if len(feature_keys) < top_n:
        for key in ranked_keys:
            if key not in feature_keys:
                feature_keys.append(key)
            if len(feature_keys) >= top_n:
                break

    if not feature_keys:
        raise RuntimeError("No Wi-Fi features found. Are Wi-Fi scans returning access points?")

    centroids: Dict[str, List[float]] = {}
    for zone in zones:
        zone_vectors = [
            vectorize(sample["fingerprint"], feature_keys)
            for sample in samples
            if sample["zone"] == zone
        ]
        centroid = []
        for i in range(len(feature_keys)):
            centroid.append(mean(vector[i] for vector in zone_vectors))
        centroids[zone] = centroid

    key_names: Dict[str, str] = {}
    for sample in samples:
        key_names.update(sample.get("ap_names", {}))

    raw_scores = [max(0.1, float(stats_by_key[key]["score"])) for key in feature_keys]
    average_score = mean(raw_scores) if raw_scores else 1.0
    feature_weights = [
        max(0.25, min(4.0, score / max(0.1, average_score)))
        for score in raw_scores
    ]

    training_vectors = [
        {
            "zone": str(sample["zone"]),
            "vector": vectorize(sample["fingerprint"], feature_keys),
        }
        for sample in samples
    ]

    feature_debug = []
    for key in feature_keys:
        debug_item = dict(stats_by_key[key])
        debug_item["ssid"] = key_names.get(key, "")
        feature_debug.append(debug_item)

    model = {
        "version": 3,
        "created_timestamp_ms": now_ms(),
        "source_type": SOURCE_TYPE,
        "missing_rssi": MISSING_RSSI,
        "feature_keys": feature_keys,
        "feature_names": [key_names.get(key, "") for key in feature_keys],
        "feature_weights": feature_weights,
        "feature_debug": feature_debug,
        "max_per_radio_group": max_per_radio_group,
        "zones": zones,
        "zone_counts": dict(counts),
        "centroids": centroids,
        "training_vectors": training_vectors,
        "classifier": "weighted_knn",
        "knn_k": 5,
        "notes": "Vectors are ordered by feature_keys. Each value is approximated RSSI from Windows netsh quality percent. v3 adds feature-level debugging and distance-gap confidence.",
    }
    save_json(MODEL_FILE, model)
    return model


# ----------------------------- Classification -------------------------------

def weighted_rmse_distance(
    a: List[float],
    b: List[float],
    weights: Optional[List[float]] = None,
) -> float:
    if len(a) != len(b):
        raise ValueError("Vectors must have the same length.")
    if not a:
        return float("inf")

    if not weights or len(weights) != len(a):
        weights = [1.0] * len(a)

    weight_sum = sum(weights)
    if weight_sum <= 0:
        weight_sum = float(len(a))
        weights = [1.0] * len(a)

    return math.sqrt(
        sum(w * ((x - y) ** 2) for x, y, w in zip(a, b, weights)) / weight_sum
    )


def classify_vector(signal_vector: List[float], model: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify using weighted K-nearest-neighbors over the real training samples.

    This is more robust than comparing only to one average centroid per zone.
    The centroid distances are still not wrong mathematically, but they can be
    misleading when Wi-Fi scans are noisy or when one enterprise AP dominates
    the selected features.
    """
    weights = model.get("feature_weights", [])
    training_vectors = model.get("training_vectors", [])

    # Fallback for old models.
    if not training_vectors:
        distances = []
        for zone, centroid in model["centroids"].items():
            distances.append({
                "zone": zone,
                "distance": weighted_rmse_distance(signal_vector, centroid, weights),
            })
        distances.sort(key=lambda item: item["distance"])
        best = distances[0]
        second = distances[1] if len(distances) > 1 else None
        confidence = 1.0 if second is None or second["distance"] <= 0 else 1.0 - (best["distance"] / second["distance"])
        confidence = max(0.0, min(1.0, confidence))
        return {
            "predicted_zone": best["zone"],
            "zone_confidence": round(confidence, 4),
            "best_distance": round(float(best["distance"]), 4),
            "second_zone": None if second is None else second["zone"],
            "second_distance": None if second is None else round(float(second["distance"]), 4),
            "all_distances": [
                {"zone": item["zone"], "distance": round(float(item["distance"]), 4)}
                for item in distances
            ],
        }

    neighbors = []
    for item in training_vectors:
        distance = weighted_rmse_distance(signal_vector, item["vector"], weights)
        neighbors.append({"zone": item["zone"], "distance": distance})

    neighbors.sort(key=lambda item: item["distance"])
    k = min(int(model.get("knn_k", 5)), len(neighbors))
    nearest = neighbors[:k]

    votes: Dict[str, float] = defaultdict(float)
    nearest_distances_by_zone: Dict[str, List[float]] = defaultdict(list)

    for item in neighbors:
        nearest_distances_by_zone[item["zone"]].append(float(item["distance"]))

    for item in nearest:
        # Inverse-distance vote. 0.5 prevents one near-perfect sample from
        # numerically overwhelming every other sample.
        votes[item["zone"]] += 1.0 / (item["distance"] + 0.5)

    vote_items = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    best_zone, best_vote = vote_items[0]
    second_zone = vote_items[1][0] if len(vote_items) > 1 else None
    second_vote = vote_items[1][1] if len(vote_items) > 1 else 0.0

    confidence = (best_vote - second_vote) / max(best_vote, 1e-9)
    confidence = max(0.0, min(1.0, confidence))

    # Report one distance per zone: the median of that zone's 3 nearest
    # training samples. This is much easier to interpret than a centroid only.
    zone_distance_items = []
    for zone in model.get("zones", sorted(nearest_distances_by_zone.keys())):
        ds = sorted(nearest_distances_by_zone.get(zone, []))
        if not ds:
            zone_distance = float("inf")
        else:
            zone_distance = median(ds[: min(3, len(ds))])
        zone_distance_items.append({"zone": zone, "distance": zone_distance})

    zone_distance_items.sort(key=lambda item: item["distance"])
    best_distance_item = zone_distance_items[0]
    second_distance_item = zone_distance_items[1] if len(zone_distance_items) > 1 else None

    return {
        "predicted_zone": best_zone,
        "zone_confidence": round(confidence, 4),
        "best_distance": round(float(best_distance_item["distance"]), 4),
        "second_zone": None if second_distance_item is None else second_distance_item["zone"],
        "second_distance": None if second_distance_item is None else round(float(second_distance_item["distance"]), 4),
        "all_distances": [
            {"zone": item["zone"], "distance": round(float(item["distance"]), 4)}
            for item in zone_distance_items
        ],
    }


def distance_gap_confidence(all_distances: List[Dict[str, Any]]) -> float:
    """
    Confidence based on how separated the best zone distance is from the second-best.
    This prevents misleading confidence=1.0 when KNN votes all agree but the distances are close.
    """
    if len(all_distances) < 2:
        return 1.0
    best = float(all_distances[0]["distance"])
    second = float(all_distances[1]["distance"])
    if not math.isfinite(best) or not math.isfinite(second) or second <= 0:
        return 0.0
    return max(0.0, min(1.0, (second - best) / second))


def add_distance_confidence(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Attach a more honest confidence value based on the distance gap."""
    all_distances = classification.get("all_distances", [])
    gap_conf = distance_gap_confidence(all_distances)
    old_conf = float(classification.get("zone_confidence", 0.0))
    classification["knn_confidence"] = round(old_conf, 4)
    classification["distance_gap_confidence"] = round(gap_conf, 4)
    # Be conservative: if the distances are close, do not print a fake 1.0.
    classification["zone_confidence"] = round(min(old_conf, gap_conf), 4)
    if gap_conf < 0.10:
        classification["decision_quality"] = "weak / close distances"
    elif gap_conf < 0.20:
        classification["decision_quality"] = "moderate"
    else:
        classification["decision_quality"] = "strong"
    return classification


def feature_debug_rows(signal_vector: List[float], model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Build per-feature debug rows so we can see exactly why a scan favors inside/outside.
    For a two-zone model, each row shows current RSSI and which zone mean it is closer to.
    """
    feature_keys = model.get("feature_keys", [])
    feature_names = model.get("feature_names", [])
    feature_debug = model.get("feature_debug", [])
    zones = model.get("zones", [])
    rows: List[Dict[str, Any]] = []

    for i, key in enumerate(feature_keys):
        current = float(signal_vector[i]) if i < len(signal_vector) else MISSING_RSSI
        ssid = str(feature_names[i]) if i < len(feature_names) else ""
        meta = feature_debug[i] if i < len(feature_debug) else {}
        means = meta.get("zone_means", {})
        errors = {}
        for zone in zones:
            zone_mean = float(means.get(zone, MISSING_RSSI))
            errors[zone] = abs(current - zone_mean)

        if errors:
            sorted_errors = sorted(errors.items(), key=lambda item: item[1])
            closest_zone = sorted_errors[0][0]
            closest_error = sorted_errors[0][1]
            second_error = sorted_errors[1][1] if len(sorted_errors) > 1 else closest_error
            evidence_margin = second_error - closest_error
        else:
            closest_zone = "unknown"
            evidence_margin = 0.0

        rows.append({
            "index": i + 1,
            "key": key,
            "ssid": ssid,
            "current": current,
            "seen": current > MISSING_RSSI + 0.1,
            "closest_zone": closest_zone,
            "evidence_margin": evidence_margin,
            "means": means,
            "errors": errors,
            "score": float(meta.get("score", 0.0)),
        })

    rows.sort(key=lambda row: row["evidence_margin"], reverse=True)
    return rows


def print_feature_debug(signal_vector: List[float], model: Dict[str, Any], max_rows: int) -> None:
    rows = feature_debug_rows(signal_vector, model)
    zones = model.get("zones", [])
    print()
    print("Feature-level evidence:")
    if len(zones) == 2:
        z0, z1 = zones[0], zones[1]
        print(f"{'#':>2} {'BSSID':<17} {'SSID':<14} {'now':>6} {'seen':>5} {z0[:12]:>12} {z1[:12]:>12} {'favors':>16} {'margin':>8}")
        print("-" * 100)
        for row in rows[:max_rows]:
            means = row["means"]
            print(
                f"{row['index']:>2} "
                f"{row['key']:<17} "
                f"{str(row['ssid'])[:14]:<14} "
                f"{row['current']:>6.1f} "
                f"{str(row['seen']):>5} "
                f"{float(means.get(z0, MISSING_RSSI)):>12.1f} "
                f"{float(means.get(z1, MISSING_RSSI)):>12.1f} "
                f"{str(row['closest_zone'])[:16]:>16} "
                f"{row['evidence_margin']:>8.1f}"
            )
    else:
        for row in rows[:max_rows]:
            print(json.dumps(row, indent=2))



def majority_vote(predictions: List[str]) -> str:
    counts = Counter(predictions)
    return counts.most_common(1)[0][0]


# ----------------------------- Commands -------------------------------------

def command_scan(args: argparse.Namespace) -> None:
    access_points = scan_access_points()
    print(f"Found {len(access_points)} Wi-Fi access points")
    print()
    print(f"{'RSSI':>6} {'SIGNAL':>7} {'CH':>5} {'SSID':<32} BSSID")
    print("-" * 100)
    for ap in access_points:
        print(
            f"{ap['rssi']:>6} "
            f"{str(ap['signal_percent']) + '%':>7} "
            f"{str(ap['channel']):>5} "
            f"{str(ap['ssid'])[:32]:<32} "
            f"{ap['bssid']}"
        )


def command_clear(args: argparse.Namespace) -> None:
    save_json(TRAINING_FILE, [])
    save_json(MODEL_FILE, {})
    save_json(CLASSIFIER_LOG_FILE, [])
    save_json(LOCAL_PERSON2_OUTPUT_FILE, [])
    if args.also_person2_output:
        save_json(Path(args.person2_output), [])

    print("Cleared Person 0 clean Wi-Fi data.")
    print(f"Training file: {TRAINING_FILE}")
    print(f"Model file:    {MODEL_FILE}")
    print(f"Local output:  {LOCAL_PERSON2_OUTPUT_FILE}")
    if args.also_person2_output:
        print(f"Person 2 output also cleared: {args.person2_output}")


def command_train(args: argparse.Namespace) -> None:
    samples = load_json(TRAINING_FILE, [])
    if not isinstance(samples, list):
        samples = []

    print()
    print(f"Training zone: {args.zone}")
    print(f"Samples: {args.samples}")
    print(f"Scans per sample: {args.scans_per_sample}")
    print("Stay in this exact physical area while training runs.")
    print()

    for i in range(args.samples):
        print(f"Sample {i + 1}/{args.samples}: scanning...")
        fingerprint, ap_names = aggregate_scans(
            args.scans_per_sample,
            args.scan_delay,
            args.min_visible_aps,
            args.max_scan_attempts,
        )
        sample = {
            "timestamp_ms": now_ms(),
            "zone": args.zone,
            "fingerprint": fingerprint,
            "ap_names": ap_names,
            "visible_ap_count": len(fingerprint),
        }
        samples.append(sample)
        save_json(TRAINING_FILE, samples)
        print(f"  visible APs: {len(fingerprint)}")

        if i != args.samples - 1:
            time.sleep(args.sample_delay)

    print()
    print(f"Saved training data to: {TRAINING_FILE}")
    print("Now train other zones, then run `build-model`.")


def command_build_model(args: argparse.Namespace) -> None:
    model = build_model(top_n=args.top_n, min_samples_per_zone=args.min_samples_per_zone, max_per_radio_group=args.max_per_radio_group)
    print()
    print("Built Wi-Fi zone model.")
    print(f"Model file: {MODEL_FILE}")
    print(f"Zones: {', '.join(model['zones'])}")
    print(f"Signal vector length: {len(model['feature_keys'])}")
    print()
    print("Top Wi-Fi features used:")
    print("      BSSID              SSID             score   seen fractions   zone means")
    for i, item in enumerate(model.get("feature_debug", []), start=1):
        key = item["key"]
        ssid = str(item.get("ssid", ""))[:14]
        score = float(item.get("score", 0.0))
        means = item.get("zone_means", {})
        seen = item.get("zone_seen_fractions", {})
        means_text = ", ".join(f"{zone}:{value}" for zone, value in means.items())
        seen_text = ", ".join(f"{zone}:{value}" for zone, value in seen.items())
        print(f"  {i:02d}. {key:<17} {ssid:<14} {score:>6.2f}   {seen_text:<34} {means_text}")


def load_model_required() -> Dict[str, Any]:
    model = load_json(MODEL_FILE, {})
    if not isinstance(model, dict) or not model.get("feature_keys"):
        raise RuntimeError("No model found. Run `build-model` first.")
    return model


def make_person2_record(timestamp_ms: int, device_id: str, signal_vector: List[float]) -> Dict[str, Any]:
    return {
        "timestamp_ms": int(timestamp_ms),
        "device_id": str(device_id),
        "source_type": SOURCE_TYPE,
        "signal_vector": [float(value) for value in signal_vector],
    }


def command_collect(args: argparse.Namespace) -> None:
    model = load_model_required()
    feature_keys = model["feature_keys"]
    person2_output = Path(args.person2_output)

    print()
    print("Collecting Person 0 Wi-Fi observations...")
    print(f"Device ID: {args.device_id}")
    print(f"Person 2 output: {person2_output}")
    print(f"Local copy: {LOCAL_PERSON2_OUTPUT_FILE}")
    print()

    recent_predictions: deque[str] = deque(maxlen=args.smoothing_window)
    accepted_records = 0
    attempted_records = 0
    max_record_attempts = args.max_record_attempts
    if max_record_attempts <= 0:
        max_record_attempts = max(args.count * 4, args.count)

    while accepted_records < args.count and attempted_records < max_record_attempts:
        attempted_records += 1
        timestamp = now_ms()
        fingerprint, ap_names = aggregate_scans(
            args.scans_per_sample,
            args.scan_delay,
            args.min_visible_aps,
            args.max_scan_attempts,
        )
        signal_vector = vectorize(fingerprint, feature_keys)
        classification = add_distance_confidence(classify_vector(signal_vector, model))

        recent_predictions.append(classification["predicted_zone"])
        smoothed_zone = majority_vote(list(recent_predictions))

        gap_confidence = float(classification.get("distance_gap_confidence") or 0.0)
        should_write = True
        skip_reasons: List[str] = []

        if args.expected_zone and classification["predicted_zone"] != args.expected_zone:
            should_write = False
            skip_reasons.append(
                f"expected {args.expected_zone}, got {classification['predicted_zone']}"
            )

        if gap_confidence < args.min_gap_confidence:
            should_write = False
            skip_reasons.append(
                f"gap confidence {gap_confidence:.4f} < {args.min_gap_confidence}"
            )

        print("=" * 72)
        print(f"Attempt {attempted_records}/{max_record_attempts}; accepted {accepted_records}/{args.count}")
        print(f"Predicted zone: {classification['predicted_zone']}")
        print(f"Smoothed zone:  {smoothed_zone}")
        print(f"Confidence:     {classification['zone_confidence']}  ({classification.get('decision_quality', 'unknown')})")
        print(f"KNN confidence: {classification.get('knn_confidence')}")
        print(f"Gap confidence: {classification.get('distance_gap_confidence')}")
        print(f"Vector length:  {len(signal_vector)}")
        print(f"Visible APs:    {len(fingerprint)}")
        print("Distances:")
        for item in classification["all_distances"]:
            print(f"  {item['zone']}: {item['distance']}")
        if args.debug_features:
            print_feature_debug(signal_vector, model, args.debug_rows)

        person2_record = make_person2_record(timestamp, args.device_id, signal_vector)
        log_record = {
            "timestamp_ms": timestamp,
            "device_id": args.device_id,
            "predicted_zone_raw": classification["predicted_zone"],
            "predicted_zone_smoothed": smoothed_zone,
            "zone_confidence": classification["zone_confidence"],
            "knn_confidence": classification.get("knn_confidence"),
            "distance_gap_confidence": classification.get("distance_gap_confidence"),
            "decision_quality": classification.get("decision_quality"),
            "best_distance": classification["best_distance"],
            "second_zone": classification["second_zone"],
            "second_distance": classification["second_distance"],
            "visible_ap_count": len(fingerprint),
            "written_to_person2": should_write,
            "skip_reasons": skip_reasons,
            "person2_record": person2_record,
        }
        append_json_list(CLASSIFIER_LOG_FILE, [log_record])

        if should_write:
            append_json_list(LOCAL_PERSON2_OUTPUT_FILE, [person2_record])
            append_json_list(person2_output, [person2_record])
            accepted_records += 1
            print(f"Accepted record {accepted_records}/{args.count}.")
            print(f"Wrote Person 2 record to: {person2_output}")
        else:
            print("Skipped this record; did NOT write to Person 2.")
            for reason in skip_reasons:
                print(f"  - {reason}")

        if accepted_records < args.count:
            time.sleep(args.delay)

    print()
    if accepted_records < args.count:
        print(f"WARNING: only accepted {accepted_records}/{args.count} records.")
        print("Try running again, increasing --max-record-attempts, or checking Wi-Fi with the scan command.")
    else:
        print("Done. Person 2-compatible JSON is ready.")


def command_status(args: argparse.Namespace) -> None:
    samples = load_json(TRAINING_FILE, [])
    model = load_json(MODEL_FILE, {})
    counts = Counter(sample.get("zone", "unknown") for sample in samples if isinstance(sample, dict))

    print()
    print("Person 0 Clean Wi-Fi Status")
    print(f"Folder: {PERSON0_DIR}")
    print(f"Training samples: {len(samples)}")
    for zone, count in counts.items():
        print(f"  {zone}: {count}")

    if model.get("feature_keys"):
        print(f"Model zones: {', '.join(model.get('zones', []))}")
        print(f"Signal vector length: {len(model['feature_keys'])}")
    else:
        print("Model: not built yet")

    person2_records = load_json(Path(args.person2_output), [])
    if isinstance(person2_records, list):
        print(f"Person 2 records: {len(person2_records)}")
    print()


def command_show_last(args: argparse.Namespace) -> None:
    records = load_json(Path(args.person2_output), [])
    if not records:
        print("No Person 2 records yet.")
        return
    print(json.dumps(records[-1], indent=2))


# ----------------------------- CLI ------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean Person 0 Wi-Fi fingerprint collector and Person 2 output writer."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="Show currently visible Wi-Fi access points.")

    clear = sub.add_parser("clear", help="Clear training/model/output files.")
    clear.add_argument("--also-person2-output", action="store_true")
    clear.add_argument("--person2-output", default=str(DEFAULT_REPO_PERSON2_OUTPUT_FILE))

    train = sub.add_parser("train", help="Record labeled Wi-Fi samples in a known zone.")
    train.add_argument("--zone", required=True, help="Example: inside_library or outside_library")
    train.add_argument("--samples", type=int, default=10)
    train.add_argument("--scans-per-sample", type=int, default=3)
    train.add_argument("--scan-delay", type=float, default=0.8)
    train.add_argument("--sample-delay", type=float, default=2.0)
    train.add_argument("--min-visible-aps", type=int, default=10, help="Ignore broken netsh scans with fewer visible APs.")
    train.add_argument("--max-scan-attempts", type=int, default=0, help="0 means scans-per-sample * 4.")

    build = sub.add_parser("build-model", help="Build the zone classifier from training samples.")
    build.add_argument("--top-n", type=int, default=16, help="Number of Wi-Fi features in signal_vector.")
    build.add_argument("--min-samples-per-zone", type=int, default=5)
    build.add_argument(
        "--max-per-radio-group",
        type=int,
        default=1,
        help="Limit correlated BSSIDs from the same enterprise AP/radio group. Use 0 to disable.",
    )

    collect = sub.add_parser("collect", help="Collect Person 2-compatible Wi-Fi records.")
    collect.add_argument("--device-id", default="person_2")
    collect.add_argument("--count", type=int, default=1)
    collect.add_argument("--delay", type=float, default=2.0)
    collect.add_argument("--scans-per-sample", type=int, default=3)
    collect.add_argument("--scan-delay", type=float, default=0.8)
    collect.add_argument("--min-visible-aps", type=int, default=10, help="Ignore broken netsh scans with fewer visible APs.")
    collect.add_argument("--max-scan-attempts", type=int, default=0, help="0 means scans-per-sample * 4.")
    collect.add_argument("--expected-zone", default="", help="Optional safety gate. If set, skip records whose debug classification does not match this zone.")
    collect.add_argument("--min-gap-confidence", type=float, default=0.0, help="Optional safety gate. Skip records with lower gap confidence.")
    collect.add_argument("--max-record-attempts", type=int, default=0, help="0 means count * 4.")
    collect.add_argument("--smoothing-window", type=int, default=3)
    collect.add_argument("--debug-features", action="store_true", help="Print per-BSSID evidence for the current prediction.")
    collect.add_argument("--debug-rows", type=int, default=16, help="Number of feature debug rows to print.")
    collect.add_argument("--person2-output", default=str(DEFAULT_REPO_PERSON2_OUTPUT_FILE))

    status = sub.add_parser("status", help="Show training/model/output status.")
    status.add_argument("--person2-output", default=str(DEFAULT_REPO_PERSON2_OUTPUT_FILE))

    show_last = sub.add_parser("show-last", help="Print the last Person 2-compatible record.")
    show_last.add_argument("--person2-output", default=str(DEFAULT_REPO_PERSON2_OUTPUT_FILE))

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "scan":
            command_scan(args)
        elif args.command == "clear":
            command_clear(args)
        elif args.command == "train":
            command_train(args)
        elif args.command == "build-model":
            command_build_model(args)
        elif args.command == "collect":
            command_collect(args)
        elif args.command == "status":
            command_status(args)
        elif args.command == "show-last":
            command_show_last(args)
        else:
            parser.error(f"Unknown command: {args.command}")
    except RuntimeError as exc:
        print()
        print(f"ERROR: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
