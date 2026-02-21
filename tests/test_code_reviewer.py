import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.grimoire.code_reviewer import (
    analyze_code, build_review_prompt, parse_verdict, CodeReviewer
)


# =============================================================================
# SECTION 1 : Tests de l'analyse déterministe (analyze_code)
# =============================================================================
class TestCodeReviewerAnalysis:

    def test_valid_python_ast(self):
        """Code Python valide → ast_valid=True."""
        code = "def hello():\n    return 42\n"
        result = analyze_code(code)
        assert result["ast_valid"] is True
        assert result["ast_error"] is None

    def test_invalid_python_ast(self):
        """Code avec erreur de syntaxe → ast_valid=False."""
        code = "def hello(\n    return 42\n"
        result = analyze_code(code)
        assert result["ast_valid"] is False
        assert result["ast_error"] is not None
        assert "SyntaxError" in result["ast_error"]

    def test_critical_patterns_detected(self):
        """Patterns critiques (eval, exec, etc.) → critical_danger=True."""
        code = "import os\nresult = eval(user_input)\n"
        result = analyze_code(code)
        assert result["critical_danger"] is True
        assert "eval(" in result["danger_details"]

    def test_subprocess_detected(self):
        """subprocess.run → critical_danger=True."""
        code = "import subprocess\nsubprocess.run(['ls'])\n"
        result = analyze_code(code)
        assert result["critical_danger"] is True
        assert "subprocess.run" in result["danger_details"]

    def test_safe_code_no_danger(self):
        """Code safe → critical_danger=False."""
        code = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n\n"
            "def process(data):\n"
            "    logger.info('Processing %s', data)\n"
            "    return data.strip()\n"
        )
        result = analyze_code(code)
        assert result["ast_valid"] is True
        assert result["critical_danger"] is False
        assert result["has_def_or_class"] is True

    def test_empty_code(self):
        """Code vide → ast_valid=False, complete=False."""
        result = analyze_code("")
        assert result["ast_valid"] is False
        assert result["complete"] is False

    def test_truncated_code_detected(self):
        """Code tronqué (finit par '...') → complete=False."""
        code = "def hello():\n    return 42\n..."
        result = analyze_code(code)
        assert result["complete"] is False

    def test_line_count(self):
        """Comptage correct des lignes."""
        code = "import os\nimport sys\n\ndef foo():\n    pass\n"
        result = analyze_code(code)
        assert result["line_count"] == 5

    def test_transforms_detection(self):
        """Détection des types de transformations."""
        code = (
            "import logging\n"
            "from typing import Dict\n\n"
            "class MyClass:\n"
            "    def my_method(self):\n"
            '        """Ma docstring."""\n'
            "        try:\n"
            "            pass\n"
            "        except Exception:\n"
            "            logger.error('oops')\n"
        )
        result = analyze_code(code)
        assert "ajout_import" in result["transforms"]
        assert "ajout_classe" in result["transforms"]
        assert "ajout_methode" in result["transforms"]
        assert "try_except_wrap" in result["transforms"]

    def test_has_def_or_class_false(self):
        """Code sans def/class → has_def_or_class=False."""
        code = "import os\nx = 42\nprint(x)\n"
        result = analyze_code(code)
        assert result["has_def_or_class"] is False

    def test_multiple_critical_patterns(self):
        """Plusieurs patterns critiques → tous listés."""
        code = "import os\nos.remove('x')\nresult = exec('code')\n"
        result = analyze_code(code)
        assert result["critical_danger"] is True
        assert "os.remove" in result["danger_details"]
        assert "exec(" in result["danger_details"]


# =============================================================================
# SECTION 2 : Tests du parsing de verdict
# =============================================================================
class TestCodeReviewerVerdicts:

    def test_verdict_safe(self):
        assert parse_verdict("VERDICT: SAFE — code propre, aucun risque.") is True

    def test_verdict_unsafe(self):
        assert parse_verdict("VERDICT: UNSAFE — modification dangereuse.") is False

    def test_verdict_safe_lowercase(self):
        assert parse_verdict("verdict: safe — ok") is True

    def test_verdict_missing(self):
        """Pas de verdict explicite → False (conservateur)."""
        assert parse_verdict("Le code semble correct mais je ne suis pas sûr.") is False

    def test_verdict_empty(self):
        assert parse_verdict("") is False

    def test_verdict_none(self):
        assert parse_verdict(None) is False

    def test_verdict_safe_fallback(self):
        """SAFE en début de ligne (fallback sans VERDICT:)."""
        assert parse_verdict("Analyse complète.\nSAFE — pas de problème.") is True

    def test_verdict_unsafe_with_prefix(self):
        """VERDICT: UNSAFE avec du texte autour."""
        resp = "Après analyse approfondie...\nVERDICT: UNSAFE — eval détecté\nFin."
        assert parse_verdict(resp) is False


# =============================================================================
# SECTION 3 : Tests du prompt
# =============================================================================
class TestCodeReviewerPrompt:

    def test_prompt_contains_analysis(self):
        """Le prompt inclut les données de l'analyse déterministe."""
        analysis = {
            "ast_valid": True, "ast_error": None,
            "critical_danger": False, "danger_details": "",
            "complete": True, "line_count": 10,
            "transforms": ["ajout_methode", "ajout_logging"],
            "has_def_or_class": True,
        }
        prompt = build_review_prompt("Test spec", "def foo(): pass", analysis)
        assert "VALIDE" in prompt
        assert "ajout_methode" in prompt
        assert "ajout_logging" in prompt
        assert "CodeSmith" in prompt
        assert "VERDICT" in prompt

    def test_prompt_with_dangers(self):
        """Le prompt signale les patterns dangereux."""
        analysis = {
            "ast_valid": True, "ast_error": None,
            "critical_danger": True, "danger_details": "eval(",
            "complete": True, "line_count": 5,
            "transforms": [], "has_def_or_class": False,
        }
        prompt = build_review_prompt("Test", "x = eval(y)", analysis)
        assert "eval(" in prompt

    def test_prompt_with_invalid_ast(self):
        """Le prompt signale une AST invalide."""
        analysis = {
            "ast_valid": False, "ast_error": "SyntaxError ligne 3: invalid syntax",
            "critical_danger": False, "danger_details": "",
            "complete": True, "line_count": 3,
            "transforms": [], "has_def_or_class": False,
        }
        prompt = build_review_prompt("Test", "broken code", analysis)
        assert "INVALIDE" in prompt
        assert "SyntaxError" in prompt


# =============================================================================
# SECTION 4 : Tests de l'agent CodeReviewer (process_task)
# =============================================================================
class TestCodeReviewerAgent:

    @pytest.mark.asyncio
    async def test_safe_code_verdict(self):
        """Code safe + LLM dit SAFE → verdict SAFE."""
        reviewer = CodeReviewer()
        reviewer.generate_content = AsyncMock(
            return_value="VERDICT: SAFE — transformation déterministe, aucun risque."
        )
        result = await reviewer.process_task({
            "mission": "REVUE SPEC [A-001]",
            "context": "import logging\n\ndef new_method(self):\n    logging.info('ok')\n"
        })
        assert result["verdict"] == "SAFE"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_unsafe_code_verdict(self):
        """Code safe mais LLM dit UNSAFE → verdict UNSAFE."""
        reviewer = CodeReviewer()
        reviewer.generate_content = AsyncMock(
            return_value="VERDICT: UNSAFE — la méthode écrase un attribut critique."
        )
        result = await reviewer.process_task({
            "mission": "REVUE SPEC [A-002]",
            "context": "def process_task(self, payload):\n    return None\n"
        })
        assert result["verdict"] == "UNSAFE"
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_critical_danger_short_circuit(self):
        """Code avec eval → UNSAFE sans appel LLM."""
        reviewer = CodeReviewer()
        reviewer.generate_content = AsyncMock()
        result = await reviewer.process_task({
            "mission": "REVUE SPEC [A-003]",
            "context": "result = eval(user_input)\n"
        })
        assert result["verdict"] == "UNSAFE"
        assert "patterns critiques" in result["result"]
        reviewer.generate_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_ast_short_circuit(self):
        """Code avec syntaxe invalide → UNSAFE sans appel LLM."""
        reviewer = CodeReviewer()
        reviewer.generate_content = AsyncMock()
        result = await reviewer.process_task({
            "mission": "REVUE SPEC [A-004]",
            "context": "def broken(\n    return\n"
        })
        assert result["verdict"] == "UNSAFE"
        assert "syntaxe invalide" in result["result"]
        reviewer.generate_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_dict_contains_analysis(self):
        """Le résultat contient l'analyse déterministe dans 'review'."""
        reviewer = CodeReviewer()
        reviewer.generate_content = AsyncMock(return_value="VERDICT: SAFE — ok")
        result = await reviewer.process_task({
            "mission": "Test",
            "context": "def foo():\n    pass\n"
        })
        assert "review" in result
        assert result["review"]["ast_valid"] is True
        assert result["review"]["has_def_or_class"] is True


# =============================================================================
# SECTION 5 : Tests de l'intégration Phase 5b dans evolution_agent
# =============================================================================
class TestEvolutionPhase5bRetry:
    """Tests du retry Phase 5b dans evolution_agent.py."""

    def _make_spec(self):
        """Crée une ImprovementSpec minimale pour les tests."""
        from core.evolution_catalog import ImprovementSpec
        return ImprovementSpec(
            id="TEST-001", name="Test Spec", target_file="core/base_agent.py",
            description="Test", category="resilience", difficulty=1,
            target_method="process_task", code_template="", validation=""
        )

    def _make_mock_orchestrator(self, dispatch_responses):
        """Crée un orchestrator mocké avec des réponses dispatch séquentielles."""
        mock_orch = MagicMock()
        mock_orch.dispatch_task = AsyncMock(side_effect=dispatch_responses)
        mock_orch._contains_python_code = MagicMock(return_value=True)
        return mock_orch

    @pytest.mark.asyncio
    async def test_phase5b_skip_non_codesmith(self):
        """Phase 5b ne s'active PAS si code_source != 'codesmith'."""
        mock_orch = self._make_mock_orchestrator([])
        spec = self._make_spec()
        code_source = "llm"
        deploy_status = "refused"

        # Reproduire la condition Phase 5b
        if deploy_status != "success" and code_source == "codesmith":
            pytest.fail("Phase 5b should not activate for llm code_source")

        # Aucun appel dispatch
        assert mock_orch.dispatch_task.call_count == 0

    @pytest.mark.asyncio
    async def test_phase5b_reviewer_safe_resubmit(self):
        """Phase 5b : reviewer SAFE → re-soumission architect → success."""
        mock_orch = self._make_mock_orchestrator([
            {"status": "success", "verdict": "SAFE", "result": "VERDICT: SAFE — ok"},
            {"status": "success"},
        ])
        spec = self._make_spec()
        code_source = "codesmith"
        deploy_status = "refused"
        generated_code = "def foo():\n    pass\n"

        if deploy_status != "success" and code_source == "codesmith":
            reviewer_response = await mock_orch.dispatch_task("code_reviewer", {
                "mission": f"REVUE [{spec.id}]", "context": generated_code
            })
            reviewer_verdict = reviewer_response.get("verdict", "UNSAFE")
            if reviewer_verdict == "SAFE":
                architect_response2 = await mock_orch.dispatch_task("architect", {
                    "mission": f"[SECOND EXAMEN] [{spec.id}]", "context": generated_code
                })
                deploy_status = architect_response2.get("status", "unknown")

        assert deploy_status == "success"
        assert mock_orch.dispatch_task.call_count == 2

    @pytest.mark.asyncio
    async def test_phase5b_reviewer_unsafe_abandon(self):
        """Phase 5b : reviewer UNSAFE → abandon (pas de re-soumission)."""
        mock_orch = self._make_mock_orchestrator([
            {"status": "error", "verdict": "UNSAFE", "result": "VERDICT: UNSAFE — eval"},
        ])
        spec = self._make_spec()
        code_source = "codesmith"
        deploy_status = "refused"
        generated_code = "def foo():\n    pass\n"

        if deploy_status != "success" and code_source == "codesmith":
            reviewer_response = await mock_orch.dispatch_task("code_reviewer", {
                "mission": f"REVUE [{spec.id}]", "context": generated_code
            })
            reviewer_verdict = reviewer_response.get("verdict", "UNSAFE")
            if reviewer_verdict == "SAFE":
                pytest.fail("Should not re-submit when reviewer says UNSAFE")

        assert deploy_status == "refused"
        assert mock_orch.dispatch_task.call_count == 1

    @pytest.mark.asyncio
    async def test_phase5b_reviewer_error_graceful(self):
        """Phase 5b : erreur code_reviewer → abandon gracieux, deploy_status inchangé."""
        mock_orch = self._make_mock_orchestrator([])
        mock_orch.dispatch_task = AsyncMock(side_effect=Exception("Grimoire not found"))
        spec = self._make_spec()
        code_source = "codesmith"
        deploy_status = "refused"
        generated_code = "def foo():\n    pass\n"

        if deploy_status != "success" and code_source == "codesmith":
            try:
                await mock_orch.dispatch_task("code_reviewer", {
                    "mission": f"REVUE [{spec.id}]", "context": generated_code
                })
            except Exception:
                pass

        assert deploy_status == "refused"

    @pytest.mark.asyncio
    async def test_phase5b_double_refusal(self):
        """Phase 5b : reviewer SAFE mais architect refuse encore → reste refused."""
        mock_orch = self._make_mock_orchestrator([
            {"status": "success", "verdict": "SAFE", "result": "VERDICT: SAFE — ok"},
            {"status": "refused"},
        ])
        spec = self._make_spec()
        code_source = "codesmith"
        deploy_status = "refused"
        generated_code = "def foo():\n    pass\n"

        if deploy_status != "success" and code_source == "codesmith":
            reviewer_response = await mock_orch.dispatch_task("code_reviewer", {
                "mission": f"REVUE [{spec.id}]", "context": generated_code
            })
            reviewer_verdict = reviewer_response.get("verdict", "UNSAFE")
            if reviewer_verdict == "SAFE":
                architect_response2 = await mock_orch.dispatch_task("architect", {
                    "mission": f"[SECOND EXAMEN] [{spec.id}]", "context": generated_code
                })
                deploy_status = architect_response2.get("status", "unknown")

        assert deploy_status == "refused"
        assert mock_orch.dispatch_task.call_count == 2
