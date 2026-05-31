# WiFi Zone Detection (macOS)

A minimal indoor "which zone am I in?" classifier driven by WiFi signal strength
instead of GPS. GPS is unreliable indoors; the access points a building already
has are not. You record what the WiFi looks like in each zone (a *fingerprint*),
then the tool guesses your zone from a fresh scan.

It is two small files working together:

| File | Language | Role |
|------|----------|------|
| `wifiscan.swift` | Swift | Scans nearby WiFi via macOS CoreWLAN and prints the results. The *sensor*. |
| `zonefit.py` | Python | Records labeled fingerprints and classifies new scans. The *brain*. |

`zonefit.py` shells out to the compiled `wifiscan` binary for every reading, so
both must live in the same folder.

---

## Why these two pieces

A normal program (and any web page) cannot read WiFi signal strengths on macOS —
only Apple's CoreWLAN framework can, and the old `airport -s` command was removed
in recent macOS. So the Swift file exists purely to reach that framework and hand
back a clean list of `{ssid, rssi, channel}`. Everything else — storing samples,
the distance math, the two classifiers — is ordinary Python and lives in
`zonefit.py`. Splitting it this way keeps the platform-specific part tiny and lets
you iterate on the logic in Python.

---

## Requirements

- A Mac (CoreWLAN is macOS-only; iOS and other platforms can't do this).
- `swiftc` — installed with the Xcode command line tools: `xcode-select --install`
- Python 3 (ships with macOS).

---

## Setup

From the folder containing both files:

```bash
swiftc wifiscan.swift -o wifiscan -framework CoreWLAN -framework CoreLocation -framework Foundation
```

`swiftc` prints nothing on success — silence means it built. You now have an
executable called `wifiscan` next to the `.swift` source.

Sanity-check the sensor before doing anything else:

```bash
./wifiscan
```

Expected output is a table of real networks:

```
Found 14 networks

RSSI   CH   SSID
----------------------------------------
-42    36   Technion-Guest
-58    1    eduroam
-67    11   <hidden/blocked>
```

Negative dBm values and channel numbers mean the scan works. **If RSSI/channel
look real but every SSID says `<hidden/blocked>`, the scan is fine — you only lack
the Location permission.** See [Location Services](#location-services-the-one-gotcha).

---

## Usage

Start the interactive tool:

```bash
python3 zonefit.py
```

You drive it from a `>` prompt. The core idea: **type the name of the zone you are
standing in to record a fingerprint there**, repeat for each zone, then ask it to
predict.

### Commands

| Type this | What it does |
|-----------|--------------|
| `ER` (any zone name) | Record one fingerprint at your current spot, labeled with that name |
| `auto ER 5` | Record 5 fingerprints for a zone automatically (waits ~5 s between scans) |
| `p` | Predict your current zone, using **both** classifiers |
| `list` | Show how many samples you have per zone |
| `del ER` | Delete all samples for a zone |
| `save` | Write data to `fingerprints.json` |
| `help` | Reprint the command list |
| `q` | Save and quit |

### A typical session

```
> ER            # stand in the ER, press Enter
  recorded 'ER' (12 APs). total for zone: 1
> ER            # take a few more, moving around the zone a little
> ER
> Lobby
> Lobby
> CT
> CT
> p             # walk somewhere and predict
  saw 12 APs
  CENTROID -> ER             88%   (nearest centroid d=4.2, runner-up CT d=19.1)
  KNN      -> ER            100%   (3/3 of nearest neighbors)
> q
```

The number in parentheses after a recording (e.g. `12 APs`) is your health check:
a handful to a couple dozen access points means good data. `0 APs` means the scan
is being blocked.

Aim for at least 4–6 samples per zone, taken from slightly different positions
within the zone, so the classifier sees the natural variation.

---

## The two classifiers (and why both)

`p` runs both methods side by side so you can see which fits your building:

- **Nearest centroid** — averages all samples of each zone into one representative
  fingerprint, then picks the zone whose average is closest to your current scan.
  Steady with few samples; its confidence is a smooth 0–100% score.
- **KNN (k-nearest neighbors)** — finds the `k` individual samples closest to your
  scan and takes a majority vote. Handles irregularly shaped zones better but wants
  more samples; its confidence is simply the winning vote fraction.

If the two agree with high confidence, your zones are cleanly separable by WiFi.
If they often disagree or hover at low confidence, those zones probably see the
same access points at similar strength and are genuinely hard to tell apart — a
real limitation of the space, not a bug.

---

## Data format

Everything is stored in `fingerprints.json` (written on `save`/`q`). To view it:

```bash
python3 -m json.tool fingerprints.json
```

Structure:

```json
{
  "samples": [
    {
      "zone": "ER",
      "fp": { "eduroam@1": -58, "Technion-Guest@36": -42, "<hidden>@11": -67 }
    }
  ]
}
```

Each sample is one scan: a zone label plus a fingerprint mapping `SSID@channel`
to its RSSI in dBm. Because macOS redacts each access point's BSSID (MAC address),
APs are keyed on **SSID + channel** rather than BSSID.

---

## Tuning

Constants at the top of `zonefit.py`:

| Name | Default | Meaning |
|------|---------|---------|
| `FLOOR` | `-100` | RSSI assumed for an access point not seen in a given scan |
| `KNN_K` | `3` | Number of neighbors KNN votes over |
| `SOFTMAX_T` | `8.0` | Temperature for centroid confidence (higher = softer/less certain) |

---

## Location Services (the one gotcha)

On recent macOS, CoreWLAN only reveals network SSIDs if Location Services is on and
granted to the app doing the scanning — here, your terminal. Symptoms of missing
permission: SSIDs all show as `<hidden/blocked>`, or scans return very little.

Fix:

1. **System Settings → Privacy & Security → Location Services** → turn it **on**.
2. Enable it for your terminal app (**Terminal** or **iTerm**). It may only appear
   in that list after you have run `./wifiscan` once.
3. Re-run `./wifiscan` and confirm real SSIDs now appear.

Note: a bare command-line binary is more fragile about this than a full app bundle,
so you may have to grant the permission manually rather than getting a popup.

---

## Limitations / honest expectations

- **macOS only.** The sensor depends on CoreWLAN.
- **Scan rate.** macOS throttles WiFi scans to roughly one every 4–6 seconds, so
  `auto` deliberately pauses between samples and live prediction isn't instant.
- **Separability is the real test.** The code is verified to classify correctly on
  clean data. Whether it works *in your building* depends entirely on whether each
  zone has a distinct enough WiFi signature. Zones that share the same nearby APs at
  similar strength will be confused no matter the classifier — that's a property of
  the radio environment, and the honest finding such a test is designed to surface.
- For finer accuracy than zone-level, real systems add BLE beacons, WiFi RTT/FTM
  (true distance to AP), or UWB. This tool is the RSSI-fingerprinting baseline.
