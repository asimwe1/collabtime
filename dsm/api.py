"""
Distributed Shared Memory API for Warehouse DSM System

Core interface for agents to interact with the distributed memory system.
Provides read_window, write_delta, and claim operations with AoI guarantees.
"""

from typing import Dict, Any, Optional
import time
import numpy as np


class DSMLayer:
    """Individual data layer in the DSM (e.g., task_signal, flow_trace, jam_signal)"""
    
    def __init__(self, name: str, merge_fn, default_value=0.0):
        self.name = name
        self.merge_fn = merge_fn
        self.data = {}  # node_id -> value
        self.timestamps = {}  # node_id -> last_update_ms
        self.default_value = default_value
    
    def read_window(self, node: int, radius: int, max_aoi_ms: int) -> Dict[str, Any]:
        """Read values within radius of node, respecting AoI constraints"""
        current_time = int(time.time() * 1000)
        window_data = {}
        peak_value = self.default_value
        peak_node = node
        
        # Get neighbors within radius (simplified - would use graph traversal)
        neighbors = self._get_neighbors(node, radius)
        
        for neighbor in neighbors:
            if neighbor in self.data:
                timestamp = self.timestamps.get(neighbor, 0)
                age_ms = current_time - timestamp
                
                if age_ms <= max_aoi_ms:
                    value = self.data[neighbor]
                    window_data[neighbor] = {
                        'value': value,
                        'age_ms': age_ms,
                        'timestamp': timestamp
                    }
                    
                    if value > peak_value:
                        peak_value = value
                        peak_node = neighbor
        
        return {
            'window_data': window_data,
            'peak_value': peak_value,
            'peak_node': peak_node,
            'query_time_ms': current_time
        }
    
    def write_delta(self, deltas: Dict[int, float], source_ts_ms: int):
        """Apply deltas using the layer's merge function"""
        for node_id, delta_value in deltas.items():
            current_value = self.data.get(node_id, self.default_value)
            merged_value = self.merge_fn(current_value, delta_value)
            
            self.data[node_id] = merged_value
            self.timestamps[node_id] = source_ts_ms
    
    def _get_neighbors(self, node: int, radius: int) -> list:
        """Get nodes within radius (simplified implementation)"""
        # TODO: Replace with actual graph traversal
        return list(range(max(0, node - radius), node + radius + 1))


class TaskRegistry:
    """Manages task lifecycle and claim coordination"""
    
    def __init__(self):
        self.tasks = {}  # task_id -> {'status', 'agent_id', 'created_ms', 'location'}
        self.task_counter = 0
    
    def create_task(self, location: int) -> int:
        """Create a new task at the specified location"""
        task_id = self.task_counter
        self.task_counter += 1
        
        self.tasks[task_id] = {
            'status': 'available',
            'agent_id': None,
            'created_ms': int(time.time() * 1000),
            'location': location
        }
        
        return task_id
    
    def claim(self, task_id: int, agent_id: int) -> bool:
        """Attempt to claim a task for an agent"""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        if task['status'] == 'available':
            task['status'] = 'claimed'
            task['agent_id'] = agent_id
            task['claimed_ms'] = int(time.time() * 1000)
            return True
        
        return False
    
    def complete_task(self, task_id: int) -> bool:
        """Mark a task as completed"""
        if task_id in self.tasks:
            self.tasks[task_id]['status'] = 'completed'
            self.tasks[task_id]['completed_ms'] = int(time.time() * 1000)
            return True
        return False


class DSM:
    """Main Distributed Shared Memory interface"""
    
    def __init__(self,
                 memory_owners: int = 1,
                 partition_strategy: str = "spatial",
                 halo_radius: int = 1,
                 aoi_threshold: int = 1000):
        # Configuration
        self.memory_owners = memory_owners
        self.partition_strategy = partition_strategy
        self.halo_radius = halo_radius
        self.aoi_threshold = aoi_threshold

        # Initialize data layers
        self.layers = {
            'task_signal': DSMLayer('task_signal', self._merge_task_signal),
            'flow_trace': DSMLayer('flow_trace', self._merge_flow_trace),
            'jam_signal': DSMLayer('jam_signal', self._merge_jam_signal)
        }
        
        self.task_registry = TaskRegistry()

        # Simple stats aggregation
        self.stats = {
            'reads': 0,
            'writes': 0,
            'gossip_messages': 0,
            'aoi_violations': 0,
            'coordination_time': 0.0,
        }
    
    def read_window(self, layer: str, node: int, radius: int, max_aoi_ms: int) -> Dict[str, Any]:
        """Read a window of data from the specified layer"""
        if layer not in self.layers:
            raise ValueError(f"Unknown layer: {layer}")
        result = self.layers[layer].read_window(node, radius, max_aoi_ms)
        self.stats['reads'] += 1
        # Count AoI violations (entries older than threshold)
        window = result.get('window_data', {})
        violations = sum(1 for v in window.values() if v.get('age_ms', 0) > self.aoi_threshold)
        self.stats['aoi_violations'] += violations
        return result
    
    def write_delta(self, layer: str, deltas: Dict[int, float], source_ts_ms: int) -> None:
        """Write deltas to the specified layer"""
        if layer not in self.layers:
            raise ValueError(f"Unknown layer: {layer}")
        
        self.layers[layer].write_delta(deltas, source_ts_ms)
        self.stats['writes'] += 1
    
    def claim(self, task_id: int, agent_id: int) -> bool:
        """Attempt to claim a task"""
        return self.task_registry.claim(task_id, agent_id)
    
    def create_task(self, location: int) -> int:
        """Create a new task and seed the signal"""
        task_id = self.task_registry.create_task(location)
        
        # Seed task signal at the location
        current_time = int(time.time() * 1000)
        self.write_delta('task_signal', {location: 1.0}, current_time)
        
        return task_id
    
    def complete_task(self, task_id: int) -> bool:
        """Complete a task and clear signals"""
        if self.task_registry.complete_task(task_id):
            # Clear task signal (simplified)
            task = self.task_registry.tasks[task_id]
            location = task['location']
            current_time = int(time.time() * 1000)
            self.write_delta('task_signal', {location: 0.0}, current_time)
            # Keep task in registry with 'completed' status for metrics tracking
            # Visualization will filter these out
            return True
        return False

    def cleanup_old_tasks(self, keep_recent: int = 1000) -> None:
        """Remove old completed/failed tasks to prevent memory bloat.
        Keeps the most recent 'keep_recent' completed tasks for metrics.
        """
        completed = [(tid, t.get('completed_ms', 0)) for tid, t in self.task_registry.tasks.items() 
                     if isinstance(t, dict) and t.get('status') in ['completed', 'failed']]
        
        if len(completed) > keep_recent:
            # Sort by completion time and remove oldest
            completed.sort(key=lambda x: x[1])
            to_remove = completed[:-keep_recent]
            for task_id, _ in to_remove:
                del self.task_registry.tasks[task_id]
    
    def reset(self) -> None:
        """Clear all DSM state: tasks, layers, and stats."""
        # Reset layers
        for layer in self.layers.values():
            layer.data.clear()
            layer.timestamps.clear()
        
        # Reset tasks
        self.task_registry = TaskRegistry()
        
        # Reset stats
        self.stats = {
            'reads': 0,
            'writes': 0,
            'gossip_messages': 0,
            'aoi_violations': 0,
            'coordination_time': 0.0,
        }

    # Diagnostics
    def get_partition_quality(self) -> float:
        """Return a simple partition quality score placeholder."""
        return 1.0
    
    # Merge functions for different data layers
    @staticmethod
    def _merge_task_signal(current: float, delta: float) -> float:
        """Task signals use maximum (strongest signal wins)"""
        return max(current, delta)
    
    @staticmethod
    def _merge_flow_trace(current: float, delta: float) -> float:
        """Flow traces accumulate (positive feedback)"""
        return max(current, delta)
    
    @staticmethod
    def _merge_jam_signal(current: float, delta: float) -> float:
        """Jam signals use exponential weighted moving average with cap"""
        alpha = 0.5
        cap = 5.0
        result = alpha * delta + (1 - alpha) * current
        return min(result, cap)


# Global DSM instance (legacy access). Prefer passing DSM into models explicitly.
dsm = DSM()