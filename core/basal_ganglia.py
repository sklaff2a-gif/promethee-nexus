# core/basal_ganglia.py — Ganglions de la Base : Selection d'Actions
# Maintient des "habitudes" (associations intent→reward) et un systeme
# GO/NO-GO pour inhiber ou favoriser des actions.
# Apprentissage par renforcement simplifie (Q-learning). 0 LLM.

import json
import os
import time
import logging
from collections import deque
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("BasalGanglia")

# --- Fichier de persistance ---

BASAL_GANGLIA_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "basal_ganglia_state.json"
)

# --- Constantes ---

LEARNING_RATE = 0.05
DISCOUNT_FACTOR = 0.9
HABIT_STRENGTH_DECAY = 0.995
HABIT_THRESHOLD = 0.3
GO_NOGO_BALANCE = 0.0
INHIBITION_STRENGTH = 2.0
MAX_HABITS = 50
NOVELTY_BONUS = 0.5
MIN_HABIT_STRENGTH = 0.01


class BasalGanglia:
    """Selection d'actions par apprentissage par renforcement."""

    _instance: Optional["BasalGanglia"] = None

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

        # Habitudes : intent → {strength, successes, failures, last_reward, avg_reward}
        self.habits: Dict[str, Dict[str, Any]] = {}

        # Etat GO/NO-GO par intent (>0 = GO, <0 = NO-GO)
        self.go_nogo_state: Dict[str, float] = {}

        # Historique d'actions
        self.action_history: deque = deque(maxlen=50)

        # Compteurs
        self.total_selections: int = 0
        self.total_inhibitions: int = 0

        # Dernier intent execute (pour renforcement dopaminique)
        self._last_intent: str = ""

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour les tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite appelee depuis main.py."""
        self._subscribe_events()
        logger.info("[BASAL_GANGLIA] Module initialise.")

    # --- Souscriptions bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("ROUTINE_FAILED", self._on_routine_failed)
            bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_surge)
            bus.subscribe("CINGULATE_CONFLICT", self._on_cingulate_conflict)
            bus.subscribe("THALAMUS_ATTENTION_SHIFT", self._on_thalamus_shift)
        except Exception as e:
            logger.warning(f"[BASAL_GANGLIA] Souscription echouee: {e}")

    # --- Handlers bus ---

    async def _on_routine_complete(self, event: dict):
        """Routine terminee → mettre a jour l'habitude."""
        intent = event.get("intent", "")
        status = event.get("status", "success")
        quality = event.get("quality_score", 0.5)

        if status in ("success", "completed"):
            reward = min(1.0, quality)
        elif status == "skipped":
            return
        else:
            reward = -0.5

        self._update_habit(intent, reward)
        self._last_intent = intent
        self.total_selections += 1
        self.action_history.append({
            "intent": intent,
            "reward": reward,
            "timestamp": time.time(),
        })

        # Verifier si habitude formee
        habit = self.habits.get(intent)
        if habit and habit["strength"] > 0.7:
            try:
                await bus.publish("BASAL_GANGLIA_HABIT_FORMED", {
                    "intent": intent,
                    "strength": round(habit["strength"], 3),
                })
            except Exception:
                pass

        # Save periodique
        if self.total_selections % 10 == 0:
            self._save()

    async def _on_routine_failed(self, event: dict):
        """Echec de routine → renforcement negatif."""
        intent = event.get("intent", "")
        self._update_habit(intent, -0.5)

    async def _on_dopamine_surge(self, event: dict):
        """Surge dopaminique → renforcer la derniere action."""
        if self._last_intent:
            multiplier = event.get("rpe", 0.5)
            self._reinforce_last_action(multiplier)

    async def _on_cingulate_conflict(self, event: dict):
        """Conflit detecte → inhiber l'intent en conflit (NO-GO)."""
        intent = event.get("intent", "")
        if intent:
            self._inhibit(intent, "conflict")

    async def _on_thalamus_shift(self, event: dict):
        """Shift attentionnel → ajuster GO/NO-GO."""
        category = event.get("new_focus", "")
        boosted = event.get("boosted_intents", [])
        for intent in boosted:
            current = self.go_nogo_state.get(intent, GO_NOGO_BALANCE)
            self.go_nogo_state[intent] = min(2.0, current + 0.2)

    # --- Apprentissage ---

    def _update_habit(self, intent: str, reward: float):
        """Q-learning simplifie : strength += lr * (reward - strength)."""
        if intent not in self.habits:
            self.habits[intent] = {
                "strength": 0.0,
                "successes": 0,
                "failures": 0,
                "last_reward": 0.0,
                "avg_reward": 0.0,
                "created_at": time.time(),
            }

        habit = self.habits[intent]
        # TD update
        error = reward - habit["strength"]
        habit["strength"] += LEARNING_RATE * error
        habit["strength"] = max(0.0, min(1.0, habit["strength"]))

        # Compteurs
        if reward > 0:
            habit["successes"] += 1
        else:
            habit["failures"] += 1

        habit["last_reward"] = reward
        total = habit["successes"] + habit["failures"]
        if total > 0:
            habit["avg_reward"] = round(
                (habit["avg_reward"] * (total - 1) + reward) / total, 4
            )

        # Limiter le nombre d'habitudes
        self._prune_habits()

    def _reinforce_last_action(self, multiplier: float):
        """Renforce la derniere action suite a un surge dopaminique."""
        if self._last_intent and self._last_intent in self.habits:
            habit = self.habits[self._last_intent]
            boost = LEARNING_RATE * abs(multiplier)
            habit["strength"] = min(1.0, habit["strength"] + boost)

    def _inhibit(self, intent: str, reason: str):
        """Active le NO-GO pour un intent."""
        current = self.go_nogo_state.get(intent, GO_NOGO_BALANCE)
        self.go_nogo_state[intent] = max(-INHIBITION_STRENGTH, current - 0.5)
        self.total_inhibitions += 1

    def _decay_habits(self):
        """Decay naturel de toutes les habitudes."""
        to_remove = []
        for intent, habit in self.habits.items():
            habit["strength"] *= HABIT_STRENGTH_DECAY
            if habit["strength"] < MIN_HABIT_STRENGTH:
                to_remove.append(intent)
        for intent in to_remove:
            del self.habits[intent]

    def _prune_habits(self):
        """Supprime les habitudes les plus faibles si on depasse MAX_HABITS."""
        if len(self.habits) <= MAX_HABITS:
            return
        sorted_habits = sorted(
            self.habits.items(),
            key=lambda kv: kv[1]["strength"],
        )
        while len(self.habits) > MAX_HABITS:
            weakest_intent, _ = sorted_habits.pop(0)
            del self.habits[weakest_intent]

    # --- GO/NO-GO ---

    def _compute_go_nogo(self, intent: str) -> float:
        """Equilibre GO/NO-GO pour un intent."""
        habit = self.habits.get(intent)
        if not habit:
            return GO_NOGO_BALANCE

        # GO : strength * success_rate
        total = habit["successes"] + habit["failures"] + 1
        success_rate = habit["successes"] / total
        go_signal = habit["strength"] * success_rate

        # NO-GO : inhibitions + echecs consecutifs
        nogo_signal = -self.go_nogo_state.get(intent, 0.0)
        if self.go_nogo_state.get(intent, 0.0) < 0:
            nogo_signal = abs(self.go_nogo_state[intent])
        else:
            nogo_signal = 0.0

        return go_signal - nogo_signal

    # --- Scoring (Couche 20) ---

    def compute_habit_bonus(self, intent: str) -> float:
        """Bonus/malus d'habitude pour un intent. Range [-1.5, +2.0]."""
        habit = self.habits.get(intent)

        # Intent inconnu → bonus de nouveaute
        if habit is None:
            return NOVELTY_BONUS

        # Habitude trop faible → pas d'influence
        if habit["strength"] < HABIT_THRESHOLD:
            return 0.0

        go_nogo = self._compute_go_nogo(intent)

        if go_nogo > 0:
            # GO dominant → bonus
            bonus = habit["strength"] * go_nogo * 2.0
            return min(2.0, round(bonus, 3))
        elif go_nogo < 0:
            # NO-GO dominant → malus
            malus = abs(go_nogo) * habit["strength"]
            return max(-1.5, round(-malus, 3))

        return 0.0

    # --- Contexte ---

    def get_habit_context(self) -> str:
        """Top 3 habitudes + inhibitions pour purpose_context."""
        if not self.habits:
            return ""

        # Top 3 par strength
        sorted_h = sorted(
            self.habits.items(),
            key=lambda kv: kv[1]["strength"],
            reverse=True,
        )[:3]

        parts = [
            f"{intent}({h['strength']:.2f})"
            for intent, h in sorted_h
        ]

        # Inhibitions actives
        inhibited = [
            intent for intent, val in self.go_nogo_state.items()
            if val < -0.3
        ]

        ctx = f"[HABITUDES] Top: {', '.join(parts)}"
        if inhibited:
            ctx += f" | Inhibe: {', '.join(inhibited[:3])}"

        return ctx

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour snapshot et endpoint API."""
        # Top 10 habitudes
        sorted_h = sorted(
            self.habits.items(),
            key=lambda kv: kv[1]["strength"],
            reverse=True,
        )[:10]

        known_intents = len(self.habits)
        novel_count = 0
        # Approximation du ratio de nouveaute
        if self.action_history:
            recent = list(self.action_history)[-10:]
            novel_count = sum(1 for a in recent if a["intent"] not in self.habits)

        return {
            "habits_top10": {k: v for k, v in sorted_h},
            "habits_count": known_intents,
            "go_nogo_state": dict(self.go_nogo_state),
            "total_selections": self.total_selections,
            "total_inhibitions": self.total_inhibitions,
            "novelty_ratio": round(novel_count / max(1, len(list(self.action_history)[-10:])), 2),
        }

    # --- Persistence ---

    def _save(self):
        """Sauvegarde atomique."""
        try:
            data = {
                "habits": dict(self.habits),
                "go_nogo_state": dict(self.go_nogo_state),
                "total_selections": self.total_selections,
                "total_inhibitions": self.total_inhibitions,
                "saved_at": time.time(),
            }
            tmp = BASAL_GANGLIA_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(BASAL_GANGLIA_STATE_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, BASAL_GANGLIA_STATE_FILE)
        except Exception as e:
            logger.warning(f"[BASAL_GANGLIA] Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat."""
        try:
            if os.path.exists(BASAL_GANGLIA_STATE_FILE):
                with open(BASAL_GANGLIA_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.habits = data.get("habits", {})
                self.go_nogo_state = data.get("go_nogo_state", {})
                self.total_selections = data.get("total_selections", 0)
                self.total_inhibitions = data.get("total_inhibitions", 0)
                logger.info("[BASAL_GANGLIA] Etat restaure.")
        except Exception as e:
            logger.warning(f"[BASAL_GANGLIA] Chargement echoue: {e}")


# --- Singleton ---
ganglia = BasalGanglia()
