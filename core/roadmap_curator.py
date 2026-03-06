# core/roadmap_curator.py — Roadmap Curator : Utilitaire Stateless
# Analyse roadmap.json, detecte modules bloques, suggere priorites,
# met a jour les statuts automatiquement.
# PAS un singleton organe — fonctions pures, 0 LLM.

import json
import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("RoadmapCurator")

# --- Constantes ---

ROADMAP_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "roadmap.json"
)

VALID_STATUSES = ["planned", "researching", "ready", "implementing", "implemented", "blocked"]

PHASE_NAMES = {
    1: "FONDATIONS",
    2: "ORGANES MANQUANTS",
    3: "SIGNAL BUS",
    4: "VM NEURALE",
    5: "NEUROCHIMIE",
    6: "VIRTUALISATION LLM",
    7: "CONSCIENCE & EMERGENCE",
}


def load_roadmap(filepath: Optional[str] = None) -> dict:
    """Charge le fichier roadmap.json. Retourne {} en cas d'erreur."""
    path = filepath or ROADMAP_FILE
    try:
        if not os.path.exists(path):
            logger.warning(f"[CURATOR] Fichier roadmap introuvable: {path}")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        logger.warning(f"[CURATOR] JSON invalide dans roadmap: {e}")
        return {}
    except Exception as e:
        logger.warning(f"[CURATOR] Erreur chargement roadmap: {e}")
        return {}


def _get_modules(filepath: Optional[str] = None) -> List[dict]:
    """Retourne la liste des modules depuis la roadmap."""
    data = load_roadmap(filepath)
    return data.get("modules", [])


def _get_implemented_ids(modules: List[dict]) -> set:
    """Retourne les IDs des modules implementes."""
    return {m["id"] for m in modules if m.get("status") == "implemented"}


def get_next_candidates(filepath: Optional[str] = None) -> List[dict]:
    """Retourne les modules prets (dependances satisfaites, pas encore implementes)."""
    modules = _get_modules(filepath)
    if not modules:
        return []

    implemented = _get_implemented_ids(modules)
    candidates = []

    for m in modules:
        status = m.get("status", "planned")
        if status == "implemented":
            continue

        deps = m.get("depends_on", [])
        # Toutes les deps doivent etre implementees
        deps_satisfied = all(d in implemented for d in deps)
        if deps_satisfied:
            candidates.append(m)

    return candidates


def get_blocked_modules(filepath: Optional[str] = None) -> List[dict]:
    """Retourne les modules bloques avec la raison du blocage."""
    modules = _get_modules(filepath)
    if not modules:
        return []

    implemented = _get_implemented_ids(modules)
    blocked = []

    for m in modules:
        status = m.get("status", "planned")
        if status == "implemented":
            continue

        deps = m.get("depends_on", [])
        missing = [d for d in deps if d not in implemented]
        if missing:
            blocked.append({
                "id": m["id"],
                "phase": m.get("phase", 0),
                "phase_name": m.get("phase_name", ""),
                "description": m.get("description", ""),
                "status": status,
                "blocked_by": missing,
                "reason": f"Dependances non implementees: {', '.join(missing)}",
            })

    return blocked


def suggest_priority_order(candidates: Optional[List[dict]] = None,
                           filepath: Optional[str] = None) -> List[dict]:
    """Trie les candidats par priorite. Criteres:
    1. Phase la plus basse d'abord
    2. Modules sans dependances d'abord
    3. Modules avec le plus de dependants (impact) d'abord
    4. Cout estime le plus bas d'abord (quick wins)
    """
    if candidates is None:
        candidates = get_next_candidates(filepath)

    if not candidates:
        return []

    # Calculer l'impact de chaque candidat (combien de modules en dependent)
    all_modules = _get_modules(filepath)
    dependant_count = {}
    for m in all_modules:
        for dep in m.get("depends_on", []):
            dependant_count[dep] = dependant_count.get(dep, 0) + 1

    def sort_key(m):
        phase = m.get("phase", 99)
        deps_count = len(m.get("depends_on", []))
        impact = -dependant_count.get(m["id"], 0)  # negatif pour tri descendant
        cost = m.get("estimated_cost", 5)
        return (phase, deps_count, impact, cost)

    return sorted(candidates, key=sort_key)


def update_module_status(module_id: str, new_status: str,
                         filepath: Optional[str] = None) -> bool:
    """Met a jour le statut d'un module. Retourne True si reussi."""
    if new_status not in VALID_STATUSES:
        logger.warning(f"[CURATOR] Statut invalide: {new_status}")
        return False

    path = filepath or ROADMAP_FILE
    data = load_roadmap(path)
    if not data:
        return False

    modules = data.get("modules", [])
    for m in modules:
        if m["id"] == module_id:
            m["status"] = new_status
            m["updated_at"] = datetime.now().isoformat()
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, path)
                logger.info(f"[CURATOR] Module {module_id} → {new_status}")
                return True
            except Exception as e:
                logger.warning(f"[CURATOR] Ecriture echouee: {e}")
                return False

    logger.warning(f"[CURATOR] Module introuvable: {module_id}")
    return False


def get_phase_progress(filepath: Optional[str] = None) -> Dict[int, dict]:
    """Retourne le pourcentage de completion par phase."""
    modules = _get_modules(filepath)
    if not modules:
        return {}

    phases: Dict[int, dict] = {}
    for m in modules:
        phase = m.get("phase", 0)
        if phase not in phases:
            phases[phase] = {
                "name": m.get("phase_name", PHASE_NAMES.get(phase, f"Phase {phase}")),
                "total": 0,
                "implemented": 0,
                "in_progress": 0,
            }
        phases[phase]["total"] += 1
        status = m.get("status", "planned")
        if status == "implemented":
            phases[phase]["implemented"] += 1
        elif status in ("implementing", "researching", "ready"):
            phases[phase]["in_progress"] += 1

    # Calculer le pourcentage
    for phase_data in phases.values():
        total = phase_data["total"]
        if total > 0:
            phase_data["progress"] = round(phase_data["implemented"] / total, 2)
        else:
            phase_data["progress"] = 0.0

    return phases


def get_dependency_graph(filepath: Optional[str] = None) -> Dict[str, Any]:
    """Retourne le graphe de dependances pour visualisation."""
    modules = _get_modules(filepath)
    if not modules:
        return {"nodes": [], "edges": []}

    nodes = []
    edges = []
    for m in modules:
        nodes.append({
            "id": m["id"],
            "phase": m.get("phase", 0),
            "status": m.get("status", "planned"),
            "cost": m.get("estimated_cost", 5),
        })
        for dep in m.get("depends_on", []):
            edges.append({"from": dep, "to": m["id"]})

    return {"nodes": nodes, "edges": edges}


def validate_roadmap_integrity(filepath: Optional[str] = None) -> List[str]:
    """Detecte les erreurs et incoherences dans la roadmap."""
    modules = _get_modules(filepath)
    if not modules:
        return ["Roadmap vide ou introuvable"]

    errors = []
    all_ids = {m["id"] for m in modules}

    for m in modules:
        mid = m.get("id", "???")

        # Dependances vers des modules inexistants
        for dep in m.get("depends_on", []):
            if dep not in all_ids:
                errors.append(f"Module '{mid}' depend de '{dep}' qui n'existe pas")

        # Statut invalide
        status = m.get("status", "")
        if status not in VALID_STATUSES:
            errors.append(f"Module '{mid}' a un statut invalide: '{status}'")

        # Champs obligatoires
        for field in ("id", "phase", "status"):
            if field not in m:
                errors.append(f"Module sans champ '{field}': {mid}")

    # Dependances circulaires (detection simple)
    def has_cycle(start, visited=None):
        if visited is None:
            visited = set()
        if start in visited:
            return True
        visited.add(start)
        mod = next((m for m in modules if m["id"] == start), None)
        if mod:
            for dep in mod.get("depends_on", []):
                if has_cycle(dep, visited.copy()):
                    return True
        return False

    for m in modules:
        if has_cycle(m["id"]):
            errors.append(f"Dependance circulaire detectee impliquant '{m['id']}'")

    return errors
