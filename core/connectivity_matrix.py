"""
Connectivity Matrix — Matrice de connectivite inter-organes dynamique.

Phase 3 Signal Bus. Inspire de la connectivity matrix du connectome
FlyWire (Eon Systems) : chaque connexion inter-organes a un poids
qui evolue avec l'usage (plasticite Hebbienne).

Les organes communiquent par broadcast sur le bus. La matrice ajoute
une couche de modulation : certaines liaisons deviennent des autoroutes
(poids eleve), d'autres s'affaiblissent (poids faible).

Singleton persistant. 0 appel LLM.
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("ConnectivityMatrix")

# --- Constantes ---
MATRIX_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "connectivity_matrix.json"
)

# Plasticite
HEBBIAN_STRENGTHEN_RATE = 0.02   # Renforcement sur co-activation reussie
HEBBIAN_WEAKEN_RATE = 0.01       # Affaiblissement sur echec
WEIGHT_MIN = 0.05                # Plancher (connexion jamais totalement coupee)
WEIGHT_MAX = 1.0                 # Plafond
WEIGHT_DECAY_PER_DAY = 0.005     # Decay naturel (0.5%/jour)
HOMEOSTATIC_TARGET = 0.5         # Poids moyen cible
HOMEOSTATIC_RATE = 0.02          # Vitesse de correction homeostatique

# Liste des organes du systeme
ORGANS = [
    "cardiac", "desire", "reptilian", "prefrontal", "dopamine",
    "thalamus", "amygdala", "hippocampus", "corpus_callosum",
    "inner_voice", "synaptic", "insula", "cingulate",
    "basal_ganglia", "hypothalamus", "circadian", "sensorium",
    "default_mode", "curiosity", "tissue",
]

# Connexions initiales basees sur les bridges existants du corpus callosum
# et les cablages des Pistes 1-7 bio-inspirees
_INITIAL_CONNECTIONS: List[Tuple[str, str, float, str]] = [
    # (source, target, weight, type)
    # Bridges corpus callosum (hardcoded, maintenant formalises)
    ("reptilian", "desire", 0.9, "inhibitory"),       # Bridge #2 crisis
    ("cardiac", "desire", 0.7, "excitatory"),          # Bridge #3+#6 flow
    ("dopamine", "desire", 0.6, "excitatory"),         # Bridge #4 creative
    ("dopamine", "cardiac", 0.8, "excitatory"),        # Bridge #7 eureka
    ("synaptic", "inner_voice", 0.5, "excitatory"),    # Bridge #5
    # Cablages Piste 1 (SensoriumLoop)
    ("cardiac", "thalamus", 0.8, "excitatory"),        # Emotion → saillance
    ("thalamus", "cardiac", 0.7, "excitatory"),        # Regle → marqueur somatique
    # Connexions scoring (top contributors)
    ("dopamine", "prefrontal", 0.6, "excitatory"),     # Motivation → focus
    ("prefrontal", "basal_ganglia", 0.7, "excitatory"),# Focus → habitudes
    ("reptilian", "amygdala", 0.9, "excitatory"),      # Menace → peur
    ("amygdala", "cardiac", 0.8, "excitatory"),        # Peur → acceleration cardiaque
    ("cardiac", "corpus_callosum", 0.7, "excitatory"), # Emotion → coherence
    ("desire", "dopamine", 0.6, "excitatory"),         # Pulsion → motivation
    ("hippocampus", "synaptic", 0.5, "excitatory"),    # Memoire → associations
    ("thalamus", "prefrontal", 0.6, "excitatory"),     # Attention → deliberation
    ("circadian", "hypothalamus", 0.7, "excitatory"),  # Rythme → homeostase
    ("insula", "cardiac", 0.6, "excitatory"),          # Interoception → cardiaque
    ("cingulate", "prefrontal", 0.5, "excitatory"),    # Conflit → deliberation
    ("tissue", "corpus_callosum", 0.5, "excitatory"),  # Territoire → coherence
    ("curiosity", "desire", 0.6, "excitatory"),        # Reflexe → pulsion CURIOSITE
    ("default_mode", "inner_voice", 0.5, "excitatory"),# Reverie → voix interieure
    ("sensorium", "reptilian", 0.6, "excitatory"),     # Hardware → menace (temp GPU)
    ("corpus_callosum", "prefrontal", 0.5, "excitatory"), # Etat cognitif → focus
]

# Mapping event bus → organe source (pour identifier les co-activations)
_EVENT_ORIGIN_MAP = {
    "CARDIAC_BEAT": "cardiac",
    "CARDIAC_EMOTION_CHANGE": "cardiac",
    "REPTILIAN_ALERT": "reptilian",
    "REPTILIAN_STATE": "reptilian",
    "DOPAMINE_SURGE": "dopamine",
    "DOPAMINE_DIP": "dopamine",
    "PREFRONTAL_GOAL_COMPLETE": "prefrontal",
    "PREFRONTAL_GOAL_CREATED": "prefrontal",
    "THALAMUS_RULE_LEARNED": "thalamus",
    "THALAMUS_SALIENCE": "thalamus",
    "INNER_VOICE_BROADCAST": "inner_voice",
    "KNOWLEDGE_GAP_DETECTED": "curiosity",
    "TISSUE_PATTERN_EMERGED": "tissue",
    "CIRCADIAN_PHASE_CHANGE": "circadian",
    "HYPOTHALAMUS_COOLDOWN": "hypothalamus",
    "SENSORIUM_UPDATE": "sensorium",
    "HIPPOCAMPUS_ARC_CREATED": "hippocampus",
    "SYNAPTIC_UPDATE": "synaptic",
    "SENSORIUM_FEEDBACK": "cardiac",  # Le feedback est multi-organe, cardiac comme proxy
}


def _conn_key(src: str, tgt: str) -> str:
    """Cle unique pour une connexion orientee."""
    return f"{src}->{tgt}"


class ConnectivityMatrix:
    """Matrice de connectivite inter-organes avec plasticite Hebbienne."""

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

        # Matrice sparse : {key: Connection}
        self.connections: Dict[str, Dict[str, Any]] = {}
        self._stats = {
            "total_strengthens": 0,
            "total_weakens": 0,
            "total_decays": 0,
            "homeostasis_applied": 0,
        }
        self._last_decay_time = time.time()
        self._subscribed = False

        self._load()

        # Initialiser les connexions par defaut si matrice vide
        if not self.connections:
            self._init_default_connections()

        self._subscribe_events()

    def _init_default_connections(self):
        """Initialise la matrice avec les connexions par defaut."""
        for src, tgt, weight, conn_type in _INITIAL_CONNECTIONS:
            key = _conn_key(src, tgt)
            self.connections[key] = {
                "src": src,
                "tgt": tgt,
                "weight": weight,
                "type": conn_type,
                "usage_count": 0,
                "last_activated": 0.0,
                "created_at": time.time(),
            }
        logger.info(f"CONNECTIVITY: Matrice initialisee avec {len(self.connections)} connexions.")

    def _subscribe_events(self):
        """Souscription au bus pour la plasticite."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("SENSORIUM_FEEDBACK", self._on_sensorium_feedback)
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
        except Exception as e:
            logger.warning(f"CONNECTIVITY: Echec souscription bus: {e}")

    # ============================================================
    # API publique
    # ============================================================

    def get_weight(self, src: str, tgt: str) -> float:
        """Retourne le poids de la connexion src→tgt (0.0 si inexistante)."""
        key = _conn_key(src, tgt)
        conn = self.connections.get(key)
        return conn["weight"] if conn else 0.0

    def get_connection(self, src: str, tgt: str) -> Optional[Dict]:
        """Retourne la connexion complete ou None."""
        return self.connections.get(_conn_key(src, tgt))

    def get_neighbors(self, organ: str) -> List[Dict]:
        """Retourne les voisins sortants d'un organe avec leurs poids."""
        result = []
        prefix = f"{organ}->"
        for key, conn in self.connections.items():
            if key.startswith(prefix):
                result.append(conn)
        result.sort(key=lambda c: c["weight"], reverse=True)
        return result

    def get_signal_weight(self, event_type: str, receiver: str) -> float:
        """Signal Bridge : retourne le poids de connexion pour un signal recu.

        Deduit l'organe source via _EVENT_ORIGIN_MAP et retourne le poids
        de la connexion source→receiver. Les organes appellent cette methode
        dans leurs handlers pour moduler leur reaction.

        Retourne 1.0 si la connexion n'existe pas (pas de modulation = fallback).
        """
        source = _EVENT_ORIGIN_MAP.get(event_type, "")
        if not source or source == receiver:
            return 1.0
        weight = self.get_weight(source, receiver)
        # Si pas de connexion explicite, pas de modulation
        return weight if weight > 0.0 else 1.0

    def get_matrix_summary(self) -> Dict[str, Any]:
        """Resume de la matrice pour API/dashboard."""
        if not self.connections:
            return {"connections": 0, "avg_weight": 0.0, "stats": self._stats}

        weights = [c["weight"] for c in self.connections.values()]
        top_5 = sorted(
            self.connections.values(),
            key=lambda c: c["weight"], reverse=True,
        )[:5]

        return {
            "connections": len(self.connections),
            "avg_weight": round(sum(weights) / len(weights), 3),
            "min_weight": round(min(weights), 3),
            "max_weight": round(max(weights), 3),
            "top_connections": [
                {"src": c["src"], "tgt": c["tgt"], "weight": round(c["weight"], 3),
                 "type": c["type"], "usage": c["usage_count"]}
                for c in top_5
            ],
            "stats": self._stats,
        }

    # ============================================================
    # Plasticite Hebbienne
    # ============================================================

    def strengthen(self, src: str, tgt: str, amount: float = None):
        """Renforce la connexion src→tgt (Hebbian : fire together, wire together)."""
        delta = amount if amount is not None else HEBBIAN_STRENGTHEN_RATE
        key = _conn_key(src, tgt)
        if key not in self.connections:
            # Creer la connexion si elle n'existe pas
            if src in ORGANS and tgt in ORGANS:
                self.connections[key] = {
                    "src": src, "tgt": tgt,
                    "weight": WEIGHT_MIN,
                    "type": "excitatory",
                    "usage_count": 0,
                    "last_activated": 0.0,
                    "created_at": time.time(),
                }
            else:
                return

        conn = self.connections[key]
        conn["weight"] = min(WEIGHT_MAX, conn["weight"] + delta)
        conn["usage_count"] += 1
        conn["last_activated"] = time.time()
        self._stats["total_strengthens"] += 1

    def weaken(self, src: str, tgt: str, amount: float = None):
        """Affaiblit la connexion src→tgt (anti-Hebbian)."""
        delta = amount if amount is not None else HEBBIAN_WEAKEN_RATE
        key = _conn_key(src, tgt)
        if key not in self.connections:
            return
        conn = self.connections[key]
        conn["weight"] = max(WEIGHT_MIN, conn["weight"] - delta)
        self._stats["total_weakens"] += 1

    def decay_all(self):
        """Decay naturel de toutes les connexions (appele periodiquement)."""
        now = time.time()
        days_elapsed = (now - self._last_decay_time) / 86400
        if days_elapsed < 0.01:  # Pas de decay si < ~15 min
            return

        decay = WEIGHT_DECAY_PER_DAY * days_elapsed
        for conn in self.connections.values():
            conn["weight"] = max(WEIGHT_MIN, conn["weight"] - decay)

        self._last_decay_time = now
        self._stats["total_decays"] += 1
        logger.debug(f"CONNECTIVITY: Decay applique ({days_elapsed:.2f} jours, -{decay:.4f})")

    def homeostatic_normalize(self):
        """Normalisation homeostatique — ramene le poids moyen vers la cible."""
        if not self.connections:
            return
        weights = [c["weight"] for c in self.connections.values()]
        avg = sum(weights) / len(weights)
        if abs(avg - HOMEOSTATIC_TARGET) < 0.01:
            return

        # Correction douce vers la cible
        correction = HOMEOSTATIC_RATE * (HOMEOSTATIC_TARGET - avg)
        for conn in self.connections.values():
            conn["weight"] = max(WEIGHT_MIN, min(WEIGHT_MAX,
                conn["weight"] + correction))

        self._stats["homeostasis_applied"] += 1

    # ============================================================
    # Handlers bus (plasticite automatique)
    # ============================================================

    async def _on_sensorium_feedback(self, event: dict):
        """SENSORIUM_FEEDBACK : plasticite post-action basee sur le resultat.

        Quand une routine reussit, on renforce les connexions entre les organes
        qui ont contribue positivement (scoring_breakdown). Sur echec, on affaiblit.
        """
        status = event.get("status", "")
        organ_snapshot = event.get("organ_snapshot", {})

        if not organ_snapshot:
            return

        # Identifier les organes "actifs" dans le snapshot
        active_organs = [
            name for name, data in organ_snapshot.items()
            if data is not None
        ]

        if len(active_organs) < 2:
            return

        # Renforcer les connexions entre organes co-actifs
        if status == "success":
            for i, src in enumerate(active_organs):
                for tgt in active_organs[i + 1:]:
                    self.strengthen(src, tgt, HEBBIAN_STRENGTHEN_RATE * 0.5)
                    self.strengthen(tgt, src, HEBBIAN_STRENGTHEN_RATE * 0.5)
        elif status == "error":
            for i, src in enumerate(active_organs):
                for tgt in active_organs[i + 1:]:
                    self.weaken(src, tgt, HEBBIAN_WEAKEN_RATE * 0.5)
                    self.weaken(tgt, src, HEBBIAN_WEAKEN_RATE * 0.5)

        # Decay periodique + homeostase
        self.decay_all()
        self.homeostatic_normalize()

    async def _on_routine_complete(self, event: dict):
        """AUTONOMY_ROUTINE_COMPLETE : renforcer les connexions des organes contributeurs."""
        breakdown = event.get("scoring_breakdown", {})
        status = event.get("status", "")

        if not breakdown:
            return

        # Les organes qui ont contribue positivement au scoring
        contributors = [
            name for name, bonus in breakdown.items()
            if abs(bonus) > 0.1
        ]

        if status == "success":
            # Renforcer les connexions entre contributeurs positifs
            positive = [n for n in contributors if breakdown.get(n, 0) > 0.1]
            for i, src in enumerate(positive):
                for tgt in positive[i + 1:]:
                    if src in ORGANS and tgt in ORGANS:
                        self.strengthen(src, tgt, HEBBIAN_STRENGTHEN_RATE)

    # ============================================================
    # Persistance
    # ============================================================

    def _load(self):
        """Charge l'etat depuis le fichier JSON."""
        try:
            with open(MATRIX_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.connections = data.get("connections", {})
            self._stats = data.get("stats", self._stats)
            self._last_decay_time = data.get("last_decay_time", time.time())
            logger.info(f"CONNECTIVITY: Etat restaure ({len(self.connections)} connexions).")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        """Sauvegarde l'etat dans le fichier JSON."""
        os.makedirs(os.path.dirname(MATRIX_STATE_FILE), exist_ok=True)
        data = {
            "connections": self.connections,
            "stats": self._stats,
            "last_decay_time": self._last_decay_time,
        }
        tmp = MATRIX_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, MATRIX_STATE_FILE)


# Singleton global
matrix = ConnectivityMatrix()
