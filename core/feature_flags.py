"""
Feature flags : aiguillage runtime de modules sans modifier le code.

Lecture depuis config/feature_flags.json. Hot-reload sur changement (mtime),
donc on peut switcher live en éditant le JSON puis attendre la prochaine
lecture (~max 30s ou immédiat selon le code appelant).

Usage :
    from core.feature_flags import get_flag
    engine_kind = get_flag("soliloque_engine", default="v2")
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("FeatureFlags")

FLAGS_FILE = Path(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "feature_flags.json"
    )
)

# Cache et hot-reload
_cache: Dict[str, Any] = {}
_cache_mtime: float = 0.0
_lock = threading.Lock()


def _load_if_changed() -> None:
    """Recharge le fichier si son mtime a changé (hot-reload)."""
    global _cache, _cache_mtime
    try:
        if not FLAGS_FILE.exists():
            return
        mtime = FLAGS_FILE.stat().st_mtime
        if mtime == _cache_mtime and _cache:
            return
        with open(FLAGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Filtre les clés _doc, _version, etc.
        flags = {k: v for k, v in data.items() if not k.startswith("_")}
        with _lock:
            _cache = flags
            _cache_mtime = mtime
        logger.info(f"[FLAGS] Rechargé : {list(flags.keys())}")
    except Exception as e:
        logger.warning(f"[FLAGS] Lecture échouée : {e}")


def get_flag(name: str, default: Any = None) -> Any:
    """Retourne la valeur du flag, ou default si absent/erreur."""
    _load_if_changed()
    return _cache.get(name, default)


def get_all() -> Dict[str, Any]:
    """Retourne tous les flags (snapshot)."""
    _load_if_changed()
    return dict(_cache)


def reset_cache() -> None:
    """Pour les tests : force un rechargement au prochain get_flag()."""
    global _cache, _cache_mtime
    with _lock:
        _cache = {}
        _cache_mtime = 0.0
