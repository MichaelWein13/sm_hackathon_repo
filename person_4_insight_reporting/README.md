# FloorFlow — Insight Engine (Person 4)

Real-time situational awareness for emergency response. Watches movement graph snapshots from Person 3, detects developing congestion and anomalies, maintains live alert state, and writes insights for Person 5 to display.

**Spec:** [`FINAL_VISION.md`](FINAL_VISION.md) · **Architecture:** [`insight_engine/DESIGN.md`](insight_engine/DESIGN.md) · **Analysis math:** [`insight_engine/ANALYSIS.md`](insight_engine/ANALYSIS.md)

---

## Integration

### For Person 3 — your output is my input

**Primary (REST):**

| Method | Path | Body / response |
|---|---|---|
| `POST` | `/ingest/graph` | Graph JSON → `{ ok, snapshot_ts, cycle, alert_count, insights[] }` |
| `GET` | `/health` | `{ status, ingest, insights }` — verify `"ingest"` before demo |

```bash
# Start the engine first
python3 insight_engine/engine.py --serve --api-only --out-dir ../anomaly_reports --fresh

# Person 3 pushes each snapshot
curl -X POST http://127.0.0.1:8765/ingest/graph \
  -H "Content-Type: application/json" \
  -d @final_movement_graph.json
```

If POST returns `501`, an old server is on the port: `fuser -k 8765/tcp` and restart.

- POST **once per snapshot** as your graph updates (same cadence you would have written files)
- Each payload is a **complete graph at that moment**, including all `time_windows` observed so far
- Include `snapshot_ts` (ms) in the JSON when you have a logical time

**Legacy (files):** `movement_graphs/graph_<timestamp_ms>.json` — still supported with `--watch-files`

**Required JSON shape:**

```json
{
  "snapshot_ts": 1718045312000,
  "nodes": ["zone_1", "zone_2", "zone_3"],
  "edges": [
    {
      "from_zone_id": "zone_1",
      "to_zone_id": "zone_3",
      "transition_count": 12,
      "transition_probability": 0.55
    }
  ],
  "zone_stats": {
    "zone_3": { "avg_dwell_ms": 7100, "visit_count": 18 }
  },
  "time_windows": [
    {
      "window_start_ms": 1718045000000,
      "window_end_ms": 1718045300000,
      "window_graph": {
        "nodes": ["zone_1", "zone_3"],
        "edges": [
          {
            "from_zone_id": "zone_1",
            "to_zone_id": "zone_3",
            "transition_count": 8,
            "transition_probability": 0.50
          }
        ]
      }
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `nodes` | yes | Zone IDs used across the session (`zone_1`, `zone_2`, …) |
| `edges` | yes | Global edge totals; can be `[]` if no movement yet |
| `zone_stats` | yes | Per-zone `avg_dwell_ms` and `visit_count` |
| `time_windows` | yes | Ordered history of per-window graphs; can be `[]` on first snapshot |
| `snapshot_ts` | recommended | Fallback if filename timestamp is unavailable |

**Reference file:** run `cd mock && python3 generate_mock_snapshots.py` once, then inspect any file in `mock/movement_graphs/`.

**Open question for integration:** per-window `zone_stats` inside each `time_windows` entry are not required today, but would improve dwell-trend detection. Confirm with Person 4 if you add them.

**Wire-up:**
```bash
export NARRATION_BACKEND=disabled
python3 insight_engine/engine.py --serve --api-only --out-dir /shared/anomaly_reports
# Person 3 POSTs to http://<host>:8765/ingest/graph
```

---

### For Person 5 — my output is your input

**Primary contract (live SSE):**

| Method | Path | Response |
|---|---|---|
| `GET` | `/analytics/stream` | `text/event-stream` — live dashboard + lifecycle events |
| `GET` | `/analytics/alerts` | Full alerts (REST fallback) |
| `GET` | `/analytics/summary` | Global headline (REST fallback) |
| `GET` | `/analytics/insights` | Flat insight array (legacy) |

**SSE events** on `/analytics/stream`:

| Event | When | Payload |
|---|---|---|
| `snapshot` | On connect | Full dashboard (`alerts`, `summary`, `headline`, `cycle`, …) |
| `cycle_update` | After each ingest cycle | Same shape as `snapshot` |
| `new`, `escalated`, `de_escalated`, `updated`, `resolved` | Lifecycle changes | Same lines as `events.ndjson` |

```javascript
const es = new EventSource('http://localhost:8765/analytics/stream');
es.addEventListener('snapshot', (e) => { /* hydrate UI */ });
es.addEventListener('cycle_update', (e) => { /* refresh UI */ });
es.addEventListener('escalated', (e) => { /* animate severity change */ });
```

**Primary contract (matches `person4.out.yml`):**

| Method | Path | Response |
|---|---|---|
| `GET` | `/analytics/insights` | Flat JSON array of `{ zone_id, insight_type, message, confidence }` |
| `GET` | `/analytics/alerts` | Full alerts with `id`, `severity`, lifecycle timestamps + `headline` |
| `GET` | `/analytics/summary` | Global `situation_summary` + `headline` |
| `POST` | `/ingest/graph` | Also returns `headline`, `summary`, and full `alerts` |

**HTTP:** with the engine running (`--serve --api-only`):

```bash
curl http://localhost:8765/analytics/insights
curl http://localhost:8765/analytics/alerts
curl http://localhost:8765/analytics/summary
curl http://localhost:8765/health
```

CORS enabled for browser dashboards.

**Option B — file poll:** read `anomaly_reports/insights_api.json` — same flat array, overwritten each cycle.

**Extended contract (lifecycle + headline):**

| File | Purpose |
|---|---|
| `insights_<timestamp_ms>.json` | Full dashboard state — `summary`, `alerts[]` with severity/lifecycle |
| `events.ndjson` | Lifecycle feed — tail for escalations and resolutions |

**Consumption model:**

1. **Headline + alert list** → parse the latest `insights_*.json` (highest `snapshot_ts` or newest mtime)
2. **Escalations, new alerts, resolutions** → read new lines from `events.ndjson`
3. **On `event: "resolved"`** → remove or grey out that `alert_id` from the UI

**Per-cycle snapshot (`insights_*.json`):**

```json
{
  "snapshot_ts": 1718045312000,
  "cycle": 7,
  "elapsed_seconds": 420,
  "summary": {
    "zone_id": "global",
    "insight_type": "situation_summary",
    "severity": "warning",
    "message": "3 active alerts across 4 observed sectors. One critical situation developing in Sector 3.",
    "confidence": 1.0
  },
  "alerts": [
    {
      "id": "zone_3__congestion_forecast",
      "zone_id": "zone_3",
      "insight_type": "congestion_forecast",
      "severity": "critical",
      "message": "Sector 3 congestion confirmed over the last 20 minutes. 52 movements in against only 6 out — traffic is stacking up. Immediate intervention required.",
      "confidence": 0.87,
      "first_seen_ts": 1718045100000,
      "last_updated_ts": 1718045312000,
      "cycle_count": 4
    }
  ]
}
```

**Event line (`events.ndjson`):**

```json
{"ts": 1718045312000, "cycle": 7, "event": "escalated", "alert_id": "zone_3__congestion_forecast", "from_severity": "warning", "to_severity": "critical", "insight": { ... }}
```

Event types: `new`, `escalated`, `de_escalated`, `updated`, `resolved`

**Severity values:** `detecting`, `warning`, `critical`, `resolving`, `resolved`

Suggested UI mapping:

| Severity | Display |
|---|---|
| `detecting` | Info / muted — pattern emerging |
| `warning` | Yellow — attention required |
| `critical` | Red — immediate action |
| `resolving` | Blue/grey — improving, keep visible |
| `resolved` | Remove from active list (event only) |

**Insight types:**

| `insight_type` | Meaning |
|---|---|
| `congestion_forecast` | Traffic rising — sector heading toward overload |
| `bottleneck_risk` | Inflow exceeding outflow, or multiple paths converging |
| `high_dwell_zone` | Unusually long stays — staging, waiting, or friction |
| `unexpected_transition` | Rare route taken — possible rerouting or new access |
| `anomaly` | New edge, quiet zone, or other structural surprise |

Full trigger logic and mock demo mapping: [`insight_engine/DESIGN.md`](insight_engine/DESIGN.md#insight-types).

**Zone labels:** messages use `Sector N` (from `zone_N`). Highlight `zone_id` on the graph.

**Wire-up:** `GET http://localhost:8765/analytics/stream` (see [`DEPLOYMENT.md`](DEPLOYMENT.md)).

---

### Person 5 — migration guide (SSE)

Person 4 now pushes live updates over **Server-Sent Events**. Replace polling with `EventSource`; keep REST as fallback.

#### 1. Start Part 4 first

```bash
cd person_4_insight_reporting   # from sm_hackathon_repo checkout
export NARRATION_BACKEND=disabled
python3 insight_engine/engine.py --serve --api-only --out-dir ../../floorflow-io/anomaly_reports --fresh
```

Verify: `curl -s http://127.0.0.1:8765/health` must include `"stream": "/analytics/stream"`.

#### 2. Proxy in dev (Create React App)

In `package.json`:

```json
"proxy": "http://localhost:8765"
```

Then use a **relative** stream URL (`/analytics/stream`) so CRA forwards to Part 4.

#### 3. Replace polling in `Api.js`

Remove `setInterval` + repeated `fetch('/analytics/alerts')`. Add:

```javascript
const INSIGHTS_STREAM_URL = '/analytics/stream';

function normalizeInsightsPayload(data) {
  return {
    snapshot_ts: data.snapshot_ts,
    cycle: data.cycle,
    elapsed_seconds: data.elapsed_seconds,
    headline: data.headline ?? null,
    alerts: data.alerts ?? [],
    summary: data.summary ?? null,
  };
}

export function subscribeToInsights({ onUpdate, onError }) {
  let closed = false;
  let source = new EventSource(INSIGHTS_STREAM_URL);

  const apply = (raw) => {
    if (!closed) onUpdate(normalizeInsightsPayload(JSON.parse(raw)));
  };

  source.addEventListener('snapshot', (e) => apply(e.data));
  source.addEventListener('cycle_update', (e) => apply(e.data));

  // Optional: animate lifecycle transitions
  source.addEventListener('escalated', (e) => { /* JSON.parse(e.data) */ });
  source.addEventListener('resolved', (e) => { /* remove alert_id from UI */ });

  source.onerror = () => { if (!closed) onError?.(new Error('SSE error')); };

  return () => { closed = true; source.close(); };
}

// Keep fetchInsights() as REST fallback when SSE fails
```

#### 4. Wire up in `App.js`

```javascript
import { subscribeToInsights, fetchInsights } from './Api';

useEffect(() => {
  let cancelled = false;

  const unsubscribe = subscribeToInsights({
    onUpdate: (payload) => {
      if (!cancelled) { setInsights(payload); setLoading(false); }
    },
    onError: () => {
      if (cancelled) return;
      fetchInsights()
        .then((data) => { if (!cancelled) { setInsights(data); setLoading(false); } })
        .catch(() => { if (!cancelled) setLoading(false); });
    },
  });

  return () => { cancelled = true; unsubscribe(); };
}, []);
```

Delete any `INSIGHTS_POLL_MS` interval — SSE replaces it.

#### 5. Payload shape (`snapshot` / `cycle_update`)

Same fields as `GET /analytics/alerts` plus embedded `summary`:

```json
{
  "snapshot_ts": 1718045312000,
  "cycle": 7,
  "elapsed_seconds": 420,
  "headline": "WARNING — Sector 3 congestion forecast",
  "summary": {
    "zone_id": "global",
    "insight_type": "situation_summary",
    "severity": "warning",
    "message": "3 active alerts across 4 observed sectors...",
    "confidence": 1.0
  },
  "alerts": [
    {
      "id": "zone_3__congestion_forecast",
      "zone_id": "zone_3",
      "insight_type": "congestion_forecast",
      "severity": "critical",
      "message": "Sector 3 traffic has tripled over the last 90 seconds...",
      "confidence": 0.87,
      "first_seen_ts": 1718045100000,
      "last_updated_ts": 1718045312000,
      "cycle_count": 4
    }
  ]
}
```

#### 6. Lifecycle events (optional UI polish)

| SSE event | UI action |
|---|---|
| `new` | Add alert card, highlight zone on graph |
| `escalated` | Change severity color (yellow → red) |
| `de_escalated` | Soften severity |
| `updated` | Refresh message text |
| `resolved` | Remove from active list (alert no longer in `cycle_update.alerts`) |

#### 7. Test without the React app

```bash
curl -sN http://127.0.0.1:8765/analytics/stream
# In another terminal, feed mock data:
python3 mock/generate_mock_snapshots.py --api-url http://127.0.0.1:8765/ingest/graph
```

You should see `event: snapshot`, then `event: new` / `event: escalated` / `event: cycle_update` as the demo runs.

#### 8. REST fallback (still supported)

If SSE is unavailable, poll these instead:

```bash
curl http://localhost:8765/analytics/alerts
curl http://localhost:8765/analytics/summary
```

---

## Quick start (mock demo)

From `person_4_insight_reporting/` (recommended):

```bash
export NARRATION_BACKEND=disabled
./scripts/run_demo.sh
```

Or two terminals manually — see [`DEPLOYMENT.md`](DEPLOYMENT.md) and [`mock/README.md`](mock/README.md).

---

## Production wiring

| Direction | Contract |
|---|---|
| **Input** (Person 3) | `POST /ingest/graph` |
| **Output** (Person 5) | `GET /analytics/stream` (SSE, primary) |
| **Output fallback** | `GET /analytics/alerts`, `/analytics/summary`, `/analytics/insights` |
| **File backup** | `anomaly_reports/` (optional; written each cycle) |

```bash
export NARRATION_BACKEND=disabled
python3 insight_engine/engine.py \
  --serve --api-only \
  --out-dir /path/to/anomaly_reports \
  --fresh
```

Legacy file ingest: add `--watch-files --graph-dir /path/to/movement_graphs`

---

## Layout

```
insight_engine/
  engine.py        — REST API + SSE stream + optional file watch
  detection.py     — signal extraction
  alert_state.py   — alert lifecycle
  narration.py     — message generation
mock/              — demo snapshot generator (POST or files)
scripts/           — run_demo.sh, reset_demo.sh, preflight.sh
```

---

## Setup

Python 3.10+. Core engine uses stdlib only.

```bash
pip install -r requirements.txt   # optional: openai for LLM narration
```

Full output field reference: [`mock/README.md`](mock/README.md)
