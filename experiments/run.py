#!/usr/bin/env python3
"""
Warehouse DSM Experiment Runner
Automated execution and analysis of warehouse distributed shared memory experiments.
"""

import yaml
import json
import argparse
import logging
import time
import os
import sys
import subprocess
import signal
import atexit
import multiprocessing as mp
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

sys.path.append(str(Path(__file__).parent.parent))

from config import STEP_DURATION_S

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

from world.model import WarehouseDSMModel
from world.graph import WarehouseGraph
from dsm.api import DSM
try:
    from dsm.router import DSMRouter, default_region_mapper
except Exception:
    DSMRouter = None
    default_region_mapper = None
from lf.bridge import LFTickClient, internal_tick_generator


@dataclass
class ExperimentResult:
    """Container for experiment results and metadata."""
    config_name: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    metrics: Dict[str, Any]
    logs: List[str]
    success: bool
    error_message: Optional[str] = None
    mode: Optional[str] = None


class ExperimentRunner:
    """Orchestrates warehouse DSM experiments with data collection and analysis."""
    
    def __init__(self, config_path: str, output_dir: str = "results"):
        """Initialize experiment runner with configuration."""
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        # Load experiment configurations
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.experiments = self.config['experiments']
        self.metrics_config = self.config['metrics']
        self.output_config = self.config['output']
        
        # Setup logging
        self.setup_logging()
        self.log_interval_steps = 100
        
    def setup_logging(self):
        """Configure logging for experiment tracking."""
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            handlers=[
                logging.FileHandler(self.output_dir / 'experiments.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('ExperimentRunner')
        
    def create_warehouse_graph(self, warehouse_config: Dict) -> WarehouseGraph:
        """Create warehouse graph from configuration."""
        size = warehouse_config['size']
        graph = WarehouseGraph(width=size[0], height=size[1])
        
        # Add storage regions
        for region in warehouse_config.get('storage_regions', []):
            center = region['center']
            radius = region['radius']
            for x in range(max(0, center[0] - radius), min(size[0], center[0] + radius + 1)):
                for y in range(max(0, center[1] - radius), min(size[1], center[1] + radius + 1)):
                    if (x - center[0])**2 + (y - center[1])**2 <= radius**2:
                        graph.add_storage_location((x, y))
        
        # Add sortation regions
        for region in warehouse_config.get('sortation_regions', []):
            center = region['center']
            radius = region['radius']
            for x in range(max(0, center[0] - radius), min(size[0], center[0] + radius + 1)):
                for y in range(max(0, center[1] - radius), min(size[1], center[1] + radius + 1)):
                    if (x - center[0])**2 + (y - center[1])**2 <= radius**2:
                        graph.add_sortation_location((x, y))
        
        return graph
        
    def generate_agent_positions(self, agents_config: Dict, warehouse_graph: WarehouseGraph) -> List[tuple]:
        """Generate initial agent positions."""
        if isinstance(agents_config['initial_positions'], str) and agents_config['initial_positions'] == 'random':
            # Generate random positions
            positions = []
            nodes = list(warehouse_graph.graph.nodes())
            np.random.seed(42)  # For reproducibility
            
            for _ in range(agents_config['count']):
                pos = nodes[np.random.randint(len(nodes))]
                positions.append(pos)
            return positions
        else:
            # Use provided positions
            return [tuple(pos) for pos in agents_config['initial_positions']]
    
    def run_single_experiment(self, config_name: str, config: Dict, use_lf: bool = False) -> ExperimentResult:
        """Run a single experiment configuration."""
        self.logger.info(f"Starting experiment: {config_name}")
        start_time = datetime.now()
        
        try:
            # Create warehouse graph
            warehouse_graph = self.create_warehouse_graph(config['warehouse'])
            
            # Generate agent positions
            agent_positions = self.generate_agent_positions(config['agents'], warehouse_graph)
            
            # Initialize DSM with configuration (centralized vs distributed)
            dsm_config = config['dsm']
            sim_mode = config.get('simulation', {}).get('mode', 'distributed')

            if sim_mode == 'distributed' and DSMRouter is not None and default_region_mapper is not None:
                num_shards = max(1, int(dsm_config.get('memory_owners', 1)))
                width = getattr(warehouse_graph, 'width', config['warehouse']['size'][0])

                def node_to_shard(node_id: int, w: int = width, shards: int = num_shards) -> int:
                    return default_region_mapper(node_id, w, shards)

                def dsm_factory() -> DSM:
                    return DSM(
                        memory_owners=dsm_config['memory_owners'],
                        partition_strategy=dsm_config['partition_strategy'],
                        halo_radius=dsm_config['halo_radius'],
                        aoi_threshold=dsm_config['aoi_threshold']
                    )

                dsm = DSMRouter(num_shards=num_shards, node_to_shard=node_to_shard, dsm_factory=dsm_factory)
                self.logger.info(f"Initialized DISTRIBUTED DSM with {num_shards} shards (mode=distributed)")
            else:
                dsm = DSM(
                    memory_owners=dsm_config['memory_owners'],
                    partition_strategy=dsm_config['partition_strategy'],
                    halo_radius=dsm_config['halo_radius'],
                    aoi_threshold=dsm_config['aoi_threshold']
                )
                if sim_mode == 'distributed':
                    self.logger.info("Requested distributed mode but router unavailable; falling back to CENTRALIZED DSM")
                else:
                    self.logger.info("Initialized CENTRALIZED DSM (mode=centralized)")
            
            duration = config['simulation']['duration']
            step_interval = config['simulation']['step_interval']
            step_dt = STEP_DURATION_S
            steps = int(duration * 1000 / step_interval)  # Convert to steps
            
            model = WarehouseDSMModel(
                n_agents=config['agents']['count'],
                warehouse_graph=warehouse_graph,
                agent_positions=agent_positions,
                dsm=dsm,
                task_arrival_rate=config['tasks']['arrival_rate'],
                task_types=config['tasks']['types'],
                task_priorities=config['tasks']['priorities'],
                step_duration_s=step_dt,
                logger=self.logger
            )
            
            self.logger.info(f"Running {steps} steps over {duration}s")
            
            # Collect metrics during simulation
            metrics_data = []
            
            if use_lf:
                try:
                    client = LFTickClient()
                    client.connect()
                    tick_iter = client.ticks()
                    self.logger.info("Connected to LF tick server on 127.0.0.1:9001")
                except Exception as e:
                    self.logger.warning(f"LF not available ({e}); using internal tick generator")
                    tick_iter = internal_tick_generator(step_interval)
                
                current_step = 0
                for _ in tick_iter:
                    if current_step >= steps:
                        break
                    model.step()
                    if current_step % 10 == 0:
                        step_metrics = self.collect_step_metrics(model, current_step)
                        metrics_data.append(step_metrics)
                        
                        if self.log_interval_steps > 0 and current_step % self.log_interval_steps == 0:
                            self.logger.info(f"Step {current_step}/{steps} ({100*current_step/steps:.1f}%) - "
                                             f"Tasks: {model.task_counter} created, "
                                             f"{len(model.completed_tasks)} completed, "
                                             f"{len(model.active_tasks)} active")
                        
                        if 'failure_schedule' in config['agents']:
                            self.handle_agent_failures(model, config['agents']['failure_schedule'], current_step, step_interval)
                    current_step += 1
            else:
                for step in range(steps):
                    model.step()
                    
                    if step % 10 == 0:
                        step_metrics = self.collect_step_metrics(model, step)
                        metrics_data.append(step_metrics)
                        
                        # Progress (step-based only)
                        if self.log_interval_steps > 0 and step % self.log_interval_steps == 0:
                            self.logger.info(f"Step {step}/{steps} ({100*step/steps:.1f}%) - "
                                             f"Tasks: {model.task_counter} created, "
                                             f"{len(model.completed_tasks)} completed, "
                                             f"{len(model.active_tasks)} active")
                        
                        if 'failure_schedule' in config['agents']:
                            self.handle_agent_failures(model, config['agents']['failure_schedule'], step, step_interval)
            
            # Collect final metrics
            final_metrics = self.collect_final_metrics(model, metrics_data, sim_duration_s=duration, step_interval_ms=step_interval)
            
            end_time = datetime.now()
            duration_seconds = (end_time - start_time).total_seconds()
            
            result = ExperimentResult(
                config_name=config_name,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration_seconds,
                metrics=final_metrics,
                logs=[],
                success=True,
                mode=sim_mode
            )
            
            self.logger.info(f"Completed experiment: {config_name} in {duration_seconds:.2f}s")
            return result
            
        except Exception as e:
            import traceback
            end_time = datetime.now()
            duration_seconds = (end_time - start_time).total_seconds()
            
            full_trace = traceback.format_exc()
            result = ExperimentResult(
                config_name=config_name,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration_seconds,
                metrics={},
                logs=[],
                success=False,
                error_message=str(e)
            )
            
            self.logger.error(f"Failed experiment: {config_name} - {e}\n{full_trace}")
            return result
    
    def collect_step_metrics(self, model: WarehouseDSMModel, step: int) -> Dict:
        """Collect metrics for a single simulation step."""
        return {
            'step': step,
            'timestamp': time.time(),
            'tasks_completed': len([t for t in model.completed_tasks]),
            'tasks_active': len([t for t in model.active_tasks]),
            'agent_states': [agent.state for agent in model.schedule.agents],
            'dsm_reads': model.dsm.stats.get('reads', 0),
            'dsm_writes': model.dsm.stats.get('writes', 0),
            'gossip_messages': model.dsm.stats.get('gossip_messages', 0),
            'aoi_violations': model.dsm.stats.get('aoi_violations', 0)
        }
    
    def collect_final_metrics(self, model: WarehouseDSMModel, step_data: List[Dict], sim_duration_s: float, step_interval_ms: int) -> Dict:
        """Collect and aggregate final experiment metrics."""
        # Convert step data to DataFrame for analysis
        df = pd.DataFrame(step_data)
        
        # Performance metrics
        total_tasks = len(model.completed_tasks) + len(model.failed_tasks)
        
        # Extract completion times from task records (now dicts with timestamps)
        completion_times = []
        for t in model.completed_tasks:
            if isinstance(t, dict) and 'completion_time' in t and 'start_time' in t:
                if t['completion_time'] and t['start_time']:
                    completion_times.append(t['completion_time'] - t['start_time'])
        latencies_s = completion_times
        p50 = float(np.percentile(latencies_s, 50)) if latencies_s else 0.0
        p90 = float(np.percentile(latencies_s, 90)) if latencies_s else 0.0
        p99 = float(np.percentile(latencies_s, 99)) if latencies_s else 0.0
        throughput_tps = (len(model.completed_tasks) / sim_duration_s) if sim_duration_s > 0 else 0.0
        
        performance_metrics = {
            'tasks_completed': len(model.completed_tasks),
            'tasks_failed': len(model.failed_tasks),
            'completion_rate': len(model.completed_tasks) / total_tasks if total_tasks > 0 else 0,
            'average_completion_time': np.mean(latencies_s) if latencies_s else 0,
            'latency_p50': p50,
            'latency_p90': p90,
            'latency_p99': p99,
            'throughput_tps': throughput_tps,
            'sim_duration_s': float(sim_duration_s),
            'step_interval_ms': int(step_interval_ms),
            'total_distance_traveled': sum(agent.total_distance for agent in model.schedule.agents),
            'agent_utilization': np.mean([agent.utilization for agent in model.schedule.agents])
        }
        
        # DSM metrics
        dsm_metrics = {
            'total_reads': model.dsm.stats.get('reads', 0),
            'total_writes': model.dsm.stats.get('writes', 0),
            'total_gossip_messages': model.dsm.stats.get('gossip_messages', 0),
            'aoi_violations': model.dsm.stats.get('aoi_violations', 0),
            'partition_quality': model.dsm.get_partition_quality(),
            'coordination_overhead': model.dsm.stats.get('coordination_time', 0)
        }
        
        # Time series data
        time_series = {
            'task_completion_timeline': df['tasks_completed'].tolist(),
            'dsm_reads_timeline': df['dsm_reads'].tolist(),
            'dsm_writes_timeline': df['dsm_writes'].tolist(),
            'steps': df['step'].tolist(),
            'latency_samples_s': latencies_s
        }
        
        return {
            'performance': performance_metrics,
            'dsm': dsm_metrics,
            'time_series': time_series
        }
    
    def handle_agent_failures(self, model: WarehouseDSMModel, failure_schedule: List[Dict], 
                            current_step: int, step_interval: int):
        """Handle scheduled agent failures during simulation."""
        current_time = current_step * step_interval / 1000  # Convert to seconds
        
        for failure in failure_schedule:
            agent_id = failure['agent_id']
            fail_time = failure['time']
            duration = failure['duration']
            
            # Check if agent should fail now
            if fail_time <= current_time < fail_time + duration:
                if agent_id < len(model.schedule.agents):
                    agent = model.schedule.agents[agent_id]
                    agent.failed = True
                    self.logger.info(f"Agent {agent_id} failed at time {current_time}s")
            
            # Check if agent should recover
            elif current_time >= fail_time + duration:
                if agent_id < len(model.schedule.agents):
                    agent = model.schedule.agents[agent_id]
                    if hasattr(agent, 'failed') and agent.failed:
                        agent.failed = False
                        self.logger.info(f"Agent {agent_id} recovered at time {current_time}s")
    
    def save_results(self, results: List[ExperimentResult]):
        """Save experiment results to various formats, grouped by mode into subfolders."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Group results by mode
        by_mode: Dict[str, List[ExperimentResult]] = {'distributed': [], 'centralized': [], 'unknown': []}
        for r in results:
            mode = (r.mode or 'unknown').lower()
            if mode not in by_mode:
                by_mode[mode] = []
            by_mode[mode].append(r)

        for mode, group in by_mode.items():
            if not group:
                continue
            out_dir = self.output_dir / ( 'distributed' if mode == 'distributed' else 'centralized' if mode == 'centralized' else 'misc')
            out_dir.mkdir(parents=True, exist_ok=True)

            # Save summary CSV
            summary_data = []
            for result in group:
                summary_row = {
                    'config_name': result.config_name,
                    'mode': result.mode,
                    'success': result.success,
                    'duration_seconds': result.duration_seconds,
                    'error_message': result.error_message or ''
                }
                if result.success:
                    summary_row.update({f"perf_{k}": v for k, v in result.metrics.get('performance', {}).items()})
                    summary_row.update({f"dsm_{k}": v for k, v in result.metrics.get('dsm', {}).items()})
                summary_data.append(summary_row)
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_csv(out_dir / f'experiment_summary_{timestamp}.csv', index=False)

            # Save detailed JSON
            detailed_results = {
                'timestamp': timestamp,
                'config_path': str(self.config_path),
                'mode': mode,
                'results': [asdict(result) for result in group]
            }
            with open(out_dir / f'experiment_details_{timestamp}.json', 'w') as f:
                json.dump(detailed_results, f, indent=2, default=str)

            # Plots
            if 'plots' in self.output_config:
                # Temporarily swap output_dir for plotting into subfolder
                prev = self.output_dir
                self.output_dir = out_dir
                try:
                    self.generate_plots(group, timestamp)
                    plots = self.output_config.get('plots')
                    if isinstance(plots, list) and 'dashboard_style' in plots:
                        self.generate_dashboard_reports(group, timestamp)
                finally:
                    self.output_dir = prev
    
    def generate_plots(self, results: List[ExperimentResult], timestamp: str):
        """Generate visualization plots for experiment results."""
        successful_results = [r for r in results if r.success]
        
        if not successful_results:
            self.logger.warning("No successful experiments to plot")
            return
        
        # Task completion timeline
        plt.figure(figsize=(12, 8))
        for result in successful_results:
            if 'time_series' in result.metrics:
                steps = result.metrics['time_series']['steps']
                completed = result.metrics['time_series']['task_completion_timeline']
                plt.plot(steps, completed, label=result.config_name, marker='o', markersize=2)
        
        plt.xlabel('Simulation Step')
        plt.ylabel('Cumulative Tasks Completed')
        plt.title('Task Completion Timeline Across Experiments')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(self.output_dir / f'task_completion_timeline_{timestamp}.png', dpi=300)
        plt.close()
        
        # Performance comparison
        config_names = [r.config_name for r in successful_results]
        completion_rates = [r.metrics['performance']['completion_rate'] for r in successful_results]
        avg_times = [r.metrics['performance']['average_completion_time'] for r in successful_results]
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # Completion rates
        ax1.bar(config_names, completion_rates)
        ax1.set_title('Task Completion Rate by Experiment')
        ax1.set_ylabel('Completion Rate')
        ax1.tick_params(axis='x', rotation=45)
        
        # Average completion times
        ax2.bar(config_names, avg_times)
        ax2.set_title('Average Task Completion Time')
        ax2.set_ylabel('Time (seconds)')
        ax2.tick_params(axis='x', rotation=45)
        
        plt.tight_layout()
        plt.savefig(self.output_dir / f'performance_comparison_{timestamp}.png', dpi=300)
        plt.close()
        
        self.logger.info(f"Generated plots saved to {self.output_dir}")

    def _calculate_throughput_series(self, completed_timeline: List[int], time_points: List[float], window_s: float = 30.0) -> List[float]:
        """Calculate moving average throughput over time (tasks per second)."""
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
                    throughput.append(tps)
                else:
                    throughput.append(0.0)
            else:
                if current_time > 0:
                    throughput.append(completed_timeline[i] / current_time)
                else:
                    throughput.append(0.0)
        
        return throughput
    
    def _calculate_latency_series(self, latency_samples: List[float], completed_timeline: List[int], window_size: int = 50) -> List[float]:
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

    def generate_dashboard_reports(self, results: List[ExperimentResult], timestamp: str):
        """Generate per-experiment dashboard-style plots and CSV with key metrics."""
        successful = [r for r in results if r.success]
        if not successful:
            return
        rows = []
        for r in successful:
            perf = r.metrics.get('performance', {})
            ts = r.metrics.get('time_series', {})
            steps = ts.get('steps', [])
            completed = ts.get('task_completion_timeline', [])
            lat_samples = ts.get('latency_samples_s', [])
            step_interval_ms = perf.get('step_interval_ms', 100)
            step_dt = step_interval_ms / 1000.0
            
            time_points = [s * step_dt for s in steps]
            throughput_series = self._calculate_throughput_series(completed, time_points, window_s=30.0)
            latency_series = self._calculate_latency_series(lat_samples, completed, window_size=50)
            
            fig, axes = plt.subplots(1, 3, figsize=(18, 5))
            
            # Timeline
            if time_points and completed:
                axes[0].plot(time_points, completed, color='tab:blue', linewidth=2)
            axes[0].set_title(f"Timeline: {r.config_name}")
            axes[0].set_xlabel('Time (s)')
            axes[0].set_ylabel('Cumulative completions')
            axes[0].grid(True, alpha=0.3)
            
            # Latency - continuous plot with rolling average
            if latency_series:
                lat_time_points = time_points[:len(latency_series)]
                axes[1].plot(lat_time_points, latency_series, color='tab:green', linewidth=2, label='Rolling avg (50 tasks)')
                axes[1].set_title('Latency over time')
                axes[1].set_xlabel('Time (s)')
                axes[1].set_ylabel('Latency (s)')
                axes[1].legend()
                axes[1].grid(True, alpha=0.3)
            else:
                axes[1].text(0.5, 0.5, 'No latency data', ha='center', va='center', transform=axes[1].transAxes)
                axes[1].set_title('Latency over time')
            
            # Throughput - continuous plot with 30s moving average
            if throughput_series:
                thr_time_points = time_points[:len(throughput_series)]
                axes[2].plot(thr_time_points, throughput_series, color='tab:orange', linewidth=2, label='30s moving avg')
                axes[2].set_title('Throughput over time')
                axes[2].set_xlabel('Time (s)')
                axes[2].set_ylabel('Tasks/sec')
                axes[2].legend()
                axes[2].grid(True, alpha=0.3)
            else:
                axes[2].text(0.5, 0.5, 'No throughput data', ha='center', va='center', transform=axes[2].transAxes)
                axes[2].set_title('Throughput over time')
            
            plt.tight_layout()
            out_png = self.output_dir / f"dashboard_report_{r.config_name}_{timestamp}.png"
            plt.savefig(out_png, dpi=200)
            plt.close(fig)
            
            rows.append({
                'config_name': r.config_name,
                'tasks_completed': perf.get('tasks_completed', 0),
                'tasks_failed': perf.get('tasks_failed', 0),
                'completion_rate': perf.get('completion_rate', 0.0),
                'avg_latency_s': perf.get('average_completion_time', 0.0),
                'p50_latency_s': perf.get('latency_p50', 0.0),
                'p90_latency_s': perf.get('latency_p90', 0.0),
                'p99_latency_s': perf.get('latency_p99', 0.0),
                'throughput_tps': perf.get('throughput_tps', 0.0),
            })
        df = pd.DataFrame(rows)
        df.to_csv(self.output_dir / f"dashboard_metrics_{timestamp}.csv", index=False)
    
    def run_experiments(self, experiment_names: Optional[List[str]] = None, use_lf: bool = False) -> List[ExperimentResult]:
        """Run specified experiments or all if none specified."""
        if experiment_names is None:
            experiment_names = list(self.experiments.keys())
        
        results = []
        
        for exp_name in experiment_names:
            if exp_name not in self.experiments:
                self.logger.error(f"Unknown experiment: {exp_name}")
                continue
                
            config = self.experiments[exp_name]
            result = self.run_single_experiment(exp_name, config, use_lf=use_lf)
            results.append(result)
        
        # Save all results
        self.save_results(results)
        
        return results


def start_lf_coordinator():
    """Start LF coordinator as subprocess."""
    lf_dir = Path(__file__).parent.parent / 'lf'
    coordinator_script = lf_dir / 'src-gen' / 'coordinator' / 'coordinator.py'
    
    if not coordinator_script.exists():
        print(f"Error: LF coordinator not compiled. Please run:")
        print(f"  cd {lf_dir}")
        print(f"  lfc coordinator.lf")
        return None
    
    print("Starting LF coordinator...")
    proc = subprocess.Popen(
        [sys.executable, str(coordinator_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(lf_dir)
    )
    
    # Wait a bit for server to start
    time.sleep(2)
    
    if proc.poll() is not None:
        print("Error: LF coordinator failed to start")
        return None
    
    print(f"LF coordinator started (PID: {proc.pid})")
    
    # Register cleanup on exit
    def cleanup():
        print("Stopping LF coordinator...")
        proc.terminate()
        proc.wait(timeout=5)
    
    atexit.register(cleanup)
    
    return proc


def main():
    """Main entry point for experiment runner."""
    parser = argparse.ArgumentParser(description='Run warehouse DSM experiments')
    parser.add_argument('--config', '-c', default='./experiments/configs.yaml',
                       help='Path to experiment configuration file')
    parser.add_argument('--output', '-o', default='results',
                       help='Output directory for results')
    parser.add_argument('--experiments', '-e', nargs='+',
                       help='Specific experiments to run (default: all)')
    parser.add_argument('--parallel', '-p', action='store_true',
                       help='Run experiments in parallel')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    lf_group = parser.add_mutually_exclusive_group()
    lf_group.add_argument('--lf', dest='lf', action='store_true', default=True,
                        help='Drive simulation steps from Lingua Franca tick events (default)')
    lf_group.add_argument('--no-lf', dest='lf', action='store_false',
                        help='Use internal clock (no Lingua Franca)')
    
    parser.add_argument('--log-interval', type=int, default=None,
                       help='Progress log interval in steps (default: 100). Use 0 to disable.')
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Start LF coordinator if requested
    lf_proc = None
    if args.lf:
        lf_proc = start_lf_coordinator()
        if lf_proc is None:
            print("LF coordinator unavailable; falling back to internal clock.")
            args.lf = False
    
    # Initialize runner
    runner = ExperimentRunner(args.config, args.output)
    if args.log_interval is not None:
        runner.log_interval_steps = max(0, int(args.log_interval))
    
    # Run experiments
    start_time = time.time()
    results = runner.run_experiments(args.experiments, use_lf=args.lf)
    total_time = time.time() - start_time
    
    # Print summary
    successful = len([r for r in results if r.success])
    failed = len([r for r in results if not r.success])
    
    print(f"\nExperiment Summary:")
    print(f"Total experiments: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Total time: {total_time:.2f}s")
    
    if failed > 0:
        print(f"\nFailed experiments:")
        for result in results:
            if not result.success:
                print(f"  {result.config_name}: {result.error_message}")


if __name__ == '__main__':
    main()