"""Premiers secours — appel manuel de cortex.dream_consolidation().

À lancer Prométhée stoppé (sinon conflit d'écriture sur synaptic_network.json).
Purge l'arriéré dream + replay MDP V12 + clean.

Usage : python scripts/dream_emergency.py
"""

import sys
import time
import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def section(title):
    print(f"\n{'═' * 70}\n  {title}\n{'═' * 70}")


def main():
    section("0. ÉTAT AVANT")
    from core.synaptic_network import cortex
    from statistics import mean, median, stdev

    syn_count_before = len(cortex.synapses)
    nodes_before = len(cortex.nodes)
    last_dream_before = cortex._last_dream_time
    print(f"  Nœuds      : {nodes_before}")
    print(f"  Synapses   : {syn_count_before}")
    if last_dream_before > 0:
        delta_h = (time.time() - last_dream_before) / 3600
        print(f"  Last dream : {datetime.datetime.fromtimestamp(last_dream_before).strftime('%Y-%m-%d %H:%M:%S')} (il y a {delta_h:.1f}h)")
    weights_before = [s.weight for s in cortex.synapses.values() if hasattr(s, 'weight')]
    if weights_before:
        print(f"  Poids      : mean={mean(weights_before):.4f} median={median(weights_before):.4f}")
        n_strong = sum(1 for w in weights_before if w >= 0.5)
        n_weak = sum(1 for w in weights_before if w < 0.1)
        print(f"  Fortes (>=0.5) : {n_strong}    Faibles (<0.1) : {n_weak} ({100*n_weak/len(weights_before):.1f}%)")

    section("1. APPEL dream_consolidation()")
    print("  En cours...")
    t0 = time.time()
    report = cortex.dream_consolidation()
    elapsed = time.time() - t0
    print(f"  Terminé en {elapsed:.2f}s\n")
    for k, v in report.items():
        print(f"    {k:30} {v}")

    section("2. SAVE")
    cortex.save()
    print("  cortex.save() OK")

    section("3. ÉTAT APRÈS")
    syn_count_after = len(cortex.synapses)
    nodes_after = len(cortex.nodes)
    print(f"  Nœuds      : {nodes_after}  (Δ {nodes_after - nodes_before:+d})")
    print(f"  Synapses   : {syn_count_after}  (Δ {syn_count_after - syn_count_before:+d})")
    weights_after = [s.weight for s in cortex.synapses.values() if hasattr(s, 'weight')]
    if weights_after:
        print(f"  Poids      : mean={mean(weights_after):.4f} median={median(weights_after):.4f}")
        n_strong = sum(1 for w in weights_after if w >= 0.5)
        n_weak = sum(1 for w in weights_after if w < 0.1)
        print(f"  Fortes (>=0.5) : {n_strong}    Faibles (<0.1) : {n_weak} ({100*n_weak/len(weights_after):.1f}%)")
    new_dream = cortex._last_dream_time
    print(f"  Last dream : {datetime.datetime.fromtimestamp(new_dream).strftime('%Y-%m-%d %H:%M:%S')}")

    section("4. REPLAY MDP V12 (anti-rumination)")
    try:
        from core.hippocampus import hippocampus
        from core.basal_ganglia import ganglia
        trajectory = hippocampus.get_recent_trajectory()
        print(f"  Trajectoire récente : {len(trajectory)} steps")
        if len(trajectory) >= 2:
            updates = ganglia.update_sequential(trajectory)
            hippocampus.trajectory_buffer.clear()
            print(f"  MDP transitions Q-updatées : {updates}")
            print(f"  Trajectory buffer : cleared")
        else:
            print("  Trajectoire trop courte, pas de replay MDP")
    except Exception as e:
        print(f"  Replay MDP échoué : {e}")

    print(f"\n  ✅ Premiers secours terminés.")


if __name__ == "__main__":
    main()
