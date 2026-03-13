# core/organ_registry.py — Registre central des organes
# Centralise l'acces aux singletons pour :
# 1. Remplacer les imports locaux directs (try/except boilerplate)
# 2. Faciliter le mocking dans les tests (reset_registry)
# 3. Contrôler le graphe de dépendances
#
# MIGRATION PROGRESSIVE :
# Ancien pattern (239 occurrences) :
#   try:
#       from core.cardiac_engine import heart
#       heart.react("success")
#   except Exception:
#       pass
#
# Nouveau pattern :
#   heart = get_organ("cardiac")
#   if heart:
#       heart.react("success")

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("OrganRegistry")

_registry: Dict[str, Any] = {}


def register_organ(name: str, organ: Any):
    """Enregistre un organe dans le registre central.
    Appelé par chaque singleton à son instanciation."""
    _registry[name] = organ
    logger.debug(f"[REGISTRY] Organe '{name}' enregistré ({type(organ).__name__})")


def get_organ(name: str) -> Optional[Any]:
    """Accède à un organe de manière sûre.
    Retourne None si l'organe n'est pas (encore) initialisé.

    Fallback : si l'organe n'est pas dans le registre, tente un import local.
    """
    organ = _registry.get(name)
    if organ is not None:
        return organ

    # Fallback : import dynamique (pour la rétro-compatibilité)
    return _import_organ_fallback(name)


def _import_organ_fallback(name: str) -> Optional[Any]:
    """Tente d'importer un organe par son nom court.
    Table de correspondance nom_court -> (module_path, instance_name)."""
    _ORGAN_MAP = {
        "cardiac": ("core.cardiac_engine", "heart"),
        "desire": ("core.desire_engine", "desires"),
        "psyche": ("core.psyche", "psyche"),
        "prefrontal": ("core.prefrontal", "prefrontal"),
        "inner_voice": ("core.inner_voice", "voice"),
        "dopamine": ("core.dopamine_system", "dopamine"),
        "callosum": ("core.corpus_callosum", "callosum"),
        "synaptic": ("core.synaptic_network", "cortex"),
        "hypothalamus": ("core.hypothalamus", "hypothalamus"),
        "insula": ("core.insula", "insula"),
        "cingulate": ("core.cingulate_cortex", "cingulate"),
        "basal_ganglia": ("core.basal_ganglia", "ganglia"),
        "dmn": ("core.default_mode_network", "dmn"),
        "reptilian": ("core.reptilian_core", "reptile"),
        "awareness": ("core.self_awareness", "awareness"),
        "spreading": ("core.spreading_activation", "activation_engine"),
        "thalamus": ("core.thalamus", "thalamus"),
        "amygdala": ("core.amygdala", "amygdala"),
        "sensorium": ("core.sensorium", "sensorium"),
        "incubation": ("core.incubation_cognitive", "incubation"),
        "curiosity": ("core.curiosity_reflex", "curiosity"),
        "tissue": ("core.neural_tissue", "tissue"),
        "roadmap": ("core.roadmap_engine", "roadmap"),
        "objectives": ("core.objectives_engine", "objectives"),
        "hippocampus": ("core.hippocampus", "hippocampus"),
        "temporal": ("core.temporal_lobe", "temporal"),
        "school": ("core.school_schedule", "schedule"),
        "visual_cortex": ("core.visual_cortex", "vision"),
    }
    mapping = _ORGAN_MAP.get(name)
    if not mapping:
        return None
    module_path, instance_name = mapping
    try:
        mod = __import__(module_path, fromlist=[instance_name])
        organ = getattr(mod, instance_name, None)
        if organ is not None:
            # Auto-register pour les prochains appels
            _registry[name] = organ
        return organ
    except Exception:
        return None


def get_all_organs() -> Dict[str, Any]:
    """Retourne tous les organes enregistrés (pour debug/inspection)."""
    return dict(_registry)


def reset_registry():
    """Vide le registre. Appelé dans les fixtures de test."""
    _registry.clear()


def get_dependency_graph() -> Dict[str, list]:
    """Retourne le graphe de dépendances statique (pour tests guardrail).
    Basé sur la table _ORGAN_MAP, pas sur les imports réels."""
    return {name: [] for name in _import_organ_fallback.__code__.co_consts
            if isinstance(name, str) and name in _registry}
