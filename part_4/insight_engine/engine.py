"""
FloorFlow — Insight, Anomaly & Prediction Engine
Main entry point.

Primary integration (REST):
  python3 engine.py --serve --api-only --out-dir ../anomaly_reports

  Person 3: POST /ingest/graph
  Person 5: GET  /analytics/insights

Legacy file watch (optional):
  python3 engine.py --watch-files --graph-dir ../movement_graphs --out-dir ../anomaly_reports

Usage:
  python3 engine.py --serve --api-only           # REST server (recommended)
  python3 engine.py --once graph.json            # one-shot file test
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from detection         import analyze_snapshot
from alert_state       import AlertStateManager, _alert_to_dict, _alert_to_person5_dict
from narration         import generate_message
from decision_support  import enrich_alert, build_summary_decisions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File naming — change INPUT_GLOB if Person 3 uses a different pattern
# ---------------------------------------------------------------------------

INPUT_GLOB       = "*.json"           # matches any JSON file in movement_graphs/
OUTPUT_SNAPSHOT  = "insights_{ts}.json"
OUTPUT_EVENTS    = "events.ndjson"
OUTPUT_PERSON5   = "insights_api.json"  # Person 5: flat array, always latest
INGEST_API_PATH  = "/ingest/graph"      # Person 3: POST movement graph snapshot
PERSON5_API_PATH = "/analytics/insights"  # Person 5: flat ZoneInsight array (legacy)
ALERTS_API_PATH  = "/analytics/alerts"    # Person 5: full alerts + severity/lifecycle
SUMMARY_API_PATH = "/analytics/summary"   # Person 5: global headline
HEALTH_API_PATH  = "/health"

# Person 2 HDBSCAN noise label — not a real sector
_NOISE_ZONE_IDS  = frozenset({"transition"})

import re as _re
_TS_FROM_FILENAME = _re.compile(r"graph_(\d+)\.json$")


def _filename_ts(path: Path) -> int | None:
    m = _TS_FROM_FILENAME.search(path.name)
    return int(m.group(1)) if m else None


def _file_sort_key(path: Path) -> tuple:
    """Sort graph snapshots by logical timestamp (filename), then mtime."""
    ts = _filename_ts(path)
    if ts is not None:
        return (0, ts)
    try:
        return (1, int(path.stat().st_mtime * 1000))
    except OSError:
        return (2, 0)


def _extract_ts(path: Path, snapshot: dict) -> int:
    """
    Returns the logical snapshot timestamp in milliseconds.
    Priority: (1) filename  graph_<ms>.json,
              (2) 'snapshot_ts' field inside the JSON,
              (3) file mtime (fallback).
    """
    m = _TS_FROM_FILENAME.search(path.name)
    if m:
        return int(m.group(1))
    return _snapshot_ts(snapshot, fallback=int(path.stat().st_mtime * 1000))


def _snapshot_ts(snapshot: dict, fallback: int | None = None) -> int:
    if "snapshot_ts" in snapshot:
        return int(snapshot["snapshot_ts"])
    return fallback if fallback is not None else int(time.time() * 1000)


def _sanitize_graph(snapshot: dict) -> dict:
    """Drop HDBSCAN noise zones so they do not flood alerts on real building data."""
    nodes = [n for n in snapshot.get("nodes", []) if n not in _NOISE_ZONE_IDS]
    edges = [
        e for e in snapshot.get("edges", [])
        if e.get("from_zone_id") not in _NOISE_ZONE_IDS
        and e.get("to_zone_id") not in _NOISE_ZONE_IDS
    ]
    zone_stats = {
        k: v for k, v in snapshot.get("zone_stats", {}).items()
        if k not in _NOISE_ZONE_IDS
    }
    time_windows = []
    for window in snapshot.get("time_windows", []):
        wg = window.get("window_graph", {})
        time_windows.append({
            **window,
            "window_graph": {
                "nodes": [n for n in wg.get("nodes", []) if n not in _NOISE_ZONE_IDS],
                "edges": [
                    e for e in wg.get("edges", [])
                    if e.get("from_zone_id") not in _NOISE_ZONE_IDS
                    and e.get("to_zone_id") not in _NOISE_ZONE_IDS
                ],
            },
        })
    return {
        **snapshot,
        "nodes":        nodes,
        "edges":        edges,
        "zone_stats":   zone_stats,
        "time_windows": time_windows,
    }


def _validate_graph(snapshot: dict) -> str | None:
    """Returns an error message if the payload is not a usable graph snapshot."""
    if not isinstance(snapshot, dict):
        return "body must be a JSON object"
    for key in ("nodes", "edges", "zone_stats", "time_windows"):
        if key not in snapshot:
            return f"missing required field: {key}"
    if not isinstance(snapshot["nodes"], list):
        return "nodes must be an array"
    if not isinstance(snapshot["edges"], list):
        return "edges must be an array"
    return None


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _sector_label(zone_id: str) -> str:
    return zone_id.replace("zone_", "Sector ") if zone_id.startswith("zone_") else zone_id


def _build_summary(
    state: AlertStateManager,
    alerts: list,
    snapshot_ts: int,
    signals: dict | None = None,
) -> dict:
    critical = sum(1 for a in alerts if a.severity == "critical")
    warning  = sum(1 for a in alerts if a.severity == "warning")
    total    = len(alerts)
    sectors  = sorted({a.zone_id for a in alerts})

    if critical:
        summary_sev = "critical"
    elif warning:
        summary_sev = "warning"
    elif total:
        summary_sev = "detecting"
    else:
        summary_sev = "info"

    elapsed = state.elapsed_seconds()
    if total:
        worst = max(
            alerts,
            key=lambda a: (
                {"critical": 3, "warning": 2, "detecting": 1, "resolving": 0}.get(a.severity, 0),
                a.confidence,
            ),
        )
        worst_label = _sector_label(worst.zone_id)
        if critical:
            situation = f"One critical situation developing in {worst_label}"
        elif warning:
            situation = f"One developing situation in {worst_label}"
        else:
            situation = f"Patterns emerging in {worst_label}"
        summary_msg = (
            f"{total} active alert{'s' if total != 1 else ''} across "
            f"{len(sectors)} observed sector{'s' if len(sectors) != 1 else ''}. "
            f"{situation}. "
            f"System has been running for {elapsed}s across {state.cycle} snapshots."
        )
    else:
        summary_msg = (
            f"No active alerts. System nominal. "
            f"Running for {elapsed}s across {state.cycle} snapshots."
        )

    summary_obj = {
        "zone_id":      "global",
        "insight_type": "situation_summary",
        "severity":     summary_sev,
        "message":      summary_msg,
        "confidence":   1.0,
    }

    if signals is not None:
        enriched_alerts = [enrich_alert(a, signals, alerts) for a in alerts]
        summary_obj.update(build_summary_decisions(alerts, enriched_alerts))
        alert_payload = enriched_alerts
    else:
        alert_payload = [_alert_to_dict(a) for a in alerts]

    return {
        "snapshot_ts":     snapshot_ts,
        "cycle":           state.cycle,
        "elapsed_seconds": elapsed,
        "summary":         summary_obj,
        "alerts":          alert_payload,
    }


def _build_headline(alerts: list) -> str | None:
    if not alerts:
        return None
    worst = max(
        alerts,
        key=lambda a: (
            {"critical": 3, "warning": 2, "detecting": 1, "resolving": 0}.get(a.severity, 0),
            a.confidence,
        ),
    )
    sev = worst.severity.upper()
    if worst.severity == "critical":
        sev = "CRITICAL"
    elif worst.severity == "warning":
        sev = "WARNING"
    elif worst.severity == "detecting":
        sev = "DETECTING"
    else:
        sev = worst.severity.upper()
    label = _sector_label(worst.zone_id)
    type_phrase = worst.insight_type.replace("_", " ")
    return f"{sev} — {label} {type_phrase}"


def _write_snapshot(
    out_dir: Path,
    alerts: list,
    events: list,
    state: AlertStateManager,
    snapshot_ts: int,
    signals: dict | None = None,
) -> dict:
    payload = _build_summary(state, alerts, snapshot_ts, signals=signals)
    critical = sum(1 for a in alerts if a.severity == "critical")
    warning  = sum(1 for a in alerts if a.severity == "warning")
    total    = len(alerts)

    filename = out_dir / OUTPUT_SNAPSHOT.format(ts=snapshot_ts)
    _atomic_write(filename, json.dumps(payload, indent=2))
    _write_person5_api(out_dir, alerts)
    logger.info(f"Wrote {filename.name} — {total} alerts ({critical} critical, {warning} warning)")
    return payload


def _write_person5_api(out_dir: Path, alerts: list):
    """Person 5 contract: flat array of {zone_id, insight_type, message, confidence}."""
    payload = [_alert_to_person5_dict(a) for a in alerts]
    _atomic_write(out_dir / OUTPUT_PERSON5, json.dumps(payload, indent=2))


def _append_events(out_dir: Path, events: list):
    if not events:
        return
    events_file = out_dir / OUTPUT_EVENTS
    lines = "\n".join(json.dumps(e, separators=(",", ":")) for e in events) + "\n"
    with open(events_file, "a") as f:
        f.write(lines)


def _atomic_write(path: Path, content: str):
    """Write to a temp file then rename — prevents Person 5 reading a partial file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Directory watcher
# ---------------------------------------------------------------------------

class DirectoryWatcher:
    """
    Watches a directory for new JSON files. Yields each new file path in
    chronological order (by mtime). Does not re-process already-seen files.
    """

    def __init__(self, watch_dir: Path):
        self.watch_dir = watch_dir
        self._seen: set[str] = set()

    def poll(self) -> list[Path]:
        """Returns newly appeared files since last call, sorted by logical timestamp."""
        try:
            files = sorted(
                self.watch_dir.glob(INPUT_GLOB),
                key=_file_sort_key,
            )
        except OSError:
            return []

        new_files = [f for f in files if f.name not in self._seen]
        for f in new_files:
            self._seen.add(f.name)
        return new_files


# ---------------------------------------------------------------------------
# Shared runtime (REST + optional file watch)
# ---------------------------------------------------------------------------

class EngineRuntime:
    """Thread-safe state for POST /ingest/graph and optional file watching."""

    def __init__(self, out_dir: Path, fresh: bool = False):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.state_manager = AlertStateManager()
        self.engine_state = {
            "ewma":            {},
            "edge_history":    set(),
            "zone_history":    {},
            "total_snapshots": 0,
        }
        if fresh:
            events_file = self.out_dir / OUTPUT_EVENTS
            if events_file.exists():
                events_file.unlink()
            logger.info("--fresh: cleared events.ndjson for a clean run")
        if not (self.out_dir / OUTPUT_PERSON5).exists():
            _atomic_write(self.out_dir / OUTPUT_PERSON5, "[]")
        self.latest_dashboard: dict = {
            "snapshot_ts":     0,
            "cycle":           0,
            "elapsed_seconds": 0,
            "summary":         None,
            "alerts":          [],
            "headline":        None,
        }

    def ingest(self, snapshot: dict, snapshot_ts: int | None = None) -> dict:
        snapshot = _sanitize_graph(snapshot)
        err = _validate_graph(snapshot)
        if err:
            raise ValueError(err)

        with self.lock:
            ts = snapshot_ts if snapshot_ts is not None else _snapshot_ts(snapshot)
            logger.info(f"Ingesting graph snapshot ts={ts}")
            active_alerts, dashboard = process_snapshot(
                snapshot, self.state_manager, self.engine_state, self.out_dir, ts
            )
            headline = _build_headline(active_alerts)
            self.latest_dashboard = {**dashboard, "headline": headline}
            return {
                "ok":           True,
                "snapshot_ts":  ts,
                "cycle":        self.state_manager.cycle,
                "alert_count":  len(active_alerts),
                "headline":     headline,
                "summary":      dashboard["summary"],
                "alerts":       dashboard["alerts"],
                "insights":     [_alert_to_person5_dict(a) for a in active_alerts],
            }


# ---------------------------------------------------------------------------
# Core processing loop
# ---------------------------------------------------------------------------

def process_snapshot(
    snapshot: dict,
    state_manager: AlertStateManager,
    engine_state: dict,
    out_dir: Path,
    snapshot_ts: int,
) -> tuple[list, dict]:
    signals = analyze_snapshot(
        snapshot=snapshot,
        ewma_state=engine_state["ewma"],
        edge_history=engine_state["edge_history"],
        zone_history=engine_state["zone_history"],
        total_prior_snapshots=engine_state["total_snapshots"],
    )
    signals["snapshot_ts"] = snapshot_ts
    engine_state["total_snapshots"] += 1

    events, active_alerts = state_manager.update(
        signals=signals,
        narrate_fn=generate_message,
        snapshot_ts=snapshot_ts,
    )

    dashboard = _write_snapshot(
        out_dir, active_alerts, events, state_manager, snapshot_ts, signals=signals
    )
    _append_events(out_dir, events)

    for ev in events:
        sev = ev.get("insight", {}).get("severity", "")
        logger.info(
            f"  [{ev['event'].upper():12}] {ev['alert_id']}  severity={sev}"
        )
    return active_alerts, dashboard


def run_watch(runtime: EngineRuntime, graph_dir: Path, interval: float):
    logger.info(f"Watching {graph_dir}  →  {runtime.out_dir}  (poll every {interval}s)")
    watcher = DirectoryWatcher(graph_dir)

    while True:
        new_files = watcher.poll()
        for path in new_files:
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                snapshot_ts = _extract_ts(path, snapshot)
                logger.info(f"Processing {path.name}")
                runtime.ingest(snapshot, snapshot_ts=snapshot_ts)
            except Exception as e:
                logger.error(f"Failed to process {path.name}: {e}")

        time.sleep(interval)


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict | list | str):
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload, separators=(",", ":"))
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(encoded)


def _make_api_handler(runtime: EngineRuntime):
    api_file = runtime.out_dir / OUTPUT_PERSON5

    class FloorFlowHandler(BaseHTTPRequestHandler):
        def _route(self) -> str:
            return self.path.split("?", 1)[0]

        def _set_cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self):
            self.send_response(204)
            self._set_cors()
            self.end_headers()

        def do_GET(self):
            route = self._route()
            if route == HEALTH_API_PATH:
                _send_json(self, 200, {
                    "status":   "ok",
                    "ingest":   INGEST_API_PATH,
                    "insights": PERSON5_API_PATH,
                    "alerts":   ALERTS_API_PATH,
                    "summary":  SUMMARY_API_PATH,
                })
                return
            if route == ALERTS_API_PATH:
                dash = runtime.latest_dashboard
                _send_json(self, 200, {
                    "snapshot_ts":     dash["snapshot_ts"],
                    "cycle":           dash["cycle"],
                    "elapsed_seconds": dash["elapsed_seconds"],
                    "headline":        dash.get("headline"),
                    "alerts":          dash["alerts"],
                })
                return
            if route == SUMMARY_API_PATH:
                dash = runtime.latest_dashboard
                _send_json(self, 200, {
                    "snapshot_ts":     dash["snapshot_ts"],
                    "cycle":           dash["cycle"],
                    "elapsed_seconds": dash["elapsed_seconds"],
                    "headline":        dash.get("headline"),
                    "summary":         dash["summary"],
                })
                return
            if route == PERSON5_API_PATH:
                try:
                    body = api_file.read_text(encoding="utf-8") if api_file.exists() else "[]"
                except OSError:
                    self.send_error(500)
                    return
                _send_json(self, 200, body)
                return
            self.send_error(404)

        def do_POST(self):
            if self._route() != INGEST_API_PATH:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                raw = self.rfile.read(length)
                snapshot = json.loads(raw.decode("utf-8"))
                result = runtime.ingest(snapshot)
                _send_json(self, 200, result)
            except json.JSONDecodeError:
                _send_json(self, 400, {"ok": False, "error": "invalid JSON"})
            except ValueError as e:
                _send_json(self, 400, {"ok": False, "error": str(e)})
            except Exception as e:
                logger.error(f"POST {INGEST_API_PATH} failed: {e}")
                _send_json(self, 500, {"ok": False, "error": "internal error"})

        def log_message(self, format, *args):
            logger.debug("API %s", format % args)

    return FloorFlowHandler


class _ReuseHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def start_api_server(
    runtime: EngineRuntime,
    port: int,
    *,
    blocking: bool = False,
) -> ThreadingHTTPServer:
    try:
        server = _ReuseHTTPServer(("0.0.0.0", port), _make_api_handler(runtime))
    except OSError as e:
        if e.errno == 98:  # Address already in use
            logger.error(
                f"Port {port} is already in use. "
                f"Stop the old engine:  fuser -k {port}/tcp"
            )
        raise
    logger.info(
        f"API http://0.0.0.0:{port}"
        f"  POST {INGEST_API_PATH}"
        f"  GET {PERSON5_API_PATH} {ALERTS_API_PATH} {SUMMARY_API_PATH} {HEALTH_API_PATH}"
    )
    if blocking:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Stopped.")
        finally:
            server.shutdown()
    else:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    return server


def run_once(graph_path: Path, out_dir: Path):
    """One-shot mode — process a single file and exit. Useful for testing."""
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot    = _sanitize_graph(json.loads(graph_path.read_text(encoding="utf-8")))
    snapshot_ts = int(time.time() * 1000)
    state_manager = AlertStateManager()
    engine_state  = {
        "ewma":            {},
        "edge_history":    set(),
        "zone_history":    {},
        "total_snapshots": 0,
    }
    _, _ = process_snapshot(snapshot, state_manager, engine_state, out_dir, snapshot_ts)
    logger.info("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="FloorFlow — Insight, Anomaly & Prediction Engine"
    )
    parser.add_argument(
        "--graph-dir",
        default=os.environ.get("FLOORFLOW_GRAPH_DIR", "../movement_graphs"),
        help="Directory to watch for graph snapshots from Person 3 "
             "(default: ../movement_graphs or $FLOORFLOW_GRAPH_DIR)",
    )
    parser.add_argument(
        "--out-dir",
        default=os.environ.get("FLOORFLOW_OUT_DIR", "../anomaly_reports"),
        help="Directory to write insights and events to "
             "(default: ../anomaly_reports or $FLOORFLOW_OUT_DIR)",
    )
    parser.add_argument(
        "--interval", type=float, default=2.0,
        help="Polling interval in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--once", metavar="FILE",
        help="Process a single graph file and exit (for testing)",
    )
    parser.add_argument(
        "--serve", action="store_true",
        help=f"Start HTTP API (POST {INGEST_API_PATH}, GET {PERSON5_API_PATH})",
    )
    parser.add_argument(
        "--api-only", action="store_true",
        help="Run HTTP API only — no file watching (recommended integration mode)",
    )
    parser.add_argument(
        "--watch-files", action="store_true",
        help="Also poll --graph-dir for graph_*.json (legacy; off by default with --api-only)",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=int(os.environ.get("FLOORFLOW_API_PORT", "8765")),
        help="API port (default: 8765 or $FLOORFLOW_API_PORT)",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Clear events.ndjson on startup (use when restarting mid-demo)",
    )
    args = parser.parse_args()

    graph_dir = Path(args.graph_dir)
    out_dir   = Path(args.out_dir)

    if args.once:
        run_once(Path(args.once), out_dir)
        return

    if args.api_only and not args.serve:
        args.serve = True

    if not args.serve and not args.watch_files:
        parser.error("Specify --serve (REST) and/or --watch-files (legacy file poll)")

    runtime = EngineRuntime(out_dir, fresh=args.fresh)
    api_server = None

    if args.api_only:
        start_api_server(runtime, args.api_port, blocking=True)
        return

    if args.serve:
        api_server = start_api_server(runtime, args.api_port, blocking=False)

    if args.watch_files:
        if not graph_dir.exists():
            logger.warning(f"graph-dir {graph_dir} does not exist yet — will keep checking")
        try:
            run_watch(runtime, graph_dir, args.interval)
        except KeyboardInterrupt:
            logger.info("Stopped.")
        finally:
            if api_server:
                api_server.shutdown()
    elif args.serve:
        logger.info("API running — waiting for POST requests (Ctrl-C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Stopped.")
        finally:
            if api_server:
                api_server.shutdown()


if __name__ == "__main__":
    main()
