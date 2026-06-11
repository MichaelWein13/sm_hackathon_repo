import json
import numpy as np
from collections import defaultdict
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, homogeneity_score, completeness_score, v_measure_score
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

print("="*80)
print("ZONE DISCOVERY VALIDATION AGAINST GROUND TRUTH")
print("="*80)

# Load original trajectory data with positions
print("\n[1/6] Loading position data from trajectory files...")
trajectory_files = [
    ('rectangular_with_rotation', '../Position-Annotated-BLE-RSSI-Dataset/trk/rectangular_with_rotation_all_sensors.mbd'),
    ('rectangular_without_rotation', '../Position-Annotated-BLE-RSSI-Dataset/trk/rectangular_without_rotation_all_sensors.mbd'),
    ('straight_01', '../Position-Annotated-BLE-RSSI-Dataset/trk/straight_01_all_sensors.mbd'),
    ('straight_02', '../Position-Annotated-BLE-RSSI-Dataset/trk/straight_02_all_sensors.mbd'),
    ('straight_03', '../Position-Annotated-BLE-RSSI-Dataset/trk/straight_03_all_sensors.mbd'),
    ('straight_04', '../Position-Annotated-BLE-RSSI-Dataset/trk/straight_04_all_sensors.mbd'),
    ('straight_05', '../Position-Annotated-BLE-RSSI-Dataset/trk/straight_05_all_sensors.mbd'),
    ('zigzagging_with_rotation', '../Position-Annotated-BLE-RSSI-Dataset/trk/zigzagging_with_rotation_all_sensors.mbd'),
    ('zigzagging_without_rotation', '../Position-Annotated-BLE-RSSI-Dataset/trk/zigzagging_without_rotation_all_sensors.mbd'),
]

# Parse position data
# Format: timestamp,MAC_sensor,MAC_beacon,RSSI,x,y,z,[orientation_matrix]
positions_by_trajectory = {}

for traj_name, filepath in trajectory_files:
    positions = []
    with open(filepath, 'r') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) >= 7:
                timestamp_sec = float(parts[0])
                timestamp_ms = int(timestamp_sec * 1000)
                x, y, z = float(parts[4]), float(parts[5]), float(parts[6])
                positions.append({
                    'timestamp_ms': timestamp_ms,
                    'x': x,
                    'y': y,
                    'z': z
                })
    
    # Group by timestamp and average (multiple sensors measure simultaneously)
    by_time = defaultdict(list)
    for pos in positions:
        by_time[pos['timestamp_ms']].append(pos)
    
    avg_positions = []
    for ts, pos_list in sorted(by_time.items()):
        avg_positions.append({
            'timestamp_ms': ts,
            'x': np.mean([p['x'] for p in pos_list]),
            'y': np.mean([p['y'] for p in pos_list]),
            'z': np.mean([p['z'] for p in pos_list])
        })
    
    positions_by_trajectory[traj_name] = avg_positions

total_positions = sum(len(p) for p in positions_by_trajectory.values())
print(f"   Loaded {total_positions} position records from {len(trajectory_files)} trajectories")

# Load zone assignments
print("\n[2/6] Loading zone assignments...")
import sys
if len(sys.argv) > 1 and '--assignments' in sys.argv:
    assignments_file = sys.argv[sys.argv.index('--assignments') + 1]
else:
    assignments_file = 'outputs/assignments.json'
    
with open(assignments_file) as f:
    assignments = json.load(f)

print(f"   Loaded {len(assignments)} zone assignments")

# Match assignments to positions
print("\n[3/6] Matching assignments to ground truth positions...")

# Group assignments by trajectory
assignments_by_traj = defaultdict(list)
for assignment in assignments:
    assignments_by_traj[assignment['device_id']].append(assignment)

# Sort by timestamp within each trajectory
for traj in assignments_by_traj:
    assignments_by_traj[traj].sort(key=lambda x: x['timestamp_ms'])

matched_data = []

for trajectory, traj_assignments in assignments_by_traj.items():
    if trajectory in positions_by_trajectory:
        positions = positions_by_trajectory[trajectory]
        
        # Match by index - both are sorted by time
        for i, assignment in enumerate(traj_assignments):
            if i < len(positions):
                pos = positions[i]
                matched_data.append({
                    'trajectory': trajectory,
                    'timestamp_ms': assignment['timestamp_ms'],
                    'zone_id': assignment['zone_id'],
                    'zone_confidence': assignment['zone_confidence'],
                    'x': pos['x'],
                    'y': pos['y'],
                    'z': pos['z']
                })

print(f"   Matched {len(matched_data)} assignments to positions ({100*len(matched_data)/len(assignments):.1f}% coverage)")

# Create grid-based ground truth zones
print("\n[4/6] Creating grid-based ground truth zones...")

all_x = [m['x'] for m in matched_data]
all_y = [m['y'] for m in matched_data]

x_min, x_max = min(all_x), max(all_x)
y_min, y_max = min(all_y), max(all_y)

print(f"   Position bounds: x=[{x_min:.2f}, {x_max:.2f}], y=[{y_min:.2f}, {y_max:.2f}]")

# Try different grid sizes
grid_sizes = [1.0, 1.5, 2.0, 3.0]
best_grid_size = None
best_zone_count = None

for grid_size in grid_sizes:
    unique_cells = set()
    for m in matched_data:
        grid_x = int((m['x'] - x_min) / grid_size)
        grid_y = int((m['y'] - y_min) / grid_size)
        unique_cells.add((grid_x, grid_y))
    
    zone_count = len(unique_cells)
    print(f"   Grid size {grid_size}m: {zone_count} ground truth zones")
    
    # Choose grid size that gives ~11 ground truth zones (known from the environment)
    if best_zone_count is None or abs(zone_count - 11) < abs(best_zone_count - 11):
        best_grid_size = grid_size
        best_zone_count = zone_count

# Count actual discovered zones
unique_discovered_zones = len([z for z in set(discovered_labels) if z != 'transition'])
print(f"\n   Selected grid size: {best_grid_size}m ({best_zone_count} zones, discovered {unique_discovered_zones} zones)")

# Assign ground truth zones
for m in matched_data:
    grid_x = int((m['x'] - x_min) / best_grid_size)
    grid_y = int((m['y'] - y_min) / best_grid_size)
    m['ground_truth_zone'] = f"gt_{grid_x}_{grid_y}"

# Calculate accuracy metrics
print("\n[5/6] Calculating accuracy metrics...")

# Prepare labels for sklearn metrics
discovered_labels = [m['zone_id'] for m in matched_data]
ground_truth_labels = [m['ground_truth_zone'] for m in matched_data]

# Convert to numeric for sklearn
discovered_label_to_id = {label: i for i, label in enumerate(sorted(set(discovered_labels)))}
gt_label_to_id = {label: i for i, label in enumerate(sorted(set(ground_truth_labels)))}

discovered_numeric = [discovered_label_to_id[label] for label in discovered_labels]
gt_numeric = [gt_label_to_id[label] for label in ground_truth_labels]

# Calculate metrics
ari = adjusted_rand_score(gt_numeric, discovered_numeric)
nmi = normalized_mutual_info_score(gt_numeric, discovered_numeric)
homogeneity = homogeneity_score(gt_numeric, discovered_numeric)
completeness = completeness_score(gt_numeric, discovered_numeric)
v_measure = v_measure_score(gt_numeric, discovered_numeric)

print("\n" + "="*80)
print("CLUSTERING ACCURACY METRICS")
print("="*80)
print(f"\nAdjusted Rand Index (ARI):      {ari:.4f}")
print("  Range: [-1, 1], Random=0, Perfect=1")
print("  Measures agreement between clusterings")
print(f"\nNormalized Mutual Information:  {nmi:.4f}")
print("  Range: [0, 1], Higher is better")
print("  Measures shared information between clusterings")
print(f"\nHomogeneity:                    {homogeneity:.4f}")
print("  Range: [0, 1], Perfect=1")
print("  Each discovered zone contains only members of a single GT zone")
print(f"\nCompleteness:                   {completeness:.4f}")
print("  Range: [0, 1], Perfect=1")
print("  All members of a GT zone are in the same discovered zone")
print(f"\nV-Measure (harmonic mean):      {v_measure:.4f}")
print("  Range: [0, 1], Perfect=1")
print("  Harmonic mean of homogeneity and completeness")

# Zone-level analysis
print("\n" + "="*80)
print("ZONE MAPPING ANALYSIS")
print("="*80)

# Map discovered zones to GT zones
zone_to_gt = defaultdict(lambda: defaultdict(int))
for m in matched_data:
    zone_to_gt[m['zone_id']][m['ground_truth_zone']] += 1

print("\nTop ground truth zone for each discovered zone:")
for zone_id in sorted(zone_to_gt.keys()):
    gt_zones = zone_to_gt[zone_id]
    total = sum(gt_zones.values())
    top_gt = max(gt_zones.items(), key=lambda x: x[1])
    purity = top_gt[1] / total
    
    num_gt_zones = len(gt_zones)
    print(f"  {zone_id}: maps to {num_gt_zones} GT zones, primary={top_gt[0]} ({top_gt[1]}/{total}, purity={purity:.2f})")

# Overall purity
total_matches = sum(max(gt_zones.values()) for gt_zones in zone_to_gt.values())
overall_purity = total_matches / len(matched_data)
print(f"\nOverall Purity: {overall_purity:.4f}")
print("  (Fraction of points in correct dominant GT zone)")

# Spatial visualization
print("\n[6/6] Creating visualizations...")

fig, axes = plt.subplots(1, 2, figsize=(16, 7))

# Plot 1: Ground Truth Zones
ax1 = axes[0]
gt_colors = {}
unique_gt = sorted(set(ground_truth_labels))
for i, gt_zone in enumerate(unique_gt):
    gt_colors[gt_zone] = plt.cm.tab20(i % 20)

for m in matched_data:
    color = gt_colors[m['ground_truth_zone']]
    ax1.scatter(m['x'], m['y'], c=[color], s=30, alpha=0.6, edgecolors='none')

ax1.set_xlabel('X Position (m)', fontsize=12)
ax1.set_ylabel('Y Position (m)', fontsize=12)
ax1.set_title(f'Ground Truth Zones (Grid: {best_grid_size}m, {best_zone_count} zones)', fontsize=14, fontweight='bold')
ax1.grid(True, alpha=0.3)
ax1.set_aspect('equal')

# Plot 2: Discovered Zones
ax2 = axes[1]
zone_colors = {}
unique_zones = sorted([z for z in set(discovered_labels) if z != 'transition'])
for i, zone in enumerate(unique_zones):
    zone_colors[zone] = plt.cm.tab20(i % 20)
zone_colors['transition'] = 'lightgray'

for m in matched_data:
    color = zone_colors[m['zone_id']]
    marker = 'x' if m['zone_id'] == 'transition' else 'o'
    size = 20 if m['zone_id'] == 'transition' else 30
    ax2.scatter(m['x'], m['y'], c=[color], s=size, alpha=0.6, marker=marker, edgecolors='none')

# Count discovered zones
num_discovered = len([z for z in set(discovered_labels) if z != 'transition'])
ax2.set_xlabel('X Position (m)', fontsize=12)
ax2.set_ylabel('Y Position (m)', fontsize=12)
ax2.set_title(f'HDBSCAN Discovered Zones ({num_discovered} zones + transitions)', fontsize=14, fontweight='bold')
ax2.grid(True, alpha=0.3)
ax2.set_aspect('equal')

# Add transition legend to plot 2
transition_patch = mpatches.Patch(color='lightgray', label='Transition/Corridor')
ax2.legend(handles=[transition_patch], loc='upper right')

plt.tight_layout()
output_file = assignments_file.replace('assignments', 'validation').replace('.json', '.png')
plt.savefig(output_file, dpi=150, bbox_inches='tight')
print(f"\n   Saved visualization to: {output_file}")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
num_discovered = len([z for z in set(discovered_labels) if z != 'transition'])
num_transition = sum(1 for z in discovered_labels if z == 'transition')
print(f"Discovered Zones:     {num_discovered} zones + {num_transition} transition points")
print(f"Ground Truth Zones:   {best_zone_count} (grid size: {best_grid_size}m)")
print(f"Data Coverage:        {len(matched_data)}/{len(assignments)} ({100*len(matched_data)/len(assignments):.1f}%)")
print(f"\nAccuracy (ARI):       {ari:.4f}")
print(f"Information (NMI):    {nmi:.4f}")
print(f"Purity:               {overall_purity:.4f}")
print(f"V-Measure:            {v_measure:.4f}")

# Interpretation
print("\n" + "="*80)
print("INTERPRETATION")
print("="*80)

if ari > 0.7:
    print("✅ EXCELLENT: Strong agreement with ground truth spatial zones")
elif ari > 0.5:
    print("✓ GOOD: Reasonable agreement with ground truth")
elif ari > 0.3:
    print("⚠️  FAIR: Moderate agreement, some misalignment")
else:
    print("❌ POOR: Weak agreement with ground truth")

if overall_purity > 0.8:
    print("✅ High purity: Most zones are spatially coherent")
elif overall_purity > 0.6:
    print("✓ Moderate purity: Zones generally coherent")
else:
    print("⚠️  Low purity: Zones may be spatially fragmented")

print("\n" + "="*80)