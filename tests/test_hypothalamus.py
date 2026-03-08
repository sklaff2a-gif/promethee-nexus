# tests/test_hypothalamus.py — Tests Hypothalamus (regulateur homeostatique)
import pytest
import json
import os
import time
from unittest.mock import patch, MagicMock, AsyncMock
from collections import deque


@pytest.fixture(autouse=True)
def isolate_hypothalamus(tmp_path, monkeypatch):
    """Isole le singleton Hypothalamus pour chaque test."""
    from core import hypothalamus as mod
    mod.Hypothalamus.reset_singleton()
    monkeypatch.setattr(mod, "HYPOTHALAMUS_STATE_FILE", str(tmp_path / "hypo_state.json"))
    with patch.object(mod.Hypothalamus, "_load"):
        h = mod.Hypothalamus()
    # Reset des attributs
    h.current_values = {
        "energy": 0.6,
        "stress": 0.3,
        "dopamine": 0.5,
        "cardiac_bpm": 60.0,
        "sleep_pressure": 0.4,
    }
    h.error_signals = {}
    h.corrections_history = deque(maxlen=50)
    h.total_corrections = 0
    h.alarms_triggered = 0
    h._cycle_count = 0
    h._last_regulation = 0.0
    h._alarm_last_fired = {}
    h._alarm_repeat_count = {}
    h._subscribed = False
    mod.hypothalamus = h
    yield h
    mod.Hypothalamus.reset_singleton()


# ===== TestSingleton =====

class TestSingleton:
    def test_singleton_identity(self, isolate_hypothalamus):
        from core.hypothalamus import Hypothalamus
        h2 = Hypothalamus()
        assert h2 is isolate_hypothalamus

    def test_reset_singleton(self, isolate_hypothalamus):
        from core.hypothalamus import Hypothalamus
        old_id = id(isolate_hypothalamus)
        Hypothalamus.reset_singleton()
        with patch.object(Hypothalamus, "_load"):
            h2 = Hypothalamus()
        assert id(h2) != old_id


# ===== TestSetpoints =====

class TestHypothalamusSetpoints:
    def test_error_zero_at_target(self, isolate_hypothalamus):
        """Erreur = 0 quand la valeur est au setpoint."""
        h = isolate_hypothalamus
        # energy=0.6 est le target
        err = h._compute_error("energy")
        assert err == 0.0

    def test_error_positive_above_target(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["energy"] = 0.9  # target=0.6, tolerance=0.15
        err = h._compute_error("energy")
        assert err > 0

    def test_error_negative_below_target(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["energy"] = 0.3  # target=0.6
        err = h._compute_error("energy")
        assert err < 0

    def test_error_clamped_max(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["energy"] = 10.0  # way above
        err = h._compute_error("energy")
        assert err == 1.0

    def test_error_clamped_min(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["energy"] = -5.0  # way below
        err = h._compute_error("energy")
        assert err == -1.0

    def test_all_errors_computed(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        errors = h._compute_all_errors()
        assert set(errors.keys()) == set(h.current_values.keys())

    def test_stress_error(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 0.7  # target=0.3, tolerance=0.2
        err = h._compute_error("stress")
        assert err > 0  # trop de stress

    def test_unknown_variable(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        err = h._compute_error("unknown_var")
        assert err == 0.0


# ===== TestCorrections =====

class TestHypothalamusCorrections:
    def test_no_correction_near_target(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        corr = h._generate_correction("energy", 0.05)
        assert corr["action"] == "none"

    def test_decrease_when_too_high(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        corr = h._generate_correction("stress", 0.8)
        assert corr["action"] == "decrease"
        assert corr["strength"] > 0

    def test_increase_when_too_low(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        corr = h._generate_correction("energy", -0.7)
        assert corr["action"] == "increase"
        assert corr["strength"] > 0

    def test_strength_proportional(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        weak = h._generate_correction("energy", 0.2)
        strong = h._generate_correction("energy", 0.9)
        assert strong["strength"] > weak["strength"]

    def test_energy_low_correction(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["energy"] = 0.2
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("MEMORY_CLEANUP")
        # Energy bas → MEMORY_CLEANUP devrait etre favorise
        assert bonus > 0

    def test_stress_high_favors_calm(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 0.8
        h.error_signals = h._compute_all_errors()
        bonus_calm = h.compute_homeostasis_bonus("JOURNAL_REFLEXION")
        bonus_exciting = h.compute_homeostasis_bonus("CREATIVE_EXPLORATION")
        assert bonus_calm > bonus_exciting

    def test_dopamine_low_favors_creative(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["dopamine"] = 0.1
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("CREATIVE_EXPLORATION")
        assert bonus > 0

    def test_correction_includes_variable(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        corr = h._generate_correction("stress", 0.5)
        assert corr["variable"] == "stress"

    def test_correction_includes_error(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        corr = h._generate_correction("stress", 0.5)
        assert corr["error"] == 0.5

    def test_energy_high_penalizes_light(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["energy"] = 0.9  # energy trop haut
        h.error_signals = h._compute_all_errors()
        # Quand energy est trop haut, les routines legeres ne sont PAS favorisees
        bonus = h.compute_homeostasis_bonus("MEMORY_CLEANUP")
        # Le bonus devrait etre negatif ou nul (l'inverse de quand energy est bas)
        assert bonus <= 0


# ===== TestRegulation =====

class TestHypothalamusRegulation:
    @pytest.mark.asyncio
    async def test_regulate_increments_cycle(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        assert h._cycle_count == 1

    @pytest.mark.asyncio
    async def test_regulate_computes_errors(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 0.9
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        assert "stress" in h.error_signals
        assert h.error_signals["stress"] > 0

    @pytest.mark.asyncio
    async def test_regulate_publishes_event(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_REGULATION"]
        assert len(calls) >= 1

    @pytest.mark.asyncio
    async def test_alarm_on_high_deviation(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0  # error > ALARM_THRESHOLD
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        assert len(alarm_calls) >= 1
        assert h.alarms_triggered > 0

    @pytest.mark.asyncio
    async def test_no_alarm_near_target(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        # Toutes les valeurs au setpoint → pas d'alarme
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        assert len(alarm_calls) == 0

    @pytest.mark.asyncio
    async def test_stability_score_perfect(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        # Toutes au setpoint → stabilite = 1.0
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        score = h._compute_stability_score()
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_stability_score_degraded(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h.current_values["energy"] = 0.1
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        score = h._compute_stability_score()
        assert score < 1.0

    @pytest.mark.asyncio
    async def test_cooldown_on_high_bpm(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["cardiac_bpm"] = 90.0  # way above 60 target
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        cooldown_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_COOLDOWN"]
        assert len(cooldown_calls) >= 1


# ===== TestBusHandlers =====

class TestHypothalamusBusHandlers:
    @pytest.mark.asyncio
    async def test_cardiac_beat_updates_bpm(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h._on_cardiac_beat({"bpm": 85.0})
        assert h.current_values["cardiac_bpm"] == 85.0

    @pytest.mark.asyncio
    async def test_dopamine_update(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        await h._on_dopamine_update({"level": 0.8})
        assert h.current_values["dopamine"] == 0.8

    @pytest.mark.asyncio
    async def test_dopamine_clamped(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        await h._on_dopamine_update({"level": 5.0})
        assert h.current_values["dopamine"] == 1.0

    @pytest.mark.asyncio
    async def test_reptilian_alert_spikes_stress(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 0.3
        await h._on_reptilian_alert({"severity": 0.8})
        assert h.current_values["stress"] > 0.3

    @pytest.mark.asyncio
    async def test_circadian_phase_sleep(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        await h._on_circadian_phase({"phase": "sommeil_profond"})
        assert h.current_values["sleep_pressure"] == 0.9

    @pytest.mark.asyncio
    async def test_routine_complete_updates_energy(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        await h._on_routine_complete({"budget_used": 100, "budget_max": 200})
        assert 0.2 <= h.current_values["energy"] <= 0.9

    @pytest.mark.asyncio
    async def test_circadian_phase_eveil(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        await h._on_circadian_phase({"phase": "eveil"})
        assert h.current_values["sleep_pressure"] == 0.2


# ===== TestScoringBonus =====

class TestHypothalamusScoringBonus:
    def test_bonus_zero_at_equilibrium(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("EXPANSION_CODE")
        assert bonus == 0.0

    def test_bonus_range_lower(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h.current_values["energy"] = 0.0
        h.current_values["dopamine"] = 1.0
        h.current_values["sleep_pressure"] = 1.0
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("EXPANSION_CODE")
        assert bonus >= -1.0

    def test_bonus_range_upper(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h.current_values["energy"] = 0.0
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("MEMORY_CLEANUP")
        assert bonus <= 1.5

    def test_unknown_intent_zero(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 0.9
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("UNKNOWN_INTENT")
        # Unknown intent with no mapping — only sleep_pressure effect possible
        assert -1.0 <= bonus <= 1.5

    def test_sleep_pressure_penalizes_heavy(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.9
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("COUNCIL_DEBATE")
        assert bonus < 0

    def test_sleep_pressure_favors_light(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.9
        h.error_signals = h._compute_all_errors()
        bonus = h.compute_homeostasis_bonus("MEMORY_CLEANUP")
        assert bonus > 0

    def test_no_errors_no_bonus(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        # error_signals vide
        bonus = h.compute_homeostasis_bonus("EXPANSION_CODE")
        assert bonus == 0.0


# ===== TestContext =====

class TestHypothalamusContext:
    def test_context_stable(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.error_signals = h._compute_all_errors()  # all at target
        ctx = h.get_homeostasis_context()
        assert "stable" in ctx.lower()

    def test_context_desequilibre(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h.error_signals = h._compute_all_errors()
        ctx = h.get_homeostasis_context()
        assert "stress" in ctx.lower()

    def test_context_empty_no_errors(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        # Pas d'error_signals du tout
        ctx = h.get_homeostasis_context()
        assert ctx == ""


# ===== TestStats =====

class TestHypothalamusStats:
    def test_get_stats_keys(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        stats = h.get_stats()
        assert "current_values" in stats
        assert "stability_score" in stats
        assert "total_corrections" in stats
        assert "alarms_triggered" in stats

    def test_stability_in_range(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        h.error_signals = h._compute_all_errors()
        stats = h.get_stats()
        assert 0.0 <= stats["stability_score"] <= 1.0


# ===== TestPersistence =====

class TestHypothalamusPersistence:
    def test_save_creates_file(self, isolate_hypothalamus, tmp_path):
        h = isolate_hypothalamus
        h._save()
        from core.hypothalamus import HYPOTHALAMUS_STATE_FILE
        assert os.path.exists(HYPOTHALAMUS_STATE_FILE)

    def test_roundtrip(self, isolate_hypothalamus, tmp_path, monkeypatch):
        from core import hypothalamus as mod
        h = isolate_hypothalamus
        h.current_values["stress"] = 0.9
        h.total_corrections = 42
        h.alarms_triggered = 5
        h._cycle_count = 100
        h._save()

        mod.Hypothalamus.reset_singleton()
        h2 = mod.Hypothalamus()
        assert h2.current_values["stress"] == 0.9
        assert h2.total_corrections == 42
        assert h2.alarms_triggered == 5

    def test_load_missing_file(self, isolate_hypothalamus):
        h = isolate_hypothalamus
        # Pas de fichier → pas d'erreur
        h._load()

    def test_load_corrupt_file(self, isolate_hypothalamus, tmp_path):
        from core.hypothalamus import HYPOTHALAMUS_STATE_FILE
        with open(HYPOTHALAMUS_STATE_FILE, "w") as f:
            f.write("NOT JSON")
        h = isolate_hypothalamus
        h._load()  # Pas de crash

    def test_retrocompatibility(self, isolate_hypothalamus, tmp_path):
        from core.hypothalamus import HYPOTHALAMUS_STATE_FILE
        # Fichier avec champs manquants
        data = {"total_corrections": 10}
        with open(HYPOTHALAMUS_STATE_FILE, "w") as f:
            json.dump(data, f)
        from core import hypothalamus as mod
        mod.Hypothalamus.reset_singleton()
        h2 = mod.Hypothalamus()
        assert h2.total_corrections == 10
        # Les autres champs gardent leur valeur par defaut
        assert h2.alarms_triggered == 0


# ===== TestAlarmCooldown =====

class TestAlarmCooldown:
    """Tests du cooldown per-variable et severity dégressive."""

    @pytest.mark.asyncio
    async def test_alarm_fires_first_time(self, isolate_hypothalamus):
        """Première alarme émise normalement."""
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0  # error > ALARM_THRESHOLD
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 1
        assert stress_alarms[0][0][1]["severity"] == 1.0

    @pytest.mark.asyncio
    async def test_alarm_blocked_during_cooldown(self, isolate_hypothalamus):
        """Alarme supprimée si <120s depuis la dernière."""
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        # Deuxième appel immédiat → cooldown bloque
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 0

    @pytest.mark.asyncio
    async def test_alarm_fires_after_cooldown(self, isolate_hypothalamus):
        """Alarme ré-émise après expiration du cooldown."""
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        # Simuler cooldown expiré
        h._alarm_last_fired["stress"] = time.time() - 200
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 1

    @pytest.mark.asyncio
    async def test_severity_degrades_on_repeat(self, isolate_hypothalamus):
        """2ème alarme a une severity réduite."""
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        # Simuler cooldown expiré pour permettre la 2ème alarme
        h._alarm_last_fired["stress"] = time.time() - 200
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 1
        # severity = max(0.3, 1.0 - 1 * 0.15) = 0.85
        assert stress_alarms[0][0][1]["severity"] == 0.85

    @pytest.mark.asyncio
    async def test_severity_floor(self, isolate_hypothalamus):
        """Après N répétitions, severity ne descend pas sous le floor."""
        from core.hypothalamus import ALARM_SEVERITY_FLOOR
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        # Simuler 20 répétitions passées
        h._alarm_repeat_count["stress"] = 20
        h._alarm_last_fired["stress"] = time.time() - 200
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 1
        assert stress_alarms[0][0][1]["severity"] == ALARM_SEVERITY_FLOOR

    @pytest.mark.asyncio
    async def test_reset_on_recovery(self, isolate_hypothalamus):
        """Variable revenue sous le seuil → compteur reset."""
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        assert h._alarm_repeat_count.get("stress", 0) == 1
        # Stress revient à la normale
        h.current_values["stress"] = 0.3
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        assert "stress" not in h._alarm_repeat_count

    @pytest.mark.asyncio
    async def test_independent_per_variable(self, isolate_hypothalamus):
        """Cooldown energy n'affecte pas stress."""
        h = isolate_hypothalamus
        h.current_values["energy"] = 0.0  # error > threshold
        h.current_values["stress"] = 1.0  # error > threshold
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        # energy en cooldown, mais on reset le cooldown de stress seulement
        h._alarm_last_fired["stress"] = time.time() - 200
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        variables = [c[0][1]["variable"] for c in alarm_calls]
        # stress doit passer, energy doit être bloquée
        assert "stress" in variables
        assert "energy" not in variables

    @pytest.mark.asyncio
    async def test_cooldown_persisted(self, isolate_hypothalamus, tmp_path):
        """Save/load préserve les cooldowns."""
        from core import hypothalamus as mod
        h = isolate_hypothalamus
        h._alarm_last_fired = {"stress": 1000.0, "energy": 2000.0}
        h._alarm_repeat_count = {"stress": 3, "energy": 1}
        h._save()

        mod.Hypothalamus.reset_singleton()
        h2 = mod.Hypothalamus()
        assert h2._alarm_last_fired["stress"] == 1000.0
        assert h2._alarm_repeat_count["stress"] == 3
        assert h2._alarm_last_fired["energy"] == 2000.0
        assert h2._alarm_repeat_count["energy"] == 1
