# Person 2 — Zone Discovery

This module receives standardized wireless observation records from Person 1 and clusters them into discovered zones. Input with the same device id and and relatively close timestamp but from a different source type will be considered one data point.

# Input

The input is a JSON array of standardized observation records from Person 1:

```json
{
  "timestamp_ms": 1710000000000,
  "device_id": "person_1",
  "source_type": "uwb",
  "signal_vector": [2.41, 2.87, 3.02],
  "confidence": 0.91
}

