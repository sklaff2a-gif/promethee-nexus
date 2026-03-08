# core/incubation_cognitive.py — Incubation Cognitive (Subconscient Asynchrone)
# Quand une routine echoue ou qu'un cycle bloque est detecte, le probleme
# est depose dans une file. Pendant les phases d'inactivite (DMN idle),
# spreading_activation lance des associations libres en tache de fond.
# Si une connexion inattendue forte emerge (eureka), elle est publiee
# pour declencher une routine autonome. 0 LLM, 100% deterministe.

import json
import os
import time
import uuid
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("IncubationCognitive")

# --- Fichier de persistance ---

INCUBATION_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "incubation_state.json"
)

# --- Constantes ---

MAX_QUEUE_SIZE = 10
EUREKA_THRESHOLD = 0.45
PROBLEM_MAX_AGE_HOURS = 48.0
MAX_PROCESSING_ATTEMPTS = 5
SAVE_INTERVAL = 3
EUREKA_HISTORY_MAX = 50


# --- Structures de donnees ---

@dataclass
class IncubatedProblem:
    """Probleme depose dans le subconscient pour incubation."""
    id: str
    text: str
    source_intent: str
    context: str
    deposited_at: float
    attempts: int = 0
    last_attempt: float = 0.0
    resolved: bool = False


@dataclass
class EurekaEvent:
    """Connexion inattendue forte detectee par incubation."""
    id: str
    problem_id: str
    problem_text: str
    bridge_node_a: str
    bridge_node_b: str
    bridge_strength: float
    hypothesis: str
    source_intent: str
    detected_at: float


class IncubationCognitive:
    """Singleton — subconscient asynchrone pour Promethee."""

    _instance: Optional["IncubationCognitive"] = None

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

        self._queue: List[IncubatedProblem] = []
        self._eureka_history: List[EurekaEvent] = []
        self._total_eurekas: int = 0
        self._eurekas_since_save: int = 0

        self._load()

    @classmethod
    def reset_singleton(cls):
        """Detruit le singleton (pour les tests)."""
        cls._instance = None

    # --- Init ---

    def init(self):
        """Initialisation explicite appelee depuis main.py."""
        self._subscribe_events()
        logger.info("[INCUBATION] Module initialise.")

    # --- Souscriptions bus ---

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            bus.subscribe("ROUTINE_FAILED", self._on_routine_failed)
            bus.subscribe("LOOP_BREAKER_ACTION", self._on_loop_breaker)
            bus.subscribe("HIPPOCAMPUS_ARC_CREATED", self._on_arc_created)
        except Exception as e:
            logger.warning(f"[INCUBATION] Souscription echouee: {e}")

    # --- Handlers bus ---

    async def _on_routine_failed(self, event: dict):
        """Routine echouee → deposer le probleme."""
        intent = event.get("intent", "")
        if not intent:
            return
        mission = event.get("mission", "")[:150]
        failure = event.get("failure_type", "unknown")
        text = f"Echec {intent}: {mission}" if mission else f"Echec routine {intent}"
        text = text[:300]
        context = f"failure_type={failure}"
        self.deposit_problem(text, intent, context)

    async def _on_loop_breaker(self, event: dict):
        """Loop breaker avec prescription redirect ou escalate → deposer."""
        prescription = event.get("prescription", "")
        if prescription not in ("redirect", "escalate"):
            return
        intent = event.get("intent", "")
        if not intent:
            return
        text = f"Cycle bloque sur {intent} (prescription: {prescription})"
        self.deposit_problem(text[:300], intent, f"loop_breaker={prescription}")

    async def _on_arc_created(self, event: dict):
        """Arc narratif de type struggle → deposer."""
        arc_type = event.get("arc_type", "")
        if arc_type != "struggle":
            return
        intent = event.get("intent", "")
        title = event.get("title", "")
        text = f"Difficulte persistante: {title}" if title else f"Struggle arc: {intent}"
        self.deposit_problem(text[:300], intent or "unknown", f"arc={arc_type}")

    # --- Depot de problemes ---

    def deposit_problem(self, text: str, source_intent: str,
                        context: str = "") -> Optional[str]:
        """Depose un probleme dans la file d'incubation.

        Returns: ID du probleme ou None si doublon/plein.
        """
        text = text[:300]
        context = context[:200]

        # Dedup par source_intent (un seul probleme actif par intent)
        for p in self._queue:
            if p.source_intent == source_intent and not p.resolved:
                return None

        # Eviction si plein : retirer le plus vieux
        if len(self._queue) >= MAX_QUEUE_SIZE:
            # Retirer le plus ancien non-resolu
            oldest_idx = None
            for i, p in enumerate(self._queue):
                if not p.resolved:
                    oldest_idx = i
                    break
            if oldest_idx is not None:
                self._queue.pop(oldest_idx)
            else:
                # Tous resolus, retirer le premier
                self._queue.pop(0)

        problem_id = uuid.uuid4().hex[:8]
        problem = IncubatedProblem(
            id=problem_id,
            text=text,
            source_intent=source_intent,
            context=context,
            deposited_at=time.time(),
        )
        self._queue.append(problem)
        logger.info(f"[INCUBATION] Probleme depose: {text[:60]}... (id={problem_id})")
        return problem_id

    # --- Traitement (appele par DMN) ---

    async def _on_dmn_wander_cycle(self):
        """Appele par DMN en fin de cycle de vagabondage.
        Prend le plus vieux probleme non resolu et lance spreading activation."""
        self._purge_old()

        # Trouver le plus vieux probleme non resolu, non epuise
        problem = None
        for p in self._queue:
            if not p.resolved and p.attempts < MAX_PROCESSING_ATTEMPTS:
                problem = p
                break

        if problem is None:
            return

        problem.attempts += 1
        problem.last_attempt = time.time()

        # Lancer spreading activation sur le texte du probleme
        try:
            from core.spreading_activation import activation_engine
            afield = await activation_engine.activate(
                stimulus=problem.text,
                source_collection="incubation",
            )
            # Chercher des bridges creatifs forts
            bridges = activation_engine.get_creative_bridges(unused_only=True)
            for bridge in bridges:
                if bridge.bridge_strength >= EUREKA_THRESHOLD:
                    await self._fire_eureka(problem, bridge)
                    problem.resolved = True
                    break
        except Exception as e:
            logger.debug(f"[INCUBATION] SA indisponible: {e}")

    async def _fire_eureka(self, problem: IncubatedProblem, bridge):
        """Publie un evenement EUREKA_MOMENT et enregistre dans l'historique."""
        eureka_id = uuid.uuid4().hex[:8]
        eureka = EurekaEvent(
            id=eureka_id,
            problem_id=problem.id,
            problem_text=problem.text,
            bridge_node_a=bridge.node_a,
            bridge_node_b=bridge.node_b,
            bridge_strength=bridge.bridge_strength,
            hypothesis=bridge.hypothesis,
            source_intent=problem.source_intent,
            detected_at=time.time(),
        )

        self._eureka_history.append(eureka)
        if len(self._eureka_history) > EUREKA_HISTORY_MAX:
            self._eureka_history = self._eureka_history[-EUREKA_HISTORY_MAX:]

        self._total_eurekas += 1
        self._eurekas_since_save += 1

        # Marquer le bridge comme utilise
        bridge.used = True

        logger.info(
            f"[INCUBATION] EUREKA! {bridge.node_a} <-> {bridge.node_b} "
            f"(force={bridge.bridge_strength:.2f}) pour '{problem.text[:50]}...'"
        )

        # Publier sur le bus
        try:
            await bus.publish("EUREKA_MOMENT", {
                "eureka_id": eureka_id,
                "problem_id": problem.id,
                "problem_text": problem.text,
                "hypothesis": bridge.hypothesis,
                "bridge_strength": bridge.bridge_strength,
                "node_a": bridge.node_a,
                "node_b": bridge.node_b,
                "source_intent": problem.source_intent,
            })
        except Exception as e:
            logger.warning(f"[INCUBATION] Publish EUREKA echoue: {e}")

        # Sauvegarder periodiquement
        if self._eurekas_since_save >= SAVE_INTERVAL:
            self._save()
            self._eurekas_since_save = 0

    # --- Purge ---

    def _purge_old(self):
        """Supprime les problemes resolus, trop vieux, ou epuises."""
        now = time.time()
        max_age_seconds = PROBLEM_MAX_AGE_HOURS * 3600
        self._queue = [
            p for p in self._queue
            if not p.resolved
            and (now - p.deposited_at) < max_age_seconds
            and p.attempts < MAX_PROCESSING_ATTEMPTS
        ]

    # --- Scoring (Couche 21) ---

    def compute_eureka_bonus(self, intent: str) -> float:
        """Bonus [0.0, +2.0] base sur les eurekas recents pour cet intent.

        - Eureka recent (<1h) avec intent matching → bonus fort
        - Eureka recent avec mots-cles communs → bonus moyen
        - Pas d'eureka → 0.0
        """
        if not self._eureka_history:
            return 0.0

        now = time.time()
        best_bonus = 0.0
        intent_lower = intent.lower()
        intent_words = set(intent_lower.replace("_", " ").split())

        for eureka in reversed(self._eureka_history):
            age_hours = (now - eureka.detected_at) / 3600.0
            if age_hours > 24.0:
                break  # Trop vieux

            # Decroissance temporelle
            freshness = max(0.0, 1.0 - age_hours / 24.0)

            # Match exact sur l'intent source
            if eureka.source_intent.lower() == intent_lower:
                bonus = 2.0 * freshness
                best_bonus = max(best_bonus, bonus)
                continue

            # Match par mots-cles (intent vs hypothesis + nodes)
            eureka_text = (
                f"{eureka.hypothesis} {eureka.bridge_node_a} {eureka.bridge_node_b} "
                f"{eureka.source_intent}"
            ).lower()
            eureka_words = set(eureka_text.replace("_", " ").split())
            common = intent_words & eureka_words
            if common:
                keyword_bonus = min(1.0, len(common) * 0.5) * freshness
                best_bonus = max(best_bonus, keyword_bonus)

        return round(best_bonus, 3)

    # --- Contexte ---

    def get_incubation_context(self) -> str:
        """Contexte injectable dans purpose_context."""
        active = [p for p in self._queue if not p.resolved]
        if not active and not self._eureka_history:
            return ""

        parts = []
        if active:
            problems_text = ", ".join(p.source_intent for p in active[:3])
            parts.append(f"{len(active)} problemes en incubation ({problems_text})")

        # Dernier eureka
        if self._eureka_history:
            last = self._eureka_history[-1]
            age_h = (time.time() - last.detected_at) / 3600.0
            if age_h < 24.0:
                parts.append(
                    f"Eureka recent: {last.hypothesis[:80]} "
                    f"({last.bridge_node_a}<->{last.bridge_node_b})"
                )

        if not parts:
            return ""

        return f"[INCUBATION] {' | '.join(parts)}"

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques pour snapshot et endpoint API."""
        active = [p for p in self._queue if not p.resolved]
        return {
            "queue_size": len(active),
            "total_deposited": len(self._queue),
            "total_eurekas": self._total_eurekas,
            "recent_eurekas": len([
                e for e in self._eureka_history
                if (time.time() - e.detected_at) < 3600.0
            ]),
            "problems": [
                {
                    "id": p.id,
                    "text": p.text[:100],
                    "source_intent": p.source_intent,
                    "attempts": p.attempts,
                    "age_hours": round((time.time() - p.deposited_at) / 3600.0, 1),
                }
                for p in active[:5]
            ],
            "last_eureka": (
                {
                    "id": self._eureka_history[-1].id,
                    "hypothesis": self._eureka_history[-1].hypothesis[:100],
                    "bridge_strength": self._eureka_history[-1].bridge_strength,
                    "source_intent": self._eureka_history[-1].source_intent,
                }
                if self._eureka_history else None
            ),
        }

    # --- Persistence ---

    def _save(self):
        """Sauvegarde atomique."""
        try:
            data = {
                "queue": [asdict(p) for p in self._queue],
                "eureka_history": [asdict(e) for e in self._eureka_history[-EUREKA_HISTORY_MAX:]],
                "total_eurekas": self._total_eurekas,
                "saved_at": time.time(),
            }
            tmp = INCUBATION_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(INCUBATION_STATE_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, INCUBATION_STATE_FILE)
        except Exception as e:
            logger.warning(f"[INCUBATION] Sauvegarde echouee: {e}")

    def _load(self):
        """Charge l'etat."""
        try:
            if os.path.exists(INCUBATION_STATE_FILE):
                with open(INCUBATION_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._queue = [
                    IncubatedProblem(**p) for p in data.get("queue", [])
                ]
                self._eureka_history = [
                    EurekaEvent(**e) for e in data.get("eureka_history", [])
                ]
                self._total_eurekas = data.get("total_eurekas", 0)
                logger.info(
                    f"[INCUBATION] Etat restaure "
                    f"({len(self._queue)} problemes, {self._total_eurekas} eurekas)."
                )
        except Exception as e:
            logger.warning(f"[INCUBATION] Chargement echoue: {e}")


# --- Singleton ---
incubation = IncubationCognitive()
