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
ALARM_COOLDOWN_SECONDS = 120   # Re-alarme même variable après 2 min minimum
ALARM_SEVERITY_DECAY = 0.15    # Réduction severity à chaque ré-alarme successive
ALARM_SEVERITY_FLOOR = 0.3     # Severity minimum (ne descend pas en dessous)
ALARM_SUSTAINED_CYCLES = 3    # Nombre de cycles consécutifs avant de déclencher une alarme

# V14.2 — Pilier 1 nocicepteurs : couplage stagnation synaptique → sleep_pressure.
# Quand z-score(dream_dette_h) >= seuil, on incrémente sleep_pressure pour que
# la douleur cognitive ait un coût métabolique réel dès le premier jour, sans
# attendre le Pilier 2 (panique reptilienne) ni le Pilier 3 (préemption).
# Bump doux et plafonné pour éviter une bascule brutale en alarme reptilienne :
# ~5 cycles cardiaques pour passer de 0.4 (baseline éveil) à 0.6 (plancher
# d'alarme), soit 25-50s en pratique.
SYNAPTIC_DEBT_Z_THRESHOLD = 1.5
SYNAPTIC_DEBT_BUMP_MAX = 0.05      # Incrément max par cycle de regulate()
SYNAPTIC_DEBT_PRESSURE_CEILING = 0.95  # Ne pousse pas jusqu'à 1.0 (laisse marge)

# V14.5 — Descente symétrique : quand la dette est résolue (post MEMORY_CONSOLIDATION),
# on relâche la pression que NOTRE patch a injectée, pour ne pas laisser une alarme
# hypothalamique générique bloquée au plafond. Hystérésis 0.5 entre seuil de montée
# (1.5) et seuil de descente (1.0) pour éviter le yo-yo si z oscille autour du seuil.
# La descente ne franchit JAMAIS le plancher circadien (sleep_pressure d'éveil = 0.2,
# crépuscule = 0.6, sommeil_profond = 0.9, aube = 0.4) — sinon on écraserait la
# pression légitime que la phase circadienne maintient.
SYNAPTIC_DEBT_RELIEF_THRESHOLD = 1.0    # z sous ce seuil → décompression active

# Plancher de pression dynamique selon la phase circadienne. Source unique de vérité,
# réutilisée par _on_circadian_phase ET _apply_synaptic_debt_pressure.
_CIRCADIAN_PRESSURE_FLOOR = {
    "eveil": 0.2,
    "crepuscule": 0.6,
    "sommeil_profond": 0.9,
    "aube": 0.4,
}
_DEFAULT_CIRCADIAN_FLOOR = 0.4

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
        self._alarm_last_fired: Dict[str, float] = {}   # variable → timestamp dernière alarme
        self._alarm_repeat_count: Dict[str, int] = {}    # variable → nb répétitions consécutives
        self._alarm_sustained_count: Dict[str, int] = {}  # cycles consécutifs au-dessus du seuil

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
            bus.subscribe("DOPAMINE_STATE", self._on_dopamine_update)
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
        """Met a jour la pression de sommeil depuis le cycle circadien.

        V14.5 : utilise _CIRCADIAN_PRESSURE_FLOOR (constante module) comme
        source unique de verite, partagee avec _apply_synaptic_debt_pressure.
        """
        phase = event.get("phase", "eveil")
        self.current_values["sleep_pressure"] = _CIRCADIAN_PRESSURE_FLOOR.get(
            phase, _DEFAULT_CIRCADIAN_FLOOR
        )

    async def _on_routine_complete(self, event: dict):
        """Met a jour l'energie basee sur le budget restant."""
        # Lire le budget depuis l'autonomy_engine (import local)
        try:
            from core.autonomy_engine import autonomy
            budget_used = autonomy.daily_budget_used
            budget_max = 200  # DAILY_BUDGET_POINTS
        except Exception:
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

    # --- V14.2 — Pilier 1 nocicepteurs : pression cognitive ---

    def _apply_synaptic_debt_pressure(self) -> Optional[Dict[str, float]]:
        """Fait monter OU descendre sleep_pressure selon la dette de rêve.

        Architecture homéostatique symétrique (V14.5) :
          - z >= 1.5 (montée)    : sleep_pressure += bump (plafond 0.95)
          - z <  1.0 (descente)  : sleep_pressure -= relief (plancher
                                    circadien : eveil=0.2, sommeil=0.9, etc.)
          - z dans [1.0, 1.5]    : zone neutre (hystérésis anti-yo-yo)

        Sans la branche descente, sleep_pressure restait collée au plafond
        après résolution de la dette par MEMORY_CONSOLIDATION, créant une
        alarme hypothalamique chronique. Bug détecté en raisonnement
        avant 1er cycle de vol nocturne (audit V14.4).

        Le plancher circadien protège la pression légitime de sommeil
        profond (0.9) ou de crépuscule (0.6). On ne casse pas le rythme
        biologique sous prétexte que la dette synaptique est résolue.

        Retourne dict d'observabilité avec "action" ∈ {rise, relief}, ou
        None si zone neutre / no-op. Defensive : capture toutes les
        exceptions pour ne jamais casser regulate().
        """
        try:
            from core.synaptic_network import cortex
            from core import baseline_tracker as _bt_mod
            last = float(getattr(cortex, "_last_dream_time", 0.0) or 0.0)
            if last <= 0:
                return None
            now = time.time()
            dette_h = (now - last) / 3600.0
            # Apprentissage continu : update du baseline avant lecture
            bt = _bt_mod.baseline_tracker
            bt.update("dream_dette_h", dette_h, now)
            zscore = bt.get_zscore("dream_dette_h", dette_h, now)
            current = self.current_values.get("sleep_pressure", 0.4)

            # ── Branche MONTÉE : z >= seuil haut ──
            if zscore >= SYNAPTIC_DEBT_Z_THRESHOLD:
                excess = zscore - SYNAPTIC_DEBT_Z_THRESHOLD + 1.0
                bump = min(SYNAPTIC_DEBT_BUMP_MAX * excess, SYNAPTIC_DEBT_BUMP_MAX * 4.0)
                new_pressure = min(SYNAPTIC_DEBT_PRESSURE_CEILING, current + bump)
                self.current_values["sleep_pressure"] = new_pressure
                return {
                    "action": "rise",
                    "dream_dette_h": round(dette_h, 2),
                    "zscore": round(zscore, 3),
                    "bump": round(bump, 4),
                    "pressure_after": round(new_pressure, 3),
                }

            # ── Branche DESCENTE : z sous seuil bas (hystérésis) ──
            if zscore < SYNAPTIC_DEBT_RELIEF_THRESHOLD:
                # Plancher dynamique selon phase circadienne — protège la
                # pression légitime de sommeil_profond / crépuscule.
                try:
                    from core.circadian_rhythm import circadian
                    floor = _CIRCADIAN_PRESSURE_FLOOR.get(
                        circadian.phase, _DEFAULT_CIRCADIAN_FLOOR
                    )
                except Exception:
                    floor = _DEFAULT_CIRCADIAN_FLOOR
                # Si la pression actuelle est déjà au plancher (ou en dessous,
                # cas où la phase circadienne a pris le relais), no-op.
                if current <= floor:
                    return None
                # Relief symétrique au bump nominal — décompression contrôlée
                relief = SYNAPTIC_DEBT_BUMP_MAX
                new_pressure = max(floor, current - relief)
                self.current_values["sleep_pressure"] = new_pressure
                return {
                    "action": "relief",
                    "dream_dette_h": round(dette_h, 2),
                    "zscore": round(zscore, 3),
                    "relief": round(relief, 4),
                    "circadian_floor": round(floor, 3),
                    "pressure_after": round(new_pressure, 3),
                }

            # ── Zone neutre [1.0, 1.5] : hystérésis, no-op ──
            return None
        except Exception as e:
            logger.debug(f"[HYPOTHALAMUS] Pression synaptique: {e}")
            return None

    # --- Cycle de regulation ---

    async def regulate(self):
        """Cycle principal : compute errors → corrections → publish."""
        self._cycle_count += 1

        # V14.2 — Pilier 1 nocicepteurs : appliquer la pression cognitive
        # AVANT de calculer les erreurs, pour que la nouvelle valeur de
        # sleep_pressure soit prise en compte dans le cycle courant.
        debt_report = self._apply_synaptic_debt_pressure()

        self.error_signals = self._compute_all_errors()

        corrections = []
        alarms = []
        for var, error in self.error_signals.items():
            correction = self._generate_correction(var, error)
            if correction["action"] != "none":
                corrections.append(correction)
                self.total_corrections += 1

            # Alarme si deviation soutenue (N cycles consécutifs au-dessus du seuil)
            if abs(error) > ALARM_THRESHOLD:
                sustained = self._alarm_sustained_count.get(var, 0) + 1
                self._alarm_sustained_count[var] = sustained

                if sustained >= ALARM_SUSTAINED_CYCLES:
                    now = time.time()
                    last = self._alarm_last_fired.get(var, 0.0)
                    if now - last >= ALARM_COOLDOWN_SECONDS:
                        # Severity dégressive sur répétitions
                        repeats = self._alarm_repeat_count.get(var, 0)
                        raw_severity = abs(error)
                        severity = max(ALARM_SEVERITY_FLOOR, raw_severity - repeats * ALARM_SEVERITY_DECAY)
                        alarm = {"variable": var, "error": round(error, 3), "severity": round(severity, 3)}
                        alarms.append(alarm)
                        self.alarms_triggered += 1
                        self._alarm_last_fired[var] = now
                        self._alarm_repeat_count[var] = repeats + 1
            else:
                # Variable OK → reset compteurs pour cette variable
                self._alarm_repeat_count.pop(var, None)
                self._alarm_sustained_count.pop(var, None)

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

        # V14.2 — Publier la pression cognitive pour observabilité
        if debt_report:
            try:
                await bus.publish("SYNAPTIC_DEBT_PRESSURE", debt_report)
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
                "alarm_last_fired": self._alarm_last_fired,
                "alarm_repeat_count": self._alarm_repeat_count,
                "alarm_sustained_count": self._alarm_sustained_count,
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
                self._alarm_last_fired = {k: float(v) for k, v in data.get("alarm_last_fired", {}).items()}
                self._alarm_repeat_count = {k: int(v) for k, v in data.get("alarm_repeat_count", {}).items()}
                self._alarm_sustained_count = {k: int(v) for k, v in data.get("alarm_sustained_count", {}).items()}
                logger.info("[HYPOTHALAMUS] Etat restaure.")
        except Exception as e:
            logger.warning(f"[HYPOTHALAMUS] Chargement echoue: {e}")


# --- Singleton ---
hypothalamus = Hypothalamus()
