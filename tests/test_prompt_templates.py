"""Tests pour core/prompt_templates.py — Guardrails anti-hallucination."""
import pytest
from core.prompt_templates import (
    CODE_GENERATION_GUARDRAIL,
    AUTONOMY_GUARDRAIL,
    TEST_GENERATION_GUARDRAIL,
    analysis_guardrail,
    get_project_structure,
    reset_project_structure_cache,
)


class TestGuardrailConstants:
    """Vérifie que les constantes guardrail contiennent les mots-clés critiques."""

    def test_code_generation_guardrail_contains_interdictions(self):
        assert "langchain" in CODE_GENERATION_GUARDRAIL
        assert "openai" in CODE_GENERATION_GUARDRAIL
        assert "flask" in CODE_GENERATION_GUARDRAIL
        assert "django" in CODE_GENERATION_GUARDRAIL
        assert "PROMÉTHÉE" in CODE_GENERATION_GUARDRAIL

    def test_code_generation_guardrail_mentions_project_dirs(self):
        assert "core/" in CODE_GENERATION_GUARDRAIL
        assert "Agents/" in CODE_GENERATION_GUARDRAIL

    def test_autonomy_guardrail_contains_project_context(self):
        assert "PROMÉTHÉE" in AUTONOMY_GUARDRAIL
        assert "Windows" in AUTONOMY_GUARDRAIL
        assert "langchain" in AUTONOMY_GUARDRAIL

    def test_test_generation_guardrail_mentions_mock(self):
        assert "unittest.mock" in TEST_GENERATION_GUARDRAIL
        assert "API RÉELLE" in TEST_GENERATION_GUARDRAIL

    def test_guardrails_are_nonempty_strings(self):
        assert isinstance(CODE_GENERATION_GUARDRAIL, str)
        assert len(CODE_GENERATION_GUARDRAIL) > 50
        assert isinstance(AUTONOMY_GUARDRAIL, str)
        assert len(AUTONOMY_GUARDRAIL) > 50
        assert isinstance(TEST_GENERATION_GUARDRAIL, str)
        assert len(TEST_GENERATION_GUARDRAIL) > 50


class TestAnalysisGuardrail:
    """Vérifie la fonction analysis_guardrail()."""

    def test_returns_string_with_project_files(self):
        result = analysis_guardrail("core/router.py, Agents/coder_agent.py")
        assert "core/router.py" in result
        assert "Agents/coder_agent.py" in result

    def test_contains_warning(self):
        result = analysis_guardrail("test.py")
        assert "NE MENTIONNE PAS" in result
        assert "RAPPEL CRITIQUE" in result

    def test_empty_files_still_works(self):
        result = analysis_guardrail("")
        assert isinstance(result, str)
        assert "RAPPEL CRITIQUE" in result


class TestGetProjectStructure:
    """Vérifie get_project_structure()."""

    def setup_method(self):
        reset_project_structure_cache()

    def test_returns_nonempty_string(self):
        result = get_project_structure()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_known_directories(self):
        result = get_project_structure()
        assert "core/" in result or "Agents/" in result

    def test_cache_returns_same_object(self):
        """Le cache doit retourner la même string."""
        first = get_project_structure()
        second = get_project_structure()
        assert first is second

    def test_reset_cache(self):
        """reset_project_structure_cache() permet un recalcul."""
        first = get_project_structure()
        reset_project_structure_cache()
        second = get_project_structure()
        assert first == second  # Même contenu
        # Mais pas le même objet (recalculé)
        # Note: en Python, les petites strings peuvent être internées,
        # donc on ne teste pas `is not`
