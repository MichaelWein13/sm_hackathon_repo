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

| Story | Alert | Arc |
|---|---|---|
| **A — Congestion** | `zone_3__congestion_forecast` | detecting → warning → critical → resolving → resolved |
| **B — New route** | `zone_1__unexpected_transition` | Overlaps with Story A from step 3 (Sector 4 → Sector 1) |
| Background | `zone_2__high_dwell_zone` | Persistent medical-staging area (high dwell) |

Default run: **12 snapshots**, one every **3 seconds** (~36 s total).

---

## Generator options

```bash
# REST (integration / demo)
python3 mock/generate_mock_snapshots.py \
  --api-url http://127.0.0.1:8765/ingest/graph \
  --steps 12 --interval 3

# Legacy files (local debugging only)
python3 mock/generate_mock_snapshots.py --out-dir mock/movement_graphs
```

Traffic tables loop after 12 steps for long runs.

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
