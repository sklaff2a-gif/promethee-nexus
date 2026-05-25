"""Repetition espacee par graines de debat (Spaced Repetition).

Chantier 23-25/05 (JM + Claude + Gemini). 3 specs verrouillees :
- Two-Key Turn (Claude propose, JM signe via /seed-ok)
- Evaluation heuristique pure (somme energies spreading_activation, 0 token LLM)
- Hard Cap J+7 (calendrier ladder J+3 -> J+5 -> J+7 sur demi-vie chemin 3.1j mesuree 24/05)

Voir memory/seeds_repetition_design_2026_05_23.md.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime
from threading import RLock
from typing import Any, Dict, List, Optional

logger = logging.getLogger("seeds_engine")

# Calendrier ladder T0 -> J+3 -> J+5 -> J+7 (max).
# Verrouille 24/05 sur demi-vie chemin = 3.1j (chemins 0.1-0.2).
# J+14 = mort par franchissement seuil pruning 0.08. J+7 = marge securisee.
INTERVAL_LADDER_DAYS = [3, 5, 7]

ENERGY_THRESHOLD = 0.5       # seuil "bonne resonance" pour monter le ladder
MAX_ENERGY_CAP = 50.0        # plafond anti-saturation (garde-fou Gemini)
PROPOSAL_TTL_SECONDS = 900   # 15 min pour proposition pending (garde-fou Gemini)
ACTIVATION_INTENSITY = 0.5   # intensite cortex.activate_concept
TOP_K_QUERY = 10             # nb concepts a requeter dans query_associations
MAX_RECALL_HISTORY = 50      # trim history par graine

_DEFAULT_SEEDS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "seeds.json"
)


class SeedsEngine:
    """Singleton — gestion des graines de repetition espacee."""

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
        self._seeds: List[Dict[str, Any]] = []
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._seeds_file = _DEFAULT_SEEDS_FILE
        self._load()

    # --- Persistance (UTF-8 sans BOM, cf. feedback_powershell_bom 23/05) ---

    def _load(self):
        if not os.path.exists(self._seeds_file):
            self._seeds = []
            return
        try:
            with open(self._seeds_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._seeds = data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"SeedsEngine: load failed: {e} — repartit vide")
            self._seeds = []

    def _save(self):
        os.makedirs(os.path.dirname(self._seeds_file), exist_ok=True)
        with open(self._seeds_file, "w", encoding="utf-8") as f:
            json.dump(self._seeds, f, indent=2, ensure_ascii=False)

    # --- Two-Key Turn (Claude propose -> JM signe) ---

    def propose(self, phrase: str, source_debat: Optional[str] = None) -> str:
        """Claude propose une graine en fin de debat. Retourne proposal_id (audit)."""
        with self._lock:
            phrase = (phrase or "").strip()
            if not phrase:
                raise ValueError("phrase vide")
            self._cleanup_pending()
            proposal_id = uuid.uuid4().hex[:12]
            self._pending[proposal_id] = {
                "phrase": phrase,
                "source_debat": source_debat,
                "ts": time.time(),
            }
            logger.info(f"🌱 [SEED-PENDING] Proposition {proposal_id} : '{phrase[:60]}...'")
            return proposal_id

    def _cleanup_pending(self):
        """Retire les propositions expirees (TTL 15 min)."""
        now = time.time()
        expired = [
            k for k, p in self._pending.items()
            if now - p["ts"] > PROPOSAL_TTL_SECONDS
        ]
        for k in expired:
            del self._pending[k]

    def get_pending(self) -> List[Dict[str, Any]]:
        """Liste des propositions pending non expirees (audit)."""
        with self._lock:
            self._cleanup_pending()
            return [
                {
                    "proposal_id": k,
                    "phrase": p["phrase"],
                    "source_debat": p.get("source_debat"),
                    "age_seconds": round(time.time() - p["ts"], 1),
                }
                for k, p in self._pending.items()
            ]

    def validate(
        self,
        proposal_id: Optional[str] = None,
        phrase: Optional[str] = None,
        source_debat: Optional[str] = None,
    ) -> Dict[str, Any]:
        """JM signe une graine. 3 modes :
        1) via proposal_id explicite
        2) via phrase exacte (fallback si TTL expire)
        3) sans arg -> derniere proposition pending non expiree
        """
        with self._lock:
            self._cleanup_pending()

            if proposal_id:
                if proposal_id not in self._pending:
                    raise ValueError(
                        f"proposal_id inconnu ou expire : {proposal_id}. "
                        "Fournis la phrase exacte via le parametre `phrase`."
                    )
                proposal = self._pending.pop(proposal_id)
                return self._create_seed(
                    proposal["phrase"],
                    source_debat or proposal.get("source_debat")
                )

            if phrase:
                return self._create_seed(phrase.strip(), source_debat)

            if self._pending:
                last_key = max(
                    self._pending.keys(),
                    key=lambda k: self._pending[k]["ts"]
                )
                proposal = self._pending.pop(last_key)
                return self._create_seed(
                    proposal["phrase"],
                    source_debat or proposal.get("source_debat")
                )

            raise ValueError(
                "Aucune proposition pending non expiree. "
                "Fournis la phrase exacte via le parametre `phrase`."
            )

    def _create_seed(self, phrase: str, source_debat: Optional[str]) -> Dict[str, Any]:
        if not phrase:
            raise ValueError("phrase vide")
        if any(s["phrase"] == phrase for s in self._seeds):
            raise ValueError(f"Graine deja presente : '{phrase[:60]}...'")
        seed_id = f"seed_{uuid.uuid4().hex[:8]}"
        now = time.time()
        first_delay_days = INTERVAL_LADDER_DAYS[0]
        next_recall_ts = now + first_delay_days * 86400
        seed: Dict[str, Any] = {
            "id": seed_id,
            "phrase": phrase,
            "creation_date": datetime.fromtimestamp(now).isoformat(),
            "ladder_index": 0,
            "stability_score": 1.0,
            "next_recall": datetime.fromtimestamp(next_recall_ts).isoformat(),
            "source_debat": source_debat,
            "recall_history": [],
        }
        self._seeds.append(seed)
        self._save()
        logger.info(
            f"🌱 [SEED] Graine plantee : {seed_id} -> J+{first_delay_days} "
            f"phrase='{phrase[:80]}...'"
        )
        return dict(seed)

    def remove(self, seed_id: str) -> bool:
        """/seed-remove — retrait manuel d'une graine obsolete."""
        with self._lock:
            before = len(self._seeds)
            self._seeds = [s for s in self._seeds if s["id"] != seed_id]
            removed = len(self._seeds) < before
            if removed:
                self._save()
                logger.info(f"🌱 [SEED] Graine retiree : {seed_id}")
            return removed

    def list_seeds(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(s) for s in self._seeds]

    def get_due(self, now_ts: Optional[float] = None) -> List[Dict[str, Any]]:
        """Graines dont next_recall <= now (triees par retard decroissant)."""
        now_ts = now_ts if now_ts is not None else time.time()
        with self._lock:
            due = []
            for seed in self._seeds:
                try:
                    next_ts = datetime.fromisoformat(seed["next_recall"]).timestamp()
                except (ValueError, KeyError, TypeError):
                    continue
                if next_ts <= now_ts:
                    due.append((next_ts, seed))
            due.sort(key=lambda x: x[0])  # plus en retard d'abord
            return [dict(s) for _, s in due]

    # --- Recall (coeur) ---

    def recall(self, seed_id: str) -> Dict[str, Any]:
        """Reveille une graine : active concepts -> mesure resonance -> ajuste.

        Effets :
        - cortex.activate_concept() boost l'energie des nodes (co-activation
          captee par STDP au cycle suivant).
        - cortex.query_associations() lit la resonance (lecture pure).
        - Le ladder monte si energy >= ENERGY_THRESHOLD, descend sinon.

        Coût LLM : ZERO (heuristique pure sur P16).
        """
        with self._lock:
            seed = next((s for s in self._seeds if s["id"] == seed_id), None)
            if seed is None:
                raise ValueError(f"Graine inconnue : {seed_id}")

            t_start = time.perf_counter()

            # 1) Extract concepts depuis la phrase
            try:
                from core.spreading_activation import extract_concepts
                concepts_with_weight = extract_concepts(seed["phrase"], max_concepts=5)
                concepts = [c for c, _ in concepts_with_weight]
            except Exception as e:
                logger.warning(f"SeedsEngine: extract_concepts failed: {e}")
                concepts = []

            if not concepts:
                latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
                return self._record_recall(
                    seed, 0.0, 0, latency_ms=latency_ms,
                    error="no_concepts_extracted"
                )

            # 2) Activate concepts -> co-activation naturelle -> STDP capture
            try:
                from core.synaptic_network import cortex
                for c in concepts:
                    cortex.activate_concept(c, intensity=ACTIVATION_INTENSITY)
            except Exception as e:
                logger.warning(f"SeedsEngine: activate_concept failed: {e}")

            # 3) Query associations -> mesure heuristique pure (0 token LLM)
            energy_sum = 0.0
            kept_count = 0
            try:
                from core.synaptic_network import cortex
                assocs = cortex.query_associations(
                    concepts, top_k=TOP_K_QUERY, use_resonance=True
                )
                # Plafonnement anti-saturation (garde-fou Gemini)
                energy_sum = min(MAX_ENERGY_CAP, sum(e for _, e in assocs))
                kept_count = sum(1 for _, e in assocs if e > 0.1)
            except Exception as e:
                logger.warning(f"SeedsEngine: query_associations failed: {e}")

            latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
            return self._record_recall(seed, energy_sum, kept_count, latency_ms)

    def _record_recall(
        self,
        seed: Dict[str, Any],
        energy: float,
        kept_count: int,
        latency_ms: float = 0.0,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ajuste ladder_index + stability_score + next_recall + history."""
        now = time.time()
        current_idx = seed.get("ladder_index", 0)

        success = (error is None and energy >= ENERGY_THRESHOLD)
        if success:
            new_idx = min(len(INTERVAL_LADDER_DAYS) - 1, current_idx + 1)
            seed["stability_score"] = round(
                min(10.0, seed.get("stability_score", 1.0) * 1.5), 3
            )
        else:
            new_idx = max(0, current_idx - 1)
            seed["stability_score"] = round(
                max(0.5, seed.get("stability_score", 1.0) * 0.8), 3
            )

        seed["ladder_index"] = new_idx
        interval_days = INTERVAL_LADDER_DAYS[new_idx]
        seed["next_recall"] = datetime.fromtimestamp(now + interval_days * 86400).isoformat()

        seed.setdefault("recall_history", []).append({
            "ts": datetime.fromtimestamp(now).isoformat(),
            "energy": round(energy, 3),
            "kept_count": kept_count,
            "success": success,
            "ladder_index_after": new_idx,
            "interval_days": interval_days,
            "stability_score": seed["stability_score"],
            "latency_ms": latency_ms,
            "error": error,
        })
        if len(seed["recall_history"]) > MAX_RECALL_HISTORY:
            seed["recall_history"] = seed["recall_history"][-MAX_RECALL_HISTORY:]

        self._save()
        flag = "OK" if success else "KO"
        logger.info(
            f"🌱 [SEED] Recall {seed['id']} [{flag}]: energy={round(energy,2)} "
            f"kept={kept_count} -> J+{interval_days} "
            f"(idx={new_idx}, stab={seed['stability_score']})"
        )
        return {
            "seed_id": seed["id"],
            "energy": round(energy, 3),
            "kept_count": kept_count,
            "success": success,
            "ladder_index": new_idx,
            "interval_days": interval_days,
            "stability_score": seed["stability_score"],
            "latency_ms": latency_ms,
            "error": error,
        }

    # --- Helpers tests / runtime override ---

    @classmethod
    def reset_singleton(cls):
        """Reinitialise le singleton (fixtures pytest)."""
        with cls._instance_lock:
            cls._instance = None

    def set_seeds_file(self, path: str):
        """Override du chemin de persistance (tests, sandbox)."""
        with self._lock:
            self._seeds_file = path
            self._load()


# Singleton accessible
seeds_engine = SeedsEngine()
