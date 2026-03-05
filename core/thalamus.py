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
            return min(1.5, focus_strength * 0.5)
        else:
            return -0.3

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
        """AUTONOMY_ROUTINE_COMPLETE : historique routines."""
        pass  # Comptabilise dans le cycle

    async def _on_council_end(self, data: Dict[str, Any]):
        """COUNCIL_END : boost deliberation."""
        if self._sleeping:
            self._buffer_event("COUNCIL_END", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "deliberation":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.2)

    async def _on_hallucination(self, data: Dict[str, Any]):
        """HALLUCINATION_DETECTED : boost urgence."""
        if self._sleeping:
            self._buffer_event("HALLUCINATION_DETECTED", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.2)

    async def _on_tissue_pattern(self, data: Dict[str, Any]):
        """TISSUE_PATTERN_EMERGED : boost emergence."""
        if self._sleeping:
            self._buffer_event("TISSUE_PATTERN_EMERGED", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "emergence":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.15)

    async def _on_tissue_creativity(self, data: Dict[str, Any]):
        """TISSUE_CREATIVITY_SPIKE : boost emergence."""
        if self._sleeping:
            self._buffer_event("TISSUE_CREATIVITY_SPIKE", data)
            return
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "emergence":
                self._scorecard[evt] = min(1.0, self._scorecard[evt] + 0.15)

    async def _on_goal_complete(self, data: Dict[str, Any]):
        """PREFRONTAL_GOAL_COMPLETE : boost cognition."""
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
        """Sort du mode sieste : restaure seuil, flush buffer."""
        if not self._sleeping:
            return
        self._sleeping = False
        buffered_count = len(self._nap_buffer)
        duration = time.time() - getattr(self, "_nap_sleep_start", time.time())
        if self._pre_nap_threshold is not None:
            self._threshold = self._pre_nap_threshold
            self._pre_nap_threshold = None
        await self._flush_nap_buffer()
        logger.info(
            f"THALAMUS: Reveil apres {duration:.0f}s "
            f"({buffered_count} events bufferises rejoues)."
        )

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

        # 6. Focus : categorie avec la plus haute somme de saillances
        self._attention_focus = self._compute_dominant_focus()

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


# --- Singleton module-level ---

thalamus = Thalamus()


# --- Utilitaire module-level ---

def is_worth_attention(event_type: str) -> bool:
    """Consulte le thalamus si disponible, sinon True (safe default)."""
    try:
        return thalamus.is_salient(event_type)
    except Exception:
        return True
