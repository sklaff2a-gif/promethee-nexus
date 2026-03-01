# core/roadmap_engine.py — RoadmapEngine : Plan de Route Vivant
# Transforme l'impact graph en fil directeur du travail autonome.
# Singleton deterministe (0 LLM), pattern identique a dopamine_system.py.
# Mecanisme WIP : quand >= 25 modules pending, la recherche s'arrete
# et le focus passe au codage. Reprend quand le backlog descend a <= 20.

import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("RoadmapEngine")

# --- Fichier de persistance ---

ROADMAP_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "roadmap.json"
)

# --- Constantes ---

# Status valides et leur ordre de progression
STATUS_ORDER = [
    "planned", "researching", "specifying", "ready",
    "in_progress", "implemented", "validated"
]

# Seuils hysteresis WIP
WIP_PAUSE_THRESHOLD = 25     # Pause recherche si pending >= 25
WIP_RESUME_THRESHOLD = 20    # Reprend recherche si pending <= 20

# Status consideres comme "pending" (pas encore en cours)
PENDING_STATUSES = {"planned", "researching", "specifying", "ready"}

# Bonus scoring
BONUS_RESEARCH = 1.5
BONUS_SPEC = 2.0
BONUS_COUNCIL_ROADMAP = 1.0
BONUS_CODE_WIP = 2.0
MALUS_BLOCKED = -10.0


class RoadmapEngine:
    """Singleton — Plan de route vivant pour l'Eveil de Promethee."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.modules: Dict[str, dict] = {}
        self._wip_paused: bool = False
        self._initialized = True
        self._load()

    def init(self):
        """Appele depuis main.py lifespan. Souscrit aux events bus."""
        self._load()
        try:
            bus.subscribe("ROADMAP_PROGRESS", self._on_progress)
        except Exception:
            pass
        logger.info(f"[ROADMAP] Engine initialisee: {len(self.modules)} modules trackes.")

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    # --- Persistance ---

    def _load(self):
        """Charge le roadmap depuis config/roadmap.json."""
        try:
            with open(ROADMAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._wip_paused = data.get("_wip_paused", False)
            self.modules = {}
            for mod in data.get("modules", []):
                self.modules[mod["id"]] = mod
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"[ROADMAP] Erreur chargement: {e}")
            self.modules = {}
            self._wip_paused = False

    def _save(self):
        """Sauvegarde atomique du roadmap dans config/roadmap.json."""
        data = {
            "_comment": "Roadmap vivante de l'Eveil de Promethee. Status mis a jour au runtime par roadmap_engine.",
            "_wip_paused": self._wip_paused,
            "modules": list(self.modules.values()),
        }
        tmp_path = ROADMAP_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, ROADMAP_FILE)
        except OSError as e:
            logger.error(f"[ROADMAP] Erreur sauvegarde: {e}")

    # --- Compteurs et requetes ---

    def get_pending_count(self) -> int:
        """Compte les modules en status pending (planned/researching/specifying/ready)."""
        return sum(1 for m in self.modules.values() if m.get("status") in PENDING_STATUSES)

    def get_next_module(self, status_filter: str = "planned") -> Optional[dict]:
        """Retourne le prochain module a traiter, trie par phase puis dependances.

        Args:
            status_filter: status cible (defaut "planned")

        Returns:
            Module dict ou None si aucun disponible.
        """
        candidates = [
            m for m in self.modules.values()
            if m.get("status") == status_filter
        ]
        if not candidates:
            return None

        # Trier par phase croissante, puis par nombre de dependances (moins = plus prioritaire)
        candidates.sort(key=lambda m: (m.get("phase", 99), len(m.get("depends_on", []))))

        # Filtrer ceux dont les dependances sont satisfaites
        for mod in candidates:
            deps = mod.get("depends_on", [])
            if not deps:
                return mod
            # Verifier que toutes les dependances sont au moins "in_progress"
            all_ready = True
            for dep_id in deps:
                dep = self.modules.get(dep_id)
                if dep:
                    dep_status = dep.get("status", "planned")
                    if STATUS_ORDER.index(dep_status) < STATUS_ORDER.index("in_progress"):
                        all_ready = False
                        break
            if all_ready:
                return mod

        # Si aucun avec deps satisfaites, retourner le premier (phase la plus basse)
        return candidates[0]

    def get_research_queue(self) -> List[dict]:
        """Retourne les modules planned avec research_topics non vides."""
        return [
            m for m in self.modules.values()
            if m.get("status") == "planned"
            and m.get("research_topics")
        ]

    def should_research(self) -> bool:
        """Hysteresis : False si pending >= 25, reprend a <= 20.

        Met a jour _wip_paused et persiste le flag.
        """
        pending = self.get_pending_count()

        if self._wip_paused:
            # En pause — reprend seulement si on descend a <= 20
            if pending <= WIP_RESUME_THRESHOLD:
                self._wip_paused = False
                self._save()
                logger.info(f"[ROADMAP] WIP resume: pending={pending} <= {WIP_RESUME_THRESHOLD}")
                return True
            return False
        else:
            # Pas en pause — pause si on atteint >= 25
            if pending >= WIP_PAUSE_THRESHOLD:
                self._wip_paused = True
                self._save()
                logger.info(f"[ROADMAP] WIP pause: pending={pending} >= {WIP_PAUSE_THRESHOLD}")
                return False
            return True

    # --- Mutations ---

    async def update_module_status(self, module_id: str, new_status: str,
                                    specs: list = None) -> bool:
        """Met a jour le status d'un module et publie un event bus.

        Args:
            module_id: identifiant du module
            new_status: nouveau status (doit etre dans STATUS_ORDER)
            specs: specs a ajouter (optionnel)

        Returns:
            True si la mise a jour a reussi.
        """
        if new_status not in STATUS_ORDER:
            logger.warning(f"[ROADMAP] Status invalide: {new_status}")
            return False

        mod = self.modules.get(module_id)
        if not mod:
            logger.warning(f"[ROADMAP] Module inconnu: {module_id}")
            return False

        old_status = mod.get("status", "planned")
        mod["status"] = new_status
        mod["updated_at"] = datetime.now().isoformat(timespec="seconds")

        if specs is not None:
            mod["specs"] = specs

        self._save()

        # Publier l'event bus
        event_data = {
            "module_id": module_id,
            "old_status": old_status,
            "new_status": new_status,
            "phase": mod.get("phase"),
            "display": mod.get("display"),
        }

        if new_status == "validated":
            await bus.publish("ROADMAP_MODULE_COMPLETED", event_data)
        elif old_status == "planned" and new_status != "planned":
            await bus.publish("ROADMAP_MODULE_STARTED", event_data)

        await bus.publish("ROADMAP_PROGRESS", event_data)

        logger.info(f"[ROADMAP] {module_id}: {old_status} -> {new_status}")
        return True

    # --- Scoring (sync, 0 bus) ---

    def compute_roadmap_bonus(self, intent: str) -> float:
        """Calcule le bonus roadmap pour le scoring des routines.

        Sync (pas de bus.publish), appele dans compute_adaptive_scoring().

        Args:
            intent: nom de l'intent (ROADMAP_RESEARCH, ROADMAP_SPEC, etc.)

        Returns:
            Bonus flottant a ajouter au score.
        """
        if self._wip_paused:
            # WIP pause : bloquer recherche/spec, favoriser codage
            if intent == "ROADMAP_RESEARCH":
                return MALUS_BLOCKED
            if intent == "ROADMAP_SPEC":
                return MALUS_BLOCKED
            if intent == "EXPANSION_CODE":
                return BONUS_CODE_WIP
            return 0.0

        # Mode normal
        if intent == "ROADMAP_RESEARCH":
            if self.get_research_queue():
                return BONUS_RESEARCH
            return 0.0

        if intent == "ROADMAP_SPEC":
            researching = [m for m in self.modules.values() if m.get("status") == "researching"]
            if researching:
                return BONUS_SPEC
            return 0.0

        if intent == "COUNCIL_DEBATE":
            actionable = [
                m for m in self.modules.values()
                if m.get("status") in ("ready", "specifying")
            ]
            if actionable:
                return BONUS_COUNCIL_ROADMAP
            return 0.0

        return 0.0

    # --- Contexte pour purpose_context ---

    def get_roadmap_context(self) -> str:
        """Texte injecte dans le purpose_context de l'autonomy engine."""
        stats = self._count_by_status()
        total = len(self.modules)
        if total == 0:
            return ""

        # Module en cours ou prochain
        current = self._get_current_focus()
        focus_text = ""
        if current:
            focus_text = f" Focus: {current['display']} (Phase {current.get('phase', '?')} {current.get('phase_name', '')})."

        wip_text = " [WIP PAUSE: focus codage]" if self._wip_paused else ""

        parts = []
        for status, count in stats.items():
            if count > 0:
                parts.append(f"{count} {status}")

        return (
            f"ROADMAP: {total} modules — {', '.join(parts)}.{focus_text}{wip_text}"
        )

    # --- Stats pour endpoint API ---

    def get_stats(self) -> dict:
        """Retourne les stats completes pour /api/roadmap."""
        stats = self._count_by_status()
        phases = {}
        for mod in self.modules.values():
            phase = mod.get("phase", 0)
            phase_name = mod.get("phase_name", "?")
            key = f"P{phase} {phase_name}"
            if key not in phases:
                phases[key] = {"total": 0, "by_status": {}}
            phases[key]["total"] += 1
            status = mod.get("status", "planned")
            phases[key]["by_status"][status] = phases[key]["by_status"].get(status, 0) + 1

        current = self._get_current_focus()
        return {
            "total_modules": len(self.modules),
            "by_status": stats,
            "by_phase": phases,
            "wip_paused": self._wip_paused,
            "pending_count": self.get_pending_count(),
            "current_focus": current.get("display") if current else None,
            "current_phase": current.get("phase") if current else None,
        }

    # --- Format pour impact_analyzer ---

    def get_modules_for_graph(self) -> list:
        """Retourne les modules au format compatible _ROADMAP pour impact_analyzer.

        Chaque entry contient : id, display, phase, phase_name, description,
        connects_to, status.
        """
        result = []
        for mod in self.modules.values():
            result.append({
                "id": mod["id"],
                "display": mod.get("display", ""),
                "phase": mod.get("phase", 0),
                "phase_name": mod.get("phase_name", ""),
                "description": mod.get("description", ""),
                "connects_to": mod.get("connects_to", []),
                "status": mod.get("status", "planned"),
            })
        return result

    # --- Helpers prives ---

    def _count_by_status(self) -> dict:
        """Compte les modules par status."""
        counts = {}
        for mod in self.modules.values():
            status = mod.get("status", "planned")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _get_current_focus(self) -> Optional[dict]:
        """Retourne le module le plus avance en cours de travail."""
        # Priorite : in_progress > ready > specifying > researching
        for status in ["in_progress", "ready", "specifying", "researching"]:
            for mod in self.modules.values():
                if mod.get("status") == status:
                    return mod
        return None

    async def _on_progress(self, data: dict):
        """Handler bus ROADMAP_PROGRESS — log uniquement."""
        module_id = data.get("module_id", "?")
        new_status = data.get("new_status", "?")
        logger.info(f"[ROADMAP] Progress: {module_id} -> {new_status}")


# Singleton
roadmap = RoadmapEngine()
