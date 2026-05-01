"""
BaselineTracker — Z-score circadien glissant pour le Body Schema.

Pour chaque métrique, maintient 6 PercentileTracker (un par créneau de 4h)
sur 7 jours de données, et fournit (mu, sigma, n_obs) pour calculer le z-score
contextuel. Bootstrap depuis config/symptomes_baseline.json tant que le
créneau n'a pas accumulé n_obs >= 30 (switch progressif entre 20 et 30).

Persiste dans memory/baselines.json. Singleton.
"""

import json
import logging
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional, Tuple

from core.percentile_tracker import PercentileTracker

logger = logging.getLogger("BaselineTracker")

# --- Constantes ---

CRENEAU_LABELS = ["nuit", "aube", "matin", "midi", "aprem", "soir"]
CRENEAU_DURATION_H = 4
CRENEAUX_PER_DAY = 24 // CRENEAU_DURATION_H  # 6

CRENEAU_CAPACITY = 60          # ~7 jours × ~9 obs/créneau (1 obs/30min en moyenne)
CRENEAU_MIN_SAMPLES_FULL = 30  # n >= 30 → 100% empirique
CRENEAU_MIN_SAMPLES_BLEND = 20 # n entre 20 et 30 → blend nominal/empirique

DERIVATIVE_WINDOW_S = 300      # 5 min pour calcul dz/dt

BASELINE_FILE = Path(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "memory",
        "baselines.json",
    )
)

CONFIG_FILE = Path(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "symptomes_baseline.json",
    )
)


def creneau_for_timestamp(ts: float) -> str:
    """Retourne le label de créneau circadien pour un timestamp Unix."""
    hour = int(time.localtime(ts).tm_hour)
    idx = hour // CRENEAU_DURATION_H
    return CRENEAU_LABELS[idx % CRENEAUX_PER_DAY]


class BaselineTracker:
    """Singleton — z-score circadien et dérivée temporelle par métrique."""

    _instance: Optional["BaselineTracker"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # metric_id → creneau_label → PercentileTracker
        self._trackers: Dict[str, Dict[str, PercentileTracker]] = {}

        # metric_id → deque[(timestamp, value)] — 30 dernières mesures pour dz/dt
        self._recent: Dict[str, deque] = {}

        # Baselines nominales (bootstrap)
        self._nominal: Dict[str, Dict[str, float]] = {}

        self._load_nominal()
        self._load_state()

    @classmethod
    def reset_singleton(cls):
        """Pour les tests : reset + recrée la variable globale du module
        pour que les références capturées par d'autres modules au moment
        de l'import voient la nouvelle instance."""
        global baseline_tracker
        cls._instance = None
        baseline_tracker = cls()

    # --- Chargement bootstrap ---

    def _load_nominal(self):
        """Charge les baselines nominales depuis config/symptomes_baseline.json."""
        try:
            if not CONFIG_FILE.exists():
                logger.warning(f"[BASELINE] Config nominale introuvable: {CONFIG_FILE}")
                return
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and "mu" in v and "sigma" in v:
                    entry = {
                        "mu": float(v["mu"]),
                        "sigma": float(v["sigma"]),
                    }
                    # V14.7 — min_sigma optionnel : empêche l'effondrement
                    # de variance pour les métriques d'accumulation nocive
                    # (dream_dette_h, etc.). Sans ce floor, sigma_empirique
                    # se resserre et déclenche des z >= 1.5 sur des états sains.
                    if "min_sigma" in v:
                        entry["min_sigma"] = float(v["min_sigma"])
                    self._nominal[k] = entry
            logger.info(f"[BASELINE] {len(self._nominal)} baselines nominales chargées.")
        except Exception as e:
            logger.warning(f"[BASELINE] Échec chargement nominal: {e}")

    # --- Update / récupération ---

    def update(self, metric_id: str, value: float, ts: Optional[float] = None) -> None:
        """Enregistre une nouvelle observation pour la métrique."""
        if ts is None:
            ts = time.time()
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if math.isnan(v) or math.isinf(v):
            return

        creneau = creneau_for_timestamp(ts)

        # Tracker circadien
        if metric_id not in self._trackers:
            self._trackers[metric_id] = {}
        if creneau not in self._trackers[metric_id]:
            self._trackers[metric_id][creneau] = PercentileTracker(
                capacity=CRENEAU_CAPACITY, min_samples=CRENEAU_MIN_SAMPLES_FULL
            )
        self._trackers[metric_id][creneau].record(v)

        # Buffer récent (pour dz/dt)
        if metric_id not in self._recent:
            self._recent[metric_id] = deque(maxlen=30)
        self._recent[metric_id].append((ts, v))

    def _apply_min_sigma(self, metric_id: str, sigma: float) -> float:
        """V14.7 : applique le plancher min_sigma déclaré dans le nominal.

        Empêche l'effondrement de variance qui produit l'hypocondrie
        statistique : si Prométhée consolide souvent (état sain), sigma
        empirique chute, et toute dette légèrement supérieure à la moyenne
        franchit z>=1.5, déclenchant l'alarme nociceptive sur un état
        parfaitement normal.
        """
        nominal = self._nominal.get(metric_id, {})
        min_sigma = nominal.get("min_sigma", 0.0)
        return max(sigma, min_sigma, 1e-6)

    def get_baseline(
        self, metric_id: str, ts: Optional[float] = None
    ) -> Tuple[float, float, int]:
        """Retourne (mu, sigma, n_obs) pour la métrique au créneau du timestamp.

        Stratégie :
        - n >= 30  : 100% empirique
        - 20 <= n < 30 : blend linéaire nominal/empirique
        - n < 20  : 100% nominal (ou défaut si pas de nominal)

        V14.7 : sigma final passe par _apply_min_sigma pour respecter le
        plancher déclaré dans symptomes_baseline.json (anti-hypocondrie).
        """
        if ts is None:
            ts = time.time()
        creneau = creneau_for_timestamp(ts)

        nominal = self._nominal.get(metric_id, {"mu": 0.0, "sigma": 1.0})
        mu_n = nominal["mu"]
        sigma_n = nominal["sigma"]

        tracker = self._trackers.get(metric_id, {}).get(creneau)
        if tracker is None or tracker.count() < CRENEAU_MIN_SAMPLES_BLEND:
            return (
                mu_n,
                self._apply_min_sigma(metric_id, sigma_n),
                tracker.count() if tracker else 0,
            )

        # Calcul empirique
        n = tracker.count()
        values = list(tracker._buffer)
        mu_e = sum(values) / n
        var_e = sum((v - mu_e) ** 2 for v in values) / n
        sigma_e = math.sqrt(var_e) if var_e > 0 else 1e-6

        if n >= CRENEAU_MIN_SAMPLES_FULL:
            return (mu_e, self._apply_min_sigma(metric_id, sigma_e), n)

        # Blend linéaire entre 20 et 30
        alpha = (n - CRENEAU_MIN_SAMPLES_BLEND) / (
            CRENEAU_MIN_SAMPLES_FULL - CRENEAU_MIN_SAMPLES_BLEND
        )
        mu_blend = (1 - alpha) * mu_n + alpha * mu_e
        sigma_blend = (1 - alpha) * sigma_n + alpha * sigma_e
        return (mu_blend, self._apply_min_sigma(metric_id, sigma_blend), n)

    def get_zscore(
        self, metric_id: str, value: float, ts: Optional[float] = None
    ) -> float:
        """Z-score circadien : (value - mu) / sigma au créneau de ts."""
        mu, sigma, _ = self.get_baseline(metric_id, ts)
        return (float(value) - mu) / sigma

    def get_derivative_zscore(
        self,
        metric_id: str,
        value: float,
        ts: Optional[float] = None,
        window_s: float = DERIVATIVE_WINDOW_S,
    ) -> float:
        """Dérivée du z-score (dz/dt) sur la fenêtre window_s.

        Si pas assez d'historique, retourne 0.0 (= métrique stable).
        """
        if ts is None:
            ts = time.time()

        recent = self._recent.get(metric_id)
        if not recent or len(recent) < 2:
            return 0.0

        z_now = self.get_zscore(metric_id, value, ts)

        # Cherche la valeur la plus proche de (ts - window_s)
        target_ts = ts - window_s
        best = None
        for past_ts, past_val in recent:
            if past_ts <= target_ts:
                best = (past_ts, past_val)
            else:
                break
        if best is None:
            # Pas de point assez vieux : prendre le plus ancien dispo
            best = recent[0]

        past_ts, past_val = best
        z_past = self.get_zscore(metric_id, past_val, past_ts)

        dt = max(ts - past_ts, 1e-3)
        # Normalise sur window_s pour que dz/dt soit homogène à un z-score
        return (z_now - z_past) * (window_s / dt)

    # --- Persistance ---

    def save(self) -> None:
        """Sauvegarde atomique de tous les trackers."""
        try:
            data = {
                "version": "1.0",
                "saved_at": time.time(),
                "trackers": {
                    metric_id: {
                        creneau: tracker.to_dict()
                        for creneau, tracker in by_creneau.items()
                    }
                    for metric_id, by_creneau in self._trackers.items()
                },
            }
            BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = BASELINE_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(str(tmp), str(BASELINE_FILE))
        except Exception as e:
            logger.warning(f"[BASELINE] Sauvegarde échouée: {e}")

    def _load_state(self) -> None:
        """Charge l'état persistant des trackers."""
        try:
            if not BASELINE_FILE.exists():
                return
            with open(BASELINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for metric_id, by_creneau in data.get("trackers", {}).items():
                self._trackers[metric_id] = {
                    creneau: PercentileTracker.from_dict(spec)
                    for creneau, spec in by_creneau.items()
                }
            logger.info(f"[BASELINE] État restauré ({len(self._trackers)} métriques).")
        except Exception as e:
            logger.warning(f"[BASELINE] Chargement échoué: {e}")

    # --- Introspection ---

    def get_stats(self, metric_id: str) -> Dict[str, int]:
        """Renvoie le n_obs par créneau pour une métrique."""
        by_creneau = self._trackers.get(metric_id, {})
        return {creneau: tracker.count() for creneau, tracker in by_creneau.items()}


# --- Singleton ---
baseline_tracker = BaselineTracker()
