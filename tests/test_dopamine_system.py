# tests/test_dopamine_system.py — Tests DopamineSystem
# 9 classes, ~55 tests

import pytest
import asyncio
import json
import os
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture(autouse=True)
def isolate_dopamine(tmp_path, monkeypatch):
    """Isole le singleton DopamineSystem pour chaque test."""
    from core import dopamine_system as mod
    mod.DopamineSystem.reset_singleton()
    monkeypatch.setattr(mod, "DOPAMINE_STATE_FILE", str(tmp_path / "dopamine_state.json"))
    with patch.object(mod.DopamineSystem, "_load"):
        d = mod.DopamineSystem()
        d.reset()
        mod.dopamine = d
    yield d
    mod.DopamineSystem.reset_singleton()


# ============================================================
# 1. TestSingleton
# ============================================================

class TestSingleton:
    """Verifie le pattern singleton et le reset."""

    def test_singleton_identity(self, isolate_dopamine):
        from core.dopamine_system import DopamineSystem
        d2 = DopamineSystem()
        assert d2 is isolate_dopamine

    def test_reset_singleton(self, isolate_dopamine):
        from core import dopamine_system as mod
        mod.DopamineSystem.reset_singleton()
        assert mod.DopamineSystem._instance is None

    def test_initial_state(self, isolate_dopamine):
        d = isolate_dopamine
        assert d.dopamine_level == 0.5
        assert d.memories == {}
        assert d._tick_count == 0

    def test_reset_clears_state(self, isolate_dopamine):
        d = isolate_dopamine
        d.dopamine_level = 0.9
        d.memories["test"] = MagicMock()
        d._tick_count = 42
        d.reset()
        assert d.dopamine_level == 0.5
        assert d.memories == {}
        assert d._tick_count == 0


# ============================================================
# 2. TestRPE
# ============================================================

class TestRPE:
    """Verifie le calcul du Reward Prediction Error."""

    def test_positive_surprise(self, isolate_dopamine):
        """Qualite > predite → RPE positif."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test_intent"] = RewardMemory(expected_reward=0.3)
        rpe = d.compute_rpe("test_intent", 0.8)
        assert rpe > 0

    def test_negative_surprise(self, isolate_dopamine):
        """Qualite < predite → RPE negatif."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test_intent"] = RewardMemory(expected_reward=0.8)
        rpe = d.compute_rpe("test_intent", 0.2)
        assert rpe < 0

    def test_zero_rpe_when_expected(self, isolate_dopamine):
        """Qualite == predite → RPE ~0."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test_intent"] = RewardMemory(expected_reward=0.5)
        rpe = d.compute_rpe("test_intent", 0.5)
        assert abs(rpe) < 0.1

    def test_rpe_clamp(self, isolate_dopamine):
        """RPE est borne a [-1, +1]."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["low"] = RewardMemory(expected_reward=0.0)
        rpe_high = d.compute_rpe("low", 5.0)  # Exagere
        assert rpe_high <= 1.0

        d.memories["high"] = RewardMemory(expected_reward=1.0)
        rpe_low = d.compute_rpe("high", 0.0)
        assert rpe_low >= -1.0

    def test_novelty_bonus_first_intent(self, isolate_dopamine):
        """Un intent jamais vu recoit le bonus novelty."""
        d = isolate_dopamine
        rpe_novel = d.compute_rpe("never_seen", 0.6)
        # Avec novelty 1.5 : effective = 0.6 * 1.5 = 0.9, predicted = 0.5 → RPE = 0.4
        assert rpe_novel > 0.3

    def test_habituation_not_in_rpe(self, isolate_dopamine):
        """Habituation ne modifie PAS le RPE (fix: divergence expected_reward)."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        mem = RewardMemory(expected_reward=0.5)
        mem.reward_history = [0.5] * 10  # Variance = 0
        d.memories["boring"] = mem
        rpe = d.compute_rpe("boring", 0.6)
        # Sans habituation: effective = 0.6, predicted = 0.5, RPE = 0.1
        assert rpe == pytest.approx(0.1, abs=0.05)

    def test_habituation_high_variance(self, isolate_dopamine):
        """Resultats varies → pas d'habituation."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        mem = RewardMemory(expected_reward=0.5)
        mem.reward_history = [0.1, 0.9, 0.2, 0.8, 0.3, 0.7, 0.15, 0.85, 0.25, 0.75]
        d.memories["varied"] = mem
        rpe = d.compute_rpe("varied", 0.8)
        # Haute variance → habituation ≈ 1.0 → RPE normal
        assert rpe > 0.2

    def test_contextual_prediction(self, isolate_dopamine):
        """La prediction tient compte du bucket temporel."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, _get_time_bucket
        mem = RewardMemory(expected_reward=0.5)
        bucket = _get_time_bucket()
        mem.success_by_bucket[bucket] = [0.9, 0.85, 0.92]
        prediction = d._get_contextual_prediction(mem)
        # 0.5 * 0.7 + 0.89 * 0.3 ≈ 0.617
        assert prediction > 0.5


# ============================================================
# 3. TestTDLearning
# ============================================================

class TestTDLearning:
    """Verifie la mise a jour des valeurs par TD-learning."""

    def test_value_increases_positive_rpe(self, isolate_dopamine):
        """RPE positif → V monte."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test"] = RewardMemory(expected_reward=0.3)
        old_v = d.memories["test"].expected_reward
        d._update_value("test", 0.8, 0.5)
        assert d.memories["test"].expected_reward > old_v

    def test_value_decreases_negative_rpe(self, isolate_dopamine):
        """RPE negatif → V descend."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test"] = RewardMemory(expected_reward=0.8)
        old_v = d.memories["test"].expected_reward
        d._update_value("test", 0.2, -0.5)
        assert d.memories["test"].expected_reward < old_v

    def test_value_clamped(self, isolate_dopamine):
        """V reste dans [0, 1]."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["high"] = RewardMemory(expected_reward=0.99)
        d._update_value("high", 1.0, 1.0)
        assert d.memories["high"].expected_reward <= 1.0

        d.memories["low"] = RewardMemory(expected_reward=0.01)
        d._update_value("low", 0.0, -1.0)
        assert d.memories["low"].expected_reward >= 0.0

    def test_alpha_decay(self, isolate_dopamine):
        """Le learning rate decroit apres chaque observation."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, INITIAL_LEARNING_RATE
        d.memories["test"] = RewardMemory()
        old_alpha = d.memories["test"].learning_rate
        d._update_value("test", 0.6, 0.1)
        assert d.memories["test"].learning_rate < old_alpha

    def test_alpha_floor(self, isolate_dopamine):
        """Le learning rate ne descend pas sous MIN_LEARNING_RATE."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, MIN_LEARNING_RATE
        d.memories["test"] = RewardMemory(learning_rate=MIN_LEARNING_RATE)
        d._update_value("test", 0.6, 0.1)
        assert d.memories["test"].learning_rate >= MIN_LEARNING_RATE

    def test_observation_count_increments(self, isolate_dopamine):
        """Le compteur d'observations augmente."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test"] = RewardMemory()
        d._update_value("test", 0.6, 0.1)
        assert d.memories["test"].observation_count == 1
        d._update_value("test", 0.7, 0.2)
        assert d.memories["test"].observation_count == 2


# ============================================================
# 4. TestDopamineLevel
# ============================================================

class TestDopamineLevel:
    """Verifie la gestion du niveau de dopamine."""

    def test_surge_on_positive_rpe(self, isolate_dopamine):
        """RPE positif → dopamine monte."""
        d = isolate_dopamine
        old = d.dopamine_level
        d._update_dopamine_level(0.5)
        assert d.dopamine_level > old

    def test_dip_on_negative_rpe(self, isolate_dopamine):
        """RPE negatif → dopamine descend."""
        d = isolate_dopamine
        old = d.dopamine_level
        d._update_dopamine_level(-0.5)
        assert d.dopamine_level < old

    def test_clamp_max(self, isolate_dopamine):
        """Dopamine ne depasse pas MAX_DOPAMINE."""
        d = isolate_dopamine
        d.dopamine_level = 0.95
        d._update_dopamine_level(1.0)
        assert d.dopamine_level <= 1.0

    def test_clamp_min(self, isolate_dopamine):
        """Dopamine ne descend pas sous MIN_DOPAMINE."""
        d = isolate_dopamine
        d.dopamine_level = 0.1
        d._update_dopamine_level(-1.0)
        assert d.dopamine_level >= 0.05

    def test_decay_above_baseline(self, isolate_dopamine):
        """Decay ramene vers baseline quand au-dessus."""
        d = isolate_dopamine
        d.dopamine_level = 0.8
        d._decay_dopamine()
        assert d.dopamine_level < 0.8
        assert d.dopamine_level > 0.5

    def test_decay_below_baseline(self, isolate_dopamine):
        """Decay ramene vers baseline quand en-dessous."""
        d = isolate_dopamine
        d.dopamine_level = 0.2
        d._decay_dopamine()
        assert d.dopamine_level > 0.2
        assert d.dopamine_level < 0.5

    def test_snap_to_baseline(self, isolate_dopamine):
        """Tres proche de baseline → snap exact."""
        d = isolate_dopamine
        d.dopamine_level = 0.503
        d._decay_dopamine()
        assert d.dopamine_level == 0.5


# ============================================================
# 5. TestMotivationBonus
# ============================================================

class TestMotivationBonus:
    """Verifie le calcul du bonus de motivation."""

    def test_unknown_intent_small_bonus(self, isolate_dopamine):
        """Intent inconnu → petit bonus exploration."""
        d = isolate_dopamine
        bonus = d.compute_motivation_bonus("never_seen")
        assert bonus > 0
        assert bonus < 2.0

    def test_high_value_intent_positive_bonus(self, isolate_dopamine):
        """Intent a haute valeur → bonus positif."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["good"] = RewardMemory(expected_reward=0.9)
        bonus = d.compute_motivation_bonus("good")
        assert bonus > 0

    def test_low_value_intent_negative_bonus(self, isolate_dopamine):
        """Intent a basse valeur → bonus negatif."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["bad"] = RewardMemory(expected_reward=0.1)
        bonus = d.compute_motivation_bonus("bad")
        assert bonus < 0

    def test_bonus_clamped_max(self, isolate_dopamine):
        """Bonus ne depasse pas MOTIVATION_BONUS_MAX."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, MOTIVATION_BONUS_MAX
        d.memories["perfect"] = RewardMemory(expected_reward=1.0)
        d.dopamine_level = 1.0  # Amplification max
        bonus = d.compute_motivation_bonus("perfect")
        assert bonus <= MOTIVATION_BONUS_MAX

    def test_bonus_clamped_min(self, isolate_dopamine):
        """Bonus ne descend pas sous MOTIVATION_BONUS_MIN."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, MOTIVATION_BONUS_MIN
        d.memories["terrible"] = RewardMemory(expected_reward=0.0)
        d.dopamine_level = 0.05
        bonus = d.compute_motivation_bonus("terrible")
        assert bonus >= MOTIVATION_BONUS_MIN

    def test_dopamine_amplifies_bonus(self, isolate_dopamine):
        """Dopamine haute amplifie le bonus."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test"] = RewardMemory(expected_reward=0.7)

        d.dopamine_level = 0.5
        bonus_normal = d.compute_motivation_bonus("test")

        d.dopamine_level = 0.9
        bonus_high = d.compute_motivation_bonus("test")

        assert bonus_high > bonus_normal

    def test_dopamine_attenuates_bonus(self, isolate_dopamine):
        """Dopamine basse attenue le bonus."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["test"] = RewardMemory(expected_reward=0.7)

        d.dopamine_level = 0.5
        bonus_normal = d.compute_motivation_bonus("test")

        d.dopamine_level = 0.1
        bonus_low = d.compute_motivation_bonus("test")

        assert bonus_low < bonus_normal

    def test_repetition_penalty(self, isolate_dopamine):
        """Intent trop repete → penalite anti-addiction."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["addict"] = RewardMemory(expected_reward=0.8)

        bonus_fresh = d.compute_motivation_bonus("addict")

        # Simuler 7 repetitions dans les 10 dernieres
        for _ in range(7):
            d._recent_intents.append("addict")

        bonus_repeated = d.compute_motivation_bonus("addict")
        assert bonus_repeated < bonus_fresh


# ============================================================
# 6. TestHabituation
# ============================================================

class TestHabituation:
    """Verifie le calcul du facteur d'habituation."""

    def test_few_samples_no_habituation(self, isolate_dopamine):
        """Moins de 3 observations → pas d'habituation."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        mem = RewardMemory()
        mem.reward_history = [0.5, 0.5]
        assert d._compute_habituation_factor(mem) == 1.0

    def test_constant_results_strong_habituation(self, isolate_dopamine):
        """Resultats identiques → habituation maximale."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, MIN_HABITUATION_FACTOR
        mem = RewardMemory()
        mem.reward_history = [0.5] * 10
        factor = d._compute_habituation_factor(mem)
        assert factor == MIN_HABITUATION_FACTOR

    def test_varied_results_no_habituation(self, isolate_dopamine):
        """Resultats tres varies → pas d'habituation."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        mem = RewardMemory()
        mem.reward_history = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        factor = d._compute_habituation_factor(mem)
        assert factor >= 0.9

    def test_habituation_bounded(self, isolate_dopamine):
        """Facteur d'habituation reste dans [MIN, 1.0]."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, MIN_HABITUATION_FACTOR
        mem = RewardMemory()
        mem.reward_history = [0.5] * 20
        factor = d._compute_habituation_factor(mem)
        assert factor >= MIN_HABITUATION_FACTOR
        assert factor <= 1.0

    def test_habituation_sliding_window(self, isolate_dopamine):
        """Utilise les 10 derniers resultats seulement."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        mem = RewardMemory()
        # 20 resultats identiques puis 10 varies
        mem.reward_history = [0.5] * 20 + [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        factor = d._compute_habituation_factor(mem)
        # Les 10 derniers sont varies → facteur haut
        assert factor >= 0.9


# ============================================================
# 7. TestEventHandlers
# ============================================================

class TestEventHandlers:
    """Verifie les handlers d'evenements du bus."""

    @pytest.mark.asyncio
    async def test_routine_success(self, isolate_dopamine):
        """Routine reussie avec bonne qualite → dopamine monte."""
        d = isolate_dopamine
        old = d.dopamine_level
        await d._on_routine_complete({
            "intent": "EXPANSION_CODE",
            "quality_score": 0.9,
            "status": "success",
        })
        # First time (novelty) → big RPE → surge
        assert d.dopamine_level > old

    @pytest.mark.asyncio
    async def test_routine_failure(self, isolate_dopamine):
        """Routine echouee → dopamine descend (apres apprentissage)."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        # Pre-entrainer avec des succes → V elevee
        d.memories["fail_test"] = RewardMemory(expected_reward=0.8)
        d.memories["fail_test"].reward_history = [0.8, 0.7, 0.9]
        old = d.dopamine_level
        await d._on_routine_complete({
            "intent": "fail_test",
            "quality_score": 0.1,
            "status": "error",
        })
        assert d.dopamine_level < old

    @pytest.mark.asyncio
    async def test_cardiac_tick_decays(self, isolate_dopamine):
        """CARDIAC_BEAT → decay vers baseline."""
        d = isolate_dopamine
        d.dopamine_level = 0.8
        await d._on_cardiac_beat({})
        assert d.dopamine_level < 0.8

    @pytest.mark.asyncio
    async def test_eureka_surge(self, isolate_dopamine):
        """EUREKA_BRIDGE → surge +0.2."""
        d = isolate_dopamine
        old = d.dopamine_level
        await d._on_eureka_bridge({})
        assert d.dopamine_level == old + 0.2

    @pytest.mark.asyncio
    async def test_artifact_surge(self, isolate_dopamine):
        """ARTIFACT_CREATED → mini-surge +0.1."""
        d = isolate_dopamine
        old = d.dopamine_level
        await d._on_artifact_created({})
        assert d.dopamine_level == old + 0.1

    @pytest.mark.asyncio
    async def test_hallucination_dip(self, isolate_dopamine):
        """HALLUCINATION_DETECTED → dip -0.15."""
        d = isolate_dopamine
        old = d.dopamine_level
        await d._on_hallucination({})
        assert d.dopamine_level == old - 0.15

    @pytest.mark.asyncio
    async def test_council_end_surge(self, isolate_dopamine):
        """COUNCIL_END → mini-surge +0.08."""
        d = isolate_dopamine
        old = d.dopamine_level
        await d._on_council_end({})
        assert d.dopamine_level == old + 0.08

    @pytest.mark.asyncio
    async def test_routine_publishes_surge_event(self, isolate_dopamine):
        """RPE significatif → publie DOPAMINE_SURGE."""
        d = isolate_dopamine
        published = []
        with patch("core.dopamine_system.bus") as mock_bus:
            mock_bus.publish = MagicMock(side_effect=lambda *a, **kw: published.append(a))
            await d._on_routine_complete({
                "intent": "NOVEL_INTENT",
                "quality_score": 0.95,
                "status": "success",
            })
        surge_events = [p for p in published if p[0] == "DOPAMINE_SURGE"]
        assert len(surge_events) >= 1


# ============================================================
# 8. TestPersistence
# ============================================================

class TestPersistence:
    """Verifie la persistance de l'etat."""

    def test_save_creates_file(self, isolate_dopamine, tmp_path):
        """La sauvegarde cree le fichier."""
        from core import dopamine_system as mod
        d = isolate_dopamine
        d._save()
        assert os.path.exists(mod.DOPAMINE_STATE_FILE)

    def test_roundtrip(self, isolate_dopamine, tmp_path):
        """Save + load preservent l'etat."""
        from core import dopamine_system as mod
        from core.dopamine_system import RewardMemory
        d = isolate_dopamine
        d.dopamine_level = 0.75
        d.memories["test_rt"] = RewardMemory(expected_reward=0.8, observation_count=5)
        d._save()

        # Creer une nouvelle instance qui va charger
        mod.DopamineSystem.reset_singleton()
        with patch.object(mod.DopamineSystem, "_subscribe_events"):
            d2 = mod.DopamineSystem()
            d2._initialized = False
            d2.__init__()
        assert d2.dopamine_level == 0.75
        assert "test_rt" in d2.memories
        assert d2.memories["test_rt"].expected_reward == 0.8

    def test_load_missing_file(self, isolate_dopamine, tmp_path):
        """Fichier absent → etat par defaut, pas de crash."""
        from core import dopamine_system as mod
        d = isolate_dopamine
        # Pas de fichier → rien ne crash
        d._load()  # Ne devrait pas lever d'exception

    def test_load_corrupt_file(self, isolate_dopamine, tmp_path):
        """Fichier corrompu → warning, pas de crash."""
        from core import dopamine_system as mod
        with open(mod.DOPAMINE_STATE_FILE, "w") as f:
            f.write("NOT JSON {{{")
        d = isolate_dopamine
        d._load()  # Ne devrait pas lever d'exception

    def test_memories_persisted(self, isolate_dopamine, tmp_path):
        """Les memories sont bien dans le fichier JSON."""
        from core import dopamine_system as mod
        from core.dopamine_system import RewardMemory
        d = isolate_dopamine
        d.memories["persist_test"] = RewardMemory(
            expected_reward=0.65,
            observation_count=3,
            reward_history=[0.6, 0.7, 0.65],
        )
        d._save()
        with open(mod.DOPAMINE_STATE_FILE, "r") as f:
            data = json.load(f)
        assert "persist_test" in data["memories"]
        assert data["memories"]["persist_test"]["expected_reward"] == 0.65


# ============================================================
# 9. TestUtilities
# ============================================================

class TestUtilities:
    """Verifie les methodes utilitaires."""

    def test_get_expected_reward_known(self, isolate_dopamine):
        """Intent connu → retourne V(intent)."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["known"] = RewardMemory(expected_reward=0.7)
        assert d.get_expected_reward("known") == 0.7

    def test_get_expected_reward_unknown(self, isolate_dopamine):
        """Intent inconnu → retourne BASELINE."""
        d = isolate_dopamine
        assert d.get_expected_reward("unknown") == 0.5

    def test_get_stats(self, isolate_dopamine):
        """get_stats retourne un dict avec les cles attendues."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        d.memories["s1"] = RewardMemory(expected_reward=0.9, observation_count=10)
        d.memories["s2"] = RewardMemory(expected_reward=0.3, observation_count=5)
        stats = d.get_stats()
        assert "dopamine_level" in stats
        assert "intents_known" in stats
        assert stats["intents_known"] == 2
        assert "total_observations" in stats
        assert stats["total_observations"] == 15
        assert "top_rewarding" in stats

    def test_get_narrative(self, isolate_dopamine):
        """get_narrative retourne une phrase non-vide."""
        d = isolate_dopamine
        assert isinstance(d.get_narrative(), str)
        assert len(d.get_narrative()) > 10

        d.dopamine_level = 0.9
        narrative_high = d.get_narrative()
        d.dopamine_level = 0.1
        narrative_low = d.get_narrative()
        assert narrative_high != narrative_low


# ============================================================
# 10. TestHabituationSeparation — Fix divergence expected_reward
# ============================================================

class TestHabituationSeparation:
    """Verifie que l'habituation module la dopamine mais PAS le TD-learning."""

    def test_convergence_consistent_success(self, isolate_dopamine):
        """12 succes constants → expected_reward converge vers ~0.9, pas vers 0."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        mem = RewardMemory(expected_reward=0.5)
        d.memories["CONSISTENT"] = mem

        for _ in range(12):
            rpe = d.compute_rpe("CONSISTENT", 1.0)
            d._update_value("CONSISTENT", 1.0, rpe)

        # Avec le fix, expected_reward converge vers ~0.94
        # Sans le fix (bug), il divergeait vers 0.088
        assert d.memories["CONSISTENT"].expected_reward > 0.85

    def test_habituation_still_dampens_dopamine(self, isolate_dopamine):
        """L'habituation modere le changement de dopamine (pas le learning)."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory, MIN_HABITUATION_FACTOR

        # Intent avec historique constant (variance=0 → habituation forte)
        mem_boring = RewardMemory(expected_reward=0.5)
        mem_boring.reward_history = [1.0] * 10
        d.memories["boring"] = mem_boring

        habituation = d._compute_habituation_factor(mem_boring)
        assert habituation == pytest.approx(MIN_HABITUATION_FACTOR, abs=0.01)

        # RPE n'est PAS attenue
        rpe = d.compute_rpe("boring", 1.0)
        # effective=1.0, predicted~0.5+context → RPE positif et significatif
        assert rpe > 0.0

        # Mais la dopamine EST moderee par habituation
        old_level = d.dopamine_level
        d._update_dopamine_level(rpe * habituation)
        change_habituated = d.dopamine_level - old_level

        d.dopamine_level = old_level  # Reset
        d._update_dopamine_level(rpe)
        change_full = d.dopamine_level - old_level

        # Le changement habituee est plus petit
        assert change_habituated < change_full

    @pytest.mark.asyncio
    async def test_on_routine_applies_habituation_to_dopamine(self, isolate_dopamine):
        """_on_routine_complete applique l'habituation au changement de dopamine."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory

        # Intent sans historique (novelty → grosse montee)
        old_novel = d.dopamine_level
        await d._on_routine_complete({
            "intent": "NOVEL_INTENT",
            "quality_score": 1.0,
            "status": "success",
        })
        rise_novel = d.dopamine_level - old_novel

        # Meme intent, beaucoup de repetitions (habituation forte)
        d.memories["HABITUATED"] = RewardMemory(expected_reward=0.8)
        d.memories["HABITUATED"].reward_history = [0.8] * 10

        d.dopamine_level = old_novel  # Reset au meme point de depart
        await d._on_routine_complete({
            "intent": "HABITUATED",
            "quality_score": 1.0,
            "status": "success",
        })
        rise_habituated = d.dopamine_level - old_novel

        # La montee de dopamine est plus faible pour l'intent habituee
        assert rise_habituated < rise_novel

    def test_motivation_positive_after_consistent_success(self, isolate_dopamine):
        """Apres des succes constants, le motivation_bonus est positif (pas negatif)."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory

        mem = RewardMemory(expected_reward=0.5)
        d.memories["GOOD_INTENT"] = mem

        # 12 succes
        for _ in range(12):
            rpe = d.compute_rpe("GOOD_INTENT", 1.0)
            d._update_value("GOOD_INTENT", 1.0, rpe)

        bonus = d.compute_motivation_bonus("GOOD_INTENT")
        # expected_reward > 0.85 → v_centered > 0.35 → bonus > 1.0
        assert bonus > 1.0

    def test_no_divergence_for_mixed_success(self, isolate_dopamine):
        """Meme avec des echecs, expected_reward reste raisonnable."""
        d = isolate_dopamine
        from core.dopamine_system import RewardMemory
        mem = RewardMemory(expected_reward=0.5)
        d.memories["MIXED"] = mem

        results = [1.0, 0.3, 1.0, 0.3, 1.0, 1.0, 0.3, 1.0, 0.3, 1.0, 1.0, 0.3]
        for r in results:
            rpe = d.compute_rpe("MIXED", r)
            d._update_value("MIXED", r, rpe)

        ev = d.memories["MIXED"].expected_reward
        actual_avg = sum(results) / len(results)
        # expected_reward devrait etre proche de la moyenne reelle (0.725)
        assert 0.4 < ev < 1.0


class TestTissuePatternHandler:
    """Sprint 3 — Grand Câblage : handler TISSUE_PATTERN_EMERGED."""

    @pytest.mark.asyncio
    async def test_tissue_pattern_boosts_dopamine(self, isolate_dopamine):
        """Pattern fort → micro-surge dopamine."""
        isolate_dopamine.dopamine_level = 0.5
        await isolate_dopamine._on_tissue_pattern({"frequency": 0.5, "fitness": 0.1})
        assert isolate_dopamine.dopamine_level > 0.5

    @pytest.mark.asyncio
    async def test_tissue_pattern_low_freq_no_boost(self, isolate_dopamine):
        """Pattern faible freq → pas de boost."""
        isolate_dopamine.dopamine_level = 0.5
        await isolate_dopamine._on_tissue_pattern({"frequency": 0.2, "fitness": 0.1})
        assert isolate_dopamine.dopamine_level == 0.5

    @pytest.mark.asyncio
    async def test_tissue_pattern_low_fitness_no_boost(self, isolate_dopamine):
        """Pattern sans fitness → pas de boost."""
        isolate_dopamine.dopamine_level = 0.5
        await isolate_dopamine._on_tissue_pattern({"frequency": 0.5, "fitness": 0.0})
        assert isolate_dopamine.dopamine_level == 0.5

    @pytest.mark.asyncio
    async def test_tissue_pattern_capped(self, isolate_dopamine):
        """Boost dopamine ne depasse pas MAX_DOPAMINE."""
        isolate_dopamine.dopamine_level = 0.99
        await isolate_dopamine._on_tissue_pattern({"frequency": 0.8, "fitness": 0.5})
        assert isolate_dopamine.dopamine_level <= 1.0
