"""
CircadianRhythm — Cycle Veille/Sommeil de Prométhée.

Homéostasie et consolidation nocturne. Machine à 4 états :
ÉVEIL → CRÉPUSCULE → SOMMEIL PROFOND → AUBE → ÉVEIL

Pas d'horloge réelle — les transitions sont basées sur l'état interne
(budget, CPU/RAM, threat_level), pas sur l'heure du jour.

Le circadien ne pense pas — il rythme. 0 LLM, singleton.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from core.event_bus.bus import bus

logger = logging.getLogger("prometheev11.circadian")

# ============================================================
# Constantes
# ============================================================

CIRCADIAN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "circadian_state.json"
)

# Phases
PHASE_EVEIL = "eveil"
PHASE_CREPUSCULE = "crepuscule"
PHASE_SOMMEIL = "sommeil_profond"
PHASE_AUBE = "aube"

ALL_PHASES = (PHASE_EVEIL, PHASE_CREPUSCULE, PHASE_SOMMEIL, PHASE_AUBE)

# Anti-flip-flop : durée minimale dans chaque phase
MIN_PHASE_DURATION = 300          # 5 min
MIN_CREPUSCULE_DURATION = 300     # 5 min avant sommeil
MIN_AUBE_DURATION = 180           # 3 min de warmup

# Seuils de pression → crépuscule
CPU_PRESSURE_THRESHOLD = 85       # %
RAM_PRESSURE_THRESHOLD = 80       # %
THREAT_PRESSURE_THRESHOLD = 5.0   # threat_level reptilien

# Modulation du sleep entre les routines
SLEEP_MULTIPLIER = {
    PHASE_EVEIL: 1.0,
    PHASE_CREPUSCULE: 1.5,
    PHASE_SOMMEIL: 0.5,       # Cycles courts en sommeil (maintenance rapide)
    PHASE_AUBE: 1.2,
}

# BPM cible pour le CardiacEngine (None = pas de modulation)
BPM_TARGET = {
    PHASE_EVEIL: None,
    PHASE_CREPUSCULE: 55.0,
    PHASE_SOMMEIL: 42.0,
    PHASE_AUBE: 50.0,
}

# Tâches de maintenance exécutées pendant le sommeil profond
SLEEP_TASKS = [
    "dream_consolidation",
    "hippocampus_consolidation",
    "chromadb_cleanup",
    "neural_compile",
    "grimoire_harvest",
]

SLEEP_TASK_TIMEOUT = 60  # Timeout par tâche (secondes)


# ============================================================
# Dataclass — Rapport de sommeil
# ============================================================

@dataclass
class SleepReport:
    """Rapport d'un cycle de sommeil complet."""
    started_at: float = 0.0
    ended_at: float = 0.0
    tasks_completed: List[str] = field(default_factory=list)
    tasks_failed: List[str] = field(default_factory=list)
    dream_connections: int = 0
    pruned_synapses: int = 0
    episodes_purged: int = 0
    arcs_consolidated: int = 0
    chromadb_removed: int = 0
    rules_compiled: int = 0
    grimoire_harvested: int = 0
    trigger_reason: str = ""


# ============================================================
# Classe principale — Singleton
# ============================================================

class CircadianRhythm:
    """Cycle circadien — rythme le système sans penser."""

    _instance: Optional["CircadianRhythm"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Phase courante
        self.phase: str = PHASE_EVEIL
        self._phase_since: float = time.time()

        # Sommeil
        self._current_sleep_task_index: int = 0
        self._sleep_report: Optional[SleepReport] = None
        self._last_sleep_report: Optional[SleepReport] = None

        # Stats
        self._total_sleep_cycles: int = 0
        self._stats = {
            "total_cycles": 0,
            "total_transitions": 0,
            "total_tasks_completed": 0,
            "total_tasks_failed": 0,
            "last_phase_change": None,
        }

        # Bus
        self._subscribed: bool = False

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    # ============================================================
    # Init & Persistance
    # ============================================================

    def init(self):
        """Appelé depuis main.py — souscrit au bus."""
        self._subscribe_events()
        logger.info(
            f"CIRCADIEN: Phase={self.phase}, "
            f"cycles={self._total_sleep_cycles}."
        )

    def _subscribe_events(self):
        """Souscriptions bus."""
        if self._subscribed:
            return
        self._subscribed = True
        # Pas de handler pour l'instant — le circadien est piloté
        # par autonomy_engine, pas par des événements.

    def _load(self):
        """Charge l'état persisté."""
        try:
            if os.path.exists(CIRCADIAN_STATE_FILE):
                with open(CIRCADIAN_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.phase = state.get("phase", PHASE_EVEIL)
                if self.phase not in ALL_PHASES:
                    self.phase = PHASE_EVEIL
                self._phase_since = float(state.get("phase_since", time.time()))
                self._total_sleep_cycles = int(state.get("total_sleep_cycles", 0))
                self._stats = state.get("stats", self._stats)
                # Restaurer le dernier rapport si présent
                last_report = state.get("last_sleep_report")
                if last_report:
                    self._last_sleep_report = SleepReport(**last_report)
                logger.debug(f"CIRCADIEN: État chargé (phase={self.phase})")
        except Exception as e:
            logger.warning(f"CIRCADIEN: Échec chargement état: {e}")

    def save(self):
        """Persiste l'état sur disque."""
        try:
            state = {
                "phase": self.phase,
                "phase_since": self._phase_since,
                "total_sleep_cycles": self._total_sleep_cycles,
                "stats": self._stats,
            }
            if self._last_sleep_report:
                state["last_sleep_report"] = asdict(self._last_sleep_report)
            os.makedirs(os.path.dirname(CIRCADIAN_STATE_FILE), exist_ok=True)
            tmp = CIRCADIAN_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CIRCADIAN_STATE_FILE)
        except Exception as e:
            logger.warning(f"CIRCADIEN: Échec sauvegarde: {e}")

    # ============================================================
    # API publique — consultée par autonomy_engine et cardiac
    # ============================================================

    def get_phase(self) -> str:
        """Phase courante."""
        return self.phase

    def get_sleep_multiplier(self) -> float:
        """Multiplicateur pour la durée de sleep inter-routines."""
        return SLEEP_MULTIPLIER.get(self.phase, 1.0)

    def get_bpm_target(self) -> Optional[float]:
        """BPM cible pour le CardiacEngine (None = pas de modulation)."""
        return BPM_TARGET.get(self.phase)

    def should_allow_routine(self, intent: str, cost: int) -> Tuple[bool, str]:
        """Filtre les routines selon la phase circadienne.

        Returns:
            (allowed, deny_reason)
        """
        if self.phase == PHASE_EVEIL:
            return (True, "")

        if self.phase == PHASE_CREPUSCULE:
            if cost > 2:
                return (False, f"crepuscule: cout {cost} > 2")
            return (True, "")

        if self.phase == PHASE_SOMMEIL:
            # Seules les tâches de maintenance passent
            return (False, "sommeil profond")

        if self.phase == PHASE_AUBE:
            if cost > 3:
                return (False, f"aube: cout {cost} > 3")
            return (True, "")

        return (True, "")

    async def execute_next_sleep_task(self) -> Optional[Dict[str, Any]]:
        """Exécute la prochaine tâche de maintenance du sommeil.

        Returns:
            dict avec résultat, ou None si toutes les tâches sont terminées.
        """
        if self.phase != PHASE_SOMMEIL:
            return None

        if self._current_sleep_task_index >= len(SLEEP_TASKS):
            return None  # Toutes les tâches faites

        task_name = SLEEP_TASKS[self._current_sleep_task_index]
        self._current_sleep_task_index += 1

        result = {"task": task_name, "success": False, "detail": ""}

        try:
            handler = _SLEEP_TASK_HANDLERS.get(task_name)
            if handler:
                detail = await asyncio.wait_for(handler(self), timeout=SLEEP_TASK_TIMEOUT)
                result["success"] = True
                result["detail"] = str(detail) if detail else "ok"
                if self._sleep_report:
                    self._sleep_report.tasks_completed.append(task_name)
                self._stats["total_tasks_completed"] += 1
                logger.info(f"CIRCADIEN: Tâche '{task_name}' terminée: {result['detail']}")
            else:
                result["detail"] = f"handler inconnu: {task_name}"
                logger.warning(f"CIRCADIEN: Pas de handler pour '{task_name}'")

        except asyncio.TimeoutError:
            result["detail"] = f"timeout ({SLEEP_TASK_TIMEOUT}s)"
            if self._sleep_report:
                self._sleep_report.tasks_failed.append(task_name)
            self._stats["total_tasks_failed"] += 1
            logger.warning(f"CIRCADIEN: Tâche '{task_name}' timeout")

        except Exception as e:
            result["detail"] = str(e)[:200]
            if self._sleep_report:
                self._sleep_report.tasks_failed.append(task_name)
            self._stats["total_tasks_failed"] += 1
            logger.warning(f"CIRCADIEN: Tâche '{task_name}' échouée: {e}")

        self.save()
        return result

    def get_dawn_recap(self) -> str:
        """Résumé déterministe du dernier sommeil. Vide si pas de sommeil."""
        report = self._last_sleep_report
        if not report:
            return ""

        duration_min = (report.ended_at - report.started_at) / 60 if report.ended_at > report.started_at else 0
        lines = [
            f"[RÉVEIL] Sommeil de {duration_min:.0f} min ({report.trigger_reason})",
        ]

        if report.tasks_completed:
            lines.append(f"  Maintenance réussie: {', '.join(report.tasks_completed)}")
        if report.tasks_failed:
            lines.append(f"  Maintenance échouée: {', '.join(report.tasks_failed)}")
        if report.dream_connections > 0:
            lines.append(f"  Rêves: {report.dream_connections} connexions créées, {report.pruned_synapses} synapses élaguées")
        if report.arcs_consolidated > 0:
            lines.append(f"  Mémoire: {report.arcs_consolidated} arcs consolidés, {report.episodes_purged} épisodes purgés")
        if report.chromadb_removed > 0:
            lines.append(f"  ChromaDB: {report.chromadb_removed} documents supprimés")
        if report.rules_compiled > 0:
            lines.append(f"  Compiler: {report.rules_compiled} règles compilées")
        if report.grimoire_harvested > 0:
            lines.append(f"  Grimoire: {report.grimoire_harvested} candidats marqués")

        return "\n".join(lines)

    def get_circadian_context(self) -> str:
        """Contexte injectable dans purpose_context."""
        phase_labels = {
            PHASE_EVEIL: "éveillé — pleine capacité",
            PHASE_CREPUSCULE: "crépuscule — transition vers le repos",
            PHASE_SOMMEIL: "sommeil profond — maintenance en cours",
            PHASE_AUBE: "aube — réveil progressif",
        }
        label = phase_labels.get(self.phase, self.phase)
        elapsed = time.time() - self._phase_since
        elapsed_min = elapsed / 60

        parts = [f"Phase circadienne: {label} (depuis {elapsed_min:.0f} min)"]

        if self.phase == PHASE_SOMMEIL and self._sleep_report:
            done = len(self._sleep_report.tasks_completed)
            total = len(SLEEP_TASKS)
            parts.append(f"Maintenance: {done}/{total} tâches terminées")

        if self.phase == PHASE_AUBE and self._last_sleep_report:
            recap = self.get_dawn_recap()
            if recap:
                parts.append(recap)

        return " | ".join(parts)

    def get_stats(self) -> Dict[str, Any]:
        """Pour endpoint API."""
        return {
            "phase": self.phase,
            "phase_since": self._phase_since,
            "phase_duration_s": time.time() - self._phase_since,
            "total_sleep_cycles": self._total_sleep_cycles,
            "sleep_multiplier": self.get_sleep_multiplier(),
            "bpm_target": self.get_bpm_target(),
            "stats": dict(self._stats),
            "current_task_index": self._current_sleep_task_index,
            "has_sleep_report": self._last_sleep_report is not None,
        }

    # ============================================================
    # Machine à états — Transitions
    # ============================================================

    def _evaluate_transition(self, budget_status: str, health: Optional[Dict] = None) -> Optional[str]:
        """Évalue si une transition de phase est nécessaire.

        Args:
            budget_status: "full", "reserve", "exhausted"
            health: dict du dernier health check (cpu_percent, ram_percent, etc.)

        Returns:
            Nouvelle phase ou None si pas de transition.
        """
        now = time.time()
        elapsed = now - self._phase_since

        # Anti-flip-flop : durée minimale dans chaque phase
        if elapsed < MIN_PHASE_DURATION:
            return None

        # FREEZE reptilien : suspend toutes les transitions
        try:
            from core.reptilian_core import reptilian
            if reptilian.should_freeze():
                return None
        except Exception:
            pass

        if self.phase == PHASE_EVEIL:
            return self._eval_eveil_transition(budget_status, health)
        elif self.phase == PHASE_CREPUSCULE:
            return self._eval_crepuscule_transition(elapsed)
        elif self.phase == PHASE_SOMMEIL:
            return self._eval_sommeil_transition(budget_status)
        elif self.phase == PHASE_AUBE:
            return self._eval_aube_transition(elapsed, budget_status)

        return None

    def _eval_eveil_transition(self, budget_status: str, health: Optional[Dict]) -> Optional[str]:
        """ÉVEIL → CRÉPUSCULE si conditions de pression."""
        # Budget épuisé → crépuscule
        if budget_status == "exhausted":
            return PHASE_CREPUSCULE

        # Pression CPU/RAM
        if health:
            cpu = health.get("cpu_percent", 0)
            ram = health.get("ram_percent", 0)
            if cpu > CPU_PRESSURE_THRESHOLD and ram > RAM_PRESSURE_THRESHOLD:
                return PHASE_CREPUSCULE

        # Threat level élevé
        try:
            from core.reptilian_core import reptilian
            if reptilian.threat_level >= THREAT_PRESSURE_THRESHOLD:
                return PHASE_CREPUSCULE
        except Exception:
            pass

        return None

    def _eval_crepuscule_transition(self, elapsed: float) -> Optional[str]:
        """CRÉPUSCULE → SOMMEIL après durée minimum."""
        if elapsed < MIN_CREPUSCULE_DURATION:
            return None

        # Vérifier qu'on n'est pas en train de traiter
        try:
            from core.autonomy_engine import autonomy
            if autonomy.is_processing:
                return None
        except Exception:
            pass

        return PHASE_SOMMEIL

    def _eval_sommeil_transition(self, budget_status: str) -> Optional[str]:
        """SOMMEIL → AUBE si maintenance terminée ou nouveau jour."""
        # Toutes les tâches terminées
        if self._current_sleep_task_index >= len(SLEEP_TASKS):
            return PHASE_AUBE

        # Nouveau jour (budget reset)
        if budget_status != "exhausted":
            return PHASE_AUBE

        return None

    def _eval_aube_transition(self, elapsed: float, budget_status: str) -> Optional[str]:
        """AUBE → ÉVEIL après warmup + budget disponible."""
        if elapsed < MIN_AUBE_DURATION:
            return None

        if budget_status == "exhausted":
            return None  # Rester en aube si budget toujours épuisé

        return PHASE_EVEIL

    async def _transition_to(self, new_phase: str, reason: str = ""):
        """Effectue une transition de phase."""
        if new_phase == self.phase:
            return

        previous = self.phase
        now = time.time()

        logger.info(f"CIRCADIEN: {previous} → {new_phase} ({reason})")

        # Actions d'entrée dans la nouvelle phase
        if new_phase == PHASE_CREPUSCULE:
            await self._enter_crepuscule(reason)
        elif new_phase == PHASE_SOMMEIL:
            await self._enter_sommeil(reason)
        elif new_phase == PHASE_AUBE:
            await self._enter_aube()
        elif new_phase == PHASE_EVEIL:
            await self._enter_eveil()

        self.phase = new_phase
        self._phase_since = now
        self._stats["total_transitions"] += 1
        self._stats["last_phase_change"] = {
            "from": previous,
            "to": new_phase,
            "reason": reason,
            "timestamp": now,
        }

        # Publier l'événement
        await bus.publish("CIRCADIAN_PHASE_CHANGE", {
            "phase": new_phase,
            "previous_phase": previous,
            "reason": reason,
            "timestamp": now,
        })

        self.save()

    async def _enter_crepuscule(self, reason: str):
        """Actions d'entrée en crépuscule."""
        # Stimulus cardiac : ralentissement
        try:
            from core.cardiac_engine import heart
            heart.react("sleep_deep")
        except Exception:
            pass
        logger.info(f"CIRCADIEN: Entrée en crépuscule — {reason}")

    async def _enter_sommeil(self, reason: str):
        """Actions d'entrée en sommeil profond."""
        self._current_sleep_task_index = 0
        self._sleep_report = SleepReport(
            started_at=time.time(),
            trigger_reason=reason,
        )
        # Stimulus cardiac : sommeil profond
        try:
            from core.cardiac_engine import heart
            heart.react("sleep_deep")
        except Exception:
            pass
        logger.info("CIRCADIEN: Entrée en sommeil profond — maintenance démarrée")

    async def _enter_aube(self):
        """Actions d'entrée en aube."""
        # Finaliser le rapport de sommeil
        if self._sleep_report:
            self._sleep_report.ended_at = time.time()
            self._last_sleep_report = self._sleep_report
            self._sleep_report = None
            self._total_sleep_cycles += 1
            self._stats["total_cycles"] += 1

        # Stimulus cardiac : réveil
        try:
            from core.cardiac_engine import heart
            heart.react("dawn")
        except Exception:
            pass

        # Afficher le récap
        recap = self.get_dawn_recap()
        if recap:
            logger.info(f"CIRCADIEN: {recap}")
            print(f"   🌅 {recap}")

        logger.info("CIRCADIEN: Entrée en aube — réveil progressif")

    async def _enter_eveil(self):
        """Actions d'entrée en éveil."""
        logger.info("CIRCADIEN: Plein éveil — toutes capacités restaurées")


# ============================================================
# Tâches de maintenance — exécutées pendant le sommeil profond
# ============================================================

async def _task_dream_consolidation(circ: CircadianRhythm) -> str:
    """Consolidation synaptique — simule le sommeil REM."""
    from core.synaptic_network import cortex
    report = cortex.dream_consolidation()
    connections = report.get("new_connections", 0)
    pruned = report.get("pruned", 0)
    if circ._sleep_report:
        circ._sleep_report.dream_connections = connections
        circ._sleep_report.pruned_synapses = pruned
    return f"{connections} connexions, {pruned} élagués"


async def _task_hippocampus_consolidation(circ: CircadianRhythm) -> str:
    """Consolidation de la mémoire épisodique."""
    from core.hippocampus import hippocampus
    # Consolider les épisodes en arcs narratifs
    hippocampus.consolidate()
    arcs_count = len(hippocampus._arcs)
    # Purger les vieux épisodes (> 7 jours)
    now = time.time()
    max_age = 7 * 86400
    before = len(hippocampus._episodes)
    hippocampus._episodes = [
        ep for ep in hippocampus._episodes
        if now - ep.when < max_age
    ]
    purged = before - len(hippocampus._episodes)
    if purged > 0:
        hippocampus._save()
    if circ._sleep_report:
        circ._sleep_report.arcs_consolidated = arcs_count
        circ._sleep_report.episodes_purged = purged
    return f"{arcs_count} arcs, {purged} épisodes purgés"


async def _task_chromadb_cleanup(circ: CircadianRhythm) -> str:
    """Nettoyage ChromaDB — purge expired + low quality."""
    from core.vector_store import ChromaMemoryManager
    # Chercher une instance existante
    instances = ChromaMemoryManager._instances
    if not instances:
        return "aucune instance ChromaDB"
    mgr = next(iter(instances.values()))
    removed_expired = await mgr.async_purge_expired(60)
    removed_quality = await mgr.async_purge_low_quality(100, 0.10)
    total_removed = removed_expired + removed_quality
    if circ._sleep_report:
        circ._sleep_report.chromadb_removed = total_removed
    return f"{removed_expired} expirés + {removed_quality} low-quality"


async def _task_neural_compile(circ: CircadianRhythm) -> str:
    """Compilation neurale — observations → règles déterministes."""
    from core.neural_compiler import compiler
    rules_compiled = compiler.compile_rules()
    if circ._sleep_report:
        circ._sleep_report.rules_compiled = rules_compiled
    return f"{rules_compiled} règles compilées"


async def _task_grimoire_harvest(circ: CircadianRhythm) -> str:
    """Marquage des specs deployed récentes comme candidats grimoire."""
    from core.evolution_catalog import EvolutionCatalog
    catalog = EvolutionCatalog()
    now = time.time()
    max_age = 7 * 86400  # 7 jours
    harvested = 0

    for spec_id, spec in catalog.specs.items():
        if spec.status != "deployed":
            continue
        # Vérifier si déployé récemment
        if not spec.deployed_at:
            continue
        try:
            from datetime import datetime
            deployed_dt = datetime.fromisoformat(spec.deployed_at)
            age = now - deployed_dt.timestamp()
        except (ValueError, TypeError):
            continue
        if age > max_age:
            continue
        # Déjà candidat ?
        if getattr(spec, "grimoire_candidate", False):
            continue
        spec.grimoire_candidate = True
        harvested += 1

    if harvested > 0:
        catalog._save()

    if circ._sleep_report:
        circ._sleep_report.grimoire_harvested = harvested
    return f"{harvested} candidats marqués"


# Registre des handlers de tâches de maintenance
_SLEEP_TASK_HANDLERS = {
    "dream_consolidation": _task_dream_consolidation,
    "hippocampus_consolidation": _task_hippocampus_consolidation,
    "chromadb_cleanup": _task_chromadb_cleanup,
    "neural_compile": _task_neural_compile,
    "grimoire_harvest": _task_grimoire_harvest,
}


# ============================================================
# Singleton
# ============================================================

circadian = CircadianRhythm()
