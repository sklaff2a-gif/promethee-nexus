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
        """Appel Gemini Flash pour une tache qui demande de la reflexion.

        SAFETY_SETTINGS = BLOCK_NONE sur les 4 categories : usage interne en
        boucle fermee avec operateur adulte identifie (JM). Sans ce relachement,
        les filtres safety par defaut coupaient nos prompts philosophiques denses
        (debats sur l'illusion, la mort des routines, la peur de la suppression)
        avec finish_reason=SAFETY -> reponses tronquees a 110-145 chars.
        Diagnostic 26/05 : 2 troncatures observees pendant la session 4 debats
        du 25/05 (D1 E11, D4 E4). Cf. fix dans chat_engine + logs explicites.
        """
        if not self.is_available():
            return None

        try:
            import google.generativeai as genai

            genai.configure(api_key=self._api_key)

            # SAFETY_SETTINGS relaxes (usage interne JM, pas de redistribution)
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            model = genai.GenerativeModel("models/gemini-2.5-flash")
            response = await model.generate_content_async(
                prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
                safety_settings=safety_settings,
            )

            self._calls_today += 1

            # --- Telemetrie finish_reason + safety_ratings ---
            # Boite noire avant ce patch ; visibilite complete maintenant.
            finish_reason = "UNKNOWN"
            safety_ratings_summary = ""
            text = ""
            try:
                if response.candidates:
                    cand = response.candidates[0]
                    fr = getattr(cand, "finish_reason", None)
                    if fr is not None:
                        finish_reason = getattr(fr, "name", str(fr))
                    # safety ratings : list de blocs categorie+probabilite
                    sr = getattr(cand, "safety_ratings", None)
                    if sr:
                        # Garder seulement les non-NEGLIGIBLE pour le log
                        notable = [
                            f"{getattr(r.category, 'name', r.category)}={getattr(r.probability, 'name', r.probability)}"
                            for r in sr
                            if getattr(getattr(r, "probability", None), "name", "") not in ("NEGLIGIBLE", "")
                        ]
                        safety_ratings_summary = ",".join(notable) if notable else "all_negligible"
            except Exception as e:
                logger.debug(f"GEMINI_HELPER: parse candidates failed: {e}")

            # response.text peut lever une exception si le contenu a ete bloque
            try:
                text = response.text.strip() if response.text else ""
            except Exception as e:
                logger.warning(
                    f"GEMINI_HELPER: response.text inaccessible "
                    f"(finish_reason={finish_reason}, safety={safety_ratings_summary}): {e}"
                )
                text = ""

            # Log structure : longueur + finish_reason toujours visible
            log_level = logger.info
            if finish_reason in ("SAFETY", "RECITATION", "OTHER") or len(text) < 100:
                log_level = logger.warning  # anomalie : log en WARNING
            log_level(
                f"GEMINI_HELPER: Reponse {len(text)} chars "
                f"(appel #{self._calls_today}/{DAILY_BUDGET}) "
                f"finish={finish_reason} safety=[{safety_ratings_summary}]"
            )
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
