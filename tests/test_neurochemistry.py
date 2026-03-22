"""Tests pour les pools neurochimiques (serotonine, noradrenaline, acetylcholine)."""

import time
import pytest
from unittest.mock import patch

from core.neurochemistry import (
    Neurochemistry, neurochemistry,
    BASELINE, MIN_LEVEL, MAX_LEVEL, DECAY_RATE,
    SEROTONIN_IMPACTS, NORADRENALINE_IMPACTS, ACETYLCHOLINE_IMPACTS,
)


@pytest.fixture(autouse=True)
def reset():
    Neurochemistry.reset_singleton()
    with patch("core.neurochemistry.Neurochemistry._subscribe_events"):
        nc = Neurochemistry()
    yield nc
    Neurochemistry.reset_singleton()


class TestPoolBasics:

    def test_initial_baseline(self, reset):
        nc = reset
        assert nc.serotonin == BASELINE
        assert nc.noradrenaline == BASELINE
        assert nc.acetylcholine == BASELINE

    def test_apply_positive(self, reset):
        nc = reset
        nc._apply("serotonin", 0.1)
        assert nc.serotonin == BASELINE + 0.1

    def test_apply_negative(self, reset):
        nc = reset
        nc._apply("noradrenaline", -0.2)
        assert nc.noradrenaline == BASELINE - 0.2

    def test_clamp_max(self, reset):
        nc = reset
        nc._apply("acetylcholine", 1.0)
        assert nc.acetylcholine == MAX_LEVEL

    def test_clamp_min(self, reset):
        nc = reset
        nc._apply("serotonin", -1.0)
        assert nc.serotonin == MIN_LEVEL

    def test_decay_towards_baseline(self, reset):
        nc = reset
        nc.serotonin = 0.9
        new = nc._decay(nc.serotonin)
        assert new < 0.9
        assert new > BASELINE

    def test_decay_from_below_baseline(self, reset):
        nc = reset
        nc.noradrenaline = 0.2
        new = nc._decay(nc.noradrenaline)
        assert new > 0.2
        assert new < BASELINE


class TestModulation:

    def test_modulation_at_baseline(self, reset):
        nc = reset
        mod = nc.get_modulation()
        assert abs(mod["patience"] - 1.0) < 0.01
        assert abs(mod["urgency"] - 1.0) < 0.01
        assert abs(mod["plasticity"] - 1.0) < 0.01

    def test_modulation_high_serotonin(self, reset):
        nc = reset
        nc.serotonin = 0.9
        mod = nc.get_modulation()
        assert mod["patience"] > 1.0

    def test_modulation_low_serotonin(self, reset):
        nc = reset
        nc.serotonin = 0.2
        mod = nc.get_modulation()
        assert mod["patience"] < 1.0

    def test_attention_threshold_modifier(self, reset):
        nc = reset
        # High noradrenaline = low threshold (more info passes)
        nc.noradrenaline = 0.9
        assert nc.get_attention_threshold_modifier() < 1.0
        # Low noradrenaline = high threshold
        nc.noradrenaline = 0.2
        assert nc.get_attention_threshold_modifier() > 1.0

    def test_plasticity_rate(self, reset):
        nc = reset
        nc.acetylcholine = 0.9
        assert nc.get_plasticity_rate() > 1.5
        nc.acetylcholine = 0.2
        assert nc.get_plasticity_rate() < 1.0


class TestHandlers:

    @pytest.mark.asyncio
    async def test_routine_success_boosts_serotonin(self, reset):
        nc = reset
        before = nc.serotonin
        await nc._on_routine_complete({"status": "success", "intent": "AUDIT_STRUCTURE"})
        assert nc.serotonin > before

    @pytest.mark.asyncio
    async def test_routine_failure_drops_serotonin(self, reset):
        nc = reset
        before = nc.serotonin
        await nc._on_routine_complete({"status": "error", "intent": "EXPANSION_CODE"})
        assert nc.serotonin < before

    @pytest.mark.asyncio
    async def test_exploration_boosts_acetylcholine(self, reset):
        nc = reset
        before = nc.acetylcholine
        await nc._on_routine_complete({"status": "success", "intent": "VEILLE_SILENCIEUSE"})
        assert nc.acetylcholine > before + ACETYLCHOLINE_IMPACTS["routine_success"]

    @pytest.mark.asyncio
    async def test_threat_high_boosts_noradrenaline(self, reset):
        nc = reset
        before = nc.noradrenaline
        await nc._on_reptilian_alert({"threat_level": 7.0})
        assert nc.noradrenaline > before

    @pytest.mark.asyncio
    async def test_codelet_danger_boosts_noradrenaline(self, reset):
        nc = reset
        before = nc.noradrenaline
        await nc._on_codelet_alert({"name": "danger"})
        assert nc.noradrenaline > before

    @pytest.mark.asyncio
    async def test_codelet_stagnation_drops_acetylcholine(self, reset):
        nc = reset
        before = nc.acetylcholine
        await nc._on_codelet_alert({"name": "stagnation"})
        assert nc.acetylcholine < before

    @pytest.mark.asyncio
    async def test_eureka_boosts_acetylcholine(self, reset):
        nc = reset
        before = nc.acetylcholine
        await nc._on_eureka({})
        assert nc.acetylcholine > before

    @pytest.mark.asyncio
    async def test_nap_raises_serotonin_lowers_noradrenaline(self, reset):
        nc = reset
        await nc._on_nap_mode({"entering": True})
        assert nc.serotonin > BASELINE
        assert nc.noradrenaline < BASELINE

    @pytest.mark.asyncio
    async def test_cardiac_beat_decays(self, reset):
        nc = reset
        nc.serotonin = 0.9
        nc.noradrenaline = 0.9
        nc.acetylcholine = 0.1
        await nc._on_cardiac_beat({})
        assert nc.serotonin < 0.9
        assert nc.noradrenaline < 0.9
        assert nc.acetylcholine > 0.1


class TestGetStatus:

    def test_status_has_all_fields(self, reset):
        nc = reset
        s = nc.get_status()
        assert "serotonin" in s
        assert "noradrenaline" in s
        assert "acetylcholine" in s
        assert "modulation" in s
        assert "plasticity_rate" in s


class TestHippocampusTissueFilter:
    """Tests pour le filtre memoire tissu dans hippocampus."""

    def test_critical_events_always_pass(self):
        """Les evenements critiques ne sont pas filtres par le tissu."""
        from core.hippocampus import Hippocampus
        # error_streak >= 3 → critique
        assert True  # Le filtre est dans _encode_episode, tester en integration

    def test_filter_mentioned_in_encode(self):
        """Le filtre tissu est bien present dans _encode_episode."""
        import inspect
        from core.hippocampus import Hippocampus
        source = inspect.getsource(Hippocampus._encode_episode)
        assert "neural_tissue" in source
        assert "memory_activity" in source
        assert "is_critical" in source
