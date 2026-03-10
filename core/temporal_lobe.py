"""
Lobe Temporal — Couche d'intégration mémoire unifiée.

Agrège ChromaDB, hippocampe épisodique, réseau synaptique,
marqueurs somatiques et pulsions pour produire des souvenirs
enrichis (MemoryTrace). Purement déterministe, 0 appel LLM.
"""

import os
import json
import time
import math
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

logger = logging.getLogger("promethee.temporal_lobe")

TEMPORAL_LOBE_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "temporal_lobe_state.json"
)

# --- Constantes ---

CACHE_TTL = 60.0               # Durée de vie du cache recall (secondes)
MAX_COOCCURRENCES = 500         # Taille max de l'index co-occurrences par clé
COOCCURRENCE_DECAY_DAYS = 7.0   # Demi-vie des co-occurrences
MEMORY_HALF_LIFE_DAYS = 30      # Demi-vie pour le scoring temporel
MIN_RELEVANCE = 0.05            # Score minimum pour garder un trace
MAX_TRACES_INTERNAL = 30        # Nombre max de traces internes avant tri


# --- MemoryTrace ---

@dataclass
class MemoryTrace:
    """Souvenir enrichi — unité de sortie du Lobe Temporal."""
    text: str
    relevance: float = 0.0
    timestamp: float = 0.0
    source: str = "chroma"          # "chroma" | "episodic" | "associative"
    # Enrichissements
    emotion: str = ""
    emotion_intensity: float = 0.0
    associated_concepts: list = field(default_factory=list)
    episode_summary: str = ""
    arc_type: str = ""
    somatic_signal: float = 0.0
    drive_context: str = ""
    agent: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- Helpers ---

def _temporal_decay(age_seconds: float, half_life_days: float = MEMORY_HALF_LIFE_DAYS) -> float:
    """Décroissance exponentielle temporelle [0, 1]."""
    if age_seconds <= 0:
        return 1.0
    age_days = age_seconds / 86400.0
    return math.exp(-age_days * 0.693 / half_life_days)


def _text_overlap(query_words: set, text: str) -> float:
    """Score de chevauchement mots-clés [0, 1]."""
    if not query_words or not text:
        return 0.0
    text_words = set(text.lower().split())
    if not text_words:
        return 0.0
    overlap = query_words & text_words
    return len(overlap) / max(len(query_words), 1)


def _cache_key(query: str, limit: int, collection: str,
               include_episodic: bool, include_associative: bool,
               emotion_filter: str) -> str:
    """Génère une clé de cache pour un recall."""
    raw = f"{query}|{limit}|{collection}|{include_episodic}|{include_associative}|{emotion_filter}"
    return hashlib.md5(raw.encode()).hexdigest()


# --- Classe principale ---

class TemporalLobe:
    """Lobe Temporal — couche d'intégration mémoire unifiée."""

    _instance: Optional["TemporalLobe"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Index co-occurrences intent/émotion/concepts
        # Format: {"intent:X": {"emotion:Y": count, "concept:Z": count}, ...}
        self._cooccurrences: dict[str, dict[str, float]] = {}

        # Cache recall (clé -> (timestamp, traces))
        self._recall_cache: dict[str, tuple[float, list[MemoryTrace]]] = {}

        # Stats session
        self._total_recalls: int = 0
        self._total_enrichments: int = 0
        self._total_cache_hits: int = 0

        # Charger état persisté
        self._load()

    @classmethod
    def reset_singleton(cls):
        cls._instance = None

    def init(self):
        """Souscriptions bus — appeler après construction."""
        try:
            from core.event_bus.bus import bus
            bus.subscribe("AUTONOMY_ROUTINE_COMPLETE", self._on_routine_complete)
            logger.info("[TEMPORAL] Lobe Temporal initialisé — intégration mémoire active")
        except Exception as e:
            logger.warning(f"[TEMPORAL] Bus indisponible : {e}")

    # =========================================================
    # RECALL UNIFIÉ
    # =========================================================

    def unified_recall(self, query: str, limit: int = 5,
                       collection: str = "collective_wisdom",
                       include_episodic: bool = True,
                       include_associative: bool = True,
                       emotion_filter: str = "") -> list[MemoryTrace]:
        """Rappel unifié : ChromaDB + hippocampe + synaptique + cardiaque.

        Args:
            query: Texte de recherche.
            limit: Nombre max de résultats.
            collection: Collection ChromaDB.
            include_episodic: Inclure les épisodes hippocampiques.
            include_associative: Inclure les associations synaptiques.
            emotion_filter: Filtrer par émotion (ex: "frustration").

        Returns:
            Liste de MemoryTrace triée par relevance décroissante.
        """
        if not query or not query.strip():
            return []

        # Cache
        ck = _cache_key(query, limit, collection, include_episodic,
                        include_associative, emotion_filter)
        cached = self._recall_cache.get(ck)
        if cached and (time.time() - cached[0]) < CACHE_TTL:
            self._total_cache_hits += 1
            return cached[1]

        now = time.time()
        query_words = set(query.lower().split())
        traces: list[MemoryTrace] = []

        # Phase 1 : ChromaDB
        try:
            traces.extend(self._recall_chroma(query, limit, collection, now))
        except Exception as e:
            logger.debug(f"[TEMPORAL] Phase ChromaDB échouée : {e}")

        # Phase 2 : Épisodique (hippocampe)
        if include_episodic:
            try:
                traces.extend(self._recall_episodic(query_words, limit, now))
            except Exception as e:
                logger.debug(f"[TEMPORAL] Phase épisodique échouée : {e}")

        # Phase 3 : Associatif (réseau synaptique)
        if include_associative:
            try:
                traces.extend(self._recall_associative(query, query_words, limit, now))
            except Exception as e:
                logger.debug(f"[TEMPORAL] Phase associative échouée : {e}")

        # Enrichissement croisé
        try:
            self._enrich_traces(traces, now)
        except Exception as e:
            logger.debug(f"[TEMPORAL] Enrichissement échoué : {e}")

        # Filtre émotion
        if emotion_filter:
            ef = emotion_filter.lower()
            traces = [t for t in traces if ef in t.emotion.lower()]

        # Scoring final composite
        for t in traces:
            recency = _temporal_decay(now - t.timestamp) if t.timestamp > 0 else 0.3
            emotional_strength = min(t.emotion_intensity, 1.0)
            associative_strength = min(len(t.associated_concepts) * 0.15, 1.0)
            somatic = abs(t.somatic_signal)

            t.relevance = (
                0.40 * t.relevance +          # Score textuel/similarité
                0.20 * recency +              # Récence temporelle
                0.15 * emotional_strength +   # Force émotionnelle
                0.15 * associative_strength + # Force associative
                0.10 * somatic                # Signal somatique
            )

        # Dédoublonner par texte (garder le meilleur score)
        seen: dict[str, MemoryTrace] = {}
        for t in traces:
            key = t.text[:100].lower().strip()
            if key not in seen or t.relevance > seen[key].relevance:
                seen[key] = t
        traces = list(seen.values())

        # Filtrer, trier, limiter
        traces = [t for t in traces if t.relevance >= MIN_RELEVANCE]
        traces.sort(key=lambda t: t.relevance, reverse=True)
        result = traces[:limit]

        # Stats et cache
        self._total_recalls += 1
        self._recall_cache[ck] = (now, result)

        # Publier événement
        self._publish_recall_event(query, result)

        return result

    # =========================================================
    # RECALL SPÉCIALISÉS
    # =========================================================

    def recall_by_emotion(self, emotion: str, limit: int = 5,
                          time_window: float = 86400) -> list[MemoryTrace]:
        """Recherche de souvenirs par émotion dominante.

        Ex: 'Qu'est-ce qu'on faisait quand on était frustré ?'
        """
        if not emotion:
            return []

        traces: list[MemoryTrace] = []
        now = time.time()
        cutoff = now - time_window

        # Épisodes hippocampiques filtrés par émotion
        try:
            from core.hippocampus import hippocampus
            episodes = hippocampus.get_session_narrative(n=20)
            for ep in episodes:
                ts = ep.get("timestamp", 0)
                if ts < cutoff:
                    continue
                ep_emotion = ep.get("emotion", "")
                if emotion.lower() in ep_emotion.lower():
                    traces.append(MemoryTrace(
                        text=ep.get("summary", ""),
                        relevance=ep.get("salience", 0.5),
                        timestamp=ts,
                        source="episodic",
                        emotion=ep_emotion,
                        emotion_intensity=ep.get("salience", 0.5),
                    ))
        except Exception:
            pass

        # Co-occurrences indexées
        emotion_key = f"emotion:{emotion.lower()}"
        for intent_key, assocs in self._cooccurrences.items():
            if emotion_key in assocs:
                count = assocs[emotion_key]
                intent = intent_key.replace("intent:", "")
                traces.append(MemoryTrace(
                    text=f"Routine {intent} associée à {emotion} ({int(count)} fois)",
                    relevance=min(count / 10.0, 1.0),
                    timestamp=now,
                    source="associative",
                    emotion=emotion,
                ))

        self._enrich_traces(traces, now)
        traces.sort(key=lambda t: t.relevance, reverse=True)
        return traces[:limit]

    def recall_by_concept(self, concepts: list[str], limit: int = 5) -> list[MemoryTrace]:
        """Recherche associative par concepts du réseau synaptique."""
        if not concepts:
            return []

        traces: list[MemoryTrace] = []
        now = time.time()

        # Associations synaptiques
        try:
            from core.synaptic_network import cortex
            for concept in concepts[:5]:  # Limiter pour perf
                assocs = cortex.query_associations(concept, top_k=limit)
                if assocs:
                    for assoc_concept, weight in assocs:
                        traces.append(MemoryTrace(
                            text=f"{concept} → {assoc_concept}",
                            relevance=weight,
                            timestamp=now,
                            source="associative",
                            associated_concepts=[concept, assoc_concept],
                        ))
        except Exception:
            pass

        # ChromaDB : chercher les concepts comme query
        query = " ".join(concepts)
        traces.extend(self._recall_chroma(query, limit, "collective_wisdom", now))

        self._enrich_traces(traces, now)
        traces.sort(key=lambda t: t.relevance, reverse=True)
        return traces[:limit]

    def recall_by_period(self, start: float, end: float,
                         limit: int = 10) -> list[MemoryTrace]:
        """Recherche de souvenirs dans une fenêtre temporelle."""
        if start >= end:
            return []

        traces: list[MemoryTrace] = []
        now = time.time()

        # Épisodes hippocampiques dans la fenêtre
        try:
            from core.hippocampus import hippocampus
            for ep in hippocampus._episodes:
                ts = ep.timestamp
                if start <= ts <= end:
                    traces.append(MemoryTrace(
                        text=ep.summary,
                        relevance=ep.salience,
                        timestamp=ts,
                        source="episodic",
                        emotion=ep.cardiac_emotion,
                        emotion_intensity=ep.salience,
                        agent=ep.agent,
                    ))
        except Exception:
            pass

        self._enrich_traces(traces, now)
        traces.sort(key=lambda t: t.timestamp)  # Chronologique
        return traces[:limit]

    def get_module_history(self, module_name: str, limit: int = 5) -> list[MemoryTrace]:
        """Historique des souvenirs liés à un module/fichier."""
        return self.unified_recall(
            query=module_name,
            limit=limit,
            include_episodic=True,
            include_associative=True,
        )

    # =========================================================
    # NARRATION
    # =========================================================

    def get_narrative_summary(self, n_hours: float = 4.0) -> str:
        """Résumé narratif cohérent des dernières N heures."""
        parts = []
        now = time.time()
        window = n_hours * 3600

        # Épisodes récents
        try:
            from core.hippocampus import hippocampus
            episodes = hippocampus.get_session_narrative(n=10)
            recent = [e for e in episodes if now - e.get("timestamp", 0) < window]
            if recent:
                summaries = [e["summary"] for e in recent[:5]]
                parts.append("Épisodes récents: " + " | ".join(summaries))
        except Exception:
            pass

        # Émotion dominante
        try:
            from core.cardiac_engine import heart
            emotion = heart.current_emotion
            parts.append(f"Humeur: {emotion}")
        except Exception:
            pass

        # Pulsion dominante
        try:
            from core.desire_engine import desires
            narrative = desires.get_dominant_narrative(top_n=1)
            if narrative:
                parts.append(f"Pulsion: {narrative[0]}")
        except Exception:
            pass

        # Patterns co-occurrence
        top_patterns = self._get_top_patterns(3)
        if top_patterns:
            parts.append("Patterns: " + ", ".join(top_patterns))

        return " — ".join(parts) if parts else "Aucune activité récente."

    def get_temporal_context(self) -> str:
        """Contexte injectable dans purpose_context."""
        parts = []

        # Narration courte
        narrative = self.get_narrative_summary(n_hours=2.0)
        if narrative and narrative != "Aucune activité récente.":
            parts.append(narrative)

        # Stats mémoire
        stats = self.get_stats()
        if stats["total_recalls"] > 0:
            parts.append(f"Rappels: {stats['total_recalls']} (cache {stats['cache_hit_rate']}%)")

        return " | ".join(parts) if parts else ""

    def format_for_prompt(self, traces: list[MemoryTrace],
                          max_chars: int = 500) -> str:
        """Formate des MemoryTrace pour injection dans un prompt LLM."""
        if not traces:
            return ""

        lines = []
        total = 0
        for t in traces:
            # Construire la ligne
            parts = [t.text[:200]]
            if t.emotion:
                parts.append(f"[{t.emotion}]")
            if t.associated_concepts:
                concepts_str = ",".join(t.associated_concepts[:3])
                parts.append(f"({concepts_str})")
            if t.source != "chroma":
                parts.append(f"<{t.source}>")

            line = " ".join(parts)
            if total + len(line) > max_chars:
                break
            lines.append(f"- {line}")
            total += len(line) + 4  # "- " + "\n"

        return "\n".join(lines)

    # =========================================================
    # SCORING (Couche pour autonomy_engine)
    # =========================================================

    def compute_temporal_bonus(self, intent: str) -> float:
        """Bonus [-0.5, +2.0] basé sur la pertinence temporelle.

        - Intent associé à émotions positives → bonus
        - Intent associé à arcs 'struggle' → malus temporaire
        - Intent associé à concepts actifs dans le synaptique → bonus
        """
        if not intent:
            return 0.0

        bonus = 0.0
        intent_key = f"intent:{intent}"
        assocs = self._cooccurrences.get(intent_key, {})

        if not assocs:
            return 0.0

        # Émotions positives vs négatives
        positive_emotions = {"joie", "sérénité", "enthousiasme", "satisfaction",
                             "curiosité", "fierté", "détermination"}
        negative_emotions = {"frustration", "anxiété", "ennui", "colère",
                             "fatigue", "stress", "peur"}

        pos_score = 0.0
        neg_score = 0.0
        for key, count in assocs.items():
            if key.startswith("emotion:"):
                emotion = key.replace("emotion:", "")
                if emotion in positive_emotions:
                    pos_score += count
                elif emotion in negative_emotions:
                    neg_score += count

        # Différentiel émotionnel → bonus
        if pos_score + neg_score > 0:
            ratio = (pos_score - neg_score) / (pos_score + neg_score)
            bonus += ratio * 0.8  # [-0.8, +0.8]

        # Arc struggle récent → malus
        arc_struggle = assocs.get("arc:struggle", 0)
        arc_breakthrough = assocs.get("arc:breakthrough", 0)
        if arc_struggle > arc_breakthrough:
            bonus -= 0.3

        # Concepts actifs dans le synaptique → bonus
        concept_count = sum(1 for k in assocs if k.startswith("concept:"))
        if concept_count > 0:
            bonus += min(concept_count * 0.1, 0.5)

        # Clamper
        return max(-0.5, min(2.0, round(bonus, 3)))

    # =========================================================
    # INDEX CO-OCCURRENCES
    # =========================================================

    async def _on_routine_complete(self, payload: dict):
        """Handler bus : indexe les co-occurrences d'une routine."""
        intent = payload.get("intent", "")
        if not intent:
            return

        success = payload.get("success", False)
        quality = payload.get("quality", 0.0)

        intent_key = f"intent:{intent}"
        if intent_key not in self._cooccurrences:
            self._cooccurrences[intent_key] = {}

        assocs = self._cooccurrences[intent_key]

        # Émotion courante
        try:
            from core.cardiac_engine import heart
            emotion = heart.current_emotion
            if emotion:
                emo_key = f"emotion:{emotion.lower()}"
                assocs[emo_key] = assocs.get(emo_key, 0) + 1.0
        except Exception:
            pass

        # Concepts associés (via spreading_activation)
        try:
            from core.spreading_activation import extract_concepts
            agent = payload.get("agent", "")
            result_text = payload.get("result", "")
            if result_text and success:
                concepts = extract_concepts(str(result_text), max_concepts=5)
                for concept, weight in concepts:
                    c_key = f"concept:{concept.lower()}"
                    assocs[c_key] = assocs.get(c_key, 0) + weight
        except Exception:
            pass

        # Arc narratif
        try:
            from core.hippocampus import hippocampus
            arc = hippocampus.get_arc_narrative(intent)
            if arc:
                arc_key = f"arc:{arc.get('arc_type', 'unknown')}"
                assocs[arc_key] = assocs.get(arc_key, 0) + 1.0
        except Exception:
            pass

        # Limiter la taille
        if len(assocs) > MAX_COOCCURRENCES:
            # Garder les plus fréquents
            sorted_items = sorted(assocs.items(), key=lambda x: x[1], reverse=True)
            self._cooccurrences[intent_key] = dict(sorted_items[:MAX_COOCCURRENCES])

        # Sauvegarder périodiquement (tous les 10 recalls)
        if self._total_recalls % 10 == 0:
            self._save()

    def _decay_cooccurrences(self):
        """Applique la décroissance temporelle aux co-occurrences."""
        decay = 0.95  # Facteur par cycle
        to_delete = []
        for intent_key, assocs in self._cooccurrences.items():
            keys_to_delete = []
            for key, count in assocs.items():
                new_count = count * decay
                if new_count < 0.1:
                    keys_to_delete.append(key)
                else:
                    assocs[key] = new_count
            for k in keys_to_delete:
                del assocs[k]
            if not assocs:
                to_delete.append(intent_key)
        for k in to_delete:
            del self._cooccurrences[k]

    # =========================================================
    # PHASES DE RECALL INTERNES
    # =========================================================

    def _recall_chroma(self, query: str, limit: int,
                       collection: str, now: float) -> list[MemoryTrace]:
        """Phase 1 : recall depuis ChromaDB."""
        traces = []
        try:
            from core.vector_store import ChromaMemoryManager
            mm = ChromaMemoryManager.get_instance()
            results = mm.query_with_metadata(
                query_texts=[query],
                n_results=limit * 3,
                collection_name=collection,
            )
            if not results:
                return traces

            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]

            for i, doc in enumerate(docs):
                meta = metas[i] if i < len(metas) else {}
                dist = dists[i] if i < len(dists) else 1.0
                # ChromaDB distance → similarité [0, 1]
                similarity = max(0.0, 1.0 - dist / 2.0)

                ts = meta.get("timestamp", 0)
                if isinstance(ts, str):
                    try:
                        ts = float(ts)
                    except ValueError:
                        ts = 0

                traces.append(MemoryTrace(
                    text=doc,
                    relevance=similarity,
                    timestamp=float(ts),
                    source="chroma",
                    agent=meta.get("agent", ""),
                ))
        except Exception as e:
            logger.debug(f"[TEMPORAL] ChromaDB indisponible : {e}")

        return traces

    def _recall_episodic(self, query_words: set, limit: int,
                         now: float) -> list[MemoryTrace]:
        """Phase 2 : épisodes hippocampiques."""
        traces = []
        try:
            from core.hippocampus import hippocampus
            episodes = hippocampus.get_session_narrative(n=limit * 3)
            for ep in episodes:
                summary = ep.get("summary", "")
                overlap = _text_overlap(query_words, summary)
                if overlap < 0.05:
                    continue

                traces.append(MemoryTrace(
                    text=summary,
                    relevance=overlap * ep.get("salience", 0.5),
                    timestamp=ep.get("timestamp", 0),
                    source="episodic",
                    emotion=ep.get("emotion", ""),
                    emotion_intensity=ep.get("salience", 0.5),
                ))
        except Exception as e:
            logger.debug(f"[TEMPORAL] Hippocampe indisponible : {e}")

        return traces

    def _recall_associative(self, query: str, query_words: set,
                            limit: int, now: float) -> list[MemoryTrace]:
        """Phase 3 : associations synaptiques."""
        traces = []
        try:
            from core.spreading_activation import extract_concepts
            concepts = extract_concepts(query, max_concepts=5)
            if not concepts:
                return traces

            from core.synaptic_network import cortex
            for concept, weight in concepts:
                assocs = cortex.query_associations(concept, top_k=3)
                if assocs:
                    for assoc_concept, assoc_weight in assocs:
                        traces.append(MemoryTrace(
                            text=f"{concept} → {assoc_concept}",
                            relevance=weight * assoc_weight,
                            timestamp=now,
                            source="associative",
                            associated_concepts=[concept, assoc_concept],
                        ))
        except Exception as e:
            logger.debug(f"[TEMPORAL] Réseau synaptique indisponible : {e}")

        return traces

    # =========================================================
    # ENRICHISSEMENT CROISÉ
    # =========================================================

    def _enrich_traces(self, traces: list[MemoryTrace], now: float):
        """Enrichit les traces avec les données des autres organes."""
        if not traces:
            return

        # Émotion courante (pour les traces sans émotion)
        current_emotion = ""
        current_intensity = 0.0
        try:
            from core.cardiac_engine import heart
            current_emotion = heart.current_emotion
            current_intensity = getattr(heart, "emotion_intensity", 0.5)
        except Exception:
            pass

        # Pulsion dominante
        drive_narrative = ""
        try:
            from core.desire_engine import desires
            narratives = desires.get_dominant_narrative(top_n=1)
            if narratives:
                drive_narrative = narratives[0]
        except Exception:
            pass

        # Signal somatique
        somatic = 0.0
        try:
            from core.cardiac_engine import heart
            somatic = heart.get_somatic_signal("")
        except Exception:
            pass

        for t in traces:
            enriched = False

            # Émotion
            if not t.emotion and current_emotion:
                t.emotion = current_emotion
                t.emotion_intensity = current_intensity
                enriched = True

            # Contexte émotionnel historique (si timestamp)
            if t.timestamp > 0 and not t.emotion:
                try:
                    from core.hippocampus import hippocampus
                    ctx = hippocampus.get_emotional_context_at(t.timestamp, window=600)
                    if ctx and ctx.get("dominant_emotion"):
                        t.emotion = ctx["dominant_emotion"]
                        t.emotion_intensity = ctx.get("avg_salience", 0.5)
                        enriched = True
                except Exception:
                    pass

            # Drive
            if not t.drive_context and drive_narrative:
                t.drive_context = drive_narrative
                enriched = True

            # Somatique
            if t.somatic_signal == 0.0 and somatic != 0.0:
                t.somatic_signal = somatic
                enriched = True

            # Concepts associatifs (si pas encore)
            if not t.associated_concepts and t.text:
                try:
                    from core.spreading_activation import extract_concepts
                    concepts = extract_concepts(t.text, max_concepts=3)
                    t.associated_concepts = [c[0] for c in concepts]
                    enriched = True
                except Exception:
                    pass

            if enriched:
                self._total_enrichments += 1

    # =========================================================
    # HELPERS
    # =========================================================

    def _get_top_patterns(self, n: int = 3) -> list[str]:
        """Retourne les N patterns co-occurrence les plus fréquents."""
        patterns = []
        for intent_key, assocs in self._cooccurrences.items():
            intent = intent_key.replace("intent:", "")
            # Trouver l'émotion la plus fréquente
            top_emotion = ""
            top_count = 0
            for key, count in assocs.items():
                if key.startswith("emotion:") and count > top_count:
                    top_emotion = key.replace("emotion:", "")
                    top_count = count
            if top_emotion:
                patterns.append((f"{intent}↔{top_emotion}", top_count))

        patterns.sort(key=lambda x: x[1], reverse=True)
        return [p[0] for p in patterns[:n]]

    def _publish_recall_event(self, query: str, traces: list[MemoryTrace]):
        """Publie un événement TEMPORAL_RECALL sur le bus."""
        if not traces:
            return
        try:
            from core.event_bus.bus import bus
            import asyncio
            top_emotion = traces[0].emotion if traces else ""
            top_source = traces[0].source if traces else ""
            payload = {
                "query": query[:100],
                "trace_count": len(traces),
                "top_emotion": top_emotion,
                "top_source": top_source,
            }
            # Fire-and-forget si pas dans une boucle async
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bus.publish("TEMPORAL_RECALL", payload))
            except RuntimeError:
                pass  # Pas de boucle async active
        except Exception:
            pass

    # =========================================================
    # PERSISTANCE
    # =========================================================

    def _save(self):
        """Sauvegarde l'état sur disque."""
        state = {
            "cooccurrences": self._cooccurrences,
            "total_recalls": self._total_recalls,
            "total_enrichments": self._total_enrichments,
        }
        try:
            tmp = TEMPORAL_LOBE_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, TEMPORAL_LOBE_STATE_FILE)
        except Exception as e:
            logger.warning(f"[TEMPORAL] Erreur sauvegarde : {e}")

    def _load(self):
        """Charge l'état depuis le disque."""
        try:
            if os.path.exists(TEMPORAL_LOBE_STATE_FILE):
                with open(TEMPORAL_LOBE_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._cooccurrences = state.get("cooccurrences", {})
                self._total_recalls = state.get("total_recalls", 0)
                self._total_enrichments = state.get("total_enrichments", 0)
                logger.info(f"[TEMPORAL] État chargé — {len(self._cooccurrences)} intent(s) indexés")
        except Exception as e:
            logger.warning(f"[TEMPORAL] Erreur chargement : {e}")

    # =========================================================
    # STATS & API ENDPOINT
    # =========================================================

    def get_stats(self) -> dict[str, Any]:
        """Statistiques du lobe temporal."""
        total_assocs = sum(len(v) for v in self._cooccurrences.values())
        cache_total = self._total_recalls + self._total_cache_hits
        cache_rate = round(self._total_cache_hits / max(cache_total, 1) * 100, 1)

        return {
            "indexed_intents": len(self._cooccurrences),
            "total_associations": total_assocs,
            "total_recalls": self._total_recalls,
            "total_enrichments": self._total_enrichments,
            "total_cache_hits": self._total_cache_hits,
            "cache_hit_rate": cache_rate,
            "cache_entries": len(self._recall_cache),
        }


# --- Singleton ---
temporal = TemporalLobe()
