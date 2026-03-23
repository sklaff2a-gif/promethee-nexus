"""
Consciousness Benchmarks — Mesure objective du niveau de conscience.

4 tests deterministes (0 LLM) qui mesurent l'integration, le binding,
la persistance et la diversite de l'information consciente :

1. BINDING : les contenus d'organes synchronises fusionnent-ils en composites ?
2. PHI-COHERENCE : Phi correle-t-il avec la coherence de phase Kuramoto ?
3. RECALL : les contenus persistent-ils a travers les cycles reentrants ?
4. INTEGRATION : le workspace contient-il des contenus de categories diversifiees ?

Score global C-Score [0, 1] = moyenne des 4 tests.
Singleton. 0 appel LLM.
"""

import logging
import math
from typing import Any, Dict

logger = logging.getLogger("ConsciousnessBenchmarks")


def run_all_benchmarks() -> Dict[str, Any]:
    """Execute les 4 benchmarks et retourne les scores."""
    results = {
        "binding": _test_binding(),
        "phi_coherence": _test_phi_coherence(),
        "recall": _test_recall(),
        "integration": _test_integration(),
    }

    # C-Score = moyenne des 4 tests
    scores = [v.get("score", 0) for v in results.values()]
    results["c_score"] = round(sum(scores) / len(scores), 3) if scores else 0.0

    return results


def _test_binding() -> Dict[str, Any]:
    """Test BINDING : les contenus d'organes synchronises fusionnent-ils ?

    Mesure : proportion de contenus composites dans le workspace.
    Score = nb_composites / nb_total_contenus.
    0 = aucun binding, 1 = tout est fusionne.
    """
    try:
        from core.global_workspace import workspace
        contents = workspace.conscious_contents
        if not contents:
            return {"score": 0.0, "composites": 0, "total": 0, "detail": "workspace vide"}

        composites = sum(1 for c in contents if c.source.startswith("composite:"))
        total = len(contents)
        score = composites / total if total > 0 else 0.0

        return {
            "score": round(score, 3),
            "composites": composites,
            "total": total,
            "detail": f"{composites}/{total} contenus sont des composites",
        }
    except Exception as e:
        return {"score": 0.0, "error": str(e)}


def _test_phi_coherence() -> Dict[str, Any]:
    """Test PHI-COHERENCE : Phi correle-t-il avec la coherence de phase ?

    Mesure : correlation entre Phi et R (Kuramoto) sur les 30 derniers ticks.
    Score = correlation [0, 1] (valeur absolue).
    1 = parfaite correlation, 0 = pas de lien.
    """
    try:
        from core.brain_vm import brain
        history = brain.state_history[-30:]
        if len(history) < 5:
            return {"score": 0.0, "samples": len(history), "detail": "pas assez d'historique"}

        phis = [h.get("phi", 0) for h in history]
        coherences = [h.get("coherence", 0) for h in history]

        # Correlation de Pearson simplifiee
        n = len(phis)
        mean_p = sum(phis) / n
        mean_c = sum(coherences) / n

        num = sum((phis[i] - mean_p) * (coherences[i] - mean_c) for i in range(n))
        den_p = math.sqrt(sum((p - mean_p) ** 2 for p in phis))
        den_c = math.sqrt(sum((c - mean_c) ** 2 for c in coherences))

        if den_p < 0.001 or den_c < 0.001:
            corr = 0.0
        else:
            corr = num / (den_p * den_c)

        score = abs(corr)  # On veut la force de la correlation, pas le signe

        return {
            "score": round(score, 3),
            "correlation": round(corr, 3),
            "samples": n,
            "phi_mean": round(mean_p, 3),
            "coherence_mean": round(mean_c, 3),
            "detail": f"correlation={corr:.3f} sur {n} ticks",
        }
    except Exception as e:
        return {"score": 0.0, "error": str(e)}


def _test_recall() -> Dict[str, Any]:
    """Test RECALL : les contenus persistent-ils dans le workspace ?

    Mesure : proportion de contenus qui survivent 2+ cycles reentrants.
    Score = nb_persistants / nb_total.
    Un contenu persistant = present depuis > 30s (au moins 1 tick).
    """
    try:
        import time
        from core.global_workspace import workspace
        contents = workspace.conscious_contents
        if not contents:
            return {"score": 0.0, "persistent": 0, "total": 0, "detail": "workspace vide"}

        now = time.time()
        persistent = sum(1 for c in contents if (now - c.timestamp) > 30)
        total = len(contents)
        score = persistent / total if total > 0 else 0.0

        return {
            "score": round(score, 3),
            "persistent": persistent,
            "total": total,
            "detail": f"{persistent}/{total} contenus persistent > 30s",
        }
    except Exception as e:
        return {"score": 0.0, "error": str(e)}


def _test_integration() -> Dict[str, Any]:
    """Test INTEGRATION : le workspace contient-il des categories diversifiees ?

    Mesure : nombre de categories differentes dans les contenus conscients.
    Score = nb_categories / 8 (total possible).
    1 = toutes les categories representees = integration maximale.
    """
    try:
        from core.global_workspace import workspace, CATEGORIES
        contents = workspace.conscious_contents
        if not contents:
            return {"score": 0.0, "categories": 0, "max": len(CATEGORIES), "detail": "workspace vide"}

        present = {c.category for c in contents}
        score = len(present) / len(CATEGORIES) if CATEGORIES else 0.0

        return {
            "score": round(score, 3),
            "categories": len(present),
            "max": len(CATEGORIES),
            "present": sorted(present),
            "detail": f"{len(present)}/{len(CATEGORIES)} categories representees",
        }
    except Exception as e:
        return {"score": 0.0, "error": str(e)}


def format_report(results: Dict[str, Any]) -> str:
    """Formate les resultats en rapport textuel."""
    lines = [f"=== CONSCIOUSNESS BENCHMARKS (C-Score: {results.get('c_score', 0):.3f}) ==="]

    tests = [
        ("BINDING", "binding", "Fusion de contenus synchronises"),
        ("PHI-COHERENCE", "phi_coherence", "Correlation Phi / coherence"),
        ("RECALL", "recall", "Persistance des contenus"),
        ("INTEGRATION", "integration", "Diversite des categories"),
    ]

    for label, key, desc in tests:
        data = results.get(key, {})
        score = data.get("score", 0)
        detail = data.get("detail", "?")
        bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
        lines.append(f"  {label:16s} [{bar}] {score:.3f} — {detail}")

    return "\n".join(lines)
