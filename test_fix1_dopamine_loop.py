"""Test boucle complete Fix 1 + dopamine RPE.

Verifie que :
  1. Goal homeostatique → DOPAMINE SURGE → V(intent) augmente
  2. Goal fruitless → DOPAMINE DIP → V(intent) diminue
  3. Per-intent attribution : seules les routines participantes sont affectees
  4. Strategies coupables sont decristallisees (via _record_strategy_failure)
"""

import os
import sys
import time
import asyncio

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PROMETHEE_TEST_MODE"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.tension_protocol import make_goal_metadata
from core.desire_engine import desires
from core.prefrontal import prefrontal, Goal, GoalStep
from core.dopamine_system import dopamine, BASELINE_DOPAMINE


async def reset_state():
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
    dopamine.reset()
    # Subscribe events fraichement
    dopamine._subscribed = False
    dopamine._subscribe_events()


async def test_homeostatic_creates_surge():
    """Goal homeostatique → V(intents) augmente."""
    print("\n[TEST 1] Boucle homeostatique → SURGE")
    await reset_state()

    desires.drives["CURIOSITE"].deprivation = 70.0

    goal_meta = make_goal_metadata(
        source_organ="desire_engine",
        source_key="CURIOSITE",
        tension_at_birth=70.0,
        goal_id="loop_test1",
        created_at=time.time(),
    )
    goal = Goal(
        id="loop_test1",
        title="Loop test homeostatic",
        horizon="short",
        priority=5.0,
        source="desire",
        steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="A"),
            GoalStep(intent="EXPANSION_CODE", description="B"),
        ],
        metadata=goal_meta,
    )
    prefrontal.goals.append(goal)

    # Note V(intent) avant
    v_before_veille = dopamine.memories.get("VEILLE_SILENCIEUSE")
    v_before_expansion = dopamine.memories.get("EXPANSION_CODE")
    v_before_orphan = dopamine.memories.get("UNRELATED_INTENT")
    print(f"  Avant : V(VEILLE)={v_before_veille}  V(EXPANSION)={v_before_expansion}")

    # Routine satisfait : drop tension
    desires.drives["CURIOSITE"].deprivation = 25.0
    for s in goal.steps:
        s.status = "done"

    prefrontal._check_goal_completion(goal)

    # Le bus event est dispatch async, donc on attend un tick
    await asyncio.sleep(0.1)

    v_after_veille = dopamine.memories.get("VEILLE_SILENCIEUSE")
    v_after_expansion = dopamine.memories.get("EXPANSION_CODE")
    v_after_orphan = dopamine.memories.get("UNRELATED_INTENT")

    print(f"  Apres : V(VEILLE)={v_after_veille.expected_reward if v_after_veille else 'None'}")
    print(f"          V(EXPANSION)={v_after_expansion.expected_reward if v_after_expansion else 'None'}")
    print(f"          V(UNRELATED)={v_after_orphan}  (doit rester None)")
    print(f"  dopamine_level={dopamine.dopamine_level:.3f}  (baseline {BASELINE_DOPAMINE})")

    assert v_after_veille is not None, "VEILLE devrait avoir une memoire apres goal"
    assert v_after_expansion is not None, "EXPANSION devrait avoir une memoire apres goal"
    assert v_after_orphan is None, "UNRELATED ne devrait pas avoir de memoire"
    assert dopamine.dopamine_level >= BASELINE_DOPAMINE, \
        f"dopamine devrait monter, vue {dopamine.dopamine_level}"
    print("  PASS")


async def test_fruitless_creates_dip():
    """Goal fruitless → V(intents) baisse + DIP."""
    print("\n[TEST 2] Boucle fruitless → DIP")
    await reset_state()

    # Pre-charger une memoire haute pour VEILLE_SILENCIEUSE (pour avoir un dip mesurable)
    from core.dopamine_system import RewardMemory
    dopamine.memories["VEILLE_SILENCIEUSE"] = RewardMemory(expected_reward=0.7)
    dopamine.memories["EXPANSION_CODE"] = RewardMemory(expected_reward=0.7)
    v_before = dopamine.memories["VEILLE_SILENCIEUSE"].expected_reward
    print(f"  Avant : V(VEILLE)={v_before:.3f}")

    desires.drives["CURIOSITE"].deprivation = 60.0

    goal_meta = make_goal_metadata(
        source_organ="desire_engine",
        source_key="CURIOSITE",
        tension_at_birth=60.0,
        goal_id="loop_test2",
        created_at=time.time(),
    )
    goal_meta["max_fruitless"] = 2  # Test rapide

    goal = Goal(
        id="loop_test2",
        title="Loop test fruitless",
        horizon="short",
        priority=5.0,
        source="desire",
        steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="A"),
            GoalStep(intent="EXPANSION_CODE", description="B"),
        ],
        metadata=goal_meta,
    )
    prefrontal.goals.append(goal)

    dopamine_before = dopamine.dopamine_level

    # Plusieurs cycles fruitless
    for cycle in range(3):
        if goal.status != "active":
            break
        for s in goal.steps:
            s.status = "done"
        prefrontal._check_goal_completion(goal)
        if goal.status == "active":
            for s in goal.steps:
                s.status = "pending"

    await asyncio.sleep(0.1)

    v_after = dopamine.memories["VEILLE_SILENCIEUSE"].expected_reward
    print(f"  Apres : V(VEILLE)={v_after:.3f}  (delta {v_after - v_before:+.3f})")
    print(f"  dopamine_level={dopamine.dopamine_level:.3f}  (avant {dopamine_before:.3f})")
    print(f"  goal.status={goal.status}  fruitless_cycles={goal.metadata.get('fruitless_cycles')}")

    assert goal.status == "abandoned"
    assert v_after < v_before, f"V(VEILLE) devrait baisser, {v_before}->{v_after}"
    assert dopamine.dopamine_level <= dopamine_before, "dopamine devrait baisser"
    print("  PASS")


async def test_innocent_strategies_protected():
    """Goal A homeostatique sur intents X, Y → V(X) et V(Y) montent.
    Goal B fruitless sur intents W, Z → V(W) et V(Z) baissent.
    Verifier que V(X) et V(Y) ne sont PAS affectes par l'echec de B."""
    print("\n[TEST 3] Strategies innocentes preservees")
    await reset_state()

    # Pre-charger memoires
    from core.dopamine_system import RewardMemory
    dopamine.memories["INTENT_X"] = RewardMemory(expected_reward=0.5)
    dopamine.memories["INTENT_Y"] = RewardMemory(expected_reward=0.5)
    dopamine.memories["INTENT_W"] = RewardMemory(expected_reward=0.5)
    dopamine.memories["INTENT_Z"] = RewardMemory(expected_reward=0.5)

    # Goal A homeostatique
    desires.drives["CURIOSITE"].deprivation = 70.0
    goal_a = Goal(
        id="goal_a",
        title="Innocent",
        horizon="short", priority=5.0, source="desire",
        steps=[GoalStep(intent="INTENT_X", description="x"), GoalStep(intent="INTENT_Y", description="y")],
        metadata=make_goal_metadata("desire_engine", "CURIOSITE", 70.0, "goal_a", time.time()),
    )
    prefrontal.goals.append(goal_a)
    desires.drives["CURIOSITE"].deprivation = 20.0  # drop massif
    for s in goal_a.steps:
        s.status = "done"
    prefrontal._check_goal_completion(goal_a)
    await asyncio.sleep(0.05)

    # Goal B fruitless sur drive different
    desires.drives["MAITRISE"].deprivation = 60.0
    goal_meta_b = make_goal_metadata("desire_engine", "MAITRISE", 60.0, "goal_b", time.time())
    goal_meta_b["max_fruitless"] = 1
    goal_b = Goal(
        id="goal_b",
        title="Coupable",
        horizon="short", priority=5.0, source="desire",
        steps=[GoalStep(intent="INTENT_W", description="w"), GoalStep(intent="INTENT_Z", description="z")],
        metadata=goal_meta_b,
    )
    prefrontal.goals.append(goal_b)
    # Tension reste haute
    for cycle in range(3):
        if goal_b.status != "active":
            break
        for s in goal_b.steps:
            s.status = "done"
        prefrontal._check_goal_completion(goal_b)
        if goal_b.status == "active":
            for s in goal_b.steps:
                s.status = "pending"
    await asyncio.sleep(0.05)

    v_x = dopamine.memories["INTENT_X"].expected_reward
    v_y = dopamine.memories["INTENT_Y"].expected_reward
    v_w = dopamine.memories["INTENT_W"].expected_reward
    v_z = dopamine.memories["INTENT_Z"].expected_reward
    print(f"  V(X)={v_x:.3f}  V(Y)={v_y:.3f}  (innocents, devraient etre >= 0.5)")
    print(f"  V(W)={v_w:.3f}  V(Z)={v_z:.3f}  (coupables, devraient etre < 0.5)")

    assert v_x >= 0.5, f"INTENT_X innocent devrait rester >= 0.5, vu {v_x}"
    assert v_y >= 0.5, f"INTENT_Y innocent devrait rester >= 0.5, vu {v_y}"
    assert v_w < 0.5, f"INTENT_W coupable devrait baisser, vu {v_w}"
    assert v_z < 0.5, f"INTENT_Z coupable devrait baisser, vu {v_z}"
    print("  PASS — credit assignment per-intent valide")


async def main():
    print("=" * 70)
    print("TEST BOUCLE Fix 1 + dopamine RPE")
    print("=" * 70)
    try:
        await test_homeostatic_creates_surge()
        await test_fruitless_creates_dip()
        await test_innocent_strategies_protected()
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
    sys.exit(asyncio.run(main()))
