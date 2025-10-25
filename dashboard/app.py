"""
Main Dash application factory.
"""

from dash import Dash
from dash import html
import logging


def create_app():
    """Create and configure the Dash app."""
    app = Dash(__name__, title="CoMemorySim")
    
    # Import layout and callbacks AFTER app is created
    from .layout import create_layout
    from . import callbacks  # This registers callbacks with @callback decorator
    
    app.layout = create_layout()
    # Inject minimal CSS for immediate interaction feedback without callback round-trip
    app.index_string = (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "    <head>\n"
        "        {%metas%}\n"
        "        <title>{%title%}</title>\n"
        "        {%favicon%}\n"
        "        {%css%}\n"
        "        <style>\n"
        "        .control-btn{width:56px;height:56px;display:inline-flex;align-items:center;justify-content:center;font-size:22px;line-height:1;padding:0;box-sizing:border-box;font-family:'Segoe UI Symbol','Noto Sans','Roboto','Helvetica','Arial',sans-serif;cursor:pointer;border:1px solid #ddd;border-radius:8px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,0.04);}\n"
        "        .control-btn:hover{box-shadow:0 2px 6px rgba(0,0,0,0.12);}\n"
        "        .control-btn:active{transform:translateY(1px);}\n"
        "        .control-btn.is-running{background:#dcfce7;border-color:#10b981;}\n"
        "        .control-btn.is-stopped{background:#fee2e2;border-color:#ef4444;}\n"
        "        </style>\n"
        "    </head>\n"
        "    <body>\n"
        "        {%app_entry%}\n"
        "        <footer>\n"
        "            {%config%}\n"
        "            {%scripts%}\n"
        "            {%renderer%}\n"
        "        </footer>\n"
        "    </body>\n"
        "</html>"
    )
    
    # Silence HTTP request logs but keep errors
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    # Enable debug logging for simulation
    sim_log = logging.getLogger('warehouse.simulation')
    sim_log.setLevel(logging.DEBUG)
    
    return app
