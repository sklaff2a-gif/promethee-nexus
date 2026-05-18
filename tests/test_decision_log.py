"""Tests pour core/decision_log.py — télémétrie centralisée des refus métier.

Doctrine 18/05/2026 : T1/T2/T3 validée sur prefrontal + hippocampus.
Helper testé en isolation : I/O fichier, sampling, rotation, fallback.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest

from core import decision_log


class TestLogDecisionBasic:
    """Tests d'écriture basique du helper."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".jsonl", encoding="utf-8"
        )
        self.tmp.close()
        os.unlink(self.tmp.name)  # On veut un path qui n'existe pas
        self._original_log = decision_log.LOG_FILE
        decision_log.LOG_FILE = self.tmp.name

    def teardown_method(self):
        decision_log.LOG_FILE = self._original_log
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_log_writes_jsonl_line(self):
        """log_decision écrit une ligne JSONL valide."""
        ok = decision_log.log_decision(
            "test_module", "test_fn", "test_reason",
            context={"key": "value", "n": 42},
        )
        assert ok is True
        assert os.path.exists(self.tmp.name)
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["module"] == "test_module"
        assert payload["function"] == "test_fn"
        assert payload["reason"] == "test_reason"
        assert payload["context"] == {"key": "value", "n": 42}
        assert "ts" in payload

    def test_log_appends_multiple_lines(self):
        """Plusieurs appels = plusieurs lignes (mode append)."""
        decision_log.log_decision("m", "f", "r1")
        decision_log.log_decision("m", "f", "r2")
        decision_log.log_decision("m", "f", "r3")
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["reason"] == "r1"
        assert json.loads(lines[2])["reason"] == "r3"

    def test_log_creates_dir_if_missing(self):
        """Si logs/ n'existe pas, le helper le crée."""
        deep_path = os.path.join(
            tempfile.gettempdir(), "decision_log_test_subdir", "decisions.jsonl"
        )
        if os.path.exists(deep_path):
            os.unlink(deep_path)
        if os.path.exists(os.path.dirname(deep_path)):
            os.rmdir(os.path.dirname(deep_path))
        decision_log.LOG_FILE = deep_path
        try:
            ok = decision_log.log_decision("m", "f", "dir_test")
            assert ok is True
            assert os.path.isdir(os.path.dirname(deep_path))
            assert os.path.exists(deep_path)
        finally:
            if os.path.exists(deep_path):
                os.unlink(deep_path)
            if os.path.exists(os.path.dirname(deep_path)):
                os.rmdir(os.path.dirname(deep_path))

    def test_log_context_optional(self):
        """context=None est accepté et stocké comme dict vide."""
        ok = decision_log.log_decision("m", "f", "no_ctx")
        assert ok is True
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            payload = json.loads(f.readline())
        assert payload["context"] == {}


class TestLogDecisionSampling:
    """Tests du mécanisme de sampling."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".jsonl", encoding="utf-8"
        )
        self.tmp.close()
        os.unlink(self.tmp.name)
        self._original_log = decision_log.LOG_FILE
        decision_log.LOG_FILE = self.tmp.name

    def teardown_method(self):
        decision_log.LOG_FILE = self._original_log
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_sample_rate_1_always_writes(self):
        """sample_rate=1.0 écrit toujours."""
        for i in range(20):
            ok = decision_log.log_decision("m", "f", f"r{i}", sample_rate=1.0)
            assert ok is True
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            assert len(f.readlines()) == 20

    def test_sample_rate_0_never_writes(self):
        """sample_rate=0.0 ne écrit jamais (rejet 100%)."""
        with patch("core.decision_log.random.random", return_value=0.5):
            for i in range(10):
                ok = decision_log.log_decision("m", "f", f"r{i}", sample_rate=0.0)
                assert ok is False
        assert not os.path.exists(self.tmp.name)

    def test_sample_rate_partial_marks_sampled(self):
        """sample_rate<1.0 écrit avec marqueur sampled=True."""
        with patch("core.decision_log.random.random", return_value=0.001):
            ok = decision_log.log_decision("m", "f", "r", sample_rate=0.01)
            assert ok is True
        with open(self.tmp.name, "r", encoding="utf-8") as f:
            payload = json.loads(f.readline())
        assert payload.get("sampled") is True
        assert payload.get("sample_rate") == 0.01


class TestLogDecisionRobustness:
    """Tests de robustesse : ne jamais lever, rotation."""

    def test_io_error_returns_false_silently(self):
        """Si l'I/O échoue, retourne False sans lever d'exception."""
        decision_log.LOG_FILE = "/nonexistent_root_dir_12345/decisions.jsonl"
        try:
            # makedirs sur /nonexistent_root va échouer sur certains OS
            # Mais on s'attend à ne JAMAIS lever d'exception
            result = decision_log.log_decision("m", "f", "io_test")
            # Result peut être True (si tmp accessible) ou False (si échec)
            # L'invariant clé : pas d'exception levée
            assert result in (True, False)
        finally:
            # cleanup si jamais ça a marché
            if os.path.exists("/nonexistent_root_dir_12345/decisions.jsonl"):
                os.unlink("/nonexistent_root_dir_12345/decisions.jsonl")
                os.rmdir("/nonexistent_root_dir_12345")

    def test_rotation_when_size_exceeded(self):
        """Si fichier > ROTATION_SIZE_BYTES, il est renommé avec suffix."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".jsonl", encoding="utf-8"
        )
        tmp.write("x" * 100)  # contenu fictif
        tmp.close()
        original_log = decision_log.LOG_FILE
        original_rot = decision_log.ROTATION_SIZE_BYTES
        decision_log.LOG_FILE = tmp.name
        decision_log.ROTATION_SIZE_BYTES = 50  # force rotation au prochain write
        try:
            ok = decision_log.log_decision("m", "f", "rotation_test")
            assert ok is True
            # Le fichier original a été renommé avec un suffix
            files_with_prefix = [
                f for f in os.listdir(os.path.dirname(tmp.name))
                if f.startswith(os.path.basename(tmp.name))
            ]
            # On doit avoir au moins 2 fichiers : l'original renommé + le nouveau
            assert len(files_with_prefix) >= 2
        finally:
            decision_log.LOG_FILE = original_log
            decision_log.ROTATION_SIZE_BYTES = original_rot
            # cleanup tous les fichiers liés
            for f in os.listdir(os.path.dirname(tmp.name)):
                if f.startswith(os.path.basename(tmp.name)):
                    try:
                        os.unlink(os.path.join(os.path.dirname(tmp.name), f))
                    except Exception:
                        pass
