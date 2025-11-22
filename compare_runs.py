#!/usr/bin/env python3
"""
Compare two specific runs: run025 (centralized) vs run022 (distributed)
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import json
from pathlib import Path

central_json = Path('results/run025/centralized/experiment_details_20251117_100453.json')
dist_json = Path('results/run026/distributed/experiment_details_20251117_120203.json')

with open(central_json, 'r') as f:
    central_data = json.load(f)['results'][0]['metrics']['performance']

with open(dist_json, 'r') as f:
    dist_data = json.load(f)['results'][0]['metrics']['performance']

central_csv = Path('results/run025/centralized/experiment_summary_20251117_100453.csv')
dist_csv = Path('results/run026/distributed/experiment_summary_20251117_120203.csv')

central_df = pd.read_csv(central_csv)
dist_df = pd.read_csv(dist_csv)

central_row = central_df.iloc[0]
dist_row = dist_df.iloc[0]

if 'perf_tasks_claimed' in central_df.columns:
    central_claimed = central_row['perf_tasks_claimed']
    dist_claimed = dist_row['perf_tasks_claimed']
    central_claimed_rate = central_row['perf_claimed_completion_rate'] * 100
    dist_claimed_rate = dist_row['perf_claimed_completion_rate'] * 100
else:
    central_claimed = central_row['coord_tasks_in_registry']
    dist_claimed = dist_row['coord_tasks_in_registry']
    central_claimed_rate = (central_data['tasks_completed'] / central_claimed) * 100 if central_claimed > 0 else 0
    dist_claimed_rate = (dist_data['tasks_completed'] / dist_claimed) * 100 if dist_claimed > 0 else 0

print("="*70)
print("COMPARISON: Run025 (Centralized) vs Run026 (Distributed)")
print("="*70)
print(f"\nCentralized: {central_row['config_name']}")
print(f"Distributed: {dist_row['config_name']}")
print()

metrics = {
    'Tasks Completed': (central_data['tasks_completed'], dist_data['tasks_completed'], False, 'higher'),
    'Throughput (TPS)': (central_data['throughput_tps'], dist_data['throughput_tps'], False, 'higher'),
    'Completion Rate': (central_claimed_rate, dist_claimed_rate, True, 'higher'),
    'Agent Utilization': (central_data['agent_utilization'] * 100, dist_data['agent_utilization'] * 100, True, 'higher'),
    'Avg Latency (s)': (central_data['average_completion_time'], dist_data['average_completion_time'], False, 'lower'),
    'Distance (km)': (central_data['total_distance_traveled'] / 1000, dist_data['total_distance_traveled'] / 1000, False, 'lower'),
}

fig, axes = plt.subplots(2, 3, figsize=(15, 10))
fig.suptitle('Centralized vs Distributed (500 Agents, 600s)', fontsize=16, fontweight='normal', color='black')

labels = ['Centralized', 'Distributed']

for idx, (metric_name, (cent_val, dist_val, is_pct, better_direction)) in enumerate(metrics.items()):
    ax = axes[idx // 3, idx % 3]
    
    bar1 = ax.bar([0], [cent_val], color='0.85', width=0.6, 
                   edgecolor='black', linewidth=1.5)
    bar2 = ax.bar([1], [dist_val], color='0.4', width=0.6, 
                   edgecolor='black', linewidth=1.5)
    
    ax.set_xticks([0, 1])
    ax.set_xticklabels(labels, fontsize=11, color='black')
    ax.set_title(metric_name, fontsize=13, fontweight='normal', color='black', pad=10)
    ax.grid(axis='y', alpha=0.15, linestyle='--', color='gray')
    ax.set_facecolor('white')
    
    for spine in ax.spines.values():
        spine.set_edgecolor('black')
        spine.set_linewidth(1.2)
    
    for i, (bar_container, val) in enumerate([(bar1, cent_val), (bar2, dist_val)]):
        bar = bar_container[0]
        height = bar.get_height()
        label_text = f'{val:.2f}%' if is_pct else f'{val:.2f}'
        ax.text(bar.get_x() + bar.get_width()/2., height,
                label_text,
                ha='center', va='bottom', fontsize=10, fontweight='normal', color='black')
    
    if better_direction == 'lower':
        dist_better = dist_val < cent_val
        pct_diff = abs((dist_val - cent_val) / cent_val) * 100
    else:
        dist_better = dist_val > cent_val
        pct_diff = abs((dist_val - cent_val) / cent_val) * 100
    
    winner = 'Dist' if dist_better else 'Cent'
    arrow = '↑' if dist_better else '↓'
    color_badge = '#27AE60' if dist_better else '#E74C3C'
    
    ax.text(0.5, ax.get_ylim()[1] * 0.92, 
            f'{arrow} {winner} {pct_diff:.1f}%',
            ha='center', va='top', fontsize=9, color='white', fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.4', facecolor=color_badge, alpha=0.85, edgecolor='none'))

plt.tight_layout(rect=[0, 0, 1, 0.96])
output_path = 'results/comparison_run025_vs_run026.png'
plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
print(f"✓ Saved comparison plot: {output_path}")

print("\n" + "="*70)
print("DETAILED COMPARISON")
print("="*70)

winners = {'cent': 0, 'dist': 0}
for metric_name, (cent_val, dist_val, is_pct, better_direction) in metrics.items():
    if better_direction == 'lower':
        dist_better = dist_val < cent_val
        pct_diff = abs((dist_val - cent_val) / cent_val) * 100
    else:
        dist_better = dist_val > cent_val
        pct_diff = abs((dist_val - cent_val) / cent_val) * 100
    
    winners['dist' if dist_better else 'cent'] += 1
    arrow = '↑ DIST' if dist_better else '↓ DIST'
    print(f"{metric_name:25s}: Cent={cent_val:8.2f}  Dist={dist_val:8.2f}  {arrow} ({pct_diff:.1f}%)")

print("\n" + "="*70)
if winners['dist'] > winners['cent']:
    print(f"WINNER: Distributed (better in {winners['dist']}/{len(metrics)} key metrics)")
elif winners['cent'] > winners['dist']:
    print(f"WINNER: Centralized (better in {winners['cent']}/{len(metrics)} key metrics)")
else:
    print("RESULT: Tie")
print("="*70)
