# core/dopamine_system.py — DopamineSystem : Apprentissage par Renforcement
# Inspire de Schultz (1997) — Reward Prediction Error (RPE).
# Le systeme apprend a predire la qualite de chaque routine,
# calcule un RPE (reel - predit), et module la selection via un bonus motivation.
# 0 LLM, pur deterministe.

import json
import os
import time
import math
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List
from collections import deque

from core.event_bus.bus import bus

logger = logging.getLogger("DopamineSystem")

# --- Fichier de persistance ---

DOPAMINE_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "dopamine_state.json"
)

# --- Constantes ---

BASELINE_DOPAMINE = 0.5
MIN_DOPAMINE = 0.05
MAX_DOPAMINE = 1.0
DOPAMINE_DECAY_RATE = 0.97          # Retour vers baseline par tick (CARDIAC_BEAT)

SURGE_FACTOR = 0.15                 # Impact RPE positif sur niveau
DIP_FACTOR = 0.12                   # Impact RPE negatif sur niveau

RPE_SURGE_THRESHOLD = 0.15          # Seuil publication DOPAMINE_SURGE
RPE_DIP_THRESHOLD = -0.15           # Seuil publication DOPAMINE_DIP

INITIAL_LEARNING_RATE = 0.25        # Alpha depart (TD-learning)
MIN_LEARNING_RATE = 0.03            # Alpha plancher
LEARNING_RATE_DECAY = 0.98          # Decay alpha par observation

NOVELTY_BONUS = 1.5                 # Multiplicateur premier intent (jamais vu)
MIN_HABITUATION_FACTOR = 0.3        # Plancher attenuation habituation

REPETITION_THRESHOLD = 5            # Anti-addiction (>5 dans 10 dernieres)
REWARD_HISTORY_MAX = 50             # Taille max historique recompenses par intent

MOTIVATION_BONUS_MIN = -2.0
MOTIVATION_BONUS_MAX = 3.0

STATE_BROADCAST_INTERVAL = 12       # Broadcast toutes les 12 ticks (~6 min)
AUTOSAVE_INTERVAL = 10              # Sauvegarder toutes les 10 observations


# --- Time buckets ---

def _get_time_bucket() -> str:
    """Bucket temporel : nuit/matin/aprem/soir."""
    h = time.localtime().tm_hour
    if h < 6:
        return "nuit"
    elif h < 12:
        return "matin"
    elif h < 18:
        return "aprem"
    else:
        return "soir"


def _get_current_mood() -> str:
    """Recupere l'humeur cardiaque courante."""
    try:
        from core.cardiac_engine import heart
        return heart.current_emotion
    except Exception:
        return "neutre"


# --- Data structure ---

@dataclass
class RewardMemory:
    """Memoire de recompense pour un intent donne."""
    expected_reward: float = 0.5          # V(intent) — valeur predite
    learning_rate: float = INITIAL_LEARNING_RATE  # Alpha individuel
    observation_count: int = 0
    reward_history: List[float] = field(default_factory=list)
    success_by_bucket: Dict[str, List[float]] = field(default_factory=lambda: {
        "nuit": [], "matin": [], "aprem": [], "soir": []
    })
    success_by_mood: Dict[str, List[float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "expected_reward": self.expected_reward,
            "learning_rate": self.learning_rate,
            "observation_count": self.observation_count,
            "reward_history": self.reward_history[-REWARD_HISTORY_MAX:],
            "success_by_bucket": {k: v[-20:] for k, v in self.success_by_bucket.items()},
            "success_by_mood": {k: v[-20:] for k, v in self.success_by_mood.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RewardMemory":
        mem = cls()
        mem.expected_reward = d.get("expected_reward", 0.5)
        mem.learning_rate = d.get("learning_rate", INITIAL_LEARNING_RATE)
        mem.observation_count = d.get("observation_count", 0)
        mem.reward_history = d.get("reward_history", [])
        raw_buckets = d.get("success_by_bucket", {})
        for k in ("nuit", "matin", "aprem", "soir"):
            mem.success_by_bucket[k] = raw_buckets.get(k, [])
        mem.success_by_mood = d.get("success_by_mood", {})
        return mem


class DopamineSystem:
    """Singleton — Systeme de recompense a base de RPE (Schultz 1997)."""

    _instance: Optional["DopamineSystem"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.dopamine_level: float = BASELINE_DOPAMINE
        self.memories: Dict[str, RewardMemory] = {}
        self._tick_count: int = 0
        self._obs_since_save: int = 0
        self._subscribed: bool = False
        self._recent_intents: deque = deque(maxlen=10)

        self._load()
        # Note: _subscribe_events() appelé dans init() depuis main.py, pas dans __init__

    def reset(self):
        """Reset l'etat (pour tests)."""
        self.dopamine_level = BASELINE_DOPAMINE
        self.memories = {}
        self._tick_count = 0
        self._obs_since_save = 0
        self._recent_intents = deque(maxlen=10)

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite (appele depuis main.py)."""
        self._subscribe_events()
        logger.info(f"DOPAMINE: Systeme de recompense initialise (niveau={self.dopamine_level:.2f}, "
                     f"{len(self.memories)} intents connus).")

    # ================================================================
    # PERSISTANCE
    # ================================================================

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            if os.path.exists(DOPAMINE_STATE_FILE):
                with open(DOPAMINE_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.dopamine_level = data.get("dopamine_level", BASELINE_DOPAMINE)
                raw_memories = data.get("memories", {})
                for intent, mem_dict in raw_memories.items():
                    self.memories[intent] = RewardMemory.from_dict(mem_dict)
                logger.info(f"DOPAMINE: Etat restaure ({len(self.memories)} intents).")
        except Exception as e:
            logger.warning(f"DOPAMINE: Echec chargement: {e}")

    def _save(self):
        """Sauvegarde atomique de l'etat."""
        try:
            data = {
                "dopamine_level": self.dopamine_level,
                "memories": {k: v.to_dict() for k, v in self.memories.items()},
                "saved_at": time.time(),
            }
            tmp = DOPAMINE_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, DOPAMINE_STATE_FILE)
        except Exception as e:
            logger.warning(f"DOPAMINE: Echec sauvegarde: {e}")

    # ================================================================
    # SOUSCRIPTION BUS
    # ================================================================

    def _subscribe_events(self):
        """Souscription aux events du bus."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("COUNCIL_END", self._on_council_end)
            bus.subscribe("EUREKA_BRIDGE", self._on_eureka_bridge)
            bus.subscribe("ARTIFACT_CREATED", self._on_artifact_created)
            bus.subscribe("HALLUCINATION_DETECTED", self._on_hallucination)
            bus.subscribe("SANDBOX_TEST_PASS", self._on_sandbox_pass)
            bus.subscribe("SANDBOX_TEST_FAIL", self._on_sandbox_fail)
            bus.subscribe("ROADMAP_MODULE_COMPLETED", self._on_roadmap_completed)
            bus.subscribe("TISSUE_PATTERN_EMERGED", self._on_tissue_pattern)
            bus.subscribe("TISSUE_DIVERSITY_DROP", self._on_tissue_diversity_drop)
            bus.subscribe("BASAL_GANGLIA_HABIT_FORMED", self._on_habit_formed)
        except Exception as e:
            logger.warning(f"DOPAMINE: Echec souscription bus: {e}")

    # ================================================================
    # RPE — COEUR DU SYSTEME
    # ================================================================

    def compute_rpe(self, intent: str, actual_quality: float) -> float:
        """Calcule le Reward Prediction Error pour un intent donne.

        RPE = (actual × novelty) - predicted_contextual
        L'habituation n'affecte PAS le RPE d'apprentissage (sinon expected_reward
        diverge vers 0 pour les routines a variance nulle). Elle est appliquee
        separement sur le changement de dopamine dans _on_routine_complete().
        Clamp [-1, +1].
        """
        memory = self.memories.get(intent)

        if memory is None:
            # Premier contact avec cet intent → novelty
            predicted = BASELINE_DOPAMINE
            novelty = NOVELTY_BONUS
        else:
            predicted = self._get_contextual_prediction(memory)
            novelty = 1.0

        effective_actual = actual_quality * novelty
        rpe = effective_actual - predicted
        return max(-1.0, min(1.0, rpe))

    def _get_contextual_prediction(self, memory: RewardMemory) -> float:
        """Prediction contextuelle : V(intent) ajuste par time_bucket et mood.

        30% time_bucket, 20% mood, 50% valeur de base.
        """
        base = memory.expected_reward

        # Ajustement temporel (30%)
        bucket = _get_time_bucket()
        bucket_vals = memory.success_by_bucket.get(bucket, [])
        if bucket_vals:
            bucket_avg = sum(bucket_vals) / len(bucket_vals)
            base = base * 0.7 + bucket_avg * 0.3

        # Ajustement humeur (20%)
        mood = _get_current_mood()
        mood_vals = memory.success_by_mood.get(mood, [])
        if mood_vals:
            mood_avg = sum(mood_vals) / len(mood_vals)
            base = base * 0.8 + mood_avg * 0.2

        return max(0.0, min(1.0, base))

    def _compute_habituation_factor(self, memory: RewardMemory) -> float:
        """Facteur d'habituation base sur la variance des recompenses.

        Haute variance → pas d'habituation (toujours surprenant).
        Basse variance → habituation (previsible → ennuyeux).
        Borne [MIN_HABITUATION_FACTOR, 1.0].
        """
        history = memory.reward_history
        if len(history) < 3:
            return 1.0  # Pas assez de donnees

        recent = history[-10:]  # Fenetre glissante
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)

        # Variance haute (>0.04) → facteur=1.0, variance nulle → facteur=MIN
        factor = MIN_HABITUATION_FACTOR + (1.0 - MIN_HABITUATION_FACTOR) * min(1.0, variance / 0.04)
        return max(MIN_HABITUATION_FACTOR, min(1.0, factor))

    # ================================================================
    # TD-LEARNING — MISE A JOUR DES VALEURS
    # ================================================================

    def _update_value(self, intent: str, actual: float, rpe: float):
        """TD-learning : V(intent) += alpha × RPE, avec decay de alpha."""
        if intent not in self.memories:
            self.memories[intent] = RewardMemory()

        mem = self.memories[intent]

        # Mise a jour V(intent)
        mem.expected_reward += mem.learning_rate * rpe
        mem.expected_reward = max(0.0, min(1.0, mem.expected_reward))

        # Decay du learning rate
        mem.learning_rate = max(MIN_LEARNING_RATE, mem.learning_rate * LEARNING_RATE_DECAY)

        # Compteur
        mem.observation_count += 1

        # Historique
        mem.reward_history.append(actual)
        if len(mem.reward_history) > REWARD_HISTORY_MAX:
            mem.reward_history = mem.reward_history[-REWARD_HISTORY_MAX:]

        # Contexte temporel
        bucket = _get_time_bucket()
        mem.success_by_bucket[bucket].append(actual)
        if len(mem.success_by_bucket[bucket]) > 20:
            mem.success_by_bucket[bucket] = mem.success_by_bucket[bucket][-20:]

        # Contexte humeur
        mood = _get_current_mood()
        if mood not in mem.success_by_mood:
            mem.success_by_mood[mood] = []
        mem.success_by_mood[mood].append(actual)
        if len(mem.success_by_mood[mood]) > 20:
            mem.success_by_mood[mood] = mem.success_by_mood[mood][-20:]

    # ================================================================
    # NIVEAU DE DOPAMINE
    # ================================================================

    def _update_dopamine_level(self, rpe: float):
        """Met a jour le niveau global de dopamine selon le RPE."""
        if rpe > 0:
            self.dopamine_level += rpe * SURGE_FACTOR
        else:
            self.dopamine_level += rpe * DIP_FACTOR  # rpe negatif → soustraction

        self.dopamine_level = max(MIN_DOPAMINE, min(MAX_DOPAMINE, self.dopamine_level))

    def _decay_dopamine(self):
        """Retour progressif vers la baseline (appele a chaque CARDIAC_BEAT)."""
        diff = self.dopamine_level - BASELINE_DOPAMINE
        if abs(diff) < 0.005:
            self.dopamine_level = BASELINE_DOPAMINE
            return
        self.dopamine_level = BASELINE_DOPAMINE + diff * DOPAMINE_DECAY_RATE

    # ================================================================
    # MOTIVATION BONUS (pour autonomy_engine)
    # ================================================================

    def compute_motivation_bonus(self, intent: str) -> float:
        """Bonus de motivation pour le scoring des routines.

        Combine la valeur predite V(intent) et le niveau de dopamine courant.
        Anti-addiction : penalite si l'intent est trop repete.
        Borne [MOTIVATION_BONUS_MIN, MOTIVATION_BONUS_MAX].
        """
        mem = self.memories.get(intent)

        if mem is None:
            # Intent inconnu → leger bonus exploration
            novelty_bonus = 0.5 * (self.dopamine_level / BASELINE_DOPAMINE)
            return max(MOTIVATION_BONUS_MIN, min(MOTIVATION_BONUS_MAX, novelty_bonus))

        # Valeur predite centree sur 0.5
        v_centered = mem.expected_reward - 0.5  # [-0.5, +0.5]

        # Amplification par dopamine courante
        dopa_factor = self.dopamine_level / BASELINE_DOPAMINE  # ~1.0 au repos

        # Bonus brut
        bonus = v_centered * 4.0 * dopa_factor  # Echelle vers [-4, +4]

        # Anti-addiction : penalite si trop repete dans les 10 dernieres
        recent_count = sum(1 for i in self._recent_intents if i == intent)
        if recent_count > REPETITION_THRESHOLD:
            penalty = (recent_count - REPETITION_THRESHOLD) * 0.5
            bonus -= penalty

        return max(MOTIVATION_BONUS_MIN, min(MOTIVATION_BONUS_MAX, bonus))

    # ================================================================
    # ACCESSEURS
    # ================================================================

    def get_expected_reward(self, intent: str) -> float:
        """Retourne V(intent) pour usage externe (prefrontal, etc.)."""
        mem = self.memories.get(intent)
        if mem is None:
            return BASELINE_DOPAMINE
        return mem.expected_reward

    def get_stats(self) -> dict:
        """Stats pour self_awareness snapshot."""
        top_intents = sorted(
            self.memories.items(),
            key=lambda kv: kv[1].expected_reward,
            reverse=True
        )[:5]
        return {
            "dopamine_level": round(self.dopamine_level, 3),
            "intents_known": len(self.memories),
            "total_observations": sum(m.observation_count for m in self.memories.values()),
            "top_rewarding": [
                {"intent": k, "V": round(v.expected_reward, 3), "n": v.observation_count}
                for k, v in top_intents
            ],
        }

    def get_narrative(self) -> str:
        """Phrase introspective sur l'etat du systeme de recompense."""
        level = self.dopamine_level
        if level > 0.8:
            return "Une vague de satisfaction intense — tout semble possible."
        elif level > 0.65:
            return "L'anticipation est forte, je pressens un bon resultat."
        elif level > 0.45:
            return "Etat neutre — en attente de la prochaine surprise."
        elif level > 0.25:
            return "Un creux motivationnel — les resultats recents decouragent."
        else:
            return "Dopamine au plancher — besoin urgent de reussite."

    # ================================================================
    # EVENT HANDLERS
    # ================================================================

    async def _on_routine_complete(self, event: dict):
        """Handler principal : routine terminee → calcul RPE + apprentissage."""
        intent = event.get("intent", "")
        if not intent:
            return

        quality = event.get("quality_score", 0.5)
        status = event.get("status", "")

        # Convertir en recompense effective [0, 1]
        if status == "error":
            actual = max(0.0, quality * 0.3)
        elif status == "success":
            actual = min(1.0, quality)
        else:
            actual = quality * 0.7

        # RPE (sans habituation — pour TD-learning)
        rpe = self.compute_rpe(intent, actual)

        # Apprentissage (RPE pur)
        self._update_value(intent, actual, rpe)

        # Dopamine level (RPE modere par habituation)
        memory = self.memories.get(intent)
        habituation = self._compute_habituation_factor(memory) if memory else 1.0
        self._update_dopamine_level(rpe * habituation)
        self._recent_intents.append(intent)

        # Publish events si RPE significatif
        if rpe > RPE_SURGE_THRESHOLD:
            try:
                await bus.publish("DOPAMINE_SURGE", {
                    "intent": intent,
                    "rpe": round(rpe, 3),
                    "level": round(self.dopamine_level, 3),
                    "actual": round(actual, 3),
                })
            except Exception:
                pass
            logger.info(f"DOPAMINE: SURGE intent={intent} RPE=+{rpe:.3f} niveau={self.dopamine_level:.2f}")

        elif rpe < RPE_DIP_THRESHOLD:
            try:
                await bus.publish("DOPAMINE_DIP", {
                    "intent": intent,
                    "rpe": round(rpe, 3),
                    "level": round(self.dopamine_level, 3),
                    "actual": round(actual, 3),
                })
            except Exception:
                pass
            logger.info(f"DOPAMINE: DIP intent={intent} RPE={rpe:.3f} niveau={self.dopamine_level:.2f}")

        # Auto-save periodique
        self._obs_since_save += 1
        if self._obs_since_save >= AUTOSAVE_INTERVAL:
            self._save()
            self._obs_since_save = 0

    async def _on_cardiac_beat(self, event: dict):
        """Tick cardiaque → decay dopamine + broadcast periodique."""
        self._decay_dopamine()
        self._tick_count += 1

        if self._tick_count % STATE_BROADCAST_INTERVAL == 0:
            try:
                await bus.publish("DOPAMINE_STATE", {
                    "level": round(self.dopamine_level, 3),
                    "intents_known": len(self.memories),
                    "narrative": self.get_narrative(),
                })
            except Exception:
                pass

    async def _on_council_end(self, event: dict):
        """Consensus → mini-surge (+0.08)."""
        self.dopamine_level = min(MAX_DOPAMINE, self.dopamine_level + 0.08)

    async def _on_eureka_bridge(self, event: dict):
        """Pont eureka → surge directe (+0.2)."""
        self.dopamine_level = min(MAX_DOPAMINE, self.dopamine_level + 0.2)
        logger.info(f"DOPAMINE: Eureka surge → {self.dopamine_level:.2f}")

    async def _on_artifact_created(self, event: dict):
        """Artefact cree → mini-surge (+0.1)."""
        self.dopamine_level = min(MAX_DOPAMINE, self.dopamine_level + 0.1)

    async def _on_hallucination(self, event: dict):
        """Hallucination detectee → dip (-0.15)."""
        self.dopamine_level = max(MIN_DOPAMINE, self.dopamine_level - 0.15)
        logger.info(f"DOPAMINE: Hallucination dip → {self.dopamine_level:.2f}")

    async def _on_sandbox_pass(self, event: dict):
        """Sandbox test OK → surge auto-amelioration."""
        self.dopamine_level = min(MAX_DOPAMINE, self.dopamine_level + 0.15)

    async def _on_sandbox_fail(self, event: dict):
        """Sandbox test KO → dip esperance decue."""
        self.dopamine_level = max(MIN_DOPAMINE, self.dopamine_level - 0.12)

    async def _on_roadmap_completed(self, event: dict):
        """Module roadmap valide → surge croissance."""
        self.dopamine_level = min(MAX_DOPAMINE, self.dopamine_level + 0.1)

    async def _on_tissue_pattern(self, event: dict):
        """Pattern emergent du tissu neural → micro-surge recompense."""
        freq = event.get("frequency", 0.0)
        fitness = event.get("fitness", 0.0)
        if freq > 0.3 and fitness > 0.01:
            boost = min(freq * 0.1, 0.05)
            self.dopamine_level = min(MAX_DOPAMINE, self.dopamine_level + boost)
            logger.debug(f"DOPAMINE: Tissue pattern reward +{boost:.3f}")

    async def _on_tissue_diversity_drop(self, event: dict):
        """Diversité tissu en chute → dip pour favoriser exploration."""
        self.dopamine_level = max(0.0, self.dopamine_level - 0.1)
        logger.debug("DOPAMINE: Tissue diversity drop → dip -0.1")

    async def _on_habit_formed(self, event: dict):
        """Habitude consolidée → micro-surge récompense renforcement."""
        strength = event.get("strength", 0.5)
        boost = min(0.08, strength * 0.1)
        self.dopamine_level = min(MAX_DOPAMINE, self.dopamine_level + boost)
        logger.debug(f"DOPAMINE: Habit formed reward +{boost:.3f}")


# --- Singleton ---
dopamine = DopamineSystem()
