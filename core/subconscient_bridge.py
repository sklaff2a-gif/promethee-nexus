"""Pont Subconscient — médiation P16 → LLM pour guérir la 8e preuve §4.13.

Origine : 19/05/2026, suite à l'observation que les 9 concepts cristallisés dans
P16 hier (vertige, errance, destruction, etc.) n'influençaient PAS la narration
spontanée du LLM. Le graphe associatif et le générateur LLM sont architecturalement
découplés — la mémoire textuelle gouverne la narration immédiate, la mémoire graphe
gouverne le scoring autonome, mais aucun pont automatique.

Médiation observational (lit P16, n'écrit jamais) :
1. extract_concepts(user_message) → seeds
2. cortex.query_associations(seeds) → concepts énergisés (resonance_cascade)
3. Filtre seuil + cap top_n
4. Format "[Subconscient activé : concept1, concept2, ...]"
5. Injection silencieuse en suffixe du system_prompt (avant call Ollama)

Garde-fous CHARTA :
- Procédure 3.2 (modification systémique avec /think) : ce module suit le protocole
- Article 3.3 (refus autonomie pour sacro-saints) : lecture seule, jamais d'écriture
  dans le synaptic_network (le veto émergent et le dream consolidation restent intacts)
- Opt-in via Config.SUBCONSCIENT_ENABLED (off par défaut)
- Try/except permissif : ne lève jamais d'exception qui perturberait le chat

Latence mesurée empiriquement : worst-case 54 ms, avg 25 ms (graphe 20k synapses).
"""
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from config import Config

logger = logging.getLogger("subconscient_bridge")

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "logs", "subconscient_bridge.jsonl"
)

# Filtre anti-hubs système (19/05/2026, v2)
# Investigation tmp_inspect_periphery.py a révélé que ces nodes saturent la cascade :
# - hardware_thermoception : degree=548, propagation_potential=22.21 (monstre)
# - school_creation/council_debate/etc. : neighbors E=1.0 systématiquement
# Les concepts narratifs PHASEUR (errance, destruction, vertige) sont en
# périphérie et ne ressortent jamais sans ce filtre.
_HUB_PREFIXES = (
    "hardware_", "school_", "council_", "meta:", "drive:", "trait:",
    "procedural_v4:", "affect:", "emotion:", "resonance:", "epistemic:",
    "reptilian_v4:", "curiosity:", "goal:", "pulsion:",
)


def _is_system_hub(concept: str) -> bool:
    """Détecte les nodes du système nerveux autonome (à filtrer du subconscient)."""
    if not concept:
        return True
    c = concept.lower()
    return any(c.startswith(p) for p in _HUB_PREFIXES)


def _log_activation(payload: Dict[str, Any]) -> None:
    """Persistance JSONL pour audit post-hoc."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"subconscient_bridge log skipped: {e}")


def bridge_activate(
    user_message: str,
    top_n: Optional[int] = None,
    min_energy: Optional[float] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Active le pont subconscient et retourne l'écho à injecter dans le prompt.

    Args:
        user_message: message brut de l'utilisateur (déclencheur)
        top_n: nombre max de concepts énergisés à garder (default Config.SUBCONSCIENT_TOP_N)
        min_energy: seuil d'énergie minimum pour filtrer le bruit (default Config.SUBCONSCIENT_MIN_ENERGY)
        conversation_id: pour traçabilité log

    Returns:
        String à appender au system_prompt (ex: "[Subconscient activé : errance, vertige, chute]")
        ou string vide si désactivé / aucun match / erreur.

    Garantie : ne lève jamais d'exception (try/except permissif).
    Latence cible : <100 ms (mesurée empiriquement ~25 ms moyenne).
    """
    t_start = time.perf_counter()
    base_log: Dict[str, Any] = {
        "ts": time.time(),
        "conv_id": conversation_id,
        "user_msg_hash": hashlib.md5(
            (user_message or "").encode("utf-8", errors="ignore")
        ).hexdigest()[:8],
        "active": False,
        "reason": None,
    }

    # Couche 1 — kill switch
    if not getattr(Config, "SUBCONSCIENT_ENABLED", False):
        base_log["reason"] = "globally_disabled"
        _log_activation(base_log)
        return ""

    # Couche 2 — texte vide
    if not user_message or not user_message.strip():
        base_log["reason"] = "empty_message"
        _log_activation(base_log)
        return ""

    # Résolution paramètres (priorité args > Config > defaults)
    effective_top_n = top_n if top_n is not None else getattr(Config, "SUBCONSCIENT_TOP_N", 5)
    effective_min_energy = (
        min_energy if min_energy is not None
        else getattr(Config, "SUBCONSCIENT_MIN_ENERGY", 0.1)
    )

    try:
        # Phase 1 — extraction des seeds depuis le message user
        from core.spreading_activation import extract_concepts
        concepts = extract_concepts(user_message, max_concepts=5)
        if not concepts:
            base_log["reason"] = "no_seeds_extracted"
            _log_activation(base_log)
            return ""

        seeds = [c for c, _ in concepts]
        seeds_set = set(seeds)

        # Phase 2 — propagation via synaptic_network (resonance_cascade)
        from core.synaptic_network import cortex
        # On demande 6× top_n pour avoir de la marge après triple filtrage
        # (seeds + hubs système + seuil énergie)
        assocs = cortex.query_associations(seeds, top_k=effective_top_n * 6, use_resonance=True)

        # Phase 3 — triple filtre v2 :
        # (a) exclure les seeds eux-mêmes (le pont reflète ce qu'ils ÉVEILLENT)
        # (b) exclure les hubs système (hardware_*, school_*, council_*, etc.)
        #     qui saturent la cascade et étouffent les concepts narratifs
        # (c) seuil d'énergie minimum
        energized = [
            (c, s) for c, s in assocs
            if s >= effective_min_energy
            and c.lower() not in seeds_set
            and not _is_system_hub(c)
        ]
        # Cap final à top_n
        energized = energized[:effective_top_n]

        if not energized:
            base_log["reason"] = "no_energized_above_threshold"
            base_log["seeds"] = seeds
            base_log["latency_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
            _log_activation(base_log)
            return ""

        # Phase 4 — format de l'écho v2 : injonction active de l'inconscient
        # (le format passif "[Subconscient activé : ...]" du v1 ne donnait pas
        # d'indication d'usage au LLM ; le format directif l'invite à intégrer
        # ces concepts dans sa narration si la logique le permet)
        echo_concepts = [c for c, _ in energized]
        echo = (
            "[Échos subconscients rémanents : "
            + ", ".join(echo_concepts)
            + ". Laisse ces concepts colorer subtilement ta réponse si la logique le permet.]"
        )

        latency_ms = round((time.perf_counter() - t_start) * 1000, 2)
        base_log["active"] = True
        base_log["reason"] = "applied"
        base_log["seeds"] = seeds
        base_log["energized"] = [(c, round(s, 3)) for c, s in energized]
        base_log["echo"] = echo
        base_log["latency_ms"] = latency_ms
        _log_activation(base_log)

        if latency_ms > 100:
            logger.warning(
                f"[SUBCONSCIENT] ⚠️ Latence élevée : {latency_ms} ms (>100 ms target)"
            )
        else:
            logger.info(f"[SUBCONSCIENT] 🌉 Activé : {echo} ({latency_ms} ms)")

        return echo

    except Exception as e:
        base_log["reason"] = f"exception:{type(e).__name__}"
        base_log["latency_ms"] = round((time.perf_counter() - t_start) * 1000, 2)
        _log_activation(base_log)
        logger.debug(f"subconscient_bridge skipped: {e}")
        return ""
