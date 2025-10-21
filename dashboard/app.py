"""
Main Dash application factory.
"""

from dash import Dash
import logging


def create_app():
    """Create and configure the Dash app."""
    app = Dash(__name__)
    
    # Import layout and callbacks AFTER app is created
    from .layout import create_layout
    from . import callbacks  # This registers callbacks with @callback decorator
    
    app.layout = create_layout()
    
    # Silence HTTP request logs but keep errors
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Enable debug logging for simulation
    sim_log = logging.getLogger('warehouse.simulation')
    sim_log.setLevel(logging.DEBUG)
    
    return app
