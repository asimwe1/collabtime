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

TASK_WORK_DURATION_S = 45.0
TASK_WORK_DURATION_STEPS = int(TASK_WORK_DURATION_S / STEP_DURATION_S)

