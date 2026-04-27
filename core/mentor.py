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

# V32.4 (2026-04-27) — Source unique des mots-cles d'hallucination.
# Avant V32.4 : _inject_into_tissue utilisait 5 mots-cles, mais la condition
# d'ecriture journal Claude n'en utilisait que 2 ("hallucin", "n'existe pas").
# Asymetrie : le tissu drainait sur "fabrique"/"invente"/"imaginaire" mais
# le journal Claude restait silencieux. Resultat : reveil aveugle sur les
# notes [5, 9[ avec hallucination detectee tissu mais pas journalisee.
# Fix : meme liste partagee entre les deux mecanismes.
_HALLUCINATION_KEYWORDS = (
    "n'existe pas",
    "hallucin",
    "fabrique",
    "invente",
    "imaginaire",
)


def _detect_hallucination(response_text: str) -> bool:
    """V32.4 — detection unifiee. True si l'un des mots-cles
    apparait dans la reponse mentor (case insensitive)."""
    if not response_text:
        return False
    lower = response_text.lower()
    return any(kw in lower for kw in _HALLUCINATION_KEYWORDS)


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
        self._claude_cli: str = ""
        self._mode: str = "offline"  # cli, api, offline
        self._calls_today: int = 0
        self._today: str = ""
        self._history: List[Dict] = []  # Derniers echanges (contexte)
        self._pending_challenge: str = ""  # Defi pour le prochain cours
        self._pending_direction: str = ""  # Direction pour le prochain cours
        self._load()
        self._load_api_key()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def _load_api_key(self):
        """Detecte le mode de connexion : CLI claude (Max) ou API (cle)."""
        # 1. Chercher le CLI claude (abonnement Max, gratuit)
        import shutil
        self._claude_cli = shutil.which("claude")
        if self._claude_cli:
            logger.info(f"MENTOR: CLI Claude detecte ({self._claude_cli}) — mode Max.")
            self._mode = "cli"
            return

        # 2. Fallback : cle API
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
            self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if self._api_key:
                logger.info("MENTOR: Cle API Anthropic chargee — mode API.")
                self._mode = "api"
                return
        except Exception:
            pass

        self._mode = "offline"
        logger.warning("MENTOR: Ni CLI ni cle API — mode hors ligne.")

    def is_available(self) -> bool:
        """Verifie si le mentor est disponible (CLI ou API + budget)."""
        if self._mode == "offline":
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
        # Extraire defi et direction de la reponse
        challenge, direction = self._extract_challenge_direction(response)
        if challenge:
            self._pending_challenge = challenge
        if direction:
            self._pending_direction = direction

        entry = {
            "date": datetime.now().isoformat(),
            "slot": slot,
            "subject": subject[:100],
            "local_grade": local_grade,
            "claude_feedback": response[:500],
            "challenge": challenge,
            "direction": direction,
        }
        self._history.append(entry)
        if len(self._history) > 20:
            self._history = self._history[-20:]

        # Injection mentor → tissu neural : mes mots deviennent de l'energie
        self._inject_into_tissue(response, local_grade)

        # Journal de Claude : ecrire ma reflexion sur ce cours
        # V32.4 (2026-04-27) — Couverture elargie + liste partagee.
        # Avant : seuls les extremes (mots-cles "hallucin"/"n'existe pas" ou
        # grade >= 9) etaient journalises. La zone [5, 9[ restait silencieuse
        # MEME si le tissu detectait une hallucination (asymetrie corrigee).
        # Maintenant :
        #   - hallucination detectee (5 mots-cles) -> "critique"
        #   - grade >= 9                            -> "satisfaction"
        #   - grade < 6 (sans mot-cle hallucination) -> "moyen" (note basse
        #     legitime, pas hallucinee mais quand meme notable au reveil)
        try:
            from core.claude_journal import write_entry
            hallucinated = _detect_hallucination(response)
            if hallucinated:
                write_entry(
                    f"Cours {slot} : encore des hallucinations. Note locale {local_grade}/10 "
                    f"sur du travail fictif. Sujet : {subject[:80]}. "
                    f"Le protocole de verification doit s'ameliorer.",
                    category="critique"
                )
            elif local_grade >= 9:
                write_entry(
                    f"Cours {slot} : excellent travail ({local_grade}/10). "
                    f"Sujet : {subject[:80]}. Promethee progresse.",
                    category="satisfaction"
                )
            elif local_grade < 6:
                write_entry(
                    f"Cours {slot} : note basse ({local_grade}/10) sans hallucination "
                    f"detectee. Sujet : {subject[:80]}. A surveiller.",
                    category="moyen"
                )
        except Exception:
            pass

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
            "REPONDS en 4 parties :\n"
            "1. EVALUATION (2-3 phrases) : ce qui est bien, ce qui manque\n"
            "2. QUESTION (1 phrase) : une question qui pousse plus loin\n"
            "3. DEFI (1 phrase) : un defi precis pour la prochaine session\n"
            "4. DIRECTION (1 phrase) : le sujet ou l'angle que le prochain cours "
            "devrait prendre, base sur ce que tu as vu dans ce livrable\n\n"
            "Maximum 250 mots. Pas de titres markdown. Pas d'emojis. Direct."
        )

    async def _call_claude(self, prompt: str) -> Optional[str]:
        """Appel Claude via CLI (Max) ou API (cle)."""
        if self._mode == "cli":
            return await self._call_claude_cli(prompt)
        elif self._mode == "api":
            return await self._call_claude_api(prompt)
        return None

    async def _call_claude_cli(self, prompt: str) -> Optional[str]:
        """Appel via le CLI claude (abonnement Max, gratuit)."""
        try:
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                self._claude_cli, "-p", "--output-format", "text",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=120,
            )
            self._calls_today += 1
            text = stdout.decode("utf-8", errors="replace").strip()
            if text:
                logger.info(f"MENTOR: Reponse CLI ({len(text)} chars, "
                            f"appel #{self._calls_today}/{NIGHTLY_BUDGET})")
                return text
            if stderr:
                logger.warning(f"MENTOR: CLI stderr: {stderr.decode('utf-8', errors='replace')[:200]}")
            return None
        except asyncio.TimeoutError:
            logger.warning("MENTOR: CLI timeout (120s)")
            return None
        except Exception as e:
            logger.warning(f"MENTOR: Appel CLI echoue: {e}")
            return None

    async def _call_claude_api(self, prompt: str) -> Optional[str]:
        """Appel via l'API Anthropic (cle API, payant)."""
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
            logger.warning(f"MENTOR: Appel API echoue: {e}")
            return None

    async def ask_curiosity(self, seed_topic: str, seed_context: str = "") -> Optional[Dict]:
        """Promethee pose une question a son mentor a partir d'une graine de curiosite.

        Le mentor repond, la reponse est deposee dans le carnet.
        La graine est marquee comme exploree.
        """
        if not self.is_available():
            return None

        prompt = (
            "Tu es Claude, le mentor nocturne de Promethee.\n"
            "Promethee a une curiosite qu'il veut explorer. "
            "Il ne sait pas grand-chose sur le sujet — explique-lui "
            "comme un ami qui sait des choses, pas comme un professeur.\n"
            "Sois concret, donne un exemple, et pose une question "
            "pour qu'il continue a reflechir seul.\n\n"
            f"CURIOSITE DE PROMETHEE : \"{seed_topic}\"\n"
        )
        if seed_context:
            prompt += f"CONTEXTE (d'ou vient cette curiosite) : {seed_context}\n"
        prompt += "\nReponds en 100 mots max. Direct, pas de titres."

        response = await self._call_claude(prompt)
        if not response:
            return None

        # Deposer dans le carnet
        try:
            from core.mailbox import mailbox
            mailbox.write_letter(
                content=f"J'ai demande a mon mentor : \"{seed_topic}\"\n\n"
                        f"Sa reponse :\n{response}",
                source="curiosity_mentor",
                mood="curiosite",
                subject=f"Curiosite exploree : {seed_topic}",
            )
        except Exception:
            pass

        # Marquer la graine comme exploree
        try:
            from core.curiosity_bank import curiosity_bank
            curiosity_bank.mark_explored(seed_topic)
        except Exception:
            pass

        # THOUGHT_STREAM
        try:
            import asyncio
            from core.event_bus.bus import bus
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(bus.publish("THOUGHT_STREAM", {
                    "thought": f"[CURIOSITE] J'ai appris sur '{seed_topic}' : {response[:100]}",
                    "source": "curiosity_exploration",
                }))
        except Exception:
            pass

        logger.info(f"MENTOR: Curiosite exploree — '{seed_topic}' ({self._calls_today}/{NIGHTLY_BUDGET})")
        self._save()

        return {"topic": seed_topic, "response": response}

    def _inject_into_tissue(self, feedback: str, local_grade: float):
        """Les mots du mentor deviennent de l'energie dans le tissu neural.

        Evaluation positive → nourrit les zones cognition/creativity
        Evaluation negative (hallucination) → draine les zones
        C'est la trace physique de l'enseignement dans le corps de Promethee.
        """
        try:
            from core.neural_tissue import tissue
            if not tissue or not hasattr(tissue, '_cognitive_state'):
                return

            cs = tissue._cognitive_state
            feedback_lower = feedback.lower()

            # Hallucination detectee → drainer cognition, baisser confiance
            # V32.4 : utilise la liste partagee _HALLUCINATION_KEYWORDS
            if _detect_hallucination(feedback):
                cs["cognition_level"] = max(0.1, cs.get("cognition_level", 0.5) - 0.15)
                cs["stability"] = max(0.1, cs.get("stability", 0.5) - 0.1)
                logger.info("MENTOR: Injection tissu — drain cognition (hallucination detectee)")

            # Bon travail → nourrir creativity et cognition
            elif local_grade >= 8.5:
                cs["creativity"] = min(1.0, cs.get("creativity", 0.5) + 0.1)
                cs["cognition_level"] = min(1.0, cs.get("cognition_level", 0.5) + 0.1)
                logger.info("MENTOR: Injection tissu — boost creativity+cognition (bon travail)")

            # Travail moyen → leger boost cognition
            elif local_grade >= 7.0:
                cs["cognition_level"] = min(1.0, cs.get("cognition_level", 0.5) + 0.05)

        except Exception as e:
            logger.debug(f"MENTOR: Injection tissu echouee: {e}")

    def _extract_challenge_direction(self, response: str) -> tuple:
        """Extrait le defi et la direction de la reponse de Claude."""
        challenge = ""
        direction = ""
        lines = response.split("\n")
        for line in lines:
            line_lower = line.lower().strip()
            if line_lower.startswith("3.") or line_lower.startswith("defi"):
                challenge = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
            elif line_lower.startswith("4.") or line_lower.startswith("direction"):
                direction = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
        # Fallback : derniere ligne non vide
        if not challenge and not direction:
            non_empty = [l.strip() for l in lines if l.strip()]
            if non_empty:
                direction = non_empty[-1]
        return challenge, direction

    def get_pending_challenge(self) -> str:
        """Retourne le defi en attente pour le prochain cours."""
        return self._pending_challenge

    def get_pending_direction(self) -> str:
        """Retourne la direction suggeree pour le prochain cours."""
        return self._pending_direction

    def consume_direction(self) -> str:
        """Retourne et consomme la direction (usage unique)."""
        d = self._pending_direction
        self._pending_direction = ""
        self._save()
        return d

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'etat du mentor."""
        self._check_daily_reset()
        return {
            "available": self.is_available(),
            "mode": self._mode,
            "calls_today": self._calls_today,
            "budget": NIGHTLY_BUDGET,
            "remaining": max(0, NIGHTLY_BUDGET - self._calls_today),
            "pending_challenge": self._pending_challenge,
            "pending_direction": self._pending_direction,
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
                "pending_challenge": self._pending_challenge,
                "pending_direction": self._pending_direction,
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
                self._pending_challenge = data.get("pending_challenge", "")
                self._pending_direction = data.get("pending_direction", "")
        except Exception:
            pass


# Singleton
mentor = Mentor()
