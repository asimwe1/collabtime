"""
Robot Agent for Mesa-based Warehouse Simulation

Simplified hardcoded agent behavior:
- IDLE: Look for nearby unclaimed tasks
- NAVIGATING: Move towards task location
- WORKING: Execute task at location
"""

import mesa
import time
import random
from typing import Optional, Dict, Any, List
from enum import Enum

# Import our DSM components
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dsm.api import dsm


class AgentState(Enum):
    IDLE = "idle"
    NAVIGATING = "navigating"
    WORKING = "working"


class RobotAgent(mesa.Agent):
    """A robotic agent operating in the warehouse"""
    
    def __init__(self, unique_id: int, model, initial_node: int):
        # Manually set mesa.Agent attributes instead of calling super().__init__()
        self.unique_id = unique_id
        self.model = model
        
        # Physical state
        self.node = initial_node
        self.path = []
        self.movement_timer = 0  # Steps remaining to finish current move
        self.stuck_counter = 0  # Count how long we've been blocked
        
        # Task state
        self.current_task_id = None
        self.task_location = None
        self.work_timer = 0
        self.work_duration = 20  # Steps to complete task (simulates handling time)
        
        # Agent state machine
        self.state = AgentState.IDLE
        
        # Search parameters
        self.search_radius = 15  # How far to look for tasks (Manhattan distance)
        
        # Performance tracking
        self.metrics = {
            'tasks_completed': 0,
            'total_distance': 0.0,
            'tasks_claimed': 0,
            'tasks_failed': 0
        }
        # Add top-level attrs for backward compat with experiment runner
        self.total_distance = 0.0
        self.utilization = 0.0
    
    def step(self):
        """Execute one simulation step"""
        if self.state == AgentState.IDLE:
            self._handle_idle()
        elif self.state == AgentState.NAVIGATING:
            self._handle_navigating()
        elif self.state == AgentState.WORKING:
            self._handle_working()
    
    def _handle_idle(self):
        """Look for nearby unclaimed tasks and claim one"""
        dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
        
        # Get all unclaimed tasks from DSM
        unclaimed_tasks = []
        for task_id, task_info in dsm_api.task_registry.tasks.items():
            if isinstance(task_info, dict) and task_info.get('status') == 'available':
                unclaimed_tasks.append((task_id, task_info))
        
        if not unclaimed_tasks:
            # No tasks - move to random staging area (perimeter) to stay out of the way
            if not hasattr(self, '_target_staging_node'):
                # Pick a random staging node as target
                staging_nodes = [n for n in range(self.model.warehouse.width * self.model.warehouse.height)
                               if self.model.warehouse.node_types.get(n) == 'staging']
                if staging_nodes:
                    self._target_staging_node = self.model.random.choice(staging_nodes)
                else:
                    return
            
            staging_node = self._target_staging_node
            
            if self.node != staging_node:
                if not hasattr(self, '_moving_to_staging'):
                    self._moving_to_staging = True
                    self.path = self._plan_path(self.node, staging_node)
                
                # Move along path with timer
                if self.movement_timer > 0:
                    self.movement_timer -= 1
                elif self.path and len(self.path) > 1:
                    next_node = self.path[1]
                    if self._can_move_to(next_node):
                        distance = self._calculate_distance(self.node, next_node)
                        self.metrics['total_distance'] += distance
                        self.total_distance += distance
                        self.node = next_node
                        self.path.pop(0)
                        self.movement_timer = 5
                else:
                    self._moving_to_staging = False
            return
        
        # Reset staging flags when tasks are available
        if hasattr(self, '_moving_to_staging'):
            self._moving_to_staging = False
        if hasattr(self, '_target_staging_node'):
            delattr(self, '_target_staging_node')
        
        # Find tasks within search radius, pick one with some randomness
        nearby_tasks = []
        
        for task_id, task_info in unclaimed_tasks:
            task_location = task_info.get('location')
            if task_location is None:
                continue
            
            # Calculate distance
            distance = self._calculate_distance(self.node, task_location)
            
            if distance <= self.search_radius:
                nearby_tasks.append((task_id, task_location, distance))
        
        # Pick task: 70% closest, 30% random nearby (reduces clustering)
        best_task = None
        if nearby_tasks:
            if self.model.random.random() < 0.7:
                # Pick closest
                nearby_tasks.sort(key=lambda x: x[2])
                best_task = (nearby_tasks[0][0], nearby_tasks[0][1])
            else:
                # Pick random
                chosen = self.model.random.choice(nearby_tasks)
                best_task = (chosen[0], chosen[1])
        
        if best_task:
            task_id, task_location = best_task
            # Try to claim the task
            if dsm_api.claim(task_id, self.unique_id):
                self.current_task_id = task_id
                self.task_location = task_location
                self.metrics['tasks_claimed'] += 1
                
                # Plan path to task
                self.path = self._plan_path(self.node, task_location)
                
                if self.path:
                    self.state = AgentState.NAVIGATING
                else:
                    # Can't reach task, release it
                    self._fail_current_task()
    
    def _handle_navigating(self):
        """Move along path towards task location"""
        # Check if arrived at destination
        if self.node == self.task_location:
            self.state = AgentState.WORKING
            self.work_timer = self.work_duration
            return
        
        # Check if path is exhausted
        if not self.path or len(self.path) < 2:
            # No valid next node in path
            self._fail_current_task()
            return
        
        # If currently moving, decrement timer
        if self.movement_timer > 0:
            self.movement_timer -= 1
            return
        
        # Ready to move to next node
        next_node = self.path[1]  # path[0] is current node
        
        if self._can_move_to(next_node):
            # Start moving to next node (takes 5 steps per cell for realistic speed)
            distance = self._calculate_distance(self.node, next_node)
            self.metrics['total_distance'] += distance
            self.total_distance += distance
            self.node = next_node
            self.path.pop(0)
            self.movement_timer = 5  # Takes 5 simulation steps to move one cell
            self.stuck_counter = 0  # Reset stuck counter
        else:
            # Path blocked - WAIT, but give up quickly if truly stuck
            self.stuck_counter += 1
            if self.stuck_counter > 15:
                # Give up on this task to break deadlock
                # Agent will return to staging and try a different task
                self._fail_current_task()
                self.stuck_counter = 0
    
    def _handle_working(self):
        """Execute work at task location"""
        self.work_timer -= 1
        
        if self.work_timer <= 0:
            # Task complete
            dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
            dsm_api.complete_task(self.current_task_id)
            
            # Update metrics
            self.metrics['tasks_completed'] += 1
            
            # Clear task state
            self.current_task_id = None
            self.task_location = None
            self.path = []
            
            # Return to idle
            self.state = AgentState.IDLE
    
    def _can_move_to(self, node: int) -> bool:
        """Check if agent can move to a node"""
        if not self.model.warehouse.is_adjacent(self.node, node):
            return False
        
        # Check capacity
        agents_at_node = sum(1 for agent in self.model.schedule.agents 
                           if hasattr(agent, 'node') and agent.node == node)
        
        capacity = self.model.warehouse.get_node_capacity(node)
        return agents_at_node < capacity
    
    def _calculate_distance(self, from_node: int, to_node: int) -> float:
        """Calculate distance between two nodes"""
        try:
            path_length = self.model.warehouse.get_path_length(from_node, to_node)
            return path_length if path_length > 0 else float('inf')
        except:
            return float('inf')
    
    def _plan_path(self, from_node: int, to_node: int) -> List[int]:
        """Plan shortest path between two nodes"""
        try:
            path = self.model.warehouse.get_shortest_path(from_node, to_node)
            return path if path else []
        except:
            return []
    
    def _fail_current_task(self):
        """Fail the current task and clean up"""
        if self.current_task_id:
            self.metrics['tasks_failed'] += 1
            self.current_task_id = None
            self.task_location = None
            self.path = []
        
        self.state = AgentState.IDLE
    
    def get_state_info(self) -> Dict[str, Any]:
        """Get current state information for debugging/monitoring"""
        return {
            'agent_id': self.unique_id,
            'state': self.state.value,
            'node': self.node,
            'current_task': self.current_task_id,
            'path_length': len(self.path),
            'metrics': self.metrics.copy()
        }