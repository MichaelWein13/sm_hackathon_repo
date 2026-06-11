import json
from collections import defaultdict

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

                # Dwell time & Visit counting (simplified: only count when entering a zone)
                if i == 0 or path[i-1]['zone_id'] != current_zone:
                    zone_visits[current_zone] += 1

                    # Calculate dwell time if there is a next observation
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

        # 6. Format Zone Stats [cite: 135]
        zone_stats = {}
        for zone in nodes:
            dwells = zone_dwell_times.get(zone, [])
            avg_dwell = sum(dwells) // len(dwells) if dwells else 0
            zone_stats[zone] = {
                "avg_dwell_ms": avg_dwell, # [cite: 137]
                "visit_count": zone_visits[zone] # [cite: 138]
            }

        # 7. Format Time Windows [cite: 139]
        time_windows = []
        if observations:
            max_window_idx = (observations[-1]['timestamp_ms'] - start_time) // self.window_size_ms
            for w_idx in range(max_window_idx + 1):
                w_start = start_time + (w_idx * self.window_size_ms)
                w_end = w_start + self.window_size_ms

                w_edges = self._format_edges(windowed_transitions[w_idx])

                time_windows.append({
                    "window_start_ms": w_start, # [cite: 140]
                    "window_end_ms": w_end, # [cite: 141]
                    "window_graph": { # [cite: 142]
                        "nodes": list(windowed_nodes[w_idx]),
                        "edges": w_edges
                    }
                })

        # 8. Construct Final JSON Object [cite: 94]
        return {
            "nodes": list(nodes), # [cite: 129]
            "edges": edges, # [cite: 130]
            "zone_stats": zone_stats, # [cite: 135]
            "time_windows": time_windows # [cite: 139]
        }

    def _format_edges(self, transitions_dict):
        """Helper to calculate probabilities and format edge objects"""
        formatted_edges = []
        # Calculate outgoing totals for probability math
        outgoing_totals = defaultdict(int)
        for (from_z, to_z), count in transitions_dict.items():
            outgoing_totals[from_z] += count

        for (from_z, to_z), count in transitions_dict.items():
            probability = count / outgoing_totals[from_z] if outgoing_totals[from_z] > 0 else 0.0
            formatted_edges.append({
                "from_zone_id": from_z, # [cite: 131]
                "to_zone_id": to_z, # [cite: 132]
                "transition_count": count, # [cite: 133]
                "transition_probability": round(probability, 2) # [cite: 134]
            })
        return formatted_edges

# --- Test Data & Execution ---
if __name__ == "__main__":
    # Open the file your friend uploaded (change the filename to match theirs!)
    with open('/Users/arielmiron/Desktop/Hackathon/sm_hackathon_repo/person2_zone_discovery/outputs/assignments.json', 'r') as file:
        person_2_output = json.load(file)

    # The rest stays exactly the same
    builder = MovementGraphBuilder()
    final_graph = builder.build_graph(person_2_output)

    with open('final_movement_graph.json', 'w') as out_file:
        json.dump(final_graph, out_file, indent=2)

    print("Graph successfully saved to final_movement_graph.json!")