"""
experience_clock.py — Horloge metabolique de Promethee (Phase C).

Compte les evenements causalement signes (PREFRONTAL_GOAL_COMPLETE,
DOPAMINE_DIP_FRUITLESS) comme des "cycles d'experience". Utilisee par la
Phase C pour mesurer le vieillissement des liens genomiques sans dependre
du temps d'horloge (qui est une illusion dans un systeme asynchrone).

Principe architectural (Gemini, Phase C 2026-04-13) :
Un cycle ne passe PAS pendant que Promethee est eteint ou en IDLE. Il passe
seulement quand une vraie experience homeostatique se produit. Une routine
devient obsolete parce qu'elle a ete battue 1000 fois dans l'arene, pas
parce que la Terre a fait le tour du Soleil.

Performance :
Le compteur est en RAM. La persistance disque est differee (toutes les 5 min
maximum), et peut etre forcee au shutdown. Objectif : 10000 ticks < 100ms.
"""

import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("experience_clock")

EXPERIENCE_CLOCK_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "experience_clock.json"
)

PERSIST_INTERVAL_S = 300.0  # Persiste sur disque toutes les 5 min max


class ExperienceClock:
    """Horloge metabolique singleton. Compte les cycles d'experience reelle."""

    _instance: Optional["ExperienceClock"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._cycle: int = 0
        self._last_persist_ts: float = time.time()
        self._dirty: bool = False
        self._load()

    def _load(self) -> None:
        """Charge le compteur depuis le disque au demarrage."""
        try:
            if os.path.exists(EXPERIENCE_CLOCK_FILE):
                with open(EXPERIENCE_CLOCK_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._cycle = int(data.get("cycle", 0))
                logger.info(f"[ExperienceClock] Loaded cycle={self._cycle}")
            else:
                self._cycle = 0
                logger.info("[ExperienceClock] No persisted state, starting at 0")
        except Exception as e:
            logger.warning(f"[ExperienceClock] Load failed ({e}), starting at 0")
            self._cycle = 0

    def _save(self) -> None:
        """Persiste sur disque. Ne pas appeler directement — utiliser tick() ou force_persist()."""
        try:
            os.makedirs(os.path.dirname(EXPERIENCE_CLOCK_FILE), exist_ok=True)
            with open(EXPERIENCE_CLOCK_FILE, "w", encoding="utf-8") as fh:
                json.dump({"cycle": self._cycle, "saved_at": time.time()}, fh)
            self._last_persist_ts = time.time()
            self._dirty = False
        except Exception as e:
            logger.warning(f"[ExperienceClock] Save failed: {e}")

    def tick(self, n: int = 1) -> None:
        """Incremente le compteur de n cycles (default 1).

        Persistance differee : n'ecrit sur disque que si PERSIST_INTERVAL_S
        s'est ecoule depuis la derniere ecriture. Garde-fou performance —
        cette fonction peut etre appelee des dizaines de milliers de fois.
        """
        if n <= 0:
            return
        self._cycle += n
        self._dirty = True
        now = time.time()
        if now - self._last_persist_ts >= PERSIST_INTERVAL_S:
            self._save()

    def current(self) -> int:
        """Retourne la valeur actuelle du compteur. Lecture pure, pas d'I/O."""
        return self._cycle

    def force_persist(self) -> None:
        """Force une ecriture disque immediate. A appeler au shutdown."""
        if self._dirty:
            self._save()

    @classmethod
    def reset_singleton(cls) -> None:
        """Reset le singleton. A utiliser UNIQUEMENT dans les tests."""
        with cls._lock:
            cls._instance = None


# Singleton global — import direct pour les consommateurs
experience_clock = ExperienceClock()


def wire_to_bus() -> None:
    """Branche l'horloge sur les evenements d'experience reelle.

    Bug historique (corrige 12/06) : le module definissait tick() mais
    PERSONNE ne l'appelait — l'horloge restait a 0 et ne persistait jamais
    (8 resets constates par l'observateur nocturne). Le docstring citait
    DOPAMINE_DIP_FRUITLESS, un event qui n'existe pas ; les events reels
    sont PREFRONTAL_GOAL_COMPLETE et DOPAMINE_DIP.
    """
    from core.event_bus.bus import bus

    async def _on_experience(_payload):
        experience_clock.tick()

    bus.subscribe("PREFRONTAL_GOAL_COMPLETE", _on_experience)
    bus.subscribe("DOPAMINE_DIP", _on_experience)
    logger.info("[ExperienceClock] Cable au bus (PREFRONTAL_GOAL_COMPLETE, DOPAMINE_DIP)")
