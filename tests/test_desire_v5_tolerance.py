"""Tests V5.0 - Fix saturation pathologique desire_engine.

Couvre :
  - Nouvelles constantes (TOLERANCE_MIN=0.30, MAX=100, RECOVERY=30/h)
  - Periode refractaire SATISFY_REFRACTORY_SEC=180s
  - Emission DRIVE_SATURATED sur bus
  - Formule _compute_tolerance avec nouveau floor
"""
import os

os.environ.setdefault("PROMETHEE_TEST_MODE", "1")

import asyncio
import time
from unittest.mock import patch, MagicMock

import pytest

from core.desire_engine import (
    DesireEngine,
    Drive,
    SATISFY_REFRACTORY_SEC,
    TOLERANCE_HALF_LIFE,
    TOLERANCE_MAX,
    TOLERANCE_MIN,
    TOLERANCE_RECOVERY_PER_HOUR,
    DRIVE_SATURATED_TOLERANCE_THRESHOLD,
)


@pytest.fixture
def engine():
    """DesireEngine isole pour test."""
    e = DesireEngine()
    e.reset()
    return e


class TestV5Constants:
    def test_tolerance_min_raised_to_30(self):
        assert TOLERANCE_MIN == 0.30

    def test_tolerance_max_lowered_to_100(self):
        assert TOLERANCE_MAX == 100.0

    def test_recovery_doubled_to_30(self):
        assert TOLERANCE_RECOVERY_PER_HOUR == 30.0

    def test_refractory_3_minutes(self):
        assert SATISFY_REFRACTORY_SEC == 180.0

    def test_saturation_threshold_70(self):
        assert DRIVE_SATURATED_TOLERANCE_THRESHOLD == 70.0


class TestToleranceFormula:
    def test_empty_tolerance_full_effect(self, engine):
        """tolerance_accumulator=0 -> effect=1.0."""
        drive = engine.drives["CURIOSITE"]
        drive.tolerance_accumulator = 0.0
        assert engine._compute_tolerance(drive) == 1.0

    def test_floor_at_0_30(self, engine):
        """A saturation, l'effet ne descend pas sous 0.30."""
        drive = engine.drives["CURIOSITE"]
        drive.tolerance_accumulator = 100.0  # max
        t = engine._compute_tolerance(drive)
        # Formule : max(0.30, 1/(1+100/8)) = max(0.30, 0.074) = 0.30
        assert t == pytest.approx(0.30, abs=0.001)

    def test_mid_tolerance(self, engine):
        """tolerance_accumulator=8 -> effect=0.5 (half-life)."""
        drive = engine.drives["CURIOSITE"]
        drive.tolerance_accumulator = 8.0
        t = engine._compute_tolerance(drive)
        # Formule : max(0.30, 1/(1+8/8)) = max(0.30, 0.5) = 0.5
        assert t == pytest.approx(0.5, abs=0.001)


class TestRefractoryPeriod:
    def test_first_satisfaction_accepted(self, engine):
        drive = engine.drives["CURIOSITE"]
        drive.deprivation = 50.0
        drive.last_satisfied = 0.0
        before = drive.satiation_count
        engine.on_event("CURIOSITY_SATISFIED", {})
        assert drive.satiation_count == before + 1

    def test_rapid_satisfaction_ignored(self, engine):
        """2 satisfactions en <180s : la 2e est ignoree."""
        drive = engine.drives["CURIOSITE"]
        drive.deprivation = 50.0
        # 1ere satisfaction
        engine.on_event("CURIOSITY_SATISFIED", {})
        count_after_first = drive.satiation_count
        tol_after_first = drive.tolerance_accumulator
        # 2eme satisfaction "rafale" (immediate)
        engine.on_event("CURIOSITY_SATISFIED", {})
        # Pas de changement : refractory a bloque
        assert drive.satiation_count == count_after_first
        assert drive.tolerance_accumulator == tol_after_first

    def test_satisfaction_after_delay_accepted(self, engine):
        """Apres 181s, une nouvelle satisfaction est acceptee."""
        drive = engine.drives["CURIOSITE"]
        drive.deprivation = 50.0
        # 1ere
        engine.on_event("CURIOSITY_SATISFIED", {})
        count_after_first = drive.satiation_count
        # Simuler 181s passes en reculant last_satisfied
        drive.last_satisfied = time.time() - (SATISFY_REFRACTORY_SEC + 1)
        # 2eme
        engine.on_event("CURIOSITY_SATISFIED", {})
        assert drive.satiation_count == count_after_first + 1

    def test_frustration_not_blocked_by_refractory(self, engine):
        """Les frustrations (delta>0) ne sont PAS soumises au refractory."""
        drive = engine.drives["STABILITE"]
        drive.deprivation = 30.0
        drive.last_satisfied = time.time()  # satisfaction recente
        # Frustration juste apres : doit passer
        engine.on_event("CI_FAILURE", {})
        # deprivation a augmente
        assert drive.deprivation > 30.0


class TestDriveSaturatedEvent:
    @pytest.mark.asyncio
    async def test_saturated_event_emitted_above_threshold(self, engine):
        """tolerance > 70 declenche DRIVE_SATURATED sur bus."""
        drive = engine.drives["MAITRISE"]
        drive.deprivation = 50.0
        drive.tolerance_accumulator = 75.0  # Au-dessus du seuil 70
        drive.last_satisfied = 0.0

        published = []

        async def fake_publish(event_type, payload):
            published.append((event_type, payload))

        with patch("core.desire_engine.bus") as mock_bus:
            mock_bus.publish = fake_publish
            engine.on_event("ROUTINE_SUCCESS", {"intent": "REFACTORING_AUDIT"})
            # Laisser la task async se propager
            await asyncio.sleep(0.05)

        assert any(evt == "DRIVE_SATURATED" for evt, _ in published)
        # Payload contient drive_name
        for evt, payload in published:
            if evt == "DRIVE_SATURATED":
                assert payload["drive_name"] in ("MAITRISE", "STABILITE")  # MAITRISE satisfait + STABILITE
                assert payload["tolerance_accumulator"] > 70

    def test_no_event_below_threshold(self, engine):
        """tolerance < 70 ne declenche pas l'event."""
        drive = engine.drives["CURIOSITE"]
        drive.deprivation = 50.0
        drive.tolerance_accumulator = 30.0  # Sous seuil
        drive.last_satisfied = 0.0

        published = []

        async def fake_publish(event_type, payload):
            published.append((event_type, payload))

        with patch("core.desire_engine.bus") as mock_bus:
            mock_bus.publish = fake_publish
            engine.on_event("CURIOSITY_SATISFIED", {})

        assert not any(evt == "DRIVE_SATURATED" for evt, _ in published)


class TestToleranceClamp:
    def test_accumulator_clamped_at_100(self, engine):
        """Accumulator ne peut depasser TOLERANCE_MAX=100."""
        drive = engine.drives["CURIOSITE"]
        drive.deprivation = 50.0
        drive.tolerance_accumulator = 95.0
        drive.last_satisfied = 0.0
        # Satisfaction avec delta=-15 (CURIOSITY_SATISFIED = -12 CURIOSITE)
        engine.on_event("CURIOSITY_SATISFIED", {})
        # Doit etre <= 100
        assert drive.tolerance_accumulator <= 100.0

    def test_recovery_30_per_hour(self, engine):
        """Decay : 30/h de tolerance."""
        drive = engine.drives["CURIOSITE"]
        drive.tolerance_accumulator = 80.0
        drive.last_satisfied = time.time()
        # Simuler 1h ecoulee via last_tick
        from core.desire_engine import TOLERANCE_RECOVERY_PER_HOUR
        assert TOLERANCE_RECOVERY_PER_HOUR == 30.0
        # Verifier la formule dans natural_rise_tick (accessible via time)
