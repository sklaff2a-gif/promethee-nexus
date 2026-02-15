# core/self_awareness.py — Conscience de Soi de Prométhée
# Synthétise les données dispersées (PSYCHE, autonomie, journal, health, cloud)
# en une introspection cohérente injectable dans les débats Council.

import json
import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from core.event_bus.bus import bus

logger = logging.getLogger("SelfAwareness")

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "self_awareness.json"
)

MAX_SNAPSHOTS = 50
MAX_DAILY_ROUTINES_REF = 20  # Copie locale pour éviter import circulaire

# --- Humeur synthétique (déterministe, premier match gagne) ---
MOOD_MAP = [
    ("productif",  lambda sr, traits: sr > 0.85),
    ("fatigue",    lambda sr, traits: sr < 0.5),
    ("instable",   lambda sr, traits: sr < 0.6 and traits.get("survie", 50) > 65),
    ("curieux",    lambda sr, traits: traits.get("curiosite", 50) > 65 and sr > 0.7),
    ("créatif",    lambda sr, traits: traits.get("creativite", 50) > 65 and sr > 0.7),
    ("prudent",    lambda sr, traits: traits.get("survie", 50) > 70),
    ("audacieux",  lambda sr, traits: traits.get("audace", 50) > 65 and sr > 0.6),
    ("équilibré",  lambda sr, traits: True),
]


def _compute_mood(success_rate: float, traits_avg: Dict[str, float]) -> str:
    for mood_name, condition in MOOD_MAP:
        try:
            if condition(success_rate, traits_avg):
                return mood_name
        except Exception:
            continue
    return "équilibré"


class SelfAwarenessEngine:
    """Singleton — moteur de conscience de soi pour Prométhée."""

    _instance: Optional["SelfAwarenessEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._snapshots: List[Dict[str, Any]] = []
        self._subscribed = False
        # Compteurs incrémentaux (collecte passive via bus)
        self._mission_count = 0
        self._mission_success = 0
        self._council_count = 0
        self._council_consensus = 0
        self._ci_pass = 0
        self._ci_fail = 0
        self._load()

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux événements bus pour la collecte passive."""
        self._subscribe_events()
        logger.info("CONSCIENCE: Moteur de conscience de soi actif.")

    def reset(self):
        self._snapshots = []
        self._subscribed = False
        self._initialized = False
        self._mission_count = 0
        self._mission_success = 0
        self._council_count = 0
        self._council_consensus = 0
        self._ci_pass = 0
        self._ci_fail = 0

    @classmethod
    def reset_singleton(cls):
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # --- Souscriptions Event Bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        bus.subscribe("MISSION_COMPLETE", self._on_agent_response)
        bus.subscribe("COUNCIL_END", self._on_council_end)
        bus.subscribe("CI_PIPELINE_RESULT", self._on_ci_result)

    async def _on_agent_response(self, event: dict):
        self._mission_count += 1
        status = event.get("status", "")
        if status == "success":
            self._mission_success += 1

    async def _on_council_end(self, event: dict):
        self._council_count += 1
        if event.get("status") == "consensus":
            self._council_consensus += 1

    async def _on_ci_result(self, event: dict):
        if event.get("success"):
            self._ci_pass += 1
        else:
            self._ci_fail += 1

    # --- Snapshot ---

    def generate_snapshot(self) -> Dict[str, Any]:
        """Portrait complet du système à l'instant T."""
        # Traits PSYCHE (import local)
        traits_avg = {}
        dominant = {"name": "inconnu", "value": 50.0}
        weakest = {"name": "inconnu", "value": 50.0}
        try:
            from core.psyche import psyche
            traits_avg = psyche.get_system_average()
            if traits_avg:
                d = max(traits_avg.items(), key=lambda x: x[1])
                dominant = {"name": d[0], "value": d[1]}
                w = min(traits_avg.items(), key=lambda x: x[1])
                weakest = {"name": w[0], "value": w[1]}
        except Exception:
            pass

        # Autonomie (import local)
        error_streak = 0
        daily_count = 0
        total_routines = 0
        last_health_check = None
        try:
            from core.autonomy_engine import autonomy
            error_streak = autonomy.error_streak
            daily_count = autonomy.daily_count
            total_routines = autonomy.total_routines_executed
            last_health_check = autonomy.last_health_check
        except Exception:
            pass

        # Budget Cloud
        cloud_used = 0
        cloud_max = 100
        try:
            from core.base_agent import BaseAgent
            cloud_used = BaseAgent._cloud_call_count
            cloud_max = BaseAgent.MAX_CLOUD_CALLS_PER_HOUR
        except Exception:
            pass

        # Journal stratégique
        journal_entries = 0
        try:
            from core.strategic_journal import journal as strat_journal
            journal_entries = strat_journal.entry_count()
        except Exception:
            pass

        # Health
        health = {"verdict": "UNKNOWN", "cpu_percent": 0, "ram_percent": 0, "ollama_alive": False}
        if last_health_check and isinstance(last_health_check, dict):
            health = {
                "verdict": last_health_check.get("verdict", "UNKNOWN"),
                "cpu_percent": last_health_check.get("cpu_percent", 0),
                "ram_percent": last_health_check.get("ram_percent", 0),
                "ollama_alive": last_health_check.get("ollama_alive", False),
            }

        # Performance
        total_missions = self._mission_count
        success_rate = (self._mission_success / total_missions) if total_missions > 0 else 1.0
        council_consensus_rate = (
            self._council_consensus / self._council_count
        ) if self._council_count > 0 else 0.0

        # Objectifs actifs (import local)
        active_objectives = []
        try:
            from core.objectives_engine import objectives as obj_engine
            active_objectives = [
                {"id": o["id"], "title": o["title"], "progress": o["progress"]}
                for o in obj_engine.get_active_objectives()
            ]
        except Exception:
            pass

        # Tendances (delta par rapport au snapshot précédent)
        trend = self._compute_trend(traits_avg)

        # Humeur
        mood = _compute_mood(success_rate, traits_avg)

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "traits": {
                "average": traits_avg,
                "dominant": dominant,
                "weakest": weakest,
            },
            "performance": {
                "success_rate": round(success_rate, 2),
                "mission_count": total_missions,
                "error_streak": error_streak,
                "daily_routines": daily_count,
                "total_routines": total_routines,
                "cloud_budget_used": cloud_used,
                "cloud_budget_max": cloud_max,
                "council_count": self._council_count,
                "council_consensus_rate": round(council_consensus_rate, 2),
                "ci_pass": self._ci_pass,
                "ci_fail": self._ci_fail,
            },
            "health": health,
            "knowledge": {"journal_entries": journal_entries},
            "objectives": active_objectives,
            "trend": trend,
            "mood": mood,
        }

        self._snapshots.append(snapshot)
        if len(self._snapshots) > MAX_SNAPSHOTS:
            self._snapshots = self._snapshots[-MAX_SNAPSHOTS:]

        self._save()
        logger.info(f"CONSCIENCE: Snapshot généré (humeur={mood}, perf={success_rate:.0%})")
        return snapshot

    def _compute_trend(self, current_avg: Dict[str, float]) -> Dict[str, Any]:
        """Compare les traits actuels au snapshot précédent."""
        if not self._snapshots or not current_avg:
            return {"status": "initial", "deltas": {}, "rising": [], "falling": []}

        prev = self._snapshots[-1]
        prev_avg = prev.get("traits", {}).get("average", {})
        if not prev_avg:
            return {"status": "initial", "deltas": {}, "rising": [], "falling": []}

        deltas = {}
        rising = []
        falling = []
        for trait, val in current_avg.items():
            prev_val = prev_avg.get(trait, val)
            delta = round(val - prev_val, 2)
            deltas[trait] = delta
            if delta > 0.5:
                rising.append(trait)
            elif delta < -0.5:
                falling.append(trait)

        status = "stable"
        if rising or falling:
            status = "shifting"

        return {"status": status, "deltas": deltas, "rising": rising, "falling": falling}

    # --- Contexte injectable ---

    def get_self_context(self, max_chars: int = 500) -> str:
        """Texte court injectable dans les prompts Council."""
        if not self._snapshots:
            return ""

        snap = self._snapshots[-1]
        traits = snap.get("traits", {})
        perf = snap.get("performance", {})
        health = snap.get("health", {})
        trend = snap.get("trend", {})
        mood = snap.get("mood", "inconnu")

        dominant = traits.get("dominant", {})
        weakest = traits.get("weakest", {})

        rising = trend.get("rising", [])
        falling = trend.get("falling", [])

        parts = [
            f"[CONSCIENCE] Humeur: {mood}.",
            f"Trait dominant: {dominant.get('name', '?')} ({dominant.get('value', 0):.0f}/100),",
            f"trait faible: {weakest.get('name', '?')} ({weakest.get('value', 0):.0f}/100).",
            f"Perf: {perf.get('success_rate', 0):.0%} succes,",
            f"erreurs consec: {perf.get('error_streak', 0)},",
            f"routines jour: {perf.get('daily_routines', 0)}/{MAX_DAILY_ROUTINES_REF}.",
            f"Sante: {health.get('verdict', '?')} (CPU {health.get('cpu_percent', 0)}%, RAM {health.get('ram_percent', 0)}%).",
        ]
        if rising:
            parts.append(f"Traits en hausse: {', '.join(rising)}.")
        if falling:
            parts.append(f"Traits en baisse: {', '.join(falling)}.")

        try:
            from core.objectives_engine import objectives as obj_engine
            active = obj_engine.get_active_objectives()
            if active:
                obj_names = [f"{o['title']} ({o['progress']:.0%})" for o in active[:3]]
                parts.append(f"Objectifs: {', '.join(obj_names)}.")
        except Exception:
            pass

        text = " ".join(parts)
        return text[:max_chars]

    # --- Détection de patterns ---

    def detect_patterns(self) -> List[Dict[str, Any]]:
        """Détecte tendances et alertes à partir des snapshots."""
        patterns = []
        if not self._snapshots:
            return patterns

        latest = self._snapshots[-1]
        perf = latest.get("performance", {})

        # 1. Error streak
        if perf.get("error_streak", 0) >= 3:
            patterns.append({
                "type": "error_streak",
                "severity": "high",
                "message": f"Série de {perf['error_streak']} erreurs consécutives.",
            })

        # 2. Low success rate
        if perf.get("mission_count", 0) >= 5 and perf.get("success_rate", 1.0) < 0.6:
            patterns.append({
                "type": "low_success_rate",
                "severity": "medium",
                "message": f"Taux de succès bas: {perf['success_rate']:.0%}.",
            })

        # 3. Trait rising/falling (3+ snapshots de hausse/baisse constante)
        if len(self._snapshots) >= 3:
            recent = self._snapshots[-3:]
            for trait in ("curiosite", "creativite", "audace", "savoir", "survie", "respect"):
                values = []
                for s in recent:
                    avg = s.get("traits", {}).get("average", {})
                    values.append(avg.get(trait, 50.0))
                if len(values) == 3:
                    if values[0] < values[1] < values[2]:
                        patterns.append({
                            "type": "trait_rising",
                            "severity": "info",
                            "message": f"Trait '{trait}' en hausse constante ({values[0]:.1f} → {values[2]:.1f}).",
                        })
                    elif values[0] > values[1] > values[2]:
                        patterns.append({
                            "type": "trait_falling",
                            "severity": "info",
                            "message": f"Trait '{trait}' en baisse constante ({values[0]:.1f} → {values[2]:.1f}).",
                        })

        # 4. High success rate (pattern positif)
        if perf.get("mission_count", 0) >= 10 and perf.get("success_rate", 0) > 0.85:
            patterns.append({
                "type": "high_success_rate",
                "severity": "info",
                "message": f"Taux de succes excellent: {perf['success_rate']:.0%}.",
            })

        # 5. Cloud budget critical
        cloud_used = perf.get("cloud_budget_used", 0)
        cloud_max = perf.get("cloud_budget_max", 100)
        if cloud_max > 0 and cloud_used >= cloud_max * 0.9:
            patterns.append({
                "type": "cloud_budget_critical",
                "severity": "high",
                "message": f"Budget Cloud critique: {cloud_used}/{cloud_max} appels utilisés.",
            })

        # 5. Health degraded
        health = latest.get("health", {})
        verdict = health.get("verdict", "GO")
        if verdict == "NO_GO":
            patterns.append({
                "type": "health_degraded",
                "severity": "high",
                "message": "Santé système: NO_GO.",
            })
        elif verdict == "DEGRADED":
            patterns.append({
                "type": "health_degraded",
                "severity": "medium",
                "message": "Santé système: DEGRADED.",
            })

        # 6. Low consensus rate
        if self._council_count >= 3 and perf.get("council_consensus_rate", 1.0) < 0.4:
            patterns.append({
                "type": "low_consensus",
                "severity": "medium",
                "message": f"Taux de consensus Council bas: {perf['council_consensus_rate']:.0%} sur {self._council_count} débats.",
            })

        return patterns

    # --- Accesseurs ---

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        return self._snapshots[-1] if self._snapshots else None

    def get_all_snapshots(self) -> List[Dict[str, Any]]:
        return list(self._snapshots)

    # --- Persistance ---

    def _load(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._snapshots = data.get("snapshots", [])
            self._mission_count = data.get("mission_count", 0)
            self._mission_success = data.get("mission_success", 0)
            self._council_count = data.get("council_count", 0)
            self._council_consensus = data.get("council_consensus", 0)
            self._ci_pass = data.get("ci_pass", 0)
            self._ci_fail = data.get("ci_fail", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        data = {
            "version": "1.0",
            "snapshots": self._snapshots,
            "mission_count": self._mission_count,
            "mission_success": self._mission_success,
            "council_count": self._council_count,
            "council_consensus": self._council_consensus,
            "ci_pass": self._ci_pass,
            "ci_fail": self._ci_fail,
        }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)


# Singleton global
awareness = SelfAwarenessEngine()
