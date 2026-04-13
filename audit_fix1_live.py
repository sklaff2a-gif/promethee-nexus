"""Audit live Fix 1 — Stress test homeostatique sur les VRAIS modules.

Utilise les modules prefrontal et desire_engine reels (avec Fix 1 actif),
simule 100 goals avec des patterns realistes, et mesure le ratio
homeostatic / bureaucratic / false_completions.

Objectif : valider l'hypothese "60-80% des fermetures sont illusoires"
avant de connecter le hook dopamine. Temps : ~10s d'execution.

Patterns simules :
  - 40% : Drives qui RESPONDENT aux routines (satisfaction reelle attendue)
  - 30% : Drives qui NE RESPONDENT PAS (les routines tournent mais la
          tension ne baisse pas — bullshit jobs)
  - 20% : Drives qui DESCENDENT NATURELLEMENT (decay > action)
  - 10% : Drives extremes (high deprivation, urgence)

Lance:
    python audit_fix1_live.py
"""

import os
import sys
import time
import random
import json

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PROMETHEE_TEST_MODE"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.tension_protocol import make_goal_metadata
from core.desire_engine import desires
from core.prefrontal import prefrontal, Goal, GoalStep


# ============================================================
# Configuration de l'audit
# ============================================================

N_GOALS = 100              # Nombre de goals a simuler
DRIVE_NAMES = ["CURIOSITE", "MAITRISE", "STABILITE", "CONNEXION",
               "CROISSANCE", "CREATION", "COMPREHENSION"]

# Simulation : la tension de chaque drive evolue selon le scenario
SCENARIOS = {
    "responsive": 0.40,    # Routine baisse vraiment la tension
    "unresponsive": 0.30,  # Routine done mais tension stable
    "natural_decay": 0.20, # Tension descend toute seule (action n'a rien fait)
    "extreme": 0.10,       # Tension haute, urgence
}


def reset_state():
    desires.reset()
    prefrontal.goals = []
    prefrontal.stats = {
        "goals_created": 0,
        "goals_completed": 0,
        "goals_abandoned": 0,
        "homeostatic_completions": 0,
        "bureaucratic_completions": 0,
        "false_completions": 0,
        "inhibitions_applied": 0,
        "overrides_applied": 0,
        "strategies_crystallized": 0,
    }


def simulate_goal_lifecycle(drive_name: str, scenario: str, goal_num: int) -> dict:
    """Cree un goal pour le drive donne, simule l'execution selon le scenario,
    et retourne le resultat."""
    drive = desires.drives[drive_name]

    # Setup tension initiale selon scenario
    if scenario == "extreme":
        initial_tension = random.uniform(75, 95)
    else:
        initial_tension = random.uniform(45, 70)

    drive.deprivation = initial_tension
    drive.frustration_streak = random.choice([0, 0, 0, 1, 2])

    # Creer le goal avec metadata
    goal_id = f"audit_{goal_num:03d}"
    goal_meta = make_goal_metadata(
        source_organ="desire_engine",
        source_key=drive_name,
        tension_at_birth=initial_tension,
        goal_id=goal_id,
        created_at=time.time(),
    )
    goal_meta["max_fruitless"] = 3  # Test rapide

    # Steps depuis _DRIVE_ROUTINE_MAP (vraie source de Promethee)
    from core.prefrontal import _DRIVE_ROUTINE_MAP
    routines = _DRIVE_ROUTINE_MAP.get(drive_name, ["VEILLE_SILENCIEUSE"])[:2]
    steps = [GoalStep(intent=r, description=f"Test {drive_name}") for r in routines]

    goal = Goal(
        id=goal_id,
        title=f"Audit {drive_name} ({scenario})",
        horizon="short",
        priority=5.0,
        source="desire",
        steps=steps,
        drive_alignment={drive_name: 1.0},
        metadata=goal_meta,
    )
    prefrontal.goals.append(goal)

    # ── Simulation execution ──
    cycle_max = 4  # Nombre max de cycles de routine
    for cycle in range(cycle_max):
        if goal.status != "active":
            break

        # Marquer toutes les steps comme done
        for s in goal.steps:
            s.status = "done"

        # Appliquer le scenario : la deprivation evolue
        if scenario == "responsive":
            # La routine satisfait vraiment : drop de 40-60% par cycle
            drop = drive.deprivation * random.uniform(0.4, 0.6)
            drive.deprivation = max(0, drive.deprivation - drop)
        elif scenario == "unresponsive":
            # La routine ne fait rien : la tension reste stable (legere fluctuation)
            drive.deprivation += random.uniform(-1, 1)
        elif scenario == "natural_decay":
            # Decay naturel attribuable a autre chose : tension baisse mais pas grace a l'action
            # Pour simuler ca, on baisse la tension mais on simule un temps long
            # de sorte que expected_if_no_action soit aussi plus bas
            time_elapsed = random.uniform(0.1, 0.3)  # heures
            goal.metadata["created_at"] -= time_elapsed * 3600  # backdate
            drive.deprivation = max(0, drive.deprivation - random.uniform(5, 15))
        elif scenario == "extreme":
            # Drive extreme, l'action aide modestement
            drop = drive.deprivation * random.uniform(0.2, 0.4)
            drive.deprivation = max(0, drive.deprivation - drop)

        # Check completion via le vrai mecanisme
        prefrontal._check_goal_completion(goal)

    return {
        "goal_id": goal_id,
        "drive": drive_name,
        "scenario": scenario,
        "initial_tension": round(initial_tension, 1),
        "final_tension": round(drive.deprivation, 1),
        "status": goal.status,
        "completion_mode": goal.metadata.get("completion_mode"),
        "causal_drop": goal.metadata.get("causal_drop"),
        "fruitless_cycles": goal.metadata.get("fruitless_cycles", 0),
    }


def main():
    print("=" * 80)
    print("AUDIT LIVE FIX 1 — Stress test homeostatique sur 100 goals")
    print("=" * 80)
    print(f"Configuration : {N_GOALS} goals, scenarios {SCENARIOS}")

    reset_state()

    results = []
    for i in range(N_GOALS):
        # Choisir scenario selon distribution
        r = random.random()
        cum = 0
        scenario = "responsive"
        for s, p in SCENARIOS.items():
            cum += p
            if r < cum:
                scenario = s
                break

        # Choisir drive aleatoire
        drive_name = random.choice(DRIVE_NAMES)

        result = simulate_goal_lifecycle(drive_name, scenario, i)
        results.append(result)

        # Live tail
        marker = {
            "homeostatic": "[HOMEOS]",
            "bureaucratic": "[BUREAU]",
            "abandoned_fruitless": "[FALSE] ",
            None: "[?????] ",
        }.get(result["completion_mode"], "[?????] ")
        cd_str = f"cd={result['causal_drop']:+.1f}" if result["causal_drop"] is not None else "cd=N/A"
        if i % 5 == 0 or "FALSE" in marker:
            print(f"  {marker} #{i:03d} {result['drive']:13s} ({result['scenario']:13s}) "
                  f"{result['initial_tension']:5.1f}→{result['final_tension']:5.1f}  {cd_str}")

    # ============================================================
    # Synthese
    # ============================================================
    print("\n" + "=" * 80)
    print("STATS GLOBALES")
    print("=" * 80)
    print(f"goals_created            = {prefrontal.stats['goals_created']}")
    print(f"goals_completed          = {prefrontal.stats['goals_completed']}")
    print(f"  homeostatic_completions = {prefrontal.stats.get('homeostatic_completions', 0)}")
    print(f"  bureaucratic_completions= {prefrontal.stats.get('bureaucratic_completions', 0)}")
    print(f"goals_abandoned          = {prefrontal.stats['goals_abandoned']}")
    print(f"  false_completions       = {prefrontal.stats.get('false_completions', 0)}")

    total_decisions = (prefrontal.stats.get('homeostatic_completions', 0) +
                       prefrontal.stats.get('bureaucratic_completions', 0) +
                       prefrontal.stats.get('false_completions', 0))
    if total_decisions > 0:
        h = prefrontal.stats.get('homeostatic_completions', 0)
        b = prefrontal.stats.get('bureaucratic_completions', 0)
        f = prefrontal.stats.get('false_completions', 0)
        print(f"\nRATIOS (sur {total_decisions} decisions) :")
        print(f"  Homeostatic   : {100*h/total_decisions:5.1f}%  ({h})")
        print(f"  Bureaucratic  : {100*b/total_decisions:5.1f}%  ({b})")
        print(f"  False         : {100*f/total_decisions:5.1f}%  ({f})")

    # ============================================================
    # Breakdown par scenario
    # ============================================================
    print("\n" + "=" * 80)
    print("BREAKDOWN PAR SCENARIO")
    print("=" * 80)
    print(f"{'scenario':<15s}{'total':<8}{'homeo':<8}{'bureau':<8}{'false':<8}{'%homeo':<8}")
    print("-" * 60)
    for sc in SCENARIOS:
        sub = [r for r in results if r["scenario"] == sc]
        n = len(sub)
        if n == 0:
            continue
        h = sum(1 for r in sub if r["completion_mode"] == "homeostatic")
        b = sum(1 for r in sub if r["completion_mode"] == "bureaucratic")
        f = sum(1 for r in sub if r["completion_mode"] == "abandoned_fruitless")
        print(f"{sc:<15s}{n:<8}{h:<8}{b:<8}{f:<8}{100*h/n:<8.1f}")

    # ============================================================
    # Diagnostic
    # ============================================================
    print("\n" + "=" * 80)
    print("DIAGNOSTIC")
    print("=" * 80)
    if total_decisions == 0:
        print("Aucune decision : edge case, voir les logs")
        return 1

    h_pct = 100 * prefrontal.stats.get('homeostatic_completions', 0) / total_decisions
    b_pct = 100 * prefrontal.stats.get('bureaucratic_completions', 0) / total_decisions
    f_pct = 100 * prefrontal.stats.get('false_completions', 0) / total_decisions

    if h_pct > 80:
        print("[OK] Le systeme est majoritairement homeostatique. Sain.")
    elif h_pct > 50:
        print("[OK] Majorite homeostatique mais reste du bureaucratique.")
    elif h_pct > 20:
        print("[WARN] Trop peu d'homeostatique. Verifier les seuils.")
    else:
        print("[FAIL] Le systeme ferme bureaucratiquement. Fix 1 inutile.")

    if f_pct > 30:
        print(f"[FAIL] {f_pct:.0f}% de false_completions : seuil trop strict ou max_fruitless trop bas")
    elif f_pct > 10:
        print(f"[OK] {f_pct:.0f}% de false_completions : detection saine des bullshit jobs")
    else:
        print(f"[INFO] {f_pct:.0f}% de false_completions")

    # Save detail
    out = {
        "stats": dict(prefrontal.stats),
        "ratios": {"homeostatic": h_pct, "bureaucratic": b_pct, "false": f_pct},
        "results_sample": results[:20],
    }
    with open("audit_fix1_results.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nResultats detailles : audit_fix1_results.json")
    return 0


if __name__ == "__main__":
    random.seed(42)  # Reproductible
    sys.exit(main())
