import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple
from fastapi import FastAPI, BackgroundTasks
import uvicorn

import numpy as np
import hdbscan
from sklearn.preprocessing import StandardScaler


Record = Dict[str, Any]
Observation = Dict[str, Any]


def validate_record(record: Record) -> None:
    required_fields = [
        "timestamp_ms",
        "device_id",
        "source_type",
        "signal_vector",
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
        if value is not None and not isinstance(value, (int, float)):
            raise ValueError("signal_vector must contain only numbers or null")




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

    feature_sums = {}
    feature_counts = {}
    timestamps = []

    for record in records_at_same_moment:
        source_type = record["source_type"]
        signal_vector = record["signal_vector"]
        timestamp_ms = int(record["timestamp_ms"])

        timestamps.append(timestamp_ms)

        for index, value in enumerate(signal_vector):
            if value is None:
                continue  # Skip null values
                
            feature_name = f"{source_type}_{index}"

            if feature_name not in feature_sums:
                feature_sums[feature_name] = 0.0
                feature_counts[feature_name] = 0

            feature_sums[feature_name] += float(value)
            feature_counts[feature_name] += 1

        feature_sums[f"{source_type}_present"] = 1.0
        feature_counts[f"{source_type}_present"] = 1

    final_features = {}

    for feature_name in feature_sums:
        final_features[feature_name] = (
            feature_sums[feature_name] / feature_counts[feature_name]
        )

    representative_timestamp = round(sum(timestamps) / len(timestamps))

    return {
        "timestamp_ms": representative_timestamp,
        "device_id": device_id,
        "features": final_features,
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


def compute_zone_confidence(probabilities: np.ndarray) -> float:
    """
    Compute confidence based on HDBSCAN membership probabilities.
    Higher probability = higher confidence that the point belongs to its assigned cluster.
    """
    if len(probabilities) == 0:
        return 0.0
    
    # HDBSCAN gives us the probability of membership in the assigned cluster
    # This is already a good confidence metric
    max_prob = float(np.max(probabilities))
    
    return round(max(0.0, min(1.0, max_prob)), 4)


def discover_zones(
    records: List[Record],
    min_cluster_size: int = 5,
    time_window_ms: int = 250,
) -> Tuple[List[Record], List[Record]]:
    """
    Discover zones using HDBSCAN density-based clustering.
    
    Args:
        records: List of observation records
        min_cluster_size: Minimum number of points to form a dense cluster
        time_window_ms: Time window for grouping multi-source observations
    
    Returns:
        Tuple of (assignments, zone_definitions)
    """
    combined_observations = group_records_by_moment(
        records,
        time_window_ms=time_window_ms,
    )

    if len(combined_observations) == 0:
        return [], []

    X, feature_names = build_feature_matrix(combined_observations)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # HDBSCAN: discovers number of clusters automatically
    # min_cluster_size is the key hyperparameter
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,  # More sensitive to local density variations
        cluster_selection_epsilon=0.0,
        metric='euclidean',
        cluster_selection_method='eom',  # Excess of Mass - good for varying density
    )

    labels = clusterer.fit_predict(X_scaled)
    probabilities = clusterer.probabilities_

    # Separate noise points (label -1) from valid clusters
    unique_labels = sorted(set(labels))
    
    # Check if we have noise points
    has_noise = -1 in unique_labels
    if has_noise:
        unique_labels = [l for l in unique_labels if l != -1]
    
    # If no clusters found, treat everything as one zone
    if len(unique_labels) == 0:
        labels = np.zeros(len(labels), dtype=int)
        unique_labels = [0]
        probabilities = np.ones(len(labels))

    # Create zone ID mapping (skip noise points for zone IDs)
    label_to_zone_id = {}
    for index, label in enumerate(unique_labels):
        label_to_zone_id[label] = f"zone_{index + 1}"
    
    # Noise points get special treatment
    if has_noise:
        label_to_zone_id[-1] = "transition"  # Noise = corridor/transition area

    # Compute cluster centers for valid zones
    cluster_centers = {}
    for label in unique_labels:
        mask = labels == label
        cluster_centers[label] = np.mean(X_scaled[mask], axis=0)

    assignments = []

    for observation, label, prob in zip(
        combined_observations,
        labels,
        probabilities,
    ):
        zone_id = label_to_zone_id[label]
        
        # For noise points, confidence is based on how "noisy" they are
        # Lower probability = more likely to be in transition/corridor
        if label == -1:
            # Invert probability for noise - low prob = high confidence it's a transition
            zone_confidence = round(1.0 - float(prob), 4)
        else:
            zone_confidence = round(float(prob), 4)

        assignment = {
            "timestamp_ms": observation["timestamp_ms"],
            "device_id": observation["device_id"],
            "zone_id": zone_id,
            "zone_confidence": zone_confidence,
        }

        assignments.append(assignment)

    # Create zone definitions
    zone_definitions = []

    for label in unique_labels:
        zone_id = label_to_zone_id[label]
        zone_size = int(np.sum(labels == label))
        
        # Get prototype vector (cluster center in original scale)
        center_scaled = cluster_centers[label]
        prototype_vector = scaler.inverse_transform(center_scaled.reshape(1, -1))[0].tolist()

        zone_definition = {
            "zone_id": zone_id,
            "prototype_vector": [
                round(float(value), 6) for value in prototype_vector
            ],
            "zone_size": zone_size,
        }

        zone_definitions.append(zone_definition)
    
    # Add transition/noise zone definition if it exists
    if has_noise:
        noise_size = int(np.sum(labels == -1))
        zone_definitions.append({
            "zone_id": "transition",
            "prototype_vector": [],  # No centroid for noise
            "zone_size": noise_size,
        })

    return assignments, zone_definitions


def save_json(data: Any, output_path: str) -> None:
    folder = os.path.dirname(output_path)

    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Person 2: Discover zones from standardized wireless observations using HDBSCAN."
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to input JSON file from Person 1.",
    )

    parser.add_argument(
        "--assignments-output",
        default="person2_zone_discovery/outputs/assignments.json",
        help="Where to save zone assignment records.",
    )

    parser.add_argument(
        "--zones-output",
        default="person2_zone_discovery/outputs/zones.json",
        help="Where to save zone definition records.",
    )

    parser.add_argument(
        "--min-cluster-size",
        type=int,
        default=5,
        help="Minimum number of points to form a dense cluster (HDBSCAN parameter).",
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
        min_cluster_size=args.min_cluster_size,
        time_window_ms=args.time_window_ms,
    )

    save_json(assignments, args.assignments_output)
    save_json(zone_definitions, args.zones_output)

    print(f"Saved {len(assignments)} assignment records to {args.assignments_output}")
    print(f"Saved {len(zone_definitions)} zone definitions to {args.zones_output}")
    
    # Print summary
    noise_count = sum(1 for a in assignments if a["zone_id"] == "transition")
    zone_count = len([z for z in zone_definitions if z["zone_id"] != "transition"])
    
    print(f"\nDiscovered {zone_count} zones")
    if noise_count > 0:
        print(f"Identified {noise_count} transition/corridor points")

    # --- NEW API PUSH TO PERSON 3 ---
    # If Person 2 is on a different laptop, change localhost to Person 3's Wi-Fi IP address!
    person_3_url = "http://localhost:8001/receive_assignments"

    print(f"\nSending assignments to Person 3 at {person_3_url}...")
    try:
        response = requests.post(person_3_url, json=assignments)
        if response.status_code == 200:
            print("Delivery successful! Person 3 received the data.")
        else:
            print(f"Delivery failed! Person 3 returned code: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("ERROR: Could not connect to Person 3. Is their server running on port 8001?")


if __name__ == "__main__":
    main()