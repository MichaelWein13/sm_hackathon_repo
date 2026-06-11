# FloorFlow — Deployment Guide

One-page reference for running the pipeline locally before the demo.

## Layout

```
cs_hackathon/
├── sm_hackathon_repo/          # Person 4 branch (part4_iape)
│   └── part_4_insight_reporting/                 # insight engine, scripts, docs
│       ├── insight_engine/
│       ├── mock/
│       └── scripts/
├── sm_hackathon-person3/       # Movement graph
├── sm_hackathon-person5/       # Visualization
└── floorflow-io/               # Shared runtime output (not in git)
    └── anomaly_reports/        # Person 4 writes → optional file backup for Person 5
```

## Prerequisites

- Python 3.10+
- Optional: `pip install -r requirements.txt` (only needed for LLM narration)

```bash
cd sm_hackathon_repo/part_4_insight_reporting
python3 --version
```

## Quick demo (mock data)

From `part_4_insight_reporting/` (Person 4 tree):

```bash
chmod +x scripts/*.sh   # first time only
./scripts/run_demo.sh
```

This will:

1. Reset `floorflow-io/anomaly_reports/`
2. Start the insight engine REST API on port **8765**
3. POST mock graph snapshots via `POST /ingest/graph` (~90 seconds at default settings)
4. Leave the engine running for Person 5 to connect via SSE

### Run modes

| Command | Use when |
|---|---|
| `./scripts/run_demo.sh` | Full demo — reset + engine + mock POSTs |
| `./scripts/run_demo.sh --engine-only` | Person 3 is POSTing graphs; engine runs in foreground |
| `./scripts/run_demo.sh --mock-only` | Engine already running; just emit mock snapshots |
| `./scripts/run_demo.sh --no-reset` | Re-run without wiping prior output |
| `./scripts/reset_demo.sh` | Wipe anomaly_reports only |
| `./scripts/preflight.sh` | Pre-demo sanity checks |

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FLOORFLOW_IO` | `../floorflow-io` | Shared output root |
| `FLOORFLOW_OUT_DIR` | `$FLOORFLOW_IO/anomaly_reports` | Engine output files |
| `FLOORFLOW_API_PORT` | `8765` | REST API port |
| `DEMO_STEPS` | `30` | Mock snapshot count |
| `DEMO_INTERVAL` | `3` | Seconds between mock snapshots |
| `NARRATION_BACKEND` | `disabled` | Template messages (no API key) |

## Production wiring

### Person 3 → Person 4 (REST)

Start the engine **first**:

```bash
./scripts/run_demo.sh --engine-only
# or manually:
export NARRATION_BACKEND=disabled
python3 insight_engine/engine.py \
  --out-dir ../../floorflow-io/anomaly_reports \
  --serve --api-only --fresh
```

Person 3 **POSTs** each graph snapshot:

```bash
curl -X POST http://127.0.0.1:8765/ingest/graph \
  -H "Content-Type: application/json" \
  -d @final_movement_graph.json
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/ingest/graph` | `POST` | Person 3 submits movement graph JSON |
| `/analytics/stream` | `GET` | **Person 5 primary** — live SSE feed |
| `/analytics/alerts` | `GET` | Full alerts (REST fallback) |
| `/analytics/summary` | `GET` | Global situation summary + headline |
| `/analytics/insights` | `GET` | Flat insights array (legacy) |
| `/health` | `GET` | Liveness check — lists all routes |

Required JSON shape: `nodes`, `edges`, `zone_stats`, `time_windows` (see `README.md`).

### Person 4 → Person 5

**Primary: SSE (live push)**

```bash
curl -sN http://127.0.0.1:8765/analytics/stream
```

Events: `snapshot` (on connect), `cycle_update` (each ingest), plus lifecycle events (`new`, `escalated`, `resolved`, …).

Person 5 should connect with browser `EventSource('/analytics/stream')`. In Create React App, set `"proxy": "http://localhost:8765"` in `package.json` and use the relative URL.

Full migration steps: **`README.md` → "Person 5 — migration guide (SSE)"**.

**REST fallback** (if SSE unavailable):

```bash
curl http://127.0.0.1:8765/analytics/alerts
curl http://127.0.0.1:8765/analytics/summary
curl http://127.0.0.1:8765/health
```

**Optional file backup** (written each cycle to `anomaly_reports/`):

| File | Purpose |
|---|---|
| `insights_api.json` | Same flat array as GET `/analytics/insights` |
| `insights_<ts>.json` | Full state: `summary` + `alerts[]` with lifecycle |
| `events.ndjson` | Append-only: `new`, `escalated`, `resolved`, … |

### Legacy file ingest (optional)

If Person 3 still writes files instead of POSTing:

```bash
python3 insight_engine/engine.py \
  --serve --watch-files \
  --graph-dir ../../floorflow-io/movement_graphs \
  --out-dir ../../floorflow-io/anomaly_reports
```

## Manual mock demo (two terminals)

**Terminal 1 — engine**
```bash
./scripts/reset_demo.sh
export NARRATION_BACKEND=disabled
python3 insight_engine/engine.py \
  --out-dir ../../floorflow-io/anomaly_reports \
  --serve --api-only --fresh
```

**Terminal 2 — mock Person 3**
```bash
python3 mock/generate_mock_snapshots.py \
  --api-url http://127.0.0.1:8765/ingest/graph
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` on POST | Start engine with `--serve --api-only` first |
| `501 Unsupported method ('POST')` | **Stale server** on the port — old engine without `/ingest/graph`. Run `fuser -k 8765/tcp`, then restart |
| `Address already in use` | `fuser -k 8765/tcp` or `FLOORFLOW_API_PORT=8766 ./scripts/run_demo.sh` |
| `/health` missing `"ingest"` or `"stream"` | Wrong server version bound to port — kill and restart with latest `engine.py` |
| SSE connects but no updates | Person 3 not POSTing — run mock generator or check Person 3 wiring |
| Empty `insights` array | Normal before first POST; check Person 3 is sending valid JSON |
| Duplicate events after restart | Use `--fresh` or run `./scripts/reset_demo.sh` first |
| `400 missing required field` | Graph body must include `nodes`, `edges`, `zone_stats`, `time_windows` |
| Engine crash on bad JSON | Validate payload against `README.md` |

## Pre-demo checklist

```bash
./scripts/preflight.sh
./scripts/run_demo.sh
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
curl -sN http://127.0.0.1:8765/analytics/stream | head -5
```

## Related docs

- `MULTI_BRANCH_SETUP.md` — worktrees and team layout
- `README.md` — full I/O contracts for Person 3 and Person 5
- `FINAL_VISION.md` — product spec and demo moment
