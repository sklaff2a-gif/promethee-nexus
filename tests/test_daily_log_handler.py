"""
Tests pour le _DailyFileHandler — logs quotidiens sans rotation.

Couvre :
- Création de fichier avec date dans le nom
- Changement de fichier à minuit (simulation)
- Nettoyage des fichiers anciens (>14 jours)
- Pas de PermissionError (contrairement à TimedRotatingFileHandler)
"""

import logging
import os
import time
from unittest.mock import patch
from datetime import datetime, timedelta

import pytest


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def logs_dir(tmp_path):
    """Répertoire de logs temporaire."""
    d = tmp_path / "logs"
    d.mkdir()
    return str(d)


@pytest.fixture
def handler(logs_dir):
    """Crée un _DailyFileHandler pour les tests."""
    # Import depuis main.py (le handler est défini là)
    import importlib
    import sys

    # On doit importer la classe sans exécuter tout main.py
    # → on l'extrait manuellement depuis le module
    from main import _DailyFileHandler
    h = _DailyFileHandler(logs_dir, prefix="test", keep_days=14)
    h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    yield h
    h.close()


# ============================================================
# 1. Création de fichier
# ============================================================

class TestDailyFileCreation:

    def test_creates_file_with_date(self, logs_dir):
        """Le fichier créé contient la date du jour."""
        from main import _DailyFileHandler
        h = _DailyFileHandler(logs_dir, prefix="promethee", keep_days=14)
        today = time.strftime("%Y-%m-%d")
        expected = os.path.join(logs_dir, f"promethee_{today}.log")
        assert os.path.exists(expected)
        h.close()

    def test_appends_to_existing_file(self, logs_dir):
        """Si le fichier du jour existe, il y ajoute (mode append)."""
        from main import _DailyFileHandler
        today = time.strftime("%Y-%m-%d")
        filepath = os.path.join(logs_dir, f"test_{today}.log")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("ligne existante\n")

        h = _DailyFileHandler(logs_dir, prefix="test", keep_days=14)
        record = logging.LogRecord("test", logging.INFO, "", 0, "nouvelle ligne", (), None)
        h.emit(record)
        h.close()

        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        assert "ligne existante" in content
        assert "nouvelle ligne" in content


# ============================================================
# 2. Rotation à minuit
# ============================================================

class TestDailyRotation:

    def test_rotates_on_date_change(self, logs_dir):
        """Change de fichier quand la date change."""
        from main import _DailyFileHandler
        h = _DailyFileHandler(logs_dir, prefix="test", keep_days=14)

        # Écrire dans le fichier du jour
        record1 = logging.LogRecord("test", logging.INFO, "", 0, "jour 1", (), None)
        h.emit(record1)

        # Simuler le passage à demain
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        with patch.object(type(h), '_today', staticmethod(lambda: tomorrow)):
            record2 = logging.LogRecord("test", logging.INFO, "", 0, "jour 2", (), None)
            h.emit(record2)

        h.close()

        # Vérifier que les deux fichiers existent
        today = time.strftime("%Y-%m-%d")
        file_today = os.path.join(logs_dir, f"test_{today}.log")
        file_tomorrow = os.path.join(logs_dir, f"test_{tomorrow}.log")

        assert os.path.exists(file_today)
        assert os.path.exists(file_tomorrow)

        with open(file_today, encoding="utf-8") as f:
            assert "jour 1" in f.read()
        with open(file_tomorrow, encoding="utf-8") as f:
            assert "jour 2" in f.read()


# ============================================================
# 3. Nettoyage des fichiers anciens
# ============================================================

class TestDailyCleanup:

    def test_cleans_old_files(self, logs_dir):
        """Les fichiers plus vieux que keep_days sont supprimés."""
        from main import _DailyFileHandler
        # Créer des vieux fichiers
        old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        recent_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        old_file = os.path.join(logs_dir, f"test_{old_date}.log")
        recent_file = os.path.join(logs_dir, f"test_{recent_date}.log")
        with open(old_file, "w") as f:
            f.write("old")
        with open(recent_file, "w") as f:
            f.write("recent")

        # Le handler nettoie à l'init
        h = _DailyFileHandler(logs_dir, prefix="test", keep_days=14)
        h.close()

        assert not os.path.exists(old_file), "Le vieux fichier devrait être supprimé"
        assert os.path.exists(recent_file), "Le fichier récent devrait être conservé"

    def test_cleans_old_format_files(self, logs_dir):
        """Les fichiers au vieux format (promethee.log.YYYY-MM-DD) sont aussi nettoyés."""
        from main import _DailyFileHandler
        old_date = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        old_file = os.path.join(logs_dir, f"test.log.{old_date}")
        with open(old_file, "w") as f:
            f.write("old format")

        h = _DailyFileHandler(logs_dir, prefix="test", keep_days=14)
        h.close()

        assert not os.path.exists(old_file), "Le vieux fichier (ancien format) devrait être supprimé"

    def test_keeps_non_matching_files(self, logs_dir):
        """Les fichiers qui ne matchent pas le pattern ne sont pas touchés."""
        from main import _DailyFileHandler
        other_file = os.path.join(logs_dir, "other_important.log")
        with open(other_file, "w") as f:
            f.write("important")

        h = _DailyFileHandler(logs_dir, prefix="test", keep_days=14)
        h.close()

        assert os.path.exists(other_file), "Les fichiers hors pattern ne doivent pas être supprimés"
