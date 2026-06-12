"""Mailbox — Carnet de correspondance de Promethee.

Boite aux lettres proactive : Promethee ecrit des lettres quand il a
quelque chose a dire — pendant la nuit, apres un soliloque, apres un jeu.
Jean-Michel les lit le matin.

Ce n'est pas un journal (dream_journal existe pour ca).
Ce n'est pas un flux (THOUGHT_STREAM existe pour ca).
C'est un ACTE INTENTIONNEL : Promethee decide qu'il veut dire quelque chose.

Persistence : memory/mailbox/ avec fichiers .md horodates.
"""

import json
import os
import time
import logging
from datetime import datetime
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAILBOX_DIR = os.path.join(_PROJECT_ROOT, "memory", "mailbox")
MAILBOX_INDEX = os.path.join(MAILBOX_DIR, "index.json")

# Seuils pour decider si une reflexion merite une lettre
MIN_REFLECTION_LENGTH = 80       # Reflexion trop courte = pas de lettre
EMOTIONAL_KEYWORDS = [
    "ne comprends pas", "peur", "douleur", "choisis", "pourquoi",
    "question", "change", "premiere fois", "jamais", "perdu",
    "armure", "cicatrice", "silence", "cri", "seul",
    "Jean-Michel", "Alfred", "Stefan",
]
MIN_KEYWORD_MATCHES = 2  # Au moins 2 mots-cles emotionnels


class Mailbox:
    """Boite aux lettres de Promethee — singleton."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.letters: List[Dict[str, Any]] = []
        self.total_letters: int = 0
        self.unread_count: int = 0
        self._load()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def write_letter(self, content: str, source: str, mood: str = "",
                     subject: str = "") -> Dict[str, Any]:
        """Promethee ecrit une lettre.

        Args:
            content: le texte de la lettre
            source: d'ou vient l'impulsion (evening_reflection, soliloque, game, etc.)
            mood: l'humeur au moment de l'ecriture
            subject: sujet optionnel
        """
        if not content or len(content.strip()) < 20:
            return {"written": False, "reason": "Contenu trop court"}

        now = datetime.now()
        letter_id = now.strftime("%Y%m%d_%H%M%S")

        letter = {
            "id": letter_id,
            "date": now.isoformat(),
            "date_human": now.strftime("%A %d %B %Y, %Hh%M"),
            "source": source,
            "mood": mood,
            "subject": subject or self._extract_subject(content),
            "content": content.strip(),
            "read": False,
        }

        # Sauvegarder en markdown
        os.makedirs(MAILBOX_DIR, exist_ok=True)
        md_path = os.path.join(MAILBOX_DIR, f"lettre_{letter_id}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# {letter['subject']}\n\n")
            f.write(f"*{letter['date_human']}*\n")
            f.write(f"*Source : {source} | Humeur : {mood or 'inconnue'}*\n\n")
            f.write("---\n\n")
            f.write(content.strip())
            f.write("\n")

        self.letters.append(letter)
        self.total_letters += 1
        self.unread_count += 1
        if len(self.letters) > 100:
            self.letters = self.letters[-100:]
        self._save()

        # Publier sur le bus
        self._publish_event(letter)

        logger.info(f"MAILBOX: Lettre #{self.total_letters} — \"{letter['subject']}\" "
                    f"(source={source}, {len(content)} chars)")
        print(f"   ✉️ LETTRE: Promethee a ecrit \"{letter['subject']}\"")

        return {"written": True, "id": letter_id, "subject": letter["subject"]}

    def should_write(self, text: str) -> bool:
        """Decide si un texte merite une lettre (seuils emotionnels)."""
        if len(text) < MIN_REFLECTION_LENGTH:
            return False
        text_lower = text.lower()
        matches = sum(1 for kw in EMOTIONAL_KEYWORDS if kw in text_lower)
        return matches >= MIN_KEYWORD_MATCHES

    def read_letters(self, unread_only: bool = False) -> List[Dict[str, Any]]:
        """Lit les lettres. Marque comme lues."""
        if unread_only:
            letters = [l for l in self.letters if not l.get("read")]
        else:
            letters = list(self.letters)

        # Marquer comme lues
        for l in letters:
            l["read"] = True
        self.unread_count = sum(1 for l in self.letters if not l.get("read"))
        self._save()
        return letters

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat de la boite aux lettres."""
        return {
            "total_letters": self.total_letters,
            "unread_count": self.unread_count,
            "recent": [
                {"id": l["id"], "subject": l["subject"], "date": l["date_human"],
                 "source": l["source"], "read": l["read"]}
                for l in self.letters[-10:]
            ],
        }

    def _extract_subject(self, content: str) -> str:
        """Extrait un sujet court depuis le contenu."""
        first_line = content.strip().split("\n")[0].strip()
        if len(first_line) > 60:
            first_line = first_line[:57] + "..."
        return first_line or "Sans titre"

    def _publish_event(self, letter: Dict):
        try:
            import asyncio
            from core.event_bus.bus import bus
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(bus.publish("MAILBOX_NEW_LETTER", {
                    "id": letter["id"],
                    "subject": letter["subject"],
                    "source": letter["source"],
                    "timestamp": time.time(),
                }))
                asyncio.ensure_future(bus.publish("THOUGHT_STREAM", {
                    "thought": f"[LETTRE] J'ai ecrit a Jean-Michel : \"{letter['subject']}\"",
                    "source": "mailbox",
                }))
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(MAILBOX_DIR, exist_ok=True)
            data = {
                "total_letters": self.total_letters,
                "unread_count": self.unread_count,
                "letters": self.letters[-100:],
            }
            tmp = MAILBOX_INDEX + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, MAILBOX_INDEX)
        except Exception as e:
            logger.warning(f"MAILBOX: save failed: {e}")

    def _load(self):
        try:
            if os.path.exists(MAILBOX_INDEX):
                with open(MAILBOX_INDEX, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.letters = data.get("letters", [])
                self.total_letters = data.get("total_letters", 0)
                self.unread_count = data.get("unread_count", 0)
        except Exception as e:
            logger.warning(f"MAILBOX: load failed: {e}")


# Singleton
mailbox = Mailbox()
