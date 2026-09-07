import pytest

from dashboard.state import create_initial_model
from dashboard.visualization import create_warehouse_figure


@pytest.mark.parametrize(
    ("dashboard_mode", "model_mode"),
    (("centralized", "centralized"), ("distributed", "p2p")),
)
def test_dashboard_creates_the_selected_simulation_mode(dashboard_mode, model_mode):
    model = create_initial_model(
        n_agents=2,
        width=20,
        height=15,
        task_rate=0.0,
        mode=dashboard_mode,
    )

    assert model.mode == model_mode
    assert len(model.schedule.agents) == 2


def test_dashboard_renders_tasks_from_the_coordinator():
    model = create_initial_model(n_agents=1, width=20, height=15, task_rate=0.0)
    location = next(iter(model.warehouse.graph.nodes))
    model.coordinator.create_task(location)

    figure = create_warehouse_figure(model)
    available_tasks = next(trace for trace in figure.data if trace.name == "Available Tasks")

    assert list(available_tasks.x) == [0]
    assert list(available_tasks.y) == [0]
