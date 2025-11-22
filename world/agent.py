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
from pathfinder import astar_with_congestion
from dsm.local_cache import LocalDSMCache
from config import (
    TASK_WORK_DURATION_STEPS,
    MOVEMENT_DURATION_STEPS,
    LATERAL_MOVE_DURATION_STEPS,
    STUCK_TIMEOUT_STEPS,
    REPLAN_ATTEMPTS,
    LOCALITY_PREFERENCE_FACTOR,
    MAX_AOI_MS
)


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
        
        # Track recently failed tasks to avoid immediate reclaim
        self.failed_tasks_cooldown: Dict[int, int] = {}  
        self.failed_task_cooldown_duration = 300 
        
        # Peer-to-peer DSM: local cache (only in distributed mode)
        self.local_cache = LocalDSMCache(unique_id) if model.mode != 'centralized' else None
        
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
        if self.state == AgentState.WORKING:
            self.steps_working += 1
        self.utilization = self.steps_working / max(1, self.steps_total)
        
        # Write own location to local cache (for others to see via gossip)
        # Only in distributed mode
        if self.local_cache:
            current_time_ms = int(time.time() * 1000)
            self.local_cache.write_agent_location(self.unique_id, self.node, current_time_ms)
        
        # Decrement cooldowns for failed tasks
        expired_tasks = []
        for task_id, cooldown in list(self.failed_tasks_cooldown.items()):
            cooldown -= 1
            if cooldown <= 0:
                expired_tasks.append(task_id)
            else:
                self.failed_tasks_cooldown[task_id] = cooldown
        for task_id in expired_tasks:
            del self.failed_tasks_cooldown[task_id]
        
        if self.state == AgentState.IDLE:
            self._handle_idle()
        elif self.state == AgentState.NAVIGATING:
            self._handle_navigating()
        elif self.state == AgentState.WORKING:
            self._handle_working()
    
    def _validate_state(self):
        """Validate agent state consistency and log anomalies"""
        if self.state == AgentState.WORKING and self.current_task_id is None:
            self.model.logger.error(f"Agent {self.unique_id}: INCONSISTENT STATE - WORKING but current_task_id=None at step {self.steps_total}")
        
        if self.state == AgentState.NAVIGATING and self.current_task_id is None:
            self.model.logger.error(f"Agent {self.unique_id}: INCONSISTENT STATE - NAVIGATING but current_task_id=None at step {self.steps_total}")
        
        if self.current_task_id is not None and self.state == AgentState.IDLE:
            self.model.logger.error(f"Agent {self.unique_id}: INCONSISTENT STATE - IDLE but has task {self.current_task_id} at step {self.steps_total}")
    
    def _handle_idle(self):
        """Event-driven: Watch for tasks and react to notifications"""
        coordinator = self.model.coordinator
        
        # CENTRALIZED MODE: PUSH - scheduler assigns tasks (atomically claims)
        if self.model.mode == 'centralized':
            assignment = self.model.central_scheduler.assign_task_to_agent(self.unique_id, self.node)
            if assignment:
                task_id, task_location = assignment
                if task_id not in self.failed_tasks_cooldown:
                    self._on_task_assigned(task_id, task_location)
                else:
                    coordinator.release_claim(task_id, self.unique_id)
                    self.model.central_scheduler.notify_task_released(self.unique_id)
            return
        
        # DISTRIBUTED MODE: PULL - agent searches and claims (unchanged)
        if not hasattr(self, 'watch_handle') or self.watch_handle is None:
            self.watch_handle = coordinator.watch(
                region_id=self.node,
                event_type='task_created',
                callback=self._on_task_available
            )
            self._on_task_available({'trigger': 'initial_scan'})
            return
        
        unclaimed_tasks = []
        available_tasks = coordinator.get_available_tasks()
        for task in available_tasks:
            if task.task_id not in self.failed_tasks_cooldown:
                unclaimed_tasks.append((task.task_id, {'location': task.location, 'status': 'available'}))
        
        if not unclaimed_tasks:
            self._idle_wander_to_staging()
            return
        
        self._moving_to_staging = False
        if hasattr(self, '_target_staging_node'):
            delattr(self, '_target_staging_node')
        self.path = []
        self.movement_timer = 0
        
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
            
            if hasattr(self.model.dsm, 'node_to_shard') if hasattr(self.model, 'dsm') else False:
                agent_shard = self.model.dsm.node_to_shard(self.node)
                task_shard = self.model.dsm.node_to_shard(task_location)
                if agent_shard == task_shard:
                    original_distance = distance
                    distance = distance * LOCALITY_PREFERENCE_FACTOR
                    if self.steps_total == 200:
                        self.model.logger.debug(f"Agent {self.unique_id}: LOCAL task {task_id} - distance {original_distance:.1f} → {distance:.1f} (shard {agent_shard})")
            
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
            attempts = 0
            attempted_ids = set()
            while attempts < 3 and best_task:
                task_id, task_location = best_task
                attempted_ids.add(task_id)
                
                claim_success = coordinator.try_claim(task_id, self.unique_id, ttl_ms=300000)
                
                if claim_success:
                    self.model.logger.info(f"Agent {self.unique_id}: CLAIMED task {task_id} at location {task_location}")
                    self.current_task_id = task_id
                    self.task_location = task_location
                    self.metrics['tasks_claimed'] += 1
                    
                    if hasattr(self, 'watch_handle') and self.watch_handle:
                        coordinator.unwatch(self.watch_handle)
                        self.watch_handle = None
                    
                    if self.node == task_location:
                        node_resource_id = f"node_{task_location}"
                        lock_success = coordinator.acquire_lock(node_resource_id, self.unique_id, ttl_ms=30000)
                        if lock_success:
                            self.model.logger.info(f"Agent {self.unique_id}: IDLE->WORKING (claimed task {task_id} already at location {task_location})")
                            self.state = AgentState.WORKING
                            self.work_timer = self.work_duration
                            self.path = []
                            self.stuck_counter = 0
                        else:
                            self._fail_current_task()
                        return
                    
                    self.path = self._plan_path(self.node, task_location)
                    if self.path:
                        self.state = AgentState.NAVIGATING
                    else:
                        self._fail_current_task()
                    return
                
                attempts += 1
                remaining = [c for c in candidates if c[0] not in attempted_ids]
                if remaining:
                    remaining.sort(key=lambda x: (x[2], self.model.random.random()))
                    best_task = (remaining[0][0], remaining[0][1])
                else:
                    best_task = None
            self._idle_wander_to_staging()
        else:
            # No visible tasks after evaluation — gentle wander
            self._idle_wander_to_staging()
    
    def _on_task_assigned(self, task_id: int, task_location: int):
        """Handle centralized task assignment (PUSH model) - already claimed by scheduler"""
        self.model.logger.info(f"Agent {self.unique_id}: ASSIGNED task {task_id} at location {task_location}")
        self.current_task_id = task_id
        self.task_location = task_location
        self.metrics['tasks_claimed'] += 1
        
        if self.node == task_location:
            node_resource_id = f"node_{task_location}"
            coordinator = self.model.coordinator
            lock_success = coordinator.acquire_lock(node_resource_id, self.unique_id, ttl_ms=30000)
            if lock_success:
                self.model.logger.info(f"Agent {self.unique_id}: IDLE->WORKING (assigned task {task_id} already at location)")
                self.state = AgentState.WORKING
                self.work_timer = self.work_duration
                self.path = []
                self.stuck_counter = 0
            else:
                self._fail_current_task()
        else:
            self.path = self._plan_path(self.node, task_location)
            if self.path:
                self.state = AgentState.NAVIGATING
            else:
                self._fail_current_task()
    
    def _handle_navigating(self):
        """Move along path towards task location"""
        if self.current_task_id is None or self.task_location is None:
            self.state = AgentState.IDLE
            self.path = []
            return
        
        if self.node == self.task_location:
            coordinator = self.model.coordinator
            
            task = coordinator.task_registry.get_task(self.current_task_id)
            if not task or task.status.value != 'claimed' or task.agent_id != self.unique_id:
                self._fail_current_task()
                return
            
            node_resource_id = f"node_{self.task_location}"
            lock_success = coordinator.acquire_lock(node_resource_id, self.unique_id, ttl_ms=30000)
            
            if lock_success:
                self.model.logger.info(f"Agent {self.unique_id}: NAVIGATING->WORKING (arrived at task {self.current_task_id}, acquired node lock)")
                self.state = AgentState.WORKING
                self.work_timer = self.work_duration
                self.stuck_counter = 0
                self.path = []
            else:
                if not hasattr(self, 'resource_wait_timer'):
                    self.resource_wait_timer = 50
                self.resource_wait_timer -= 1
                if self.resource_wait_timer <= 0:
                    self.model.logger.warning(f"Agent {self.unique_id}: Resource lock timeout for node {self.task_location}")
                    self._fail_current_task()
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
            distance = self._calculate_distance(self.node, next_node)
            self.metrics['total_distance'] += distance
            self.total_distance += distance
            
            self._write_flow_trace(self.node, next_node)
            
            self.node = next_node
            self.path.pop(0)
            self.movement_timer = MOVEMENT_DURATION_STEPS
            self.stuck_counter = 0
        else:
            self.stuck_counter += 1
            
            if self.stuck_counter > STUCK_TIMEOUT_STEPS:
                self._write_jam_signal()
                self._fail_current_task()
                return
            
            if self._try_lateral_escape():
                self.stuck_counter = 0
                return
            
            if self.stuck_counter in REPLAN_ATTEMPTS:
                self.path = self._plan_path(self.node, self.task_location)
            
            if self.stuck_counter % 20 == 0:
                self._write_jam_signal()
    
    def _handle_working(self):
        """Execute work at task location"""
        if self.current_task_id is None:
            self.model.logger.warning(f"Agent {self.unique_id}: BUG CAUGHT - in WORKING state with no task! Recovering to IDLE at pos={self.node}")
            self.state = AgentState.IDLE
            self.task_location = None
            self.path = []
            self.work_timer = 0
            return
        
        if self.work_timer <= 0:
            coordinator = self.model.coordinator
            
            node_resource_id = f"node_{self.task_location}"
            coordinator.release_lock(node_resource_id, self.unique_id)
            
            coordinator.complete_task(self.current_task_id, self.unique_id)
            self.model.logger.info(f"Agent {self.unique_id}: COMPLETED task {self.current_task_id}, released node lock")
            
            # Notify central scheduler in centralized mode
            if self.model.mode == 'centralized' and self.model.central_scheduler:
                self.model.central_scheduler.notify_task_released(self.unique_id)
            
            self.metrics['tasks_completed'] += 1
            
            self.current_task_id = None
            self.task_location = None
            self.path = []
            self.stuck_counter = 0
            self.work_timer = 0
            
            if hasattr(self, 'resource_wait_timer'):
                delattr(self, 'resource_wait_timer')
            
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
        
        # Centralized: trust the scheduler, always allow
        if self.model.mode == 'centralized':
            capacity = self.model.warehouse.get_node_capacity(node)
            return capacity > 0
        
        # Distributed: check capacity using cached agent locations (may be stale!)
        agents_at_node = self.local_cache.read_agents_at_node(node, max_aoi_ms=MAX_AOI_MS)
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
        """
        Plan shortest path between two nodes.
        
        DISTRIBUTED (P2P): Smart agent plans own path using local stale data
        CENTRALIZED: Dumb worker asks central scheduler (BOTTLENECK)
        """
        if self.model.mode == 'centralized':
            return self._plan_path_centralized(from_node, to_node)
        else:
            return self._plan_path_distributed(from_node, to_node)
    
    def _plan_path_centralized(self, from_node: int, to_node: int) -> List[int]:
        """
        CENTRALIZED MODE: Request path from central scheduler.
        
        This is the BOTTLENECK - all agents serialize here.
        Agent becomes a dumb worker that just follows orders.
        """
        try:
            self.model.central_scheduler.update_agent_position(self.unique_id, from_node)
            
            path = self.model.central_scheduler.request_path(
                agent_id=self.unique_id,
                start=from_node,
                goal=to_node,
                current_step=self.model.step_count
            )
            
            return path if path else []
            
        except Exception as e:
            self.model.logger.error(f"Agent {self.unique_id}: Central scheduler EXCEPTION from {from_node} to {to_node}: {e}")
            return []
    
    def _plan_path_distributed(self, from_node: int, to_node: int) -> List[int]:
        """
        DISTRIBUTED MODE: Smart agent plans own path using local stale data.
        
        Uses local cache with gossip - eventual consistency.
        """
        try:
            path = astar_with_congestion(
                warehouse=self.model.warehouse,
                dsm_api=self.local_cache,
                start=from_node,
                goal=to_node,
                cost_params={'alpha': 2.0, 'beta': 0.5, 'max_aoi_ms': MAX_AOI_MS}
            )
            if not path:
                self.model.logger.error(f"Agent {self.unique_id}: A* returned empty path from {from_node} to {to_node}")
                return []
            
            current_time_ms = int(time.time() * 1000)
            estimated_time_per_step = int(self.model.step_duration_s * MOVEMENT_DURATION_STEPS * 1000)
            
            for i, node in enumerate(path):
                arrival_time = current_time_ms + (i * estimated_time_per_step)
                self.local_cache.write_path_intent(
                    agent_id=self.unique_id,
                    node_id=node,
                    arrival_time_ms=arrival_time,
                    duration_ms=estimated_time_per_step,
                    timestamp_ms=current_time_ms
                )
            
            return path
        except Exception as e:
            self.model.logger.error(f"Agent {self.unique_id}: Path planning EXCEPTION from {from_node} to {to_node}: {e}")
            return []
    
    def _fail_current_task(self):
        """Fail the current task and clean up"""
        if self.current_task_id is not None:
            self.model.logger.info(f"Agent {self.unique_id}: FAILED task {self.current_task_id} - releasing back to available")
            
            coordinator = self.model.coordinator
            
            # Notify central scheduler in centralized mode
            if self.model.mode == 'centralized' and self.model.central_scheduler:
                self.model.central_scheduler.notify_task_released(self.unique_id)
            
            coordinator.release_claim(self.current_task_id, self.unique_id)
            
            if self.task_location:
                node_resource_id = f"node_{self.task_location}"
                coordinator.release_lock(node_resource_id, self.unique_id)
            
            self.failed_tasks_cooldown[self.current_task_id] = self.failed_task_cooldown_duration
            
            self.metrics['tasks_failed'] += 1
            self.current_task_id = None
            self.task_location = None
            self.path = []
            
            if hasattr(self, 'resource_wait_timer'):
                delattr(self, 'resource_wait_timer')
        
        self.state = AgentState.IDLE
        self.stuck_counter = 0
    
    def _reserve_edge(self, from_node: int, to_node: int) -> bool:
        """Request edge reservation from the model for conflict-free move."""
        try:
            return self.model.try_reserve_edge(from_node, to_node, duration_steps=MOVEMENT_DURATION_STEPS)
        except Exception:
            return True
    
    def _write_flow_trace(self, from_node: int, to_node: int):
        """Write flow trace for congestion tracking"""
        if self.model.mode == 'centralized':
            # Report to central scheduler (perfect, instant)
            try:
                self.model.central_scheduler.report_flow(to_node, 1.0)
            except Exception:
                pass
        elif self.local_cache:
            # Distributed: write to local cache (eventual consistency via gossip)
            try:
                current_time_ms = int(time.time() * 1000)
                self.local_cache.write_flow(to_node, 1.0, current_time_ms)
            except Exception:
                pass
    
    def _write_jam_signal(self):
        """Write jam signal when stuck"""
        jam_value = min(5.0, self.stuck_counter / 50.0)
        
        if self.model.mode == 'centralized':
            # Report to central scheduler (perfect, instant)
            try:
                self.model.central_scheduler.report_jam(self.node, jam_value)
            except Exception:
                pass
        elif self.local_cache:
            # Distributed: write to local cache (eventual consistency via gossip)
            try:
                current_time_ms = int(time.time() * 1000)
                self.local_cache.write_jam(self.node, jam_value, current_time_ms)
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
            candidates = []
            for nb in neighbors:
                if self._can_move_to(nb):
                    x, y = self.model.warehouse.node_to_pos(nb)
                    h = abs(hx - x) + abs(hy - y)
                    if h <= base_h:
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
                self.movement_timer = LATERAL_MOVE_DURATION_STEPS
                return True
            return False
        except Exception:
            return False
    
    def _on_task_available(self, event_data):
        """Callback: React to task_created event from WatchManager"""
        if self.state != AgentState.IDLE:
            return
        
        self._handle_idle()
    
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
                    self.movement_timer = MOVEMENT_DURATION_STEPS
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