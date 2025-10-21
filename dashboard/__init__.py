"""
Dash-based visualization dashboard for warehouse simulation.

Modular structure:
- app.py: Dash app factory
- layout.py: UI layout components
- callbacks.py: Interactive callbacks
- visualization.py: Plotly figure generation
- state.py: Global state and simulation loop
"""

from .app import create_app

__all__ = ['create_app']

