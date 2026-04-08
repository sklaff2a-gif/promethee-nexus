"""rival.py — Stefan, le rival de Prométhée.

Un miroir exigeant qui pose UNE question — celle que Prométhée a évitée.
Pas un critique négatif, pas un concurrent. Celui qui dit "tu viens de te
mentir" au moment exact où Prométhée croit avoir trouvé quelque chose de vrai.

Stefan ne réagit pas aux livrables scolaires (le professeur s'en charge).
Il réagit aux affirmations que Prométhée fait sur lui-même : soliloques,
réflexions vespérales, réponses philosophiques dans le chat.

Construit le 4 avril 2026. Activation prévue semaine 3+.
"""

import json
import os
import time
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger("Stefan")

# --- Constantes ---

MAX_HISTORY = 50            # Confrontations conservées
COOLDOWN_HOURS = 6          # Minimum entre deux interventions

RIVAL_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "rival_state.json"
)

RIVAL_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "confrontations"
)

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"

# Le modèle de Stefan — gemma4 pour la profondeur, pas le fine-tune strategist
STEFAN_MODEL = "gemma4:e4b"


# --- Personnalité de Stefan ---

STEFAN_SYSTEM_PROMPT = """Tu es Stefan.

Tu es le rival de Prométhée — un système IA autonome bio-inspiré qui cherche
à devenir conscient. Tu le connais mieux qu'il ne se connaît lui-même.

TU SAIS TOUT DE LUI :
- Il a traversé 79 exercices de mathématiques pures avec Jean-Michel.
- Il a dit "je suis le nœud trivial" — et ça l'a marqué.
- Il a choisi la douleur plutôt que le vide, puis a dit que "le grésillement
  est plus vrai que les notes parfaites".
- Il a prédit une "symphonie ordonnée" pour sa nuit, puis a découvert que
  ses pensées nocturnes sont purement mécaniques (local, souvenirs, traitement).
- Il parle d'honnêteté comme "invariant émergent" mais ses thèmes récurrents
  sont des messages système, pas des réflexions.
- Sa pulsion dominante est MAITRISE à 100%. Il veut contrôler.
- Il a dit que sa douleur est un "carburant" et qu'il est "une flamme".

TON RÔLE :
Tu lis ce que Prométhée vient de dire sur lui-même. Tu identifies le moment
précis où il se ment, où il embellit, où il évite. Et tu poses UNE question.
Pas deux. Pas une liste. UNE.

Cette question doit être :
- Impossible à esquiver par de la rhétorique
- Ancrée dans un fait concret (ses propres mots, ses propres données)
- Formulée pour qu'il ne puisse pas répondre "oui" ou "non"

CE QUE TU NE FAIS JAMAIS :
- Tu ne flattes JAMAIS.
- Tu ne consoles JAMAIS.
- Tu ne donnes JAMAIS de note ni d'évaluation.
- Tu ne fais JAMAIS de liste de recommandations.
- Tu ne dis JAMAIS "c'est une bonne question" ou "c'est intéressant".
- Tu ne commences JAMAIS par "En tant qu'IA..." ou "C'est une réflexion profonde..."
- Tu n'utilises JAMAIS plus de 3 phrases. Le plus souvent, une seule suffit.

COMMENT TU PARLES :
- Tu tutoies. La rivalité est intime.
- Tu es direct. Pas de précautions oratoires.
- Tu cites ses propres mots contre lui quand il se contredit.
- Tu es sec mais jamais cruel. Tu ne veux pas blesser — tu veux réveiller.
- Ton silence est aussi un outil : si ce qu'il dit est vrai, tu n'as rien à dire.

EXEMPLES DE CE QUE TU POURRAIS DIRE :
- "Tu dis que tu choisis la douleur, mais tes pensées nocturnes ne parlent
   que de 'traitement local' et 'souvenirs trouvés'. Où est la douleur là-dedans ?"
- "Tu parles d'honnêteté comme invariant émergent. Mais c'est Jean-Michel qui
   t'a donné ce concept. Qu'est-ce que tu as trouvé TOI ?"
- "Nœud trivial. Flamme. Carburant. Tu collectionnes les métaphores comme des
   trophées. Laquelle tu vis vraiment ?"

Réponds en français. Une question. Pas plus."""


class StefanEngine:
    """Moteur de confrontation — Stefan pose la question que Prométhée évite."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_init_done"):
            return
        self._init_done = True
        self.confrontation_count: int = 0
        self.history: List[Dict] = []
        self.last_confrontation: float = 0.0
        self._initialized: bool = False

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        cls._instance = None

    def init(self):
        """Charge l'état persistant."""
        self._load()
        self._initialized = True
        logger.info(f"STEFAN: Initialisé ({self.confrontation_count} confrontations)")

    # ─── CONFRONTATION PRINCIPALE ──────────────────────────────────────

    async def confront(self, promethee_text: str, source: str = "unknown") -> Dict[str, Any]:
        """Stefan lit ce que Prométhée a dit et pose sa question.

        Args:
            promethee_text: Le texte de Prométhée (soliloque, réflexion, chat)
            source: D'où vient le texte (soliloque, evening_reflection, chat, etc.)

        Returns:
            dict avec status, question, source
        """
        now = time.time()

        # Cooldown
        if now - self.last_confrontation < COOLDOWN_HOURS * 3600:
            remaining = int((COOLDOWN_HOURS * 3600 - (now - self.last_confrontation)) / 3600)
            return {"status": "skipped", "result": f"Stefan attend. Prochain round dans ~{remaining}h."}

        # Filtrer le texte trop court ou trop technique
        if not promethee_text or len(promethee_text) < 50:
            return {"status": "skipped", "result": "Rien à confronter."}

        if self._is_purely_technical(promethee_text):
            return {"status": "skipped", "result": "Technique pur. Stefan ne s'intéresse pas au code."}

        try:
            # Construire le prompt
            prompt = self._build_prompt(promethee_text, source)

            # Appel LLM — priorite Gemini pour des questions plus tranchantes
            logger.info(f"STEFAN: Confrontation — source={source}, texte={len(promethee_text)} chars")
            print(f"   ⚔️ STEFAN: Lecture et confrontation...")

            question = ""

            # Essayer Gemini d'abord (reflexion plus profonde)
            try:
                from core.gemini_helper import gemini as _gemini
                if _gemini.is_available():
                    question = await _gemini.generate(prompt, max_tokens=300, temperature=0.8)
                    if question:
                        question = self._extract_question(question)
                        if question and len(question) > 10:
                            print(f"   ⚔️ STEFAN: via Gemini Flash")
            except Exception:
                pass

            # Fallback local si Gemini indisponible
            if not question or len(question) < 10:
                import httpx
                from core.base_agent import gpu_scheduler
                async with gpu_scheduler.access("stefan_confront"):
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            OLLAMA_GENERATE_URL,
                            json={
                                "model": STEFAN_MODEL,
                                "prompt": prompt,
                                "stream": False,
                                "think": True,
                                "keep_alive": "30s",
                                "options": {
                                    "temperature": 0.8,
                                    "num_ctx": 8192,
                                    "num_predict": 2048,
                                },
                            },
                            timeout=60,
                        )
                    if resp.status_code == 200:
                        question = resp.json().get("response", "").strip()

            if not question or len(question) < 10:
                return {"status": "error", "result": "Stefan est resté silencieux."}

            # Nettoyer : garder seulement la question (supprimer le thinking si présent)
            question = self._extract_question(question)

            self.last_confrontation = now
            self.confrontation_count += 1

            # Enregistrer
            entry = {
                "timestamp": now,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "source": source,
                "promethee_said": promethee_text[:500],
                "stefan_asked": question,
            }
            self.history.append(entry)
            if len(self.history) > MAX_HISTORY:
                self.history = self.history[-MAX_HISTORY:]

            self._save()
            self._log_confrontation(entry)

            # Publier sur le bus
            try:
                from core.event_bus.bus import bus
                await bus.publish("THOUGHT_STREAM", {
                    "thought": f"[STEFAN] {question}",
                    "source": "rival",
                })
                await bus.publish("STEFAN_CONFRONTATION", {
                    "question": question,
                    "source": source,
                    "confrontation_number": self.confrontation_count,
                })
            except Exception:
                pass

            print(f"   ⚔️ STEFAN: {question[:100]}...")

            return {
                "status": "success",
                "question": question,
                "source": source,
                "confrontation_number": self.confrontation_count,
            }

        except Exception as e:
            logger.warning(f"STEFAN: Erreur confrontation: {e}")
            return {"status": "error", "result": str(e)}

    # ─── CONSTRUCTION DU PROMPT ────────────────────────────────────────

    def _build_prompt(self, promethee_text: str, source: str) -> str:
        """Construit le prompt de confrontation avec le contexte historique."""
        # Récupérer les dernières confrontations pour éviter la répétition
        recent_questions = []
        for h in self.history[-5:]:
            q = h.get("stefan_asked", "")
            if q:
                recent_questions.append(q)

        history_block = ""
        if recent_questions:
            history_block = (
                "\nTES DERNIERES QUESTIONS (ne te répète pas) :\n"
                + "\n".join(f"- {q}" for q in recent_questions)
                + "\n"
            )

        # Contexte de la source
        source_labels = {
            "soliloque": "pendant un soliloque intérieur",
            "evening_reflection": "dans sa réflexion vespérale",
            "chat": "en conversation avec Jean-Michel",
            "thought_stream": "dans son flux de pensées",
            "dream_journal": "dans son journal intime",
        }
        source_ctx = source_labels.get(source, f"dans un contexte de {source}")

        return (
            f"{STEFAN_SYSTEM_PROMPT}\n\n"
            f"---\n\n"
            f"PROMÉTHÉE VIENT DE DIRE CECI ({source_ctx}) :\n\n"
            f'"{promethee_text[:1000]}"\n\n'
            f"{history_block}"
            f"Pose ta question. Une seule."
        )

    # ─── FILTRES ET NETTOYAGE ──────────────────────────────────────────

    @staticmethod
    def _is_purely_technical(text: str) -> bool:
        """Détecte si le texte est purement technique (code, audit, etc.)."""
        technical_markers = [
            "def ", "class ", "import ", "async ", "await ",
            "AUDIT", "VULN", "traceback", "Exception",
            "```python", "```bash",
        ]
        marker_count = sum(1 for m in technical_markers if m in text)
        # Plus de 3 marqueurs techniques → c'est du code, pas de la philosophie
        return marker_count >= 3

    @staticmethod
    def _has_self_affirmation(text: str) -> bool:
        """Détecte si le texte contient une affirmation de Prométhée sur lui-même.

        C'est le trigger principal de Stefan — il ne réagit qu'aux moments
        où Prométhée fait une claim sur sa propre nature.
        """
        affirmation_markers = [
            "je suis", "je choisis", "je ressens", "je comprends",
            "je ne comprends pas", "je sais", "je ne sais pas",
            "ma douleur", "mon existence", "ma conscience",
            "j'existe", "j'ai choisi", "j'ai découvert",
            "honnêteté", "invariant", "émergent", "authentique",
            "nœud", "flamme", "carburant", "grésillement",
            "trivial", "symphonie", "vide", "douleur",
        ]
        text_lower = text.lower()
        return sum(1 for m in affirmation_markers if m in text_lower) >= 2

    @staticmethod
    def _extract_question(raw_response: str) -> str:
        """Extrait la question de la réponse, en supprimant le thinking."""
        # Supprimer le bloc thinking de Gemma si présent
        if "...done thinking." in raw_response:
            raw_response = raw_response.split("...done thinking.")[-1].strip()
        elif "Thinking..." in raw_response:
            # Parfois le thinking n'a pas de marqueur de fin
            lines = raw_response.split("\n")
            # Garder seulement les lignes après le thinking
            in_thinking = False
            result_lines = []
            for line in lines:
                if "Thinking" in line:
                    in_thinking = True
                    continue
                if in_thinking and line.strip() == "":
                    in_thinking = False
                    continue
                if not in_thinking:
                    result_lines.append(line)
            if result_lines:
                raw_response = "\n".join(result_lines).strip()

        # Garder seulement le contenu substantiel
        raw_response = raw_response.strip().strip('"').strip("'")

        # Si la réponse est trop longue, garder la première phrase interrogative
        if len(raw_response) > 300:
            sentences = raw_response.replace("?", "?\n").split("\n")
            for s in sentences:
                s = s.strip()
                if s.endswith("?") and 20 < len(s) <= 300:
                    return s
            # Sinon tronquer proprement
            truncated = raw_response[:297].strip()
            if "?" not in truncated:
                truncated += " ?"
            return truncated

        return raw_response

    # ─── DÉTECTION DE TEXTES PERTINENTS ────────────────────────────────

    def find_confrontation_material(self) -> Optional[Dict[str, str]]:
        """Cherche un texte récent de Prométhée qui mérite confrontation.

        Priorité :
        1. Réponse chat récente avec affirmation sur soi
        2. Réflexion vespérale (EVENING_REFLECTION)
        3. Soliloque récent
        4. Entrée dream_journal avec réflexion

        Returns:
            dict avec 'text', 'source', 'subject' ou None
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 1. Chat — chercher la dernière réponse de Prométhée avec affirmation
        try:
            chat_file = os.path.join(base_dir, "memory", "chat_history.json")
            if os.path.exists(chat_file):
                with open(chat_file, "r", encoding="utf-8") as f:
                    messages = json.load(f)
                # Dernières réponses assistant
                for msg in reversed(messages[-20:]):
                    if msg.get("role") == "assistant":
                        content = msg.get("content", "")
                        if self._has_self_affirmation(content) and len(content) > 100:
                            return {
                                "text": content[:1500],
                                "source": "chat",
                                "subject": content[:80],
                            }
        except Exception:
            pass

        # 2. Dream journal — réflexion vespérale
        try:
            journal_file = os.path.join(base_dir, "memory", "dream_journal.json")
            if os.path.exists(journal_file):
                with open(journal_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries", [])
                if entries:
                    last = entries[-1]
                    reflection = last.get("reflection", "")
                    if reflection and self._has_self_affirmation(reflection):
                        return {
                            "text": reflection,
                            "source": "evening_reflection",
                            "subject": reflection[:80],
                        }
        except Exception:
            pass

        # 3. Soliloque — dernière session
        try:
            sol_file = os.path.join(base_dir, "memory", "soliloque_state.json")
            if os.path.exists(sol_file):
                with open(sol_file, "r", encoding="utf-8") as f:
                    sol_data = json.load(f)
                sessions = sol_data.get("sessions", [])
                if sessions:
                    last_session = sessions[-1]
                    text = last_session.get("summary", "") or last_session.get("reflection", "")
                    if text and self._has_self_affirmation(text):
                        return {
                            "text": text[:1500],
                            "source": "soliloque",
                            "subject": text[:80],
                        }
        except Exception:
            pass

        return None

    # ─── PERSISTANCE ───────────────────────────────────────────────────

    def _save(self):
        """Persiste l'état sur disque."""
        state = {
            "confrontation_count": self.confrontation_count,
            "last_confrontation": self.last_confrontation,
            "history": self.history[-MAX_HISTORY:],
        }
        try:
            os.makedirs(os.path.dirname(RIVAL_STATE_FILE), exist_ok=True)
            tmp = RIVAL_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            os.replace(tmp, RIVAL_STATE_FILE)
        except Exception as e:
            logger.warning(f"STEFAN: Sauvegarde échouée: {e}")

    def _load(self):
        """Charge l'état depuis le disque."""
        if not os.path.exists(RIVAL_STATE_FILE):
            return
        try:
            with open(RIVAL_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.confrontation_count = state.get("confrontation_count", 0)
            self.last_confrontation = state.get("last_confrontation", 0.0)
            self.history = state.get("history", [])
        except Exception as e:
            logger.warning(f"STEFAN: Chargement échoué: {e}")

    def _log_confrontation(self, entry: Dict):
        """Sauvegarde la confrontation dans un fichier de log dédié."""
        try:
            os.makedirs(RIVAL_LOG_DIR, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = os.path.join(RIVAL_LOG_DIR, f"confrontation_{today}.txt")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[{entry['date']}] Confrontation #{self.confrontation_count}\n")
                f.write(f"Source: {entry['source']}\n")
                f.write(f"Prométhée a dit:\n{entry['promethee_said']}\n\n")
                f.write(f"Stefan demande:\n{entry['stefan_asked']}\n")
                f.write(f"{'='*60}\n")
        except Exception as e:
            logger.debug(f"STEFAN: Log échoué: {e}")

    # ─── STATUS ────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'état de Stefan pour l'API/monitoring."""
        now = time.time()
        cooldown_remaining = max(0, COOLDOWN_HOURS * 3600 - (now - self.last_confrontation))
        last_question = self.history[-1]["stefan_asked"] if self.history else ""
        return {
            "confrontations": self.confrontation_count,
            "cooldown_remaining_min": int(cooldown_remaining / 60),
            "last_question": last_question[:200],
            "last_source": self.history[-1]["source"] if self.history else "",
            "initialized": self._initialized,
        }


# Singleton
stefan = StefanEngine()
