"""Gemini Helper — appels API cibles pour les taches qui demandent de la reflexion.

Usage cible (pas un remplacement general) :
  1. EVENING_REFLECTION — introspection profonde
  2. Stefan — questions tranchantes
  3. Chat complexe — questions philosophiques/profondes de Jean-Michel

Budget : compteur quotidien, max configurable.
Modele : gemini-2.5-flash (rapide, quasi-illimite dans le budget).
"""

import os
import time
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_BUDGET = 10  # Max 10 appels Gemini/jour (Free Tier = 20/jour)


class GeminiHelper:
    """Appels Gemini cibles — singleton."""

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
        self._api_key = ""
        self._calls_today = 0
        self._today = ""
        self._load_key()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def _load_key(self):
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
            self._api_key = os.getenv("GOOGLE_API_KEY", "")
            if self._api_key:
                logger.info("GEMINI_HELPER: Cle API Google chargee.")
            else:
                logger.warning("GEMINI_HELPER: Cle API Google absente.")
        except Exception:
            self._api_key = ""

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        self._check_daily_reset()
        return self._calls_today < DAILY_BUDGET

    def _check_daily_reset(self):
        today = date.today().isoformat()
        if today != self._today:
            self._calls_today = 0
            self._today = today

    async def generate(self, prompt: str, max_tokens: int = 1000,
                       temperature: float = 0.7) -> Optional[str]:
        """Appel Gemini Flash pour une tache qui demande de la reflexion."""
        if not self.is_available():
            return None

        try:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)

            model = genai.GenerativeModel("models/gemini-2.5-flash")
            response = await model.generate_content_async(
                prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )

            self._calls_today += 1
            text = response.text.strip() if response.text else ""

            logger.info(f"GEMINI_HELPER: Reponse {len(text)} chars "
                        f"(appel #{self._calls_today}/{DAILY_BUDGET})")
            return text

        except Exception as e:
            logger.warning(f"GEMINI_HELPER: Appel echoue: {e}")
            return None

    def get_status(self):
        self._check_daily_reset()
        return {
            "available": self.is_available(),
            "has_key": bool(self._api_key),
            "calls_today": self._calls_today,
            "budget": DAILY_BUDGET,
            "remaining": max(0, DAILY_BUDGET - self._calls_today),
        }


# Singleton
gemini = GeminiHelper()
