# core/cingulate_cortex.py — Cortex Cingulaire : Detecteur de Conflits
# Detecte quand deux organes "tirent" dans des directions opposees
# et signale le conflit pour resolution. Moniteur d'erreurs.
# 0 LLM, 100% deterministe.

import json
import os
import time
import logging
from collections import deque
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("CingulateCortex")

# --- Fichier de persistance ---

CINGULATE_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "cingulate_state.json"
)

# --- Constantes ---

CONFLICT_DETECTION_WINDOW = 10
CONFLICT_THRESHOLD = 1.5          # Seuil de DETECTION (log interne)
ERROR_MEMORY_SIZE = 30
ADAPTATION_RATE = 0.1
CONFLICT_COOLDOWN = 120
ADAPTATION_DECAY = 0.99
MAX_ADAPTATION_MALUS = 0.8

# V11.0 (Phase 12C - 2026-04-21) : Filtre mnemonique du cingulate
# Contexte : 59071 CINGULATE_CONFLICT emis par le cingulate (observe via
# les 59071 inhibitions cumulees dans basal_ganglia). Cause : chaque
# divergence entre 2 couches du scoring (ex: amygdale +0.5 vs ganglions
# -1.0, ecart 1.5) declenchait un conflict public, alors que la
# divergence est le mode de fonctionnement NORMAL d'un systeme multi-agents.
# V11.0 (validation Gemini) : seuil de publication 2.5 (vs detection 1.5)
# pour filtrer le bruit a la source.
CONFLICT_PUBLISH_SEVERITY = 2.5   # Seuil de PUBLICATION bus (plus strict)

# V11.0 : hooks periodiques via cardiac_beat
ADAPTATION_DECAY_INTERVAL = 100   # decay_adaptations tous les 100 beats (~50min)
CINGULATE_PERSIST_INTERVAL = 500  # _save() tous les 500 beats (~4h)


class CingulateCortex:
    """Detecteur de conflits et moniteur d'erreurs."""

    _instance: Optional["CingulateCortex"] = None

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

        # Historique des decisions (intent + scoring breakdown)
        self.decision_history: deque = deque(maxlen=CONFLICT_DETECTION_WINDOW)

        # Log des conflits detectes
        self.conflict_log: deque = deque(maxlen=50)

        # Memoire d'erreurs
        self.error_memory: deque = deque(maxlen=ERROR_MEMORY_SIZE)

        # Poids d'adaptation par intent (malus pour intents echouant souvent)
        self.adaptation_weights: Dict[str, float] = {}

        # Compteurs
        self.total_conflicts: int = 0
        self.total_errors: int = 0

        # Cooldowns par type de conflit
        self._conflict_cooldowns: Dict[str, float] = {}

        # Conflits actifs courants
        self._active_conflicts: List[dict] = []

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour les tests)."""
        cls._instance = None

    def init(self):
        """Initialisation explicite appelee depuis main.py."""
        self._subscribe_events()
        logger.info("[CINGULATE] Module initialise.")

    # --- Souscriptions bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("ROUTINE_FAILED", self._on_routine_failed)
            bus.subscribe("HYPOTHALAMUS_REGULATION", self._on_homeostatic_regulation)
            # V11.0 Piste C+E : decay adaptations + persistance periodique
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
        except Exception as e:
            logger.warning(f"[CINGULATE] Souscription echouee: {e}")

    async def _on_cardiac_beat(self, data: dict):
        """V11.0 (2026-04-21) : hook cardiaque pour cycle metabolique.

        Piste C : decay_adaptations (rancune algorithmique evitee).
        Piste E : persistance periodique (guerit l'amnesie anterograde
        observee le 21/04 - cingulate_state vierge malgre 59071 conflits).
        """
        self._cardiac_ticks = getattr(self, '_cardiac_ticks', 0) + 1

        # Piste C : decay des poids d'adaptation tous les 100 beats
        if self._cardiac_ticks % ADAPTATION_DECAY_INTERVAL == 0:
            n_before = len(self.adaptation_weights)
            self.decay_adaptations()
            n_after = len(self.adaptation_weights)
            if n_before != n_after:
                logger.info(
                    f"[CINGULATE] V11.0 decay : {n_before} -> {n_after} adaptations"
                )

        # Piste E : persistance tous les 500 beats (~4h)
        if self._cardiac_ticks % CINGULATE_PERSIST_INTERVAL == 0:
            self._save()
            logger.info(
                f"[CINGULATE] V11.0 save : tick #{self._cardiac_ticks}, "
                f"{self.total_conflicts} conflicts, "
                f"{len(self.adaptation_weights)} adaptations"
            )

    # --- Handlers bus ---

    async def _on_routine_complete(self, event: dict):
        """Enregistre une decision et son scoring."""
        intent = event.get("intent", "")
        breakdown = event.get("scoring_breakdown", {})
        status = event.get("status", "success")

        self._record_decision(intent, breakdown)

        # Detecter les conflits
        conflicts = self._detect_conflicts()
        if conflicts:
            self._active_conflicts = conflicts
            for conflict in conflicts:
                # Log interne : TOUS les conflits (pour stats + debug)
                self.conflict_log.append({
                    "timestamp": time.time(),
                    **conflict,
                })
                self.total_conflicts += 1

                # V11.0 Piste D : Gate de severite a la publication.
                # Seuls les conflits reellement severes (>= 2.5) sont
                # publies sur le bus pour declencher une inhibition downstream.
                # Les conflits mineurs (divergence normale entre couches du
                # scoring) restent visibles dans le log mais ne punissent
                # pas les ganglions.
                severity = conflict.get("severity", 0)
                if severity >= CONFLICT_PUBLISH_SEVERITY:
                    try:
                        await bus.publish("CINGULATE_CONFLICT", conflict)
                    except Exception:
                        pass
                # Sinon : log interne uniquement, pas d'inhibition des ganglions

        # Si echec, enregistrer l'erreur
        if status in ("error", "failed", "failure"):
            self._record_error(intent, event.get("reason", status))

    async def _on_routine_failed(self, event: dict):
        """Enregistre un echec de routine."""
        intent = event.get("intent", "")
        reason = event.get("reason", "unknown")
        self._record_error(intent, reason)

    async def _on_homeostatic_regulation(self, event: dict):
        """Verifie les conflits homeostatiques."""
        errors = event.get("errors", {})
        self._check_homeostatic_conflict(errors)

    # --- Enregistrement ---

    def _record_decision(self, intent: str, breakdown: Dict[str, float]):
        """Stocke une decision avec son scoring detaille."""
        self.decision_history.append({
            "intent": intent,
            "breakdown": dict(breakdown),
            "timestamp": time.time(),
        })

    def _record_error(self, intent: str, reason: str):
        """Enregistre une erreur et adapte les poids."""
        self.error_memory.append({
            "intent": intent,
            "reason": reason,
            "timestamp": time.time(),
        })
        self.total_errors += 1

        # Adapter le poids de l'intent (malus progressif)
        current = self.adaptation_weights.get(intent, 0.0)
        new_weight = min(MAX_ADAPTATION_MALUS, current + ADAPTATION_RATE)
        self.adaptation_weights[intent] = round(new_weight, 4)

    # --- Detection de conflits ---

    def _detect_conflicts(self) -> List[dict]:
        """Analyse les dernieres decisions pour detecter des conflits."""
        conflicts = []
        now = time.time()

        if len(self.decision_history) < 2:
            return conflicts

        # Conflit organique : 2 couches contradictoires sur le meme intent
        for entry in self.decision_history:
            breakdown = entry.get("breakdown", {})
            if len(breakdown) < 2:
                continue

            layers = list(breakdown.items())
            for i in range(len(layers)):
                for j in range(i + 1, len(layers)):
                    layer_a, bonus_a = layers[i]
                    layer_b, bonus_b = layers[j]
                    # Contradiction : un positif, un negatif, ecart > seuil
                    if bonus_a * bonus_b < 0 and abs(bonus_a - bonus_b) > CONFLICT_THRESHOLD:
                        conflict_key = f"organic:{layer_a}:{layer_b}"
                        cooldown = self._conflict_cooldowns.get(conflict_key, 0)
                        if now - cooldown >= CONFLICT_COOLDOWN:
                            conflicts.append({
                                "type": "organic",
                                "organs_involved": [layer_a, layer_b],
                                "intent": entry["intent"],
                                "severity": round(abs(bonus_a - bonus_b), 2),
                                "details": {layer_a: bonus_a, layer_b: bonus_b},
                            })
                            self._conflict_cooldowns[conflict_key] = now

        # Conflit de tendance : meme intent tantot favorise, tantot penalise
        intent_scores: Dict[str, List[float]] = {}
        for entry in self.decision_history:
            intent = entry["intent"]
            total_score = sum(entry.get("breakdown", {}).values())
            if intent not in intent_scores:
                intent_scores[intent] = []
            intent_scores[intent].append(total_score)

        for intent, scores in intent_scores.items():
            if len(scores) >= 2:
                has_positive = any(s > 0.5 for s in scores)
                has_negative = any(s < -0.5 for s in scores)
                if has_positive and has_negative:
                    conflict_key = f"tendency:{intent}"
                    cooldown = self._conflict_cooldowns.get(conflict_key, 0)
                    if now - cooldown >= CONFLICT_COOLDOWN:
                        conflicts.append({
                            "type": "tendency",
                            "organs_involved": [],
                            "intent": intent,
                            "severity": round(max(scores) - min(scores), 2),
                        })
                        self._conflict_cooldowns[conflict_key] = now

        return conflicts

    def _check_homeostatic_conflict(self, errors: Dict[str, float]):
        """Detecte les conflits entre hypothalamus et dopamine."""
        # Si stress haut ET dopamine bas → conflit (besoin de calme vs besoin de stimulation)
        stress_err = errors.get("stress", 0.0)
        dopa_err = errors.get("dopamine", 0.0)

        if stress_err > 0.3 and dopa_err < -0.3:
            now = time.time()
            conflict_key = "homeostatic:stress_dopamine"
            cooldown = self._conflict_cooldowns.get(conflict_key, 0)
            if now - cooldown >= CONFLICT_COOLDOWN:
                conflict = {
                    "type": "homeostatic",
                    "organs_involved": ["hypothalamus_stress", "hypothalamus_dopamine"],
                    "intent": "",
                    "severity": round(abs(stress_err) + abs(dopa_err), 2),
                }
                self._active_conflicts.append(conflict)
                self.conflict_log.append({"timestamp": now, **conflict})
                self.total_conflicts += 1
                self._conflict_cooldowns[conflict_key] = now

    # --- Adaptation ---

    def _compute_adaptation(self, intent: str) -> float:
        """Malus adaptatif pour un intent avec haut taux d'echec."""
        return self.adaptation_weights.get(intent, 0.0)

    def decay_adaptations(self):
        """Decay naturel des poids d'adaptation."""
        for intent in list(self.adaptation_weights.keys()):
            self.adaptation_weights[intent] *= ADAPTATION_DECAY
            if self.adaptation_weights[intent] < 0.01:
                del self.adaptation_weights[intent]

    # --- Scoring (Couche 19) ---

    def compute_conflict_bonus(self, intent: str) -> float:
        """Bonus/malus de conflit pour un intent. Range [-1.0, +1.0]."""
        bonus = 0.0

        # Intent en conflit actif → malus
        for conflict in self._active_conflicts:
            if conflict.get("intent") == intent:
                severity = conflict.get("severity", 0.5)
                bonus -= min(0.8, severity * 0.3)

        # Adaptation : malus croissant pour intents a haut taux d'echec
        adaptation = self._compute_adaptation(intent)
        if adaptation > 0:
            bonus -= adaptation

        # Intent sans conflit + pas d'echecs → leger bonus
        if bonus == 0.0 and intent not in self.adaptation_weights:
            # Verifier s'il y a des decisions positives recentes
            recent_successes = sum(
                1 for e in self.decision_history
                if e.get("intent") == intent
            )
            if recent_successes > 0:
                bonus = min(0.3, recent_successes * 0.1)

        return max(-1.0, min(1.0, round(bonus, 3)))

    def get_conflict_level(self) -> float:
        """Niveau de conflit global [0, 1]. 0 = consensus total, 1 = conflit severe."""
        if not self._active_conflicts:
            return 0.0
        max_severity = max(c.get("severity", 0.0) for c in self._active_conflicts)
        # Normaliser : severity 1.5 (seuil) → 0.3, severity 5.0 → 1.0
        return min(1.0, max_severity / 5.0)

    # --- Contexte ---

    def get_conflict_context(self) -> str:
        """Conflits actifs pour purpose_context."""
        if not self._active_conflicts:
            return ""

        parts = []
        for c in self._active_conflicts[:3]:
            ctype = c.get("type", "unknown")
            intent = c.get("intent", "")
            severity = c.get("severity", 0)
            if intent:
                parts.append(f"{ctype}({intent}, sev={severity:.1f})")
            else:
                parts.append(f"{ctype}(sev={severity:.1f})")

        return f"[CONFLITS] {len(self._active_conflicts)} actif(s): {', '.join(parts)}"

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour snapshot et endpoint API."""
        error_rate: Dict[str, int] = {}
        for e in self.error_memory:
            intent = e.get("intent", "")
            error_rate[intent] = error_rate.get(intent, 0) + 1

        return {
            "total_conflicts": self.total_conflicts,
            "total_errors": self.total_errors,
            "active_conflicts": len(self._active_conflicts),
            "adaptation_weights": dict(self.adaptation_weights),
            "error_rate_by_intent": error_rate,
            "decision_count": len(self.decision_history),
            "conflict_log_size": len(self.conflict_log),
        }

    # --- Persistence ---

    def _save(self):
        """Sauvegarde atomique."""
        try:
            data = {
                "total_conflicts": self.total_conflicts,
                "total_errors": self.total_errors,
                "adaptation_weights": dict(self.adaptation_weights),
                "conflict_log": list(self.conflict_log)[-20:],
                "error_memory": list(self.error_memory),
                "saved_at": time.time(),
            }
            tmp = CINGULATE_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(CINGULATE_STATE_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CINGULATE_STATE_FILE)
        except Exception as e:
            logger.warning(f"[CINGULATE] Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat."""
        try:
            if os.path.exists(CINGULATE_STATE_FILE):
                with open(CINGULATE_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.total_conflicts = data.get("total_conflicts", 0)
                self.total_errors = data.get("total_errors", 0)
                self.adaptation_weights = data.get("adaptation_weights", {})
                errors = data.get("error_memory", [])
                self.error_memory = deque(errors[-ERROR_MEMORY_SIZE:], maxlen=ERROR_MEMORY_SIZE)
                conflicts = data.get("conflict_log", [])
                self.conflict_log = deque(conflicts[-50:], maxlen=50)
                logger.info("[CINGULATE] Etat restaure.")
        except Exception as e:
            logger.warning(f"[CINGULATE] Chargement echoue: {e}")


# --- Singleton ---
cingulate = CingulateCortex()
