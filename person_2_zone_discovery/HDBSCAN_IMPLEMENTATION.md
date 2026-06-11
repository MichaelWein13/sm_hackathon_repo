# HDBSCAN Implementation for Zone Discovery

## Overview

This module now uses **HDBSCAN** (Hierarchical Density-Based Spatial Clustering of Applications with Noise) instead of K-Means for automatic zone discovery. This addresses the fundamental limitation of K-Means: having to pre-specify the number of zones.

## Why HDBSCAN?

### Key Advantages

1. **Automatic Zone Count Discovery**: HDBSCAN discovers the number of zones automatically based on the density structure of the data - no need to guess or iterate.

2. **Non-Spherical Zone Shapes**: Unlike K-Means which assumes spherical clusters, HDBSCAN can find zones of arbitrary shapes, which is more realistic for physical spaces.

3. **Transition/Corridor Detection**: HDBSCAN naturally identifies low-density points as "noise" (label `-1`), which we interpret as **transition areas** or **corridors** between zones - a free bonus feature that's perfect for indoor positioning.

4. **Single Key Parameter**: Effectively one hyperparameter that matters (`min_cluster_size`), making setup minimal compared to K-Means with silhouette score iteration.

5. **Density-Based**: Works well for varying density zones (e.g., a crowded office area vs. a quiet conference room).

## How It Works

### Algorithm Parameters

```python
clusterer = hdbscan.HDBSCAN(
    min_cluster_size=5,      # Minimum points to form a dense zone
    min_samples=1,           # Sensitivity to local density variations
    cluster_selection_epsilon=0.0,
    metric='euclidean',
    cluster_selection_method='eom',  # Excess of Mass
)
```

**`min_cluster_size`** (default: 5)
- The primary tuning parameter
- Minimum number of observations needed to form a dense zone
- Smaller values = more zones, potentially detecting smaller rooms
- Larger values = fewer zones, focusing on major areas
- Recommended: 3-10 depending on your data granularity

**`min_samples`** (default: 1)
- Controls how conservative the clustering is
- Set to 1 for maximum sensitivity to local density variations
- Higher values make clustering more conservative

**`cluster_selection_method`** = 'eom' (Excess of Mass)
- Good for zones with varying densities
- Alternative: 'leaf' (more zones, potentially noisier)

### Output Format

#### Zone Assignments

Each observation is assigned to either:
- A **zone** (e.g., `zone_1`, `zone_2`, ...)
- A **transition** area (for corridor/noise points)

```json
{
  "timestamp_ms": 1710000000000,
  "device_id": "person_1",
  "zone_id": "zone_2",
  "zone_confidence": 0.9399
}
```

**Confidence Interpretation:**
- For **zones**: Probability of membership in that zone (0.0 to 1.0)
- For **transitions**: Inverted probability (higher = more likely to be a corridor)

#### Zone Definitions

```json
{
  "zone_id": "zone_1",
  "prototype_vector": [2.41, 2.87, 3.02, ...],
  "zone_size": 15
}
```

**Transition zones** have empty prototype vectors:
```json
{
  "zone_id": "transition",
  "prototype_vector": [],
  "zone_size": 3
}
```

## Usage

### Basic Usage

```bash
python zone_discovery.py \
  --input data/observations.json \
  --min-cluster-size 5
```

### Tuning min_cluster_size

**Small values (2-3)**: Detect fine-grained zones
```bash
python zone_discovery.py --input data.json --min-cluster-size 2
```
- Use when: You have many small rooms or want to detect sub-zones
- Trade-off: May create too many zones, some might not be meaningful

**Medium values (5-10)**: Balanced detection (recommended)
```bash
python zone_discovery.py --input data.json --min-cluster-size 5
```
- Use when: Standard indoor environments with typical room sizes
- Trade-off: Good balance between precision and generalization

**Large values (15+)**: Detect only major zones
```bash
python zone_discovery.py --input data.json --min-cluster-size 15
```
- Use when: You want only main areas, ignoring smaller spaces
- Trade-off: May miss legitimate small zones

## Transition/Noise Handling

### What are Transitions?

Points labeled as `zone_id: "transition"` are low-density observations that don't fit well into any dense zone. These typically represent:

- **Corridors**: Paths between rooms
- **Doorways**: Transition areas between zones
- **Transient positions**: Brief stops while moving
- **Outliers**: Anomalous readings

### Interpreting Transition Confidence

```json
{
  "zone_id": "transition",
  "zone_confidence": 0.85
}
```

High confidence (0.7-1.0) = High certainty this is a corridor/transition
Low confidence (0.0-0.3) = Borderline case, might belong to a zone

### Filtering Transitions

If you want to exclude transitions from downstream processing:

```python
valid_zones = [a for a in assignments if a["zone_id"] != "transition"]
```

## Comparison with K-Means

| Feature | K-Means (Old) | HDBSCAN (New) |
|---------|---------------|---------------|
| **Zone count** | Must specify via silhouette search | Automatic discovery |
| **Setup complexity** | Iterate over k=2 to max_zones | Single parameter |
| **Zone shapes** | Spherical only | Arbitrary shapes |
| **Transition detection** | No | Yes (noise points) |
| **Varying density** | Poor | Excellent |
| **Computational cost** | Low | Moderate |
| **Interpretability** | Simple centroids | Density-based regions |

## Migration from K-Means

### Command Line Changes

**Old:**
```bash
python zone_discovery.py --input data.json --max-zones 8
```

**New:**
```bash
python zone_discovery.py --input data.json --min-cluster-size 5
```

### Output Changes

1. **Zone IDs**: Still use `zone_1`, `zone_2`, etc.
2. **New**: `transition` zone for corridors
3. **Confidence**: Now based on HDBSCAN membership probabilities (more meaningful)
4. **Zone count**: No longer capped; discovered automatically

## Example Output

### Sample Run

```bash
$ python zone_discovery.py --input sample_input.json --min-cluster-size 2

Saved 5 assignment records to person2_zone_discovery/outputs/assignments.json
Saved 3 zone definitions to person2_zone_discovery/outputs/zones.json

Discovered 3 zones
```

### Typical Results

For an office environment with 100 observations:
- **3-5 zones**: Main office areas (open space, conference rooms, break room)
- **10-20% transitions**: Corridors and doorways
- **High confidence**: Most assignments have >0.85 confidence

## Troubleshooting

### Too Many Zones

**Problem**: HDBSCAN finds 15+ zones, seems excessive

**Solution**: Increase `min_cluster_size`
```bash
python zone_discovery.py --input data.json --min-cluster-size 10
```

### Everything is One Zone

**Problem**: All points assigned to a single zone

**Solution**: Decrease `min_cluster_size`
```bash
python zone_discovery.py --input data.json --min-cluster-size 2
```

### Too Many Transitions

**Problem**: >50% of points are labeled "transition"

**Causes**:
- Data is very sparse or noisy
- Observations don't cluster well naturally
- `min_cluster_size` is too large

**Solutions**:
1. Reduce `min_cluster_size`
2. Check data quality (are signal vectors meaningful?)
3. Consider if your environment actually has well-defined zones

## Technical Details

### Dependencies

```
hdbscan>=0.8.44
numpy>=1.20
scipy>=1.0
scikit-learn>=1.6
```

Install:
```bash
pip install hdbscan
```

### Implementation Notes

1. **Feature Scaling**: Data is StandardScaler-normalized before clustering
2. **Cluster Centers**: Computed as mean of all points in each zone
3. **Label Mapping**: Internal HDBSCAN labels mapped to user-friendly `zone_N` format
4. **Noise Handling**: Label `-1` mapped to `"transition"`

## References

- HDBSCAN Paper: [Campello, Moulavi, Sander (2013)](https://link.springer.com/chapter/10.1007/978-3-642-37456-2_14)
- HDBSCAN Documentation: https://hdbscan.readthedocs.io/
- Comparison with K-Means: https://scikit-learn.org/stable/modules/clustering.html