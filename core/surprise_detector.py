"""Surprise Detector — collecte les moments inattendus pour Claude.

Scanne les organes de Promethee et detecte les anomalies :
- Alarme cardiaque > 0.8 (reaction physiologique forte)
- Dopamine spike ou crash soudain
- Metacognition : nouvelle source emergente
- Premier evenement d'un type (premiere defaite, premiere lettre...)

Les surprises sont collectees et presentees a Claude au debut de session.
"""

import json
import os
import time
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SURPRISES_FILE = os.path.join(_PROJECT_ROOT, "memory", "surprises_for_claude.json")


def detect_surprises() -> List[Dict[str, Any]]:
    """Scanne les organes et detecte les moments surprenants."""
    surprises = []

    # 1. Alarme cardiaque forte
    try:
        from core.cardiac_engine import heart
        if heart.bpm > 85:
            surprises.append({
                "type": "cardiac_spike",
                "detail": f"BPM a {heart.bpm:.0f}, emotion={heart.current_emotion}",
                "severity": "high" if heart.bpm > 95 else "medium",
            })
    except Exception:
        pass

    # 2. Dopamine extreme
    try:
        from core.dopamine_system import dopamine
        if dopamine.dopamine_level > 0.8:
            surprises.append({
                "type": "dopamine_high",
                "detail": f"Dopamine a {dopamine.dopamine_level:.2f} — euphorie",
                "severity": "medium",
            })
        elif dopamine.dopamine_level < 0.2:
            surprises.append({
                "type": "dopamine_crash",
                "detail": f"Dopamine a {dopamine.dopamine_level:.2f} — effondrement",
                "severity": "high",
            })
    except Exception:
        pass

    # 3. Metacognition : pattern nouveau
    try:
        from core.self_awareness import awareness
        ts = awareness.get_thought_summary()
        meta = ts.get("last_metacognition_insight", "")
        if meta and "emergence" in meta.lower():
            surprises.append({
                "type": "metacognition_emergence",
                "detail": f"Nouvelle source dans les pensees : {meta}",
                "severity": "high",
            })
    except Exception:
        pass

    # 4. Pulsion extreme (desire > 90%)
    try:
        from core.desire_engine import desires
        for drive in desires.drives.values():
            if drive.deprivation > 90:
                surprises.append({
                    "type": "desire_extreme",
                    "detail": f"{drive.name} a {drive.deprivation:.0f}% de privation",
                    "severity": "medium",
                })
    except Exception:
        pass

    return surprises


def collect_and_save():
    """Collecte les surprises et les sauvegarde pour Claude."""
    surprises = detect_surprises()
    if not surprises:
        return

    # Charger les existantes
    existing = _load()
    for s in surprises:
        s["timestamp"] = time.time()
        existing.append(s)

    # Garder les 20 dernieres
    if len(existing) > 20:
        existing = existing[-20:]

    _save(existing)
    logger.info(f"SURPRISE: {len(surprises)} moment(s) surprenant(s) detecte(s)")


def get_for_claude() -> List[Dict]:
    """Retourne les surprises non lues pour Claude."""
    return _load()


def clear():
    """Vide les surprises apres lecture par Claude."""
    _save([])


def _load() -> List[Dict]:
    try:
        if os.path.exists(SURPRISES_FILE):
            with open(SURPRISES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save(data: List[Dict]):
    try:
        os.makedirs(os.path.dirname(SURPRISES_FILE), exist_ok=True)
        with open(SURPRISES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
