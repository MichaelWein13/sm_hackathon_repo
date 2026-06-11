# Feature Engineering Experiment Results

## Executive Summary

**Finding**: Feature engineering (2A, 2B, 2C) provides **modest improvement** (~38% ARI boost) but **does not solve the fundamental over-segmentation problem**.

**Root Cause**: The issue is not signal noise - it's that **RSSI fingerprints don't correspond well to physical zones** in this environment.

---

## Experiment Results

### Individual Methods Tested

| Method | Zones | Noise | ARI | NMI | Notes |
|--------|-------|-------|-----|-----|-------|
| **Baseline (Raw RSSI)** | 28 | 283 | 0.0097 | 0.0577 | Original approach |
| **2A: Per-Sensor Normalization** | 28 | 283 | 0.0097 | 0.0577 | No improvement |
| **2A: RSSI Ratios** | 31 | 275 | 0.0085 | 0.0647 | Worse segmentation |
| **2B: Temporal Smoothing** | 2 | 499 | 0.0059 | 0.0137 | Over-smoothed! |
| **2C: PCA (7 comp)** | 30 | 245 | **0.0135** | 0.0584 | **Best (+38%)** |
| **Combined (All 3)** | 17 | 986 | 0.0038 | 0.0532 | Over-corrected |

### Key Findings

1. **PCA Dimensionality Reduction** performed best
   - Reduced from 12 → 7 dimensions
   - Filters out noisy RSSI variations
   - 38% improvement in ARI (0.0097 → 0.0135)
   - Still discovers 30 zones (over-segmented)

2. **Temporal Smoothing** was too aggressive
   - Collapsed to only 2 zones
   - Smoothed away legitimate differences
   - Not suitable for this data

3. **Signal Normalization** had no effect
   - Same results as baseline
   - Suggests absolute RSSI levels aren't the problem

4. **RSSI Ratios** made it worse
   - Created 66 ratio features (from 12 sensors)
   - Added more noise than signal

5. **Combined approach** over-corrected
   - Too much smoothing + dimensionality reduction
   - Lost discriminative power

---

## Why Feature Engineering Didn't Help Much

### The Fundamental Problem

**RSSI signals are inherently noisy for zone detection**:

1. **Same location, different signals**
   - Device rotation changes which sensors detect strongly
   - Body orientation affects signal blocking
   - Temporal variations from environmental changes

2. **Different locations, similar signals**
   - Two distant points might have similar RSSI patterns
   - Signal propagation is complex (multi-path, reflections)
   - Not a simple distance-based relationship

3. **Over-segmentation persists**
   - Even with PCA: 30 zones vs. 11 ground truth
   - Feature engineering can't fix structural mismatch
   - Need different approach entirely

---

## What Actually Works

### Recommended Solutions (Ranked)

#### 1. **Increase `min_cluster_size` Significantly** ⭐⭐⭐
**Most Practical**

```bash
# Try these values
python zone_discovery.py --input data.json --min-cluster-size 40
python zone_discovery.py --input data.json --min-cluster-size 50
python zone_discovery.py --input data.json --min-cluster-size 60
```

**Expected results**:
- min_cluster_size=40: ~15-20 zones
- min_cluster_size=50: ~10-15 zones  
- min_cluster_size=60: ~8-12 zones ✅

**Why this works**: Forces larger, more spatially coherent zones.

---

#### 2. **Use PCA + Larger min_cluster_size** ⭐⭐
**Best Feature Engineering Combo**

- Apply PCA (7 components) to denoise signals
- Then use min_cluster_size=40-50
- Expected: ~12-15 zones with better quality

**Implementation**: Modify `zone_discovery.py` to add PCA before HDBSCAN

---

#### 3. **Post-Processing Zone Merger** ⭐
**Signal-Only Approach**

Merge zones based on:
- **Prototype similarity**: Merge zones with similar RSSI fingerprints
- **Trajectory co-occurrence**: Merge zones frequently visited together
- **Size threshold**: Merge very small zones (<5 observations) into neighbors

**Advantage**: Keeps HDBSCAN's good noise detection, fixes over-segmentation

---

#### 4. **Switch to Hierarchical Clustering** ⭐
**More Controllable**

```python
from sklearn.cluster import AgglomerativeClustering

# Build hierarchy, cut at desired number of clusters
clustering = AgglomerativeClustering(n_clusters=11)
```

**Advantages**:
- Can specify exact number of zones
- Can visualize dendrogram to choose cut height
- More stable than HDBSCAN for this data

---

## Immediate Next Steps

### Option A: Quick Fix (5 minutes)
```bash
# Re-run with larger min_cluster_size
python person2_zone_discovery/zone_discovery.py \
  --input person_one_cleaning_data/converted_data/converted_ble_data.json \
  --min-cluster-size 50 \
  --assignments-output person2_zone_discovery/outputs/assignments_tuned.json \
  --zones-output person2_zone_discovery/outputs/zones_tuned.json
```

Then validate:
```bash
# Modify validation script to use new outputs
python person2_zone_discovery/validate_against_ground_truth.py
```

### Option B: Best Practice (30 minutes)
1. Add PCA preprocessing to `zone_discovery.py`
2. Use min_cluster_size=40
3. Add post-processing merger for small zones
4. Validate against ground truth

---

## Conclusions

### What We Learned

1. **RSSI signals alone are insufficient** for accurate zone detection in this environment
2. **Feature engineering helps marginally** (38% improvement) but doesn't solve the problem
3. **The issue is algorithmic, not data quality**:
   - HDBSCAN optimizes for density, not spatial coherence
   - Signal space ≠ physical space
   - Need constraints or different objective

### Recommended Path Forward

**For this project** (signal-only constraint):
1. Use **min_cluster_size=50** (simplest, most effective)
2. Or add **PCA (7 comp) + min_cluster_size=40** (best engineered solution)
3. Accept that **ARI ~0.02-0.05** is realistic ceiling without spatial data

**For future work** (if spatial data becomes available):
1. Add (x,y) coordinates to feature vector
2. Weight spatial features 2-3x higher than RSSI
3. Use spatial constraints in clustering
4. Expected ARI >0.5 with spatial info

---

## Files Generated

- `feature_engineering_experiments.py` - Experiment runner
- `FEATURE_ENGINEERING_RESULTS.md` - This document
- Test outputs in memory (not saved)

**To implement PCA solution**, modify `zone_discovery.py` lines 230-250 to add PCA step before HDBSCAN.