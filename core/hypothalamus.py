# core/hypothalamus.py — Hypothalamus : Regulateur Homeostatique
# Maintient l'equilibre interne en surveillant des variables vitales
# et en emettant des corrections. Comme un thermostat biologique.
# 0 LLM, 100% deterministe, cycle ~45s.

import json
import os
import time
import logging
from collections import deque
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("Hypothalamus")

# --- Fichier de persistance ---

HYPOTHALAMUS_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "hypothalamus_state.json"
)

# --- Constantes ---

REGULATION_CYCLE_SECONDS = 45
SETPOINTS = {
    "energy": {"target": 0.6, "tolerance": 0.15, "weight": 1.0},
    "stress": {"target": 0.3, "tolerance": 0.2, "weight": 0.8},
    "dopamine": {"target": 0.5, "tolerance": 0.2, "weight": 0.7},
    "cardiac_bpm": {"target": 60.0, "tolerance": 15.0, "weight": 0.6},
    "sleep_pressure": {"target": 0.4, "tolerance": 0.2, "weight": 0.5},
}
CORRECTION_STRENGTH = 0.3
HOMEOSTASIS_HISTORY_SIZE = 50
ALARM_THRESHOLD = 0.7

# --- Mapping intent → variable favorisee/penalisee ---

_INTENT_ENERGY_MAP = {
    # Routines legeres → favorisees quand energy bas
    "MEMORY_CLEANUP": 0.3,
    "AUDIT_STRUCTURE": 0.2,
    "JOURNAL_REFLEXION": 0.2,
    # Routines lourdes → penalisees quand energy bas
    "COUNCIL_DEBATE": -0.4,
    "EXPANSION_CODE": -0.3,
    "EVOLUTION_PIPELINE": -0.3,
    "CREATIVE_EXPLORATION": -0.2,
}

_INTENT_STRESS_MAP = {
    # Routines calmes → favorisees quand stress haut
    "MEMORY_CLEANUP": 0.3,
    "JOURNAL_REFLEXION": 0.3,
    "AUDIT_STRUCTURE": 0.2,
    # Routines excitantes → penalisees quand stress haut
    "CREATIVE_EXPLORATION": -0.3,
    "EXPANSION_CODE": -0.2,
    "EVOLUTION_PIPELINE": -0.2,
}

_INTENT_DOPAMINE_MAP = {
    # Creatif → favorise quand dopamine bas
    "CREATIVE_EXPLORATION": 0.3,
    "EXPANSION_CODE": 0.2,
    "COUNCIL_DEBATE": 0.2,
    # Repetitif → penalise quand dopamine bas
    "MEMORY_CLEANUP": -0.2,
    "AUDIT_STRUCTURE": -0.2,
}


class Hypothalamus:
    """Regulateur homeostatique — maintient l'equilibre interne."""

    _instance: Optional["Hypothalamus"] = None

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

        # Valeurs courantes des variables surveillees
        self.current_values: Dict[str, float] = {
            "energy": 0.6,
            "stress": 0.3,
            "dopamine": 0.5,
            "cardiac_bpm": 60.0,
            "sleep_pressure": 0.4,
        }

        # Signaux d'erreur (deviation par rapport au setpoint)
        self.error_signals: Dict[str, float] = {}

        # Historique des corrections
        self.corrections_history: deque = deque(maxlen=HOMEOSTASIS_HISTORY_SIZE)

        # Compteurs
        self.total_corrections: int = 0
        self.alarms_triggered: int = 0
        self._cycle_count: int = 0
        self._last_regulation: float = 0.0

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour les tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite appelee depuis main.py."""
        self._subscribe_events()
        logger.info("[HYPOTHALAMUS] Module initialise.")

    # --- Souscriptions bus ---

    def _subscribe_events(self):
        """Souscrit aux events du bus."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("DOPAMINE_UPDATE", self._on_dopamine_update)
            bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_update)
            bus.subscribe("DOPAMINE_DIP", self._on_dopamine_update)
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("CIRCADIAN_PHASE_CHANGE", self._on_circadian_phase)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        except Exception as e:
            logger.warning(f"[HYPOTHALAMUS] Souscription echouee: {e}")

    # --- Handlers bus ---

    async def _on_cardiac_beat(self, event: dict):
        """Met a jour cardiac_bpm depuis le battement cardiaque."""
        bpm = event.get("bpm", 60.0)
        self.current_values["cardiac_bpm"] = float(bpm)
        # Regulation sur chaque battement (cycle naturel)
        await self.regulate()

    async def _on_dopamine_update(self, event: dict):
        """Met a jour le niveau de dopamine."""
        level = event.get("level", event.get("dopamine_level", 0.5))
        self.current_values["dopamine"] = max(0.0, min(1.0, float(level)))

    async def _on_reptilian_alert(self, event: dict):
        """Met a jour le stress depuis les alertes reptiliennes."""
        severity = event.get("severity", event.get("threat_level", 0.5))
        # Spike de stress proportionnel a la severite
        current = self.current_values["stress"]
        spike = min(1.0, current + float(severity) * 0.3)
        self.current_values["stress"] = spike

    async def _on_circadian_phase(self, event: dict):
        """Met a jour la pression de sommeil depuis le cycle circadien."""
        phase = event.get("phase", "eveil")
        pressure_map = {
            "eveil": 0.2,
            "crepuscule": 0.6,
            "sommeil_profond": 0.9,
            "aube": 0.4,
        }
        self.current_values["sleep_pressure"] = pressure_map.get(phase, 0.4)

    async def _on_routine_complete(self, event: dict):
        """Met a jour l'energie basee sur le budget restant."""
        budget_used = event.get("budget_used", 0)
        budget_max = event.get("budget_max", 200)
        if budget_max > 0:
            remaining_ratio = max(0.0, 1.0 - budget_used / budget_max)
            # Energie = proportion du budget restant
            self.current_values["energy"] = 0.2 + remaining_ratio * 0.7

    # --- Calcul d'erreur ---

    def _compute_error(self, variable: str) -> float:
        """Calcule l'erreur normalisee pour une variable.
        Retourne float [-1, +1] : (current - target) / tolerance, clampe.
        Positif = trop haut, Negatif = trop bas."""
        sp = SETPOINTS.get(variable)
        if not sp:
            return 0.0
        current = self.current_values.get(variable, sp["target"])
        deviation = current - sp["target"]
        if sp["tolerance"] == 0:
            return 0.0
        error = deviation / sp["tolerance"]
        return max(-1.0, min(1.0, error))

    def _compute_all_errors(self) -> Dict[str, float]:
        """Calcule les erreurs pour toutes les variables."""
        return {var: self._compute_error(var) for var in SETPOINTS}

    # --- Generation de corrections ---

    def _generate_correction(self, variable: str, error: float) -> Dict[str, Any]:
        """Genere une correction pour une variable en desequilibre.
        Retourne {variable, error, action, strength}."""
        if abs(error) < 0.1:
            return {"variable": variable, "error": error, "action": "none", "strength": 0.0}

        strength = abs(error) * CORRECTION_STRENGTH
        if error > 0:
            action = "decrease"
        else:
            action = "increase"

        return {
            "variable": variable,
            "error": round(error, 3),
            "action": action,
            "strength": round(strength, 3),
        }

    # --- Cycle de regulation ---

    async def regulate(self):
        """Cycle principal : compute errors → corrections → publish."""
        self._cycle_count += 1
        self.error_signals = self._compute_all_errors()

        corrections = []
        alarms = []
        for var, error in self.error_signals.items():
            correction = self._generate_correction(var, error)
            if correction["action"] != "none":
                corrections.append(correction)
                self.total_corrections += 1

            # Alarme si deviation trop forte
            if abs(error) > ALARM_THRESHOLD:
                alarm = {"variable": var, "error": round(error, 3), "severity": round(abs(error), 3)}
                alarms.append(alarm)
                self.alarms_triggered += 1

        # Stocker dans l'historique
        if corrections:
            self.corrections_history.append({
                "timestamp": time.time(),
                "corrections": corrections,
                "cycle": self._cycle_count,
            })

        # Publier regulation
        stability = self._compute_stability_score()
        try:
            await bus.publish("HYPOTHALAMUS_REGULATION", {
                "errors": self.error_signals,
                "corrections": corrections,
                "stability_score": round(stability, 3),
                "cycle": self._cycle_count,
            })
        except Exception:
            pass

        # Publier alarmes
        for alarm in alarms:
            try:
                await bus.publish("HYPOTHALAMUS_ALARM", alarm)
            except Exception:
                pass

        # Cooldown cardiaque si BPM trop haut
        bpm_error = self.error_signals.get("cardiac_bpm", 0.0)
        if bpm_error > 0.5:
            try:
                await bus.publish("HYPOTHALAMUS_COOLDOWN", {
                    "reason": "cardiac_bpm_high",
                    "bpm": self.current_values.get("cardiac_bpm", 60.0),
                })
            except Exception:
                pass

        self._last_regulation = time.time()

        # Save periodique (toutes les 10 cycles)
        if self._cycle_count % 10 == 0:
            self._save()

    def _compute_stability_score(self) -> float:
        """Score de stabilite globale [0, 1]. 1 = parfaitement stable."""
        if not self.error_signals:
            return 1.0
        weighted_sum = 0.0
        weight_total = 0.0
        for var, error in self.error_signals.items():
            sp = SETPOINTS.get(var, {})
            w = sp.get("weight", 0.5)
            weighted_sum += abs(error) * w
            weight_total += w
        if weight_total == 0:
            return 1.0
        avg_error = weighted_sum / weight_total
        return max(0.0, 1.0 - avg_error)

    # --- Scoring (Couche 17) ---

    def compute_homeostasis_bonus(self, intent: str) -> float:
        """Bonus/malus homeostatique pour un intent. Range [-1.0, +1.5]."""
        if not self.error_signals:
            return 0.0

        bonus = 0.0

        # Energie : favorise routines legeres si energy bas
        energy_error = self.error_signals.get("energy", 0.0)
        if energy_error != 0.0:
            intent_effect = _INTENT_ENERGY_MAP.get(intent, 0.0)
            if intent_effect != 0.0:
                # Si energy bas (error < 0), favoriser les routines avec effect > 0
                bonus += -energy_error * intent_effect

        # Stress : favorise routines calmes si stress haut
        stress_error = self.error_signals.get("stress", 0.0)
        if stress_error != 0.0:
            intent_effect = _INTENT_STRESS_MAP.get(intent, 0.0)
            if intent_effect != 0.0:
                bonus += stress_error * intent_effect

        # Dopamine : favorise creatif si dopamine bas
        dopa_error = self.error_signals.get("dopamine", 0.0)
        if dopa_error != 0.0:
            intent_effect = _INTENT_DOPAMINE_MAP.get(intent, 0.0)
            if intent_effect != 0.0:
                bonus += -dopa_error * intent_effect

        # Sleep pressure : favorise routines legeres si pression haute
        sleep_error = self.error_signals.get("sleep_pressure", 0.0)
        if sleep_error > 0.3:
            # Haute pression de sommeil → penalise les routines lourdes
            heavy = {"COUNCIL_DEBATE", "EXPANSION_CODE", "EVOLUTION_PIPELINE"}
            light = {"MEMORY_CLEANUP", "AUDIT_STRUCTURE", "JOURNAL_REFLEXION"}
            if intent in heavy:
                bonus -= sleep_error * 0.3
            elif intent in light:
                bonus += sleep_error * 0.2

        return max(-1.0, min(1.5, round(bonus, 3)))

    # --- Contexte pour purpose_context ---

    def get_homeostasis_context(self) -> str:
        """Resume des desequilibres pour injection dans purpose_context."""
        if not self.error_signals:
            return ""

        desequilibres = []
        for var, error in self.error_signals.items():
            if abs(error) > 0.3:
                direction = "trop haut" if error > 0 else "trop bas"
                desequilibres.append(f"{var} {direction} ({error:+.2f})")

        if not desequilibres:
            return "[HOMEOSTASIE] Equilibre interne stable."

        return f"[HOMEOSTASIE] Desequilibres: {', '.join(desequilibres)}"

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour snapshot et endpoint API."""
        return {
            "current_values": dict(self.current_values),
            "error_signals": dict(self.error_signals),
            "stability_score": round(self._compute_stability_score(), 3),
            "total_corrections": self.total_corrections,
            "alarms_triggered": self.alarms_triggered,
            "cycle_count": self._cycle_count,
            "corrections_recent": len(self.corrections_history),
        }

    # --- Persistence ---

    def _save(self):
        """Sauvegarde atomique de l'etat."""
        try:
            data = {
                "current_values": dict(self.current_values),
                "total_corrections": self.total_corrections,
                "alarms_triggered": self.alarms_triggered,
                "cycle_count": self._cycle_count,
                "corrections_history": list(self.corrections_history),
                "saved_at": time.time(),
            }
            tmp = HYPOTHALAMUS_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(HYPOTHALAMUS_STATE_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, HYPOTHALAMUS_STATE_FILE)
        except Exception as e:
            logger.warning(f"[HYPOTHALAMUS] Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            if os.path.exists(HYPOTHALAMUS_STATE_FILE):
                with open(HYPOTHALAMUS_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.current_values = data.get("current_values", self.current_values)
                self.total_corrections = data.get("total_corrections", 0)
                self.alarms_triggered = data.get("alarms_triggered", 0)
                self._cycle_count = data.get("cycle_count", 0)
                hist = data.get("corrections_history", [])
                self.corrections_history = deque(hist[-HOMEOSTASIS_HISTORY_SIZE:], maxlen=HOMEOSTASIS_HISTORY_SIZE)
                logger.info("[HYPOTHALAMUS] Etat restaure.")
        except Exception as e:
            logger.warning(f"[HYPOTHALAMUS] Chargement echoue: {e}")


# --- Singleton ---
hypothalamus = Hypothalamus()
