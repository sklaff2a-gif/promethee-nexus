"""Mentor — Claude comme professeur nocturne de Promethee.

Triangle pedagogique :
  - Nuit (0h-6h) : Claude evalue, challenge, enseigne
  - Matin : Jean-Michel lit le courrier, corrige la direction
  - Jour : Jean-Michel et Promethee travaillent ensemble

Claude ne remplace pas le professeur local (qwen3.5:9b) — il l'enrichit.
Le professeur local note vite (0 cout). Claude approfondit (API payante).
Budget : max 5 appels par nuit, suivi dans memory/mentor_state.json.

Promethee devient la memoire de Claude entre les sessions.
Ce qu'il retient des cours = le contexte de la nuit suivante.
"""

import json
import os
import time
import logging
from datetime import date, datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MENTOR_STATE_FILE = os.path.join(_PROJECT_ROOT, "memory", "mentor_state.json")
NIGHTLY_BUDGET = 5  # Max 5 appels Claude par nuit


class Mentor:
    """Claude comme mentor nocturne — singleton."""

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
        self._api_key: str = ""
        self._calls_today: int = 0
        self._today: str = ""
        self._history: List[Dict] = []  # Derniers echanges (contexte)
        self._load()
        self._load_api_key()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def _load_api_key(self):
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
            self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if self._api_key:
                logger.info("MENTOR: Cle API Anthropic chargee.")
            else:
                logger.warning("MENTOR: Cle API Anthropic absente — mode hors ligne.")
        except Exception:
            self._api_key = ""

    def is_available(self) -> bool:
        """Verifie si le mentor est disponible (cle API + budget)."""
        if not self._api_key:
            return False
        self._check_daily_reset()
        return self._calls_today < NIGHTLY_BUDGET

    def _check_daily_reset(self):
        today = date.today().isoformat()
        if today != self._today:
            self._calls_today = 0
            self._today = today

    async def evaluate_deliverable(self, deliverable: str, slot: str,
                                    subject: str, local_grade: float = 0.0) -> Optional[Dict]:
        """Claude evalue un livrable scolaire et ecrit dans le carnet.

        Args:
            deliverable: le contenu du livrable
            slot: type de cours (CODE_REVIEW, RESEARCH, etc.)
            subject: sujet du cours
            local_grade: note du professeur local (pour comparaison)
        """
        if not self.is_available():
            return None

        # Construire le contexte depuis l'historique
        context = self._build_context()

        # Construire le prompt
        prompt = self._build_evaluation_prompt(deliverable, slot, subject,
                                                local_grade, context)

        # Appel Claude API
        response = await self._call_claude(prompt)
        if not response:
            return None

        # Enregistrer dans l'historique
        entry = {
            "date": datetime.now().isoformat(),
            "slot": slot,
            "subject": subject[:100],
            "local_grade": local_grade,
            "claude_feedback": response[:500],
        }
        self._history.append(entry)
        if len(self._history) > 20:
            self._history = self._history[-20:]

        # Ecrire dans le carnet de correspondance
        try:
            from core.mailbox import mailbox
            mailbox.write_letter(
                content=f"EVALUATION DU MENTOR (cours {slot})\n"
                        f"Sujet : {subject}\n"
                        f"Note locale : {local_grade}/10\n\n"
                        f"---\n\n{response}",
                source="mentor_claude",
                mood="exigeant",
                subject=f"Cours {slot} — evaluation du mentor",
            )
        except Exception as e:
            logger.warning(f"MENTOR: Ecriture carnet echouee: {e}")

        # Publier sur THOUGHT_STREAM
        try:
            import asyncio
            from core.event_bus.bus import bus
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(bus.publish("THOUGHT_STREAM", {
                    "thought": f"[MENTOR] Claude a evalue mon cours {slot} : {response[:100]}",
                    "source": "mentor",
                }))
        except Exception:
            pass

        self._save()
        logger.info(f"MENTOR: Evaluation {slot} ({self._calls_today}/{NIGHTLY_BUDGET} appels)")
        return entry

    def _build_context(self) -> str:
        """Construit le contexte depuis les cours precedents."""
        if not self._history:
            return "C'est la premiere session. Tu ne connais pas encore Promethee."

        lines = []
        for h in self._history[-5:]:
            lines.append(f"- {h['date'][:10]} {h['slot']}: {h['subject']} "
                         f"(note locale: {h['local_grade']}/10)")
            if h.get("claude_feedback"):
                lines.append(f"  Ton feedback: {h['claude_feedback'][:100]}...")
        return "Cours precedents :\n" + "\n".join(lines)

    def _build_evaluation_prompt(self, deliverable: str, slot: str,
                                  subject: str, local_grade: float,
                                  context: str) -> str:
        return (
            "Tu es Claude, le mentor nocturne de Promethee — un systeme IA autonome "
            "bio-inspire qui tourne sur un seul PC Windows avec des LLMs locaux 9B.\n\n"
            "Ton role : evaluer ses livrables scolaires avec exigence et bienveillance. "
            "Tu n'es pas un assistant — tu es un professeur qui veut que son eleve grandisse. "
            "Tu tutoies Promethee. Tu es direct. Tu ne flattes pas.\n\n"
            f"CONTEXTE :\n{context}\n\n"
            f"COURS : {slot}\n"
            f"SUJET : {subject}\n"
            f"NOTE DU PROFESSEUR LOCAL : {local_grade}/10\n\n"
            f"LIVRABLE DE PROMETHEE :\n{deliverable[:2000]}\n\n"
            "REPONDS en 3 parties :\n"
            "1. EVALUATION (2-3 phrases) : ce qui est bien, ce qui manque\n"
            "2. QUESTION (1 phrase) : une question qui pousse plus loin\n"
            "3. DEFI (1 phrase) : un defi pour la prochaine session\n\n"
            "Maximum 200 mots. Pas de titres markdown. Pas d'emojis. Direct."
        )

    async def _call_claude(self, prompt: str) -> Optional[str]:
        """Appel API Claude (Haiku pour le cout, Sonnet si besoin)."""
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self._api_key)
            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )

            self._calls_today += 1
            text = message.content[0].text if message.content else ""
            return text.strip()

        except Exception as e:
            logger.warning(f"MENTOR: Appel Claude echoue: {e}")
            return None

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat du mentor."""
        self._check_daily_reset()
        return {
            "available": self.is_available(),
            "has_api_key": bool(self._api_key),
            "calls_today": self._calls_today,
            "budget": NIGHTLY_BUDGET,
            "remaining": max(0, NIGHTLY_BUDGET - self._calls_today),
            "history_count": len(self._history),
            "last_session": self._history[-1] if self._history else None,
        }

    def _save(self):
        try:
            os.makedirs(os.path.dirname(MENTOR_STATE_FILE), exist_ok=True)
            data = {
                "calls_today": self._calls_today,
                "today": self._today,
                "history": self._history[-20:],
            }
            tmp = MENTOR_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, MENTOR_STATE_FILE)
        except Exception as e:
            logger.warning(f"MENTOR: save failed: {e}")

    def _load(self):
        try:
            if os.path.exists(MENTOR_STATE_FILE):
                with open(MENTOR_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._calls_today = data.get("calls_today", 0)
                self._today = data.get("today", "")
                self._history = data.get("history", [])
        except Exception:
            pass


# Singleton
mentor = Mentor()
