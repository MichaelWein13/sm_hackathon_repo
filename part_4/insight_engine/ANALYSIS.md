# Insight Engine — Analysis Math & Implementation

Technical reference for how detection, scoring, and alert lifecycle work in code.
Product spec: [`FINAL_VISION.md`](../FINAL_VISION.md). Architecture overview: [`DESIGN.md`](DESIGN.md).

> **Reading the math:** Display formulas use `$$ ... $$`, inline uses `$ ... $` (renders on GitHub and in most Markdown math previews). Each key formula also has a **Plain** line you can read as source text if LaTeX does not render.

---

## 1. Pipeline overview

Each ingest cycle (one `POST /ingest/graph` or one new file in `movement_graphs/`) runs:

```
snapshot JSON
    │
    ▼
detection.analyze_snapshot()     ← pure math, returns signals dict
    │                              (updates ewma_state, edge_history, zone_history in-place)
    ▼
alert_state.AlertStateManager.update()   ← lifecycle, hysteresis, insight typing
    │
    ▼
narration.generate_message()     ← human-readable strings (LLM or templates)
    │
    ▼
engine writes insights + events + API cache
```

**Module boundaries**

| Module | Stateful? | Role |
|---|---|---|
| `detection.py` | EWMA + edge/zone history passed in | Extract signals from one snapshot |
| `alert_state.py` | Active alerts, cycle counter | Turn signals into persistent alerts |
| `narration.py` | None (LLM client singleton) | Format messages for incident commander |
| `engine.py` | `EngineRuntime` holds all cross-cycle state | I/O, API, orchestration |

Detection never touches the filesystem or LLM. All cross-snapshot memory lives in `engine_state` (EWMA, edge history) and `AlertStateManager` (active alerts).

---

## 2. Input data model

Person 3 sends a **complete graph at time T**, not a delta.

```json
{
  "snapshot_ts": 1718045312000,
  "nodes": ["zone_1", "zone_2"],
  "edges": [
    {
      "from_zone_id": "zone_1",
      "to_zone_id": "zone_2",
      "transition_count": 12,
      "transition_probability": 0.55
    }
  ],
  "zone_stats": {
    "zone_2": { "avg_dwell_ms": 7100, "visit_count": 18 }
  },
  "time_windows": [
    {
      "window_start_ms": 1718045000000,
      "window_end_ms": 1718045300000,
      "window_graph": {
        "nodes": ["zone_1", "zone_2"],
        "edges": [ ... ]
      }
    }
  ]
}
```

### Two time bases (important)

Person 3’s top-level `edges` are **session-cumulative** totals. `time_windows[].window_graph.edges` are **per-period** counts.

The engine deliberately uses **different slices** for different signals:

| Signal | Data source | Time meaning |
|---|---|---|
| Accumulation, EWMA, convergence, cascade | **Latest** `time_window` edges | Recent flow (rate-normalized) |
| Intra-trend | **All** `time_windows` | History within this snapshot |
| Structural `new_edges` | Top-level cumulative `edges` vs `edge_history` | Session topology growth |
| Structural `unexpected` | Latest window edges vs `edge_history` | New rare route in recent period |

If `time_windows` is empty, flow metrics fall back to top-level `edges` with `DEFAULT_WINDOW_S = 300` seconds.

### Preprocessing (`engine._sanitize_graph`)

Zones named `"transition"` (HDBSCAN noise from Person 2) are stripped from `nodes`, `edges`, `zone_stats`, and window graphs before detection runs.

---

## 3. Flow edge selection

```python
flow_edges, duration_s = _flow_edges_and_duration(time_windows, snapshot_edges)
```

- If `time_windows` non-empty: `flow_edges = time_windows[-1].window_graph.edges`
- `duration_s = (window_end_ms - window_start_ms) / 1000`, minimum 60s
- Else: `flow_edges = snapshot_edges`, `duration_s = 300`

All rate calculations use this pair.

---

## 4. Signal 1 — Accumulation (weight 50%)

**File:** `detection.compute_accumulation`

For each zone `z`, sum transition counts on `flow_edges`:

$$
I_z = \sum_{\text{edges into } z} \text{count}, \qquad
O_z = \sum_{\text{edges out of } z} \text{count}
$$

**Plain:** `I_z` = total inbound transitions to zone z; `O_z` = total outbound.

Convert to rates (movements per second):

$$
\dot{I}_z = \frac{I_z}{\Delta t}, \qquad \dot{O}_z = \frac{O_z}{\Delta t}
$$

**Plain:** `inbound_rate = inbound / duration_s` (same for outbound).

**Accumulation ratio** (dimensionless stress proxy):

$$
A_z = \frac{\dot{I}_z}{\dot{O}_z + \varepsilon}, \qquad \varepsilon = 0.01 \text{ mov/s}
$$

**Plain:** `accumulation_ratio = inbound_rate / (outbound_rate + 0.01)`

Physical intuition: if inbound rate consistently exceeds outbound rate, occupancy grows — queue buildup. Related to net flow $N \propto \dot{I} - \dot{O}$, but the ratio form is scale-free when both sides use the same $\Delta t$.

**Score contribution:**

$$
f_{\text{acc}}(z) = \mathrm{clamp}\left(\frac{A_z}{5},\ 0,\ 1\right)
$$

**Plain:** `acc_score = min(1, accumulation_ratio / 5)`

The divisor `5` maps ratio ≥ 5 to maximum stress. Tunable.

**Stored per zone:** `inbound`, `outbound` (raw counts for narration), `inbound_rate`, `outbound_rate`, `duration_s`, `accumulation_ratio`.

---

## 5. Signal 2 — Intra-snapshot trend (weight 25%)

**File:** `detection.compute_intra_trend`

**Requires:** `len(time_windows) >= 2`. Otherwise returns `{}` (no trend signal).

For each zone, build a series of inbound **rates** across windows:

$$
y_k = \frac{\text{inbound count in window } k}{\Delta t_k}
$$

Run ordinary least squares (OLS) with $x_k = 0, 1, \ldots, n-1$:

$$
\hat{\beta} = \frac{\sum (x_k - \bar{x})(y_k - \bar{y})}{\sum (x_k - \bar{x})^2}
$$

$$
R^2 = \frac{\left(\sum (x_k - \bar{x})(y_k - \bar{y})\right)^2}{\sum(x_k-\bar{x})^2 \sum(y_k-\bar{y})^2}
$$

**Plain:** `slope, r_squared = linear_regression(rate_series)`

Normalize slope for scale invariance:

$$
\bar{y} = \frac{1}{n}\sum y_k
$$

$$
\hat{\beta}_{\text{norm}} = \frac{\hat{\beta}}{\bar{y} + \varepsilon}
\quad\text{(when }\bar{y} < 1\text{; else divide by }\bar{y} + 1\text{)}
$$

**Trend score** (only rising trends count):

$$
f_{\text{trend}}(z) =
\begin{cases}
\mathrm{clamp}(\hat{\beta}_{\text{norm}}) \cdot R^2 & \text{if } \hat{\beta}_{\text{norm}} > 0 \\
0 & \text{otherwise}
\end{cases}
$$

**Plain:** `trend_score = normalized_slope * r_squared` (zero if slope ≤ 0)

Multiplying by $R^2$ down-weights noisy spikes: a large slope only matters if the pattern is consistent across windows.

**Score contribution:** $0.25 \cdot f_{\text{trend}}(z)$

---

## 6. Signal 3 — EWMA drift (weight 25%)

**File:** `detection.update_ewma`

Tracks a running baseline of each zone’s **inbound rate** across POST cycles (not within one snapshot).

$$
\mu_t = \alpha \cdot x_t + (1 - \alpha) \cdot \mu_{t-1}, \qquad \alpha = 0.35
$$

where $x_t = \dot{I}_z$ from the latest window this cycle.

**Plain:** `ewma = 0.35 * current_rate + 0.65 * prev_ewma`

**Cold start:** first time zone is seen, $\mu_1 = x_1$, so deviation is 0.

**Relative deviation:**

$$
d_t = \mathrm{clamp}\left(\frac{x_t - \mu_t}{\mu_t + \varepsilon},\ -1,\ 1\right)
$$

**Plain:** `deviation_score = (current_rate - ewma) / (ewma + 0.01)`

**Score contribution:** $0.25 \cdot \mathrm{clamp}(d_t,\ 0,\ 1)$

Only positive deviations increase urgency. Negative deviations (recovery) do not reduce the composite score directly — de-escalation is handled by alert lifecycle (Section 8).

**Memory:** effective half-life $\approx \ln(0.5)/\ln(1-\alpha) \approx 1.5$ snapshots at $\alpha=0.35$.

State stored in `engine_state["ewma"]`: `dict[zone_id → float]`.

---

## 7. Signal 4 — Dwell multiplier

**File:** `detection.compute_urgency` (dwell block)

If zone dwell exceeds session median:

$$
r_{\text{dwell}} = \min\left(\frac{\text{avg\_dwell\_ms}_z}{\text{median dwell}},\ 3\right)
$$

$$
m_{\text{dwell}} = 1 + 0.2 \cdot (r_{\text{dwell}} - 1)
$$

**Plain:** `urgency *= 1 + 0.2 * (min(dwell/median, 3) - 1)` when dwell > median

Maximum boost: 40% at 3× median dwell. Median is computed across all zones in `zone_stats` for this snapshot.

---

## 8. Composite urgency score

**File:** `detection.compute_urgency`

$$
U_z = \mathrm{clamp}\left(
  m_{\text{dwell}} \cdot \left(
    0.50 \cdot f_{\text{acc}} +
    0.25 \cdot f_{\text{trend}} +
    0.25 \cdot f_{\text{ewma}}
  \right)
\right)
$$

**Plain:**

```
U = clamp( dwell_multiplier * (0.5*acc_score + 0.25*trend_score + 0.25*ewma_score) )
```

$U_z \in [0, 1]$. This is the primary continuous output per zone.

**Note:** `confidence` on emitted alerts is currently set equal to $U_z$ (or structural confidence for edge alerts). These are conflated in the implementation — a known limitation if true statistical confidence is needed later.

---

## 9. Cross-zone signals

### 9.1 Convergence

**File:** `detection.compute_convergence`

A destination zone $d$ is flagged if it receives inbound edges from ≥ 2 sources where each edge’s rate meets:

$$
\frac{\text{count}}{\Delta t} \geq 0.015 \text{ mov/s}
$$

(~4.5 movements in a 5-minute window).

**Plain:** `edge_rate = transition_count / duration_s`; need ≥ 2 feeders each ≥ 0.015 mov/s

In `alert_state`, convergence boosts the downstream zone’s bottleneck alert:

$$
U_{\text{conv}} = \mathrm{clamp}(U_{\text{zone}} \times 1.15)
$$

### 9.2 Cascade risk

**File:** `detection.compute_cascade_risk`

For each zone $a$ with $U_a \geq 0.45$:

1. Find outbound edge with highest `transition_probability`
2. Let destination be $b$
3. If $U_b \geq 0.45$, emit cascade pair $(a \to b)$

Alert attaches to downstream zone with:

$$
U_{\text{cascade}} = \mathrm{clamp}\left(\frac{U_a + U_b}{2} \times 1.2\right)
$$

### 9.3 Structural changes

**File:** `detection.compute_structural_changes`

| Pattern | Rule | Alert type |
|---|---|---|
| **New edge** | `(from, to)` in cumulative edges but not in `edge_history` | `anomaly` |
| **Unexpected transition** | In `flow_edges`: `p < 0.05`, `count ≥ 2`, edge not in `edge_history` | `unexpected_transition` |
| **Isolation** | Zone present in ≥ 60% of prior snapshots but absent from `nodes` now (after ≥ 3 prior snapshots) | `anomaly` |

`edge_history` is updated every cycle with the full cumulative edge set (for session memory).
`unexpected` deliberately probes **latest window only** — not cumulative edges — to avoid flagging normal sparse topology on large buildings.

**Warm-up:** structural alerts are suppressed in `alert_state` while `cycle ≤ STRUCTURAL_WARMUP_CYCLES` (default 2). History still seeds; alerts wait.

---

## 10. Alert state machine

**File:** `alert_state.AlertStateManager`

### 10.1 Building active alerts each cycle

The manager collects candidate `(alert_id → urgency)` pairs:

1. **Zone-level:** each zone with $U_z \geq 0.20$ and a selected `insight_type`
2. **Structural / cross-zone:** after warm-up, from structural, cascade, convergence passes

Alert ID format: `{zone_id}__{insight_type}` (e.g. `zone_3__congestion_forecast`).

Candidates below `SEVERITY_THRESHOLDS["detecting"]` (0.20) are dropped.

### 10.2 Insight type selection

**File:** `alert_state._select_insight_type` — one type per zone-level alert, priority order:

| Priority | Type | Condition |
|---|---|---|
| 1 | `congestion_forecast` | `trend_score > 0.25` AND $U \geq 0.42$ |
| 2 | `bottleneck_risk` | `accumulation_ratio > 2.5` AND $U \geq 0.42$ |
| 3 | `high_dwell_zone` | `dwell > 2×` session median |
| 4 | `anomaly` | $U \geq 0.20$ |

Structural types (`unexpected_transition`, new-route `anomaly`) are raised separately and can coexist on the same zone.

### 10.3 Severity ladder

Displayed severity is **not** equal to raw urgency on first sighting.

```
detecting → warning → critical → resolving → resolved
```

**Target severity** from urgency (instantaneous plant output):

| Urgency | Target |
|---|---|
| $U \geq 0.68$ | critical |
| $U \geq 0.42$ | warning |
| $U \geq 0.20$ | detecting |
| else | none |

**Controller rules (`_apply_hysteresis`):**

- **New alerts always enter at `detecting`** — even if $U = 0.80$
- **Escalation:** one rung at a time; requires `CYCLES_TO_ESCALATE = 2` consecutive cycles where target supports the next rung
- **Faster re-escalation:** if `prior_max_severity` already reached the next rung, only 1 cycle needed
- **De-escalation (while active):** one rung down after `CYCLES_TO_DEESCALATE = 3` cycles below target
- **Absent from active set:** → `resolving` immediately; after 3 absent cycles → `resolved` event, removed from state

Minimum time from birth to `critical`: 4 snapshots (detecting → warning → critical, 2 cycles each step).

### 10.4 Events emitted

| Event | When |
|---|---|
| `new` | Alert ID first appears |
| `escalated` | Severity moves up |
| `de_escalated` | Severity moves down (including → resolving) |
| `updated` | Same severity, new data |
| `resolved` | Removed after absent streak |

---

## 11. Cross-cycle state

**In `engine_state` (per `EngineRuntime`):**

```python
{
    "ewma":            dict[str, float],   # zone → inbound rate baseline
    "edge_history":    set[tuple],         # (from, to) edges ever seen
    "zone_history":    dict[str, int],     # zone → snapshot appearance count
    "total_snapshots": int,                # cycles processed
}
```

**In `AlertStateManager`:**

```python
{
    "active_alerts": dict[str, Alert],  # keyed by alert_id
    "cycle": int,                       # ingest count
    "start_ts": int,                    # for elapsed_seconds in summary
}
```

---

## 12. Tunable constants

### `detection.py`

| Constant | Default | Effect |
|---|---|---|
| `DEFAULT_WINDOW_S` | 300 | Fallback window duration |
| `RATE_EPS` | 0.01 | Floor in rate ratios (mov/s) |
| `EWMA_ALPHA` | 0.35 | Baseline responsiveness |
| `MIN_UNEXPECTED_COUNT` | 2 | Min transitions for rare-route alert |
| `MAX_UNEXPECTED_PROB` | 0.05 | Probability ceiling for “unexpected” |
| `MIN_CONVERGENCE_RATE` | 0.015 | Min mov/s per feeder edge |
| Accumulation divisor | 5 | Maps ratio to $f_{\text{acc}}$ |
| Urgency weights | 50 / 25 / 25 | acc / trend / ewma |
| Cascade threshold | 0.45 | Min urgency for cascade scan |

### `alert_state.py`

| Constant | Default | Effect |
|---|---|---|
| `SEVERITY_THRESHOLDS` | 0.20 / 0.42 / 0.68 | detecting / warning / critical |
| `CYCLES_TO_ESCALATE` | 2 | Hysteresis up |
| `CYCLES_TO_DEESCALATE` | 3 | Hysteresis down |
| `STRUCTURAL_WARMUP_CYCLES` | 2 | Suppress structural alerts early |
| Convergence boost | ×1.15 | Convergence urgency injection |
| Cascade boost | ×1.2 on mean | Cascade urgency injection |
| Structural confidence | `1 - 15p` | Unexpected transition confidence |

Calibrate thresholds against Person 3’s real POST stream during integration.

---

## 13. Narration inputs

**File:** `narration._build_context`

Messages use raw counts from `accumulation` (latest window), not rates — so “32 inbound movements” is readable.

Derived fields:

- `traffic_growth_x = series[-1] / series[0]` (rate series from trend)
- `elapsed_seconds` from `snapshot_ts - alert.first_seen_ts`
- `_capacity_pct(acc_ratio)` in templates: `min(95, int(acc_ratio / 5 * 100))` — cosmetic mapping from the same `/5` saturation, not physical capacity

Set `NARRATION_BACKEND=disabled` for deterministic template messages in demo.

---

## 14. Implementation file map

```
insight_engine/
  detection.py      Signals 1–6, composite U, analyze_snapshot()
  alert_state.py    Lifecycle, hysteresis, insight typing, events
  narration.py      generate_message(), templates, optional LLM
  engine.py         EngineRuntime, REST API, _sanitize_graph, I/O
  ANALYSIS.md       ← this document
  DESIGN.md         Architecture + contracts
```

**Key functions to read when debugging:**

| Question | Start here |
|---|---|
| Why is zone X urgent? | `compute_urgency`, check `accumulation`, `intra_trend`, `ewma_deviations` in signals dict |
| Why this insight type? | `_select_insight_type` |
| Why didn’t it escalate? | `_apply_hysteresis`, `CYCLES_TO_ESCALATE` |
| Why flood of anomalies on startup? | `STRUCTURAL_WARMUP_CYCLES`, `edge_history` seeding |
| Why no trend? | `len(time_windows) < 2` |
| Wrong counts in message? | `_build_context` ← `accumulation` from latest window |

---

## 15. Assumptions and limitations

**Assumptions**

- Person 3 POSTs complete snapshots with monotonically growing `time_windows` over a session
- Window timestamps are accurate; equal window durations make trend regression comparable
- `transition_probability` from Person 3 is meaningful (count / outgoing total per zone)
- Zone IDs are stable within a session (re-clustering breaks EWMA and history)

**Known limitations**

- No absolute capacity model — urgency is relative to session baselines and ratios
- `confidence` equals urgency — not a separate statistical measure
- Negative EWMA/trend does not reduce composite $U$ — only lifecycle handles recovery
- Structural `p < 0.05` is a heuristic, not information-theoretic surprise
- Top-level cumulative edges are used only for `new_edges` / history; flow uses latest window — intentional but requires Person 3 to populate windows correctly

**Minimum data for full model**

| Channel | Minimum |
|---|---|
| Trend | ≥ 2 `time_windows` |
| EWMA drift | ≥ 2 POSTs with changing rates |
| Isolation | ≥ 3 prior snapshots |
| Structural alerts | cycle > 2 |
| Congestion forecast | trend + $U \geq 0.42$ |

---

## 16. Worked example (mock peak, zone_3)

At mock step 11 (congestion peak), approximate values:

| Component | Value |
|---|---|
| Latest window inbound / outbound | 40 / 0 |
| $\dot{I}$, $\dot{O}$ | 0.133, 0 mov/s |
| $A_z = \dot{I}/(\dot{O}+\varepsilon)$ | ~13.3 |
| $f_{\text{acc}}$ | 1.0 (capped) |
| `trend_score` | ~0.48 |
| `ewma deviation` | ~0.44 |
| Base $U$ | $0.5 + 0.12 + 0.11 \approx 0.73$ |
| Alert type | `congestion_forecast` |
| After hysteresis | `critical` by step 11 |

At Person 3 static graph (28 zones, 1 window, balanced hub traffic): $U_{\max} \approx 0.13$, 0 alerts after warm-up — expected until windows accumulate and traffic imbalances develop.
