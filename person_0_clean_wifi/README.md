# Person 0 Clean Wi-Fi Zone Detector

This folder replaces the older Person 0 demo with a cleaner Wi-Fi fingerprinting pipeline.

## What it does

1. Trains on labeled zones such as `inside_library` and `outside_library`.
2. Builds a stable Wi-Fi feature vector from BSSIDs.
3. Classifies the current zone for demo/debugging.
4. Writes a Person 2-compatible JSON array:

```json
[
  {
    "timestamp_ms": 1710000000000,
    "device_id": "person_1",
    "source_type": "wifi",
    "signal_vector": [-62.0, -71.0, -100.0]
  }
]
```

## Commands from repo root

```powershell
python person_0_clean_wifi\person0_wifi.py scan
python person_0_clean_wifi\person0_wifi.py clear --also-person2-output
python person_0_clean_wifi\person0_wifi.py train --zone inside_library --samples 10
python person_0_clean_wifi\person0_wifi.py train --zone outside_library --samples 10
python person_0_clean_wifi\person0_wifi.py build-model --top-n 16
python person_0_clean_wifi\person0_wifi.py collect --device-id person_1 --count 1
python person_0_clean_wifi\person0_wifi.py show-last
```

Then run Person 2:

```powershell
python person_2_zone_discovery\zone_discovery.py --input person_1_cleaning_data\converted_data\person2_input.json --min-cluster-size 2
```

## Important concept

Person 0 prints a predicted zone for demo/debugging, but the file sent to Person 2 intentionally does not contain the zone label. Person 2 receives only the wireless vector and performs zone discovery/clustering.
