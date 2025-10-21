#!/usr/bin/env python3
"""
Launch Interactive Mesa Visualization
Opens a browser-based UI using Mesa 3.x Solara framework
"""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from mesa.visualization import Slider, SolaraViz
from world.model import WarehouseDSMModel


def agent_portrayal(agent):
    """Define how agents are displayed on the grid."""
    return {
        "color": "tab:blue" if agent.state.value != "idle" else "tab:gray",
        "size": 25,
        "marker": "o",
    }


def make_model(params):
    """Create model instance from params."""
    return WarehouseDSMModel(
        n_agents=params.get("n_agents", 8),
        warehouse_width=params.get("warehouse_width", 20),
        warehouse_height=params.get("warehouse_height", 15),
        task_arrival_rate=params.get("task_arrival_rate", 0.1),
    )


if __name__ == "__main__":
    print("=" * 60)
    print("Warehouse DSM Simulation - Interactive Visualization")
    print("=" * 60)
    print("\nStarting Mesa Solara server...")
    print("The browser will open automatically.")
    print("\nPress Ctrl+C to stop the server.")
    print("=" * 60)
    print()
    
    # Define model parameters for the UI
    model_params = {
        "n_agents": Slider("Number of Robots", 8, 1, 32, 1),
        "warehouse_width": Slider("Warehouse Width", 20, 5, 50, 1),
        "warehouse_height": Slider("Warehouse Height", 15, 5, 50, 1),
        "task_arrival_rate": Slider("Task Arrival Rate", 0.1, 0.0, 1.0, 0.01),
    }
    
    # Create the visualization page
    page = SolaraViz(
        WarehouseDSMModel,
        model_params,
        measures=[
            "Total_Tasks_Completed",
            "Total_Tasks_Created", 
            "Active_Tasks",
        ],
        name="Warehouse DSM Simulation",
    )
    
    # Launch using solara
    import solara.server.starlette
    page  # This triggers the Solara app to be created
    
    # For running standalone, we need uvicorn
    try:
        import uvicorn
        print("Starting server on http://localhost:8765")
        # The page object is a Solara component, we need to run it properly
        solara.server.starlette.ServerStarlette()
    except ImportError:
        print("Installing uvicorn...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "uvicorn", "-q"])
        import uvicorn
    
    # Use mesa command instead
    print("\nUse: solara run visualize.py")
    print("Or run from within the package structure")
