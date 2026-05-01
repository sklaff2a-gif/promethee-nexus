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
    h._alarm_sustained_count = {}
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
        # L'alarme nécessite ALARM_SUSTAINED_CYCLES cycles consécutifs
        from core.hypothalamus import ALARM_SUSTAINED_CYCLES
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            for _ in range(ALARM_SUSTAINED_CYCLES):
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
        # V14.5 : on patch _apply_synaptic_debt_pressure pour qu'il ne touche pas
        # sleep_pressure (sinon relief actif si dette > seuil bas, ce qui est
        # un comportement valide mais non testé ici).
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock), \
             patch.object(h, "_apply_synaptic_debt_pressure", return_value=None):
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
        """Première alarme émise après ALARM_SUSTAINED_CYCLES cycles."""
        from core.hypothalamus import ALARM_SUSTAINED_CYCLES
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0  # error > ALARM_THRESHOLD
        # Pré-seed : il ne reste plus qu'un cycle pour déclencher
        h._alarm_sustained_count["stress"] = ALARM_SUSTAINED_CYCLES - 1
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 1
        assert stress_alarms[0][0][1]["severity"] == 1.0

    @pytest.mark.asyncio
    async def test_alarm_blocked_during_cooldown(self, isolate_hypothalamus):
        """Alarme supprimée si <120s depuis la dernière."""
        from core.hypothalamus import ALARM_SUSTAINED_CYCLES
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h._alarm_sustained_count["stress"] = ALARM_SUSTAINED_CYCLES - 1
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        # Deuxième appel immédiat → cooldown bloque (sustained déjà atteint)
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 0

    @pytest.mark.asyncio
    async def test_alarm_fires_after_cooldown(self, isolate_hypothalamus):
        """Alarme ré-émise après expiration du cooldown."""
        from core.hypothalamus import ALARM_SUSTAINED_CYCLES
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h._alarm_sustained_count["stress"] = ALARM_SUSTAINED_CYCLES - 1
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
        from core.hypothalamus import ALARM_SUSTAINED_CYCLES
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h._alarm_sustained_count["stress"] = ALARM_SUSTAINED_CYCLES - 1
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
        from core.hypothalamus import ALARM_SEVERITY_FLOOR, ALARM_SUSTAINED_CYCLES
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        # Simuler 20 répétitions passées + sustained déjà atteint
        h._alarm_repeat_count["stress"] = 20
        h._alarm_last_fired["stress"] = time.time() - 200
        h._alarm_sustained_count["stress"] = ALARM_SUSTAINED_CYCLES - 1
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock) as mock_pub:
            await h.regulate()
        alarm_calls = [c for c in mock_pub.call_args_list if c[0][0] == "HYPOTHALAMUS_ALARM"]
        stress_alarms = [c for c in alarm_calls if c[0][1]["variable"] == "stress"]
        assert len(stress_alarms) == 1
        assert stress_alarms[0][0][1]["severity"] == ALARM_SEVERITY_FLOOR

    @pytest.mark.asyncio
    async def test_reset_on_recovery(self, isolate_hypothalamus):
        """Variable revenue sous le seuil → compteurs reset."""
        from core.hypothalamus import ALARM_SUSTAINED_CYCLES
        h = isolate_hypothalamus
        h.current_values["stress"] = 1.0
        h._alarm_sustained_count["stress"] = ALARM_SUSTAINED_CYCLES - 1
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        assert h._alarm_repeat_count.get("stress", 0) == 1
        # Stress revient à la normale
        h.current_values["stress"] = 0.3
        with patch("core.hypothalamus.bus.publish", new_callable=AsyncMock):
            await h.regulate()
        assert "stress" not in h._alarm_repeat_count
        assert "stress" not in h._alarm_sustained_count

    @pytest.mark.asyncio
    async def test_independent_per_variable(self, isolate_hypothalamus):
        """Cooldown energy n'affecte pas stress."""
        from core.hypothalamus import ALARM_SUSTAINED_CYCLES
        h = isolate_hypothalamus
        h.current_values["energy"] = 0.0  # error > threshold
        h.current_values["stress"] = 1.0  # error > threshold
        h._alarm_sustained_count["energy"] = ALARM_SUSTAINED_CYCLES - 1
        h._alarm_sustained_count["stress"] = ALARM_SUSTAINED_CYCLES - 1
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


# ===== V14.2 Pilier 1 nocicepteurs : pression cognitive =====

class TestSynapticDebtPressure:
    """Tests du couplage stagnation synaptique → sleep_pressure (V14.2)."""

    def _mock_cortex(self, last_dream_time):
        """Helper pour mocker core.synaptic_network.cortex."""
        from unittest.mock import MagicMock
        m = MagicMock()
        m.cortex = MagicMock()
        m.cortex._last_dream_time = last_dream_time
        return m

    def test_apply_returns_none_si_pas_de_dream(self, isolate_hypothalamus):
        """Si _last_dream_time = 0, retourne None (no-op)."""
        h = isolate_hypothalamus
        with patch.dict("sys.modules", {"core.synaptic_network": self._mock_cortex(0.0)}):
            r = h._apply_synaptic_debt_pressure()
        assert r is None
        assert h.current_values["sleep_pressure"] == 0.4  # inchange

    def test_apply_no_op_si_dette_faible_et_pression_au_floor(self, isolate_hypothalamus):
        """V14.5 : dette faible (z<1.0) + pression deja au floor circadien → no-op.

        Avant V14.5, le test verifiait juste 'dette faible → no-op', mais V14.5
        a introduit la branche relief qui RELACHE la pression si elle est au-dessus
        du floor. Le no-op pur n'arrive donc plus que si pression == floor.
        """
        from core import baseline_tracker as bt_mod
        from unittest.mock import MagicMock
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.2  # exactement au floor eveil
        recent = time.time() - 4 * 3600
        circ_mock = MagicMock()
        circ_mock.circadian = MagicMock()
        circ_mock.circadian.phase = "eveil"
        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(recent),
            "core.circadian_rhythm": circ_mock,
        }):
            r = h._apply_synaptic_debt_pressure()
        assert r is None
        assert h.current_values["sleep_pressure"] == 0.2
        bt_mod.BaselineTracker.reset_singleton()

    def test_apply_incremente_si_dette_haute(self, isolate_hypothalamus):
        """Dette de 24h (z=2.67 sur baseline mu=8 sigma=6) declenche bump."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        old = time.time() - 24 * 3600
        with patch.dict("sys.modules", {"core.synaptic_network": self._mock_cortex(old)}):
            r = h._apply_synaptic_debt_pressure()
        assert r is not None, "Dette de 24h doit declencher"
        assert r["zscore"] >= 1.5
        assert r["bump"] > 0
        assert h.current_values["sleep_pressure"] > 0.4
        bt_mod.BaselineTracker.reset_singleton()

    def test_apply_plafonne_a_ceiling(self, isolate_hypothalamus):
        """Sleep_pressure ne depasse jamais SYNAPTIC_DEBT_PRESSURE_CEILING."""
        from core import baseline_tracker as bt_mod
        from core.hypothalamus import SYNAPTIC_DEBT_PRESSURE_CEILING
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.94  # juste sous le plafond
        old = time.time() - 100 * 3600  # dette enorme
        with patch.dict("sys.modules", {"core.synaptic_network": self._mock_cortex(old)}):
            r = h._apply_synaptic_debt_pressure()
        assert h.current_values["sleep_pressure"] <= SYNAPTIC_DEBT_PRESSURE_CEILING
        bt_mod.BaselineTracker.reset_singleton()

    def test_apply_robust_aux_exceptions(self, isolate_hypothalamus):
        """Si cortex absent ou broken, retourne None sans crash."""
        h = isolate_hypothalamus
        broken = MagicMock()
        broken.cortex = None  # AttributeError sur _last_dream_time
        with patch.dict("sys.modules", {"core.synaptic_network": broken}):
            r = h._apply_synaptic_debt_pressure()
        # Soit None, soit un dict — surtout pas une exception remontee
        assert r is None or isinstance(r, dict)
        # sleep_pressure doit rester valide
        assert 0.0 <= h.current_values["sleep_pressure"] <= 1.0

    @pytest.mark.asyncio
    async def test_regulate_appelle_apply_synaptic_debt(self, isolate_hypothalamus):
        """regulate() doit appeler _apply_synaptic_debt_pressure une fois par cycle."""
        h = isolate_hypothalamus
        with patch.object(h, "_apply_synaptic_debt_pressure", return_value=None) as mock_apply:
            await h.regulate()
        mock_apply.assert_called_once()



# ===== V14.5 — Descente symétrique sleep_pressure =====

class TestSynapticDebtRelief:
    """V14.5 : la pression doit redescendre quand la dette est résolue,
    mais sans franchir le plancher circadien."""

    def _mock_cortex(self, last_dream_time):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.cortex = MagicMock()
        m.cortex._last_dream_time = last_dream_time
        return m

    def _mock_circadian(self, phase: str):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.circadian = MagicMock()
        m.circadian.phase = phase
        return m

    def test_relief_actif_si_z_bas_et_pression_haute(self, isolate_hypothalamus):
        """Dette fraîche (z < 1) ET pression injectée haute → relief actif."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.85  # post-pic
        recent = time.time() - 1 * 3600  # 1h, z bien sous baseline mu=8

        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(recent),
            "core.circadian_rhythm": self._mock_circadian("eveil"),
        }):
            r = h._apply_synaptic_debt_pressure()

        assert r is not None, "Relief doit s'activer"
        assert r["action"] == "relief"
        assert h.current_values["sleep_pressure"] < 0.85, "Pression doit baisser"
        assert h.current_values["sleep_pressure"] >= 0.2, "Mais pas sous floor eveil"
        bt_mod.BaselineTracker.reset_singleton()

    def test_relief_respecte_floor_eveil(self, isolate_hypothalamus):
        """En éveil, la pression ne descend pas sous 0.2 même après relief répétés."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.25  # juste au-dessus du floor 0.2
        recent = time.time() - 1 * 3600

        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(recent),
            "core.circadian_rhythm": self._mock_circadian("eveil"),
        }):
            for _ in range(20):
                h._apply_synaptic_debt_pressure()

        assert h.current_values["sleep_pressure"] >= 0.2, \
            "Floor eveil 0.2 doit être respecté"
        assert h.current_values["sleep_pressure"] <= 0.25, \
            "Mais pression doit avoir baissé"
        bt_mod.BaselineTracker.reset_singleton()

    def test_relief_floor_dynamique_sommeil_profond(self, isolate_hypothalamus):
        """En sommeil_profond, le floor monte à 0.9 — relief NE descend PAS sous."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.95  # max
        recent = time.time() - 1 * 3600

        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(recent),
            "core.circadian_rhythm": self._mock_circadian("sommeil_profond"),
        }):
            for _ in range(10):
                h._apply_synaptic_debt_pressure()

        assert h.current_values["sleep_pressure"] >= 0.9, \
            "Floor sommeil_profond 0.9 doit être respecté"
        bt_mod.BaselineTracker.reset_singleton()

    def test_relief_no_op_si_pression_deja_au_floor(self, isolate_hypothalamus):
        """Si la pression est déjà au plancher circadien, no-op (return None)."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.20  # exactement au floor eveil
        recent = time.time() - 1 * 3600

        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(recent),
            "core.circadian_rhythm": self._mock_circadian("eveil"),
        }):
            r = h._apply_synaptic_debt_pressure()

        assert r is None
        assert h.current_values["sleep_pressure"] == 0.20
        bt_mod.BaselineTracker.reset_singleton()

    def test_hysteresis_zone_neutre_no_op(self, isolate_hypothalamus):
        """z dans [1.0, 1.5] (zone neutre) → no-op, pression inchangée."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.6
        # baseline nominal : mu=8 sigma=6 → pour z=1.2, dette = 8 + 1.2*6 = 15.2h
        in_zone = time.time() - 15.2 * 3600

        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(in_zone),
            "core.circadian_rhythm": self._mock_circadian("eveil"),
        }):
            r = h._apply_synaptic_debt_pressure()

        assert r is None, "Zone neutre [1, 1.5] doit être no-op"
        assert h.current_values["sleep_pressure"] == 0.6, "Pression inchangée"
        bt_mod.BaselineTracker.reset_singleton()

    def test_relief_event_publie_avec_action(self, isolate_hypothalamus):
        """Le dict retourné doit contenir action=relief + circadian_floor."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        h.current_values["sleep_pressure"] = 0.9
        recent = time.time() - 1 * 3600

        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(recent),
            "core.circadian_rhythm": self._mock_circadian("eveil"),
        }):
            r = h._apply_synaptic_debt_pressure()

        assert r is not None
        assert r["action"] == "relief"
        assert "circadian_floor" in r
        assert r["circadian_floor"] == 0.2
        assert "relief" in r
        assert "pressure_after" in r
        bt_mod.BaselineTracker.reset_singleton()

    def test_montee_inchangee_action_rise(self, isolate_hypothalamus):
        """V14.2 préservée : montée renvoie action=rise (pas action=relief)."""
        from core import baseline_tracker as bt_mod
        bt_mod.BaselineTracker.reset_singleton()
        h = isolate_hypothalamus
        old = time.time() - 24 * 3600  # z = 2.67

        with patch.dict("sys.modules", {
            "core.synaptic_network": self._mock_cortex(old),
            "core.circadian_rhythm": self._mock_circadian("eveil"),
        }):
            r = h._apply_synaptic_debt_pressure()

        assert r is not None
        assert r["action"] == "rise"
        assert "bump" in r
        assert "circadian_floor" not in r  # pas de floor sur montée
        bt_mod.BaselineTracker.reset_singleton()
