# core/synaptic_network.py — Cortex Associatif : Memoire Synaptique Multi-Dimensionnelle
# Graphe synaptique persistant avec apprentissage Hebbien, resonance emotionnelle,
# cascades oscillatoires et mode reve (consolidation stochastique).

import asyncio
import hashlib
import json
import logging
import math
import os
import random
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SynapticNetwork")

# --- Constantes ---

MAX_NODES = 5000
MAX_SYNAPSES = 20000
HEBBIAN_LEARNING_RATE = 0.08
ANTI_HEBBIAN_RATE = 0.03

# --- Hebbian causal V3 (Phase C Etape 3, 2026-04-14) ---
# Constantes pour la regle V3 : apprentissage par pointeurs causaux
# (PREFRONTAL_GOAL_COMPLETE mode=homeostatic + PREFRONTAL_GOAL_ABANDONED
#  completion_mode=abandoned_fruitless). Voir docs/phase_c_etape_3_hebbian_causal.md
# Design valide par Gemini 2026-04-14 : ces valeurs sont les verdicts du trio.
HEBBIAN_CAUSAL_LEARNING_RATE = 0.10    # Cap max par event positif (Gemini Q4: "parfait")
HEBBIAN_CAUSAL_EXTINCTION_DELTA = 0.03 # Penalite uniforme par intent (Gemini Q1: EGA)
HEBBIAN_CAUSAL_EXTINCTION_FLOOR = 0.0  # Plancher strict (Gemini Q2: 0.0, pas 0.01)
HEBBIAN_CAUSAL_DROP_CAP = 100.0        # Cap normalisation causal_drop
HEBBIAN_CAUSAL_KNOWN_DRIVES = frozenset([
    "CURIOSITE", "MAITRISE", "STABILITE", "CONNEXION",
    "CROISSANCE", "CREATION", "COMPREHENSION",
])
SPIKE_TIMING_WINDOW = 300.0       # 5 min pour causalite temporelle
HOMEOSTATIC_TARGET = 0.3
SYNAPSE_DECAY_PER_DAY = 0.02
PRUNING_THRESHOLD = 0.08
MAX_PRUNE_RATIO = 0.05            # Max 5% du réseau purgé par dream (fallback)
ADAPTIVE_PRUNE_RATIO = 0.98       # Pruning adaptatif : 98% du taux de création
MIN_CONCEPT_LENGTH = 3            # Rejeter les concepts trop courts (bruit)
RESONANCE_CYCLES = 4
STDP_MULTIPLIER = 1.5             # STDP 1.5x plus fort que Hebb classique
STDP_BUFFER_SIZE = 15             # Taille du buffer STDP (était 50, réduit pour limiter le bruit)

# --- Modele LIF neuronal (Piste 6 bio-inspired) ---
# Inspire d'Eon Systems : quand un noeud accumule assez d'energie, il "fire"
# et propage son activation aux noeuds connectes via les synapses ponderees.
NEURON_FIRE_THRESHOLD = 0.85      # Seuil de fire (energie haute)
NEURON_RESET_ENERGY = 0.3         # Reset a HOMEOSTATIC_TARGET apres fire
NEURON_PROPAGATION_FACTOR = 0.15  # Energie transmise aux voisins (reduit pour eviter tempetes)
NEURON_MAX_CASCADE_DEPTH = 2      # Max profondeur de cascade (reduit de 3)
NEURON_MAX_PROPAGATION = 8        # Max voisins propages par fire (evite les hubs)
NEURON_MIN_SYNAPSE_WEIGHT = 0.3   # Seuil synapse pour propagation (reduit bruit)

# --- Plasticite structurelle (inspire NEST) ---
# Les noeuds co-actifs non-connectes font "pousser" des synapses spontanement.
STRUCTURAL_GROWTH_THRESHOLD = 0.6  # Energie min pour etre "actif" (candidat croissance)
STRUCTURAL_GROWTH_MAX_PER_TICK = 3 # Max nouvelles synapses par tick
STRUCTURAL_GROWTH_INITIAL_WEIGHT = 0.05  # Poids initial (faible, doit etre renforce par Hebbian)
STRUCTURAL_GROWTH_FILL_LIMIT = 0.9  # Ne pas faire pousser si synapses > 90% de MAX

# --- Budget poids sortants (inspire AttnRes, Moonshot AI mars 2026) ---
# Chaque noeud a un budget total de poids sortants. Renforcer A→B
# affaiblit proportionnellement les autres sorties de A (competition synaptique).
# Biologiquement : ressources synaptiques limitees, renforcer = choisir.
OUTGOING_WEIGHT_BUDGET = 3.0  # Budget total max des poids sortants par noeud

# Noeuds système exclus du STDP — ces noeuds sont activés à chaque cycle
# par les organes internes et créent du bruit auto-référentiel massif.
_STDP_EXCLUDED_PREFIXES = frozenset({
    "dmn", "synaptic", "desire", "reptilian", "cardiac",
    "reflex:", "trait:", "pulsion:", "zone:",
})

# Noeuds bruit rejetes par ensure_node() — artefacts filesystem et mots-béquilles LLM
_NODE_STOPLIST = frozenset({
    # Artefacts techniques
    "__pycache__", "node_modules", ".git", "venv", "__init__",
    # Mots vides français (aucune valeur sémantique dans le réseau)
    "okay", "suis", "juste", "vraiment", "d'accord",
    "dossiers", "fichiers", "répertoire", "résultats",
    "partir", "groupes", "permet", "faire", "comme", "aussi",
    "cette", "entre", "dans", "avec", "pour", "plus",
    "tout", "tous", "très", "bien", "fait", "sont",
    # Mots anglais génériques (erreurs Python, logs techniques)
    "name", "defined", "error", "none", "true", "false",
    "failed", "traceback", "exception", "file", "line",
})

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "synaptic_network.json"
)

# Types de noeuds valides
VALID_NODE_TYPES = frozenset({
    "memory", "desire", "trait", "event", "objective", "eureka", "meta", "affect", "zone"
})

# Types de synapses valides
VALID_SYNAPSE_TYPES = frozenset({
    "hebbian", "anti_hebbian", "temporal", "emotional", "eureka"
})


# --- Helpers ---

def _make_node_id(concept: str) -> str:
    """Hash MD5[:12] deterministe, case-insensitive."""
    return hashlib.md5(concept.strip().lower().encode("utf-8")).hexdigest()[:12]


def _synapse_key(src: str, tgt: str) -> str:
    """Cle unique pour une synapse orientee."""
    return f"{src}->{tgt}"


def _make_node(concept: str, node_type: str = "memory",
               semantic_weight: float = 0.5,
               functional_systems: Optional[List[str]] = None,
               affect: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Cree un noeud synaptique."""
    now = time.time()
    return {
        "id": _make_node_id(concept),
        "concept": concept.strip().lower(),
        "node_type": node_type if node_type in VALID_NODE_TYPES else "memory",
        "affect": affect or {
            "mood": "neutre",
            "dominant_desire": "",
            "desire_intensity": 0.0,
            "dominant_trait": "",
            "trait_value": 50.0,
            "valence": 0.0,
        },
        "dimensions": {
            "semantic_weight": max(0.0, min(1.0, semantic_weight)),
            "emotional_valence": 0.0,
            "temporal_score": 1.0,
            "functional_systems": functional_systems or [],
        },
        "activation_count": 1,
        "last_activated": now,
        "created_at": now,
        "energy": max(0.0, min(1.0, semantic_weight)),
    }


def _make_synapse(source: str, target: str,
                  weight: float = 0.1,
                  synapse_type: str = "hebbian",
                  context: str = "") -> Dict[str, Any]:
    """Cree une synapse entre deux noeuds."""
    now = time.time()
    return {
        "source": source,
        "target": target,
        "weight": max(0.01, min(1.0, weight)),
        "synapse_type": synapse_type if synapse_type in VALID_SYNAPSE_TYPES else "hebbian",
        "formation_count": 1,
        "last_strengthened": now,
        "created_at": now,
        "context": context[:200] if context else "",
    }


# --- Distance emotionnelle ---

def emotional_distance(affect_a: Dict[str, Any], affect_b: Dict[str, Any]) -> float:
    """Distance emotionnelle euclidienne 3D normalisee [0, 1]."""
    v_a = affect_a.get("valence", 0.0)
    v_b = affect_b.get("valence", 0.0)
    d_a = affect_a.get("desire_intensity", 0.0) / 100.0
    d_b = affect_b.get("desire_intensity", 0.0) / 100.0
    t_a = affect_a.get("trait_value", 50.0) / 100.0
    t_b = affect_b.get("trait_value", 50.0) / 100.0

    dist_sq = (v_a - v_b) ** 2 + (d_a - d_b) ** 2 + (t_a - t_b) ** 2
    return min(1.0, math.sqrt(dist_sq) / math.sqrt(3.0))


# --- Classe principale ---

class SynapticNetwork:
    """Cortex Associatif — Graphe synaptique persistant avec apprentissage Hebbien."""

    _instance: Optional["SynapticNetwork"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.nodes: Dict[str, Dict[str, Any]] = {}       # node_id -> node
        self.synapses: Dict[str, Dict[str, Any]] = {}    # synapse_key -> synapse
        self._activation_buffer: List[Tuple[str, float]] = []   # (node_id, timestamp)
        self._mutations_since_save: int = 0
        self._subscribed = False
        self._last_dream_time: float = time.time()  # Pour decay incrémental entre dreams
        self._last_routine_node: str = ""  # Dernier noeud routine (pour associations sensorium)
        self._suppress_deltas: bool = False  # Batch mode : supprime les deltas individuels
        self._pending_deltas: List[dict] = []  # Deltas accumules en mode batch
        self._load()

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux evenements bus et injecte les noeuds organes."""
        self._subscribe_events()
        self._seed_organ_nodes()
        logger.info(
            f"SYNAPSE: Cortex associatif actif "
            f"({len(self.nodes)} noeuds, {len(self.synapses)} synapses)."
        )

    def reset(self):
        """Reset complet (utilise par les tests)."""
        self.nodes = {}
        self.synapses = {}
        self._activation_buffer = []
        self._mutations_since_save = 0
        self._subscribed = False
        self._last_dream_time = time.time()
        self._last_routine_node = ""
        self._initialized = False

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilise par les tests)."""
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # --- Purge bruit ---

    def purge_noise_nodes(self) -> dict:
        """Supprime les noeuds dont le concept est dans _NODE_STOPLIST.

        Supprime aussi les synapses orphelines (source ou target supprime).
        Publie node_removed pour chaque noeud purge (neural_tissue recoit le signal).
        Retourne {"purged_nodes": int, "purged_synapses": int}.
        """
        purged_nodes = 0
        purged_synapses = 0

        # 1. Identifier et supprimer les noeuds bruit
        to_remove = [
            nid for nid, node in self.nodes.items()
            if node.get("concept", "").lower() in _NODE_STOPLIST
        ]
        for nid in to_remove:
            concept = self.nodes[nid].get("concept", "")
            del self.nodes[nid]
            purged_nodes += 1
            self._publish_delta("node_removed", {"id": nid, "concept": concept})

        # 2. Supprimer les synapses orphelines
        if purged_nodes > 0:
            removed_ids = set(to_remove)
            orphan_keys = [
                key for key, syn in self.synapses.items()
                if syn["source"] in removed_ids or syn["target"] in removed_ids
            ]
            for key in orphan_keys:
                del self.synapses[key]
                purged_synapses += 1

            self._mutations_since_save += purged_nodes + purged_synapses
            self._auto_save()

        return {"purged_nodes": purged_nodes, "purged_synapses": purged_synapses}

    # --- Publication delta temps reel ---

    def _publish_delta(self, change_type: str, data: dict):
        """Publie un delta SYNAPTIC_UPDATE via le bus (non-bloquant).
        Si _suppress_deltas est actif, accumule dans _pending_deltas."""
        if self._suppress_deltas:
            self._pending_deltas.append({"change": change_type, **data})
            return
        try:
            loop = asyncio.get_running_loop()
            from core.event_bus.bus import bus
            loop.create_task(bus.publish("SYNAPTIC_UPDATE", {"change": change_type, **data}))
        except RuntimeError:
            pass  # Pas de boucle asyncio (tests)

    async def _flush_deltas(self):
        """Publie tous les deltas accumules en un seul SYNAPTIC_BATCH."""
        if not self._pending_deltas:
            return
        batch = self._pending_deltas[:]
        self._pending_deltas = []
        try:
            from core.event_bus.bus import bus
            await bus.publish("SYNAPTIC_BATCH", {
                "count": len(batch),
                "deltas": batch[:20],  # Limiter le payload (resume)
            })
        except Exception:
            pass

    # --- Capture d'affect ---

    def _capture_affect_signature(self) -> Dict[str, Any]:
        """Capture mood/desires/traits actuels (imports locaux + try/except)."""
        affect = {
            "mood": "neutre",
            "dominant_desire": "",
            "desire_intensity": 0.0,
            "dominant_trait": "",
            "trait_value": 50.0,
            "valence": 0.0,
        }

        # Mood depuis self_awareness
        try:
            from core.self_awareness import awareness
            if awareness._snapshots:
                last_snap = awareness._snapshots[-1]
                affect["mood"] = last_snap.get("mood", "neutre")
                # Valence depuis success_rate
                sr = last_snap.get("success_rate", 0.5)
                affect["valence"] = round((sr - 0.5) * 2, 2)  # [0,1] -> [-1, +1]
        except Exception:
            pass

        # Desirs dominants
        try:
            from core.desire_engine import desires
            dominant = max(desires.drives.values(), key=lambda d: d.deprivation)
            affect["dominant_desire"] = dominant.name
            affect["desire_intensity"] = round(dominant.deprivation, 1)
        except Exception:
            pass

        # Trait dominant
        try:
            from core.psyche import psyche
            avg = psyche.get_system_average()
            if avg:
                dominant_trait = max(avg, key=avg.get)
                affect["dominant_trait"] = dominant_trait
                affect["trait_value"] = round(avg[dominant_trait], 1)
        except Exception:
            pass

        return affect

    # --- Gestion des noeuds ---

    def ensure_node(self, concept: str, node_type: str = "memory",
                    semantic_weight: float = 0.5,
                    functional_systems: Optional[List[str]] = None) -> str:
        """Cree ou met a jour un noeud. Retourne le node_id (vide si concept rejete)."""
        cleaned = concept.strip()
        if len(cleaned) < MIN_CONCEPT_LENGTH:
            return ""
        if cleaned.lower() in _NODE_STOPLIST:
            return ""
        node_id = _make_node_id(concept)

        if node_id in self.nodes:
            node = self.nodes[node_id]
            node["activation_count"] += 1
            node["last_activated"] = time.time()
            # Boost energy avec decroissance
            node["energy"] = min(1.0, node["energy"] + 0.1)
            # Update dimensions si fourni
            if functional_systems:
                existing = set(node["dimensions"].get("functional_systems", []))
                existing.update(functional_systems)
                node["dimensions"]["functional_systems"] = list(existing)
            # Refresh temporal_score
            age_hours = (time.time() - node["created_at"]) / 3600
            recency = math.exp(-age_hours / 168)  # demi-vie 1 semaine
            node["dimensions"]["temporal_score"] = round(
                recency * min(3.0, 1.0 + math.log1p(node["activation_count"]) * 0.3), 2
            )
            self._publish_delta("node_activate", {
                "id": node_id, "concept": node["concept"],
                "energy": round(node["energy"], 3),
                "activation": node["activation_count"],
            })
        else:
            affect = self._capture_affect_signature()
            node = _make_node(concept, node_type, semantic_weight,
                              functional_systems, affect)
            self.nodes[node_id] = node
            self._enforce_node_limit()
            self._publish_delta("node_new", {
                "id": node_id, "concept": node["concept"],
                "type": node["node_type"],
                "energy": round(node["energy"], 3),
            })

        # LIF neuronal : fire si l'energie depasse le seuil
        # Budget global : max 5 fires par 30s pour eviter les tempetes
        if node_id in self.nodes and self.nodes[node_id]["energy"] >= NEURON_FIRE_THRESHOLD:
            now = time.time()
            if not hasattr(self, "_lif_fire_budget"):
                self._lif_fire_budget = {"count": 0, "window_start": now}
            budget = self._lif_fire_budget
            # Reset le budget toutes les 30s
            if now - budget["window_start"] >= 30.0:
                budget["count"] = 0
                budget["window_start"] = now
            if budget["count"] < 5:
                budget["count"] += 1
                self._lif_fire(node_id, depth=0)
            else:
                # Budget epuise : reset l'energie sans fire
                self.nodes[node_id]["energy"] = NEURON_RESET_ENERGY

        self._record_activation(node_id)
        self._mutations_since_save += 1
        self._auto_save()
        return node_id

    def _lif_fire(self, node_id: str, depth: int = 0):
        """LIF neuronal : le noeud fire et propage aux voisins.

        Inspire d'Eon Systems (mouche drosophile) : quand un noeud accumule
        assez d'energie, il fire, propage aux voisins via les poids synaptiques,
        puis reset son energie (periode refractaire).
        """
        if depth >= NEURON_MAX_CASCADE_DEPTH:
            return
        if getattr(self, "_lif_firing", False):
            return  # Anti-reentrance
        node = self.nodes.get(node_id)
        if not node:
            return

        self._lif_firing = True
        try:
            fired_energy = node["energy"]
            concept = node.get("concept", node_id[:8])

            # RESET : periode refractaire
            node["energy"] = NEURON_RESET_ENERGY

            # COLLECTER les voisins eligibles (synapses fortes seulement)
            candidates = []
            prefix_out = f"{node_id}->"
            for key, syn in self.synapses.items():
                if syn["weight"] < NEURON_MIN_SYNAPSE_WEIGHT:
                    continue
                if key.startswith(prefix_out):
                    neighbor_id = key[len(prefix_out):]
                elif key.endswith(f"->{node_id}"):
                    neighbor_id = key.split("->", 1)[0]
                else:
                    continue
                if neighbor_id in self.nodes:
                    candidates.append((neighbor_id, syn["weight"]))

            # TRIER par poids et limiter aux top N
            candidates.sort(key=lambda x: x[1], reverse=True)
            candidates = candidates[:NEURON_MAX_PROPAGATION]

            # PROPAGER aux top voisins
            propagated = 0
            for neighbor_id, weight in candidates:
                neighbor = self.nodes[neighbor_id]
                injection = fired_energy * weight * NEURON_PROPAGATION_FACTOR
                neighbor["energy"] = min(1.0, neighbor["energy"] + injection)
                propagated += 1

            # CASCADE limitee (depth+1, pas de liberation du verrou)
            if depth + 1 < NEURON_MAX_CASCADE_DEPTH:
                for neighbor_id, weight in candidates:
                    neighbor = self.nodes.get(neighbor_id)
                    if neighbor and neighbor["energy"] >= NEURON_FIRE_THRESHOLD:
                        self._lif_firing = False
                        self._lif_fire(neighbor_id, depth + 1)
                        self._lif_firing = True
                        break  # Un seul fire en cascade par niveau

            if propagated > 0:
                logger.debug(
                    f"SYNAPSE LIF: '{concept}' FIRE (e={fired_energy:.2f}) "
                    f"→ {propagated} voisins (depth={depth})"
                )
        finally:
            self._lif_firing = False

    def _enforce_node_limit(self):
        """Supprime les noeuds les moins actifs si > MAX_NODES."""
        if len(self.nodes) <= MAX_NODES:
            return
        # Trier par score composite : energy * activation_count
        scored = sorted(
            self.nodes.items(),
            key=lambda kv: kv[1]["energy"] * kv[1]["activation_count"]
        )
        to_remove = len(self.nodes) - MAX_NODES
        for node_id, _ in scored[:to_remove]:
            # Supprimer aussi les synapses associees
            self._remove_node_synapses(node_id)
            del self.nodes[node_id]
            self._publish_delta("node_removed", {"id": node_id})

    def _enforce_synapse_limit(self):
        """Supprime les synapses les plus faibles si > MAX_SYNAPSES."""
        if len(self.synapses) <= MAX_SYNAPSES:
            return
        sorted_synapses = sorted(
            self.synapses.items(),
            key=lambda kv: kv[1]["weight"]
        )
        to_remove = len(self.synapses) - MAX_SYNAPSES
        for key, _ in sorted_synapses[:to_remove]:
            del self.synapses[key]

    def _remove_node_synapses(self, node_id: str):
        """Supprime toutes les synapses connectees a un noeud."""
        to_remove = [
            key for key, syn in self.synapses.items()
            if syn["source"] == node_id or syn["target"] == node_id
        ]
        for key in to_remove:
            del self.synapses[key]

    # --- Apprentissage Hebbien ---

    def hebbian_strengthen(self, src_id: str, tgt_id: str,
                           success: bool = True, context: str = ""):
        """Apprentissage Hebbien classique avec anti-Hebb sur echec."""
        src = self.nodes.get(src_id)
        tgt = self.nodes.get(tgt_id)
        if not src or not tgt:
            return

        key = _synapse_key(src_id, tgt_id)
        e_src = src["energy"]
        e_tgt = tgt["energy"]

        if success:
            # Renforcement : Dw = lr * E_src * E_tgt * (1 - w)
            is_new = key not in self.synapses
            if is_new:
                syn = _make_synapse(src_id, tgt_id, 0.1, "hebbian", context)
                self.synapses[key] = syn
                self._enforce_synapse_limit()
            syn = self.synapses[key]
            dw = HEBBIAN_LEARNING_RATE * e_src * e_tgt * (1.0 - syn["weight"])
            syn["weight"] = min(1.0, syn["weight"] + dw)
            syn["formation_count"] += 1
            syn["last_strengthened"] = time.time()
            if context and len(context) > len(syn["context"]):
                syn["context"] = context[:200]
            # Competition synaptique : normaliser les poids sortants (AttnRes-inspired)
            self._normalize_outgoing_weights(src_id)
            change = "synapse_new" if is_new else "synapse_strengthen"
            self._publish_delta(change, {
                "source": src_id, "target": tgt_id,
                "weight": round(syn["weight"], 3),
                "type": syn["synapse_type"],
            })
        else:
            # Anti-Hebb : affaiblir seulement (ne cree PAS de nouvelle synapse)
            if key not in self.synapses:
                return
            syn = self.synapses[key]
            dw = ANTI_HEBBIAN_RATE * e_src * e_tgt
            syn["weight"] = max(0.01, syn["weight"] - dw)

        self._mutations_since_save += 1
        self._auto_save()

    def spike_timing_strengthen(self, earlier_id: str, later_id: str,
                                delta_t: float):
        """Spike-Timing-Dependent Plasticity (STDP).

        A active AVANT B → renforcement proportionnel a exp(-dt / tau).
        Fenetre de 5 minutes max. 1.5x plus fort que Hebb classique.
        """
        if delta_t < 0 or delta_t > SPIKE_TIMING_WINDOW:
            return
        if earlier_id not in self.nodes or later_id not in self.nodes:
            return

        # Facteur temporel : plus le delai est court, plus le renforcement est fort
        tau = SPIKE_TIMING_WINDOW / 3.0  # constante de temps ~100s
        temporal_factor = math.exp(-delta_t / tau)

        key = _synapse_key(earlier_id, later_id)
        if key not in self.synapses:
            syn = _make_synapse(earlier_id, later_id, 0.1, "temporal", "STDP")
            self.synapses[key] = syn
            self._enforce_synapse_limit()

        syn = self.synapses[key]
        e_src = self.nodes[earlier_id]["energy"]
        e_tgt = self.nodes[later_id]["energy"]
        dw = HEBBIAN_LEARNING_RATE * STDP_MULTIPLIER * e_src * e_tgt * temporal_factor * (1.0 - syn["weight"])
        syn["weight"] = min(1.0, syn["weight"] + dw)
        syn["formation_count"] += 1
        syn["last_strengthened"] = time.time()
        # Competition synaptique : normaliser les poids sortants (AttnRes-inspired)
        self._normalize_outgoing_weights(earlier_id)
        self._mutations_since_save += 1
        self._auto_save()

    def _normalize_outgoing_weights(self, node_id: str):
        """Normalise les poids sortants d'un noeud pour respecter le budget.

        Inspire AttnRes (Moonshot AI, mars 2026) : renforcer une synapse
        affaiblit proportionnellement les autres — competition synaptique.
        Biologiquement : les ressources synaptiques sont limitees.
        """
        outgoing = [
            (k, s) for k, s in self.synapses.items()
            if s["source"] == node_id
        ]
        if not outgoing:
            return
        total = sum(s["weight"] for _, s in outgoing)
        if total <= OUTGOING_WEIGHT_BUDGET:
            return
        scale = OUTGOING_WEIGHT_BUDGET / total
        for _, s in outgoing:
            s["weight"] = max(PRUNING_THRESHOLD, s["weight"] * scale)

    def homeostatic_normalize(self):
        """Normalisation homeostatique — ramene l'energie moyenne vers HOMEOSTATIC_TARGET."""
        if not self.nodes:
            return
        avg_energy = sum(n["energy"] for n in self.nodes.values()) / len(self.nodes)
        if avg_energy == 0:
            return
        rate = 0.1
        scale = 1.0 + rate * (HOMEOSTATIC_TARGET - avg_energy)
        scale = max(0.8, min(1.2, scale))
        for node in self.nodes.values():
            node["energy"] = max(0.0, min(1.0, node["energy"] * scale))

    # --- Plasticite structurelle (NEST-inspired) ---

    def structural_growth(self) -> int:
        """Plasticite structurelle : les noeuds co-actifs non-connectes
        font pousser des synapses spontanement.

        Inspire de NEST : "cells that fire together WIRE together" —
        pas juste renforcer, mais CREER les connexions manquantes.
        Appele a chaque BRAIN_TICK (30s).

        Retourne le nombre de synapses creees.
        """
        # Guard : si le reseau est quasi-plein, activer le remplacement
        # au lieu de bloquer. Les synapses quasi-mortes (< 0.1) sont remplacees.
        network_full = len(self.synapses) >= int(MAX_SYNAPSES * STRUCTURAL_GROWTH_FILL_LIMIT)
        if network_full:
            # Trouver les synapses les plus faibles pour remplacement
            weak = [(k, s["weight"]) for k, s in self.synapses.items() if s["weight"] < 0.1]
            if not weak:
                return 0  # Pas de synapse remplacable → vraiment plein
            weak.sort(key=lambda x: x[1])
            self._replacement_candidates = [k for k, _ in weak[:STRUCTURAL_GROWTH_MAX_PER_TICK * 2]]
        else:
            self._replacement_candidates = []

        # Seuil dynamique HID (Inhibition Homeostatique Dynamique — concept Promethee)
        # Le signal "creation" des signaux descendants abaisse le seuil :
        # creation=0 → seuil=0.6 (repos), creation=1 → seuil=0.3 (pleine croissance)
        try:
            from core.autonomy_engine import autonomy
            creation_signal = autonomy._compute_descending_signals().get("creation", 0.0)
        except Exception:
            creation_signal = 0.0
        dynamic_threshold = STRUCTURAL_GROWTH_THRESHOLD * (1.0 - creation_signal * 0.7)

        # Collecter les noeuds actifs (energie > seuil dynamique)
        active_nodes = [
            (nid, node) for nid, node in self.nodes.items()
            if node["energy"] >= dynamic_threshold
        ]

        if len(active_nodes) < 2:
            return 0

        # Trier par energie decroissante (les plus actifs en premier)
        active_nodes.sort(key=lambda x: x[1]["energy"], reverse=True)

        # Chercher les paires co-actives non-connectees
        created = 0
        for i in range(len(active_nodes)):
            if created >= STRUCTURAL_GROWTH_MAX_PER_TICK:
                break
            nid_a, node_a = active_nodes[i]
            for j in range(i + 1, len(active_nodes)):
                if created >= STRUCTURAL_GROWTH_MAX_PER_TICK:
                    break
                nid_b, node_b = active_nodes[j]

                # Verifier que la connexion n'existe pas deja
                key_ab = _synapse_key(nid_a, nid_b)
                key_ba = _synapse_key(nid_b, nid_a)
                if key_ab in self.synapses or key_ba in self.synapses:
                    continue

                # Si reseau plein, remplacer une synapse quasi-morte
                if network_full:
                    if not self._replacement_candidates:
                        break  # Plus de candidats au remplacement
                    dead_key = self._replacement_candidates.pop(0)
                    if dead_key in self.synapses:
                        del self.synapses[dead_key]

                # Creer une synapse faible (sera renforcee par Hebbian ou elaguee)
                self.synapses[key_ab] = _make_synapse(nid_a, nid_b,
                    weight=STRUCTURAL_GROWTH_INITIAL_WEIGHT, syn_type="structural")
                created += 1

                concept_a = node_a.get("concept", nid_a[:8])
                concept_b = node_b.get("concept", nid_b[:8])
                logger.debug(
                    f"SYNAPSE GROWTH: '{concept_a}' <-> '{concept_b}' "
                    f"(e={node_a['energy']:.2f}/{node_b['energy']:.2f})"
                    f"{' [REPLACE]' if network_full else ''}"
                )

        if created > 0:
            self._mutations_since_save += created
            self._auto_save()

        return created

    async def _on_brain_tick_growth(self, event: dict):
        """BRAIN_TICK : declencher la plasticite structurelle."""
        try:
            self.structural_growth()
        except Exception as e:
            logger.debug(f"SYNAPSE: Erreur plasticite structurelle: {e}")

    # --- Buffer temporel STDP ---

    def _record_activation(self, node_id: str):
        """Enregistre une activation pour le buffer STDP.
        Les noeuds système (organes internes) sont exclus pour éviter
        le bruit auto-référentiel massif (dmn=6800+ activations)."""
        # Filtrer les noeuds système du STDP
        node = self.nodes.get(node_id)
        if node:
            concept = node.get("concept", "")
            if any(concept.startswith(p) for p in _STDP_EXCLUDED_PREFIXES):
                return

        now = time.time()

        # Renforcer les paires causales avec les activations precedentes
        for prev_id, prev_ts in self._activation_buffer:
            if prev_id == node_id:
                continue
            delta_t = now - prev_ts
            if 0 < delta_t <= SPIKE_TIMING_WINDOW:
                self.spike_timing_strengthen(prev_id, node_id, delta_t)

        self._activation_buffer.append((node_id, now))
        # Limiter le buffer
        if len(self._activation_buffer) > STDP_BUFFER_SIZE:
            self._activation_buffer = self._activation_buffer[-STDP_BUFFER_SIZE:]

    # --- Resonance emotionnelle ---

    def compute_emotional_resonance(self, node_id: str) -> float:
        """Resonance emotionnelle [0.5, 2.0] — mood-congruent memory.

        Distance 0 (etat identique) → 2.0 (boost maximal)
        Distance 1 (etat oppose) → 0.5 (attenuation)
        """
        node = self.nodes.get(node_id)
        if not node:
            return 1.0

        current_affect = self._capture_affect_signature()
        node_affect = node.get("affect", {})
        dist = emotional_distance(node_affect, current_affect)

        # Mapping lineaire : dist 0 → 2.0, dist 1 → 0.5
        return 2.0 - 1.5 * dist

    # --- Cascades de resonance oscillatoires ---

    def resonance_cascade(self, seed_concepts: List[str],
                          cycles: int = RESONANCE_CYCLES) -> List[Tuple[str, float]]:
        """Cascade oscillatoire forward/backward avec amortissement.

        Cycles pairs : FORWARD (source → target)
        Cycles impairs : BACKWARD (target → source)
        Damping *= 0.85 par cycle.
        Resonance emotionnelle appliquee a chaque propagation.
        Retourne les noeuds stables (energy > 0.1) apres convergence, top 20.
        """
        if not seed_concepts or not self.nodes:
            return []

        # Initialiser les energies de cascade
        cascade_energy: Dict[str, float] = {}
        for concept in seed_concepts:
            nid = _make_node_id(concept)
            if nid in self.nodes:
                cascade_energy[nid] = self.nodes[nid]["energy"]

        if not cascade_energy:
            return []

        damping = 1.0
        noise_threshold = 0.05

        for cycle in range(cycles):
            damping *= 0.85
            new_energy: Dict[str, float] = dict(cascade_energy)

            for key, syn in self.synapses.items():
                if cycle % 2 == 0:
                    # FORWARD : source → target
                    src_id, tgt_id = syn["source"], syn["target"]
                else:
                    # BACKWARD : target → source
                    src_id, tgt_id = syn["target"], syn["source"]

                src_e = cascade_energy.get(src_id, 0.0)
                if src_e < noise_threshold:
                    continue

                # Propagation avec resonance emotionnelle
                resonance = self.compute_emotional_resonance(tgt_id)
                propagated = src_e * syn["weight"] * damping * resonance
                if propagated > noise_threshold:
                    new_energy[tgt_id] = new_energy.get(tgt_id, 0.0) + propagated

            cascade_energy = new_energy

        # Filtrer et trier
        results = []
        for nid, energy in cascade_energy.items():
            if energy > 0.1 and nid in self.nodes:
                results.append((self.nodes[nid]["concept"], round(energy, 3)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:20]

    # --- APIs de requete ---

    def mood_congruent_recall(self, concepts: List[str],
                              top_k: int = 10) -> List[Tuple[str, float]]:
        """Rappel etat-dependant : boost les noeuds proches de l'affect actuel."""
        if not concepts or not self.nodes:
            return []

        results = []
        for nid, node in self.nodes.items():
            # Score de base : correspondance avec les concepts requetes
            concept = node["concept"]
            base_score = 0.0
            for q in concepts:
                if q.lower() in concept or concept in q.lower():
                    base_score += 0.5
                nid_q = _make_node_id(q)
                # Verifier les synapses directes
                fwd = _synapse_key(nid_q, nid)
                bwd = _synapse_key(nid, nid_q)
                if fwd in self.synapses:
                    base_score += self.synapses[fwd]["weight"]
                if bwd in self.synapses:
                    base_score += self.synapses[bwd]["weight"] * 0.5

            if base_score > 0:
                resonance = self.compute_emotional_resonance(nid)
                final_score = base_score * resonance * node["energy"]
                results.append((concept, round(final_score, 3)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def query_associations(self, concepts: List[str], top_k: int = 10,
                           use_resonance: bool = True) -> List[Tuple[str, float]]:
        """Requete associative complete : cascade + mood-congruent."""
        if not concepts:
            return []

        if use_resonance and self.synapses:
            return self.resonance_cascade(concepts, RESONANCE_CYCLES)[:top_k]

        # Fallback sans resonance : simple lookup de voisins
        results: Dict[str, float] = {}
        for concept in concepts:
            nid = _make_node_id(concept)
            for key, syn in self.synapses.items():
                if syn["source"] == nid and syn["target"] in self.nodes:
                    tgt = self.nodes[syn["target"]]
                    results[tgt["concept"]] = max(
                        results.get(tgt["concept"], 0.0),
                        syn["weight"] * tgt["energy"]
                    )
                elif syn["target"] == nid and syn["source"] in self.nodes:
                    src = self.nodes[syn["source"]]
                    results[src["concept"]] = max(
                        results.get(src["concept"], 0.0),
                        syn["weight"] * src["energy"] * 0.5
                    )

        sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
        return [(c, round(s, 3)) for c, s in sorted_results[:top_k]]

    def format_for_prompt(self, concepts: List[str],
                          max_chars: int = 300) -> str:
        """Formate les associations pour injection dans un prompt."""
        assocs = self.query_associations(concepts, top_k=8)
        if not assocs:
            return ""

        parts = [f"{c}({s:.2f})" for c, s in assocs if s >= 0.1]
        if not parts:
            return ""

        result = "[ASSOCIATIONS SYNAPTIQUES] " + ", ".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars - 3] + "..."
        return result

    def compute_routine_affinity(self, intent: str) -> float:
        """Bonus [0, +1.5] base sur les associations synaptiques avec l'intent."""
        if not self.nodes or not self.synapses:
            return 0.0

        # Mots-cles par intent
        intent_keywords = {
            "EXPANSION_CODE": ["code", "optimiser", "refactor", "fonction", "classe"],
            "AUDIT_STRUCTURE": ["structure", "fichier", "audit", "organiser"],
            "VEILLE_SILENCIEUSE": ["recherche", "apprendre", "veille", "documentation"],
            "COUNCIL_DEBATE": ["debat", "conseil", "strategie", "amelioration"],
            "SECURITY_AUDIT": ["securite", "vulnerabilite", "audit", "risque"],
            "MEMORY_CLEANUP": ["memoire", "nettoyage", "doublon"],
            "MEMORY_CONSOLIDATION": ["memoire", "consolidation", "synthese"],
            "REFACTOR_RANDOM": ["refactoring", "simplifier", "lisibilite"],
            "GRIMOIRE_INVOKE": ["grimoire", "specialiste", "recette"],
            "DROPZONE_SCAN": ["dropzone", "fichier", "ingestion"],
            "SOLILOQUE_INTERNE": ["soliloque", "dialogue", "introspection", "connexion"],
        }

        keywords = intent_keywords.get(intent, [])
        if not keywords:
            return 0.0

        total_affinity = 0.0
        for kw in keywords:
            nid = _make_node_id(kw)
            if nid in self.nodes:
                node = self.nodes[nid]
                total_affinity += node["energy"] * 0.3

        return min(1.5, round(total_affinity, 2))

    def get_stats(self) -> Dict[str, Any]:
        """Stats pour les snapshots."""
        meta_count = sum(
            1 for n in self.nodes.values() if n["node_type"] == "meta"
        )
        strong_synapses = sum(
            1 for s in self.synapses.values() if s["weight"] >= 0.5
        )
        avg_energy = 0.0
        if self.nodes:
            avg_energy = round(
                sum(n["energy"] for n in self.nodes.values()) / len(self.nodes), 3
            )
        return {
            "total_nodes": len(self.nodes),
            "total_synapses": len(self.synapses),
            "meta_concepts": meta_count,
            "strong_synapses": strong_synapses,
            "avg_energy": avg_energy,
            "buffer_size": len(self._activation_buffer),
        }

    # --- Souscriptions Event Bus ---

    def _subscribe_events(self):
        """Souscrit aux evenements bus pour ingestion passive."""
        if self._subscribed:
            return
        self._subscribed = True
        try:
            from core.event_bus.bus import bus
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            bus.subscribe("COUNCIL_END", self._on_council_end)
            bus.subscribe("EUREKA_BRIDGE", self._on_eureka_bridge)
            bus.subscribe("ARTIFACT_CREATED", self._on_artifact_created)
            bus.subscribe("KNOWLEDGE_GAP_DETECTED", self._on_knowledge_gap)
            bus.subscribe("EXPERIENCE_RECORDED", self._on_experience_recorded)
            bus.subscribe("MISSION_FINISHED", self._on_mission_finished)
            bus.subscribe("INNER_VOICE_BROADCAST", self._on_inner_voice)
            bus.subscribe("PSYCHE_UPDATE", self._on_psyche_update)
            bus.subscribe("CARDIAC_BEAT", self._on_cardiac_beat)
            bus.subscribe("REPTILIAN_ALERT", self._on_reptilian_alert)
            bus.subscribe("PREFRONTAL_GOAL_CREATED", self._on_goal_created)
            bus.subscribe("PREFRONTAL_GOAL_COMPLETE", self._on_goal_complete)
            bus.subscribe("PREFRONTAL_GOAL_ABANDONED", self._on_goal_abandoned)
            bus.subscribe("CARDIAC_EMOTION_CHANGE", self._on_cardiac_emotion_change)
            # Sensorium hardware (Sprint 2 Sensorium)
            bus.subscribe("SENSORIUM_UPDATE", self._on_sensorium_update)
            bus.subscribe("TISSUE_ZONE_UPDATE", self._on_tissue_zone_update)
            # Plasticite structurelle sur BRAIN_TICK
            bus.subscribe("BRAIN_TICK", self._on_brain_tick_growth)
        except Exception as e:
            logger.warning(f"SYNAPSE: Impossible de souscrire aux evenements: {e}")

    def _seed_organ_nodes(self):
        """Cree les noeuds desire et trait au demarrage (idempotent)."""
        # Désactiver auto_save pendant le seed pour éviter une save partielle
        self._seeding = True

        # 7 pulsions
        DRIVE_NAMES = [
            "curiosite", "maitrise", "stabilite", "connexion",
            "croissance", "creation", "comprehension",
        ]
        for drive in DRIVE_NAMES:
            self.ensure_node(f"pulsion:{drive}", "desire", 0.4, ["desire_engine"])

        # 6 traits PSYCHE
        TRAIT_NAMES = [
            "curiosite", "creativite", "audace", "savoir", "survie", "respect",
        ]
        for trait in TRAIT_NAMES:
            self.ensure_node(f"trait:{trait}", "trait", 0.4, ["psyche"])

        # Liens TRAIT_RESONANCE (pulsion -> trait)
        try:
            from core.desire_engine import TRAIT_RESONANCE
            for drive, traits in TRAIT_RESONANCE.items():
                drive_nid = _make_node_id(f"pulsion:{drive.lower()}")
                for trait_name, weight in traits.items():
                    trait_nid = _make_node_id(f"trait:{trait_name}")
                    if drive_nid in self.nodes and trait_nid in self.nodes:
                        key = _synapse_key(drive_nid, trait_nid)
                        if key not in self.synapses:
                            self.synapses[key] = _make_synapse(
                                drive_nid, trait_nid, weight,
                                "emotional",
                                f"resonance:{drive.lower()}->{trait_name}",
                            )
                            self._publish_delta("synapse_new", {
                                "source": drive_nid, "target": trait_nid,
                                "weight": round(weight, 3),
                                "type": "emotional",
                            })
        except ImportError:
            logger.warning("SYNAPSE: desire_engine non disponible pour seed")

        # Réactiver auto_save et sauver l'état COMPLET (noeuds + synapses)
        self._seeding = False
        self._mutations_since_save = 0

        count_d = sum(1 for n in self.nodes.values() if n["node_type"] == "desire")
        count_t = sum(1 for n in self.nodes.values() if n["node_type"] == "trait")
        count_s = len(self.synapses)
        self.save()
        logger.info(
            f"SYNAPSE: Seed organes -> {count_d} desire, {count_t} trait noeuds, {count_s} synapses"
        )

    async def _on_psyche_update(self, event: dict):
        """PSYCHE_UPDATE : reactive les noeuds trait et renforce les liens dominants."""
        try:
            avg = event.get("system_average", {})
            if not avg:
                return

            # Reactiver chaque trait (boost energy via ensure_node)
            for trait_name, value in avg.items():
                nid = self.ensure_node(
                    f"trait:{trait_name}", "trait", 0.4, ["psyche"]
                )
                if nid in self.nodes:
                    self.nodes[nid]["affect"]["trait_value"] = round(value, 1)

            # Renforcer le lien trait dominant -> ses pulsions
            dominant = max(avg, key=avg.get)
            dominant_nid = _make_node_id(f"trait:{dominant}")
            try:
                from core.desire_engine import TRAIT_RESONANCE
                for drive, traits in TRAIT_RESONANCE.items():
                    if dominant in traits:
                        drive_nid = _make_node_id(f"pulsion:{drive.lower()}")
                        if drive_nid in self.nodes and dominant_nid in self.nodes:
                            self.hebbian_strengthen(
                                dominant_nid, drive_nid, success=True,
                                context=f"psyche_resonance:{dominant}",
                            )
            except ImportError:
                pass
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_psyche_update: {e}")

    async def _on_cardiac_beat(self, event: dict):
        """Battement cardiaque : module l'energie des noeuds organes."""
        try:
            emotion = event.get("emotion", "serenite")
            intensity = event.get("emotion_intensity", 0.3)
            coherence = event.get("coherence", 0.5)

            # Importer arousal depuis cardiac
            try:
                from core.cardiac_engine import EMOTIONS
                _, arousal = EMOTIONS.get(emotion, (0.0, 0.3))
            except ImportError:
                arousal = intensity

            # Sync deprivation reelle -> energie pulsions
            try:
                from core.desire_engine import desires
                for drive in desires.drives.values():
                    nid = _make_node_id(f"pulsion:{drive.name.lower()}")
                    if nid in self.nodes:
                        base = drive.deprivation / 100.0
                        pulse = (arousal - 0.5) * 0.06  # [-0.03, +0.03]
                        new_energy = max(0.1, min(1.0, base + pulse))
                        if abs(new_energy - self.nodes[nid]["energy"]) > 0.01:
                            self.nodes[nid]["energy"] = round(new_energy, 3)
                            self.nodes[nid]["affect"]["desire_intensity"] = round(drive.deprivation, 1)
                            self._publish_delta("node_activate", {
                                "id": nid,
                                "concept": self.nodes[nid]["concept"],
                                "energy": self.nodes[nid]["energy"],
                                "activation": self.nodes[nid]["activation_count"],
                            })
            except ImportError:
                pass

            # Module energie traits avec coherence
            for nid, node in self.nodes.items():
                if node["node_type"] == "trait":
                    base = node["affect"].get("trait_value", 50.0) / 100.0
                    pulse = (coherence - 0.5) * 0.04  # [-0.02, +0.02]
                    new_energy = max(0.1, min(1.0, base + pulse))
                    if abs(new_energy - node["energy"]) > 0.01:
                        node["energy"] = round(new_energy, 3)
                        self._publish_delta("node_activate", {
                            "id": nid,
                            "concept": node["concept"],
                            "energy": node["energy"],
                            "activation": node["activation_count"],
                        })
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_cardiac_beat: {e}")

    async def _on_goal_created(self, event: dict):
        """Nouveau goal -> noeud objective (rouge) dans le reseau."""
        try:
            title = event.get("title", "")
            if not title or len(title) < MIN_CONCEPT_LENGTH:
                return
            source = event.get("source", "")
            horizon = event.get("horizon", "short")

            weight_map = {"immediate": 0.5, "short": 0.6, "medium": 0.7, "long": 0.8}
            weight = weight_map.get(horizon, 0.6)

            nid = self.ensure_node(
                f"goal:{title}", "objective", weight, ["prefrontal", source]
            )
            if not nid:
                return

            # Lier le goal a la pulsion dominante
            try:
                from core.desire_engine import desires
                dominant = max(desires.drives.values(), key=lambda d: d.deprivation)
                drive_nid = _make_node_id(f"pulsion:{dominant.name.lower()}")
                if drive_nid in self.nodes:
                    self.hebbian_strengthen(
                        nid, drive_nid, success=True,
                        context=f"goal_drive:{title[:50]}",
                    )
            except ImportError:
                pass
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_goal_created: {e}")

    async def _on_goal_complete(self, event: dict):
        """Goal accompli -> boost energie + apprentissage causal V3."""
        try:
            title = event.get("title", "")
            if title:
                nid = _make_node_id(f"goal:{title}")
                if nid in self.nodes:
                    self.nodes[nid]["energy"] = 0.95
                    self._publish_delta("node_activate", {
                        "id": nid, "concept": self.nodes[nid]["concept"],
                        "energy": 0.95, "activation": self.nodes[nid]["activation_count"],
                    })
            # Phase C Etape 3 : apprentissage causal Hebbian V3
            await self._learn_from_homeostatic_closure(event)
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_goal_complete: {e}")

    async def _on_goal_abandoned(self, event: dict):
        """Goal abandonne -> energy chute + extinction causale V3."""
        try:
            title = event.get("title", "")
            if title:
                nid = _make_node_id(f"goal:{title}")
                if nid in self.nodes:
                    self.nodes[nid]["energy"] = 0.1
                    self._publish_delta("node_activate", {
                        "id": nid, "concept": self.nodes[nid]["concept"],
                        "energy": 0.1, "activation": self.nodes[nid]["activation_count"],
                    })
            # Phase C Etape 3 : extinction causale Hebbian V3
            await self._learn_from_fruitless_goal(event)
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_goal_abandoned: {e}")

    # ================================================================
    # PHASE C ETAPE 3 - Hebbian causal V3 (2026-04-14)
    # Design doc : docs/phase_c_etape_3_hebbian_causal.md
    # Valide par trio adversarial (Claude + Gemini + Jean-Michel)
    # ================================================================

    @staticmethod
    def _triangular_weight(idx: int, n: int) -> float:
        """Poids triangulaire : (idx+1) / (n*(n+1)/2).

        Proprietes (testees dans test_synaptic_hebbian_causal.py) :
          - Conservation : sum(weights) == 1.0 pour tout n
          - Monotonie : weight(k) < weight(k+1)
          - Dernier step = 2/(n+1) : le coup de grace porte le plus gros credit
        """
        if n <= 0:
            return 0.0
        total = n * (n + 1) / 2
        return (idx + 1) / total

    async def _learn_from_homeostatic_closure(self, event: dict):
        """Renforcement Hebbian causal sur fermeture homeostatique.

        Ecoute UNIQUEMENT les events avec completion_mode=homeostatic
        (Fix 1 Phase C). Distribue le credit causal_drop sur les step_intents
        via une distribution triangulaire (le dernier step = coup de grace).

        Filtres de securite (Gemini-validated) :
          F1. completion_mode != homeostatic    -> skip (pas de superstition)
          F2. causal_drop <= 0                   -> skip (rien a apprendre)
          F3. step_intents vide                  -> skip (aucun intent credit)
          F4. source_drive inconnu ou vide       -> fallback avec WARNING log

        Ne touche JAMAIS aux poids synaptiques sans un event signe causalement.
        """
        # F1 : seule la fermeture homeostatique enseigne
        completion_mode = event.get("completion_mode", "")
        if completion_mode != "homeostatic":
            self.stats["hebbian_causal_skipped_non_homeostatic"] = \
                self.stats.get("hebbian_causal_skipped_non_homeostatic", 0) + 1
            return

        # F2 : pas de drop reel -> pas d'apprentissage
        causal_drop = float(event.get("causal_drop") or 0.0)
        if causal_drop <= 0:
            self.stats["hebbian_causal_skipped_zero_drop"] = \
                self.stats.get("hebbian_causal_skipped_zero_drop", 0) + 1
            return

        # F3 : aucun step fait -> pas de credit a distribuer
        step_intents = event.get("step_intents") or []
        if not step_intents:
            self.stats["hebbian_causal_skipped_empty_steps"] = \
                self.stats.get("hebbian_causal_skipped_empty_steps", 0) + 1
            return

        # F4 : verifier que source_drive est un drive connu
        source_drive = (event.get("source_drive") or "").upper()
        if source_drive not in HEBBIAN_CAUSAL_KNOWN_DRIVES:
            # Gemini Q3 : fallback acceptable EN V3 avec log WARNING
            logger.warning(
                f"SYNAPSE_HEBB_V3: source_drive='{source_drive}' inconnu, "
                f"goal={event.get('goal_id', '?')} — skip learning. "
                f"Dette V3.1 : chaque organe doit signer source_drive indelebile."
            )
            self.stats["hebbian_causal_skipped_unknown_drive"] = \
                self.stats.get("hebbian_causal_skipped_unknown_drive", 0) + 1
            return

        # ─── Calcul du renforcement ─────────────────────────────────
        n = len(step_intents)
        normalized_drop = min(1.0, causal_drop / HEBBIAN_CAUSAL_DROP_CAP)

        drive_nid = _make_node_id(f"pulsion:{source_drive.lower()}")
        if drive_nid not in self.nodes:
            # Le noeud drive n'existe pas encore (boot recent) : skip gracieux
            logger.debug(
                f"SYNAPSE_HEBB_V3: drive node '{drive_nid}' not yet in graph, "
                f"skip learning for goal={event.get('goal_id', '?')}"
            )
            return

        total_delta = 0.0
        for idx, intent in enumerate(step_intents):
            if not intent:
                continue
            intent_nid = _make_node_id(intent)
            if intent_nid not in self.nodes:
                # Creation implicite : si l'intent n'a pas encore de noeud,
                # on le cree avec l'energie par defaut (sera renforce)
                intent_nid = self.ensure_node(
                    intent, "event", 0.6, ["autonomy"]
                )
                if not intent_nid:
                    continue

            weight_triangular = self._triangular_weight(idx, n)
            delta = normalized_drop * weight_triangular * HEBBIAN_CAUSAL_LEARNING_RATE

            # Appliquer le renforcement directement sur la synapse drive<->intent
            self._apply_causal_delta(intent_nid, drive_nid, delta, context=(
                f"hebb_v3:{source_drive}<-{intent}[{idx+1}/{n}]"
            ))
            total_delta += delta

        # Stats
        self.stats["hebbian_causal_reinforcements"] = \
            self.stats.get("hebbian_causal_reinforcements", 0) + 1
        self.stats["hebbian_causal_total_delta_applied"] = \
            round(self.stats.get("hebbian_causal_total_delta_applied", 0.0) + total_delta, 4)

        logger.info(
            f"SYNAPSE_HEBB_V3: +{total_delta:.4f} sur {source_drive} "
            f"via {n} step(s) {step_intents} causal_drop={causal_drop:.1f} "
            f"goal={event.get('goal_id', '?')}"
        )

    async def _learn_from_fruitless_goal(self, event: dict):
        """Extinction causale sur abandon fruitless.

        Ecoute UNIQUEMENT les events avec completion_mode=abandoned_fruitless.
        Distribue la penalite EXTINCTION_DELTA UNIFORMEMENT sur les
        step_intents (Gemini Q1 : pas de triangulaire inverse, car un
        echec est opaque — tous les maillons sont suspects).

        Plancher strict : EXTINCTION_FLOOR = 0.0 (Gemini Q2). Le genome
        du registre (Brique 1) protege les liens canoniques.
        """
        # F1' : seule l'abandon fruitless enseigne par extinction
        completion_mode = event.get("completion_mode", "")
        if completion_mode != "abandoned_fruitless":
            return

        # F2' : pas de steps tentes -> rien a eteindre
        step_intents = event.get("step_intents") or []
        if not step_intents:
            return

        # F3' : drive inconnu -> skip avec WARNING
        source_drive = (event.get("source_drive") or "").upper()
        if source_drive not in HEBBIAN_CAUSAL_KNOWN_DRIVES:
            logger.warning(
                f"SYNAPSE_HEBB_V3_EXT: source_drive='{source_drive}' inconnu, "
                f"goal={event.get('goal_id', '?')} — skip extinction."
            )
            return

        drive_nid = _make_node_id(f"pulsion:{source_drive.lower()}")
        if drive_nid not in self.nodes:
            return

        # ─── Extinction uniforme ───────────────────────────────────
        # Gemini : "En cas d'echec d'une chaine, tous les maillons sont
        # suspects. Distribue la penalite uniformement."
        extinctions_applied = 0
        for intent in step_intents:
            if not intent:
                continue
            intent_nid = _make_node_id(intent)
            if intent_nid not in self.nodes:
                continue  # pas de noeud = pas de synapse a affaiblir

            # Appliquer la penalite avec plancher strict 0.0
            self._apply_causal_delta(
                intent_nid, drive_nid,
                -HEBBIAN_CAUSAL_EXTINCTION_DELTA,
                context=f"hebb_v3_ext:{source_drive}<-{intent}",
                floor=HEBBIAN_CAUSAL_EXTINCTION_FLOOR,
            )
            extinctions_applied += 1

        if extinctions_applied > 0:
            self.stats["hebbian_causal_extinctions"] = \
                self.stats.get("hebbian_causal_extinctions", 0) + 1
            logger.info(
                f"SYNAPSE_HEBB_V3_EXT: -{HEBBIAN_CAUSAL_EXTINCTION_DELTA:.3f} "
                f"x {extinctions_applied} sur {source_drive} "
                f"via {step_intents} goal={event.get('goal_id', '?')}"
            )

    def _apply_causal_delta(self, src_nid: str, tgt_nid: str, delta: float,
                             context: str = "", floor: float = 0.0) -> None:
        """Applique un delta (positif ou negatif) sur la synapse src<->tgt.

        Cree la synapse si necessaire (seulement pour delta > 0). Pour
        delta < 0, si la synapse n'existe pas, no-op gracieux (pas de
        synapse negative creee). Plancher strict : weight >= floor.

        Contrairement a hebbian_strengthen, ce delta est PRE-CALCULE
        par la regle V3 (pas de multiplication par e_src * e_tgt). C'est
        voulu : la magnitude est entierement determinee par le causal_drop
        et la distribution triangulaire, pas par l'activation instantanee
        des noeuds (qui serait une contamination temporelle).
        """
        src = self.nodes.get(src_nid)
        tgt = self.nodes.get(tgt_nid)
        if not src or not tgt:
            return

        key = _synapse_key(src_nid, tgt_nid)

        if delta > 0:
            # Renforcement : creer la synapse si absente
            is_new = key not in self.synapses
            if is_new:
                syn = _make_synapse(src_nid, tgt_nid, 0.05, "hebbian", context)
                self.synapses[key] = syn
                self._enforce_synapse_limit()
            syn = self.synapses[key]
            syn["weight"] = min(1.0, syn["weight"] + delta)
            syn["formation_count"] += 1
            syn["last_strengthened"] = time.time()
            if context and len(context) > len(syn.get("context", "")):
                syn["context"] = context[:200]
            change = "synapse_new" if is_new else "synapse_strengthen"
            self._publish_delta(change, {
                "source": src_nid, "target": tgt_nid,
                "weight": round(syn["weight"], 3),
                "type": syn["synapse_type"],
            })
        else:
            # Extinction : ne jamais creer une synapse pour l'affaiblir
            if key not in self.synapses:
                return
            syn = self.synapses[key]
            new_weight = max(floor, syn["weight"] + delta)  # delta est negatif
            syn["weight"] = new_weight
            # Note : on ne supprime pas la synapse si weight atteint 0.0
            # Le pruning naturel s'en chargera au prochain cycle dream.

        self._mutations_since_save += 1
        self._auto_save()

    # ================================================================
    # PHASE C ETAPE 4 - Provider pour drive_routine_registry
    # ================================================================

    def get_drive_intent_weights(self, drive: str) -> Dict[str, float]:
        """Retourne les poids synaptiques drive<->intents pour un drive donne.

        Utilise par le drive_routine_registry via le Provider Pattern
        (Phase C Etape 4). Expose les poids appris par la regle Hebbian
        causale V3 (_learn_from_homeostatic_closure) pour que les
        consommateurs puissent les utiliser via get_routines_for_drive_live().

        Parcourt toutes les synapses qui touchent le noeud 'pulsion:{drive}'
        et retourne un dict {intent_concept: weight}. Si plusieurs synapses
        existent (rare), garde le max.

        Retourne {} si le drive n'est pas dans le graphe (boot precoce ou
        drive inconnu).

        Note de purete : cette methode est une LECTURE pure, elle ne modifie
        jamais l'etat du graphe. Appel safe a chaque cycle de scoring.
        """
        drive_nid = _make_node_id(f"pulsion:{drive.lower()}")
        if drive_nid not in self.nodes:
            return {}

        weights: Dict[str, float] = {}
        for key, syn in self.synapses.items():
            try:
                src, tgt = key.split("->", 1)
            except ValueError:
                continue
            if src != drive_nid and tgt != drive_nid:
                continue
            other_nid = tgt if src == drive_nid else src
            other_node = self.nodes.get(other_nid)
            if not other_node:
                continue
            # On ne garde que les noeuds de type "event" (routines/intents)
            if other_node.get("type") != "event":
                continue
            concept = other_node.get("concept", "")
            if not concept:
                continue
            # Si plusieurs synapses existent (bi-directionnel rare), prendre le max
            current = weights.get(concept, 0.0)
            weights[concept] = max(current, float(syn.get("weight", 0.0)))
        return weights

    async def _on_reptilian_alert(self, event: dict):
        """Reflexe reptilien -> flash intense dans le reseau."""
        try:
            reflex = event.get("reflex", "")
            threat_level = event.get("threat_level", 0.0)
            if not reflex or threat_level < 3.0:
                return

            energy = min(1.0, 0.5 + threat_level * 0.05)
            nid = self.ensure_node(
                f"reflex:{reflex}", "event", energy, ["reptilian"]
            )
            if not nid:
                return

            # Lier a pulsion:stabilite (instinct de survie)
            stab_nid = _make_node_id("pulsion:stabilite")
            if stab_nid in self.nodes:
                self.hebbian_strengthen(
                    nid, stab_nid, success=True,
                    context=f"threat:{reflex}:{threat_level:.0f}",
                )

            # Lier a trait:survie
            surv_nid = _make_node_id("trait:survie")
            if surv_nid in self.nodes:
                self.hebbian_strengthen(
                    nid, surv_nid, success=True,
                    context=f"survival:{reflex}",
                )
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_reptilian_alert: {e}")

    async def _on_inner_voice(self, event: dict):
        """Active le noeud correspondant au source de la pensee diffusee."""
        try:
            source = event.get("source", "")
            if source:
                node_id = self.ensure_node(source, node_type="event")
                # Boost d'énergie directement (ensure_node fait +0.1, on ajoute 0.2)
                if node_id in self.nodes:
                    self.nodes[node_id]["energy"] = min(
                        1.0, self.nodes[node_id]["energy"] + 0.2
                    )
        except Exception:
            pass

    async def _on_routine_complete(self, event: dict):
        """Routine terminee : creer/renforcer les noeuds correspondants."""
        try:
            intent = event.get("intent", "")
            status = event.get("status", "")
            quality = event.get("quality_score", 0.5)
            result_text = event.get("result", "")

            if not intent:
                return

            # Noeud pour l'intent (toujours créé, même en échec)
            intent_nid = self.ensure_node(
                intent, "event", 0.6, ["autonomy"]
            )
            # Tracker pour associations sensorium
            if intent_nid:
                self._last_routine_node = intent_nid

            # Anti-bruit : ne pas ingérer les résultats des routines en échec
            # (erreurs Python, messages techniques → pollution du réseau)
            if status != "success" or quality < 0.3:
                logger.debug(f"SYNAPSE: Routine '{intent}' en échec (q={quality:.2f}), skip extraction concepts")
                return

            # Extraire concepts du resultat (limite differenciee selon richesse)
            _ROUTINE_CONCEPT_LIMITS = {
                "COUNCIL_DEBATE": 12, "EXPANSION_CODE": 8, "REFACTOR_RANDOM": 8,
                "VEILLE_SILENCIEUSE": 8, "SOLILOQUE_INTERNE": 10,
                "GRIMOIRE_INVOKE": 8, "SECURITY_AUDIT": 6,
            }
            max_c = _ROUTINE_CONCEPT_LIMITS.get(intent, 5)
            concept_nids = self._extract_and_ensure(result_text, "memory", ["autonomy"], max_c)

            # Liens Hebbiens entre intent et concepts
            success = (status == "success" and quality >= 0.6)
            for cnid in concept_nids:
                self.hebbian_strengthen(intent_nid, cnid, success=success,
                                        context=f"routine:{intent}")

            # ================================================================
            # PHASE B LEGACY - TEMPORAL SUPERSTITION (commente 2026-04-14)
            # ================================================================
            # Ce bloc renforcait drive<->intent sur la seule base de
            # status=success et quality>=0.6, SANS verifier si la tension
            # upstream avait vraiment baisse. Loi de Goodhart appliquee au
            # graphe synaptique lui-meme.
            #
            # Pire : utilisait DRIVE_ROUTINE_AFFINITY (table heretique de la
            # V1) pour decider quel drive renforcer, potentiellement
            # renforcant des liens vers plusieurs drives qui n'etaient pas
            # la vraie source du goal courant (superstition multi-drive).
            #
            # Remplace par _learn_from_homeostatic_closure (Phase C Etape 3,
            # V3) qui n'ecoute QUE PREFRONTAL_GOAL_COMPLETE mode=homeostatic
            # avec source_drive + causal_drop + step_intents signes
            # causalement. Voir docs/phase_c_etape_3_hebbian_causal.md.
            # ================================================================
            # if success:
            #     try:
            #         from core.desire_engine import DRIVE_ROUTINE_AFFINITY
            #         for drive, routines in DRIVE_ROUTINE_AFFINITY.items():
            #             if intent in routines:
            #                 drive_nid = _make_node_id(
            #                     f"pulsion:{drive.lower()}"
            #                 )
            #                 if drive_nid in self.nodes:
            #                     self.hebbian_strengthen(
            #                         intent_nid, drive_nid, success=True,
            #                         context=f"affinity:{intent}->{drive}",
            #                     )
            #     except ImportError:
            #         pass

            # Sync immediat deprivation -> energie pulsions
            try:
                from core.desire_engine import desires
                for drive in desires.drives.values():
                    drive_nid = _make_node_id(f"pulsion:{drive.name.lower()}")
                    if drive_nid in self.nodes:
                        new_e = max(0.1, min(1.0, drive.deprivation / 100.0))
                        self.nodes[drive_nid]["energy"] = round(new_e, 3)
                        self.nodes[drive_nid]["affect"]["desire_intensity"] = round(drive.deprivation, 1)
            except ImportError:
                pass

            # --- Renforcement Hebbian contextuel ---
            # "Cells that fire together wire together" : associer l'intent
            # a l'etat cognitif dans lequel il a ete execute.
            # Permet d'apprendre que certaines routines reussissent mieux
            # dans certains etats (flow, crisis, exploration, etc.)
            cog_ctx = event.get("cognitive_context", {})
            hebbian_context_links = 0

            if cog_ctx.get("cognitive_state"):
                state_nid = self.ensure_node(
                    f"cogstate:{cog_ctx['cognitive_state']}", "affect",
                    0.4, ["hebbian"],
                )
                if state_nid and intent_nid:
                    self.hebbian_strengthen(
                        intent_nid, state_nid, success=success,
                        context=f"hebbian:{intent}<>{cog_ctx['cognitive_state']}",
                    )
                    hebbian_context_links += 1

            if cog_ctx.get("dominant_drive"):
                drive_ctx_nid = self.ensure_node(
                    f"drive:{cog_ctx['dominant_drive'].lower()}", "affect",
                    0.4, ["hebbian"],
                )
                if drive_ctx_nid and intent_nid:
                    self.hebbian_strengthen(
                        intent_nid, drive_ctx_nid, success=success,
                        context=f"hebbian:{intent}<>{cog_ctx['dominant_drive']}",
                    )
                    hebbian_context_links += 1

            if cog_ctx.get("cardiac_emotion"):
                emo_nid = self.ensure_node(
                    f"emotion:{cog_ctx['cardiac_emotion']}", "affect",
                    0.4, ["hebbian"],
                )
                if emo_nid and intent_nid:
                    self.hebbian_strengthen(
                        intent_nid, emo_nid, success=success,
                        context=f"hebbian:{intent}<>{cog_ctx['cardiac_emotion']}",
                    )
                    hebbian_context_links += 1

            logger.info(
                f"SYNAPSE: Routine '{intent}' -> +1 noeud, "
                f"{len(concept_nids)} concepts, "
                f"{len(concept_nids)} liens, "
                f"{hebbian_context_links} ctx hebbiens "
                f"({len(self.nodes)} noeuds, "
                f"{len(self.synapses)} synapses total)"
            )
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_routine_complete: {e}")

    async def _on_council_end(self, event: dict):
        """Council termine : concepts du debat (batch mode pour eviter flood bus)."""
        try:
            topic = event.get("topic", event.get("council_id", ""))
            status = event.get("status", "")
            summary = event.get("final_summary", event.get("summary", ""))

            if not topic:
                return

            # Activer batch mode — accumule les deltas au lieu de les publier un par un
            self._suppress_deltas = True
            self._pending_deltas = []

            topic_nid = self.ensure_node(topic, "event", 0.7, ["council"])
            concept_nids = self._extract_and_ensure(
                summary or topic, "memory", ["council"], max_concepts=12
            )

            success = (status == "consensus")
            for cnid in concept_nids:
                self.hebbian_strengthen(topic_nid, cnid, success=success,
                                        context=f"council:{topic[:50]}")

            # Flush : publier un seul evenement SYNAPTIC_BATCH
            self._suppress_deltas = False
            await self._flush_deltas()
        except Exception as e:
            self._suppress_deltas = False
            self._pending_deltas = []
            logger.warning(f"SYNAPSE: Erreur _on_council_end: {e}")

    async def _on_eureka_bridge(self, event: dict):
        """Eureka : lien fort entre les concepts du pont creatif."""
        try:
            node_a = event.get("node_a", "")
            node_b = event.get("node_b", "")
            hypothesis = event.get("hypothesis", "")

            if not node_a or not node_b:
                return

            nid_a = self.ensure_node(node_a, "eureka", 0.8, ["creativity"])
            nid_b = self.ensure_node(node_b, "eureka", 0.8, ["creativity"])

            # Synapse forte bidirectionnelle
            self.hebbian_strengthen(nid_a, nid_b, success=True,
                                    context=f"eureka:{hypothesis[:100]}")
            self.hebbian_strengthen(nid_b, nid_a, success=True,
                                    context=f"eureka:{hypothesis[:100]}")
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_eureka_bridge: {e}")

    async def _on_artifact_created(self, event: dict):
        """Artefact cree : lien entre agent et fichier."""
        try:
            agent = event.get("agent", "")
            filename = event.get("filename", "")
            if not agent or not filename:
                return
            agent_nid = self.ensure_node(agent, "event", 0.5, ["production"])
            file_nid = self.ensure_node(filename, "memory", 0.6, ["production"])
            self.hebbian_strengthen(agent_nid, file_nid, success=True,
                                    context=f"artifact:{filename[:100]}")
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_artifact_created: {e}")

    async def _on_knowledge_gap(self, event: dict):
        """Lacune detectee : noeud avec haute energie pour attirer l'attention."""
        try:
            gap = event.get("topic", event.get("gap", event.get("description", "")))
            if not gap:
                return
            self.ensure_node(gap, "objective", 0.9, ["knowledge"])
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_knowledge_gap: {e}")

    async def _on_experience_recorded(self, event: dict):
        """Experience enregistree : concepts de l'experience."""
        try:
            description = event.get("description", "")
            if not description:
                return
            self._extract_and_ensure(description, "memory", ["experience"], max_concepts=8)
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_experience_recorded: {e}")

    async def _on_mission_finished(self, event: dict):
        """Mission terminee : concepts de la mission."""
        try:
            mission = event.get("mission", "")
            result = event.get("result", "")
            agent = event.get("agent", "")
            success = event.get("success", True)

            if not mission:
                return

            mission_nid = self.ensure_node(mission, "event", 0.7, ["mission"])
            if agent:
                agent_nid = self.ensure_node(agent, "event", 0.4, ["mission"])
                self.hebbian_strengthen(mission_nid, agent_nid,
                                        success=success,
                                        context=f"mission:{mission[:80]}")

            concept_nids = self._extract_and_ensure(
                result or mission, "memory", ["mission"], max_concepts=10
            )
            for cnid in concept_nids:
                self.hebbian_strengthen(mission_nid, cnid, success=success,
                                        context=f"mission:{mission[:80]}")
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_mission_finished: {e}")

    async def _on_cardiac_emotion_change(self, event: dict):
        """Transition emotionnelle cardiaque -> synapse emotionnelle.
        Cree un noeud affect pour l'emotion et le lie a la cause et a la pulsion dominante."""
        try:
            emotion = event.get("emotion", "")
            intensity = event.get("intensity", 0.0)
            cause = event.get("cause", "")

            if not emotion or intensity < 0.6:
                return

            # Noeud pour l'emotion courante
            emotion_nid = self.ensure_node(
                f"emotion:{emotion}", "affect", intensity, ["cardiac"]
            )
            if not emotion_nid:
                return

            # Lier a la cause (intent ou stimulus)
            if cause:
                cause_nid = _make_node_id(cause)
                if cause_nid in self.nodes:
                    key = _synapse_key(emotion_nid, cause_nid)
                    if key not in self.synapses:
                        self.synapses[key] = _make_synapse(
                            emotion_nid, cause_nid, intensity * 0.5,
                            "emotional", f"affect:{emotion}<-{cause}"
                        )
                    else:
                        self.synapses[key]["weight"] = min(
                            1.0, self.synapses[key]["weight"] + 0.05
                        )

            # Lier a la pulsion dominante
            try:
                from core.desire_engine import desires
                top_drives = sorted(
                    desires.drives.values(),
                    key=lambda d: d.deprivation, reverse=True
                )
                if top_drives:
                    dominant = top_drives[0].name
                    drive_nid = _make_node_id(f"pulsion:{dominant.lower()}")
                    if drive_nid in self.nodes:
                        key = _synapse_key(emotion_nid, drive_nid)
                        if key not in self.synapses:
                            self.synapses[key] = _make_synapse(
                                emotion_nid, drive_nid, 0.3,
                                "emotional", f"affect:{emotion}->drive:{dominant}"
                            )
            except Exception:
                pass

        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_cardiac_emotion_change: {e}")

    async def _on_sensorium_update(self, event: dict):
        """Hardware sensorium → nodes affect + associations Hebbiennes.

        Ne cree des noeuds que quand un sens est intense (> 0.7) pour eviter le spam.
        """
        try:
            comfort = event.get("comfort", 0.5)
            senses = event.get("senses", {})

            for sense_name, value in senses.items():
                if value > 0.7:
                    node_id = self.ensure_node(
                        f"hardware_{sense_name}",
                        node_type="affect",
                        semantic_weight=value,
                    )
                    if not node_id:
                        continue
                    # Associer au dernier contexte connu (routine en cours)
                    if self._last_routine_node and self._last_routine_node in self.nodes:
                        self.hebbian_strengthen(
                            self._last_routine_node, node_id,
                            success=True,
                            context="sensorium",
                        )
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_sensorium_update: {e}")

    async def _on_tissue_zone_update(self, event: dict):
        """Zones actives du tissu neural → noeuds zone + synapses contextuelles."""
        try:
            data = event.get("data", event) if isinstance(event, dict) else {}
            zones = data.get("zones", {})
            dominants = data.get("dominants", {})

            for zone_name, metrics in zones.items():
                activity = metrics.get("activity", 0.0)
                diversity = metrics.get("diversity", 0.0)

                # Ne créer/renforcer un noeud que si la zone est significativement active
                if activity < 0.3 and diversity < 0.2:
                    continue

                node_id = self.ensure_node(
                    f"zone:{zone_name}",
                    node_type="zone",
                    semantic_weight=min(1.0, activity * diversity * 2),
                    functional_systems=["neural_tissue"],
                )
                if not node_id:
                    continue

                # Energie proportionnelle à activité × diversité
                if node_id in self.nodes:
                    self.nodes[node_id]["energy"] = min(
                        1.0, activity * (0.5 + diversity * 0.5)
                    )

                # Relier au dernier noeud routine si disponible
                if self._last_routine_node and self._last_routine_node in self.nodes:
                    self.hebbian_strengthen(
                        node_id, self._last_routine_node,
                        success=True, context="tissue_zone",
                    )

                # Zone créative haute → relier aux noeuds eureka récents
                if zone_name == "creativity" and activity > 1.0:
                    eureka_nodes = [
                        nid for nid, n in self.nodes.items()
                        if n.get("node_type") == "eureka"
                    ][-3:]
                    for enid in eureka_nodes:
                        self.hebbian_strengthen(
                            node_id, enid, success=True, context="tissue_creativity",
                        )

        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_tissue_zone_update: {e}")

    def activate_concept(self, concept: str, intensity: float = 0.5):
        """Active un concept (alias pour ensure_node + boost energie)."""
        node_id = self.ensure_node(concept, "memory", intensity)
        if node_id and node_id in self.nodes:
            self.nodes[node_id]["energy"] = min(
                1.0, self.nodes[node_id]["energy"] + intensity
            )
        return node_id

    def _extract_and_ensure(self, text: str, node_type: str = "memory",
                            functional_systems: Optional[List[str]] = None,
                            max_concepts: int = 5) -> List[str]:
        """Extrait des concepts d'un texte et les enregistre comme noeuds.

        Retourne la liste des node_ids crees/mis a jour.
        """
        if not text:
            return []
        try:
            from core.spreading_activation import extract_concepts
            concepts = extract_concepts(text, max_concepts=max_concepts)
        except Exception:
            return []

        nids = []
        for concept, weight in concepts:
            nid = self.ensure_node(concept, node_type, weight, functional_systems)
            if nid:  # Filtrer les concepts rejetes (trop courts)
                nids.append(nid)
        return nids

    # --- Dream Mode ---

    def dream_consolidation(self) -> Dict[str, Any]:
        """Consolidation stochastique — simule le sommeil REM.

        1. Activation stochastique (top 30% + bruit)
        2. Connexions inattendues (non-voisins)
        3. Pruning synaptique (decay + suppression)
        4. Consolidation des synapses fortes
        5. Meta-concepts (clustering)
        6. Normalisation homeostatique
        """
        report = {
            "pruned_synapses": 0,
            "dream_connections": 0,
            "new_meta_concepts": 0,
            "strengthened": 0,
        }

        if not self.nodes:
            return report

        # 1. ACTIVATION STOCHASTIQUE
        # Top 30% par energie + bruit gaussien
        node_list = list(self.nodes.items())
        scored = []
        for nid, node in node_list:
            noise = random.gauss(0, 0.15)
            score = node["energy"] + noise
            scored.append((nid, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        activated_count = max(1, int(len(scored) * 0.3))
        activated = {nid for nid, _ in scored[:activated_count]}

        # 2. CONNEXIONS INATTENDUES
        activated_list = list(activated)
        all_nids = list(self.nodes.keys())
        for nid in activated_list:
            # Trouver les non-voisins
            neighbors = set()
            for key, syn in self.synapses.items():
                if syn["source"] == nid:
                    neighbors.add(syn["target"])
                elif syn["target"] == nid:
                    neighbors.add(syn["source"])

            non_neighbors = [n for n in all_nids if n != nid and n not in neighbors]
            if not non_neighbors:
                continue

            # Tenter 1-2 connexions
            attempts = min(2, len(non_neighbors))
            for target_nid in random.sample(non_neighbors, attempts):
                energy_combined = (
                    self.nodes[nid]["energy"] + self.nodes[target_nid]["energy"]
                ) / 2.0
                if energy_combined > 0.15:
                    key = _synapse_key(nid, target_nid)
                    if key not in self.synapses:
                        # Noeuds fréquemment activés → connexion hebbian
                        # (renforce l'apprentissage au lieu du bruit émotionnel)
                        src_act = self.nodes[nid]["activation_count"]
                        tgt_act = self.nodes[target_nid]["activation_count"]
                        if src_act > 5 and tgt_act > 5:
                            syn_type = "hebbian"
                            syn_weight = 0.12
                        else:
                            syn_type = "emotional"
                            syn_weight = 0.08
                        self.synapses[key] = _make_synapse(
                            nid, target_nid, syn_weight, syn_type, "dream"
                        )
                        report["dream_connections"] += 1

        # 3. PRUNING SYNAPTIQUE ADAPTATIF
        # Le pruning cible 98% du taux de création → croissance nette de 2%
        now = time.time()
        days_since_last_dream = (now - self._last_dream_time) / 86400
        to_prune = []
        for key, syn in self.synapses.items():
            decay = SYNAPSE_DECAY_PER_DAY * days_since_last_dream
            syn["weight"] = max(0.0, syn["weight"] - decay)
            if syn["weight"] < PRUNING_THRESHOLD:
                to_prune.append(key)

        # Cap adaptatif : pruning = 98% du nombre de connexions créées ce cycle
        dream_created = report.get("dream_connections", 0)
        # Estimation créations depuis le dernier dream (mutations_since_save comme proxy)
        estimated_creations = max(dream_created, self._mutations_since_save // 2)
        adaptive_cap = max(10, int(estimated_creations * ADAPTIVE_PRUNE_RATIO))
        # Fallback : ne pas dépasser MAX_PRUNE_RATIO du réseau total
        hard_cap = max(10, int(len(self.synapses) * MAX_PRUNE_RATIO))
        max_prune = min(len(to_prune), max(adaptive_cap, hard_cap))

        if len(to_prune) > max_prune:
            to_prune_scored = [(k, self.synapses[k]["weight"]) for k in to_prune]
            to_prune_scored.sort(key=lambda x: x[1])  # Plus faibles en premier
            saved = to_prune_scored[max_prune:]
            to_prune = [k for k, _ in to_prune_scored[:max_prune]]
            # Restaurer les sauvés juste au seuil (seconde chance)
            for k, _ in saved:
                self.synapses[k]["weight"] = PRUNING_THRESHOLD
            report["pruning_capped"] = True

        for key in to_prune:
            del self.synapses[key]
        report["pruned_synapses"] = len(to_prune)

        # 4. CONSOLIDATION DES FORTES
        promoted = 0
        for syn in self.synapses.values():
            if syn["weight"] >= 0.5:
                syn["weight"] = min(1.0, syn["weight"] * 1.05)
                report["strengthened"] += 1
            # 4b. PROMOTION : temporal fort → hebbian (apprentissage prouvé par l'usage)
            if (syn["synapse_type"] == "temporal"
                    and syn["weight"] >= 0.7
                    and syn["formation_count"] >= 20):
                syn["synapse_type"] = "hebbian"
                promoted += 1
        report["promoted_to_hebbian"] = promoted

        # 5. META-CONCEPTS (clustering)
        report["new_meta_concepts"] = self._create_meta_concepts()

        # 5b. CURIOSITE SYNAPTIQUE — forcer l'exploration inter-systemes
        report["curiosity_links"] = self._curiosity_explore()

        # 6b. SEED TISSULAIRE — zones actives guident la consolidation
        report["tissue_seeds"] = self._dream_tissue_seed()

        # 6. Normalisation homeostatique
        self.homeostatic_normalize()

        # Enforce limits
        self._enforce_synapse_limit()

        self._last_dream_time = time.time()
        self._mutations_since_save += 10
        self._auto_save()

        logger.info(
            f"SYNAPSE: Dream consolidation: "
            f"+{report['dream_connections']} connexions, "
            f"-{report['pruned_synapses']} pruned, "
            f"+{report['new_meta_concepts']} meta, "
            f"+{report.get('curiosity_links', 0)} curiosite, "
            f"{report['strengthened']} renforcees, "
            f"{report.get('promoted_to_hebbian', 0)} promues hebbian"
        )
        return report

    def _curiosity_explore(self, max_links: int = 5) -> int:
        """Curiosite synaptique : cree des liens entre systemes fonctionnels differents.
        Force l'exploration de connexions inattendues entre noeuds de domaines separes."""
        created = 0
        # Grouper les noeuds par systeme fonctionnel
        systems: Dict[str, List[str]] = {}
        for nid, node in self.nodes.items():
            if node.get("node_type") == "meta":
                continue
            for sys in node.get("dimensions", {}).get("functional_systems", []):
                systems.setdefault(sys, []).append(nid)

        sys_names = list(systems.keys())
        if len(sys_names) < 2:
            return 0

        # Tenter des ponts entre systemes differents
        for _ in range(max_links * 3):  # 3x attempts pour compenser les doublons
            s1, s2 = random.sample(sys_names, 2)
            if not systems[s1] or not systems[s2]:
                continue
            n1 = random.choice(systems[s1])
            n2 = random.choice(systems[s2])
            if n1 == n2:
                continue
            key = _synapse_key(n1, n2)
            if key in self.synapses:
                continue
            # Verifier que les noeuds ont un minimum d'energie (pas des noeuds morts)
            e1 = self.nodes[n1].get("energy", 0)
            e2 = self.nodes[n2].get("energy", 0)
            if e1 < 0.1 or e2 < 0.1:
                continue
            # Creer un lien exploratoire faible
            self.synapses[key] = _make_synapse(
                n1, n2, 0.12, "emotional", f"curiosity:{s1}->{s2}"
            )
            created += 1
            if created >= max_links:
                break

        return created

    def _dream_tissue_seed(self) -> int:
        """Seed la consolidation dream depuis les zones actives du tissu neural.

        Les noeuds zone: avec haute énergie deviennent des hubs de connexion
        pendant le rêve — les zones actives du tissu guident la consolidation.
        """
        seeded = 0
        try:
            zone_nodes = [
                (nid, node) for nid, node in self.nodes.items()
                if node.get("node_type") == "zone" and node.get("energy", 0) > 0.3
            ]
            if not zone_nodes:
                return 0

            # Pour chaque zone active, créer 1-2 connexions vers des noeuds mémoire récents
            memory_nodes = [
                nid for nid, node in self.nodes.items()
                if node.get("node_type") in ("memory", "event", "eureka")
                and node.get("energy", 0) > 0.1
            ]
            if not memory_nodes:
                return 0

            for zone_nid, zone_node in zone_nodes:
                # Sélectionner 1-2 noeuds mémoire non-connectés
                connected = set()
                for key, syn in self.synapses.items():
                    if syn["source"] == zone_nid:
                        connected.add(syn["target"])
                    elif syn["target"] == zone_nid:
                        connected.add(syn["source"])
                candidates = [n for n in memory_nodes if n not in connected and n != zone_nid]
                if not candidates:
                    continue
                targets = random.sample(candidates, min(2, len(candidates)))
                for target_nid in targets:
                    key = _synapse_key(zone_nid, target_nid)
                    if key not in self.synapses:
                        self.synapses[key] = _make_synapse(
                            zone_nid, target_nid, 0.12, "emotional", "tissue_dream"
                        )
                        seeded += 1
        except Exception as e:
            logger.debug(f"SYNAPSE: Erreur dream tissue seed: {e}")
        return seeded

    def _create_meta_concepts(self) -> int:
        """Detecte des cliques de 3+ noeuds fortement connectes et cree des meta-concepts."""
        # Max 10% de meta-noeuds
        current_meta = sum(1 for n in self.nodes.values() if n["node_type"] == "meta")
        max_meta = max(1, int(len(self.nodes) * 0.1))
        if current_meta >= max_meta:
            return 0

        # Construire la matrice d'adjacence (seulement synapses fortes)
        adjacency: Dict[str, set] = {}
        for syn in self.synapses.values():
            if syn["weight"] >= 0.4:
                adjacency.setdefault(syn["source"], set()).add(syn["target"])
                adjacency.setdefault(syn["target"], set()).add(syn["source"])

        # Detecter les cliques de taille 3
        new_meta = 0
        visited_cliques: set = set()
        for nid, neighbors in adjacency.items():
            if self.nodes.get(nid, {}).get("node_type") == "meta":
                continue
            for n1 in neighbors:
                if self.nodes.get(n1, {}).get("node_type") == "meta":
                    continue
                for n2 in neighbors:
                    if n2 <= n1:
                        continue
                    if self.nodes.get(n2, {}).get("node_type") == "meta":
                        continue
                    # n1 et n2 sont voisins de nid. Sont-ils aussi voisins entre eux ?
                    if n2 in adjacency.get(n1, set()):
                        clique_key = tuple(sorted([nid, n1, n2]))
                        if clique_key in visited_cliques:
                            continue
                        visited_cliques.add(clique_key)

                        # Creer le meta-concept
                        members = [self.nodes[m]["concept"] for m in clique_key
                                   if m in self.nodes]
                        if len(members) < 3:
                            continue
                        meta_name = f"meta:{'+'.join(sorted(members)[:3])}"
                        meta_nid = _make_node_id(meta_name)

                        if meta_nid not in self.nodes:
                            self.nodes[meta_nid] = _make_node(
                                meta_name, "meta", 0.6, ["meta"]
                            )
                            # Lier aux membres
                            for member_nid in clique_key:
                                key = _synapse_key(meta_nid, member_nid)
                                if key not in self.synapses:
                                    self.synapses[key] = _make_synapse(
                                        meta_nid, member_nid, 0.5, "hebbian",
                                        f"meta-cluster:{meta_name[:100]}"
                                    )
                            new_meta += 1

                        if new_meta >= 3:
                            return new_meta
                        if current_meta + new_meta >= max_meta:
                            return new_meta

        return new_meta

    # --- Persistance ---

    def _load(self):
        """Charge l'etat depuis le fichier JSON.

        Protection : si le fichier principal est corrompu ou anormalement petit,
        tente de charger le backup (.bak).
        """
        loaded = False
        for path in [STATE_FILE, STATE_FILE + ".bak"]:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                nodes = data.get("nodes", {})
                synapses = data.get("synapses", {})
                if len(nodes) < 50 and os.path.exists(STATE_FILE + ".bak"):
                    # Fichier suspect — essayer le backup
                    logger.warning(
                        f"SYNAPSE: Fichier {os.path.basename(path)} suspect "
                        f"({len(nodes)} noeuds), tentative backup..."
                    )
                    if path == STATE_FILE:
                        continue  # essayer le .bak
                self.nodes = nodes
                self.synapses = synapses
                self._last_dream_time = data.get("last_dream_time", time.time())
                self._loaded_node_count = len(self.nodes)
                logger.info(
                    f"SYNAPSE: Charge {len(self.nodes)} noeuds, "
                    f"{len(self.synapses)} synapses"
                    f"{' (depuis backup)' if path != STATE_FILE else ''}."
                )
                loaded = True
                break
            except FileNotFoundError:
                continue
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"SYNAPSE: {os.path.basename(path)} corrompu: {e}")
                continue
        if not loaded:
            self.nodes = {}
            self.synapses = {}
            self._loaded_node_count = 0

    def save(self):
        """Sauvegarde atomique de l'etat avec protection anti-perte.

        3 garde-fous :
        1. Ne sauve PAS si le reseau a perdu >80% de ses noeuds (corruption)
        2. Backup rotatif : l'ancien fichier devient .bak AVANT ecrasement
        3. Ecriture atomique via .tmp + os.replace
        """
        node_count = len(self.nodes)
        loaded_count = getattr(self, '_loaded_node_count', 0)

        # Protection 1 : refus de sauver un reseau vide/suspect
        if loaded_count > 100 and node_count < loaded_count * 0.2:
            logger.error(
                f"SYNAPSE: REFUS DE SAUVEGARDER — reseau passe de "
                f"{loaded_count} a {node_count} noeuds (perte >80%). "
                f"Fichier preserve."
            )
            return

        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

        # Protection 2 : backup rotatif avant ecrasement
        if os.path.exists(STATE_FILE):
            try:
                os.replace(STATE_FILE, STATE_FILE + ".bak")
            except Exception as e:
                logger.warning(f"SYNAPSE: Backup rotation echouee: {e}")

        data = {
            "version": "1.0",
            "saved_at": time.time(),
            "nodes": self.nodes,
            "synapses": self.synapses,
            "last_dream_time": self._last_dream_time,
        }
        tmp_path = STATE_FILE + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, STATE_FILE)
            self._mutations_since_save = 0
            self._loaded_node_count = node_count  # mise a jour du compteur
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur sauvegarde: {e}")
            # Restaurer le backup si l'ecriture a echoue
            if os.path.exists(STATE_FILE + ".bak") and not os.path.exists(STATE_FILE):
                try:
                    os.replace(STATE_FILE + ".bak", STATE_FILE)
                    logger.info("SYNAPSE: Backup restaure apres echec ecriture.")
                except Exception:
                    pass

    def _auto_save(self):
        """Sauvegarde automatique toutes les 10 mutations."""
        if getattr(self, '_seeding', False):
            return  # Pas de save pendant le seed (save explicite après)
        if self._mutations_since_save >= 10:
            self.save()


# --- Singleton global ---
cortex = SynapticNetwork()
try:
    from core.organ_registry import register_organ
    register_organ("synaptic", cortex)
except Exception:
    pass

# --- Phase C Etape 4 : brancher le Provider sur drive_routine_registry ---
# Principe d'inversion de controle : le registre reste pur (il n'importe
# jamais synaptic_network). C'est synaptic_network qui vient "brancher sa
# prise" via set_synaptic_provider() au boot. Les consommateurs peuvent
# ensuite utiliser get_routines_for_drive_live() sans connaitre le graphe.
try:
    from core.drive_routine_registry import set_synaptic_provider
    set_synaptic_provider(cortex.get_drive_intent_weights)
    logger.info("[synaptic_network] registered as synaptic_provider for drive_routine_registry")
except ImportError:
    pass  # registry pas encore disponible (tests isoles, boot partiel)
except Exception as e:
    logger.warning(f"[synaptic_network] failed to register synaptic_provider: {e}")
