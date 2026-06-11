import json
import numpy as np
from collections import defaultdict
import os

print("="*80)
print("ZONE DISCOVERY ACCURACY ANALYSIS")
print("="*80)

# Load zone assignments
with open('person2_zone_discovery/outputs/assignments.json') as f:
    assignments = json.load(f)

with open('person2_zone_discovery/outputs/zones.json') as f:
    zones = json.load(f)

print(f"\nLoaded {len(assignments)} zone assignments")
print(f"Loaded {len(zones)} zone definitions")

# Group assignments by trajectory and analyze
trajectories = {}
for assignment in assignments:
    traj = assignment['device_id']
    if traj not in trajectories:
        trajectories[traj] = []
    trajectories[traj].append(assignment)

print(f"\nTrajectories in dataset: {len(trajectories)}")
for traj_name in sorted(trajectories.keys()):
    print(f"  {traj_name}: {len(trajectories[traj_name])} observations")

# Analyze zone assignments
zone_counts = defaultdict(int)
zone_trajectories = defaultdict(set)

for assignment in assignments:
    zone_id = assignment['zone_id']
    zone_counts[zone_id] += 1
    zone_trajectories[zone_id].add(assignment['device_id'])

print("\n" + "="*80)
print("DISCOVERED ZONES")
print("="*80)

regular_zones = sorted([z for z in zone_counts.keys() if z != 'transition'])
print(f"\nRegular zones: {len(regular_zones)}")
for zone_id in regular_zones:
    count = zone_counts[zone_id]
    trajs = len(zone_trajectories[zone_id])
    print(f"  {zone_id}: {count} observations across {trajs} trajectories")

if 'transition' in zone_counts:
    count = zone_counts['transition']
    trajs = len(zone_trajectories['transition'])
    print(f"\n  transition: {count} observations across {trajs} trajectories")
    print(f"  (These are corridor/between-zone points)")

# Analyze zone distribution per trajectory
print("\n" + "="*80)
print("ZONE DISTRIBUTION PER TRAJECTORY")
print("="*80)

for traj_name in sorted(trajectories.keys()):
    traj_assignments = trajectories[traj_name]
    zone_dist = defaultdict(int)
    
    for assignment in traj_assignments:
        zone_dist[assignment['zone_id']] += 1
    
    print(f"\n{traj_name} ({len(traj_assignments)} observations):")
    for zone_id in sorted(zone_dist.keys(), key=lambda x: zone_dist[x], reverse=True):
        count = zone_dist[zone_id]
        pct = 100 * count / len(traj_assignments)
        print(f"  {zone_id}: {count} ({pct:.1f}%)")

# Analyze zone confidence
print("\n" + "="*80)
print("CONFIDENCE ANALYSIS")
print("="*80)

confidences_by_zone = defaultdict(list)
for assignment in assignments:
    confidences_by_zone[assignment['zone_id']].append(assignment['zone_confidence'])

print("\nAverage confidence per zone:")
for zone_id in sorted(confidences_by_zone.keys()):
    confs = confidences_by_zone[zone_id]
    avg_conf = np.mean(confs)
    min_conf = np.min(confs)
    max_conf = np.max(confs)
    print(f"  {zone_id}: avg={avg_conf:.3f}, min={min_conf:.3f}, max={max_conf:.3f}")

# Estimate ground truth from trajectory patterns
print("\n" + "="*80)
print("TRAJECTORY PATTERN ANALYSIS")
print("="*80)

print("\nTrajectory types suggest expected coverage:")
print("  - straight_* (5 trajectories): Likely 5-10 zones along straight paths")
print("  - rectangular_* (2 trajectories): Likely 4-8 zones around rectangle")
print("  - zigzag_* (2 trajectories): Likely 10-15 zones in zigzag pattern")

# Check if we're over-segmenting or under-segmenting
avg_obs_per_zone = np.mean([zone_counts[z] for z in regular_zones])
print(f"\nAverage observations per zone: {avg_obs_per_zone:.1f}")
print(f"Total regular zones: {len(regular_zones)}")
print(f"Transition points: {zone_counts.get('transition', 0)} ({100*zone_counts.get('transition', 0)/len(assignments):.1f}%)")

# Analysis of zone sizes
print("\n" + "="*80)
print("ZONE SIZE DISTRIBUTION")
print("="*80)

zone_sizes = [zone_counts[z] for z in regular_zones]
print(f"Min zone size: {min(zone_sizes) if zone_sizes else 0}")
print(f"Max zone size: {max(zone_sizes) if zone_sizes else 0}")
print(f"Median zone size: {np.median(zone_sizes) if zone_sizes else 0:.1f}")
print(f"Mean zone size: {np.mean(zone_sizes) if zone_sizes else 0:.1f}")

# Check for potential issues
print("\n" + "="*80)
print("POTENTIAL ISSUES & IMPROVEMENTS")
print("="*80)

issues = []
improvements = []

# Check for very small zones
small_zones = [z for z in regular_zones if zone_counts[z] < 5]
if small_zones:
    issues.append(f"Found {len(small_zones)} very small zones (<5 observations)")
    improvements.append("Consider increasing min_cluster_size parameter")

# Check for very large zones
large_zones = [z for z in regular_zones if zone_counts[z] > 100]
if large_zones:
    issues.append(f"Found {len(large_zones)} very large zones (>100 observations)")
    improvements.append("May be under-segmenting; consider decreasing min_cluster_size")

# Check transition ratio
transition_ratio = zone_counts.get('transition', 0) / len(assignments)
if transition_ratio > 0.3:
    issues.append(f"High transition ratio ({100*transition_ratio:.1f}%)")
    improvements.append("Too many transition points; decrease min_cluster_size")
elif transition_ratio < 0.05:
    issues.append(f"Low transition ratio ({100*transition_ratio:.1f}%)")
    improvements.append("May be missing corridors; increase min_cluster_size slightly")

# Check zone count reasonableness
if len(regular_zones) > 40:
    issues.append(f"Many zones ({len(regular_zones)}) - possible over-segmentation")
    improvements.append("Increase min_cluster_size to merge similar zones")
elif len(regular_zones) < 10:
    issues.append(f"Few zones ({len(regular_zones)}) - possible under-segmentation")
    improvements.append("Decrease min_cluster_size to find finer zones")

if issues:
    print("\nIssues detected:")
    for issue in issues:
        print(f"  ⚠️  {issue}")
    
    print("\nSuggested improvements:")
    for improvement in improvements:
        print(f"  💡 {improvement}")
else:
    print("\n✅ No major issues detected!")
    print("Zone discovery appears reasonable.")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"Discovered {len(regular_zones)} zones from {len(assignments)} observations")
print(f"Transition/corridor points: {zone_counts.get('transition', 0)} ({100*zone_counts.get('transition', 0)/len(assignments):.1f}%)")
print(f"Average zone size: {avg_obs_per_zone:.1f} observations")
print(f"Current min_cluster_size: 10 (used in zone discovery)")