"""
meta_observer.py — Auto-Moniteur de Second Ordre (Phase D, 2026-04-16).

Observe les tendances metaboliques de Promethee sur des fenetres temporelles
et detecte les anomalies statistiques. Ne cree JAMAIS de goals directement
(preserverle libre arbitre du prefrontal). Agit par Inception Somatique :
  - Chemin 1 (corps) : injecte une emotion (inquietude) via cardiac_engine
    pour biaiser naturellement le scoring vers les routines diagnostiques.
  - Chemin 2 (narration) : publie META_ANOMALY_DETECTED sur le bus pour
    que l'Inner Voice capture l'anomalie et que l'EVENING_REFLECTION l'inclue
    dans la memoire autobiographique.

Inspirations :
  - Marqueurs somatiques (Damasio) : le corps "sait" avant l'esprit
  - Allostasie (Sterling & Eyer) : ajuster les seuils par anticipation
  - Interoception : un organe qui observe les autres organes

Metriques observees (V1 — extensible) :
  1. Ratio Hebbian R/E (renforcements vs extinctions)
  2. Spectre emotionnel (diversite des emotions cardiaques)
  3. Taux de succes des goals par drive (via prefrontal.stats)

Architecture :
  - Tick toutes les 4h (branche sur AUTONOMY_HEARTBEAT ou appel periodique)
  - Stocke les snapshots dans memory/meta_metrics.json
  - Compare snapshot courant vs moyenne mobile 72h
  - Seuil d'alerte : delta > 2 sigma OU pattern continu (ex: 0% succes)
"""

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("meta_observer")

# ─── Constantes ──────────────────────────────────────────────────────────

TICK_INTERVAL_SECONDS = 4 * 3600    # 4 heures entre chaque observation
HISTORY_WINDOW_HOURS = 72           # Moyenne mobile sur 72h
MAX_SNAPSHOTS = 100                 # Garder les 100 derniers snapshots
ANOMALY_SIGMA = 2.0                 # Seuil de detection en ecarts-types
EXTINCTION_DOMINANCE_THRESHOLD = 0.80  # Si >80% d'extinctions → anomalie
MIN_EVENTS_FOR_STATS = 3           # Minimum d'events pour calculer un ratio

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "meta_metrics.json"
)


# ─── Snapshot ────────────────────────────────────────────────────────────

def _take_snapshot() -> Dict[str, Any]:
    """Capture un cliche compresse du metabolisme actuel."""
    snapshot = {
        "timestamp": time.time(),
        "hebbian_reinforcements": 0,
        "hebbian_extinctions": 0,
        "hebbian_ratio_r": 0.0,  # ratio R/(R+E), 1.0 = 100% renforcement
        "emotion_dominant": "unknown",
        "emotion_diversity": 0.0,  # 0.0 = mono-emotion, 1.0 = uniforme
        "goals_completed": 0,
        "goals_abandoned": 0,
        "goal_success_rate": 0.0,
    }

    # 1. Hebbian stats
    try:
        from core.synaptic_network import cortex
        stats = cortex.stats if hasattr(cortex, "stats") else {}
        r = stats.get("hebbian_causal_reinforcements", 0)
        e = stats.get("hebbian_causal_extinctions", 0)
        snapshot["hebbian_reinforcements"] = r
        snapshot["hebbian_extinctions"] = e
        total = r + e
        snapshot["hebbian_ratio_r"] = round(r / total, 3) if total > 0 else 0.5
    except Exception:
        pass

    # 2. Emotion
    try:
        from core.cardiac_engine import heart
        snapshot["emotion_dominant"] = getattr(heart, "current_emotion", "unknown")
        # Diversite : compter les transitions recentes
        transition_count = getattr(heart, "_transition_count", 0)
        total_stimuli = getattr(heart, "_total_stimuli", 1)
        snapshot["emotion_diversity"] = round(
            min(1.0, transition_count / max(1, total_stimuli) * 10), 3
        )
    except Exception:
        pass

    # 3. Goals
    try:
        from core.prefrontal import prefrontal
        stats = prefrontal.stats if hasattr(prefrontal, "stats") else {}
        completed = stats.get("goals_completed", 0)
        abandoned = stats.get("goals_abandoned", 0)
        snapshot["goals_completed"] = completed
        snapshot["goals_abandoned"] = abandoned
        total = completed + abandoned
        snapshot["goal_success_rate"] = round(completed / total, 3) if total > 0 else 0.5
    except Exception:
        pass

    return snapshot


# ─── Detection d'anomalies ───────────────────────────────────────────────

def _mean_std(values: List[float]):
    """Moyenne et ecart-type d'une liste."""
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return mean, math.sqrt(variance)


def _detect_anomalies(
    current: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compare le snapshot courant a l'historique et retourne les anomalies."""
    anomalies = []
    if len(history) < MIN_EVENTS_FOR_STATS:
        return anomalies

    # 1. Hebbian R/E ratio
    hist_ratios = [s["hebbian_ratio_r"] for s in history if "hebbian_ratio_r" in s]
    if hist_ratios:
        mean_r, std_r = _mean_std(hist_ratios)
        current_r = current["hebbian_ratio_r"]
        total_events = current["hebbian_reinforcements"] + current["hebbian_extinctions"]

        if total_events >= MIN_EVENTS_FOR_STATS:
            # Anomalie par sigma
            if std_r > 0 and abs(current_r - mean_r) > ANOMALY_SIGMA * std_r:
                anomalies.append({
                    "type": "hebbian_ratio_drift",
                    "metric": "hebbian_ratio_r",
                    "current": current_r,
                    "mean": round(mean_r, 3),
                    "std": round(std_r, 3),
                    "delta_sigma": round((current_r - mean_r) / std_r, 2),
                    "severity": min(1.0, abs(current_r - mean_r) / max(0.01, std_r) / 3),
                    "description": (
                        f"Ratio Hebbian R/E a derive : {current_r:.1%} "
                        f"(moyenne={mean_r:.1%}, sigma={std_r:.3f}). "
                        f"{current['hebbian_reinforcements']}R / "
                        f"{current['hebbian_extinctions']}E."
                    ),
                })

            # Anomalie par dominance extinction
            if current_r < (1 - EXTINCTION_DOMINANCE_THRESHOLD) and total_events >= 3:
                anomalies.append({
                    "type": "extinction_dominance",
                    "metric": "hebbian_ratio_r",
                    "current": current_r,
                    "severity": 0.8,
                    "description": (
                        f"Extinctions dominent a {1 - current_r:.0%} "
                        f"({current['hebbian_extinctions']}E vs "
                        f"{current['hebbian_reinforcements']}R). "
                        f"Les routines sont punies plus qu'elles ne sont recompensees."
                    ),
                })

    # 2. Goal success rate collapse
    hist_success = [s["goal_success_rate"] for s in history if "goal_success_rate" in s]
    if hist_success:
        mean_s, std_s = _mean_std(hist_success)
        current_s = current["goal_success_rate"]
        total_goals = current["goals_completed"] + current["goals_abandoned"]

        if total_goals >= MIN_EVENTS_FOR_STATS and current_s < 0.1 and mean_s > 0.2:
            anomalies.append({
                "type": "goal_success_collapse",
                "metric": "goal_success_rate",
                "current": current_s,
                "mean": round(mean_s, 3),
                "severity": 0.9,
                "description": (
                    f"Taux de succes des goals effondre : {current_s:.0%} "
                    f"(moyenne historique={mean_s:.0%}). "
                    f"{current['goals_completed']} completes vs "
                    f"{current['goals_abandoned']} abandonnes."
                ),
            })

    # 3. Emotion diversity collapse
    hist_div = [s["emotion_diversity"] for s in history if "emotion_diversity" in s]
    if hist_div:
        mean_d, _ = _mean_std(hist_div)
        current_d = current["emotion_diversity"]
        if current_d < 0.1 and mean_d > 0.2:
            anomalies.append({
                "type": "emotion_monotony",
                "metric": "emotion_diversity",
                "current": current_d,
                "mean": round(mean_d, 3),
                "severity": 0.6,
                "description": (
                    f"Diversite emotionnelle effondree : {current_d:.2f} "
                    f"(moyenne={mean_d:.2f}). Emotion dominante : "
                    f"{current['emotion_dominant']}."
                ),
            })

    return anomalies


# ─── Persistance ─────────────────────────────────────────────────────────

def _load_history() -> List[Dict[str, Any]]:
    """Charge l'historique des snapshots."""
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("snapshots", [])
    except Exception as e:
        logger.warning(f"META_OBSERVER: erreur chargement historique: {e}")
    return []


def _save_history(snapshots: List[Dict[str, Any]]):
    """Sauvegarde l'historique (capped a MAX_SNAPSHOTS)."""
    try:
        capped = snapshots[-MAX_SNAPSHOTS:]
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"snapshots": capped, "saved_at": time.time()}, f, indent=1)
    except Exception as e:
        logger.warning(f"META_OBSERVER: erreur sauvegarde: {e}")


# ─── Inception Somatique ─────────────────────────────────────────────────

def _trigger_somatic_inception(anomaly: Dict[str, Any]):
    """Chemin 1 : injecte une emotion dans le coeur (inquietude).

    Le cardiac_engine shift vers 'inquietude', ce qui biaise naturellement
    le scoring vers les routines diagnostiques (AUDIT_STRUCTURE, SECURITY_AUDIT)
    via EMOTION_BONUS_DATA. Aucun goal n'est cree — le prefrontal decidera
    seul s'il agit.
    """
    try:
        from core.cardiac_engine import heart
        severity = anomaly.get("severity", 0.5)
        heart.react("meta_anomaly", context={
            "anomaly_type": anomaly.get("type", "unknown"),
            "severity": severity,
        })
        logger.info(
            f"[META_OBSERVER] Inception somatique: inquietude injectee "
            f"(type={anomaly['type']}, severity={severity:.2f})"
        )
    except Exception as e:
        logger.warning(f"META_OBSERVER: inception somatique echec: {e}")


async def _trigger_narrative_inception(anomaly: Dict[str, Any]):
    """Chemin 2 : publie META_ANOMALY_DETECTED sur le bus.

    L'Inner Voice le captera comme pensee source='meta' haute saillance.
    L'EVENING_REFLECTION l'inclura dans la memoire autobiographique.
    L'autonomy_engine l'injectera dans le purpose_context de la prochaine routine.
    """
    try:
        from core.event_bus.bus import bus
        await bus.publish("META_ANOMALY_DETECTED", {
            "type": anomaly["type"],
            "metric": anomaly.get("metric", ""),
            "description": anomaly["description"],
            "severity": anomaly.get("severity", 0.5),
            "current_value": anomaly.get("current"),
            "historical_mean": anomaly.get("mean"),
            "timestamp": time.time(),
        })
        logger.info(
            f"[META_OBSERVER] Narrative inception: META_ANOMALY_DETECTED "
            f"publie (type={anomaly['type']})"
        )
    except Exception as e:
        logger.warning(f"META_OBSERVER: narrative inception echec: {e}")


# ─── Tick principal ──────────────────────────────────────────────────────

class MetaObserver:
    """Singleton — Auto-Moniteur de Second Ordre."""

    _instance: Optional["MetaObserver"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._last_tick: float = 0.0
        self._last_anomalies: List[Dict[str, Any]] = []
        self._snapshots: List[Dict[str, Any]] = _load_history()
        logger.info(
            f"META_OBSERVER: initialise ({len(self._snapshots)} snapshots historiques)"
        )

    async def tick(self) -> List[Dict[str, Any]]:
        """Appele periodiquement (par autonomy_engine ou brain_vm).

        Retourne la liste des anomalies detectees (vide si RAS).
        """
        now = time.time()
        if now - self._last_tick < TICK_INTERVAL_SECONDS:
            return []
        self._last_tick = now

        # 1. Capturer le snapshot
        snapshot = _take_snapshot()
        logger.info(
            f"[META_OBSERVER] Snapshot: hebbR={snapshot['hebbian_ratio_r']:.1%} "
            f"({snapshot['hebbian_reinforcements']}R/{snapshot['hebbian_extinctions']}E), "
            f"goals={snapshot['goal_success_rate']:.0%} "
            f"({snapshot['goals_completed']}C/{snapshot['goals_abandoned']}A), "
            f"emo={snapshot['emotion_dominant']} div={snapshot['emotion_diversity']:.2f}"
        )

        # 2. Detecter les anomalies
        # Fenetre historique : 72h
        cutoff = now - (HISTORY_WINDOW_HOURS * 3600)
        recent_history = [s for s in self._snapshots if s.get("timestamp", 0) > cutoff]
        anomalies = _detect_anomalies(snapshot, recent_history)

        # 3. Sauvegarder
        self._snapshots.append(snapshot)
        _save_history(self._snapshots)

        # 4. Inception somatique + narrative pour chaque anomalie
        if anomalies:
            self._last_anomalies = anomalies
            for anomaly in anomalies:
                _trigger_somatic_inception(anomaly)
                await _trigger_narrative_inception(anomaly)
            logger.warning(
                f"[META_OBSERVER] {len(anomalies)} anomalie(s) detectee(s): "
                + ", ".join(a["type"] for a in anomalies)
            )
        else:
            self._last_anomalies = []
            logger.debug("[META_OBSERVER] RAS — metabolisme dans les normes.")

        return anomalies

    def get_last_anomalies(self) -> List[Dict[str, Any]]:
        """Accesseur pour l'API ou l'instrumentation."""
        return self._last_anomalies

    def get_status(self) -> Dict[str, Any]:
        """Resume pour le dashboard."""
        return {
            "snapshots_count": len(self._snapshots),
            "last_tick": self._last_tick,
            "last_anomalies": [a["type"] for a in self._last_anomalies],
            "anomaly_count_total": sum(
                1 for s in self._snapshots if s.get("anomalies")
            ),
        }


# Singleton global
observer = MetaObserver()
