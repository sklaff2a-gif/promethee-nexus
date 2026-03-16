"""
Tests pour core/connectivity_matrix.py — Matrice de connectivite inter-organes.

Couvre : singleton, connexions initiales, plasticite Hebbienne,
decay, homeostase, persistance, handlers bus.
"""

import json
import os
import time
from unittest.mock import patch, AsyncMock

import pytest

from core.connectivity_matrix import (
    ConnectivityMatrix,
    MATRIX_STATE_FILE,
    HEBBIAN_STRENGTHEN_RATE,
    HEBBIAN_WEAKEN_RATE,
    WEIGHT_MIN,
    WEIGHT_MAX,
    HOMEOSTATIC_TARGET,
    ORGANS,
    _conn_key,
    _INITIAL_CONNECTIONS,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def isolate_matrix(tmp_path, monkeypatch):
    """Reset le singleton et isole le fichier d'etat."""
    import core.connectivity_matrix as mod
    ConnectivityMatrix.reset_singleton()
    monkeypatch.setattr(mod, "MATRIX_STATE_FILE", str(tmp_path / "matrix.json"))
    with patch.object(ConnectivityMatrix, "_load"):
        m = ConnectivityMatrix()
        m.connections = {}
        m._init_default_connections()
        mod.matrix = m
    yield m
    ConnectivityMatrix.reset_singleton()


# ============================================================
# Tests Singleton
# ============================================================

class TestSingleton:

    def test_singleton_identity(self, isolate_matrix):
        m2 = ConnectivityMatrix()
        assert m2 is isolate_matrix

    def test_reset_singleton(self, isolate_matrix):
        ConnectivityMatrix.reset_singleton()
        assert ConnectivityMatrix._instance is None


# ============================================================
# Tests Connexions Initiales
# ============================================================

class TestInitialConnections:

    def test_initial_connections_count(self, isolate_matrix):
        """La matrice est initialisee avec les connexions par defaut."""
        assert len(isolate_matrix.connections) == len(_INITIAL_CONNECTIONS)

    def test_cardiac_to_thalamus_exists(self, isolate_matrix):
        """La connexion cardiac→thalamus existe (Piste 1)."""
        w = isolate_matrix.get_weight("cardiac", "thalamus")
        assert w == 0.8

    def test_reptilian_to_desire_inhibitory(self, isolate_matrix):
        """reptilian→desire est inhibitoire (Bridge #2 crisis)."""
        conn = isolate_matrix.get_connection("reptilian", "desire")
        assert conn is not None
        assert conn["type"] == "inhibitory"
        assert conn["weight"] == 0.9

    def test_nonexistent_connection_returns_zero(self, isolate_matrix):
        """Connexion inexistante retourne 0.0."""
        w = isolate_matrix.get_weight("cardiac", "curiosity")
        assert w == 0.0

    def test_get_neighbors(self, isolate_matrix):
        """get_neighbors retourne les voisins sortants tries par poids."""
        neighbors = isolate_matrix.get_neighbors("cardiac")
        assert len(neighbors) > 0
        # Trie decroissant
        for i in range(len(neighbors) - 1):
            assert neighbors[i]["weight"] >= neighbors[i + 1]["weight"]


# ============================================================
# Tests Plasticite Hebbienne
# ============================================================

class TestPlasticity:

    def test_strengthen_increases_weight(self, isolate_matrix):
        """strengthen augmente le poids."""
        w_before = isolate_matrix.get_weight("cardiac", "thalamus")
        isolate_matrix.strengthen("cardiac", "thalamus")
        w_after = isolate_matrix.get_weight("cardiac", "thalamus")
        assert w_after > w_before

    def test_weaken_decreases_weight(self, isolate_matrix):
        """weaken diminue le poids."""
        w_before = isolate_matrix.get_weight("cardiac", "thalamus")
        isolate_matrix.weaken("cardiac", "thalamus")
        w_after = isolate_matrix.get_weight("cardiac", "thalamus")
        assert w_after < w_before

    def test_strengthen_capped_at_max(self, isolate_matrix):
        """Le poids ne depasse pas WEIGHT_MAX."""
        key = _conn_key("cardiac", "thalamus")
        isolate_matrix.connections[key]["weight"] = 0.99
        isolate_matrix.strengthen("cardiac", "thalamus", 0.1)
        assert isolate_matrix.connections[key]["weight"] <= WEIGHT_MAX

    def test_weaken_floored_at_min(self, isolate_matrix):
        """Le poids ne descend pas sous WEIGHT_MIN."""
        key = _conn_key("cardiac", "thalamus")
        isolate_matrix.connections[key]["weight"] = 0.06
        isolate_matrix.weaken("cardiac", "thalamus", 0.1)
        assert isolate_matrix.connections[key]["weight"] >= WEIGHT_MIN

    def test_strengthen_creates_new_connection(self, isolate_matrix):
        """strengthen cree une connexion si elle n'existe pas."""
        assert isolate_matrix.get_weight("cardiac", "curiosity") == 0.0
        isolate_matrix.strengthen("cardiac", "curiosity")
        assert isolate_matrix.get_weight("cardiac", "curiosity") > 0.0

    def test_strengthen_unknown_organ_ignored(self, isolate_matrix):
        """strengthen avec un organe inconnu est ignore."""
        initial_count = len(isolate_matrix.connections)
        isolate_matrix.strengthen("cardiac", "unknown_organ")
        assert len(isolate_matrix.connections) == initial_count

    def test_weaken_nonexistent_noop(self, isolate_matrix):
        """weaken sur connexion inexistante ne fait rien."""
        isolate_matrix.weaken("cardiac", "curiosity")  # Pas de crash

    def test_usage_count_incremented(self, isolate_matrix):
        """strengthen incremente le usage_count."""
        conn = isolate_matrix.get_connection("cardiac", "thalamus")
        initial_usage = conn["usage_count"]
        isolate_matrix.strengthen("cardiac", "thalamus")
        assert conn["usage_count"] == initial_usage + 1

    def test_stats_tracked(self, isolate_matrix):
        """Les stats de plasticite sont tracees."""
        isolate_matrix.strengthen("cardiac", "thalamus")
        isolate_matrix.weaken("dopamine", "cardiac")
        assert isolate_matrix._stats["total_strengthens"] >= 1
        assert isolate_matrix._stats["total_weakens"] >= 1


# ============================================================
# Tests Decay et Homeostase
# ============================================================

class TestDecayAndHomeostasis:

    def test_decay_reduces_weights(self, isolate_matrix):
        """decay_all reduit tous les poids."""
        # Simuler 1 jour ecoule
        isolate_matrix._last_decay_time = time.time() - 86400

        weights_before = {k: c["weight"] for k, c in isolate_matrix.connections.items()}
        isolate_matrix.decay_all()
        for k, c in isolate_matrix.connections.items():
            assert c["weight"] <= weights_before[k]

    def test_decay_respects_min(self, isolate_matrix):
        """decay_all ne descend pas sous WEIGHT_MIN."""
        for c in isolate_matrix.connections.values():
            c["weight"] = WEIGHT_MIN + 0.001
        isolate_matrix._last_decay_time = time.time() - 86400
        isolate_matrix.decay_all()
        for c in isolate_matrix.connections.values():
            assert c["weight"] >= WEIGHT_MIN

    def test_homeostatic_normalize(self, isolate_matrix):
        """homeostatic_normalize ramene vers HOMEOSTATIC_TARGET."""
        # Mettre tous les poids tres haut
        for c in isolate_matrix.connections.values():
            c["weight"] = 0.95
        isolate_matrix.homeostatic_normalize()
        avg = sum(c["weight"] for c in isolate_matrix.connections.values()) / len(isolate_matrix.connections)
        # L'average devrait avoir baisse vers le target
        assert avg < 0.95

    def test_homeostatic_does_nothing_at_target(self, isolate_matrix):
        """Pas de correction si deja a la cible."""
        for c in isolate_matrix.connections.values():
            c["weight"] = HOMEOSTATIC_TARGET
        isolate_matrix.homeostatic_normalize()
        for c in isolate_matrix.connections.values():
            assert abs(c["weight"] - HOMEOSTATIC_TARGET) < 0.02


# ============================================================
# Tests Handlers Bus
# ============================================================

class TestBusHandlers:

    @pytest.mark.asyncio
    async def test_sensorium_feedback_success_strengthens(self, isolate_matrix):
        """SENSORIUM_FEEDBACK success renforce les connexions entre organes actifs."""
        m = isolate_matrix
        # Forcer une connexion connue avec un poids mesurable
        m.strengthen("cardiac", "dopamine", 0.0)  # Creer si absent

        w_before = m.get_weight("cardiac", "dopamine")

        await m._on_sensorium_feedback({
            "status": "success",
            "organ_snapshot": {
                "cardiac": {"bpm": 70},
                "dopamine": {"level": 0.5},
            },
        })

        w_after = m.get_weight("cardiac", "dopamine")
        assert w_after >= w_before

    @pytest.mark.asyncio
    async def test_sensorium_feedback_error_weakens(self, isolate_matrix):
        """SENSORIUM_FEEDBACK error affaiblit les connexions."""
        m = isolate_matrix

        # Mettre un poids connu
        key = _conn_key("cardiac", "dopamine")
        if key not in m.connections:
            m.strengthen("cardiac", "dopamine", 0.5)

        w_before = m.get_weight("cardiac", "dopamine")

        await m._on_sensorium_feedback({
            "status": "error",
            "organ_snapshot": {
                "cardiac": {"bpm": 70},
                "dopamine": {"level": 0.2},
            },
        })

        w_after = m.get_weight("cardiac", "dopamine")
        assert w_after <= w_before

    @pytest.mark.asyncio
    async def test_routine_complete_strengthens_contributors(self, isolate_matrix):
        """AUTONOMY_ROUTINE_COMPLETE renforce les connexions entre contributeurs."""
        m = isolate_matrix

        await m._on_routine_complete({
            "status": "success",
            "scoring_breakdown": {
                "cardiac": 0.5,
                "dopamine": 1.2,
                "thalamus": 0.3,
            },
        })

        # Les stats doivent montrer des renforcements
        assert m._stats["total_strengthens"] > 0

    @pytest.mark.asyncio
    async def test_empty_snapshot_noop(self, isolate_matrix):
        """Snapshot vide ne fait rien."""
        m = isolate_matrix
        initial_stats = dict(m._stats)

        await m._on_sensorium_feedback({
            "status": "success",
            "organ_snapshot": {},
        })

        assert m._stats["total_strengthens"] == initial_stats["total_strengthens"]


# ============================================================
# Tests Persistance
# ============================================================

class TestPersistence:

    def test_save_and_load(self, isolate_matrix, tmp_path, monkeypatch):
        """La matrice survit au save/load."""
        import core.connectivity_matrix as mod
        path = str(tmp_path / "matrix2.json")
        monkeypatch.setattr(mod, "MATRIX_STATE_FILE", path)

        isolate_matrix.strengthen("cardiac", "thalamus")
        isolate_matrix.save()

        # Verifier que le fichier existe
        assert os.path.exists(path)

        # Charger dans une nouvelle instance
        with open(path, "r") as f:
            data = json.load(f)
        assert "connections" in data
        assert len(data["connections"]) > 0

    def test_matrix_summary_structure(self, isolate_matrix):
        """get_matrix_summary retourne la bonne structure."""
        summary = isolate_matrix.get_matrix_summary()
        assert "connections" in summary
        assert "avg_weight" in summary
        assert "top_connections" in summary
        assert "stats" in summary
        assert len(summary["top_connections"]) <= 5
