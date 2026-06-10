import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler


Record = Dict[str, Any]
Observation = Dict[str, Any]


def validate_record(record: Record) -> None:
    required_fields = [
        "timestamp_ms",
        "device_id",
        "source_type",
        "signal_vector",
        "confidence",
    ]

    for field in required_fields:
        if field not in record:
            raise ValueError(f"Missing required field: {field}")

    if not isinstance(record["timestamp_ms"], int):
        raise ValueError("timestamp_ms must be an integer")

    if not isinstance(record["device_id"], str):
        raise ValueError("device_id must be a string")

    if record["source_type"] not in ["uwb", "ble", "wifi"]:
        raise ValueError("source_type must be one of: uwb, ble, wifi")

    if not isinstance(record["signal_vector"], list):
        raise ValueError("signal_vector must be a list")

    for value in record["signal_vector"]:
        if not isinstance(value, (int, float)):
            raise ValueError("signal_vector must contain only numbers")

    confidence = record["confidence"]

    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number")

    if confidence < 0.0 or confidence > 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")


def load_records(input_path: str) -> List[Record]:
    with open(input_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("Input JSON must be a list of records")

    for record in data:
        validate_record(record)

    return data


def combine_group_into_observation(records_at_same_moment: List[Record]) -> Observation:
    device_id = records_at_same_moment[0]["device_id"]

    feature_values = {}
    feature_weights = {}
    confidences = []
    timestamps = []

    for record in records_at_same_moment:
        source_type = record["source_type"]
        signal_vector = record["signal_vector"]
        confidence = float(record["confidence"])
        timestamp_ms = int(record["timestamp_ms"])

        confidences.append(confidence)
        timestamps.append(timestamp_ms)

        for index, value in enumerate(signal_vector):
            feature_name = f"{source_type}_{index}"

            if feature_name not in feature_values:
                feature_values[feature_name] = 0.0
                feature_weights[feature_name] = 0.0

            feature_values[feature_name] += float(value) * confidence
            feature_weights[feature_name] += confidence

        feature_values[f"{source_type}_present"] = 1.0
        feature_weights[f"{source_type}_present"] = 1.0

        feature_values[f"{source_type}_confidence"] = confidence
        feature_weights[f"{source_type}_confidence"] = 1.0

    final_features = {}

    for feature_name in feature_values:
        if feature_weights[feature_name] == 0:
            final_features[feature_name] = 0.0
        else:
            final_features[feature_name] = (
                feature_values[feature_name] / feature_weights[feature_name]
            )

    representative_timestamp = round(sum(timestamps) / len(timestamps))

    return {
        "timestamp_ms": representative_timestamp,
        "device_id": device_id,
        "features": final_features,
        "input_confidence": sum(confidences) / len(confidences),
    }


def group_records_by_moment(
    records: List[Record],
    time_window_ms: int = 250,
) -> List[Observation]:
    records_by_device = defaultdict(list)

    for record in records:
        records_by_device[record["device_id"]].append(record)

    combined_observations = []

    for device_id, device_records in records_by_device.items():
        device_records.sort(key=lambda record: record["timestamp_ms"])

        current_group = []
        current_group_start_time = None
        current_group_sources = set()

        for record in device_records:
            timestamp_ms = record["timestamp_ms"]
            source_type = record["source_type"]

            if not current_group:
                current_group = [record]
                current_group_start_time = timestamp_ms
                current_group_sources = {source_type}
                continue

            close_in_time = (
                abs(timestamp_ms - current_group_start_time) <= time_window_ms
            )

            new_source_for_this_moment = source_type not in current_group_sources

            if close_in_time and new_source_for_this_moment:
                current_group.append(record)
                current_group_sources.add(source_type)
            else:
                combined_observations.append(
                    combine_group_into_observation(current_group)
                )

                current_group = [record]
                current_group_start_time = timestamp_ms
                current_group_sources = {source_type}

        if current_group:
            combined_observations.append(
                combine_group_into_observation(current_group)
            )

    combined_observations.sort(
        key=lambda observation: (
            observation["device_id"],
            observation["timestamp_ms"],
        )
    )

    return combined_observations


def build_feature_matrix(
    combined_observations: List[Observation],
) -> Tuple[np.ndarray, List[str]]:
    all_feature_names = set()

    for observation in combined_observations:
        all_feature_names.update(observation["features"].keys())

    feature_names = sorted(all_feature_names)

    rows = []

    for observation in combined_observations:
        row = []

        for feature_name in feature_names:
            row.append(observation["features"].get(feature_name, 0.0))

        rows.append(row)

    return np.array(rows, dtype=float), feature_names


def choose_number_of_zones(X_scaled: np.ndarray, max_zones: int) -> int:
    number_of_samples = len(X_scaled)

    if number_of_samples < 4:
        return 1

    best_k = 2
    best_score = -1.0

    highest_k_to_try = min(max_zones, number_of_samples - 1)

    for k in range(2, highest_k_to_try + 1):
        model = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = model.fit_predict(X_scaled)

        if len(set(labels)) < 2:
            continue

        score = silhouette_score(X_scaled, labels)

        if score > best_score:
            best_score = score
            best_k = k

    return best_k


def compute_zone_confidence(
    distances_to_centers: np.ndarray,
    input_confidence: float,
) -> float:
    if len(distances_to_centers) == 1:
        return round(input_confidence, 4)

    exp_values = np.exp(-distances_to_centers)
    probabilities = exp_values / np.sum(exp_values)

    model_confidence = float(np.max(probabilities))
    final_confidence = model_confidence * input_confidence

    final_confidence = max(0.0, min(1.0, final_confidence))

    return round(final_confidence, 4)


def discover_zones(
    records: List[Record],
    max_zones: int = 8,
    time_window_ms: int = 250,
) -> Tuple[List[Record], List[Record]]:
    combined_observations = group_records_by_moment(
    records,
    time_window_ms=time_window_ms,
)

    if len(combined_observations) == 0:
        return [], []

    X, feature_names = build_feature_matrix(combined_observations)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    number_of_zones = choose_number_of_zones(X_scaled, max_zones)

    model = KMeans(
        n_clusters=number_of_zones,
        random_state=42,
        n_init=10,
    )

    labels = model.fit_predict(X_scaled)
    centers_original_scale = scaler.inverse_transform(model.cluster_centers_)

    label_to_zone_id = {}

    for index, label in enumerate(sorted(set(labels))):
        label_to_zone_id[label] = f"zone_{index + 1}"

    assignments = []

    for observation, label, x_scaled in zip(
        combined_observations,
        labels,
        X_scaled,
    ):
        distances = np.linalg.norm(model.cluster_centers_ - x_scaled, axis=1)

        assignment = {
            "timestamp_ms": observation["timestamp_ms"],
            "device_id": observation["device_id"],
            "zone_id": label_to_zone_id[label],
            "zone_confidence": compute_zone_confidence(
                distances,
                observation["input_confidence"],
            ),
        }

        assignments.append(assignment)

    zone_definitions = []

    for label in sorted(set(labels)):
        zone_id = label_to_zone_id[label]
        zone_size = int(np.sum(labels == label))
        prototype_vector = centers_original_scale[label].tolist()

        zone_definition = {
            "zone_id": zone_id,
            "prototype_vector": [
                round(float(value), 6) for value in prototype_vector
            ],
            "zone_size": zone_size,
        }

        zone_definitions.append(zone_definition)

    return assignments, zone_definitions


def save_json(data: Any, output_path: str) -> None:
    folder = os.path.dirname(output_path)

    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Person 2: Discover zones from standardized wireless observations."
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to input JSON file from Person 1.",
    )

    parser.add_argument(
        "--assignments-output",
        default="outputs/assignments.json",
        help="Where to save zone assignment records.",
    )

    parser.add_argument(
        "--zones-output",
        default="outputs/zones.json",
        help="Where to save zone definition records.",
    )

    parser.add_argument(
        "--max-zones",
        type=int,
        default=8,
        help="Maximum number of zones to try.",
    )

    parser.add_argument(
    "--time-window-ms",
    type=int,
    default=250,
    help="Maximum time difference, in milliseconds, for combining different-source records from the same device.",
    )

    args = parser.parse_args()

    records = load_records(args.input)

    assignments, zone_definitions = discover_zones(
    records,
    max_zones=args.max_zones,
    time_window_ms=args.time_window_ms,
    )

    save_json(assignments, args.assignments_output)
    save_json(zone_definitions, args.zones_output)

    print(f"Saved {len(assignments)} assignment records to {args.assignments_output}")
    print(f"Saved {len(zone_definitions)} zone definitions to {args.zones_output}")


if __name__ == "__main__":
    main()