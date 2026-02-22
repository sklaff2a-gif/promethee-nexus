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
from core.autonomy_engine import MAX_DAILY_ROUTINES as MAX_DAILY_ROUTINES_REF

# --- Humeur synthétique (déterministe, premier match gagne) ---
MOOD_MAP = [
    ("productif",  lambda sr, traits: sr > 0.85),
    ("fatigue",    lambda sr, traits: sr < 0.5),
    ("instable",   lambda sr, traits: sr < 0.6 and traits.get("survie", 50) > 65),
    ("curieux",    lambda sr, traits: traits.get("curiosite", 50) > 65 and sr > 0.7),
    ("anime",      lambda sr, traits: sr > 0.6 and _has_urgent_desires()),
    ("créatif",    lambda sr, traits: traits.get("creativite", 50) > 65 and sr > 0.7),
    ("prudent",    lambda sr, traits: traits.get("survie", 50) > 70),
    ("audacieux",  lambda sr, traits: traits.get("audace", 50) > 65 and sr > 0.6),
    ("équilibré",  lambda sr, traits: True),
]


def _has_urgent_desires() -> bool:
    """Retourne True si au moins une pulsion a une deprivation >= 75."""
    try:
        from core.desire_engine import desires
        return any(d.deprivation >= 75 for d in desires.drives.values())
    except Exception:
        return False


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
        self._council_aborted = 0
        # Lacunes de connaissances détectées
        self._knowledge_gaps: List[Dict[str, Any]] = []
        # Santé mémoire vectorielle (mise à jour via bus)
        self._memory_status = "unknown"
        self._memory_warnings: List[str] = []
        # Événements de personnalité (traits extrêmes détectés)
        self._personality_events: List[Dict[str, Any]] = []
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
        self._council_aborted = 0
        self._knowledge_gaps = []
        self._memory_status = "unknown"
        self._memory_warnings = []
        self._personality_events = []

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
        bus.subscribe("MISSION_FINISHED", self._on_agent_response)
        bus.subscribe("COUNCIL_END", self._on_council_end)
        bus.subscribe("CI_PIPELINE_RESULT", self._on_ci_result)
        bus.subscribe("MEMORY_HEALTH_ALERT", self._on_memory_alert)
        bus.subscribe("PSYCHE_UPDATE", self._on_psyche_update)
        bus.subscribe("OBJECTIVE_COMPLETED", self._on_objective_completed)
        bus.subscribe("OBJECTIVE_FAILED", self._on_objective_failed)

    async def _on_agent_response(self, event: dict):
        self._mission_count += 1
        status = event.get("status", "")
        if status == "success":
            self._mission_success += 1

    async def _on_council_end(self, event: dict):
        self._council_count += 1
        if event.get("status") == "consensus":
            self._council_consensus += 1
        elif event.get("status") == "aborted":
            self._council_aborted += 1

    async def _on_ci_result(self, event: dict):
        if event.get("success"):
            self._ci_pass += 1
        else:
            self._ci_fail += 1

    async def _on_memory_alert(self, event: dict):
        self._memory_status = event.get("status", "unknown")
        self._memory_warnings = event.get("warnings", [])

    async def _on_psyche_update(self, event):
        """Réagit aux changements de personnalité significatifs."""
        data = event if isinstance(event, dict) else {}
        avg = data.get("system_average", {})
        for trait, value in avg.items():
            if isinstance(value, (int, float)):
                if value > 80:
                    self._record_personality_event(f"trait_extreme_high:{trait}={value:.0f}")
                elif value < 25:
                    self._record_personality_event(f"trait_extreme_low:{trait}={value:.0f}")

    async def _on_objective_completed(self, event: dict):
        """Un objectif atteint enrichit le snapshot."""
        title = event.get("title", "?")
        logger.info(f"CONSCIENCE: Objectif atteint — {title}")
        self._mission_success += 1  # Compte comme un succès global

    async def _on_objective_failed(self, event: dict):
        """Un objectif expiré est noté."""
        title = event.get("title", "?")
        logger.info(f"CONSCIENCE: Objectif expiré — {title}")

    def _record_personality_event(self, event_str: str):
        """Enregistre un événement de personnalité (trait extrême détecté)."""
        logger.info(f"CONSCIENCE: Événement personnalité — {event_str}")
        self._personality_events.append({
            "event": event_str,
            "timestamp": datetime.now().isoformat(),
        })
        # Garder les 50 derniers événements
        if len(self._personality_events) > 50:
            self._personality_events = self._personality_events[-50:]
        self._save()

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

        # Budget Cloud (résumé RPM + quotas journaliers par modèle)
        cloud_used = 0
        cloud_max = 0
        try:
            from core.base_agent import BaseAgent
            from config import Config as _Cfg
            cloud_used = sum(BaseAgent._daily_model_calls.values())
            daily_limits = getattr(_Cfg, 'CLOUD_DAILY_LIMITS', {})
            cloud_max = sum(daily_limits.values()) if daily_limits else 500
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

        # Mémoire vectorielle
        memory_health = {"status": self._memory_status}
        if self._memory_warnings:
            memory_health["warnings"] = self._memory_warnings
        try:
            from core.vector_store import ChromaMemoryManager
            instances = ChromaMemoryManager._instances
            if instances:
                mgr = next(iter(instances.values()))
                memory_health["persistent"] = mgr.is_persistent
                memory_health["doc_count"] = sum(
                    mgr.count_documents(c) for c in mgr.collections
                )
        except Exception:
            pass

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
                "council_aborted": self._council_aborted,
                "ci_pass": self._ci_pass,
                "ci_fail": self._ci_fail,
                "dead_letters": bus.dead_letter_count,
            },
            "health": health,
            "memory": memory_health,
            "knowledge": {"journal_entries": journal_entries},
            "objectives": active_objectives,
            "trend": trend,
            "mood": mood,
        }

        # Stats spreading activation
        try:
            from core.spreading_activation import activation_engine
            snapshot["spreading_activation"] = activation_engine.get_stats()
        except Exception:
            pass

        # Pulsions (desirs)
        try:
            from core.desire_engine import desires
            snapshot["desires"] = desires.get_drive_summary()
        except Exception:
            pass

        # Stats cortex synaptique
        try:
            from core.synaptic_network import cortex
            snapshot["synaptic_network"] = cortex.get_stats()
        except Exception:
            pass

        # Stats cardiaques (coeur)
        try:
            from core.cardiac_engine import heart
            snapshot["cardiac"] = heart.get_stats()
        except Exception:
            pass

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
        dlq = perf.get("dead_letters", 0)
        if dlq > 0:
            parts.append(f"⚠️ Dead letters: {dlq}.")

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

        # 7. High council abort rate
        if self._council_count >= 3 and self._council_aborted > 0:
            abort_rate = self._council_aborted / self._council_count
            if abort_rate > 0.3:
                patterns.append({
                    "type": "high_council_abort",
                    "severity": "high",
                    "message": f"Taux d'abort Council élevé: {abort_rate:.0%} ({self._council_aborted}/{self._council_count}).",
                })

        return patterns

    # --- Scoring adaptatif ---

    def compute_adaptive_scoring(self, routine_history: List[Dict[str, Any]]) -> Dict[str, float]:
        """Analyse l'historique des routines et les snapshots pour retourner
        un dict {intent: float} d'ajustements de scoring adaptatifs.

        10 règles cumulatives :
        1. Routine bruyante : intent >50% low_quality sur ses 10 dernières → -3.0
        2. Councils stériles : <30% consensus sur les 5 derniers COUNCIL_DEBATE → -4.0
        3. Evolution bloquée : 0 success sur 15 derniers EXPANSION_CODE → -2.0 EXP, +1.0 GRIMOIRE
        4. Mode maintenance : error_streak >= 5 → -3.0 EXP/GRIMOIRE, +2.0 AUDIT/MEMORY
        5. Humeur fatigue/instable → -2.0 EXP, +1.0 AUDIT
        6. Humeur productif → +0.5 EXP, +0.5 GRIMOIRE
        7. Refactor stérile : >50% low_quality/error sur REFACTOR_RANDOM récents → -2.0
        8. Routine performante : >60% success sur 10 dernières → +2.0 (générique)
        9. Councils productifs : >=50% consensus → +1.0 COUNCIL_DEBATE
        10. Diversité Evolution : EXPANSION_CODE absent des 10 dernières routines → +2.0
        """
        adjustments: Dict[str, float] = {}

        def _add(intent: str, delta: float):
            adjustments[intent] = adjustments.get(intent, 0.0) + delta

        # --- Règle 1 : Routine bruyante ---
        intent_results: Dict[str, List[str]] = {}
        for h in routine_history:
            intent = h.get("intent", "")
            status = h.get("status", "")
            if intent and status:
                intent_results.setdefault(intent, []).append(status)

        for intent, statuses in intent_results.items():
            last_10 = statuses[-10:]
            if len(last_10) >= 4:  # Assez d'échantillons
                low_q_count = sum(1 for s in last_10 if s == "low_quality")
                if low_q_count / len(last_10) > 0.5:
                    _add(intent, -3.0)

        # --- Règle 2 : Councils stériles ---
        council_entries = [h for h in routine_history if h.get("intent") == "COUNCIL_DEBATE"]
        last_5_councils = council_entries[-5:]
        if len(last_5_councils) >= 3:
            consensus_count = sum(1 for h in last_5_councils if h.get("status") == "success")
            if consensus_count / len(last_5_councils) < 0.3:
                _add("COUNCIL_DEBATE", -4.0)

        # --- Règle 3 : Evolution bloquée ---
        if self.is_evolution_stuck(routine_history):
            _add("EXPANSION_CODE", -2.0)
            _add("GRIMOIRE_INVOKE", 1.0)

        # --- Règle 4 : Mode maintenance ---
        latest = self._snapshots[-1] if self._snapshots else None
        error_streak = 0
        if latest:
            error_streak = latest.get("performance", {}).get("error_streak", 0)

        if error_streak >= 5:
            _add("EXPANSION_CODE", -3.0)
            _add("GRIMOIRE_INVOKE", -3.0)
            _add("AUDIT_STRUCTURE", 2.0)
            _add("MEMORY_CLEANUP", 2.0)

        # --- Règle 5 & 6 : Humeur ---
        mood = latest.get("mood", "équilibré") if latest else "équilibré"

        if mood in ("fatigue", "instable"):
            _add("EXPANSION_CODE", -2.0)
            _add("AUDIT_STRUCTURE", 1.0)
        elif mood == "productif":
            _add("EXPANSION_CODE", 0.5)
            _add("GRIMOIRE_INVOKE", 0.5)
        elif mood == "curieux":
            _add("VEILLE_SILENCIEUSE", 1.5)
            _add("COUNCIL_DEBATE", 0.5)
        elif mood in ("créatif", "anime"):
            _add("EXPANSION_CODE", 1.0)
            _add("GRIMOIRE_INVOKE", 1.0)
        elif mood == "prudent":
            _add("SECURITY_AUDIT", 1.0)
            _add("AUDIT_STRUCTURE", 0.5)
            _add("EXPANSION_CODE", -1.0)

        # --- Règle 7 : Refactor stérile ---
        refactor_entries = [h for h in routine_history if h.get("intent") == "REFACTOR_RANDOM"]
        last_10_refactor = refactor_entries[-10:]
        if len(last_10_refactor) >= 4:
            bad_count = sum(1 for h in last_10_refactor if h.get("status") in ("low_quality", "error"))
            if bad_count / len(last_10_refactor) > 0.5:
                _add("REFACTOR_RANDOM", -2.0)

        # --- Règle 8 : Routine performante (anti-passivité) ---
        # Si une routine a >60% de succès sur ses 10 dernières, bonus
        for intent, statuses in intent_results.items():
            last_10 = statuses[-10:]
            if len(last_10) >= 4:
                success_count = sum(1 for s in last_10 if s == "success")
                if success_count / len(last_10) > 0.6:
                    _add(intent, 2.0)

        # --- Règle 9 : Councils productifs ---
        if len(last_5_councils) >= 3:
            consensus_count = sum(1 for h in last_5_councils if h.get("status") == "success")
            if consensus_count / len(last_5_councils) >= 0.5:
                _add("COUNCIL_DEBATE", 1.0)

        # --- Règle 10 : Diversité Evolution ---
        # Si EXPANSION_CODE n'a pas été exécuté récemment, forcer une rotation
        last_10_all = routine_history[-10:]
        expansion_in_last_10 = sum(1 for h in last_10_all if h.get("intent") == "EXPANSION_CODE")
        if expansion_in_last_10 == 0 and len(routine_history) >= 10:
            _add("EXPANSION_CODE", 2.0)

        # --- Règle 11 : Ponts créatifs non explorés (spreading activation) ---
        try:
            from core.spreading_activation import activation_engine
            unused_bridges = activation_engine.get_stats().get("unused_bridges", 0)
            if unused_bridges >= 3:
                _add("COUNCIL_DEBATE", 2.0)
        except Exception:
            pass

        # --- Règle 12 : Mode stratégique global ---
        strategic_mode = self.compute_strategic_mode()
        if strategic_mode == "survie":
            _add("AUDIT_STRUCTURE", 3.0)
            _add("EXPANSION_CODE", -5.0)
            _add("GRIMOIRE_INVOKE", -3.0)
        elif strategic_mode == "consolidation":
            _add("AUDIT_STRUCTURE", 1.0)
            _add("EXPANSION_CODE", -2.0)
            _add("COUNCIL_DEBATE", 1.0)
        elif strategic_mode == "exploration":
            _add("VEILLE_SILENCIEUSE", 1.0)
            _add("EXPANSION_CODE", 1.0)
            _add("COUNCIL_DEBATE", 1.0)
            _add("GRIMOIRE_INVOKE", 1.0)

        # --- Règle 13 : Méta-réflexion ---
        reflection = self.meta_reflect()
        if reflection.get("success_trend", 0) < -0.15:
            _add("AUDIT_STRUCTURE", 2.0)
            _add("EXPANSION_CODE", -2.0)
        if reflection.get("volatile_traits"):
            _add("COUNCIL_DEBATE", 1.0)

        # --- Règle 14 : Lacunes ouvertes ---
        open_gaps = self.get_open_gaps()
        if open_gaps and len(open_gaps) >= 2:
            _add("VEILLE_SILENCIEUSE", 1.5)
            _add("EXPANSION_CODE", 0.5)

        # --- Règle 15 : Patterns détectés ---
        patterns = self.detect_patterns()
        if patterns:
            pattern_types = {p["type"] for p in patterns}
            if "health_degraded" in pattern_types:
                _add("EXPANSION_CODE", -2.0)
                _add("AUDIT_STRUCTURE", 1.0)
            if "error_streak" in pattern_types:
                _add("EXPANSION_CODE", -1.0)
                _add("MEMORY_CLEANUP", 1.0)
            if "low_consensus" in pattern_types:
                _add("COUNCIL_DEBATE", -2.0)

        return adjustments

    def is_evolution_stuck(self, routine_history: List[Dict[str, Any]]) -> bool:
        """Retourne True si Evolution n'a rien déployé sur ses 15 dernières
        tentatives EXPANSION_CODE."""
        expansion_entries = [h for h in routine_history if h.get("intent") == "EXPANSION_CODE"]
        last_15 = expansion_entries[-15:]
        if len(last_15) < 5:
            return False  # Pas assez de données
        return not any(h.get("status") == "success" for h in last_15)

    # --- Knowledge Gaps (lacunes de connaissances) ---

    def record_knowledge_gap(self, topic: str, source_intent: str):
        """Enregistre une lacune de connaissance détectée."""
        # Pas de doublon sur le même topic
        for gap in self._knowledge_gaps:
            if gap["topic"] == topic:
                return
        self._knowledge_gaps.append({
            "topic": topic,
            "detected_at": datetime.now().isoformat(),
            "source_intent": source_intent,
            "learned": False,
            "learned_at": None,
        })
        self._save()
        logger.info(f"CONSCIENCE: Lacune enregistrée — {topic} (via {source_intent})")
        # Publier pour que DesireEngine réagisse (pulsion CURIOSITE/COMPREHENSION)
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(bus.publish("KNOWLEDGE_GAP_DETECTED", {
                "topic": topic, "source_intent": source_intent,
            }))
        except RuntimeError:
            pass  # Pas de boucle asyncio (tests sync)

    def mark_gap_learned(self, topic: str):
        """Marque une lacune comme comblée par un apprentissage ciblé."""
        for gap in self._knowledge_gaps:
            if gap["topic"] == topic and not gap["learned"]:
                gap["learned"] = True
                gap["learned_at"] = datetime.now().isoformat()
                self._save()
                logger.info(f"CONSCIENCE: Lacune comblée — {topic}")
                return

    def get_open_gaps(self) -> List[Dict[str, Any]]:
        """Retourne les lacunes non comblées."""
        return [g for g in self._knowledge_gaps if not g["learned"]]

    # --- Méta-réflexion ---

    def meta_reflect(self) -> dict:
        """Analyse les 10 derniers snapshots pour détecter des tendances. Zero LLM."""
        if len(self._snapshots) < 5:
            return {"insight": None, "success_trend": 0, "volatile_traits": [], "snapshot_count": len(self._snapshots)}

        recent = self._snapshots[-10:]

        # Tendance success_rate
        rates = [s.get("performance", {}).get("success_rate", 0) for s in recent]
        trend = rates[-1] - rates[0] if len(rates) >= 2 else 0

        # Volatilité des traits (écart-type)
        trait_values = {}
        for s in recent:
            for trait, val in s.get("traits", {}).get("average", {}).items():
                trait_values.setdefault(trait, []).append(val)

        volatile_traits = []
        for trait, vals in trait_values.items():
            if len(vals) >= 3:
                mean = sum(vals) / len(vals)
                std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
                if std > 5:
                    volatile_traits.append((trait, std))

        # Construire l'insight
        insight_parts = []
        if trend < -0.1:
            insight_parts.append(f"performance en baisse ({trend:+.2f})")
        elif trend > 0.1:
            insight_parts.append(f"performance en hausse ({trend:+.2f})")
        if volatile_traits:
            vt = ", ".join(f"{t}(std={s:.1f})" for t, s in volatile_traits)
            insight_parts.append(f"traits instables: {vt}")

        return {
            "insight": " | ".join(insight_parts) if insight_parts else None,
            "success_trend": trend,
            "volatile_traits": volatile_traits,
            "snapshot_count": len(recent),
        }

    def compute_strategic_mode(self) -> str:
        """Détermine le mode stratégique global. Zero LLM."""
        reflection = self.meta_reflect()
        latest = self._snapshots[-1] if self._snapshots else {}
        perf = latest.get("performance", {})
        health = latest.get("health", {})

        error_streak = perf.get("error_streak", 0)
        success_rate = perf.get("success_rate", 0.5)
        trend = reflection.get("success_trend", 0)

        # SURVIE : erreurs critiques, santé dégradée
        if error_streak >= 7 or health.get("verdict") == "NO_GO":
            return "survie"

        # CONSOLIDATION : performance en baisse, traits instables
        if trend < -0.15 or (error_streak >= 4 and success_rate < 0.5):
            return "consolidation"

        # EXPLORATION : performance stable/haute, curiosité dominante
        if success_rate > 0.75 and trend >= 0:
            return "exploration"

        # STANDARD : par défaut
        return "standard"

    # --- Mission existentielle ---

    def get_purpose_context(self) -> str:
        """Texte court injectable dans les décisions autonomes, adapté à l'état."""
        parts = []
        gaps = self.get_open_gaps()
        mood = "équilibré"
        if self._snapshots:
            mood = self._snapshots[-1].get("mood", "équilibré")

        if gaps:
            parts.append(f"[MISSION] J'ai {len(gaps)} lacune(s) à combler. Chaque échec est une opportunité d'apprendre.")
        elif mood == "productif":
            parts.append("[MISSION] Je suis en forme. C'est le moment d'explorer et de créer.")
        elif mood in ("fatigue", "instable"):
            parts.append("[MISSION] Je dois consolider mes acquis et me reposer sur ce que je sais.")
        else:
            parts.append("[MISSION] J'existe pour aider mon utilisateur. Chaque connaissance me rend plus utile.")

        # Pulsions dominantes
        try:
            from core.desire_engine import desires
            narrative = desires.get_dominant_narrative(top_n=1)
            if narrative:
                parts.append(f"[DESIR] {narrative}")
        except Exception:
            pass

        # Méta-réflexion
        reflection = self.meta_reflect()
        if reflection.get("insight"):
            parts.append(f"[META] {reflection['insight']}")

        # Patterns actifs
        patterns = self.detect_patterns()
        if patterns:
            pattern_names = [p["type"] for p in patterns[:3]]
            parts.append(f"[ALERTES] {', '.join(pattern_names)}")

        # Lacunes ouvertes
        open_gaps = self.get_open_gaps()
        if open_gaps:
            gap_topics = [g["topic"][:30] for g in open_gaps[:2]]
            parts.append(f"[LACUNES] {', '.join(gap_topics)}")

        # Pulsions frustrées
        try:
            from core.desire_engine import desires as _desires
            frustrated = [(n, d.frustration_streak) for n, d in _desires.drives.items() if d.frustration_streak >= 3]
            if frustrated:
                parts.append(f"[FRUSTRATIONS] {', '.join(f'{n}(x{s})' for n, s in frustrated)}")
        except Exception:
            pass

        return " ".join(parts)

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
            self._council_aborted = data.get("council_aborted", 0)
            self._knowledge_gaps = data.get("knowledge_gaps", [])
            self._personality_events = data.get("personality_events", [])
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
            "council_aborted": self._council_aborted,
            "knowledge_gaps": self._knowledge_gaps,
            "personality_events": self._personality_events,
        }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STATE_FILE)


# Singleton global
awareness = SelfAwarenessEngine()
