"""
Global state management for the dashboard.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from world.model import WarehouseDSMModel
import threading
import time

# Optional: LF integration
try:
    from lf.bridge import LFTickClient
    LF_AVAILABLE = True
except ImportError:
    LF_AVAILABLE = False


class GlobalState:
    """Thread-safe global state container."""
    def __init__(self, initial_value=None):
        self._value = initial_value
    
    def get(self):
        return self._value
    
    def set(self, value):
        self._value = value


# Global state
model = GlobalState(None)
model_lock = threading.Lock()
running = GlobalState(False)
use_lf = GlobalState(False)


def create_initial_model(n_agents=8, width=20, height=15, task_rate=0.1):
    """Create a new model instance."""
    return WarehouseDSMModel(
        n_agents=n_agents,
        warehouse_width=width,
        warehouse_height=height,
        task_arrival_rate=task_rate,
    )


def simulation_loop():
    """Background thread for stepping the simulation."""
    import logging
    logger = logging.getLogger('warehouse.simulation')
    logger.info("Simulation loop started")
    
    if use_lf.get() and LF_AVAILABLE:
        # LF-driven simulation
        logger.info("Attempting to connect to LF tick server on 127.0.0.1:9001...")
        print("Connecting to LF tick server on 127.0.0.1:9001...")
        
        try:
            client = LFTickClient()
            client.connect()
            
            if client.sock:
                logger.info("Successfully connected to LF tick server!")
                print("Connected to LF tick server!")
                for _ in client.ticks():
                    if running.get() and model.get():
                        with model_lock:
                            model.get().step()
                    if not running.get():
                        time.sleep(0.1)
            else:
                logger.error("LF connection failed (no socket), falling back to internal timing")
                print("LF connection failed, using internal timing")
                use_lf.set(False)
        except Exception as e:
            logger.error(f"LF connection error: {e}", exc_info=True)
            print(f"LF connection error: {e}")
            print("Falling back to internal timing")
            use_lf.set(False)
    
    if not use_lf.get():
        # Internal timing
        step_count = 0
        while True:
            if running.get() and model.get():
                with model_lock:
                    m = model.get()
                    m.step()
                    step_count += 1
                    if step_count % 50 == 0:
                        logger.info(f"Step {step_count}: {len(m.schedule.agents)} agents, "
                                  f"{m.task_counter} tasks created, "
                                  f"{len(m.completed_tasks)} completed")
            time.sleep(0.5)  # Step every 0.5s for smoother, more realistic visualization

