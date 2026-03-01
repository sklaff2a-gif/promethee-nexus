#!/usr/bin/env python3
"""Nettoyage chirurgical de la mémoire Prométhée.

Usage: python tools/memory_cleanup.py [--dry-run] [chemin_memory/]

- Par défaut opère sur memory/ du répertoire courant
- --dry-run : affiche les actions sans modifier les fichiers
- Crée des backups .bak avant toute modification
- Idempotent : peut être relancé sans danger
"""

import json
import shutil
import sys
import time
from pathlib import Path

# --- Constantes ---------------------------------------------------------------

DESIRE_DEFAULT_DEPRIVATION = 40.0
PREFRONTAL_COMPLETED_KEEP = 20
NARRATIVE_LOG_MAX_AGE_DAYS = 7
REPTILIAN_OCCURRENCE_CAP = 10
SYNAPTIC_PRUNE_WEIGHT = 0.10
SYNAPTIC_TEMPORAL_MIN_WEIGHT = 0.30
HIPPOCAMPUS_EPISODE_MAX_AGE_DAYS = 7
HIPPOCAMPUS_ARC_MAX_AGE_DAYS = 30
DOPAMINE_HISTORY_MAX = 20
INNER_VOICE_KEEP_STATUSES = {"confirmed", "pending"}

NOW = time.time()
SECS_PER_DAY = 86400


# --- Helpers ------------------------------------------------------------------

def load_json(path: Path) -> dict | list | None:
    """Charge un fichier JSON, retourne None si absent ou invalide."""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data, dry_run: bool):
    """Sauvegarde JSON avec backup .bak préalable."""
    if dry_run:
        return
    # Backup
    bak = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def report_section(name: str, stats: dict[str, int]):
    """Affiche un rapport de section."""
    print(f"\n{'-' * 60}")
    print(f"  {name}")
    print(f"{'-' * 60}")
    for key, val in stats.items():
        print(f"  {key:.<40} {val}")


# --- 1. Desire ----------------------------------------------------------------

def cleanup_desire(data: dict, dry_run: bool) -> dict[str, int]:
    """Ramène les drives à des niveaux sains."""
    stats = {"deprivation_reset": 0, "frustration_reset": 0, "tolerance_reset": 0}
    drives = data.get("drives", {})
    for name, drive in drives.items():
        # Deprivation → 40.0
        if drive.get("deprivation", 0) != DESIRE_DEFAULT_DEPRIVATION:
            drive["deprivation"] = DESIRE_DEFAULT_DEPRIVATION
            stats["deprivation_reset"] += 1
        # Frustration streak → 0
        if drive.get("frustration_streak", 0) != 0:
            drive["frustration_streak"] = 0
            stats["frustration_reset"] += 1
        # Tolerance accumulator → 0.0
        if drive.get("tolerance_accumulator", 0) != 0.0:
            drive["tolerance_accumulator"] = 0.0
            stats["tolerance_reset"] += 1
    return stats


# --- 2. Prefrontal ------------------------------------------------------------

def cleanup_prefrontal(data: dict, dry_run: bool) -> dict[str, int]:
    """Purge goals abandonnés, garde les 20 derniers complétés."""
    stats = {"abandoned_purged": 0, "completed_trimmed": 0, "narrative_purged": 0}
    goals = data.get("goals", [])

    # Séparer par statut
    active = [g for g in goals if g.get("status") in ("active", "pending", "in_progress")]
    completed = [g for g in goals if g.get("status") == "completed"]
    abandoned = [g for g in goals if g.get("status") == "abandoned"]

    stats["abandoned_purged"] = len(abandoned)

    # Garder les 20 derniers complétés (triés par created_at)
    completed.sort(key=lambda g: g.get("created_at", 0))
    if len(completed) > PREFRONTAL_COMPLETED_KEEP:
        stats["completed_trimmed"] = len(completed) - PREFRONTAL_COMPLETED_KEEP
        completed = completed[-PREFRONTAL_COMPLETED_KEEP:]

    data["goals"] = active + completed

    # Purger narrative_log > 7 jours
    cutoff = NOW - (NARRATIVE_LOG_MAX_AGE_DAYS * SECS_PER_DAY)
    narrative = data.get("narrative_log", [])
    before = len(narrative)
    data["narrative_log"] = [
        entry for entry in narrative
        if entry.get("timestamp", 0) > cutoff
    ]
    stats["narrative_purged"] = before - len(data["narrative_log"])

    return stats


# --- 3. Reptilian -------------------------------------------------------------

def cleanup_reptilian(data: dict, dry_run: bool) -> dict[str, int]:
    """Reset menace, adrenaline, cap occurrences, clear compteurs."""
    stats = {
        "threat_level_reset": 0, "adrenaline_reset": 0,
        "occurrences_capped": 0, "reflexes_reset": 0,
        "timers_cleared": 0,
    }

    # Threat level → 0.0
    if data.get("threat_level", 0) != 0.0:
        data["threat_level"] = 0.0
        stats["threat_level_reset"] = 1

    # Adrenaline → 0.0
    if data.get("adrenaline", 0) != 0.0:
        data["adrenaline"] = 0.0
        stats["adrenaline_reset"] = 1

    # Cap occurrences dans threat_memories
    for pattern, mem in data.get("threat_memories", {}).items():
        occ = mem.get("occurrences", 0)
        if occ > REPTILIAN_OCCURRENCE_CAP:
            stats["occurrences_capped"] += occ - REPTILIAN_OCCURRENCE_CAP
            mem["occurrences"] = REPTILIAN_OCCURRENCE_CAP

    # Reset reflexes_triggered
    reflexes = data.get("reflexes_triggered", {})
    for reflex, count in reflexes.items():
        if count != 0:
            stats["reflexes_reset"] += count
            reflexes[reflex] = 0

    # Clear freeze/shed timers
    for field in ("freeze_until", "shed_until"):
        if data.get(field, 0) != 0.0:
            data[field] = 0.0
            stats["timers_cleared"] += 1

    return stats


# --- 4. Cardiac ---------------------------------------------------------------

def cleanup_cardiac(data: dict, dry_run: bool) -> dict[str, int]:
    """Recalcule l'intensité des somatic markers proportionnellement."""
    stats = {"markers_recalculated": 0}
    markers = data.get("somatic_markers", {})
    for intent, marker in markers.items():
        fc = marker.get("formation_count", 1)
        old_intensity = marker.get("intensity", 0)
        # Formule : min(0.9, fc / (fc + 5))
        new_intensity = round(min(0.9, fc / (fc + 5)), 4)
        if old_intensity != new_intensity:
            marker["intensity"] = new_intensity
            stats["markers_recalculated"] += 1
    return stats


# --- 5. Synaptic --------------------------------------------------------------

def cleanup_synaptic(data: dict, dry_run: bool) -> dict[str, int]:
    """Prune synapses faibles et nœuds orphelins."""
    stats = {
        "synapses_pruned_weak": 0, "synapses_pruned_temporal": 0,
        "nodes_orphaned_removed": 0,
    }
    synapses = data.get("synapses", {})
    nodes = data.get("nodes", {})

    # Collecter les IDs de synapses à supprimer
    to_remove = []
    for syn_id, syn in synapses.items():
        weight = syn.get("weight", 0)
        syn_type = syn.get("synapse_type", "")
        fc = syn.get("formation_count", 0)

        # Prune faibles (weight < 0.10)
        if weight < SYNAPTIC_PRUNE_WEIGHT:
            to_remove.append(syn_id)
            stats["synapses_pruned_weak"] += 1
        # Prune temporelles éphémères (formation_count == 1 et weight < 0.3)
        elif syn_type == "temporal" and fc == 1 and weight < SYNAPTIC_TEMPORAL_MIN_WEIGHT:
            to_remove.append(syn_id)
            stats["synapses_pruned_temporal"] += 1

    for syn_id in to_remove:
        del synapses[syn_id]

    # Trouver les nœuds référencés par les synapses restantes
    referenced_nodes = set()
    for syn in synapses.values():
        referenced_nodes.add(syn.get("source", ""))
        referenced_nodes.add(syn.get("target", ""))

    # Supprimer les nœuds orphelins (sans synapse ET activation faible)
    # On garde les nœuds même orphelins s'ils n'étaient liés à aucune synapse initialement
    # (cas où synapses est vide = réseau neuf)
    if to_remove:  # On a purgé des synapses → chercher les orphelins
        orphans = []
        for node_id in list(nodes.keys()):
            if node_id not in referenced_nodes:
                # Vérifier que le nœud n'a pas une activation élevée
                node = nodes[node_id]
                if node.get("activation_count", 0) <= 1 and node.get("energy", 0) < 0.2:
                    orphans.append(node_id)
        for node_id in orphans:
            del nodes[node_id]
            stats["nodes_orphaned_removed"] += 1

    return stats


# --- 6. Hippocampus -----------------------------------------------------------

def cleanup_hippocampus(data: dict, dry_run: bool) -> dict[str, int]:
    """Purge vieux épisodes et arcs non-marqués."""
    stats = {"episodes_purged": 0, "arcs_purged": 0}

    # Épisodes > 7 jours
    cutoff_ep = NOW - (HIPPOCAMPUS_EPISODE_MAX_AGE_DAYS * SECS_PER_DAY)
    episodes = data.get("episodes", [])
    before_ep = len(episodes)
    data["episodes"] = [
        ep for ep in episodes
        if ep.get("timestamp", 0) > cutoff_ep
    ]
    stats["episodes_purged"] = before_ep - len(data["episodes"])

    # Arcs non-marqués > 30 jours
    cutoff_arc = NOW - (HIPPOCAMPUS_ARC_MAX_AGE_DAYS * SECS_PER_DAY)
    arcs = data.get("arcs", [])
    before_arc = len(arcs)
    data["arcs"] = [
        arc for arc in arcs
        if arc.get("marked", False) or arc.get("ended_at", arc.get("started_at", 0)) > cutoff_arc
    ]
    stats["arcs_purged"] = before_arc - len(data["arcs"])

    return stats


# --- 7. Dopamine --------------------------------------------------------------

def cleanup_dopamine(data: dict, dry_run: bool) -> dict[str, int]:
    """Trim reward_history, reset dopamine_level à baseline."""
    stats = {"histories_trimmed": 0, "level_reset": 0}

    # Reset dopamine_level → 0.5
    if data.get("dopamine_level", 0.5) != 0.5:
        data["dopamine_level"] = 0.5
        stats["level_reset"] = 1

    # Trim reward_history dans memories
    memories = data.get("memories", {})
    for intent, mem in memories.items():
        history = mem.get("reward_history", [])
        if len(history) > DOPAMINE_HISTORY_MAX:
            trimmed = len(history) - DOPAMINE_HISTORY_MAX
            mem["reward_history"] = history[-DOPAMINE_HISTORY_MAX:]
            stats["histories_trimmed"] += trimmed

        # Aussi trim success_by_bucket et success_by_mood
        for bucket_dict_key in ("success_by_bucket", "success_by_mood"):
            bucket_dict = mem.get(bucket_dict_key, {})
            for bucket, vals in bucket_dict.items():
                if len(vals) > DOPAMINE_HISTORY_MAX:
                    bucket_dict[bucket] = vals[-DOPAMINE_HISTORY_MAX:]

    return stats


# --- 8. Corpus Callosum -------------------------------------------------------

def cleanup_corpus_callosum(data: dict, dry_run: bool) -> dict[str, int]:
    """Reset resonance_log et cycle_count."""
    stats = {"resonance_log_cleared": 0, "cycle_count_reset": 0}

    log = data.get("resonance_log", [])
    if log:
        stats["resonance_log_cleared"] = len(log)
        data["resonance_log"] = []

    if data.get("stats", {}).get("cycles_completed", 0) != 0:
        stats["cycle_count_reset"] = data["stats"]["cycles_completed"]
        data["stats"]["cycles_completed"] = 0

    return stats


# --- 9. Inner Voice -----------------------------------------------------------

def cleanup_inner_voice(data: dict, dry_run: bool) -> dict[str, int]:
    """Purge prédictions violées/expirées, garde confirmées et pending."""
    stats = {"predictions_purged": 0}

    predictions = data.get("predictions", [])
    before = len(predictions)
    data["predictions"] = [
        p for p in predictions
        if p.get("outcome", "pending") in INNER_VOICE_KEEP_STATUSES
           or not p.get("resolved", False)
    ]
    stats["predictions_purged"] = before - len(data["predictions"])

    return stats


# --- Main ---------------------------------------------------------------------

FILE_HANDLERS = {
    "desire_state.json": cleanup_desire,
    "prefrontal_state.json": cleanup_prefrontal,
    "reptilian_state.json": cleanup_reptilian,
    "cardiac_state.json": cleanup_cardiac,
    "synaptic_network.json": cleanup_synaptic,
    "hippocampus_state.json": cleanup_hippocampus,
    "dopamine_state.json": cleanup_dopamine,
    "corpus_callosum_state.json": cleanup_corpus_callosum,
    "inner_voice_state.json": cleanup_inner_voice,
}


def main():
    # Parse args
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    memory_dir = Path(args[0]) if args else Path("memory")

    if not memory_dir.exists():
        print(f"ERREUR: repertoire '{memory_dir}' introuvable.")
        sys.exit(1)

    mode = "DRY-RUN" if dry_run else "ECRITURE"
    print(f"\n{'=' * 60}")
    print(f"  Nettoyage memoire Promethee -- Mode {mode}")
    print(f"  Repertoire : {memory_dir.resolve()}")
    print(f"{'=' * 60}")

    all_stats = {}
    total_changes = 0

    for filename, handler in FILE_HANDLERS.items():
        filepath = memory_dir / filename
        data = load_json(filepath)
        if data is None:
            print(f"\n  [SKIP] {filename} -- absent, ignore")
            continue

        stats = handler(data, dry_run)
        all_stats[filename] = stats
        changes = sum(stats.values())
        total_changes += changes

        if changes > 0:
            save_json(filepath, data, dry_run)
            report_section(filename, stats)
        else:
            print(f"\n  [OK] {filename} -- deja propre")

    # Rapport final
    print(f"\n{'=' * 60}")
    print(f"  RESUME FINAL")
    print(f"{'=' * 60}")
    print(f"  Fichiers traites : {len(all_stats)}")
    print(f"  Total modifications : {total_changes}")
    if dry_run:
        print(f"  -> Aucune ecriture (mode dry-run)")
    else:
        print(f"  -> Fichiers .bak crees pour rollback")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
