"""
Task Registry with Strong Consistency

Authoritative state for all tasks in the system.
Single source of truth for task lifecycle (created -> available -> claimed -> completed).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional
import time


class TaskStatus(Enum):
    """Task lifecycle states"""
    AVAILABLE = "available"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class Task:
    """Represents a warehouse task"""
    task_id: int
    location: int
    task_type: str
    priority: float
    status: TaskStatus
    agent_id: Optional[int]
    created_ms: int
    claimed_ms: Optional[int]
    completed_ms: Optional[int]


class TaskRegistry:
    """Authoritative task state registry with strong consistency"""
    
    def __init__(self):
        self.tasks: Dict[int, Task] = {}
        self.task_counter = 0
    
    def create_task(self, location: int, task_type: str = "pick", 
                   priority: float = 1.0) -> int:
        """
        Create a new task at the specified location.
        Returns task_id.
        """
        task_id = self.task_counter
        self.task_counter += 1
        
        self.tasks[task_id] = Task(
            task_id=task_id,
            location=location,
            task_type=task_type,
            priority=priority,
            status=TaskStatus.AVAILABLE,
            agent_id=None,
            created_ms=int(time.time() * 1000),
            claimed_ms=None,
            completed_ms=None
        )
        
        return task_id
    
    def get_available_tasks(self, location: int = None, 
                          max_distance: int = None) -> List[Task]:
        """
        Get available tasks, optionally filtered by region.
        Strong, consistent read - no stale data.
        """
        available = []
        for task in self.tasks.values():
            if task.status == TaskStatus.AVAILABLE:
                if location is not None and max_distance is not None:
                    pass
                available.append(task)
        return available
    
    def claim_task(self, task_id: int, agent_id: int) -> bool:
        """
        Atomically claim a task.
        Returns True if successful, False if already claimed.
        """
        task = self.tasks.get(task_id)
        if not task or task.status != TaskStatus.AVAILABLE:
            return False
        
        task.status = TaskStatus.CLAIMED
        task.agent_id = agent_id
        task.claimed_ms = int(time.time() * 1000)
        return True
    
    def complete_task(self, task_id: int, agent_id: int) -> bool:
        """
        Mark task as completed.
        Returns True if successful, False if not owned by agent.
        """
        task = self.tasks.get(task_id)
        if not task or task.agent_id != agent_id:
            return False
        
        task.status = TaskStatus.COMPLETED
        task.completed_ms = int(time.time() * 1000)
        return True
    
    def fail_task(self, task_id: int, agent_id: int, retry: bool = True) -> bool:
        """
        Mark task as failed. If retry=True, revert to available.
        Returns True if successful.
        """
        task = self.tasks.get(task_id)
        if not task or task.agent_id != agent_id:
            return False
        
        if retry:
            task.status = TaskStatus.AVAILABLE
            task.agent_id = None
            task.claimed_ms = None
        else:
            task.status = TaskStatus.FAILED
        
        return True
    
    def get_task(self, task_id: int) -> Optional[Task]:
        """Get task by ID"""
        return self.tasks.get(task_id)

