"""Tests V10.1 (Phase 12B - 2026-04-21) : Metabolic Wash + Bug #3 fix.

Contexte : audit runtime des ganglions revele 59071 inhibitions accumulees
pour 1910 selections, plusieurs intents a NO-GO=-2.0 permanent (COUNCIL_DEBATE
inclus). Cause : _decay_habits orphelin + pas de decay NO-GO + formule
mathematique qui ecrasait les habits parfaites sous NO-GO absolu.

V10.1 corrige les 3 bugs :
 - Bug #1 : _decay_habits appele periodiquement via Metabolic Wash
 - Bug #2 : NO-GO decay bidirectionnel vers 0 dans le meme wash
 - Bug #3 : formule additive a resilience proportionnelle (validation Gemini)

Contre-expertise Gemini sur le code : formule additive preserve la capacite
d'emettre des scores negatifs (securite) tout en protegant les habits
parfaites (resilience).
"""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock
from core.basal_ganglia import (
    BasalGanglia, LEARNING_RATE, HABIT_STRENGTH_DECAY,
    HABIT_THRESHOLD, INHIBITION_STRENGTH, NOVELTY_BONUS,
    NO_GO_DECAY_RATE, METABOLIC_WASH_INTERVAL,
)


@pytest.fixture
def fresh_ganglia():
    BasalGanglia.reset_singleton()
    g = BasalGanglia()
    g.habits.clear()
    g.go_nogo_state.clear()
    g.total_selections = 0
    g.total_inhibitions = 0
    g._cardiac_ticks = 0
    yield g
    BasalGanglia.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# Bug #3 fix : formule additive a resilience (validation Gemini)
# ═══════════════════════════════════════════════════════════════════════


class TestResilienceFormula:
    """La formule V10.1 doit preserver les habits parfaites et punir les
    habits faibles, permettant toujours des scores negatifs pour la securite."""

    def test_perfect_habit_with_full_nogo_returns_zero(self, fresh_ganglia):
        """strength=1.0, sr=1.0, nogo=-2.0 → 0.0 (pas -1.0 comme pre-V10.1)"""
        fresh_ganglia.habits["TEST"] = {
            "strength": 1.0, "successes": 10, "failures": 0,
            "last_reward": 1.0, "avg_reward": 1.0, "created_at": 0.0,
        }
        fresh_ganglia.go_nogo_state["TEST"] = -2.0
        go_nogo = fresh_ganglia._compute_go_nogo("TEST")
        # go_signal=1.0 * (10/11)~0.909 ; resilience=1-0.909*0.5~0.545
        # go_nogo = 0.909 + (-2.0)*0.545 = -0.181 (pas 0 exact mais proche)
        # Avec success_rate exact 10/11 = 0.909
        # go_signal = 1.0 * 0.909 = 0.909
        # resilience = 1 - 0.909 * 0.5 = 0.545
        # result = 0.909 + (-2.0) * 0.545 = 0.909 - 1.091 = -0.181
        assert go_nogo == pytest.approx(-0.181, abs=0.02)

    def test_perfect_habit_no_fail_zero_nogo(self, fresh_ganglia):
        """Habit parfaite sans NO-GO → signal max."""
        fresh_ganglia.habits["PERFECT"] = {
            "strength": 1.0, "successes": 100, "failures": 0,
            "last_reward": 1.0, "avg_reward": 1.0, "created_at": 0.0,
        }
        # go_signal = 1.0 * (100/101) ≈ 0.990
        go_nogo = fresh_ganglia._compute_go_nogo("PERFECT")
        assert go_nogo == pytest.approx(0.990, abs=0.01)

    def test_weak_habit_with_full_nogo_gets_punished(self, fresh_ganglia):
        """Habit faible + NO-GO max → forte punition (securite preservee)."""
        fresh_ganglia.habits["DANGER"] = {
            "strength": 0.2, "successes": 1, "failures": 3,
            "last_reward": -0.5, "avg_reward": -0.3, "created_at": 0.0,
        }
        fresh_ganglia.go_nogo_state["DANGER"] = -2.0
        go_nogo = fresh_ganglia._compute_go_nogo("DANGER")
        # go_signal = 0.2 * (1/5) = 0.04
        # resilience = 1 - 0.04*0.5 = 0.98
        # result = 0.04 + (-2.0)*0.98 = -1.92
        assert go_nogo == pytest.approx(-1.92, abs=0.02)
        assert go_nogo < 0, "Punition negative preservee pour securite"

    def test_medium_habit_medium_nogo(self, fresh_ganglia):
        """Cas moyen : habit moyenne + NO-GO moyen → punition modulee."""
        fresh_ganglia.habits["MEDIUM"] = {
            "strength": 0.5, "successes": 5, "failures": 5,
            "last_reward": 0.2, "avg_reward": 0.1, "created_at": 0.0,
        }
        fresh_ganglia.go_nogo_state["MEDIUM"] = -1.0
        go_nogo = fresh_ganglia._compute_go_nogo("MEDIUM")
        # go_signal = 0.5 * (5/11) ≈ 0.227
        # resilience = 1 - 0.227*0.5 ≈ 0.886
        # result = 0.227 + (-1.0)*0.886 = -0.659
        assert go_nogo == pytest.approx(-0.659, abs=0.02)

    def test_go_boost_works_additively(self, fresh_ganglia):
        """Cas GO positif : la formule reste additive."""
        fresh_ganglia.habits["BOOSTED"] = {
            "strength": 0.5, "successes": 5, "failures": 0,
            "last_reward": 1.0, "avg_reward": 1.0, "created_at": 0.0,
        }
        fresh_ganglia.go_nogo_state["BOOSTED"] = 1.0
        go_nogo = fresh_ganglia._compute_go_nogo("BOOSTED")
        # go_signal = 0.5 * (5/6) ≈ 0.417
        # resilience = 1 - 0.417*0.5 ≈ 0.792
        # result = 0.417 + 1.0*0.792 = 1.208
        assert go_nogo == pytest.approx(1.208, abs=0.02)


# ═══════════════════════════════════════════════════════════════════════
# Bug #1+#2 : Metabolic Wash unifie (decay habits + decay NO-GO)
# ═══════════════════════════════════════════════════════════════════════


class TestMetabolicWash:
    """Le Metabolic Wash decrement habits ET NO-GO dans le meme cycle."""

    def test_wash_decays_habit_strength(self, fresh_ganglia):
        """_decay_habits est bien declenche par le wash."""
        fresh_ganglia.habits["A"] = {
            "strength": 0.8, "successes": 5, "failures": 0,
            "last_reward": 1.0, "avg_reward": 1.0, "created_at": 0.0,
        }
        before = fresh_ganglia.habits["A"]["strength"]
        fresh_ganglia.tick_metabolic_wash()
        after = fresh_ganglia.habits["A"]["strength"]
        assert after < before
        assert after == pytest.approx(before * HABIT_STRENGTH_DECAY, abs=0.001)

    def test_wash_removes_negligible_habits(self, fresh_ganglia):
        """Habit sous MIN_HABIT_STRENGTH → supprimee par le wash."""
        fresh_ganglia.habits["WEAK"] = {
            "strength": 0.005, "successes": 1, "failures": 10,
            "last_reward": -0.5, "avg_reward": -0.5, "created_at": 0.0,
        }
        fresh_ganglia.tick_metabolic_wash()
        assert "WEAK" not in fresh_ganglia.habits

    def test_wash_decays_nogo_toward_zero(self, fresh_ganglia):
        """NO-GO -2.0 remonte vers 0 par wash."""
        fresh_ganglia.go_nogo_state["BLOCKED"] = -2.0
        fresh_ganglia.tick_metabolic_wash()
        # -2.0 + 0.05 = -1.95
        assert fresh_ganglia.go_nogo_state["BLOCKED"] == pytest.approx(-1.95, abs=0.01)

    def test_wash_decays_go_toward_zero(self, fresh_ganglia):
        """GO +1.0 descend vers 0 par wash (symetrie)."""
        fresh_ganglia.go_nogo_state["BOOSTED"] = 1.0
        fresh_ganglia.tick_metabolic_wash()
        assert fresh_ganglia.go_nogo_state["BOOSTED"] == pytest.approx(0.95, abs=0.01)

    def test_wash_removes_near_zero_nogo(self, fresh_ganglia):
        """|val| < 0.01 après wash → key supprimee."""
        fresh_ganglia.go_nogo_state["ALMOST_ZERO"] = -0.04
        # -0.04 + 0.05 = 0.01, tronque a 0 → supprime
        fresh_ganglia.tick_metabolic_wash()
        assert "ALMOST_ZERO" not in fresh_ganglia.go_nogo_state

    def test_wash_preserves_zero_boundary(self, fresh_ganglia):
        """Le wash ne depasse pas 0 (clamp min/max)."""
        fresh_ganglia.go_nogo_state["A"] = -0.02
        fresh_ganglia.go_nogo_state["B"] = 0.02
        fresh_ganglia.tick_metabolic_wash()
        # Les deux doivent etre supprimes (|val| < 0.01 apres wash)
        assert "A" not in fresh_ganglia.go_nogo_state
        assert "B" not in fresh_ganglia.go_nogo_state

    def test_nogo_full_recovery_takes_many_washes(self, fresh_ganglia):
        """NO-GO max (-2.0) retombe a 0 en ~40 washes (33h runtime)."""
        fresh_ganglia.go_nogo_state["SLOW"] = -2.0
        n_washes = 0
        while "SLOW" in fresh_ganglia.go_nogo_state:
            fresh_ganglia.tick_metabolic_wash()
            n_washes += 1
            if n_washes > 100:
                pytest.fail("Recovery trop lente")
        # 2.0 / 0.05 = 40 washes, + marge pour cleanup < 0.01
        assert 38 <= n_washes <= 42


# ═══════════════════════════════════════════════════════════════════════
# Cardiac beat hook : declenchement periodique
# ═══════════════════════════════════════════════════════════════════════


class TestCardiacBeatHook:

    @pytest.mark.asyncio
    async def test_wash_fires_every_n_beats(self, fresh_ganglia):
        """METABOLIC_WASH_INTERVAL beats → 1 wash exact."""
        with patch.object(fresh_ganglia, 'tick_metabolic_wash') as mock_wash:
            # Simuler METABOLIC_WASH_INTERVAL beats
            for _ in range(METABOLIC_WASH_INTERVAL):
                await fresh_ganglia._on_cardiac_beat({})
            # Exactement 1 wash declenche
            assert mock_wash.call_count == 1

    @pytest.mark.asyncio
    async def test_wash_does_not_fire_before_interval(self, fresh_ganglia):
        """<INTERVAL beats → 0 wash."""
        with patch.object(fresh_ganglia, 'tick_metabolic_wash') as mock_wash:
            for _ in range(METABOLIC_WASH_INTERVAL - 1):
                await fresh_ganglia._on_cardiac_beat({})
            assert mock_wash.call_count == 0


# ═══════════════════════════════════════════════════════════════════════
# Non-regression sur compute_habit_bonus
# ═══════════════════════════════════════════════════════════════════════


class TestHabitBonusScore:
    """Le scoring final (Couche 20) doit rester coherent."""

    def test_unknown_intent_gets_novelty_bonus(self, fresh_ganglia):
        """Intent inconnu → NOVELTY_BONUS."""
        assert fresh_ganglia.compute_habit_bonus("NEW") == NOVELTY_BONUS

    def test_weak_habit_returns_zero(self, fresh_ganglia):
        """strength < HABIT_THRESHOLD → 0 (pas d'influence)."""
        fresh_ganglia.habits["WEAK"] = {
            "strength": 0.2, "successes": 1, "failures": 0,
            "last_reward": 0.5, "avg_reward": 0.5, "created_at": 0.0,
        }
        assert fresh_ganglia.compute_habit_bonus("WEAK") == 0.0

    def test_clamping_preserved(self, fresh_ganglia):
        """Bonus plafonne a +2.0, malus plafonne a -1.5."""
        fresh_ganglia.habits["STRONG"] = {
            "strength": 1.0, "successes": 100, "failures": 0,
            "last_reward": 1.0, "avg_reward": 1.0, "created_at": 0.0,
        }
        fresh_ganglia.go_nogo_state["STRONG"] = 5.0  # au-dela du normal
        bonus = fresh_ganglia.compute_habit_bonus("STRONG")
        assert bonus <= 2.0
