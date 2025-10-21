#!/usr/bin/env python3
"""
Modern Mesa Solara Visualization for Warehouse DSM
Uses Mesa 3.x+ Solara-based visualization framework.
"""

import mesa
from mesa.visualization import SolaraViz, make_space_component
import solara

from .model import WarehouseDSMModel
from .graph import create_standard_warehouse


def agent_portrayal(agent):
    """Define how agents are displayed."""
    return {
        "color": "tab:blue",
        "size": 50,
        "marker": "o",
    }


def make_visualization():
    """Create and configure the Solara visualization."""
    
    # Model parameters that users can adjust
    model_params = {
        "n_agents": {
            "type": "SliderInt",
            "value": 8,
            "label": "Number of Robots",
            "min": 1,
            "max": 32,
            "step": 1,
        },
        "warehouse_width": {
            "type": "SliderInt",
            "value": 20,
            "label": "Warehouse Width",
            "min": 5,
            "max": 50,
            "step": 1,
        },
        "warehouse_height": {
            "type": "SliderInt",
            "value": 15,
            "label": "Warehouse Height",
            "min": 5,
            "max": 50,
            "step": 1,
        },
        "task_arrival_rate": {
            "type": "SliderFloat",
            "value": 0.1,
            "label": "Task Arrival Rate (per step)",
            "min": 0.0,
            "max": 1.0,
            "step": 0.01,
        },
    }
    
    # Create the visualization
    viz = SolaraViz(
        WarehouseDSMModel,
        model_params,
        measures=[
            "Total_Tasks_Completed",
            "Active_Tasks",
            "Average_Task_Completion_Time",
            "Agent_Utilization",
        ],
        name="Warehouse DSM Simulation",
        agent_portrayal=agent_portrayal,
    )
    
    return viz


if __name__ == "__main__":
    viz = make_visualization()
    viz.launch()


