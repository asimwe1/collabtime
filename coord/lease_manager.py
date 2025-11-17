"""
Lease Manager for Exclusive Resource Locking

Provides distributed lock semantics with TTL-based expiration for both:
- Task locks (long-term, minutes): Claim responsibility for a task
- Resource locks (short-term, seconds): JIT exclusive access to physical nodes
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional


@dataclass
class Lease:
    """Represents an exclusive lease on a resource"""
    resource_id: any
    owner_id: int
    granted_ms: int
    expires_ms: int


class LeaseManager:
    """Manages exclusive leases with TTL expiration"""
    
    def __init__(self, default_ttl_ms: int = 5000):
        self.default_ttl_ms = default_ttl_ms
        self.leases: Dict[any, Lease] = {}
    
    def try_acquire(self, resource_id: any, owner_id: int, ttl_ms: int, 
                    current_time_ms: int) -> bool:
        """
        CAS-like: grant lease if resource is free or lease expired.
        Returns True if granted, False if already held by another owner.
        """
        existing = self.leases.get(resource_id)
        
        if existing is None or existing.expires_ms <= current_time_ms:
            self.leases[resource_id] = Lease(
                resource_id=resource_id,
                owner_id=owner_id,
                granted_ms=current_time_ms,
                expires_ms=current_time_ms + ttl_ms
            )
            return True
        
        return False
    
    def renew(self, resource_id: any, owner_id: int, ttl_ms: int, 
              current_time_ms: int) -> bool:
        """
        Extend lease TTL if owned by this agent.
        Returns True if renewed, False if not owned or expired.
        """
        lease = self.leases.get(resource_id)
        if lease and lease.owner_id == owner_id and lease.expires_ms > current_time_ms:
            lease.expires_ms = current_time_ms + ttl_ms
            return True
        return False
    
    def release(self, resource_id: any, owner_id: int) -> bool:
        """
        Release lease if owned by this agent.
        Returns True if released, False if not owned.
        """
        lease = self.leases.get(resource_id)
        if lease and lease.owner_id == owner_id:
            del self.leases[resource_id]
            return True
        return False
    
    def expire_leases(self, current_time_ms: int) -> List[Tuple[any, int]]:
        """
        Remove expired leases; return list of (resource_id, owner_id).
        Called periodically by coordinator tick().
        """
        expired = []
        for resource_id, lease in list(self.leases.items()):
            if lease.expires_ms <= current_time_ms:
                expired.append((resource_id, lease.owner_id))
                del self.leases[resource_id]
        return expired
    
    def get_owner(self, resource_id: any) -> Optional[int]:
        """
        Get current owner of a resource, or None if free.
        """
        lease = self.leases.get(resource_id)
        return lease.owner_id if lease else None

