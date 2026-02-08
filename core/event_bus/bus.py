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

    def reset(self):
        """Réinitialise tous les abonnés. Utilisé par les tests."""
        self.subscribers = {}

    def subscribe(self, event_type: str, callback: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable):
        """Retire un callback d'un type d'événement."""
        if event_type in self.subscribers:
            try:
                self.subscribers[event_type].remove(callback)
            except ValueError:
                pass

    async def publish(self, event_type: str, payload: Any):
        # 1. Abonnés directs
        if event_type in self.subscribers:
            for callback in self.subscribers[event_type]:
                await self._safe_call(callback, payload)

        # 2. Abonnés globaux (*) - Pour les WebSockets
        if "*" in self.subscribers:
            for callback in self.subscribers["*"]:
                wildcard_payload = {"type": event_type, "data": payload}
                await self._safe_call(callback, wildcard_payload)

    async def _safe_call(self, callback, data):
        try:
            if asyncio.iscoroutinefunction(callback):
                asyncio.create_task(callback(data))
            else:
                callback(data)
        except Exception as e:
            logger.error(f"Bus Error: {e}")

bus = InMemoryEventBus()
