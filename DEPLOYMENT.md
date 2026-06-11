# FloorFlow — Deployment Guide

One-page reference for running the pipeline locally before the demo.

## Layout

```
cs_hackathon/
├── sm_hackathon_repo/          # Person 4 branch (insight engine + scripts)
│   ├── part_4/
│   └── scripts/
├── sm_hackathon-person3/       # Movement graph
├── sm_hackathon-person5/       # Visualization
└── floorflow-io/               # Shared runtime output (not in git)
    └── anomaly_reports/        # Person 4 writes → optional file backup for Person 5
```

## Prerequisites

- Python 3.10+
- Optional: `pip install -r part_4/requirements.txt` (only needed for LLM narration)

```bash
cd sm_hackathon_repo
python3 --version
```

## Quick demo (mock data)

From the repo root:

```bash
chmod +x scripts/*.sh   # first time only
./scripts/run_demo.sh
```

This will:

1. Reset `floorflow-io/anomaly_reports/`
2. Start the insight engine REST API on port **8765**
3. POST 12 mock graph snapshots via `POST /ingest/graph` (~36 seconds)
4. Leave the engine running for Person 5 to poll

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
| `DEMO_STEPS` | `12` | Mock snapshot count |
| `DEMO_INTERVAL` | `3` | Seconds between mock snapshots |
| `NARRATION_BACKEND` | `disabled` | Template messages (no API key) |

## Production wiring

### Person 3 → Person 4 (REST)

Start the engine **first**:

```bash
./scripts/run_demo.sh --engine-only
# or manually:
export NARRATION_BACKEND=disabled
python3 part_4/insight_engine/engine.py \
  --out-dir ../floorflow-io/anomaly_reports \
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
| `/analytics/insights` | `GET` | Person 5 reads flat insights array |
| `/health` | `GET` | Liveness check |

Required JSON shape: `nodes`, `edges`, `zone_stats`, `time_windows` (see `part_4/README.md`).

### Person 4 → Person 5

```bash
curl http://127.0.0.1:8765/analytics/insights
curl http://127.0.0.1:8765/health
```

Point Person 5's frontend at `http://localhost:8765/analytics/insights`.

**Optional file backup** (written each cycle to `anomaly_reports/`):

| File | Purpose |
|---|---|
| `insights_api.json` | Same flat array as GET `/analytics/insights` |
| `insights_<ts>.json` | Full state: `summary` + `alerts[]` with lifecycle |
| `events.ndjson` | Append-only: `new`, `escalated`, `resolved`, … |

### Legacy file ingest (optional)

If Person 3 still writes files instead of POSTing:

```bash
python3 part_4/insight_engine/engine.py \
  --serve --watch-files \
  --graph-dir ../floorflow-io/movement_graphs \
  --out-dir ../floorflow-io/anomaly_reports
```

## Manual mock demo (two terminals)

**Terminal 1 — engine**
```bash
./scripts/reset_demo.sh
export NARRATION_BACKEND=disabled
python3 part_4/insight_engine/engine.py \
  --out-dir ../floorflow-io/anomaly_reports \
  --serve --api-only --fresh
```

**Terminal 2 — mock Person 3**
```bash
python3 part_4/mock/generate_mock_snapshots.py \
  --api-url http://127.0.0.1:8765/ingest/graph
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` on POST | Start engine with `--serve --api-only` first |
| `501 Unsupported method ('POST')` | **Stale server** on the port — old engine without `/ingest/graph`. Run `fuser -k 8765/tcp`, then restart |
| `Address already in use` | `fuser -k 8765/tcp` or `FLOORFLOW_API_PORT=8766 ./scripts/run_demo.sh` |
| `/health` missing `"ingest"` | Wrong server version bound to port — kill and restart with latest `engine.py` |
| Empty `insights` array | Normal before first POST; check Person 3 is sending valid JSON |
| Duplicate events after restart | Use `--fresh` or run `./scripts/reset_demo.sh` first |
| `400 missing required field` | Graph body must include `nodes`, `edges`, `zone_stats`, `time_windows` |
| Engine crash on bad JSON | Validate payload against `part_4/README.md` |

## Pre-demo checklist

```bash
./scripts/preflight.sh
./scripts/run_demo.sh
curl -s http://127.0.0.1:8765/health | python3 -m json.tool
curl -s http://127.0.0.1:8765/analytics/insights | python3 -m json.tool | head
```

## Related docs

- `MULTI_BRANCH_SETUP.md` — worktrees and team layout
- `part_4/README.md` — full I/O contracts for Person 3 and Person 5
- `part_4/FINAL_VISION.md` — product spec and demo moment
