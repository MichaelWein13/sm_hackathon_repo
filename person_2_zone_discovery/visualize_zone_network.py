import json
import matplotlib.pyplot as plt
import networkx as nx
from collections import defaultdict
import numpy as np

print("Creating zone transition network graph...")

# Load semantic labels and assignments
with open('outputs/semantic_labels.json') as f:
    label_data = json.load(f)

with open('outputs/assignments_labeled.json') as f:
    assignments = json.load(f)

semantic_labels = label_data['semantic_labels']
stats = label_data['statistics']

# Build transition graph
transitions = defaultdict(int)
zone_sequences = defaultdict(list)

# Track transitions per trajectory
for i in range(len(assignments) - 1):
    curr = assignments[i]
    next_assign = assignments[i + 1]
    
    # Only track same trajectory transitions
    if curr['device_id'] == next_assign['device_id']:
        curr_zone = curr.get('semantic_label', 'Transitional Space')
        next_zone = next_assign.get('semantic_label', 'Transitional Space')
        
        # Skip self-loops and transitions involving transitional spaces for cleaner graph
        if curr_zone != next_zone and curr_zone != 'Transitional Space' and next_zone != 'Transitional Space':
            transitions[(curr_zone, next_zone)] += 1
            zone_sequences[curr['device_id']].append((curr_zone, next_zone))

print(f"Found {len(transitions)} unique zone transitions")

# Create directed graph
G = nx.DiGraph()

# Add nodes for all zones
tier_colors = {
    'Primary Activity Zone': '#2E86AB',
    'Secondary Hub': '#A23B72',
    'Functional Niche': '#F18F01'
}

def get_tier_color(label):
    if 'Primary' in label:
        return tier_colors['Primary Activity Zone']
    elif 'Secondary' in label:
        return tier_colors['Secondary Hub']
    else:
        return tier_colors['Functional Niche']

# Add nodes with size based on zone size
node_sizes = {}
node_colors = {}
for zone_id, label in semantic_labels.items():
    G.add_node(label)
    node_sizes[label] = stats[zone_id]['size']
    node_colors[label] = get_tier_color(label)

# Add edges with weights
for (from_zone, to_zone), count in transitions.items():
    if G.has_edge(from_zone, to_zone):
        G[from_zone][to_zone]['weight'] += count
    else:
        G.add_edge(from_zone, to_zone, weight=count)

# Create visualization
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 10))

# Left plot: Network graph with all transitions
ax1.set_title('Zone Transition Network\n(Direction shows movement flow)', 
              fontsize=16, fontweight='bold', pad=20)

# Use spring layout for better node distribution
pos = nx.spring_layout(G, k=2, iterations=50, seed=42)

# Draw nodes
node_list = list(G.nodes())
sizes = [node_sizes[node] * 2 for node in node_list]  # Scale for visibility
colors = [node_colors[node] for node in node_list]

nx.draw_networkx_nodes(G, pos, node_list, 
                       node_size=sizes,
                       node_color=colors,
                       alpha=0.9,
                       edgecolors='black',
                       linewidths=2,
                       ax=ax1)

# Draw edges with varying width based on transition frequency
edges = G.edges()
weights = [G[u][v]['weight'] for u, v in edges]
max_weight = max(weights) if weights else 1

edge_widths = [2 + (w / max_weight) * 8 for w in weights]  # Scale 2-10

nx.draw_networkx_edges(G, pos,
                       edgelist=edges,
                       width=edge_widths,
                       alpha=0.6,
                       edge_color='gray',
                       arrows=True,
                       arrowsize=20,
                       arrowstyle='->',
                       connectionstyle='arc3,rad=0.1',
                       ax=ax1)

# Draw labels with better formatting
labels = {node: node.replace('Primary Activity Zone ', 'PAZ-').replace('Secondary Hub ', 'SH-').replace('Functional Niche ', 'FN-') 
          for node in node_list}

nx.draw_networkx_labels(G, pos, labels,
                       font_size=9,
                       font_weight='bold',
                       font_color='white',
                       ax=ax1)

ax1.axis('off')

# Add legend
from matplotlib.patches import Patch
legend_elements = [
    Patch(facecolor=tier_colors['Primary Activity Zone'], edgecolor='black', label='Primary Activity Zones'),
    Patch(facecolor=tier_colors['Secondary Hub'], edgecolor='black', label='Secondary Hubs'),
    Patch(facecolor=tier_colors['Functional Niche'], edgecolor='black', label='Functional Niches')
]
ax1.legend(handles=legend_elements, loc='upper left', fontsize=11, framealpha=0.9)

# Right plot: Top transition flows
ax2.set_title('Most Frequent Zone Transitions\n(Shows movement patterns)', 
              fontsize=16, fontweight='bold', pad=20)

# Get top 15 transitions
top_transitions = sorted(transitions.items(), key=lambda x: x[1], reverse=True)[:15]

# Create bar chart
transition_labels = [f"{frm.replace('Primary Activity Zone ', 'PAZ-').replace('Secondary Hub ', 'SH-').replace('Functional Niche ', 'FN-')}\n→\n{to.replace('Primary Activity Zone ', 'PAZ-').replace('Secondary Hub ', 'SH-').replace('Functional Niche ', 'FN-')}" 
                    for (frm, to), _ in top_transitions]
transition_counts = [count for _, count in top_transitions]

# Color bars by source zone tier
bar_colors = [get_tier_color(frm) for (frm, to), _ in top_transitions]

bars = ax2.barh(range(len(transition_labels)), transition_counts, 
               color=bar_colors, alpha=0.8, edgecolor='black', linewidth=1.5)

ax2.set_yticks(range(len(transition_labels)))
ax2.set_yticklabels(transition_labels, fontsize=9)
ax2.set_xlabel('Number of Transitions', fontsize=12, fontweight='bold')
ax2.grid(axis='x', alpha=0.3)
ax2.invert_yaxis()

# Add value labels
for i, (bar, count) in enumerate(zip(bars, transition_counts)):
    ax2.text(count + 0.5, i, str(count), va='center', fontsize=9, fontweight='bold')

# Overall title and layout
fig.suptitle('Zone Transition Analysis: Movement Patterns Over Time', 
            fontsize=18, fontweight='bold', y=0.98)

plt.tight_layout()
plt.savefig('outputs/zone_transition_network.png', dpi=150, bbox_inches='tight', facecolor='white')
print("✓ Saved network graph to: outputs/zone_transition_network.png")

# Print summary statistics
print("\n" + "="*80)
print("TRANSITION ANALYSIS")
print("="*80)

print(f"\nTotal unique transitions: {len(transitions)}")
print(f"Total transition events: {sum(transitions.values())}")

# Most connected zones
in_degree = dict(G.in_degree(weight='weight'))
out_degree = dict(G.out_degree(weight='weight'))

print("\nMost Visited Zones (incoming transitions):")
for zone, degree in sorted(in_degree.items(), key=lambda x: x[1], reverse=True)[:5]:
    print(f"  {zone}: {degree} incoming transitions")

print("\nMost Active Departure Zones (outgoing transitions):")
for zone, degree in sorted(out_degree.items(), key=lambda x: x[1], reverse=True)[:5]:
    print(f"  {zone}: {degree} outgoing transitions")

print("\nTop 10 Movement Flows:")
for i, ((frm, to), count) in enumerate(top_transitions[:10], 1):
    print(f"  {i}. {frm} → {to}: {count} times")

print("="*80)

plt.show()