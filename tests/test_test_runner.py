# tests/test_test_runner.py — Tests pour le TestRunner unifie
import asyncio
import os
import pytest
from unittest.mock import MagicMock, patch

import core.test_runner as mod
from core.test_runner import (
    parse_pytest_output,
    run_pytest,
    pre_validate_ast,
    select_tests_for_file,
    derive_test_file,
    get_stats,
    reset_stats,
    _detect_alien_imports_ast,
)


@pytest.fixture(autouse=True)
def reset_runner_stats():
    """Reset les stats globales entre chaque test."""
    reset_stats()
    yield
    reset_stats()


# =============================================================
# PARSE PYTEST OUTPUT
# =============================================================

class TestParsePytestOutput:
    def test_all_passed(self):
        p, f, e = parse_pytest_output("10 passed in 2.5s")
        assert p == 10
        assert f == 0
        assert e == 0

    def test_mixed(self):
        p, f, e = parse_pytest_output("5 passed, 3 failed, 1 error in 4.0s")
        assert p == 5
        assert f == 3
        assert e == 1

    def test_empty(self):
        p, f, e = parse_pytest_output("")
        assert p == 0
        assert f == 0
        assert e == 0

    def test_only_failed(self):
        p, f, e = parse_pytest_output("7 failed in 1.0s")
        assert p == 0
        assert f == 7
        assert e == 0

    def test_errors_only(self):
        p, f, e = parse_pytest_output("2 error in 0.5s")
        assert p == 0
        assert f == 0
        assert e == 2


# =============================================================
# RUN PYTEST
# =============================================================

class TestRunPytest:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (b"3 passed in 0.5s", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        success, output, passed, failed, errors = await run_pytest(
            ["tests/test_foo.py", "-v"]
        )
        assert success is True
        assert passed == 3
        assert failed == 0
        assert errors == 0
        assert "3 passed" in output

    @pytest.mark.asyncio
    async def test_failure(self, monkeypatch):
        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 1
                async def communicate(self):
                    return (b"1 passed, 2 failed in 1.0s", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        success, output, passed, failed, errors = await run_pytest(
            ["tests/test_foo.py"]
        )
        assert success is False
        assert passed == 1
        assert failed == 2

    @pytest.mark.asyncio
    async def test_timeout(self, monkeypatch):
        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = None
                async def communicate(self):
                    await asyncio.sleep(10)
                    return (b"", None)
                def kill(self):
                    self.returncode = -9
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        success, output, passed, failed, errors = await run_pytest(
            ["tests/test_foo.py"], timeout=0.1
        )
        assert success is False
        assert "TIMEOUT" in output

    @pytest.mark.asyncio
    async def test_truncate(self, monkeypatch):
        long_output = "x" * 500

        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (long_output.encode(), None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        _, output, _, _, _ = await run_pytest(
            ["tests/test_foo.py"], truncate=100
        )
        assert len(output) < 200
        assert "tronque" in output

    @pytest.mark.asyncio
    async def test_stats_incremented(self, monkeypatch):
        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (b"1 passed", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        await run_pytest(["tests/test_foo.py"])
        stats = get_stats()
        assert stats["total_pytest_calls"] == 1

    @pytest.mark.asyncio
    async def test_file_not_found(self, monkeypatch):
        async def mock_exec(*args, **kwargs):
            raise FileNotFoundError("python not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        success, output, _, _, _ = await run_pytest(["tests/test_foo.py"])
        assert success is False
        assert "non trouve" in output

    @pytest.mark.asyncio
    async def test_no_truncate(self, monkeypatch):
        """truncate=0 desactive la troncature."""
        long_output = "x" * 5000

        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (long_output.encode(), None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        _, output, _, _, _ = await run_pytest(
            ["tests/test_foo.py"], truncate=0
        )
        assert len(output) == 5000


# =============================================================
# PRE-VALIDATE AST
# =============================================================

class TestPreValidateAst:
    def test_valid_code_passes(self):
        result = pre_validate_ast("import os\nx = 1\n")
        assert result is None

    def test_syntax_error_caught(self):
        result = pre_validate_ast("def broken(\n")
        assert result is not None
        assert "SyntaxError" in result

    def test_alien_imports_detected(self):
        result = pre_validate_ast(
            "import django\nfrom flask import Flask\n",
            check_alien_imports=True,
        )
        assert result is not None
        assert "aliens" in result.lower()
        assert "django" in result

    def test_alien_imports_not_checked_by_default(self):
        result = pre_validate_ast("import django\n")
        assert result is None  # pas de check alien par defaut

    def test_project_imports_checked(self, tmp_path):
        # Creer un faux module projet
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        (core_dir / "existing.py").write_text("x = 1\n")

        result = pre_validate_ast(
            "from core.existing import x\n",
            check_project_imports=True,
            search_dirs=[str(tmp_path)],
        )
        assert result is None  # module existe

    def test_missing_project_import_caught(self, tmp_path):
        result = pre_validate_ast(
            "from core.nonexistent_xyz import foo\n",
            check_project_imports=True,
            search_dirs=[str(tmp_path)],
        )
        assert result is not None
        assert "introuvable" in result

    def test_stdlib_not_checked(self, tmp_path):
        """Les imports stdlib ne sont pas verifies."""
        result = pre_validate_ast(
            "from collections import deque\nimport json\n",
            check_project_imports=True,
            search_dirs=[str(tmp_path)],
        )
        assert result is None

    def test_stats_incremented_on_catch(self):
        pre_validate_ast("def broken(\n")
        stats = get_stats()
        assert stats["total_pre_validation_catches"] == 1

    def test_multiple_alien_imports(self):
        result = pre_validate_ast(
            "import tensorflow\nimport torch\nimport pygame\n",
            check_alien_imports=True,
        )
        assert result is not None
        assert "tensorflow" in result


# =============================================================
# DETECT ALIEN IMPORTS AST
# =============================================================

class TestDetectAlienImports:
    def test_clean_code(self):
        import ast
        tree = ast.parse("import os\nimport json\n")
        aliens = _detect_alien_imports_ast(tree)
        assert aliens == []

    def test_alien_import(self):
        import ast
        tree = ast.parse("import django\n")
        aliens = _detect_alien_imports_ast(tree)
        assert "django" in aliens

    def test_alien_from_import(self):
        import ast
        tree = ast.parse("from flask import Flask\n")
        aliens = _detect_alien_imports_ast(tree)
        assert "flask" in aliens

    def test_submodule_alien(self):
        import ast
        tree = ast.parse("from tensorflow.keras import layers\n")
        aliens = _detect_alien_imports_ast(tree)
        assert "tensorflow" in aliens

    def test_dedup_sorted(self):
        import ast
        tree = ast.parse("import django\nimport django.db\nfrom django.http import HttpResponse\n")
        aliens = _detect_alien_imports_ast(tree)
        assert aliens == ["django"]


# =============================================================
# SELECT TESTS FOR FILE
# =============================================================

class TestSelectTestsForFile:
    def test_with_supervised_map(self, tmp_path):
        test_file = tmp_path / "tests" / "test_memory.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_ok(): assert True\n")

        result = select_tests_for_file(
            "core/vector_store.py",
            supervised_map={"core/vector_store.py": ["tests/test_memory.py"]},
            project_root=str(tmp_path),
            verify_existence=True,
            verify_dir=str(tmp_path),
        )
        assert result == ["tests/test_memory.py"]

    def test_graph_priority(self, tmp_path):
        """Le graphe a priorite sur le mapping supervise."""
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = ["tests/test_graph_found.py"]

        result = select_tests_for_file(
            "core/foo.py",
            test_graph=mock_graph,
            supervised_map={"core/foo.py": ["tests/test_supervised.py"]},
        )
        assert result == ["tests/test_graph_found.py"]

    def test_graph_hub_returns_none(self):
        """Module hub retourne None."""
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = None

        result = select_tests_for_file(
            "core/base_agent.py",
            test_graph=mock_graph,
        )
        assert result is None

    def test_derivation_fallback(self, tmp_path):
        """Sans graphe ni mapping, fallback sur derivation."""
        test_file = tmp_path / "tests" / "test_foo.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_ok(): assert True\n")

        result = select_tests_for_file(
            "core/foo.py",
            project_root=str(tmp_path),
        )
        assert result == ["tests/test_foo.py"]

    def test_no_tests_found(self, tmp_path):
        """Aucun test trouve retourne liste vide."""
        result = select_tests_for_file(
            "core/unknown_module.py",
            project_root=str(tmp_path),
        )
        assert result == []

    def test_verify_existence_filters(self, tmp_path):
        """verify_existence filtre les fichiers inexistants."""
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = [
            "tests/test_exists.py",
            "tests/test_missing.py",
        ]

        # Creer seulement un des deux
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_exists.py").write_text("pass\n")

        result = select_tests_for_file(
            "core/foo.py",
            test_graph=mock_graph,
            verify_existence=True,
            verify_dir=str(tmp_path),
        )
        assert result == ["tests/test_exists.py"]

    def test_stats_graph_selection(self):
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = ["tests/test_foo.py"]

        select_tests_for_file("core/foo.py", test_graph=mock_graph)
        stats = get_stats()
        assert stats["total_graph_selections"] == 1

    def test_backslash_normalization(self, tmp_path):
        """Les chemins Windows sont normalises."""
        test_file = tmp_path / "tests" / "test_foo.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("pass\n")

        result = select_tests_for_file(
            "core\\foo.py",
            project_root=str(tmp_path),
        )
        assert result == ["tests/test_foo.py"]


# =============================================================
# DERIVE TEST FILE
# =============================================================

class TestDeriveTestFile:
    def test_core_module(self):
        assert derive_test_file("core/foo.py") == "tests/test_foo.py"

    def test_agent_module(self):
        assert derive_test_file("Agents/bar_agent.py") == "tests/test_bar_agent.py"

    def test_backslash_path(self):
        assert derive_test_file("core\\foo.py") == "tests/test_foo.py"

    def test_nested_path(self):
        assert derive_test_file("core/event_bus/bus.py") == "tests/test_bus.py"


# =============================================================
# STATS
# =============================================================

class TestStats:
    def test_initial_stats(self):
        stats = get_stats()
        assert stats["total_pytest_calls"] == 0
        assert stats["total_pre_validation_catches"] == 0
        assert stats["total_graph_selections"] == 0

    def test_reset_stats(self):
        mod._stats["total_pytest_calls"] = 42
        reset_stats()
        assert get_stats()["total_pytest_calls"] == 0


# =============================================================
# INTEGRATION CI + SANDBOX
# =============================================================

class TestIntegration:
    """Verifie que CI et sandbox utilisent bien le meme test_runner."""

    def test_sandbox_parse_delegates(self):
        """SandboxEngine._parse_pytest_output delegue a test_runner."""
        from core.sandbox_engine import SandboxEngine
        SandboxEngine.reset_singleton()
        engine = SandboxEngine()
        p, f, e = engine._parse_pytest_output("5 passed, 2 failed in 1s")
        assert p == 5
        assert f == 2
        SandboxEngine.reset_singleton()

    def test_sandbox_derive_delegates(self):
        """SandboxEngine._derive_test_file delegue a test_runner."""
        from core.sandbox_engine import SandboxEngine
        SandboxEngine.reset_singleton()
        engine = SandboxEngine()
        result = engine._derive_test_file("core/foo.py")
        assert result == "tests/test_foo.py"
        SandboxEngine.reset_singleton()
