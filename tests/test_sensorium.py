"""
Tests pour core/sensorium.py — Sensorium Sprint 1.
Perception corporelle du hardware : 5 sens, sigmoide, EMA, calibration.
"""
import asyncio
import json
import math
import os
from collections import deque
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from core.sensorium import (
    Sensorium,
    sensorium,
    SIGMOID_PARAMS,
    EMA_ALPHAS,
    SENSE_NAMES,
    COMFORT_WEIGHTS,
    SENSORIUM_STATE_FILE,
    CALIBRATION_STARTUP_TICKS,
    CALIBRATION_MIN_SAMPLES,
    CALIBRATION_BUFFER_MAX,
    SAMPLE_INTERVAL,
)


# ============================================================
# Fixture autouse — isolation singleton + fichier
# ============================================================

@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Isole chaque test : reset singleton, state file dans tmp."""
    Sensorium.reset_singleton()
    state_file = str(tmp_path / "sensorium_state.json")
    monkeypatch.setattr("core.sensorium.SENSORIUM_STATE_FILE", state_file)
    s = Sensorium()
    # Eviter la detection GPU reelle dans les tests
    s._gpu_backend = "heuristic"
    s._gpu_name = ""
    yield s
    Sensorium.reset_singleton()


# ============================================================
# TestSingleton
# ============================================================

class TestSingleton:
    def test_identity(self, isolate):
        """Deux instanciations retournent le meme objet."""
        a = Sensorium()
        b = Sensorium()
        assert a is b

    def test_reset(self, isolate):
        """reset_singleton() cree une nouvelle instance."""
        a = Sensorium()
        Sensorium.reset_singleton()
        b = Sensorium()
        b._gpu_backend = "heuristic"
        b._gpu_name = ""
        assert a is not b

    def test_reinit_after_reset(self, isolate):
        """Apres reset, l'etat est frais."""
        isolate._tick_count = 999
        Sensorium.reset_singleton()
        fresh = Sensorium()
        fresh._gpu_backend = "heuristic"
        fresh._gpu_name = ""
        assert fresh._tick_count == 0


# ============================================================
# TestSampleRaw
# ============================================================

class TestSampleRaw:
    def test_with_psutil(self, isolate):
        """Collecte CPU, RAM, freq via psutil mock."""
        mock_mem = MagicMock()
        mock_mem.percent = 65.0
        mock_freq = MagicMock()
        mock_freq.current = 4500.0

        with patch("psutil.cpu_percent", return_value=45.0), \
             patch("psutil.virtual_memory", return_value=mock_mem), \
             patch("psutil.cpu_freq", return_value=mock_freq):
            raw = isolate._sample_raw()

        assert raw["effort"] == 45.0
        assert raw["oppression"] == 65.0
        assert raw["vitality"] == 4500.0
        # Heuristic backend → thermoception estimated
        assert raw["thermoception"] == pytest.approx(45.0 + 45.0 * 0.45)

    def test_with_gpu(self, isolate):
        """Collecte GPU temp et VRAM via GPUtil mock."""
        isolate._gpu_backend = "gputil"
        mock_gpu = MagicMock()
        mock_gpu.temperature = 72.0
        mock_gpu.memoryUtil = 0.85

        mock_mem = MagicMock()
        mock_mem.percent = 50.0
        mock_freq = MagicMock()
        mock_freq.current = 4000.0

        mock_gputil = MagicMock()
        mock_gputil.getGPUs.return_value = [mock_gpu]

        with patch("psutil.cpu_percent", return_value=30.0), \
             patch("psutil.virtual_memory", return_value=mock_mem), \
             patch("psutil.cpu_freq", return_value=mock_freq), \
             patch.dict("sys.modules", {"GPUtil": mock_gputil}):
            raw = isolate._sample_raw()

        assert raw["thermoception"] == 72.0
        assert raw["suffocation"] == 85.0

    def test_psutil_failure(self, isolate):
        """Si psutil echoue, fallback a des valeurs par defaut."""
        with patch("psutil.cpu_percent", side_effect=Exception("fail")):
            raw = isolate._sample_raw()

        assert raw["effort"] == 0.0
        assert raw["oppression"] == 0.0
        assert raw["vitality"] == 3500.0

    def test_gpu_failure(self, isolate):
        """Si GPUtil echoue, fallback heuristique."""
        isolate._gpu_backend = "gputil"
        mock_mem = MagicMock()
        mock_mem.percent = 50.0
        mock_freq = MagicMock()
        mock_freq.current = 4000.0

        mock_gputil = MagicMock()
        mock_gputil.getGPUs.side_effect = Exception("gpu fail")

        with patch("psutil.cpu_percent", return_value=60.0), \
             patch("psutil.virtual_memory", return_value=mock_mem), \
             patch("psutil.cpu_freq", return_value=mock_freq), \
             patch.dict("sys.modules", {"GPUtil": mock_gputil}):
            raw = isolate._sample_raw()

        # Fallback heuristique
        assert raw["thermoception"] == pytest.approx(45.0 + 60.0 * 0.45)

    def test_estimate_temp(self, isolate):
        """Heuristique temperature : 45 idle, 90 full."""
        assert Sensorium._estimate_temp(0) == 45.0
        assert Sensorium._estimate_temp(100) == pytest.approx(90.0)
        assert Sensorium._estimate_temp(50) == pytest.approx(67.5)


# ============================================================
# TestNormalize
# ============================================================

class TestNormalize:
    def test_sigmoid_at_midpoint(self, isolate):
        """Au midpoint, la sigmoide vaut ~0.5."""
        for sense, (mid, k, inv) in SIGMOID_PARAMS.items():
            raw = {sense: mid}
            result = isolate._normalize(raw)
            expected = 0.5 if not inv else 0.5
            assert result[sense] == pytest.approx(expected, abs=0.01)

    def test_sigmoid_high_value(self, isolate):
        """Valeur tres haute → proche de 1.0 (ou 0.0 si inverse)."""
        raw = {"thermoception": 95.0}  # Tres chaud
        result = isolate._normalize(raw)
        assert result["thermoception"] > 0.95

    def test_sigmoid_low_value(self, isolate):
        """Valeur tres basse → proche de 0.0 (ou 1.0 si inverse)."""
        raw = {"thermoception": 30.0}  # Tres froid
        result = isolate._normalize(raw)
        assert result["thermoception"] < 0.05

    def test_inverted_vitality_high(self, isolate):
        """Haute frequence → basse valeur normalisee (inversee)."""
        raw = {"vitality": 5700.0}  # Haute freq = bien
        result = isolate._normalize(raw)
        # Inversee : haute freq → basse sensation (peu d'inconfort)
        assert result["vitality"] < 0.01

    def test_inverted_vitality_low(self, isolate):
        """Basse frequence → haute valeur normalisee (inversee = mauvais)."""
        raw = {"vitality": 2200.0}  # Basse freq = mal
        result = isolate._normalize(raw)
        assert result["vitality"] > 0.95

    def test_all_senses(self, isolate):
        """Normaliser les 5 sens en une passe."""
        raw = {
            "thermoception": 78.0,
            "effort": 70.0,
            "oppression": 75.0,
            "suffocation": 80.0,
            "vitality": 4000.0,
        }
        result = isolate._normalize(raw)
        assert set(result.keys()) == set(SENSE_NAMES)
        for s in SENSE_NAMES:
            assert 0.0 <= result[s] <= 1.0


# ============================================================
# TestSmooth
# ============================================================

class TestSmooth:
    def test_first_smooth(self, isolate):
        """Premier lissage depuis la valeur par defaut 0.5."""
        isolate.senses = {s: 0.5 for s in SENSE_NAMES}
        normalized = {"effort": 1.0}
        isolate._smooth(normalized)
        # EMA: 0.5 + 0.4 * (1.0 - 0.5) = 0.7
        assert isolate.senses["effort"] == pytest.approx(0.7)

    def test_convergence(self, isolate):
        """Apres plusieurs iterations, converge vers la cible."""
        isolate.senses = {"effort": 0.0}
        for _ in range(50):
            isolate._smooth({"effort": 1.0})
        assert isolate.senses["effort"] == pytest.approx(1.0, abs=0.01)

    def test_alpha_fast_vs_slow(self, isolate):
        """Alpha rapide (effort=0.4) converge plus vite que lent (thermo=0.15)."""
        isolate.senses = {"effort": 0.0, "thermoception": 0.0}
        for _ in range(5):
            isolate._smooth({"effort": 1.0, "thermoception": 1.0})
        # effort (alpha=0.4) devrait etre plus proche de 1.0
        assert isolate.senses["effort"] > isolate.senses["thermoception"]

    def test_stability(self, isolate):
        """Si la cible ne change pas, la valeur reste stable."""
        isolate.senses = {"effort": 0.5}
        isolate._smooth({"effort": 0.5})
        assert isolate.senses["effort"] == 0.5


# ============================================================
# TestCalibration
# ============================================================

class TestCalibration:
    def test_startup_phase(self, isolate):
        """Pendant le startup, pas de calibration."""
        isolate._tick_count = 0
        raw = {"effort": 50.0}
        isolate._update_calibration(raw)
        assert not isolate._calibration_ready

    def test_after_startup(self, isolate):
        """Apres le startup, calibration demarre si assez de samples."""
        isolate._tick_count = CALIBRATION_STARTUP_TICKS + 1
        for _ in range(CALIBRATION_MIN_SAMPLES + 1):
            isolate._calibration_buffers["effort"].append(50.0)
        raw = {"effort": 50.0}
        isolate._update_calibration(raw)
        assert isolate._calibration_ready

    def test_midpoint_shift(self, isolate):
        """Les midpoints s'ajustent aux valeurs observees."""
        isolate._tick_count = CALIBRATION_STARTUP_TICKS + 1
        # Simuler une machine ou effort oscille entre 20 et 40
        for i in range(CALIBRATION_MIN_SAMPLES + 1):
            isolate._calibration_buffers["effort"].append(20.0 + (i % 20))
        raw = {"effort": 30.0}
        isolate._update_calibration(raw)

        new_mid = isolate._active_sigmoid_params["effort"][0]
        # Le midpoint devrait etre entre 20 et 40
        assert 20.0 < new_mid < 40.0

    def test_buffer_maxlen(self, isolate):
        """Le buffer de calibration respecte la taille max."""
        for i in range(CALIBRATION_BUFFER_MAX + 100):
            isolate._calibration_buffers["effort"].append(float(i))
        assert len(isolate._calibration_buffers["effort"]) == CALIBRATION_BUFFER_MAX

    def test_min_samples_guard(self, isolate):
        """Pas d'ajustement avec trop peu de samples."""
        isolate._tick_count = CALIBRATION_STARTUP_TICKS + 1
        # Seulement 10 samples, bien en dessous de CALIBRATION_MIN_SAMPLES
        for _ in range(10):
            isolate._calibration_buffers["effort"].append(50.0)
        original_mid = SIGMOID_PARAMS["effort"][0]
        raw = {"effort": 50.0}
        isolate._update_calibration(raw)
        # Le midpoint ne devrait pas avoir change
        assert isolate._active_sigmoid_params["effort"][0] == original_mid


# ============================================================
# TestGetSenses
# ============================================================

class TestGetSenses:
    def test_default_values(self, isolate):
        """Valeurs par defaut = 0.5 pour chaque sens."""
        senses = isolate.get_senses()
        assert len(senses) == 5
        for s in SENSE_NAMES:
            assert senses[s] == 0.5

    def test_after_smooth(self, isolate):
        """Apres un lissage, les valeurs changent."""
        isolate._smooth({"effort": 0.9})
        senses = isolate.get_senses()
        assert senses["effort"] != 0.5

    def test_returns_copy(self, isolate):
        """get_senses() retourne une copie, pas la reference interne."""
        senses = isolate.get_senses()
        senses["effort"] = 999.0
        assert isolate.senses["effort"] != 999.0


# ============================================================
# TestComfortIndex
# ============================================================

class TestComfortIndex:
    def test_all_zero(self, isolate):
        """Tous les sens a 0 → confort maximum."""
        isolate.senses = {s: 0.0 for s in SENSE_NAMES}
        idx = isolate.get_comfort_index()
        assert idx == pytest.approx(1.0)

    def test_all_max(self, isolate):
        """Tous les sens a 1.0 → inconfort maximum."""
        isolate.senses = {s: 1.0 for s in SENSE_NAMES}
        idx = isolate.get_comfort_index()
        assert idx == pytest.approx(0.0)

    def test_balanced(self, isolate):
        """Sens a 0.5 → confort intermediaire."""
        isolate.senses = {s: 0.5 for s in SENSE_NAMES}
        idx = isolate.get_comfort_index()
        assert 0.3 < idx < 0.7

    def test_in_range(self, isolate):
        """Le confort est toujours dans [0.0, 1.0]."""
        import random
        for _ in range(20):
            isolate.senses = {s: random.random() for s in SENSE_NAMES}
            idx = isolate.get_comfort_index()
            assert 0.0 <= idx <= 1.0


# ============================================================
# TestPersistence
# ============================================================

class TestPersistence:
    def test_roundtrip(self, isolate, tmp_path):
        """Sauvegarder et recharger preserve l'etat."""
        isolate.senses = {"thermoception": 0.7, "effort": 0.3, "oppression": 0.5,
                          "suffocation": 0.2, "vitality": 0.8}
        isolate._raw_last = {"thermoception": 72.0, "effort": 30.0, "oppression": 50.0,
                             "suffocation": 20.0, "vitality": 4500.0}
        isolate._tick_count = 42
        isolate._total_samples = 42
        isolate._save()

        # Reset et recharger
        Sensorium.reset_singleton()
        s2 = Sensorium()
        s2._gpu_backend = "heuristic"
        s2._gpu_name = ""
        assert s2.senses["thermoception"] == pytest.approx(0.7)
        assert s2._tick_count == 42

    def test_missing_file(self, isolate, tmp_path):
        """Fichier absent → pas d'erreur, valeurs par defaut."""
        Sensorium.reset_singleton()
        s2 = Sensorium()
        s2._gpu_backend = "heuristic"
        s2._gpu_name = ""
        # Pas de crash, valeurs par defaut
        assert s2._tick_count == 0

    def test_corrupt_file(self, isolate, tmp_path, monkeypatch):
        """Fichier corrompu → pas d'erreur."""
        state_file = str(tmp_path / "sensorium_state.json")
        monkeypatch.setattr("core.sensorium.SENSORIUM_STATE_FILE", state_file)
        with open(state_file, "w") as f:
            f.write("{{{invalid json")

        Sensorium.reset_singleton()
        s2 = Sensorium()
        s2._gpu_backend = "heuristic"
        s2._gpu_name = ""
        assert s2._tick_count == 0

    def test_calibration_restored(self, isolate, tmp_path):
        """La calibration est restauree depuis le fichier."""
        # Remplir le buffer de calibration
        for _ in range(CALIBRATION_MIN_SAMPLES + 10):
            isolate._calibration_buffers["effort"].append(40.0)
        isolate._calibration_ready = True
        isolate._active_sigmoid_params["effort"] = (40.0, 0.08, False)
        isolate._tick_count = 100
        isolate._total_samples = 100
        isolate._save()

        Sensorium.reset_singleton()
        s2 = Sensorium()
        s2._gpu_backend = "heuristic"
        s2._gpu_name = ""
        assert s2._calibration_ready
        assert s2._active_sigmoid_params["effort"][0] == pytest.approx(40.0)


# ============================================================
# TestSamplingLoop
# ============================================================

class TestSamplingLoop:
    @pytest.mark.asyncio
    async def test_starts_and_stops(self, isolate):
        """La boucle demarre et s'arrete proprement."""
        mock_mem = MagicMock()
        mock_mem.percent = 50.0
        mock_freq = MagicMock()
        mock_freq.current = 4000.0

        with patch("psutil.cpu_percent", return_value=50.0), \
             patch("psutil.virtual_memory", return_value=mock_mem), \
             patch("psutil.cpu_freq", return_value=mock_freq), \
             patch("core.sensorium.SAMPLE_INTERVAL", 0.01):
            await isolate.start_sampling()
            assert isolate._running
            await asyncio.sleep(0.05)
            isolate.stop()
            assert not isolate._running

    @pytest.mark.asyncio
    async def test_tick_count_increments(self, isolate):
        """Le tick count augmente a chaque iteration."""
        mock_mem = MagicMock()
        mock_mem.percent = 50.0
        mock_freq = MagicMock()
        mock_freq.current = 4000.0

        initial = isolate._tick_count
        with patch("psutil.cpu_percent", return_value=50.0), \
             patch("psutil.virtual_memory", return_value=mock_mem), \
             patch("psutil.cpu_freq", return_value=mock_freq), \
             patch("core.sensorium.SAMPLE_INTERVAL", 0.01):
            await isolate.start_sampling()
            await asyncio.sleep(0.08)
            isolate.stop()
        assert isolate._tick_count > initial

    @pytest.mark.asyncio
    async def test_error_resilience(self, isolate):
        """La boucle survit aux erreurs de sampling."""
        call_count = 0

        def flaky_sample():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("flaky")
            return {s: 50.0 for s in SENSE_NAMES}

        with patch.object(isolate, "_sample_raw", side_effect=flaky_sample), \
             patch("core.sensorium.SAMPLE_INTERVAL", 0.01):
            await isolate.start_sampling()
            await asyncio.sleep(0.1)
            isolate.stop()
        # La boucle a survecu aux erreurs
        assert call_count >= 3


# ============================================================
# TestBackendDetection
# ============================================================

class TestBackendDetection:
    def test_gputil_available(self, isolate):
        """Quand GPUtil retourne un GPU, backend = gputil."""
        mock_gpu = MagicMock()
        mock_gpu.name = "NVIDIA GeForce RTX 5070 Ti"
        mock_gpu.temperature = 55.0

        mock_gputil = MagicMock()
        mock_gputil.getGPUs.return_value = [mock_gpu]

        with patch.dict("sys.modules", {"GPUtil": mock_gputil}):
            isolate._detect_gpu_backend()
        assert isolate._gpu_backend == "gputil"
        assert "5070" in isolate._gpu_name

    def test_gputil_no_gpu(self, isolate):
        """Quand GPUtil ne trouve pas de GPU, backend = heuristic."""
        mock_gputil = MagicMock()
        mock_gputil.getGPUs.return_value = []

        with patch.dict("sys.modules", {"GPUtil": mock_gputil}):
            isolate._detect_gpu_backend()
        assert isolate._gpu_backend == "heuristic"

    def test_gputil_import_error(self, isolate):
        """Quand GPUtil n'est pas installe, backend = heuristic."""
        with patch.dict("sys.modules", {"GPUtil": None}):
            isolate._detect_gpu_backend()
        assert isolate._gpu_backend == "heuristic"


# ============================================================
# TestStats
# ============================================================

class TestStats:
    def test_stats_structure(self, isolate):
        """get_stats() retourne toutes les cles attendues."""
        stats = isolate.get_stats()
        assert "senses" in stats
        assert "raw" in stats
        assert "comfort_index" in stats
        assert "calibration" in stats
        assert "gpu_backend" in stats
        assert "tick_count" in stats
        assert "running" in stats
        assert len(stats["senses"]) == 5

    def test_stats_calibration_per_sense(self, isolate):
        """La calibration contient les infos par sens."""
        stats = isolate.get_stats()
        for sense in SENSE_NAMES:
            assert sense in stats["calibration"]
            cal = stats["calibration"][sense]
            assert "midpoint" in cal
            assert "samples" in cal
            assert "calibrated" in cal
