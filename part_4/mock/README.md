# Mock Data & Demo — Person 4 (Insight Engine)

Simulates Person 3 by POSTing movement graph snapshots to the insight engine REST API.

---

## Quick demo (recommended)

From `sm_hackathon_repo` root:

```bash
./scripts/run_demo.sh
```

Starts the engine, POSTs 12 snapshots, leaves the API running for Person 5.

---

## Manual two-terminal demo

**Terminal 1 — engine**
```bash
export NARRATION_BACKEND=disabled
python3 insight_engine/engine.py \
  --serve --api-only \
  --out-dir ../floorflow-io/anomaly_reports \
  --fresh
```

**Terminal 2 — mock Person 3**
```bash
python3 mock/generate_mock_snapshots.py \
  --api-url http://127.0.0.1:8765/ingest/graph
```

Verify Person 5 output:
```bash
curl -s http://127.0.0.1:8765/analytics/insights | python3 -m json.tool | head
```

Confirm the server supports ingest (not a stale old process):
```bash
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
# must include: "ingest": "/ingest/graph"
```

If port is busy or POST returns `501`:
```bash
fuser -k 8765/tcp
```

---

## What the mock scenario shows

**Multi-sector chaos** — overlapping crises in different zones (~30 steps × 3 s ≈ 90 s):

| When | Sector | What fires |
|------|--------|------------|
| Steps 2–6 | zone_2 | `high_dwell_zone` — medical staging fills up |
| Steps 4–6 | zone_1 | `unexpected_transition` — rare route from zone_4 |
| Steps 8–14 | zone_3 | `congestion_forecast` + `bottleneck_risk` — food-hall surge (short) |
| Steps 7–13 | zone_2 | `bottleneck_risk` — staging backup while zone_3 is also hot |
| Steps 14–20 | zone_4 | convergence crisis — lobby + kitchen feed practice room |
| Steps 16–23 | zone_5 | aquarium opens; crowding + rare return route to zone_1 |
| Steps 25–26 | zone_4 | sector briefly offline → isolation `anomaly` |
| Steps 27–29 | all | staggered resolutions |

Alert types mix across the run: `high_dwell_zone`, `unexpected_transition`, `congestion_forecast`, `bottleneck_risk`, and structural `anomaly` — not one long zone_3 meltdown.

Default run: **30 snapshots**, one every **3 seconds** (~90 s total).

---

## Generator options

```bash
# REST (integration / demo)
python3 mock/generate_mock_snapshots.py \
  --api-url http://127.0.0.1:8765/ingest/graph \
  --steps 30 --interval 3

# Legacy files (local debugging only)
python3 mock/generate_mock_snapshots.py --out-dir mock/movement_graphs
```

Traffic tables loop after 30 steps for long soak runs.

---

## 5-minute soak test

```bash
./mock/run_soak_test.sh
```

Or manually with REST:
```bash
fuser -k 8767/tcp 2>/dev/null || true
export NARRATION_BACKEND=disabled
python3 insight_engine/engine.py \
  --serve --api-only --api-port 8767 \
  --out-dir mock/anomaly_reports --fresh &
sleep 1
python3 mock/generate_mock_snapshots.py \
  --api-url http://127.0.0.1:8767/ingest/graph \
  --steps 100 --interval 3
```

---

## Output

Each POST still writes backup files to `--out-dir` (default `../floorflow-io/anomaly_reports/` in production):

| File | Contents |
|---|---|
| `insights_<timestamp_ms>.json` | Per-cycle snapshot: `summary`, `alerts[]`, `cycle`, `elapsed_seconds` |
| `insights_api.json` | Flat array — same as `GET /analytics/insights` |
| `events.ndjson` | Append-only event stream: `new`, `escalated`, `de_escalated`, `updated`, `resolved` |

**Insight types:** see [`insight_engine/DESIGN.md`](../insight_engine/DESIGN.md#insight-types).

---

## One-shot test (single file, no server)

```bash
python3 insight_engine/engine.py \
  --once mock/movement_graphs/graph_<timestamp_ms>.json \
  --out-dir mock/anomaly_reports
```

---

## Environment

```bash
export NARRATION_BACKEND=disabled   # templates only — recommended for demo
```

Install optional deps: `pip install -r requirements.txt`
