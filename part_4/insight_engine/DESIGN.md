# Insight Engine — Design Document

**Authoritative spec:** [`FINAL_VISION.md`](../FINAL_VISION.md) — this document describes how the code implements it. If they disagree, FINAL_VISION wins.

**Math & low-level implementation:** [`ANALYSIS.md`](ANALYSIS.md) — formulas, time bases, state, tunables.

---

## Purpose

Stage 4 of FloorFlow. A **real-time situational awareness engine** that watches movement graph snapshots from Person 3, maintains live alert state, and writes operational insights for Person 5.

It does not produce static reports. It tracks developing situations across cycles and speaks in the language of emergency operations.

---

## Position in the Pipeline

```
Person 3 (Movement Graph)
        │
        │  POST /ingest/graph  (primary)
        │  or movement_graphs/graph_<ts>.json  (legacy --watch-files)
        ▼
 insight_engine/              ← you are here
   engine.py       — REST API + optional file watch
   detection.py    — signal extraction (stateless)
   alert_state.py  — lifecycle, hysteresis, memory
   narration.py    — message generation (LLM + templates)
        │
        │  GET /analytics/stream  → Person 5 (SSE, primary)
        │  GET /analytics/alerts  → Person 5 (REST fallback)
        │  GET /analytics/summary → Person 5 (headline)
        │  GET /analytics/insights → Person 5 (legacy flat array)
        │  anomaly_reports/  (optional file backup)
        ▼
Person 5 (Visualization)
```

---

## Module Responsibilities

| Module | Role |
|---|---|
| `engine.py` | REST API (`POST /ingest/graph`, `GET /analytics/*`), optional file watch, writes outputs |
| `detection.py` | Pure functions: accumulation, intra-trend, EWMA drift, structural change, cascade, convergence → per-zone urgency |
| `alert_state.py` | Alert lifecycle (`detecting → warning → critical → resolving → resolved`), hysteresis, pattern memory |
| `narration.py` | Turns signals into incident-commander messages; LLM primary, templates fallback |

Detection is side-effect-free. All cross-snapshot memory lives in `engine.py` (EWMA baselines, edge history) and `alert_state.py` (active alerts).

---

## Detection Signals

Each snapshot yields a signals dict consumed by the alert state manager.

### Per-zone urgency (`detection.py` → `compute_urgency`)

Combined score in `[0, 1]`:

| Component | Weight | Source |
|---|---|---|
| Accumulation | 50% | Inbound vs outbound ratio — people arriving faster than leaving |
| Intra-trend | 25% | Linear regression slope × R² over `time_windows` inbound series |
| EWMA deviation | 25% | Current inbound vs running baseline across snapshots |

Dwell time above median applies a multiplier (long stays under congestion = more urgent).

### Cross-zone signals

| Signal | Function | What it catches |
|---|---|---|
| Convergence | `compute_convergence` | Multiple high-traffic edges feeding one destination |
| Cascade risk | `compute_cascade_risk` | Warning upstream + warning downstream on connected path |
| Structural | `compute_structural_changes` | New edges, low-probability transitions, isolated zones |

### Insight types

Each active alert carries an `insight_type` — the operational category Person 5 displays and messages reference. Types are chosen in `alert_state.py`.

#### Glossary

| `insight_type` | What it means (incident commander view) | When it fires |
|---|---|---|
| `congestion_forecast` | Traffic is rising and this sector is heading toward overload | Rising inbound trend across `time_windows` (`trend_score > 0.25`) plus warning-level urgency |
| `bottleneck_risk` | People arriving faster than leaving, or multiple paths converging on one destination | High inflow/outflow ratio (`acc_ratio > 2.5`), **or** convergence (≥2 high-traffic feeders), **or** cascade (stressed upstream + downstream pair) |
| `high_dwell_zone` | People staying unusually long — staging, waiting, or friction | Zone `avg_dwell_ms` > 2× the building median |
| `unexpected_transition` | A rare route was taken — possible rerouting or access to an unused area | Edge with `transition_probability < 0.05` that fired this cycle |
| `anomaly` | Structural surprise — new topology or a zone going quiet | Brand-new edge never seen in the session, **or** previously active zone now absent, **or** general unusual activity below other type thresholds |

**Global headline only** (not in Person 5's flat API): `situation_summary` on `zone_id: "global"` — one-line dashboard headline in `insights_<ts>.json`.

#### Selection rules

**Zone-level urgency alerts** — one type per zone, by priority in `_select_insight_type`:

`congestion_forecast` → `bottleneck_risk` → `high_dwell_zone` → `anomaly`

**Structural / cross-zone alerts** — raised separately, can coexist with zone-level alerts on the same `zone_id`:

| Type | Source in `alert_state.py` |
|---|---|
| `unexpected_transition` | `structural.unexpected_transitions` |
| `anomaly` (new route) | `structural.new_edges` |
| `anomaly` (isolation) | `structural.isolated_zones` |
| `bottleneck_risk` (convergence) | `convergence` pass |
| `bottleneck_risk` (cascade) | `cascades` pass |

Alert IDs are `{zone_id}__{insight_type}` — e.g. `zone_3__congestion_forecast`.

#### Mock demo mapping

See [`mock/README.md`](../mock/README.md). Short version:

| Story | Alert ID | Type |
|---|---|---|
| A — Congestion arc | `zone_3__congestion_forecast` | `congestion_forecast` |
| A — Convergence feeder | `zone_3__bottleneck_risk` | `bottleneck_risk` |
| B — New rare route | `zone_1__unexpected_transition` | `unexpected_transition` |
| Background — Medical staging | `zone_2__high_dwell_zone` | `high_dwell_zone` |
| New zone / edge appears | `zone_*__anomaly` | `anomaly` |

---

## Alert Lifecycle

Every alert moves through a severity ladder, not on/off:

```
detecting → warning → critical → resolving → resolved
```

Rules (`alert_state.py`):
- **New alerts always start at `detecting`** — severity is earned over cycles, not assigned from raw urgency
- **Escalation is one step at a time** — never jumps detecting → critical in one cycle
- **Hysteresis** — `CYCLES_TO_ESCALATE` / `CYCLES_TO_DEESCALATE` consecutive cycles before step change
- **Pattern memory** — zones that previously hit a severity escalate faster on re-entry
- **Resolution** — absent signals for N cycles → `resolving` → `resolved`; explicit `resolved` event emitted

Thresholds in `SEVERITY_THRESHOLDS` are tunable constants — calibrate against real Person 3 data during integration.

---

## Narration

`narration.py` generates one message per alert create/escalation/de-escalation/resolving transition.

- **Primary:** OpenAI-compatible API (configurable via env vars)
- **Fallback:** Templates written to incident-commander standard — time context, magnitude, recommendations; no statistical jargon

Set `NARRATION_BACKEND=disabled` to use templates only (recommended for demo reliability).

---

## Output Contract

See FINAL_VISION for full schema. Summary:

**Per cycle:** `anomaly_reports/insights_<timestamp_ms>.json`
- `summary` — global situation headline (`zone_id: "global"`)
- `alerts[]` — active alerts with `id`, `severity`, `message`, `confidence`, lifecycle timestamps

**Append-only:** `anomaly_reports/events.ndjson`
- Event types: `new`, `escalated`, `de_escalated`, `updated`, `resolved`

---

## Input Contract

**Primary:** Person 3 `POST /ingest/graph` with a complete graph JSON body (including cumulative `time_windows`). Optional `snapshot_ts` field (ms); defaults to server time.

**Legacy:** `movement_graphs/graph_<timestamp_ms>.json` via `--watch-files`.

Required fields: `nodes`, `edges`, `zone_stats`, `time_windows`.

---

## Tuning

| Parameter | Location | Effect |
|---|---|---|
| `SEVERITY_THRESHOLDS` | `alert_state.py` | Urgency → detecting / warning / critical boundaries |
| `CYCLES_TO_ESCALATE` / `CYCLES_TO_DEESCALATE` | `alert_state.py` | Hysteresis strength |
| Urgency weights (50 / 25 / 25) | `detection.py` → `compute_urgency` | Accumulation vs trend vs EWMA |
| `EWMA_ALPHA` | `detection.py` | Cross-snapshot baseline responsiveness |
| Convergence / cascade cutoffs | `detection.py` | Cross-zone pattern sensitivity |
| `NARRATION_*` env vars | `narration.py` | LLM backend and model |

---

## Dependencies

**Use whatever produces the best product.** No arbitrary stdlib-only restriction.

Current stack:
- **Core:** Python 3.10+ stdlib — sufficient for current detection math and I/O
- **Optional:** `openai` — LLM narration (`requirements.txt`)

Add libraries when they meaningfully improve quality or reliability:
- `numpy` / `pandas` — richer time-series if snapshot history grows large
- `scikit-learn` — isolation forest or other anomaly models if heuristics prove insufficient
- `watchdog` — replace polling if filesystem latency becomes an issue

Evaluate each on merit at integration time, not upfront.

---

## Usage

```bash
# Demo (from part_4/)
./scripts/run_demo.sh

# Production API server
python3 insight_engine/engine.py --serve --api-only --out-dir ../anomaly_reports --fresh

# Person 3 pushes snapshots
curl -X POST http://localhost:8765/ingest/graph -H "Content-Type: application/json" -d @graph.json

# One-shot file test (no server)
python3 insight_engine/engine.py --once path/to/graph.json --out-dir anomaly_reports

# Legacy file watch
python3 insight_engine/engine.py --serve --watch-files --graph-dir ../movement_graphs --out-dir ../anomaly_reports

# 5-minute soak test
./mock/run_soak_test.sh
```

See [`mock/README.md`](../mock/README.md) for full run instructions and Person 5 handoff details.

---

## Integration Notes

These are open questions for Person 3 integration, not design constraints:

- Whether `time_windows` will include per-window `zone_stats` (would improve dwell-trend precision)
- Real traffic levels for threshold calibration
- Exact field names if their schema differs from mock

Fix mismatches in `detection.py` input parsing when real snapshots arrive. Do not degrade the product to match a weaker input — coordinate schema with Person 3 instead.
