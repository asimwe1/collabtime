"""
Memory Owners and Halo Gossip for Distributed Shared Memory

Implements tile ownership, CRDT merges, and halo gossip between memory agents.
Each owner manages a region of the warehouse graph and synchronizes with neighbors.
"""

import time
import numpy as np
from typing import Dict, List, Set, Any
from dataclasses import dataclass


@dataclass
class TileDelta:
    """Represents a change to be applied to a tile"""
    layer: str
    tile_id: int
    value: float
    timestamp_ms: int
    agent_id: int = None


@dataclass
class HaloMessage:
    """Message for halo gossip between memory owners"""
    source_owner: int
    target_owner: int
    deltas: List[TileDelta]
    epoch: int
    sequence: int


class MemoryOwner:
    """Manages a region of tiles and coordinates with neighboring owners"""
    
    def __init__(self, owner_id: int, managed_tiles: Set[int], halo_neighbors: Set[int]):
        self.owner_id = owner_id
        self.managed_tiles = managed_tiles
        self.halo_neighbors = halo_neighbors
        
        # Local tile storage
        self.tile_data = {}  # (layer, tile_id) -> value
        self.tile_timestamps = {}  # (layer, tile_id) -> timestamp_ms
        
        # Halo cache from neighboring owners
        self.halo_cache = {}  # (layer, tile_id) -> {'value', 'timestamp_ms', 'source_owner'}
        
        # Gossip coordination
        self.gossip_epoch = 0
        self.gossip_sequence = 0
        self.last_gossip_time = 0
        self.gossip_period_ms = 150  # 150ms gossip period
        
        # Metrics
        self.metrics = {
            'messages_sent': 0,
            'messages_received': 0,
            'bytes_sent': 0,
            'bytes_received': 0,
            'halo_updates': 0
        }
    
    def read_tile(self, layer: str, tile_id: int, max_aoi_ms: int) -> Dict[str, Any]:
        """Read a tile value, checking both local and halo cache"""
        current_time = int(time.time() * 1000)
        
        # Check local tiles first
        if tile_id in self.managed_tiles:
            key = (layer, tile_id)
            if key in self.tile_data:
                timestamp = self.tile_timestamps.get(key, 0)
                age_ms = current_time - timestamp
                
                if age_ms <= max_aoi_ms:
                    return {
                        'value': self.tile_data[key],
                        'age_ms': age_ms,
                        'timestamp': timestamp,
                        'source': 'local'
                    }
        
        # Check halo cache
        halo_key = (layer, tile_id)
        if halo_key in self.halo_cache:
            halo_entry = self.halo_cache[halo_key]
            age_ms = current_time - halo_entry['timestamp_ms']
            
            if age_ms <= max_aoi_ms:
                return {
                    'value': halo_entry['value'],
                    'age_ms': age_ms,
                    'timestamp': halo_entry['timestamp_ms'],
                    'source': f"halo_from_{halo_entry['source_owner']}"
                }
        
        # No valid data found
        return None
    
    def write_tile(self, layer: str, tile_id: int, value: float, timestamp_ms: int, agent_id: int = None):
        """Write to a tile (only if we own it)"""
        if tile_id not in self.managed_tiles:
            raise ValueError(f"Owner {self.owner_id} does not manage tile {tile_id}")
        
        key = (layer, tile_id)
        
        # Apply CRDT merge function
        current_value = self.tile_data.get(key, 0.0)
        merged_value = self._merge_value(layer, current_value, value)
        
        self.tile_data[key] = merged_value
        self.tile_timestamps[key] = timestamp_ms
        
        # Queue for halo gossip
        delta = TileDelta(layer, tile_id, merged_value, timestamp_ms, agent_id)
        self._queue_for_gossip(delta)
    
    def receive_halo_message(self, message: HaloMessage):
        """Process incoming halo gossip message"""
        self.metrics['messages_received'] += 1
        self.metrics['bytes_received'] += self._estimate_message_size(message)
        
        for delta in message.deltas:
            # Only accept halo updates for boundary tiles
            if self._is_boundary_tile(delta.tile_id):
                halo_key = (delta.layer, delta.tile_id)
                
                # Update halo cache with newer information
                current_entry = self.halo_cache.get(halo_key)
                if (current_entry is None or 
                    delta.timestamp_ms > current_entry['timestamp_ms']):
                    
                    self.halo_cache[halo_key] = {
                        'value': delta.value,
                        'timestamp_ms': delta.timestamp_ms,
                        'source_owner': message.source_owner
                    }
                    self.metrics['halo_updates'] += 1
    
    def periodic_gossip(self) -> List[HaloMessage]:
        """Generate halo gossip messages for neighboring owners"""
        current_time = int(time.time() * 1000)
        
        if current_time - self.last_gossip_time < self.gossip_period_ms:
            return []
        
        messages = []
        boundary_deltas = self._get_boundary_deltas()
        
        if boundary_deltas:
            self.gossip_epoch += 1
            
            for neighbor_owner in self.halo_neighbors:
                # Filter deltas relevant to this neighbor
                relevant_deltas = [d for d in boundary_deltas 
                                 if self._is_relevant_to_neighbor(d.tile_id, neighbor_owner)]
                
                if relevant_deltas:
                    message = HaloMessage(
                        source_owner=self.owner_id,
                        target_owner=neighbor_owner,
                        deltas=relevant_deltas,
                        epoch=self.gossip_epoch,
                        sequence=self.gossip_sequence
                    )
                    messages.append(message)
                    
                    self.metrics['messages_sent'] += 1
                    self.metrics['bytes_sent'] += self._estimate_message_size(message)
            
            self.gossip_sequence += 1
            self.last_gossip_time = current_time
        
        return messages
    
    def _merge_value(self, layer: str, current: float, new: float) -> float:
        """Apply CRDT merge function based on layer type"""
        if layer == 'task_signal':
            return max(current, new)
        elif layer == 'flow_trace':
            return max(current, new)
        elif layer == 'jam_signal':
            alpha = 0.5
            cap = 5.0
            result = alpha * new + (1 - alpha) * current
            return min(result, cap)
        else:
            return new  # Default: last writer wins
    
    def _queue_for_gossip(self, delta: TileDelta):
        """Queue a delta for inclusion in next gossip round"""
        # In a full implementation, this would maintain a gossip queue
        # For now, we rely on periodic_gossip to scan recent updates
        pass
    
    def _get_boundary_deltas(self) -> List[TileDelta]:
        """Get recent deltas for tiles near region boundaries"""
        current_time = int(time.time() * 1000)
        boundary_deltas = []
        
        # Look for recent updates to boundary tiles
        for (layer, tile_id), timestamp in self.tile_timestamps.items():
            if (current_time - timestamp < self.gossip_period_ms * 2 and
                self._is_boundary_tile(tile_id)):
                
                value = self.tile_data[(layer, tile_id)]
                delta = TileDelta(layer, tile_id, value, timestamp)
                boundary_deltas.append(delta)
        
        return boundary_deltas
    
    def _is_boundary_tile(self, tile_id: int) -> bool:
        """Check if a tile is near the boundary of our region"""
        # Simplified: assume tiles are boundary if they're adjacent to non-owned tiles
        # In practice, this would use the actual graph topology
        return True  # For now, treat all tiles as potential boundary tiles
    
    def _is_relevant_to_neighbor(self, tile_id: int, neighbor_owner: int) -> bool:
        """Check if a tile update is relevant to a specific neighbor"""
        # Simplified: assume all boundary updates are relevant to all neighbors
        return True
    
    def _estimate_message_size(self, message: HaloMessage) -> int:
        """Estimate message size in bytes for bandwidth tracking"""
        # Rough estimate: header + deltas
        base_size = 32  # Message header
        delta_size = len(message.deltas) * 24  # Each delta ~24 bytes
        return base_size + delta_size


class DistributedMemorySystem:
    """Coordinates multiple memory owners"""
    
    def __init__(self):
        self.owners = {}  # owner_id -> MemoryOwner
        self.message_queue = []  # Pending halo messages
    
    def add_owner(self, owner: MemoryOwner):
        """Add a memory owner to the system"""
        self.owners[owner.owner_id] = owner
    
    def route_message(self, message: HaloMessage):
        """Route a halo message to its target owner"""
        if message.target_owner in self.owners:
            self.owners[message.target_owner].receive_halo_message(message)
    
    def step(self):
        """Execute one simulation step - process gossip"""
        # Collect gossip messages from all owners
        new_messages = []
        for owner in self.owners.values():
            messages = owner.periodic_gossip()
            new_messages.extend(messages)
        
        # Route messages
        for message in new_messages:
            self.route_message(message)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Collect metrics from all owners"""
        total_metrics = {
            'total_messages_sent': 0,
            'total_messages_received': 0,
            'total_bytes_sent': 0,
            'total_bytes_received': 0,
            'total_halo_updates': 0,
            'owner_metrics': {}
        }
        
        for owner_id, owner in self.owners.items():
            total_metrics['total_messages_sent'] += owner.metrics['messages_sent']
            total_metrics['total_messages_received'] += owner.metrics['messages_received']
            total_metrics['total_bytes_sent'] += owner.metrics['bytes_sent']
            total_metrics['total_bytes_received'] += owner.metrics['bytes_received']
            total_metrics['total_halo_updates'] += owner.metrics['halo_updates']
            total_metrics['owner_metrics'][owner_id] = owner.metrics.copy()
        
        return total_metrics