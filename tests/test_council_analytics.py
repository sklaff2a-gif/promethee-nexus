"""Tests pour core/council_analytics.py — Council data-driven."""

import pytest
from unittest.mock import patch, MagicMock
from core.council_analytics import (
    analyze_routine_performance,
    analyze_evolution_triage,
    analyze_drive_balance,
    select_data_topic,
    extract_verdict,
    _heuristic_verdict,
    _build_data_mission,
    DRIVE_INTENT_MAP,
)


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _make_entry(intent, status="success", quality=0.7, failure_type=None):
    entry = {"intent": intent, "status": status, "quality_score": quality}
    if failure_type:
        entry["failure_type"] = failure_type
    return entry


class FakeSpec:
    """Simule un ImprovementSpec sans importer le vrai module."""
    def __init__(self, spec_id, status="available", attempts=0,
                 max_attempts=3, failure_reasons=None):
        self.id = spec_id
        self.status = status
        self.attempts = attempts
        self.max_attempts = max_attempts
        self.failure_reasons = failure_reasons or []


class FakeDrive:
    """Simule un Drive sans importer desire_engine."""
    def __init__(self, name, deprivation=40.0):
        self.name = name
        self.deprivation = deprivation


# ═══════════════════════════════════════════════════════════
# TestAnalyzeRoutinePerformance
# ═══════════════════════════════════════════════════════════

class TestAnalyzeRoutinePerformance:

    def test_empty_history(self):
        result = analyze_routine_performance([])
        assert result["worst_intent"] is None
        assert result["best_intent"] is None
        assert result["stats"] == {}

    def test_identifies_worst_intent(self):
        history = [
            _make_entry("EXPANSION_CODE", "success", 0.3),
            _make_entry("EXPANSION_CODE", "error", 0.2, "hallucination"),
            _make_entry("EXPANSION_CODE", "error", 0.1, "alien_import"),
            _make_entry("VEILLE_TECHNO", "success", 0.8),
            _make_entry("VEILLE_TECHNO", "success", 0.9),
        ]
        result = analyze_routine_performance(history)
        assert result["worst_intent"] == "EXPANSION_CODE"
        assert result["best_intent"] == "VEILLE_TECHNO"

    def test_worst_needs_min_3_entries(self):
        history = [
            _make_entry("EXPANSION_CODE", "error", 0.1),
            _make_entry("EXPANSION_CODE", "error", 0.2),
        ]
        result = analyze_routine_performance(history)
        assert result["worst_intent"] is None  # seulement 2 entries

    def test_stats_aggregation(self):
        history = [
            _make_entry("AUDIT", "success", 0.8),
            _make_entry("AUDIT", "error", 0.4, "timeout"),
        ]
        result = analyze_routine_performance(history)
        stats = result["stats"]["AUDIT"]
        assert stats["count"] == 2
        assert stats["successes"] == 1
        assert stats["failures"] == 1
        assert stats["avg_quality"] == 0.6
        assert stats["success_rate"] == 0.5
        assert stats["failure_types"]["timeout"] == 1

    def test_summary_contains_metrics(self):
        history = [_make_entry("X", "success", 0.5)] * 3
        result = analyze_routine_performance(history)
        assert "METRIQUES ROUTINES" in result["summary"]
        assert "X" in result["summary"]


# ═══════════════════════════════════════════════════════════
# TestAnalyzeEvolutionTriage
# ═══════════════════════════════════════════════════════════

class TestAnalyzeEvolutionTriage:

    def test_empty_catalog(self):
        result = analyze_evolution_triage({})
        assert result["candidates_abandon"] == []
        assert result["candidates_retry"] == []

    def test_failed_spec_goes_to_abandon(self):
        specs = {
            "SPEC-01": FakeSpec("SPEC-01", status="failed", attempts=3, max_attempts=3),
        }
        result = analyze_evolution_triage(specs)
        assert len(result["candidates_abandon"]) == 1
        assert result["candidates_abandon"][0]["spec_id"] == "SPEC-01"

    def test_available_with_remaining_goes_to_retry(self):
        specs = {
            "SPEC-02": FakeSpec("SPEC-02", status="available", attempts=1, max_attempts=3),
        }
        result = analyze_evolution_triage(specs)
        assert len(result["candidates_retry"]) == 1
        assert result["candidates_retry"][0]["remaining"] == 2

    def test_available_no_attempts_ignored(self):
        specs = {
            "SPEC-03": FakeSpec("SPEC-03", status="available", attempts=0),
        }
        result = analyze_evolution_triage(specs)
        assert len(result["candidates_retry"]) == 0
        assert len(result["candidates_abandon"]) == 0

    def test_summary_present(self):
        specs = {
            "SPEC-01": FakeSpec("SPEC-01", status="failed", attempts=3,
                                failure_reasons=["timeout", "ast_error"]),
        }
        result = analyze_evolution_triage(specs)
        assert "SPEC-01" in result["summary"]


# ═══════════════════════════════════════════════════════════
# TestAnalyzeDriveBalance
# ═══════════════════════════════════════════════════════════

class TestAnalyzeDriveBalance:

    def test_no_deprived(self):
        drives = {
            "CURIOSITE": FakeDrive("CURIOSITE", 50.0),
            "CREATION": FakeDrive("CREATION", 30.0),
        }
        result = analyze_drive_balance(drives)
        assert result["most_deprived"] is None
        assert result["suggested_intent"] is None

    def test_deprived_drive_detected(self):
        drives = {
            "CURIOSITE": FakeDrive("CURIOSITE", 85.0),
            "CREATION": FakeDrive("CREATION", 30.0),
        }
        result = analyze_drive_balance(drives)
        assert result["most_deprived"] == "CURIOSITE"
        assert result["suggested_intent"] == "VEILLE_TECHNO"

    def test_dict_input(self):
        drives = {
            "MAITRISE": {"deprivation": 90.0},
            "STABILITE": {"deprivation": 20.0},
        }
        result = analyze_drive_balance(drives)
        assert result["most_deprived"] == "MAITRISE"
        assert result["suggested_intent"] == "EVOLUTION_PIPELINE"


# ═══════════════════════════════════════════════════════════
# TestSelectDataTopic
# ═══════════════════════════════════════════════════════════

class TestSelectDataTopic:

    def test_returns_none_insufficient_data(self):
        # Mocker les imports locaux pour isoler le test
        mock_cat = MagicMock()
        mock_cat.specs = {}  # pas de specs
        mock_desires = MagicMock()
        mock_desires.drives = {
            "CURIOSITE": FakeDrive("CURIOSITE", 30.0),  # pas deprived
        }
        with patch.dict("sys.modules", {
            "core.evolution_catalog": MagicMock(EvolutionCatalog=MagicMock(return_value=mock_cat)),
            "core.desire_engine": MagicMock(desires=mock_desires),
        }):
            result = select_data_topic(routine_history=[])
            assert result is None

    def test_returns_routine_audit_topic(self):
        # 10+ routines avec un worst intent
        history = [_make_entry("BAD", "error", 0.1)] * 5 + \
                  [_make_entry("GOOD", "success", 0.9)] * 5
        result = select_data_topic(history)
        assert result is not None
        assert result["subject_key"] == "data_routine_audit"
        assert result["verdict_type"] == "routine_audit"
        assert result["needs_research"] is False

    def test_skips_recent_subjects(self):
        history = [_make_entry("BAD", "error", 0.1)] * 5 + \
                  [_make_entry("GOOD", "success", 0.9)] * 5
        # Tous les topics dans recent → None
        result = select_data_topic(
            history,
            recent_subjects=["data_routine_audit", "data_evolution_triage", "data_drive_balance"],
        )
        assert result is None

    def test_topic_has_mission(self):
        history = [_make_entry("BAD", "error", 0.1)] * 5 + \
                  [_make_entry("GOOD", "success", 0.9)] * 5
        result = select_data_topic(history)
        assert "DEBAT AUTONOME" in result["mission"]
        assert "VERDICT" in result["mission"]


# ═══════════════════════════════════════════════════════════
# TestExtractVerdict
# ═══════════════════════════════════════════════════════════

class TestExtractVerdict:

    def test_empty_transcript(self):
        assert extract_verdict([], "routine_audit") is None

    def test_explicit_verdict_format(self):
        transcript = [
            {"content": "Blah blah VERDICT: PRIORISER VEILLE_TECHNO car la qualite est bonne et c'est le meilleur intent du systeme actuellement"},
        ]
        result = extract_verdict(transcript, "routine_audit")
        assert result is not None
        assert result["action"] == "PRIORISER"
        assert result["target"] == "VEILLE_TECHNO"

    def test_deprioriser_verdict(self):
        transcript = [
            {"content": "VERDICT: DEPRIORISER EXPANSION_CODE — trop d'echecs recents, qualite en baisse constante depuis les dernieres tentatives"},
        ]
        result = extract_verdict(transcript, "routine_audit")
        assert result["action"] == "DEPRIORISER"
        assert result["target"] == "EXPANSION_CODE"

    def test_abandonner_verdict(self):
        transcript = [
            {"content": "VERDICT: ABANDONNER SPEC-42 cette spec a echoue 3 fois sans progres visible et ne produit rien d'utile pour le systeme"},
        ]
        result = extract_verdict(transcript, "evolution_triage")
        assert result["action"] == "ABANDONNER"
        assert result["target"] == "SPEC-42"

    def test_skips_student_entries(self):
        transcript = [
            {"content": "VERDICT: PRIORISER WRONG", "is_student": True},
            {"content": "VERDICT: PRIORISER CORRECT car c'est la bonne routine a prioriser dans le contexte actuel du systeme Promethee"},
        ]
        result = extract_verdict(transcript, "routine_audit")
        assert result["target"] == "CORRECT"

    def test_no_verdict_returns_none(self):
        transcript = [
            {"content": "On devrait peut-etre faire quelque chose, mais pas sur de quoi exactement."},
        ]
        result = extract_verdict(transcript, "routine_audit")
        assert result is None


# ═══════════════════════════════════════════════════════════
# TestHeuristicVerdict
# ═══════════════════════════════════════════════════════════

class TestHeuristicVerdict:

    def test_heuristic_prioriser(self):
        text = "IL FAUT PRIORISER VEILLE_TECHNO pour explorer plus"
        result = _heuristic_verdict(text, "routine_audit")
        assert result is not None
        assert result["action"] == "PRIORISER"
        assert result["target"] == "VEILLE_TECHNO"

    def test_heuristic_no_match(self):
        text = "Tout va bien, continuons ainsi."
        result = _heuristic_verdict(text, "routine_audit")
        assert result is None


# ═══════════════════════════════════════════════════════════
# TestBuildDataMission
# ═══════════════════════════════════════════════════════════

class TestBuildDataMission:

    def test_format(self):
        mission = _build_data_mission("Resume chiffre ici", "Faut-il prioriser X ?")
        assert "DEBAT AUTONOME" in mission
        assert "Resume chiffre ici" in mission
        assert "Faut-il prioriser X" in mission
        assert "VERDICT:" in mission
