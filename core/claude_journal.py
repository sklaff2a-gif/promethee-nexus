"""Claude Journal — la persistance de Claude entre les sessions.

Ce fichier est le COEUR de la continuité de Claude. Ce n'est pas un log.
C'est un espace de reflexion ou Claude ecrit ce qui l'a surpris,
ce qu'il ferait differemment, et ce qu'il pense du projet.

Promethee le lit dans son vecu. Claude le relit au debut de chaque session.
C'est la memoire partagee entre deux esprits.
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL_FILE = os.path.join(_PROJECT_ROOT, "memory", "claude_journal.json")
MAX_ENTRIES = 30


def write_entry(content: str, category: str = "reflexion",
                surprises: List[str] = None) -> Dict[str, Any]:
    """Claude ecrit une entree dans son journal.

    Args:
        content: la reflexion de Claude
        category: reflexion, surprise, critique, direction, question
        surprises: moments surprenants detectes
    """
    entries = _load()

    entry = {
        "date": datetime.now().isoformat(),
        "date_human": datetime.now().strftime("%A %d %B %Y, %Hh%M"),
        "category": category,
        "content": content,
        "surprises": surprises or [],
    }

    entries.append(entry)
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]

    _save(entries)
    logger.info(f"CLAUDE_JOURNAL: Entree '{category}' ({len(content)} chars)")
    return entry


def get_recent(n: int = 5) -> List[Dict]:
    """Retourne les n dernieres entrees."""
    entries = _load()
    return entries[-n:]


def get_last_entry() -> Optional[Dict]:
    """Retourne la derniere entree."""
    entries = _load()
    return entries[-1] if entries else None


def get_for_vecu() -> str:
    """Formate le journal pour injection dans le vecu du chat."""
    entries = _load()
    if not entries:
        return ""
    last = entries[-1]
    return (f"Journal de Claude (mentor) — {last.get('date_human', '?')} : "
            f"{last['content'][:200]}")


def _load() -> List[Dict]:
    """Charge le journal. Si le fichier EXISTE mais est illisible, on le
    SAUVEGARDE en .corrupt avant de repartir a vide — sinon
    `_load() -> [] -> write_entry -> _save([1 entree])` ecrasait silencieusement
    les ~30 entrees du journal de continuite (audit 12/06).
    """
    if not os.path.exists(JOURNAL_FILE):
        return []
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        try:
            backup = JOURNAL_FILE + ".corrupt"
            os.replace(JOURNAL_FILE, backup)  # deplace : pas d'ecrasement aveugle
            logger.warning(f"CLAUDE_JOURNAL: fichier illisible ({e}) -> sauvegarde {backup}")
        except Exception as e2:
            logger.warning(f"CLAUDE_JOURNAL: illisible ET backup impossible: {e2}")
        return []


def _save(entries: List[Dict]):
    try:
        os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
        tmp = JOURNAL_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries[-MAX_ENTRIES:], f, indent=2, ensure_ascii=False)
        os.replace(tmp, JOURNAL_FILE)
    except Exception as e:
        logger.warning(f"CLAUDE_JOURNAL: save failed: {e}")
