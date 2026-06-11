import json
from collections import defaultdict
from fastapi import FastAPI, BackgroundTasks
import uvicorn
import requests

class MovementGraphBuilder:
    def __init__(self, window_size_ms=300000):
        # Default window size: 5 minutes (300,000 ms)
        self.window_size_ms = window_size_ms

    def build_graph(self, observations):
        """
        Takes a list of zone assignments and returns the graph summary dictionary.
        """
        if not observations:
            return {}

        # 1. Sort observations chronologically
        observations.sort(key=lambda x: x['timestamp_ms'])

        # Determine global time boundaries
        start_time = observations[0]['timestamp_ms']

        # 2. Group by device to track individual paths
        device_paths = defaultdict(list)
        for obs in observations:
            device_paths[obs['device_id']].append(obs)

        # 3. Initialize tracking structures
        nodes = set()
        zone_visits = defaultdict(int)
        zone_dwell_times = defaultdict(list)

        global_transitions = defaultdict(int)
        windowed_transitions = defaultdict(lambda: defaultdict(int))
        windowed_nodes = defaultdict(set)

        # 4. Process the movement path for each device
        for device_id, path in device_paths.items():
            for i in range(len(path)):
                current_obs = path[i]
                current_zone = current_obs['zone_id']
                current_time = current_obs['timestamp_ms']

                nodes.add(current_zone)

                # Determine which time window this observation falls into
                window_idx = (current_time - start_time) // self.window_size_ms
                windowed_nodes[window_idx].add(current_zone)

                # Dwell time & Visit counting
                if i == 0 or path[i-1]['zone_id'] != current_zone:
                    zone_visits[current_zone] += 1

                    if i < len(path) - 1:
                        next_diff_zone_idx = i + 1
                        while next_diff_zone_idx < len(path) and path[next_diff_zone_idx]['zone_id'] == current_zone:
                            next_diff_zone_idx += 1

                        if next_diff_zone_idx < len(path):
                            exit_time = path[next_diff_zone_idx]['timestamp_ms']
                            zone_dwell_times[current_zone].append(exit_time - current_time)

                # Transition counting
                if i < len(path) - 1:
                    next_zone = path[i+1]['zone_id']
                    if current_zone != next_zone:
                        trans_key = (current_zone, next_zone)
                        global_transitions[trans_key] += 1
                        windowed_transitions[window_idx][trans_key] += 1

        # 5. Format Global Edges
        edges = self._format_edges(global_transitions)

        # 6. Format Zone Stats
        zone_stats = {}
        for zone in nodes:
            dwells = zone_dwell_times.get(zone, [])
            avg_dwell = sum(dwells) // len(dwells) if dwells else 0
            zone_stats[zone] = {
                "avg_dwell_ms": avg_dwell,
                "visit_count": zone_visits[zone]
            }

        # 7. Format Time Windows
        time_windows = []
        if observations:
            max_window_idx = (observations[-1]['timestamp_ms'] - start_time) // self.window_size_ms
            for w_idx in range(max_window_idx + 1):
                w_start = start_time + (w_idx * self.window_size_ms)
                w_end = w_start + self.window_size_ms

                w_edges = self._format_edges(windowed_transitions[w_idx])

                time_windows.append({
                    "window_start_ms": w_start,
                    "window_end_ms": w_end,
                    "window_graph": {
                        "nodes": list(windowed_nodes[w_idx]),
                        "edges": w_edges
                    }
                })

        # 8. Construct Final JSON Object
        return {
            "nodes": list(nodes),
            "edges": edges,
            "zone_stats": zone_stats,
            "time_windows": time_windows
        }

    def _format_edges(self, transitions_dict):
        """Helper to calculate probabilities and format edge objects"""
        formatted_edges = []
        outgoing_totals = defaultdict(int)
        for (from_z, to_z), count in transitions_dict.items():
            outgoing_totals[from_z] += count

        for (from_z, to_z), count in transitions_dict.items():
            probability = count / outgoing_totals[from_z] if outgoing_totals[from_z] > 0 else 0.0
            formatted_edges.append({
                "from_zone_id": from_z,
                "to_zone_id": to_z,
                "transition_count": count,
                "transition_probability": round(probability, 2)
            })
        return formatted_edges


# --- SERVER SETUP ---

app = FastAPI()
builder = MovementGraphBuilder()

# 🌟 NEW: A global variable to hold the latest graph in memory!
LATEST_GRAPH_DATA = {}

# --- CONFIGURATION ---
PERSON_4_URL = "http://localhost:8765/receive_graph"
PERSON_5_URL = "http://localhost:3000/analytics/graph"

def send_to_node(target_url: str, target_name: str, graph_data: dict):
    """This function handles pushing data to any teammate."""
    print(f"Sending newly built graph to {target_name} at {target_url}...")
    try:
        response = requests.post(target_url, json=graph_data)
        if response.status_code == 200:
            print(f"✅ Delivery successful! {target_name} has the data.")
        else:
            print(f"❌ Delivery failed! {target_name} returned code: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print(f"⚠️ ERROR: Could not connect to {target_name}. Is their server running?")


# --- ENDPOINT 1: PERSON 2 PUSHES DATA HERE ---
@app.post("/receive_assignments")
def receive_from_person_2(person_2_output: list, background_tasks: BackgroundTasks):
    global LATEST_GRAPH_DATA # Tell Python we are modifying the global memory
    print(f"\n--- Incoming data! Received {len(person_2_output)} observations from Person 2. ---")

    # 1. Build the graph immediately
    final_graph = builder.build_graph(person_2_output)

    # 🌟 NEW: Save it to our global memory so Person 5 can request it later
    LATEST_GRAPH_DATA = final_graph

    # Optional: Keep a local backup of the latest run
    with open('final_movement_graph.json', 'w') as out_file:
        json.dump(final_graph, out_file, indent=2)

    # 2. Tell FastAPI to send the data to teammate 3 in the background
    background_tasks.add_task(send_to_node, PERSON_4_URL, "Person 4", final_graph)

    # 3. Instantly reply to Person 2
    return {"status": "Success! Graph built and forwarded to Persons 4 and 5."}


# --- 🌟 NEW ENDPOINT 2: PERSON 5 CAN PULL DATA FROM HERE ---
@app.get("/graph")
def send_to_person_5():
    """Person 5 calls this GET endpoint whenever they want the newest map data."""
    print("Person 5 just requested the latest graph data!")

    # Check if Person 2 has actually sent us anything yet
    if not LATEST_GRAPH_DATA:
        return {"status": "Waiting on data", "message": "The graph has not been built yet. Please wait for Person 2."}

    # If we have data, hand it right over to Person 5
    return LATEST_GRAPH_DATA


if __name__ == "__main__":
    print("Starting Person 3 Pipeline Node...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
