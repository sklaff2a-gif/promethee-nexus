# core/experience_registry.py — Registre d'expériences Evolution
# Enregistre chaque tentative du pipeline Evolution avec son contexte,
# et fournit un résumé des échecs passés pour enrichir les prompts.

import json
import os
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional, List, Dict, Any

logger = logging.getLogger("ExperienceRegistry")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REGISTRY_FILE = os.path.join(PROJECT_ROOT, "memory", "experience_registry.json")


@dataclass
class Experience:
    """Une tentative d'évolution enregistrée."""
    id: str                         # "EXP-{timestamp}-{spec_id}"
    spec_id: str                    # "PERF-001"
    spec_name: str                  # "Cache LRU Router"
    attempt_number: int             # 1, 2, 3...
    timestamp: str                  # ISO8601

    # Résultat
    phase_reached: str              # "phase1"|"phase2"|...|"phase5"
    outcome: str                    # "failed"|"deployed"|"confirmed"|"rolled_back"
    failure_reason: str             # "" si success

    # Détails techniques
    code_source: str                # "cloud"|"local"|"cloud+retry"
    code_length: int                # chars du code généré (0 si pas de code)
    alien_imports: list = field(default_factory=list)
    syntax_error: str = ""
    truncation_ratio: float = 0.0
    architect_verdict: str = ""     # "success"|"rejected"|""

    # Contexte système
    system_mood: str = ""
    success_rate_at_attempt: float = 1.0
    error_streak_at_attempt: int = 0

    # Feedback post-deploy (ajouté plus tard)
    post_deploy_verdict: str = ""   # ""|"improved"|"neutral"|"degraded"
    post_deploy_delta: float = 0.0


class ExperienceRegistry:
    """Singleton — registre persistant des expériences Evolution (FIFO 200)."""

    _instance: Optional["ExperienceRegistry"] = None
    MAX_EXPERIENCES = 200

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._experiences: List[Dict[str, Any]] = []
        self._load()

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilisé par les tests)."""
        cls._instance = None

    # --- Enregistrement ---

    def record(self, experience: Experience):
        """Ajoute une expérience au registre (FIFO 200)."""
        self._experiences.append(asdict(experience))
        if len(self._experiences) > self.MAX_EXPERIENCES:
            self._experiences = self._experiences[-self.MAX_EXPERIENCES:]
        self._save()

        # Publier l'événement (fire-and-forget)
        try:
            import asyncio
            from core.event_bus.bus import bus
            loop = asyncio.get_running_loop()
            loop.create_task(bus.publish("EXPERIENCE_RECORDED", {
                "spec_id": experience.spec_id,
                "phase_reached": experience.phase_reached,
                "outcome": experience.outcome,
                "failure_reason": experience.failure_reason[:80],
            }))
        except Exception:
            pass

    # --- Consultation ---

    def get_spec_history(self, spec_id: str) -> List[Dict[str, Any]]:
        """Retourne toutes les expériences pour une spec donnée, triées par date."""
        return [e for e in self._experiences if e["spec_id"] == spec_id]

    def get_failure_summary(self, spec_id: str) -> str:
        """Résumé textuel des échecs passés pour enrichir le prompt Evolution."""
        history = self.get_spec_history(spec_id)
        if not history:
            return ""
        failures = [e for e in history if e["outcome"] == "failed"]
        if not failures:
            return ""
        lines = [f"HISTORIQUE ({len(failures)} échec(s) passé(s)) :"]
        for f in failures[-3:]:  # 3 derniers échecs max
            lines.append(
                f"- Phase {f['phase_reached']}: {f['failure_reason'][:100]}"
            )
        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques globales du registre."""
        total = len(self._experiences)
        by_outcome: Dict[str, int] = {}
        by_phase: Dict[str, int] = {}
        for e in self._experiences:
            by_outcome[e["outcome"]] = by_outcome.get(e["outcome"], 0) + 1
            if e["outcome"] == "failed":
                by_phase[e["phase_reached"]] = by_phase.get(e["phase_reached"], 0) + 1
        return {
            "total": total,
            "by_outcome": by_outcome,
            "failure_phases": by_phase,
        }

    def get_learning_insights(self) -> List[Dict[str, Any]]:
        """Analyse les patterns récurrents dans les échecs."""
        insights: List[Dict[str, Any]] = []

        # Pattern 1: même spec échoue toujours à la même phase
        spec_phases: Dict[str, list] = {}
        for e in self._experiences:
            if e["outcome"] == "failed":
                spec_phases.setdefault(e["spec_id"], []).append(e["phase_reached"])
        for sid, phases in spec_phases.items():
            if len(phases) >= 2:
                from collections import Counter
                most_common = Counter(phases).most_common(1)[0]
                if most_common[1] >= 2:
                    insights.append({
                        "type": "repeated_phase_failure",
                        "spec_id": sid,
                        "phase": most_common[0],
                        "count": most_common[1],
                    })

        # Pattern 2: alien imports récurrents
        alien_counts: Dict[str, int] = {}
        for e in self._experiences:
            for alien in e.get("alien_imports", []):
                alien_counts[alien] = alien_counts.get(alien, 0) + 1
        for alien, count in alien_counts.items():
            if count >= 3:
                insights.append({
                    "type": "recurring_alien",
                    "import": alien,
                    "count": count,
                })

        return insights

    # --- Persistance ---

    def _load(self):
        """Charge le registre depuis le JSON."""
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._experiences = data if isinstance(data, list) else data.get("experiences", [])
        except (FileNotFoundError, json.JSONDecodeError):
            self._experiences = []

    def _save(self):
        """Persiste le registre dans le JSON (écriture atomique)."""
        os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
        tmp_path = REGISTRY_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._experiences, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, REGISTRY_FILE)
        except Exception as e:
            logger.warning(f"REGISTRY: Erreur sauvegarde: {e}")
