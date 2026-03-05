# tests/test_thalamus.py — Tests Thalamus : Relais Sensoriel
# ~20 tests couvrant singleton, scorecard, handlers, cycle, persistance

import pytest
import asyncio
import json
import os
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture(autouse=True)
def isolate_thalamus(tmp_path, monkeypatch):
    """Isole le singleton Thalamus pour chaque test."""
    from core import thalamus as mod
    mod.Thalamus.reset_singleton()
    monkeypatch.setattr(mod, "THALAMUS_STATE_FILE", str(tmp_path / "thalamus_state.json"))
    with patch.object(mod.Thalamus, "_load"):
        t = mod.Thalamus()
        mod.thalamus = t
    yield t
    mod.Thalamus.reset_singleton()


# ============================================================
# 1. TestSingleton
# ============================================================

class TestSingleton:

    def test_singleton_identity(self, isolate_thalamus):
        from core.thalamus import Thalamus
        t2 = Thalamus()
        assert t2 is isolate_thalamus

    def test_reset_singleton(self, isolate_thalamus):
        from core import thalamus as mod
        mod.Thalamus.reset_singleton()
        assert mod.Thalamus._instance is None


# ============================================================
# 2. TestScorecard
# ============================================================

class TestScorecard:

    def test_initial_scorecard_has_15_entries(self, isolate_thalamus):
        t = isolate_thalamus
        assert len(t._scorecard) == 15

    def test_initial_scorecard_all_05(self, isolate_thalamus):
        t = isolate_thalamus
        for val in t._scorecard.values():
            assert val == 0.5

    def test_get_salience_known(self, isolate_thalamus):
        t = isolate_thalamus
        t._scorecard["REPTILIAN_ALERT"] = 0.8
        assert t.get_salience("REPTILIAN_ALERT") == 0.8

    def test_get_salience_unknown(self, isolate_thalamus):
        t = isolate_thalamus
        assert t.get_salience("TOTALLY_UNKNOWN_EVENT") == 0.5


# ============================================================
# 3. TestSalienceThreshold
# ============================================================

class TestSalienceThreshold:

    def test_is_salient_above_threshold(self, isolate_thalamus):
        t = isolate_thalamus
        t._threshold = 0.3
        t._scorecard["DOPAMINE_SURGE"] = 0.6
        assert t.is_salient("DOPAMINE_SURGE") is True

    def test_is_salient_below_threshold(self, isolate_thalamus):
        t = isolate_thalamus
        t._threshold = 0.7
        t._scorecard["DOPAMINE_SURGE"] = 0.4
        assert t.is_salient("DOPAMINE_SURGE") is False

    def test_is_salient_equal_threshold(self, isolate_thalamus):
        """Saillance egale au seuil => pas saillant (strict >)."""
        t = isolate_thalamus
        t._threshold = 0.5
        t._scorecard["DOPAMINE_SURGE"] = 0.5
        assert t.is_salient("DOPAMINE_SURGE") is False


# ============================================================
# 4. TestDecayCycle
# ============================================================

class TestDecayCycle:

    @pytest.mark.asyncio
    async def test_decay_cycle(self, isolate_thalamus):
        """Apres un cycle, saillances diminuent de x0.95."""
        t = isolate_thalamus
        from core.thalamus import SALIENCE_DECAY
        t._scorecard["REPTILIAN_ALERT"] = 0.8
        initial = 0.8
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        expected = initial * SALIENCE_DECAY
        assert abs(t._scorecard["REPTILIAN_ALERT"] - expected) < 0.01


# ============================================================
# 5. TestUrgencyBoost
# ============================================================

class TestUrgencyBoost:

    @pytest.mark.asyncio
    async def test_reptilian_alert_boosts_urgence(self, isolate_thalamus):
        """REPTILIAN_ALERT met les events urgence a 0.9 minimum."""
        t = isolate_thalamus
        await t._on_reptilian_alert({"threat_level": 7.0})
        from core.thalamus import EVENT_CATEGORIES
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                assert t._scorecard[evt] >= 0.9


# ============================================================
# 6. TestThresholdAdaptation
# ============================================================

class TestThresholdAdaptation:

    @pytest.mark.asyncio
    async def test_threshold_adapts_to_eveil(self, isolate_thalamus):
        t = isolate_thalamus
        from core.thalamus import PHASE_THRESHOLDS
        await t._on_phase_change({"phase": "eveil"})
        assert t._threshold == PHASE_THRESHOLDS["eveil"]

    @pytest.mark.asyncio
    async def test_threshold_adapts_to_sommeil(self, isolate_thalamus):
        t = isolate_thalamus
        from core.thalamus import PHASE_THRESHOLDS
        await t._on_phase_change({"phase": "sommeil_profond"})
        assert t._threshold == PHASE_THRESHOLDS["sommeil_profond"]

    @pytest.mark.asyncio
    async def test_dopamine_modulates_threshold(self, isolate_thalamus):
        """Dopamine haute => seuil baisse (plus attentif)."""
        t = isolate_thalamus
        t._threshold = 0.5
        t._context["dopamine_level"] = 0.9
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        assert t._threshold < 0.5


# ============================================================
# 7. TestFocus
# ============================================================

class TestFocus:

    def test_focus_computed_from_category_sums(self, isolate_thalamus):
        """La categorie avec la plus haute somme de saillances = focus."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        # Met toutes les urgences a 0.9, le reste bas
        for evt, cat in EVENT_CATEGORIES.items():
            t._scorecard[evt] = 0.9 if cat == "urgence" else 0.1
        focus = t._compute_dominant_focus()
        assert focus == "urgence"

    def test_get_focus_returns_attention_focus(self, isolate_thalamus):
        t = isolate_thalamus
        t._attention_focus = "cognition"
        assert t.get_focus() == "cognition"


# ============================================================
# 8. TestEventPublishing
# ============================================================

class TestEventPublishing:

    @pytest.mark.asyncio
    async def test_attention_shift_published(self, isolate_thalamus):
        """Changement de focus => THALAMUS_ATTENTION_SHIFT publie."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        t._attention_focus = "regulation"
        # Rend urgence dominante pour forcer un shift
        for evt, cat in EVENT_CATEGORIES.items():
            t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
        t._last_scorecard = dict(t._scorecard)  # Pas de changement saillance
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await t._update_cycle()
        shift_calls = [c for c in mock_pub.call_args_list if c[0][0] == "THALAMUS_ATTENTION_SHIFT"]
        assert len(shift_calls) >= 1

    @pytest.mark.asyncio
    async def test_salience_published_on_change(self, isolate_thalamus):
        """Changement > MIN_SALIENCE_CHANGE => THALAMUS_SALIENCE publie."""
        t = isolate_thalamus
        # Ecart initial vs apres decay force un changement
        t._scorecard["REPTILIAN_ALERT"] = 0.9
        t._last_scorecard["REPTILIAN_ALERT"] = 0.5  # Grande difference
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await t._update_cycle()
        sal_calls = [c for c in mock_pub.call_args_list if c[0][0] == "THALAMUS_SALIENCE"]
        assert len(sal_calls) >= 1


# ============================================================
# 9. TestCardiacTriggersCycle
# ============================================================

class TestCardiacTriggersCycle:

    @pytest.mark.asyncio
    async def test_cardiac_beat_triggers_cycle(self, isolate_thalamus):
        """CARDIAC_BEAT declenche _update_cycle."""
        t = isolate_thalamus
        initial_count = t._cycle_count
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._on_cardiac_beat({"bpm": 72, "emotion": "joie", "emotion_intensity": 0.6})
        assert t._cycle_count == initial_count + 1
        assert t._context["bpm"] == 72
        assert t._context["emotion"] == "joie"


# ============================================================
# 10. TestNapMode
# ============================================================

class TestNapMode:

    @pytest.mark.asyncio
    async def test_nap_mode_toggles_sleeping(self, isolate_thalamus):
        t = isolate_thalamus
        assert t._sleeping is False
        await t._on_nap_mode({"active": True})
        assert t._sleeping is True
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._on_nap_mode({"active": False})
        assert t._sleeping is False


# ============================================================
# 11. TestComputeAttentionBonus
# ============================================================

class TestComputeAttentionBonus:

    def test_bonus_aligned_to_focus(self, isolate_thalamus):
        """Routines alignees au focus => bonus positif."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        t._scorecard["REPTILIAN_ALERT"] = 0.9
        t._scorecard["HALLUCINATION_DETECTED"] = 0.8
        t._scorecard["TISSUE_EXTINCTION_RISK"] = 0.7
        t._scorecard["OLLAMA_UNRESPONSIVE"] = 0.7
        bonus = t.compute_attention_bonus("SECURITY_SCAN")
        assert bonus > 0.0

    def test_malus_not_aligned(self, isolate_thalamus):
        """Routines non-alignees => malus."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        bonus = t.compute_attention_bonus("MEMORY_CLEANUP")
        assert bonus == -0.3

    def test_no_bonus_unknown_intent(self, isolate_thalamus):
        """Intent inconnu => pas de bonus."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        bonus = t.compute_attention_bonus("TOTALLY_UNKNOWN_ROUTINE")
        assert bonus == 0.0

    def test_no_bonus_no_focus(self, isolate_thalamus):
        """Pas de focus => pas de bonus."""
        t = isolate_thalamus
        t._attention_focus = None
        bonus = t.compute_attention_bonus("SECURITY_SCAN")
        assert bonus == 0.0


# ============================================================
# 12. TestIsWorthAttention
# ============================================================

class TestIsWorthAttention:

    def test_fallback_true_when_exception(self):
        """is_worth_attention retourne True si thalamus leve une exception."""
        from core import thalamus as mod
        with patch.object(mod.thalamus, "is_salient", side_effect=RuntimeError("boom")):
            assert mod.is_worth_attention("ANYTHING") is True

    def test_delegates_to_is_salient(self, isolate_thalamus):
        t = isolate_thalamus
        t._threshold = 0.3
        t._scorecard["REPTILIAN_ALERT"] = 0.8
        from core.thalamus import is_worth_attention
        assert is_worth_attention("REPTILIAN_ALERT") is True


# ============================================================
# 13. TestGetStats
# ============================================================

class TestGetStats:

    def test_get_stats_keys(self, isolate_thalamus):
        t = isolate_thalamus
        stats = t.get_stats()
        assert "scorecard" in stats
        assert "threshold" in stats
        assert "focus" in stats
        assert "sleeping" in stats
        assert "cycle_count" in stats
        assert "context" in stats
        assert "salient_events" in stats


# ============================================================
# 14. TestPersistence
# ============================================================

class TestPersistence:

    def test_save_load_roundtrip(self, isolate_thalamus, tmp_path):
        """Sauvegarde et rechargement preservent l'etat."""
        from core import thalamus as mod
        t = isolate_thalamus
        t._scorecard["REPTILIAN_ALERT"] = 0.85
        t._threshold = 0.42
        t._attention_focus = "urgence"
        t._cycle_count = 7
        t._save()

        # Verifie que le fichier existe
        state_file = str(tmp_path / "thalamus_state.json")
        assert os.path.exists(state_file)

        # Reset et recharge
        mod.Thalamus.reset_singleton()
        t2 = mod.Thalamus.__new__(mod.Thalamus)
        t2._initialized = False
        t2.__init__()
        # _load est appele dans __init__ et va lire le fichier
        assert abs(t2._scorecard["REPTILIAN_ALERT"] - 0.85) < 0.001
        assert abs(t2._threshold - 0.42) < 0.001
        assert t2._attention_focus == "urgence"
        assert t2._cycle_count == 7


# ============================================================
# 15. TestContextUpdate
# ============================================================

class TestContextUpdate:

    @pytest.mark.asyncio
    async def test_dopamine_surge_updates_context(self, isolate_thalamus):
        t = isolate_thalamus
        await t._on_dopamine_surge({"level": 0.85})
        assert t._context["dopamine_level"] == 0.85

    @pytest.mark.asyncio
    async def test_dopamine_dip_updates_context(self, isolate_thalamus):
        t = isolate_thalamus
        await t._on_dopamine_dip({"level": 0.15})
        assert t._context["dopamine_level"] == 0.15

    @pytest.mark.asyncio
    async def test_phase_change_updates_context(self, isolate_thalamus):
        t = isolate_thalamus
        await t._on_phase_change({"phase": "crepuscule"})
        assert t._context["phase"] == "crepuscule"


# ============================================================
# 16. TestClamping
# ============================================================

class TestClamping:

    @pytest.mark.asyncio
    async def test_salience_clamped_0_1(self, isolate_thalamus):
        """Saillances restent dans [0.0, 1.0]."""
        t = isolate_thalamus
        t._scorecard["REPTILIAN_ALERT"] = 1.5  # Au-dessus
        t._scorecard["DOPAMINE_SURGE"] = -0.3  # En-dessous
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        assert 0.0 <= t._scorecard["REPTILIAN_ALERT"] <= 1.0
        assert 0.0 <= t._scorecard["DOPAMINE_SURGE"] <= 1.0

    @pytest.mark.asyncio
    async def test_threshold_clamped_01_095(self, isolate_thalamus):
        """Seuil reste dans [0.1, 0.95]."""
        t = isolate_thalamus
        t._threshold = 0.01  # Trop bas
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        assert t._threshold >= 0.1

        t._threshold = 0.99  # Trop haut
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        assert t._threshold <= 0.95


# ============================================================
# 17. TestCategoryHandlers
# ============================================================

class TestCategoryHandlers:

    @pytest.mark.asyncio
    async def test_hallucination_boosts_urgence(self, isolate_thalamus):
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        initial = t._scorecard["REPTILIAN_ALERT"]
        await t._on_hallucination({})
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                assert t._scorecard[evt] > initial

    @pytest.mark.asyncio
    async def test_tissue_pattern_boosts_emergence(self, isolate_thalamus):
        t = isolate_thalamus
        initial = t._scorecard["TISSUE_PATTERN_EMERGED"]
        await t._on_tissue_pattern({})
        assert t._scorecard["TISSUE_PATTERN_EMERGED"] > initial

    @pytest.mark.asyncio
    async def test_council_end_boosts_deliberation(self, isolate_thalamus):
        t = isolate_thalamus
        initial = t._scorecard["COUNCIL_END"]
        await t._on_council_end({})
        assert t._scorecard["COUNCIL_END"] > initial

    @pytest.mark.asyncio
    async def test_knowledge_gap_boosts_cognition(self, isolate_thalamus):
        t = isolate_thalamus
        initial = t._scorecard["PREFRONTAL_GOAL_COMPLETE"]
        await t._on_knowledge_gap({})
        assert t._scorecard["PREFRONTAL_GOAL_COMPLETE"] > initial

    @pytest.mark.asyncio
    async def test_goal_complete_boosts_cognition(self, isolate_thalamus):
        t = isolate_thalamus
        initial = t._scorecard["KNOWLEDGE_GAP_DETECTED"]
        await t._on_goal_complete({})
        assert t._scorecard["KNOWLEDGE_GAP_DETECTED"] > initial


# ============================================================
# 18. TestNapSleepMode (Sprint B)
# ============================================================

class TestNapSleepMode:

    @pytest.mark.asyncio
    async def test_enter_sleep_sets_threshold(self, isolate_thalamus):
        """Entree en sieste => seuil monte a sommeil_profond (0.8)."""
        t = isolate_thalamus
        from core.thalamus import PHASE_THRESHOLDS
        t._threshold = 0.3
        t._enter_sleep()
        assert t._sleeping is True
        assert t._threshold == PHASE_THRESHOLDS["sommeil_profond"]

    @pytest.mark.asyncio
    async def test_enter_sleep_clears_buffer(self, isolate_thalamus):
        """Entree en sieste => buffer vide."""
        t = isolate_thalamus
        t._nap_buffer = [{"event_type": "X", "data": {}, "timestamp": 0}]
        t._enter_sleep()
        assert len(t._nap_buffer) == 0

    @pytest.mark.asyncio
    async def test_exit_sleep_restores_threshold(self, isolate_thalamus):
        """Sortie de sieste => seuil revient a pre-nap."""
        t = isolate_thalamus
        t._threshold = 0.35
        t._enter_sleep()
        assert t._threshold == 0.8
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._exit_sleep()
        assert t._sleeping is False
        assert abs(t._threshold - 0.35) < 0.001

    @pytest.mark.asyncio
    async def test_events_buffered_during_sleep(self, isolate_thalamus):
        """Events bufferises au lieu de booster la scorecard pendant sieste."""
        t = isolate_thalamus
        t._enter_sleep()
        initial = t._scorecard["KNOWLEDGE_GAP_DETECTED"]
        await t._on_knowledge_gap({})
        # Scorecard inchangee
        assert t._scorecard["KNOWLEDGE_GAP_DETECTED"] == initial
        # Event dans le buffer
        assert len(t._nap_buffer) == 1
        assert t._nap_buffer[0]["event_type"] == "KNOWLEDGE_GAP_DETECTED"

    @pytest.mark.asyncio
    async def test_buffer_max_size(self, isolate_thalamus):
        """Buffer ne depasse pas NAP_BUFFER_MAX."""
        t = isolate_thalamus
        from core.thalamus import NAP_BUFFER_MAX
        t._enter_sleep()
        for i in range(NAP_BUFFER_MAX + 10):
            await t._on_knowledge_gap({})
        assert len(t._nap_buffer) == NAP_BUFFER_MAX

    @pytest.mark.asyncio
    async def test_reptilian_alert_wakes_up(self, isolate_thalamus):
        """REPTILIAN_ALERT pendant sommeil => reveil force + boost urgence."""
        t = isolate_thalamus
        t._threshold = 0.3
        t._enter_sleep()
        assert t._sleeping is True
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._on_reptilian_alert({"threat_level": 8.0})
        assert t._sleeping is False
        from core.thalamus import EVENT_CATEGORIES
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                assert t._scorecard[evt] >= 0.9

    @pytest.mark.asyncio
    async def test_flush_buffer_updates_scorecard(self, isolate_thalamus):
        """Au reveil, buffer rejoue => saillances augmentent."""
        t = isolate_thalamus
        t._enter_sleep()
        await t._on_council_end({})
        await t._on_knowledge_gap({})
        initial_delib = t._scorecard["COUNCIL_END"]
        initial_cogni = t._scorecard["KNOWLEDGE_GAP_DETECTED"]
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._exit_sleep()
        assert t._scorecard["COUNCIL_END"] > initial_delib
        assert t._scorecard["KNOWLEDGE_GAP_DETECTED"] > initial_cogni

    @pytest.mark.asyncio
    async def test_flush_buffer_attenuation(self, isolate_thalamus):
        """Events anciens (>60s) recoivent un boost attenue (x0.7)."""
        import time as time_mod
        t = isolate_thalamus
        t._enter_sleep()
        # Simule un event ancien (120s dans le passe)
        t._nap_buffer.append({
            "event_type": "COUNCIL_END",
            "data": {},
            "timestamp": time_mod.time() - 120,
        })
        initial = t._scorecard["COUNCIL_END"]
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._exit_sleep()
        boost = t._scorecard["COUNCIL_END"] - initial
        # Boost attenue = 0.15 * 0.7 = 0.105
        assert abs(boost - 0.105) < 0.02

    @pytest.mark.asyncio
    async def test_no_attention_shift_during_sleep(self, isolate_thalamus):
        """Pas de THALAMUS_ATTENTION_SHIFT publie en mode sommeil."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        t._enter_sleep()
        t._attention_focus = "regulation"
        for evt, cat in EVENT_CATEGORIES.items():
            t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await t._update_cycle()
        shift_calls = [c for c in mock_pub.call_args_list if c[0][0] == "THALAMUS_ATTENTION_SHIFT"]
        assert len(shift_calls) == 0

    @pytest.mark.asyncio
    async def test_no_salience_published_during_sleep(self, isolate_thalamus):
        """Pas de THALAMUS_SALIENCE publie en mode sommeil."""
        t = isolate_thalamus
        t._enter_sleep()
        t._scorecard["REPTILIAN_ALERT"] = 0.9
        t._last_scorecard["REPTILIAN_ALERT"] = 0.5
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await t._update_cycle()
        sal_calls = [c for c in mock_pub.call_args_list if c[0][0] == "THALAMUS_SALIENCE"]
        assert len(sal_calls) == 0

    @pytest.mark.asyncio
    async def test_cardiac_continues_during_sleep(self, isolate_thalamus):
        """Cycle continue meme en sommeil (cycle_count augmente)."""
        t = isolate_thalamus
        t._enter_sleep()
        initial_count = t._cycle_count
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._on_cardiac_beat({"bpm": 55, "emotion": "serenite"})
        assert t._cycle_count == initial_count + 1

    @pytest.mark.asyncio
    async def test_nap_mode_active_key(self, isolate_thalamus):
        """Handler _on_nap_mode lit la cle 'active' (pas 'sleeping')."""
        t = isolate_thalamus
        await t._on_nap_mode({"active": True})
        assert t._sleeping is True
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._on_nap_mode({"active": False})
        assert t._sleeping is False

    def test_get_stats_nap_buffer_size(self, isolate_thalamus):
        """get_stats inclut nap_buffer_size."""
        t = isolate_thalamus
        t._nap_buffer = [{"x": 1}, {"x": 2}]
        stats = t.get_stats()
        assert stats["nap_buffer_size"] == 2
