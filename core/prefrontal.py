"""
Cortex Préfrontal — La Fonction Exécutive de Prométhée.

Chef d'orchestre cognitif : fixe des objectifs, maintient l'attention,
inhibe les distractions, apprend des stratégies, donne un sens à chaque action.

7 sous-systèmes, 0 appel LLM :
- Goals (dlPFC) : objectifs multi-horizon, priorisation dynamique
- Working Memory : 3-5 slots contextuels
- Attention Exécutive (ACC) : focus_bonus pour le scoring
- Inhibition (vmPFC) : arbitrage avec couches inférieures
- Mémoire Stratégique : apprentissage de séquences
- Mémoire Prospective : triggers différés
- Monologue Intérieur : narration interne
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from core.event_bus.bus import bus

logger = logging.getLogger("prefrontal")

# ─── Fichier de persistance ───────────────────────────────────────────
PREFRONTAL_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "prefrontal_state.json"
)

# ─── Constantes ───────────────────────────────────────────────────────
DELIBERATION_INTERVAL = 120.0   # Secondes entre chaque cycle de délibération
MAX_GOALS = 7                   # Miller's number
MAX_ACTIVE_GOALS = 3            # Slots de travail simultanés
MAX_STRATEGIES = 50             # Mémoire stratégique (FIFO)
MAX_PROSPECTIVE_TRIGGERS = 20   # Rappels différés max
MAX_NARRATIVE_LOG = 100         # Journal interne (FIFO)

# Horizons temporels (secondes)
HORIZON_IMMEDIATE = 600         # 10 min
HORIZON_SHORT = 3600            # 1h
HORIZON_MEDIUM = 14400          # 4h
HORIZON_LONG = 86400            # 24h

_HORIZON_DURATIONS = {
    "immediate": HORIZON_IMMEDIATE,
    "short": HORIZON_SHORT,
    "medium": HORIZON_MEDIUM,
    "long": HORIZON_LONG,
}

# Scoring d'attention
FOCUS_BONUS_PRIMARY = 6.0       # Goal #1
FOCUS_BONUS_SECONDARY = 3.0     # Goal #2
FOCUS_BONUS_TERTIARY = 1.5      # Goal #3
DISTRACTION_PENALTY = -2.0      # Hors-focus

# Inhibition
INHIBITION_OVERRIDE_THRESHOLD = 0.7  # Progression au-delà de laquelle on override

# Cristallisation
STRATEGY_CRYSTALLIZE_THRESHOLD = 3   # Succès pour devenir "habitude"
STRATEGY_CONFIDENCE_BOOST = 0.15
STRATEGY_CONFIDENCE_DECAY = 0.25

# Charge cognitive
COGNITIVE_LOAD_THRESHOLD = 5         # Dégradation au-delà


# ─── Structures de données ────────────────────────────────────────────

@dataclass
class GoalStep:
    intent: str                      # Ex: "VEILLE_SILENCIEUSE"
    description: str
    status: str = "pending"          # "pending"|"in_progress"|"done"|"failed"|"skipped"
    required: bool = True            # Bloquant ?
    result_summary: str = ""


@dataclass
class Goal:
    id: str                          # UUID court (8 chars)
    title: str
    horizon: str                     # "immediate"|"short"|"medium"|"long"
    priority: float                  # [0, 10] dynamique
    progress: float = 0.0            # [0, 1]
    steps: List[GoalStep] = field(default_factory=list)
    current_step: int = 0
    source: str = "auto"             # "desire"|"pattern"|"council"|"gap"|"user"|"meta"|"strategy"
    created_at: float = field(default_factory=time.time)
    deadline: Optional[float] = None
    status: str = "active"           # "active"|"completed"|"abandoned"|"blocked"
    abandon_reason: str = ""
    cost_spent: int = 0
    cost_estimated: int = 10
    drive_alignment: Dict[str, float] = field(default_factory=dict)
    last_advanced: float = field(default_factory=time.time)


@dataclass
class Strategy:
    id: str
    sequence: List[str]              # Ex: ["VEILLE", "EXPANSION"]
    context_tags: List[str]          # Conditions (ex: ["gap_detected"])
    confidence: float = 0.5          # [0, 1]
    successes: int = 0
    failures: int = 0
    last_used: float = field(default_factory=time.time)
    crystallized: bool = False       # Habitude (bypass délibération)


@dataclass
class ProspectiveTrigger:
    id: str
    condition_type: str              # "event"|"state"|"time"
    condition_key: str               # Event type ou state key
    condition_value: str
    action_intent: str
    action_context: str = ""
    one_shot: bool = True
    created_at: float = field(default_factory=time.time)
    triggered_count: int = 0
    max_triggers: int = 0            # 0 = illimité


@dataclass
class NarrativeEntry:
    timestamp: float
    thought: str
    category: str                    # "decision"|"inhibition"|"goal"|"insight"|"frustration"
    context: Dict[str, Any] = field(default_factory=dict)


# ─── Mapping pulsions → intents ──────────────────────────────────────

_DRIVE_ROUTINE_MAP = {
    "CURIOSITE": ["VEILLE_SILENCIEUSE", "EXPANSION_CODE", "GRIMOIRE_INVOKE"],
    "MAITRISE": ["REFACTORING_AUDIT", "SECURITY_AUDIT", "CI_PIPELINE_RUN"],
    "STABILITE": ["MEMORY_CONSOLIDATION", "AUDIT_STRUCTURE"],
    "CONNEXION": ["COUNCIL_DEBATE"],
    "CROISSANCE": ["EXPANSION_CODE", "GRIMOIRE_INVOKE"],
    "CREATION": ["EXPANSION_CODE", "ARTIFACT_CREATION"],
    "COMPREHENSION": ["VEILLE_SILENCIEUSE", "COUNCIL_DEBATE", "MEMORY_CONSOLIDATION"],
}


# ─── Classe PrefrontalCortex ─────────────────────────────────────────

class PrefrontalCortex:
    """Cortex Préfrontal — Singleton, 0 LLM, fonction exécutive."""

    _instance: Optional["PrefrontalCortex"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Goals (dlPFC)
        self.goals: List[Goal] = []
        # Mémoire stratégique
        self.strategies: List[Strategy] = []
        # Mémoire prospective
        self.triggers: List[ProspectiveTrigger] = []
        # Monologue intérieur
        self.narrative_log: List[NarrativeEntry] = []

        # Stats
        self.stats: Dict[str, Any] = {
            "goals_created": 0,
            "goals_completed": 0,
            "goals_abandoned": 0,
            "strategies_crystallized": 0,
            "inhibitions_applied": 0,
            "overrides_applied": 0,
            "triggers_fired": 0,
            "deliberation_cycles": 0,
        }

        # Contrôle
        self._alive: bool = False
        self._delib_task: Optional[asyncio.Task] = None
        self._subscribed: bool = False
        self._current_routine_intent: str = ""  # Tracking de la routine en cours

        self._load()

    @classmethod
    def reset_singleton(cls):
        if cls._instance is not None:
            cls._instance._alive = False
            if cls._instance._delib_task and not cls._instance._delib_task.done():
                cls._instance._delib_task.cancel()
            cls._instance = None

    def init(self):
        """Initialise les souscriptions bus. Appelé depuis main.py."""
        self._subscribe_events()
        goals_active = len([g for g in self.goals if g.status == "active"])
        logger.info(f"PREFRONTAL: Fonction exécutive active ({goals_active} goals).")

    # ─── Souscriptions Bus ────────────────────────────────────────────

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
        bus.subscribe("EUREKA_BRIDGE", self._on_eureka_bridge)
        bus.subscribe("COUNCIL_END", self._on_council_end)
        bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
        bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
        bus.subscribe("HALLUCINATION_DETECTED", self._on_hallucination)
        bus.subscribe("INNER_VOICE_BROADCAST", self._on_inner_voice)

    async def _on_inner_voice(self, data: dict):
        """Intègre la pensée broadcast dans le narrative_log."""
        thought = data.get("thought", "")
        if thought:
            self.narrative_log.append(NarrativeEntry(
                timestamp=time.time(),
                thought=f"[voix] {thought}",
                category="insight",
                context={"source": data.get("source", ""), "mode": data.get("mode", "")},
            ))
            if len(self.narrative_log) > MAX_NARRATIVE_LOG:
                self.narrative_log = self.narrative_log[-MAX_NARRATIVE_LOG:]

    # ─── Handlers Bus (async) ─────────────────────────────────────────

    async def _on_routine_complete(self, data: dict):
        """Reçoit le résultat d'une routine autonome."""
        intent = data.get("intent", "")
        status = data.get("status", "")
        quality = data.get("quality_score", 0.0)
        preview = data.get("result", "")
        self.on_routine_complete(intent, status, quality, preview)

    async def _on_knowledge_gap(self, data: dict):
        """Crée un goal pour combler une lacune de connaissance."""
        topic = data.get("topic", data.get("gap", "inconnu"))
        # Vérifier qu'on n'a pas déjà un goal similaire
        for g in self.goals:
            if g.status == "active" and topic.lower() in g.title.lower():
                return
        if len([g for g in self.goals if g.status == "active"]) >= MAX_GOALS:
            return
        goal = Goal(
            id=uuid.uuid4().hex[:8],
            title=f"Combler lacune: {topic}",
            horizon="short",
            priority=5.0,
            source="gap",
            steps=[
                GoalStep(intent="VEILLE_SILENCIEUSE", description=f"Rechercher: {topic}"),
                GoalStep(intent="EXPANSION_CODE", description=f"Intégrer: {topic}"),
            ],
            cost_estimated=6,
            drive_alignment={"CURIOSITE": 0.8, "COMPREHENSION": 0.9},
        )
        self.goals.append(goal)
        self.stats["goals_created"] += 1
        self._narrate("goal", f"Nouvelle lacune détectée: {topic}. Goal créé.")
        self._publish_goal_event("PREFRONTAL_GOAL_CREATED", goal)

    async def _on_eureka_bridge(self, data: dict):
        """Crée un goal pour explorer un pont créatif."""
        concept_a = data.get("node_a", data.get("source", "?"))
        concept_b = data.get("node_b", data.get("target", "?"))
        bridge_title = f"Explorer: {concept_a} <-> {concept_b}"
        for g in self.goals:
            if g.status == "active" and concept_a in g.title and concept_b in g.title:
                return
        if len([g for g in self.goals if g.status == "active"]) >= MAX_GOALS:
            return
        goal = Goal(
            id=uuid.uuid4().hex[:8],
            title=bridge_title,
            horizon="short",
            priority=4.0,
            source="pattern",
            steps=[
                GoalStep(intent="COUNCIL_DEBATE", description=f"Débattre: {concept_a} et {concept_b}"),
                GoalStep(intent="EXPANSION_CODE", description=f"Implémenter pont créatif"),
            ],
            cost_estimated=8,
            drive_alignment={"CURIOSITE": 0.9, "CREATION": 0.8},
        )
        self.goals.append(goal)
        self.stats["goals_created"] += 1
        self._narrate("insight", f"Pont créatif détecté: {concept_a} <-> {concept_b}")
        self._publish_goal_event("PREFRONTAL_GOAL_CREATED", goal)

    async def _on_council_end(self, data: dict):
        """Crée un goal si le council a atteint un consensus actionnable."""
        consensus = data.get("final_summary", "")
        status = data.get("status", "")
        if status not in ("consensus", "max_rounds"):
            return
        if not consensus or len(consensus) < 50:
            return
        for g in self.goals:
            if g.status == "active" and "council" in g.source:
                # On a déjà un goal council en cours
                return
        if len([g for g in self.goals if g.status == "active"]) >= MAX_GOALS:
            return
        goal = Goal(
            id=uuid.uuid4().hex[:8],
            title=f"Implémenter décision council",
            horizon="medium",
            priority=5.5,
            source="council",
            steps=[
                GoalStep(intent="EXPANSION_CODE", description=f"Implémenter: {consensus[:80]}"),
                GoalStep(intent="SECURITY_AUDIT", description="Vérifier la qualité"),
            ],
            cost_estimated=10,
            drive_alignment={"CONNEXION": 0.7, "CREATION": 0.6},
        )
        self.goals.append(goal)
        self.stats["goals_created"] += 1
        self._narrate("goal", f"Council consensus → goal créé")
        self._publish_goal_event("PREFRONTAL_GOAL_CREATED", goal)

    async def _on_reptilian_alert(self, data: dict):
        """Mode survie si menace critique."""
        reflex = data.get("reflex", "")
        threat_level = data.get("threat_level", 0.0)
        if reflex == "FREEZE" or threat_level >= 7:
            self._narrate("frustration", f"Alerte reptilienne: {reflex} (menace={threat_level:.1f}). Mode survie.")
            # En mode survie, on ne crée pas de nouveaux goals
            # L'inhibition s'occupera de bloquer les distractions

    async def _on_cardiac_beat(self, data: dict):
        """Détecte l'état flow pour ajuster la stratégie."""
        emotion = data.get("emotion", "")
        coherence = data.get("coherence", 0.0)
        if emotion == "flow" and coherence > 0.7:
            # En flow : ne pas interrompre, boost le goal primaire
            active = [g for g in self.goals if g.status == "active"]
            if active:
                active.sort(key=lambda g: g.priority, reverse=True)
                active[0].priority = min(10.0, active[0].priority + 0.2)

    async def _on_hallucination(self, data: dict):
        """Crée un trigger prospectif pour auditer après hallucination."""
        self.add_trigger(
            condition_type="event",
            condition_key="ARTIFACT_CREATED",
            condition_value="any",
            action_intent="SECURITY_AUDIT",
            action_context="Post-hallucination audit",
            one_shot=True,
        )

    # ─── 1. GOALS (dlPFC) ────────────────────────────────────────────

    def create_goal(self, title: str, horizon: str, priority: float,
                    steps: List[Dict], source: str = "auto",
                    cost_estimated: int = 10,
                    drive_alignment: Optional[Dict[str, float]] = None,
                    deadline: Optional[float] = None) -> Optional[Goal]:
        """Crée un goal manuellement ou programmatiquement."""
        active_count = len([g for g in self.goals if g.status == "active"])
        if active_count >= MAX_GOALS:
            self._narrate("frustration", f"Impossible de créer '{title}': MAX_GOALS atteint ({MAX_GOALS})")
            return None

        goal_steps = [
            GoalStep(
                intent=s.get("intent", ""),
                description=s.get("description", ""),
                required=s.get("required", True),
            )
            for s in steps
        ]

        goal = Goal(
            id=uuid.uuid4().hex[:8],
            title=title,
            horizon=horizon if horizon in _HORIZON_DURATIONS else "short",
            priority=max(0.0, min(10.0, priority)),
            steps=goal_steps,
            source=source,
            cost_estimated=cost_estimated,
            drive_alignment=drive_alignment or {},
            deadline=deadline,
        )
        self.goals.append(goal)
        self.stats["goals_created"] += 1
        self._narrate("goal", f"Goal créé: {title} (horizon={horizon}, prio={priority:.1f})")
        self._publish_goal_event("PREFRONTAL_GOAL_CREATED", goal)
        return goal

    def _compute_goal_priority(self, goal: Goal) -> float:
        """Recalcule la priorité dynamique d'un goal."""
        now = time.time()
        priority = goal.priority

        # 1. Urgence temporelle (deadline)
        if goal.deadline:
            remaining = goal.deadline - now
            horizon_sec = _HORIZON_DURATIONS.get(goal.horizon, HORIZON_SHORT)
            if remaining <= 0:
                priority += 4.0  # Deadline dépassée
            elif remaining < horizon_sec * 0.25:
                priority += 3.0  # Urgent
            elif remaining < horizon_sec * 0.5:
                priority += 1.5

        # 2. Alignement pulsions (deprivation × affinity)
        try:
            from core.desire_engine import desires
            drive_bonus = 0.0
            for drive_name, affinity in goal.drive_alignment.items():
                d = desires.drives.get(drive_name)
                if d and d.deprivation > 30:
                    urgency = (d.deprivation - 30) / 70.0
                    drive_bonus += urgency * affinity
            priority += min(3.0, drive_bonus)
        except Exception:
            pass

        # 3. Momentum (progrès > 20%)
        if goal.progress > 0.2:
            priority += 1.5

        # 4. Coût-bénéfice (trop cher + peu de progrès)
        if goal.cost_spent > goal.cost_estimated * 1.2 and goal.progress < 0.3:
            priority -= 3.0

        # 5. Ancienneté sans progrès
        stale_time = now - goal.last_advanced
        horizon_sec = _HORIZON_DURATIONS.get(goal.horizon, HORIZON_SHORT)
        if stale_time > horizon_sec * 0.5 and goal.progress < 0.1:
            priority -= 2.0

        # 6. Mode stratégique (survie → seul "immediate" survit)
        try:
            from core.self_awareness import awareness
            mode = awareness.get_strategic_mode() if hasattr(awareness, 'get_strategic_mode') else "standard"
            if mode == "survie" and goal.horizon != "immediate":
                priority -= 5.0
        except Exception:
            pass

        return round(max(-5.0, min(10.0, priority)), 2)

    def _update_goal_progress(self, goal: Goal):
        """Recalcule la progression d'un goal basé sur ses steps."""
        if not goal.steps:
            return
        done = sum(1 for s in goal.steps if s.status in ("done", "skipped"))
        goal.progress = round(done / len(goal.steps), 2)

    def _check_goal_completion(self, goal: Goal):
        """Vérifie si un goal est terminé."""
        if not goal.steps:
            return
        required_done = all(
            s.status in ("done", "skipped") for s in goal.steps if s.required
        )
        all_done = all(s.status in ("done", "skipped", "failed") for s in goal.steps)
        if required_done or all_done:
            goal.status = "completed"
            self.stats["goals_completed"] += 1
            self._narrate("goal", f"Goal accompli: {goal.title}")
            self._publish_goal_event("PREFRONTAL_GOAL_COMPLETE", goal)
            # Extraire stratégie
            self._learn_strategy_from_goal(goal)

    def _check_goal_abandonment(self, goal: Goal) -> bool:
        """Vérifie si un goal doit être abandonné."""
        now = time.time()
        reasons = []

        # Coût excessif
        if goal.cost_spent > goal.cost_estimated * 1.5 and goal.progress < 0.5:
            reasons.append("coût excessif")

        # Priorité effondrée
        new_prio = self._compute_goal_priority(goal)
        if new_prio < -2.0:
            reasons.append(f"priorité effondrée ({new_prio:.1f})")

        # Stagnation + échecs
        stale_time = now - goal.last_advanced
        failed_steps = sum(1 for s in goal.steps if s.status == "failed")
        horizon_sec = _HORIZON_DURATIONS.get(goal.horizon, HORIZON_SHORT)
        if stale_time > horizon_sec and failed_steps >= 2:
            reasons.append("stagnation + échecs répétés")

        if reasons:
            goal.status = "abandoned"
            goal.abandon_reason = "; ".join(reasons)
            self.stats["goals_abandoned"] += 1
            self._narrate("frustration", f"Goal abandonné: {goal.title} — {goal.abandon_reason}")
            self._publish_goal_event("PREFRONTAL_GOAL_ABANDONED", goal)
            return True
        return False

    # ─── 2. WORKING MEMORY ───────────────────────────────────────────

    def get_working_memory(self) -> List[Dict]:
        """Retourne les top 3 goals actifs avec contexte pour le cycle courant."""
        active = [g for g in self.goals if g.status == "active"]
        if not active:
            return []

        # Recalculer les priorités et trier
        for g in active:
            g.priority = self._compute_goal_priority(g)
        active.sort(key=lambda g: g.priority, reverse=True)

        slots = []
        for g in active[:MAX_ACTIVE_GOALS]:
            # Trouver le prochain step
            next_step = None
            step_desc = ""
            for i, s in enumerate(g.steps):
                if s.status == "pending":
                    next_step = s.intent
                    step_desc = s.description
                    break
                elif s.status == "in_progress":
                    next_step = s.intent
                    step_desc = s.description
                    break

            # Contexte de continuité : résultat du step précédent
            prev_context = ""
            if g.current_step > 0 and g.current_step <= len(g.steps):
                prev = g.steps[g.current_step - 1]
                if prev.result_summary:
                    prev_context = prev.result_summary

            slots.append({
                "goal_id": g.id,
                "goal_title": g.title,
                "next_intent": next_step or "",
                "step_description": step_desc,
                "progress": g.progress,
                "priority": g.priority,
                "context": prev_context,
            })
        return slots

    # ─── 3. ATTENTION EXÉCUTIVE (ACC) ────────────────────────────────

    def compute_focus_bonus(self, intent: str) -> float:
        """Calcule le bonus/malus d'attention pour un intent donné.
        Appelé comme 8ème couche de scoring dans autonomy_engine."""
        active = [g for g in self.goals if g.status == "active"]
        if not active:
            return 0.0

        # Recalculer priorités et trier
        for g in active:
            g.priority = self._compute_goal_priority(g)
        active.sort(key=lambda g: g.priority, reverse=True)

        bonus = 0.0
        # Bonus par rang pour les top 3
        bonuses = [FOCUS_BONUS_PRIMARY, FOCUS_BONUS_SECONDARY, FOCUS_BONUS_TERTIARY]
        intent_matched = False

        for rank, g in enumerate(active[:MAX_ACTIVE_GOALS]):
            rank_bonus = bonuses[rank] if rank < len(bonuses) else 0.5

            # Identifier le step courant (premier pending ou in_progress)
            current_step_intent = None
            future_intents = []
            found_current = False
            for s in g.steps:
                if not found_current and s.status in ("pending", "in_progress"):
                    current_step_intent = s.intent
                    found_current = True
                elif found_current and s.status == "pending":
                    future_intents.append(s.intent)

            # Step courant correspond ? → bonus plein
            if current_step_intent == intent:
                bonus = max(bonus, rank_bonus)
                intent_matched = True
            # Step futur correspond ? → demi-bonus
            elif intent in future_intents:
                bonus = max(bonus, rank_bonus * 0.5)
                intent_matched = True

        # Pénalité de distraction
        if not intent_matched and active:
            primary = active[0]
            if primary.progress > 0.2:
                bonus = DISTRACTION_PENALTY

        # Surcharge cognitive
        active_count = len(active)
        if active_count > COGNITIVE_LOAD_THRESHOLD:
            bonus *= 0.7

        return round(bonus, 2)

    # ─── 4. INHIBITION (vmPFC) ───────────────────────────────────────

    def compute_inhibition(self, intent: str, veto_source: str = "") -> Dict:
        """Arbitrage avec les couches inférieures.
        Retourne {action, reason, override_target}.
        action: "allow"|"inhibit"|"override"|"defer"
        """
        result = {"action": "allow", "reason": "", "override_target": ""}

        active = [g for g in self.goals if g.status == "active"]
        if not active:
            return result

        active.sort(key=lambda g: g.priority, reverse=True)
        primary = active[0]

        # Hiérarchie sacrée : FREEZE jamais inhibé
        if veto_source and "FREEZE" in veto_source.upper():
            return result  # Laisser le FREEZE passer sans interférence

        # Somatic marker jamais inhibé
        if veto_source and "somatique" in veto_source.lower():
            return result

        # Négociations possibles
        if veto_source:
            # SHED reptilien + goal avancé → override
            if "SHED" in veto_source.upper() and primary.progress > INHIBITION_OVERRIDE_THRESHOLD:
                self.stats["overrides_applied"] += 1
                self._narrate("inhibition",
                              f"Override SHED — goal '{primary.title}' à {primary.progress:.0%}")
                result["action"] = "override"
                result["reason"] = f"Goal '{primary.title}' à {primary.progress:.0%}, trop avancé pour SHED"
                result["override_target"] = "SHED"
                return result

            # FLINCH + goal quasi-terminé → override
            if "FLINCH" in veto_source.upper() and primary.progress > 0.9:
                self.stats["overrides_applied"] += 1
                self._narrate("inhibition",
                              f"Override FLINCH — goal '{primary.title}' à {primary.progress:.0%}")
                result["action"] = "override"
                result["reason"] = f"Goal '{primary.title}' quasi-terminé ({primary.progress:.0%})"
                result["override_target"] = "FLINCH"
                return result

        # Inhibition de distraction : intent hors-focus + goal primaire en cours
        is_on_focus = False
        for g in active[:MAX_ACTIVE_GOALS]:
            for s in g.steps:
                if s.status in ("pending", "in_progress") and s.intent == intent:
                    is_on_focus = True
                    break
            if is_on_focus:
                break

        if not is_on_focus and primary.progress > 0.3:
            # Vérifier si c'est une vraie distraction (pas un intent utile)
            intent_in_any_goal = False
            for g in active:
                for s in g.steps:
                    if s.intent == intent:
                        intent_in_any_goal = True
                        break
                if intent_in_any_goal:
                    break

            if not intent_in_any_goal:
                self.stats["inhibitions_applied"] += 1
                self._narrate("inhibition",
                              f"Distraction inhibée: {intent} (focus sur '{primary.title}')")
                result["action"] = "inhibit"
                result["reason"] = f"Distraction: focus sur '{primary.title}' ({primary.progress:.0%})"
                return result

        # Defer : goal secondaire prêt mais primaire pas assez avancé
        if len(active) > 1:
            secondary = active[1]
            sec_next = None
            for s in secondary.steps:
                if s.status in ("pending", "in_progress"):
                    sec_next = s.intent
                    break
            if sec_next == intent and primary.progress < 0.5:
                result["action"] = "defer"
                result["reason"] = f"Goal secondaire différé: priorité au goal primaire ({primary.progress:.0%})"
                return result

        return result

    # ─── 5. MÉMOIRE STRATÉGIQUE ──────────────────────────────────────

    def _learn_strategy_from_goal(self, goal: Goal):
        """Extrait une stratégie d'un goal terminé avec succès."""
        if goal.status != "completed":
            return

        sequence = [s.intent for s in goal.steps if s.status == "done"]
        if len(sequence) < 2:
            return

        context_tags = [goal.source, goal.horizon]
        # Ajouter les tags de drive
        for drive_name, affinity in goal.drive_alignment.items():
            if affinity > 0.5:
                context_tags.append(drive_name.lower())

        # Chercher une stratégie existante avec la même séquence
        for strat in self.strategies:
            if strat.sequence == sequence:
                strat.successes += 1
                strat.confidence = min(1.0, strat.confidence + STRATEGY_CONFIDENCE_BOOST)
                strat.last_used = time.time()
                # Cristallisation ?
                if strat.successes >= STRATEGY_CRYSTALLIZE_THRESHOLD and not strat.crystallized:
                    strat.crystallized = True
                    self.stats["strategies_crystallized"] += 1
                    self._narrate("insight",
                                  f"Stratégie cristallisée: {' → '.join(sequence)}")
                return

        # Nouvelle stratégie
        strat = Strategy(
            id=uuid.uuid4().hex[:8],
            sequence=sequence,
            context_tags=context_tags,
            confidence=0.4,
            successes=1,
        )
        self.strategies.append(strat)

        # FIFO
        if len(self.strategies) > MAX_STRATEGIES:
            # Garder les cristallisées, virer les plus anciennes
            non_crystal = [s for s in self.strategies if not s.crystallized]
            if non_crystal:
                non_crystal.sort(key=lambda s: s.last_used)
                self.strategies.remove(non_crystal[0])

    def _record_strategy_failure(self, sequence: List[str]):
        """Enregistre l'échec d'une séquence."""
        for strat in self.strategies:
            if strat.sequence == sequence:
                strat.failures += 1
                strat.confidence = max(0.0, strat.confidence - STRATEGY_CONFIDENCE_DECAY)
                if strat.crystallized and strat.confidence < 0.2:
                    strat.crystallized = False  # Décristallisation
                    self._narrate("frustration",
                                  f"Stratégie décristallisée: {' → '.join(sequence)}")
                return

    def suggest_strategy(self, context_tags: List[str]) -> Optional[Strategy]:
        """Suggère la meilleure stratégie pour un contexte donné."""
        if not self.strategies or not context_tags:
            return None

        best = None
        best_score = 0.0
        tag_set = set(context_tags)

        for strat in self.strategies:
            if strat.confidence < 0.2:
                continue
            overlap = len(tag_set & set(strat.context_tags))
            if overlap == 0:
                continue
            score = overlap * strat.confidence
            if strat.crystallized:
                score *= 1.5
            if score > best_score:
                best_score = score
                best = strat

        return best

    # ─── 6. MÉMOIRE PROSPECTIVE ──────────────────────────────────────

    def add_trigger(self, condition_type: str, condition_key: str,
                    condition_value: str, action_intent: str,
                    action_context: str = "", one_shot: bool = True,
                    max_triggers: int = 0) -> Optional[ProspectiveTrigger]:
        """Ajoute un trigger différé."""
        if len(self.triggers) >= MAX_PROSPECTIVE_TRIGGERS:
            # Virer le plus ancien one-shot déjà tiré
            expired = [t for t in self.triggers if t.one_shot and t.triggered_count > 0]
            if expired:
                self.triggers.remove(expired[0])
            else:
                return None

        trigger = ProspectiveTrigger(
            id=uuid.uuid4().hex[:8],
            condition_type=condition_type,
            condition_key=condition_key,
            condition_value=condition_value,
            action_intent=action_intent,
            action_context=action_context,
            one_shot=one_shot,
            max_triggers=max_triggers,
        )
        self.triggers.append(trigger)
        return trigger

    def _check_triggers(self, state: Dict[str, Any]) -> List[ProspectiveTrigger]:
        """Vérifie les triggers et retourne ceux qui se sont déclenchés."""
        fired = []
        now = time.time()

        for trigger in self.triggers[:]:
            # Vérifier si déjà expiré
            if trigger.one_shot and trigger.triggered_count > 0:
                continue
            if trigger.max_triggers > 0 and trigger.triggered_count >= trigger.max_triggers:
                continue

            matched = False
            if trigger.condition_type == "state":
                val = state.get(trigger.condition_key, "")
                if str(val) == trigger.condition_value or trigger.condition_value == "any":
                    matched = True
            elif trigger.condition_type == "time":
                try:
                    target_time = float(trigger.condition_value)
                    if now >= target_time:
                        matched = True
                except (ValueError, TypeError):
                    pass
            # "event" triggers sont traités via les handlers bus directement

            if matched:
                trigger.triggered_count += 1
                fired.append(trigger)
                self.stats["triggers_fired"] += 1

        # Nettoyer les one-shots tirés
        self.triggers = [
            t for t in self.triggers
            if not (t.one_shot and t.triggered_count > 0)
            or t in fired  # Garder temporairement les récemment tirés
        ]

        return fired

    # ─── 7. MONOLOGUE INTÉRIEUR ──────────────────────────────────────

    def _narrate(self, category: str, thought: str):
        """Ajoute une entrée au monologue intérieur et publie sur le bus."""
        entry = NarrativeEntry(
            timestamp=time.time(),
            thought=thought,
            category=category,
        )
        self.narrative_log.append(entry)
        if len(self.narrative_log) > MAX_NARRATIVE_LOG:
            self.narrative_log = self.narrative_log[-MAX_NARRATIVE_LOG:]

        # Publier fire-and-forget
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(bus.publish("PREFRONTAL_THOUGHT", {
                "thought": thought,
                "category": category,
                "timestamp": entry.timestamp,
            }))
        except RuntimeError:
            pass  # Pas de boucle asyncio

    def get_narrative(self, n: int = 10) -> List[Dict]:
        """Retourne les n dernières pensées."""
        return [
            {
                "timestamp": e.timestamp,
                "thought": e.thought,
                "category": e.category,
            }
            for e in self.narrative_log[-n:]
        ]

    # ─── CYCLE DE DÉLIBÉRATION ───────────────────────────────────────

    async def start_deliberation(self) -> asyncio.Task:
        """Lance la boucle de délibération (appelé depuis main.py)."""
        self._alive = True
        self._delib_task = asyncio.current_task() or asyncio.ensure_future(
            self._deliberation_loop()
        )
        try:
            await self._deliberation_loop()
        except asyncio.CancelledError:
            self._alive = False
        return self._delib_task

    async def _deliberation_loop(self):
        """Boucle de délibération toutes les DELIBERATION_INTERVAL secondes."""
        while self._alive:
            await asyncio.sleep(DELIBERATION_INTERVAL)
            try:
                self._deliberate()
            except Exception as e:
                logger.warning(f"PREFRONTAL: Erreur délibération: {e}")

    def _deliberate(self):
        """Un cycle complet de délibération."""
        self.stats["deliberation_cycles"] += 1

        # 1. PERCEVOIR — collecter état
        state = self._perceive()

        # 2. ÉVALUER — recalculer priorités
        for g in self.goals:
            if g.status == "active":
                g.priority = self._compute_goal_priority(g)

        # 3. ÉLAGUER — abandonner les non-viables
        for g in self.goals[:]:
            if g.status == "active":
                self._check_goal_abandonment(g)

        # 4. GÉNÉRER — créer de nouveaux goals si slots libres
        self._generate_goals(state)

        # 5. PLANIFIER — steps pour goals sans plan
        for g in self.goals:
            if g.status == "active" and not g.steps:
                self._auto_plan_goal(g)

        # 6. VÉRIFIER — tirer les triggers prospectifs
        fired = self._check_triggers(state)
        for trigger in fired:
            self._narrate("decision", f"Trigger prospectif tiré: {trigger.action_intent}")

        # 7. APPRENDRE — (déjà fait dans on_routine_complete)

        # 8. NARRER — résumé
        active = [g for g in self.goals if g.status == "active"]
        if active:
            active.sort(key=lambda g: g.priority, reverse=True)
            top = active[0]
            self._narrate("decision",
                          f"Délibération: focus sur '{top.title}' (prio={top.priority:.1f}, "
                          f"progrès={top.progress:.0%}). {len(active)} goals actifs.")

        # 9. PERSISTER
        self.save()

        # Publier état
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(bus.publish("PREFRONTAL_STATE", self.get_stats()))
        except RuntimeError:
            pass

    def _perceive(self) -> Dict[str, Any]:
        """Collecte l'état de tous les systèmes pour la délibération."""
        state = {}
        # Budget
        try:
            from core.autonomy_engine import autonomy
            state["budget_remaining"] = (
                autonomy.state.get("daily_budget_points", 200)
                - autonomy.state.get("daily_budget_used", 0)
            )
            state["daily_count"] = autonomy.state.get("daily_count", 0)
            state["error_streak"] = autonomy.state.get("error_streak", 0)
        except Exception:
            state["budget_remaining"] = 200

        # Pulsions
        try:
            from core.desire_engine import desires
            for name, drive in desires.drives.items():
                state[f"drive_{name.lower()}"] = drive.deprivation
        except Exception:
            pass

        # Menace reptilienne
        try:
            from core.reptilian_core import reptile
            state["threat_level"] = reptile.threat_level
        except Exception:
            state["threat_level"] = 0.0

        # Cohérence cardiaque
        try:
            from core.cardiac_engine import heart
            state["cardiac_coherence"] = heart.compute_coherence()
            state["cardiac_emotion"] = heart.current_emotion
        except Exception:
            pass

        # Mode stratégique
        try:
            from core.self_awareness import awareness
            if hasattr(awareness, 'get_strategic_mode'):
                state["strategic_mode"] = awareness.get_strategic_mode()
        except Exception:
            pass

        return state

    def _generate_goals(self, state: Dict[str, Any]):
        """Génère automatiquement des goals basés sur l'état courant."""
        active_count = len([g for g in self.goals if g.status == "active"])
        if active_count >= MAX_GOALS:
            return

        # Pulsion frustrée → goal
        try:
            from core.desire_engine import desires
            for name, drive in desires.drives.items():
                if drive.deprivation >= 40 and active_count < MAX_GOALS:
                    # Vérifier qu'on n'a pas déjà un goal pour cette pulsion
                    already = any(
                        g.status == "active" and name.lower() in g.title.lower()
                        for g in self.goals
                    )
                    if already:
                        continue

                    routines = _DRIVE_ROUTINE_MAP.get(name, ["VEILLE_SILENCIEUSE"])
                    steps = [
                        GoalStep(intent=r, description=f"Satisfaire {name}")
                        for r in routines[:3]
                    ]
                    goal = Goal(
                        id=uuid.uuid4().hex[:8],
                        title=f"Satisfaire pulsion: {name}",
                        horizon="short",
                        priority=4.5,
                        source="desire",
                        steps=steps,
                        cost_estimated=len(steps) * 3,
                        drive_alignment={name: 1.0},
                    )
                    self.goals.append(goal)
                    self.stats["goals_created"] += 1
                    active_count += 1
                    self._narrate("goal", f"Pulsion {name} frustrée (déprivation={drive.deprivation:.0f}). Goal créé.")
                    self._publish_goal_event("PREFRONTAL_GOAL_CREATED", goal)
        except Exception as e:
            logger.warning(f"PREFRONTAL: _generate_goals erreur pulsions: {e}")

        # Stratégie cristallisée → goal automatique
        if active_count < MAX_GOALS:
            state_tags = []
            if state.get("error_streak", 0) > 2:
                state_tags.append("error_streak")
            if state.get("threat_level", 0) > 3:
                state_tags.append("high_threat")
            if state.get("budget_remaining", 200) < 60:
                state_tags.append("low_budget")

            suggested = self.suggest_strategy(state_tags)
            if suggested and suggested.crystallized:
                # Vérifier qu'on n'a pas déjà cette séquence
                seq_str = "→".join(suggested.sequence)
                already = any(
                    g.status == "active" and seq_str in g.title
                    for g in self.goals
                )
                if not already:
                    steps = [
                        GoalStep(intent=s, description=f"Étape habitude: {s}")
                        for s in suggested.sequence
                    ]
                    goal = Goal(
                        id=uuid.uuid4().hex[:8],
                        title=f"Habitude: {seq_str}",
                        horizon="immediate",
                        priority=5.0,
                        source="strategy",
                        steps=steps,
                        cost_estimated=len(steps) * 2,
                    )
                    self.goals.append(goal)
                    self.stats["goals_created"] += 1
                    suggested.last_used = time.time()
                    self._narrate("decision", f"Habitude cristallisée déclenchée: {seq_str}")
                    self._publish_goal_event("PREFRONTAL_GOAL_CREATED", goal)

        # Performance mode consolidation → goal redressement
        try:
            from core.self_awareness import awareness
            if hasattr(awareness, 'get_strategic_mode'):
                mode = awareness.get_strategic_mode()
                if mode == "consolidation" and active_count < MAX_GOALS:
                    already = any(
                        g.status == "active" and "Redresser" in g.title
                        for g in self.goals
                    )
                    if not already:
                        goal = Goal(
                            id=uuid.uuid4().hex[:8],
                            title="Redresser performance",
                            horizon="immediate",
                            priority=7.0,
                            source="meta",
                            steps=[
                                GoalStep(intent="AUDIT_STRUCTURE", description="Audit santé"),
                                GoalStep(intent="MEMORY_CONSOLIDATION", description="Consolider mémoire"),
                                GoalStep(intent="REFACTORING_AUDIT", description="Refactorer si besoin"),
                            ],
                            cost_estimated=9,
                            drive_alignment={"STABILITE": 0.9, "MAITRISE": 0.7},
                        )
                        self.goals.append(goal)
                        self.stats["goals_created"] += 1
                        self._narrate("goal", "Mode consolidation → goal redressement créé")
                        self._publish_goal_event("PREFRONTAL_GOAL_CREATED", goal)
        except Exception:
            pass

    def _auto_plan_goal(self, goal: Goal):
        """Génère des steps automatiques pour un goal sans plan."""
        # Basé sur le source du goal
        if goal.source == "gap":
            goal.steps = [
                GoalStep(intent="VEILLE_SILENCIEUSE", description=f"Rechercher: {goal.title}"),
                GoalStep(intent="EXPANSION_CODE", description=f"Appliquer: {goal.title}"),
            ]
        elif goal.source == "council":
            goal.steps = [
                GoalStep(intent="EXPANSION_CODE", description=f"Implémenter: {goal.title}"),
                GoalStep(intent="SECURITY_AUDIT", description="Vérifier qualité"),
            ]
        else:
            goal.steps = [
                GoalStep(intent="VEILLE_SILENCIEUSE", description=goal.title),
            ]

    # ─── API PUBLIQUE ────────────────────────────────────────────────

    def on_routine_complete(self, intent: str, status: str,
                           quality: float, preview: str = ""):
        """Feedback post-routine. Avance le premier goal dont le step correspond."""
        advanced = False
        for goal in self.goals:
            if goal.status != "active" or advanced:
                continue

            for i, step in enumerate(goal.steps):
                if step.intent == intent and step.status in ("pending", "in_progress"):
                    if status == "success" or quality >= 0.6:
                        step.status = "done"
                        step.result_summary = preview[:200] if preview else f"OK (q={quality:.1f})"
                        goal.last_advanced = time.time()
                        goal.cost_spent += 1
                        if i == goal.current_step:
                            goal.current_step = min(i + 1, len(goal.steps) - 1)
                    else:
                        step.status = "failed"
                        step.result_summary = f"Échec (q={quality:.1f})"
                        goal.cost_spent += 1

                    self._update_goal_progress(goal)
                    self._check_goal_completion(goal)
                    advanced = True
                    break

    def on_routine_start(self, intent: str):
        """Notification pre-routine. Marque le step comme in_progress."""
        self._current_routine_intent = intent
        for goal in self.goals:
            if goal.status != "active":
                continue
            for step in goal.steps:
                if step.intent == intent and step.status == "pending":
                    step.status = "in_progress"
                    break

    def get_deliberation_context(self) -> str:
        """Retourne un contexte textuel pour injection dans purpose_context."""
        wm = self.get_working_memory()
        if not wm:
            return ""

        parts = ["[PRÉFRONTAL]"]
        for i, slot in enumerate(wm):
            marker = ["🎯", "📌", "📎"][i] if i < 3 else "·"
            parts.append(
                f"{marker} Goal #{i+1}: {slot['goal_title']} "
                f"({slot['progress']:.0%}) → {slot['next_intent'] or '?'}"
            )
        # Dernière pensée
        if self.narrative_log:
            last = self.narrative_log[-1]
            parts.append(f"💭 {last.thought[:100]}")

        return "\n".join(parts)

    def get_goals_summary(self) -> List[Dict]:
        """Résumé de tous les goals pour l'API."""
        return [
            {
                "id": g.id,
                "title": g.title,
                "horizon": g.horizon,
                "priority": g.priority,
                "progress": g.progress,
                "status": g.status,
                "source": g.source,
                "steps_done": sum(1 for s in g.steps if s.status == "done"),
                "steps_total": len(g.steps),
                "cost_spent": g.cost_spent,
            }
            for g in self.goals
        ]

    def get_stats(self) -> Dict:
        """Retourne les statistiques complètes pour l'API."""
        active = [g for g in self.goals if g.status == "active"]
        return {
            "goals_active": len(active),
            "goals_total": len(self.goals),
            "goals_completed": self.stats.get("goals_completed", 0),
            "goals_abandoned": self.stats.get("goals_abandoned", 0),
            "strategies_total": len(self.strategies),
            "strategies_crystallized": self.stats.get("strategies_crystallized", 0),
            "triggers_active": len([t for t in self.triggers if not (t.one_shot and t.triggered_count > 0)]),
            "inhibitions_applied": self.stats.get("inhibitions_applied", 0),
            "overrides_applied": self.stats.get("overrides_applied", 0),
            "deliberation_cycles": self.stats.get("deliberation_cycles", 0),
            "narrative_entries": len(self.narrative_log),
            "working_memory": self.get_working_memory(),
            "goals": self.get_goals_summary(),
            "last_thought": self.narrative_log[-1].thought[:120] if self.narrative_log else "",
        }

    def stop(self):
        """Arrête la délibération proprement."""
        self._alive = False
        if self._delib_task and not self._delib_task.done():
            self._delib_task.cancel()
        self.save()

    # ─── PERSISTANCE ─────────────────────────────────────────────────

    def save(self):
        """Sauvegarde atomique de l'état."""
        state = {
            "goals": [self._goal_to_dict(g) for g in self.goals],
            "strategies": [asdict(s) for s in self.strategies],
            "triggers": [asdict(t) for t in self.triggers],
            "stats": self.stats,
            "narrative_log": [
                {
                    "timestamp": e.timestamp,
                    "thought": e.thought,
                    "category": e.category,
                    "context": e.context,
                }
                for e in self.narrative_log[-MAX_NARRATIVE_LOG:]
            ],
        }
        try:
            os.makedirs(os.path.dirname(PREFRONTAL_STATE_FILE), exist_ok=True)
            tmp = PREFRONTAL_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, PREFRONTAL_STATE_FILE)
        except Exception as e:
            logger.warning(f"PREFRONTAL: Échec sauvegarde: {e}")

    def _load(self):
        """Charge l'état depuis le fichier JSON."""
        try:
            if not os.path.exists(PREFRONTAL_STATE_FILE):
                return
            with open(PREFRONTAL_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

            # Goals
            for gd in state.get("goals", []):
                steps = [
                    GoalStep(**s) for s in gd.pop("steps", [])
                ]
                goal = Goal(steps=steps, **gd)
                self.goals.append(goal)

            # Strategies
            for sd in state.get("strategies", []):
                self.strategies.append(Strategy(**sd))

            # Triggers
            for td in state.get("triggers", []):
                self.triggers.append(ProspectiveTrigger(**td))

            # Stats
            saved_stats = state.get("stats", {})
            for k, v in saved_stats.items():
                if k in self.stats:
                    self.stats[k] = v

            # Narrative
            for nd in state.get("narrative_log", []):
                self.narrative_log.append(NarrativeEntry(
                    timestamp=nd.get("timestamp", 0),
                    thought=nd.get("thought", ""),
                    category=nd.get("category", ""),
                    context=nd.get("context", {}),
                ))

        except Exception as e:
            logger.warning(f"PREFRONTAL: Échec chargement: {e}")

    def _goal_to_dict(self, goal: Goal) -> Dict:
        """Sérialise un Goal en dict."""
        d = {
            "id": goal.id,
            "title": goal.title,
            "horizon": goal.horizon,
            "priority": goal.priority,
            "progress": goal.progress,
            "current_step": goal.current_step,
            "source": goal.source,
            "created_at": goal.created_at,
            "deadline": goal.deadline,
            "status": goal.status,
            "abandon_reason": goal.abandon_reason,
            "cost_spent": goal.cost_spent,
            "cost_estimated": goal.cost_estimated,
            "drive_alignment": goal.drive_alignment,
            "last_advanced": goal.last_advanced,
            "steps": [asdict(s) for s in goal.steps],
        }
        return d

    # ─── Helpers ──────────────────────────────────────────────────────

    def _publish_goal_event(self, event_type: str, goal: Goal):
        """Publie un événement goal sur le bus (fire-and-forget)."""
        try:
            loop = asyncio.get_running_loop()
            payload = {
                "goal_id": goal.id,
                "title": goal.title,
                "horizon": goal.horizon,
                "source": goal.source,
            }
            if event_type == "PREFRONTAL_GOAL_ABANDONED":
                payload["reason"] = goal.abandon_reason
            loop.create_task(bus.publish(event_type, payload))
        except RuntimeError:
            pass


# ─── Singleton global ─────────────────────────────────────────────────
prefrontal = PrefrontalCortex()
