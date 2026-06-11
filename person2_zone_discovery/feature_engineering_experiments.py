"""
Feature Engineering Experiments for Zone Discovery
Tests signal normalization, temporal smoothing, and dimensionality reduction
"""

import json
import numpy as np
from collections import defaultdict
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
import hdbscan
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score
import warnings
warnings.filterwarnings('ignore')

print("="*80)
print("FEATURE ENGINEERING EXPERIMENTS")
print("="*80)

# Load data
print("\n[1/7] Loading data...")
with open('person_one_cleaning_data/converted_data/converted_ble_data.json') as f:
    records = json.load(f)

print(f"   Loaded {len(records)} records")

# Load ground truth for validation
print("\n[2/7] Loading ground truth positions...")
trajectory_files = [
    ('rectangular_with_rotation', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/rectangular_with_rotation_all_sensors.mbd'),
    ('rectangular_without_rotation', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/rectangular_without_rotation_all_sensors.mbd'),
    ('straight_01', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/straight_01_all_sensors.mbd'),
    ('straight_02', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/straight_02_all_sensors.mbd'),
    ('straight_03', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/straight_03_all_sensors.mbd'),
    ('straight_04', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/straight_04_all_sensors.mbd'),
    ('straight_05', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/straight_05_all_sensors.mbd'),
    ('zigzagging_with_rotation', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/zigzagging_with_rotation_all_sensors.mbd'),
    ('zigzagging_without_rotation', 'person_one_cleaning_data/Position-Annotated-BLE-RSSI-Dataset/trk/zigzagging_without_rotation_all_sensors.mbd'),
]

positions_by_trajectory = {}
for traj_name, filepath in trajectory_files:
    positions = []
    with open(filepath, 'r') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) >= 7:
                timestamp_ms = int(float(parts[0]) * 1000)
                x, y = float(parts[4]), float(parts[5])
                positions.append({'timestamp_ms': timestamp_ms, 'x': x, 'y': y})
    
    by_time = defaultdict(list)
    for pos in positions:
        by_time[pos['timestamp_ms']].append(pos)
    
    avg_positions = []
    for ts, pos_list in sorted(by_time.items()):
        avg_positions.append({
            'timestamp_ms': ts,
            'x': np.mean([p['x'] for p in pos_list]),
            'y': np.mean([p['y'] for p in pos_list])
        })
    
    positions_by_trajectory[traj_name] = avg_positions

# Create ground truth labels (1m grid)
records_by_traj = defaultdict(list)
for record in records:
    records_by_traj[record['device_id']].append(record)

for traj in records_by_traj:
    records_by_traj[traj].sort(key=lambda x: x['timestamp_ms'])

ground_truth_labels = []
all_x, all_y = [], []

for trajectory, traj_records in records_by_traj.items():
    if trajectory in positions_by_trajectory:
        positions = positions_by_trajectory[trajectory]
        for i, record in enumerate(traj_records):
            if i < len(positions):
                pos = positions[i]
                all_x.append(pos['x'])
                all_y.append(pos['y'])

if all_x:
    x_min, y_min = min(all_x), min(all_y)
    grid_size = 1.0
    
    idx = 0
    for trajectory, traj_records in records_by_traj.items():
        if trajectory in positions_by_trajectory:
            positions = positions_by_trajectory[trajectory]
            for i, record in enumerate(traj_records):
                if i < len(positions):
                    pos = positions[i]
                    grid_x = int((pos['x'] - x_min) / grid_size)
                    grid_y = int((pos['y'] - y_min) / grid_size)
                    ground_truth_labels.append(f"gt_{grid_x}_{grid_y}")
                    idx += 1

print(f"   Created {len(set(ground_truth_labels))} ground truth zones")

# Helper function to extract features and run HDBSCAN
def run_experiment(name, records, feature_extractor, min_cluster_size=10):
    """Run HDBSCAN with given feature extractor"""
    
    # Extract features
    X, feature_names = feature_extractor(records)
    
    # Scale
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # Cluster
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,
        metric='euclidean',
        cluster_selection_method='eom',
    )
    
    labels = clusterer.fit_predict(X_scaled)
    
    # Calculate metrics
    n_zones = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int(np.sum(labels == -1))
    
    # Compare to ground truth
    if len(ground_truth_labels) == len(labels):
        gt_label_to_id = {label: i for i, label in enumerate(sorted(set(ground_truth_labels)))}
        disc_label_to_id = {label: i for i, label in enumerate(sorted(set(labels)))}
        
        gt_numeric = [gt_label_to_id[label] for label in ground_truth_labels]
        disc_numeric = [disc_label_to_id[label] for label in labels]
        
        ari = adjusted_rand_score(gt_numeric, disc_numeric)
        nmi = normalized_mutual_info_score(gt_numeric, disc_numeric)
    else:
        ari, nmi = 0.0, 0.0
    
    return {
        'name': name,
        'n_zones': n_zones,
        'n_noise': n_noise,
        'ari': ari,
        'nmi': nmi,
        'feature_dim': X.shape[1]
    }

# Feature extractors
print("\n[3/7] Defining feature extractors...")

def baseline_features(records):
    """Baseline: raw RSSI values"""
    rows = []
    for record in records:
        row = []
        for val in record['signal_vector']:
            row.append(val if val is not None else 0.0)
        rows.append(row)
    return np.array(rows, dtype=float), None

def normalized_per_sensor(records):
    """2A: Normalize each sensor independently (z-score)"""
    X_raw, _ = baseline_features(records)
    
    # Normalize each column (sensor) independently
    X_normalized = np.zeros_like(X_raw)
    for col in range(X_raw.shape[1]):
        col_data = X_raw[:, col]
        # Only normalize if there's variance
        if np.std(col_data) > 0:
            X_normalized[:, col] = (col_data - np.mean(col_data)) / np.std(col_data)
        else:
            X_normalized[:, col] = col_data
    
    return X_normalized, None

def rssi_ratios(records):
    """2A: RSSI ratios between sensors (more robust to absolute levels)"""
    X_raw, _ = baseline_features(records)
    
    rows = []
    for row in X_raw:
        ratios = []
        # Compute all pairwise ratios
        for i in range(len(row)):
            for j in range(i+1, len(row)):
                if row[j] != 0:
                    ratios.append(row[i] / row[j])
                else:
                    ratios.append(0.0)
        rows.append(ratios)
    
    return np.array(rows, dtype=float), None

def temporal_smoothed(records, window_size=3):
    """2B: Apply moving average to smooth temporal variations"""
    # Group by trajectory
    by_traj = defaultdict(list)
    for i, record in enumerate(records):
        by_traj[record['device_id']].append((i, record))
    
    # Sort by time within trajectory
    for traj in by_traj:
        by_traj[traj].sort(key=lambda x: x[1]['timestamp_ms'])
    
    # Apply smoothing
    X_raw, _ = baseline_features(records)
    X_smoothed = np.copy(X_raw)
    
    for traj, indexed_records in by_traj.items():
        indices = [idx for idx, _ in indexed_records]
        
        for i, orig_idx in enumerate(indices):
            # Get window around this point
            start = max(0, i - window_size // 2)
            end = min(len(indices), i + window_size // 2 + 1)
            
            window_indices = indices[start:end]
            window_values = X_raw[window_indices]
            
            # Average across window
            X_smoothed[orig_idx] = np.mean(window_values, axis=0)
    
    return X_smoothed, None

def pca_reduced(records, n_components=7):
    """2C: PCA dimensionality reduction"""
    X_raw, _ = baseline_features(records)
    
    # Remove zero-variance columns first
    X_filtered = X_raw[:, np.std(X_raw, axis=0) > 0]
    
    # Apply PCA
    n_components = min(n_components, X_filtered.shape[1], X_filtered.shape[0] - 1)
    pca = PCA(n_components=n_components)
    X_pca = pca.fit_transform(X_filtered)
    
    return X_pca, None

def combined_all(records):
    """Combination: Normalize + Smooth + PCA"""
    # Step 1: Normalize
    X_norm, _ = normalized_per_sensor(records)
    
    # Step 2: Smooth
    by_traj = defaultdict(list)
    for i, record in enumerate(records):
        by_traj[record['device_id']].append((i, record))
    
    for traj in by_traj:
        by_traj[traj].sort(key=lambda x: x[1]['timestamp_ms'])
    
    X_smoothed = np.copy(X_norm)
    window_size = 3
    
    for traj, indexed_records in by_traj.items():
        indices = [idx for idx, _ in indexed_records]
        
        for i, orig_idx in enumerate(indices):
            start = max(0, i - window_size // 2)
            end = min(len(indices), i + window_size // 2 + 1)
            
            window_indices = indices[start:end]
            window_values = X_norm[window_indices]
            
            X_smoothed[orig_idx] = np.mean(window_values, axis=0)
    
    # Step 3: PCA
    X_filtered = X_smoothed[:, np.std(X_smoothed, axis=0) > 0]
    n_components = min(7, X_filtered.shape[1], X_filtered.shape[0] - 1)
    pca = PCA(n_components=n_components)
    X_pca = pca.fit_transform(X_filtered)
    
    return X_pca, None

# Run experiments
print("\n[4/7] Running experiments...")
print("-" * 80)

experiments = [
    ("Baseline (Raw RSSI)", baseline_features),
    ("2A: Per-Sensor Normalization", normalized_per_sensor),
    ("2A: RSSI Ratios", rssi_ratios),
    ("2B: Temporal Smoothing", temporal_smoothed),
    ("2C: PCA Reduction (7 comp)", pca_reduced),
    ("Combined (Norm+Smooth+PCA)", combined_all),
]

results = []
for name, extractor in experiments:
    print(f"\n  Testing: {name}...")
    result = run_experiment(name, records, extractor, min_cluster_size=10)
    results.append(result)
    print(f"    Zones: {result['n_zones']}, Noise: {result['n_noise']}, ARI: {result['ari']:.4f}, NMI: {result['nmi']:.4f}")

# Try different min_cluster_size for best methods
print("\n[5/7] Testing best methods with different min_cluster_size...")
print("-" * 80)

best_methods = [
    ("Combined (Norm+Smooth+PCA)", combined_all),
]

cluster_sizes = [15, 20, 30, 40]
tuning_results = []

for name, extractor in best_methods:
    for min_size in cluster_sizes:
        print(f"\n  {name} with min_cluster_size={min_size}...")
        result = run_experiment(name, records, extractor, min_cluster_size=min_size)
        tuning_results.append(result)
        print(f"    Zones: {result['n_zones']}, Noise: {result['n_noise']}, ARI: {result['ari']:.4f}, NMI: {result['nmi']:.4f}")

# Summary
print("\n" + "="*80)
print("RESULTS SUMMARY")
print("="*80)

print("\n📊 Baseline Comparison:")
print(f"{'Method':<35} {'Zones':>6} {'Noise':>6} {'ARI':>8} {'NMI':>8} {'Dims':>6}")
print("-" * 80)

for result in results:
    print(f"{result['name']:<35} {result['n_zones']:>6} {result['n_noise']:>6} {result['ari']:>8.4f} {result['nmi']:>8.4f} {result['feature_dim']:>6}")

# Find best
best_result = max(results, key=lambda x: x['ari'])
print(f"\n⭐ Best Method: {best_result['name']}")
print(f"   ARI: {best_result['ari']:.4f} (vs baseline: {results[0]['ari']:.4f})")
print(f"   Improvement: {((best_result['ari'] - results[0]['ari']) / max(results[0]['ari'], 0.001) * 100):.1f}%")

if tuning_results:
    print("\n📊 Parameter Tuning (Best Method):")
    print(f"{'min_cluster_size':>17} {'Zones':>6} {'Noise':>6} {'ARI':>8} {'NMI':>8}")
    print("-" * 60)
    for result in tuning_results:
        min_size = result['name'].split('=')[-1] if '=' in result['name'] else "N/A"
        print(f"{min_size:>17} {result['n_zones']:>6} {result['n_noise']:>6} {result['ari']:>8.4f} {result['nmi']:>8.4f}")
    
    best_tuned = max(tuning_results, key=lambda x: x['ari'])
    print(f"\n⭐ Best Configuration:")
    print(f"   Method: Combined (Norm+Smooth+PCA)")
    print(f"   min_cluster_size: Extract from name")
    print(f"   ARI: {best_tuned['ari']:.4f}")
    print(f"   Zones: {best_tuned['n_zones']} (target: ~11)")

print("\n" + "="*80)
print("RECOMMENDATIONS")
print("="*80)

if best_result['ari'] > results[0]['ari'] * 1.5:
    print("✅ Feature engineering provides significant improvement!")
else:
    print("⚠️  Feature engineering provides modest improvement")

print(f"\n1. Use: {best_result['name']}")
print(f"2. Achieved ARI: {best_result['ari']:.4f} (baseline: {results[0]['ari']:.4f})")
print(f"3. Discovered {best_result['n_zones']} zones (target: ~11)")

if best_result['n_zones'] > 15:
    print(f"4. Still over-segmenting - increase min_cluster_size to 20-30")
elif best_result['n_zones'] < 8:
    print(f"4. Under-segmenting - decrease min_cluster_size to 5-8")
else:
    print(f"4. Zone count looks reasonable!")

print("\n" + "="*80)