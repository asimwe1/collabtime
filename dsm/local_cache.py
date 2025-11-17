"""
Local CRDT-style Cache for Peer-to-Peer DSM

Each agent maintains its own local cache with timestamped values.
Gossip protocol merges caches using Last-Write-Wins (LWW) semantics.
"""

from typing import Dict, Any, Optional
import time


class LocalDSMCache:
    """
    Local cache of spatial and resource data with LWW merge semantics.
    
    Data categories:
    - flow_trace: Historical edge usage
    - jam_signal: Congestion levels
    - agent_location: Where other agents are
    - path_intent: Where other agents plan to be
    - resource_state: Physical resource lock ownership
    """
    
    def __init__(self, agent_id: int):
        self.agent_id = agent_id
        
        self.flow_trace: Dict[int, Dict[str, Any]] = {}
        self.jam_signal: Dict[int, Dict[str, Any]] = {}
        self.agent_location: Dict[int, Dict[str, Any]] = {}
        self.path_intent: Dict[tuple, Dict[str, Any]] = {}
        self.resource_state: Dict[str, Dict[str, Any]] = {}
    
    def write_flow(self, node_id: int, value: float, timestamp_ms: int):
        """Write flow trace value at node"""
        existing = self.flow_trace.get(node_id, {'value': 0.0, 'timestamp': 0})
        
        if timestamp_ms >= existing['timestamp']:
            self.flow_trace[node_id] = {
                'value': max(existing['value'], value),
                'timestamp': timestamp_ms
            }
    
    def write_jam(self, node_id: int, value: float, timestamp_ms: int):
        """Write jam signal value at node"""
        existing = self.jam_signal.get(node_id, {'value': 0.0, 'timestamp': 0})
        
        if timestamp_ms >= existing['timestamp']:
            alpha = 0.5
            merged_value = alpha * value + (1 - alpha) * existing['value']
            
            self.jam_signal[node_id] = {
                'value': min(merged_value, 5.0),
                'timestamp': timestamp_ms
            }
    
    def read_flow(self, node_id: int, max_aoi_ms: int) -> float:
        """Read flow trace at node (with AoI filtering)"""
        current_time = int(time.time() * 1000)
        entry = self.flow_trace.get(node_id)
        
        if entry and (current_time - entry['timestamp']) <= max_aoi_ms:
            return entry['value']
        return 0.0
    
    def read_jam(self, node_id: int, max_aoi_ms: int) -> float:
        """Read jam signal at node (with AoI filtering)"""
        current_time = int(time.time() * 1000)
        entry = self.jam_signal.get(node_id)
        
        if entry and (current_time - entry['timestamp']) <= max_aoi_ms:
            return entry['value']
        return 0.0
    
    def write_agent_location(self, agent_id: int, node_id: int, timestamp_ms: int):
        """Write agent's current location"""
        self.agent_location[agent_id] = {
            'node': node_id,
            'timestamp': timestamp_ms
        }
    
    def read_agents_at_node(self, node_id: int, max_aoi_ms: int) -> int:
        """Count agents at a node based on cached locations (with AoI filtering)"""
        current_time = int(time.time() * 1000)
        count = 0
        
        for agent_id, entry in self.agent_location.items():
            if entry['node'] == node_id:
                age_ms = current_time - entry['timestamp']
                if age_ms <= max_aoi_ms:
                    count += 1
        
        return count
    
    def write_path_intent(self, agent_id: int, node_id: int, arrival_time_ms: int, 
                         duration_ms: int, timestamp_ms: int):
        """Write agent's intent to be at a node at a specific time"""
        key = (agent_id, node_id)
        self.path_intent[key] = {
            'agent_id': agent_id,
            'node': node_id,
            'arrival_time': arrival_time_ms,
            'duration': duration_ms,
            'timestamp': timestamp_ms
        }
    
    def check_path_conflicts(self, node_id: int, my_arrival_time: int, 
                            max_aoi_ms: int, conflict_window_ms: int = 5000) -> bool:
        """Check if other agents plan to be at this node around the same time"""
        current_time = int(time.time() * 1000)
        
        for (agent_id, intent_node), entry in self.path_intent.items():
            if intent_node != node_id:
                continue
            
            if agent_id == self.agent_id:
                continue
            
            age_ms = current_time - entry['timestamp']
            if age_ms > max_aoi_ms:
                continue
            
            other_arrival = entry['arrival_time']
            if abs(my_arrival_time - other_arrival) < conflict_window_ms:
                return True
        
        return False
    
    def write_resource_state(self, resource_id: str, owner_id: int, 
                            expires_at_ms: int, timestamp_ms: int):
        """Write resource lock state (who owns it, when it expires)"""
        self.resource_state[resource_id] = {
            'owner': owner_id,
            'expires_at': expires_at_ms,
            'timestamp': timestamp_ms
        }
    
    def read_resource_owner(self, resource_id: str, max_aoi_ms: int) -> Optional[int]:
        """Check who owns a resource (returns None if free or stale data)"""
        current_time = int(time.time() * 1000)
        entry = self.resource_state.get(resource_id)
        
        if not entry:
            return None
        
        age_ms = current_time - entry['timestamp']
        if age_ms > max_aoi_ms:
            return None
        
        if current_time >= entry['expires_at']:
            return None
        
        return entry['owner']
    
    def release_resource_state(self, resource_id: str, timestamp_ms: int):
        """Mark a resource as released"""
        self.resource_state[resource_id] = {
            'owner': None,
            'expires_at': 0,
            'timestamp': timestamp_ms
        }
    
    def merge_from(self, other: 'LocalDSMCache'):
        """
        Merge another agent's cache into this one using LWW.
        This is the gossip merge operation.
        """
        # --- MERGE FLOW TRACE ---
        for node_id, other_entry in other.flow_trace.items():
            my_entry = self.flow_trace.get(node_id, {'value': 0.0, 'timestamp': 0})
            if other_entry['timestamp'] > my_entry['timestamp']:
                self.flow_trace[node_id] = other_entry.copy()

        # --- MERGE JAM SIGNAL ---
        for node_id, other_entry in other.jam_signal.items():
            my_entry = self.jam_signal.get(node_id, {'value': 0.0, 'timestamp': 0})
            if other_entry['timestamp'] > my_entry['timestamp']:
                self.jam_signal[node_id] = other_entry.copy()

        # --- MERGE AGENT LOCATIONS (CRITICAL FIX) ---
        # The original implementation was flawed. This ensures all newer entries
        # from the other cache are adopted, not just a simple LWW on the whole dictionary.
        for agent_id, other_entry in other.agent_location.items():
            my_entry = self.agent_location.get(agent_id, {'node': -1, 'timestamp': 0})
            if other_entry['timestamp'] > my_entry['timestamp']:
                self.agent_location[agent_id] = other_entry.copy()
        
        # --- MERGE PATH INTENT ---
        for key, other_entry in other.path_intent.items():
            my_entry = self.path_intent.get(key, {'timestamp': 0})
            if other_entry.get('timestamp', 0) > my_entry.get('timestamp', 0):
                self.path_intent[key] = other_entry.copy()
        
        # --- MERGE RESOURCE STATE ---
        for resource_id, other_entry in other.resource_state.items():
            my_entry = self.resource_state.get(resource_id, {'timestamp': 0})
            if other_entry.get('timestamp', 0) > my_entry.get('timestamp', 0):
                self.resource_state[resource_id] = other_entry.copy()
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            'flow_entries': len(self.flow_trace),
            'jam_entries': len(self.jam_signal),
            'agent_locations': len(self.agent_location),
            'path_intents': len(self.path_intent),
            'resource_states': len(self.resource_state)
        }
    
    def get_jam_intensity(self) -> float:
        """Get total jam intensity (sum of all jam values in cache)"""
        return sum(entry['value'] for entry in self.jam_signal.values())
    
    def cleanup_stale_entries(self, max_age_ms: int):
        """Remove cache entries older than max_age_ms to prevent unbounded growth"""
        current_time = int(time.time() * 1000)
        
        self.flow_trace = {
            k: v for k, v in self.flow_trace.items()
            if current_time - v['timestamp'] <= max_age_ms
        }
        
        self.jam_signal = {
            k: v for k, v in self.jam_signal.items()
            if current_time - v['timestamp'] <= max_age_ms
        }
        
        self.agent_location = {
            k: v for k, v in self.agent_location.items()
            if current_time - v['timestamp'] <= max_age_ms
        }
        
        self.path_intent = {
            k: v for k, v in self.path_intent.items()
            if current_time - v['timestamp'] <= max_age_ms
        }
        
        self.resource_state = {
            k: v for k, v in self.resource_state.items()
            if current_time - v['timestamp'] <= max_age_ms
        }

