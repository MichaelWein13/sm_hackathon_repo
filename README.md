# FloorFlow

End-to-end system that turns indoor wireless signals (Wi-Fi, BLE, UWB) into discovered zones, a live movement graph, operational alerts, and a dashboard — without a floor plan.

**Team:** Michael Wein · Or Prager · Shelley Roth · Shay Lavi · Ariel Miron

---

## System overview

FloorFlow is a six-stage pipeline. Each stage is a folder in this repo. Data flows forward; later stages consume the output of earlier ones.

```
person_0 → person_1 → person_2 → person_3 → person_4 → person_5
collect    standardize  zones     movement   insights    dashboard
```

At runtime, Person 3 pushes graph snapshots to Person 4 (insight engine) and Person 5 (visualization). Person 5 also receives live alerts from Person 4.

---

## Components

| Folder | Role | Key entry points |
|--------|------|------------------|
| `person_0_data_collection_demo/` | Collect raw signals from demo hardware | `multimodal_demo/collect_multimodal_records.py`, `esp_receiver_server.py`, `wifi_windows_scan.py` |
| `person_1_cleaning_data/` | Normalize raw data into a standard observation format | `adapt_person0_output.py`, `convert_ble_to_standard_format.py` |
| `person_2_zone_discovery/` | Cluster observations into discovered zones | `zone_discovery.py` |
| `person_3_pipeline/` | Build a movement graph from zone assignments | `main.py` |
| `person_4_insight_reporting/` | Detect anomalies, maintain alert state, emit insights | `insight_engine/engine.py` |
| `person_5_visualization/` | Render the graph and live alerts | `zone-graph/` (React app) |
| `scripts/` | Run the collection → standardization → zone-discovery chain | `run_pipeline.sh` |

---

## Data flow

### 1. Raw signals → standard observations

**Person 0** writes multimodal records (Wi-Fi fingerprints, ESP/UWB anchor readings) to its output directory.

**Person 1** converts these — or an offline BLE dataset — into a JSON array of observation records:

```json
{ "timestamp_ms": 1710000000000, "device_id": "person_1", "source_type": "uwb", "signal_vector": [2.41, 2.87, 3.02] }
```

Output: `person_1_cleaning_data/converted_data/person2_input.json`

### 2. Observations → zone assignments

**Person 2** clusters observations by signal similarity (HDBSCAN) and assigns each record a `zone_id` (e.g. `zone_1`, `zone_2`, or `transition` for corridor noise).

Output:
- `person_2_zone_discovery/outputs/assignments.json` — per-device, per-timestamp zone labels
- `person_2_zone_discovery/outputs/zones.json` — zone definitions

### 3. Assignments → movement graph

**Person 3** tracks how each device moves between zones over time. It produces a graph snapshot:

- **Nodes** — discovered zones
- **Edges** — transitions between zones (count + probability)
- **Zone stats** — average dwell time and visit count per zone
- **Time windows** — per-period subgraphs for trend detection

Output: `person_3_pipeline/final_movement_graph.json`, pushed via HTTP to Person 4 and Person 5.

### 4. Graph → insights

**Person 4** receives successive graph snapshots and compares them over time. It maintains a live alert state and detects:

- Congestion and bottleneck risk
- High-dwell zones
- Unexpected transitions and structural anomalies
- Cross-zone patterns (convergence, isolation, overflow, cascade)

Output: live stream to Person 5 (`GET /analytics/stream` on port 8765), plus optional files in `anomaly_reports/` (`insights_*.json`, `events.ndjson`).

### 5. Graph + insights → dashboard

**Person 5** renders the movement graph as an interactive force-directed layout and displays active alerts with a situation summary headline.

---

## How the stages connect

```
Person 0 output dir
       ↓  adapt_person0_output.py
Person 1 observations JSON
       ↓  zone_discovery.py
Person 2 assignments JSON
       ↓  POST /receive_assignments
Person 3 movement graph
       ↓  POST /ingest/graph          ↓  graph to dashboard
Person 4 insights (alerts)  ────────→  Person 5
```

- **Person 0 → 1 → 2:** `scripts/run_pipeline.sh` (live or offline mode)
- **Person 3 → 4:** Person 3 POSTs each graph snapshot to `http://127.0.0.1:8765/ingest/graph`
- **Person 4 → 5:** Person 5 subscribes to Person 4's live event stream

---

## Running the system

```bash
# Stages 0 → 1 → 2 (signal collection and zone discovery)
./scripts/run_pipeline.sh live       # continuous
./scripts/run_pipeline.sh offline    # historical BLE dataset

# Insight engine + mock graph demo (stages 3–4 without live hardware)
cd person_4_insight_reporting && ./scripts/run_demo.sh

# Dashboard
cd person_5_visualization/zone-graph && npm start
```

Full wiring guide: `person_4_insight_reporting/DEPLOYMENT.md`

---

## Module documentation

| Path | Contents |
|------|----------|
| `person_2_zone_discovery/README.md` | Zone discovery input/output |
| `person_4_insight_reporting/README.md` | API contracts between stages 3, 4, and 5 |
| `person_4_insight_reporting/FINAL_VISION.md` | Alert types, lifecycle, and message format |
| `person_5_visualization/zone-graph/GRAPH_API.md` | Graph data API for the dashboard |
