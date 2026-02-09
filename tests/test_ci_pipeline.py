import pytest
import asyncio
import os
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

from core.ci_pipeline import (
    extract_python_code,
    _slugify_filename,
    _rollback,
    _remember_failure,
    _remember_success,
    _recall_failures,
    _recall_successes,
    run_pipeline,
    _on_artifact_created,
)
from core.event_bus.bus import bus


# --- Tests extract_python_code ---

class TestExtractPythonCode:

    def test_bloc_markdown_python(self):
        text = "Voici les tests :\n```python\nimport pytest\ndef test_ok():\n    assert True\n```\nFin."
        result = extract_python_code(text)
        assert result == "import pytest\ndef test_ok():\n    assert True"

    def test_bloc_markdown_sans_lang(self):
        text = "```\nimport os\nprint(os.getcwd())\n```"
        result = extract_python_code(text)
        assert result == "import os\nprint(os.getcwd())"

    def test_multiple_blocs_prend_le_dernier(self):
        text = "```python\nprint('premier')\n```\nBla bla\n```python\nimport pytest\ndef test_final():\n    assert 1\n```"
        result = extract_python_code(text)
        assert "test_final" in result
        assert "premier" not in result

    def test_heuristique_fallback(self):
        text = "import os\nfrom pathlib import Path\ndef hello():\n    return 42"
        result = extract_python_code(text)
        assert result is not None
        assert "import os" in result

    def test_pas_de_code_retourne_none(self):
        text = "Ceci est un texte normal sans code Python."
        result = extract_python_code(text)
        assert result is None


# --- Tests _slugify_filename ---

class TestSlugifyFilename:

    def test_nom_simple(self):
        assert _slugify_filename("coder_agent.py") == "coder_agent"

    def test_chemin_imbrique(self):
        assert _slugify_filename("Agents/coder_agent.py") == "coder_agent"

    def test_chemin_windows(self):
        assert _slugify_filename("core\\utils\\helper.py") == "helper"


# --- Tests anti-boucle ---

class TestAntiLoopProtection:

    @pytest.mark.asyncio
    async def test_fichier_dans_tests_ignore(self):
        """Les fichiers dans tests/ ne déclenchent pas le pipeline."""
        with patch("core.ci_pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
            await _on_artifact_created({
                "filepath": "tests/auto/test_foo.py",
                "filename": "test_foo.py"
            })
            mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_fichier_non_py_ignore(self):
        """Les fichiers non-.py ne déclenchent pas le pipeline."""
        with patch("core.ci_pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
            await _on_artifact_created({
                "filepath": "docs/readme.md",
                "filename": "readme.md"
            })
            mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_fichier_py_valide_declenche(self):
        """Un .py hors tests/ déclenche le pipeline."""
        with patch("core.ci_pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
            await _on_artifact_created({
                "filepath": "Agents/new_agent.py",
                "filename": "new_agent.py"
            })
            # run_pipeline est lancé via asyncio.create_task, on attend un tick
            await asyncio.sleep(0.05)
            mock_run.assert_called_once()


# --- Tests _rollback ---

class TestRollback:

    def test_rollback_avec_bak(self, tmp_path):
        original = tmp_path / "module.py"
        bak = tmp_path / "module.py.bak"

        original.write_text("code modifié")
        bak.write_text("code original")

        result = _rollback(str(original))
        assert result is True
        assert original.read_text() == "code original"

    def test_rollback_sans_bak(self, tmp_path):
        original = tmp_path / "module.py"
        original.write_text("code")

        result = _rollback(str(original))
        assert result is False


# --- Tests run_pipeline ---

class TestRunPipeline:

    @pytest.fixture
    def setup_agents(self):
        """Configure les agents mockés dans orchestrator.agents."""
        coder = AsyncMock()
        coder.has_memory = True
        coder.remember = MagicMock()
        coder.recall = MagicMock(return_value="")
        architect = AsyncMock()
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.remember = MagicMock()
        strategist.recall = MagicMock(return_value="")

        agents = {
            "coder": coder,
            "architect": architect,
            "strategist": strategist,
        }
        return agents, coder, architect, strategist

    @pytest.mark.asyncio
    async def test_pipeline_succes_complet(self, tmp_path, setup_agents):
        agents, coder, architect, strategist = setup_agents

        # Fichier source
        src = tmp_path / "module.py"
        src.write_text("def hello():\n    return 42\n")

        # Coder génère des tests
        coder.generate_content = AsyncMock(return_value=(
            "```python\nimport pytest\ndef test_hello():\n    assert True\n```"
        ))

        # Architect valide
        architect.generate_content = AsyncMock(return_value="VALIDÉ - Code propre et fonctionnel")

        # Pytest réussit
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys") as mock_sys:
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        strategist.remember.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_rollback_tests_echoues(self, tmp_path, setup_agents):
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "module.py"
        src.write_text("def broken():\n    pass\n")
        bak = tmp_path / "module.py.bak"
        bak.write_text("def original():\n    pass\n")

        coder.generate_content = AsyncMock(return_value=(
            "```python\ndef test_x():\n    assert True\n```"
        ))

        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(False, "FAILED")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Rollback effectué
        assert src.read_text() == "def original():\n    pass\n"

    @pytest.mark.asyncio
    async def test_pipeline_rollback_architect_refuse(self, tmp_path, setup_agents):
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "module.py"
        src.write_text("def risky():\n    pass\n")
        bak = tmp_path / "module.py.bak"
        bak.write_text("def safe():\n    pass\n")

        coder.generate_content = AsyncMock(return_value=(
            "```python\ndef test_x():\n    assert True\n```"
        ))
        architect.generate_content = AsyncMock(return_value="REFUSÉ - Vulnérabilité détectée")

        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Rollback effectué
        assert src.read_text() == "def safe():\n    pass\n"

    @pytest.mark.asyncio
    async def test_pipeline_fichier_introuvable(self, tmp_path):
        """Pipeline publie un échec si le fichier n'existe pas."""
        events = []

        async def capture(data):
            events.append(data)

        bus.subscribe("CI_PIPELINE_RESULT", capture)

        await run_pipeline("fantome.py", str(tmp_path / "fantome.py"))

        # Laisser le bus traiter
        await asyncio.sleep(0.05)
        assert any(not e.get("success", True) for e in events)


# --- Tests mémoire CI/CD ---

class TestMemoryFailure:

    def test_remember_failure_appelle_coder_remember(self):
        """Un échec est archivé dans la collection ci_failures via le coder."""
        coder = MagicMock()
        coder.has_memory = True
        coder.remember = MagicMock()

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"coder": coder}
            _remember_failure("module.py", "def broken(): pass", "test_execution", "AssertionError")

        coder.remember.assert_called_once()
        call_args = coder.remember.call_args
        assert "ÉCHEC" in call_args[0][0]
        assert "module.py" in call_args[0][0]
        assert "test_execution" in call_args[0][0]
        assert call_args[0][1]["outcome"] == "failure"
        assert call_args[0][2] == "ci_failures"

    def test_remember_failure_sans_coder_ne_crash_pas(self):
        """Pas de crash si le coder n'est pas disponible."""
        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {}
            _remember_failure("x.py", "code", "step", "err")  # Ne doit pas lever d'exception

    def test_remember_failure_sans_memoire_ne_crash_pas(self):
        """Pas de crash si le coder n'a pas de mémoire."""
        coder = MagicMock()
        coder.has_memory = False
        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"coder": coder}
            _remember_failure("x.py", "code", "step", "err")
        coder.remember.assert_not_called()


class TestMemorySuccess:

    def test_remember_success_appelle_strategist_remember(self):
        """Un succès est archivé dans ci_successes via le strategist."""
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.remember = MagicMock()

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"strategist": strategist}
            _remember_success("module.py", "def ok(): pass", "def test_ok(): assert True", "VALIDÉ")

        strategist.remember.assert_called_once()
        call_args = strategist.remember.call_args
        assert "SUCCÈS" in call_args[0][0]
        assert "module.py" in call_args[0][0]
        assert "def ok(): pass" in call_args[0][0]
        assert "def test_ok()" in call_args[0][0]
        assert "VALIDÉ" in call_args[0][0]
        assert call_args[0][1]["outcome"] == "success"
        assert call_args[0][2] == "ci_successes"


class TestMemoryRecall:

    def test_recall_failures_interroge_ci_failures(self):
        """recall_failures consulte la bonne collection."""
        coder = MagicMock()
        coder.has_memory = True
        coder.recall = MagicMock(return_value="échec passé sur module similaire")

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"coder": coder}
            result = _recall_failures("module.py", "def hello(): pass")

        coder.recall.assert_called_once()
        assert coder.recall.call_args[1]["collection"] == "ci_failures"
        assert result == "échec passé sur module similaire"

    def test_recall_successes_interroge_ci_successes(self):
        """recall_successes consulte la bonne collection."""
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.recall = MagicMock(return_value="pattern validé")

        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {"strategist": strategist}
            result = _recall_successes("module.py", "def hello(): pass")

        strategist.recall.assert_called_once()
        assert strategist.recall.call_args[1]["collection"] == "ci_successes"
        assert result == "pattern validé"

    def test_recall_sans_agent_retourne_vide(self):
        """Pas de crash si agent absent, retourne chaîne vide."""
        with patch("core.ci_pipeline.orchestrator") as mock_orch:
            mock_orch.agents = {}
            assert _recall_failures("x.py", "code") == ""
            assert _recall_successes("x.py", "code") == ""


class TestMemoryIntegrationPipeline:

    @pytest.fixture
    def setup_agents(self):
        coder = AsyncMock()
        coder.has_memory = True
        coder.remember = MagicMock()
        coder.recall = MagicMock(return_value="")
        architect = AsyncMock()
        strategist = MagicMock()
        strategist.has_memory = True
        strategist.remember = MagicMock()
        strategist.recall = MagicMock(return_value="")
        return {"coder": coder, "architect": architect, "strategist": strategist}, coder, architect, strategist

    @pytest.mark.asyncio
    async def test_echec_tests_memorise_failure(self, tmp_path, setup_agents):
        """Quand pytest échoue, l'échec est archivé dans ci_failures."""
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "bad.py"
        src.write_text("def broken(): pass\n")
        (tmp_path / "bad.py.bak").write_text("def original(): pass\n")

        coder.generate_content = AsyncMock(return_value="```python\ndef test_x(): assert True\n```")
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(False, "FAILED")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)):
            mock_orch.agents = agents
            await run_pipeline("bad.py", str(src))

        # L'échec doit être mémorisé
        coder.remember.assert_called_once()
        call_text = coder.remember.call_args[0][0]
        assert "ÉCHEC" in call_text
        assert "test_execution" in call_text

    @pytest.mark.asyncio
    async def test_succes_complet_memorise_riche(self, tmp_path, setup_agents):
        """Quand le pipeline réussit, le succès est archivé avec code+tests+verdict."""
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "good.py"
        src.write_text("def hello(): return 42\n")

        coder.generate_content = AsyncMock(return_value="```python\ndef test_hello(): assert True\n```")
        architect.generate_content = AsyncMock(return_value="VALIDÉ - Excellent code")
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys"):
            mock_orch.agents = agents
            await run_pipeline("good.py", str(src))

        # Le succès doit être mémorisé avec contenu riche
        strategist.remember.assert_called_once()
        call_text = strategist.remember.call_args[0][0]
        assert "SUCCÈS" in call_text
        assert "def hello()" in call_text
        assert "def test_hello()" in call_text
        assert "VALIDÉ" in call_text

    @pytest.mark.asyncio
    async def test_recall_injecte_dans_prompt_coder(self, tmp_path, setup_agents):
        """Les souvenirs d'échecs passés sont injectés dans le prompt du Coder."""
        agents, coder, architect, strategist = setup_agents

        src = tmp_path / "module.py"
        src.write_text("def func(): pass\n")

        # Simuler un souvenir d'échec passé
        coder.recall = MagicMock(return_value="[CI/CD ÉCHEC] module.py — ImportError: no module named X")
        coder.generate_content = AsyncMock(return_value="```python\ndef test_func(): assert True\n```")
        architect.generate_content = AsyncMock(return_value="VALIDÉ")
        auto_dir = tmp_path / "auto"

        with patch("core.ci_pipeline.orchestrator") as mock_orch, \
             patch("core.ci_pipeline._run_pytest", new_callable=AsyncMock, return_value=(True, "1 passed")), \
             patch("core.ci_pipeline.AUTO_TESTS_DIR", str(auto_dir)), \
             patch("core.ci_pipeline.sys"):
            mock_orch.agents = agents
            await run_pipeline("module.py", str(src))

        # Vérifier que le prompt envoyé au coder contient l'avertissement
        prompt_sent = coder.generate_content.call_args[0][0]
        assert "ATTENTION" in prompt_sent
        assert "Échecs CI/CD passés" in prompt_sent
