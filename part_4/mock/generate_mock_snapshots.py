"""
Mock snapshot generator — FloorFlow demo scenario.

Multi-sector chaos demo (~30 steps): overlapping crises in different zones
instead of one long corridor meltdown. Traffic is shaped so the engine fires
a mix of insight types across zone_1..zone_5.

  Steps  0-2   Calm start; medical staging (zone_2) begins filling
  Steps  3-6   zone_4 appears; rare route zone_4→zone_1 (unexpected_transition)
  Steps  5-12  zone_2 staging overload (high_dwell + accumulation) — parallel track
  Steps  8-14  zone_3 food-hall surge (congestion + bottleneck) — resolves early
  Steps 14-20  zone_4 practice-room convergence (bottleneck + evac from zone_2)
  Steps 16-23  zone_5 aquarium opens; crowding + rare zone_5→zone_1 route
  Steps 25-26  zone_4 briefly offline (isolation anomaly)
  Steps 27-29  staggered stand-down across sectors

Run:
  python3 mock/generate_mock_snapshots.py --api-url http://127.0.0.1:8765/ingest/graph
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "movement_graphs"
DEFAULT_INTERVAL_S = 3.0
DEFAULT_STEPS    = 30
WINDOW_MS        = 300_000

# ---------------------------------------------------------------------------
# Per-step traffic tables (index = step, 0-based)
# ---------------------------------------------------------------------------

Z1_TO_Z3 = [
    2,  2,  2,  3,  4,  5,  6,  7,  8, 10, 12, 14, 12,
    8,  5,  3,  2,  2,  2,  2,  2,  2,  2,  2,  2,  2,
    2,  2,  2,  2,
]

Z4_TO_Z3 = [
    0,  0,  0,  0,  0,  0,  0,  0,  4,  6,  8,  6,  4,
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  0,
]

Z4_TO_Z1 = [
    0,  0,  0,  2,  2,  2,  2,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  0,
]

Z1_TO_Z2 = [
    6,  7,  8,  9, 10, 11, 12, 14, 16, 18, 20, 18, 16,
   14, 16, 18, 20, 22, 20, 16, 12, 10,  9,  8,  8,  8,
    8,  8,  8,  8,
]

Z2_TO_Z1 = [
    4,  4,  3,  3,  3,  2,  2,  2,  2,  1,  1,  1,  2,
    2,  1,  1,  0,  1,  2,  3,  4,  4,  4,  4,  4,  4,
    4,  4,  4,  4,
]

Z2_TO_Z4 = [
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    3,  4,  5,  7,  8,  6,  4,  2,  0,  0,  0,  0,  0,
    0,  0,  0,  0,
]

Z1_TO_Z4 = [
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    4,  6,  8, 10, 12, 10,  8,  4,  0,  0,  0,  0,  0,
    0,  0,  0,  0,
]

Z4_TO_Z2 = [
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  1,  1,  1,  1,  1,  0,  0,  0,  0,  0,
    0,  0,  0,  0,
]

Z1_TO_Z5 = [
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  2,  4,  6,  8, 10,  8,  6,  4,  2,  0,
    0,  0,  0,  0,
]

Z5_TO_Z1 = [
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  0,  0,  0,  3,  3,  3,  2,  0,  0,  0,
    0,  0,  0,  0,
]

Z5_TO_Z3 = [
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
    0,  0,  0,  0,  0,  2,  3,  4,  3,  2,  0,  0,  0,
    0,  0,  0,  0,
]

Z2_DWELL_MS = [
    20_000, 21_000, 22_000, 23_000, 24_000, 26_000, 28_000, 30_000,
    32_000, 34_000, 36_000, 38_000, 40_000, 42_000, 44_000, 46_000,
    48_000, 50_000, 48_000, 42_000, 36_000, 32_000, 28_000, 26_000,
    24_000, 22_000, 20_000, 20_000, 20_000, 20_000,
]

Z4_FIRST_STEP = 3
Z5_FIRST_STEP = 16
Z4_ABSENT_STEPS = {25, 26}


def _phase(step: int) -> int:
    return step % len(Z1_TO_Z3)


def _at(table: list, step: int) -> int:
    return table[_phase(step)]


def _z4_active(step: int) -> bool:
    return step >= Z4_FIRST_STEP and step not in Z4_ABSENT_STEPS


def _z5_active(step: int) -> bool:
    return step >= Z5_FIRST_STEP


def _nodes_for(step: int) -> list[str]:
    nodes = ["zone_1", "zone_2", "zone_3"]
    if _z4_active(step):
        nodes.append("zone_4")
    if _z5_active(step):
        nodes.append("zone_5")
    return nodes


def _z3_outbound(step: int, total_inbound: int) -> int:
    p = _phase(step)
    if 12 <= p <= 14:
        return max(total_inbound, 2)
    if 8 <= p <= 11:
        return max(total_inbound // 7, 1)
    return max(total_inbound // 3, 1)


def _z4_outbound(step: int, total_inbound: int) -> int:
    """Practice room traps traffic during convergence (steps 14-20)."""
    p = _phase(step)
    if 14 <= p <= 20:
        return max(total_inbound // 10, 1)
    return max(total_inbound // 2, 1)


def _z3_dwell_ms(step: int) -> int:
    p = _phase(step)
    if p <= 11:
        return 1_500 + p * 600
    return max(8_100 - (p - 11) * 1_200, 1_500)


def _z5_dwell_ms(step: int) -> int:
    if not _z5_active(step):
        return 2_000
    p = _phase(step)
    if p <= 19:
        return 2_000 + (p - 16) * 900
    return max(5_600 - (p - 19) * 800, 2_000)


def _edges_for(step: int) -> list[dict]:
    """Build all flow edges for a given step (used in snapshot + time_windows)."""
    z4_active = _z4_active(step)
    z5_active = _z5_active(step)
    edges: list[dict] = []

    def add(from_z: str, to_z: str, count: int, prob: float):
        if count > 0:
            edges.append({
                "from_zone_id":          from_z,
                "to_zone_id":            to_z,
                "transition_count":      count,
                "transition_probability": prob,
            })

    z1_z3 = _at(Z1_TO_Z3, step)
    add("zone_1", "zone_3", z1_z3, min(0.30 + _phase(step) * 0.025, 0.65))

    z3_in = z1_z3 + (_at(Z4_TO_Z3, step) if z4_active else 0)
    add("zone_3", "zone_1", _z3_outbound(step, z3_in), 0.15)

    add("zone_1", "zone_2", _at(Z1_TO_Z2, step), 0.28)
    add("zone_2", "zone_1", _at(Z2_TO_Z1, step), 0.18)

    if z4_active:
        add("zone_4", "zone_1", _at(Z4_TO_Z1, step), 0.03)
        z4_z3 = _at(Z4_TO_Z3, step)
        if z4_z3 > 0:
            add("zone_4", "zone_3", z4_z3, 0.04 if _phase(step) == 9 else 0.18)

        z4_in = _at(Z1_TO_Z4, step) + _at(Z2_TO_Z4, step) + z4_z3
        add("zone_4", "zone_2", _z4_outbound(step, z4_in), 0.12)
        add("zone_2", "zone_4", _at(Z2_TO_Z4, step), 0.03)
        add("zone_1", "zone_4", _at(Z1_TO_Z4, step), 0.22)

    if z5_active:
        add("zone_1", "zone_5", _at(Z1_TO_Z5, step), 0.24)
        add("zone_5", "zone_1", _at(Z5_TO_Z1, step), 0.03)
        add("zone_5", "zone_3", _at(Z5_TO_Z3, step), 0.16)

    return edges


def make_snapshot(step: int, base_ts: int) -> dict:
    nodes = _nodes_for(step)
    edges = _edges_for(step)

    windows = []
    for w in range(max(0, step - 2), step + 1):
        windows.append({
            "window_start_ms": base_ts + w * WINDOW_MS,
            "window_end_ms":   base_ts + (w + 1) * WINDOW_MS,
            "window_graph": {
                "nodes": _nodes_for(w),
                "edges": _edges_for(w),
            },
        })

    z3_in = _at(Z1_TO_Z3, step) + (_at(Z4_TO_Z3, step) if _z4_active(step) else 0)

    zone_stats = {
        "zone_1": {"avg_dwell_ms": 3_000, "visit_count": 18 + step},
        "zone_2": {
            "avg_dwell_ms": _at(Z2_DWELL_MS, step),
            "visit_count":  10 + _at(Z1_TO_Z2, step),
        },
        "zone_3": {
            "avg_dwell_ms": _z3_dwell_ms(step),
            "visit_count":  z3_in + 5,
        },
    }
    if _z4_active(step):
        zone_stats["zone_4"] = {
            "avg_dwell_ms": 1_800 + _phase(step) * 100,
            "visit_count":  4 + _at(Z1_TO_Z4, step) + _at(Z2_TO_Z4, step),
        }
    if _z5_active(step):
        zone_stats["zone_5"] = {
            "avg_dwell_ms": _z5_dwell_ms(step),
            "visit_count":  3 + _at(Z1_TO_Z5, step) + _at(Z5_TO_Z3, step),
        }

    return {
        "snapshot_ts":  base_ts + step * WINDOW_MS,
        "nodes":        nodes,
        "edges":        edges,
        "zone_stats":   zone_stats,
        "time_windows": windows,
    }


def _step_note(step: int) -> str:
    notes = {
        3:  "z4 online",
        5:  "staging↑",
        8:  "food-hall↑ + staging",
        14: "practice-room convergence",
        16: "aquarium online",
        20: "z5 rare route",
        25: "z4 offline",
    }
    return notes.get(step, "")


def _post_snapshot(api_url: str, snapshot: dict) -> dict:
    data = json.dumps(snapshot).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="FloorFlow mock graph snapshot generator")
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--out-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--api-url", default=None)
    args = parser.parse_args()

    out_dir = args.out_dir
    if not args.api_url:
        out_dir.mkdir(parents=True, exist_ok=True)

    base_ts = int(time.time() * 1000)
    if args.api_url:
        print(f"POSTing {args.steps} snapshots to {args.api_url}")
    else:
        print(f"Writing {args.steps} snapshots to {out_dir}/")
    print(f"Interval: {args.interval}s per step  |  Ctrl-C to stop\n")

    for step in range(args.steps):
        snapshot = make_snapshot(step, base_ts)
        ts = base_ts + step * WINDOW_MS

        if args.api_url:
            try:
                result = _post_snapshot(args.api_url, snapshot)
                label = f"cycle={result.get('cycle', '?')} alerts={result.get('alert_count', '?')}"
            except urllib.error.URLError as e:
                print(f"  step {step:2d}  →  POST failed: {e}")
                raise
        else:
            path = out_dir / f"graph_{ts}.json"
            path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            label = path.name

        note = _step_note(step)
        suffix = f"  ({note})" if note else ""
        print(f"  step {step:2d}  →  {label}{suffix}")

        if step < args.steps - 1:
            time.sleep(args.interval)

    print("\nSimulation complete.")


if __name__ == "__main__":
    main()
