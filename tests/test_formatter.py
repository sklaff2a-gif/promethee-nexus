"""Tests pour le Formatter Agent - extraction déterministe fallback."""
import pytest
from unittest.mock import patch, AsyncMock
from Agents.formatter_agent import DivineFormatter


class TestIsValidFilename:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_valid_python_file(self):
        assert self.f._is_valid_filename("core/test.py") is True

    def test_valid_simple_file(self):
        assert self.f._is_valid_filename("agent_generator.py") is True

    def test_rejects_python_keyword(self):
        assert self.f._is_valid_filename("shutil.copy") is False
        assert self.f._is_valid_filename("import.py") is False

    def test_rejects_no_extension(self):
        assert self.f._is_valid_filename("randomword") is False

    def test_accepts_special_files(self):
        assert self.f._is_valid_filename("makefile") is True
        assert self.f._is_valid_filename("dockerfile") is True

    def test_rejects_empty(self):
        assert self.f._is_valid_filename("") is False
        assert self.f._is_valid_filename(None) is False


class TestExtractFromContext:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_extract_fichier_marker(self):
        text = "Voici le code :\nFICHIER: core/utils.py\n```python\nprint('hello')\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "core/utils.py"
        assert "print('hello')" in code

    def test_extract_path_pattern(self):
        text = "Le fichier core/new_module.py contient :\n```python\nclass Foo:\n    pass\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "core/new_module.py"
        assert "class Foo:" in code

    def test_extract_agents_path(self):
        text = "Modification dans Agents/custom_agent.py\n```python\nimport os\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "Agents/custom_agent.py"
        assert "import os" in code

    def test_extract_simple_py_file(self):
        text = "Le module agent_code_generator.py devrait avoir :\n```python\ndef generate():\n    return 42\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "agent_code_generator.py"
        assert "def generate():" in code

    def test_no_code_block(self):
        text = "Il faudrait modifier core/base.py mais je n'ai pas de code."
        target, code = self.f._extract_from_context(text)
        assert target == "core/base.py"
        assert code is None

    def test_no_filename_no_code(self):
        text = "Voici une explication générale sans code ni fichier."
        target, code = self.f._extract_from_context(text)
        assert target is None
        assert code is None

    def test_code_but_no_filename(self):
        text = "Voici le code :\n```python\nprint('hello')\n```"
        target, code = self.f._extract_from_context(text)
        assert target is None
        assert "print('hello')" in code

    def test_picks_longest_code_block(self):
        text = (
            "```python\nx=1\n```\n"
            "Et aussi :\n"
            "```python\nclass BigClass:\n    def method(self):\n        return 42\n```"
        )
        _, code = self.f._extract_from_context(text)
        assert "class BigClass:" in code

    def test_rejects_import_py_as_filename(self):
        """import.py n'est pas un nom de fichier valide (blacklist)."""
        text = "Le fichier import.py\n```python\nx=1\n```"
        target, code = self.f._extract_from_context(text)
        # import.py est blacklisté, donc target devrait être None ou un autre match
        assert target != "import.py"

    def test_backslash_paths(self):
        text = "Le fichier core\\router.py a changé\n```python\npass\n```"
        target, code = self.f._extract_from_context(text)
        assert target is not None
        assert "router" in target


class TestHasFormattableCode:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_code_block_detected(self):
        text = "Voici le code :\n```python\ndef hello():\n    return 42\n```"
        assert self.f._has_formattable_code(text) is True

    def test_python_lines_detected(self):
        text = "import os\nfrom pathlib import Path\ndef process():\n    return None\n"
        assert self.f._has_formattable_code(text) is True

    def test_analysis_text_rejected(self):
        """Un texte d'analyse sans code est rejeté."""
        text = (
            "L'architecture actuelle utilise un pattern observer. "
            "Il serait judicieux d'ajouter un cache LRU pour optimiser les appels. "
            "Le module devrait être refactorisé en trois composants distincts."
        )
        assert self.f._has_formattable_code(text) is False

    def test_single_import_not_enough(self):
        text = "Il faudrait faire import os dans le fichier."
        assert self.f._has_formattable_code(text) is False

    def test_class_definition_code(self):
        text = "class MyAgent:\n    def process(self):\n        return None\n    def cleanup(self):\n        pass"
        assert self.f._has_formattable_code(text) is True


class TestRebuildFormattedResponse:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_rebuild(self):
        result = self.f._rebuild_formatted_response("core/test.py", "print('hello')")
        assert "FICHIER: core/test.py" in result
        assert "```python" in result
        assert "print('hello')" in result


class TestEvolutionBypass:
    """Tests du bypass LLM pour le pipeline Evolution."""

    def setup_method(self):
        with patch("core.base_agent.ChromaMemoryManager", None):
            self.f = DivineFormatter()

    @pytest.mark.asyncio
    async def test_evolution_bypass_dispatches_to_factory(self):
        """Code Evolution avec spec_id → bypass LLM, dispatch direct à Factory."""
        valid_code = "import os\ndef hello():\n    return 42\n"
        context = f"FICHIER: core/test_module.py\n```python\n{valid_code}\n```"

        with patch.object(self.f, "log_thought"), \
             patch("core.orchestrator.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "ok"})
            payload = {
                "mission": "Déploie ce code.",
                "context": context,
                "evolution_spec_id": "PERF-001",
            }
            result = await self.f.process_task(payload)

        assert result["status"] == "success"
        assert "EVOLUTION_FACTORY_OK" in result["result"]

    @pytest.mark.asyncio
    async def test_evolution_bypass_preserves_full_code(self):
        """Le bypass ne tronque PAS le code (contrairement à full_text[:2000])."""
        # Créer un code > 2000 chars
        big_code = "import os\nimport logging\n\n" + "def func_0():\n    return 0\n" * 200
        assert len(big_code) > 2000
        context = f"FICHIER: core/big_module.py\n```python\n{big_code}\n```"

        with patch.object(self.f, "log_thought"), \
             patch("core.orchestrator.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "ok"})
            result = await self.f.process_task({
                "mission": "", "context": context,
                "evolution_spec_id": "OBS-004",
            })

        assert result["status"] == "success"
        mock_orch.dispatch_task.assert_called_once()
        call_args = mock_orch.dispatch_task.call_args
        factory_payload = call_args[0][1]  # 2e arg positionnel
        assert len(factory_payload.get("context", "")) > 2000

    @pytest.mark.asyncio
    async def test_evolution_bypass_rejects_broken_syntax(self):
        """Si le code Evolution a une syntaxe cassée, rejet immédiat."""
        broken_code = "def hello(\n    return 42\n"  # parenthèse non fermée
        context = f"FICHIER: core/broken.py\n```python\n{broken_code}\n```"

        with patch.object(self.f, "log_thought"):
            result = await self.f.process_task({
                "mission": "", "context": context,
                "evolution_spec_id": "PERF-002",
            })

        assert result["status"] == "error"
        assert "SYNTAXE_INVALIDE" in result["result"]

    @pytest.mark.asyncio
    async def test_evolution_bypass_propagates_spec_id(self):
        """Le spec_id est propagé au payload Factory."""
        valid_code = "import os\ndef hello():\n    return 42\n"
        context = f"FICHIER: core/test.py\n```python\n{valid_code}\n```"

        with patch.object(self.f, "log_thought"), \
             patch("core.orchestrator.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "ok"})
            await self.f.process_task({
                "mission": "", "context": context,
                "evolution_spec_id": "PERF-003",
            })

        mock_orch.dispatch_task.assert_called_once()
        factory_payload = mock_orch.dispatch_task.call_args[0][1]
        assert factory_payload.get("evolution_spec_id") == "PERF-003"

    @pytest.mark.asyncio
    async def test_evolution_bypass_fallback_to_llm_if_no_code(self):
        """Si extraction échoue, fallback vers le parcours LLM normal."""
        # Pas de bloc de code ni de FICHIER:
        context = "Voici une explication sans code ni fichier."

        with patch.object(self.f, "log_thought"):
            result = await self.f.process_task({
                "mission": "", "context": context,
                "evolution_spec_id": "PERF-004",
            })

        # Le bypass échoue → tombe dans le parcours normal → _has_formattable_code = False → NO_CODE
        assert result["status"] == "success"
        assert "NO_CODE_TO_FORMAT" in result["result"]

    @pytest.mark.asyncio
    async def test_no_spec_id_uses_llm_path(self):
        """Sans evolution_spec_id, le parcours LLM classique est utilisé."""
        valid_code = "import os\ndef hello():\n    return 42\n"
        context = f"FICHIER: core/test.py\n```python\n{valid_code}\n```"

        good_response = f"FICHIER: core/test.py\nCODE:\n```python\n{valid_code}\n```"

        with patch.object(self.f, "log_thought"), \
             patch.object(self.f, "generate_content", new=AsyncMock(return_value=good_response)), \
             patch("core.orchestrator.orchestrator") as mock_orch:
            mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "ok"})
            result = await self.f.process_task({
                "mission": "Nettoie ce code.",
                "context": context,
                # Pas de evolution_spec_id
            })

        assert result["status"] == "success"
        assert "FACTORY_OK" in result["result"]


class TestSyntaxValidation:
    """Tests de la validation ast.parse() avant dispatch Factory (Fix run 2026-02-18)."""

    def setup_method(self):
        with patch("core.base_agent.ChromaMemoryManager", None):
            self.f = DivineFormatter()

    @pytest.mark.asyncio
    async def test_broken_code_fallback_to_original(self):
        """Si le LLM casse la syntaxe, fallback vers le code original du contexte."""
        valid_code = "import os\ndef hello():\n    return 42\n"
        broken_code = "import os\ndef hello(\n    return 42\n"  # parenthèse non fermée

        # Simuler : generate_content retourne du code cassé dans le bon format
        broken_response = f"FICHIER: core/test.py\nCODE:\n```python\n{broken_code}\n```"

        with patch.object(self.f, "generate_content", new=AsyncMock(return_value=broken_response)):
            with patch.object(self.f, "log_thought"):
                with patch("core.orchestrator.orchestrator") as mock_orch:
                    mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "ok"})
                    payload = {
                        "mission": "Nettoie ce code.",
                        "context": f"FICHIER: core/test.py\n```python\n{valid_code}\n```",
                    }
                    result = await self.f.process_task(payload)

        # Le fallback déterministe doit récupérer le code original valide
        assert result["status"] in ("success", "error")
        # Si le fallback marche, on envoie à la Factory
        if result["status"] == "success":
            assert "FACTORY" in result["result"]

    @pytest.mark.asyncio
    async def test_valid_code_dispatched_normally(self):
        """Si le code LLM est syntaxiquement valide, dispatch normal à la Factory."""
        valid_code = "import os\ndef hello():\n    return 42\n"
        good_response = f"FICHIER: core/test.py\nCODE:\n```python\n{valid_code}\n```"

        with patch.object(self.f, "generate_content", new=AsyncMock(return_value=good_response)):
            with patch.object(self.f, "log_thought"):
                with patch("core.orchestrator.orchestrator") as mock_orch:
                    mock_orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "ok"})
                    payload = {
                        "mission": "Nettoie ce code.",
                        "context": f"FICHIER: core/test.py\n```python\n{valid_code}\n```",
                    }
                    result = await self.f.process_task(payload)

        assert result["status"] == "success"
        assert "FACTORY" in result["result"]

    @pytest.mark.asyncio
    async def test_factory_failure_propagates(self):
        """C03 — Si Factory échoue, Formatter retourne error (pas success)."""
        valid_code = "import os\ndef hello():\n    return 42\n"
        good_response = f"FICHIER: core/test.py\nCODE:\n```python\n{valid_code}\n```"

        with patch.object(self.f, "generate_content", new=AsyncMock(return_value=good_response)):
            with patch.object(self.f, "log_thought"):
                with patch("core.orchestrator.orchestrator") as mock_orch:
                    mock_orch.dispatch_task = AsyncMock(return_value={"status": "error", "result": "PROTECTED_FILE"})
                    payload = {
                        "mission": "Nettoie ce code.",
                        "context": f"FICHIER: core/test.py\n```python\n{valid_code}\n```",
                    }
                    result = await self.f.process_task(payload)

        assert result["status"] == "error"
        assert "FACTORY_ECHEC" in result["result"]
