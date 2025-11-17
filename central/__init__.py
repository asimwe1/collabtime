"""
Centralized Coordination Module

Traditional centralized warehouse coordination (OpenRMF-style baseline).
The central scheduler is the architectural bottleneck that distributed systems avoid.
"""

from .scheduler import CentralizedScheduler

__all__ = ['CentralizedScheduler']

