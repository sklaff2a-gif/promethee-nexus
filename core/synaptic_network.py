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
SPIKE_TIMING_WINDOW = 300.0       # 5 min pour causalite temporelle
HOMEOSTATIC_TARGET = 0.3
SYNAPSE_DECAY_PER_DAY = 0.02
PRUNING_THRESHOLD = 0.08
MIN_CONCEPT_LENGTH = 3            # Rejeter les concepts trop courts (bruit)
RESONANCE_CYCLES = 4
STDP_MULTIPLIER = 1.5             # STDP 1.5x plus fort que Hebb classique

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "synaptic_network.json"
)

# Types de noeuds valides
VALID_NODE_TYPES = frozenset({
    "memory", "desire", "trait", "event", "objective", "eureka", "meta"
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
        self._load()

    # --- Init & Reset ---

    def init(self):
        """Souscrit aux evenements bus."""
        self._subscribe_events()
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
        self._initialized = False

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilise par les tests)."""
        if cls._instance is not None:
            cls._instance.reset()
            cls._instance = None

    # --- Publication delta temps reel ---

    def _publish_delta(self, change_type: str, data: dict):
        """Publie un delta SYNAPTIC_UPDATE via le bus (non-bloquant)."""
        try:
            loop = asyncio.get_running_loop()
            from core.event_bus.bus import bus
            loop.create_task(bus.publish("SYNAPTIC_UPDATE", {"change": change_type, **data}))
        except RuntimeError:
            pass  # Pas de boucle asyncio (tests)

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

        self._record_activation(node_id)
        self._mutations_since_save += 1
        self._auto_save()
        return node_id

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
        self._mutations_since_save += 1

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

    # --- Buffer temporel STDP ---

    def _record_activation(self, node_id: str):
        """Enregistre une activation pour le buffer STDP."""
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
        if len(self._activation_buffer) > 50:
            self._activation_buffer = self._activation_buffer[-50:]

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
        except Exception as e:
            logger.warning(f"SYNAPSE: Impossible de souscrire aux evenements: {e}")

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

            # Noeud pour l'intent
            intent_nid = self.ensure_node(
                intent, "event", 0.6, ["autonomy"]
            )

            # Extraire concepts du resultat
            concept_nids = self._extract_and_ensure(result_text, "memory", ["autonomy"])

            # Liens Hebbiens entre intent et concepts
            success = (status == "success" and quality >= 0.5)
            for cnid in concept_nids:
                self.hebbian_strengthen(intent_nid, cnid, success=success,
                                        context=f"routine:{intent}")

            logger.info(
                f"SYNAPSE: Routine '{intent}' -> +1 noeud, "
                f"{len(concept_nids)} concepts, "
                f"{len(concept_nids)} liens ({len(self.nodes)} noeuds, "
                f"{len(self.synapses)} synapses total)"
            )
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_routine_complete: {e}")

    async def _on_council_end(self, event: dict):
        """Council termine : concepts du debat."""
        try:
            topic = event.get("topic", event.get("council_id", ""))
            status = event.get("status", "")
            summary = event.get("final_summary", event.get("summary", ""))

            if not topic:
                return

            topic_nid = self.ensure_node(topic, "event", 0.7, ["council"])
            concept_nids = self._extract_and_ensure(
                summary or topic, "memory", ["council"]
            )

            success = (status == "consensus")
            for cnid in concept_nids:
                self.hebbian_strengthen(topic_nid, cnid, success=success,
                                        context=f"council:{topic[:50]}")
        except Exception as e:
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
            self._extract_and_ensure(description, "memory", ["experience"])
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
                result or mission, "memory", ["mission"]
            )
            for cnid in concept_nids:
                self.hebbian_strengthen(mission_nid, cnid, success=success,
                                        context=f"mission:{mission[:80]}")
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur _on_mission_finished: {e}")

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
                        self.synapses[key] = _make_synapse(
                            nid, target_nid, 0.08, "emotional", "dream"
                        )
                        report["dream_connections"] += 1

        # 3. PRUNING SYNAPTIQUE (decay incrémental depuis le dernier dream)
        now = time.time()
        days_since_last_dream = (now - self._last_dream_time) / 86400
        to_prune = []
        for key, syn in self.synapses.items():
            decay = SYNAPSE_DECAY_PER_DAY * days_since_last_dream
            syn["weight"] = max(0.0, syn["weight"] - decay)
            if syn["weight"] < PRUNING_THRESHOLD:
                to_prune.append(key)

        for key in to_prune:
            del self.synapses[key]
        report["pruned_synapses"] = len(to_prune)

        # 4. CONSOLIDATION DES FORTES
        for syn in self.synapses.values():
            if syn["weight"] >= 0.5:
                syn["weight"] = min(1.0, syn["weight"] * 1.05)
                report["strengthened"] += 1

        # 5. META-CONCEPTS (clustering)
        report["new_meta_concepts"] = self._create_meta_concepts()

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
            f"{report['strengthened']} renforcees"
        )
        return report

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
        """Charge l'etat depuis le fichier JSON."""
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.nodes = data.get("nodes", {})
            self.synapses = data.get("synapses", {})
            self._last_dream_time = data.get("last_dream_time", time.time())
            logger.info(
                f"SYNAPSE: Charge {len(self.nodes)} noeuds, "
                f"{len(self.synapses)} synapses."
            )
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"SYNAPSE: Fichier corrompu, reset: {e}")
            self.nodes = {}
            self.synapses = {}

    def save(self):
        """Sauvegarde atomique de l'etat."""
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
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
        except Exception as e:
            logger.warning(f"SYNAPSE: Erreur sauvegarde: {e}")

    def _auto_save(self):
        """Sauvegarde automatique toutes les 10 mutations."""
        if self._mutations_since_save >= 10:
            self.save()


# --- Singleton global ---
cortex = SynapticNetwork()
