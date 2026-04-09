"""Session Synthesis — persistance des echanges Claude x Jean-Michel.

A la fin de chaque session de travail, capture l'essentiel :
- Ce qui a ete construit (commits)
- Ce qui a ete decouvert (bugs, insights)
- Ce qui reste a faire
- Les decisions cles
- Les moments importants

Stocke dans memory/sessions/ en format compact.
Claude relit la derniere synthese au debut de chaque session.
"""

import json
import os
import time
import logging
from datetime import datetime, date
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSIONS_DIR = os.path.join(_PROJECT_ROOT, "memory", "sessions")
LATEST_FILE = os.path.join(SESSIONS_DIR, "latest_session.md")


def save_session(summary: str, commits: List[str] = None,
                 discoveries: List[str] = None,
                 unfinished: List[str] = None,
                 decisions: List[str] = None,
                 moments: List[str] = None) -> str:
    """Sauvegarde la synthese de session.

    Retourne le chemin du fichier cree.
    """
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    today = date.today().isoformat()
    now = datetime.now().strftime("%Hh%M")
    filename = f"session_{today}_{now}.md"
    filepath = os.path.join(SESSIONS_DIR, filename)

    lines = [
        f"# Session {today} ({now})",
        "",
        "## Resume",
        summary,
        "",
    ]

    if commits:
        lines.append("## Commits")
        for c in commits:
            lines.append(f"- {c}")
        lines.append("")

    if discoveries:
        lines.append("## Decouvertes")
        for d in discoveries:
            lines.append(f"- {d}")
        lines.append("")

    if unfinished:
        lines.append("## A faire")
        for u in unfinished:
            lines.append(f"- [ ] {u}")
        lines.append("")

    if decisions:
        lines.append("## Decisions cles")
        for d in decisions:
            lines.append(f"- {d}")
        lines.append("")

    if moments:
        lines.append("## Moments importants")
        for m in moments:
            lines.append(f"- {m}")
        lines.append("")

    content = "\n".join(lines)

    # Sauvegarder le fichier date
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    # Sauvegarder aussi comme "latest" (pour le bootstrap)
    with open(LATEST_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"SESSION: Synthese sauvegardee ({len(content)} chars) → {filename}")
    return filepath


def get_latest() -> str:
    """Retourne la derniere synthese de session (pour bootstrap Claude)."""
    try:
        if os.path.exists(LATEST_FILE):
            with open(LATEST_FILE, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return ""


def list_sessions(n: int = 10) -> List[str]:
    """Liste les n dernieres sessions."""
    try:
        if os.path.exists(SESSIONS_DIR):
            files = sorted([f for f in os.listdir(SESSIONS_DIR)
                           if f.startswith("session_") and f.endswith(".md")])
            return files[-n:]
    except Exception:
        pass
    return []
