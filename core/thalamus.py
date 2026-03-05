# core/thalamus.py — Thalamus : Relais Sensoriel & Filtrage Attentionnel
# Filtre les events du bus par saillance, adapte le seuil d'attention
# selon le contexte (phase circadienne, dopamine, arousal cardiaque).
# Cycle cale sur CARDIAC_BEAT (~30s), 0 LLM, 100% deterministe.

import json
import os
import time
import logging
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("Thalamus")

# --- Fichier de persistance ---

THALAMUS_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "thalamus_state.json"
)

# --- Constantes ---

SALIENCE_DECAY = 0.95
MIN_SALIENCE_CHANGE = 0.05
PHASE_THRESHOLDS = {
    "eveil": 0.3,
    "crepuscule": 0.5,
    "sommeil_profond": 0.8,
    "aube": 0.4,
}
MAX_LEARNED_RULES = 20  # Pour Sprint E futur
NAP_BUFFER_MAX = 50  # Max events bufferises pendant sieste

# Sprint C — Stabilite focus
FOCUS_INERTIA_CYCLES = 3      # Nb cycles min avant qu'un shift de focus soit accepte
ATTENTION_FATIGUE_RATE = 0.01  # Montee fatigue par cycle quand focus stable
ATTENTION_FATIGUE_MAX = 1.0
ATTENTION_FATIGUE_RECOVERY = 0.05  # Baisse fatigue par cycle apres un shift
ROUTINE_FEEDBACK_BOOST = 0.1   # Boost saillance sur routine success
ROUTINE_FEEDBACK_MALUS = 0.05  # Malus saillance sur routine error

# Sprint E — Learned Rules
RULE_CRYSTALLIZE_THRESHOLD = 3     # Observations concordantes pour cristalliser
RULE_BOOST_WEIGHT = 0.08           # Poids initial regle boost (success)
RULE_DAMPEN_WEIGHT = 0.06          # Poids initial regle dampen (error)
RULE_REINFORCE_STEP = 0.02         # Renforcement par confirmation supplementaire
RULE_DECAY_ON_CONTRADICTION = 0.03 # Degradation quand observation contredit la regle
RULE_MIN_WEIGHT = 0.01             # En-dessous → regle supprimee
RULE_MAX_WEIGHT = 0.20             # Plafond poids

# Sprint F — Habituation & Novelty
HABITUATION_WINDOW = 10            # Max timestamps gardes par event
HABITUATION_THRESHOLD = 5          # Occurrences dans la fenetre → habitue
HABITUATION_EXTRA_DECAY = 0.07     # Malus supplementaire par cycle (progressif)
HABITUATION_TIME_WINDOW = 600.0    # Fenetre temporelle 10 min
NOVELTY_ABSENCE_THRESHOLD = 300.0  # 5 min sans occurrence → "novel"
NOVELTY_BOOST = 0.15               # Boost ponctuel a l'arrivee d'un event novel

# --- Events sensoriels (pour habituation/novelty) ---

_SENSORY_EVENTS = frozenset({
    "REPTILIAN_ALERT",
    "KNOWLEDGE_GAP_DETECTED",
    "HALLUCINATION_DETECTED",
    "TISSUE_PATTERN_EMERGED",
    "TISSUE_CREATIVITY_SPIKE",
    "PREFRONTAL_GOAL_COMPLETE",
    "COUNCIL_END",
})

# --- Categories d'events ---

EVENT_CATEGORIES: Dict[str, str] = {
    "REPTILIAN_ALERT": "urgence",
    "HALLUCINATION_DETECTED": "urgence",
    "TISSUE_EXTINCTION_RISK": "urgence",
    "OLLAMA_UNRESPONSIVE": "urgence",
    "DOPAMINE_SURGE": "motivation",
    "DOPAMINE_DIP": "motivation",
    "PREFRONTAL_GOAL_COMPLETE": "cognition",
    "PREFRONTAL_GOAL_ABANDONED": "cognition",
    "KNOWLEDGE_GAP_DETECTED": "cognition",
    "COUNCIL_END": "deliberation",
    "TISSUE_PATTERN_EMERGED": "emergence",
    "TISSUE_CREATIVITY_SPIKE": "emergence",
    "EUREKA_BRIDGE": "emergence",
    "CIRCADIAN_PHASE_CHANGE": "regulation",
    "AUTONOMY_ROUTINE_COMPLETE": "regulation",
}

# Mapping intent → categorie pour compute_attention_bonus
_INTENT_CATEGORY_HINTS: Dict[str, str] = {
    "SECURITY_SCAN": "urgence",
    "AUDIT_STRUCTURE": "urgence",
    "SELF_ANALYSIS": "cognition",
    "COUNCIL_DEBATE": "deliberation",
    "EVOLUTION_PIPELINE": "emergence",
    "EXPLORE_KNOWLEDGE": "cognition",
    "CREATIVE_WRITING": "emergence",
    "MEMORY_CLEANUP": "regulation",
    "MEMORY_CONSOLIDATION": "regulation",
    "ROUTINE_MAINTENANCE": "regulation",
}

SAVE_INTERVAL = 10  # Sauvegarder toutes les 10 cycles


class Thalamus:
    """Singleton — Relais sensoriel, filtre attentionnel du bus d'events."""

    _instance: Optional["Thalamus"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Scorecard : saillance par event_type (init 0.5)
        self._scorecard: Dict[str, float] = {
            evt: 0.5 for evt in EVENT_CATEGORIES
        }
        # Seuil adaptatif (init phase eveil)
        self._threshold: float = PHASE_THRESHOLDS["eveil"]
        # Mode sieste (Sprint B)
        self._sleeping: bool = False
        # Buffer sieste (Sprint B)
        self._nap_buffer: List[Dict] = []
        # Seuil pre-sieste (restaure au reveil)
        self._pre_nap_threshold: Optional[float] = None
        # Categorie dominante
        self._attention_focus: Optional[str] = None
        # Snapshot contexte organes
        self._context: Dict[str, Any] = {
            "threat_level": 0.0,
            "dopamine_level": 0.5,
            "phase": "eveil",
            "bpm": 60.0,
            "emotion": "serenite",
            "emotion_intensity": 0.3,
        }
        # Derniere scorecard pour detecter les changements
        self._last_scorecard: Dict[str, float] = dict(self._scorecard)
        self._cycle_count: int = 0
        self._subscribed: bool = False
        # Sprint C — Stabilite focus
        self._focus_since_cycle: int = 0
        self._pending_focus: Optional[str] = None
        self._pending_focus_count: int = 0
        # Sprint C — Fatigue attentionnelle
        self._attention_fatigue: float = 0.0
        # Sprint C — Historique focus
        self._focus_history: List[Dict] = []
        # Sprint E — Learned Rules
        self._learned_rules: List[Dict[str, Any]] = []
        self._intent_observations: Dict[str, Dict[str, int]] = {}

        # Sprint F — Habituation & Novelty
        self._event_timestamps: Dict[str, List[float]] = {evt: [] for evt in _SENSORY_EVENTS}
        self._event_last_seen: Dict[str, float] = {}

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite (appele depuis main.py)."""
        self._subscribe_events()
        logger.info(
            f"THALAMUS: Relais sensoriel actif "
            f"(seuil={self._threshold:.2f}, focus={self._attention_focus})."
        )

    # ================================================================
    # PERSISTANCE
    # ================================================================

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            if os.path.exists(THALAMUS_STATE_FILE):
                with open(THALAMUS_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved_scorecard = data.get("scorecard", {})
                for k in self._scorecard:
                    if k in saved_scorecard:
                        self._scorecard[k] = float(saved_scorecard[k])
                self._threshold = float(data.get("threshold", self._threshold))
                self._attention_focus = data.get("attention_focus")
                self._context.update(data.get("context", {}))
                self._cycle_count = int(data.get("cycle_count", 0))
                # Sprint C
                self._attention_fatigue = float(data.get("attention_fatigue", 0.0))
                self._focus_since_cycle = int(data.get("focus_since_cycle", 0))
                self._focus_history = data.get("focus_history", [])
                # Sprint E
                self._learned_rules = data.get("learned_rules", [])
                self._intent_observations = data.get("intent_observations", {})
                # Sprint F
                saved_ts = data.get("event_timestamps", {})
                for evt in _SENSORY_EVENTS:
                    if evt in saved_ts:
                        self._event_timestamps[evt] = saved_ts[evt]
                self._event_last_seen = data.get("event_last_seen", {})
                logger.info(f"THALAMUS: Etat restaure (cycles={self._cycle_count}).")
        except Exception as e:
            logger.warning(f"THALAMUS: Echec chargement: {e}")

    def _save(self):
        """Sauvegarde atomique de l'etat."""
        try:
            data = {
                "scorecard": {k: round(v, 4) for k, v in self._scorecard.items()},
                "threshold": round(self._threshold, 4),
                "attention_focus": self._attention_focus,
                "context": self._context,
                "cycle_count": self._cycle_count,
                "attention_fatigue": round(self._attention_fatigue, 4),
                "focus_since_cycle": self._focus_since_cycle,
                "focus_history": self._focus_history[-20:],
                "learned_rules": self._learned_rules,
                "intent_observations": self._intent_observations,
                "event_timestamps": {k: v[-HABITUATION_WINDOW:] for k, v in self._event_timestamps.items() if v},
                "event_last_seen": self._event_last_seen,
                "saved_at": time.time(),
            }
            os.makedirs(os.path.dirname(THALAMUS_STATE_FILE), exist_ok=True)
            tmp = THALAMUS_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, THALAMUS_STATE_FILE)
        except Exception as e:
            logger.warning(f"THALAMUS: Echec sauvegarde: {e}")

    # ================================================================
    # METHODES PUBLIQUES
    # ================================================================

    def get_salience(self, event_type: str) -> float:
        """Retourne la saillance d'un event (0.5 si inconnu)."""
        return self._scorecard.get(event_type, 0.5)

    def is_salient(self, event_type: str) -> bool:
        """L'event depasse-t-il le seuil d'attention actuel ?"""
        return self.get_salience(event_type) > self._threshold

    def get_focus(self) -> Optional[str]:
        """Retourne la categorie dominante."""
        return self._attention_focus

    def get_stats(self) -> Dict[str, Any]:
        """Retourne un snapshot pour API et self_awareness."""
        return {
            "scorecard": {k: round(v, 3) for k, v in self._scorecard.items()},
            "threshold": round(self._threshold, 3),
            "focus": self._attention_focus,
            "sleeping": self._sleeping,
            "nap_buffer_size": len(self._nap_buffer),
            "cycle_count": self._cycle_count,
            "context": dict(self._context),
            "salient_events": [
                evt for evt, s in self._scorecard.items() if s > self._threshold
            ],
            "attention_fatigue": round(self._attention_fatigue, 3),
            "focus_duration_cycles": self._cycle_count - self._focus_since_cycle,
            "focus_history_size": len(self._focus_history),
            "learned_rules_count": len(self._learned_rules),
            "learned_rules": [
                {"intent": r["intent"], "type": r["type"], "weight": round(r["weight"], 4)}
                for r in self._learned_rules
            ],
            "habituated_events": [
                evt for evt in _SENSORY_EVENTS
                if self._get_habituation_level(evt) > 0.0
            ],
            "novel_events": [
                evt for evt in _SENSORY_EVENTS
                if evt not in self._event_last_seen
                or (time.time() - self._event_last_seen[evt]) >= NOVELTY_ABSENCE_THRESHOLD
            ],
        }

    def compute_attention_bonus(self, intent: str) -> float:
        """Bonus/malus pour le scoring autonomy_engine (Couche 15).

        Routines alignees au focus attentionnel recoivent un bonus.
        Routines non-alignees recoivent un leger malus.
        Range : [-0.5, +1.5]
        """
        if not self._attention_focus:
            return 0.0

        intent_cat = _INTENT_CATEGORY_HINTS.get(intent)
        if intent_cat is None:
            return 0.0

        if intent_cat == self._attention_focus:
            # Bonus proportionnel a la force du focus
            focus_strength = self._compute_category_strength(self._attention_focus)
            bonus = min(1.5, focus_strength * 0.5)
        else:
            bonus = -0.3

        # Sprint C — Modulation par fatigue attentionnelle
        if self._attention_fatigue > 0.5:
            fatigue_factor = 1.0 - (self._attention_fatigue - 0.5)  # [1.0 → 0.5]
            bonus *= fatigue_factor

        return bonus

    def get_nap_events(self) -> List[Dict[str, Any]]:
        """Retourne une copie du nap_buffer (events accumules pendant la sieste)."""
        return list(self._nap_buffer)

    def get_health(self) -> Dict[str, Any]:
        """Health check thalamique."""
        focus_duration = self._cycle_count - self._focus_since_cycle
        return {
            "attention_fatigue": round(self._attention_fatigue, 3),
            "focus_stability": focus_duration,
            "focus": self._attention_focus,
            "sleeping": self._sleeping,
            "salient_count": sum(1 for v in self._scorecard.values() if v > self._threshold),
            "status": "FATIGUED" if self._attention_fatigue > 0.7 else "OK",
            "learned_rules_count": len(self._learned_rules),
            "habituated_count": sum(
                1 for evt in _SENSORY_EVENTS
                if self._get_habituation_level(evt) > 0.0
            ),
        }

    # ================================================================
    # SOUSCRIPTIONS BUS
    # ================================================================

    def _subscribe_events(self):
        """Souscription aux 12 events du bus."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_surge)
            bus.subscribe("DOPAMINE_DIP", self._on_dopamine_dip)
            bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
            bus.subscribe("CIRCADIAN_PHASE_CHANGE", self._on_phase_change)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("COUNCIL_END", self._on_council_end)
            bus.subscribe("HALLUCINATION_DETECTED", self._on_hallucination)
            bus.subscribe("TISSUE_PATTERN_EMERGED", self._on_tissue_pattern)
            bus.subscribe("TISSUE_CREATIVITY_SPIKE", self._on_tissue_creativity)
            bus.subscribe("PREFRONTAL_GOAL_COMPLETE", self._on_goal_complete)
            bus.subscribe("NAP_MODE", self._on_nap_mode)
        except Exception as e:
            logger.warning(f"THALAMUS: Echec souscription bus: {e}")

    # ================================================================
    # HANDLERS
    # ================================================================

    async def _on_cardiac_beat(self, data: Dict[str, Any]):
        """CARDIAC_BEAT : declenche le cycle de mise a jour."""
        self._context["bpm"] = data.get("bpm", 60.0)
        self._context["emotion"] = data.get("emotion", "serenite")
        self._context["emotion_intensity"] = data.get("emotion_intensity", 0.3)
        await self._update_cycle()

    async def _on_reptilian_alert(self, data: Dict[str, Any]):
        """REPTILIAN_ALERT : boost immediat urgence a 0.9. Urgence = reveil force."""
        self._record_event("REPTILIAN_ALERT")
        self._context["threat_level"] = data.get("threat_level", 5.0)
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                self._scorecard[evt] = max(self._scorecard[evt], 0.9)
        if self._sleeping:
            await self._exit_sleep()

    async def _on_dopamine_surge(self, data: Dict[str, Any]):
        """DOPAMINE_SURGE : met a jour contexte dopamine."""
        self._context["dopamine_level"] = data.get("level", 0.8)

    async def _on_dopamine_dip(self, data: Dict[str, Any]):
        """DOPAMINE_DIP : met a jour contexte dopamine."""
        self._context["dopamine_level"] = data.get("level", 0.2)

    async def _on_knowledge_gap(self, data: Dict[str, Any]):
        """KNOWLEDGE_GAP_DETECTED : boost cognition."""
        self._record_event("KNOWLEDGE_GAP_DETECTED")
        if self._sleeping:
            self._buffer_event("KNOWLEDGE_GAP_DETECTED", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "cognition":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.15)

    async def _on_phase_change(self, data: Dict[str, Any]):
        """CIRCADIAN_PHASE_CHANGE : adapte le seuil."""
        phase = data.get("phase", "eveil")
        self._context["phase"] = phase
        if phase in PHASE_THRESHOLDS:
            self._threshold = PHASE_THRESHOLDS[phase]

    async def _on_routine_complete(self, data: Dict[str, Any]):
        """AUTONOMY_ROUTINE_COMPLETE : feedback saillance selon succes/echec."""
        intent = data.get("intent", "")
        status = data.get("status", "")
        cat = _INTENT_CATEGORY_HINTS.get(intent)
        if not cat:
            return
        if status == "success":
            for evt, c in EVENT_CATEGORIES.items():
                if c == cat:
                    self._scorecard[evt] = min(1.0, self._scorecard[evt] + ROUTINE_FEEDBACK_BOOST)
        elif status == "error":
            for evt, c in EVENT_CATEGORIES.items():
                if c == cat:
                    self._scorecard[evt] = max(0.0, self._scorecard[evt] - ROUTINE_FEEDBACK_MALUS)

        # Sprint E — Observation learned rules
        rule_info = self._observe_routine_outcome(intent, status)
        if rule_info:
            await bus.publish("THALAMUS_RULE_LEARNED", rule_info)

    async def _on_council_end(self, data: Dict[str, Any]):
        """COUNCIL_END : boost deliberation."""
        self._record_event("COUNCIL_END")
        if self._sleeping:
            self._buffer_event("COUNCIL_END", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "deliberation":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.2)

    async def _on_hallucination(self, data: Dict[str, Any]):
        """HALLUCINATION_DETECTED : boost urgence."""
        self._record_event("HALLUCINATION_DETECTED")
        if self._sleeping:
            self._buffer_event("HALLUCINATION_DETECTED", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.2)

    async def _on_tissue_pattern(self, data: Dict[str, Any]):
        """TISSUE_PATTERN_EMERGED : boost emergence."""
        self._record_event("TISSUE_PATTERN_EMERGED")
        if self._sleeping:
            self._buffer_event("TISSUE_PATTERN_EMERGED", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "emergence":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.15)

    async def _on_tissue_creativity(self, data: Dict[str, Any]):
        """TISSUE_CREATIVITY_SPIKE : boost emergence."""
        self._record_event("TISSUE_CREATIVITY_SPIKE")
        if self._sleeping:
            self._buffer_event("TISSUE_CREATIVITY_SPIKE", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "emergence":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.15)

    async def _on_goal_complete(self, data: Dict[str, Any]):
        """PREFRONTAL_GOAL_COMPLETE : boost cognition."""
        self._record_event("PREFRONTAL_GOAL_COMPLETE")
        if self._sleeping:
            self._buffer_event("PREFRONTAL_GOAL_COMPLETE", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "cognition":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.15)

    async def _on_nap_mode(self, data: Dict[str, Any]):
        """NAP_MODE : bascule mode sieste (Sprint B)."""
        active = data.get("active", data.get("sleeping", False))
        if active:
            self._enter_sleep()
        else:
            await self._exit_sleep()

    # ================================================================
    # MODE SIESTE (Sprint B)
    # ================================================================

    def _enter_sleep(self):
        """Entre en mode sieste : seuil monte, buffer vide."""
        self._sleeping = True
        self._pre_nap_threshold = self._threshold
        self._threshold = PHASE_THRESHOLDS["sommeil_profond"]
        self._nap_buffer.clear()
        self._nap_sleep_start = time.time()
        logger.info(
            f"THALAMUS: Entree en mode sieste "
            f"(seuil {self._pre_nap_threshold:.2f} -> {self._threshold:.2f})."
        )

    async def _exit_sleep(self):
        """Sort du mode sieste : restaure seuil, flush buffer, publie resume."""
        if not self._sleeping:
            return
        self._sleeping = False
        buffered_count = len(self._nap_buffer)
        duration = time.time() - getattr(self, "_nap_sleep_start", time.time())
        if self._pre_nap_threshold is not None:
            self._threshold = self._pre_nap_threshold
            self._pre_nap_threshold = None
        # Construire le resume AVANT le flush (le flush clear le buffer)
        wake_summary = self._build_wake_summary(duration)
        await self._flush_nap_buffer()
        # Publier le resume au reveil
        if wake_summary["event_count"] > 0:
            await bus.publish("THALAMUS_WAKE_SUMMARY", wake_summary)
        logger.info(
            f"THALAMUS: Reveil apres {duration:.0f}s "
            f"({buffered_count} events bufferises rejoues)."
        )

    def _build_wake_summary(self, nap_duration: float) -> Dict[str, Any]:
        """Construit le resume des events accumules pendant la sieste."""
        cat_counts: Dict[str, int] = {}
        events_list = []
        for entry in self._nap_buffer:
            evt_type = entry["event_type"]
            cat = EVENT_CATEGORIES.get(evt_type, "unknown")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            events_list.append({
                "event_type": evt_type,
                "category": cat,
                "timestamp": entry.get("timestamp", 0),
            })
        # Trier par categorie urgence d'abord, puis par timestamp
        priority_order = [
            "urgence", "cognition", "deliberation",
            "emergence", "motivation", "regulation",
        ]
        events_list.sort(key=lambda e: (
            priority_order.index(e["category"]) if e["category"] in priority_order else 99,
            e["timestamp"],
        ))
        top_category = max(cat_counts, key=cat_counts.get) if cat_counts else None
        return {
            "events_during_nap": events_list,
            "nap_duration": round(nap_duration, 1),
            "event_count": len(self._nap_buffer),
            "top_category": top_category,
            "category_counts": cat_counts,
        }

    def _buffer_event(self, event_type: str, data: Dict[str, Any]):
        """Bufferise un event pendant le sommeil."""
        if len(self._nap_buffer) < NAP_BUFFER_MAX:
            self._nap_buffer.append({
                "event_type": event_type,
                "data": data,
                "timestamp": time.time(),
            })

    async def _flush_nap_buffer(self):
        """Rejoue le buffer au reveil avec attenuation temporelle."""
        if not self._nap_buffer:
            return
        now = time.time()
        for entry in self._nap_buffer:
            evt_type = entry["event_type"]
            cat = EVENT_CATEGORIES.get(evt_type)
            if not cat:
                continue
            # Attenuation : events plus anciens recoivent 70% du boost
            age = now - entry.get("timestamp", now)
            factor = 0.7 if age > 60 else 1.0
            boost = 0.15 * factor
            for evt, c in EVENT_CATEGORIES.items():
                if c == cat:
                    self._scorecard[evt] = min(1.0, self._scorecard[evt] + boost)
        # Publication si changements significatifs
        has_change = any(
            abs(self._scorecard[k] - self._last_scorecard.get(k, 0.5)) > MIN_SALIENCE_CHANGE
            for k in self._scorecard
        )
        if has_change:
            filtered = {k: round(v, 3) for k, v in self._scorecard.items() if v > 0.1}
            await bus.publish("THALAMUS_SALIENCE", {
                "scorecard": filtered,
                "threshold": round(self._threshold, 3),
                "focus": self._attention_focus,
                "source": "nap_flush",
            })
            self._last_scorecard = dict(self._scorecard)
        self._nap_buffer.clear()

    # ================================================================
    # CYCLE DE MISE A JOUR (declenche par CARDIAC_BEAT)
    # ================================================================

    async def _update_cycle(self):
        """Cycle complet de mise a jour du thalamus."""
        self._cycle_count += 1
        old_focus = self._attention_focus

        # 1. Decroissance
        for k in self._scorecard:
            self._scorecard[k] *= SALIENCE_DECAY

        # 1b. Application des regles apprises (Sprint E)
        if self._learned_rules:
            self._apply_learned_rules()

        # 1c. Habituation extra decay (Sprint F)
        self._apply_habituation()

        # 2. Bonus contextuel urgence (threat_level)
        threat = self._context.get("threat_level", 0.0)
        if threat > 0:
            urgence_bonus = (threat / 10.0) * 0.3
            for evt, cat in EVENT_CATEGORIES.items():
                if cat == "urgence":
                    self._scorecard[evt] += urgence_bonus

        # 3. Modulation dopaminergique du seuil
        dopamine = self._context.get("dopamine_level", 0.5)
        if dopamine > 0.7:
            self._threshold -= 0.1  # Plus attentif
        elif dopamine < 0.3:
            self._threshold += 0.1  # Moins attentif

        # 4. Modulation cardiaque (arousal)
        bpm = self._context.get("bpm", 60.0)
        if bpm > 80:
            self._threshold -= 0.05  # Arousal haute → plus attentif
        elif bpm < 50:
            self._threshold += 0.05  # Arousal basse → moins attentif

        # 5. Clamping
        for k in self._scorecard:
            self._scorecard[k] = max(0.0, min(1.0, self._scorecard[k]))
        self._threshold = max(0.1, min(0.95, self._threshold))

        # 6. Focus avec inertie (Sprint C)
        raw_focus = self._compute_dominant_focus()
        old_focus_pre = self._attention_focus
        self._attention_focus = self._apply_focus_inertia(raw_focus)

        # 6b. Fatigue attentionnelle
        if self._attention_focus == old_focus_pre and self._attention_focus is not None:
            self._attention_fatigue = min(
                ATTENTION_FATIGUE_MAX,
                self._attention_fatigue + ATTENTION_FATIGUE_RATE
            )
        else:
            self._attention_fatigue = max(0.0, self._attention_fatigue - ATTENTION_FATIGUE_RECOVERY)

        # 6c. Historique focus (sur shift confirme)
        if self._attention_focus != old_focus_pre:
            self._focus_since_cycle = self._cycle_count
            self._focus_history.append({
                "focus": self._attention_focus,
                "cycle": self._cycle_count,
                "timestamp": time.time(),
            })
            if len(self._focus_history) > 20:
                self._focus_history = self._focus_history[-20:]

        # En mode sommeil : pas de publication (focus gele, bruit supprime)
        if self._sleeping:
            if self._cycle_count % SAVE_INTERVAL == 0:
                self._save()
            return

        # 7. Publication THALAMUS_SALIENCE si changement significatif
        has_change = any(
            abs(self._scorecard[k] - self._last_scorecard.get(k, 0.5)) > MIN_SALIENCE_CHANGE
            for k in self._scorecard
        )
        if has_change:
            filtered = {k: round(v, 3) for k, v in self._scorecard.items() if v > 0.1}
            await bus.publish("THALAMUS_SALIENCE", {
                "scorecard": filtered,
                "threshold": round(self._threshold, 3),
                "focus": self._attention_focus,
            })
            self._last_scorecard = dict(self._scorecard)

        # 8. Publication THALAMUS_ATTENTION_SHIFT si changement de focus
        if self._attention_focus != old_focus:
            await bus.publish("THALAMUS_ATTENTION_SHIFT", {
                "old_focus": old_focus,
                "new_focus": self._attention_focus,
                "cause": "cycle_update",
            })

        # 9. Sauvegarde periodique
        if self._cycle_count % SAVE_INTERVAL == 0:
            self._save()

    def _apply_focus_inertia(self, new_focus: Optional[str]) -> Optional[str]:
        """Filtre les changements de focus trop rapides (anti-jitter)."""
        if new_focus == self._attention_focus:
            # Focus stable → reset candidat
            self._pending_focus = None
            self._pending_focus_count = 0
            return self._attention_focus

        # Nouveau candidat ?
        if new_focus == self._pending_focus:
            self._pending_focus_count += 1
        else:
            self._pending_focus = new_focus
            self._pending_focus_count = 1

        # Le candidat doit persister FOCUS_INERTIA_CYCLES cycles
        if self._pending_focus_count >= FOCUS_INERTIA_CYCLES:
            self._pending_focus = None
            self._pending_focus_count = 0
            return new_focus

        # Pas encore assez stable → garder l'ancien focus
        return self._attention_focus

    def _compute_dominant_focus(self) -> Optional[str]:
        """Categorie avec la plus haute somme de saillances."""
        cat_sums: Dict[str, float] = {}
        for evt, cat in EVENT_CATEGORIES.items():
            cat_sums[cat] = cat_sums.get(cat, 0.0) + self._scorecard.get(evt, 0.0)
        if not cat_sums:
            return None
        best_cat = max(cat_sums, key=cat_sums.get)
        return best_cat

    def _compute_category_strength(self, category: str) -> float:
        """Somme des saillances d'une categorie."""
        total = 0.0
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == category:
                total += self._scorecard.get(evt, 0.0)
        return total

    # ================================================================
    # HABITUATION & NOVELTY (Sprint F)
    # ================================================================

    def _record_event(self, event_type: str):
        """Enregistre un timestamp pour un event sensoriel. Applique novelty boost si absent longtemps."""
        if event_type not in _SENSORY_EVENTS:
            return

        now = time.time()

        # Detection novelty AVANT enregistrement
        last_seen = self._event_last_seen.get(event_type)
        is_novel = (last_seen is None) or (now - last_seen >= NOVELTY_ABSENCE_THRESHOLD)

        if is_novel and not self._sleeping:
            # Boost ponctuel sur la categorie de cet event (pas en sieste)
            cat = EVENT_CATEGORIES.get(event_type)
            if cat:
                for evt, c in EVENT_CATEGORIES.items():
                    if c == cat:
                        self._scorecard[evt] = min(1.0, self._scorecard[evt] + NOVELTY_BOOST)

        # Enregistrer timestamp (toujours, meme en sieste)
        self._event_timestamps[event_type].append(now)
        self._event_last_seen[event_type] = now

        # Elagage fenetre glissante (temps)
        cutoff = now - HABITUATION_TIME_WINDOW
        self._event_timestamps[event_type] = [
            ts for ts in self._event_timestamps[event_type] if ts >= cutoff
        ][-HABITUATION_WINDOW:]

    def _get_habituation_level(self, event_type: str) -> float:
        """Retourne le niveau d'habituation [0.0, 1.0] progressif."""
        if event_type not in _SENSORY_EVENTS:
            return 0.0
        now = time.time()
        cutoff = now - HABITUATION_TIME_WINDOW
        recent = [ts for ts in self._event_timestamps.get(event_type, []) if ts >= cutoff]
        count = len(recent)
        if count < HABITUATION_THRESHOLD:
            return 0.0
        # Lineaire de THRESHOLD → WINDOW (0.0 → 1.0)
        span = HABITUATION_WINDOW - HABITUATION_THRESHOLD
        if span <= 0:
            return 1.0
        return min(1.0, (count - HABITUATION_THRESHOLD) / span)

    def _apply_habituation(self):
        """Applique extra decay aux events sensoriels habitues."""
        for event_type in _SENSORY_EVENTS:
            level = self._get_habituation_level(event_type)
            if level > 0.0:
                malus = HABITUATION_EXTRA_DECAY * level
                if event_type in self._scorecard:
                    self._scorecard[event_type] = max(0.0, self._scorecard[event_type] - malus)

    # ================================================================
    # LEARNED RULES (Sprint E)
    # ================================================================

    def _observe_routine_outcome(self, intent: str, status: str) -> Optional[Dict[str, Any]]:
        """Enregistre observation success/error pour un intent. Appelle cristallisation.
        Retourne les infos de la regle cristallisee (ou None)."""
        cat = _INTENT_CATEGORY_HINTS.get(intent)
        if not cat:
            return None
        if status not in ("success", "error"):
            return None

        if intent not in self._intent_observations:
            self._intent_observations[intent] = {"success": 0, "error": 0}

        obs = self._intent_observations[intent]
        obs[status] += 1

        # Contradiction : decremente le compteur oppose
        opposite = "error" if status == "success" else "success"
        if obs[opposite] > 0:
            obs[opposite] -= 1

        return self._maybe_crystallize_rule(intent)

    def _maybe_crystallize_rule(self, intent: str) -> Optional[Dict[str, Any]]:
        """Si seuil atteint : cree/renforce/degrade regle. Reset compteurs.
        Retourne les infos de la regle affectee (ou None)."""
        obs = self._intent_observations.get(intent)
        if not obs:
            return None

        cat = _INTENT_CATEGORY_HINTS.get(intent)
        if not cat:
            return None

        for direction in ("success", "error"):
            if obs[direction] < RULE_CRYSTALLIZE_THRESHOLD:
                continue

            rule_type = "boost" if direction == "success" else "dampen"
            idx = self._find_rule(intent)
            action = None

            if idx is not None:
                existing = self._learned_rules[idx]
                if existing["type"] == rule_type:
                    # Renforcement
                    existing["weight"] = min(
                        RULE_MAX_WEIGHT,
                        existing["weight"] + RULE_REINFORCE_STEP
                    )
                    action = "reinforced"
                else:
                    # Contradiction avec regle existante → degradation
                    existing["weight"] -= RULE_DECAY_ON_CONTRADICTION
                    if existing["weight"] < RULE_MIN_WEIGHT:
                        self._learned_rules.pop(idx)
                        action = "removed"
                    else:
                        action = "weakened"
            else:
                # Nouvelle regle
                if len(self._learned_rules) >= MAX_LEARNED_RULES:
                    self._evict_weakest_rule()
                weight = RULE_BOOST_WEIGHT if rule_type == "boost" else RULE_DAMPEN_WEIGHT
                self._learned_rules.append({
                    "intent": intent,
                    "category": cat,
                    "type": rule_type,
                    "weight": weight,
                })
                action = "created"

            # Reset compteurs apres cristallisation
            self._intent_observations[intent] = {"success": 0, "error": 0}
            return {
                "intent": intent,
                "category": cat,
                "type": rule_type,
                "action": action,
                "rules_count": len(self._learned_rules),
            }
        return None

    def _find_rule(self, intent: str) -> Optional[int]:
        """Retourne l'index de la regle pour un intent, ou None."""
        for i, rule in enumerate(self._learned_rules):
            if rule["intent"] == intent:
                return i
        return None

    def _evict_weakest_rule(self):
        """Supprime la regle avec le plus petit poids."""
        if not self._learned_rules:
            return
        min_idx = 0
        min_weight = self._learned_rules[0]["weight"]
        for i, rule in enumerate(self._learned_rules[1:], 1):
            if rule["weight"] < min_weight:
                min_weight = rule["weight"]
                min_idx = i
        self._learned_rules.pop(min_idx)

    def _apply_learned_rules(self):
        """Applique les regles apprises sur la scorecard."""
        for rule in self._learned_rules:
            cat = rule["category"]
            weight = rule["weight"]
            for evt, c in EVENT_CATEGORIES.items():
                if c == cat:
                    if rule["type"] == "boost":
                        self._scorecard[evt] = min(1.0, self._scorecard[evt] + weight)
                    else:
                        self._scorecard[evt] = max(0.0, self._scorecard[evt] - weight)

    def get_learned_rules(self) -> List[Dict]:
        """Copie des regles pour API/debug."""
        import copy
        return copy.deepcopy(self._learned_rules)

    # ================================================================
    # MODULATION ZONES TISSUE (Couplage Thalamus→Tissu)
    # ================================================================

    # Mapping catégorie attention → zones tissue
    _CATEGORY_ZONE_MAP = {
        "urgence":      ["threat", "stability"],
        "motivation":   ["dopamine", "desire"],
        "cognition":    ["cognition", "memory"],
        "deliberation": ["goals", "cognition"],
        "emergence":    ["creativity", "emotion"],
        "regulation":   ["stability", "memory"],
    }

    def compute_zone_modulation(self) -> Dict[str, float]:
        """Calcule un facteur de modulation [0.5, 2.0] par zone tissue.

        Basé sur les forces d'attention par catégorie.
        Retourne dict {zone_name: factor}.
        """
        if not self._scorecard:
            return {}

        modulation = {}
        for category, zones in self._CATEGORY_ZONE_MAP.items():
            strength = self._compute_category_strength(category)
            # strength [0,1+] → factor [0.7, 1.5]
            # Focus fort → boost les zones associées
            factor = 0.7 + min(strength, 1.0) * 0.8
            for zone in zones:
                # Si plusieurs catégories ciblent la même zone, prendre le max
                modulation[zone] = max(modulation.get(zone, 1.0), factor)

        return modulation


# --- Singleton module-level ---

thalamus = Thalamus()


# --- Utilitaire module-level ---

def is_worth_attention(event_type: str) -> bool:
    """Consulte le thalamus si disponible, sinon True (safe default)."""
    try:
        return thalamus.is_salient(event_type)
    except Exception:
        return True
