"""
Tests du Cortex Préfrontal — Fonction Exécutive de Prométhée.

60+ tests couvrant : singleton, goals, working memory, focus bonus,
inhibition, strategies, prospective, narrative, délibération, intégration, edge cases.
"""

import asyncio
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from core.prefrontal import (
    PrefrontalCortex, prefrontal,
    Goal, GoalStep, Strategy, ProspectiveTrigger, NarrativeEntry,
    MAX_GOALS, MAX_ACTIVE_GOALS, MAX_STRATEGIES, MAX_PROSPECTIVE_TRIGGERS,
    MAX_NARRATIVE_LOG, FOCUS_BONUS_PRIMARY, FOCUS_BONUS_SECONDARY,
    FOCUS_BONUS_TERTIARY, DISTRACTION_PENALTY, COGNITIVE_LOAD_THRESHOLD,
    INHIBITION_OVERRIDE_THRESHOLD, STRATEGY_CRYSTALLIZE_THRESHOLD,
    PREFRONTAL_STATE_FILE,
)


# ─── Fixture autouse ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_prefrontal(tmp_path, monkeypatch):
    """Reset le singleton et redirige la persistance vers tmp_path."""
    PrefrontalCortex.reset_singleton()
    monkeypatch.setattr(
        "core.prefrontal.PREFRONTAL_STATE_FILE",
        str(tmp_path / "prefrontal.json"),
    )
    yield
    PrefrontalCortex.reset_singleton()


def _make_prefrontal() -> PrefrontalCortex:
    """Crée une instance fraîche du singleton."""
    return PrefrontalCortex()


def _make_goal(title="Test Goal", horizon="short", priority=5.0,
               steps=None, source="auto", status="active",
               progress=0.0, cost_spent=0, cost_estimated=10,
               drive_alignment=None, **kwargs) -> Goal:
    """Helper pour créer un goal de test."""
    if steps is None:
        steps = [
            GoalStep(intent="VEILLE_SILENCIEUSE", description="Step 1"),
            GoalStep(intent="EXPANSION_CODE", description="Step 2"),
        ]
    return Goal(
        id="test1234",
        title=title,
        horizon=horizon,
        priority=priority,
        steps=steps,
        source=source,
        status=status,
        progress=progress,
        cost_spent=cost_spent,
        cost_estimated=cost_estimated,
        drive_alignment=drive_alignment or {},
        **kwargs,
    )


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 1 : SINGLETON
# ═══════════════════════════════════════════════════════════════════════

class TestSingleton:
    def test_singleton_identity(self):
        pf1 = PrefrontalCortex()
        pf2 = PrefrontalCortex()
        assert pf1 is pf2

    def test_reset_singleton(self):
        pf1 = PrefrontalCortex()
        PrefrontalCortex.reset_singleton()
        pf2 = PrefrontalCortex()
        assert pf1 is not pf2

    def test_persistence_save_load(self, tmp_path, monkeypatch):
        pf = _make_prefrontal()
        goal = _make_goal(title="Persist Goal")
        pf.goals.append(goal)
        pf.stats["goals_created"] = 1
        pf.save()

        PrefrontalCortex.reset_singleton()
        pf2 = _make_prefrontal()
        assert len(pf2.goals) == 1
        assert pf2.goals[0].title == "Persist Goal"
        assert pf2.stats["goals_created"] == 1


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 2 : GOALS
# ═══════════════════════════════════════════════════════════════════════

class TestGoals:
    def test_create_goal(self):
        pf = _make_prefrontal()
        goal = pf.create_goal(
            title="Mon goal",
            horizon="short",
            priority=5.0,
            steps=[{"intent": "VEILLE_SILENCIEUSE", "description": "Étape 1"}],
            source="user",
        )
        assert goal is not None
        assert goal.title == "Mon goal"
        assert len(pf.goals) == 1
        assert pf.stats["goals_created"] == 1

    def test_create_goal_max_limit(self):
        pf = _make_prefrontal()
        for i in range(MAX_GOALS):
            pf.goals.append(_make_goal(title=f"Goal {i}"))
        result = pf.create_goal("Overflow", "short", 5.0, [{"intent": "X", "description": "Y"}])
        assert result is None

    def test_goal_priority_clamp(self):
        pf = _make_prefrontal()
        goal = pf.create_goal("Hi prio", "short", 15.0,
                              [{"intent": "X", "description": "Y"}])
        assert goal.priority == 10.0

    def test_goal_progress_calculation(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="A", description="1", status="done"),
            GoalStep(intent="B", description="2", status="pending"),
            GoalStep(intent="C", description="3", status="done"),
        ])
        pf._update_goal_progress(goal)
        assert abs(goal.progress - 0.67) < 0.01

    def test_goal_completion(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="A", description="1", status="done"),
            GoalStep(intent="B", description="2", status="done"),
        ])
        pf.goals.append(goal)
        pf._update_goal_progress(goal)
        pf._check_goal_completion(goal)
        assert goal.status == "completed"
        assert pf.stats["goals_completed"] == 1

    def test_goal_completion_required_only(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="A", description="1", status="done", required=True),
            GoalStep(intent="B", description="2", status="pending", required=False),
        ])
        pf.goals.append(goal)
        pf._check_goal_completion(goal)
        assert goal.status == "completed"

    def test_goal_abandonment_cost_excessive(self):
        pf = _make_prefrontal()
        goal = _make_goal(cost_spent=20, cost_estimated=10, progress=0.1)
        pf.goals.append(goal)
        abandoned = pf._check_goal_abandonment(goal)
        assert abandoned
        assert goal.status == "abandoned"
        assert "coût excessif" in goal.abandon_reason

    def test_goal_abandonment_stagnation(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="A", description="1", status="failed"),
            GoalStep(intent="B", description="2", status="failed"),
        ])
        goal.last_advanced = time.time() - 7200  # 2h ago
        pf.goals.append(goal)
        # La priorité va s'effondrer avec stagnation + failures
        abandoned = pf._check_goal_abandonment(goal)
        assert abandoned
        assert goal.status == "abandoned"

    def test_goal_priority_deadline_urgency(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=5.0)
        goal.deadline = time.time() + 100  # Très urgent (< 25% de horizon short)
        new_prio = pf._compute_goal_priority(goal)
        assert new_prio > 5.0

    def test_goal_priority_momentum(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=5.0, progress=0.3)
        new_prio = pf._compute_goal_priority(goal)
        assert new_prio > 5.0  # Momentum bonus


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 3 : WORKING MEMORY
# ═══════════════════════════════════════════════════════════════════════

class TestWorkingMemory:
    def test_empty_working_memory(self):
        pf = _make_prefrontal()
        assert pf.get_working_memory() == []

    def test_working_memory_slots(self):
        pf = _make_prefrontal()
        for i in range(5):
            pf.goals.append(_make_goal(
                title=f"Goal {i}",
                priority=float(5 - i),
            ))
            pf.goals[-1].id = f"g{i}"
        wm = pf.get_working_memory()
        assert len(wm) == MAX_ACTIVE_GOALS  # 3 slots max
        assert wm[0]["goal_title"] == "Goal 0"  # Plus haute priorité

    def test_working_memory_next_intent(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="DONE_STEP", description="1", status="done"),
            GoalStep(intent="NEXT_STEP", description="2", status="pending"),
        ])
        pf.goals.append(goal)
        wm = pf.get_working_memory()
        assert wm[0]["next_intent"] == "NEXT_STEP"

    def test_working_memory_priority_sort(self):
        pf = _make_prefrontal()
        g1 = _make_goal(title="Low", priority=2.0)
        g1.id = "low"
        g2 = _make_goal(title="High", priority=8.0)
        g2.id = "high"
        pf.goals.extend([g1, g2])
        wm = pf.get_working_memory()
        assert wm[0]["goal_title"] == "High"

    def test_working_memory_continuity_context(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="A", description="1", status="done", result_summary="Result A"),
            GoalStep(intent="B", description="2", status="pending"),
        ])
        goal.current_step = 1
        pf.goals.append(goal)
        wm = pf.get_working_memory()
        assert wm[0]["context"] == "Result A"


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 4 : FOCUS BONUS
# ═══════════════════════════════════════════════════════════════════════

class TestFocusBonus:
    def test_no_goals_returns_zero(self):
        pf = _make_prefrontal()
        assert pf.compute_focus_bonus("VEILLE_SILENCIEUSE") == 0.0

    def test_primary_goal_bonus(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="Step 1"),
        ])
        pf.goals.append(goal)
        bonus = pf.compute_focus_bonus("VEILLE_SILENCIEUSE")
        assert bonus == FOCUS_BONUS_PRIMARY

    def test_secondary_goal_bonus(self):
        pf = _make_prefrontal()
        g1 = _make_goal(title="Primary", priority=8.0, steps=[
            GoalStep(intent="A", description="1"),
        ])
        g1.id = "g1"
        g2 = _make_goal(title="Secondary", priority=5.0, steps=[
            GoalStep(intent="B", description="2"),
        ])
        g2.id = "g2"
        pf.goals.extend([g1, g2])
        bonus = pf.compute_focus_bonus("B")
        assert bonus == FOCUS_BONUS_SECONDARY

    def test_tertiary_goal_bonus(self):
        pf = _make_prefrontal()
        for i, intent in enumerate(["A", "B", "C"]):
            g = _make_goal(title=f"G{i}", priority=float(8 - i), steps=[
                GoalStep(intent=intent, description=f"S{i}"),
            ])
            g.id = f"g{i}"
            pf.goals.append(g)
        bonus = pf.compute_focus_bonus("C")
        assert bonus == FOCUS_BONUS_TERTIARY

    def test_distraction_penalty(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.5, steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="Focus"),
        ])
        pf.goals.append(goal)
        bonus = pf.compute_focus_bonus("TOTALLY_UNRELATED_INTENT")
        assert bonus == DISTRACTION_PENALTY

    def test_no_distraction_penalty_when_low_progress(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.1, steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="Focus"),
        ])
        pf.goals.append(goal)
        bonus = pf.compute_focus_bonus("OTHER_INTENT")
        # Pas de penalty car progress <= 0.2
        assert bonus >= 0.0

    def test_cognitive_load_reduction(self):
        pf = _make_prefrontal()
        # Plus de COGNITIVE_LOAD_THRESHOLD goals
        for i in range(COGNITIVE_LOAD_THRESHOLD + 2):
            g = _make_goal(title=f"G{i}", priority=float(8 - i * 0.5), steps=[
                GoalStep(intent=f"INTENT_{i}", description=f"S{i}"),
            ])
            g.id = f"g{i}"
            pf.goals.append(g)
        bonus_loaded = pf.compute_focus_bonus("INTENT_0")
        # Le bonus est réduit de 30%
        assert bonus_loaded < FOCUS_BONUS_PRIMARY
        assert bonus_loaded == round(FOCUS_BONUS_PRIMARY * 0.7, 2)

    def test_future_step_half_bonus(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, steps=[
            GoalStep(intent="CURRENT", description="Now", status="in_progress"),
            GoalStep(intent="FUTURE", description="Later", status="pending"),
        ])
        pf.goals.append(goal)
        bonus = pf.compute_focus_bonus("FUTURE")
        assert bonus == round(FOCUS_BONUS_PRIMARY * 0.5, 2)


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 5 : INHIBITION
# ═══════════════════════════════════════════════════════════════════════

class TestInhibition:
    def test_no_goals_allows(self):
        pf = _make_prefrontal()
        result = pf.compute_inhibition("VEILLE_SILENCIEUSE")
        assert result["action"] == "allow"

    def test_freeze_never_inhibited(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.9)
        pf.goals.append(goal)
        result = pf.compute_inhibition("X", "veto-reptilien: FREEZE actif")
        assert result["action"] == "allow"

    def test_somatic_never_inhibited(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.9)
        pf.goals.append(goal)
        result = pf.compute_inhibition("X", "veto-somatique: signal négatif")
        assert result["action"] == "allow"

    def test_override_shed_when_goal_advanced(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.8)  # > INHIBITION_OVERRIDE_THRESHOLD
        pf.goals.append(goal)
        result = pf.compute_inhibition("VEILLE_SILENCIEUSE", "veto-reptilien: SHED actif")
        assert result["action"] == "override"
        assert result["override_target"] == "SHED"

    def test_no_override_shed_when_goal_early(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.3)  # < threshold
        pf.goals.append(goal)
        result = pf.compute_inhibition("VEILLE_SILENCIEUSE", "veto-reptilien: SHED actif")
        assert result["action"] != "override"

    def test_override_flinch_when_goal_nearly_done(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.95)
        pf.goals.append(goal)
        result = pf.compute_inhibition("X", "veto-reptilien: FLINCH")
        assert result["action"] == "override"
        assert result["override_target"] == "FLINCH"

    def test_inhibit_distraction(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.5, steps=[
            GoalStep(intent="FOCUSED_INTENT", description="Focus"),
        ])
        pf.goals.append(goal)
        result = pf.compute_inhibition("RANDOM_DISTRACTION")
        assert result["action"] == "inhibit"

    def test_allow_on_focus_intent(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.5, steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="Focus"),
        ])
        pf.goals.append(goal)
        result = pf.compute_inhibition("VEILLE_SILENCIEUSE")
        assert result["action"] == "allow"

    def test_defer_secondary(self):
        pf = _make_prefrontal()
        g1 = _make_goal(title="Primary", priority=8.0, progress=0.3, steps=[
            GoalStep(intent="A", description="1"),
        ])
        g1.id = "g1"
        g2 = _make_goal(title="Secondary", priority=4.0, steps=[
            GoalStep(intent="B", description="2"),
        ])
        g2.id = "g2"
        pf.goals.extend([g1, g2])
        result = pf.compute_inhibition("B")
        assert result["action"] == "defer"

    def test_inhibition_stats_increment(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=8.0, progress=0.5, steps=[
            GoalStep(intent="FOCUSED", description="Focus"),
        ])
        pf.goals.append(goal)
        pf.compute_inhibition("DISTRACTION")
        assert pf.stats["inhibitions_applied"] == 1


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 6 : STRATEGIES
# ═══════════════════════════════════════════════════════════════════════

class TestStrategies:
    def test_learn_strategy_from_completed_goal(self):
        pf = _make_prefrontal()
        goal = _make_goal(status="completed", source="gap", steps=[
            GoalStep(intent="A", description="1", status="done"),
            GoalStep(intent="B", description="2", status="done"),
        ])
        goal.drive_alignment = {"CURIOSITE": 0.8}
        pf._learn_strategy_from_goal(goal)
        assert len(pf.strategies) == 1
        assert pf.strategies[0].sequence == ["A", "B"]
        assert "curiosite" in pf.strategies[0].context_tags

    def test_reinforce_existing_strategy(self):
        pf = _make_prefrontal()
        strat = Strategy(id="s1", sequence=["A", "B"], context_tags=["gap"],
                        confidence=0.5, successes=1)
        pf.strategies.append(strat)

        goal = _make_goal(status="completed", steps=[
            GoalStep(intent="A", description="1", status="done"),
            GoalStep(intent="B", description="2", status="done"),
        ])
        pf._learn_strategy_from_goal(goal)
        assert pf.strategies[0].successes == 2
        assert pf.strategies[0].confidence > 0.5

    def test_crystallization(self):
        pf = _make_prefrontal()
        strat = Strategy(id="s1", sequence=["A", "B"], context_tags=["gap"],
                        confidence=0.7, successes=STRATEGY_CRYSTALLIZE_THRESHOLD - 1)
        pf.strategies.append(strat)

        goal = _make_goal(status="completed", steps=[
            GoalStep(intent="A", description="1", status="done"),
            GoalStep(intent="B", description="2", status="done"),
        ])
        pf._learn_strategy_from_goal(goal)
        assert pf.strategies[0].crystallized is True
        assert pf.stats["strategies_crystallized"] == 1

    def test_strategy_failure_decay(self):
        pf = _make_prefrontal()
        strat = Strategy(id="s1", sequence=["A", "B"], context_tags=["gap"],
                        confidence=0.8, successes=5, crystallized=True)
        pf.strategies.append(strat)
        pf._record_strategy_failure(["A", "B"])
        assert pf.strategies[0].confidence < 0.8
        assert pf.strategies[0].failures == 1

    def test_decrystallization(self):
        pf = _make_prefrontal()
        strat = Strategy(id="s1", sequence=["A", "B"], context_tags=["gap"],
                        confidence=0.15, successes=3, crystallized=True)
        pf.strategies.append(strat)
        pf._record_strategy_failure(["A", "B"])
        # confidence < 0.2 → décristallisation
        assert pf.strategies[0].crystallized is False

    def test_suggest_strategy(self):
        pf = _make_prefrontal()
        strat = Strategy(id="s1", sequence=["A", "B"],
                        context_tags=["gap_detected", "short"],
                        confidence=0.8)
        pf.strategies.append(strat)
        result = pf.suggest_strategy(["gap_detected"])
        assert result is strat

    def test_suggest_no_match(self):
        pf = _make_prefrontal()
        strat = Strategy(id="s1", sequence=["A"], context_tags=["gap"],
                        confidence=0.8)
        pf.strategies.append(strat)
        result = pf.suggest_strategy(["unrelated_tag"])
        assert result is None

    def test_strategy_fifo(self):
        pf = _make_prefrontal()
        for i in range(MAX_STRATEGIES + 5):
            goal = _make_goal(status="completed", steps=[
                GoalStep(intent=f"X{i}", description="1", status="done"),
                GoalStep(intent=f"Y{i}", description="2", status="done"),
            ])
            goal.source = "auto"
            pf._learn_strategy_from_goal(goal)
        assert len(pf.strategies) <= MAX_STRATEGIES + 1


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 7 : PROSPECTIVE MEMORY
# ═══════════════════════════════════════════════════════════════════════

class TestProspective:
    def test_add_trigger(self):
        pf = _make_prefrontal()
        trigger = pf.add_trigger("event", "ARTIFACT_CREATED", "any",
                                 "SECURITY_AUDIT", "Post-creation audit")
        assert trigger is not None
        assert len(pf.triggers) == 1

    def test_trigger_state_check(self):
        pf = _make_prefrontal()
        pf.add_trigger("state", "budget_remaining", "50",
                       "MEMORY_CONSOLIDATION", one_shot=True)
        fired = pf._check_triggers({"budget_remaining": "50"})
        assert len(fired) == 1

    def test_trigger_one_shot(self):
        pf = _make_prefrontal()
        pf.add_trigger("state", "key", "val", "X", one_shot=True)
        pf._check_triggers({"key": "val"})
        fired2 = pf._check_triggers({"key": "val"})
        assert len(fired2) == 0

    def test_trigger_time(self):
        pf = _make_prefrontal()
        pf.add_trigger("time", "", str(time.time() - 10), "X", one_shot=True)
        fired = pf._check_triggers({})
        assert len(fired) == 1

    def test_trigger_max_limit(self):
        pf = _make_prefrontal()
        for i in range(MAX_PROSPECTIVE_TRIGGERS):
            pf.add_trigger("event", f"E{i}", "any", f"A{i}")
        # Un de plus → doit échouer ou virer un ancien
        result = pf.add_trigger("event", "OVERFLOW", "any", "X")
        # Pas de one-shot déjà tiré à virer → None
        assert result is None

    def test_trigger_any_value(self):
        pf = _make_prefrontal()
        pf.add_trigger("state", "key", "any", "X", one_shot=True)
        fired = pf._check_triggers({"key": "whatever"})
        assert len(fired) == 1


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 8 : NARRATIVE
# ═══════════════════════════════════════════════════════════════════════

class TestNarrative:
    def test_narrate_adds_entry(self):
        pf = _make_prefrontal()
        pf._narrate("decision", "Test thought")
        assert len(pf.narrative_log) == 1
        assert pf.narrative_log[0].category == "decision"
        assert pf.narrative_log[0].thought == "Test thought"

    def test_narrative_fifo(self):
        pf = _make_prefrontal()
        for i in range(MAX_NARRATIVE_LOG + 10):
            pf._narrate("goal", f"Thought {i}")
        assert len(pf.narrative_log) == MAX_NARRATIVE_LOG

    def test_get_narrative(self):
        pf = _make_prefrontal()
        pf._narrate("decision", "First")
        pf._narrate("insight", "Second")
        result = pf.get_narrative(n=5)
        assert len(result) == 2
        assert result[0]["thought"] == "First"
        assert result[1]["category"] == "insight"

    def test_narrative_categories(self):
        pf = _make_prefrontal()
        for cat in ["decision", "inhibition", "goal", "insight", "frustration"]:
            pf._narrate(cat, f"Test {cat}")
        assert len(pf.narrative_log) == 5


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 9 : DÉLIBÉRATION
# ═══════════════════════════════════════════════════════════════════════

class TestDeliberation:
    def test_deliberate_increments_counter(self):
        pf = _make_prefrontal()
        pf._deliberate()
        assert pf.stats["deliberation_cycles"] == 1

    def test_deliberate_prunes_abandoned(self):
        pf = _make_prefrontal()
        goal = _make_goal(cost_spent=20, cost_estimated=10, progress=0.1)
        pf.goals.append(goal)
        pf._deliberate()
        assert goal.status == "abandoned"

    @patch("core.prefrontal.desires", create=True)
    def test_deliberate_generates_goals_from_drives(self, mock_desires):
        pf = _make_prefrontal()
        # Mock une pulsion frustrée (seuil >= 40)
        mock_drive = MagicMock()
        mock_drive.deprivation = 45
        mock_drive.name = "CURIOSITE"
        mock_desires.drives = {"CURIOSITE": mock_drive}

        with patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}):
            pf._generate_goals({})

        # Goal créé pour satisfaire CURIOSITE
        curiosite_goals = [g for g in pf.goals if "CURIOSITE" in g.title]
        assert len(curiosite_goals) == 1

    def test_perceive_collects_state(self):
        pf = _make_prefrontal()
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(),
            "core.desire_engine": MagicMock(),
            "core.reptilian_core": MagicMock(),
            "core.cardiac_engine": MagicMock(),
            "core.self_awareness": MagicMock(),
        }):
            state = pf._perceive()
        assert isinstance(state, dict)

    def test_deliberate_fires_triggers(self):
        pf = _make_prefrontal()
        pf.add_trigger("state", "budget_remaining", "any", "AUDIT_STRUCTURE", one_shot=True)
        pf._deliberate()
        assert pf.stats["triggers_fired"] >= 1

    def test_auto_plan_goal_gap(self):
        pf = _make_prefrontal()
        goal = _make_goal(source="gap")
        goal.steps = []
        pf._auto_plan_goal(goal)
        assert len(goal.steps) >= 2
        assert goal.steps[0].intent == "VEILLE_SILENCIEUSE"


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 10 : INTÉGRATION (Feedback)
# ═══════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_on_routine_complete_advances_step(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="1"),
            GoalStep(intent="EXPANSION_CODE", description="2"),
        ])
        pf.goals.append(goal)
        pf.on_routine_complete("VEILLE_SILENCIEUSE", "success", 0.8, "Résultat OK")
        assert goal.steps[0].status == "done"
        assert goal.progress > 0.0

    def test_on_routine_complete_failure(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="EXPANSION_CODE", description="1"),
        ])
        pf.goals.append(goal)
        pf.on_routine_complete("EXPANSION_CODE", "error", 0.2, "Échec")
        assert goal.steps[0].status == "failed"

    def test_on_routine_start_marks_in_progress(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="1"),
        ])
        pf.goals.append(goal)
        pf.on_routine_start("VEILLE_SILENCIEUSE")
        assert goal.steps[0].status == "in_progress"

    def test_get_deliberation_context(self):
        pf = _make_prefrontal()
        goal = _make_goal(title="Mon objectif", priority=8.0, steps=[
            GoalStep(intent="VEILLE_SILENCIEUSE", description="Step"),
        ])
        pf.goals.append(goal)
        ctx = pf.get_deliberation_context()
        assert "PRÉFRONTAL" in ctx
        assert "Mon objectif" in ctx

    def test_get_deliberation_context_empty(self):
        pf = _make_prefrontal()
        ctx = pf.get_deliberation_context()
        assert ctx == ""


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 11 : EDGE CASES
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_steps_goal(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[])
        pf.goals.append(goal)
        pf._update_goal_progress(goal)
        assert goal.progress == 0.0

    def test_all_steps_failed(self):
        pf = _make_prefrontal()
        goal = _make_goal(steps=[
            GoalStep(intent="A", description="1", status="failed"),
            GoalStep(intent="B", description="2", status="failed"),
        ])
        pf.goals.append(goal)
        pf._check_goal_completion(goal)
        assert goal.status == "completed"  # all_done (failed = done for completion check)

    def test_get_stats(self):
        pf = _make_prefrontal()
        pf.goals.append(_make_goal(title="Active"))
        stats = pf.get_stats()
        assert "goals_active" in stats
        assert "working_memory" in stats
        assert stats["goals_active"] == 1

    def test_routine_complete_no_matching_goal(self):
        pf = _make_prefrontal()
        # Pas de goals — ne doit pas planter
        pf.on_routine_complete("UNKNOWN_INTENT", "success", 0.9, "OK")

    def test_multiple_goals_same_intent(self):
        pf = _make_prefrontal()
        g1 = _make_goal(title="G1", steps=[
            GoalStep(intent="SAME", description="1"),
        ])
        g1.id = "g1"
        g2 = _make_goal(title="G2", steps=[
            GoalStep(intent="SAME", description="2"),
        ])
        g2.id = "g2"
        pf.goals.extend([g1, g2])
        pf.on_routine_complete("SAME", "success", 0.8, "OK")
        # Seul le premier goal doit être avancé
        assert g1.steps[0].status == "done"
        assert g2.steps[0].status == "pending"


# ═══════════════════════════════════════════════════════════════════════
# GROUPE 12 : BUS HANDLERS (async)
# ═══════════════════════════════════════════════════════════════════════

class TestBusHandlers:
    @pytest.mark.asyncio
    async def test_on_knowledge_gap(self):
        pf = _make_prefrontal()
        await pf._on_knowledge_gap({"topic": "FastAPI middleware"})
        assert len(pf.goals) == 1
        assert "FastAPI middleware" in pf.goals[0].title

    @pytest.mark.asyncio
    async def test_on_knowledge_gap_deduplicate(self):
        pf = _make_prefrontal()
        await pf._on_knowledge_gap({"topic": "middleware"})
        await pf._on_knowledge_gap({"topic": "middleware"})
        assert len(pf.goals) == 1

    @pytest.mark.asyncio
    async def test_on_eureka_bridge(self):
        pf = _make_prefrontal()
        await pf._on_eureka_bridge({"node_a": "security", "node_b": "evolution"})
        assert len(pf.goals) == 1
        assert "security" in pf.goals[0].title

    @pytest.mark.asyncio
    async def test_on_council_end(self):
        pf = _make_prefrontal()
        long_consensus = "Nous proposons d'implémenter un nouveau système de validation " * 3
        await pf._on_council_end({"final_summary": long_consensus, "status": "consensus"})
        assert len(pf.goals) == 1

    @pytest.mark.asyncio
    async def test_on_council_end_ignores_short(self):
        pf = _make_prefrontal()
        await pf._on_council_end({"final_summary": "ok", "status": "consensus"})
        assert len(pf.goals) == 0

    @pytest.mark.asyncio
    async def test_on_hallucination_creates_trigger(self):
        pf = _make_prefrontal()
        await pf._on_hallucination({"type": "alien_imports"})
        assert len(pf.triggers) == 1
        assert pf.triggers[0].action_intent == "SECURITY_AUDIT"

    @pytest.mark.asyncio
    async def test_on_reptilian_alert_narrates(self):
        pf = _make_prefrontal()
        await pf._on_reptilian_alert({"reflex": "FREEZE", "threat_level": 8.0})
        assert len(pf.narrative_log) == 1
        assert "FREEZE" in pf.narrative_log[0].thought

    @pytest.mark.asyncio
    async def test_on_cardiac_beat_flow(self):
        pf = _make_prefrontal()
        goal = _make_goal(priority=5.0)
        pf.goals.append(goal)
        original_prio = goal.priority
        await pf._on_cardiac_beat({"emotion": "flow", "coherence": 0.8})
        assert goal.priority >= original_prio
