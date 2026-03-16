"""
Tests pour core/global_workspace.py — Global Workspace (Theorie de Baars).

Couvre : soumission, competition, slots, priority bypass,
mode dominant, format, stats, collecte organes.
"""

import time
from unittest.mock import patch, MagicMock

import pytest

from core.global_workspace import (
    GlobalWorkspace,
    ConsciousContent,
    MAX_CONSCIOUS_SLOTS,
    MIN_CONSCIOUS_SLOTS,
    CONTENT_TTL,
    CATEGORIES,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def isolate_workspace():
    """Reset le singleton."""
    GlobalWorkspace.reset_singleton()
    with patch.object(GlobalWorkspace, "_subscribe_events"):
        w = GlobalWorkspace()
    yield w
    GlobalWorkspace.reset_singleton()


# ============================================================
# Tests Singleton
# ============================================================

class TestSingleton:

    def test_singleton_identity(self, isolate_workspace):
        with patch.object(GlobalWorkspace, "_subscribe_events"):
            w2 = GlobalWorkspace()
        assert w2 is isolate_workspace

    def test_reset_singleton(self):
        GlobalWorkspace.reset_singleton()
        assert GlobalWorkspace._instance is None


# ============================================================
# Tests Soumission
# ============================================================

class TestSubmission:

    def test_submit_adds_content(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "BPM eleve, emotion enthousiasme", 0.7, "emotion")
        assert len(w._submissions) == 1

    def test_submit_empty_ignored(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "", 0.5)
        w.submit("cardiac", "   ", 0.5)
        assert len(w._submissions) == 0

    def test_submit_clamps_salience(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "test content value", 1.5, "emotion")
        w.submit("desire", "another test content", -0.3, "motivation")
        assert w._submissions[0].salience == 1.0
        assert w._submissions[1].salience == 0.0

    def test_submit_invalid_category_defaults(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "test with invalid category", 0.5, "nonexistent")
        assert w._submissions[0].category == "cognition"

    def test_stats_tracked(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "test submission tracking", 0.5)
        assert w._stats["total_submissions"] == 1


# ============================================================
# Tests Competition
# ============================================================

class TestCompetition:

    def test_competition_selects_top_by_salience(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "low salience content here", 0.2, "emotion")
        w.submit("prefrontal", "high salience deliberation", 0.9, "cognition")
        w.submit("desire", "medium salience motivation", 0.5, "motivation")

        contents = w.compete()
        assert len(contents) == 3
        # Premier = plus haute saillance
        assert contents[0].source == "prefrontal"

    def test_competition_limited_to_max_slots(self, isolate_workspace):
        w = isolate_workspace
        for i in range(15):
            w.submit(f"organ_{i}", f"content from organ number {i}", 0.5 + i * 0.03)

        contents = w.compete()
        assert len(contents) <= MAX_CONSCIOUS_SLOTS

    def test_critical_bypasses_filter(self, isolate_workspace):
        w = isolate_workspace
        # Remplir avec des contenus normaux
        for i in range(10):
            w.submit(f"organ_{i}", f"normal content number {i}", 0.9)

        # Ajouter un critical avec saillance basse
        w.submit("reptilian", "MENACE CRITIQUE DETECTEE", 0.1, "urgence", "critical")

        contents = w.compete()
        critical_in = [c for c in contents if c.source == "reptilian"]
        assert len(critical_in) == 1  # Critical toujours present

    def test_deduplication_by_source(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "premier contenu cardiac", 0.5, "emotion")
        # Forcer un timestamp plus recent
        w._submissions[-1].timestamp -= 1.0
        w.submit("cardiac", "deuxieme contenu cardiac plus recent", 0.7, "emotion")

        contents = w.compete()
        cardiac = [c for c in contents if c.source == "cardiac"]
        assert len(cardiac) == 1
        assert "deuxieme" in cardiac[0].content

    def test_expired_content_removed(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "old content should expire", 0.8, "emotion")
        # Forcer l'expiration
        w._submissions[0].timestamp = time.time() - CONTENT_TTL - 10

        contents = w.compete()
        assert len(contents) == 0

    def test_mode_dominant_boosts_category(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "emotion contenu test", 0.5, "urgence")
        w.submit("prefrontal", "cognition contenu test", 0.5, "cognition")

        # En mode urgence, les contenus urgence sont boostes
        contents = w.compete(dominant_mode="urgence")
        assert contents[0].source == "cardiac"  # Booste par le mode

    def test_empty_workspace(self, isolate_workspace):
        w = isolate_workspace
        contents = w.compete()
        assert contents == []


# ============================================================
# Tests Format
# ============================================================

class TestFormat:

    def test_get_conscious_context_format(self, isolate_workspace):
        w = isolate_workspace
        w.submit("prefrontal", "Objectif actif: ameliorer le scoring", 0.8, "cognition")
        w.submit("cardiac", "Emotion: enthousiasme intense", 0.6, "emotion")

        ctx = w.get_conscious_context()
        assert "[CONSCIENCE GLOBALE]" in ctx
        assert "PREFRONTAL" in ctx
        assert "CARDIAC" in ctx

    def test_critical_marked_with_exclamation(self, isolate_workspace):
        w = isolate_workspace
        w.submit("reptilian", "ALERTE MENACE", 0.9, "urgence", "critical")

        ctx = w.get_conscious_context()
        assert "!!" in ctx

    def test_empty_context_returns_empty(self, isolate_workspace):
        w = isolate_workspace
        ctx = w.get_conscious_context()
        assert ctx == ""


# ============================================================
# Tests Collecte depuis organes
# ============================================================

class TestCollectFromOrgans:

    def test_collect_resilient_to_missing_organs(self, isolate_workspace):
        """La collecte ne plante pas si des organes sont absents."""
        w = isolate_workspace
        with patch("builtins.__import__", side_effect=ImportError("test")):
            w.collect_from_organs()
        # Pas de crash, submissions vides (imports echouent)
        assert w._stats["total_submissions"] == 0

    def test_collect_adds_submissions(self, isolate_workspace):
        """La collecte ajoute des soumissions si organes disponibles."""
        w = isolate_workspace

        mock_mod = MagicMock()
        mock_mod.awareness = MagicMock()
        mock_mod.awareness.get_purpose_context = MagicMock(return_value="Promethee en mode flow")

        with patch("importlib.import_module", return_value=mock_mod):
            w.collect_from_organs()

        # Au moins une soumission (awareness)
        assert w._stats["total_submissions"] >= 1


# ============================================================
# Tests API
# ============================================================

class TestAPI:

    def test_get_status_structure(self, isolate_workspace):
        w = isolate_workspace
        w.submit("cardiac", "test contenu pour status", 0.5, "emotion")
        w.compete()

        status = w.get_status()
        assert "conscious_count" in status
        assert "max_slots" in status
        assert "contents" in status
        assert "stats" in status
        assert status["max_slots"] == MAX_CONSCIOUS_SLOTS

    def test_content_preview_truncated(self, isolate_workspace):
        w = isolate_workspace
        long_content = "x" * 200
        w.submit("cardiac", long_content, 0.5, "emotion")
        w.compete()

        status = w.get_status()
        assert len(status["contents"][0]["preview"]) <= 80
