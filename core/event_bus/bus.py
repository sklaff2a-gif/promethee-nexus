import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Any

logger = logging.getLogger("Bus")


@dataclass
class DeadLetter:
    """Événement échoué capturé pour diagnostic et retry."""
    event_type: str
    payload: Any
    callback_name: str
    error: str
    error_type: str
    timestamp: float

class InMemoryEventBus:
    _instance = None
    MAX_DEAD_LETTERS = 100

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(InMemoryEventBus, cls).__new__(cls)
            cls._instance.subscribers = {}
            cls._instance._dead_letters: list = []
        return cls._instance

    def reset(self):
        """Réinitialise tous les abonnés et la DLQ. Utilisé par les tests."""
        self.subscribers = {}
        self._dead_letters = []

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
                await self._safe_call(callback, payload, event_type=event_type)

        # 2. Abonnés globaux (*) - Pour les WebSockets
        if "*" in self.subscribers:
            for callback in self.subscribers["*"]:
                wildcard_payload = {"type": event_type, "data": payload}
                await self._safe_call(callback, wildcard_payload, event_type=event_type)

    async def _safe_call(self, callback, data, event_type: str = ""):
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.error(f"Bus Error [{event_type}]: {e}")
            dl = DeadLetter(
                event_type=event_type,
                payload=data,
                callback_name=repr(callback),
                error=str(e),
                error_type=type(e).__name__,
                timestamp=time.time(),
            )
            self._dead_letters.append(dl)
            if len(self._dead_letters) > self.MAX_DEAD_LETTERS:
                self._dead_letters.pop(0)

    # --- Dead-letter queue ---

    def get_dead_letters(self) -> list:
        """Retourne une copie de la DLQ."""
        return list(self._dead_letters)

    def clear_dead_letters(self) -> int:
        """Vide la DLQ. Retourne le nombre d'entrées supprimées."""
        count = len(self._dead_letters)
        self._dead_letters.clear()
        return count

    @property
    def dead_letter_count(self) -> int:
        return len(self._dead_letters)

    async def retry_dead_letter(self, index: int) -> bool:
        """Rejoue un dead letter par index. Retourne True si succès."""
        if index < 0 or index >= len(self._dead_letters):
            return False
        dl = self._dead_letters[index]
        try:
            await self.publish(dl.event_type, dl.payload)
            self._dead_letters.pop(index)
            return True
        except Exception:
            return False


bus = InMemoryEventBus()


def publish_from_sync(event_type: str, payload: Any, label: str = ""):
    """Publie un événement depuis un contexte synchrone (pas de await possible).

    Remplace le pattern fire-and-forget `loop.create_task(bus.publish(...))`
    dont l'exception était avalée silencieusement si le task crashait
    (loop fermée, erreur infra). Les erreurs de handlers restent gérées
    par _safe_call/DLQ — ici on observe le task de publication lui-même.

    Retourne le task créé, ou None si aucune loop ne tourne (perte logguée).
    """
    suffix = f" ({label})" if label else ""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Normal hors serveur (tests, CLI) → debug, pas warning
        logger.debug(f"Bus publish_from_sync [{event_type}]{suffix}: pas de loop active, événement perdu")
        return None

    task = loop.create_task(bus.publish(event_type, payload))

    def _log_exc(t):
        if not t.cancelled() and t.exception():
            logger.warning(f"Bus publish_from_sync [{event_type}]{suffix}: {t.exception()}")

    task.add_done_callback(_log_exc)
    return task
