"""
Global state management for the dashboard.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from world.model import WarehouseDSMModel
from config import (
    STEP_DURATION_S, 
    DEFAULT_TASK_ARRIVAL_RATE,
    calculate_task_latency,
    calculate_agent_capacity,
    TASK_WORK_DURATION_S
)
import threading
import time
import logging
from datetime import datetime

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
auto_stop_deadline = GlobalState(None)  # wall-clock deadline (epoch seconds)


def create_initial_model(n_agents=8, width=20, height=15, task_rate=None, mode: str = 'centralized', num_shards: int = 4):
    """Create a new model instance (centralized or distributed)."""
    if task_rate is None:
        task_rate = DEFAULT_TASK_ARRIVAL_RATE
    step_dt = STEP_DURATION_S

    dsm_instance = None
    if mode == 'distributed':
        try:
            from dsm.router import DSMRouter, default_region_mapper
            def node_to_shard(node_id: int, w: int = width, shards: int = num_shards) -> int:
                return default_region_mapper(node_id, w, shards)
            dsm_instance = DSMRouter(num_shards=num_shards, node_to_shard=node_to_shard)
        except Exception:
            dsm_instance = None

    dashboard_logger = logging.getLogger('WarehouseDashboard')
    if not dashboard_logger.handlers:
        log_dir = Path(__file__).parent.parent / 'results'
        log_dir.mkdir(exist_ok=True)
        
        file_handler = logging.FileHandler(log_dir / 'dashboard.log')
        file_handler.setLevel(logging.INFO)
        
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(message)s')
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)
        
        dashboard_logger.addHandler(file_handler)
        dashboard_logger.addHandler(stream_handler)
        dashboard_logger.setLevel(logging.INFO)
    
    task_latency = calculate_task_latency(width, height)
    agent_capacity = calculate_agent_capacity(width, height)
    system_capacity = n_agents * agent_capacity
    utilization = task_rate / system_capacity if system_capacity > 0 else 0
    
    dashboard_logger.info("=" * 70)
    dashboard_logger.info(f"WAREHOUSE SIMULATION RUN - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    dashboard_logger.info("=" * 70)
    dashboard_logger.info(f"Memory Architecture: {mode.upper()}")
    if mode == 'distributed':
        dashboard_logger.info(f"  DSM Shards: {num_shards}")
    dashboard_logger.info(f"Warehouse Dimensions: {width} × {height} = {width * height} cells")
    dashboard_logger.info(f"Agents: {n_agents}")
    dashboard_logger.info(f"Step Duration: {step_dt:.3f}s ({int(step_dt * 1000)}ms)")
    dashboard_logger.info(f"Robot Velocity: {1.0 / step_dt:.2f} cells/sec")
    dashboard_logger.info(f"Work Duration: {int(TASK_WORK_DURATION_S / step_dt)} steps ({TASK_WORK_DURATION_S}s)")
    dashboard_logger.info("")
    dashboard_logger.info("Capacity Analysis (for this warehouse size):")
    dashboard_logger.info(f"  Avg Task Distance: {(width + height) / 3:.1f} cells")
    dashboard_logger.info(f"  Typical Task Latency: {task_latency:.1f}s")
    dashboard_logger.info(f"  Agent Capacity: {agent_capacity:.4f} tasks/sec")
    dashboard_logger.info(f"  System Capacity ({n_agents} agents): {system_capacity:.4f} tasks/sec")
    dashboard_logger.info(f"  Task Arrival Rate: {task_rate:.4f} tasks/sec")
    dashboard_logger.info(f"  Expected Utilization: {utilization:.1%}")
    dashboard_logger.info("=" * 70)
    dashboard_logger.info("")
    
    return WarehouseDSMModel(
        n_agents=n_agents,
        warehouse_width=width,
        warehouse_height=height,
        task_arrival_rate=task_rate,
        step_duration_s=step_dt,
        dsm=dsm_instance,
        logger=dashboard_logger,
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
                    # Auto-stop if deadline reached
                    if running.get() and auto_stop_deadline.get() and time.time() >= auto_stop_deadline.get():
                        running.set(False)
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
            # Auto-stop if deadline reached
            if running.get() and auto_stop_deadline.get() and time.time() >= auto_stop_deadline.get():
                running.set(False)
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

