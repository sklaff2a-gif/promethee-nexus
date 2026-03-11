"""
core/semantic_pointer.py — Pointeurs sémantiques pour communication JIT inter-agents.

Au lieu de passer des blocs de texte complets entre agents (consomme des tokens),
on stocke le contenu dans un store transitoire et on passe juste l'ID (8 chars).
L'agent destinataire résout le pointeur au moment exact où il en a besoin.

Usage:
    from core.semantic_pointer import pointer_store

    # Agent A stocke du contenu
    ptr_id = pointer_store.store("code python...", source_agent="architect")

    # Passe l'ID dans le payload
    await orchestrator.dispatch_task("factory", {
        "mission": "Applique ce code.",
        "memory_refs": [ptr_id],
    })

    # Agent B résout les pointeurs
    content = pointer_store.resolve_many(["ptr_abc12345"])
"""

import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("SemanticPointer")

DEFAULT_TTL = 3600  # 1 heure


@dataclass
class SemanticPointer:
    """Un pointeur vers du contenu stocké temporairement."""
    id: str
    content: str
    source_agent: str
    created_at: float
    metadata: dict = field(default_factory=dict)
    ttl: float = DEFAULT_TTL


class PointerStore:
    """Stockage transitoire de pointeurs sémantiques — singleton."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._pointers = {}
        return cls._instance

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def store(self, content: str, source_agent: str = "",
              metadata: dict = None, ttl: float = DEFAULT_TTL) -> str:
        """Stocke du contenu et retourne un ID compact (ptr_XXXXXXXX)."""
        self._cleanup_expired()
        ptr_id = f"ptr_{uuid.uuid4().hex[:8]}"
        self._pointers[ptr_id] = SemanticPointer(
            id=ptr_id,
            content=content,
            source_agent=source_agent,
            created_at=time.time(),
            metadata=metadata or {},
            ttl=ttl,
        )
        logger.debug(f"[PTR] Stocké {ptr_id} ({len(content)} chars) par {source_agent}")
        return ptr_id

    def resolve(self, ptr_id: str) -> Optional[str]:
        """Résout un pointeur → contenu. None si expiré ou inexistant."""
        ptr = self._pointers.get(ptr_id)
        if ptr is None:
            return None
        if time.time() - ptr.created_at > ptr.ttl:
            del self._pointers[ptr_id]
            return None
        return ptr.content

    def resolve_many(self, ptr_ids: list) -> str:
        """Résout plusieurs pointeurs, concatène les contenus trouvés."""
        parts = []
        for pid in ptr_ids:
            content = self.resolve(pid)
            if content:
                parts.append(content)
        return "\n\n---\n\n".join(parts)

    def _cleanup_expired(self):
        """Purge les pointeurs expirés (appelé automatiquement)."""
        now = time.time()
        expired = [k for k, v in self._pointers.items()
                   if now - v.created_at > v.ttl]
        for k in expired:
            del self._pointers[k]

    @property
    def count(self):
        return len(self._pointers)


# Singleton global
pointer_store = PointerStore()
