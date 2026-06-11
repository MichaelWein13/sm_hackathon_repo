# Graph Data API

This project now loads graph structure data by POSTing to an HTTP endpoint instead of reading the local `graph.json` mock file.

## Endpoint

- URL: `/analytics/graph`
- Method: `POST`
- Content-Type: `application/json`

## Request body

The app sends an empty JSON body, but the server may accept graph payloads in the same shape as the legacy `graph.json` file.

Example request body:

```json
{}
```

If you want to support sending graph data directly, accept a full graph object in the request body with the same structure used by the client:

```json
{
  "snapshot_ts": 1718045312000,
  "nodes": ["zone_1", "zone_2", "zone_3"],
  "edges": [
    {
      "from_zone_id": "zone_1",
      "to_zone_id": "zone_3",
      "transition_count": 12,
      "transition_probability": 0.55
    }
  ],
  "zone_stats": {
    "zone_3": {
      "avg_dwell_ms": 7100,
      "visit_count": 18
    }
  },
  "time_windows": [
    {
      "window_start_ms": 1718045000000,
      "window_end_ms": 1718045300000,
      "window_graph": {
        "nodes": ["zone_1", "zone_3"],
        "edges": [
          {
            "from_zone_id": "zone_1",
            "to_zone_id": "zone_3",
            "transition_count": 8,
            "transition_probability": 0.50
          }
        ]
      }
    }
  ]
}
```

## Example curl

```bash
curl -X POST http://localhost:3000/analytics/graph \
  -H "Content-Type: application/json" \
  -d '{}'
```

If your server accepts graph payloads directly, replace `-d '{}'` with the full JSON graph object.

## Notes

- The React app currently calls this endpoint once on load using `fetchGraphData()`.
- The response must return JSON matching the graph shape expected by the client.
- The endpoint is intended to replace the old local `graph.json` mock file.
