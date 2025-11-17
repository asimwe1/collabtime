"""
Centralized Path Scheduler for Baseline Comparison
All agents request paths from this single authority, which has perfect global state
but must serialize all pathfinding operations.
"""

from typing import List, Dict, Tuple, Optional
import networkx as nx
from collections import defaultdict


class CentralizedScheduler:
    """
    Central authority that plans paths for all agents.
    
    This is the architectural bottleneck that distributed systems avoid.
    - Has perfect global state (all agent positions, all reserved paths)
    - Must serialize all pathfinding requests
    - Runs space-time A* to avoid conflicts
    """
    
    def __init__(self, warehouse_graph, logger=None):
        self.graph = warehouse_graph
        self.logger = logger
        
        self.path_reservations: Dict[int, List[Tuple[int, int]]] = {}
        self.agent_positions: Dict[int, int] = {}
        
        # Central congestion data store (perfect, always fresh)
        self.flow_data: Dict[int, float] = defaultdict(float)
        self.jam_data: Dict[int, float] = defaultdict(float)
        
        self.metrics = {
            'total_requests': 0,
            'total_computation_time': 0,
            'active_reservations': 0,
            'congestion_data_size': 0
        }
    
    def update_agent_position(self, agent_id: int, node: int):
        """Agents must report their position to central authority."""
        self.agent_positions[agent_id] = node
    
    def report_flow(self, node: int, flow_value: float = 1.0):
        """Agent reports edge usage (perfect, instant update)."""
        self.flow_data[node] += flow_value
        self.metrics['congestion_data_size'] = len(self.flow_data) + len(self.jam_data)
    
    def report_jam(self, node: int, jam_value: float):
        """Agent reports congestion (perfect, instant update)."""
        alpha = 0.5
        self.jam_data[node] = alpha * jam_value + (1 - alpha) * self.jam_data[node]
        self.metrics['congestion_data_size'] = len(self.flow_data) + len(self.jam_data)
    
    def request_path(self, agent_id: int, start: int, goal: int, current_step: int) -> List[int]:
        """
        Central pathfinding request (BLOCKING) with congestion awareness.
        
        Uses perfect, fresh congestion data (no staleness like distributed).
        This is the bottleneck: all agents wait in line for the scheduler.
        """
        self.metrics['total_requests'] += 1
        
        occupied_nodes = set(self.agent_positions.values())
        occupied_nodes.discard(start)
        
        try:
            if start == goal:
                return [start]
            
            path = self._astar_with_congestion(start, goal, occupied_nodes)
            
            if path:
                self.path_reservations[agent_id] = path[:min(3, len(path))]
                self.metrics['active_reservations'] = len(self.path_reservations)
                return path
            else:
                return []
                
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Central scheduler pathfinding failed: {e}")
            return []
    
    def release_path(self, agent_id: int):
        """Agent releases its path reservation when done."""
        if agent_id in self.path_reservations:
            del self.path_reservations[agent_id]
            self.metrics['active_reservations'] = len(self.path_reservations)
    
    def _astar_with_congestion(self, start: int, goal: int, obstacles: set) -> List[int]:
        """
        Congestion-aware A* using central perfect data.
        
        Same algorithm as distributed but with PERFECT data (no AoI).
        """
        if start in obstacles or goal in obstacles:
            obstacles_copy = obstacles - {start, goal}
        else:
            obstacles_copy = obstacles
        
        G = self.graph.graph
        temp_graph = G.copy()
        
        for node in obstacles_copy:
            if node in temp_graph and node != start and node != goal:
                temp_graph.remove_node(node)
        
        if start not in temp_graph or goal not in temp_graph:
            return []
        
        try:
            path = nx.astar_path(
                temp_graph,
                start,
                goal,
                heuristic=lambda n1, n2: self._manhattan_distance(n1, n2),
                weight=lambda u, v, d: self._edge_cost_with_congestion(u, v)
            )
            return path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
    
    def _edge_cost_with_congestion(self, from_node: int, to_node: int) -> float:
        """
        Compute edge cost with congestion (PERFECT data, no staleness).
        
        Uses same cost model as distributed but with perfect information.
        """
        base_cost = 1.0
        alpha = 2.0
        beta = 0.5
        
        jam_cost = self.jam_data.get(to_node, 0.0)
        flow_cost = self.flow_data.get(to_node, 0.0)
        
        return base_cost + alpha * jam_cost + beta * flow_cost
    
    def _astar_with_obstacles_simple(self, start: int, goal: int, obstacles: set) -> List[int]:
        """
        Simple A* avoiding occupied/reserved nodes.
        
        This is simplified - real space-time A* would be more complex.
        The bottleneck is that this runs serially for ALL agents.
        """
        if start in obstacles or goal in obstacles:
            obstacles_copy = obstacles - {start, goal}
        else:
            obstacles_copy = obstacles
        
        G = self.graph.graph
        
        temp_graph = G.copy()
        for node in obstacles_copy:
            if node in temp_graph and node != start and node != goal:
                temp_graph.remove_node(node)
        
        if start not in temp_graph or goal not in temp_graph:
            return []
        
        try:
            path = nx.astar_path(
                temp_graph,
                start,
                goal,
                heuristic=lambda n1, n2: self._manhattan_distance(n1, n2),
                weight='weight'
            )
            return path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
    
    def _manhattan_distance(self, node1: int, node2: int) -> float:
        """Manhattan distance heuristic."""
        try:
            x1 = self.graph.graph.nodes[node1]['x']
            y1 = self.graph.graph.nodes[node1]['y']
            x2 = self.graph.graph.nodes[node2]['x']
            y2 = self.graph.graph.nodes[node2]['y']
            return abs(x1 - x2) + abs(y1 - y2)
        except (KeyError, TypeError):
            return 0
    
    def get_metrics(self) -> Dict:
        """Return scheduler performance metrics."""
        return {
            'total_path_requests': self.metrics['total_requests'],
            'active_path_reservations': self.metrics['active_reservations'],
        }

