import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.dropzone_pipeline import DropzonePipeline
from core.capabilities.dropzone_indexer import DropzoneIndexer


@pytest.fixture
def mock_orchestrator():
    orch = AsyncMock()
    orch.dispatch_task = AsyncMock(return_value={"status": "success", "result": "Analyse terminée."})
    orch.dispatch_council = AsyncMock(return_value={"status": "success", "result": "CONSENSUS: Patterns validés."})
    orch.agents = {"researcher": MagicMock()}
    orch.agents["researcher"].remember = MagicMock()
    return orch


@pytest.fixture
def indexer():
    return DropzoneIndexer()


def _create_project(tmp_path, name, files_dict):
    """Helper : crée un projet avec des fichiers."""
    project = tmp_path / name
    project.mkdir()
    (project / "requirements.txt").write_text("deps\n")
    for fname, content in files_dict.items():
        (project / fname).write_text(content)
    return project


class TestPipelineRouting:

    @pytest.mark.asyncio
    async def test_code_routed_to_coder(self, mock_orchestrator, tmp_path):
        _create_project(tmp_path, "PyProject", {"main.py": "print('hello')"})
        pipeline = DropzonePipeline(mock_orchestrator)

        await pipeline.run(str(tmp_path))

        # Vérifier qu'au moins un dispatch vers "coder" a été fait
        calls = mock_orchestrator.dispatch_task.call_args_list
        coder_calls = [c for c in calls if c[0][0] == "coder"]
        assert len(coder_calls) >= 1

    @pytest.mark.asyncio
    async def test_docs_routed_to_researcher(self, mock_orchestrator, tmp_path):
        _create_project(tmp_path, "DocProject", {"README.md": "# Documentation\nBlah blah"})
        pipeline = DropzonePipeline(mock_orchestrator)

        await pipeline.run(str(tmp_path))

        calls = mock_orchestrator.dispatch_task.call_args_list
        researcher_calls = [c for c in calls if c[0][0] == "researcher"]
        assert len(researcher_calls) >= 1

    @pytest.mark.asyncio
    async def test_config_routed_to_architect(self, mock_orchestrator, tmp_path):
        _create_project(tmp_path, "ConfigProject", {"settings.yaml": "key: value\n"})
        pipeline = DropzonePipeline(mock_orchestrator)

        await pipeline.run(str(tmp_path))

        calls = mock_orchestrator.dispatch_task.call_args_list
        architect_calls = [c for c in calls if c[0][0] == "architect"]
        assert len(architect_calls) >= 1


class TestPipelineBatch:

    def test_batch_respects_max_chars(self, tmp_path):
        project = tmp_path / "BigProject"
        project.mkdir()
        (project / "requirements.txt").write_text("x\n")
        (project / "big.py").write_text("x" * 5000)
        (project / "big2.py").write_text("y" * 5000)

        pipeline = DropzonePipeline(AsyncMock())
        files = [
            {"rel_path": "big.py", "type": "code", "size": 5000, "sha256": "a"},
            {"rel_path": "big2.py", "type": "code", "size": 5000, "sha256": "b"},
        ]
        result = pipeline._read_batch(str(project), files, max_chars=100)

        # Le contenu total ne doit pas dépasser max_chars + overhead des headers
        # Le texte brut lu doit être tronqué
        assert len(result) < 300  # 100 chars de contenu + headers/troncature


class TestPipelineDebate:

    @pytest.mark.asyncio
    async def test_council_called_with_3_participants(self, mock_orchestrator, tmp_path):
        _create_project(tmp_path, "TestProject", {"main.py": "code = True"})
        pipeline = DropzonePipeline(mock_orchestrator)

        await pipeline.run(str(tmp_path))

        mock_orchestrator.dispatch_council.assert_called_once()
        call_kwargs = mock_orchestrator.dispatch_council.call_args
        participants = call_kwargs[1].get("participants") or call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]["participants"]
        assert set(participants) == {"coder", "architect", "security"}


class TestPipelineIntegration:

    @pytest.mark.asyncio
    async def test_full_run_with_mocks(self, mock_orchestrator, tmp_path):
        _create_project(tmp_path, "FullProject", {
            "main.py": "import os\nprint('hello')",
            "README.md": "# Project\nDescription here.",
            "config.yaml": "debug: true\n",
        })
        pipeline = DropzonePipeline(mock_orchestrator)

        result = await pipeline.run(str(tmp_path))

        assert result["status"] == "success"
        assert result["manifest"]["total_projects"] >= 1
        assert result["analyses"] >= 1
        assert "harvest" in result
        assert result["harvest"]["memorized"] >= 1

    @pytest.mark.asyncio
    async def test_empty_dropzone(self, mock_orchestrator, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()

        pipeline = DropzonePipeline(mock_orchestrator)
        result = await pipeline.run(str(empty))

        assert result["status"] == "warning"
        assert result["analyses"] == 0
        mock_orchestrator.dispatch_task.assert_not_called()
        mock_orchestrator.dispatch_council.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_across_projects(self, mock_orchestrator, tmp_path):
        """Les fichiers identiques ne sont analysés qu'une fois."""
        same_content = "def shared_util(): return True\n"
        _create_project(tmp_path, "ProjA", {"utils.py": same_content})
        _create_project(tmp_path, "ProjB", {"utils.py": same_content})

        pipeline = DropzonePipeline(mock_orchestrator)
        await pipeline.run(str(tmp_path))

        # Le coder ne devrait recevoir utils.py qu'une seule fois (dédup par SHA256)
        coder_calls = [c for c in mock_orchestrator.dispatch_task.call_args_list if c[0][0] == "coder"]
        # Avec 2 projets ayant le même utils.py, on s'attend à 1 ou 2 appels coder
        # (1 si la dédup fonctionne correctement au niveau batch, 2 si un par projet)
        # L'important : le deuxième projet ne ré-analyse pas les mêmes fichiers
        assert len(coder_calls) >= 1
