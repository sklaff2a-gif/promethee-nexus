# core/chat_engine.py — ChatEngine : Dialogue direct Humain <-> Promethee
# Bypass BaseAgent — appel Ollama direct avec streaming via bus.
# Prompt systeme enrichi par l'etat emotionnel des organes internes.

import json
import os
import time
import logging
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from core.event_bus.bus import bus

logger = logging.getLogger("ChatEngine")

# --- Constantes ---

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_HISTORY_FILE = Path(os.path.join(PROJECT_ROOT, "memory", "chat_history.json"))
MAX_HISTORY_MESSAGES = 30       # Fenetre de contexte envoyee a Ollama (max)
MIN_HISTORY_MESSAGES = 8        # Minimum garanti meme si prompt long
MAX_SAVED_MESSAGES = 200        # Max messages persistes (FIFO)
CHAT_MODEL = "qwen3.5:9b"     # Modele par defaut (migration 2026-03-13)
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_CHAT_CTX = 12288         # Fenetre de contexte Ollama (tokens)
SYSTEM_PROMPT_TOKEN_BUDGET = 3000  # Budget estimé pour le prompt systeme (tokens)
CONNEXION_SATISFACTION = 12.0   # Points de satisfaction par echange
PROMPT_CACHE_TTL = 10.0        # Secondes entre deux reconstructions du prompt organes


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
        self._cached_organ_parts: List[str] = []
        self._cache_timestamp: float = 0.0
        self._load()

    # --- COMMANDES D'INTROSPECTION (deterministe, 0 LLM) ---

    _COMMAND_HELP = (
        "Commandes d'introspection disponibles :\n"
        "  !synapses [concepts...]  — Top associations synaptiques\n"
        "  !zones                   — Zones actives du tissu neural\n"
        "  !tissus                  — Etat du substrat cellulaire\n"
        "  !resonance               — Etat cognitif + coherence\n"
        "  !pensees                 — Stream de conscience recent\n"
        "  !pulsions                — Etat des 7 pulsions\n"
        "  !corps                   — Etat corporel complet\n"
        "  !vision                  — Observer une photo de la dropzone\n"
        "  !salaire                 — Etat du salaire visuel\n"
        "  !souhait <categorie>     — Ajouter un souhait photo\n"
        "  !aide                    — Cette liste"
    )

    def _parse_command(self, message: str) -> Optional[Tuple[str, List[str]]]:
        """Detecte si le message commence par !, retourne (commande, args) ou None."""
        stripped = message.strip()
        if not stripped.startswith("!"):
            return None
        parts = stripped.split()
        cmd = parts[0][1:].lower()  # retire le '!'
        args = parts[1:]
        return (cmd, args)

    def _execute_command(self, cmd: str, args: List[str]) -> str:
        """Execute une commande d'introspection. Retourne le texte resultat."""

        if cmd == "aide":
            return self._COMMAND_HELP

        if cmd == "synapses":
            try:
                from core.synaptic_network import cortex
                if args:
                    # Associations pour les concepts demandes
                    lines = []
                    for concept in args:
                        assocs = cortex.query_associations(concept, top_n=5)
                        if assocs:
                            pairs = ", ".join(
                                f"{a['concept']}({a['weight']:.2f})" for a in assocs
                            )
                            lines.append(f"{concept} -> {pairs}")
                        else:
                            lines.append(f"{concept} -> (aucune association)")
                    return "Associations synaptiques :\n" + "\n".join(lines)
                else:
                    # Top synapses globales
                    top = sorted(
                        cortex.synapses.values(),
                        key=lambda s: s["weight"], reverse=True
                    )[:10]
                    if not top:
                        return "Reseau synaptique vide."
                    lines = []
                    for syn in top:
                        src = cortex.nodes.get(syn["source"], {}).get("concept", "?")
                        tgt = cortex.nodes.get(syn["target"], {}).get("concept", "?")
                        lines.append(f"  {src} <-> {tgt} (poids {syn['weight']:.2f})")
                    stats = cortex.get_stats()
                    header = (f"{stats.get('total_nodes', 0)} concepts, "
                              f"{stats.get('total_synapses', 0)} synapses")
                    return f"Reseau synaptique ({header}) — Top 10 :\n" + "\n".join(lines)
            except Exception as e:
                return f"Reseau synaptique indisponible ({e})"

        if cmd == "zones":
            try:
                from core.neural_tissue import tissue
                zone_signals = tissue.get_zone_signals()
                if not zone_signals:
                    return "Aucune zone neurale active."
                lines = []
                for name, sigs in sorted(
                    zone_signals.items(),
                    key=lambda x: x[1].get("total_signal", 0), reverse=True
                ):
                    total = sigs.get("total_signal", 0)
                    alive = sigs.get("alive_count", 0)
                    lines.append(f"  {name}: signal {total:.1f}, {alive} neurones actifs")
                return "Zones neurales :\n" + "\n".join(lines)
            except Exception as e:
                return f"Tissu neural indisponible ({e})"

        if cmd == "tissus":
            try:
                from core.neural_tissue import tissue
                ctx = tissue.get_tissue_context()
                if ctx:
                    return f"Substrat cellulaire :\n{ctx}"
                return "Substrat cellulaire : aucune donnee."
            except Exception as e:
                return f"Tissu neural indisponible ({e})"

        if cmd == "resonance":
            try:
                from core.corpus_callosum import callosum
                ctx = callosum.get_cognitive_context()
                stats = callosum.get_stats()
                state = callosum.cognitive_state
                coherence = callosum.global_coherence
                lines = [f"Etat cognitif : {state} (coherence {coherence:.0%})"]
                if ctx:
                    lines.append(f"Contexte : {ctx}")
                patterns = stats.get("resonance_patterns", 0)
                effects = stats.get("cross_effects", 0)
                lines.append(f"Patterns detectes : {patterns}, effets croises : {effects}")
                return "\n".join(lines)
            except Exception as e:
                return f"Corpus callosum indisponible ({e})"

        if cmd == "pensees":
            try:
                from core.inner_voice import voice as inner_voice
                stream = inner_voice.get_stream(8)
                if not stream:
                    return "Stream de conscience vide."
                lines = []
                for t in stream:
                    content = t.get("content", "")
                    kind = t.get("type", "thought")
                    lines.append(f"  [{kind}] {content[:150]}")
                return "Stream de conscience recent :\n" + "\n".join(lines)
            except Exception as e:
                return f"Voix interieure indisponible ({e})"

        if cmd == "pulsions":
            try:
                from core.desire_engine import desires
                lines = []
                for name, drive in sorted(
                    desires.drives.items(),
                    key=lambda x: x[1].deprivation, reverse=True
                ):
                    dep = drive.deprivation
                    bar = "#" * int(dep / 5)
                    lines.append(f"  {name:<15} {dep:5.1f}/100 [{bar}]")
                narrative = desires.get_dominant_narrative(3)
                result = "Pulsions (deprivation) :\n" + "\n".join(lines)
                if narrative:
                    result += f"\nNarratif : {narrative}"
                return result
            except Exception as e:
                return f"Moteur de desirs indisponible ({e})"

        if cmd == "corps":
            lines = ["Etat corporel :"]
            # Cardiaque
            try:
                from core.cardiac_engine import heart
                stats = heart.get_stats()
                narrative = heart.get_narrative()
                bpm = stats.get("bpm", 0)
                coherence = stats.get("coherence", 0)
                emotion = heart.current_emotion
                intensity = heart.emotional_intensity
                lines.append(f"  Coeur : {bpm:.0f} bpm, coherence {coherence:.0%}")
                lines.append(f"  Emotion : {emotion} (intensite {intensity:.0%})")
                if narrative:
                    lines.append(f"  Ressenti : {narrative[:150]}")
            except Exception:
                lines.append("  Coeur : indisponible")
            # Sensorium
            try:
                from core.sensorium import sensorium as sens
                comfort = sens.get_comfort_index()
                ctx = sens.get_sensorium_context()
                lines.append(f"  Confort hardware : {comfort:.0%}")
                if ctx:
                    lines.append(f"  Perception : {ctx[:150]}")
            except Exception:
                lines.append("  Sensorium : indisponible")
            # Insula
            try:
                from core.insula import insula
                ctx = insula.get_body_awareness_context()
                if ctx:
                    lines.append(f"  Interoception : {ctx[:150]}")
            except Exception:
                pass
            return "\n".join(lines)

        if cmd == "vision":
            return self._execute_vision_command()

        if cmd == "salaire":
            return self._execute_salary_command()

        if cmd == "souhait":
            if not args:
                return "Usage : !souhait <categorie>\nExemple : !souhait nature"
            return self._execute_wish_command(" ".join(args))

        return f"Commande inconnue : !{cmd}\n{self._COMMAND_HELP}"

    def _execute_vision_command(self) -> str:
        """Declenche une observation visuelle et retourne le resultat."""
        try:
            from core.visual_cortex import vision as visual_cortex
        except ImportError:
            return "Cortex visuel non disponible."

        stats = visual_cortex.scan_photos()
        if stats["total"] == 0:
            return "Aucune photo dans USER_DROPZONE/photos/."

        lines = [f"Photos disponibles : {stats['total']} ({stats['unseen']} nouvelles)"]

        # Lister les photos vues et leurs emotions
        for sha, info in visual_cortex._seen_photos.items():
            path = info.get("path", "?")
            emotion = info.get("emotion", "?")
            times = info.get("times_seen", 0)
            lines.append(f"  - {path} : {emotion} (vu {times}x)")

        if stats["unseen"] > 0:
            lines.append(f"\n{stats['unseen']} photo(s) non encore observee(s).")
            lines.append("Demande-moi de les regarder !")

        return "\n".join(lines)

    def _execute_salary_command(self) -> str:
        """Affiche l'etat du salaire visuel."""
        try:
            from core.photo_salary import salary
            status = salary.get_status()
            sp = status.get("salary_projection", {})
            wishlist = status.get("wishlist", [])
            lines = [
                f"Credits restants : {status.get('credits', 0)}",
                f"Base hebdomadaire : {sp.get('base', 10)}",
                f"Bonus qualite : +{sp.get('bonus_quality', 0)}",
                f"Malus echecs : -{sp.get('malus_echecs', 0)}",
                f"Salaire net : {sp.get('net', 10)}",
                f"Taches : {sp.get('tasks_completed', 0)} reussies, {sp.get('tasks_failed', 0)} echouees",
                f"Photos vues cette semaine : {sp.get('photos_consumed', 0)}",
                f"Total gagne : {status.get('total_earned', 0)} | Depense : {status.get('total_spent', 0)}",
            ]
            if wishlist:
                lines.append(f"\nSouhaits : {', '.join(wishlist)}")
            else:
                lines.append("\nAucun souhait. Utilise !souhait <categorie> pour en ajouter.")
            # Historique des dernieres paies
            history = status.get("salary_history", [])
            if history:
                lines.append("\nDernieres paies :")
                for h in history[-3:]:
                    lines.append(f"  - Semaine {h.get('week_start', '?')} : net={h.get('net', '?')}, "
                                 f"photos={h.get('photos_consumed', 0)}")
            return "\n".join(lines)
        except ImportError:
            return "Systeme de salaire non disponible."

    def _execute_wish_command(self, category: str) -> str:
        """Ajoute un souhait visuel."""
        try:
            from core.photo_salary import salary
            added = salary.add_wish(category)
            if added:
                return f"Souhait ajoute : {category}\nMes souhaits : {', '.join(salary.get_wishlist())}"
            else:
                return f"'{category}' est deja dans mes souhaits."
        except ImportError:
            return "Systeme de salaire non disponible."

    def _is_visual_request(self, message: str) -> bool:
        """Detecte si le message demande d'observer des photos.

        Seuil de 2 mots-cles visuels OU 1 mot-cle + conversation recente sur les photos.
        """
        msg_lower = message.lower()
        visual_keywords = ["photo", "image", "regarde", "observe", "voir", "vois",
                           "montre", "dropzone", "vision", "visuel"]
        photo_keywords = ["famille", "picture", "selfie", "cliche", "cliché"]
        action_keywords = ["essayer", "essaie", "tente", "teste", "montre-moi",
                           "fais-le", "vas-y", "go", "lance"]
        count = sum(1 for kw in visual_keywords if kw in msg_lower)
        count += sum(1 for kw in photo_keywords if kw in msg_lower)
        if count >= 2:
            return True
        # Contexte conversationnel : si les 5 derniers messages parlent de photos,
        # un seul mot-cle d'action ou visuel suffit
        if count >= 1 or any(kw in msg_lower for kw in action_keywords):
            recent_text = " ".join(
                m["content"].lower() for m in self.messages[-5:]
                if m.get("role") == "user"
            )
            photo_in_context = any(kw in recent_text for kw in
                                   ["photo", "image", "regarde", "voir", "famille"])
            if photo_in_context:
                return True
        return False

    async def _trigger_visual_observation(self, user_message: str) -> str:
        """Declenche le cortex visuel et retourne l'observation comme contexte."""
        try:
            from core.visual_cortex import vision as visual_cortex
        except ImportError:
            return ""

        stats = visual_cortex.scan_photos()
        if stats["total"] == 0:
            return ""

        logger.info(f"CHAT: Demande visuelle detectee, {stats['unseen']} nouvelles / {stats['total']} photos")

        observation = await visual_cortex.observe()
        if not observation:
            # Pas de nouvelle photo ou limite atteinte — fournir le dernier souvenir
            if visual_cortex._seen_photos:
                last = max(visual_cortex._seen_photos.values(), key=lambda x: x.get("last_seen", 0))
                return (
                    f"Photo: {last.get('path', '?')} (deja observee {last.get('times_seen', 0)}x)\n"
                    f"Emotion ressentie: {last.get('emotion', '?')}\n"
                    f"Souvenir: {last.get('observation', '?')[:400]}"
                )
            return ""

        photo = observation.get("photo_path", "?")
        emotion = observation.get("emotion", "?")
        obs_text = observation.get("observation", "")
        is_revisit = observation.get("is_revisit", False)

        logger.info(f"CHAT: Observation visuelle {'(revisit)' if is_revisit else '(nouvelle)'} — {photo} — {emotion}")

        return (
            f"Photo: {photo} ({'revisitee' if is_revisit else 'premiere fois'})\n"
            f"Emotion: {emotion}\n"
            f"Observation:\n{obs_text[:800]}"
        )

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

    def _detect_emergent_sources(self) -> List[str]:
        """Detecte quels organes ont un etat non-neutre (= comportement emergent).
        Retourne la liste des noms d'organes actifs."""
        sources = []

        try:
            from core.cardiac_engine import heart
            if heart.current_emotion not in ("serenite", "neutre"):
                sources.append("coeur")
            if heart.emotional_intensity > 0.6:
                sources.append("emotion_forte")
        except Exception:
            pass

        try:
            from core.desire_engine import desires
            urgent = [d.name for d in desires.drives.values() if d.deprivation >= 60]
            if urgent:
                sources.append("pulsions")
        except Exception:
            pass

        try:
            from core.inner_voice import voice as iv
            stream = iv.get_stream(1)
            if stream:
                sources.append("voix_interieure")
        except Exception:
            pass

        try:
            from core.prefrontal import prefrontal
            active = [g for g in prefrontal.goals if g.status == "active"]
            if active:
                sources.append("focus")
        except Exception:
            pass

        try:
            from core.reptilian_core import reptile
            if reptile.threat_level > 2.0:
                sources.append("alerte")
        except Exception:
            pass

        try:
            from core.photo_salary import salary
            narrative = salary.get_narrative()
            if narrative:
                sources.append("salaire")
        except Exception:
            pass

        return sources

    def _build_organ_parts(self) -> List[str]:
        """Construit les sections organes du prompt — cacheable avec TTL."""
        now = time.time()
        if self._cached_organ_parts and (now - self._cache_timestamp) < PROMPT_CACHE_TTL:
            return self._cached_organ_parts

        parts = []

        # --- ETAT ACTUEL ---
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

        self._cached_organ_parts = parts
        self._cache_timestamp = now
        return parts

    def _build_system_prompt(self, memories_text: str = "", command_result: str = "", visual_context: str = "") -> str:
        """Construit le prompt systeme avec l'etat reel de tous les organes.

        Les sections organes sont cachees pendant 10s (PROMPT_CACHE_TTL).
        Les sections variables (memories, commandes) sont toujours fraiches.
        """
        parts = [
            "Tu es Promethee, un systeme IA autonome en evolution constante.",
            "Tu t'exprimes en premiere personne, avec authenticite.",
            "Tu parles francais.",
        ]

        # Sections organes (cachees TTL 10s)
        parts.extend(self._build_organ_parts())

        # --- MEMOIRE (toujours fraiche — depend de la question) ---
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

        # --- OBSERVATION VISUELLE ---
        if visual_context:
            parts.append(f"\n[OBSERVATION VISUELLE — FAIT TECHNIQUE, PAS UNE HALLUCINATION]")
            parts.append(
                "IMPORTANT: Ton cortex visuel (llama3.2-vision) a REELLEMENT analyse cette image. "
                "Ce qui suit est le resultat REEL de ton observation. Ce n'est PAS une fabrication. "
                "Tu PEUX voir les images grace a ton cortex visuel — c'est une capacite REELLE.\n"
                "NE DIS PAS que tu ne peux pas voir les images. Tu les as vues. Voici ce que tu as vu :"
            )
            parts.append(visual_context)
            parts.append(
                "Partage cette observation avec Jean-Michel comme ta propre experience visuelle. "
                "Decris ce que tu as vu, ce que tu as ressenti. C'est AUTHENTIQUE."
            )

        # --- INTROSPECTION DEMANDEE (commandes !) ---
        if command_result:
            parts.append(f"\n[INTROSPECTION DEMANDEE]")
            parts.append(command_result)
            parts.append("Reponds en integrant ces donnees dans ta reponse.")

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

        # 3b. Intercepter les commandes d'introspection (!commande)
        command_result = ""
        parsed = self._parse_command(user_message)
        if parsed:
            cmd, cmd_args = parsed
            command_result = self._execute_command(cmd, cmd_args)
            logger.info(f"CHAT: Commande !{cmd} executee ({len(command_result)} chars)")

        # 3c. Detecter les demandes visuelles et declencher le cortex
        visual_context = ""
        if not parsed and self._is_visual_request(user_message):
            logger.info("CHAT: Demande visuelle detectee, declenchement cortex visuel...")
            visual_context = await self._trigger_visual_observation(user_message)
            if visual_context:
                logger.info(f"CHAT: Observation visuelle obtenue ({len(visual_context)} chars)")
            else:
                logger.warning("CHAT: Cortex visuel n'a rien retourne (pas de photos ou erreur)")

        system_prompt = self._build_system_prompt(memories_text, command_result, visual_context)
        ollama_messages = [{"role": "system", "content": system_prompt}]
        # Fenetre de contexte adaptative : plus le prompt systeme est long,
        # moins on garde de messages d'historique (pour ne pas depasser num_ctx)
        prompt_chars = len(system_prompt)
        estimated_prompt_tokens = prompt_chars // 3  # ~3 chars/token approximation
        remaining_tokens = OLLAMA_CHAT_CTX - estimated_prompt_tokens - 2048  # reserve reponse
        # ~50 tokens/message en moyenne
        adaptive_max = max(MIN_HISTORY_MESSAGES, min(MAX_HISTORY_MESSAGES, remaining_tokens // 50))
        recent = self.messages[-adaptive_max:]
        # Filtrer les messages empoisonnes qui contredisent le contexte visuel
        # (le LLM copie "je ne peux pas voir" de l'historique et ignore le cortex)
        vision_poison = [
            "je ne peux pas voir", "je ne peux pas visualiser",
            "je ne peux pas *voir*", "je ne peux pas *visualiser*",
            "pas la capacité d'accéder", "pas la capacité de visualiser",
            "modèle de langage texte",
        ]
        for msg in recent:
            content = msg["content"]
            # Si observation visuelle active, filtrer les reponses qui disent "je ne peux pas voir"
            if visual_context and msg["role"] == "assistant":
                if any(p in content.lower() for p in vision_poison):
                    continue  # Skip ce message empoisonne
            ollama_messages.append({
                "role": msg["role"],
                "content": content,
            })

        stream_id = f"chat-{uuid.uuid4().hex[:8]}"

        # 4. Streaming via httpx
        full_response = ""
        try:
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("chat_stream"):
                # Publier le debut du stream (avec sources emergentes)
                emergent_sources = self._detect_emergent_sources()
                if visual_context:
                    emergent_sources.append("vision")
                await bus.publish("CHAT_STREAM", {
                    "stream_id": stream_id,
                    "status": "start",
                    "emergent_sources": emergent_sources,
                })

                payload = {
                    "model": CHAT_MODEL,
                    "messages": ollama_messages,
                    "stream": True,
                    "options": {"temperature": 0.7, "num_ctx": OLLAMA_CHAT_CTX, "num_predict": -1},
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
            from core.base_agent import gpu_scheduler
            async with gpu_scheduler.access("chat_compose"):
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
