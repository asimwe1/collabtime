"""
Watch Manager for Event Notifications

Provides ZooKeeper-style watches for task events:
- task_created: New task spawned in region
- task_claimed: Task claimed by agent
- task_released: Task released (failed or expired lease)
- task_completed: Task finished successfully
"""

from dataclasses import dataclass
from typing import Dict, Callable, Any
import logging


@dataclass
class Watch:
    """Represents a registered watch callback"""
    handle: str
    region_id: int
    event_type: str
    callback: Callable[[Dict[str, Any]], None]


class WatchManager:
    """Manages event notification watches"""
    
    def __init__(self):
        self.watches: Dict[str, Watch] = {}
        self.watch_counter = 0
        self.logger = logging.getLogger(__name__)
    
    def register(self, region_id: int, event_type: str, 
                callback: Callable[[Dict[str, Any]], None]) -> str:
        """
        Register a callback for events in a region.
        Returns watch handle (use for unregister).
        """
        handle = f"watch_{self.watch_counter}"
        self.watch_counter += 1
        
        self.watches[handle] = Watch(
            handle=handle,
            region_id=region_id,
            event_type=event_type,
            callback=callback
        )
        
        return handle
    
    def unregister(self, handle: str) -> None:
        """Remove a watch callback"""
        self.watches.pop(handle, None)
    
    def fire(self, event_type: str, event_data: Dict[str, Any]) -> int:
        """
        Fire event to matching watches.
        Returns count of callbacks fired.
        """
        fired = 0
        for watch in list(self.watches.values()):
            if watch.event_type == event_type:
                try:
                    watch.callback(event_data)
                    fired += 1
                except Exception as e:
                    self.logger.error(f"Watch callback failed for {watch.handle}: {e}")
        
        return fired

