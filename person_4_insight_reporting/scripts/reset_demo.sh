#!/usr/bin/env bash
# Clear shared pipeline I/O for a clean demo run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PART4_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IO_ROOT="${FLOORFLOW_IO:-$(cd "$PART4_ROOT/../floorflow-io" && pwd)}"
GRAPH_DIR="$IO_ROOT/movement_graphs"
OUT_DIR="$IO_ROOT/anomaly_reports"

mkdir -p "$GRAPH_DIR" "$OUT_DIR"

rm -f "$GRAPH_DIR"/*.json
rm -f "$OUT_DIR"/insights_*.json
rm -f "$OUT_DIR"/events.ndjson
rm -f "$OUT_DIR"/insights_api.json

echo "Reset complete."
echo "  movement_graphs: $GRAPH_DIR"
echo "  anomaly_reports: $OUT_DIR"
