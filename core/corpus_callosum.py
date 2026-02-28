"""
Corpus Callosum — Resonance Inter-Organes.

Pont central entre les 7 organes cognitifs de Promethee.
Detecte les patterns emergents cross-organes et cree des effets croises :
amplifier la coherence ou amortir la dissonance.

Le Corpus Callosum synchronise, il ne pense pas.
0 LLM, boucle async 60s, singleton.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from core.event_bus.bus import bus

logger = logging.getLogger("prometheus.corpus_callosum")

# ============================================================
# Constantes
# ============================================================

CALLOSUM_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "corpus_callosum_state.json"
)

RESONANCE_INTERVAL = 60.0           # Boucle entre cardiac (30s) et reptilian (60s)
MAX_STATE_BUFFER = 60               # ~1h de snapshots
MAX_RESONANCE_LOG = 50              # Historique des patterns detectes
MIN_RESONANCE_INTERVAL = 120.0      # Anti-spam par type (doublé : un effet tous les 2 cycles)
MAX_RESONANCE_EFFECTS_PER_CYCLE = 5 # Effets max par cycle (augmenté de 3 à 5)

# Etats cognitifs possibles
COGNITIVE_STATES = ("standard", "flow", "crisis", "creative_surge",
                    "stagnation", "recovery", "exploration")

# Ponderation des organes pour la coherence globale
COHERENCE_WEIGHTS = {
    "cardiac": 0.20,
    "desire": 0.15,
    "reptilian": 0.15,
    "prefrontal": 0.15,
    "dopamine": 0.15,
    "synaptic": 0.10,
    "voice": 0.10,
}

# Bonus scoring par etat cognitif (intent_keyword -> bonus)
FLOW_BOOST_INTENTS = {
    "code", "evolution", "architect", "audit", "test", "refactor",
}
FLOW_PENALIZE_INTENTS = {
    "soliloque", "council", "routine_check",
}
CRISIS_BOOST_INTENTS = {
    "security", "infra", "health_check", "backup",
}
EXPLORATION_BOOST_INTENTS = {
    "research", "curiosity", "explore", "discover", "council",
    "veille", "grimoire", "dropzone",
}
CREATIVE_BOOST_INTENTS = {
    "evolution", "create", "code", "experiment", "soliloque",
}
RECOVERY_BOOST_INTENTS = {
    "memory", "cleanup", "consolidation", "health_check", "backup",
}


# ============================================================
# Dataclasses
# ============================================================

@dataclass
class OrganSnapshot:
    """Photo instantanee des 7 organes."""
    timestamp: float = 0.0
    # Cardiac
    cardiac_bpm: float = 65.0
    cardiac_coherence: float = 0.5
    cardiac_emotion: str = "serenite"
    cardiac_emotion_intensity: float = 0.3
    # Desire
    dominant_drive: str = ""
    dominant_deprivation: float = 40.0
    frustrated_drives: List[str] = field(default_factory=list)
    # Reptilian
    threat_level: float = 0.0
    adrenaline: float = 0.0
    # Prefrontal
    active_goals: int = 0
    goal_progress: float = 0.0
    has_active_goal: bool = False
    # Dopamine
    dopamine_level: float = 0.5
    # Synaptic
    synaptic_energy: float = 0.0
    active_nodes: int = 0
    # InnerVoice
    voice_active: bool = False
    voice_mode: str = ""


@dataclass
class ResonancePattern:
    """Pattern de resonance detecte entre organes."""
    pattern_type: str           # "flow", "crisis", etc.
    confidence: float = 0.0     # [0, 1]
    contributing_organs: List[str] = field(default_factory=list)
    effects_applied: List[str] = field(default_factory=list)
    timestamp: float = 0.0
    narrative: str = ""


# ============================================================
# Classe Principale
# ============================================================

class CorpusCallosum:
    """Pont inter-organes — synchronise sans penser."""

    _instance: Optional["CorpusCallosum"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Etat cognitif courant
        self.cognitive_state: str = "standard"
        self._previous_state: str = "standard"
        self._state_since: float = time.time()

        # Coherence globale
        self.global_coherence: float = 0.5

        # Historiques
        self._snapshots: List[OrganSnapshot] = []
        self._resonance_log: List[ResonancePattern] = []
        self._last_pattern: Optional[ResonancePattern] = None

        # Anti-spam : dernier timestamp par type de pattern
        self._last_effect_time: Dict[str, float] = {}

        # Cycle
        self._task: Optional[asyncio.Task] = None
        self._alive: bool = False
        self._subscribed: bool = False
        self._cycle_count: int = 0

        # Stats
        self._stats = {
            "cycles_completed": 0,
            "patterns_detected": 0,
            "effects_applied": 0,
            "state_transitions": 0,
        }

        # Signaux des handlers bus (passifs, consommes dans le cycle)
        self._pending_reptilian_alert: Optional[dict] = None
        self._pending_dopamine_surge: Optional[dict] = None

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        if cls._instance is not None:
            cls._instance._alive = False
            if cls._instance._task and not cls._instance._task.done():
                cls._instance._task.cancel()
            cls._instance = None

    # ============================================================
    # Init & Lifecycle
    # ============================================================

    def init(self):
        """Appele depuis main.py — souscrit au bus."""
        self._subscribe_events()
        logger.info(
            "CORPUS CALLOSUM: Pont inter-organes actif "
            f"(etat={self.cognitive_state}, coherence={self.global_coherence:.2f})."
        )

    def _subscribe_events(self):
        """Souscriptions bus — handlers passifs."""
        if self._subscribed:
            return
        self._subscribed = True

        bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
        bus.subscribe("REPTILIAN_STATE", self._on_reptilian_state)
        bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
        bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_surge)
        bus.subscribe("DOPAMINE_DIP", self._on_dopamine_dip)
        bus.subscribe("PREFRONTAL_GOAL_CREATED", self._on_prefrontal_event)
        bus.subscribe("PREFRONTAL_GOAL_COMPLETE", self._on_prefrontal_event)
        bus.subscribe("PREFRONTAL_GOAL_ABANDONED", self._on_prefrontal_event)
        bus.subscribe("INNER_VOICE_BROADCAST", self._on_voice_broadcast)
        bus.subscribe("SYNAPTIC_UPDATE", self._on_synaptic_update)
        bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)

    async def start_resonance(self):
        """Boucle async principale — 60s."""
        self._alive = True
        logger.info("CORPUS CALLOSUM: Boucle de resonance demarree.")
        while self._alive:
            try:
                await self._resonance_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"CORPUS CALLOSUM: Erreur cycle: {e}")
            await asyncio.sleep(RESONANCE_INTERVAL)
        logger.info("CORPUS CALLOSUM: Boucle de resonance arretee.")

    # ============================================================
    # Bus Handlers (PASSIFS — pas d'effets directs sauf urgence)
    # ============================================================

    async def _on_cardiac_beat(self, event: dict):
        """Recoit le battement cardiaque — passif."""
        pass  # Le cycle capturera l'etat cardiaque

    async def _on_reptilian_state(self, event: dict):
        """Recoit l'etat reptilien — passif."""
        pass

    async def _on_reptilian_alert(self, event: dict):
        """Urgence reptilienne — seule exception au passif."""
        reflex = event.get("reflex", "")
        if reflex in ("FREEZE", "FIGHT"):
            self._pending_reptilian_alert = event
            # Transition immediate en crisis si pas deja
            if self.cognitive_state != "crisis":
                self._previous_state = self.cognitive_state
                self.cognitive_state = "crisis"
                self._state_since = time.time()
                self._stats["state_transitions"] += 1
                logger.info(
                    f"CORPUS CALLOSUM: Alerte reptilienne {reflex} "
                    "→ transition immediate en CRISIS."
                )

    async def _on_dopamine_surge(self, event: dict):
        """Surge dopaminique — bridge direct vers cardiac (Hole #7)."""
        self._pending_dopamine_surge = event
        # Bridge #7 : dopamine → cardiac
        try:
            from core.cardiac_engine import heart
            heart.react("eureka")
            logger.debug("CORPUS CALLOSUM: Bridge dopamine→cardiac (eureka).")
        except Exception:
            pass

    async def _on_dopamine_dip(self, event: dict):
        """Dip dopaminique — passif."""
        pass

    async def _on_prefrontal_event(self, event: dict):
        """Evenement prefrontal — passif."""
        pass

    async def _on_voice_broadcast(self, event: dict):
        """Broadcast voix interieure — passif."""
        pass

    async def _on_synaptic_update(self, event: dict):
        """Mise a jour synaptique — passif."""
        pass

    async def _on_routine_complete(self, event: dict):
        """Routine terminee — passif."""
        pass

    # ============================================================
    # Cycle de Resonance
    # ============================================================

    async def _resonance_cycle(self):
        """Cycle complet : capturer → detecter → amplifier → evaluer → publier."""
        # 1. CAPTURER
        snapshot = self._capture_organ_states()
        self._snapshots.append(snapshot)
        if len(self._snapshots) > MAX_STATE_BUFFER:
            self._snapshots = self._snapshots[-MAX_STATE_BUFFER:]

        # 2. DETECTER
        patterns = self._detect_resonance_patterns(snapshot)

        # 3. AMPLIFIER (max effets par cycle)
        effects_this_cycle = 0
        for pattern in patterns:
            if effects_this_cycle >= MAX_RESONANCE_EFFECTS_PER_CYCLE:
                break
            applied = self._apply_cross_effects(pattern, snapshot)
            effects_this_cycle += len(applied)
            pattern.effects_applied = applied

            self._resonance_log.append(pattern)
            self._last_pattern = pattern
            self._stats["patterns_detected"] += 1
            self._stats["effects_applied"] += len(applied)

        if len(self._resonance_log) > MAX_RESONANCE_LOG:
            self._resonance_log = self._resonance_log[-MAX_RESONANCE_LOG:]

        # 4. EVALUER
        self.global_coherence = self._compute_global_coherence(snapshot)

        # 5. ETAT
        new_state = self._determine_cognitive_state(snapshot)
        if new_state != self.cognitive_state:
            self._previous_state = self.cognitive_state
            self.cognitive_state = new_state
            self._state_since = time.time()
            self._stats["state_transitions"] += 1
            logger.info(
                f"CORPUS CALLOSUM: Transition {self._previous_state} → {new_state} "
                f"(coherence={self.global_coherence:.2f})."
            )

        # 6. PUBLIER
        await self._publish_state()

        # 7. PERSISTER (toutes les 10 cycles)
        self._cycle_count += 1
        self._stats["cycles_completed"] = self._cycle_count
        if self._cycle_count % 10 == 0:
            self.save()

        # Reset signaux pending
        self._pending_reptilian_alert = None
        self._pending_dopamine_surge = None

    # ============================================================
    # 1. CAPTURER
    # ============================================================

    def _capture_organ_states(self) -> OrganSnapshot:
        """Capture instantanee de tous les organes (imports locaux, try/except)."""
        snap = OrganSnapshot(timestamp=time.time())

        # Cardiac
        try:
            from core.cardiac_engine import heart
            snap.cardiac_bpm = heart.bpm
            snap.cardiac_coherence = getattr(heart, "coherence", 0.5)
            snap.cardiac_emotion = heart.current_emotion
            snap.cardiac_emotion_intensity = heart.emotion_intensity
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
                snap.dominant_drive = top_drives[0].name
                snap.dominant_deprivation = top_drives[0].deprivation
                snap.frustrated_drives = [
                    d.name for d in top_drives if d.deprivation >= 55
                ]
        except Exception:
            pass

        # Reptilian
        try:
            from core.reptilian_core import reptile
            snap.threat_level = reptile.threat_level
            snap.adrenaline = reptile.adrenaline
        except Exception:
            pass

        # Prefrontal
        try:
            from core.prefrontal import prefrontal
            active = [g for g in prefrontal.goals if g.status == "active"]
            snap.active_goals = len(active)
            snap.has_active_goal = len(active) > 0
            if active:
                snap.goal_progress = sum(g.progress for g in active) / len(active)
        except Exception:
            pass

        # Dopamine
        try:
            from core.dopamine_system import dopamine
            snap.dopamine_level = dopamine.dopamine_level
        except Exception:
            pass

        # Synaptic
        try:
            from core.synaptic_network import cortex
            if cortex.nodes:
                energies = [n.get("energy", 0) for n in cortex.nodes.values()]
                snap.synaptic_energy = sum(energies) / len(energies)
                snap.active_nodes = sum(1 for e in energies if e > 0.3)
        except Exception:
            pass

        # InnerVoice
        try:
            from core.inner_voice import voice as inner_voice
            snap.voice_active = not inner_voice._is_idle
            if inner_voice.stream:
                snap.voice_mode = inner_voice.stream[-1].mode
        except Exception:
            pass

        return snap

    # ============================================================
    # 2. DETECTER — 6 patterns de resonance
    # ============================================================

    def _detect_resonance_patterns(self, snap: OrganSnapshot) -> List[ResonancePattern]:
        """Identifie les patterns emergents dans le snapshot."""
        patterns = []
        now = time.time()

        # --- FLOW ---
        if (snap.cardiac_coherence >= 0.65
                and snap.dopamine_level >= 0.60
                and snap.has_active_goal
                and snap.threat_level < 3.0):
            confidence = min(1.0, (
                snap.cardiac_coherence * 0.3
                + snap.dopamine_level * 0.3
                + (snap.goal_progress / 100.0 if snap.goal_progress > 1 else snap.goal_progress) * 0.2
                + (1.0 - snap.threat_level / 10.0) * 0.2
            ))
            patterns.append(ResonancePattern(
                pattern_type="flow",
                confidence=confidence,
                contributing_organs=["cardiac", "dopamine", "prefrontal"],
                timestamp=now,
                narrative=(
                    f"Etat de flow detecte : coherence cardiaque elevee ({snap.cardiac_coherence:.2f}), "
                    f"dopamine au sommet ({snap.dopamine_level:.2f}), "
                    f"objectif actif en cours."
                ),
            ))

        # --- CRISIS ---
        if (snap.threat_level >= 4.0
                and snap.dominant_deprivation >= 55
                and snap.dopamine_level < 0.4):
            confidence = min(1.0, (
                snap.threat_level / 10.0 * 0.4
                + snap.dominant_deprivation / 100.0 * 0.3
                + (1.0 - snap.dopamine_level) * 0.3
            ))
            patterns.append(ResonancePattern(
                pattern_type="crisis",
                confidence=confidence,
                contributing_organs=["reptilian", "desire", "dopamine"],
                timestamp=now,
                narrative=(
                    f"Crise detectee : menace elevee ({snap.threat_level:.1f}), "
                    f"pulsions frustrees (deprivation={snap.dominant_deprivation:.0f}), "
                    f"dopamine basse ({snap.dopamine_level:.2f})."
                ),
            ))

        # --- CREATIVE_SURGE ---
        if (snap.dopamine_level >= 0.65
                and any(d in ("CURIOSITE", "CREATION") for d in snap.frustrated_drives)
                and snap.synaptic_energy > 0.2):
            confidence = min(1.0, (
                snap.dopamine_level * 0.35
                + min(snap.synaptic_energy, 1.0) * 0.35
                + (len(snap.frustrated_drives) / 7.0) * 0.3
            ))
            patterns.append(ResonancePattern(
                pattern_type="creative_surge",
                confidence=confidence,
                contributing_organs=["dopamine", "desire", "synaptic"],
                timestamp=now,
                narrative=(
                    f"Vague creative : dopamine haute ({snap.dopamine_level:.2f}), "
                    f"pulsions creatives frustrees, reseau synaptique actif."
                ),
            ))

        # --- STAGNATION ---
        # Détection assouplie : on stagne aussi quand les goals n'avancent pas
        stagnation_check = (
            snap.dopamine_level < 0.40
            and snap.dominant_deprivation >= 60
            and snap.cardiac_coherence < 0.5
            and (not snap.has_active_goal or snap.goal_progress < 0.15)
        )
        if stagnation_check:
            confidence = min(1.0, (
                (1.0 - snap.dopamine_level) * 0.3
                + snap.dominant_deprivation / 100.0 * 0.3
                + (1.0 - snap.cardiac_coherence) * 0.2
                + 0.2  # bonus absence goal
            ))
            patterns.append(ResonancePattern(
                pattern_type="stagnation",
                confidence=confidence,
                contributing_organs=["dopamine", "desire", "prefrontal", "cardiac"],
                timestamp=now,
                narrative=(
                    f"Stagnation detectee : dopamine basse ({snap.dopamine_level:.2f}), "
                    f"pulsions frustrees, aucun objectif actif, coherence faible."
                ),
            ))

        # --- RECOVERY ---
        if (snap.cardiac_bpm < 55
                and snap.cardiac_coherence > 0.5
                and snap.threat_level < 2.0
                and self._previous_state == "crisis"):
            confidence = min(1.0, (
                snap.cardiac_coherence * 0.4
                + (1.0 - snap.threat_level / 10.0) * 0.3
                + (1.0 - snap.cardiac_bpm / 100.0) * 0.3
            ))
            patterns.append(ResonancePattern(
                pattern_type="recovery",
                confidence=confidence,
                contributing_organs=["cardiac", "reptilian"],
                timestamp=now,
                narrative=(
                    f"Recuperation post-crise : calme cardiaque (bpm={snap.cardiac_bpm:.0f}), "
                    f"menace reduite ({snap.threat_level:.1f})."
                ),
            ))

        # --- EXPLORATION ---
        if ("CURIOSITE" in snap.frustrated_drives
                and snap.threat_level < 2.0
                and snap.dopamine_level >= 0.4):
            confidence = min(1.0, (
                snap.dopamine_level * 0.3
                + snap.dominant_deprivation / 100.0 * 0.3
                + (1.0 - snap.threat_level / 10.0) * 0.2
                + (snap.synaptic_energy if snap.synaptic_energy <= 1.0 else 1.0) * 0.2
            ))
            patterns.append(ResonancePattern(
                pattern_type="exploration",
                confidence=confidence,
                contributing_organs=["desire", "reptilian", "dopamine"],
                timestamp=now,
                narrative=(
                    f"Mode exploration : curiosite frustree, environnement sur, "
                    f"dopamine adequate ({snap.dopamine_level:.2f})."
                ),
            ))

        return patterns

    # ============================================================
    # 3. AMPLIFIER — Effets croises
    # ============================================================

    def _apply_cross_effects(
        self, pattern: ResonancePattern, snap: OrganSnapshot
    ) -> List[str]:
        """Applique les effets croises d'un pattern. Retourne la liste des effets."""
        now = time.time()
        effects = []

        # Anti-spam : cooldown par type de pattern
        last = self._last_effect_time.get(pattern.pattern_type, 0)
        if now - last < MIN_RESONANCE_INTERVAL:
            return effects
        self._last_effect_time[pattern.pattern_type] = now

        if pattern.pattern_type == "flow":
            effects.extend(self._effects_flow(snap))

        elif pattern.pattern_type == "crisis":
            effects.extend(self._effects_crisis(snap))

        elif pattern.pattern_type == "creative_surge":
            effects.extend(self._effects_creative_surge(snap))

        elif pattern.pattern_type == "stagnation":
            effects.extend(self._effects_stagnation(snap))

        elif pattern.pattern_type == "recovery":
            effects.extend(self._effects_recovery(snap))

        elif pattern.pattern_type == "exploration":
            effects.extend(self._effects_exploration(snap))

        return effects

    def _effects_flow(self, snap: OrganSnapshot) -> List[str]:
        """Effets du flow : Cardiac↔Prefrontal (Holes #3, #6)."""
        effects = []
        # Bridge #3+#6 : cardiac → creation, desire → MAITRISE satisfied
        try:
            from core.cardiac_engine import heart
            heart.react("creation")
            effects.append("cardiac:react(creation)")
        except Exception:
            pass

        try:
            from core.desire_engine import desires
            if "MAITRISE" in desires.drives:
                desires.on_event("satisfaction", {"drive": "MAITRISE", "amount": 8})
                effects.append("desire:satisfy(MAITRISE,-8)")
        except Exception:
            pass

        return effects

    def _effects_crisis(self, snap: OrganSnapshot) -> List[str]:
        """Effets de crise : Reptilian↔Desire (Hole #2)."""
        effects = []
        try:
            from core.desire_engine import desires
            # Supprime CURIOSITE et CREATION
            for drive_name in ("CURIOSITE", "CREATION"):
                if drive_name in desires.drives:
                    drive = desires.drives[drive_name]
                    drive.deprivation = max(0, drive.deprivation - 15)
                    effects.append(f"desire:suppress({drive_name},-15)")
            # Booste STABILITE
            if "STABILITE" in desires.drives:
                drive = desires.drives["STABILITE"]
                drive.deprivation = min(100, drive.deprivation + 10)
                effects.append("desire:boost(STABILITE,+10)")
        except Exception:
            pass
        return effects

    def _effects_creative_surge(self, snap: OrganSnapshot) -> List[str]:
        """Effets vague creative : Dopamine↔Desire (Hole #4), Voice↔Synaptic (Hole #5)."""
        effects = []
        # Hole #4 : satisfait CREATION/CURIOSITE
        try:
            from core.desire_engine import desires
            for drive_name in ("CREATION", "CURIOSITE"):
                if drive_name in desires.drives:
                    drive = desires.drives[drive_name]
                    drive.deprivation = max(0, drive.deprivation - 8)
                    effects.append(f"desire:satisfy({drive_name},-8)")
        except Exception:
            pass

        # Hole #5 : active concept synaptique
        try:
            from core.synaptic_network import cortex
            cortex.activate_concept("creativite", intensity=0.7)
            effects.append("synaptic:activate(creativite)")
        except Exception:
            pass

        return effects

    def _effects_stagnation(self, snap: OrganSnapshot) -> List[str]:
        """Effets stagnation : pousse exploration, micro-boost dopamine."""
        effects = []
        # Hole #5 : active concept exploration
        try:
            from core.synaptic_network import cortex
            cortex.activate_concept("exploration", intensity=0.6)
            effects.append("synaptic:activate(exploration)")
        except Exception:
            pass

        # Micro-boost dopamine
        try:
            from core.dopamine_system import dopamine
            dopamine.dopamine_level = min(1.0, dopamine.dopamine_level + 0.05)
            effects.append("dopamine:micro_boost(+0.05)")
        except Exception:
            pass

        return effects

    def _effects_recovery(self, snap: OrganSnapshot) -> List[str]:
        """Effets recovery : calme cardiaque, transition douce."""
        effects = []
        try:
            from core.cardiac_engine import heart
            heart.react("serenite")
            effects.append("cardiac:react(serenite)")
        except Exception:
            pass
        return effects

    def _effects_exploration(self, snap: OrganSnapshot) -> List[str]:
        """Effets exploration : active concept synaptique, favorise decouverte."""
        effects = []
        try:
            from core.synaptic_network import cortex
            cortex.activate_concept("exploration", intensity=0.5)
            effects.append("synaptic:activate(exploration)")
        except Exception:
            pass
        return effects

    # ============================================================
    # 4. EVALUER — Coherence globale
    # ============================================================

    def _compute_global_coherence(self, snap: OrganSnapshot) -> float:
        """Score de coherence [0, 1] — ponderation 7 organes."""
        scores = {}

        # Cardiac : coherence directe
        scores["cardiac"] = snap.cardiac_coherence

        # Desire : inverse de la frustration moyenne
        scores["desire"] = max(0.0, 1.0 - snap.dominant_deprivation / 100.0)

        # Reptilian : inverse de la menace
        scores["reptilian"] = max(0.0, 1.0 - snap.threat_level / 10.0)

        # Prefrontal : bonus si goals actifs ET progression
        if snap.has_active_goal:
            progress = snap.goal_progress / 100.0 if snap.goal_progress > 1 else snap.goal_progress
            scores["prefrontal"] = 0.5 + progress * 0.5
        else:
            scores["prefrontal"] = 0.3

        # Dopamine : niveau normalise
        scores["dopamine"] = snap.dopamine_level

        # Synaptic : energie normalisee
        scores["synaptic"] = min(1.0, snap.synaptic_energy)

        # Voice : bonus si active
        scores["voice"] = 0.6 if snap.voice_active else 0.4

        # Moyenne ponderee
        total = sum(
            scores.get(organ, 0.5) * weight
            for organ, weight in COHERENCE_WEIGHTS.items()
        )
        return round(min(1.0, max(0.0, total)), 3)

    # ============================================================
    # 5. ETAT — Transition cognitive
    # ============================================================

    def _determine_cognitive_state(self, snap: OrganSnapshot) -> str:
        """Determine l'etat cognitif emergent."""
        # Priorite : crisis > flow > creative_surge > stagnation > recovery > exploration > standard

        # CRISIS : menace haute + deprivation + dopamine basse
        if (snap.threat_level >= 4.0
                and snap.dominant_deprivation >= 55
                and snap.dopamine_level < 0.4):
            return "crisis"

        # FLOW : coherence + dopamine + goal actif + safe
        if (snap.cardiac_coherence >= 0.65
                and snap.dopamine_level >= 0.60
                and snap.has_active_goal
                and snap.threat_level < 3.0):
            return "flow"

        # CREATIVE_SURGE : dopamine haute + drives creatifs frustres + synaptique
        if (snap.dopamine_level >= 0.65
                and any(d in ("CURIOSITE", "CREATION") for d in snap.frustrated_drives)
                and snap.synaptic_energy > 0.2):
            return "creative_surge"

        # STAGNATION : dopamine basse + frustration + pas de goals + incoherence
        if (snap.dopamine_level < 0.35
                and snap.dominant_deprivation >= 60
                and not snap.has_active_goal
                and snap.cardiac_coherence < 0.4):
            return "stagnation"

        # RECOVERY : calme post-crise
        if (snap.cardiac_bpm < 55
                and snap.cardiac_coherence > 0.5
                and snap.threat_level < 2.0
                and self._previous_state == "crisis"):
            return "recovery"

        # EXPLORATION : curiosite frustree + safe + dopamine adequate
        if ("CURIOSITE" in snap.frustrated_drives
                and snap.threat_level < 2.0
                and snap.dopamine_level >= 0.4):
            return "exploration"

        return "standard"

    # ============================================================
    # 6. PUBLIER
    # ============================================================

    async def _publish_state(self):
        """Publie l'etat sur le bus."""
        await bus.publish("CORPUS_CALLOSUM_STATE", {
            "cognitive_state": self.cognitive_state,
            "global_coherence": self.global_coherence,
            "last_pattern": self._last_pattern.pattern_type if self._last_pattern else None,
            "timestamp": time.time(),
        })

    # ============================================================
    # API Publique
    # ============================================================

    def compute_resonance_bonus(self, intent: str) -> float:
        """Couche 10 du scoring autonomy_engine. Retourne [-1.5, +2.0]."""
        if not self._initialized or self.cognitive_state == "standard":
            return 0.0

        intent_lower = intent.lower() if intent else ""
        bonus = 0.0

        if self.cognitive_state == "flow":
            if any(kw in intent_lower for kw in FLOW_BOOST_INTENTS):
                bonus = 1.5 * self.global_coherence
            elif any(kw in intent_lower for kw in FLOW_PENALIZE_INTENTS):
                bonus = -1.0

        elif self.cognitive_state == "crisis":
            if any(kw in intent_lower for kw in CRISIS_BOOST_INTENTS):
                bonus = 1.5
            elif any(kw in intent_lower for kw in EXPLORATION_BOOST_INTENTS):
                bonus = -1.0  # Pas d'exploration en crise

        elif self.cognitive_state == "creative_surge":
            if any(kw in intent_lower for kw in CREATIVE_BOOST_INTENTS):
                bonus = 1.2
            elif any(kw in intent_lower for kw in CRISIS_BOOST_INTENTS):
                bonus = -0.5

        elif self.cognitive_state == "stagnation":
            if any(kw in intent_lower for kw in EXPLORATION_BOOST_INTENTS):
                bonus = 1.5
            elif any(kw in intent_lower for kw in ("routine_check",)):
                bonus = -0.5

        elif self.cognitive_state == "recovery":
            if any(kw in intent_lower for kw in RECOVERY_BOOST_INTENTS):
                bonus = 0.8
            elif any(kw in intent_lower for kw in ("evolution", "council")):
                bonus = -0.5

        elif self.cognitive_state == "exploration":
            if any(kw in intent_lower for kw in EXPLORATION_BOOST_INTENTS):
                bonus = 1.2
            elif any(kw in intent_lower for kw in ("routine_check",)):
                bonus = -0.3

        # Clamping [-1.5, +2.0]
        return max(-1.5, min(2.0, bonus))

    def should_amplify_veto(self, veto_source: str = "") -> bool:
        """Renforce les vetos en crisis ou flow."""
        if self.cognitive_state == "crisis":
            return True
        if self.cognitive_state == "flow" and veto_source in ("prefrontal", "reptilian"):
            return True
        return False

    def get_cognitive_context(self) -> str:
        """Injection dans purpose_context de l'autonomy_engine."""
        if self.cognitive_state == "standard":
            return ""

        state_labels = {
            "flow": "FLOW — concentration optimale, maintenir le cap",
            "crisis": "CRISE — mode survie, priorite securite et stabilite",
            "creative_surge": "VAGUE CREATIVE — explorer, creer, experimenter",
            "stagnation": "STAGNATION — chercher de nouvelles directions",
            "recovery": "RECUPERATION — calme post-crise, routines legeres",
            "exploration": "EXPLORATION — curiosite en eveil, decouvrir",
        }
        label = state_labels.get(self.cognitive_state, self.cognitive_state)
        narrative = self.get_narrative()
        ctx = f"[RESONANCE] Etat cognitif: {label} (coherence={self.global_coherence:.2f})"
        if narrative:
            ctx += f" — {narrative}"
        return ctx

    def get_narrative(self) -> str:
        """Phrase introspective du dernier pattern detecte."""
        if self._last_pattern:
            return self._last_pattern.narrative
        return ""

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour endpoint API et self_awareness."""
        return {
            "cognitive_state": self.cognitive_state,
            "previous_state": self._previous_state,
            "global_coherence": self.global_coherence,
            "state_since": self._state_since,
            "cycles_completed": self._stats["cycles_completed"],
            "patterns_detected": self._stats["patterns_detected"],
            "effects_applied": self._stats["effects_applied"],
            "state_transitions": self._stats["state_transitions"],
            "last_pattern": asdict(self._last_pattern) if self._last_pattern else None,
            "resonance_log_size": len(self._resonance_log),
            "snapshots_buffered": len(self._snapshots),
        }

    # ============================================================
    # Persistance
    # ============================================================

    def save(self):
        """Sauvegarde atomique de l'etat."""
        state = {
            "version": "1.0",
            "cognitive_state": self.cognitive_state,
            "previous_state": self._previous_state,
            "global_coherence": self.global_coherence,
            "state_since": self._state_since,
            "stats": self._stats,
            "last_effect_time": self._last_effect_time,
            "resonance_log": [asdict(p) for p in self._resonance_log[-20:]],
            "timestamp": time.time(),
        }
        try:
            os.makedirs(os.path.dirname(CALLOSUM_STATE_FILE), exist_ok=True)
            tmp = CALLOSUM_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CALLOSUM_STATE_FILE)
        except Exception as e:
            logger.warning(f"CORPUS CALLOSUM: Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            if not os.path.exists(CALLOSUM_STATE_FILE):
                return
            with open(CALLOSUM_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

            self.cognitive_state = state.get("cognitive_state", "standard")
            self._previous_state = state.get("previous_state", "standard")
            self.global_coherence = state.get("global_coherence", 0.5)
            self._state_since = state.get("state_since", time.time())
            self._stats.update(state.get("stats", {}))
            self._last_effect_time = state.get("last_effect_time", {})

            # Restaurer le log de resonance
            for entry in state.get("resonance_log", []):
                try:
                    self._resonance_log.append(ResonancePattern(**entry))
                except Exception:
                    pass
            if self._resonance_log:
                self._last_pattern = self._resonance_log[-1]

            logger.info(
                f"CORPUS CALLOSUM: Etat charge "
                f"(state={self.cognitive_state}, coherence={self.global_coherence:.2f})."
            )
        except Exception as e:
            logger.warning(f"CORPUS CALLOSUM: Chargement echoue: {e}")


# ============================================================
# Singleton global
# ============================================================

callosum = CorpusCallosum()
