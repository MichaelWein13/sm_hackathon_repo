#!/usr/bin/env python3
"""
zonefit.py — label where you are, then test which classifier guesses it right.

Workflow:
  1. Stand in a zone, type its name (e.g. "ER") and press Enter -> records a
     WiFi fingerprint of that spot. Do it a few times per zone, ideally from
     slightly different positions within the zone.
  2. Walk somewhere, type  p  -> it predicts your zone with BOTH methods
     (nearest-centroid and KNN) so you can see which one works for your space.

It calls the compiled Swift scanner (./wifiscan --json) for each reading.
Data is saved to fingerprints.json so you can stop and resume.

Run:  python3 zonefit.py
"""

import json, math, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCANNER = os.path.join(HERE, "wifiscan")     # the compiled Swift binary
STORE = os.path.join(HERE, "fingerprints.json")
FLOOR = -100        # dBm assumed for an access point not seen in a scan
KNN_K = 3           # neighbors for KNN
SOFTMAX_T = 8.0     # temperature for centroid confidence

# ----------------------------- scanning -----------------------------
def scan():
    """Return one fingerprint: {"SSID@channel": rssi, ...}."""
    if not os.path.exists(SCANNER):
        sys.exit(f"Scanner not found at {SCANNER}\n"
                 f"Build it first:\n"
                 f"  swiftc wifiscan.swift -o wifiscan -framework CoreWLAN "
                 f"-framework CoreLocation -framework Foundation")
    try:
        out = subprocess.run([SCANNER, "--json"], capture_output=True,
                             text=True, timeout=20)
    except subprocess.TimeoutExpired:
        print("  ! scan timed out"); return {}
    if out.returncode != 0:
        print("  ! scan error:", out.stderr.strip()); return {}
    try:
        nets = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        print("  ! could not parse scanner output"); return {}
    fp = {}
    for n in nets:
        ssid = n.get("ssid") or "<hidden>"
        key = f"{ssid}@{n.get('ch', 0)}"
        # if the same SSID/channel appears twice, keep the stronger reading
        if key not in fp or n["rssi"] > fp[key]:
            fp[key] = n["rssi"]
    return fp

# ----------------------------- storage -----------------------------
def load():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE))
        except Exception:
            pass
    return {"samples": []}

def save(db):
    json.dump(db, open(STORE, "w"), indent=2)
    print(f"  saved -> {STORE}")

def counts(db):
    c = {}
    for s in db["samples"]:
        c[s["zone"]] = c.get(s["zone"], 0) + 1
    return c

# ----------------------------- math -----------------------------
def feature_keys(db, *extra):
    keys = set()
    for s in db["samples"]:
        keys.update(s["fp"].keys())
    for fp in extra:
        keys.update(fp.keys())
    return sorted(keys)

def vec(fp, keys):
    return [fp.get(k, FLOOR) for k in keys]

def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

def predict_centroid(db, fp):
    if not db["samples"]:
        return None
    keys = feature_keys(db, fp)
    q = vec(fp, keys)
    # mean vector per zone
    by_zone = {}
    for s in db["samples"]:
        by_zone.setdefault(s["zone"], []).append(vec(s["fp"], keys))
    zones, dists = [], []
    for z, vs in by_zone.items():
        mean = [sum(col) / len(vs) for col in zip(*vs)]
        # normalize by feature count so distance scale is stable
        d = dist(q, mean) / math.sqrt(len(keys) or 1)
        zones.append(z); dists.append(d)
    # softmax over -distance for a 0..1 confidence
    exps = [math.exp(-d / SOFTMAX_T) for d in dists]
    tot = sum(exps) or 1
    ranked = sorted(zip(zones, dists, exps), key=lambda t: t[1])
    best_z, best_d, best_e = ranked[0]
    return {"zone": best_z, "conf": best_e / tot,
            "detail": f"nearest centroid d={best_d:.1f}" +
                      (f", runner-up {ranked[1][0]} d={ranked[1][1]:.1f}" if len(ranked) > 1 else "")}

def predict_knn(db, fp, k=KNN_K):
    if not db["samples"]:
        return None
    keys = feature_keys(db, fp)
    q = vec(fp, keys)
    scored = sorted(((dist(q, vec(s["fp"], keys)), s["zone"]) for s in db["samples"]),
                    key=lambda t: t[0])
    kk = min(k, len(scored))
    votes = {}
    for _, z in scored[:kk]:
        votes[z] = votes.get(z, 0) + 1
    best = max(votes, key=votes.get)
    return {"zone": best, "conf": votes[best] / kk,
            "detail": f"{votes[best]}/{kk} of nearest neighbors"}

# ----------------------------- REPL -----------------------------
HELP = """commands:
  <zone name>      record one fingerprint here, labeled with that zone
  auto <zone> <n>  record n fingerprints for a zone (waits ~5s between, for WiFi throttle)
  p                predict current zone (centroid + KNN)
  list             show how many samples per zone
  del <zone>       delete all samples for a zone
  save             write data to disk
  help             show this
  q                save and quit"""

def main():
    db = load()
    print("zonefit — WiFi fingerprint zone tester")
    if db["samples"]:
        print("loaded:", ", ".join(f"{z}:{n}" for z, n in counts(db).items()))
    print(HELP)
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            save(db); print(); break
        if not line:
            continue
        cmd = line.split()
        head = cmd[0].lower()

        if head == "q":
            save(db); break
        elif head == "help":
            print(HELP)
        elif head == "list":
            c = counts(db)
            print("  " + (", ".join(f"{z}: {n}" for z, n in c.items()) if c else "(no samples yet)"))
        elif head == "save":
            save(db)
        elif head == "del" and len(cmd) >= 2:
            z = cmd[1]
            before = len(db["samples"])
            db["samples"] = [s for s in db["samples"] if s["zone"] != z]
            print(f"  removed {before - len(db['samples'])} samples for '{z}'")
        elif head == "auto" and len(cmd) >= 3:
            z, n = cmd[1], int(cmd[2])
            for i in range(n):
                fp = scan()
                if fp:
                    db["samples"].append({"zone": z, "fp": fp})
                    print(f"  [{i+1}/{n}] {z}: {len(fp)} APs")
                if i < n - 1:
                    time.sleep(5)   # macOS throttles scans to ~ every 4-6s
            save(db)
        elif head == "p":
            fp = scan()
            if not fp:
                continue
            if not db["samples"]:
                print("  no calibration samples yet — record some zones first")
                continue
            c = predict_centroid(db, fp)
            k = predict_knn(db, fp)
            print(f"  saw {len(fp)} APs")
            print(f"  CENTROID -> {c['zone']:<12} {c['conf']*100:4.0f}%   ({c['detail']})")
            print(f"  KNN      -> {k['zone']:<12} {k['conf']*100:4.0f}%   ({k['detail']})")
        else:
            # treat the whole line as a zone label -> record one sample
            z = line
            fp = scan()
            if fp:
                db["samples"].append({"zone": z, "fp": fp})
                print(f"  recorded '{z}' ({len(fp)} APs). total for zone: {counts(db)[z]}")

if __name__ == "__main__":
    main()
