"""Phase 3 — Compression semantique + Phase 3.5 — Assimilation.

Friston/Predictive Coding implementation : un organisme a "compris" un
document quand il peut le compresser via ses acquis. Sinon, il doit
INTEGRER ce qu il n a pas predit, sans quoi la douleur epistemique devient
infinie sans apprentissage (angle mort de la version initiale —
correction Jean-Michel 2026-05-07).

Phase 3 (verdict, zero LLM) :
    ratio = |concepts_reconnus| / |concepts_extraits|
    - ratio < 0.2  -> douleur epistemique (DOPAMINE_DIP)
    - 0.2 <= ratio < 0.6 -> micro-consolidation
    - ratio >= 0.6 -> consolidation pleine (DOPAMINE_SURGE)

Phase 3.5 (assimilation, zero LLM) :
    Les concepts inconnus sont injectes comme floating_concepts dans le
    synaptic_network avec un poids initial faible. Au prochain document
    qui les mentionnera, ils seront reconnus et le ratio montera. C est
    la mecanique d aplatissement de la courbe de surprise.

Note critique : si total_concepts_extraits == 0 (tous les chunks ont
retourne RIEN), le ratio n est PAS 0 — il est N/A. Le document doit
etre ignore sans penalite (is_meaningful=False).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set


# Poids initial des floating_concepts — aligne sur le plancher synaptic
FLOATING_CONCEPT_INITIAL_ENERGY = 0.080
FLOATING_CONCEPT_NODE_TYPE = "concept"
FLOATING_CONCEPT_FUNCTIONAL_SYSTEMS = ("immersion", "floating")


@dataclass
class CompressionResult:
    """Verdict d ingestion d un document immersion."""
    total_extracted: int
    recognized: List[str] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)
    ratio: Optional[float] = None
    is_meaningful: bool = True

    @property
    def verdict(self) -> str:
        """Categorisation pour la Phase 4 (tir Hebbien)."""
        if not self.is_meaningful:
            return "ignored"
        if self.ratio is None:
            return "ignored"
        if self.ratio < 0.2:
            return "pain"
        if self.ratio < 0.6:
            return "micro_consolidation"
        return "full_consolidation"


def _normalize_concept(concept: str) -> str:
    """Forme canonique d un concept pour matching exact.

    Comparaison case-insensitive, espaces et soulignes equivalents.
    Conserve la casse originale dans recognized/unknown pour la trace.
    """
    return concept.strip().lower().replace("_", " ")


def compute_compression(
    extracted_concepts: Iterable[str],
    known_concepts: Iterable[str],
) -> CompressionResult:
    """Phase 3 : ratio de compression semantique.

    Args:
        extracted_concepts : concepts saillants sortis de la Phase 2
            (extractor.py). Peut contenir des doublons — sera dedup-e.
        known_concepts : ensemble des concepts deja presents dans le
            synaptic_network (precalcule par digestion_routine.py pour
            performance — eviter une iteration sur 909 noeuds par doc).

    Returns:
        CompressionResult avec ratio, recognized, unknown, verdict.
    """
    # Dedup en preservant l ordre d apparition (utile pour audit)
    seen_norm: Set[str] = set()
    extracted_unique: List[str] = []
    for c in extracted_concepts:
        c_clean = c.strip()
        if not c_clean:
            continue
        norm = _normalize_concept(c_clean)
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        extracted_unique.append(c_clean)

    total = len(extracted_unique)
    if total == 0:
        return CompressionResult(
            total_extracted=0,
            recognized=[],
            unknown=[],
            ratio=None,
            is_meaningful=False,
        )

    known_norm: Set[str] = {_normalize_concept(k) for k in known_concepts if k}

    recognized: List[str] = []
    unknown: List[str] = []
    for c in extracted_unique:
        if _normalize_concept(c) in known_norm:
            recognized.append(c)
        else:
            unknown.append(c)

    ratio = len(recognized) / total
    return CompressionResult(
        total_extracted=total,
        recognized=recognized,
        unknown=unknown,
        ratio=ratio,
        is_meaningful=True,
    )


def assimilate_unknowns(
    unknown_concepts: List[str],
    synaptic_network,  # type: core.synaptic_network.SynapticNetwork
    initial_energy: float = FLOATING_CONCEPT_INITIAL_ENERGY,
) -> int:
    """Phase 3.5 : injecte les concepts inconnus comme floating_concepts.

    Pour chaque concept absent du synaptic_network, on cree un node neuf
    avec une energie minimale (poids plancher 0.080). Au prochain document
    qui contiendra ce concept, il sera reconnu en Phase 3, le ratio
    montera, et la Phase 4 declenchera un renforcement Hebbien sur la
    synapse routine:immersion_domain -> concept:X.

    Args:
        unknown_concepts : liste deduplique des concepts inconnus
            (sortie de compute_compression().unknown).
        synaptic_network : instance SynapticNetwork du runtime.
        initial_energy : energie initiale du noeud floating.

    Returns:
        Nombre de noeuds reellement crees (un concept deja injecte par
        un autre cycle ne sera pas dupplique grace a ensure_node).
    """
    if not unknown_concepts:
        return 0

    created = 0
    for concept in unknown_concepts:
        c_clean = concept.strip()
        if not c_clean:
            continue
        # ensure_node est idempotent : retourne le node_id existant si
        # le concept a deja un noeud, ou en cree un sinon.
        nid_before = _node_exists(synaptic_network, c_clean)
        try:
            synaptic_network.ensure_node(
                c_clean,
                FLOATING_CONCEPT_NODE_TYPE,
                initial_energy,
                list(FLOATING_CONCEPT_FUNCTIONAL_SYSTEMS),
            )
        except Exception:
            # Defensif : un concept malforme ne doit pas faire planter
            # la routine d immersion. On laisse le comptage refleter la
            # realite.
            continue
        if not nid_before:
            created += 1
    return created


def _node_exists(synaptic_network, concept: str) -> bool:
    """Verifie si un concept est deja un noeud (par concept exact).

    Helper local pour compter precisement les nouveaux floating_concepts.
    """
    try:
        # On reutilise la canonicalisation interne du synaptic via
        # _make_node_id si disponible.
        from core.synaptic_network import _make_node_id  # local import
        nid = _make_node_id(concept)
        return nid in synaptic_network.nodes
    except Exception:
        return False
