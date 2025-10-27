"""
Mesa Model for Warehouse DSM Simulation

Main simulation model that coordinates agents, warehouse graph, and DSM system.
Handles task generation, data collection, and simulation orchestration.
"""

import mesa
from mesa import Model
from mesa.datacollection import DataCollector
import importlib
try:
    RandomActivation = importlib.import_module("mesa.time").RandomActivation
except Exception:
    class RandomActivation:  # minimal fallback scheduler that actually randomizes
        def __init__(self, model):
            self.model = model
            self.agents = []
        def add(self, agent):
            self.agents.append(agent)
        def step(self):
            # Randomly shuffle agents each step for fair task claiming
            shuffled_agents = list(self.agents)
            random.shuffle(shuffled_agents)
            for agent in shuffled_agents:
                if hasattr(agent, "step"):
                    agent.step()
import random
import time
from typing import Dict, List, Any
import math

from .graph import WarehouseGraph, create_standard_warehouse
from .agent import RobotAgent

# Import DSM components
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dsm.api import dsm
from config import LOG_INTERVAL_STEPS, TASK_SPAWN_LOG_INTERVAL_STEPS


class WarehouseDSMModel(Model):
    """Mesa model for warehouse DSM simulation"""
    
    def __init__(self,
                 n_agents: int = None,
                 warehouse_graph: WarehouseGraph = None,
                 agent_positions: List[tuple] = None,
                 dsm=None,
                 task_arrival_rate: float = 0.1,
                 task_types: List[str] = None,
                 task_priorities: List[int] = None,
                 warehouse_width: int = 20,
                 warehouse_height: int = 10,
                 seed: int = None,
                 step_duration_s: float = 0.05,
                 logger=None):
        
        super().__init__(seed=seed)
        
        import logging
        logging.getLogger("MESA").setLevel(logging.WARNING)
        
        self.logger = logger if logger else logging.getLogger(__name__)
        
        if seed is not None:
            self.random.seed(seed)
            random.seed(seed)
        
        # Simulation parameters
        self.num_agents = n_agents if n_agents is not None else 16
        # Interpret task_arrival_rate as tasks per second
        self.task_arrival_rate = task_arrival_rate  # tasks/sec
        self.step_duration_s = step_duration_s
        # Event-driven Poisson arrivals: track time to next arrival (seconds)
        if self.task_arrival_rate > 0:
            self._time_to_next_arrival_s = random.expovariate(self.task_arrival_rate)
        else:
            self._time_to_next_arrival_s = float('inf')
        self.step_count = 0
        self.start_time = time.time()
        # Edge reservations for conflict-free movements (key: unordered edge, value: expire_step)
        self.edge_reservations = {}
        
        # Create or accept warehouse graph
        self.warehouse = warehouse_graph or create_standard_warehouse(warehouse_width, warehouse_height)

        # Attach DSM instance if provided
        self.dsm = dsm if dsm is not None else None
        
        # Agent scheduler
        self.schedule = RandomActivation(self)
        
        # Create agents
        self._create_agents(agent_positions)
        
        # Task management
        self.active_tasks = {}  # task_id -> task_info
        self.task_counter = 0
        
        # Metrics collection
        self.datacollector = DataCollector(
            model_reporters={
                "Total_Tasks_Created": lambda m: m.get_total_tasks_created(),
                "Total_Tasks_Completed": lambda m: m.get_total_tasks_completed(),
                "Active_Tasks": lambda m: len(m.active_tasks),
                "Average_Task_Completion_Time": lambda m: m.get_avg_completion_time(),
                "Agent_Utilization": lambda m: m.get_agent_utilization(),
                "DSM_Messages_Per_Second": lambda m: m.get_dsm_message_rate(),
                "Average_Agent_Distance": lambda m: m.get_avg_agent_distance(),
                "Jam_Incidents": lambda m: m.get_total_jam_incidents()
            },
            agent_reporters={
                "State": lambda a: a.state.value if hasattr(a, 'state') else "unknown",
                "Node": lambda a: a.node if hasattr(a, 'node') else -1,
                "Current_Task": lambda a: a.current_task_id if hasattr(a, 'current_task_id') else None,
                "Tasks_Completed": lambda a: (a.metrics['tasks_completed'] if hasattr(a, 'metrics') and 'tasks_completed' in a.metrics else 0),
                "Distance_Traveled": lambda a: (a.metrics['total_distance'] if hasattr(a, 'metrics') and 'total_distance' in a.metrics else 0)
            }
        )
        
        self.running = True
        # For runner compatibility
        self.completed_tasks = []
        self.failed_tasks = []
        # Latency samples for completed tasks (seconds)
        self.completed_latencies = []
        self.datacollector.collect(self)
    
    def _create_agents(self, agent_positions: List[tuple] = None):
        """Create and place agents in the warehouse"""
        # Get available starting positions (staging area - leftmost column)
        staging_nodes = [n for n in range(self.warehouse.width * self.warehouse.height)
                        if self.warehouse.node_types.get(n) == 'staging']
        
        if not staging_nodes:
            # Fallback to aisle nodes
            staging_nodes = [n for n in range(self.warehouse.width * self.warehouse.height)
                           if self.warehouse.node_types.get(n) == 'aisle'][:self.num_agents]
        
        agent_id = 0
        
        # If explicit positions provided, place agents there
        if agent_positions:
            for pos in agent_positions[:self.num_agents]:
                # pos may be a node id (int) or a coordinate (x,y)
                if isinstance(pos, (list, tuple)) and len(pos) == 2:
                    node_id = self.warehouse.node_id_from_pos((pos[0], pos[1])) if hasattr(self.warehouse, 'node_id_from_pos') else pos
                else:
                    node_id = int(pos)
                agent = RobotAgent(agent_id, self, node_id)
                self.schedule.add(agent)
                agent_id += 1
            return

        # Place all agents randomly in staging area (full perimeter)
        # Try to spread them out by avoiding occupied nodes when possible
        used_nodes = []
        for i in range(self.num_agents):
            if staging_nodes:
                # Prefer unused nodes, but allow reuse if we run out
                available = [n for n in staging_nodes if n not in used_nodes]
                if not available:
                    available = staging_nodes
                
                start_node = self.random.choice(available)
                used_nodes.append(start_node)
                agent = RobotAgent(agent_id, self, start_node)
                self.schedule.add(agent)
                agent_id += 1
    
    def step(self):
        """Execute one model step"""
        self.step_count += 1
        # Cleanup expired edge reservations
        expired_keys = [k for k, exp in self.edge_reservations.items() if exp <= self.step_count]
        for k in expired_keys:
            self.edge_reservations.pop(k, None)
        
        # Generate new tasks
        self._generate_tasks()
        
        # Step all agents
        self.schedule.step()
        
        # Clean up completed tasks
        self._cleanup_tasks()
        
        # Collect data
        self.datacollector.collect(self)
        
        if self.step_count % LOG_INTERVAL_STEPS == 0:
            active_count = len(self.active_tasks)
            completed_count = len(self.completed_tasks)
            agent_states = {}
            for agent in self.schedule.agents:
                state = agent.state.value if hasattr(agent, 'state') else 'unknown'
                agent_states[state] = agent_states.get(state, 0) + 1
            
            state_str = ", ".join([f"{k}={v}" for k, v in sorted(agent_states.items())])
            self.logger.info(f"Step {self.step_count}: Tasks: {active_count} active, {completed_count} completed | Agents: {state_str}")
            
            for agent in self.schedule.agents:
                x, y = self.warehouse.node_to_pos(agent.node)
                state = agent.state.value if hasattr(agent, 'state') else 'unknown'
                task_str = f"task={agent.current_task_id}" if agent.current_task_id is not None else "no_task"
                
                target_str = ""
                if agent.task_location is not None:
                    tx, ty = self.warehouse.node_to_pos(agent.task_location)
                    target_str = f" → ({tx},{ty})"
                
                path_len = len(agent.path) if agent.path else 0
                stuck = getattr(agent, 'stuck_counter', 0)
                
                self.logger.info(f"  Agent {agent.unique_id}: pos=({x},{y}) state={state} {task_str}{target_str} path={path_len} stuck={stuck}")
        
        if self.step_count >= 10000:
            self.running = False

    # --- Movement coordination ---
    def try_reserve_edge(self, from_node: int, to_node: int, duration_steps: int) -> bool:
        """Reserve an edge for duration_steps to prevent head-on collisions.
        Returns True if reservation granted, False otherwise.
        """
        key = tuple(sorted((from_node, to_node)))
        expires = self.edge_reservations.get(key)
        if expires is None or expires <= self.step_count:
            reserve_duration = max(1, min(duration_steps // 10, duration_steps))
            self.edge_reservations[key] = self.step_count + reserve_duration
            return True
        return False
    
    def _generate_tasks(self):
        """Generate new tasks using event-driven Poisson arrivals (exact in continuous time)."""
        lam = max(self.task_arrival_rate, 0.0)
        dt = max(self.step_duration_s, 0.0)
        if lam <= 0 or dt <= 0:
            return
        
        timer_before = self._time_to_next_arrival_s
        self._time_to_next_arrival_s -= dt
        
        spawns_this_step = 0
        max_spawns = 1000
        while self._time_to_next_arrival_s <= 0 and spawns_this_step < max_spawns:
            task_created = self._create_random_task()
            spawns_this_step += 1
            next_exp = random.expovariate(lam)
            self._time_to_next_arrival_s += next_exp
            
            if task_created:
                self.logger.info(f"Step {self.step_count}: TASK SPAWNED #{self.task_counter}! timer_before={timer_before:.2f}s, next_exp={next_exp:.2f}s, final_timer={self._time_to_next_arrival_s:.2f}s")
            else:
                self.logger.warning(f"Step {self.step_count}: TASK SPAWN ATTEMPT FAILED (no available nodes), next_exp={next_exp:.2f}s, final_timer={self._time_to_next_arrival_s:.2f}s")
        
        if self.step_count % (TASK_SPAWN_LOG_INTERVAL_STEPS * 10) == 0:
            self.logger.info(f"Step {self.step_count}: Poisson state - time_to_next: {self._time_to_next_arrival_s:.2f}s, lam={lam}, dt={dt}")
    
    def _create_random_task(self) -> bool:
        """Create a random task at a random location (on any aisle). Returns True if task was created."""
        pick_pack_nodes = [n for n in range(self.warehouse.width * self.warehouse.height)
                           if self.warehouse.node_types.get(n) in ('pick_location', 'pack_station')]
        candidate_nodes = pick_pack_nodes
        if not candidate_nodes:
            candidate_nodes = [n for n in range(self.warehouse.width * self.warehouse.height)
                               if self.warehouse.node_types.get(n) == 'aisle']

        occupied_nodes = set(self.get_warehouse_occupancy().keys())
        
        dsm_api = self.dsm if self.dsm is not None else dsm
        task_locations = set()
        for task_id, task_info in dsm_api.task_registry.tasks.items():
            if isinstance(task_info, dict) and task_info.get('status') in ['available', 'claimed']:
                loc = task_info.get('location')
                if loc is not None:
                    task_locations.add(loc)
        
        occupied_or_tasked = occupied_nodes | task_locations
        available_nodes = [n for n in candidate_nodes if n not in occupied_or_tasked]

        if not available_nodes:
            if self.step_count % (TASK_SPAWN_LOG_INTERVAL_STEPS * 10) == 0:
                self.logger.warning(f"Step {self.step_count}: TASK SPAWN BLOCKED - no available nodes! "
                                   f"Candidates: {len(candidate_nodes)}, Occupied: {len(occupied_nodes)}, "
                                   f"Active tasks: {len(task_locations)}, Pick/pack nodes: {len(pick_pack_nodes)}")
            return False

        if available_nodes:
            location = self.random.choice(available_nodes)
            
            if self.step_count % TASK_SPAWN_LOG_INTERVAL_STEPS == 0:
                self.logger.info(f"Step {self.step_count}: {len(available_nodes)}/{len(candidate_nodes)} locations free (excl agents & tasks), spawned at node {location}")
            
            task_id = dsm_api.create_task(location)
            
            sim_time = self.step_count * self.step_duration_s
            self.active_tasks[task_id] = {
                'location': location,
                'created_step': self.step_count,
                'created_time': sim_time,
                'start_time': sim_time
            }
            
            self.task_counter += 1
            return True
        
        return False
    
    def _cleanup_tasks(self):
        """Remove completed tasks from tracking"""
        completed_tasks = []
        
        for task_id, task_info in self.active_tasks.items():
            # Check if task is completed in DSM
            dsm_api = self.dsm if self.dsm is not None else dsm
            task = dsm_api.task_registry.tasks.get(task_id)
            if task and isinstance(task, dict):
                # Task is a dict with 'status' key
                if task.get('status') in ['completed', 'failed', 'expired']:
                    completed_tasks.append(task_id)
                    if task.get('status') == 'completed':
                        completion_time = self.step_count * self.step_duration_s
                        task_record = {
                            'task_id': task_id,
                            'start_time': task_info.get('start_time'),
                            'completion_time': completion_time
                        }
                        self.completed_tasks.append(task_record)
                        # Record latency if we have creation time
                        created_time = task_info.get('created_time')
                        if created_time:
                            self.completed_latencies.append(completion_time - created_time)
                    else:
                        self.failed_tasks.append({'task_id': task_id})
        
        for task_id in completed_tasks:
            del self.active_tasks[task_id]
    
    # Metrics and reporting methods
    def get_total_tasks_created(self) -> int:
        """Get total number of tasks created"""
        return self.task_counter
    
    def get_total_tasks_completed(self) -> int:
        """Get total number of tasks completed by all agents"""
        return sum(agent.metrics['tasks_completed'] 
                  for agent in self.schedule.agents 
                  if hasattr(agent, 'metrics'))
    
    def get_avg_completion_time(self) -> float:
        """Get average task completion time"""
        # This would need more sophisticated tracking in a full implementation
        return 0.0
    
    def get_agent_utilization(self) -> float:
        """Get percentage of agents currently working on tasks"""
        if not self.schedule.agents:
            return 0.0
        
        working_agents = sum(1 for agent in self.schedule.agents 
                           if hasattr(agent, 'current_task_id') and agent.current_task_id is not None)
        
        return working_agents / len(self.schedule.agents)
    
    def get_dsm_message_rate(self) -> float:
        """Get DSM messages per second"""
        # This would need DSM instrumentation
        return 0.0
    
    def get_avg_agent_distance(self) -> float:
        """Get average distance traveled per agent"""
        if not self.schedule.agents:
            return 0.0
        
        total_distance = sum(agent.metrics['total_distance'] 
                           for agent in self.schedule.agents 
                           if hasattr(agent, 'metrics'))
        
        return total_distance / len(self.schedule.agents)
    
    def get_total_jam_incidents(self) -> int:
        """Get total number of jam incidents detected"""
        return sum(agent.metrics.get('jam_detections', 0) 
                  for agent in self.schedule.agents 
                  if hasattr(agent, 'metrics'))
    
    def get_agent_states_summary(self) -> Dict[str, int]:
        """Get count of agents in each state"""
        state_counts = {}
        
        for agent in self.schedule.agents:
            if hasattr(agent, 'state'):
                state = agent.state.value
                state_counts[state] = state_counts.get(state, 0) + 1
        
        return state_counts
    
    def get_warehouse_occupancy(self) -> Dict[int, int]:
        """Get number of agents at each warehouse node"""
        occupancy = {}
        
        for agent in self.schedule.agents:
            if hasattr(agent, 'node'):
                node = agent.node
                occupancy[node] = occupancy.get(node, 0) + 1
        
        return occupancy
    
    def visualize_current_state(self):
        """Visualize current simulation state"""
        # Get agent positions
        agent_positions = {}
        for agent in self.schedule.agents:
            if hasattr(agent, 'node'):
                agent_positions[agent.unique_id] = agent.node
        
        # Get task locations
        task_locations = [task_info['location'] for task_info in self.active_tasks.values()]
        
        # Visualize
        self.warehouse.visualize(agent_positions, task_locations)
    
    def export_results(self, filename: str = None):
        """Export simulation results to CSV"""
        if filename is None:
            filename = f"warehouse_dsm_results_{int(time.time())}.csv"
        
        # Get model data
        model_data = self.datacollector.get_model_vars_dataframe()
        model_data.to_csv(f"model_{filename}")
        
        # Get agent data
        agent_data = self.datacollector.get_agent_vars_dataframe()
        agent_data.to_csv(f"agent_{filename}")
        
        print(f"Results exported to model_{filename} and agent_{filename}")
    
    def run_simulation(self, steps: int = 1000, verbose: bool = False):
        """Run simulation for specified number of steps"""
        print(f"Starting warehouse DSM simulation with {self.num_agents} agents...")
        print(f"Warehouse size: {self.warehouse.width}x{self.warehouse.height}")
        print(f"Task arrival rate: {self.task_arrival_rate}")
        
        for step in range(steps):
            if not self.running:
                break
            
            self.step()
            
            if verbose and step % 100 == 0:
                print(f"Step {step}: {len(self.active_tasks)} active tasks, "
                      f"{self.get_total_tasks_completed()} completed")
                print(f"  Agent states: {self.get_agent_states_summary()}")
        
        print(f"Simulation completed after {self.step_count} steps")
        print(f"Total tasks created: {self.get_total_tasks_created()}")
        print(f"Total tasks completed: {self.get_total_tasks_completed()}")
        print(f"Final agent utilization: {self.get_agent_utilization():.2%}")
        
        return self.datacollector.get_model_vars_dataframe()


def run_basic_experiment():
    """Run a basic experiment"""
    model = WarehouseDSMModel(
        num_agents=16,
        warehouse_width=20,
        warehouse_height=10,
        task_arrival_rate=0.05
    )
    
    results = model.run_simulation(steps=1000, verbose=True)
    model.export_results()
    
    return model, results


if __name__ == "__main__":
    # Run basic experiment
    model, results = run_basic_experiment()
    
    # Show final state
    print("\nFinal warehouse state:")
    model.visualize_current_state()