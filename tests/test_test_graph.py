# tests/test_test_graph.py — Tests pour le graphe de dependances AST
import os
import time

import pytest

from core.test_graph import TestGraph


@pytest.fixture
def graph_env(tmp_path):
    """Cree un environnement avec fichiers source et tests fictifs."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    # Modules source (juste pour reference, le graphe parse les tests)
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "thalamus.py").write_text("class Thalamus: pass\n")
    (core_dir / "base_agent.py").write_text("class BaseAgent: pass\n")

    agents_dir = tmp_path / "Agents"
    agents_dir.mkdir()
    (agents_dir / "coder_agent.py").write_text("class CoderAgent: pass\n")

    # Fichiers de tests
    (tests_dir / "test_thalamus.py").write_text(
        "from core.thalamus import Thalamus\n"
        "def test_ok():\n    assert True\n"
    )
    (tests_dir / "test_coder.py").write_text(
        "from Agents.coder_agent import CoderAgent\n"
        "from core.base_agent import BaseAgent\n"
        "def test_ok():\n    assert True\n"
    )
    (tests_dir / "test_multi.py").write_text(
        "from core.thalamus import Thalamus\n"
        "from core.base_agent import BaseAgent\n"
        "def test_ok():\n    assert True\n"
    )
    (tests_dir / "test_import_style.py").write_text(
        "import core.thalamus as thal\n"
        "def test_ok():\n    assert True\n"
    )
    # Fichiers non-test (doivent etre ignores)
    (tests_dir / "conftest.py").write_text("# conftest\n")
    (tests_dir / "not_a_test.py").write_text("# pas un test\n")

    return {
        "tests_dir": str(tests_dir),
        "project_root": str(tmp_path),
    }


# =============================================================
# BUILD
# =============================================================

class TestBuild:
    def test_builds_graph(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        assert g._built is True
        assert len(g._graph) > 0

    def test_finds_from_imports(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        assert "core.thalamus" in g._graph
        assert "Agents.coder_agent" in g._graph

    def test_finds_import_style(self, graph_env):
        """import core.thalamus as thal est aussi detecte."""
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        thalamus_tests = g._graph.get("core.thalamus", set())
        assert "tests/test_import_style.py" in thalamus_tests

    def test_ignores_non_test_files(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        assert "conftest.py" not in g._mtimes
        assert "not_a_test.py" not in g._mtimes

    def test_handles_syntax_error(self, graph_env):
        """Un fichier test avec erreur de syntaxe est ignore sans crash."""
        bad_file = os.path.join(graph_env["tests_dir"], "test_broken.py")
        with open(bad_file, "w") as f:
            f.write("def broken(\n")
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()  # Ne doit pas crasher
        assert g._built is True


# =============================================================
# RELEVANT TESTS
# =============================================================

class TestRelevantTests:
    def test_single_module(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        tests = g.get_relevant_tests("core/thalamus.py")
        assert tests is not None
        assert "tests/test_thalamus.py" in tests
        assert "tests/test_multi.py" in tests
        assert "tests/test_import_style.py" in tests

    def test_agent_module(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        tests = g.get_relevant_tests("Agents/coder_agent.py")
        assert tests is not None
        assert "tests/test_coder.py" in tests

    def test_unknown_module_returns_empty(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        tests = g.get_relevant_tests("core/unknown_module.py")
        assert tests is not None
        assert tests == []

    def test_backslash_path(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        tests = g.get_relevant_tests("core\\thalamus.py")
        assert tests is not None
        assert "tests/test_thalamus.py" in tests

    def test_results_sorted(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        tests = g.get_relevant_tests("core/thalamus.py")
        assert tests == sorted(tests)


# =============================================================
# HUB MODULES
# =============================================================

class TestHubModules:
    def test_base_agent_returns_none(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        result = g.get_relevant_tests("core/base_agent.py")
        assert result is None

    def test_event_bus_returns_none(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        result = g.get_relevant_tests("core/event_bus/bus.py")
        assert result is None

    def test_orchestrator_returns_none(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        result = g.get_relevant_tests("core/orchestrator.py")
        assert result is None

    def test_event_bus_parent_returns_none(self, graph_env):
        """core/event_bus/ est parent du hub core.event_bus.bus → run complet."""
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        result = g.get_relevant_tests("core/event_bus.py")
        assert result is None


# =============================================================
# CACHE MTIME
# =============================================================

class TestMtimeCache:
    def test_skips_unchanged(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        first_mtimes = dict(g._mtimes)
        g.build()
        assert g._mtimes == first_mtimes

    def test_reparses_modified(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()

        # Modifier un fichier test
        test_file = os.path.join(graph_env["tests_dir"], "test_thalamus.py")
        time.sleep(0.05)
        with open(test_file, "w") as f:
            f.write(
                "from core.thalamus import Thalamus\n"
                "from core.base_agent import BaseAgent\n"
                "def test_ok():\n    assert True\n"
            )

        g.build()
        # test_thalamus doit maintenant aussi lier vers core.base_agent
        assert "tests/test_thalamus.py" in g._graph.get("core.base_agent", set())


# =============================================================
# AUTO-BUILD
# =============================================================

class TestAutoBuild:
    def test_auto_builds_on_first_query(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        assert g._built is False
        g.get_relevant_tests("core/thalamus.py")
        assert g._built is True


# =============================================================
# STATS
# =============================================================

class TestStats:
    def test_structure(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        g.build()
        stats = g.get_stats()
        assert "modules_tracked" in stats
        assert "test_files_parsed" in stats
        assert "total_links" in stats
        assert "built" in stats
        assert stats["built"] is True
        assert stats["test_files_parsed"] == 4  # 4 fichiers test_*

    def test_before_build(self, graph_env):
        g = TestGraph(tests_dir=graph_env["tests_dir"],
                      project_root=graph_env["project_root"])
        stats = g.get_stats()
        assert stats["built"] is False
        assert stats["modules_tracked"] == 0


# =============================================================
# CAS LIMITES
# =============================================================

class TestEdgeCases:
    def test_missing_dir(self, tmp_path):
        g = TestGraph(tests_dir=str(tmp_path / "nonexistent"))
        g.build()
        assert g._built is True
        assert len(g._graph) == 0

    def test_empty_dir(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        g = TestGraph(tests_dir=str(tests_dir))
        g.build()
        assert g._built is True
        assert len(g._graph) == 0
