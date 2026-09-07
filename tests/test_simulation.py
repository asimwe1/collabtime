import pytest

from pathfinder.routing import astar_with_congestion
from world.graph import WarehouseGraph
from world.model import WarehouseDSMModel


def test_congestion_aware_routing_returns_a_connected_path():
    warehouse = WarehouseGraph(width=20, height=15)
    start, goal = 0, warehouse.width * warehouse.height - 1

    path = astar_with_congestion(warehouse, dsm_api=None, start=start, goal=goal)

    assert path[0] == start
    assert path[-1] == goal
    assert all(warehouse.is_adjacent(node, next_node) for node, next_node in zip(path, path[1:]))


def test_model_uses_supplied_logical_time_for_agent_cache_updates():
    model = WarehouseDSMModel(n_agents=1, task_arrival_rate=0.0, seed=42, mode="p2p")
    agent = model.schedule.agents[0]

    model.advance(current_time_ms=125)

    assert model.current_time_ms == 125
    assert agent.local_cache.agent_location[agent.unique_id]["timestamp"] == 125


def test_p2p_model_records_gossip_messages():
    model = WarehouseDSMModel(n_agents=2, task_arrival_rate=0.0, seed=42, mode="p2p")

    for _ in range(3):
        model.step()

    metrics = model.get_coordination_metrics()
    assert metrics["gossip_rounds"] == 1
    assert metrics["gossip_messages"] == 2


@pytest.mark.parametrize("mode", ("p2p", "centralized"))
def test_model_completes_a_task_at_a_nearby_free_node(mode):
    model = WarehouseDSMModel(
        n_agents=1,
        warehouse_width=20,
        warehouse_height=15,
        task_arrival_rate=0.0,
        seed=42,
        mode=mode,
    )
    agent = model.schedule.agents[0]
    agent.work_duration = 0

    model.step()
    task_location = model.warehouse.get_neighbors(agent.node)[0]
    task_id = model.coordinator.create_task(task_location)
    for _ in range(50):
        model.step()
        task = model.coordinator.task_registry.get_task(task_id)
        if task.status.value == "completed":
            break

    task = model.coordinator.task_registry.get_task(task_id)
    assert task.status.value == "completed"
    assert agent.metrics["tasks_completed"] == 1
    assert agent.state.value == "idle"
