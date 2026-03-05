"""
PercentileTracker — Suivi adaptatif par percentiles glissants.

Classe utilitaire (PAS un singleton — chaque organe crée ses instances).
Buffer circulaire + liste triée pour lookup O(log n).

Utilisé par ReptilianCore pour calibrer les seuils CPU/RAM au hardware réel,
au lieu de seuils absolus codés en dur.
"""

import bisect
from collections import deque
from typing import Any, Dict, Optional


# ============================================================
# Constantes
# ============================================================

DEFAULT_CAPACITY = 8640       # 24h × 10s/tick (ou 5s watchdog → 12h, OK)
DEFAULT_MIN_SAMPLES = 360     # 1h minimum avant percentiles fiables


# ============================================================
# PercentileTracker
# ============================================================

class PercentileTracker:
    """Buffer circulaire avec lookup percentile O(log n).

    Utilise une deque(maxlen) pour l'insertion O(1) et une liste triée
    via bisect.insort pour le calcul de percentiles.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY,
                 min_samples: int = DEFAULT_MIN_SAMPLES):
        self._capacity = max(1, capacity)
        self._min_samples = max(1, min_samples)
        self._buffer: deque = deque(maxlen=self._capacity)
        self._sorted: list = []

    # --- Enregistrement ---

    def record(self, value: float) -> None:
        """Enregistre une valeur dans le buffer circulaire."""
        # Si le buffer est plein, la deque évince le plus ancien
        if len(self._buffer) == self._capacity:
            evicted = self._buffer[0]
            # Retirer de la liste triée
            idx = bisect.bisect_left(self._sorted, evicted)
            if idx < len(self._sorted) and self._sorted[idx] == evicted:
                self._sorted.pop(idx)

        self._buffer.append(value)
        bisect.insort(self._sorted, value)

    # --- Requêtes ---

    def get_percentile_of(self, value: float) -> float:
        """Rang de `value` dans la distribution [0.0, 1.0].

        Retourne la fraction d'échantillons <= value.
        """
        n = len(self._sorted)
        if n == 0:
            return 0.0
        # bisect_right donne le nombre d'éléments <= value
        rank = bisect.bisect_right(self._sorted, value)
        return rank / n

    def get_value_at(self, percentile: float) -> float:
        """Valeur au percentile donné (0.0 à 1.0).

        Clampe le percentile dans [0, 1] et retourne l'élément correspondant.
        """
        n = len(self._sorted)
        if n == 0:
            return 0.0
        p = max(0.0, min(1.0, percentile))
        idx = int(p * (n - 1))
        return self._sorted[idx]

    def is_ready(self) -> bool:
        """True si assez d'échantillons pour des percentiles fiables."""
        return len(self._buffer) >= self._min_samples

    def count(self) -> int:
        """Nombre d'échantillons actuels."""
        return len(self._buffer)

    # --- Sérialisation ---

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise l'état pour la persistance."""
        return {
            "capacity": self._capacity,
            "min_samples": self._min_samples,
            "values": list(self._buffer),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PercentileTracker":
        """Restaure depuis un dict sérialisé."""
        capacity = data.get("capacity", DEFAULT_CAPACITY)
        min_samples = data.get("min_samples", DEFAULT_MIN_SAMPLES)
        tracker = cls(capacity=capacity, min_samples=min_samples)
        values = data.get("values", [])
        for v in values:
            tracker.record(float(v))
        return tracker

    def __repr__(self) -> str:
        ready = "ready" if self.is_ready() else "cold"
        return f"PercentileTracker({self.count()}/{self._capacity}, {ready})"
