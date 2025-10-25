"""
Edge cost computation using DSM congestion signals.
"""

from typing import Optional


def compute_edge_cost(
    warehouse,
    dsm_api,
    from_node: int,
    to_node: int,
    base_cost: float = 1.0,
    alpha: float = 2.0,
    beta: float = 0.5,
    max_aoi_ms: int = 5000
) -> float:
    """
    Compute dynamic edge cost using DSM congestion signals.
    
    Args:
        warehouse: WarehouseGraph instance
        dsm_api: DSM instance (or router)
        from_node: Source node
        to_node: Destination node
        base_cost: Base traversal cost (default 1.0)
        alpha: Weight for jam_signal penalty
        beta: Weight for flow_trace penalty
        max_aoi_ms: Maximum age of information in milliseconds
    
    Returns:
        Dynamic cost: base_cost + α × jam + β × flow
    """
    # Start with base traversal cost
    cost = base_cost
    
    if dsm_api is None:
        return cost
    
    try:
        # Query jam signal at destination node
        jam_result = dsm_api.read_window('jam_signal', to_node, radius=0, max_aoi_ms=max_aoi_ms)
        jam_value = jam_result.get('peak_value', 0.0)
        
        # Query flow trace at destination node
        flow_result = dsm_api.read_window('flow_trace', to_node, radius=0, max_aoi_ms=max_aoi_ms)
        flow_value = flow_result.get('peak_value', 0.0)
        
        # Apply penalties
        cost += alpha * jam_value
        cost += beta * flow_value
        
    except Exception:
        pass
    
    return max(base_cost, cost)


def get_default_params():
    """Return default hand-tuned parameters for congestion-aware routing."""
    return {
        'alpha': 2.0,   # Jam penalty weight
        'beta': 0.5,    # Flow penalty weight
        'max_aoi_ms': 5000  # 5 second max age
    }

