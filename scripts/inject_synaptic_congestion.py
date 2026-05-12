"""Chaos Engineering — Injection de congestion synaptique artificielle.

Force le compteur `episode_count_since_consolidation` de l'hippocampe au-delà
du seuil de déclenchement REPTILIAN_ALERT (z>=2.5 sur baseline mu=5 σ=8).

Au prochain reboot du Guardian, l'arc nociceptif Option B doit déclencher :

    body_schema.gather_state → hippocampus.pending_episodes = N
        ↓
    hypothalamus._apply_synaptic_congestion_pressure (push sleep_pressure)
        ↓
    reptilian_core._on_synaptic_congestion_pressure
        (threat_memories[synaptic_congestion])
        ↓
    REPTILIAN_ALERT pattern=synaptic_congestion (severity ≥ 5.0)
        ↓
    autonomy_engine._on_reptilian_alert (filter accepte synaptic_congestion)
        → _forced_next_intent = MEMORY_CONSOLIDATION
        → reptile.urgency_cond.notify_all() (V14.11 couplage fort)
        → _urgency_mirror_watcher set _urgency_mirror Event (V14.11 bridge)
        ↓
    🚨 REFLEXE PURGE → MEMORY_CONSOLIDATION exécutée en ~1s

Le script ne stoppe PAS le Guardian. Restart manuel requis pour activation
(le compteur live en RAM survit au modif disque ; le _load() restaurera
la valeur injectée).

Usage :
    python scripts/inject_synaptic_congestion.py            # 50 pending, écrit
    python scripts/inject_synaptic_congestion.py --count 80 # 80 pending
    python scripts/inject_synaptic_congestion.py --dry-run  # affiche sans écrire
    python scripts/inject_synaptic_congestion.py --restore  # restaure dernier backup
"""
import argparse
import json
import os
import shutil
import sys
import time
from glob import glob

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "memory", "hippocampus_state.json"
)
BACKUP_PREFIX = STATE_FILE + ".bak_pre_congestion_"


def restore_latest_backup():
    backups = sorted(glob(BACKUP_PREFIX + "*"), reverse=True)
    if not backups:
        print("[ERREUR] Aucun backup pre_congestion trouvé")
        sys.exit(1)
    latest = backups[0]
    shutil.copyfile(latest, STATE_FILE)
    print(f"[RESTORE] {latest} → {STATE_FILE}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50,
                    help="Nombre d'épisodes pending à injecter (défaut 50)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Affiche sans écrire")
    ap.add_argument("--restore", action="store_true",
                    help="Restaure le dernier backup et sort")
    args = ap.parse_args()

    if args.restore:
        restore_latest_backup()
        return

    if not os.path.exists(STATE_FILE):
        print(f"[ERREUR] {STATE_FILE} introuvable")
        sys.exit(1)

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    current = int(state.get("episode_count_since_consolidation", 0))
    print(f"[OBSERVE] episode_count_since_consolidation actuel = {current}")
    print(f"[INJECT]  Nouvelle valeur cible                    = {args.count}")
    # Calibration : baseline mu=5 σ=8 (min_sigma=8). Pour z>=1.5 → count>=17
    # (push hypothalamique). Pour z>=2.5 (severity>=5.0 → REPTILIAN_ALERT)
    # → count>=25.
    z = (args.count - 5.0) / 8.0
    severity = min(10.0, max(0.0, z * 2.0))
    print(f"          z-score attendu (baseline mu=5 σ=8)    = {z:.2f}")
    print(f"          severity attendue                        = {severity:.2f}"
          f" (seuil REPTILIAN_ALERT = 5.0)")

    if args.dry_run:
        print("[DRY-RUN] Aucune modification écrite")
        return

    now = time.time()
    backup = BACKUP_PREFIX + str(int(now))
    shutil.copyfile(STATE_FILE, backup)
    print(f"[BACKUP]  {backup}")

    state["episode_count_since_consolidation"] = int(args.count)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)

    print(f"[OK]      Injection effectuée. Restart Guardian pour activation.")
    print(f"[NEXT]    Au boot, surveiller :")
    print(f"          - hippocampus.get_pending_episodes_count() = {args.count}")
    print(f"          - hypothalamus → SYNAPTIC_CONGESTION_PRESSURE event")
    print(f"          - reptilian → threat_memories[synaptic_congestion]")
    print(f"          - REPTILIAN_ALERT pattern=synaptic_congestion")
    print(f"          - 🚨 REFLEXE PURGE → MEMORY_CONSOLIDATION (~1s via V14.10)")


if __name__ == "__main__":
    main()
