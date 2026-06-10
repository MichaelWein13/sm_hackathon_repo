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


def group_records_by_moment(records: List[Record]) -> List[Observation]:
    grouped = defaultdict(list)

    for record in records:
        key = (record["device_id"], record["timestamp_ms"])
        grouped[key].append(record)

    combined_observations = []

    for (device_id, timestamp_ms), records_at_same_moment in grouped.items():
        feature_values = {}
        confidences = []

        for record in records_at_same_moment:
            source_type = record["source_type"]
            signal_vector = record["signal_vector"]
            confidence = float(record["confidence"])

            confidences.append(confidence)

            for index, value in enumerate(signal_vector):
                feature_name = f"{source_type}_{index}"
                feature_values[feature_name] = float(value)

            feature_values[f"{source_type}_present"] = 1.0
            feature_values[f"{source_type}_confidence"] = confidence

        combined_observations.append(
            {
                "timestamp_ms": timestamp_ms,
                "device_id": device_id,
                "features": feature_values,
                "input_confidence": sum(confidences) / len(confidences),
            }
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
) -> Tuple[List[Record], List[Record]]:
    combined_observations = group_records_by_moment(records)

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

    args = parser.parse_args()

    records = load_records(args.input)

    assignments, zone_definitions = discover_zones(
        records,
        max_zones=args.max_zones,
    )

    save_json(assignments, args.assignments_output)
    save_json(zone_definitions, args.zones_output)

    print(f"Saved {len(assignments)} assignment records to {args.assignments_output}")
    print(f"Saved {len(zone_definitions)} zone definitions to {args.zones_output}")


if __name__ == "__main__":
    main()