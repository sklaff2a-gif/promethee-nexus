"""
core/amygdala.py — Amygdale : mémoire émotionnelle et biais affectif.

Organe qui remplace et étend la proto-amygdale du reptilien :
- Valence émotionnelle (positif/négatif) au lieu de menaces seules
- Extinction progressive des souvenirs non-confirmés
- Biais de scoring (Couche 16 dans autonomy_engine)
- Appraisal rapide déterministe (0 LLM)

Singleton : `amygdala`
"""

import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

logger = logging.getLogger("amygdala")

# ── Constantes ──────────────────────────────────────────────────────

AMYGDALA_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "amygdala_state.json"
)
EXTINCTION_THRESHOLD = 50       # Cycles sans observation → affaiblissement
EXTINCTION_DECAY = 0.95         # Facteur de décroissance par cycle post-seuil
MIN_MEMORY_AROUSAL = 0.05       # En-dessous → suppression
MAX_MEMORIES = 200              # Limite mémoires stockées
EMOTIONAL_BIAS_RANGE = (-1.5, 1.5)  # Plage du biais Couche 16


# ── Dataclass ───────────────────────────────────────────────────────

@dataclass
class EmotionalMemory:
    """Un souvenir émotionnel — conditionnement affectif."""
    pattern: str                # "ollama_down", "COUNCIL_DEBATE:success", etc.
    valence: float              # [-1.0, +1.0] — négatif=menace, positif=récompense
    arousal: float              # [0.0, 1.0] — intensité émotionnelle
    occurrences: int            # Nombre d'observations
    last_seen: float            # timestamp
    conditioned_reflex: str     # Réflexe associé (FREEZE, SHED, etc.) ou "" si positif
    extinction_counter: int     # Cycles sans re-observation


# ── Appraisal mapping ──────────────────────────────────────────────

# Événement → (valence, arousal) par défaut
_APPRAISAL_MAP: Dict[str, tuple] = {
    "REPTILIAN_ALERT":          (-0.8, 0.9),
    "HALLUCINATION_DETECTED":   (-0.6, 0.7),
    "DOPAMINE_SURGE":           (+0.7, 0.6),
    "PREFRONTAL_GOAL_COMPLETE": (+0.8, 0.5),
    "COUNCIL_END":              (+0.3, 0.3),
    "TISSUE_PATTERN_EMERGED":   (+0.5, 0.4),
    "TISSUE_EXTINCTION_RISK":   (-0.5, 0.6),
    "TISSUE_PANDEMIC_START":    (-0.6, 0.8),
    "TISSUE_PANDEMIC_END":      (+0.4, 0.5),
}

_DEFAULT_APPRAISAL = (0.0, 0.2)

# Intent → patterns émotionnels associés (pour compute_emotional_bias)
_INTENT_EMOTION_MAP: Dict[str, List[str]] = {
    "EXPANSION_CODE":       ["HALLUCINATION_DETECTED", "code_success", "code_error"],
    "COUNCIL_DEBATE":       ["COUNCIL_DEBATE:success", "COUNCIL_DEBATE:error"],
    "EVOLUTION_RUN":        ["evolution_success", "evolution_error", "HALLUCINATION_DETECTED"],
    "MEMORY_CONSOLIDATION": ["memory_success", "memory_error"],
    "AUDIT_STRUCTURE":      ["audit_success", "audit_error"],
    "SECURITY_AUDIT":       ["security_success", "security_error"],
    "MEMORY_CLEANUP":       ["memory_cleanup_success", "memory_cleanup_error"],
    "DREAM":                ["dream_success"],
    "CREATIVE_WRITING":     ["creative_success", "creative_error"],
    "RESEARCH":             ["research_success", "research_error"],
}


# ── Classe Amygdala ─────────────────────────────────────────────────

class Amygdala:
    """Mémoire émotionnelle — appraisal rapide, conditionnement, extinction."""

    _instance: Optional["Amygdala"] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if Amygdala._initialized:
            return
        Amygdala._initialized = True
        self.memories: Dict[str, EmotionalMemory] = {}
        self._subscribed = False
        self._extinction_cycle = 0
        self._load()

    def __repr__(self):
        pos = sum(1 for m in self.memories.values() if m.valence > 0)
        neg = sum(1 for m in self.memories.values() if m.valence < 0)
        return f"<Amygdala memories={len(self.memories)} pos={pos} neg={neg}>"

    @classmethod
    def reset_singleton(cls):
        cls._instance = None
        cls._initialized = False

    # ── Init (bus subscriptions) ────────────────────────────────────

    def init(self):
        """Initialise les souscriptions bus."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("HALLUCINATION_DETECTED", self._on_hallucination)
            bus.subscribe("DOPAMINE_SURGE", self._on_dopamine_surge)
            bus.subscribe("PREFRONTAL_GOAL_COMPLETE", self._on_goal_complete)
            bus.subscribe("COUNCIL_END", self._on_council_end)
            bus.subscribe("TISSUE_PATTERN_EMERGED", self._on_tissue_pattern)
            bus.subscribe("TISSUE_EXTINCTION_RISK", self._on_tissue_extinction)
            bus.subscribe("TISSUE_PANDEMIC_START", self._on_tissue_pandemic_start)
            bus.subscribe("TISSUE_PANDEMIC_END", self._on_tissue_pandemic_end)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            logger.info("[AMYGDALA] Bus subscriptions actives.")
        except Exception as e:
            logger.warning(f"[AMYGDALA] Bus subscription échouée: {e}")

    # ── Appraisal rapide ────────────────────────────────────────────

    def appraise(self, event_type: str, context: dict = None) -> dict:
        """Évaluation rapide d'un événement → {valence, arousal, memory_key}.

        Déterministe, 0 LLM.
        """
        context = context or {}
        valence, arousal = _APPRAISAL_MAP.get(event_type, _DEFAULT_APPRAISAL)

        # Moduler par le contexte si disponible
        severity = context.get("severity", 0)
        if severity and valence < 0:
            arousal = min(1.0, arousal + severity * 0.05)

        success = context.get("success")
        if success is True and valence <= 0:
            valence = abs(valence) * 0.5  # Inverser si success
        elif success is False and valence >= 0:
            valence = -abs(valence) * 0.5

        memory_key = self._build_memory_key(event_type, context)

        # Enregistrer / renforcer la mémoire
        self._record_memory(memory_key, valence, arousal,
                            context.get("reflex", ""))

        return {
            "valence": valence,
            "arousal": arousal,
            "memory_key": memory_key,
        }

    def _build_memory_key(self, event_type: str, context: dict) -> str:
        """Construit une clé de mémoire à partir de l'événement et du contexte."""
        suffix = context.get("pattern", "") or context.get("intent", "")
        if suffix:
            return f"{event_type}:{suffix}"
        return event_type

    # ── Conditionnement ─────────────────────────────────────────────

    def condition(self, pattern: str, valence: float, arousal: float,
                  reflex: str = ""):
        """Conditionnement explicite (appelé par reptilien ou directement)."""
        valence = max(-1.0, min(1.0, valence))
        arousal = max(0.0, min(1.0, arousal))
        self._record_memory(pattern, valence, arousal, reflex)

    def _record_memory(self, pattern: str, valence: float, arousal: float,
                       reflex: str = ""):
        """Enregistre ou renforce une mémoire émotionnelle."""
        if pattern in self.memories:
            mem = self.memories[pattern]
            mem.occurrences += 1
            # Moyenne mobile pour valence et arousal
            mem.valence = mem.valence * 0.7 + valence * 0.3
            mem.arousal = mem.arousal * 0.7 + arousal * 0.3
            mem.last_seen = time.time()
            mem.extinction_counter = 0  # Reset extinction
            # Le réflexe le plus grave l'emporte
            if reflex and mem.conditioned_reflex:
                reflex_order = ["ADRENALINE", "FLINCH", "SHED", "FIGHT", "FREEZE"]
                if (reflex in reflex_order and
                        mem.conditioned_reflex in reflex_order):
                    if reflex_order.index(reflex) > reflex_order.index(
                            mem.conditioned_reflex):
                        mem.conditioned_reflex = reflex
            elif reflex and not mem.conditioned_reflex:
                mem.conditioned_reflex = reflex
        else:
            # Vérifier la limite
            if len(self.memories) >= MAX_MEMORIES:
                self._evict_weakest()
            self.memories[pattern] = EmotionalMemory(
                pattern=pattern,
                valence=max(-1.0, min(1.0, valence)),
                arousal=max(0.0, min(1.0, arousal)),
                occurrences=1,
                last_seen=time.time(),
                conditioned_reflex=reflex,
                extinction_counter=0,
            )

    def _evict_weakest(self):
        """Supprime la mémoire la plus faible (arousal le plus bas)."""
        if not self.memories:
            return
        weakest = min(self.memories.values(), key=lambda m: m.arousal)
        del self.memories[weakest.pattern]

    # ── Consultation ────────────────────────────────────────────────

    def get_conditioned_response(self, pattern: str) -> Optional[EmotionalMemory]:
        """Consulte la mémoire émotionnelle pour un pattern donné."""
        return self.memories.get(pattern)

    # ── Extinction ──────────────────────────────────────────────────

    def tick_extinction(self):
        """Cycle d'extinction — les mémoires non-confirmées s'affaiblissent."""
        self._extinction_cycle += 1
        to_remove = []
        for key, mem in self.memories.items():
            mem.extinction_counter += 1
            if mem.extinction_counter > EXTINCTION_THRESHOLD:
                mem.arousal *= EXTINCTION_DECAY
                if mem.arousal < MIN_MEMORY_AROUSAL:
                    to_remove.append(key)
        for key in to_remove:
            del self.memories[key]

    # ── Biais émotionnel (Couche 16) ────────────────────────────────

    def compute_emotional_bias(self, intent: str) -> float:
        """Biais [-1.5, +1.5] pour le scoring autonomy_engine.

        Combine valence × arousal des mémoires liées à l'intent.
        """
        patterns = _INTENT_EMOTION_MAP.get(intent, [])
        if not patterns:
            return 0.0

        total_bias = 0.0
        count = 0
        for p in patterns:
            mem = self.memories.get(p)
            if mem:
                total_bias += mem.valence * mem.arousal
                count += 1

        # Chercher aussi les mémoires dont le pattern contient l'intent
        intent_lower = intent.lower()
        for key, mem in self.memories.items():
            if intent_lower in key.lower() and key not in patterns:
                total_bias += mem.valence * mem.arousal * 0.5  # Demi-poids
                count += 1

        if count == 0:
            return 0.0

        bias = total_bias / count
        return max(EMOTIONAL_BIAS_RANGE[0],
                   min(EMOTIONAL_BIAS_RANGE[1], bias))

    # ── Bus handlers ────────────────────────────────────────────────

    async def _on_reptilian_alert(self, data: dict):
        """Menace reptilienne → mémoire négative."""
        self.appraise("REPTILIAN_ALERT", {
            "severity": data.get("threat_level", 5),
            "pattern": data.get("reflex", "alert"),
            "reflex": data.get("reflex", ""),
        })

    async def _on_hallucination(self, data: dict):
        """Hallucination détectée → mémoire négative."""
        self.appraise("HALLUCINATION_DETECTED", {
            "pattern": data.get("type", "hallucination"),
        })

    async def _on_dopamine_surge(self, data: dict):
        """Surge dopaminergique → mémoire positive."""
        self.appraise("DOPAMINE_SURGE", {
            "pattern": data.get("trigger", "surge"),
        })

    async def _on_goal_complete(self, data: dict):
        """Objectif préfrontal atteint → mémoire positive."""
        self.appraise("PREFRONTAL_GOAL_COMPLETE", {
            "pattern": data.get("goal_id", "goal"),
        })

    async def _on_council_end(self, data: dict):
        """Fin de conseil → mémoire légèrement positive."""
        self.appraise("COUNCIL_END", {
            "pattern": data.get("topic", "council"),
        })

    async def _on_tissue_pattern(self, data: dict):
        """Pattern émergent dans le tissu → mémoire positive."""
        self.appraise("TISSUE_PATTERN_EMERGED", {
            "pattern": data.get("pattern", "emergence"),
        })

    async def _on_tissue_extinction(self, data: dict):
        """Risque d'extinction tissu → mémoire négative."""
        self.appraise("TISSUE_EXTINCTION_RISK", {
            "pattern": data.get("zone", "extinction"),
        })

    async def _on_tissue_pandemic_start(self, data: dict):
        """Pandémie tissu déclenchée → mémoire négative forte."""
        self.appraise("TISSUE_PANDEMIC_START", {
            "pattern": data.get("motif", "pandemic"),
        })

    async def _on_tissue_pandemic_end(self, data: dict):
        """Pandémie tissu terminée → mémoire positive (résilience)."""
        self.appraise("TISSUE_PANDEMIC_END", {
            "pattern": data.get("motif", "recovery"),
        })

    async def _on_routine_complete(self, data: dict):
        """Routine autonome terminée → conditionnement succès/erreur."""
        intent = data.get("intent", "unknown")
        status = data.get("status", "")
        if status == "success":
            key = f"{intent.lower()}_success"
            self.condition(key, valence=0.4, arousal=0.3)
        elif status in ("error", "failed"):
            key = f"{intent.lower()}_error"
            self.condition(key, valence=-0.4, arousal=0.5)

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Statistiques pour API et self_awareness."""
        pos = sum(1 for m in self.memories.values() if m.valence > 0)
        neg = sum(1 for m in self.memories.values() if m.valence < 0)
        neutral = len(self.memories) - pos - neg

        avg_valence = 0.0
        avg_arousal = 0.0
        if self.memories:
            avg_valence = sum(m.valence for m in self.memories.values()) / len(self.memories)
            avg_arousal = sum(m.arousal for m in self.memories.values()) / len(self.memories)

        # Top 5 mémoires les plus intenses
        top = sorted(self.memories.values(), key=lambda m: m.arousal, reverse=True)[:5]

        return {
            "total_memories": len(self.memories),
            "positive_memories": pos,
            "negative_memories": neg,
            "neutral_memories": neutral,
            "avg_valence": round(avg_valence, 3),
            "avg_arousal": round(avg_arousal, 3),
            "extinction_cycle": self._extinction_cycle,
            "top_memories": [
                {"pattern": m.pattern, "valence": round(m.valence, 2),
                 "arousal": round(m.arousal, 2), "occurrences": m.occurrences}
                for m in top
            ],
        }

    # ── Persistance ─────────────────────────────────────────────────

    def save(self):
        """Sauvegarde l'état sur disque."""
        try:
            data = {
                "memories": {k: asdict(v) for k, v in self.memories.items()},
                "extinction_cycle": self._extinction_cycle,
            }
            tmp_path = AMYGDALA_STATE_FILE + ".tmp"
            os.makedirs(os.path.dirname(AMYGDALA_STATE_FILE) or ".", exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, AMYGDALA_STATE_FILE)
        except Exception as e:
            logger.warning(f"[AMYGDALA] Save échoué: {e}")

    def _load(self):
        """Charge l'état depuis le disque."""
        if not os.path.exists(AMYGDALA_STATE_FILE):
            return
        try:
            with open(AMYGDALA_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._extinction_cycle = data.get("extinction_cycle", 0)
            raw_memories = data.get("memories", {})
            for key, val in raw_memories.items():
                # Migration depuis ThreatMemory (rétrocompatibilité)
                if "severity" in val and "valence" not in val:
                    val = {
                        "pattern": val.get("pattern", key),
                        "valence": -min(1.0, val["severity"] / 10.0),
                        "arousal": min(1.0, val["severity"] / 8.0),
                        "occurrences": val.get("occurrences", 1),
                        "last_seen": val.get("last_seen", time.time()),
                        "conditioned_reflex": val.get("conditioned_reflex", ""),
                        "extinction_counter": 0,
                    }
                try:
                    self.memories[key] = EmotionalMemory(**val)
                except TypeError:
                    logger.debug(f"[AMYGDALA] Mémoire ignorée (format invalide): {key}")
        except Exception as e:
            logger.warning(f"[AMYGDALA] Load échoué: {e}")


# ── Singleton ───────────────────────────────────────────────────────

amygdala = Amygdala()
