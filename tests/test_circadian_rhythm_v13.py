"""Tests V13 (2026-04-23) — Pacemaker Circadien Unifié.

V13-A : Processus C (horloge biologique) — force EVEIL→CRÉPUSCULE
        dans la fenêtre [23h, 5h[ avec période réfractaire 12h.
V13-B : Unification dream — _task_dream_consolidation inclut le
        replay MDP V12 + clear trajectory_buffer.

Principe de la période réfractaire : évite le Calendar Bug (re-trigger
immédiat après minuit). Utilise un timestamp biologique au lieu d'un
flag calendaire.
"""
import asyncio
import datetime
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.circadian_rhythm import (
    CIRCADIAN_NIGHT_START,
    CIRCADIAN_NIGHT_END,
    CIRCADIAN_REFRACTORY_SECONDS,
    CircadianRhythm,
    PHASE_EVEIL,
    PHASE_CREPUSCULE,
    SleepReport,
    _task_dream_consolidation,
)


@pytest.fixture
def fresh_circadian():
    CircadianRhythm.reset_singleton()
    c = CircadianRhythm()
    c.phase = PHASE_EVEIL
    c._last_circadian_sleep_time = None
    yield c
    CircadianRhythm.reset_singleton()


# ═══════════════════════════════════════════════════════════════════════
# V13-A — Hook horaire + Période réfractaire
# ═══════════════════════════════════════════════════════════════════════


class TestV13HourTrigger:
    """Le Processus C force le crépuscule dans [23h, 5h[ si refract OK."""

    def test_hour_23_in_night_window(self, fresh_circadian):
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 23, 30)
            mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
            result = fresh_circadian._eval_eveil_transition("full", None)
            assert result == PHASE_CREPUSCULE

    def test_hour_3_in_night_window(self, fresh_circadian):
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 3, 0)
            result = fresh_circadian._eval_eveil_transition("full", None)
            assert result == PHASE_CREPUSCULE

    def test_hour_5_excluded(self, fresh_circadian):
        """5h est la fin EXCLUSIVE de la fenêtre."""
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 5, 0)
            assert fresh_circadian._eval_eveil_transition("full", None) is None

    def test_hour_12_not_triggered(self, fresh_circadian):
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 12, 0)
            assert fresh_circadian._eval_eveil_transition("full", None) is None

    def test_hour_22_just_before_window(self, fresh_circadian):
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 22, 59)
            assert fresh_circadian._eval_eveil_transition("full", None) is None


class TestV13RefractoryPeriod:
    """La période réfractaire 12h évite les rechutes immédiates."""

    def test_no_insomnia_after_midnight_crossing(self, fresh_circadian):
        """REGRESSION TEST : si endormi à 01h lundi, doit pouvoir se
        rendormir lundi soir 23h (22h > 12h de réfractaire).

        C'était LE bug Calendar que la refonte via timestamp évite.
        """
        fresh_circadian._last_circadian_sleep_time = datetime.datetime(2026, 4, 21, 1, 0)
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 21, 23, 30)
            mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
            result = fresh_circadian._eval_eveil_transition("full", None)
            assert result == PHASE_CREPUSCULE, \
                "Le bug Calendar aurait verrouille ce cas — doit etre fix"

    def test_refractory_blocks_recent_sleep(self, fresh_circadian):
        """Si endormi il y a 2h, pas de re-trigger même en fenêtre nuit."""
        fresh_circadian._last_circadian_sleep_time = datetime.datetime(2026, 4, 23, 1, 0)
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 3, 0)
            result = fresh_circadian._eval_eveil_transition("full", None)
            assert result is None

    def test_refractory_first_time_ok(self, fresh_circadian):
        """_last_circadian_sleep_time = None (jamais endormi) → OK."""
        assert fresh_circadian._last_circadian_sleep_time is None
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 23, 30)
            mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
            result = fresh_circadian._eval_eveil_transition("full", None)
            assert result == PHASE_CREPUSCULE


class TestV13StatePersistence:
    """_last_circadian_sleep_time doit survivre aux reboots."""

    def test_save_load_roundtrip(self, fresh_circadian, tmp_path):
        ts = datetime.datetime(2026, 4, 23, 1, 0, 0)
        fresh_circadian._last_circadian_sleep_time = ts

        state_file = tmp_path / "circ.json"
        with patch("core.circadian_rhythm.CIRCADIAN_STATE_FILE", str(state_file)):
            fresh_circadian.save()
            CircadianRhythm.reset_singleton()
            c2 = CircadianRhythm()
            c2._load()
        assert c2._last_circadian_sleep_time == ts
        CircadianRhythm.reset_singleton()

    def test_load_legacy_state_no_timestamp(self, fresh_circadian, tmp_path):
        """Un JSON pre-V13 (sans last_circadian_sleep_time) ne doit pas lever."""
        state_file = tmp_path / "circ_old.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"phase": "eveil", "phase_since": 0.0,
                       "total_sleep_cycles": 5, "stats": {}}, f)
        with patch("core.circadian_rhythm.CIRCADIAN_STATE_FILE", str(state_file)):
            CircadianRhythm.reset_singleton()
            c = CircadianRhythm()
            c._load()
        assert c._last_circadian_sleep_time is None
        CircadianRhythm.reset_singleton()


class TestV13NonRegressionLegacyTriggers:
    """Les triggers historiques (budget, CPU/RAM, threat) restent actifs."""

    def test_budget_exhausted_still_triggers_day(self, fresh_circadian):
        """En pleine journée (ex 14h), budget épuisé → crépuscule (inchangé)."""
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 14, 0)
            result = fresh_circadian._eval_eveil_transition("exhausted", None)
            assert result == PHASE_CREPUSCULE

    def test_cpu_ram_pressure_day(self, fresh_circadian):
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 14, 0)
            health = {"cpu_percent": 90, "ram_percent": 85}
            assert fresh_circadian._eval_eveil_transition("full", health) == PHASE_CREPUSCULE

    def test_no_trigger_day_normal_pressure(self, fresh_circadian):
        with patch("core.circadian_rhythm.datetime") as mock_dt:
            mock_dt.datetime.now.return_value = datetime.datetime(2026, 4, 23, 14, 0)
            health = {"cpu_percent": 30, "ram_percent": 50}
            assert fresh_circadian._eval_eveil_transition("full", health) is None


# ═══════════════════════════════════════════════════════════════════════
# V13-B — Unification dream (replay MDP dans _task_dream_consolidation)
# ═══════════════════════════════════════════════════════════════════════


class TestV13DreamUnification:
    """_task_dream_consolidation doit appeler le replay MDP + clear buffer."""

    @pytest.mark.asyncio
    async def test_dream_task_calls_mdp_replay(self, fresh_circadian):
        fresh_circadian._sleep_report = SleepReport()

        # Mocks pour éviter de déclencher les vrais singletons
        with patch("core.synaptic_network.cortex") as mock_cortex, \
             patch("core.hippocampus.hippocampus") as mock_hippo, \
             patch("core.basal_ganglia.ganglia") as mock_ganglia:
            mock_cortex.dream_consolidation.return_value = {
                "new_connections": 10, "pruned": 3
            }
            # Trajectory avec 3 épisodes simulés
            mock_hippo.get_recent_trajectory.return_value = [
                MagicMock(), MagicMock(), MagicMock(),
            ]
            mock_ganglia.update_sequential.return_value = 2  # 2 transitions

            result = await _task_dream_consolidation(fresh_circadian)

        assert "MDP transitions" in result
        assert mock_ganglia.update_sequential.called
        # V12.1b : buffer doit être vidé après replay
        assert mock_hippo.trajectory_buffer.clear.called
        # SleepReport enrichi
        assert fresh_circadian._sleep_report.mdp_transitions == 2

    @pytest.mark.asyncio
    async def test_dream_task_no_mdp_if_trajectory_short(self, fresh_circadian):
        fresh_circadian._sleep_report = SleepReport()
        with patch("core.synaptic_network.cortex") as mock_cortex, \
             patch("core.hippocampus.hippocampus") as mock_hippo, \
             patch("core.basal_ganglia.ganglia") as mock_ganglia:
            mock_cortex.dream_consolidation.return_value = {
                "new_connections": 5, "pruned": 1
            }
            mock_hippo.get_recent_trajectory.return_value = [MagicMock()]  # 1 seul
            result = await _task_dream_consolidation(fresh_circadian)

        assert "0 MDP transitions" in result
        # Pas de update_sequential appelé (trajectory < 2)
        assert not mock_ganglia.update_sequential.called
        assert fresh_circadian._sleep_report.mdp_transitions == 0
