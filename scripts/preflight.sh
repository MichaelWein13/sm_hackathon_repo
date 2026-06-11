#!/usr/bin/env bash
# Quick checks before a demo or integration run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PART4="$REPO_ROOT/part_4"
IO_ROOT="${FLOORFLOW_IO:-$(cd "$REPO_ROOT/../floorflow-io" && pwd)}"
GRAPH_DIR="$IO_ROOT/movement_graphs"
OUT_DIR="$IO_ROOT/anomaly_reports"
API_PORT="${FLOORFLOW_API_PORT:-8765}"

FAIL=0

check() {
  if "$@"; then
    echo "  OK   $1"
  else
    echo "  FAIL $1"
    FAIL=1
  fi
}

echo "FloorFlow preflight"
echo "  IO root:  $IO_ROOT"
echo "  API port: $API_PORT"
echo ""

echo "Environment"
python3 -c 'import sys; assert sys.version_info >= (3, 10), sys.version' \
  && echo "  OK   Python >= 3.10 ($(python3 --version 2>&1))" \
  || { echo "  FAIL Python >= 3.10 required"; FAIL=1; }

test -f "$PART4/insight_engine/engine.py" \
  && echo "  OK   insight engine present" \
  || { echo "  FAIL missing $PART4/insight_engine/engine.py"; FAIL=1; }

echo ""
echo "Shared I/O"
mkdir -p "$GRAPH_DIR" "$OUT_DIR"
check test -w "$GRAPH_DIR"
check test -w "$OUT_DIR"

echo ""
echo "Insight engine API (optional — start engine with --serve --api-only first)"
if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
  echo "  OK   GET /health on port $API_PORT"
  if curl -sf "http://127.0.0.1:${API_PORT}/analytics/insights" >/dev/null 2>&1; then
    echo "  OK   GET /analytics/insights"
  else
    echo "  WARN /analytics/insights not reachable"
  fi
  echo "  OK   POST /ingest/graph expected at http://127.0.0.1:${API_PORT}/ingest/graph"
else
  echo "  SKIP engine not running on port $API_PORT (start with scripts/run_demo.sh --engine-only)"
fi

GRAPH_COUNT=$(find "$GRAPH_DIR" -maxdepth 1 -name 'graph_*.json' 2>/dev/null | wc -l)
INSIGHT_COUNT=$(find "$OUT_DIR" -maxdepth 1 -name 'insights_*.json' 2>/dev/null | wc -l)
echo ""
echo "Pipeline state"
echo "  graph snapshots:  $GRAPH_COUNT"
echo "  insight files:    $INSIGHT_COUNT"
if [[ -f "$OUT_DIR/events.ndjson" ]]; then
  echo "  event lines:      $(wc -l < "$OUT_DIR/events.ndjson")"
else
  echo "  event lines:      0"
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "Preflight passed."
else
  echo "Preflight failed — fix the items above before demo."
  exit 1
fi
