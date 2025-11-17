#!/usr/bin/env python3
"""
Compare centralized vs distributed baseline experiments.
Generates side-by-side plots similar to dashboard reports.
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import matplotlib.pyplot as plt
import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
from config import STEP_DURATION_S
from experiments.fileHandler import ResultsManager


def find_latest_experiment(results_dir: Path) -> Optional[Path]:
    """Find the most recent experiment_details_*.json file."""
    if not results_dir.exists():
        return None
    json_files = list(results_dir.glob("experiment_details_*.json"))
    if not json_files:
        return None
    return max(json_files, key=lambda p: p.stat().st_mtime)


def load_experiment_data(json_path: Path, config_name: str = None) -> Optional[Dict]:
    """Load experiment data from JSON file. If config_name provided, find that specific config."""
    with open(json_path, 'r') as f:
        data = json.load(f)
    if not data.get('results') or len(data['results']) == 0:
        return None
    
    if config_name:
        for result in data['results']:
            if result.get('config_name') == config_name:
                return result
        return None
    return data['results'][0]


def load_all_experiments(json_path: Path) -> List[Dict]:
    """Load all experiment results from JSON file."""
    with open(json_path, 'r') as f:
        data = json.load(f)
    return data.get('results', [])


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
    """Generate side-by-side comparison plot for centralized vs one distributed config."""
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


def print_summary_stats(central_data: Dict, dist_data_list: List[Dict]):
    """Print summary statistics comparing centralized with all distributed configs."""
    if not central_data or not dist_data_list:
        print("\nERROR: Missing data for comparison")
        return
    
    central_metrics = central_data.get('metrics', {})
    central_perf = central_metrics.get('performance', {})
    central_dsm = central_metrics.get('dsm', {})
    central_ts = central_metrics.get('time_series', {})
    
    central_created = central_ts.get('tasks_created_timeline', [])[-1] if central_ts.get('tasks_created_timeline') else central_perf.get('tasks_completed', 0) + central_perf.get('tasks_failed', 0)
    
    print("\n" + "=" * 120)
    print("EXPERIMENT COMPARISON SUMMARY")
    print("=" * 120)
    
    col_width = 18
    header = f"{'Metric':<30} {'Centralized':>{col_width}}"
    for dist_data in dist_data_list:
        config_name = dist_data.get('config_name', 'Unknown')
        short_name = config_name.replace('distributed_', '')[:col_width]
        header += f" {short_name:>{col_width}}"
    print(header)
    print("-" * 120)
    
    def format_multi_row(name, central_val, dist_vals, fmt='{}', compute_diff=True):
        c_str = fmt.format(central_val)
        row = f"{name:<30} {c_str:>{col_width}}"
        for dist_val in dist_vals:
            d_str = fmt.format(dist_val)
            if compute_diff and isinstance(central_val, (int, float)) and isinstance(dist_val, (int, float)):
                if central_val != 0:
                    diff_pct = ((dist_val - central_val) / central_val) * 100
                    d_str += f" ({diff_pct:+.0f}%)"
            row += f" {d_str:>{col_width}}"
        print(row)
    
    tasks_created = [dist_data.get('metrics', {}).get('time_series', {}).get('tasks_created_timeline', [])[-1] 
                     if dist_data.get('metrics', {}).get('time_series', {}).get('tasks_created_timeline') 
                     else dist_data.get('metrics', {}).get('performance', {}).get('tasks_completed', 0) + 
                          dist_data.get('metrics', {}).get('performance', {}).get('tasks_failed', 0)
                     for dist_data in dist_data_list]
    
    tasks_completed = [dist_data.get('metrics', {}).get('performance', {}).get('tasks_completed', 0) for dist_data in dist_data_list]
    tasks_failed = [dist_data.get('metrics', {}).get('performance', {}).get('tasks_failed', 0) for dist_data in dist_data_list]
    completion_rate = [dist_data.get('metrics', {}).get('performance', {}).get('completion_rate', 0) for dist_data in dist_data_list]
    
    format_multi_row("Tasks Created", central_created, tasks_created, '{:,}')
    format_multi_row("Tasks Completed", central_perf.get('tasks_completed', 0), tasks_completed, '{:,}')
    format_multi_row("Tasks Failed", central_perf.get('tasks_failed', 0), tasks_failed, '{:,}')
    format_multi_row("Completion Rate", central_perf.get('completion_rate', 0), completion_rate, '{:.1%}', compute_diff=False)
    
    print("-" * 120)
    avg_latency = [dist_data.get('metrics', {}).get('performance', {}).get('average_completion_time', 0) for dist_data in dist_data_list]
    p50_latency = [dist_data.get('metrics', {}).get('performance', {}).get('latency_p50', 0) for dist_data in dist_data_list]
    p90_latency = [dist_data.get('metrics', {}).get('performance', {}).get('latency_p90', 0) for dist_data in dist_data_list]
    
    format_multi_row("Avg Latency (s)", central_perf.get('average_completion_time', 0), avg_latency, '{:.1f}')
    format_multi_row("P50 Latency (s)", central_perf.get('latency_p50', 0), p50_latency, '{:.1f}')
    format_multi_row("P90 Latency (s)", central_perf.get('latency_p90', 0), p90_latency, '{:.1f}')
    
    print("-" * 120)
    central_throughput = central_perf.get('throughput_tps', 0) * 60
    throughput = [dist_data.get('metrics', {}).get('performance', {}).get('throughput_tps', 0) * 60 for dist_data in dist_data_list]
    utilization = [dist_data.get('metrics', {}).get('performance', {}).get('agent_utilization', 0) for dist_data in dist_data_list]
    distance = [dist_data.get('metrics', {}).get('performance', {}).get('total_distance_traveled', 0) for dist_data in dist_data_list]
    
    format_multi_row("Throughput (tasks/min)", central_throughput, throughput, '{:.2f}')
    format_multi_row("Agent Utilization", central_perf.get('agent_utilization', 0), utilization, '{:.1%}', compute_diff=False)
    format_multi_row("Distance (cells)", central_perf.get('total_distance_traveled', 0), distance, '{:,.0f}')
    
    print("-" * 120)
    dsm_reads = [dist_data.get('metrics', {}).get('dsm', {}).get('total_reads', 0) for dist_data in dist_data_list]
    dsm_writes = [dist_data.get('metrics', {}).get('dsm', {}).get('total_writes', 0) for dist_data in dist_data_list]
    aoi_violations = [dist_data.get('metrics', {}).get('dsm', {}).get('aoi_violations', 0) for dist_data in dist_data_list]
    
    format_multi_row("DSM Reads", central_dsm.get('total_reads', 0), dsm_reads, '{:,}')
    format_multi_row("DSM Writes", central_dsm.get('total_writes', 0), dsm_writes, '{:,}')
    format_multi_row("AoI Violations", central_dsm.get('aoi_violations', 0), aoi_violations, '{:,}')
    
    print("=" * 120)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Compare centralized vs distributed baseline experiments')
    parser.add_argument('--run', '-r', type=str, help='Specific run ID to compare (e.g., run001). Default: latest')
    parser.add_argument('--output', '-o', type=str, default='results', help='Results base directory')
    parser.add_argument('--central-config', '-c', type=str, default='centralized_baseline', 
                        help='Centralized config name to use as baseline (default: centralized_baseline)')
    args = parser.parse_args()
    
    file_handler = ResultsManager(args.output)
    
    print("Searching for experiment results...")
    central_dir, dist_dir = file_handler.find_comparison_pair(args.run)
    
    if not central_dir or not dist_dir:
        print(f"Error: Could not find both centralized and distributed results for run: {args.run or 'latest'}")
        print("\nAvailable runs:")
        for run in file_handler.list_runs():
            print(f"  {run['run_id']}: {run.get('description', 'No description')} - {run.get('status', 'unknown')}")
        return
    
    print(f"Comparing: {central_dir.parent.name}")
    print(f"  Centralized: {central_dir}")
    print(f"  Distributed: {dist_dir}")
    
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
    
    central_data = load_experiment_data(central_json, args.central_config)
    dist_data_list = load_all_experiments(dist_json)
    
    if not central_data:
        print(f"ERROR: Centralized config '{args.central_config}' not found in {central_json.name}")
        sys.exit(1)
    if not dist_data_list:
        print(f"ERROR: No distributed experiments found in {dist_json.name}")
        sys.exit(1)
    
    print(f"\nFound {len(dist_data_list)} distributed config(s):")
    for dist_data in dist_data_list:
        print(f"  - {dist_data.get('config_name', 'Unknown')}")
    
    print_summary_stats(central_data, dist_data_list)
    
    run_dir = central_dir.parent
    for dist_data in dist_data_list:
        config_name = dist_data.get('config_name', 'unknown')
        short_name = config_name.replace('distributed_', '')
        output_path = run_dir / f'comparison_vs_{short_name}.png'
        generate_comparison_plot(central_data, dist_data, output_path)
    
    print(f"\nComparison complete! Generated {len(dist_data_list)} plot(s).")


if __name__ == '__main__':
    main()

