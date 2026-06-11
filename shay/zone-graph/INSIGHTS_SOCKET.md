# Insights Socket Integration

This app now receives insight updates through a WebSocket connection instead of fetching `insights.json` repeatedly.

## Endpoint

- WebSocket endpoint: `ws(s)://<host>/analytics/insights`
- The client connects at runtime using the same host and protocol as the page.

## Payload

Each incoming WebSocket message should contain a JSON object matching the existing `insights.json` schema:

- `snapshot_ts`
- `cycle`
- `elapsed_seconds`
- `summary`
  - `zone_id`
  - `insight_type`
  - `severity`
  - `message`
  - `confidence`
- `alerts[]`
  - `id`
  - `zone_id`
  - `insight_type`
  - `severity`
  - `message`
  - `confidence`
  - lifecycle timestamps, etc.

## Client behavior

- The app fetches graph structure data once using `fetchGraphData()`.
- It opens a WebSocket to `/analytics/insights`.
- Each socket message is parsed as JSON and stored in the `insights` state.
- The graph sidebar and node annotations update automatically when a new insight payload arrives.

## Notes

- The sidebar displays all alerts from the current insights object.
- Notification badges and hover labels are based on the latest `alerts[]` data.
- If the socket closes or errors, the connection is cleaned up on unmount.
