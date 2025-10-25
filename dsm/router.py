"""
Lightweight DSM Router to simulate distributed memory owners.

Design goals:
- Preserve the `DSM` interface used by agents/model: create_task, claim, complete_task, reset
- Expose a unified `task_registry.tasks` dictionary view for compatibility
- Partition tasks by region using a simple node->shard mapping
"""

from typing import Callable, Dict, Tuple, List, Any

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
    """Aggregated view over per-shard TaskRegistries."""

    def __init__(self, router: "DSMRouter"):
        self._router = router

    @property
    def tasks(self) -> Dict[int, Dict[str, Any]]:
        # Build a merged dict of tasks keyed by global_id
        merged: Dict[int, Dict[str, Any]] = {}
        for shard_idx, shard in enumerate(self._router.shards):
            for local_id, task in shard.task_registry.tasks.items():
                gid = self._router._compose_global_id(shard_idx, local_id)
                merged[gid] = task
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
    ) -> None:
        self.num_shards = max(1, num_shards)
        self.node_to_shard = node_to_shard
        self.shards: List[DSM] = [dsm_factory() for _ in range(self.num_shards)]
        self.task_registry = RouterTaskRegistry(self)
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
        # Aggregate stats roughly
        self.stats['reads'] += 1
        self.stats['aoi_violations'] += max(0, result.get('window_data') and 0)
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
        return self._compose_global_id(shard_idx, local_id)

    def claim(self, task_id: int, agent_id: int) -> bool:
        shard_idx, local_id = self._split_global_id(task_id)
        if shard_idx < 0 or shard_idx >= len(self.shards):
            return False
        return self.shards[shard_idx].claim(local_id, agent_id)

    def complete_task(self, task_id: int) -> bool:
        shard_idx, local_id = self._split_global_id(task_id)
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
        # Reset aggregated stats
        self.stats = {
            'reads': 0,
            'writes': 0,
            'gossip_messages': 0,
            'aoi_violations': 0,
            'coordination_time': 0.0,
        }

    # Diagnostics API (minimal)
    def get_partition_quality(self) -> float:
        return 1.0


