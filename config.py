#!/usr/bin/env python3
"""
Central configuration for warehouse simulation.
Single source of truth for simulation timing and constants.
"""

LF_TICK_DURATION_MS = 50
STEP_DURATION_S = LF_TICK_DURATION_MS / 1000.0

THROUGHPUT_WINDOW_S = 30.0
LATENCY_WINDOW_SIZE = 50

DASHBOARD_UPDATE_INTERVAL_MS = 500

CELL_SIZE_M = 1.0
ROBOT_VELOCITY_MS = 0.5
LATERAL_VELOCITY_MS = 0.5

MOVEMENT_DURATION_S = CELL_SIZE_M / ROBOT_VELOCITY_MS
MOVEMENT_DURATION_STEPS = int(MOVEMENT_DURATION_S / STEP_DURATION_S)

LATERAL_MOVE_DURATION_S = CELL_SIZE_M / LATERAL_VELOCITY_MS
LATERAL_MOVE_DURATION_STEPS = int(LATERAL_MOVE_DURATION_S / STEP_DURATION_S)

TASK_WORK_DURATION_S = 45.0
TASK_WORK_DURATION_STEPS = int(TASK_WORK_DURATION_S / STEP_DURATION_S)

STUCK_TIMEOUT_STEPS = MOVEMENT_DURATION_STEPS * 3
REPLAN_ATTEMPTS = [2, 5, 10]

DEFAULT_WAREHOUSE_WIDTH = 20
DEFAULT_WAREHOUSE_HEIGHT = 15

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

LOG_INTERVAL_STEPS = 100
TASK_SPAWN_LOG_INTERVAL_STEPS = 100

