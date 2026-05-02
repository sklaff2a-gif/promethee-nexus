"""Chaos Engineering — Injection de privation REM artificielle.

Recule `last_dream_time` dans memory/synaptic_network.json pour simuler N heures
sans consolidation. Au prochain reboot du Guardian, l'arc nociceptif (V14.2 + V14.3
+ V14.4) doit déclencher la cascade complète :

    hypothalamus._apply_synaptic_debt_pressure (push sleep_pressure)
        ↓
    reptilian_core._on_synaptic_debt_pressure (threat_memory[stale_dream])
        ↓
    REPTILIAN_ALERT publié (severity = 2 × zscore, threshold ≥ 5.0)
        ↓
    autonomy_engine._on_reptilian_alert → préemption MEMORY_CONSOLIDATION
        ↓
    🚨 REFLEXE PURGE : MEMORY_CONSOLIDATION forcée

Le script ne stoppe PAS le Guardian. Il faut redémarrer manuellement après
injection pour que `_load()` charge la nouvelle valeur.

Usage :
    python scripts/inject_sleep_deprivation.py            # 20h, écrit
    python scripts/inject_sleep_deprivation.py --hours 24 # 24h
    python scripts/inject_sleep_deprivation.py --dry-run  # affiche sans écrire
    python scripts/inject_sleep_deprivation.py --restore  # restaure le dernier backup
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
    "memory", "synaptic_network.json"
)
BACKUP_PREFIX = STATE_FILE + ".bak_pre_chaos_"


def restore_latest_backup():
    backups = sorted(glob(BACKUP_PREFIX + "*"), reverse=True)
    if not backups:
        print("[ERREUR] Aucun backup pre_chaos trouvé")
        sys.exit(1)
    latest = backups[0]
    shutil.copyfile(latest, STATE_FILE)
    print(f"[RESTORE] {latest} → {STATE_FILE}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=20.0,
                    help="Heures de privation à injecter (défaut 20)")
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

    now = time.time()
    current = state.get("last_dream_time", 0.0)

    if current > 0:
        actual_dette_h = (now - current) / 3600.0
        print(f"[OBSERVE] last_dream_time actuel = {current:.0f}")
        print(f"          dette actuelle         = {actual_dette_h:.2f}h")
    else:
        print("[OBSERVE] last_dream_time = 0 (jamais initialisé)")

    new_value = now - args.hours * 3600
    print(f"[INJECT]  Nouvelle valeur cible    = {new_value:.0f}")
    print(f"          dette injectée           = {args.hours:.2f}h")
    print(f"          z-score attendu (baseline mu=8 sigma=4) = "
          f"{(args.hours - 8.0) / 4.0:.2f}")
    print(f"          severity attendue        = "
          f"{min(10.0, max(0.0, (args.hours - 8.0) / 4.0 * 2.0)):.2f} "
          f"(seuil REPTILIAN_ALERT = 5.0)")

    if args.dry_run:
        print("[DRY-RUN] Aucune modification écrite")
        return

    # Backup atomique avant écriture
    backup = BACKUP_PREFIX + str(int(now))
    shutil.copyfile(STATE_FILE, backup)
    print(f"[BACKUP]  {backup}")

    state["last_dream_time"] = new_value
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)

    print(f"[OK]      Injection effectuée. Restart Guardian pour activation.")
    print(f"[NEXT]    Au boot, surveiller :")
    print(f"          - hypothalamus.current_values.sleep_pressure (V14.2 push)")
    print(f"          - reptilian.threat_memories.stale_dream (V14.3)")
    print(f"          - autonomy.routine_history → MEMORY_CONSOLIDATION (V14.4)")
    print(f"          - logs : grep 'REFLEXE PURGE'")


if __name__ == "__main__":
    main()
