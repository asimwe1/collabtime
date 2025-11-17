"""
DSM Module - Distributed Shared Memory for Warehouse System

This module provides the core distributed shared memory functionality
for coordinating robotic agents in a warehouse environment.
"""

from .api import DSM, DSMLayer, TaskRegistry, dsm
from .partition import WarehousePartition, create_example_partition
from .registry import TaskRegistry as TaskRegistryFull, Task, TaskStatus

__all__ = [
    'DSM',
    'DSMLayer', 
    'TaskRegistry',
    'dsm',
    'WarehousePartition',
    'create_example_partition',
    'TaskRegistryFull',
    'Task',
    'TaskStatus'
]