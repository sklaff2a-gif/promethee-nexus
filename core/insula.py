# core/insula.py — Insula : Conscience Interoceptive
# Percoit et integre l'etat interne global du systeme.
# Genere un "sentiment visceral" (gut feeling) sur chaque action
# base sur l'etat des organes. 0 LLM, 100% deterministe.

import json
import os
import time
import logging
from collections import deque
from typing import Dict, Any, Optional

from core.event_bus.bus import bus

logger = logging.getLogger("Insula")

# --- Fichier de persistance ---

INSULA_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "insula_state.json"
)

# --- Constantes ---

INTEROCEPTION_CYCLE_SECONDS = 30
BODY_STATE_DIMENSIONS = ["energy", "stress", "arousal", "valence", "confidence", "fatigue"]
SOMATIC_MARKER_DECAY = 0.98
SOMATIC_MARKER_LEARNING_RATE = 0.1
GUT_FEELING_THRESHOLD = 0.3
AWARENESS_HISTORY_SIZE = 40


class Insula:
    """Conscience interoceptive — percoit l'etat interne global."""

    _instance: Optional["Insula"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._subscribed = False

        # Etat corporel (chaque dimension [0, 1])
        self.body_state: Dict[str, float] = {
            "energy": 0.6,
            "stress": 0.3,
            "arousal": 0.4,
            "valence": 0.5,
            "confidence": 0.6,
            "fatigue": 0.3,
        }

        # Marqueurs somatiques appris (intent → float [-1, +1])
        self.somatic_markers: Dict[str, float] = {}

        # Historique de perception
        self.awareness_history: deque = deque(maxlen=AWARENESS_HISTORY_SIZE)

        # Precision interoceptive (capacite a predire son propre etat)
        self.interoception_accuracy: float = 0.5

        self.total_readings: int = 0
        self._cycle_count: int = 0

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour les tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite appelee depuis main.py."""
        self._subscribe_events()
        logger.info("[INSULA] Module initialise.")

    # --- Souscriptions bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("HYPOTHALAMUS_REGULATION", self._on_hypothalamus_regulation)
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("DOPAMINE_STATE", self._on_dopamine_update)
            bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_update)
            bus.subscribe("DOPAMINE_DIP", self._on_dopamine_dip)
            bus.subscribe("CIRCADIAN_PHASE_CHANGE", self._on_circadian_phase)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            # Sensorium hardware (Sprint 4 Sensorium)
            bus.subscribe("SENSORIUM_UPDATE", self._on_sensorium_update)
        except Exception as e:
            logger.warning(f"[INSULA] Souscription echouee: {e}")

    # --- Handlers bus ---

    async def _on_sensorium_update(self, event: dict):
        """Hardware sensorium → body_state fatigue/stress/energy."""
        comfort = event.get("comfort", 0.7)
        senses = event.get("senses", {})
        effort = senses.get("effort", 0)
        vitality = senses.get("vitality", 0.5)
        stress_hw = 1.0 - comfort
        # Blending EMA : 70-80% ancien + 20-30% nouveau
        self.body_state["fatigue"] = min(1.0, self.body_state["fatigue"] * 0.7 + effort * 0.3)
        self.body_state["stress"] = min(1.0, self.body_state["stress"] * 0.8 + stress_hw * 0.2)
        self.body_state["energy"] = min(1.0, self.body_state["energy"] * 0.8 + (1.0 - vitality) * 0.2)

    async def _on_cardiac_beat(self, event: dict):
        """Met a jour arousal depuis BPM."""
        bpm = event.get("bpm", 60.0)
        # Normaliser BPM [40, 120] → arousal [0, 1]
        arousal = max(0.0, min(1.0, (bpm - 40.0) / 80.0))
        self.body_state["arousal"] = round(arousal, 3)

    async def _on_hypothalamus_regulation(self, event: dict):
        """Met a jour energy et stress depuis les erreurs homeostatiques."""
        errors = event.get("errors", {})
        stability = event.get("stability_score", 0.5)

        # Energy : inverse de l'erreur energy
        energy_err = errors.get("energy", 0.0)
        self.body_state["energy"] = max(0.0, min(1.0, 0.6 - energy_err * 0.3))

        # Stress : erreur stress directe
        stress_err = errors.get("stress", 0.0)
        self.body_state["stress"] = max(0.0, min(1.0, 0.3 + stress_err * 0.4))

        # Confidence : proportionnelle a la stabilite
        self.body_state["confidence"] = round(stability, 3)

    async def _on_reptilian_alert(self, event: dict):
        """Spike de stress, baisse de confidence."""
        severity = event.get("severity", event.get("threat_level", 0.5))
        self.body_state["stress"] = min(1.0, self.body_state["stress"] + float(severity) * 0.3)
        self.body_state["confidence"] = max(0.0, self.body_state["confidence"] - float(severity) * 0.2)

    async def _on_dopamine_update(self, event: dict):
        """Met a jour valence depuis dopamine."""
        level = event.get("level", event.get("dopamine_level", 0.5))
        self.body_state["valence"] = max(0.0, min(1.0, float(level)))

    async def _on_dopamine_dip(self, event: dict):
        """Dip dopaminique → baisse de valence."""
        self.body_state["valence"] = max(0.0, self.body_state["valence"] - 0.2)

    async def _on_circadian_phase(self, event: dict):
        """Met a jour fatigue depuis le cycle circadien."""
        phase = event.get("phase", "eveil")
        fatigue_map = {
            "eveil": 0.2,
            "crepuscule": 0.6,
            "sommeil_profond": 0.9,
            "aube": 0.4,
        }
        self.body_state["fatigue"] = fatigue_map.get(phase, 0.3)

    async def _on_routine_complete(self, event: dict):
        """Met a jour le marqueur somatique apres une routine."""
        intent = event.get("intent", "")
        status = event.get("status", "success")
        quality = event.get("quality_score", 0.5)

        if status == "skipped":
            return

        # Outcome [-1, +1]
        if status in ("success", "completed"):
            outcome = min(1.0, quality)
        else:
            outcome = -0.5

        self._update_somatic_marker(intent, outcome)
        self.total_readings += 1

        # Enregistrer la perception
        self.awareness_history.append({
            "timestamp": time.time(),
            "body_state": dict(self.body_state),
            "intent": intent,
            "outcome": outcome,
        })

        # Save periodique
        if self.total_readings % 10 == 0:
            self._save()

    # --- Marqueurs somatiques ---

    def _update_somatic_marker(self, intent: str, outcome: float):
        """Apprentissage associatif : intent → body_state au moment du resultat."""
        current_marker = self.somatic_markers.get(intent, 0.0)
        # Influence de l'etat corporel sur le marqueur
        body_signal = (self.body_state["valence"] - 0.5) * 2.0  # [-1, +1]
        combined = (outcome + body_signal) / 2.0

        new_marker = current_marker + SOMATIC_MARKER_LEARNING_RATE * (combined - current_marker)
        self.somatic_markers[intent] = max(-1.0, min(1.0, round(new_marker, 4)))

    def decay_somatic_markers(self):
        """Decay naturel des marqueurs somatiques."""
        to_remove = []
        for intent in self.somatic_markers:
            self.somatic_markers[intent] *= SOMATIC_MARKER_DECAY
            if abs(self.somatic_markers[intent]) < 0.01:
                to_remove.append(intent)
        for intent in to_remove:
            del self.somatic_markers[intent]

    # --- Gut feeling ---

    def _compute_gut_feeling(self, intent: str) -> float:
        """Combinaison marqueur somatique + etat corporel.
        Retourne float [-1, +1]."""
        # 1. Marqueur somatique appris
        marker = self.somatic_markers.get(intent, 0.0)

        # 2. Influence de l'etat corporel
        stress = self.body_state.get("stress", 0.3)
        energy = self.body_state.get("energy", 0.6)
        valence = self.body_state.get("valence", 0.5)

        # Stress haut → gut feeling negatif pour taches lourdes
        body_influence = (valence - 0.5) * 0.3 + (energy - 0.5) * 0.2 - (stress - 0.5) * 0.3

        # 3. Coherence interne (toutes dimensions proches → confiance haute)
        coherence = self._compute_coherence()
        confidence_factor = 0.5 + coherence * 0.5  # [0.5, 1.0]

        # Combinaison
        gut = (marker * 0.6 + body_influence * 0.4) * confidence_factor
        return max(-1.0, min(1.0, round(gut, 4)))

    def _compute_coherence(self) -> float:
        """Coherence interne [0, 1]. 1 = toutes dimensions proches."""
        values = list(self.body_state.values())
        if len(values) < 2:
            return 1.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        # Normaliser : variance max ~0.25 (si valeurs entre 0 et 1)
        coherence = max(0.0, 1.0 - variance * 4.0)
        return round(coherence, 3)

    # --- Scoring (Couche 18) ---

    def compute_interoception_bonus(self, intent: str) -> float:
        """Bonus interoceptif pour un intent. Range [-0.5, +1.0]."""
        gut = self._compute_gut_feeling(intent)

        if abs(gut) < GUT_FEELING_THRESHOLD:
            return 0.0

        confidence = self.body_state.get("confidence", 0.5)

        if gut > 0:
            # Gut feeling positif + confiance → bonus
            bonus = gut * confidence
            return min(1.0, round(bonus, 3))
        else:
            # Gut feeling negatif + stress haut → malus
            stress = self.body_state.get("stress", 0.3)
            malus = gut * (0.5 + stress * 0.5)
            return max(-0.5, round(malus, 3))

    # --- Contexte ---

    def get_body_awareness_context(self) -> str:
        """Etat corporel resume pour purpose_context."""
        dims = self.body_state
        high = [d for d, v in dims.items() if v > 0.7]
        low = [d for d, v in dims.items() if v < 0.3]

        parts = []
        if high:
            parts.append(f"haut({', '.join(high)})")
        if low:
            parts.append(f"bas({', '.join(low)})")

        coherence = self._compute_coherence()
        if not parts:
            return f"[INTEROCEPTION] Corps equilibre (coherence={coherence:.2f})"

        return f"[INTEROCEPTION] {'; '.join(parts)} (coherence={coherence:.2f})"

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour snapshot et endpoint API."""
        # Top marqueurs somatiques
        sorted_markers = sorted(
            self.somatic_markers.items(),
            key=lambda kv: abs(kv[1]),
            reverse=True,
        )[:5]

        return {
            "body_state": dict(self.body_state),
            "coherence_score": self._compute_coherence(),
            "somatic_markers_top5": dict(sorted_markers),
            "somatic_markers_count": len(self.somatic_markers),
            "interoception_accuracy": self.interoception_accuracy,
            "total_readings": self.total_readings,
            "cycle_count": self._cycle_count,
        }

    # --- Persistence ---

    def _save(self):
        """Sauvegarde atomique."""
        try:
            data = {
                "body_state": dict(self.body_state),
                "somatic_markers": dict(self.somatic_markers),
                "interoception_accuracy": self.interoception_accuracy,
                "total_readings": self.total_readings,
                "cycle_count": self._cycle_count,
                "saved_at": time.time(),
            }
            tmp = INSULA_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(INSULA_STATE_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, INSULA_STATE_FILE)
        except Exception as e:
            logger.warning(f"[INSULA] Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat."""
        try:
            if os.path.exists(INSULA_STATE_FILE):
                with open(INSULA_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.body_state = data.get("body_state", self.body_state)
                self.somatic_markers = data.get("somatic_markers", {})
                self.interoception_accuracy = data.get("interoception_accuracy", 0.5)
                self.total_readings = data.get("total_readings", 0)
                self._cycle_count = data.get("cycle_count", 0)
                logger.info("[INSULA] Etat restaure.")
        except Exception as e:
            logger.warning(f"[INSULA] Chargement echoue: {e}")


# --- Singleton ---
insula = Insula()
