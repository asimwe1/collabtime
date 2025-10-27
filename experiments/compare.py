#!/usr/bin/env python3
"""
Compare centralized vs distributed baseline experiments.
Generates side-by-side plots similar to dashboard reports.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import matplotlib.pyplot as plt
import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
from config import STEP_DURATION_S


def find_latest_experiment(results_dir: Path) -> Optional[Path]:
    """Find the most recent experiment_details_*.json file."""
    if not results_dir.exists():
        return None
    json_files = list(results_dir.glob("experiment_details_*.json"))
    if not json_files:
        return None
    return max(json_files, key=lambda p: p.stat().st_mtime)


def load_experiment_data(json_path: Path) -> Optional[Dict]:
    """Load experiment data from JSON file."""
    with open(json_path, 'r') as f:
        data = json.load(f)
    if not data.get('results') or len(data['results']) == 0:
        return None
    return data['results'][0]


def calculate_throughput_series(completed_timeline: List[int], time_points: List[float], window_s: float = 30.0) -> List[float]:
    """Calculate moving average throughput over time (tasks per minute)."""
    if not completed_timeline or not time_points:
        return []
    
    throughput = []
    for i, current_time in enumerate(time_points):
        cutoff_time = current_time - window_s
        start_idx = 0
        for j in range(i - 1, -1, -1):
            if time_points[j] <= cutoff_time:
                start_idx = j
                break
        
        if start_idx < i:
            completed_at_start = completed_timeline[start_idx]
            completed_now = completed_timeline[i]
            actual_window = current_time - time_points[start_idx]
            
            if actual_window > 0:
                tps = (completed_now - completed_at_start) / actual_window
                throughput.append(tps * 60.0)
            else:
                throughput.append(0.0)
        else:
            if current_time > 0:
                throughput.append(completed_timeline[i] / current_time * 60.0)
            else:
                throughput.append(0.0)
    
    return throughput


def calculate_latency_series(latency_samples: List[float], completed_timeline: List[int], window_size: int = 50) -> List[float]:
    """Calculate rolling average latency over completed tasks."""
    if not latency_samples or not completed_timeline:
        return []
    
    latency_series = []
    current_task_idx = 0
    
    for num_completed in completed_timeline:
        if num_completed > 0 and current_task_idx < len(latency_samples):
            start_idx = max(0, current_task_idx - window_size + 1)
            end_idx = min(current_task_idx + 1, len(latency_samples))
            
            if start_idx < end_idx:
                window_latencies = latency_samples[start_idx:end_idx]
                avg_lat = sum(window_latencies) / len(window_latencies)
                latency_series.append(avg_lat)
            else:
                latency_series.append(0.0)
            
            if current_task_idx < num_completed:
                current_task_idx = min(num_completed, len(latency_samples))
        else:
            latency_series.append(latency_series[-1] if latency_series else 0.0)
    
    return latency_series


def generate_comparison_plot(central_data: Dict, dist_data: Dict, output_path: Path):
    """Generate side-by-side comparison plots."""
    fig, axes = plt.subplots(2, 2, figsize=(20, 12))
    axes = axes.flatten()
    
    for idx, (data, label, colors) in enumerate([
        (central_data, 'Centralized', {'created': 'tab:blue', 'completed': 'tab:green', 'active': 'tab:orange', 'main': 'tab:blue'}), 
        (dist_data, 'Distributed', {'created': 'tab:purple', 'completed': 'tab:red', 'active': 'tab:cyan', 'main': 'tab:purple'})
    ]):
        if not data:
            continue
            
        metrics = data.get('metrics', {})
        perf = metrics.get('performance', {})
        ts = metrics.get('time_series', {})
        
        steps = ts.get('steps', [])
        created = ts.get('tasks_created_timeline', [])
        completed = ts.get('task_completion_timeline', [])
        active = ts.get('tasks_active_timeline', [])
        lat_samples = ts.get('latency_samples_s', [])
        
        step_interval_ms = perf.get('step_interval_ms', 50)
        step_dt = step_interval_ms / 1000.0
        time_points = [s * step_dt for s in steps]
        
        throughput_series = calculate_throughput_series(completed, time_points, window_s=30.0)
        latency_series = calculate_latency_series(lat_samples, completed, window_size=50)
        
        alpha = 0.8
        linestyle = '-' if idx == 0 else '--'
        linewidth = 2.5 if idx == 0 else 2.0
        
        if time_points and created and completed and active:
            axes[0].plot(time_points, created, linestyle=linestyle, linewidth=linewidth, 
                        label=f'{label} Created', alpha=alpha, color=colors['created'])
            axes[0].plot(time_points, completed, linestyle=linestyle, linewidth=linewidth, 
                        label=f'{label} Completed', alpha=alpha, color=colors['completed'])
            axes[0].plot(time_points, active, linestyle=linestyle, linewidth=linewidth, 
                        label=f'{label} Active', alpha=alpha, color=colors['active'])
        
        if throughput_series:
            thr_time_points = time_points[:len(throughput_series)]
            axes[1].plot(thr_time_points, throughput_series, linestyle=linestyle, 
                        linewidth=linewidth, label=f'{label}', alpha=alpha, color=colors['active'])
        
        if latency_series:
            lat_time_points = time_points[:len(latency_series)]
            axes[2].plot(lat_time_points, latency_series, linestyle=linestyle, 
                        linewidth=linewidth, label=f'{label}', alpha=alpha, color=colors['completed'])
        
        if time_points and completed:
            axes[3].plot(time_points, completed, linestyle=linestyle, linewidth=linewidth, 
                        label=f'{label}', alpha=alpha, color=colors['main'])
    
    axes[0].set_title('Task Timeline Comparison', fontsize=14, fontweight='bold')
    axes[0].set_xlabel('Time (s)')
    axes[0].set_ylabel('Task Count')
    axes[0].legend(loc='best')
    axes[0].grid(True, alpha=0.3)
    
    axes[1].set_title('Throughput Comparison (30s moving avg)', fontsize=14, fontweight='bold')
    axes[1].set_xlabel('Time (s)')
    axes[1].set_ylabel('Tasks/min')
    axes[1].legend(loc='best')
    axes[1].grid(True, alpha=0.3)
    
    axes[2].set_title('Latency Comparison (rolling avg)', fontsize=14, fontweight='bold')
    axes[2].set_xlabel('Time (s)')
    axes[2].set_ylabel('Latency (s)')
    axes[2].legend(loc='best')
    axes[2].grid(True, alpha=0.3)
    
    axes[3].set_title('Cumulative Completions Comparison', fontsize=14, fontweight='bold')
    axes[3].set_xlabel('Time (s)')
    axes[3].set_ylabel('Tasks Completed')
    axes[3].legend(loc='best')
    axes[3].grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close(fig)
    print(f"Comparison plot saved to: {output_path}")


def print_summary_stats(central_data: Dict, dist_data: Dict):
    """Print summary statistics for both experiments."""
    print("\n" + "=" * 70)
    print("EXPERIMENT COMPARISON SUMMARY")
    print("=" * 70)
    
    for label, data in [("CENTRALIZED", central_data), ("DISTRIBUTED", dist_data)]:
        if not data:
            print(f"\n{label}: No data available")
            continue
            
        metrics = data.get('metrics', {})
        perf = metrics.get('performance', {})
        dsm = metrics.get('dsm', {})
        ts = metrics.get('time_series', {})
        
        tasks_created = ts.get('tasks_created_timeline', [])[-1] if ts.get('tasks_created_timeline') else perf.get('tasks_completed', 0) + perf.get('tasks_failed', 0)
        
        print(f"\n{label} BASELINE:")
        print(f"  Tasks Created:       {tasks_created}")
        print(f"  Tasks Completed:     {perf.get('tasks_completed', 0)}")
        print(f"  Tasks Failed:        {perf.get('tasks_failed', 0)}")
        print(f"  Completion Rate:     {perf.get('completion_rate', 0):.2%}")
        print(f"  Avg Latency:         {perf.get('average_completion_time', 0):.1f}s")
        print(f"  P50 Latency:         {perf.get('latency_p50', 0):.1f}s")
        print(f"  P90 Latency:         {perf.get('latency_p90', 0):.1f}s")
        print(f"  P99 Latency:         {perf.get('latency_p99', 0):.1f}s")
        print(f"  Throughput:          {perf.get('throughput_tps', 0) * 60:.2f} tasks/min")
        print(f"  Agent Utilization:   {perf.get('agent_utilization', 0):.2%}")
        print(f"  Total Distance:      {perf.get('total_distance_traveled', 0):.0f} cells")
        print(f"  DSM Reads:           {dsm.get('total_reads', 0)}")
        print(f"  DSM Writes:          {dsm.get('total_writes', 0)}")
        print(f"  AoI Violations:      {dsm.get('aoi_violations', 0)}")
    
    print("\n" + "=" * 70)


def main():
    """Main entry point."""
    results_dir = Path(__file__).parent.parent / 'results'
    central_dir = results_dir / 'centralized'
    dist_dir = results_dir / 'distributed'
    
    print("Searching for latest experiment results...")
    central_json = find_latest_experiment(central_dir)
    dist_json = find_latest_experiment(dist_dir)
    
    if not central_json:
        print(f"ERROR: No centralized experiment found in {central_dir}")
        sys.exit(1)
    if not dist_json:
        print(f"ERROR: No distributed experiment found in {dist_dir}")
        sys.exit(1)
    
    print(f"Loading centralized: {central_json.name}")
    print(f"Loading distributed: {dist_json.name}")
    
    central_data = load_experiment_data(central_json)
    dist_data = load_experiment_data(dist_json)
    
    if not central_data or not dist_data:
        print("ERROR: Failed to load experiment data")
        sys.exit(1)
    
    print_summary_stats(central_data, dist_data)
    
    output_path = results_dir / 'baseline_comparison.png'
    generate_comparison_plot(central_data, dist_data, output_path)
    
    print(f"\nComparison complete!")


if __name__ == '__main__':
    main()

