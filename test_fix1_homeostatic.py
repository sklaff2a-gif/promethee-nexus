"""Test fonctionnel Fix 1 — Fermeture homeostatique des goals.

Scenarios :
  1. Goal cree depuis drive frustre, routine "vraiment satisfaisante" -> fermeture homeostatique
  2. Goal cree depuis drive frustre, routine "bureaucratique" -> abandon fruitless
  3. Goal sans metadata -> fallback bureaucratique
  4. Goal avec drive inconnu -> fallback gracieux

Lance ce test depuis la racine du repo Promethee :
    python test_fix1_homeostatic.py
"""

import os
import sys
import time

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PROMETHEE_TEST_MODE"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.tension_protocol import make_goal_metadata, TensionMeasurement
from core.desire_engine import desires, NATURAL_RISE_PER_HOUR
from core.prefrontal import prefrontal, Goal, GoalStep


def reset_state():
    """Remet les organes dans un etat propre."""
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


def test_1_homeostatic_success():
    """Goal cree depuis drive CURIOSITE a 60. Routine satisfait drive a 25.
    -> causal_drop = (60 + rise * tiny) - 25 ≈ 35 -> resolved (>= 60*0.4=24)."""
    print("\n[TEST 1] Fermeture homeostatique reussie")
    reset_state()

    desires.drives["CURIOSITE"].deprivation = 60.0

    goal_meta = make_goal_metadata(
        source_organ="desire_engine",
        source_key="CURIOSITE",
        tension_at_birth=60.0,
        goal_id="test1",
        created_at=time.time(),
    )
    goal = Goal(
        id="test1",
        title="Satisfaire pulsion: CURIOSITE",
        horizon="short",
        priority=5.0,
        source="desire",
        steps=[GoalStep(intent="VEILLE_SILENCIEUSE", description="Lecture")],
        drive_alignment={"CURIOSITE": 1.0},
        metadata=goal_meta,
    )
    prefrontal.goals.append(goal)

    # Simuler routine satisfaisante : la deprivation tombe a 25 (drop de 35)
    desires.drives["CURIOSITE"].deprivation = 25.0
    goal.steps[0].status = "done"

    prefrontal._check_goal_completion(goal)

    print(f"  status={goal.status}  completion_mode={goal.metadata.get('completion_mode')}")
    print(f"  causal_drop={goal.metadata.get('causal_drop')}")
    print(f"  homeostatic={prefrontal.stats['homeostatic_completions']}  "
          f"bureaucratic={prefrontal.stats['bureaucratic_completions']}  "
          f"false={prefrontal.stats['false_completions']}")
    assert goal.status == "completed", f"Expected completed, got {goal.status}"
    assert goal.metadata.get("completion_mode") == "homeostatic", \
        f"Expected homeostatic, got {goal.metadata.get('completion_mode')}"
    assert prefrontal.stats["homeostatic_completions"] == 1
    print("  PASS")


def test_2_fruitless_abandonment():
    """Goal cree depuis drive CURIOSITE a 60. La deprivation reste a 60 apres routine.
    -> causal_drop ≈ 0 -> non resolu -> fruitless cycle -> apres N cycles -> abandon."""
    print("\n[TEST 2] Abandon fruitless apres N cycles steriles")
    reset_state()

    desires.drives["CURIOSITE"].deprivation = 60.0

    goal_meta = make_goal_metadata(
        source_organ="desire_engine",
        source_key="CURIOSITE",
        tension_at_birth=60.0,
        goal_id="test2",
        created_at=time.time(),
    )
    goal_meta["max_fruitless"] = 3  # Pour test rapide
    goal = Goal(
        id="test2",
        title="Satisfaire pulsion: CURIOSITE (sterile)",
        horizon="short",
        priority=5.0,
        source="desire",
        steps=[GoalStep(intent="ROUTINE_BRISEE", description="Cassee")],
        drive_alignment={"CURIOSITE": 1.0},
        metadata=goal_meta,
    )
    prefrontal.goals.append(goal)

    # Simuler 3 cycles sans satisfaction
    for cycle in range(4):
        # La deprivation reste a 60 (ou monte legerement avec le rise)
        goal.steps[0].status = "done"
        prefrontal._check_goal_completion(goal)
        # Reset le step pour le prochain cycle
        if goal.status == "active":
            goal.steps[0].status = "pending"

    print(f"  status={goal.status}  completion_mode={goal.metadata.get('completion_mode')}")
    print(f"  fruitless_cycles={goal.metadata.get('fruitless_cycles')}")
    print(f"  abandon_reason={goal.abandon_reason}")
    print(f"  homeostatic={prefrontal.stats['homeostatic_completions']}  "
          f"bureaucratic={prefrontal.stats['bureaucratic_completions']}  "
          f"false={prefrontal.stats['false_completions']}")
    assert goal.status == "abandoned", f"Expected abandoned, got {goal.status}"
    assert prefrontal.stats["false_completions"] == 1
    print("  PASS")


def test_3_bureaucratic_fallback():
    """Goal sans metadata -> fallback bureaucratique."""
    print("\n[TEST 3] Fallback bureaucratique (pas de metadata)")
    reset_state()

    goal = Goal(
        id="test3",
        title="Goal sans metadata",
        horizon="short",
        priority=5.0,
        source="manual",
        steps=[GoalStep(intent="EXPANSION_CODE", description="Build")],
        # metadata vide par defaut
    )
    prefrontal.goals.append(goal)

    goal.steps[0].status = "done"
    prefrontal._check_goal_completion(goal)

    print(f"  status={goal.status}  completion_mode={goal.metadata.get('completion_mode')}")
    print(f"  homeostatic={prefrontal.stats['homeostatic_completions']}  "
          f"bureaucratic={prefrontal.stats['bureaucratic_completions']}")
    assert goal.status == "completed"
    assert goal.metadata.get("completion_mode") == "bureaucratic"
    assert prefrontal.stats["bureaucratic_completions"] == 1
    print("  PASS")


def test_4_unknown_drive():
    """Goal avec drive inconnu -> fallback gracieux (mesure neutre, fermeture bureaucratique)."""
    print("\n[TEST 4] Drive inconnu (fallback gracieux)")
    reset_state()

    goal_meta = make_goal_metadata(
        source_organ="desire_engine",
        source_key="DRIVE_INCONNU",
        tension_at_birth=50.0,
        goal_id="test4",
        created_at=time.time(),
    )
    goal = Goal(
        id="test4",
        title="Goal drive inconnu",
        horizon="short",
        priority=5.0,
        source="desire",
        steps=[GoalStep(intent="ANYTHING", description="Test")],
        metadata=goal_meta,
    )
    prefrontal.goals.append(goal)

    goal.steps[0].status = "done"
    # measure_tension va retourner is_resolved=False, is_worsened=False
    # -> fruitless_cycles va incrementer
    for _ in range(6):  # plus que max_fruitless=5
        if goal.status != "active":
            break
        goal.steps[0].status = "done"
        prefrontal._check_goal_completion(goal)
        if goal.status == "active":
            goal.steps[0].status = "pending"

    print(f"  status={goal.status}  completion_mode={goal.metadata.get('completion_mode')}")
    # Soit abandon fruitless soit completed selon la logique
    assert goal.status in ("abandoned", "completed"), f"Got {goal.status}"
    print("  PASS")


def main():
    print("=" * 70)
    print("TEST FONCTIONNEL FIX 1 — Fermeture homeostatique")
    print("=" * 70)
    try:
        test_1_homeostatic_success()
        test_2_fruitless_abandonment()
        test_3_bureaucratic_fallback()
        test_4_unknown_drive()
        print("\n" + "=" * 70)
        print("TOUS LES TESTS PASSENT")
        print("=" * 70)
        return 0
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
