"""
Membership Tracker for Agent Heartbeats

Optional component for failure detection.
Tracks agent liveness via periodic heartbeats.
"""

from typing import Dict, List, Set
import time


class MembershipTracker:
    """Tracks agent membership and detects failures"""
    
    def __init__(self, heartbeat_timeout_ms: int = 30000):
        self.heartbeat_timeout_ms = heartbeat_timeout_ms
        self.agents: Dict[int, int] = {}
        self.failed_agents: Set[int] = set()
    
    def register(self, agent_id: int, current_time_ms: int) -> None:
        """Register a new agent"""
        self.agents[agent_id] = current_time_ms
        self.failed_agents.discard(agent_id)
    
    def heartbeat(self, agent_id: int, current_time_ms: int) -> None:
        """Record heartbeat from agent"""
        self.agents[agent_id] = current_time_ms
    
    def detect_failures(self, current_time_ms: int) -> List[int]:
        """
        Detect failed agents (no heartbeat within timeout).
        Returns list of newly failed agent IDs.
        """
        newly_failed = []
        
        for agent_id, last_heartbeat_ms in list(self.agents.items()):
            if current_time_ms - last_heartbeat_ms > self.heartbeat_timeout_ms:
                if agent_id not in self.failed_agents:
                    newly_failed.append(agent_id)
                    self.failed_agents.add(agent_id)
        
        return newly_failed
    
    def get_active(self, current_time_ms: int) -> List[int]:
        """Get list of currently active agents"""
        active = []
        
        for agent_id, last_heartbeat_ms in self.agents.items():
            if current_time_ms - last_heartbeat_ms <= self.heartbeat_timeout_ms:
                active.append(agent_id)
        
        return active

