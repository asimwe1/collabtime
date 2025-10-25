"""
Pathfinding module with DSM-aware congestion routing.

Provides:
- Hand-tuned congestion-aware A* pathfinding
- Edge cost computation using DSM signals (jam, flow)
- Placeholder for future ML-based routing predictor
"""

from .routing import astar_with_congestion
from .costs import compute_edge_cost

__all__ = ['astar_with_congestion', 'compute_edge_cost']

