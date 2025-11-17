"""
Lightweight DSM Router to simulate distributed memory owners.

Design goals:
- Preserve the `DSM` interface used by agents/model: create_task, claim, complete_task, reset
- Expose a unified `task_registry.tasks` dictionary view for compatibility
- Partition tasks by region using a simple node->shard mapping
- Implement halo exchange: periodically share boundary data between adjacent shards
"""

from typing import Callable, Dict, Tuple, List, Any, Set, Optional
import time

from .api import DSM


def default_region_mapper(node_id: int, width: int, num_shards: int) -> int:
    """Partition nodes by vertical slices across the warehouse width."""
    if width <= 0 or num_shards <= 0:
        return 0
    x = node_id % width
    slice_width = max(1, width // num_shards)
    shard = min(num_shards - 1, x // slice_width)
    return shard


class RouterTaskRegistry:
    """Aggregated view over per-shard TaskRegistries with caching."""

    def __init__(self, router: "DSMRouter"):
        self._router = router
        self._cached_tasks: Optional[Dict[int, Dict[str, Any]]] = None
        self._cache_dirty = True

    def invalidate_cache(self):
        """Mark cache as dirty so next access rebuilds it."""
        self._cache_dirty = True

    @property
    def tasks(self) -> Dict[int, Dict[str, Any]]:
        # Return cached view if still valid
        if not self._cache_dirty and self._cached_tasks is not None:
            return self._cached_tasks
        
        # Rebuild merged dict
        merged: Dict[int, Dict[str, Any]] = {}
        for shard_idx, shard in enumerate(self._router.shards):
            for local_id, task in shard.task_registry.tasks.items():
                gid = self._router._compose_global_id(shard_idx, local_id)
                merged[gid] = task
        
        self._cached_tasks = merged
        self._cache_dirty = False
        return merged


class DSMRouter:
    """Facade over multiple DSM shards with region-based routing.

    Global task ids encode (shard_index, local_task_id) using 32-bit fields.
    """

    def __init__(
        self,
        num_shards: int,
        node_to_shard: Callable[[int], int],
        dsm_factory: Callable[[], DSM] = DSM,
        total_nodes: int = 300,
        halo_radius: int = 2,
        gossip_period_ms: int = 150,
        aoi_threshold: int = 1000,
    ) -> None:
        self.num_shards = max(1, num_shards)
        self.node_to_shard = node_to_shard
        self.shards: List[DSM] = [dsm_factory() for _ in range(self.num_shards)]
        self.task_registry = RouterTaskRegistry(self)
        self.total_nodes = total_nodes
        self.halo_radius = halo_radius
        self.gossip_period_ms = gossip_period_ms
        self.aoi_threshold = aoi_threshold
        self.last_gossip_time = 0
        
        self.boundary_nodes: Dict[int, Set[int]] = {}
        self.shard_neighbors: Dict[int, Set[int]] = {}
        self._compute_boundaries_and_neighbors()
        
        self.stats: Dict[str, Any] = {
            'reads': 0,
            'writes': 0,
            'gossip_messages': 0,
            'aoi_violations': 0,
            'coordination_time': 0.0,
        }

    # --- ID encoding helpers ---
    @staticmethod
    def _compose_global_id(shard_index: int, local_id: int) -> int:
        return (int(shard_index) << 32) | int(local_id)

    @staticmethod
    def _split_global_id(global_id: int) -> Tuple[int, int]:
        shard_index = (int(global_id) >> 32) & 0xFFFFFFFF
        local_id = int(global_id) & 0xFFFFFFFF
        return shard_index, local_id

    # --- Core API (compat with DSM) ---
    def read_window(self, layer: str, node: int, radius: int, max_aoi_ms: int) -> Dict[str, Any]:
        shard_idx = self.node_to_shard(node)
        result = self.shards[shard_idx].read_window(layer, node, radius, max_aoi_ms)
        
        self.stats['reads'] += 1
        self.stats['aoi_violations'] += result.get('filtered_count', 0)
        
        return result

    def write_delta(self, layer: str, deltas: Dict[int, float], source_ts_ms: int) -> None:
        # Route each node's delta to its shard
        per_shard: Dict[int, Dict[int, float]] = {}
        for node_id, value in deltas.items():
            idx = self.node_to_shard(node_id)
            if idx not in per_shard:
                per_shard[idx] = {}
            per_shard[idx][node_id] = value
        for idx, shard_deltas in per_shard.items():
            self.shards[idx].write_delta(layer, shard_deltas, source_ts_ms)
        self.stats['writes'] += 1

    def create_task(self, location: int) -> int:
        shard_idx = self.node_to_shard(location)
        local_id = self.shards[shard_idx].create_task(location)
        self.task_registry.invalidate_cache()  # Cache invalidation
        return self._compose_global_id(shard_idx, local_id)

    def claim(self, task_id: int, agent_id: int) -> bool:
        shard_idx, local_id = self._split_global_id(task_id)
        if shard_idx < 0 or shard_idx >= len(self.shards):
            return False
        result = self.shards[shard_idx].claim(local_id, agent_id)
        if result:  # Only invalidate if claim succeeded
            self.task_registry.invalidate_cache()
        return result

    def complete_task(self, task_id: int) -> bool:
        shard_idx, local_id = self._split_global_id(task_id)
        if shard_idx < 0 or shard_idx >= len(self.shards):
            return False
        result = self.shards[shard_idx].complete_task(local_id)
        if result:  # Only invalidate if completion succeeded
            self.task_registry.invalidate_cache()
        return result
        if shard_idx < 0 or shard_idx >= len(self.shards):
            return False
        return self.shards[shard_idx].complete_task(local_id)

    def cleanup_old_tasks(self, keep_recent: int = 1000) -> None:
        """Remove old completed/failed tasks from all shards."""
        for shard in self.shards:
            if hasattr(shard, 'cleanup_old_tasks'):
                shard.cleanup_old_tasks(keep_recent // len(self.shards))
    
    def reset(self) -> None:
        for shard in self.shards:
            shard.reset()
        self.last_gossip_time = 0
        self.stats = {
            'reads': 0,
            'writes': 0,
            'gossip_messages': 0,
            'aoi_violations': 0,
            'coordination_time': 0.0,
        }

    # --- Halo Exchange / Gossip ---
    def _compute_boundaries_and_neighbors(self) -> None:
        """Identify boundary nodes and neighbor relationships for each shard."""
        for node_id in range(self.total_nodes):
            shard_id = self.node_to_shard(node_id)
            if shard_id not in self.boundary_nodes:
                self.boundary_nodes[shard_id] = set()
                self.shard_neighbors[shard_id] = set()
            
            left = node_id - 1
            right = node_id + 1
            neighbors_different_shard = False
            
            if left >= 0 and self.node_to_shard(left) != shard_id:
                neighbors_different_shard = True
                self.shard_neighbors[shard_id].add(self.node_to_shard(left))
            if right < self.total_nodes and self.node_to_shard(right) != shard_id:
                neighbors_different_shard = True
                self.shard_neighbors[shard_id].add(self.node_to_shard(right))
            
            if neighbors_different_shard:
                self.boundary_nodes[shard_id].add(node_id)

    def periodic_gossip(self, current_time_ms: int) -> int:
        """Exchange boundary layer data between adjacent shards. Returns number of gossip messages sent."""
        if current_time_ms - self.last_gossip_time < self.gossip_period_ms:
            return 0
        
        gossip_start = time.time()
        messages_sent = 0
        
        for shard_id, boundary_set in self.boundary_nodes.items():
            if not boundary_set:
                continue
            
            source_shard = self.shards[shard_id]
            
            for layer_name in ['task_signal', 'flow_trace', 'jam_signal']:
                layer = source_shard.layers.get(layer_name)
                if not layer:
                    continue
                
                for node_id in boundary_set:
                    if node_id in layer.data:
                        value = layer.data[node_id]
                        timestamp = layer.timestamps.get(node_id, current_time_ms)
                        
                        for neighbor_shard_id in self.shard_neighbors.get(shard_id, set()):
                            if neighbor_shard_id < len(self.shards):
                                neighbor_shard = self.shards[neighbor_shard_id]
                                neighbor_layer = neighbor_shard.layers.get(layer_name)
                                if neighbor_layer:
                                    if node_id not in neighbor_layer.data or neighbor_layer.timestamps.get(node_id, 0) < timestamp:
                                        neighbor_layer.data[node_id] = value
                                        neighbor_layer.timestamps[node_id] = timestamp
                                        messages_sent += 1
        
        gossip_time = time.time() - gossip_start
        self.stats['gossip_messages'] += messages_sent
        self.stats['coordination_time'] += gossip_time
        self.last_gossip_time = current_time_ms
        
        if messages_sent > 0 and self.stats['gossip_messages'] % 1000 == 0:
            print(f"[DSMRouter] Gossip: {messages_sent} messages this round, {self.stats['gossip_messages']} total, {gossip_time*1000:.2f}ms")
        
        return messages_sent

    # Diagnostics API (minimal)
    def get_partition_quality(self) -> float:
        if not self.boundary_nodes:
            return 1.0
        total_boundary = sum(len(nodes) for nodes in self.boundary_nodes.values())
        if total_boundary == 0:
            return 1.0
        return max(0.0, 1.0 - (total_boundary / self.total_nodes))


