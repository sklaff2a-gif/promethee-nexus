# tests/test_sensorium_calibration.py
"""Sprint 5 Sensorium — Calibration avancee et anti-oscillation."""

from collections import deque

import pytest

from core.sensorium import (
    Sensorium,
    SENSE_NAMES,
    EMA_ALPHAS,
    OSCILLATION_PERIOD_THRESHOLD,
    OSCILLATION_AMPLITUDE_THRESHOLD,
    OSCILLATION_DAMPING,
    CALIBRATION_BUFFER_MAX,
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Isole chaque test : reset singleton, state file dans tmp."""
    Sensorium.reset_singleton()
    state_file = str(tmp_path / "sensorium_state.json")
    monkeypatch.setattr("core.sensorium.SENSORIUM_STATE_FILE", state_file)
    s = Sensorium()
    s._gpu_backend = "heuristic"
    s._gpu_name = ""
    yield s
    Sensorium.reset_singleton()


class TestOscillationDetection:
    """Sprint 5 Sensorium : detection et amortissement des oscillations."""

    def test_oscillation_detected(self, isolate):
        """Sequence oscillante → True."""
        s = isolate
        # Remplir le buffer avec une oscillation nette (zigzag)
        buf = deque(maxlen=CALIBRATION_BUFFER_MAX)
        for i in range(OSCILLATION_PERIOD_THRESHOLD):
            val = 80.0 if i % 2 == 0 else 60.0  # Amplitude = 20
            buf.append(val)
        s._calibration_buffers["thermoception"] = buf

        assert s._detect_oscillation("thermoception") is True

    def test_stable_no_oscillation(self, isolate):
        """Sequence stable → False."""
        s = isolate
        buf = deque(maxlen=CALIBRATION_BUFFER_MAX)
        for i in range(OSCILLATION_PERIOD_THRESHOLD):
            buf.append(75.0 + i * 0.1)  # Montee reguliere
        s._calibration_buffers["thermoception"] = buf

        assert s._detect_oscillation("thermoception") is False

    def test_small_amplitude_no_oscillation(self, isolate):
        """Oscillation faible amplitude (< seuil) → False."""
        s = isolate
        buf = deque(maxlen=CALIBRATION_BUFFER_MAX)
        for i in range(OSCILLATION_PERIOD_THRESHOLD):
            val = 75.0 if i % 2 == 0 else 74.9  # Amplitude 0.1 < seuil
            buf.append(val)
        s._calibration_buffers["thermoception"] = buf

        assert s._detect_oscillation("thermoception") is False

    def test_oscillation_increases_smoothing(self, isolate):
        """Oscillation → alpha EMA reduit (plus de lissage)."""
        s = isolate
        # Preparer une oscillation
        buf = deque(maxlen=CALIBRATION_BUFFER_MAX)
        for i in range(OSCILLATION_PERIOD_THRESHOLD):
            buf.append(80.0 if i % 2 == 0 else 60.0)
        s._calibration_buffers["thermoception"] = buf

        # Avant smooth, verifier que la detection fonctionne
        assert s._detect_oscillation("thermoception") is True

        # Le smooth doit utiliser un alpha reduit
        s.senses["thermoception"] = 0.5
        s._smooth({"thermoception": 0.9})
        smoothed = s.senses["thermoception"]

        # Avec alpha normal (0.15) : 0.5 + 0.15 * 0.4 = 0.56
        # Avec amortissement (0.15 * 0.5 = 0.075) : 0.5 + 0.075 * 0.4 = 0.53
        expected_damped = 0.5 + EMA_ALPHAS["thermoception"] * OSCILLATION_DAMPING * (0.9 - 0.5)
        assert smoothed == pytest.approx(expected_damped, abs=0.01)

    def test_insufficient_buffer_no_detection(self, isolate):
        """Buffer trop court → pas de detection."""
        s = isolate
        buf = deque(maxlen=CALIBRATION_BUFFER_MAX)
        for i in range(5):
            buf.append(80.0 if i % 2 == 0 else 60.0)
        s._calibration_buffers["thermoception"] = buf

        assert s._detect_oscillation("thermoception") is False

    def test_damping_flag_set_on_oscillation(self, isolate):
        """Oscillation active le flag _oscillation_damped."""
        s = isolate
        buf = deque(maxlen=CALIBRATION_BUFFER_MAX)
        for i in range(OSCILLATION_PERIOD_THRESHOLD):
            buf.append(80.0 if i % 2 == 0 else 60.0)
        s._calibration_buffers["thermoception"] = buf

        assert not s._oscillation_damped["thermoception"]
        s._smooth({"thermoception": 0.7})
        assert s._oscillation_damped["thermoception"]
