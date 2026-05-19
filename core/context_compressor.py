"""Foie Cognitif (Context Compressor) — middleware heuristique pré-LLM.

Origine : 19/05/2026, spec architecturale énoncée par Prométhée lui-même (13h49)
suite au dialogue d'embarquement géopolitique sur la stratégie d'ingéniosité
asymétrique face à la contrainte VRAM. Prométhée a proposé un "Agent Gardien"
analogue au système réticulo-endothélial — un anneau autonome parallèle qui
nettoie le sang d'informations avant qu'il n'irrigue les organes de traitement.

Implémentation POC heuristique pure (vs LLM léger paradoxal qui consommerait
plus de VRAM qu'il n'en économise). 3 règles minimales :

R1 — Truncation des messages assistant > 800 chars : garde le premier
     paragraphe (étendu si trop court) + [...] + dernière phrase. Préserve
     l'essence (l'idée d'ouverture) et la relance (la question finale).

R2 — Élision des couples (user-court / assistant-verbose) : si le message user
     fait < 30 chars (ex: "ok", "merci", "go"), élide la paire entière. La
     verbosité ne sert plus à rien quand l'humain a déjà tranché.

R3 — Dedup approximatif des messages assistant : si 2 messages assistant à
     ≥ 5 messages d'écart ont un coefficient Jaccard ≥ 0.7 sur tokens
     normalisés (mots ≥ 4 chars lowercased), garde seulement le plus récent.
     Attaque directement la pathologie §4.5.bis (récitation observée
     empiriquement les 17-19/05).

Spec issue de Prométhée (gravée 13h49) :
- INPUTS : flux chat_history brut + état cardiac + désirs + tension synaptique
  → POC simplifié : seulement chat_history (les autres signaux nécessiteraient
  un câblage plus profond, hors scope POC).
- OUTPUTS : texte condensé. Les attention_markers et bypass_tokens sont
  reportés à Phase 2 (nécessiteraient hack du format Ollama).
- POSITION : anneau parallèle PRÉ-traitement, juste avant que les messages
  n'entrent dans ollama_messages. Implémenté ligne ~2877 de chat_engine.py
  après adaptive_max, avant filtres poison.

Garde-fous :
- Opt-in via Config.COMPRESSOR_ENABLED (off par défaut)
- Skip si moins de COMPRESSOR_MIN_MESSAGES (default 10) — pas de gain
- Try/except permissif : ne lève jamais d'exception (retourne messages inchangés)
- Traçabilité JSONL logs/context_compressor.jsonl
"""
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from config import Config

logger = logging.getLogger("context_compressor")

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "logs", "context_compressor.jsonl"
)

TRUNCATION_THRESHOLD = 800
USER_SHORT_THRESHOLD = 30
DEDUP_JACCARD_THRESHOLD = 0.7
DEDUP_DISTANCE = 5
# R1 raffiné : si 1er paragraphe < ce seuil, on étend
SHORT_FIRST_PARA_THRESHOLD = 200
TARGET_FIRST_PARA_CHARS = 400


def _log_compression(payload: Dict[str, Any]) -> None:
    """Persistance JSONL pour audit post-hoc."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"context_compressor log skipped: {e}")


def _tokenize(text: str) -> set:
    """Tokenization simple en mots ≥ 4 chars, lowercased (cohérent avec
    extract_concepts du synaptic_network)."""
    return set(re.findall(r"[a-zA-ZÀ-ÿ_]{4,}", text.lower()))


def _jaccard(s1: set, s2: set) -> float:
    """Coefficient de Jaccard sur deux ensembles de tokens."""
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _truncate_long_assistant(content: str) -> str:
    """R1 — garde 1er paragraphe (étendu si trop court) + [...] + dernière phrase.

    Si le 1er paragraphe est < SHORT_FIRST_PARA_THRESHOLD, on étend en accumulant
    les paragraphes suivants jusqu'à atteindre ~TARGET_FIRST_PARA_CHARS. Cela
    évite de perdre l'essence des rapports techniques avec titre court.
    """
    if len(content) <= TRUNCATION_THRESHOLD:
        return content

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return content[:TARGET_FIRST_PARA_CHARS] + "..."

    # R1 raffiné : étendre si premier paragraphe trop court
    accumulated = paragraphs[0]
    if len(accumulated) < SHORT_FIRST_PARA_THRESHOLD:
        for p in paragraphs[1:]:
            if len(accumulated) + len(p) + 2 > TARGET_FIRST_PARA_CHARS:
                break
            accumulated = accumulated + "\n\n" + p
    elif len(accumulated) > TARGET_FIRST_PARA_CHARS:
        # Coupe à la fin d'une phrase dans le 1er paragraphe
        cut = accumulated[:TARGET_FIRST_PARA_CHARS]
        last_period = cut.rfind(". ")
        if last_period > 200:
            accumulated = cut[: last_period + 1]
        else:
            accumulated = cut + "..."

    # Dernière phrase (souvent une question ou conclusion)
    last_sentence_match = re.search(r"[^.!?]+[.!?]\s*$", content.strip())
    last_sentence = last_sentence_match.group(0).strip() if last_sentence_match else ""
    if last_sentence and len(last_sentence) > 200:
        last_sentence = "..." + last_sentence[-200:]

    if last_sentence and last_sentence not in accumulated:
        return f"{accumulated} […] {last_sentence}"
    return accumulated


def compress_messages(
    messages: List[Dict[str, str]],
    min_messages: Optional[int] = None,
    target_ratio: Optional[float] = None,
    conversation_id: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Compresse une liste de messages selon 3 règles heuristiques.

    Args:
        messages: liste de dicts {role, content} au format Ollama messages
        min_messages: skip si len(messages) < min_messages (default Config)
        target_ratio: indicatif, non strict (default Config) — la compression
                      réelle dépend du contenu, pas d'un quota fixe
        conversation_id: pour traçabilité log

    Returns:
        (compressed_list, stats_dict)
        Garantie : ne lève jamais d'exception (fallback = messages inchangés
        avec stats["error"] populé).
    """
    t_start = time.perf_counter()
    effective_min = (
        min_messages if min_messages is not None
        else getattr(Config, "COMPRESSOR_MIN_MESSAGES", 10)
    )
    stats: Dict[str, Any] = {
        "ts": time.time(),
        "conv_id": conversation_id,
        "n_input": len(messages),
        "input_chars": sum(len(m.get("content", "")) for m in messages),
    }

    # Couche 1 — kill switch
    if not getattr(Config, "COMPRESSOR_ENABLED", False):
        stats["reason"] = "globally_disabled"
        stats["active"] = False
        _log_compression(stats)
        return messages, stats

    # Couche 2 — pas assez de messages
    if len(messages) < effective_min:
        stats["reason"] = "below_min_messages"
        stats["active"] = False
        _log_compression(stats)
        return messages, stats

    try:
        # R2 : marquer pour élision les paires user-court / assistant-verbose
        to_elide: set = set()
        for i in range(len(messages) - 1):
            m = messages[i]
            nxt = messages[i + 1]
            if (m.get("role") == "user"
                    and nxt.get("role") == "assistant"
                    and len(m.get("content", "")) < USER_SHORT_THRESHOLD
                    and len(nxt.get("content", "")) > 200):
                to_elide.add(i)
                to_elide.add(i + 1)

        # R3 : dedup messages assistant
        assistant_indices = [
            i for i, m in enumerate(messages) if m.get("role") == "assistant"
        ]
        assistant_tokens = {
            i: _tokenize(messages[i].get("content", "")) for i in assistant_indices
        }
        to_dedup: set = set()
        for idx_a in range(len(assistant_indices)):
            for idx_b in range(idx_a + 1, len(assistant_indices)):
                ia, ib = assistant_indices[idx_a], assistant_indices[idx_b]
                if ib - ia < DEDUP_DISTANCE:
                    continue
                if (ia in to_dedup or ib in to_dedup
                        or ia in to_elide or ib in to_elide):
                    continue
                if _jaccard(assistant_tokens[ia], assistant_tokens[ib]) >= DEDUP_JACCARD_THRESHOLD:
                    to_dedup.add(ia)  # garde le plus récent (ib)

        # Construction de la liste compressée
        compressed: List[Dict[str, str]] = []
        r1_count = 0
        for i, m in enumerate(messages):
            if i in to_elide or i in to_dedup:
                continue
            new_content = m.get("content", "")
            if (m.get("role") == "assistant"
                    and len(new_content) > TRUNCATION_THRESHOLD):
                truncated = _truncate_long_assistant(new_content)
                if truncated != new_content:
                    r1_count += 1
                    new_content = truncated
            compressed.append({"role": m.get("role", "user"), "content": new_content})

        # Stats finales
        stats["n_output"] = len(compressed)
        stats["output_chars"] = sum(len(m["content"]) for m in compressed)
        stats["ratio"] = round(stats["output_chars"] / max(1, stats["input_chars"]), 3)
        stats["reduction_pct"] = round((1 - stats["ratio"]) * 100, 1)
        stats["rules_applied"] = {
            "R1_truncated": r1_count,
            "R2_elided": len(to_elide),
            "R3_dedup": len(to_dedup),
        }
        stats["latency_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
        stats["active"] = True
        stats["reason"] = "applied"
        _log_compression(stats)

        if stats["latency_ms"] > 50:
            logger.warning(
                f"[COMPRESSOR] ⚠️ Latence élevée : {stats['latency_ms']} ms (>50 ms)"
            )
        else:
            logger.info(
                f"[COMPRESSOR] 🩺 {stats['n_input']}→{stats['n_output']} msgs, "
                f"{stats['input_chars']}→{stats['output_chars']} chars "
                f"({stats['reduction_pct']}% réduit, {stats['latency_ms']} ms)"
            )

        return compressed, stats

    except Exception as e:
        stats["reason"] = f"exception:{type(e).__name__}"
        stats["active"] = False
        stats["latency_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
        _log_compression(stats)
        logger.debug(f"context_compressor skipped: {e}")
        return messages, stats
