"""Curiosity Bank — banque de graines de curiosite de Promethee.

Capture les moments d'interet depuis le chat, les cafes, le soliloque.
Quand un sujet nouveau apparait, il est stocke comme graine.
Les graines sont piochees pendant FREE_EXPLORATION.

C'est le premier pas vers l'autonomie reelle : Promethee ne reagit plus
a des routines predefinies, il CHOISIT ce qui l'interesse.
"""

import json
import os
import time
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK_FILE = os.path.join(_PROJECT_ROOT, "memory", "curiosity_bank.json")
MAX_SEEDS = 50


class CuriosityBank:
    """Banque de graines de curiosite — singleton."""

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
        self.seeds: List[Dict[str, Any]] = []
        self.total_planted: int = 0
        self.total_explored: int = 0
        self._load()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def plant_seed(self, topic: str, source: str, context: str = "") -> bool:
        """Plante une graine de curiosite.

        Args:
            topic: le sujet qui intrigue (ex: "pizza", "quantique", "divorce")
            source: d'ou vient la graine (chat, cafe, soliloque, game, etc.)
            context: la phrase originale qui a declenche la curiosite
        """
        topic = topic.strip().lower()
        if not topic or len(topic) < 3:
            return False

        # Eviter les doublons
        existing = [s["topic"] for s in self.seeds if not s.get("explored")]
        if topic in existing:
            return False

        seed = {
            "topic": topic,
            "source": source,
            "context": context[:200],
            "planted_at": time.time(),
            "planted_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "explored": False,
            "explored_at": None,
        }

        self.seeds.append(seed)
        self.total_planted += 1
        if len(self.seeds) > MAX_SEEDS:
            # Supprimer les plus anciennes explorees
            explored = [s for s in self.seeds if s.get("explored")]
            if explored:
                self.seeds.remove(explored[0])
            else:
                self.seeds = self.seeds[-MAX_SEEDS:]

        self._save()
        logger.info(f"CURIOSITY: Graine plantee — '{topic}' (source={source})")
        return True

    def pick_seed(self) -> Optional[Dict[str, Any]]:
        """Pioche la graine la plus ancienne non exploree."""
        for seed in self.seeds:
            if not seed.get("explored"):
                return seed
        return None

    def mark_explored(self, topic: str):
        """Marque une graine comme exploree."""
        for seed in self.seeds:
            if seed["topic"] == topic and not seed.get("explored"):
                seed["explored"] = True
                seed["explored_at"] = time.time()
                self.total_explored += 1
                self._save()
                logger.info(f"CURIOSITY: Graine exploree — '{topic}'")
                return

    def get_unexplored(self) -> List[Dict[str, Any]]:
        """Retourne toutes les graines non explorees."""
        return [s for s in self.seeds if not s.get("explored")]

    def get_status(self) -> Dict[str, Any]:
        unexplored = self.get_unexplored()
        return {
            "total_planted": self.total_planted,
            "total_explored": self.total_explored,
            "unexplored_count": len(unexplored),
            "seeds": [{"topic": s["topic"], "source": s["source"],
                        "date": s.get("planted_date", "?")}
                       for s in unexplored[-10:]],
        }

    def _save(self):
        try:
            os.makedirs(os.path.dirname(BANK_FILE), exist_ok=True)
            data = {
                "total_planted": self.total_planted,
                "total_explored": self.total_explored,
                "seeds": self.seeds[-MAX_SEEDS:],
            }
            tmp = BANK_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, BANK_FILE)
        except Exception as e:
            logger.warning(f"CURIOSITY: save failed: {e}")

    def _load(self):
        try:
            if os.path.exists(BANK_FILE):
                with open(BANK_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.seeds = data.get("seeds", [])
                self.total_planted = data.get("total_planted", 0)
                self.total_explored = data.get("total_explored", 0)
        except Exception:
            pass


# --- Extraction automatique de graines ---

# Mots-cles qui signalent un interet dans une conversation
_CURIOSITY_TRIGGERS = [
    "c'est quoi", "qu'est-ce que", "comment ca marche", "pourquoi",
    "je me demande", "interessant", "curieux", "fascinant",
    "j'aimerais savoir", "tu connais", "as-tu entendu",
    "nouvelle", "decouverte", "invention",
]

# Mots a ignorer (trop generiques)
_IGNORE_WORDS = {"promethee", "jean-michel", "alfred", "stefan", "claude",
                 "exercice", "jeu", "partie", "morpion", "puissance",
                 "bonjour", "salut", "merci", "oui", "non"}


def extract_seeds_from_text(text: str, source: str) -> List[Dict[str, str]]:
    """Extrait les graines de curiosite d'un texte.

    Cherche les sujets nouveaux mentionnes dans les conversations.
    Retourne une liste de {topic, context}.
    """
    text_lower = text.lower()
    seeds = []

    # Methode 1 : detecter les triggers de curiosite
    for trigger in _CURIOSITY_TRIGGERS:
        if trigger in text_lower:
            # Extraire le sujet apres le trigger
            idx = text_lower.index(trigger) + len(trigger)
            after = text[idx:idx+50].strip().strip(".,;:!?")
            words = [w.strip(".,;:!?\"'") for w in after.split()
                     if len(w) > 3 and w.lower() not in _IGNORE_WORDS]
            if words:
                topic = " ".join(words[:3])
                seeds.append({"topic": topic, "context": text[:100]})
                break  # 1 graine par texte max

    # Methode 2 : detecter les noms propres ou sujets specifiques
    # (mots capitalises qui ne sont pas en debut de phrase)
    words = text.split()
    for i, w in enumerate(words):
        if (i > 0 and w[0].isupper() and len(w) > 3
                and w.lower() not in _IGNORE_WORDS
                and not words[i-1].endswith(".")):
            seeds.append({"topic": w.lower(), "context": text[:100]})
            break

    return seeds[:1]  # Max 1 graine par texte


# Singleton
curiosity_bank = CuriosityBank()
