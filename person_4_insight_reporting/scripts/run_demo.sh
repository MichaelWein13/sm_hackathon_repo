#!/usr/bin/env bash
# End-to-end demo: reset shared I/O, start insight engine, run mock graph generator.
#
# Usage:
#   ./scripts/run_demo.sh                  # full demo (default)
#   ./scripts/run_demo.sh --no-reset       # keep existing graph/insight files
#   ./scripts/run_demo.sh --engine-only    # start engine in foreground (Person 3 / live data)
#   ./scripts/run_demo.sh --mock-only       # run mock generator only (engine already up)
#
# Environment:
#   FLOORFLOW_IO       Shared I/O root (default: ../../floorflow-io from person_4_insight_reporting/)
#   FLOORFLOW_API_PORT API port (default: 8765)
#   DEMO_STEPS         Mock snapshots (default: 12)
#   DEMO_INTERVAL      Seconds between snapshots (default: 3)
#   NARRATION_BACKEND  Set to disabled for template messages (default: disabled)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PART4_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IO_ROOT="${FLOORFLOW_IO:-$(cd "$PART4_ROOT/../floorflow-io" && pwd)}"
GRAPH_DIR="$IO_ROOT/movement_graphs"
OUT_DIR="$IO_ROOT/anomaly_reports"
API_PORT="${FLOORFLOW_API_PORT:-8765}"
DEMO_STEPS="${DEMO_STEPS:-30}"
DEMO_INTERVAL="${DEMO_INTERVAL:-3}"

NO_RESET=false
ENGINE_ONLY=false
MOCK_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-reset)   NO_RESET=true; shift ;;
    --engine-only) ENGINE_ONLY=true; shift ;;
    --mock-only)  MOCK_ONLY=true; shift ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1  (try --help)" >&2
      exit 1
      ;;
  esac
done

if [[ "$ENGINE_ONLY" == true && "$MOCK_ONLY" == true ]]; then
  echo "Cannot use --engine-only and --mock-only together." >&2
  exit 1
fi

mkdir -p "$GRAPH_DIR" "$OUT_DIR"
export NARRATION_BACKEND="${NARRATION_BACKEND:-disabled}"
export FLOORFLOW_GRAPH_DIR="$GRAPH_DIR"
export FLOORFLOW_OUT_DIR="$OUT_DIR"
export FLOORFLOW_API_PORT="$API_PORT"

if [[ "$NO_RESET" != true && "$MOCK_ONLY" != true ]]; then
  "$SCRIPT_DIR/reset_demo.sh"
elif [[ "$NO_RESET" != true ]]; then
  echo "Skipping reset (--mock-only); graph dir unchanged."
fi

ENGINE_PID=""

cleanup() {
  if [[ -n "$ENGINE_PID" ]]; then
    kill "$ENGINE_PID" 2>/dev/null || true
    wait "$ENGINE_PID" 2>/dev/null || true
  fi
}

run_engine() {
  python3 "$PART4_ROOT/insight_engine/engine.py" \
    --out-dir "$OUT_DIR" \
    --serve --api-only --api-port "$API_PORT" \
    --fresh \
    "$@"
}

API_URL="http://127.0.0.1:${API_PORT}/ingest/graph"
HEALTH_URL="http://127.0.0.1:${API_PORT}/health"

free_api_port() {
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${API_PORT}/tcp" 2>/dev/null || true
    sleep 0.5
  fi
}

wait_for_engine() {
  local i
  for i in $(seq 1 15); do
    if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
      echo "Engine process exited during startup (pid $ENGINE_PID)." >&2
      echo "Check logs above — often port ${API_PORT} in use or a Python traceback." >&2
      return 1
    fi
    if curl -sf "$HEALTH_URL" 2>/dev/null | grep -q '"ingest"'; then
      return 0
    fi
    sleep 0.5
  done
  echo "Engine on port ${API_PORT} did not become ready (expected POST /ingest/graph in /health)." >&2
  echo "An old server may still be bound — run:  fuser -k ${API_PORT}/tcp" >&2
  return 1
}

if [[ "$MOCK_ONLY" != true ]]; then
  free_api_port
  if [[ "$ENGINE_ONLY" == true ]]; then
    free_api_port
    echo "Starting insight engine (foreground) — Ctrl-C to stop"
    echo "  graphs:  $GRAPH_DIR"
    echo "  reports: $OUT_DIR"
    echo "  ingest:  POST http://127.0.0.1:${API_PORT}/ingest/graph"
    echo "  API:     http://127.0.0.1:${API_PORT}/analytics/insights"
    exec run_engine
  fi

  run_engine &
  ENGINE_PID=$!
  trap cleanup EXIT

  echo "Insight engine started (pid $ENGINE_PID)"
  echo "  graphs:  $GRAPH_DIR"
  echo "  reports: $OUT_DIR"
  echo "  ingest:  POST http://127.0.0.1:${API_PORT}/ingest/graph"
  echo "  API:     http://127.0.0.1:${API_PORT}/analytics/insights"
  echo "  health:  http://127.0.0.1:${API_PORT}/health"
  wait_for_engine
fi

if [[ "$ENGINE_ONLY" != true ]]; then
  echo ""
  echo "Running mock graph generator (${DEMO_STEPS} steps × ${DEMO_INTERVAL}s)..."
  python3 "$PART4_ROOT/mock/generate_mock_snapshots.py" \
    --api-url "$API_URL" \
    --steps "$DEMO_STEPS" \
    --interval "$DEMO_INTERVAL"
fi

if [[ -n "$ENGINE_PID" ]]; then
  sleep 2
  INSIGHT_COUNT=$(find "$OUT_DIR" -maxdepth 1 -name 'insights_*.json' 2>/dev/null | wc -l)
  EVENT_COUNT=0
  if [[ -f "$OUT_DIR/events.ndjson" ]]; then
    EVENT_COUNT=$(wc -l < "$OUT_DIR/events.ndjson")
  fi

  echo ""
  echo "Demo run complete."
  echo "  insight files: $INSIGHT_COUNT"
  echo "  event lines:   $EVENT_COUNT"
  echo ""
  echo "Engine still running (pid $ENGINE_PID). Person 5 can poll:"
  echo "  curl http://127.0.0.1:${API_PORT}/analytics/insights"
  echo "  ls -lt $OUT_DIR/insights_*.json | head"
  echo ""
  echo "Press Ctrl-C to stop the engine."
  wait "$ENGINE_PID"
fi
