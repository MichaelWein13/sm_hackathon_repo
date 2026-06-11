import json
import os
import sys

# 1. Establish the root folder of the repository so Python can find everything
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(ROOT_DIR)

# 2. Import from Person 2 and Person 3 (Yourself)
from person2_zone_discovery.zone_discovery import load_records, discover_zones
from main import MovementGraphBuilder


def run_person_4_anomaly_detection(movement_graph: dict) -> dict:
    """
    Person 4's logic: Scans the final movement graph for emergencies and bottlenecks.
    """
    anomalies = []

    for edge in movement_graph.get("edges", []):
        if edge["transition_probability"] < 0.03 and edge["transition_count"] >= 2:
            anomalies.append({
                "type": "unusual_movement",
                "severity": "medium",
                "description": f"Rare transition detected from {edge['from_zone_id']} to {edge['to_zone_id']}",
                "data": edge
            })

    zone_stats = movement_graph.get("zone_stats", {})
    for zone_id, stats in zone_stats.items():
        if stats["visit_count"] > 100 and stats["avg_dwell_ms"] > 600:
            anomalies.append({
                "type": "severe_bottleneck",
                "severity": "high",
                "description": f"Severe congestion forming in {zone_id}! High visits and dwell times.",
                "data": stats
            })

    return {
        "pipeline_status": "complete",
        "total_anomalies_found": len(anomalies),
        "alerts": anomalies
    }


def run_person_5_dashboard_compiler(movement_graph: dict, anomaly_report: dict) -> dict:
    """
    Person 5 (Shay)'s logic: Merges Person 3's map and Person 4's alerts into
    one final payload for the frontend UI.
    """
    # Create the ultimate master payload
    dashboard_payload = {
        "metadata": {
            "status": "Live",
            "total_zones": len(movement_graph.get("zone_stats", {})),
            "active_alerts": anomaly_report.get("total_anomalies_found", 0)
        },
        # Inject Person 3's data so Shay can draw the map
        "network_geometry": movement_graph,

        # Inject Person 4's data so Shay can draw the warning labels
        "security_alerts": anomaly_report.get("alerts", [])
    }

    return dashboard_payload


def run_full_offline_pipeline(input_path: str, output_path: str):
    print(f"Reading cleaned BLE data from: {input_path}...")

    # --- STEP 1: PERSON 2 (Zone Discovery) ---
    print("\n[1/4] Running Person 2's Zone Discovery...")
    try:
        records = load_records(input_path)
        assignments, zone_definitions = discover_zones(records, min_cluster_size=5, time_window_ms=250)
        print(f" -> Success: Discovered {len(zone_definitions)} distinct zones.")
    except Exception as e:
        print(f" -> ERROR in Person 2's code: {e}")
        return

    # --- STEP 2: PERSON 3 (Your Movement Graph) ---
    print("\n[2/4] Running Person 3's Movement Graph Builder...")
    try:
        builder = MovementGraphBuilder()
        final_graph = builder.build_graph(assignments)
        print(f" -> Success: Generated graph with {len(final_graph.get('edges', []))} specific routes.")
    except Exception as e:
        print(f" -> ERROR in Person 3's code: {e}")
        return

    # --- STEP 3: PERSON 4 (Anomaly Detection) ---
    print("\n[3/4] Running Person 4's Anomaly Detection Engine...")
    try:
        final_report = run_person_4_anomaly_detection(final_graph)
        print(f" -> Success: Found {final_report['total_anomalies_found']} potential emergencies.")
    except Exception as e:
        print(f" -> ERROR in Person 4's code: {e}")
        return

    # --- STEP 4: PERSON 5 (Shay's Dashboard Aggregator) ---
    print("\n[4/4] Running Person 5's Dashboard Compiler...")
    try:
        # Notice we pass BOTH the graph (from Step 2) and the report (from Step 3)!
        shay_final_output = run_person_5_dashboard_compiler(final_graph, final_report)
        print(" -> Success: Final UI payload compiled.")
    except Exception as e:
        print(f" -> ERROR in Person 5's code: {e}")
        return

    # --- FINAL OUTPUT ---
    with open(output_path, 'w') as out_file:
        json.dump(shay_final_output, out_file, indent=2)
    print(f"\n✅ Pipeline Complete! Shay's final dashboard file saved to: {output_path}")


if __name__ == "__main__":
    # Dynamically point to Person 1's converted BLE data
    RAW_INPUT_FILE = os.path.join(
        ROOT_DIR,
        "person_one_cleaning_data",
        "converted_data",
        "converted_ble_data.json"
    )

    # Save the absolute final UI output
    FINAL_OUTPUT_FILE = os.path.join(
        os.path.dirname(__file__),
        "person5_final_dashboard_data.json"
    )

    try:
        if not os.path.exists(RAW_INPUT_FILE):
            print(f"ERROR: Could not find the input file at '{RAW_INPUT_FILE}'.")
        else:
            run_full_offline_pipeline(RAW_INPUT_FILE, FINAL_OUTPUT_FILE)
    except Exception as e:
        print(f"Unexpected error occurred: {e}")