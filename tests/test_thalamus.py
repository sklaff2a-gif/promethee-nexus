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

    def test_initial_scorecard_has_17_entries(self, isolate_thalamus):
        t = isolate_thalamus
        assert len(t._scorecard) == 17

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
        """Changement de focus => THALAMUS_ATTENTION_SHIFT publie (apres inertie)."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES, FOCUS_INERTIA_CYCLES
        t._attention_focus = "regulation"
        # Rend urgence dominante pour forcer un shift (doit persister FOCUS_INERTIA_CYCLES)
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock) as mock_pub:
            for _ in range(FOCUS_INERTIA_CYCLES):
                for evt, cat in EVENT_CATEGORIES.items():
                    t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
                t._last_scorecard = dict(t._scorecard)
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


# ============================================================
# 19. TestFocusInertia (Sprint C)
# ============================================================

class TestFocusInertia:

    def test_focus_stable_no_change(self, isolate_thalamus):
        """Focus identique => pas de shift, candidat reset."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        result = t._apply_focus_inertia("urgence")
        assert result == "urgence"
        assert t._pending_focus is None
        assert t._pending_focus_count == 0

    def test_focus_shift_rejected_if_too_fast(self, isolate_thalamus):
        """1 cycle different => rejete, ancien focus maintenu."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        result = t._apply_focus_inertia("cognition")
        assert result == "urgence"  # Ancien maintenu
        assert t._pending_focus == "cognition"
        assert t._pending_focus_count == 1

    def test_focus_shift_accepted_after_inertia(self, isolate_thalamus):
        """FOCUS_INERTIA_CYCLES cycles consecutifs => shift accepte."""
        from core.thalamus import FOCUS_INERTIA_CYCLES
        t = isolate_thalamus
        t._attention_focus = "urgence"
        for i in range(FOCUS_INERTIA_CYCLES - 1):
            result = t._apply_focus_inertia("cognition")
            assert result == "urgence"  # Pas encore
        result = t._apply_focus_inertia("cognition")
        assert result == "cognition"  # Accepte

    def test_pending_focus_reset_on_new_candidate(self, isolate_thalamus):
        """Changement de candidat => compteur reset."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        t._apply_focus_inertia("cognition")
        assert t._pending_focus_count == 1
        t._apply_focus_inertia("emergence")  # Nouveau candidat
        assert t._pending_focus == "emergence"
        assert t._pending_focus_count == 1  # Reset

    def test_inertia_preserves_old_focus(self, isolate_thalamus):
        """Pendant l'attente d'inertie, l'ancien focus est maintenu."""
        t = isolate_thalamus
        t._attention_focus = "regulation"
        for _ in range(2):
            result = t._apply_focus_inertia("emergence")
            assert result == "regulation"
        assert t.get_focus() == "regulation"

    @pytest.mark.asyncio
    async def test_focus_history_appended_on_shift(self, isolate_thalamus):
        """Shift confirme => entree dans _focus_history."""
        from core.thalamus import EVENT_CATEGORIES, FOCUS_INERTIA_CYCLES
        t = isolate_thalamus
        t._attention_focus = "regulation"
        # Force urgence dominante pendant FOCUS_INERTIA_CYCLES cycles
        for evt, cat in EVENT_CATEGORIES.items():
            t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            for _ in range(FOCUS_INERTIA_CYCLES):
                await t._update_cycle()
        assert len(t._focus_history) >= 1
        assert t._focus_history[-1]["focus"] == "urgence"


# ============================================================
# 20. TestAttentionFatigue (Sprint C)
# ============================================================

class TestAttentionFatigue:

    @pytest.mark.asyncio
    async def test_fatigue_increases_with_stable_focus(self, isolate_thalamus):
        """Fatigue monte quand focus stable."""
        from core.thalamus import ATTENTION_FATIGUE_RATE
        t = isolate_thalamus
        t._attention_focus = "urgence"
        t._attention_fatigue = 0.0
        # Force urgence dominante pour garder focus stable
        from core.thalamus import EVENT_CATEGORIES
        for evt, cat in EVENT_CATEGORIES.items():
            t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        assert t._attention_fatigue >= ATTENTION_FATIGUE_RATE

    @pytest.mark.asyncio
    async def test_fatigue_decreases_on_shift(self, isolate_thalamus):
        """Shift de focus => fatigue baisse (recovery)."""
        from core.thalamus import ATTENTION_FATIGUE_RECOVERY, FOCUS_INERTIA_CYCLES, EVENT_CATEGORIES
        t = isolate_thalamus
        t._attention_focus = "regulation"
        t._attention_fatigue = 0.3
        # Force urgence dominante pendant assez de cycles pour shift
        for evt, cat in EVENT_CATEGORIES.items():
            t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            for _ in range(FOCUS_INERTIA_CYCLES):
                # Maintenir la scorecard car decay la reduit
                for evt, cat in EVENT_CATEGORIES.items():
                    t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
                await t._update_cycle()
        # Apres le shift, fatigue doit avoir baisse
        assert t._attention_fatigue < 0.3

    @pytest.mark.asyncio
    async def test_fatigue_clamped_max(self, isolate_thalamus):
        """Fatigue ne depasse pas ATTENTION_FATIGUE_MAX (1.0)."""
        from core.thalamus import ATTENTION_FATIGUE_MAX
        t = isolate_thalamus
        t._attention_fatigue = ATTENTION_FATIGUE_MAX
        t._attention_focus = "urgence"
        # Simule focus stable manuellement
        from core.thalamus import EVENT_CATEGORIES
        for evt, cat in EVENT_CATEGORIES.items():
            t._scorecard[evt] = 0.95 if cat == "urgence" else 0.01
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        assert t._attention_fatigue <= ATTENTION_FATIGUE_MAX

    def test_fatigue_attenuates_bonus(self, isolate_thalamus):
        """Bonus reduit quand fatigue > 0.5."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        t._scorecard["REPTILIAN_ALERT"] = 0.9
        t._scorecard["HALLUCINATION_DETECTED"] = 0.8
        t._scorecard["TISSUE_EXTINCTION_RISK"] = 0.7
        t._scorecard["OLLAMA_UNRESPONSIVE"] = 0.7
        # Bonus sans fatigue
        t._attention_fatigue = 0.0
        bonus_fresh = t.compute_attention_bonus("SECURITY_SCAN")
        # Bonus avec fatigue
        t._attention_fatigue = 0.8
        bonus_fatigued = t.compute_attention_bonus("SECURITY_SCAN")
        assert bonus_fatigued < bonus_fresh
        assert bonus_fatigued > 0  # Pas nul

    def test_fatigue_no_effect_below_threshold(self, isolate_thalamus):
        """Fatigue < 0.5 => bonus normal."""
        t = isolate_thalamus
        t._attention_focus = "urgence"
        t._scorecard["REPTILIAN_ALERT"] = 0.9
        t._scorecard["HALLUCINATION_DETECTED"] = 0.8
        t._scorecard["TISSUE_EXTINCTION_RISK"] = 0.7
        t._scorecard["OLLAMA_UNRESPONSIVE"] = 0.7
        t._attention_fatigue = 0.0
        bonus_zero = t.compute_attention_bonus("SECURITY_SCAN")
        t._attention_fatigue = 0.4
        bonus_low = t.compute_attention_bonus("SECURITY_SCAN")
        assert bonus_zero == bonus_low


# ============================================================
# 21. TestRoutineFeedback (Sprint C)
# ============================================================

class TestRoutineFeedback:

    @pytest.mark.asyncio
    async def test_routine_success_boosts_category(self, isolate_thalamus):
        """Success SECURITY_SCAN => boost urgence."""
        from core.thalamus import EVENT_CATEGORIES, ROUTINE_FEEDBACK_BOOST
        t = isolate_thalamus
        initial = t._scorecard["REPTILIAN_ALERT"]
        await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                assert t._scorecard[evt] == pytest.approx(initial + ROUTINE_FEEDBACK_BOOST, abs=0.001)

    @pytest.mark.asyncio
    async def test_routine_error_reduces_category(self, isolate_thalamus):
        """Error SECURITY_SCAN => malus urgence."""
        from core.thalamus import EVENT_CATEGORIES, ROUTINE_FEEDBACK_MALUS
        t = isolate_thalamus
        initial = t._scorecard["REPTILIAN_ALERT"]
        await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "error"})
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                assert t._scorecard[evt] == pytest.approx(initial - ROUTINE_FEEDBACK_MALUS, abs=0.001)

    @pytest.mark.asyncio
    async def test_routine_unknown_intent_ignored(self, isolate_thalamus):
        """Intent inconnu => noop."""
        t = isolate_thalamus
        snapshot = dict(t._scorecard)
        await t._on_routine_complete({"intent": "TOTALLY_UNKNOWN", "status": "success"})
        assert t._scorecard == snapshot

    @pytest.mark.asyncio
    async def test_routine_feedback_clamped(self, isolate_thalamus):
        """Feedback respecte les bornes [0, 1]."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        # Scorecard a 0.02 => malus ne descend pas sous 0
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                t._scorecard[evt] = 0.02
        await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "error"})
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                assert t._scorecard[evt] >= 0.0


# ============================================================
# 22. TestGetHealth (Sprint C)
# ============================================================

class TestGetHealth:

    def test_get_health_keys(self, isolate_thalamus):
        """Verifie toutes les cles de get_health."""
        t = isolate_thalamus
        health = t.get_health()
        expected_keys = {"attention_fatigue", "focus_stability", "focus", "sleeping", "salient_count", "status", "learned_rules_count", "habituated_count"}
        assert set(health.keys()) == expected_keys

    def test_get_health_fatigued_status(self, isolate_thalamus):
        """Fatigue > 0.7 => status FATIGUED."""
        t = isolate_thalamus
        t._attention_fatigue = 0.8
        health = t.get_health()
        assert health["status"] == "FATIGUED"

    def test_get_health_ok_status(self, isolate_thalamus):
        """Fatigue <= 0.7 => status OK."""
        t = isolate_thalamus
        t._attention_fatigue = 0.5
        health = t.get_health()
        assert health["status"] == "OK"


# ============================================================
# 23. TestSprintCGetStats
# ============================================================

class TestSprintCGetStats:

    def test_get_stats_has_sprint_c_fields(self, isolate_thalamus):
        """get_stats inclut les champs Sprint C."""
        t = isolate_thalamus
        stats = t.get_stats()
        assert "attention_fatigue" in stats
        assert "focus_duration_cycles" in stats
        assert "focus_history_size" in stats


# ============================================================
# 24. TestWakeSummary (Sprint D)
# ============================================================

class TestWakeSummary:

    @pytest.mark.asyncio
    async def test_wake_summary_published_on_exit_sleep(self, isolate_thalamus):
        """Buffer non-vide au reveil → THALAMUS_WAKE_SUMMARY publie."""
        t = isolate_thalamus
        published = []
        async def capture(data):
            published.append(data)
        from core.event_bus.bus import bus
        bus.subscribe("THALAMUS_WAKE_SUMMARY", capture)
        # Entrer en sieste, bufferiser, reveiller
        t._enter_sleep()
        t._buffer_event("KNOWLEDGE_GAP_DETECTED", {"topic": "test"})
        t._buffer_event("COUNCIL_END", {"status": "consensus"})
        await t._exit_sleep()
        assert len(published) == 1
        assert published[0]["event_count"] == 2

    @pytest.mark.asyncio
    async def test_wake_summary_fields(self, isolate_thalamus):
        """Le resume contient toutes les cles attendues."""
        t = isolate_thalamus
        published = []
        async def capture(data):
            published.append(data)
        from core.event_bus.bus import bus
        bus.subscribe("THALAMUS_WAKE_SUMMARY", capture)
        t._enter_sleep()
        t._buffer_event("REPTILIAN_ALERT", {"threat_level": 8})
        await t._exit_sleep()
        assert len(published) == 1
        summary = published[0]
        expected_keys = {"events_during_nap", "nap_duration", "event_count", "top_category", "category_counts"}
        assert expected_keys == set(summary.keys())
        assert summary["top_category"] == "urgence"
        assert summary["category_counts"]["urgence"] == 1
        assert isinstance(summary["nap_duration"], float)

    @pytest.mark.asyncio
    async def test_wake_summary_events_sorted_by_priority(self, isolate_thalamus):
        """Events tries : urgence avant cognition avant regulation."""
        t = isolate_thalamus
        published = []
        async def capture(data):
            published.append(data)
        from core.event_bus.bus import bus
        bus.subscribe("THALAMUS_WAKE_SUMMARY", capture)
        t._enter_sleep()
        # Bufferiser dans le desordre : regulation, urgence, cognition
        t._buffer_event("CIRCADIAN_PHASE_CHANGE", {"phase": "aube"})
        t._buffer_event("HALLUCINATION_DETECTED", {"source": "test"})
        t._buffer_event("KNOWLEDGE_GAP_DETECTED", {"topic": "test"})
        await t._exit_sleep()
        events = published[0]["events_during_nap"]
        categories = [e["category"] for e in events]
        assert categories == ["urgence", "cognition", "regulation"]

    @pytest.mark.asyncio
    async def test_wake_summary_not_published_if_buffer_empty(self, isolate_thalamus):
        """Buffer vide au reveil → pas de publication."""
        t = isolate_thalamus
        published = []
        async def capture(data):
            published.append(data)
        from core.event_bus.bus import bus
        bus.subscribe("THALAMUS_WAKE_SUMMARY", capture)
        t._enter_sleep()
        # Pas de buffer
        await t._exit_sleep()
        assert len(published) == 0


# ============================================================
# 25. TestLearnedRulesBasic (Sprint E)
# ============================================================

class TestLearnedRulesBasic:

    def test_initial_no_rules(self, isolate_thalamus):
        """Pas de regles a l'init."""
        t = isolate_thalamus
        assert t._learned_rules == []
        assert t._intent_observations == {}

    @pytest.mark.asyncio
    async def test_observe_unknown_intent_ignored(self, isolate_thalamus):
        """Intent inconnu dans _INTENT_CATEGORY_HINTS → noop."""
        t = isolate_thalamus
        await t._on_routine_complete({"intent": "TOTALLY_UNKNOWN", "status": "success"})
        assert t._learned_rules == []
        assert t._intent_observations == {}

    @pytest.mark.asyncio
    async def test_observe_builds_up_successes(self, isolate_thalamus):
        """2 success → compteur=2, pas de regle."""
        t = isolate_thalamus
        await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        assert t._intent_observations["SECURITY_SCAN"]["success"] == 2
        assert len(t._learned_rules) == 0

    @pytest.mark.asyncio
    async def test_crystallize_after_threshold(self, isolate_thalamus):
        """3 success → regle boost creee."""
        from core.thalamus import RULE_BOOST_WEIGHT
        t = isolate_thalamus
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        assert len(t._learned_rules) == 1
        rule = t._learned_rules[0]
        assert rule["intent"] == "SECURITY_SCAN"
        assert rule["type"] == "boost"
        assert rule["category"] == "urgence"
        assert rule["weight"] == RULE_BOOST_WEIGHT

    @pytest.mark.asyncio
    async def test_crystallize_dampen_on_errors(self, isolate_thalamus):
        """3 errors → regle dampen creee."""
        from core.thalamus import RULE_DAMPEN_WEIGHT
        t = isolate_thalamus
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "error"})
        assert len(t._learned_rules) == 1
        rule = t._learned_rules[0]
        assert rule["type"] == "dampen"
        assert rule["weight"] == RULE_DAMPEN_WEIGHT

    @pytest.mark.asyncio
    async def test_counters_reset_after_crystallize(self, isolate_thalamus):
        """Compteurs a 0 apres cristallisation."""
        t = isolate_thalamus
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        obs = t._intent_observations["SECURITY_SCAN"]
        assert obs["success"] == 0
        assert obs["error"] == 0


# ============================================================
# 26. TestLearnedRulesReinforcement (Sprint E)
# ============================================================

class TestLearnedRulesReinforcement:

    @pytest.mark.asyncio
    async def test_reinforce_same_direction(self, isolate_thalamus):
        """Regle existante + 3 success supp → weight augmente."""
        from core.thalamus import RULE_BOOST_WEIGHT, RULE_REINFORCE_STEP
        t = isolate_thalamus
        # Cristalliser
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        assert t._learned_rules[0]["weight"] == RULE_BOOST_WEIGHT
        # Renforcer
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        assert t._learned_rules[0]["weight"] == pytest.approx(
            RULE_BOOST_WEIGHT + RULE_REINFORCE_STEP, abs=0.001
        )

    @pytest.mark.asyncio
    async def test_contradiction_weakens_rule(self, isolate_thalamus):
        """Regle boost + 3 errors → weight diminue."""
        from core.thalamus import RULE_BOOST_WEIGHT, RULE_DECAY_ON_CONTRADICTION
        t = isolate_thalamus
        # Cristalliser boost
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        initial_weight = t._learned_rules[0]["weight"]
        # Contredire
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "error"})
        assert t._learned_rules[0]["weight"] == pytest.approx(
            initial_weight - RULE_DECAY_ON_CONTRADICTION, abs=0.001
        )

    @pytest.mark.asyncio
    async def test_contradiction_removes_weak_rule(self, isolate_thalamus):
        """Contradictions repetees → regle supprimee quand weight < MIN."""
        from core.thalamus import RULE_MIN_WEIGHT
        t = isolate_thalamus
        # Cristalliser boost
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        # Forcer un poids tres faible
        t._learned_rules[0]["weight"] = RULE_MIN_WEIGHT + 0.001
        # Contredire → suppression
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "error"})
        assert len(t._learned_rules) == 0


# ============================================================
# 27. TestLearnedRulesEviction (Sprint E)
# ============================================================

class TestLearnedRulesEviction:

    @pytest.mark.asyncio
    async def test_evict_weakest_when_full(self, isolate_thalamus):
        """20 regles + nouvelle → la plus faible ejectee."""
        from core.thalamus import MAX_LEARNED_RULES
        t = isolate_thalamus
        # Creer 20 regles manuellement
        for i in range(MAX_LEARNED_RULES):
            t._learned_rules.append({
                "intent": f"FAKE_INTENT_{i}",
                "category": "urgence",
                "type": "boost",
                "weight": 0.05 + i * 0.005,
            })
        assert len(t._learned_rules) == MAX_LEARNED_RULES
        # La plus faible est FAKE_INTENT_0 (weight=0.05)
        # Cristalliser une nouvelle regle
        for _ in range(3):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
        assert len(t._learned_rules) == MAX_LEARNED_RULES
        intents = [r["intent"] for r in t._learned_rules]
        assert "FAKE_INTENT_0" not in intents
        assert "SECURITY_SCAN" in intents


# ============================================================
# 28. TestLearnedRulesApplication (Sprint E)
# ============================================================

class TestLearnedRulesApplication:

    @pytest.mark.asyncio
    async def test_apply_boost_rule_increases_scorecard(self, isolate_thalamus):
        """Cycle avec boost rule → saillances urgence augmentent."""
        from core.thalamus import EVENT_CATEGORIES, RULE_BOOST_WEIGHT, SALIENCE_DECAY
        t = isolate_thalamus
        t._learned_rules = [{
            "intent": "SECURITY_SCAN",
            "category": "urgence",
            "type": "boost",
            "weight": RULE_BOOST_WEIGHT,
        }]
        # Fixer scorecard a 0.5 partout
        for k in t._scorecard:
            t._scorecard[k] = 0.5
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        # Apres decay (0.5*0.95=0.475) + boost (0.475+0.08=0.555)
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                expected = 0.5 * SALIENCE_DECAY + RULE_BOOST_WEIGHT
                assert t._scorecard[evt] >= expected - 0.01

    @pytest.mark.asyncio
    async def test_apply_dampen_rule_decreases_scorecard(self, isolate_thalamus):
        """Cycle avec dampen rule → saillances cognition diminuent."""
        from core.thalamus import EVENT_CATEGORIES, RULE_DAMPEN_WEIGHT, SALIENCE_DECAY
        t = isolate_thalamus
        t._learned_rules = [{
            "intent": "SELF_ANALYSIS",
            "category": "cognition",
            "type": "dampen",
            "weight": RULE_DAMPEN_WEIGHT,
        }]
        for k in t._scorecard:
            t._scorecard[k] = 0.5
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "cognition":
                expected = 0.5 * SALIENCE_DECAY - RULE_DAMPEN_WEIGHT
                assert t._scorecard[evt] <= expected + 0.01

    @pytest.mark.asyncio
    async def test_rules_applied_after_decay(self, isolate_thalamus):
        """Verifie ordre : decay PUIS rules (pas l'inverse)."""
        from core.thalamus import SALIENCE_DECAY, RULE_BOOST_WEIGHT
        t = isolate_thalamus
        t._learned_rules = [{
            "intent": "SECURITY_SCAN",
            "category": "urgence",
            "type": "boost",
            "weight": RULE_BOOST_WEIGHT,
        }]
        t._scorecard["REPTILIAN_ALERT"] = 0.5
        t._context["threat_level"] = 0.0  # Pas de bonus urgence
        t._context["dopamine_level"] = 0.5  # Pas de modulation seuil
        t._context["bpm"] = 60.0
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        # decay puis boost : 0.5 * 0.95 + 0.08 = 0.555
        expected = 0.5 * SALIENCE_DECAY + RULE_BOOST_WEIGHT
        assert abs(t._scorecard["REPTILIAN_ALERT"] - expected) < 0.01


# ============================================================
# 29. TestLearnedRulesPersistence (Sprint E)
# ============================================================

class TestLearnedRulesPersistence:

    def test_save_load_preserves_rules(self, isolate_thalamus, tmp_path):
        """Save + reset + load → regles restaurees."""
        from core import thalamus as mod
        t = isolate_thalamus
        t._learned_rules = [{
            "intent": "SECURITY_SCAN",
            "category": "urgence",
            "type": "boost",
            "weight": 0.08,
        }]
        t._intent_observations = {"SECURITY_SCAN": {"success": 1, "error": 0}}
        t._save()

        # Reset et recharge
        mod.Thalamus.reset_singleton()
        t2 = mod.Thalamus.__new__(mod.Thalamus)
        t2._initialized = False
        t2.__init__()
        assert len(t2._learned_rules) == 1
        assert t2._learned_rules[0]["intent"] == "SECURITY_SCAN"
        assert t2._learned_rules[0]["weight"] == 0.08
        assert t2._intent_observations["SECURITY_SCAN"]["success"] == 1


# ============================================================
# 30. TestLearnedRulesGetStats (Sprint E)
# ============================================================

class TestLearnedRulesGetStats:

    def test_get_stats_includes_rules(self, isolate_thalamus):
        """get_stats inclut learned_rules_count et learned_rules."""
        t = isolate_thalamus
        t._learned_rules = [{
            "intent": "SECURITY_SCAN",
            "category": "urgence",
            "type": "boost",
            "weight": 0.08,
        }]
        stats = t.get_stats()
        assert "learned_rules_count" in stats
        assert stats["learned_rules_count"] == 1
        assert "learned_rules" in stats
        assert stats["learned_rules"][0]["intent"] == "SECURITY_SCAN"

    def test_get_health_includes_rules_count(self, isolate_thalamus):
        """get_health inclut learned_rules_count."""
        t = isolate_thalamus
        t._learned_rules = [{"x": 1}, {"x": 2}]
        health = t.get_health()
        assert "learned_rules_count" in health
        assert health["learned_rules_count"] == 2


# ============================================================
# 31. TestLearnedRulesContradiction (Sprint E)
# ============================================================

class TestLearnedRulesContradiction:

    @pytest.mark.asyncio
    async def test_alternating_prevents_crystallization(self, isolate_thalamus):
        """Alternance success/error → jamais de cristallisation."""
        t = isolate_thalamus
        for _ in range(10):
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "success"})
            await t._on_routine_complete({"intent": "SECURITY_SCAN", "status": "error"})
        assert len(t._learned_rules) == 0


# ============================================================
# 32. TestHabituationBasic (Sprint F)
# ============================================================

class TestHabituationBasic:

    def test_initial_timestamps_empty(self, isolate_thalamus):
        """Listes vides pour chaque event sensoriel a l'init."""
        from core.thalamus import _SENSORY_EVENTS
        t = isolate_thalamus
        for evt in _SENSORY_EVENTS:
            assert t._event_timestamps[evt] == []

    @pytest.mark.asyncio
    async def test_record_event_adds_timestamp(self, isolate_thalamus):
        """_on_hallucination → 1 timestamp enregistre."""
        t = isolate_thalamus
        await t._on_hallucination({})
        assert len(t._event_timestamps["HALLUCINATION_DETECTED"]) == 1

    @pytest.mark.asyncio
    async def test_record_event_updates_last_seen(self, isolate_thalamus):
        """_event_last_seen renseigne apres handler."""
        import time
        t = isolate_thalamus
        before = time.time()
        await t._on_tissue_pattern({})
        after = time.time()
        assert "TISSUE_PATTERN_EMERGED" in t._event_last_seen
        assert before <= t._event_last_seen["TISSUE_PATTERN_EMERGED"] <= after

    def test_record_non_sensory_ignored(self, isolate_thalamus):
        """_record_event('CARDIAC_BEAT') → noop."""
        t = isolate_thalamus
        t._record_event("CARDIAC_BEAT")
        assert "CARDIAC_BEAT" not in t._event_timestamps

    def test_timestamps_pruned_beyond_window(self, isolate_thalamus):
        """Timestamps > HABITUATION_TIME_WINDOW elagués."""
        import time
        from core.thalamus import HABITUATION_TIME_WINDOW
        t = isolate_thalamus
        now = time.time()
        # Ajouter des timestamps anciens
        t._event_timestamps["COUNCIL_END"] = [now - HABITUATION_TIME_WINDOW - 100, now - HABITUATION_TIME_WINDOW - 50]
        t._record_event("COUNCIL_END")
        # Seul le nouveau doit rester
        assert len(t._event_timestamps["COUNCIL_END"]) == 1


# ============================================================
# 33. TestHabituationDecay (Sprint F)
# ============================================================

class TestHabituationDecay:

    @pytest.mark.asyncio
    async def test_no_extra_decay_below_threshold(self, isolate_thalamus):
        """4 occurrences → _get_habituation_level == 0.0."""
        import time
        t = isolate_thalamus
        now = time.time()
        t._event_timestamps["REPTILIAN_ALERT"] = [now - i for i in range(4)]
        assert t._get_habituation_level("REPTILIAN_ALERT") == 0.0

    @pytest.mark.asyncio
    async def test_extra_decay_above_threshold(self, isolate_thalamus):
        """7 occurrences → saillance baisse plus qu'avec decay seul."""
        import time
        from core.thalamus import SALIENCE_DECAY, HABITUATION_EXTRA_DECAY
        t = isolate_thalamus
        now = time.time()
        # 7 occurrences recentes pour HALLUCINATION_DETECTED
        t._event_timestamps["HALLUCINATION_DETECTED"] = [now - i * 10 for i in range(7)]
        t._scorecard["HALLUCINATION_DETECTED"] = 0.8
        # Event sans habituation pour comparaison
        t._scorecard["TISSUE_EXTINCTION_RISK"] = 0.8
        level = t._get_habituation_level("HALLUCINATION_DETECTED")
        assert level > 0.0
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        # HALLUCINATION doit etre plus basse que TISSUE_EXTINCTION (meme categorie urgence, mais habituation)
        assert t._scorecard["HALLUCINATION_DETECTED"] < t._scorecard["TISSUE_EXTINCTION_RISK"]

    @pytest.mark.asyncio
    async def test_habituation_only_affects_individual_event(self, isolate_thalamus):
        """REPTILIAN_ALERT repete → HALLUCINATION_DETECTED pas penalise en extra."""
        import time
        from core.thalamus import SALIENCE_DECAY
        t = isolate_thalamus
        now = time.time()
        # Habituer REPTILIAN_ALERT
        t._event_timestamps["REPTILIAN_ALERT"] = [now - i * 10 for i in range(8)]
        # Pas d'habituation sur HALLUCINATION_DETECTED
        t._event_timestamps["HALLUCINATION_DETECTED"] = []
        t._scorecard["REPTILIAN_ALERT"] = 0.6
        t._scorecard["HALLUCINATION_DETECTED"] = 0.6
        t._context["threat_level"] = 0.0
        t._context["dopamine_level"] = 0.5
        t._context["bpm"] = 60.0
        with patch("core.event_bus.bus.bus.publish", new_callable=AsyncMock):
            await t._update_cycle()
        # HALLUCINATION ne doit subir que decay standard, REPTILIAN aussi decay + habituation extra
        hallucination_val = t._scorecard["HALLUCINATION_DETECTED"]
        reptilian_val = t._scorecard["REPTILIAN_ALERT"]
        assert reptilian_val < hallucination_val


# ============================================================
# 34. TestNoveltyDetection (Sprint F)
# ============================================================

class TestNoveltyDetection:

    @pytest.mark.asyncio
    async def test_first_occurrence_is_novel(self, isolate_thalamus):
        """Premier event → boost NOVELTY_BOOST applique."""
        from core.thalamus import NOVELTY_BOOST, EVENT_CATEGORIES
        t = isolate_thalamus
        initial = t._scorecard["COUNCIL_END"]
        await t._on_council_end({})
        # Council END est dans categorie 'deliberation'
        assert t._scorecard["COUNCIL_END"] >= initial + NOVELTY_BOOST

    @pytest.mark.asyncio
    async def test_no_novelty_if_seen_recently(self, isolate_thalamus):
        """2 appels rapproches → pas de double novelty."""
        from core.thalamus import NOVELTY_BOOST, EVENT_CATEGORIES
        t = isolate_thalamus
        initial = t._scorecard["COUNCIL_END"]
        await t._on_council_end({})
        after_first = t._scorecard["COUNCIL_END"]
        await t._on_council_end({})
        after_second = t._scorecard["COUNCIL_END"]
        # Le 2e appel ajoute le boost handler (+0.2) mais PAS novelty en plus
        # Le premier : novelty (+0.15) + handler (+0.2) = +0.35
        # Le deuxieme : handler (+0.2) seulement = +0.2 de plus
        # Donc after_second - after_first < after_first - initial
        first_delta = after_first - initial
        second_delta = after_second - after_first
        assert second_delta < first_delta

    @pytest.mark.asyncio
    async def test_novelty_after_long_absence(self, isolate_thalamus):
        """Simuler last_seen ancien → novelty boost au retour."""
        import time
        from core.thalamus import NOVELTY_BOOST, NOVELTY_ABSENCE_THRESHOLD, EVENT_CATEGORIES
        t = isolate_thalamus
        # Simuler un event vu il y a longtemps
        t._event_last_seen["TISSUE_PATTERN_EMERGED"] = time.time() - NOVELTY_ABSENCE_THRESHOLD - 10
        initial = t._scorecard["TISSUE_PATTERN_EMERGED"]
        await t._on_tissue_pattern({})
        # Doit avoir le boost novelty + boost handler
        assert t._scorecard["TISSUE_PATTERN_EMERGED"] >= initial + NOVELTY_BOOST

    @pytest.mark.asyncio
    async def test_novelty_boost_applies_to_category(self, isolate_thalamus):
        """Le boost novelty touche tous les events de la categorie."""
        from core.thalamus import NOVELTY_BOOST, EVENT_CATEGORIES
        t = isolate_thalamus
        # TISSUE_PATTERN_EMERGED et TISSUE_CREATIVITY_SPIKE sont dans 'emergence'
        initial_pattern = t._scorecard["TISSUE_PATTERN_EMERGED"]
        initial_creativity = t._scorecard["TISSUE_CREATIVITY_SPIKE"]
        # Appel pattern → novelty boost sur toute la categorie 'emergence'
        await t._on_tissue_pattern({})
        assert t._scorecard["TISSUE_CREATIVITY_SPIKE"] >= initial_creativity + NOVELTY_BOOST


# ============================================================
# 35. TestHabituationPersistence (Sprint F)
# ============================================================

class TestHabituationPersistence:

    def test_save_load_preserves_state(self, isolate_thalamus, tmp_path):
        """Save + reset + load → timestamps et last_seen restaures."""
        import time
        from core import thalamus as mod
        t = isolate_thalamus
        now = time.time()
        t._event_timestamps["COUNCIL_END"] = [now - 10, now - 5, now]
        t._event_last_seen["COUNCIL_END"] = now
        t._event_last_seen["REPTILIAN_ALERT"] = now - 100
        t._save()

        # Reset et recharge
        mod.Thalamus.reset_singleton()
        t2 = mod.Thalamus.__new__(mod.Thalamus)
        t2._initialized = False
        t2.__init__()
        assert len(t2._event_timestamps["COUNCIL_END"]) == 3
        assert t2._event_last_seen["COUNCIL_END"] == now
        assert t2._event_last_seen["REPTILIAN_ALERT"] == pytest.approx(now - 100)


# ============================================================
# 36. TestHabituationGetStats (Sprint F)
# ============================================================

class TestHabituationGetStats:

    def test_get_stats_includes_habituation_fields(self, isolate_thalamus):
        """get_stats inclut habituated_events et novel_events."""
        t = isolate_thalamus
        stats = t.get_stats()
        assert "habituated_events" in stats
        assert "novel_events" in stats
        # Au debut, tous les events sont 'novel' (jamais vus)
        assert len(stats["novel_events"]) > 0

    def test_get_health_includes_habituated_count(self, isolate_thalamus):
        """get_health inclut habituated_count."""
        t = isolate_thalamus
        health = t.get_health()
        assert "habituated_count" in health
        assert health["habituated_count"] == 0


# ============================================================
# TestZoneModulation (Couplage Thalamus→Tissu)
# ============================================================

class TestZoneModulation:

    def test_compute_zone_modulation_empty(self, isolate_thalamus):
        """Scorecard vide → dict vide."""
        t = isolate_thalamus
        t._scorecard = {}
        result = t.compute_zone_modulation()
        assert result == {}

    def test_compute_zone_modulation_default(self, isolate_thalamus):
        """Avec scorecard par défaut (0.5), toutes les zones ont un facteur."""
        t = isolate_thalamus
        result = t.compute_zone_modulation()
        assert isinstance(result, dict)
        # Doit contenir au moins quelques zones
        assert len(result) > 0

    def test_compute_zone_modulation_urgence_focused(self, isolate_thalamus):
        """Urgence forte → threat/stability boostées."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        # Mettre tous les events urgence à 1.0
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "urgence":
                t._scorecard[evt] = 1.0
            else:
                t._scorecard[evt] = 0.0
        result = t.compute_zone_modulation()
        # threat et stability doivent être boostés
        assert result.get("threat", 1.0) > 1.0
        assert result.get("stability", 1.0) > 1.0

    def test_compute_zone_modulation_factors_in_range(self, isolate_thalamus):
        """Les facteurs doivent être dans [0.7, 1.5]."""
        t = isolate_thalamus
        # Mettre des valeurs extrêmes
        for evt in t._scorecard:
            t._scorecard[evt] = 1.0
        result = t.compute_zone_modulation()
        for zone, factor in result.items():
            assert 0.7 <= factor <= 1.5, f"{zone}: {factor} hors bornes"

    def test_compute_zone_modulation_low_strength(self, isolate_thalamus):
        """Attention faible → facteurs proches de 0.7."""
        t = isolate_thalamus
        for evt in t._scorecard:
            t._scorecard[evt] = 0.0
        result = t.compute_zone_modulation()
        for zone, factor in result.items():
            assert factor <= 1.0, f"{zone}: {factor} devrait être ≤1.0 avec attention nulle"

    def test_compute_zone_modulation_multiple_categories_same_zone(self, isolate_thalamus):
        """Cognition cible par cognition et deliberation → max des deux."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "cognition":
                t._scorecard[evt] = 0.8
            elif cat == "deliberation":
                t._scorecard[evt] = 0.3
            else:
                t._scorecard[evt] = 0.0
        result = t.compute_zone_modulation()
        # cognition est ciblée par "cognition" et "deliberation"
        # Le max des deux forces s'applique
        assert "cognition" in result

    def test_compute_zone_modulation_emergence(self, isolate_thalamus):
        """Émergence forte → creativity et emotion boostées."""
        t = isolate_thalamus
        from core.thalamus import EVENT_CATEGORIES
        for evt, cat in EVENT_CATEGORIES.items():
            if cat == "emergence":
                t._scorecard[evt] = 0.9
            else:
                t._scorecard[evt] = 0.0
        result = t.compute_zone_modulation()
        assert result.get("creativity", 1.0) > 1.0
        assert result.get("emotion", 1.0) > 1.0


# ============================================================
# Habituation Zonale (Sprint G — Écologie cellulaire)
# ============================================================

class TestZoneHabituation:
    """Habituation thalamique zonale — réduit la modulation des zones chroniquement surchargées."""

    def test_zone_habituation_initial(self, isolate_thalamus):
        """Toutes les zones commencent à 1.0 (pas d'habituation)."""
        t = isolate_thalamus
        for zone in t._ZONE_NAMES:
            assert t._zone_habituation.get(zone, 1.0) == 1.0

    def test_zone_habituation_overload_decays(self, isolate_thalamus):
        """Zone surchargée → facteur diminue."""
        from core.thalamus import ZONE_HABITUATION_DECAY, ZONE_HABITUATION_OVERLOAD
        t = isolate_thalamus
        signals = {"goals": {"activity": ZONE_HABITUATION_OVERLOAD + 1.0}}
        t._update_zone_habituation(signals)
        assert t._zone_habituation["goals"] < 1.0
        assert t._zone_habituation["goals"] == pytest.approx(ZONE_HABITUATION_DECAY)

    def test_zone_habituation_recovery(self, isolate_thalamus):
        """Zone normale → facteur remonte."""
        from core.thalamus import ZONE_HABITUATION_RECOVERY, ZONE_HABITUATION_OVERLOAD
        t = isolate_thalamus
        t._zone_habituation["goals"] = 0.5
        signals = {"goals": {"activity": 0.5}}  # Sous le seuil
        t._update_zone_habituation(signals)
        assert t._zone_habituation["goals"] > 0.5
        assert t._zone_habituation["goals"] == pytest.approx(0.5 * ZONE_HABITUATION_RECOVERY)

    def test_zone_habituation_floor(self, isolate_thalamus):
        """Jamais < ZONE_HABITUATION_MIN."""
        from core.thalamus import ZONE_HABITUATION_MIN, ZONE_HABITUATION_OVERLOAD
        t = isolate_thalamus
        t._zone_habituation["goals"] = ZONE_HABITUATION_MIN
        signals = {"goals": {"activity": ZONE_HABITUATION_OVERLOAD + 5.0}}
        t._update_zone_habituation(signals)
        assert t._zone_habituation["goals"] >= ZONE_HABITUATION_MIN

    def test_zone_habituation_cap(self, isolate_thalamus):
        """Jamais > 1.0."""
        from core.thalamus import ZONE_HABITUATION_RECOVERY
        t = isolate_thalamus
        t._zone_habituation["goals"] = 1.0
        signals = {"goals": {"activity": 0.0}}
        t._update_zone_habituation(signals)
        assert t._zone_habituation["goals"] <= 1.0

    def test_zone_modulation_includes_habituation(self, isolate_thalamus):
        """Facteur final = category strength × habituation."""
        t = isolate_thalamus
        # Mettre une habituation basse sur goals
        t._zone_habituation["goals"] = 0.5
        result = t.compute_zone_modulation()
        # goals est ciblée par "deliberation" → factor basé sur strength
        # Avec habituation 0.5, le facteur final devrait être ≤ au facteur sans habituation
        assert result.get("goals", 1.0) <= 1.0

    def test_zone_habituation_persistence(self, isolate_thalamus, tmp_path):
        """Save/load roundtrip de _zone_habituation."""
        from core import thalamus as mod
        t = isolate_thalamus
        t._zone_habituation["goals"] = 0.42
        t._zone_habituation["threat"] = 0.75
        t._save()
        # Charger dans un nouveau thalamus
        mod.Thalamus.reset_singleton()
        t2 = mod.Thalamus()
        assert t2._zone_habituation.get("goals") == pytest.approx(0.42, abs=0.001)
        assert t2._zone_habituation.get("threat") == pytest.approx(0.75, abs=0.001)

    def test_chronic_overload_attenuated(self, isolate_thalamus):
        """50 cycles de surcharge → modulation proche de ZONE_HABITUATION_MIN."""
        from core.thalamus import ZONE_HABITUATION_MIN, ZONE_HABITUATION_OVERLOAD
        t = isolate_thalamus
        signals = {"goals": {"activity": ZONE_HABITUATION_OVERLOAD + 2.0}}
        for _ in range(50):
            t._update_zone_habituation(signals)
        hab = t._zone_habituation["goals"]
        # 0.98^50 ≈ 0.36 → devrait être proche du plancher 0.3
        assert hab <= 0.4
        assert hab >= ZONE_HABITUATION_MIN
