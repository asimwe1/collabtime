from central.scheduler import CentralizedScheduler
from coord.coordinator import Coordinator
from world.graph import WarehouseGraph


def test_scheduler_records_path_planning_service_time():
    warehouse = WarehouseGraph(width=20, height=15)
    scheduler = CentralizedScheduler(warehouse, Coordinator())

    path = scheduler.request_path(agent_id=1, start=0, goal=299, current_step=0)
    metrics = scheduler.get_metrics()

    assert path[0] == 0
    assert path[-1] == 299
    assert metrics["total_path_requests"] == 1
    assert metrics["total_path_planning_time_ms"] >= 0.0
    assert metrics["avg_path_planning_time_ms"] >= 0.0
