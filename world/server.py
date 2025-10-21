#!/usr/bin/env python3
"""
Mesa Browser-based UI for Warehouse DSM Simulation

Renders the warehouse NetworkX graph and overlays agent occupancy.
"""

from typing import Dict

from mesa.visualization.ModularVisualization import ModularServer
from mesa.visualization.modules import ChartModule
from mesa.visualization.modules import NetworkModule
from mesa.visualization.UserParam import UserSettableParameter

from .model import WarehouseDSMModel


def network_portrayal(G):
    """Create a node/edge portrayal for Mesa NetworkModule from the model graph."""
    def portray(model):
        portrayal = {
            "nodes": [],
            "edges": [],
        }
        # Build node portrayals
        occupancy: Dict[int, int] = model.get_warehouse_occupancy()
        for node_id, data in model.warehouse.graph.nodes(data=True):
            node_type = model.warehouse.get_node_type(node_id)
            x = data.get("x", 0)
            y = data.get("y", 0)
            num_agents = occupancy.get(node_id, 0)

            # Color by node type
            if node_type == "storage":
                color = "#9ecae1"
            elif node_type == "sortation":
                color = "#a1d99b"
            elif node_type == "pick_location":
                color = "#3182bd"
            elif node_type == "pack_station":
                color = "#31a354"
            else:
                color = "#d9d9d9"

            portrayal["nodes"].append(
                {
                    "id": node_id,
                    "size": 4 + 2 * num_agents,
                    "color": color,
                    "label": str(num_agents) if num_agents > 0 else "",
                    "x": x,
                    "y": y,
                }
            )

        # Edges (no color coding for now)
        for u, v in model.warehouse.graph.edges():
            portrayal["edges"].append({"source": u, "target": v, "color": "#999999"})

        return portrayal

    return portray


def make_server(width: int = 20, height: int = 10) -> ModularServer:
    """Create and return a configured Mesa ModularServer."""
    # Network module for warehouse graph
    network = NetworkModule(network_portrayal, width=600, height=600)

    # Charts for basic metrics
    chart = ChartModule(
        [
            {"Label": "Total_Tasks_Completed", "Color": "#2ca02c"},
            {"Label": "Active_Tasks", "Color": "#ff7f0e"},
        ],
        data_collector_name="datacollector",
    )

    # User-settable parameters
    params = {
        "n_agents": UserSettableParameter("slider", "Agents (N)", 8, 1, 64, 1),
        "warehouse_width": UserSettableParameter("slider", "Width", width, 5, 50, 1),
        "warehouse_height": UserSettableParameter("slider", "Height", height, 5, 50, 1),
        "task_arrival_rate": UserSettableParameter("slider", "Task Arrival (per step)", 0.1, 0.0, 1.0, 0.01),
    }

    server = ModularServer(
        WarehouseDSMModel,
        [network, chart],
        "Warehouse DSM Simulation",
        params,
    )
    server.port = 8521
    return server


if __name__ == "__main__":
    srv = make_server()
    srv.launch()
