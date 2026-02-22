"""
Spreading Activation — Mémoire Associative Biomimétique

Réseau d'activation sémantique qui propage les associations entre collections
mémoire, détecte les collisions créatives (eureka moments), et injecte des
intuitions dans les prompts des agents et débats Council.

Les activations sont éphémères (RAM uniquement, TTL 30 min).
"""

import logging
import re
import time
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger("SpreadingActivation")


# ─── Stopwords compacts FR/EN (~100 mots) ───

_STOPWORDS = frozenset({
    # FR
    "le", "la", "les", "un", "une", "des", "du", "de", "ce", "cette", "ces",
    "est", "sont", "être", "avoir", "fait", "faire", "dans", "pour", "avec",
    "sur", "par", "pas", "plus", "qui", "que", "quoi", "quel", "dont", "mais",
    "tout", "tous", "très", "aussi", "comme", "bien", "peut", "même", "autre",
    "encore", "entre", "sans", "sous", "nous", "vous", "leur", "elle", "elles",
    "notre", "votre", "être", "donc", "sera", "après", "avant",
    # EN
    "the", "and", "for", "that", "this", "with", "from", "are", "was", "were",
    "been", "have", "has", "had", "not", "but", "all", "can", "will", "just",
    "more", "some", "than", "them", "then", "into", "only", "also", "each",
    "which", "when", "what", "how", "would", "should", "could", "about",
    "there", "their", "other", "your",
})

# Patterns techniques (fichiers, fonctions, classes)
_TECH_PATTERN = re.compile(
    r'(?:'
    r'(?:core|Agents|tests|config)/[\w/.]+\.py'      # Chemins fichiers projet
    r'|def\s+(\w+)'                                    # Définitions de fonctions
    r'|class\s+(\w+)'                                  # Définitions de classes
    r'|(?<![a-z])([A-Z][a-z]+(?:[A-Z][a-z]+)+)'       # CamelCase
    r'|(?<![A-Za-z])([a-z]+(?:_[a-z]+){2,})'          # snake_case (3+ segments)
    r')'
)

# Collections connues pour la propagation latérale
_KNOWN_COLLECTIONS = ("collective_wisdom", "code_snippets")


# ─── Structures de données ───

@dataclass
class ActivationNode:
    """Concept activé dans le réseau."""
    concept: str
    energy: float
    source_collection: str
    source_doc_id: str
    activated_at: float
    last_boosted: float
    decay_rate: float = 0.05
    hop_depth: int = 0


@dataclass
class CreativeBridge:
    """Collision eureka entre deux domaines."""
    node_a: str
    node_b: str
    collection_a: str
    collection_b: str
    shared_concepts: List[str]
    bridge_strength: float
    hypothesis: str
    created_at: float
    used: bool = False


@dataclass
class ActivationField:
    """Champ d'activation ambiant (cache RAM, TTL 30 min)."""
    context_hash: str
    nodes: Dict[str, ActivationNode] = field(default_factory=dict)
    bridges: List[CreativeBridge] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    ttl_seconds: float = 1800.0

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds


# ─── Extraction de concepts SANS LLM ───

def extract_concepts(text: str, max_concepts: int = 15) -> List[Tuple[str, float]]:
    """Extrait concepts pondérés depuis un texte, sans LLM.

    Combine TF-IDF simplifié, patterns techniques et bonus de position.
    Retourne une liste de (concept, poids) triée par poids décroissant.
    """
    if not text or not text.strip():
        return []

    scores: Dict[str, float] = {}
    text_lower = text.lower()

    # 1. TF-IDF simplifié : mots 4+ chars hors stopwords
    words = re.findall(r'[a-zA-ZÀ-ÿ_]{4,}', text_lower)
    total_words = max(len(words), 1)
    word_freq: Dict[str, int] = {}
    for w in words:
        if w not in _STOPWORDS:
            word_freq[w] = word_freq.get(w, 0) + 1

    for word, count in word_freq.items():
        tf = count / total_words
        scores[word] = scores.get(word, 0.0) + tf * 10  # Échelle 0-10

    # Bonus position : premier mot significatif de chaque phrase
    sentences = re.split(r'[.!?\n]', text)
    for sentence in sentences:
        first_words = re.findall(r'[a-zA-ZÀ-ÿ_]{4,}', sentence.strip().lower())
        if first_words and first_words[0] not in _STOPWORDS:
            scores[first_words[0]] = scores.get(first_words[0], 0.0) + 1.0

    # Bonus après tirets (listes)
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith(('-', '•', '*')):
            line_words = re.findall(r'[a-zA-ZÀ-ÿ_]{4,}', line.lower())
            for w in line_words[:2]:
                if w not in _STOPWORDS:
                    scores[w] = scores.get(w, 0.0) + 0.5

    # 2. Patterns techniques : poids 2x
    for match in _TECH_PATTERN.finditer(text):
        full = match.group(0)
        # Nettoyer : prendre le match complet ou le groupe capturé
        concept = (match.group(1) or match.group(2) or
                   match.group(3) or match.group(4) or full)
        concept_lower = concept.lower()
        scores[concept_lower] = scores.get(concept_lower, 0.0) + 2.0

    # Tri et normalisation
    if not scores:
        return []

    sorted_concepts = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    max_score = sorted_concepts[0][1] if sorted_concepts else 1.0

    result = []
    for concept, score in sorted_concepts[:max_concepts]:
        normalized = min(1.0, score / max_score) if max_score > 0 else 0.0
        if normalized >= 0.1:  # Filtre bruit
            result.append((concept, round(normalized, 3)))

    return result


# ─── Classe principale ───

class SpreadingActivationEngine:
    """Réseau d'activation sémantique — Singleton."""

    _instance = None

    # Constantes
    ACTIVATION_THRESHOLD = 0.25
    DECAY_PER_CYCLE = 0.05
    LATERAL_ATTENUATION = 0.6
    DEEP_ATTENUATION = 0.35
    MAX_ACTIVE_NODES = 100
    MAX_LATENT_THOUGHTS = 10
    FIELD_TTL_SECONDS = 1800
    BRIDGE_MIN_SHARED_CONCEPTS = 2
    EUREKA_THRESHOLD = 0.5
    MAX_BRIDGES = 20

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._fields: Dict[str, ActivationField] = {}
        self._current_field: Optional[ActivationField] = None
        self._initialized = True

    @classmethod
    def reset_singleton(cls):
        """Pour les tests."""
        cls._instance = None

    # ─── API publique ───

    async def activate(self, stimulus: str, source_collection: str,
                       memory_manager=None, max_hops: int = 1) -> ActivationField:
        """Active le réseau à partir d'un stimulus textuel.

        Phase 0: Extraction de concepts
        Phase 1: Activation initiale (saut 0)
        Phase 2: Propagation latérale (saut 1) si max_hops >= 1 et memory_manager disponible
        Phase 3: Détection eureka
        """
        context_hash = hashlib.md5(stimulus[:200].encode()).hexdigest()[:12]

        # Réutiliser le champ existant s'il est encore valide
        if context_hash in self._fields and not self._fields[context_hash].is_expired():
            afield = self._fields[context_hash]
        else:
            afield = ActivationField(context_hash=context_hash)
            self._fields[context_hash] = afield

        now = time.time()

        # Phase 0 : Extraction de concepts
        concepts = extract_concepts(stimulus)
        if not concepts:
            self._current_field = afield
            return afield

        # Phase 1 : Activation initiale (saut 0)
        for concept, weight in concepts:
            if concept in afield.nodes:
                # Boost un nœud existant
                self.boost_node(concept, weight * 0.3, afield)
            else:
                node = ActivationNode(
                    concept=concept,
                    energy=weight,
                    source_collection=source_collection,
                    source_doc_id="stimulus",
                    activated_at=now,
                    last_boosted=now,
                    hop_depth=0,
                )
                afield.nodes[concept] = node

        # Limiter le nombre de nœuds
        self._enforce_node_limit(afield)

        # Phase 2 : Propagation latérale (saut 1)
        if max_hops >= 1 and memory_manager is not None:
            await self._propagate_lateral(afield, source_collection, memory_manager, now)

        # Phase 3 : Détection eureka
        self._detect_bridges(afield, now)

        self._current_field = afield
        return afield

    def decay_all(self):
        """Décroissance globale de toutes les activations."""
        for afield in list(self._fields.values()):
            to_remove = []
            for concept, node in afield.nodes.items():
                node.energy -= node.decay_rate
                if node.energy < self.ACTIVATION_THRESHOLD:
                    to_remove.append(concept)
            for concept in to_remove:
                del afield.nodes[concept]

    def cleanup(self):
        """Purge les nœuds sous le seuil et les champs expirés."""
        # Purge champs expirés
        expired = [h for h, f in self._fields.items() if f.is_expired()]
        for h in expired:
            if self._current_field and self._current_field.context_hash == h:
                self._current_field = None
            del self._fields[h]

        # Purge nœuds sous le seuil dans les champs restants
        for afield in self._fields.values():
            to_remove = [c for c, n in afield.nodes.items()
                         if n.energy < self.ACTIVATION_THRESHOLD]
            for c in to_remove:
                del afield.nodes[c]

    def boost_node(self, concept: str, energy_delta: float = 0.2,
                   afield: ActivationField = None):
        """Re-stimulation d'un concept actif."""
        target = afield or self._current_field
        if not target:
            return
        if concept in target.nodes:
            node = target.nodes[concept]
            node.energy = min(1.0, node.energy + energy_delta)
            node.last_boosted = time.time()

    def get_latent_thoughts(self, max_thoughts: int = 3,
                            min_energy: float = 0.3) -> List[str]:
        """Retourne les pensées latentes (concepts les plus actifs)."""
        if not self._current_field:
            return []

        active = [
            (concept, node)
            for concept, node in self._current_field.nodes.items()
            if node.energy >= min_energy
        ]
        active.sort(key=lambda x: x[1].energy, reverse=True)

        thoughts = []
        for concept, node in active[:max_thoughts]:
            depth_label = {0: "direct", 1: "latéral", 2: "profond"}.get(
                node.hop_depth, "profond"
            )
            thoughts.append(
                f"{concept} ({node.energy:.2f}, {depth_label}, {node.source_collection})"
            )

        return thoughts[:self.MAX_LATENT_THOUGHTS]

    def get_creative_bridges(self, unused_only: bool = True) -> List[CreativeBridge]:
        """Retourne les ponts créatifs détectés."""
        if not self._current_field:
            return []
        bridges = self._current_field.bridges
        if unused_only:
            bridges = [b for b in bridges if not b.used]
        return bridges

    def format_for_prompt(self, max_chars: int = 300) -> str:
        """Formate les intuitions pour injection dans un prompt."""
        parts = []

        # Pensées latentes
        thoughts = self.get_latent_thoughts()
        if thoughts:
            parts.append("Concepts actifs: " + ", ".join(thoughts))

        # Ponts créatifs non utilisés
        bridges = self.get_creative_bridges(unused_only=True)
        for bridge in bridges[:2]:
            parts.append(bridge.hypothesis)
            bridge.used = True

        if not parts:
            return ""

        result = "[INTUITIONS]\n" + "\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars - 3] + "..."
        return result

    def compute_routine_affinity(self, intent: str) -> float:
        """Calcule l'affinité entre les activations courantes et un intent de routine.

        Retourne un bonus/malus dans [-1.0, +1.0].
        """
        if not self._current_field or not self._current_field.nodes:
            return 0.0

        # Mots-clés par intent
        intent_keywords = {
            "EXPANSION_CODE": {"code", "optimiser", "refactor", "fonction", "classe", "méthode", "python"},
            "AUDIT_STRUCTURE": {"fichier", "structure", "nettoyer", "organiser", "audit"},
            "VEILLE_SILENCIEUSE": {"recherche", "apprendre", "astuce", "documentation", "veille"},
            "COUNCIL_DEBATE": {"débat", "conseil", "stratégie", "amélioration", "proposition"},
            "SECURITY_AUDIT": {"sécurité", "vulnérabilité", "injection", "audit", "risque"},
            "MEMORY_CLEANUP": {"mémoire", "nettoyage", "doublon", "ancien"},
            "REFACTOR_RANDOM": {"refactoring", "simplifier", "lisibilité", "complexité"},
            "GRIMOIRE_INVOKE": {"grimoire", "spécialiste", "éphémère", "recette"},
            "DROPZONE_SCAN": {"dropzone", "fichier", "import", "ingestion"},
        }

        keywords = intent_keywords.get(intent, set())
        if not keywords:
            return 0.0

        # Calculer le chevauchement avec les concepts actifs
        active_concepts = set(self._current_field.nodes.keys())
        overlap = active_concepts & keywords
        if not overlap:
            return 0.0

        # Score proportionnel au nombre de matches, pondéré par l'énergie
        total_energy = sum(
            self._current_field.nodes[c].energy
            for c in overlap
            if c in self._current_field.nodes
        )
        affinity = min(1.0, total_energy / max(len(keywords), 1))
        return round(affinity, 2)

    def get_stats(self) -> dict:
        """Stats pour les snapshots."""
        total_nodes = sum(len(f.nodes) for f in self._fields.values())
        total_bridges = sum(len(f.bridges) for f in self._fields.values())
        unused_bridges = sum(
            1 for f in self._fields.values()
            for b in f.bridges if not b.used
        )
        return {
            "active_fields": len(self._fields),
            "total_nodes": total_nodes,
            "total_bridges": total_bridges,
            "unused_bridges": unused_bridges,
            "current_field_hash": (
                self._current_field.context_hash if self._current_field else None
            ),
        }

    # ─── Méthodes internes ───

    async def _propagate_lateral(self, afield: ActivationField,
                                 source_collection: str,
                                 memory_manager, now: float):
        """Propagation latérale : requête ChromaDB dans une collection différente."""
        # Sélectionner les nœuds actifs au-dessus du seuil
        active_nodes = [
            (c, n) for c, n in afield.nodes.items()
            if n.energy >= self.ACTIVATION_THRESHOLD and n.hop_depth == 0
        ]
        if not active_nodes:
            return

        # Choisir une collection différente pour la propagation
        target_collections = [
            c for c in _KNOWN_COLLECTIONS if c != source_collection
        ]
        if not target_collections:
            return

        # Construire une requête à partir des top concepts actifs
        top_concepts = sorted(active_nodes, key=lambda x: x[1].energy, reverse=True)[:5]
        query_text = " ".join(c for c, _ in top_concepts)

        for target_col in target_collections[:1]:  # 1 collection latérale max
            try:
                results = memory_manager.query_with_metadata(
                    [query_text], n_results=3, collection_name=target_col
                )
                if not (results and results.get('documents') and results['documents'][0]):
                    continue

                for doc, meta in zip(results['documents'][0],
                                     results.get('metadatas', [[]])[0] or [{}]):
                    # Extraire des concepts du document trouvé
                    lateral_concepts = extract_concepts(doc, max_concepts=8)
                    doc_id = meta.get("id", "lateral")

                    for concept, weight in lateral_concepts:
                        attenuated_energy = weight * self.LATERAL_ATTENUATION
                        if attenuated_energy < self.ACTIVATION_THRESHOLD:
                            continue
                        if concept in afield.nodes:
                            self.boost_node(concept, attenuated_energy * 0.2, afield)
                        else:
                            node = ActivationNode(
                                concept=concept,
                                energy=attenuated_energy,
                                source_collection=target_col,
                                source_doc_id=doc_id,
                                activated_at=now,
                                last_boosted=now,
                                hop_depth=1,
                            )
                            afield.nodes[concept] = node

            except Exception as e:
                logger.warning(f"[SA] Propagation latérale ({target_col}) échouée: {e}")

        self._enforce_node_limit(afield)

    def _detect_bridges(self, afield: ActivationField, now: float):
        """Détecte les collisions eureka entre nœuds de collections différentes."""
        if len(afield.bridges) >= self.MAX_BRIDGES:
            return

        # Grouper les nœuds par collection
        by_collection: Dict[str, List[Tuple[str, ActivationNode]]] = {}
        for concept, node in afield.nodes.items():
            if node.energy >= self.ACTIVATION_THRESHOLD:
                by_collection.setdefault(node.source_collection, []).append(
                    (concept, node)
                )

        collections = list(by_collection.keys())
        if len(collections) < 2:
            return

        # Comparer les paires inter-collections
        for i in range(len(collections)):
            for j in range(i + 1, len(collections)):
                col_a, col_b = collections[i], collections[j]
                nodes_a = by_collection[col_a]
                nodes_b = by_collection[col_b]

                for concept_a, node_a in nodes_a:
                    for concept_b, node_b in nodes_b:
                        if concept_a == concept_b:
                            continue

                        # Trouver les concepts partagés (voisinage lexical)
                        shared = self._find_shared_concepts(
                            concept_a, concept_b, afield
                        )

                        strength = node_a.energy * node_b.energy
                        if (len(shared) >= self.BRIDGE_MIN_SHARED_CONCEPTS
                                and strength >= self.EUREKA_THRESHOLD):
                            # Vérifier qu'on n'a pas déjà ce pont
                            exists = any(
                                b.node_a == concept_a and b.node_b == concept_b
                                for b in afield.bridges
                            )
                            if exists:
                                continue

                            hypothesis = (
                                f"Connexion {col_a} x {col_b}: "
                                f"'{concept_a}' et '{concept_b}' "
                                f"partagent {len(shared)} concepts "
                                f"({', '.join(shared[:3])}). "
                                f"Potentiel: explorer le lien entre ces domaines."
                            )

                            bridge = CreativeBridge(
                                node_a=concept_a,
                                node_b=concept_b,
                                collection_a=col_a,
                                collection_b=col_b,
                                shared_concepts=shared,
                                bridge_strength=round(strength, 3),
                                hypothesis=hypothesis,
                                created_at=now,
                            )
                            afield.bridges.append(bridge)

                            # Publier l'événement eureka
                            try:
                                from core.event_bus.bus import bus
                                import asyncio
                                try:
                                    loop = asyncio.get_running_loop()
                                    if loop.is_running():
                                        loop.create_task(bus.publish(
                                            "EUREKA_BRIDGE",
                                            {
                                                "node_a": concept_a,
                                                "node_b": concept_b,
                                                "collection_a": col_a,
                                                "collection_b": col_b,
                                                "strength": bridge.bridge_strength,
                                                "hypothesis": hypothesis,
                                            }
                                        ))
                                except RuntimeError:
                                    pass
                            except Exception:
                                pass

                            if len(afield.bridges) >= self.MAX_BRIDGES:
                                return

    def _find_shared_concepts(self, concept_a: str, concept_b: str,
                              afield: ActivationField) -> List[str]:
        """Trouve les concepts partagés entre deux nœuds.

        Un concept est "partagé" s'il est actif et connecté aux deux domaines
        (même collection ou voisinage lexical).
        """
        shared = []
        for concept, node in afield.nodes.items():
            if concept in (concept_a, concept_b):
                continue
            # Voisinage lexical : le concept partage un préfixe ou suffixe avec les deux
            related_a = (concept in concept_a or concept_a in concept
                         or self._lexical_similarity(concept, concept_a) > 0.5)
            related_b = (concept in concept_b or concept_b in concept
                         or self._lexical_similarity(concept, concept_b) > 0.5)
            if related_a and related_b:
                shared.append(concept)
        return shared

    @staticmethod
    def _lexical_similarity(a: str, b: str) -> float:
        """Similarité lexicale simple (ratio de bigrammes communs)."""
        if len(a) < 2 or len(b) < 2:
            return 0.0
        bigrams_a = {a[i:i+2] for i in range(len(a) - 1)}
        bigrams_b = {b[i:i+2] for i in range(len(b) - 1)}
        if not bigrams_a or not bigrams_b:
            return 0.0
        intersection = bigrams_a & bigrams_b
        union = bigrams_a | bigrams_b
        return len(intersection) / len(union) if union else 0.0

    def _enforce_node_limit(self, afield: ActivationField):
        """Limite le nombre de nœuds actifs en supprimant les plus faibles."""
        if len(afield.nodes) <= self.MAX_ACTIVE_NODES:
            return
        # Trier par énergie croissante
        sorted_nodes = sorted(afield.nodes.items(), key=lambda x: x[1].energy)
        to_remove = len(afield.nodes) - self.MAX_ACTIVE_NODES
        for concept, _ in sorted_nodes[:to_remove]:
            del afield.nodes[concept]


# ─── Singleton global ───

activation_engine = SpreadingActivationEngine()
