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

from config import (
    STEP_DURATION_S,
    calculate_task_latency,
    calculate_agent_capacity,
    TASK_WORK_DURATION_S,
    GOSSIP_PERIOD_MS
)
from experiments.fileHandler import ResultsManager

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

from world.model import WarehouseDSMModel
from world.graph import WarehouseGraph
from lf.bridge import LFTickClient


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
    
    def __init__(self, config_path: str, output_dir: str = "results", duration_override: Optional[int] = None):
        """Initialize experiment runner with configuration."""
        self.config_path = Path(config_path)
        self.file_handler = ResultsManager(output_dir)
        self.duration_override = duration_override
        
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
            valid_nodes = [n for n in warehouse_graph.graph.nodes()
                          if warehouse_graph.node_types.get(n) in ('staging', 'aisle', 'buffer')
                          and warehouse_graph.capacities.get(n, 0) > 0
                          and len(list(warehouse_graph.graph.neighbors(n))) > 0]
            
            if not valid_nodes:
                raise ValueError("No valid starting positions found in warehouse graph!")
            
            positions = []
            np.random.seed(42)  # For reproducibility
            
            for _ in range(agents_config['count']):
                pos = valid_nodes[np.random.randint(len(valid_nodes))]
                positions.append(pos)
            return positions
        else:
            # Use provided positions
            return [tuple(pos) for pos in agents_config['initial_positions']]
    
    def run_single_experiment(self, config_name: str, config: Dict, use_lf: bool = False) -> ExperimentResult:
        """Run a single experiment configuration."""
        sim_mode = config.get('simulation', {}).get('mode', 'p2p')
        
        mode_dir = self.file_handler.get_mode_directory(
            'distributed' if sim_mode == 'p2p' else 'centralized'
        )
        
        mode_log_path = mode_dir / 'experiments.log'
        mode_file_handler = logging.FileHandler(mode_log_path)
        mode_file_handler.setLevel(logging.INFO)
        mode_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        
        self.logger.addHandler(mode_file_handler)
        
        self.logger.info("=" * 70)
        self.logger.info(f"EXPERIMENT: {config_name}")
        self.logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info("=" * 70)
        
        warehouse_size = config['warehouse']['size']
        num_agents = config['agents']['count']
        task_rate = config['tasks']['arrival_rate']
        duration = config['simulation']['duration']
        step_interval = config['simulation']['step_interval']
        seed = config.get('simulation', {}).get('seed', None)
        
        if self.duration_override is not None:
            duration = self.duration_override
            config['simulation']['duration'] = duration
            self.logger.info(f"Duration overridden to {duration}s via command-line")
        
        task_latency = calculate_task_latency(warehouse_size[0], warehouse_size[1])
        agent_capacity = calculate_agent_capacity(warehouse_size[0], warehouse_size[1])
        system_capacity = num_agents * agent_capacity
        utilization = task_rate / system_capacity if system_capacity > 0 else 0
        
        gossip_interval = config.get('coordination', {}).get('gossip_interval', 3)
        aoi_threshold = config.get('coordination', {}).get('aoi_threshold', 1000)
        
        self.logger.info(f"Coordination Architecture: P2P (Peer-to-Peer)")
        self.logger.info(f"  Control Plane: Coordinator (strong consistency)")
        self.logger.info(f"  Data Plane: Local caches + epidemic gossip")
        self.logger.info(f"  Gossip Interval: {gossip_interval} steps")
        self.logger.info(f"  AoI Threshold: {aoi_threshold}ms")
        self.logger.info(f"Warehouse Dimensions: {warehouse_size[0]} × {warehouse_size[1]} = {warehouse_size[0] * warehouse_size[1]} cells")
        self.logger.info(f"Agents: {num_agents}")
        self.logger.info(f"Simulation Duration: {duration}s ({int(duration * 1000 / step_interval)} steps)")
        self.logger.info(f"Step Duration: {STEP_DURATION_S:.3f}s ({int(STEP_DURATION_S * 1000)}ms)")
        self.logger.info(f"Timing Mode: {'LF-coordinated' if use_lf else 'Fast-as-possible'}")
        self.logger.info(f"Random Seed: {seed if seed is not None else 'None (unseeded)'}")
        self.logger.info("")
        self.logger.info("Capacity Analysis (for this warehouse size):")
        self.logger.info(f"  Avg Task Distance: {(warehouse_size[0] + warehouse_size[1]) / 3:.1f} cells")
        self.logger.info(f"  Typical Task Latency: {task_latency:.1f}s")
        self.logger.info(f"  Agent Capacity: {agent_capacity:.4f} tasks/sec")
        self.logger.info(f"  System Capacity ({num_agents} agents): {system_capacity:.4f} tasks/sec")
        self.logger.info(f"  Task Arrival Rate: {task_rate:.4f} tasks/sec")
        self.logger.info(f"  Expected Utilization: {utilization:.1%}")
        self.logger.info("=" * 70)
        self.logger.info("")
        
        start_time = datetime.now()
        
        try:
            warehouse_graph = self.create_warehouse_graph(config['warehouse'])
            agent_positions = self.generate_agent_positions(config['agents'], warehouse_graph)
            
            duration = config['simulation']['duration']
            step_interval = config['simulation']['step_interval']
            step_dt = STEP_DURATION_S
            steps = int(duration * 1000 / step_interval)
            seed = config.get('simulation', {}).get('seed', None)
            
            model = WarehouseDSMModel(
                n_agents=config['agents']['count'],
                warehouse_graph=warehouse_graph,
                agent_positions=agent_positions,
                task_arrival_rate=config['tasks']['arrival_rate'],
                task_types=config['tasks']['types'],
                task_priorities=config['tasks']['priorities'],
                step_duration_s=step_dt,
                aoi_threshold_ms=aoi_threshold,
                mode=sim_mode,
                seed=seed,
                logger=self.logger
            )
            
            mode_label = "CENTRALIZED (bottleneck)" if sim_mode == 'centralized' else "P2P DISTRIBUTED"
            self.logger.info(f"Initialized {mode_label} model with {config['agents']['count']} agents")
            
            self.logger.info(f"Running {steps} steps over {duration}s")
            
            metrics_data = []
            
            if use_lf:
                client = LFTickClient()
                client.connect()
                tick_iter = client.ticks()
                self.logger.info("Connected to LF tick server on 127.0.0.1:9001")
                
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
                error_message=str(e),
                mode=sim_mode
            )
            
            self.logger.error(f"Failed experiment: {config_name} - {e}\n{full_trace}")
            return result
        finally:
            self.logger.removeHandler(mode_file_handler)
            mode_file_handler.close()
    
    def collect_step_metrics(self, model: WarehouseDSMModel, step: int) -> Dict:
        """Collect metrics for a single simulation step."""
        cache_stats_per_agent = [
            agent.local_cache.get_stats() if agent.local_cache else {}
            for agent in model.schedule.agents
        ]
        
        total_cache_entries = sum(
            stats.get('flow_entries', 0) + stats.get('jam_entries', 0) + 
            stats.get('agent_locations', 0) + stats.get('path_intents', 0) + 
            stats.get('resource_states', 0)
            for stats in cache_stats_per_agent
        )
        
        total_jam_entries = sum(stats.get('jam_entries', 0) for stats in cache_stats_per_agent)
        
        # Compute unique jam metrics (avoid double-counting replicated jams via gossip)
        # In centralized mode, use central scheduler data
        unique_jams = {}
        if model.mode == 'centralized' and model.central_scheduler:
            # Centralized: use central jam data
            for node_id, jam_value in model.central_scheduler.jam_data.items():
                unique_jams[node_id] = {'value': jam_value, 'timestamp': step}
        else:
            # Distributed: aggregate from agent caches
            for agent in model.schedule.agents:
                if agent.local_cache:
                    for node_id, jam_entry in agent.local_cache.jam_signal.items():
                        if node_id not in unique_jams or jam_entry['timestamp'] > unique_jams[node_id]['timestamp']:
                            unique_jams[node_id] = jam_entry
        
        num_jammed_nodes = len(unique_jams)
        avg_jam_intensity = sum(entry['value'] for entry in unique_jams.values()) / num_jammed_nodes if num_jammed_nodes > 0 else 0.0
        
        agent_states = [agent.state for agent in model.schedule.agents]
        working_count = sum(1 for state in agent_states if state.value == 'working')
        navigating_count = sum(1 for state in agent_states if state.value == 'navigating')
        utilization_working = working_count / len(agent_states) if agent_states else 0.0
        utilization_active = (working_count + navigating_count) / len(agent_states) if agent_states else 0.0
        
        stuck_counters = [agent.stuck_counter for agent in model.schedule.agents]
        num_stuck = sum(1 for sc in stuck_counters if sc > 0)
        avg_stuck = sum(stuck_counters) / len(stuck_counters) if stuck_counters else 0.0
        max_stuck = max(stuck_counters) if stuck_counters else 0
        
        stalls_in_transit = 0
        stalls_at_resource = 0
        stalls_idle = 0
        
        for agent in model.schedule.agents:
            if agent.stuck_counter > 0:
                if agent.state.value == 'navigating':
                    if agent.task_location is not None and agent.node == agent.task_location:
                        stalls_at_resource += 1
                    else:
                        stalls_in_transit += 1
                elif agent.state.value == 'idle':
                    stalls_idle += 1
        
        return {
            'step': step,
            'timestamp': time.time(),
            'tasks_created': model.task_counter,
            'tasks_completed': len([t for t in model.completed_tasks]),
            'tasks_active': len([t for t in model.active_tasks]),
            'agent_states': agent_states,
            'cache_entries': total_cache_entries,
            'jam_entries': total_jam_entries,
            'num_jammed_nodes': num_jammed_nodes,
            'jam_intensity_avg': avg_jam_intensity,
            'gossip_rounds': step // 3,
            'utilization_working': utilization_working,
            'utilization_active': utilization_active,
            'num_stuck_agents': num_stuck,
            'avg_stuck_counter': avg_stuck,
            'max_stuck_counter': max_stuck,
            'stalls_in_transit': stalls_in_transit,
            'stalls_at_resource': stalls_at_resource,
            'stalls_idle': stalls_idle
        }
    
    def collect_final_metrics(self, model: WarehouseDSMModel, step_data: List[Dict], sim_duration_s: float, step_interval_ms: int) -> Dict:
        """Collect and aggregate final experiment metrics."""
        # Convert step data to DataFrame for analysis
        df = pd.DataFrame(step_data)
        
        # Performance metrics
        total_tasks = model.task_counter
        
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
        
        tasks_claimed = sum(agent.metrics['tasks_claimed'] for agent in model.schedule.agents)
        
        performance_metrics = {
            'tasks_completed': len(model.completed_tasks),
            'tasks_failed': len(model.failed_tasks),
            'tasks_claimed': tasks_claimed,
            'completion_rate': len(model.completed_tasks) / total_tasks if total_tasks > 0 else 0,
            'claimed_completion_rate': len(model.completed_tasks) / tasks_claimed if tasks_claimed > 0 else 0,
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
        
        # Data plane metrics depend on mode
        if model.mode == 'centralized':
            # Centralized: report central scheduler data store size
            avg_cache_size = model.central_scheduler.metrics['congestion_data_size'] if model.central_scheduler else 0
            total_gossip_rounds = 0
        else:
            # Distributed: report per-agent local cache and gossip
            avg_cache_size = np.mean([
                sum(agent.local_cache.get_stats().values()) if agent.local_cache else 0
                for agent in model.schedule.agents
            ])
            total_gossip_rounds = df['gossip_rounds'].max() if 'gossip_rounds' in df else 0
        
        coordination_metrics = {
            'avg_cache_size': float(avg_cache_size),
            'total_gossip_rounds': int(total_gossip_rounds),
            'tasks_in_registry': len(model.coordinator.task_registry.tasks),
            'active_leases': len([l for l in model.coordinator.lease_manager.leases.values() if l]),
            'coordination_mode': model.mode
        }
        
        time_series = {
            'tasks_created_timeline': df['tasks_created'].tolist(),
            'task_completion_timeline': df['tasks_completed'].tolist(),
            'tasks_active_timeline': df['tasks_active'].tolist(),
            'cache_entries_timeline': df.get('cache_entries', []).tolist() if 'cache_entries' in df else [],
            'jam_entries_timeline': df.get('jam_entries', []).tolist() if 'jam_entries' in df else [],
            'num_jammed_nodes_timeline': df.get('num_jammed_nodes', []).tolist() if 'num_jammed_nodes' in df else [],
            'jam_intensity_avg_timeline': df.get('jam_intensity_avg', []).tolist() if 'jam_intensity_avg' in df else [],
            'utilization_working_timeline': df.get('utilization_working', []).tolist() if 'utilization_working' in df else [],
            'utilization_active_timeline': df.get('utilization_active', []).tolist() if 'utilization_active' in df else [],
            'num_stuck_agents_timeline': df.get('num_stuck_agents', []).tolist() if 'num_stuck_agents' in df else [],
            'avg_stuck_counter_timeline': df.get('avg_stuck_counter', []).tolist() if 'avg_stuck_counter' in df else [],
            'max_stuck_counter_timeline': df.get('max_stuck_counter', []).tolist() if 'max_stuck_counter' in df else [],
            'stalls_in_transit_timeline': df.get('stalls_in_transit', []).tolist() if 'stalls_in_transit' in df else [],
            'stalls_at_resource_timeline': df.get('stalls_at_resource', []).tolist() if 'stalls_at_resource' in df else [],
            'stalls_idle_timeline': df.get('stalls_idle', []).tolist() if 'stalls_idle' in df else [],
            'steps': df['step'].tolist(),
            'latency_samples_s': latencies_s
        }
        
        return {
            'performance': performance_metrics,
            'coordination': coordination_metrics,
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
            mode_name = 'distributed' if mode in ['distributed', 'p2p'] else 'centralized' if mode == 'centralized' else 'misc'
            out_dir = self.file_handler.get_mode_directory(mode_name)

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
                    summary_row.update({f"coord_{k}": v for k, v in result.metrics.get('coordination', {}).items()})
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
                self._generate_mode_plots(group, timestamp, out_dir)
    
    def _generate_mode_plots(self, results: List[ExperimentResult], timestamp: str, out_dir: Path):
        """Generate plots for a specific mode in the given output directory."""
        self.generate_plots(results, timestamp, out_dir)
        plots = self.output_config.get('plots')
        if isinstance(plots, list) and 'dashboard_style' in plots:
            self.generate_dashboard_reports(results, timestamp, out_dir)
    
    def generate_plots(self, results: List[ExperimentResult], timestamp: str, out_dir: Path):
        """Generate visualization plots for experiment results."""
        successful_results = [r for r in results if r.success]
        
        if not successful_results:
            self.logger.warning("No successful experiments to plot")
            return
        
        self.logger.info(f"Plots saved to {out_dir}")

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
                    throughput.append(tps * 60.0)
                else:
                    throughput.append(0.0)
            else:
                if current_time > 0:
                    throughput.append(completed_timeline[i] / current_time * 60.0)
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

    def generate_dashboard_reports(self, results: List[ExperimentResult], timestamp: str, out_dir: Path):
        """Generate per-experiment dashboard-style plots and CSV with key metrics."""
        successful = [r for r in results if r.success]
        if not successful:
            return
        rows = []
        for r in successful:
            perf = r.metrics.get('performance', {})
            ts = r.metrics.get('time_series', {})
            steps = ts.get('steps', [])
            created = ts.get('tasks_created_timeline', [])
            completed = ts.get('task_completion_timeline', [])
            active = ts.get('tasks_active_timeline', [])
            lat_samples = ts.get('latency_samples_s', [])
            step_interval_ms = perf.get('step_interval_ms', 100)
            step_dt = step_interval_ms / 1000.0
            
            time_points = [s * step_dt for s in steps]
            throughput_series = self._calculate_throughput_series(completed, time_points, window_s=30.0)
            latency_series = self._calculate_latency_series(lat_samples, completed, window_size=50)
            
            fig, axes = plt.subplots(2, 2, figsize=(18, 10))
            axes = axes.flatten()
            
            # Task Timeline - created/completed/active
            if time_points and created and completed and active:
                axes[0].plot(time_points, created, color='tab:blue', linewidth=2, label='Created', alpha=0.8)
                axes[0].plot(time_points, completed, color='tab:green', linewidth=2, label='Completed', alpha=0.8)
                axes[0].plot(time_points, active, color='tab:orange', linewidth=2, label='Active', alpha=0.8)
                axes[0].set_title(f"Task Timeline: {r.config_name}")
                axes[0].set_xlabel('Time (s)')
                axes[0].set_ylabel('Task Count')
                axes[0].legend()
                axes[0].grid(True, alpha=0.3)
            else:
                axes[0].text(0.5, 0.5, 'No task timeline data', ha='center', va='center', transform=axes[0].transAxes)
                axes[0].set_title(f"Task Timeline: {r.config_name}")
            
            # Throughput - continuous plot with 30s moving average
            if throughput_series:
                thr_time_points = time_points[:len(throughput_series)]
                axes[1].plot(thr_time_points, throughput_series, color='tab:orange', linewidth=2, label='30s moving avg')
                axes[1].set_title('Throughput over time')
                axes[1].set_xlabel('Time (s)')
                axes[1].set_ylabel('Tasks/min')
                axes[1].legend()
                axes[1].grid(True, alpha=0.3)
            else:
                axes[1].text(0.5, 0.5, 'No throughput data', ha='center', va='center', transform=axes[1].transAxes)
                axes[1].set_title('Throughput over time')
            
            # Latency - continuous plot with rolling average
            if latency_series:
                lat_time_points = time_points[:len(latency_series)]
                axes[2].plot(lat_time_points, latency_series, color='tab:green', linewidth=2, label='Rolling avg (50 tasks)')
                axes[2].set_title('Latency over time')
                axes[2].set_xlabel('Time (s)')
                axes[2].set_ylabel('Latency (s)')
                axes[2].legend()
                axes[2].grid(True, alpha=0.3)
            else:
                axes[2].text(0.5, 0.5, 'No latency data', ha='center', va='center', transform=axes[2].transAxes)
                axes[2].set_title('Latency over time')
            
            # Utilization - working and active (busy) agents over time
            util_working = ts.get('utilization_working_timeline', [])
            util_active = ts.get('utilization_active_timeline', [])
            
            if util_working and util_active and len(util_working) > 0:
                util_time_points = time_points[:len(util_working)]
                axes[3].plot(util_time_points, util_working, color='tab:blue', linewidth=2.5, label='Working', alpha=0.9)
                axes[3].plot(util_time_points, util_active, color='tab:orange', linewidth=2.5, label='Active (Working + Navigating)', alpha=0.9)
                axes[3].set_title('Agent Utilization over time')
                axes[3].set_xlabel('Time (s)')
                axes[3].set_ylabel('Utilization (fraction of agents)')
                axes[3].set_ylim([0, 1.05])
                axes[3].legend(loc='best')
                axes[3].grid(True, alpha=0.3)
                # Debug: print data ranges
                if util_working:
                    self.logger.info(f"Utilization plot data - working: min={min(util_working):.3f}, max={max(util_working):.3f}, samples={len(util_working)}")
                if util_active:
                    self.logger.info(f"Utilization plot data - active: min={min(util_active):.3f}, max={max(util_active):.3f}, samples={len(util_active)}")
            else:
                axes[3].text(0.5, 0.5, f'No utilization data (working={len(util_working)}, active={len(util_active)})', 
                           ha='center', va='center', transform=axes[3].transAxes, fontsize=10)
                axes[3].set_title('Agent Utilization over time')
                axes[3].set_xlabel('Time (s)')
                axes[3].set_ylabel('Utilization (fraction of agents)')
                axes[3].set_ylim([0, 1.0])
                axes[3].grid(True, alpha=0.3)
            
            plt.tight_layout()
            out_png = out_dir / f"dashboard_report_{r.config_name}_{timestamp}.png"
            plt.savefig(out_png, dpi=200)
            plt.close(fig)
            
            # Generate separate system performance plot
            self._generate_system_perf_plot(r, timestamp, out_dir, time_points, ts)
            
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
        df.to_csv(out_dir / f"dashboard_metrics_{timestamp}.csv", index=False)
    
    def _generate_system_perf_plot(self, result: ExperimentResult, timestamp: str, out_dir: Path, 
                                    time_points: List[float], ts: Dict):
        """Generate standalone system performance plot (cache, gossip, contention, etc.)."""
        cache_entries = ts.get('cache_entries_timeline', [])
        coord = result.metrics.get('coordination', {})
        
        if not cache_entries:
            return
        
        fig, axes = plt.subplots(2, 3, figsize=(20, 10))
        axes = axes.flatten()
        
        # Cache entries over time
        cache_time_points = time_points[:len(cache_entries)]
        if cache_entries:
            axes[0].plot(cache_time_points, cache_entries, color='tab:purple', linewidth=2)
            axes[0].set_title(f'DSM Cache Entries: {result.config_name}')
            axes[0].set_xlabel('Time (s)')
            axes[0].set_ylabel('Total Cache Entries')
            axes[0].grid(True, alpha=0.3)
        
        # High-value coordination metrics (bar chart)
        high_labels = ['Avg Cache\nSize', 'Total Gossip\nRounds']
        high_values = [
            coord.get('avg_cache_size', 0),
            coord.get('total_gossip_rounds', 0)
        ]
        colors_high = ['tab:purple', 'tab:cyan']
        axes[1].bar(high_labels, high_values, color=colors_high, alpha=0.7)
        axes[1].set_title('DSM Overhead')
        axes[1].set_ylabel('Count')
        axes[1].grid(True, alpha=0.3, axis='y')
        
        # Low-value coordination metrics (separate scale)
        low_labels = ['Tasks in\nRegistry', 'Active\nLeases']
        low_values = [
            coord.get('tasks_in_registry', 0),
            coord.get('active_leases', 0)
        ]
        colors_low = ['tab:orange', 'tab:red']
        axes[2].bar(low_labels, low_values, color=colors_low, alpha=0.7)
        axes[2].set_title('Coordinator State')
        axes[2].set_ylabel('Count')
        axes[2].grid(True, alpha=0.3, axis='y')
        
        # Congestion: Number of jammed nodes vs Average jam severity
        num_jammed_nodes = ts.get('num_jammed_nodes_timeline', [])
        jam_intensity_avg = ts.get('jam_intensity_avg_timeline', [])
        
        if num_jammed_nodes and jam_intensity_avg:
            contention_time = time_points[:len(num_jammed_nodes)]
            
            # Dual-axis: # jammed nodes (spread) vs avg jam severity (intensity)
            axes[3].plot(contention_time, num_jammed_nodes, color='#1f77b4', linewidth=2.5, label='# Jammed Nodes', alpha=0.9)
            ax3_twin = axes[3].twinx()
            ax3_twin.plot(contention_time, jam_intensity_avg, color='#9467bd', linewidth=2.5, label='Avg Jam Severity', alpha=0.9, linestyle='--')
            
            axes[3].set_title('Congestion: Spread vs Severity')
            axes[3].set_xlabel('Time (s)')
            axes[3].set_ylabel('# Jammed Nodes (spread)', color='#1f77b4')
            axes[3].tick_params(axis='y', labelcolor='#1f77b4')
            ax3_twin.set_ylabel('Avg Jam Severity (0-5)', color='#9467bd')
            ax3_twin.tick_params(axis='y', labelcolor='#9467bd')
            ax3_twin.set_ylim([0, 5.5])
            axes[3].grid(True, alpha=0.3)
            
            lines1, labels1 = axes[3].get_legend_handles_labels()
            lines2, labels2 = ax3_twin.get_legend_handles_labels()
            axes[3].legend(lines1 + lines2, labels1 + labels2, loc='best', fontsize=9)
        else:
            axes[3].text(0.5, 0.5, f'No congestion data (nodes={len(num_jammed_nodes)}, severity={len(jam_intensity_avg)})', 
                        ha='center', va='center', transform=axes[3].transAxes, fontsize=10)
            axes[3].set_title('Congestion: Spread vs Severity')
        
        # Point Contention: Number of stalled agents vs avg stall duration
        num_stuck = ts.get('num_stuck_agents_timeline', [])
        avg_stuck_counter = ts.get('avg_stuck_counter_timeline', [])
        
        if num_stuck and avg_stuck_counter:
            stall_time = time_points[:len(num_stuck)]
            
            axes[4].plot(stall_time, num_stuck, color='#d62728', linewidth=2.5, label='# Stalled Agents', alpha=0.9)
            ax4_twin = axes[4].twinx()
            ax4_twin.plot(stall_time, avg_stuck_counter, color='#ff7f0e', linewidth=2.5, label='Avg Stall Duration', alpha=0.9, linestyle='--')
            
            axes[4].set_title('Point Contention: Stalled Agents vs Stall Duration')
            axes[4].set_xlabel('Time (s)')
            axes[4].set_ylabel('# Stalled Agents (count)', color='#d62728')
            axes[4].tick_params(axis='y', labelcolor='#d62728')
            ax4_twin.set_ylabel('Avg Stall Duration (steps)', color='#ff7f0e')
            ax4_twin.tick_params(axis='y', labelcolor='#ff7f0e')
            axes[4].grid(True, alpha=0.3)
            
            lines1, labels1 = axes[4].get_legend_handles_labels()
            lines2, labels2 = ax4_twin.get_legend_handles_labels()
            axes[4].legend(lines1 + lines2, labels1 + labels2, loc='best', fontsize=9)
        else:
            axes[4].text(0.5, 0.5, f'No point contention data (stuck={len(num_stuck)}, duration={len(avg_stuck_counter)})', 
                        ha='center', va='center', transform=axes[4].transAxes, fontsize=10)
            axes[4].set_title('Point Contention: Stalled Agents vs Stall Duration')
        
        # Stall Location Breakdown: Where agents are getting stuck
        stalls_in_transit = ts.get('stalls_in_transit_timeline', [])
        stalls_at_resource = ts.get('stalls_at_resource_timeline', [])
        stalls_idle = ts.get('stalls_idle_timeline', [])
        
        if stalls_in_transit and stalls_at_resource:
            stall_loc_time = time_points[:len(stalls_in_transit)]
            
            axes[5].stackplot(stall_loc_time,
                             stalls_in_transit,
                             stalls_at_resource,
                             stalls_idle,
                             labels=['In Transit (Aisle)', 'At Resource (Pickup/Drop)', 'Idle'],
                             colors=['#8c564b', '#e377c2', '#7f7f7f'],
                             alpha=0.7)
            
            axes[5].set_title('Stall Location Breakdown: Where Agents Get Stuck')
            axes[5].set_xlabel('Time (s)')
            axes[5].set_ylabel('# Stalled Agents by Location')
            axes[5].legend(loc='upper right', fontsize=9)
            axes[5].grid(True, alpha=0.3)
        else:
            axes[5].text(0.5, 0.5, f'No stall location data', 
                        ha='center', va='center', transform=axes[5].transAxes, fontsize=10)
            axes[5].set_title('Stall Location Breakdown')
        
        plt.tight_layout()
        out_png = out_dir / f"system_perf_{result.config_name}_{timestamp}.png"
        plt.savefig(out_png, dpi=200)
        plt.close(fig)
    
    def _extract_config_details(self, experiment_names: List[str]) -> Dict:
        """Extract configuration details for metadata"""
        config_details = {}
        
        for exp_name in experiment_names:
            if exp_name not in self.experiments:
                continue
            
            config = self.experiments[exp_name]
            
            warehouse_size = config.get('warehouse', {}).get('size', [0, 0])
            simulation_config = config.get('simulation', {})
            agents_config = config.get('agents', {})
            tasks_config = config.get('tasks', {})
            dsm_config = config.get('dsm', {})
            
            aoi_threshold = config.get('aoi_threshold', dsm_config.get('aoi_threshold', 1000))
            
            config_details[exp_name] = {
                'warehouse': {
                    'dimensions': warehouse_size,
                    'total_cells': warehouse_size[0] * warehouse_size[1] if len(warehouse_size) == 2 else 0,
                    'storage_regions': len(config.get('warehouse', {}).get('storage_regions', [])),
                    'sortation_regions': len(config.get('warehouse', {}).get('sortation_regions', []))
                },
                'agents': {
                    'count': agents_config.get('count', 0),
                    'initial_positions': agents_config.get('initial_positions', 'unknown')
                },
                'simulation': {
                    'mode': simulation_config.get('mode', 'distributed'),
                    'duration_s': simulation_config.get('duration', 0),
                    'step_interval_ms': simulation_config.get('step_interval', 50),
                    'total_steps': int(simulation_config.get('duration', 0) * 1000 / simulation_config.get('step_interval', 50)),
                    'seed': simulation_config.get('seed')
                },
                'tasks': {
                    'arrival_rate': tasks_config.get('arrival_rate', 0),
                    'types': tasks_config.get('types', []),
                    'priorities': tasks_config.get('priorities', [])
                },
                'coordination': {
                    'gossip_interval': coord_config.get('gossip_interval', 3),
                    'aoi_threshold_ms': coord_config.get('aoi_threshold', 1000),
                    'lease_ttl_ms': coord_config.get('lease_ttl', 30000)
                } if (coord_config := config.get('coordination', {})) else None
            }
        
        return config_details
    
    def run_experiments(self, experiment_names: Optional[List[str]] = None, use_lf: bool = False) -> List[ExperimentResult]:
        """Run specified experiments or all if none specified."""
        if experiment_names is None:
            experiment_names = list(self.experiments.keys())
        
        config_details = self._extract_config_details(experiment_names)
        
        run_dir = self.file_handler.create_run_directory(
            description=f"Baseline comparison: {', '.join(experiment_names)}",
            config_names=experiment_names,
            config_details=config_details
        )
        self.logger.info(f"Created run directory: {run_dir}")
        
        results = []
        
        for exp_name in experiment_names:
            if exp_name not in self.experiments:
                self.logger.error(f"Unknown experiment: {exp_name}")
                continue
                
            config = self.experiments[exp_name]
            result = self.run_single_experiment(exp_name, config, use_lf=use_lf)
            results.append(result)
        
        self.save_results(results)
        self.file_handler.mark_run_complete()
        
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
                        help='Drive simulation steps from Lingua Franca tick events (default, required for determinism)')
    lf_group.add_argument('--no-lf', dest='lf', action='store_false',
                        help='Use internal clock - WARNING: NOT DETERMINISTIC, for testing only')
    
    parser.add_argument('--log-interval', type=int, default=None,
                       help='Progress log interval in steps (default: 100). Use 0 to disable.')
    parser.add_argument('--duration', '-d', type=int, default=None,
                       help='Override simulation duration in seconds')
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Start LF coordinator if requested
    lf_proc = None
    if args.lf:
        lf_proc = start_lf_coordinator()
        if lf_proc is None:
            print("\nERROR: LF coordinator required but unavailable.")
            print("Experiments require Lingua Franca for deterministic, reproducible results.")
            print("Please compile the LF coordinator:")
            print(f"  cd {Path(__file__).parent.parent / 'lf'}")
            print("  lfc coordinator.lf")
            print("\nOr use --no-lf for non-deterministic testing (NOT recommended for experiments).")
            sys.exit(1)
    
    # Initialize runner
    runner = ExperimentRunner(args.config, args.output, duration_override=args.duration)
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