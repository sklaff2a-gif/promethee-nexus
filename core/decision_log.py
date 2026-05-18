"""Décision Log — Télémétrie centralisée des refus métier silencieux.

Origine : 18/05/2026, suite à découverte des "trous noirs architecturaux" lors
de l'audit déclenché par la 5e preuve doctrinale §4.13 (refus silencieux Couche 3
PHASEUR sur \\bquand\\b). Doctrine T1/T2/T3 validée sur prefrontal.py (40-54%
critiques) et hippocampus.py (4% critiques) — calibrage cross-profil opérationnel.

Loggue UNIQUEMENT les refus métier silencieux d'opérations centrales :
- handlers d'événements bus avec quotas/anti-doublon/filtres
- API publiques avec rejets non-tracés
- seuils homéostatiques dépassés sans publish

Ne loggue JAMAIS :
- les defaults de getters (return [], None, "", {}) — c'est du contrat d'API
- les results de computational dispatchers (if event == X: return f"...")
- les singletons __new__ et patterns structuraux

Fichier de sortie : logs/decisions.jsonl (1 ligne JSON par décision tracée).
Rotation auto au-delà de 50 MB (renommé avec suffix timestamp).
"""
import json
import logging
import os
import random
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("decision_log")

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "logs", "decisions.jsonl"
)

# Rotation à 50 MB (au-delà, le fichier est renommé avec suffix timestamp)
ROTATION_SIZE_BYTES = 50 * 1024 * 1024


def _rotate_if_needed() -> None:
    """Si le fichier dépasse ROTATION_SIZE_BYTES, le renomme avec suffix ts."""
    try:
        if not os.path.exists(LOG_FILE):
            return
        if os.path.getsize(LOG_FILE) < ROTATION_SIZE_BYTES:
            return
        suffix = time.strftime("%Y%m%d_%H%M%S")
        rotated = f"{LOG_FILE}.{suffix}"
        os.rename(LOG_FILE, rotated)
        logger.info(f"[DECISION_LOG] Rotation auto : {LOG_FILE} -> {rotated}")
    except Exception as e:
        logger.debug(f"decision_log rotation skipped: {e}")


def log_decision(
    module: str,
    function: str,
    reason: str,
    context: Optional[Dict[str, Any]] = None,
    sample_rate: float = 1.0,
) -> bool:
    """Trace un refus métier silencieux dans logs/decisions.jsonl.

    Args:
        module: nom du module (ex: "prefrontal", "hippocampus")
        function: nom de la fonction où le refus a lieu
        reason: code court identifiant la raison (snake_case, ex: "max_goals_reached")
        context: dict de métadonnées pour diagnostic post-hoc (optionnel)
        sample_rate: taux d'échantillonnage 0.0-1.0 (1.0 = toujours, 0.01 = 1%)

    Returns:
        True si la ligne a été effectivement écrite, False si filtrée par sampling
        ou par échec I/O silencieux.

    Garantie : ne lève jamais d'exception (try/except permissif pour ne pas
    perturber le flux métier appelant).
    """
    # Sampling — exit fast si on n'écrit pas cet appel
    if sample_rate < 1.0 and random.random() >= sample_rate:
        return False

    payload = {
        "ts": time.time(),
        "module": module,
        "function": function,
        "reason": reason,
        "context": context or {},
    }
    if sample_rate < 1.0:
        payload["sampled"] = True
        payload["sample_rate"] = sample_rate

    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        _rotate_if_needed()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logger.debug(f"decision_log write skipped: {e}")
        return False
