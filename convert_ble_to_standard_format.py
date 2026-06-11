#!/usr/bin/env python3
"""
Convert BLE RSSI data to standardized JSON format.

Input format (MBD files):
<timestamp>,<MAC sensor>,<MAC beacon>,<RSSI>,<coord_x>,<coord_y>,<coord_z>,<3x3 orientation matrix>

Output format:
{
  "timestamp_ms": integer milliseconds since epoch (normalized to start at 0),
  "device_id": string (trajectory name),
  "source_type": "ble",
  "signal_vector": [RSSI values from all sensors in fixed order, null if missing]
}

Processing rules:
- Each trajectory becomes its own "device_id"
- Timestamps are normalized to start at 0 for each trajectory
- Signals within 500ms intervals are grouped together
- Signal vector has fixed positions for each sensor (null if sensor didn't report)
"""

import json
import os
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any


def parse_mbd_line(line: str) -> Dict[str, Any]:
    """Parse a single line from an MBD file."""
    parts = line.strip().split(',')
    if len(parts) < 4:
        return None
    
    return {
        'timestamp': float(parts[0]),
        'mac_sensor': parts[1],
        'mac_beacon': parts[2],
        'rssi': int(parts[3]),
    }


def get_all_sensors(filepath: str) -> List[str]:
    """Extract all unique sensor MAC addresses from a file."""
    sensors = set()
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parsed = parse_mbd_line(line)
            if parsed:
                sensors.add(parsed['mac_sensor'])
    
    # Return sorted list for consistent ordering
    return sorted(list(sensors))


def process_trajectory_file(filepath: str, device_id: str, sensor_order: List[str]) -> List[Dict[str, Any]]:
    """
    Process a single trajectory file and convert to standard format.
    
    Args:
        filepath: Path to the MBD file
        device_id: Name for this trajectory/device
        sensor_order: Fixed order of sensors for the signal vector
    
    Returns:
        List of standardized observation records
    """
    print(f"Processing {device_id}...")
    
    # Read all data points
    data_points = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parsed = parse_mbd_line(line)
            if parsed:
                data_points.append(parsed)
    
    if not data_points:
        return []
    
    # Sort by timestamp
    data_points.sort(key=lambda x: x['timestamp'])
    
    # Find earliest timestamp and normalize
    earliest_timestamp = data_points[0]['timestamp']
    
    # Group by 500ms intervals
    interval_ms = 500
    grouped_data = defaultdict(lambda: {'timestamp_ms': None, 'sensors': {}})
    
    for point in data_points:
        # Normalize timestamp to start at 0
        normalized_time = point['timestamp'] - earliest_timestamp
        timestamp_ms = int(normalized_time * 1000)
        
        # Determine which interval this belongs to
        interval_key = (timestamp_ms // interval_ms) * interval_ms
        
        # Use the first actual timestamp in the interval (not the interval boundary)
        if grouped_data[interval_key]['timestamp_ms'] is None:
            grouped_data[interval_key]['timestamp_ms'] = timestamp_ms
        
        # Store RSSI value for this sensor
        sensor = point['mac_sensor']
        # If multiple readings from same sensor in interval, keep the first one
        if sensor not in grouped_data[interval_key]['sensors']:
            grouped_data[interval_key]['sensors'][sensor] = point['rssi']
    
    # Convert to standard format
    output_records = []
    for interval_key in sorted(grouped_data.keys()):
        interval_data = grouped_data[interval_key]
        
        # Build signal vector in fixed sensor order
        signal_vector = []
        for sensor in sensor_order:
            rssi_value = interval_data['sensors'].get(sensor, None)
            signal_vector.append(rssi_value)
        
        record = {
            "timestamp_ms": interval_data['timestamp_ms'],
            "device_id": device_id,
            "source_type": "ble",
            "signal_vector": signal_vector
        }
        output_records.append(record)
    
    print(f"  → Generated {len(output_records)} records from {len(data_points)} data points")
    return output_records


def main():
    """Main conversion process."""
    # Define trajectory files in the trk directory
    trk_dir = Path("Position-Annotated-BLE-RSSI-Dataset/trk")
    
    trajectory_files = [
        ("rectangular_with_rotation_all_sensors.mbd", "rectangular_with_rotation"),
        ("rectangular_without_rotation_all_sensors.mbd", "rectangular_without_rotation"),
        ("straight_01_all_sensors.mbd", "straight_01"),
        ("straight_02_all_sensors.mbd", "straight_02"),
        ("straight_03_all_sensors.mbd", "straight_03"),
        ("straight_04_all_sensors.mbd", "straight_04"),
        ("straight_05_all_sensors.mbd", "straight_05"),
        ("zigzagging_with_rotation_all_sensors.mbd", "zigzagging_with_rotation"),
        ("zigzagging_without_rotation_all_sensors.mbd", "zigzagging_without_rotation"),
    ]
    
    # First pass: collect all unique sensors across all trajectories
    print("Collecting all sensors...")
    all_sensors = set()
    for filename, _ in trajectory_files:
        filepath = trk_dir / filename
        if filepath.exists():
            sensors = get_all_sensors(str(filepath))
            all_sensors.update(sensors)
    
    sensor_order = sorted(list(all_sensors))
    print(f"Found {len(sensor_order)} unique sensors: {sensor_order}")
    
    # Create output directory
    output_dir = Path("converted_data")
    output_dir.mkdir(exist_ok=True)
    
    # Process each trajectory
    all_records = []
    for filename, device_id in trajectory_files:
        filepath = trk_dir / filename
        if not filepath.exists():
            print(f"Warning: {filepath} not found, skipping...")
            continue
        
        records = process_trajectory_file(str(filepath), device_id, sensor_order)
        all_records.extend(records)
    
    # Sort all records by timestamp_ms
    all_records.sort(key=lambda x: x['timestamp_ms'])
    
    # Save single combined file
    output_file = output_dir / "converted_ble_data.json"
    with open(output_file, 'w') as f:
        json.dump(all_records, f, indent=2)
    print(f"\nSaved all data to {output_file}")
    print(f"Total records: {len(all_records)}")
    
    # Save sensor order reference
    metadata_file = output_dir / "metadata.json"
    metadata = {
        "sensor_order": sensor_order,
        "signal_vector_length": len(sensor_order),
        "source_type": "ble",
        "interval_ms": 500,
        "description": "BLE RSSI data converted to standard format. signal_vector indices correspond to sensor_order."
    }
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata to {metadata_file}")


if __name__ == "__main__":
    main()