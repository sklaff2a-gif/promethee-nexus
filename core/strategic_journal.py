# core/strategic_journal.py — Journal Stratégique de Prométhée
# Capture les conclusions des débats Council et les découvertes de recherche.
# Réinjecte le contexte récent dans les débats suivants.

import os
import logging
from datetime import datetime
from typing import Optional, List

logger = logging.getLogger("StrategicJournal")

JOURNAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "journal_strategique.md"
)

HEADER = "# Journal Stratégique de Prométhée\n"
SEPARATOR = "\n---\n"
MAX_ENTRIES = 200


class StrategicJournal:
    """Singleton — journal markdown persistant des débats et découvertes."""

    _instance: Optional["StrategicJournal"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._entries: List[str] = []
        self._load()

    # --- Init & Reset ---

    def _load(self):
        """Charge les entrées depuis le fichier markdown."""
        if not os.path.exists(JOURNAL_FILE):
            self._entries = []
            return
        try:
            with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            # Retirer le header
            if content.startswith(HEADER):
                content = content[len(HEADER):]
            # Parser les entrées séparées par ---
            raw_entries = content.split("\n---\n")
            self._entries = [e.strip() for e in raw_entries if e.strip()]
        except Exception as e:
            logger.warning(f"[JOURNAL] Erreur chargement: {e}")
            self._entries = []

    def _save(self):
        """Persiste les entrées dans le fichier markdown."""
        os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
        content = HEADER + SEPARATOR.join([""] + self._entries) if self._entries else HEADER
        try:
            with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.warning(f"[JOURNAL] Erreur sauvegarde: {e}")

    def _trim(self):
        """Supprime les entrées les plus anciennes si > MAX_ENTRIES."""
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]

    @classmethod
    def reset_singleton(cls):
        """Reset le singleton (utilisé par les tests)."""
        cls._instance = None

    # --- Ajout d'entrées ---

    def append_council_entry(self, participants: list, subject: str,
                             status: str, conclusion: str,
                             research_context: str = ""):
        """Ajoute une entrée de débat Council."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        parts = [
            f"### {now} — Council",
            f"**Participants**: {', '.join(participants)}",
            f"**Sujet**: {subject}",
        ]
        if research_context:
            # Tronquer le contexte recherche pour garder le journal lisible
            short = research_context[:500].rstrip()
            parts.append(f"**Recherche**: {short}")
        parts.append(f"**Verdict**: {status or 'inconnu'}")
        if conclusion:
            short_conclusion = conclusion[:1000].rstrip()
            parts.append(f"**Conclusion**: {short_conclusion}")

        entry = "\n".join(parts)
        self._entries.append(entry)
        self._trim()
        self._save()
        logger.info(f"[JOURNAL] +Council: {subject[:60]}")

    def append_partial_insight(self, mission: str, rounds_used: int,
                                partial: dict, participants: list):
        """V6.0 Reforme 3 (2026-04-20) : archive la valeur extraite d'un
        debat max_rounds (sans consensus). Les convergences partielles et
        les divergences structurelles sont conservees pour alimenter les
        debats futurs (via get_recent_context).
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        parts = [
            f"### {now} — Partial Insight (max_rounds)",
            f"**Participants**: {', '.join(participants)}",
            f"**Sujet**: {mission[:200]}",
            f"**Tours utilises**: {rounds_used}",
        ]
        convergence = partial.get("convergence_keywords") or []
        if convergence:
            parts.append(f"**Convergences**: {', '.join(convergence)}")
        divergence = partial.get("divergence_by_agent") or {}
        if divergence:
            div_lines = [f"  - {agent}: {', '.join(kws)}"
                         for agent, kws in divergence.items()]
            parts.append("**Divergences par agent**:\n" + "\n".join(div_lines))
        best = partial.get("best_argument")
        if best:
            parts.append(
                f"**Meilleur argument** ({best['agent']}, score={best['score']}):\n"
                f"{best['excerpt']}"
            )
        entry = "\n".join(parts)
        self._entries.append(entry)
        self._trim()
        self._save()
        logger.info(f"[JOURNAL] +Partial Insight: {mission[:60]}")

    def append_objectives_report(self, report_text: str):
        """Ajoute un bilan d'objectifs au journal."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"### {now} — Bilan Objectifs\n{report_text}"
        self._entries.append(entry)
        self._trim()
        self._save()
        logger.info(f"[JOURNAL] +Bilan objectifs")

    def append_evolution_report(self, report_text: str):
        """Ajoute un rapport de feedback Evolution au journal."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"### {now} — Feedback Evolution\n{report_text}"
        self._entries.append(entry)
        self._trim()
        self._save()
        logger.info("[JOURNAL] +Feedback Evolution")

    def append_research_entry(self, topic: str, findings: str,
                              source: str = "web"):
        """Ajoute une entrée de veille/recherche."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        short_findings = findings[:1000].rstrip() if findings else "(aucune)"
        entry = "\n".join([
            f"### {now} — Veille {source}",
            f"**Sujet**: {topic}",
            f"**Découvertes**: {short_findings}",
        ])
        self._entries.append(entry)
        self._trim()
        self._save()
        logger.info(f"[JOURNAL] +Veille ({source}): {topic[:60]}")

    # --- Lecture ---

    def get_recent_context(self, n_entries: int = 3,
                           max_chars: int = 1500) -> str:
        """Retourne les N dernières entrées, tronquées à max_chars."""
        if not self._entries:
            return ""
        recent = self._entries[-n_entries:]
        text = SEPARATOR.join(recent)
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n[...tronqué]"
        return text

    def get_by_subject(self, subject_key: str) -> Optional[dict]:
        """Cherche la dernière entrée Council dont le sujet contient subject_key.
        Retourne {"summary": ..., "status": ...} ou None."""
        if not subject_key or not self._entries:
            return None
        key_lower = subject_key.lower()
        # Parcourir en ordre inverse (plus récent d'abord)
        for entry in reversed(self._entries):
            if "Council" not in entry:
                continue
            entry_lower = entry.lower()
            if key_lower not in entry_lower:
                continue
            # Extraire le verdict et la conclusion
            status = ""
            conclusion = ""
            for line in entry.split("\n"):
                if line.startswith("**Verdict**:"):
                    status = line.split(":", 1)[1].strip()
                elif line.startswith("**Conclusion**:"):
                    conclusion = line.split(":", 1)[1].strip()
            return {"summary": conclusion, "status": status}
        return None

    def get_full_journal(self) -> str:
        """Retourne le journal complet en markdown."""
        if not self._entries:
            return HEADER + "\n(Aucune entrée)\n"
        return HEADER + SEPARATOR.join([""] + self._entries)

    def entry_count(self) -> int:
        """Nombre d'entrées actuelles."""
        return len(self._entries)


# Singleton global
journal = StrategicJournal()
