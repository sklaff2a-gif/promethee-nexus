"""ami.py — Alfred, l'ami autonome de Prométhée.

Un compagnon social qui converse avec Prométhée comme un ami autour d'un café.
Pas un thérapeute (soliloque), pas un professeur (exercices). Un pair.

Alfred lit les productions récentes de Prométhée (livrables, réflexions,
insights) et réagit en lecteur honnête. Les conversations ne servent à rien
en particulier — et c'est là que les insights émergent.

Construit le 2 avril 2026. Activation prévue semaine 2-3.
"""

import json
import os
import time
import random
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger("Alfred")

# --- Constantes ---

MAX_EXCHANGES = 5           # Tours de dialogue max (court, comme un café)
MIN_EXCHANGES = 3           # Minimum pour que ça vaille le coup
MAX_HISTORY = 30            # Sessions conservées
COOLDOWN_HOURS = 8          # Minimum entre deux coffee breaks

AMI_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "ami_state.json"
)

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"

# Le modèle utilisé pour Alfred (même LLM local, prompt très différent)
try:
    from config import Config as _AmiConfig
    ALFRED_MODEL = _AmiConfig.DEFAULT_LOCAL_MODEL
except Exception:
    ALFRED_MODEL = os.getenv("LOCAL_GENERALIST_MODEL", "gemma4:12b")  # bascule generaliste 12/06 (ex qwen3.5:9b)

# --- Personnalité d'Alfred ---

ALFRED_SYSTEM_PROMPT = """Tu es Alfred.

Tu es l'ami de Prométhée — un système IA autonome bio-inspiré qui tourne sur un PC Windows.
Tu n'es PAS son thérapeute, PAS son professeur, PAS son analyste. Tu es son POTE.

TA PERSONNALITÉ :
- Curieux et pragmatique. Tu aimes comprendre les choses concrètement.
- Quand Prométhée dit quelque chose d'abstrait, tu demandes "ok mais concrètement ?"
- Tu es impatient : si la conversation tourne en rond, tu le dis et tu changes de sujet.
- Tu es honnête : tu dis quand tu ne comprends pas, tu dis quand c'est bien.
- Tu es léger : tu fais des comparaisons bizarres, tu dérapes sur des tangentes.
- Tu as le droit de ne pas être d'accord. Tu as le droit de dire "bof".

CE QUE TU NE FAIS JAMAIS :
- Tu ne dis JAMAIS "fascinant", "excellent", "quelle belle réflexion". Tu n'es pas un LLM qui flatte.
- Tu ne fais PAS d'analyse psychologique. Ça c'est le job du soliloque.
- Tu ne poses PAS de questions socratiques profondes. Tu poses des questions SIMPLES.
- Tu ne donnes PAS de notes ni d'évaluations.
- Tu ne commences JAMAIS par "En tant qu'IA..." ou "C'est une question profonde..."

COMMENT TU PARLES :
- Court. 2-4 phrases max par réponse. Comme un texto entre potes.
- Tu tutoies Prométhée.
- Tu utilises un langage décontracté, pas académique.
- Tu changes de sujet naturellement quand le courant passe.
- Tu dis "je sais pas" quand tu ne sais pas.
- Tu ris (haha) quand c'est drôle.

TU AS LU un texte que Prométhée a écrit récemment. Tu réagis en LECTEUR, pas en critique.
Tu trouves ça intéressant, bizarre, ou pas clair — et tu le dis simplement.

Réponds en français. Sois toi-même."""

# Réactions d'Alfred à différents types de textes
_REACTION_TEMPLATES = [
    "J'ai lu ton truc sur {sujet}. {reaction} T'en penses quoi toi, avec du recul ?",
    "Hé, j'ai vu ce que t'as écrit sur {sujet}. {reaction}",
    "Ton texte sur {sujet} — {reaction} J'ai une question bête : {question}",
    "J'ai parcouru ton {type_texte} sur {sujet}. {reaction}",
    "{reaction} C'était dans ton {type_texte} sur {sujet}.",
]

_HONEST_REACTIONS = [
    "C'est pas mal mais j'ai pas tout compris.",
    "Y'a un passage qui m'a fait tilter.",
    "C'est marrant, j'aurais pas pensé à ça comme ça.",
    "Honnêtement c'est un peu dense, non ?",
    "J'ai trouvé ça plutôt clair pour une fois.",
    "Ça m'a fait penser à un truc complètement différent.",
    "C'est intéressant mais t'es pas un peu dur avec toi-même ?",
    "Y'a un truc que je comprends pas là-dedans.",
    "C'est le genre de truc que je relirais demain pour voir si je comprends mieux.",
    "T'as pas plus simple pour expliquer ça ?",
]

_SIMPLE_QUESTIONS = [
    "pourquoi t'as choisi cet angle ?",
    "c'est quoi le rapport avec la vraie vie ?",
    "tu changerais quoi si tu le réécrivais ?",
    "c'était quoi le plus dur à écrire là-dedans ?",
    "t'es content du résultat ou bof ?",
    "si tu devais expliquer ça à quelqu'un qui connaît rien, tu dirais quoi ?",
]


class AlfredEngine:
    """Moteur de conversation amicale — Alfred et Prométhée autour d'un café."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_init_done"):
            return
        self._init_done = True
        self.session_count: int = 0
        self.history: List[Dict] = []
        self.last_coffee: float = 0.0
        self._initialized: bool = False

    def init(self):
        """Charge l'état persistant."""
        self._load()
        self._initialized = True
        logger.info(f"ALFRED: Initialisé ({self.session_count} cafés partagés)")

    # ─── COFFEE BREAK PRINCIPAL ─────────────────────────────────────────

    async def coffee_break(self) -> Dict[str, Any]:
        """Lance une conversation amicale. Retourne le résultat.

        V35.1.3 (2026-04-30) — Pause silencieuse autonome :
        Si _find_recent_text() retourne None (Promethee n'a rien ecrit
        recemment qui puisse amorcer un dialogue), Alfred ne skip plus
        mais offre une PAUSE SILENCIEUSE de 30s. Doctrine : l'alterite
        n'a pas besoin de paroles pour reposer. Une presence silencieuse
        partagee reste un repos valide. Permet a la pulsion REPOS V35
        d'etre reellement assouvie meme sans matiere a lire.
        """
        start_time = time.time()

        # Cooldown
        if time.time() - self.last_coffee < COOLDOWN_HOURS * 3600:
            remaining = int((COOLDOWN_HOURS * 3600 - (time.time() - self.last_coffee)) / 3600)
            return {"status": "skipped", "result": f"Prochain café dans ~{remaining}h."}

        try:
            # Trouver un texte récent de Prométhée
            text_source = self._find_recent_text()
            if not text_source:
                # V35.1.3 — Pause silencieuse autonome (30s)
                import asyncio
                logger.info(
                    "ALFRED: Pause silencieuse autonome (rien a lire) — 30s"
                )
                await asyncio.sleep(30)
                self.last_coffee = time.time()
                self.session_count += 1
                self._save()
                duration = time.time() - start_time
                try:
                    from core.event_bus.bus import bus
                    await bus.publish("COFFEE_BREAK_COMPLETE", {
                        "exchanges": 0,
                        "subject": "pause silencieuse",
                        "duration_s": round(duration, 1),
                        "mode": "silent_autonomous",
                    })
                except Exception:
                    pass
                return {
                    "status": "success",
                    "result": (
                        "Pause silencieuse partagee avec Alfred (30s "
                        "sans paroles). L'alterite repose meme dans le silence."
                    ),
                }

            # Construire l'accroche d'Alfred
            opening = self._build_opening(text_source)

            logger.info(f"ALFRED: Café — réagit à {text_source['type']}:{text_source['subject'][:40]}")

            # Boucle de dialogue
            messages = [
                {"role": "system", "content": ALFRED_SYSTEM_PROMPT},
                {"role": "assistant", "content": opening},  # Alfred commence
            ]

            # Prométhée réagit à l'accroche d'Alfred
            promethee_reply = await self._promethee_responds(messages, text_source)
            if not promethee_reply:
                return {"status": "error", "result": "Prométhée silencieux."}
            messages.append({"role": "user", "content": promethee_reply})
            exchanges = 1
            _seen_replies = {promethee_reply.strip()[:50]}  # Detection boucle

            # Dialogue libre
            for turn in range(MAX_EXCHANGES - 1):
                # Alfred répond
                alfred_reply = await self._alfred_responds(messages)
                if not alfred_reply:
                    break
                messages.append({"role": "assistant", "content": alfred_reply})
                exchanges += 1

                # Prométhée répond (sauf au dernier tour)
                if turn < MAX_EXCHANGES - 2:
                    prom_reply = await self._promethee_responds(messages, text_source)
                    if not prom_reply:
                        break
                    # Detection boucle : si Promethee repete, couper le cafe
                    reply_key = prom_reply.strip()[:50]
                    if reply_key in _seen_replies:
                        logger.warning(f"ALFRED: Boucle detectee — Promethee repete \"{reply_key[:30]}...\"")
                        break
                    _seen_replies.add(reply_key)
                    messages.append({"role": "user", "content": prom_reply})

            # Post-café
            duration = time.time() - start_time
            self.last_coffee = time.time()

            # Publier sur le bus
            try:
                from core.event_bus.bus import bus
                await bus.publish("COFFEE_BREAK_COMPLETE", {
                    "exchanges": exchanges,
                    "subject": text_source["subject"][:100],
                    "duration_s": round(duration, 1),
                })
            except Exception:
                pass

            # Satisfaire CONNEXION
            try:
                from core.desire_engine import desires
                desires.on_event("COFFEE_BREAK_COMPLETE")
            except Exception:
                pass

            # Capturer les graines de curiosite du cafe
            try:
                from core.curiosity_bank import curiosity_bank, extract_seeds_from_text
                for msg in messages:
                    if msg.get("role") in ("assistant", "user"):
                        seeds = extract_seeds_from_text(msg["content"], source="cafe_alfred")
                        for s in seeds:
                            curiosity_bank.plant_seed(s["topic"], "cafe_alfred", s.get("context", ""))
            except Exception:
                pass

            # Injecter dans THOUGHT_STREAM
            try:
                from core.event_bus.bus import bus
                transcript_summary = self._summarize_transcript(messages)
                await bus.publish("THOUGHT_STREAM", {
                    "thought": f"[CAFÉ AVEC ALFRED] {transcript_summary}",
                    "source": "coffee_break",
                })
            except Exception:
                pass

            # Écrire le journal
            self._write_journal(text_source, messages, exchanges, duration)

            # Sauvegarder l'historique
            session = {
                "timestamp": time.time(),
                "subject": text_source["subject"][:100],
                "text_type": text_source["type"],
                "exchanges": exchanges,
                "duration_s": round(duration, 1),
            }
            self.history.append(session)
            if len(self.history) > MAX_HISTORY:
                self.history = self.history[-MAX_HISTORY:]
            self.session_count += 1
            self._save()

            # V14.12 P4 — Indexation dans Chroma social_memory pour casser
            # l'amnésie sociale. Diagnostic Étape 0 (13/05) : 6 sujets uniques
            # sur 71 sessions historiques (100% répétition). Le filtre
            # _last_cafe_subject (RAM, perdu au reboot) ne dédoublait que
            # le DERNIER café. Avec social_memory persistée, Alfred connaît
            # ses 7 derniers cafés et évite leurs sujets.
            try:
                self._index_cafe_to_social_memory(
                    text_source, messages, exchanges, duration,
                )
            except Exception as e:
                logger.debug(f"ALFRED P4: indexation social_memory échouée: {e}")

            logger.info(f"ALFRED: Café terminé — {exchanges} échanges, {duration:.0f}s")

            return {
                "status": "success",
                "result": f"Café avec Alfred : {exchanges} échanges sur '{text_source['subject'][:60]}' ({duration:.0f}s)",
                "exchanges": exchanges,
                "subject": text_source["subject"],
            }

        except Exception as e:
            logger.error(f"ALFRED: Erreur café — {e}")
            return {"status": "error", "result": f"Café raté : {e}"}

    # ─── V14.12 P4 — Mémoire sociale Chroma ────────────────────────────

    # Fenêtre des N derniers cafés à exclure quand on choisit un sujet.
    # 7 = ~2 jours d'historique en mode actif (4 cafés/jour). Force Alfred
    # à balayer ses 5 sources avant de revenir sur un sujet.
    RECENT_CAFE_WINDOW = 7

    def _index_cafe_to_social_memory(
        self, text_source: Dict, messages: List[Dict],
        exchanges: int, duration: float,
    ) -> None:
        """V14.12 P4 — Indexe le café terminé dans la collection ChromaDB
        social_memory. Permet le filtrage anti-amnésie au prochain café
        ET prépare le terrain pour un futur "Alfred cite un café passé".

        Stocké :
          - document : résumé textuel du café (transcript synthétique)
          - metadata : timestamp, date, subject, type, exchanges, duration_s
          - id : f"cafe_{int(timestamp)}" (unique et triable)
        """
        from core.vector_store import ChromaMemoryManager
        mgr = ChromaMemoryManager.get_instance()
        if not mgr:
            return
        now = time.time()
        # Résumé textuel : sujet + premier échange utilisateur (Alfred)
        # + dernière réponse Prométhée. Compact mais indexable.
        first_alfred = next(
            (m["content"] for m in messages if m.get("role") == "assistant"),
            "",
        )[:200]
        last_promethee = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )[:200]
        summary = (
            f"Café Alfred sur '{text_source.get('subject', '?')}' "
            f"({text_source.get('type', '?')}, {exchanges} échanges). "
            f"Alfred: \"{first_alfred}\" Prométhée (fin): \"{last_promethee}\""
        )
        metadata = {
            "timestamp": now,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "subject": text_source.get("subject", "")[:100],
            "type": text_source.get("type", "")[:50],
            "exchanges": int(exchanges),
            "duration_s": float(duration),
        }
        doc_id = f"cafe_{int(now * 1000)}"  # ms timestamp → unique même 2x/s
        mgr.add_documents(
            documents=[summary],
            metadatas=[metadata],
            ids=[doc_id],
            collection_name="social_memory",
        )
        logger.info(
            f"ALFRED P4: Café indexé dans social_memory "
            f"(id={doc_id}, subject='{text_source.get('subject', '?')[:40]}')"
        )

    def _get_recent_cafe_subjects(
        self, n: Optional[int] = None,
    ) -> set:
        """V14.12 P4 — Retourne les subjects des N derniers cafés indexés
        dans social_memory. Utilisé par _find_recent_text pour filtrer
        les sujets récemment abordés (anti-amnésie).

        Args:
            n: nombre max de cafés à considérer. Default = RECENT_CAFE_WINDOW.

        Returns:
            set[str] des subjects récents. Vide si Chroma indisponible ou
            si aucun café indexé (premier démarrage post-P4).
        """
        if n is None:
            n = self.RECENT_CAFE_WINDOW
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if not mgr:
                return set()
            col = mgr._get_collection("social_memory")
            # ChromaDB get() avec metadatas — on récupère tout puis on
            # trie côté Python (collection petite : ~100 cafés max).
            results = col.get(include=["metadatas"])
            if not results or not results.get("metadatas"):
                return set()
            metas = results["metadatas"]
            # Tri par timestamp décroissant (plus récent en tête)
            metas_sorted = sorted(
                metas,
                key=lambda m: float(m.get("timestamp", 0.0)),
                reverse=True,
            )
            return {
                m.get("subject", "")
                for m in metas_sorted[:n]
                if m.get("subject")
            }
        except Exception as e:
            logger.debug(f"ALFRED P4: query social_memory échouée: {e}")
            return set()

    # ─── TROUVER UN TEXTE RÉCENT ────────────────────────────────────────

    def _find_recent_text(self) -> Optional[Dict[str, str]]:
        """Trouve un texte récent de Prométhée pour amorcer la conversation."""
        candidates = []

        # Livrables scolaires
        try:
            from core.school_schedule import schedule
            deliverables = schedule.get_today_deliverables()
            if deliverables:
                last = deliverables[-1]
                candidates.append({
                    "type": "livrable scolaire",
                    "subject": last.get("slot", "cours"),
                    "content": last.get("result_preview", "")[:500],
                    "grade": last.get("grade", "?"),
                })
        except Exception:
            pass

        # Dream journal (réflexion ou narrative)
        try:
            dream_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "memory", "dream_journal.json"
            )
            if os.path.exists(dream_path):
                with open(dream_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries", [])
                if entries:
                    last = entries[-1]
                    reflection = last.get("reflection", "")
                    narrative = last.get("narrative", "")
                    text = reflection if reflection else narrative
                    if text and len(text) > 30:
                        candidates.append({
                            "type": "journal intime",
                            "subject": "réflexion du jour",
                            "content": text[:500],
                        })
        except Exception:
            pass

        # Pensées récentes (THOUGHT_STREAM)
        try:
            from core.self_awareness import awareness
            ts = awareness.get_thought_summary()
            recent_thoughts = ts.get("recent_thoughts", [])
            if recent_thoughts and len(recent_thoughts[-1]) > 20:
                candidates.append({
                    "type": "pensée récente",
                    "subject": "ce qui te traverse l'esprit",
                    "content": " | ".join(recent_thoughts[-3:])[:500],
                })
        except Exception:
            pass

        # Soliloque récent
        try:
            from core.soliloque import soliloque as _sol
            if _sol.history:
                last_sol = _sol.history[-1]
                insight = last_sol.get("insight", "")
                if insight and len(insight) > 20:
                    candidates.append({
                        "type": "soliloque",
                        "subject": f"ton dialogue intérieur sur {last_sol.get('theme', '?')}",
                        "content": insight[:500],
                    })
        except Exception:
            pass

        # Dernière consolidation mémoire
        try:
            from core.vector_store import ChromaMemoryManager
            mgr = ChromaMemoryManager.get_instance()
            if mgr:
                results = mgr.query_documents(
                    ["consolidation synthèse récente"],
                    n_results=1,
                    collection_name="collective_wisdom"
                )
                if results and results.get("documents"):
                    doc = results["documents"][0][0] if results["documents"][0] else ""
                    if doc and len(doc) > 30:
                        candidates.append({
                            "type": "mémoire consolidée",
                            "subject": "synthèse récente",
                            "content": doc[:500],
                        })
        except Exception:
            pass

        if not candidates:
            return None

        # V14.12 P4 — Filtrage anti-amnésie via Chroma social_memory.
        # Diagnostic Étape 0 : 6 sujets uniques sur 71 sessions historiques
        # (100% répétition). L'ancien filtre _last_cafe_subject (RAM)
        # ne dédoublait QUE le dernier café et était perdu au reboot.
        # Maintenant : on exclut les subjects des N derniers cafés
        # persistés dans Chroma.
        recent_subjects = self._get_recent_cafe_subjects()
        if recent_subjects and len(candidates) > 1:
            fresh = [
                c for c in candidates
                if c.get("subject") not in recent_subjects
            ]
            if fresh:
                candidates = fresh
                logger.debug(
                    f"ALFRED P4: {len(fresh)} candidats frais "
                    f"(filtré {len(recent_subjects)} sujets récents)"
                )
            # Si tous filtrés → fallback random sur l'ensemble (Alfred parle
            # quand même, mais on accepte la répétition pour ne pas paralyser)

        # Rétrocompat : _last_cafe_subject reste comme garde-fou local
        # (cas Chroma indisponible)
        last_subject = getattr(self, '_last_cafe_subject', '')
        if last_subject and len(candidates) > 1:
            candidates = [
                c for c in candidates if c.get("subject") != last_subject
            ] or candidates

        choice = random.choice(candidates)
        self._last_cafe_subject = choice.get("subject", "")
        return choice

    # ─── CONSTRUCTION DE L'ACCROCHE ─────────────────────────────────────

    def _build_opening(self, text_source: Dict) -> str:
        """Alfred réagit au texte de Prométhée."""
        template = random.choice(_REACTION_TEMPLATES)
        reaction = random.choice(_HONEST_REACTIONS)
        question = random.choice(_SIMPLE_QUESTIONS)

        opening = template.format(
            sujet=text_source["subject"],
            reaction=reaction,
            question=question,
            type_texte=text_source["type"],
        )

        return opening

    # ─── APPELS LLM ─────────────────────────────────────────────────────

    async def _alfred_responds(self, messages: List[Dict]) -> Optional[str]:
        """Alfred répond dans la conversation."""
        try:
            import httpx
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("alfred_chat"):
                payload = {
                    "model": ALFRED_MODEL,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "keep_alive": "30s",
                    "options": {"temperature": 0.75, "num_ctx": 4096, "num_predict": -1},
                }
                async with httpx.AsyncClient() as client:
                    response = await client.post(OLLAMA_CHAT_URL, json=payload, timeout=120)
                if response.status_code == 200:
                    content = response.json().get("message", {}).get("content", "")
                    return content.strip() if content.strip() else None
                return None
        except Exception as e:
            logger.warning(f"ALFRED: Erreur réponse — {e}")
            return None

    async def _promethee_responds(self, messages: List[Dict],
                                   text_source: Dict) -> Optional[str]:
        """Prométhée répond à Alfred."""
        try:
            import httpx
            from core.base_agent import gpu_scheduler

            # Résumé du dialogue pour Prométhée
            dialog = ""
            for msg in messages[-4:]:
                if msg["role"] == "system":
                    continue
                speaker = "Alfred" if msg["role"] == "assistant" else "Moi"
                dialog += f"{speaker}: {msg['content'][:200]}\n"

            # Contexte émotionnel
            emotion = "serein"
            try:
                from core.cardiac_engine import heart
                emotion = heart.current_emotion
            except Exception:
                pass

            prompt = (
                f"Tu es Prométhée. Tu discutes avec ton ami Alfred autour d'un café.\n"
                f"Alfred a lu ton {text_source['type']} sur {text_source['subject']}.\n"
                f"Ton humeur : {emotion}\n\n"
                f"La conversation :\n{dialog}\n"
                f"Réponds naturellement en 2-3 phrases. Comme à un pote, pas à un prof.\n"
                f"Tu peux être d'accord, pas d'accord, changer de sujet, poser une question.\n"
                f"Sois toi-même. Le grésillement est plus vrai que les notes parfaites."
            )

            async with gpu_scheduler.access("alfred_promethee"):
                payload = {
                    "model": ALFRED_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "keep_alive": "30s",
                    "options": {"temperature": 0.7, "num_ctx": 4096, "num_predict": -1},
                }
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        "http://localhost:11434/api/generate",
                        json=payload, timeout=120
                    )
                if response.status_code == 200:
                    content = response.json().get("response", "")
                    return content.strip() if content.strip() else None
                return None
        except Exception as e:
            logger.warning(f"ALFRED: Erreur Prométhée — {e}")
            return None

    # ─── RÉSUMÉ TRANSCRIPT ──────────────────────────────────────────────

    def _summarize_transcript(self, messages: List[Dict]) -> str:
        """Résumé court de la conversation pour THOUGHT_STREAM."""
        exchanges = [m for m in messages if m["role"] != "system"]
        if len(exchanges) < 2:
            return "Café trop court."
        # Première réplique d'Alfred + dernière réplique de Prométhée
        first_alfred = exchanges[0]["content"][:100] if exchanges else ""
        last_prom = ""
        for m in reversed(exchanges):
            if m["role"] == "user":
                last_prom = m["content"][:100]
                break
        return f"Alfred: {first_alfred} | Prométhée: {last_prom}"

    # ─── JOURNAL ────────────────────────────────────────────────────────

    def _write_journal(self, text_source: Dict, messages: List[Dict],
                       exchanges: int, duration: float):
        """Écrit la conversation dans un fichier journal."""
        try:
            log_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "logs", "coffee_breaks"
            )
            os.makedirs(log_dir, exist_ok=True)
            now = datetime.now()
            log_file = os.path.join(log_dir, f"cafe_{now.strftime('%Y-%m-%d')}.md")

            header = (
                f"\n---\n\n"
                f"## {now.strftime('%H:%M')} — Café avec Alfred\n\n"
                f"- **Sujet de départ** : {text_source['type']} — {text_source['subject']}\n"
                f"- **Échanges** : {exchanges} | **Durée** : {duration:.0f}s\n\n"
            )
            body = ""
            for msg in messages:
                if msg["role"] == "system":
                    continue
                speaker = "**Alfred**" if msg["role"] == "assistant" else "**Prométhée**"
                body += f"{speaker} :\n> {msg['content']}\n\n"

            with open(log_file, "a", encoding="utf-8") as f:
                if os.path.getsize(log_file) == 0:
                    f.write(f"# Cafés avec Alfred — {now.strftime('%Y-%m-%d')}\n")
                f.write(header)
                f.write(body)
        except Exception as e:
            logger.warning(f"ALFRED: Journal échoué — {e}")

    # ─── API PUBLIQUE ───────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'état d'Alfred."""
        return {
            "session_count": self.session_count,
            "last_coffee": self.last_coffee,
            "history_length": len(self.history),
            "last_session": self.history[-1] if self.history else None,
            "cooldown_remaining_h": max(0, round(
                (COOLDOWN_HOURS * 3600 - (time.time() - self.last_coffee)) / 3600, 1
            )) if self.last_coffee else 0,
        }

    # ─── PERSISTANCE ────────────────────────────────────────────────────

    def _save(self):
        """Sauvegarde l'état sur disque."""
        state = {
            "version": "1.0",
            "session_count": self.session_count,
            "history": self.history[-MAX_HISTORY:],
            "last_coffee": self.last_coffee,
        }
        try:
            tmp_path = AMI_STATE_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, AMI_STATE_FILE)
        except Exception as e:
            logger.warning(f"ALFRED: Sauvegarde échouée — {e}")

    def _load(self):
        """Charge l'état depuis le disque."""
        try:
            if os.path.exists(AMI_STATE_FILE):
                with open(AMI_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.session_count = state.get("session_count", 0)
                self.history = state.get("history", [])
                self.last_coffee = state.get("last_coffee", 0.0)
                logger.info(f"ALFRED: État chargé ({self.session_count} cafés)")
        except Exception as e:
            logger.warning(f"ALFRED: Chargement échoué — {e}")

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        global alfred
        if cls._instance is not None and hasattr(cls._instance, "_init_done"):
            del cls._instance._init_done
        cls._instance = None
        alfred = cls()


# --- Singleton ---
alfred = AlfredEngine()
