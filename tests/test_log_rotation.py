# tests/test_log_rotation.py — Tests pour _DailyFileHandler
import logging
import os
import time
from unittest.mock import patch
from datetime import datetime, timedelta

import pytest


class TestDailyFileHandler:
    """Vérifie que _DailyFileHandler crée un fichier par jour sans rotation."""

    def test_creates_file_with_date(self, tmp_path):
        """Le fichier créé contient la date du jour."""
        from main import _DailyFileHandler
        h = _DailyFileHandler(str(tmp_path), prefix="promethee", keep_days=14)
        today = time.strftime("%Y-%m-%d")
        expected = tmp_path / f"promethee_{today}.log"
        assert expected.exists()
        h.close()

    def test_emit_writes_to_file(self, tmp_path):
        """emit() écrit bien dans le fichier du jour."""
        from main import _DailyFileHandler
        h = _DailyFileHandler(str(tmp_path), prefix="promethee", keep_days=14)
        h.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord("test", logging.INFO, "", 0, "hello", (), None)
        h.emit(record)
        h.close()

        today = time.strftime("%Y-%m-%d")
        content = (tmp_path / f"promethee_{today}.log").read_text(encoding="utf-8")
        assert "hello" in content

    def test_no_permission_error_on_rotation(self, tmp_path):
        """Pas de PermissionError car pas de rename — nouveau fichier directement."""
        from main import _DailyFileHandler
        h = _DailyFileHandler(str(tmp_path), prefix="promethee", keep_days=14)
        h.setFormatter(logging.Formatter("%(message)s"))

        # Écrire jour 1
        record = logging.LogRecord("test", logging.INFO, "", 0, "jour1", (), None)
        h.emit(record)

        # Simuler changement de jour (pas de rename, juste nouveau fichier)
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        with patch.object(type(h), '_today', staticmethod(lambda: tomorrow)):
            record2 = logging.LogRecord("test", logging.INFO, "", 0, "jour2", (), None)
            h.emit(record2)  # Ne doit PAS lever PermissionError

        h.close()

        # Les deux fichiers existent
        today = time.strftime("%Y-%m-%d")
        assert (tmp_path / f"promethee_{today}.log").exists()
        assert (tmp_path / f"promethee_{tomorrow}.log").exists()
