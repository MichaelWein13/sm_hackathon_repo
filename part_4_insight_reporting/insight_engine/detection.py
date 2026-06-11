"""
Detection engine — pure functions that extract signals from a graph snapshot.

Each function returns a dict of per-zone or per-edge signals.
Nothing here touches state, filesystem, or LLM — all side-effect-free.
"""

from collections import defaultdict


# ---------------------------------------------------------------------------
# Tunable detection constants
# ---------------------------------------------------------------------------

DEFAULT_WINDOW_S        = 300.0   # fallback if window timestamps missing (5 min)
RATE_EPS                = 0.01    # movements/s floor in accumulation denominator
MIN_UNEXPECTED_COUNT    = 2       # rare edges need repeated observations
MAX_UNEXPECTED_PROB     = 0.05
MIN_CONVERGENCE_RATE    = 0.015   # movements/s per inbound edge (~4.5 per 5-min window)


# ---------------------------------------------------------------------------
# Internal math helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _window_duration_s(window: dict) -> float:
    """Window span in seconds; falls back to DEFAULT_WINDOW_S."""
    start = window.get("window_start_ms")
    end   = window.get("window_end_ms")
    if start is not None and end is not None and end > start:
        return max((end - start) / 1000.0, 60.0)
    return DEFAULT_WINDOW_S


def _flow_edges_and_duration(
    time_windows: list[dict],
    snapshot_edges: list[dict],
) -> tuple[list[dict], float]:
    """
    Edges and duration for rate-based flow metrics.

    Prefer the latest time_window (recent activity). Person 3's top-level edges
    are session-cumulative; window edges reflect the current period.
    """
    if time_windows:
        latest = time_windows[-1]
        edges  = latest.get("window_graph", {}).get("edges", [])
        return edges, _window_duration_s(latest)
    return snapshot_edges, DEFAULT_WINDOW_S


def _linear_regression(ys: list[float]) -> tuple[float, float]:
    """
    Returns (slope, r_squared) for equally-spaced y values.
    slope > 0 means rising. r_squared near 1 means clean trend.
    """
    n = len(ys)
    if n < 2:
        return 0.0, 0.0

    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    ss_xy = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    ss_xx = sum((xs[i] - x_mean) ** 2 for i in range(n))
    ss_yy = sum((ys[i] - y_mean) ** 2 for i in range(n))

    if ss_xx == 0:
        return 0.0, 0.0

    slope = ss_xy / ss_xx
    r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0
    return slope, _clamp(r_squared)


# ---------------------------------------------------------------------------
# Signal 1: Accumulation ratio (inflow vs outflow per zone)
# ---------------------------------------------------------------------------

def compute_accumulation(edges: list[dict], duration_s: float = DEFAULT_WINDOW_S) -> dict[str, dict]:
    """
    For each zone, computes inbound/outbound counts and rates (movements/s).

    accumulation_ratio = inbound_rate / (outbound_rate + RATE_EPS)

    Rate normalization makes scores comparable across window sizes and
    POST cadences. Raw counts are retained for narration.
    """
    inbound: dict[str, int] = defaultdict(int)
    outbound: dict[str, int] = defaultdict(int)

    for edge in edges:
        inbound[edge["to_zone_id"]]   += edge["transition_count"]
        outbound[edge["from_zone_id"]] += edge["transition_count"]

    duration_s = max(duration_s, 60.0)
    all_zones = set(inbound) | set(outbound)
    result = {}
    for zone in all_zones:
        ib = inbound.get(zone, 0)
        ob = outbound.get(zone, 0)
        ib_rate = ib / duration_s
        ob_rate = ob / duration_s
        result[zone] = {
            "inbound":            ib,
            "outbound":           ob,
            "inbound_rate":       round(ib_rate, 4),
            "outbound_rate":      round(ob_rate, 4),
            "duration_s":         round(duration_s, 1),
            "accumulation_ratio": ib_rate / (ob_rate + RATE_EPS),
        }
    return result


# ---------------------------------------------------------------------------
# Signal 2: Intra-snapshot trend (across time_windows within one snapshot)
# ---------------------------------------------------------------------------

def compute_intra_trend(time_windows: list[dict]) -> dict[str, dict]:
    """
    For each zone, computes the linear regression slope and R² of its
    inbound traffic series across the snapshot's time_windows.

    trend_score = normalized_slope * r_squared
    Only positive trends (rising traffic) produce a non-zero score.
    """
    if len(time_windows) < 2:
        return {}

    # Build per-zone inbound rate series (movements/s) across windows
    zone_series: dict[str, list[float]] = defaultdict(list)
    for window in time_windows:
        edges = window.get("window_graph", {}).get("edges", [])
        duration_s = _window_duration_s(window)
        window_inbound: dict[str, int] = defaultdict(int)
        for edge in edges:
            window_inbound[edge["to_zone_id"]] += edge["transition_count"]

        # All zones seen so far need an entry (0 if absent this window)
        all_seen = set(zone_series) | set(window_inbound)
        for zone in all_seen:
            rate = window_inbound.get(zone, 0) / duration_s
            zone_series[zone].append(rate)

    result = {}
    for zone, series in zone_series.items():
        slope, r_sq = _linear_regression(series)
        mean_traffic = sum(series) / len(series) if series else 1.0
        # Rate series are << 1 mov/s — use RATE_EPS, not +1 (count-scale)
        slope_denom = (mean_traffic + RATE_EPS) if mean_traffic < 1.0 else (mean_traffic + 1.0)
        normalized_slope = slope / slope_denom
        trend_score = _clamp(normalized_slope) * r_sq if normalized_slope > 0 else 0.0
        result[zone] = {
            "series":           series,
            "slope":            slope,
            "r_squared":        round(r_sq, 3),
            "normalized_slope": round(normalized_slope, 3),
            "trend_score":      round(trend_score, 3),
        }
    return result


# ---------------------------------------------------------------------------
# Signal 3: Cross-snapshot EWMA drift (requires history from prior snapshots)
# ---------------------------------------------------------------------------

EWMA_ALPHA = 0.35  # Weight given to the latest snapshot vs running average


def update_ewma(
    current_inbound: dict[str, float],
    previous_ewma: dict[str, float],
) -> tuple[dict[str, float], dict[str, dict]]:
    """
    Updates the EWMA baseline for each zone given current inbound rates (mov/s).

    deviation_score = (current - ewma) / (ewma + RATE_EPS)
    Positive score = above baseline (worsening).
    Negative score = below baseline (recovering or quiet).
    """
    new_ewma: dict[str, float] = {}
    deviations: dict[str, dict] = {}

    all_zones = set(current_inbound) | set(previous_ewma)
    for zone in all_zones:
        current = float(current_inbound.get(zone, 0))
        prev    = previous_ewma.get(zone, current)  # First time: baseline = current
        ewma    = EWMA_ALPHA * current + (1 - EWMA_ALPHA) * prev
        new_ewma[zone] = ewma
        deviation = (current - ewma) / (ewma + RATE_EPS)
        deviations[zone] = {
            "current":        round(current, 4),
            "ewma_baseline":  round(ewma, 4),
            "deviation_score": round(_clamp(deviation, -1.0, 1.0), 3),
        }

    return new_ewma, deviations


# ---------------------------------------------------------------------------
# Signal 4: Graph structure change (new/disappeared edges and zones)
# ---------------------------------------------------------------------------

def compute_structural_changes(
    current_snapshot: dict,
    previous_edge_history: set[tuple],
    previous_zone_history: dict[str, int],  # zone_id -> windows_present count
    total_prior_snapshots: int,
    recent_edges: list[dict] | None = None,
) -> dict:
    """
    Detects:
    - New edges that weren't seen in any prior snapshot
    - Low-probability edges that fired recently (unexpected_transition)
    - Zones that used to be active but have gone quiet

    unexpected_transition uses recent_edges (latest window), not session-cumulative
    totals — avoids flagging normal sparse topology on large graphs.
    """
    current_edges = {
        (e["from_zone_id"], e["to_zone_id"]): e
        for e in current_snapshot.get("edges", [])
    }
    current_edge_set = set(current_edges)
    current_zones    = set(current_snapshot.get("nodes", []))

    new_edges = [
        current_edges[key]
        for key in (current_edge_set - previous_edge_history)
    ]

    probe_edges = recent_edges if recent_edges is not None else current_snapshot.get("edges", [])
    unexpected_transitions = [
        e for e in probe_edges
        if e.get("transition_probability", 1.0) < MAX_UNEXPECTED_PROB
        and e["transition_count"] >= MIN_UNEXPECTED_COUNT
        and (e["from_zone_id"], e["to_zone_id"]) not in previous_edge_history
    ]

    # Zones that were active before but now absent
    isolated_zones = []
    if total_prior_snapshots >= 3:
        for zone, count in previous_zone_history.items():
            presence_ratio = count / total_prior_snapshots
            if presence_ratio >= 0.6 and zone not in current_zones:
                isolated_zones.append({
                    "zone_id":        zone,
                    "presence_ratio": round(presence_ratio, 2),
                })

    return {
        "new_edges":              new_edges,
        "unexpected_transitions": unexpected_transitions,
        "isolated_zones":         isolated_zones,
        "current_edge_set":       current_edge_set,
        "current_zones":          current_zones,
    }


# ---------------------------------------------------------------------------
# Signal 5: Cross-zone cascade risk
# ---------------------------------------------------------------------------

def compute_cascade_risk(
    edges: list[dict],
    zone_urgency: dict[str, float],
    warning_threshold: float = 0.45,
) -> list[dict]:
    """
    For each zone at or above warning_threshold urgency, follows its
    highest-probability outbound edge. If the destination is also at or above
    warning_threshold, flags a cascade risk pair.
    """
    # Build outbound edge map
    best_outbound: dict[str, dict] = {}
    for edge in edges:
        zone = edge["from_zone_id"]
        if zone not in best_outbound or \
           edge["transition_probability"] > best_outbound[zone]["transition_probability"]:
            best_outbound[zone] = edge

    cascades = []
    for zone_a, urgency_a in zone_urgency.items():
        if urgency_a < warning_threshold:
            continue
        edge = best_outbound.get(zone_a)
        if not edge:
            continue
        zone_b   = edge["to_zone_id"]
        urgency_b = zone_urgency.get(zone_b, 0.0)
        if urgency_b >= warning_threshold:
            cascades.append({
                "from_zone":    zone_a,
                "to_zone":      zone_b,
                "urgency_a":    round(urgency_a, 3),
                "urgency_b":    round(urgency_b, 3),
                "via_probability": edge["transition_probability"],
            })

    return cascades


# ---------------------------------------------------------------------------
# Signal 6: Convergence (multiple high-traffic sources feeding one zone)
# ---------------------------------------------------------------------------

def compute_convergence(
    edges: list[dict],
    zone_urgency: dict[str, float],
    duration_s: float = DEFAULT_WINDOW_S,
    min_sources: int = 2,
    min_inbound_rate: float = MIN_CONVERGENCE_RATE,
) -> list[dict]:
    """
    Finds zones that are the destination of multiple high-traffic edges
    simultaneously — a convergence point that may be overwhelmed even if
    its own urgency score isn't yet critical.
    """
    duration_s = max(duration_s, 60.0)
    inbound_edges: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        rate = edge["transition_count"] / duration_s
        if rate >= min_inbound_rate:
            inbound_edges[edge["to_zone_id"]].append(edge)

    convergences = []
    for zone, sources in inbound_edges.items():
        if len(sources) >= min_sources:
            total_inbound = sum(e["transition_count"] for e in sources)
            convergences.append({
                "zone_id":       zone,
                "source_count":  len(sources),
                "total_inbound": total_inbound,
                "sources":       [e["from_zone_id"] for e in sources],
                "current_urgency": round(zone_urgency.get(zone, 0.0), 3),
            })

    return convergences


# ---------------------------------------------------------------------------
# Composite urgency score per zone
# ---------------------------------------------------------------------------

def compute_urgency(
    zone_id: str,
    accumulation: dict,
    intra_trend: dict,
    ewma_deviation: dict,
    zone_stats: dict,
    all_dwell_values: list[float],
) -> float:
    """
    Combines all signals into a single urgency score [0, 1].

    Weights (tunable):
      50% accumulation ratio — if people cannot leave, that IS the crisis
      25% trend score (slope * R²) — worsening trajectory amplifies urgency
      25% EWMA deviation — how far current load exceeds the running baseline

    Dwell time is used as a multiplier: a zone where people linger long
    under congestion is more urgent than one with fast throughput.
    """
    acc    = accumulation.get(zone_id, {})
    trend  = intra_trend.get(zone_id, {})
    ewma   = ewma_deviation.get(zone_id, {})
    stats  = zone_stats.get(zone_id, {})

    acc_score   = _clamp(acc.get("accumulation_ratio", 0) / 5.0)
    trend_score = _clamp(trend.get("trend_score", 0.0))
    ewma_score  = _clamp(ewma.get("deviation_score", 0.0))

    base = (
        0.50 * acc_score +
        0.25 * trend_score +
        0.25 * ewma_score
    )

    # Dwell amplifier: if this zone's dwell is above median, boost urgency
    dwell = stats.get("avg_dwell_ms", 0)
    if all_dwell_values:
        sorted_dwells = sorted(all_dwell_values)
        median_dwell  = sorted_dwells[len(sorted_dwells) // 2]
        if median_dwell > 0 and dwell > median_dwell:
            dwell_ratio = min(dwell / median_dwell, 3.0)  # cap at 3×
            base = base * (1.0 + 0.2 * (dwell_ratio - 1.0))

    return round(_clamp(base), 3)


# ---------------------------------------------------------------------------
# Entry point: analyze one snapshot, given cross-snapshot state
# ---------------------------------------------------------------------------

def analyze_snapshot(
    snapshot: dict,
    ewma_state: dict[str, float],
    edge_history: set[tuple],
    zone_history: dict[str, int],
    total_prior_snapshots: int,
) -> dict:
    """
    Runs all detection signals on a single graph snapshot.
    Returns a signals dict consumed by the alert state manager.

    ewma_state, edge_history, zone_history are updated in-place.
    """
    snapshot_edges = snapshot.get("edges", [])
    zone_stats     = snapshot.get("zone_stats", {})
    time_windows   = snapshot.get("time_windows", [])
    nodes          = set(snapshot.get("nodes", []))

    flow_edges, duration_s = _flow_edges_and_duration(time_windows, snapshot_edges)

    accumulation = compute_accumulation(flow_edges, duration_s)
    intra_trend  = compute_intra_trend(time_windows)

    current_inbound_rates = {
        z: accumulation.get(z, {}).get("inbound_rate", 0.0) for z in nodes
    }
    new_ewma, ewma_deviations = update_ewma(current_inbound_rates, ewma_state)
    ewma_state.update(new_ewma)

    structural = compute_structural_changes(
        snapshot, edge_history, zone_history, total_prior_snapshots,
        recent_edges=flow_edges,
    )
    edge_history.update(structural["current_edge_set"])
    for zone in structural["current_zones"]:
        zone_history[zone] = zone_history.get(zone, 0) + 1

    all_dwell_values = [
        s["avg_dwell_ms"] for s in zone_stats.values()
        if "avg_dwell_ms" in s
    ]

    zone_urgency = {
        zone: compute_urgency(
            zone, accumulation, intra_trend, ewma_deviations,
            zone_stats, all_dwell_values
        )
        for zone in (set(zone_stats) | nodes)
    }

    cascades    = compute_cascade_risk(flow_edges, zone_urgency)
    convergence = compute_convergence(flow_edges, zone_urgency, duration_s)

    return {
        "zone_urgency":           zone_urgency,
        "accumulation":           accumulation,
        "intra_trend":            intra_trend,
        "ewma_deviations":        ewma_deviations,
        "structural":             structural,
        "cascades":               cascades,
        "convergence":            convergence,
        "zone_stats":             zone_stats,
        "nodes":                  sorted(nodes),
        "flow_duration_s":        round(duration_s, 1),
        "snapshot_ts":            snapshot.get("snapshot_ts", 0),
    }
