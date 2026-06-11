#!/usr/bin/env bash
# 5-minute continuous run: REST API engine + mock POST generator.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STEPS="${1:-100}"
INTERVAL="${2:-3}"
API_PORT="${FLOORFLOW_API_PORT:-8767}"

echo "Soak test: ${STEPS} steps × ${INTERVAL}s ≈ $(( STEPS * INTERVAL / 60 )) min"
echo "API port: ${API_PORT}"

rm -f "$ROOT/mock/anomaly_reports/insights_"*.json
rm -f "$ROOT/mock/anomaly_reports/events.ndjson"
rm -f "$ROOT/mock/anomaly_reports/insights_api.json"

if command -v fuser >/dev/null 2>&1; then
  fuser -k "${API_PORT}/tcp" 2>/dev/null || true
  sleep 0.5
fi

export NARRATION_BACKEND=disabled

python3 "$ROOT/insight_engine/engine.py" \
  --serve --api-only \
  --api-port "$API_PORT" \
  --out-dir "$ROOT/mock/anomaly_reports" \
  --fresh &
ENGINE_PID=$!

cleanup() {
  kill "$ENGINE_PID" 2>/dev/null || true
  wait "$ENGINE_PID" 2>/dev/null || true
}
trap cleanup EXIT

sleep 1

python3 "$ROOT/mock/generate_mock_snapshots.py" \
  --api-url "http://127.0.0.1:${API_PORT}/ingest/graph" \
  --steps "$STEPS" \
  --interval "$INTERVAL"

INSIGHTS=$(ls -1 "$ROOT/mock/anomaly_reports/insights_"*.json 2>/dev/null | wc -l)
EVENTS=$(wc -l < "$ROOT/mock/anomaly_reports/events.ndjson" 2>/dev/null || echo 0)

echo ""
echo "Soak complete."
echo "  Insight files written: $INSIGHTS"
echo "  Event lines appended:  $EVENTS"

if [[ "$INSIGHTS" -lt "$(( STEPS - 2 ))" ]]; then
  echo "  WARNING: expected ~${STEPS} insight files — check engine logs"
  exit 1
fi
