"""
A* pathfinding with DSM congestion awareness.
"""

import heapq
from typing import List, Optional, Callable

from .costs import compute_edge_cost


def astar_with_congestion(
    warehouse,
    dsm_api,
    start: int,
    goal: int,
    cost_params: dict = None
) -> List[int]:
    """
    A* pathfinding with dynamic edge costs from DSM congestion signals.
    
    Args:
        warehouse: WarehouseGraph instance
        dsm_api: DSM instance (or router)
        start: Start node
        goal: Goal node
        cost_params: Optional dict with 'alpha', 'beta', 'max_aoi_ms'
    
    Returns:
        Path as list of node IDs, or [] if no path found
    """
    if start == goal:
        return [start]
    
    # Use default params if none provided
    if cost_params is None:
        cost_params = {'alpha': 2.0, 'beta': 0.5, 'max_aoi_ms': 5000}
    
    # A* data structures
    open_set = []
    heapq.heappush(open_set, (0.0, start))
    came_from = {}
    g_score = {start: 0.0}
    f_score = {start: _heuristic(warehouse, start, goal)}
    
    while open_set:
        _, current = heapq.heappop(open_set)
        
        if current == goal:
            return _reconstruct_path(came_from, current)
        
        for neighbor in warehouse.get_neighbors(current):
            # Compute dynamic edge cost using DSM signals
            edge_cost = compute_edge_cost(
                warehouse, dsm_api, current, neighbor,
                base_cost=1.0,
                alpha=cost_params.get('alpha', 2.0),
                beta=cost_params.get('beta', 0.5),
                max_aoi_ms=cost_params.get('max_aoi_ms', 5000)
            )
            
            tentative_g = g_score.get(current, float('inf')) + edge_cost
            
            if tentative_g < g_score.get(neighbor, float('inf')):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f = tentative_g + _heuristic(warehouse, neighbor, goal)
                f_score[neighbor] = f
                heapq.heappush(open_set, (f, neighbor))
    
    return []


def _heuristic(warehouse, node: int, goal: int) -> float:
    """Manhattan distance heuristic."""
    try:
        x1, y1 = warehouse.node_to_pos(node)
        x2, y2 = warehouse.node_to_pos(goal)
        return abs(x2 - x1) + abs(y2 - y1)
    except Exception:
        return 0.0


def _reconstruct_path(came_from: dict, current: int) -> List[int]:
    """Reconstruct path from came_from map."""
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path

