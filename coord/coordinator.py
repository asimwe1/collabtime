"""
Coordinator: ZooKeeper-Style Control Plane

Provides strong consistency for task management with:
- Atomic task claims via LeaseManager
- Event notifications via WatchManager
- Authoritative task state via TaskRegistry
- Optional failure detection via MembershipTracker
"""

from typing import Dict, List, Any, Optional, Callable
import time
import logging

from .lease_manager import LeaseManager
from .task_registry import TaskRegistry, TaskStatus, Task
from .watch_manager import WatchManager
from .membership import MembershipTracker


class Coordinator:
    """
    ZooKeeper-like coordination service for warehouse simulation.
    
    Separates control plane (strong consistency) from data plane (eventual).
    Integrates with LF time base via tick().
    """
    
    def __init__(self, 
                 default_lease_ttl_ms: int = 300000,
                 heartbeat_timeout_ms: int = 30000):
        self.task_registry = TaskRegistry()
        self.lease_manager = LeaseManager(default_ttl_ms=default_lease_ttl_ms)
        self.watch_manager = WatchManager()
        self.membership = MembershipTracker(heartbeat_timeout_ms=heartbeat_timeout_ms)
        
        self.current_time_ms = 0
        self.logger = logging.getLogger(__name__)
        
        self.metrics = {
            'claim_attempts': 0,
            'claim_successes': 0,
            'claim_conflicts': 0,
            'lease_expirations': 0,
            'lease_renewals': 0,
            'acquire_calls': 0,
            'acquire_latency_ms': [],
            'watch_events_fired': 0,
            'resource_lock_attempts': 0,
            'resource_lock_successes': 0,
            'resource_lock_conflicts': 0,
        }
    
    def tick(self, current_time_ms: int) -> None:
        """
        Advance coordinator logical time; expire leases; detect failures.
        Called by model.step() before agent steps.
        """
        self.current_time_ms = current_time_ms
        
        expired = self.lease_manager.expire_leases(current_time_ms)
        self.metrics['lease_expirations'] += len(expired)
        
        for resource_id, owner_id in expired:
            if isinstance(resource_id, int):
                task = self.task_registry.get_task(resource_id)
                if task and task.status == TaskStatus.CLAIMED:
                    self.task_registry.fail_task(resource_id, owner_id, retry=True)
                    self.watch_manager.fire('task_released', {
                        'task_id': resource_id,
                        'agent_id': owner_id,
                        'reason': 'lease_expired'
                    })
        
        failed_agents = self.membership.detect_failures(current_time_ms)
        if failed_agents:
            for agent_id in failed_agents:
                self._release_all_leases(agent_id)
    
    def create_task(self, location: int, task_type: str = "pick", 
                   priority: float = 1.0) -> int:
        """
        Create a new task in the authoritative registry.
        Single source of truth for task existence and status.
        """
        task_id = self.task_registry.create_task(
            location=location,
            task_type=task_type,
            priority=priority,
            current_time_ms=self.current_time_ms,
        )
        
        self.watch_manager.fire('task_created', {
            'task_id': task_id,
            'location': location,
            'task_type': task_type
        })
        
        return task_id
    
    def get_available_tasks(self, region_id: int = None, 
                          max_distance: int = None) -> List[Task]:
        """
        Get available tasks, optionally filtered by region.
        Strong, consistent read - no stale data.
        """
        return self.task_registry.get_available_tasks(
            location=region_id,
            max_distance=max_distance
        )
    
    def get_task_status(self, task_id: int) -> Optional[str]:
        """Get current status of a task"""
        task = self.task_registry.get_task(task_id)
        return task.status.value if task else None
    
    def update_task_status(self, task_id: int, status: str) -> bool:
        """Update task status (internal use)"""
        task = self.task_registry.get_task(task_id)
        if task:
            task.status = TaskStatus(status)
            return True
        return False
    
    def try_claim(self, task_id: int, agent_id: int, ttl_ms: int = None) -> bool:
        """
        Attempt to claim a task lease. Returns True if granted.
        Atomically: 1) acquires lease, 2) updates task status to 'claimed'
        """
        self.metrics['claim_attempts'] += 1
        
        task = self.task_registry.get_task(task_id)
        if not task or task.status != TaskStatus.AVAILABLE:
            self.metrics['claim_conflicts'] += 1
            return False
        
        ttl = ttl_ms or self.lease_manager.default_ttl_ms
        lease_success = self.lease_manager.try_acquire(
            resource_id=task_id,
            owner_id=agent_id,
            ttl_ms=ttl,
            current_time_ms=self.current_time_ms
        )
        
        if lease_success:
            self.task_registry.claim_task(task_id, agent_id, self.current_time_ms)
            self.metrics['claim_successes'] += 1
            
            self.watch_manager.fire('task_claimed', {
                'task_id': task_id,
                'agent_id': agent_id
            })
        else:
            self.metrics['claim_conflicts'] += 1
        
        return lease_success
    
    def renew_claim(self, task_id: int, agent_id: int, ttl_ms: int = None) -> bool:
        """Renew an existing lease to extend TTL"""
        ttl = ttl_ms or self.lease_manager.default_ttl_ms
        success = self.lease_manager.renew(
            resource_id=task_id,
            owner_id=agent_id,
            ttl_ms=ttl,
            current_time_ms=self.current_time_ms
        )
        if success:
            self.metrics['lease_renewals'] += 1
        return success
    
    def release_claim(self, task_id: int, agent_id: int) -> bool:
        """Explicitly release a lease (for failed/cancelled tasks)"""
        success = self.lease_manager.release(
            resource_id=task_id,
            owner_id=agent_id
        )
        
        if success:
            task = self.task_registry.get_task(task_id)
            if task and task.agent_id == agent_id:
                self.task_registry.fail_task(task_id, agent_id, retry=True)
            
            self.watch_manager.fire('task_released', {
                'task_id': task_id,
                'agent_id': agent_id
            })
        
        return success
    
    def complete_task(self, task_id: int, agent_id: int) -> bool:
        """
        Mark task as completed and release lease.
        Atomically: 1) releases lease, 2) updates status to 'completed'
        """
        if self.lease_manager.get_owner(task_id) != agent_id:
            return False
        
        self.lease_manager.release(task_id, agent_id)
        success = self.task_registry.complete_task(task_id, agent_id, self.current_time_ms)
        
        if success:
            self.watch_manager.fire('task_completed', {
                'task_id': task_id,
                'agent_id': agent_id
            })
        
        return success
    
    def get_lease_owner(self, task_id: int) -> Optional[int]:
        """Check who currently holds the lease for a task"""
        return self.lease_manager.get_owner(task_id)
    
    def acquire_lock(self, resource_id: str, agent_id: int, ttl_ms: int = None) -> bool:
        """
        Acquire exclusive lock on a physical resource (bin, charger, etc).
        Use just-in-time when agent arrives at resource location.
        Returns True if granted, False if already held.
        """
        self.metrics['resource_lock_attempts'] += 1
        
        ttl = ttl_ms or 30000
        success = self.lease_manager.try_acquire(
            resource_id=resource_id,
            owner_id=agent_id,
            ttl_ms=ttl,
            current_time_ms=self.current_time_ms
        )
        
        if success:
            self.metrics['resource_lock_successes'] += 1
        else:
            self.metrics['resource_lock_conflicts'] += 1
        
        return success
    
    def release_lock(self, resource_id: str, agent_id: int) -> bool:
        """Release physical resource lock"""
        return self.lease_manager.release(
            resource_id=resource_id,
            owner_id=agent_id
        )
    
    def watch(self, region_id: int, event_type: str, 
             callback: Callable[[Dict[str, Any]], None]) -> str:
        """
        Register a callback for events in a region.
        
        Supported events:
          - 'task_created'
          - 'task_claimed'
          - 'task_released'
          - 'task_completed'
        
        Returns watch handle (use for unwatch).
        """
        return self.watch_manager.register(
            region_id=region_id,
            event_type=event_type,
            callback=callback
        )
    
    def unwatch(self, handle: str) -> None:
        """Unregister a watch callback"""
        self.watch_manager.unregister(handle)
    
    def register_agent(self, agent_id: int) -> None:
        """Register agent with coordinator (for failure detection)"""
        self.membership.register(agent_id, self.current_time_ms)
    
    def heartbeat(self, agent_id: int) -> None:
        """Agent heartbeat to signal liveness"""
        self.membership.heartbeat(agent_id, self.current_time_ms)
    
    def get_active_agents(self) -> List[int]:
        """Get list of currently active agents"""
        return self.membership.get_active(self.current_time_ms)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get coordinator metrics"""
        metrics = self.metrics.copy()
        if self.metrics['acquire_latency_ms']:
            metrics['avg_acquire_latency_ms'] = sum(self.metrics['acquire_latency_ms']) / len(self.metrics['acquire_latency_ms'])
        return metrics
    
    def _release_all_leases(self, agent_id: int) -> None:
        """Release all leases held by an agent (for failure recovery)"""
        released = []
        for resource_id, lease in list(self.lease_manager.leases.items()):
            if lease.owner_id == agent_id:
                self.lease_manager.release(resource_id, agent_id)
                released.append(resource_id)
        
        for resource_id in released:
            if isinstance(resource_id, int):
                task = self.task_registry.get_task(resource_id)
                if task and task.status == TaskStatus.CLAIMED:
                    self.task_registry.fail_task(resource_id, agent_id, retry=True)
