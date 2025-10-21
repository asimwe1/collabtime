"""
Task Registry for Distributed Shared Memory System

Manages task lifecycle, claim coordination, and deadlock prevention.
Provides centralized task state management with distributed access.
"""

import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum


class TaskStatus(Enum):
    AVAILABLE = "available"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class Task:
    """Represents a warehouse task"""
    task_id: int
    location: int
    task_type: str  # e.g., "pick", "place", "transport"
    priority: float
    created_ms: int
    deadline_ms: Optional[int] = None
    estimated_duration_ms: int = 30000  # 30 seconds default
    payload: Dict = None  # Additional task-specific data
    
    # Execution tracking
    status: TaskStatus = TaskStatus.AVAILABLE
    agent_id: Optional[int] = None
    claimed_ms: Optional[int] = None
    started_ms: Optional[int] = None
    completed_ms: Optional[int] = None
    
    # Retry tracking
    attempt_count: int = 0
    max_attempts: int = 3


class TaskRegistry:
    """Centralized registry for task management"""
    
    def __init__(self):
        self.tasks: Dict[int, Task] = {}
        self.task_counter = 0
        
        # Indexing for efficient queries
        self.tasks_by_location: Dict[int, Set[int]] = {}  # location -> task_ids
        self.tasks_by_agent: Dict[int, Set[int]] = {}     # agent_id -> task_ids
        self.tasks_by_status: Dict[TaskStatus, Set[int]] = {
            status: set() for status in TaskStatus
        }
        
        # Configuration
        self.default_task_timeout_ms = 60000  # 1 minute
        self.claim_timeout_ms = 5000  # 5 seconds to start after claiming
        
        # Metrics
        self.metrics = {
            'tasks_created': 0,
            'tasks_completed': 0,
            'tasks_failed': 0,
            'tasks_expired': 0,
            'claim_conflicts': 0,
            'avg_completion_time_ms': 0.0
        }
    
    def create_task(self, location: int, task_type: str = "pick", 
                   priority: float = 1.0, deadline_ms: Optional[int] = None,
                   payload: Dict = None) -> int:
        """Create a new task"""
        task_id = self.task_counter
        self.task_counter += 1
        
        current_time = int(time.time() * 1000)
        if deadline_ms is None:
            deadline_ms = current_time + self.default_task_timeout_ms
        
        task = Task(
            task_id=task_id,
            location=location,
            task_type=task_type,
            priority=priority,
            created_ms=current_time,
            deadline_ms=deadline_ms,
            payload=payload or {}
        )
        
        self.tasks[task_id] = task
        self._add_to_indices(task)
        
        self.metrics['tasks_created'] += 1
        return task_id
    
    def claim_task(self, task_id: int, agent_id: int) -> bool:
        """Attempt to claim a task for an agent"""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        current_time = int(time.time() * 1000)
        
        # Check if task is available and not expired
        if (task.status != TaskStatus.AVAILABLE or 
            (task.deadline_ms and current_time > task.deadline_ms)):
            
            if task.deadline_ms and current_time > task.deadline_ms:
                self._expire_task(task)
            
            return False
        
        # Claim the task
        old_status = task.status
        task.status = TaskStatus.CLAIMED
        task.agent_id = agent_id
        task.claimed_ms = current_time
        
        self._update_indices(task, old_status)
        
        # Add to agent's task list
        if agent_id not in self.tasks_by_agent:
            self.tasks_by_agent[agent_id] = set()
        self.tasks_by_agent[agent_id].add(task_id)
        
        return True
    
    def start_task(self, task_id: int, agent_id: int) -> bool:
        """Mark a claimed task as started"""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        
        if (task.status != TaskStatus.CLAIMED or 
            task.agent_id != agent_id):
            return False
        
        old_status = task.status
        task.status = TaskStatus.IN_PROGRESS
        task.started_ms = int(time.time() * 1000)
        
        self._update_indices(task, old_status)
        return True
    
    def complete_task(self, task_id: int, agent_id: int) -> bool:
        """Mark a task as completed"""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        
        if task.agent_id != agent_id:
            return False
        
        if task.status not in [TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]:
            return False
        
        old_status = task.status
        task.status = TaskStatus.COMPLETED
        task.completed_ms = int(time.time() * 1000)
        
        self._update_indices(task, old_status)
        
        # Update metrics
        self.metrics['tasks_completed'] += 1
        if task.started_ms:
            completion_time = task.completed_ms - task.started_ms
            self._update_avg_completion_time(completion_time)
        
        return True
    
    def fail_task(self, task_id: int, agent_id: int = None, retry: bool = True) -> bool:
        """Mark a task as failed, optionally retry"""
        if task_id not in self.tasks:
            return False
        
        task = self.tasks[task_id]
        
        if agent_id and task.agent_id != agent_id:
            return False
        
        task.attempt_count += 1
        
        if retry and task.attempt_count < task.max_attempts:
            # Reset for retry
            old_status = task.status
            task.status = TaskStatus.AVAILABLE
            task.agent_id = None
            task.claimed_ms = None
            task.started_ms = None
            
            self._update_indices(task, old_status)
            
            # Remove from agent's task list
            if task.agent_id and task.agent_id in self.tasks_by_agent:
                self.tasks_by_agent[task.agent_id].discard(task_id)
        
        else:
            # Mark as permanently failed
            old_status = task.status
            task.status = TaskStatus.FAILED
            
            self._update_indices(task, old_status)
            self.metrics['tasks_failed'] += 1
        
        return True
    
    def get_available_tasks(self, location: int = None, max_distance: int = None) -> List[Task]:
        """Get list of available tasks, optionally filtered by location"""
        available_task_ids = self.tasks_by_status[TaskStatus.AVAILABLE]
        available_tasks = [self.tasks[tid] for tid in available_task_ids]
        
        # Filter by location if specified
        if location is not None:
            if max_distance is None:
                available_tasks = [t for t in available_tasks if t.location == location]
            else:
                # TODO: Implement distance-based filtering with graph
                available_tasks = [t for t in available_tasks 
                                 if abs(t.location - location) <= max_distance]
        
        # Sort by priority (descending) and creation time (ascending)
        available_tasks.sort(key=lambda t: (-t.priority, t.created_ms))
        
        return available_tasks
    
    def get_agent_tasks(self, agent_id: int) -> List[Task]:
        """Get all tasks assigned to an agent"""
        task_ids = self.tasks_by_agent.get(agent_id, set())
        return [self.tasks[tid] for tid in task_ids if tid in self.tasks]
    
    def cleanup_expired_tasks(self):
        """Remove expired and old completed tasks"""
        current_time = int(time.time() * 1000)
        expired_tasks = []
        
        for task in self.tasks.values():
            # Check for expired tasks
            if (task.deadline_ms and current_time > task.deadline_ms and 
                task.status in [TaskStatus.AVAILABLE, TaskStatus.CLAIMED]):
                expired_tasks.append(task)
            
            # Check for claimed tasks that haven't started
            elif (task.status == TaskStatus.CLAIMED and task.claimed_ms and
                  current_time - task.claimed_ms > self.claim_timeout_ms):
                expired_tasks.append(task)
        
        # Expire tasks
        for task in expired_tasks:
            self._expire_task(task)
    
    def get_status_summary(self) -> Dict[str, int]:
        """Get count of tasks by status"""
        return {
            status.value: len(task_ids) 
            for status, task_ids in self.tasks_by_status.items()
        }
    
    def get_metrics(self) -> Dict:
        """Get registry metrics"""
        return self.metrics.copy()
    
    def _add_to_indices(self, task: Task):
        """Add task to all relevant indices"""
        # Location index
        if task.location not in self.tasks_by_location:
            self.tasks_by_location[task.location] = set()
        self.tasks_by_location[task.location].add(task.task_id)
        
        # Status index
        self.tasks_by_status[task.status].add(task.task_id)
    
    def _update_indices(self, task: Task, old_status: TaskStatus):
        """Update indices when task status changes"""
        # Remove from old status
        self.tasks_by_status[old_status].discard(task.task_id)
        
        # Add to new status
        self.tasks_by_status[task.status].add(task.task_id)
    
    def _expire_task(self, task: Task):
        """Mark a task as expired"""
        old_status = task.status
        task.status = TaskStatus.EXPIRED
        
        self._update_indices(task, old_status)
        self.metrics['tasks_expired'] += 1
        
        # Remove from agent's task list
        if task.agent_id and task.agent_id in self.tasks_by_agent:
            self.tasks_by_agent[task.agent_id].discard(task.task_id)
    
    def _update_avg_completion_time(self, completion_time_ms: int):
        """Update rolling average completion time"""
        current_avg = self.metrics['avg_completion_time_ms']
        completed_count = self.metrics['tasks_completed']
        
        # Simple moving average
        self.metrics['avg_completion_time_ms'] = (
            (current_avg * (completed_count - 1) + completion_time_ms) / completed_count
        )