# core/basal_ganglia.py — Ganglions de la Base : Selection d'Actions
# Maintient des "habitudes" (associations intent→reward) et un systeme
# GO/NO-GO pour inhiber ou favoriser des actions.
# Apprentissage par renforcement simplifie (Q-learning). 0 LLM.

import json
import os
import time
import logging
from collections import deque
from typing import Dict, Any, Optional

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

# V10.1 (Phase 12B - 2026-04-21) : cycle metabolique unifie (Metabolic Wash)
# Audit runtime : 59071 inhibitions pour 1910 selections (31x), plusieurs
# intents a NO-GO=-2.0 permanent (COUNCIL_DEBATE inclus) par absence de
# _decay_habits + absence de decay NO-GO. Catatonie cognitive progressive.
NO_GO_DECAY_RATE = 0.05                # Remontee |NO-GO| vers 0 par wash
METABOLIC_WASH_INTERVAL = 100          # 1 wash tous les 100 cardiac_beats
                                        # ~= 50 min de runtime reel

# V12.0 (Phase 13 - 2026-04-22) : MDP + Replay Hippocampique
# Q-learning sequentiel sur etat enrichi (drive, prev_intent, curr_intent).
# Sources en interne : prev = self._last_intent, drive = desires.dominant.
# Plancher -1.0 contre la phobie algorithmique (COUNCIL_DEBATE et autres
# routines de resolution de crise restent joignables malgre des echecs).
GAMMA_SEQ = 0.95                       # Discount factor rewards differes
LR_SEQ = 0.05                          # Learning rate Q-sequential
FAILURE_FLOOR = -1.0                   # Plancher rewards negatives
SEQ_HABIT_DECAY = 0.998                # Decay par Metabolic Wash
MAX_SEQ_HABITS = 5000                  # Cap combinatoire (prune au-dela)
SEQ_HABIT_THRESHOLD = 0.05             # Seuil sous lequel bonus=0
SEQ_BONUS_SCALE = 1.0                  # Facteur d'amplification
SEQ_KEY_SEP = "|"                      # Separateur clef stringifiee


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

        # V12.0 Phase 13 : habits sequentielles (MDP).
        # cle = "drive|prev_intent|curr_intent"
        # valeur = {q_value, visits, last_reward, updated_at}
        self.sequential_habits: Dict[str, Dict[str, Any]] = {}

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
            # V10.1 : declencheur du Metabolic Wash periodique
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
        except Exception as e:
            logger.warning(f"[BASAL_GANGLIA] Souscription echouee: {e}")

    # --- V10.1 : Metabolic Wash ---

    async def _on_cardiac_beat(self, data: dict):
        """V10.1 : hook cardiaque pour declencher le wash periodique
        tous les METABOLIC_WASH_INTERVAL beats (~50 min)."""
        self._cardiac_ticks = getattr(self, '_cardiac_ticks', 0) + 1
        if self._cardiac_ticks % METABOLIC_WASH_INTERVAL == 0:
            self.tick_metabolic_wash()
            logger.info(
                f"[BASAL_GANGLIA] V10.1 metabolic wash tick #{self._cardiac_ticks} : "
                f"{len(self.habits)} habits, {len(self.go_nogo_state)} go_nogo actifs"
            )

    def tick_metabolic_wash(self):
        """V10.1 : 'lavage chimique' periodique unifie.

        Restaure la symetrie apprentissage/oubli qui manquait pre-V10.1.
        Audit runtime 21/04 avait revele 59071 inhibitions accumulees
        et habits plafonnant a 1.0 sans extinction naturelle possible.

        Cycle bidirectionnel :
        - Decay des habits (appel de _decay_habits, fonction orpheline pre-V10.1)
        - Decay bidirectionnel du NO-GO / GO vers 0 (NO_GO_DECAY_RATE=0.05)
        - Nettoyage des entrees a zero (evite accumulation memoire)
        """
        # 1) Decay des habits (fonction orpheline reactivee)
        self._decay_habits()

        # 2) Decay bidirectionnel NO-GO / GO vers 0
        to_remove = []
        for intent, val in list(self.go_nogo_state.items()):
            if val < 0:
                new_val = min(0.0, val + NO_GO_DECAY_RATE)
            elif val > 0:
                new_val = max(0.0, val - NO_GO_DECAY_RATE)
            else:
                to_remove.append(intent)
                continue

            if abs(new_val) < 0.01:
                to_remove.append(intent)
            else:
                self.go_nogo_state[intent] = new_val

        for intent in to_remove:
            self.go_nogo_state.pop(intent, None)

        # 3) V12.0 : decay des sequential_habits (MDP)
        seq_to_remove = []
        for key, entry in self.sequential_habits.items():
            entry["q_value"] *= SEQ_HABIT_DECAY
            if abs(entry["q_value"]) < 0.005:
                seq_to_remove.append(key)
        for key in seq_to_remove:
            self.sequential_habits.pop(key, None)

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

    # --- V12.0 Phase 13 : MDP + Replay ---

    def _seq_key(self, drive: str, prev_intent: str, curr_intent: str) -> str:
        """Construit une cle stable pour sequential_habits."""
        d = drive or "none"
        p = prev_intent or "none"
        c = curr_intent or "none"
        return f"{d}{SEQ_KEY_SEP}{p}{SEQ_KEY_SEP}{c}"

    def _get_dominant_drive(self) -> str:
        """Lecture best-effort de la pulsion dominante."""
        try:
            from core.desire_engine import desires
            top = sorted(
                desires.drives.values(),
                key=lambda d: getattr(d, "deprivation", 0.0),
                reverse=True,
            )
            if top:
                return getattr(top[0], "name", "") or "none"
        except Exception:
            pass
        return "none"

    def update_sequential(self, trajectory: list) -> int:
        """V12.0 : Q-learning sequentiel avec retropropagation gamma=0.95.

        trajectory = liste d'Episodes (hippocampus), du plus ancien au plus
        recent. Pour chaque transition (ep[i-1] -> ep[i]) :

            state     = (drive de ep[i], intent de ep[i-1], intent de ep[i])
            action    = intent de ep[i]      (inclus dans la cle)
            reward    = quality_score de ep[i] (clippe FAILURE_FLOOR)
            next_max  = max_a Q(drive de ep[i+1], intent de ep[i], a)
            Q[s,a]   += LR_SEQ * (reward + GAMMA_SEQ * next_max - Q[s,a])

        On evalue l'action qu'on vient d'observer (ep_curr) via son propre
        reward. Les failures sont tirees sous le plancher FAILURE_FLOOR
        (-1.0) pour eviter la phobie algorithmique definitive sur les
        routines de resolution de crise (COUNCIL_DEBATE, etc.).

        Returns: nombre de transitions effectivement Q-updatees.
        """
        if not trajectory or len(trajectory) < 2:
            return 0

        updates = 0
        n = len(trajectory)
        for i in range(1, n):
            ep_prev = trajectory[i - 1]
            ep_curr = trajectory[i]

            prev_intent = getattr(ep_prev, "intent", "") or "none"
            curr_intent = getattr(ep_curr, "intent", "") or ""
            if not curr_intent:
                continue

            drive_curr = getattr(ep_curr, "dominant_drive", "") or "none"
            key = self._seq_key(drive_curr, prev_intent, curr_intent)

            # Reward : quality_score de ep_curr (l'action observee).
            raw_reward = float(getattr(ep_curr, "quality_score", 0.0) or 0.0)
            if getattr(ep_curr, "event_type", "") == "routine_failure":
                raw_reward = min(raw_reward - 0.5, -0.3)
            reward = max(FAILURE_FLOOR, raw_reward)

            # next_max : meilleure action a partir de l'etat suivant
            # (drive de ep[i+1], intent de ep_curr, *). Si fin de trajectoire,
            # next_max = 0 (absence d'information sur l'avenir).
            next_max = 0.0
            if i + 1 < n:
                ep_next = trajectory[i + 1]
                drive_next = getattr(ep_next, "dominant_drive", "") or "none"
                prefix = f"{drive_next}{SEQ_KEY_SEP}{curr_intent}{SEQ_KEY_SEP}"
                next_qs = [
                    entry["q_value"]
                    for k, entry in self.sequential_habits.items()
                    if k.startswith(prefix)
                ]
                if next_qs:
                    next_max = max(next_qs)

            entry = self.sequential_habits.setdefault(key, {
                "q_value": 0.0,
                "visits": 0,
                "last_reward": 0.0,
                "updated_at": time.time(),
            })
            td_error = reward + GAMMA_SEQ * next_max - entry["q_value"]
            entry["q_value"] += LR_SEQ * td_error
            entry["q_value"] = max(-2.0, min(2.0, entry["q_value"]))
            entry["visits"] += 1
            entry["last_reward"] = reward
            entry["updated_at"] = time.time()
            updates += 1

        self._prune_sequential_habits()

        # V12.1a (2026-04-23) : filet de securite anti-crash post-dream.
        # L'autosave /10 routines success suffit en regime normal, mais ce
        # save immediat garantit la persistance des Q-updates si un crash
        # intervient avant la prochaine routine success (cas kernel panic
        # en pleine sieste). Seulement si des changements ont eu lieu.
        if updates > 0:
            self._save()

        return updates

    def _prune_sequential_habits(self) -> None:
        """Prune les entrees les moins significatives si MAX_SEQ_HABITS depasse."""
        if len(self.sequential_habits) <= MAX_SEQ_HABITS:
            return
        sorted_items = sorted(
            self.sequential_habits.items(),
            key=lambda kv: abs(kv[1]["q_value"]) * max(1, kv[1]["visits"]),
        )
        excess = len(self.sequential_habits) - MAX_SEQ_HABITS
        for key, _ in sorted_items[:excess]:
            del self.sequential_habits[key]

    def _compute_sequential_bonus(self, intent: str) -> float:
        """V12.0 : bonus issu du Q(drive, prev, intent).

        Retourne 0.0 si : pas d'habit sequentiel, pas de prev intent,
        etat jamais visite, ou Q sous le seuil SEQ_HABIT_THRESHOLD.
        """
        if not self.sequential_habits or not self._last_intent:
            return 0.0
        drive = self._get_dominant_drive()
        key = self._seq_key(drive, self._last_intent, intent)
        entry = self.sequential_habits.get(key)
        if not entry:
            return 0.0
        q = entry["q_value"]
        if abs(q) < SEQ_HABIT_THRESHOLD:
            return 0.0
        return round(q * SEQ_BONUS_SCALE, 3)

    # --- GO/NO-GO ---

    def _compute_go_nogo(self, intent: str) -> float:
        """V10.1 : equilibre GO/NO-GO avec resilience proportionnelle.

        Bug #3 pre-V10.1 : la formule etait `go_signal - |no_go_state|`,
        erreur dimensionnelle qui ecrasait les routines parfaites sous le
        NO-GO absolu (routine 100% success, strength=1.0, NO-GO=-2.0 →
        -1.0 → malus permanent sur une routine ideale).

        V10.1 (validation Gemini) : formule additive a resilience
        proportionnelle a la qualite de l'habit :
            go_nogo = go_signal + gng_state × (1.0 - go_signal × 0.5)

        Consequences :
        - Routine parfaite (go=1.0) + NO-GO=-2.0 → 1.0 + (-2.0)×0.5 = 0.0
          (preserve, retombe a neutre mais pas en malus)
        - Routine faible   (go=0.2) + NO-GO=-2.0 → 0.2 + (-2.0)×0.9 = -1.6
          (punie fortement, le signal de securite est preserve)
        - Routine moyenne  (go=0.5) + NO-GO=-1.0 → 0.5 + (-1.0)×0.75 = -0.25
          (punition modulee)

        Le NO-GO punit surtout les routines faibles (correct : les habits
        solides sont legitimement resistantes a un conflit ponctuel).
        Le GO favorise surtout les routines naissantes (exploration).
        """
        habit = self.habits.get(intent)
        if not habit:
            return GO_NOGO_BALANCE

        # GO signal : strength × success_rate
        total = habit["successes"] + habit["failures"] + 1
        success_rate = habit["successes"] / total
        go_signal = habit["strength"] * success_rate  # ∈ [0, 1]

        gng_state = self.go_nogo_state.get(intent, 0.0)
        if gng_state == 0.0:
            return go_signal

        # V10.1 : resilience = 1 - go_signal × 0.5  ∈ [0.5, 1.0]
        # Une habit parfaite (go_signal=1.0) donne resistance=0.5
        # Une habit nulle (go_signal=0.0) donne resistance=1.0 (pleine punition)
        resilience = 1.0 - (go_signal * 0.5)
        return go_signal + gng_state * resilience

    # --- Scoring (Couche 20) ---

    def compute_habit_bonus(self, intent: str) -> float:
        """Bonus/malus d'habitude pour un intent. Range [-1.5, +2.0].

        V12.0 : enrichi avec un terme sequentiel base sur (drive, prev, intent).
        Signature publique (intent,) preservee : l'enrichissement est lu en
        interne (self._last_intent + singleton desires), donc l'appel via
        introspection dynamique dans autonomy_engine.py reste intact.

        NOVELTY_BONUS reste prioritaire pour les intents inconnus (les 4800
        tests existants sur cette branche sont invariants).
        """
        habit = self.habits.get(intent)

        # Intent inconnu → bonus de nouveaute (contrat existant inchange)
        if habit is None:
            return NOVELTY_BONUS

        # Habitude trop faible → terme unitaire nul, mais on peut encore
        # ajouter un signal sequentiel s'il existe.
        if habit["strength"] < HABIT_THRESHOLD:
            seq_only = self._compute_sequential_bonus(intent)
            if seq_only != 0.0:
                return max(-1.5, min(2.0, round(seq_only, 3)))
            return 0.0

        go_nogo = self._compute_go_nogo(intent)

        if go_nogo > 0:
            unitary = min(2.0, round(habit["strength"] * go_nogo * 2.0, 3))
        elif go_nogo < 0:
            unitary = max(-1.5, round(-abs(go_nogo) * habit["strength"], 3))
        else:
            unitary = 0.0

        # V12.0 : somme unitaire + sequentiel, re-clampee
        seq = self._compute_sequential_bonus(intent)
        if seq == 0.0:
            return unitary

        total = unitary + seq
        return max(-1.5, min(2.0, round(total, 3)))

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
                "sequential_habits": dict(self.sequential_habits),
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
                self.sequential_habits = data.get("sequential_habits", {})
                self.total_selections = data.get("total_selections", 0)
                self.total_inhibitions = data.get("total_inhibitions", 0)
                logger.info("[BASAL_GANGLIA] Etat restaure.")
        except Exception as e:
            logger.warning(f"[BASAL_GANGLIA] Chargement echoue: {e}")


# --- Singleton ---
ganglia = BasalGanglia()
