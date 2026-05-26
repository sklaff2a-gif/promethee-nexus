"""Memoire des predictions confirmees — repare l'asymetrie hippocampe (26/05).

Chantier 26/05 (JM + Claude + Gemini) suite au debat Pilote/Ingenieur sur
l'asymetrie de l'hippocampe :

CONSTAT 25/05 : `hippocampus._on_prediction_resolved` (l.709-721) jette
explicitement les predictions correctes (`prediction_correct_skipped`).
Resultat : sur 57 predictions, seuls les 55 echecs sont memorises -> precision
predictive bloquee a 10% (2/57). Le systeme apprend par soustraction d'erreurs,
jamais par renforcement de succes.

DESIGN : repliquer le pattern `Strategy` du prefrontal (successes counter +
crystallization a 3 succes + decristallisation a confidence<0.2) mais ADAPTE
a la nature ouverte de la prediction :

1. **Distinction ontologique (E7 du debat Pilote)** : un acte cristallise est
   une boucle fermee (l'agent controle), une prediction est une boucle ouverte
   (le monde change). Copier-coller mecanique du pattern Strategy creerait des
   superstitions par cristallisation.

2. **Decay temporel anti-superstition (E8)** : ajout de PREDICTION_DECAY_PER_DAY.
   La confidence baisse de 0.1/jour sans renforcement, meme sans echec direct.
   Force la reconfirmation -> coherent avec epistemologie popperienne (toute
   connaissance est revisable). Echo direct de la graine D1 plantee 25/05 :
   *"L'illusion ne se resout pas par une verification unique."*

3. **Signature 3 dimensions orchestree (E9)** : context_signature combine
   concepts P16 + goals prefrontal + emotion dominante. Top-K + tri + norm
   pour tolerance aux micro-variations sans fusion de contextes differents.

GARDE-FOUS :
- Decay temporel (anti-superstition)
- MAX_PREDICTION_STRATEGIES (anti-saturation memoire)
- Cristallisation reversible (decristallise si confidence<0.2)
- Persistance JSON sans BOM (lecon feedback_powershell_bom 23/05)
"""
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("prediction_memory")

# Constantes — calibrees sur les choix du Pilote (debat 26/05)
PREDICTION_CRYSTALLIZE_THRESHOLD = 3       # repris du prefrontal Strategy
PREDICTION_DECRYSTALLIZE_THRESHOLD = 0.2   # repris du prefrontal Strategy
PREDICTION_CONFIDENCE_BOOST = 0.15         # par succes
PREDICTION_CONFIDENCE_DECAY_ON_FAIL = 0.2  # par echec direct
PREDICTION_DECAY_PER_DAY = 0.1             # anti-superstition (E8 du Pilote)
PREDICTION_INITIAL_CONFIDENCE = 0.4        # naissance d'une PredictionStrategy
MAX_PREDICTION_STRATEGIES = 200            # cap memoire (FIFO non-cristallisees)
TOP_CONCEPTS_IN_SIG = 5                    # tolerance signature
TOP_GOALS_IN_SIG = 2                       # tolerance signature

_DEFAULT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "prediction_memory.json"
)


# ============================================================================
# Signature de contexte (3 dimensions orchestrees)
# ============================================================================

def context_signature_for_prediction(
    concepts: Optional[List[str]] = None,
    goals: Optional[List[str]] = None,
    emotion: Optional[str] = None,
    top_concepts: int = TOP_CONCEPTS_IN_SIG,
    top_goals: int = TOP_GOALS_IN_SIG,
) -> str:
    """Hash composite tolerant aux micro-variations.

    Tolerance :
    - top_concepts=5 ignore les concepts mineurs
    - sorted(set()) : ordre + doublons indifferents
    - _norm[:12] : suffixes versionnes (_v1/_v2) meme bucket
    - lowercase : casse indifferente

    Distinction :
    - Goals actifs seulement (top 2) -> changement de focus = nouveau hash
    - Emotion dominante (1) -> vraie polarisation distingue
    """
    def _norm(s: str, max_len: int = 12) -> str:
        return (s or "").strip().lower()[:max_len]

    parts = []
    if concepts:
        normalized = sorted({_norm(c) for c in concepts[:top_concepts] if c})
        parts.append("c:" + "-".join(normalized))
    if goals:
        normalized = sorted({_norm(g) for g in goals[:top_goals] if g})
        parts.append("g:" + "-".join(normalized))
    if emotion:
        parts.append("e:" + _norm(emotion))

    if not parts:
        return "empty_context"
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ============================================================================
# Dataclass PredictionStrategy
# ============================================================================

@dataclass
class PredictionStrategy:
    """Une prediction qui s'est confirmee, candidate au pattern recognition.

    Inspiree de `prefrontal.Strategy` mais avec decay temporel anti-superstition.
    """
    id: str
    context_signature: str
    predicted_pattern: str
    successes: int = 0
    failures: int = 0
    confidence: float = PREDICTION_INITIAL_CONFIDENCE
    crystallized: bool = False
    last_confirmed: float = field(default_factory=time.time)
    last_decay_applied: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)

    def days_since_decay(self) -> float:
        return (time.time() - self.last_decay_applied) / 86400.0

    def apply_decay(self, now_ts: Optional[float] = None) -> float:
        """Decay temporel selon nb de jours ecoules depuis dernier decay.

        Retourne la quantite de decay applique (pour audit).
        """
        if now_ts is None:
            now_ts = time.time()
        days = (now_ts - self.last_decay_applied) / 86400.0
        if days < 0.01:
            return 0.0
        delta = PREDICTION_DECAY_PER_DAY * days
        self.confidence = max(0.0, self.confidence - delta)
        self.last_decay_applied = now_ts
        return delta


# ============================================================================
# Singleton PredictionMemory
# ============================================================================

class PredictionMemory:
    """Singleton — registre des PredictionStrategy."""

    _instance = None
    _instance_lock = RLock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._lock = RLock()
        self._strategies: List[PredictionStrategy] = []
        self._file_path = _DEFAULT_FILE
        self._load()

    # --- Persistance (UTF-8 sans BOM, cf. feedback_powershell_bom 23/05) ---

    def _load(self):
        if not os.path.exists(self._file_path):
            self._strategies = []
            return
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._strategies = [
                PredictionStrategy(**d) for d in data if isinstance(d, dict)
            ]
            logger.info(
                f"PREDICTION_MEMORY: {len(self._strategies)} strategies chargees"
            )
        except Exception as e:
            logger.warning(f"PREDICTION_MEMORY: load failed: {e}")
            self._strategies = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
            with open(self._file_path, "w", encoding="utf-8") as f:
                json.dump(
                    [asdict(s) for s in self._strategies],
                    f, indent=2, ensure_ascii=False,
                )
        except Exception as e:
            logger.warning(f"PREDICTION_MEMORY: save failed: {e}")

    # --- API publique ---

    def record_success(
        self,
        context_signature: str,
        predicted_pattern: str,
    ) -> Dict[str, Any]:
        """Enregistre une prediction confirmee.

        Si une PredictionStrategy existe deja pour ce (signature, pattern),
        increment successes + boost confidence + check cristallisation.
        Sinon cree une nouvelle entree.
        """
        with self._lock:
            now = time.time()
            # Recherche d'une strategy existante
            for strat in self._strategies:
                if (strat.context_signature == context_signature
                        and strat.predicted_pattern == predicted_pattern):
                    strat.successes += 1
                    strat.confidence = min(1.0, strat.confidence + PREDICTION_CONFIDENCE_BOOST)
                    strat.last_confirmed = now
                    # Cristallisation ?
                    newly_crystallized = False
                    if (strat.successes >= PREDICTION_CRYSTALLIZE_THRESHOLD
                            and not strat.crystallized):
                        strat.crystallized = True
                        newly_crystallized = True
                        logger.info(
                            f"PREDICTION_MEMORY: 🔮 Cristallisation pattern "
                            f"'{predicted_pattern[:50]}' (sig={context_signature[:8]}, "
                            f"successes={strat.successes})"
                        )
                    self._save()
                    return {
                        "status": "reinforced",
                        "strategy_id": strat.id,
                        "successes": strat.successes,
                        "confidence": round(strat.confidence, 3),
                        "crystallized": strat.crystallized,
                        "newly_crystallized": newly_crystallized,
                    }

            # Nouvelle strategy
            strat = PredictionStrategy(
                id=uuid.uuid4().hex[:12],
                context_signature=context_signature,
                predicted_pattern=predicted_pattern,
                successes=1,
                confidence=PREDICTION_INITIAL_CONFIDENCE,
            )
            self._strategies.append(strat)

            # Cap FIFO (garde les cristallisees + plus recentes)
            if len(self._strategies) > MAX_PREDICTION_STRATEGIES:
                non_crystal = [s for s in self._strategies if not s.crystallized]
                if non_crystal:
                    non_crystal.sort(key=lambda s: s.last_confirmed)
                    self._strategies.remove(non_crystal[0])

            self._save()
            logger.info(
                f"PREDICTION_MEMORY: 🌱 Nouvelle strategy "
                f"'{predicted_pattern[:50]}' (sig={context_signature[:8]})"
            )
            return {
                "status": "created",
                "strategy_id": strat.id,
                "successes": 1,
                "confidence": round(strat.confidence, 3),
                "crystallized": False,
                "newly_crystallized": False,
            }

    def record_failure(
        self,
        context_signature: str,
        predicted_pattern: str,
    ) -> Optional[Dict[str, Any]]:
        """Enregistre l'echec d'une prediction (rare car prefrontal stocke deja
        les echecs via hippocampus, mais permet la decristallisation rapide
        de PredictionStrategy si une cristallisee echoue)."""
        with self._lock:
            for strat in self._strategies:
                if (strat.context_signature == context_signature
                        and strat.predicted_pattern == predicted_pattern):
                    strat.failures += 1
                    strat.confidence = max(0.0, strat.confidence - PREDICTION_CONFIDENCE_DECAY_ON_FAIL)
                    decrystallized = False
                    if (strat.crystallized
                            and strat.confidence < PREDICTION_DECRYSTALLIZE_THRESHOLD):
                        strat.crystallized = False
                        decrystallized = True
                        logger.info(
                            f"PREDICTION_MEMORY: 💔 Decristallisation pattern "
                            f"'{predicted_pattern[:50]}' "
                            f"(confidence={strat.confidence:.2f} < {PREDICTION_DECRYSTALLIZE_THRESHOLD})"
                        )
                    self._save()
                    return {
                        "status": "failure_recorded",
                        "strategy_id": strat.id,
                        "failures": strat.failures,
                        "confidence": round(strat.confidence, 3),
                        "decrystallized": decrystallized,
                    }
            return None  # Pas de strategy existante a affaiblir

    def apply_decay_all(self) -> Dict[str, Any]:
        """Decay temporel sur toutes les strategies. Decristallise si confidence
        passe sous le seuil. Retourne stats pour audit."""
        with self._lock:
            now = time.time()
            total_decay = 0.0
            decrystallized_count = 0
            removed_count = 0
            for strat in list(self._strategies):
                delta = strat.apply_decay(now)
                total_decay += delta
                if (strat.crystallized
                        and strat.confidence < PREDICTION_DECRYSTALLIZE_THRESHOLD):
                    strat.crystallized = False
                    decrystallized_count += 1
                # Garbage-collect : strategies non-cristallisees avec confidence
                # totalement effondree (≤0.05) sont retirees
                if not strat.crystallized and strat.confidence <= 0.05:
                    self._strategies.remove(strat)
                    removed_count += 1
            if total_decay > 0 or removed_count > 0:
                self._save()
            return {
                "strategies_total": len(self._strategies),
                "total_decay_applied": round(total_decay, 3),
                "decrystallized_count": decrystallized_count,
                "removed_count": removed_count,
            }

    def suggest_prediction(
        self,
        context_signature: str,
    ) -> Optional[Dict[str, Any]]:
        """Recherche un pattern cristallise pour ce contexte (bypass deliberation).

        Retourne le pattern cristallise le plus confiant, ou None.
        """
        with self._lock:
            matches = [
                s for s in self._strategies
                if s.context_signature == context_signature and s.crystallized
            ]
            if not matches:
                return None
            best = max(matches, key=lambda s: s.confidence)
            return {
                "predicted_pattern": best.predicted_pattern,
                "confidence": round(best.confidence, 3),
                "successes": best.successes,
                "strategy_id": best.id,
            }

    def list_strategies(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [asdict(s) for s in self._strategies]

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._strategies)
            crystallized = sum(1 for s in self._strategies if s.crystallized)
            avg_confidence = (
                sum(s.confidence for s in self._strategies) / total
                if total else 0.0
            )
            return {
                "total_strategies": total,
                "crystallized": crystallized,
                "avg_confidence": round(avg_confidence, 3),
            }

    # --- Helpers tests ---

    @classmethod
    def reset_singleton(cls):
        with cls._instance_lock:
            cls._instance = None

    def set_file_path(self, path: str):
        with self._lock:
            self._file_path = path
            self._load()


# Singleton accessible
prediction_memory = PredictionMemory()
