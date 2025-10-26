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

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dsm.api import dsm
from pathfinder import astar_with_congestion
from config import TASK_WORK_DURATION_STEPS


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
        self.work_duration = TASK_WORK_DURATION_STEPS
        
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
        self.steps_total = 0
        self.steps_working = 0
    
    def step(self):
        """Execute one simulation step"""
        self.steps_total += 1
        if self.state != AgentState.IDLE:
            self.steps_working += 1
        
        if self.steps_total > 0:
            self.utilization = self.steps_working / self.steps_total
        
        if self.state == AgentState.IDLE:
            self._handle_idle()
        elif self.state == AgentState.NAVIGATING:
            self._handle_navigating()
        elif self.state == AgentState.WORKING:
            self._handle_working()
    
    def _handle_idle(self):
        """Look for nearby unclaimed tasks and claim one"""
        dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
        
        unclaimed_tasks = []
        for task_id, task_info in dsm_api.task_registry.tasks.items():
            if isinstance(task_info, dict) and task_info.get('status') == 'available':
                unclaimed_tasks.append((task_id, task_info))
        
        if not unclaimed_tasks:
            self._idle_wander_to_staging()
            return
        
        if hasattr(self, '_moving_to_staging'):
            self._moving_to_staging = False
        if hasattr(self, '_target_staging_node'):
            delattr(self, '_target_staging_node')
        
        occupancy = self.model.get_warehouse_occupancy()
        
        nearby_tasks = []
        distant_tasks = []
        
        self.model.random.shuffle(unclaimed_tasks)
        for task_id, task_info in unclaimed_tasks:
            task_location = task_info.get('location')
            if task_location is None:
                continue
            
            if occupancy.get(task_location, 0) > 0:
                continue
            
            distance = self._calculate_distance(self.node, task_location)
            
            if distance <= self.search_radius:
                nearby_tasks.append((task_id, task_location, distance))
            else:
                distant_tasks.append((task_id, task_location, distance))
        
        # Pick task: prioritize nearby, but accept distant if no nearby options
        best_task = None
        candidates = nearby_tasks if nearby_tasks else distant_tasks
        
        if candidates:
            # Sort by distance with random tiebreaker for equal distances
            candidates.sort(key=lambda x: (x[2], self.model.random.random()))
            best_task = (candidates[0][0], candidates[0][1])
        
        if best_task:
            # Try multiple candidates this tick to avoid idle stalls when claims race
            attempts = 0
            attempted_ids = set()
            while attempts < 3 and best_task:
                task_id, task_location = best_task
                attempted_ids.add(task_id)
                # Try to claim the task
                if dsm_api.claim(task_id, self.unique_id):
                    self.current_task_id = task_id
                    self.task_location = task_location
                    self.metrics['tasks_claimed'] += 1
                    
                    if self.node == task_location:
                        self.state = AgentState.WORKING
                        self.work_timer = self.work_duration
                        self.path = []
                        self.stuck_counter = 0
                    else:
                        self.path = self._plan_path(self.node, task_location)
                        if self.path:
                            self.state = AgentState.NAVIGATING
                        else:
                            self._fail_current_task()
                    return
                attempts += 1
                # try the next best remaining candidate with some randomization
                remaining = [c for c in candidates if c[0] not in attempted_ids]
                if remaining:
                    remaining.sort(key=lambda x: (x[2], self.model.random.random()))
                    best_task = (remaining[0][0], remaining[0][1])
                else:
                    best_task = None
            # If nothing could be claimed, wander
            self._idle_wander_to_staging()
        else:
            # No visible tasks after evaluation — gentle wander
            self._idle_wander_to_staging()
    
    def _handle_navigating(self):
        """Move along path towards task location"""
        if self.node == self.task_location:
            dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
            task = dsm_api.task_registry.tasks.get(self.current_task_id)
            
            if not task or not isinstance(task, dict):
                self._fail_current_task()
                return
            
            if task.get('status') != 'claimed' or task.get('agent_id') != self.unique_id:
                self._fail_current_task()
                return
            
            self.state = AgentState.WORKING
            self.work_timer = self.work_duration
            self.stuck_counter = 0
            self.path = []
            return
        
        # Check if path is exhausted (but we haven't arrived yet)
        if not self.path or len(self.path) < 2:
            # No valid next node in path and we're not at goal - try to replan once
            self.path = self._plan_path(self.node, self.task_location)
            if not self.path or len(self.path) < 2:
                # Still no path after replan - fail this task
                self._fail_current_task()
            return
        
        # If currently moving, decrement timer
        if self.movement_timer > 0:
            self.movement_timer -= 1
            return
        
        # Ready to move to next node
        next_node = self.path[1]  # path[0] is current node
        
        if self._can_move_to(next_node) and self._reserve_edge(self.node, next_node):
            # Start moving to next node (takes 5 steps per cell for realistic speed)
            distance = self._calculate_distance(self.node, next_node)
            self.metrics['total_distance'] += distance
            self.total_distance += distance
            
            # Write flow trace to DSM for congestion awareness
            self._write_flow_trace(self.node, next_node)
            
            self.node = next_node
            self.path.pop(0)
            self.movement_timer = 5  # Takes 5 simulation steps to move one cell
            self.stuck_counter = 0  # Reset stuck counter
        else:
            self.stuck_counter += 1
            
            if self.stuck_counter > 12:
                self._fail_current_task()
                return
            
            if self._try_lateral_escape():
                self.stuck_counter = 0
                return
            
            if self.stuck_counter in (2, 4, 8):
                self.path = self._plan_path(self.node, self.task_location)
    
    def _handle_working(self):
        """Execute work at task location"""
        if self.current_task_id is None or self.work_timer <= 0:
            if self.current_task_id is None:
                self.state = AgentState.IDLE
                self.task_location = None
                self.path = []
                return
            
            if self.work_timer <= 0:
                dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
                dsm_api.complete_task(self.current_task_id)
                self.metrics['tasks_completed'] += 1
                
                self.current_task_id = None
                self.task_location = None
                self.path = []
                self.stuck_counter = 0
                
                occupancy = self.model.get_warehouse_occupancy()
                if occupancy.get(self.node, 0) > 1:
                    delattr(self, '_target_staging_node') if hasattr(self, '_target_staging_node') else None
                    self._moving_to_staging = False
                
                self.state = AgentState.IDLE
                return
        
        self.work_timer -= 1
    
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
        """Plan shortest path between two nodes using DSM congestion awareness"""
        try:
            dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
            path = astar_with_congestion(
                warehouse=self.model.warehouse,
                dsm_api=dsm_api,
                start=from_node,
                goal=to_node,
                cost_params={'alpha': 2.0, 'beta': 0.5, 'max_aoi_ms': 5000}
            )
            return path if path else []
        except:
            return []
    
    def _fail_current_task(self):
        """Fail the current task and clean up"""
        if self.current_task_id:
            dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
            task = dsm_api.task_registry.tasks.get(self.current_task_id)
            if task and isinstance(task, dict) and task.get('status') == 'claimed' and task.get('agent_id') == self.unique_id:
                task['status'] = 'available'
                task['agent_id'] = None
            
            self.metrics['tasks_failed'] += 1
            self.current_task_id = None
            self.task_location = None
            self.path = []
        
        self.stuck_counter = 0
        self.state = AgentState.IDLE
    
    def _reserve_edge(self, from_node: int, to_node: int) -> bool:
        """Request edge reservation from the model for conflict-free move."""
        try:
            return self.model.try_reserve_edge(from_node, to_node, duration_steps=5)
        except Exception:
            return True
    
    def _write_flow_trace(self, from_node: int, to_node: int):
        """Write flow trace to DSM to signal agent movement for congestion tracking."""
        try:
            import time
            dsm_api = self.model.dsm if hasattr(self.model, 'dsm') and self.model.dsm is not None else dsm
            current_time_ms = int(time.time() * 1000)
            # Write increment to flow_trace at destination node
            dsm_api.write_delta('flow_trace', {to_node: 1.0}, current_time_ms)
        except Exception:
            pass
    
    def _try_lateral_escape(self) -> bool:
        """Try a one-step lateral move to de-queue if blocked.
        Chooses an adjacent aisle neighbor that reduces or maintains heuristic distance.
        Returns True if moved, False otherwise.
        """
        try:
            if self.movement_timer > 0:
                return False
            neighbors = self.model.warehouse.get_neighbors(self.node)
            # Heuristic distance to target
            if self.task_location is None:
                return False
            hx, hy = self.model.warehouse.node_to_pos(self.task_location)
            nx, ny = self.model.warehouse.node_to_pos(self.node)
            base_h = abs(hx - nx) + abs(hy - ny)
            # Prefer moves that keep or slightly improve heuristic
            candidates = []
            for nb in neighbors:
                if self._can_move_to(nb):
                    x, y = self.model.warehouse.node_to_pos(nb)
                    h = abs(hx - x) + abs(hy - y)
                    if h <= base_h + 1:
                        candidates.append((h, nb))
            if not candidates:
                return False
            candidates.sort(key=lambda t: (t[0], self.model.random.random()))
            next_nb = candidates[0][1]
            # Reserve and move
            if self._reserve_edge(self.node, next_nb):
                distance = self._calculate_distance(self.node, next_nb)
                self.metrics['total_distance'] += distance
                self.total_distance += distance
                self._write_flow_trace(self.node, next_nb)
                self.node = next_nb
                self.movement_timer = 2
                return True
            return False
        except Exception:
            return False
    
    def _idle_wander_to_staging(self):
        """Non-blocking idle behavior: drift to a random perimeter staging node."""
        if not hasattr(self, '_target_staging_node'):
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