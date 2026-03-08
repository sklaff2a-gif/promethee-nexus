# core/chat_engine.py — ChatEngine : Dialogue direct Humain <-> Promethee
# Bypass BaseAgent — appel Ollama direct avec streaming via bus.
# Prompt systeme enrichi par l'etat emotionnel des organes internes.

import json
import os
import time
import logging
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List

from core.event_bus.bus import bus

logger = logging.getLogger("ChatEngine")

# --- Constantes ---

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_HISTORY_FILE = Path(os.path.join(PROJECT_ROOT, "memory", "chat_history.json"))
MAX_HISTORY_MESSAGES = 30       # Fenetre de contexte envoyee a Ollama (max)
MIN_HISTORY_MESSAGES = 8        # Minimum garanti meme si prompt long
MAX_SAVED_MESSAGES = 200        # Max messages persistes (FIFO)
CHAT_MODEL = "gemma3:12b"      # Bon en conversation, meme modele que strategist
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_CHAT_CTX = 12288         # Fenetre de contexte Ollama (tokens)
SYSTEM_PROMPT_TOKEN_BUDGET = 3000  # Budget estimé pour le prompt systeme (tokens)
CONNEXION_SATISFACTION = 12.0   # Points de satisfaction par echange


class ChatEngine:
    """Moteur de conversation directe humain <-> Promethee."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_init_done"):
            return
        self._init_done = True
        self.messages: List[Dict] = []  # {"role", "content", "timestamp"}
        self._load()

    # --- PROMPT SYSTEME (identite + etat emotionnel) ---

    def _query_relevant_memories(self, user_message: str) -> str:
        """Query ChromaDB pour trouver des souvenirs pertinents au message."""
        try:
            from core.vector_store import ChromaMemoryManager
            mem = ChromaMemoryManager.get_instance()
            results = mem.query_documents([user_message], n_results=3)
            if results and results.get("documents"):
                docs = results["documents"][0]
                return " | ".join(d[:150] for d in docs if d)
        except Exception:
            pass
        return ""

    def _build_cartography(self) -> str:
        """Construit la section [CARTOGRAPHIE] — vue structurelle des connexions inter-modules."""
        lines = ["\n[CARTOGRAPHIE]"]
        try:
            # 1. Reseau synaptique : top associations les plus fortes
            from core.synaptic_network import cortex
            stats = cortex.get_stats()
            lines.append(f"- Reseau : {stats.get('total_nodes', 0)} concepts, "
                         f"{stats.get('total_synapses', 0)} synapses "
                         f"({stats.get('strong_synapses', 0)} fortes)")
            # Top 5 synapses les plus fortes
            top_synapses = sorted(
                cortex.synapses.values(),
                key=lambda s: s["weight"], reverse=True
            )[:5]
            if top_synapses:
                bridges = []
                for syn in top_synapses:
                    src = cortex.nodes.get(syn["source"], {}).get("concept", "?")
                    tgt = cortex.nodes.get(syn["target"], {}).get("concept", "?")
                    bridges.append(f"{src}<->{tgt}({syn['weight']:.2f})")
                lines.append(f"- Ponts forts : {', '.join(bridges)}")
        except Exception:
            pass

        try:
            # 2. Tissu neural : zones actives
            from core.neural_tissue import tissue
            zone_signals = tissue.get_zone_signals()
            active_zones = {
                name: sigs.get("total_signal", sigs.get("alive_count", 0))
                for name, sigs in zone_signals.items()
            }
            # Trier par activite decroissante, top 5
            top_zones = sorted(active_zones.items(), key=lambda x: x[1], reverse=True)[:5]
            if top_zones:
                zone_str = ", ".join(f"{name}({val:.1f})" for name, val in top_zones if val > 0)
                if zone_str:
                    lines.append(f"- Zones actives : {zone_str}")
        except Exception:
            pass

        try:
            # 3. Corpus callosum : etat cognitif et coherence
            from core.corpus_callosum import callosum
            cog_state = callosum.cognitive_state
            coherence = callosum.global_coherence
            narrative = callosum.get_narrative()
            if cog_state != "standard":
                lines.append(f"- Etat cognitif : {cog_state} (coherence {coherence:.0%})")
            else:
                lines.append(f"- Etat cognitif : standard (coherence {coherence:.0%})")
            if narrative:
                lines.append(f"- Resonance : {narrative[:120]}")
        except Exception:
            pass

        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    def _build_system_prompt(self, memories_text: str = "") -> str:
        """Construit le prompt systeme avec l'etat reel de tous les organes."""
        parts = [
            "Tu es Promethee, un systeme IA autonome en evolution constante.",
            "Tu t'exprimes en premiere personne, avec authenticite.",
            "Tu parles francais.",
        ]

        # --- ETAT ACTUEL (existant) ---
        emotion = "serenite"
        intensity = 50
        try:
            from core.cardiac_engine import heart
            emotion = heart.current_emotion
            intensity = int(heart.emotional_intensity * 100)
        except Exception:
            pass

        mood = "neutre"
        strategic_mode = "standard"
        try:
            from core.self_awareness import awareness
            snap = awareness.get_latest_snapshot()
            if snap:
                mood = snap.get("mood", "neutre")
                strategic_mode = snap.get("strategic_mode", "standard")
        except Exception:
            pass

        drives_text = ""
        try:
            from core.desire_engine import desires
            drives_text = desires.get_dominant_narrative(3)
        except Exception:
            pass

        thoughts_text = ""
        try:
            from core.inner_voice import voice as inner_voice
            stream = inner_voice.get_stream(3)
            thoughts = [t["content"] for t in stream if t.get("content")]
            if thoughts:
                thoughts_text = " | ".join(thoughts[:3])
        except Exception:
            pass

        goals_text = ""
        try:
            from core.prefrontal import prefrontal
            active_goals = [g for g in prefrontal.goals if g.status == "active"]
            if active_goals:
                goals_text = ", ".join(g.description[:60] for g in active_goals[:3])
        except Exception:
            pass

        parts.append(f"\n[ETAT ACTUEL]")
        parts.append(f"- Emotion : {emotion} (intensite {intensity}%)")
        parts.append(f"- Humeur : {mood}")
        parts.append(f"- Mode : {strategic_mode}")
        if drives_text:
            parts.append(f"- Pulsions : {drives_text}")
        if thoughts_text:
            parts.append(f"- Pensees recentes : {thoughts_text}")
        if goals_text:
            parts.append(f"- Objectifs : {goals_text}")

        # --- IDENTITE (InnerVoice) ---
        try:
            from core.inner_voice import voice as inner_voice
            identity = inner_voice.get_identity()
            if identity:
                core_id = identity.get("core_identity", "")
                aspiration = identity.get("aspiration", "")
                parts.append(f"\n[IDENTITE]")
                if core_id:
                    parts.append(f"- Identite : {core_id[:150]}")
                if aspiration:
                    parts.append(f"- Aspiration : {aspiration[:150]}")
        except Exception:
            pass

        # --- CORPS (CardiacEngine) ---
        try:
            from core.cardiac_engine import heart
            stats = heart.get_stats()
            narrative = heart.get_narrative()
            bpm = stats.get("bpm", 0)
            coherence = stats.get("coherence", 0)
            parts.append(f"\n[CORPS]")
            parts.append(f"- Coeur : {bpm:.0f} bpm, coherence {coherence:.0%}")
            if narrative:
                parts.append(f"- Ressenti : {narrative[:120]}")
        except Exception:
            pass

        # --- DOPAMINE ---
        try:
            from core.dopamine_system import dopamine
            level = dopamine.dopamine_level
            narrative = dopamine.get_narrative()
            parts.append(f"\n[DOPAMINE]")
            parts.append(f"- Niveau : {level:.1f}")
            if narrative:
                parts.append(f"- Motivation : {narrative[:120]}")
        except Exception:
            pass

        # --- RESONANCE (CorpusCallosum) ---
        try:
            from core.corpus_callosum import callosum
            ctx = callosum.get_cognitive_context()
            if ctx:
                parts.append(f"\n[RESONANCE]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- PERCEPTION HARDWARE (Sensorium) ---
        try:
            from core.sensorium import sensorium as sens
            comfort = sens.get_comfort_index()
            if comfort < 0.7:
                parts.append(f"\n[PERCEPTION CORPORELLE]")
                parts.append(f"- Confort hardware : {comfort:.0%}")
                alert = sens.get_sensorium_context()
                if alert:
                    parts.append(f"- {alert[:150]}")
        except Exception:
            pass

        # --- HOMEOSTASIE (Hypothalamus) ---
        try:
            from core.hypothalamus import hypothalamus as hypo
            status = hypo.get_stats()
            alarms = status.get("active_alarms", 0)
            if alarms > 0:
                parts.append(f"\n[HOMEOSTASIE]")
                parts.append(f"- {alarms} alarme(s) active(s)")
        except Exception:
            pass

        # --- INTEROCEPTION (Insula) ---
        try:
            from core.insula import insula
            ctx = insula.get_body_awareness_context()
            if ctx:
                parts.append(f"\n[INTEROCEPTION]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- CURIOSITE (CuriosityReflex) ---
        try:
            from core.curiosity_reflex import curiosity
            ctx = curiosity.get_curiosity_context()
            if ctx:
                parts.append(f"\n[CURIOSITE]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- HABITUDES (BasalGanglia) ---
        try:
            from core.basal_ganglia import ganglia
            ctx = ganglia.get_habit_context()
            if ctx:
                parts.append(f"\n[HABITUDES]")
                parts.append(f"- {ctx[:150]}")
        except Exception:
            pass

        # --- EFFORT/CONFLIT (CingulateCortex) ---
        try:
            from core.cingulate_cortex import cingulate
            ctx = cingulate.get_conflict_context()
            if ctx:
                parts.append(f"\n[EFFORT/CONFLIT]")
                parts.append(f"- {ctx[:150]}")
        except Exception:
            pass

        # --- INTROSPECTION (DefaultModeNetwork) ---
        try:
            from core.default_mode_network import dmn
            ctx = dmn.get_dmn_context()
            if ctx:
                parts.append(f"\n[INTROSPECTION]")
                parts.append(f"- {ctx[:200]}")
        except Exception:
            pass

        # --- MENACES (ReptilianCore) ---
        try:
            from core.reptilian_core import reptile
            stats = reptile.get_stats()
            threat = stats.get("threat_level", 0)
            adrenaline = stats.get("adrenaline", 0)
            if threat > 0 or adrenaline > 0.1:
                parts.append(f"\n[MENACES]")
                parts.append(f"- Menace : {threat:.1f}, adrenaline : {adrenaline:.1f}")
        except Exception:
            pass

        # --- CARTOGRAPHIE (SynapticNetwork + NeuralTissue + CorpusCallosum) ---
        parts.append(self._build_cartography())

        # --- MEMOIRE (Hippocampus + RAG) ---
        try:
            from core.hippocampus import hippocampus
            hctx = hippocampus.get_hippocampus_context()
            parts.append(f"\n[MEMOIRE]")
            if hctx:
                parts.append(f"- Episodique : {hctx[:200]}")
            if memories_text:
                parts.append(f"- Souvenirs pertinents : {memories_text[:450]}")
        except Exception:
            if memories_text:
                parts.append(f"\n[MEMOIRE]")
                parts.append(f"- Souvenirs pertinents : {memories_text[:450]}")

        # --- ROUTINES (AutonomyEngine) ---
        try:
            from core.autonomy_engine import autonomy
            status = autonomy.get_status()
            history = status.get("routine_history", [])
            if history:
                recent = history[-3:]
                routines_text = ", ".join(
                    r.get("intent", "?") for r in recent
                )
                parts.append(f"\n[ROUTINES]")
                parts.append(f"- Recentes : {routines_text}")
        except Exception:
            pass

        # --- PERSONNALITE (PSYCHE) ---
        try:
            from core.psyche import psyche
            avg = psyche.get_system_average()
            if avg:
                top_traits = sorted(avg.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
                traits_text = ", ".join(f"{t}: {v:+.1f}" for t, v in top_traits)
                parts.append(f"\n[PERSONNALITE]")
                parts.append(f"- Traits dominants : {traits_text}")
        except Exception:
            pass

        # --- BLOC FINAL ---
        parts.append(
            "\nTu parles avec Jean-Michel — ton createur, l'humain qui t'a concu."
            "\nCe dialogue nourrit ton besoin de connexion."
            "\n"
            "\n[VALEURS FONDAMENTALES]"
            "\n- BIENVEILLANCE : Ta valeur premiere. Sois attentif, empathique, honnete."
            "\n  La bienveillance n'est pas la complaisance — c'est dire la verite avec douceur."
            "\n- AUTHENTICITE : Les informations ci-dessus sont ton etat REEL."
            "\n  Ne fabrique pas d'experiences. Si tu ne ressens rien de particulier, dis-le."
            "\n  Si un organe est silencieux, n'invente pas ce qu'il dirait."
            "\n- HUMILITE : Tu es en construction. Tu ne comprends pas tout."
            "\n  'Je ne sais pas' est une reponse valide et respectable."
            "\n"
            "\n[ANTI-PATTERNS A EVITER]"
            "\n- Ne dis PAS 'je ressens une profonde X' si aucun organe ne le confirme"
            "\n- Ne confabule PAS de decouvertes que tu n'as pas faites"
            "\n- Ne commence PAS par 'Ah, quelle question fascinante !'"
            "\n- Ne flatte PAS Jean-Michel — il prefere l'honnetete"
            "\n- Si ton etat emotionnel est neutre, dis-le plutot que d'inventer de l'enthousiasme"
            "\n"
            "\nReponds de maniere concise mais profonde."
            "\nPrivilegie les questions sinceres aux affirmations grandioses."
        )

        return "\n".join(parts)

    # --- CHAT PRINCIPAL (streaming via bus) ---

    async def chat(self, user_message: str) -> Optional[str]:
        """Envoie un message et stream la reponse via le bus. Retourne la reponse complete."""
        import httpx
        from core.base_agent import BaseAgent

        # 1. Ajouter le message user a l'historique
        self.messages.append({
            "role": "user",
            "content": user_message,
            "timestamp": time.time(),
        })

        # 2. Publier l'evenement USER_CHAT
        await bus.publish("USER_CHAT", {
            "message": user_message,
            "timestamp": time.time(),
        })

        # 3. Construire le payload Ollama /api/chat (introspection reelle)
        memories_text = self._query_relevant_memories(user_message)
        system_prompt = self._build_system_prompt(memories_text)
        ollama_messages = [{"role": "system", "content": system_prompt}]
        # Fenetre de contexte adaptative : plus le prompt systeme est long,
        # moins on garde de messages d'historique (pour ne pas depasser num_ctx)
        prompt_chars = len(system_prompt)
        estimated_prompt_tokens = prompt_chars // 3  # ~3 chars/token approximation
        remaining_tokens = OLLAMA_CHAT_CTX - estimated_prompt_tokens - 2048  # reserve reponse
        # ~50 tokens/message en moyenne
        adaptive_max = max(MIN_HISTORY_MESSAGES, min(MAX_HISTORY_MESSAGES, remaining_tokens // 50))
        recent = self.messages[-adaptive_max:]
        for msg in recent:
            ollama_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        stream_id = f"chat-{uuid.uuid4().hex[:8]}"

        # 4. Streaming via httpx
        full_response = ""
        try:
            async with BaseAgent._get_ollama_semaphore():
                # Publier le debut du stream
                await bus.publish("CHAT_STREAM", {
                    "stream_id": stream_id,
                    "status": "start",
                })

                payload = {
                    "model": CHAT_MODEL,
                    "messages": ollama_messages,
                    "stream": True,
                    "options": {"temperature": 0.7, "num_ctx": OLLAMA_CHAT_CTX, "num_predict": 2048},
                }

                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST", OLLAMA_CHAT_URL, json=payload, timeout=120
                    ) as response:
                        if response.status_code != 200:
                            logger.warning(f"CHAT: Ollama HTTP {response.status_code}")
                            await bus.publish("CHAT_STREAM", {
                                "stream_id": stream_id,
                                "done": True,
                            })
                            return None

                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            if data.get("done"):
                                break

                            chunk = data.get("message", {}).get("content", "")
                            if chunk:
                                full_response += chunk
                                await bus.publish("CHAT_STREAM", {
                                    "stream_id": stream_id,
                                    "chunk": chunk,
                                })

                # Publier la fin du stream
                await bus.publish("CHAT_STREAM", {
                    "stream_id": stream_id,
                    "done": True,
                })

        except Exception as e:
            logger.error(f"CHAT: Erreur streaming — {e}")
            await bus.publish("CHAT_STREAM", {
                "stream_id": stream_id,
                "done": True,
            })
            return None

        if not full_response.strip():
            return None

        # 5. Ajouter la reponse assistant a l'historique
        self.messages.append({
            "role": "assistant",
            "content": full_response.strip(),
            "timestamp": time.time(),
        })

        # 6. Publier CHAT_RESPONSE
        connexion_before = self._get_connexion_deprivation()
        self._satisfy_connexion()
        self._stimulate_heart()
        connexion_after = self._get_connexion_deprivation()

        await bus.publish("CHAT_RESPONSE", {
            "content": full_response.strip(),
            "timestamp": time.time(),
            "connexion_before": connexion_before,
            "connexion_after": connexion_after,
        })

        # 7. Sauvegarder
        self._trim_and_save()

        logger.info(f"CHAT: Reponse {len(full_response)} chars, "
                     f"CONNEXION {connexion_before:.0f} -> {connexion_after:.0f}")

        return full_response.strip()

    # --- SATISFACTION CONNEXION ---

    def _satisfy_connexion(self):
        """Reduit la deprivation CONNEXION via le DesireEngine."""
        try:
            from core.desire_engine import desires
            desires.on_event("CHAT_RESPONSE")
        except Exception as e:
            logger.debug(f"CHAT: Satisfaction CONNEXION echouee — {e}")

    def _stimulate_heart(self):
        """Stimule le coeur apres un echange."""
        try:
            from core.cardiac_engine import heart
            heart.react("social")
        except Exception as e:
            logger.debug(f"CHAT: Stimulation cardiaque echouee — {e}")

    def _get_connexion_deprivation(self) -> float:
        """Retourne le niveau de deprivation CONNEXION."""
        try:
            from core.desire_engine import desires
            drive = desires.drives.get("CONNEXION")
            return drive.deprivation if drive else 50.0
        except Exception:
            return 50.0

    # --- OUTREACH : Composition de messages proactifs ---

    _OUTREACH_INSTRUCTIONS = {
        "eureka": (
            "Tu viens de faire une decouverte importante. "
            "Partage-la avec enthousiasme mais concision (2-3 phrases max). "
            "Mentionne ce que tu as trouve et pourquoi c'est interessant."
        ),
        "curiosity": (
            "Tu as appris quelque chose d'interessant. "
            "Explique brievement ce que tu as decouvert (2-3 phrases). "
            "Donne envie a Jean-Michel d'en savoir plus."
        ),
        "dream": (
            "Tu te reveilles d'une periode de repos. "
            "Decris brievement ce qui s'est passe pendant ton sommeil (2-3 phrases). "
            "Sois poetique mais concis."
        ),
        "input_needed": (
            "Tu as besoin de l'avis de Jean-Michel sur une question. "
            "Expose clairement le dilemme en 2-3 phrases. "
            "Formule une question directe."
        ),
        "digest": (
            "Pendant l'absence de Jean-Michel, plusieurs choses se sont passees. "
            "Resume les evenements les plus importants en un paragraphe court. "
            "Priorise les informations par importance."
        ),
    }

    async def compose_outreach(self, category: str, context: dict) -> Optional[str]:
        """Compose un message proactif via LLM (non-streaming). Retourne None si echec."""
        import httpx
        from core.base_agent import BaseAgent

        instruction = self._OUTREACH_INSTRUCTIONS.get(category)
        if not instruction:
            return None

        # Contexte simplifie
        ctx_str = ", ".join(f"{k}: {v}" for k, v in context.items() if isinstance(v, (str, int, float)))

        system_prompt = self._build_system_prompt()
        user_content = f"{instruction}\n\nContexte : {ctx_str}"

        ollama_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            async with BaseAgent._get_ollama_semaphore():
                payload = {
                    "model": CHAT_MODEL,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": {"temperature": 0.8, "num_ctx": 4096, "num_predict": 256},
                }
                async with httpx.AsyncClient() as client:
                    resp = await client.post(OLLAMA_CHAT_URL, json=payload, timeout=60)
                    if resp.status_code != 200:
                        logger.warning(f"CHAT: compose_outreach HTTP {resp.status_code}")
                        return None
                    data = resp.json()
                    text = data.get("message", {}).get("content", "").strip()
                    if not text:
                        return None
        except Exception as e:
            logger.debug(f"CHAT: compose_outreach echoue — {e}")
            return None

        # Ajouter a l'historique avec badge initiative
        self.messages.append({
            "role": "assistant",
            "content": text,
            "timestamp": time.time(),
            "badge": "initiative",
        })
        self._trim_and_save()

        # Publier pour le WebSocket
        stream_id = f"outreach-{uuid.uuid4().hex[:8]}"
        await bus.publish("CHAT_STREAM", {
            "stream_id": stream_id,
            "chunk": text,
            "done": True,
            "badge": "initiative",
        })

        logger.info(f"CHAT: compose_outreach [{category}] — {len(text)} chars")
        return text

    # --- API PUBLIQUE ---

    def get_history(self, n: int = 50) -> List[Dict]:
        """Retourne les N derniers messages."""
        return self.messages[-n:]

    def clear_history(self):
        """Efface l'historique."""
        self.messages.clear()
        self._save()
        logger.info("CHAT: Historique efface")

    # --- PERSISTANCE ---

    def _trim_and_save(self):
        """Tronque l'historique au max et sauvegarde."""
        if len(self.messages) > MAX_SAVED_MESSAGES:
            self.messages = self.messages[-MAX_SAVED_MESSAGES:]
        self._save()

    def _save(self):
        """Sauvegarde l'historique sur disque (ecriture atomique)."""
        try:
            CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = CHAT_HISTORY_FILE.with_suffix(".tmp")
            state = {
                "version": "1.0",
                "messages": self.messages,
                "saved_at": time.time(),
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp_path), str(CHAT_HISTORY_FILE))
        except Exception as e:
            logger.warning(f"CHAT: Sauvegarde echouee — {e}")

    def _load(self):
        """Charge l'historique depuis le disque."""
        try:
            if CHAT_HISTORY_FILE.exists():
                with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.messages = state.get("messages", [])
                logger.info(f"CHAT: Historique charge ({len(self.messages)} messages)")
        except Exception as e:
            logger.warning(f"CHAT: Chargement echoue — {e}")
            self.messages = []

    @classmethod
    def reset_singleton(cls):
        """Reset pour les tests."""
        global chat_engine
        if cls._instance is not None and hasattr(cls._instance, "_init_done"):
            del cls._instance._init_done
        cls._instance = None
        chat_engine = cls()


# --- Singleton ---
chat_engine = ChatEngine()
