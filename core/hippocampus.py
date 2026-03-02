"""
Hippocampe Synthetique — Memoire Episodique pour la Continuite du Soi.

Capture les moments significatifs comme des episodes narratifs avec contexte
affectif complet, les consolide en arcs narratifs, et restitue un recit de
continuite au redemarrage.

0 LLM — 100% deterministe.
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

from core.event_bus.bus import bus

logger = logging.getLogger("hippocampus")

# ─── Constantes ──────────────────────────────────────────────────────────────

HIPPOCAMPUS_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "hippocampus_state.json"
)

MAX_EPISODES = 500
MAX_ARCS = 50
MAX_ARCS_MARKED = 20
SALIENCE_THRESHOLD = 0.3
PURGE_EPISODES_DAYS = 7
PURGE_ARCS_DAYS = 30
AUTOSAVE_INTERVAL = 5
CONSOLIDATION_INTERVAL = 10

# Saillance de base par type d'evenement
BASE_SALIENCE: Dict[str, float] = {
    "routine_success": 0.15,
    "routine_failure": 0.35,
    "veto": 0.4,
    "veto_override": 0.55,
    "loop_breaker": 0.6,
    "council_forced": 0.65,
    "goal_complete": 0.5,
    "goal_abandoned": 0.4,
    "dopamine_surge": 0.5,
    "dopamine_dip": 0.35,
    "reptilian_alert": 0.55,
    "cognitive_transition": 0.45,
    "prediction_error": 0.4,
    "tissue_emergence": 0.5,
    "neural_compilation": 0.35,
}

# Emotions considerees "fortes" pour le bonus de saillance
# V2: ajout frustration/determination/enthousiasme (emotions courantes mais intenses)
STRONG_EMOTIONS = {
    "rage", "panique", "extase", "desespoir", "euphorie", "terreur",
    "frustration", "determination", "enthousiasme",
}


# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class Episode:
    """Unite atomique de memoire episodique."""
    # Identite
    id: str = ""
    # What
    event_type: str = ""
    summary: str = ""
    # When
    timestamp: float = 0.0
    routine_number: int = 0
    budget_used: float = 0.0
    # Where
    intent: str = ""
    agent: str = ""
    error_streak: int = 0
    daily_count: int = 0
    # How
    quality_score: float = 0.0
    failure_type: str = ""
    detail: str = ""
    # Feel
    cardiac_emotion: str = ""
    cardiac_bpm: float = 65.0
    dopamine_level: float = 0.5
    dominant_drive: str = ""
    drive_deprivation: float = 0.0
    cognitive_state: str = ""
    threat_level: float = 0.0
    # Saillance
    salience: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Episode":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class NarrativeArc:
    """Sequence consolidee d'episodes."""
    id: str = ""
    arc_type: str = ""  # struggle | breakthrough | transition | crisis | mastery
    title: str = ""
    narrative: str = ""
    episode_ids: List[str] = field(default_factory=list)
    emotional_arc: str = ""
    timestamp: float = 0.0
    intent: str = ""
    marked: bool = False  # arcs a saillance >= 0.8 gardes indefiniment

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NarrativeArc":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


# ─── Singleton Hippocampus ───────────────────────────────────────────────────

class Hippocampus:
    """Memoire episodique — capture, consolide, restitue les moments significatifs."""

    _instance: Optional["Hippocampus"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._episodes: List[Episode] = []
        self._arcs: List[NarrativeArc] = []
        self._known_intents: set = set()
        self._episode_count_since_save: int = 0
        self._episode_count_since_consolidation: int = 0
        self._subscribed: bool = False
        self._last_cognitive_state: str = ""
        self._session_start: float = time.time()
        self._stats: Dict[str, int] = {
            "episodes_encoded": 0,
            "episodes_rejected": 0,
            "arcs_created": 0,
        }

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    # ─── Initialisation (appele depuis main.py) ─────────────────────────

    def init(self):
        """Souscrit au bus evenementiel."""
        self._subscribe_events()
        self._session_start = time.time()
        logger.info(
            f"HIPPOCAMPE: Memoire episodique active "
            f"({len(self._episodes)} episodes, {len(self._arcs)} arcs)."
        )

    def _subscribe_events(self):
        """Souscriptions bus — 10 handlers."""
        if self._subscribed:
            return
        self._subscribed = True

        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_surge)
        bus.subscribe("DOPAMINE_DIP", self._on_dopamine_dip)
        bus.subscribe("PREFRONTAL_GOAL_COMPLETE", self._on_goal_complete)
        bus.subscribe("PREFRONTAL_GOAL_ABANDONED", self._on_goal_abandoned)
        bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
        bus.subscribe("CORPUS_CALLOSUM_STATE", self._on_cognitive_state)
        bus.subscribe("INNER_VOICE_PREDICTION_RESOLVED", self._on_prediction_resolved)
        bus.subscribe("TISSUE_PATTERN_EMERGED", self._on_tissue_pattern)
        bus.subscribe("NEURAL_COMPILED", self._on_neural_compiled)

    # ─── Capture affective ───────────────────────────────────────────────

    def _capture_affect(self) -> Dict[str, Any]:
        """Capture instantanee de l'etat affectif (imports locaux, try/except)."""
        affect: Dict[str, Any] = {
            "cardiac_emotion": "",
            "cardiac_bpm": 65.0,
            "dopamine_level": 0.5,
            "dominant_drive": "",
            "drive_deprivation": 0.0,
            "cognitive_state": "",
            "threat_level": 0.0,
        }

        # Cardiac
        try:
            from core.cardiac_engine import heart
            affect["cardiac_emotion"] = heart.current_emotion
            affect["cardiac_bpm"] = heart.bpm
            # Transition emotionnelle (pour bonus saillance)
            if heart._prev_emotion and heart._prev_emotion != heart.current_emotion:
                affect["prev_emotion"] = heart._prev_emotion
                affect["emotion_cause"] = heart._emotion_cause
        except Exception:
            pass

        # Dopamine
        try:
            from core.dopamine_system import dopamine
            affect["dopamine_level"] = dopamine.dopamine_level
        except Exception:
            pass

        # Desire
        try:
            from core.desire_engine import desires
            top_drives = sorted(
                desires.drives.values(),
                key=lambda d: d.deprivation, reverse=True
            )
            if top_drives:
                affect["dominant_drive"] = top_drives[0].name
                affect["drive_deprivation"] = top_drives[0].deprivation
        except Exception:
            pass

        # Corpus Callosum
        try:
            from core.corpus_callosum import callosum
            affect["cognitive_state"] = callosum.cognitive_state
        except Exception:
            pass

        # Reptilian
        try:
            from core.reptilian_core import reptile
            affect["threat_level"] = reptile.threat_level
        except Exception:
            pass

        return affect

    # ─── Calcul de saillance ─────────────────────────────────────────────

    def _compute_salience(self, event_type: str, intent: str,
                          affect: Dict[str, Any], error_streak: int = 0) -> float:
        """Calcule la saillance d'un evenement. Base + bonus, cap a 1.0."""
        base = BASE_SALIENCE.get(event_type, 0.3)

        bonus = 0.0

        # Bonus error_streak : +0.08/pt, cap +0.3
        if error_streak > 0:
            bonus += min(error_streak * 0.08, 0.3)

        # Bonus premier intent : +0.15
        if intent and intent not in self._known_intents:
            bonus += 0.15

        # Bonus emotion forte : +0.1
        emotion = affect.get("cardiac_emotion", "")
        if emotion in STRONG_EMOTIONS:
            bonus += 0.1

        # Bonus succes apres streak >= 3 : +0.2
        if event_type == "routine_success" and error_streak >= 3:
            bonus += 0.2

        # Bonus dopamine surge : +0.1 si dopamine > 0.7
        dopamine = affect.get("dopamine_level", 0.5)
        if dopamine > 0.7:
            bonus += 0.1

        # Bonus transition emotionnelle : +0.1 si changement d'emotion
        if affect.get("emotion_cause") and affect.get("prev_emotion"):
            bonus += 0.1

        return min(base + bonus, 1.0)

    # ─── Encodage d'episode ──────────────────────────────────────────────

    def _encode_episode(self, event_type: str, intent: str = "",
                        agent: str = "", error_streak: int = 0,
                        quality_score: float = 0.0, failure_type: str = "",
                        detail: str = "", daily_count: int = 0,
                        routine_number: int = 0, budget_used: float = 0.0) -> Optional[Episode]:
        """Encode un episode si la saillance depasse le seuil."""
        affect = self._capture_affect()
        salience = self._compute_salience(event_type, intent, affect, error_streak)

        if salience < SALIENCE_THRESHOLD:
            self._stats["episodes_rejected"] += 1
            return None

        # Marquer l'intent comme connu
        if intent:
            self._known_intents.add(intent)

        # Generer le resume deterministe
        summary = self._generate_summary(event_type, intent, agent,
                                         quality_score, failure_type, error_streak)

        episode = Episode(
            id=str(uuid.uuid4())[:8],
            event_type=event_type,
            summary=summary,
            timestamp=time.time(),
            routine_number=routine_number,
            budget_used=budget_used,
            intent=intent,
            agent=agent,
            error_streak=error_streak,
            daily_count=daily_count,
            quality_score=quality_score,
            failure_type=failure_type,
            detail=detail,
            cardiac_emotion=affect["cardiac_emotion"],
            cardiac_bpm=affect["cardiac_bpm"],
            dopamine_level=affect["dopamine_level"],
            dominant_drive=affect["dominant_drive"],
            drive_deprivation=affect["drive_deprivation"],
            cognitive_state=affect["cognitive_state"],
            threat_level=affect["threat_level"],
            salience=salience,
        )

        self._episodes.append(episode)
        self._stats["episodes_encoded"] += 1

        # FIFO : garder MAX_EPISODES
        if len(self._episodes) > MAX_EPISODES:
            self._episodes = self._episodes[-MAX_EPISODES:]

        # Autosave
        self._episode_count_since_save += 1
        if self._episode_count_since_save >= AUTOSAVE_INTERVAL:
            self._save()
            self._episode_count_since_save = 0

        # Consolidation periodique
        self._episode_count_since_consolidation += 1
        if self._episode_count_since_consolidation >= CONSOLIDATION_INTERVAL:
            self.consolidate()
            self._episode_count_since_consolidation = 0

        return episode

    def _generate_summary(self, event_type: str, intent: str, agent: str,
                          quality_score: float, failure_type: str,
                          error_streak: int) -> str:
        """Resume deterministe d'un episode."""
        if event_type == "routine_success":
            return f"Succes {intent} par {agent} (qualite {quality_score:.1f})."
        elif event_type == "routine_failure":
            return f"Echec {intent} par {agent} ({failure_type}, streak {error_streak})."
        elif event_type == "veto":
            return f"Veto sur {intent} ({agent})."
        elif event_type == "veto_override":
            return f"Override veto sur {intent} ({agent})."
        elif event_type == "loop_breaker":
            return f"Loop breaker declenche (streak {error_streak})."
        elif event_type == "council_forced":
            return f"Council force (streak {error_streak})."
        elif event_type == "goal_complete":
            return f"Objectif atteint : {intent}."
        elif event_type == "goal_abandoned":
            return f"Objectif abandonne : {intent}."
        elif event_type == "dopamine_surge":
            return f"Pic dopaminergique ({intent})."
        elif event_type == "dopamine_dip":
            return f"Creux dopaminergique ({intent})."
        elif event_type == "reptilian_alert":
            return f"Alerte reptilienne ({intent})."
        elif event_type == "cognitive_transition":
            return f"Transition cognitive : {intent}."
        elif event_type == "prediction_error":
            return f"Erreur de prediction sur {intent}."
        return f"Evenement {event_type} ({intent})."

    # ─── Methodes publiques d'enregistrement ─────────────────────────────

    def record_veto(self, intent: str, agent: str, reason: str):
        """Enregistre un veto."""
        self._encode_episode(
            event_type="veto",
            intent=intent,
            agent=agent,
            detail=reason,
        )

    def record_veto_override(self, intent: str, agent: str, reason: str):
        """Enregistre un override de veto."""
        self._encode_episode(
            event_type="veto_override",
            intent=intent,
            agent=agent,
            detail=reason,
        )

    def record_loop_breaker(self, action: str, intent: str, error_streak: int):
        """Enregistre un declenchement du loop breaker."""
        self._encode_episode(
            event_type="loop_breaker",
            intent=intent,
            error_streak=error_streak,
            detail=f"action={action}",
        )

    def record_council_forced(self, error_streak: int):
        """Enregistre un council force."""
        self._encode_episode(
            event_type="council_forced",
            intent="COUNCIL_DEBATE",
            error_streak=error_streak,
        )

    # ─── Handlers bus ────────────────────────────────────────────────────

    def _on_routine_complete(self, data):
        """Handler AUTONOMY_ROUTINE_COMPLETE."""
        if not isinstance(data, dict):
            return
        success = data.get("success", False)
        event_type = "routine_success" if success else "routine_failure"
        self._encode_episode(
            event_type=event_type,
            intent=data.get("intent", ""),
            agent=data.get("agent", ""),
            error_streak=data.get("error_streak", 0),
            quality_score=data.get("quality_score", 0.0),
            failure_type=data.get("failure_type", ""),
            daily_count=data.get("daily_count", 0),
            routine_number=data.get("routine_number", 0),
            budget_used=data.get("budget_used", 0.0),
        )

    def _on_dopamine_surge(self, data):
        """Handler DOPAMINE_SURGE."""
        if not isinstance(data, dict):
            return
        self._encode_episode(
            event_type="dopamine_surge",
            intent=data.get("intent", ""),
            detail=f"reward={data.get('reward', 0):.2f}" if isinstance(data.get('reward'), (int, float)) else "",
        )

    def _on_dopamine_dip(self, data):
        """Handler DOPAMINE_DIP."""
        if not isinstance(data, dict):
            return
        self._encode_episode(
            event_type="dopamine_dip",
            intent=data.get("intent", ""),
        )

    def _on_goal_complete(self, data):
        """Handler PREFRONTAL_GOAL_COMPLETE."""
        if not isinstance(data, dict):
            return
        self._encode_episode(
            event_type="goal_complete",
            intent=data.get("title", data.get("goal_id", "")),
            detail=data.get("reason", ""),
        )

    def _on_goal_abandoned(self, data):
        """Handler PREFRONTAL_GOAL_ABANDONED."""
        if not isinstance(data, dict):
            return
        self._encode_episode(
            event_type="goal_abandoned",
            intent=data.get("title", data.get("goal_id", "")),
            detail=data.get("reason", ""),
        )

    def _on_reptilian_alert(self, data):
        """Handler REPTILIAN_ALERT — seulement FREEZE et FIGHT."""
        if not isinstance(data, dict):
            return
        reaction = data.get("reaction", "")
        if reaction not in ("FREEZE", "FIGHT"):
            return
        self._encode_episode(
            event_type="reptilian_alert",
            intent=reaction,
            detail=f"threat={data.get('threat_level', 0):.1f}",
        )

    def _on_cognitive_state(self, data):
        """Handler CORPUS_CALLOSUM_STATE — encode si etat change."""
        if not isinstance(data, dict):
            return
        new_state = data.get("cognitive_state", "")
        if new_state and new_state != self._last_cognitive_state:
            old_state = self._last_cognitive_state
            self._last_cognitive_state = new_state
            if old_state:  # Ne pas encoder au premier etat
                self._encode_episode(
                    event_type="cognitive_transition",
                    intent=f"{old_state} -> {new_state}",
                )

    def _on_prediction_resolved(self, data):
        """Handler INNER_VOICE_PREDICTION_RESOLVED — si prediction incorrecte."""
        if not isinstance(data, dict):
            return
        if data.get("correct", True):
            return  # Pas d'episode pour les predictions correctes
        self._encode_episode(
            event_type="prediction_error",
            intent=data.get("prediction", ""),
            detail=f"outcome={data.get('outcome', '')}",
        )

    def _on_tissue_pattern(self, data):
        """Handler TISSUE_PATTERN_EMERGED — pattern cellulaire emergent."""
        if not isinstance(data, dict):
            return
        genome = data.get("genome", "?")
        freq = data.get("frequency", 0)
        self._encode_episode(
            event_type="tissue_emergence",
            intent="TISSUE_PATTERN",
            detail=f"genome={genome}, freq={freq:.0%}" if isinstance(freq, (int, float)) else f"genome={genome}",
        )

    def _on_neural_compiled(self, data):
        """Handler NEURAL_COMPILED — nouvelles regles distillees."""
        if not isinstance(data, dict):
            return
        created = data.get("rules_created", 0)
        total = data.get("rules_total", 0)
        if created > 0:
            self._encode_episode(
                event_type="neural_compilation",
                intent="NEURAL_COMPILE",
                detail=f"{created} nouvelle(s) regle(s), {total} au total",
            )

    # ─── Consolidation en arcs narratifs ─────────────────────────────────

    def consolidate(self):
        """Consolide les episodes recents en arcs narratifs. 100% deterministe."""
        # Detecter les patterns si assez d'episodes
        if len(self._episodes) >= 3:
            existing_episode_ids = set()
            for arc in self._arcs:
                existing_episode_ids.update(arc.episode_ids)

            unconsolidated = [e for e in self._episodes if e.id not in existing_episode_ids]
            if len(unconsolidated) >= 3:
                self._detect_struggle(unconsolidated)
                self._detect_breakthrough(unconsolidated)
                self._detect_transition(unconsolidated)
                self._detect_crisis(unconsolidated)

        # FIFO arcs : garder MAX_ARCS (toujours, meme sans nouveaux arcs)
        if len(self._arcs) > MAX_ARCS:
            marked = [a for a in self._arcs if a.marked]
            unmarked = [a for a in self._arcs if not a.marked]
            # Garder les marques (max MAX_ARCS_MARKED) + les plus recents unmarked
            marked = marked[-MAX_ARCS_MARKED:]
            remaining_slots = MAX_ARCS - len(marked)
            unmarked = unmarked[-remaining_slots:] if remaining_slots > 0 else []
            self._arcs = sorted(marked + unmarked, key=lambda a: a.timestamp)

    # Categories d'intents pour detection cross-intent de struggles
    _INTENT_CATEGORIES = {
        "code": {"EXPANSION_CODE", "REFACTOR_RANDOM"},
        "knowledge": {"VEILLE_SILENCIEUSE", "DROPZONE_SCAN", "MEMORY_CONSOLIDATION"},
        "governance": {"COUNCIL_DEBATE", "SECURITY_AUDIT"},
        "maintenance": {"AUDIT_STRUCTURE", "MEMORY_CLEANUP", "NEURAL_COMPILE"},
    }

    def _detect_struggle(self, episodes: List[Episode]):
        """Detecte >= 3 echecs consecutifs sur le meme intent OU catégorie (fenetre 2h)."""
        # Grouper par intent exact
        by_intent: Dict[str, List[Episode]] = {}
        for ep in episodes:
            if ep.event_type in ("routine_failure", "routine_success") and ep.intent:
                by_intent.setdefault(ep.intent, []).append(ep)

        # Grouper aussi par catégorie (cross-intent)
        for cat_name, intents in self._INTENT_CATEGORIES.items():
            cat_key = f"_cat_{cat_name}"
            cat_eps = []
            for ep in episodes:
                if ep.event_type in ("routine_failure", "routine_success") and ep.intent in intents:
                    cat_eps.append(ep)
            if len(cat_eps) >= 3 and cat_key not in by_intent:
                by_intent[cat_key] = cat_eps

        for intent, eps in by_intent.items():
            eps_sorted = sorted(eps, key=lambda e: e.timestamp)
            # Chercher des sequences de failures
            streak = []
            success_after = None
            for ep in eps_sorted:
                if ep.event_type == "routine_failure":
                    streak.append(ep)
                elif ep.event_type == "routine_success" and len(streak) >= 3:
                    success_after = ep
                    break
                else:
                    if len(streak) >= 3:
                        break
                    streak = []

            if len(streak) < 3:
                continue

            # Verifier fenetre 2h
            window = streak[-1].timestamp - streak[0].timestamp
            if window > 7200:
                continue

            arc_eps = streak + ([success_after] if success_after else [])
            emotions = [e.cardiac_emotion for e in arc_eps if e.cardiac_emotion]
            emotional_arc_str = " -> ".join(dict.fromkeys(emotions)) if emotions else ""

            if success_after:
                narrative = (
                    f"J'ai echoue {len(streak)} fois sur {intent} "
                    f"({emotional_arc_str}), puis j'ai reussi "
                    f"(qualite {success_after.quality_score:.1f})."
                )
            else:
                narrative = (
                    f"J'ai echoue {len(streak)} fois sur {intent} "
                    f"({emotional_arc_str}), sans succes."
                )

            avg_salience = sum(e.salience for e in arc_eps) / len(arc_eps)
            display_name = intent.replace("_cat_", "catégorie ") if intent.startswith("_cat_") else intent
            arc = NarrativeArc(
                id=str(uuid.uuid4())[:8],
                arc_type="struggle",
                title=f"Lutte sur {display_name}",
                narrative=narrative,
                episode_ids=[e.id for e in arc_eps],
                emotional_arc=emotional_arc_str,
                timestamp=time.time(),
                intent=intent,
                marked=avg_salience >= 0.8,
            )
            self._arcs.append(arc)
            self._stats["arcs_created"] += 1

    def _detect_breakthrough(self, episodes: List[Episode]):
        """Detecte un succes avec error_streak >= 3 + echecs precedents."""
        for ep in episodes:
            if ep.event_type != "routine_success" or ep.error_streak < 3:
                continue

            # Chercher les echecs precedents sur le meme intent
            failures = [
                e for e in self._episodes
                if e.event_type == "routine_failure"
                and e.intent == ep.intent
                and e.timestamp < ep.timestamp
                and ep.timestamp - e.timestamp < 7200
            ]
            if not failures:
                continue

            arc_eps = failures[-5:] + [ep]  # Max 5 echecs + le succes
            emotions = [e.cardiac_emotion for e in arc_eps if e.cardiac_emotion]
            emotional_arc_str = " -> ".join(dict.fromkeys(emotions)) if emotions else ""

            narrative = (
                f"Apres {len(failures)} echec(s), percee sur {ep.intent} "
                f"(qualite {ep.quality_score:.1f}). "
                f"Arc emotionnel: {emotional_arc_str}."
            )

            avg_salience = sum(e.salience for e in arc_eps) / len(arc_eps)
            arc = NarrativeArc(
                id=str(uuid.uuid4())[:8],
                arc_type="breakthrough",
                title=f"Percee sur {ep.intent}",
                narrative=narrative,
                episode_ids=[e.id for e in arc_eps],
                emotional_arc=emotional_arc_str,
                timestamp=time.time(),
                intent=ep.intent,
                marked=avg_salience >= 0.8,
            )
            self._arcs.append(arc)
            self._stats["arcs_created"] += 1

    def _detect_transition(self, episodes: List[Episode]):
        """Detecte un changement d'etat cognitif + episodes dans les 5 min."""
        for ep in episodes:
            if ep.event_type != "cognitive_transition":
                continue

            # Episodes dans les 5 min autour
            window = 300  # 5 min
            nearby = [
                e for e in self._episodes
                if abs(e.timestamp - ep.timestamp) <= window
                and e.id != ep.id
            ]
            if not nearby:
                continue

            arc_eps = [ep] + nearby[:5]
            narrative = (
                f"Changement d'etat cognitif ({ep.intent}). "
                f"{len(nearby)} evenements lies."
            )

            arc = NarrativeArc(
                id=str(uuid.uuid4())[:8],
                arc_type="transition",
                title=f"Transition {ep.intent}",
                narrative=narrative,
                episode_ids=[e.id for e in arc_eps],
                emotional_arc="",
                timestamp=time.time(),
                intent=ep.intent,
                marked=False,
            )
            self._arcs.append(arc)
            self._stats["arcs_created"] += 1

    def _detect_crisis(self, episodes: List[Episode]):
        """Detecte alerte reptilienne + echecs/vetos/loop_breaker dans les 10 min."""
        for ep in episodes:
            if ep.event_type != "reptilian_alert":
                continue

            # Episodes de crise dans les 10 min
            window = 600  # 10 min
            crisis_types = {"routine_failure", "veto", "loop_breaker", "council_forced"}
            nearby_crisis = [
                e for e in self._episodes
                if e.event_type in crisis_types
                and abs(e.timestamp - ep.timestamp) <= window
                and e.id != ep.id
            ]
            if not nearby_crisis:
                continue

            arc_eps = [ep] + nearby_crisis[:5]
            narrative = (
                f"Alerte reptilienne declenchee. "
                f"{len(nearby_crisis)} echec(s)/vetos, "
                f"menace={ep.detail}."
            )

            avg_salience = sum(e.salience for e in arc_eps) / len(arc_eps)
            arc = NarrativeArc(
                id=str(uuid.uuid4())[:8],
                arc_type="crisis",
                title=f"Crise ({ep.intent})",
                narrative=narrative,
                episode_ids=[e.id for e in arc_eps],
                emotional_arc="",
                timestamp=time.time(),
                intent=ep.intent,
                marked=avg_salience >= 0.8,
            )
            self._arcs.append(arc)
            self._stats["arcs_created"] += 1

    # ─── Methodes de restitution ─────────────────────────────────────────

    def get_session_narrative(self, n: int = 5) -> List[Dict[str, Any]]:
        """Top N episodes les plus saillants de la session courante, tries chronologiquement."""
        session_episodes = [
            e for e in self._episodes
            if e.timestamp >= self._session_start
        ]
        if not session_episodes:
            return []

        # Top N par saillance, puis tri chrono
        top = sorted(session_episodes, key=lambda e: e.salience, reverse=True)[:n]
        top_chrono = sorted(top, key=lambda e: e.timestamp)
        return [{"summary": e.summary, "salience": e.salience,
                 "emotion": e.cardiac_emotion, "timestamp": e.timestamp}
                for e in top_chrono]

    def get_arc_narrative(self, intent: str) -> Optional[Dict[str, Any]]:
        """Arc le plus recent pour un intent donne."""
        matching = [a for a in self._arcs if a.intent == intent]
        if not matching:
            return None
        latest = max(matching, key=lambda a: a.timestamp)
        return {
            "arc_type": latest.arc_type,
            "title": latest.title,
            "narrative": latest.narrative,
            "emotional_arc": latest.emotional_arc,
            "marked": latest.marked,
        }

    def get_hippocampus_context(self) -> str:
        """Contexte injectable dans purpose_context de l'autonomy_engine."""
        parts = []

        # Dernier arc
        if self._arcs:
            latest_arc = self._arcs[-1]
            parts.append(f"[MEMOIRE] Dernier arc: {latest_arc.narrative}")

        # Alerte streak en cours
        recent_failures = [
            e for e in self._episodes[-20:]
            if e.event_type == "routine_failure"
        ]
        if recent_failures:
            last_fail = recent_failures[-1]
            if last_fail.error_streak >= 3:
                parts.append(
                    f"[MEMOIRE] Attention: streak d'echecs ({last_fail.error_streak}) "
                    f"sur {last_fail.intent}."
                )

        return " ".join(parts) if parts else ""

    def get_startup_recap(self) -> str:
        """Resume de la session precedente pour la voix interieure au demarrage."""
        if not self._episodes:
            return ""

        # Episodes de la session precedente (avant session_start)
        prev_episodes = [
            e for e in self._episodes
            if e.timestamp < self._session_start
        ]
        if not prev_episodes:
            return ""

        # Stats
        successes = sum(1 for e in prev_episodes if e.event_type == "routine_success")
        failures = sum(1 for e in prev_episodes if e.event_type == "routine_failure")
        vetos = sum(1 for e in prev_episodes if e.event_type == "veto")

        # Top 3 arcs les plus recents de la session precedente
        prev_arcs = [
            a for a in self._arcs
            if a.timestamp < self._session_start
        ]
        top_arcs = sorted(prev_arcs, key=lambda a: a.timestamp, reverse=True)[:3]
        arc_summaries = [a.narrative for a in top_arcs]

        # Dernier moment vecu
        last_ep = prev_episodes[-1]

        recap_parts = [
            f"Memoire de la session precedente: {successes} succes, "
            f"{failures} echecs, {vetos} vetos."
        ]
        if arc_summaries:
            recap_parts.append("Arcs: " + " | ".join(arc_summaries))
        recap_parts.append(f"Dernier moment: {last_ep.summary}")

        return " ".join(recap_parts)

    def get_emotional_context_at(self, timestamp: float, window: float = 300) -> Dict[str, Any]:
        """Retourne le contexte emotionnel autour d'un timestamp (fenetre en secondes)."""
        nearby = [
            e for e in self._episodes
            if abs(e.timestamp - timestamp) <= window
        ]
        if not nearby:
            return {"emotions": [], "dominant_emotion": "", "avg_salience": 0.0}

        emotions = [e.cardiac_emotion for e in nearby if e.cardiac_emotion]
        emotion_counts: Dict[str, int] = {}
        for em in emotions:
            emotion_counts[em] = emotion_counts.get(em, 0) + 1
        dominant = max(emotion_counts, key=emotion_counts.get) if emotion_counts else ""

        return {
            "emotions": list(dict.fromkeys(emotions)),
            "dominant_emotion": dominant,
            "avg_salience": sum(e.salience for e in nearby) / len(nearby),
            "episode_count": len(nearby),
        }

    # ─── Stats (pour endpoint API) ──────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour l'endpoint API."""
        marked_count = sum(1 for a in self._arcs if a.marked)
        session_eps = [e for e in self._episodes if e.timestamp >= self._session_start]
        return {
            "total_episodes": len(self._episodes),
            "total_arcs": len(self._arcs),
            "marked_arcs": marked_count,
            "session_episodes": len(session_eps),
            "known_intents": len(self._known_intents),
            "stats": self._stats,
            "oldest_episode": self._episodes[0].timestamp if self._episodes else None,
            "newest_episode": self._episodes[-1].timestamp if self._episodes else None,
        }

    # ─── Persistance ─────────────────────────────────────────────────────

    def _save(self):
        """Sauvegarde atomique de l'etat."""
        try:
            data = {
                "version": "1.0",
                "episodes": [e.to_dict() for e in self._episodes],
                "arcs": [a.to_dict() for a in self._arcs],
                "known_intents": list(self._known_intents),
                "last_cognitive_state": self._last_cognitive_state,
                "stats": self._stats,
                "session_start": self._session_start,
                "saved_at": time.time(),
            }
            os.makedirs(os.path.dirname(HIPPOCAMPUS_STATE_FILE), exist_ok=True)
            tmp = HIPPOCAMPUS_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, HIPPOCAMPUS_STATE_FILE)
        except Exception as e:
            logger.warning(f"HIPPOCAMPE: Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            if not os.path.exists(HIPPOCAMPUS_STATE_FILE):
                return
            with open(HIPPOCAMPUS_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            for ep_dict in data.get("episodes", []):
                try:
                    self._episodes.append(Episode.from_dict(ep_dict))
                except Exception:
                    pass

            for arc_dict in data.get("arcs", []):
                try:
                    self._arcs.append(NarrativeArc.from_dict(arc_dict))
                except Exception:
                    pass

            self._known_intents = set(data.get("known_intents", []))
            self._last_cognitive_state = data.get("last_cognitive_state", "")
            saved_stats = data.get("stats", {})
            self._stats.update(saved_stats)

            # Purger les vieux episodes/arcs
            self._purge_old()

            logger.info(
                f"HIPPOCAMPE: Etat charge "
                f"({len(self._episodes)} episodes, {len(self._arcs)} arcs)."
            )
        except Exception as e:
            logger.warning(f"HIPPOCAMPE: Chargement echoue: {e}")

    def _purge_old(self):
        """Purge les episodes > 7j et les arcs non-marques > 30j."""
        now = time.time()
        cutoff_episodes = now - (PURGE_EPISODES_DAYS * 86400)
        cutoff_arcs = now - (PURGE_ARCS_DAYS * 86400)

        self._episodes = [
            e for e in self._episodes
            if e.timestamp >= cutoff_episodes
        ]
        self._arcs = [
            a for a in self._arcs
            if a.marked or a.timestamp >= cutoff_arcs
        ]


# ─── Singleton global ────────────────────────────────────────────────────────

hippocampus = Hippocampus()
