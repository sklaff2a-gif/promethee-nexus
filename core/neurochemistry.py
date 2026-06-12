"""
Neurochemistry — Pools neurochimiques globaux de Promethee.

3 neuromodulateurs qui baignent l'ensemble du cerveau et modulent
le comportement de tous les organes a chaque tick :

- Serotonine [0, 1] : patience, inhibition de l'impulsivite
  Monte sur succes, consolidation, sommeil. Baisse sur erreurs, threat.
  Effet : favorise les routines longues et la consolidation.

- Noradrenaline [0, 1] : vigilance, focus attentionnel
  Monte sur menaces, erreurs, alertes codelet danger. Baisse au repos.
  Effet : abaisse les seuils d'attention (thalamus), urgence dans le scoring.

- Acetylcholine [0, 1] : plasticite synaptique, apprentissage
  Monte sur exploration, apprentissage, eureka. Baisse en stagnation.
  Effet : module le taux de plasticite hebbienne et le taux de mutation tissu.

Inspire de la neurochimie reelle. Chaque pool est un float avec decay
naturel vers le baseline (0.5). Singleton. 0 appel LLM.
"""

import logging
import os
import json
from typing import Any, Dict

logger = logging.getLogger("Neurochemistry")

# --- Fichier de persistance ---
NEUROCHEMISTRY_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "neurochemistry_state.json"
)

# --- Constantes ---
BASELINE = 0.5
MIN_LEVEL = 0.05
MAX_LEVEL = 0.95
DECAY_RATE = 0.97   # Retour vers baseline par tick (~30s)

# Impacts par evenement (positif = monte, negatif = descend)
SEROTONIN_IMPACTS = {
    "routine_success": +0.05,
    "routine_failure": -0.08,
    "memory_consolidation": +0.08,
    "nap_start": +0.10,
    "threat_high": -0.06,
    "error_streak": -0.10,
    "council_consensus": +0.04,
}

NORADRENALINE_IMPACTS = {
    "threat_high": +0.12,
    "threat_moderate": +0.06,
    "codelet_danger": +0.15,
    "error_streak": +0.08,
    "routine_failure": +0.05,
    "calm_period": -0.04,
    "nap_start": -0.10,
    "routine_success": -0.02,
}

ACETYLCHOLINE_IMPACTS = {
    "exploration": +0.08,
    "learning_success": +0.10,
    "eureka": +0.15,              # Renforce de +0.12 a +0.15 (concu par Promethee)
    "codelet_novelty": +0.06,
    "stagnation": -0.08,
    "nap_start": -0.06,
    "routine_success": +0.03,
    "SCHOOL_GRADE_HIGH": +0.10,   # Note scolaire >= 8.0 (concu par Promethee)
    "SPREADING_ACTIVATION": +0.05, # Activation reseau synaptique (concu par Promethee)
}


class Neurochemistry:
    """Singleton — Pools neurochimiques globaux."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.serotonin: float = BASELINE
        self.noradrenaline: float = BASELINE
        self.acetylcholine: float = BASELINE

        self._tick_count: int = 0
        self._subscribed: bool = False

        self._load()
        self._subscribe_events()

    def _subscribe_events(self):
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("CODELET_ALERT", self._on_codelet_alert)
            bus.subscribe("NAP_MODE", self._on_nap_mode)
            bus.subscribe("EUREKA_BRIDGE", self._on_eureka)
            # 12/06 — raccordement aux events REELS : COUNCIL_CONSENSUS et
            # SCHOOL_GRADE_HIGH n'etaient JAMAIS publies (3 handlers morts =
            # 3 voies de recompense neurochimique inactives). Les events reels
            # sont COUNCIL_END (status="consensus") et SCHOOL_GRADE_RECEIVED.
            bus.subscribe("COUNCIL_END", self._on_council_consensus)
            bus.subscribe("SCHOOL_GRADE_RECEIVED", self._on_school_grade_high)
            # SPREADING_ACTIVATION : non rebranche — l'activation laterale est
            # tres frequente (chaque recall), un boost ACh la-dessus spammerait
            # le pool. Handler retire plutot que faux-cable.
        except Exception as e:
            logger.warning(f"NEUROCHIMIE: Echec souscription bus: {e}")

    # ============================================================
    # Tick — decay vers baseline
    # ============================================================

    async def _on_cardiac_beat(self, event: dict):
        """A chaque battement, les pools decroissent vers le baseline."""
        self._tick_count += 1
        self.serotonin = self._decay(self.serotonin)
        self.noradrenaline = self._decay(self.noradrenaline)
        self.acetylcholine = self._decay(self.acetylcholine, pool_name="acetylcholine")

        # Persister toutes les 20 ticks (~10 min)
        if self._tick_count % 20 == 0:
            self.save()

    def _decay(self, level: float, pool_name: str = None) -> float:
        """Decay vers baseline avec clamp. ACh a un decay plus lent (0.98)."""
        if pool_name == "acetylcholine":
            rate = 0.98  # Persiste plus longtemps (concu par Promethee)
        else:
            rate = DECAY_RATE
        new = BASELINE + (level - BASELINE) * rate
        return max(MIN_LEVEL, min(MAX_LEVEL, new))

    # ============================================================
    # Handlers bus
    # ============================================================

    async def _on_routine_complete(self, event: dict):
        status = event.get("status", "")
        intent = event.get("intent", "")
        if status == "success":
            self._apply("serotonin", SEROTONIN_IMPACTS["routine_success"])
            self._apply("noradrenaline", NORADRENALINE_IMPACTS["routine_success"])
            self._apply("acetylcholine", ACETYLCHOLINE_IMPACTS["routine_success"])
            # Exploration boost acetylcholine
            if intent in ("VEILLE_SILENCIEUSE", "VEILLE_IA", "ROADMAP_RESEARCH",
                          "DROPZONE_SCAN", "CREATIVE_PLAY"):
                self._apply("acetylcholine", ACETYLCHOLINE_IMPACTS["exploration"])
            # Consolidation boost serotonine
            if intent in ("MEMORY_CONSOLIDATION", "MEMORY_CLEANUP"):
                self._apply("serotonin", SEROTONIN_IMPACTS["memory_consolidation"])
        elif status == "error":
            self._apply("serotonin", SEROTONIN_IMPACTS["routine_failure"])
            self._apply("noradrenaline", NORADRENALINE_IMPACTS["routine_failure"])

    async def _on_reptilian_alert(self, event: dict):
        threat = event.get("threat_level", 0)
        if threat >= 6:
            self._apply("noradrenaline", NORADRENALINE_IMPACTS["threat_high"])
            self._apply("serotonin", SEROTONIN_IMPACTS["threat_high"])
        elif threat >= 3:
            self._apply("noradrenaline", NORADRENALINE_IMPACTS["threat_moderate"])

    async def _on_codelet_alert(self, event: dict):
        name = event.get("name", "")
        if name == "danger":
            self._apply("noradrenaline", NORADRENALINE_IMPACTS["codelet_danger"])
        elif name == "novelty":
            self._apply("acetylcholine", ACETYLCHOLINE_IMPACTS["codelet_novelty"])
        elif name == "stagnation":
            self._apply("acetylcholine", ACETYLCHOLINE_IMPACTS["stagnation"])

    async def _on_nap_mode(self, event: dict):
        entering = event.get("entering", False)
        if entering:
            self._apply("serotonin", SEROTONIN_IMPACTS["nap_start"])
            self._apply("noradrenaline", NORADRENALINE_IMPACTS["nap_start"])
            self._apply("acetylcholine", ACETYLCHOLINE_IMPACTS["nap_start"])

    async def _on_eureka(self, event: dict):
        self._apply("acetylcholine", ACETYLCHOLINE_IMPACTS["eureka"])

    async def _on_council_consensus(self, event: dict):
        # COUNCIL_END porte status="consensus"|"timeout" : ne recompenser que le consensus
        if event.get("status") == "consensus":
            self._apply("serotonin", SEROTONIN_IMPACTS["council_consensus"])

    async def _on_school_grade_high(self, event: dict):
        """Note scolaire >= 8.0 → boost ACh (concu par Promethee).

        SCHOOL_GRADE_RECEIVED porte 'grade' (cf professor_agent._feed_desire_engine).
        """
        grade = event.get("grade", 0.0)
        if grade >= 8.0:
            self._apply("acetylcholine", ACETYLCHOLINE_IMPACTS["SCHOOL_GRADE_HIGH"])

    # ============================================================
    # Modulation
    # ============================================================

    def _apply(self, pool: str, delta: float):
        """Applique un delta a un pool avec clamp."""
        current = getattr(self, pool, BASELINE)
        new = max(MIN_LEVEL, min(MAX_LEVEL, current + delta))
        setattr(self, pool, new)

    def get_modulation(self) -> Dict[str, float]:
        """Retourne les facteurs de modulation pour les organes.

        Chaque facteur est dans [0.5, 1.5] — multiplie les bonus existants.
        """
        return {
            "patience": 0.5 + self.serotonin,          # [0.55, 1.45]
            "urgency": 0.5 + self.noradrenaline,       # [0.55, 1.45]
            "plasticity": 0.5 + self.acetylcholine,     # [0.55, 1.45]
        }

    def get_attention_threshold_modifier(self) -> float:
        """Modifie le seuil d'attention du thalamus.

        Noradrenaline haute → seuil bas (plus d'info passe).
        Retourne un facteur [0.6, 1.2] a multiplier au seuil.
        """
        # Noradrenaline haute = seuil bas (inverse)
        return max(0.6, min(1.2, 1.5 - self.noradrenaline))

    def get_plasticity_rate(self) -> float:
        """Facteur de plasticite pour le reseau synaptique.

        Acetylcholine haute → plasticite augmentee.
        Retourne un facteur [0.5, 2.0].
        """
        return max(0.5, min(2.0, self.acetylcholine * 2.5))

    # ============================================================
    # API
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        return {
            "serotonin": round(self.serotonin, 3),
            "noradrenaline": round(self.noradrenaline, 3),
            "acetylcholine": round(self.acetylcholine, 3),
            "modulation": self.get_modulation(),
            "attention_modifier": round(self.get_attention_threshold_modifier(), 3),
            "plasticity_rate": round(self.get_plasticity_rate(), 3),
            "tick_count": self._tick_count,
        }

    # ============================================================
    # Persistance
    # ============================================================

    def _load(self):
        try:
            with open(NEUROCHEMISTRY_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.serotonin = data.get("serotonin", BASELINE)
            self.noradrenaline = data.get("noradrenaline", BASELINE)
            self.acetylcholine = data.get("acetylcholine", BASELINE)
            self._tick_count = data.get("tick_count", 0)
            logger.info(f"NEUROCHIMIE: Etat restaure (S={self.serotonin:.2f} "
                        f"NA={self.noradrenaline:.2f} ACh={self.acetylcholine:.2f})")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(NEUROCHEMISTRY_STATE_FILE), exist_ok=True)
        data = {
            "serotonin": self.serotonin,
            "noradrenaline": self.noradrenaline,
            "acetylcholine": self.acetylcholine,
            "tick_count": self._tick_count,
        }
        tmp = NEUROCHEMISTRY_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, NEUROCHEMISTRY_STATE_FILE)


# Singleton global
neurochemistry = Neurochemistry()
