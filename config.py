#!/usr/bin/env python3
"""
Central configuration for warehouse simulation.
Single source of truth for simulation timing and constants.
"""

# Lingua Franca timing
LF_TICK_DURATION_MS = 50
STEP_DURATION_S = LF_TICK_DURATION_MS / 1000.0

# Metrics calculation windows
THROUGHPUT_WINDOW_S = 30.0
LATENCY_WINDOW_SIZE = 50

# Dashboard refresh rate
DASHBOARD_UPDATE_INTERVAL_MS = 500

# Physical robot parameters
CELL_SIZE_M = 1.0
ROBOT_VELOCITY_MS = 0.5
LATERAL_VELOCITY_MS = 0.5

# Movement timing derived from physical parameters
MOVEMENT_DURATION_S = CELL_SIZE_M / ROBOT_VELOCITY_MS
MOVEMENT_DURATION_STEPS = int(MOVEMENT_DURATION_S / STEP_DURATION_S)

LATERAL_MOVE_DURATION_S = CELL_SIZE_M / LATERAL_VELOCITY_MS
LATERAL_MOVE_DURATION_STEPS = int(LATERAL_MOVE_DURATION_S / STEP_DURATION_S)

# Task work duration
TASK_WORK_DURATION_S = 45.0
TASK_WORK_DURATION_STEPS = int(TASK_WORK_DURATION_S / STEP_DURATION_S)

# Agent behavior thresholds
STUCK_TIMEOUT_STEPS = MOVEMENT_DURATION_STEPS * 3
REPLAN_ATTEMPTS = [2, 5, 10]

# Default warehouse dimensions
DEFAULT_WAREHOUSE_WIDTH = 20
DEFAULT_WAREHOUSE_HEIGHT = 15

# Warehouse layout: fixed topology structure
PERIMETER_DEPTH = 1
BUFFER_DEPTH = 1

VERTICAL_AISLE_WIDTH = 2
HORIZONTAL_AISLE_WIDTH = 2
MIDDLE_SEPARATOR_WIDTH = 3
SHELF_BLOCK_WIDTH = 2
SHELF_BLOCK_HEIGHT = 3

# Node capacities: agents per cell
STAGING_CAPACITY = 1
AISLE_CAPACITY = 1
SHELF_CAPACITY = 0
WORK_STATION_CAPACITY = 1

# Dynamic calculations: adapt to warehouse size
def calculate_pick_pack_locations(width=DEFAULT_WAREHOUSE_WIDTH, height=DEFAULT_WAREHOUSE_HEIGHT):
    total_cells = width * height
    pick_locations = max(10, int(total_cells * 0.15))
    pack_stations = max(5, int(total_cells * 0.08))
    return pick_locations, pack_stations

DEFAULT_PICK_LOCATIONS, DEFAULT_PACK_STATIONS = calculate_pick_pack_locations()

def calculate_task_latency(width=DEFAULT_WAREHOUSE_WIDTH, height=DEFAULT_WAREHOUSE_HEIGHT):
    typical_distance = (width + height) / 3
    return 2 * typical_distance * MOVEMENT_DURATION_S + TASK_WORK_DURATION_S

def calculate_agent_capacity(width=DEFAULT_WAREHOUSE_WIDTH, height=DEFAULT_WAREHOUSE_HEIGHT):
    latency = calculate_task_latency(width, height)
    return 1.0 / latency

def calculate_arrival_rate(n_agents=8, utilization=0.75, width=DEFAULT_WAREHOUSE_WIDTH, height=DEFAULT_WAREHOUSE_HEIGHT):
    capacity = calculate_agent_capacity(width, height)
    return n_agents * capacity * utilization

DEFAULT_TASK_LATENCY_S = calculate_task_latency()
DEFAULT_AGENT_CAPACITY_TASKS_PER_SEC = calculate_agent_capacity()
DEFAULT_TASK_ARRIVAL_RATE = calculate_arrival_rate()

# Distributed Shared Memory (DSM) coordination parameters
GOSSIP_PERIOD_MS = 50
LOCALITY_PREFERENCE_FACTOR = 0.9
MAX_AOI_MS = 3000  # 3s - max age for pathfinding decisions

# Logging frequency
LOG_INTERVAL_STEPS = 100
TASK_SPAWN_LOG_INTERVAL_STEPS = 100

