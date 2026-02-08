import asyncio
import logging
from typing import Callable, Dict, List, Any

logger = logging.getLogger("Bus")

class InMemoryEventBus:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(InMemoryEventBus, cls).__new__(cls)
            cls._instance.subscribers = {}
        return cls._instance

    def subscribe(self, event_type: str, callback: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)

    async def publish(self, event_type: str, payload: Any):
        # Notification des abonnés spécifiques
        if event_type in self.subscribers:
            for callback in self.subscribers[event_type]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        asyncio.create_task(callback(payload))
                    else:
                        callback(payload)
                except Exception as e:
                    logger.error(f"Bus Error (Specific): {e}")

        # Notification des abonnés globaux (*)
        if "*" in self.subscribers:
            for callback in self.subscribers["*"]:
                try:
                    msg = {"type": event_type, "data": payload}
                    if asyncio.iscoroutinefunction(callback):
                        asyncio.create_task(callback(msg))
                    else:
                        callback(msg)
                except Exception as e:
                    logger.error(f"Bus Error (Wildcard): {e}")

bus = InMemoryEventBus()
