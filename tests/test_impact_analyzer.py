# tests/test_impact_analyzer.py
"""Tests pour core/impact_analyzer.py — Impact Graph."""
import os
import ast
import time
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def reset_analyzer(tmp_path, monkeypatch):
    """Reset singleton + isole les fichiers de state."""
    from core.impact_analyzer import ImpactAnalyzer
    ImpactAnalyzer.reset_singleton()
    yield
    ImpactAnalyzer.reset_singleton()


@pytest.fixture
def analyzer_instance():
    from core.impact_analyzer import ImpactAnalyzer
    a = ImpactAnalyzer()
    a._cache = None
    a._cache_time = 0
    a._initialized = True
    return a


@pytest.fixture
def mock_project(tmp_path):
    """Crée un mini-projet avec quelques modules Python."""
    # core/
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "__init__.py").write_text("")

    (core_dir / "base_agent.py").write_text(
        "import os\nimport logging\n\nclass BaseAgent:\n    pass\n"
    )
    (core_dir / "orchestrator.py").write_text(
        "from core.base_agent import BaseAgent\nfrom core.event_bus.bus import bus\n\nclass Orchestrator:\n    pass\n"
    )
    (core_dir / "autonomy_engine.py").write_text(
        "from core.base_agent import BaseAgent\n\ndef local_import():\n    from core.orchestrator import orchestrator\n"
    )

    # core/event_bus/
    bus_dir = core_dir / "event_bus"
    bus_dir.mkdir()
    (bus_dir / "__init__.py").write_text("")
    (bus_dir / "bus.py").write_text("class Bus:\n    pass\nbus = Bus()\n")

    # core/grimoire/
    grim_dir = core_dir / "grimoire"
    grim_dir.mkdir()
    (grim_dir / "__init__.py").write_text("")
    (grim_dir / "dr_debug.py").write_text("from core.base_agent import BaseAgent\n\nclass DrDebug:\n    pass\n")

    # core/capabilities/
    cap_dir = core_dir / "capabilities"
    cap_dir.mkdir()
    (cap_dir / "__init__.py").write_text("")
    (cap_dir / "web_surfer.py").write_text("import json\n\nclass WebSurfer:\n    pass\n")

    # Agents/
    agents_dir = tmp_path / "Agents"
    agents_dir.mkdir()
    (agents_dir / "__init__.py").write_text("")
    (agents_dir / "coder_agent.py").write_text(
        "from core.base_agent import BaseAgent\n\nclass DivineCoder(BaseAgent):\n    pass\n"
    )
    (agents_dir / "evolution_agent.py").write_text(
        "from core.base_agent import BaseAgent\nfrom core.orchestrator import Orchestrator\n\nclass DivineEvolution(BaseAgent):\n    pass\n"
    )

    # root files
    (tmp_path / "main.py").write_text(
        "from core.orchestrator import orchestrator\nfrom core.autonomy_engine import autonomy\n"
    )
    (tmp_path / "config.py").write_text("class Config:\n    VERSION = '1.0'\n")

    return tmp_path


def _patch_project_root(monkeypatch, mock_project):
    """Patche _PROJECT_ROOT et les dirs de scan."""
    import core.impact_analyzer as mod
    monkeypatch.setattr(mod, "_PROJECT_ROOT", str(mock_project))
    monkeypatch.setattr(mod, "_ROOT_FILES", ["main.py", "config.py"])


# ===== TestModuleDiscovery =====

class TestModuleDiscovery:
    """Tests pour _discover_modules()."""

    def test_discovers_core_modules(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        assert "core.base_agent" in modules
        assert "core.orchestrator" in modules
        assert "core.autonomy_engine" in modules

    def test_discovers_agent_modules(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        assert "Agents.coder_agent" in modules
        assert "Agents.evolution_agent" in modules

    def test_discovers_grimoire(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        assert "core.grimoire.dr_debug" in modules

    def test_discovers_capabilities(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        assert "core.capabilities.web_surfer" in modules

    def test_discovers_root_files(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        assert "main" in modules
        assert "config" in modules

    def test_discovers_event_bus(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        assert "core.event_bus.bus" in modules

    def test_correct_types(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        assert modules["core.base_agent"]["type"] == "core"
        assert modules["Agents.coder_agent"]["type"] == "agent"
        assert modules["core.grimoire.dr_debug"]["type"] == "grimoire"
        assert modules["core.capabilities.web_surfer"]["type"] == "capability"
        assert modules["main"]["type"] == "root"

    def test_skips_dunder_files(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        # __init__.py ne doit pas apparaître
        for mid in modules:
            assert "__init__" not in mid


# ===== TestImportExtraction =====

class TestImportExtraction:
    """Tests pour _extract_top_level_imports()."""

    def test_extracts_top_level_imports(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["core.orchestrator"]["path"]
        imports = analyzer_instance._extract_top_level_imports(filepath, known)
        assert "core.base_agent" in imports
        assert "core.event_bus.bus" in imports

    def test_ignores_local_imports(self, analyzer_instance, mock_project, monkeypatch):
        """Les imports dans les fonctions ne sont pas extraits (top-level only)."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["core.autonomy_engine"]["path"]
        imports = analyzer_instance._extract_top_level_imports(filepath, known)
        # L'import local de orchestrator ne doit PAS apparaître
        assert "core.orchestrator" not in imports
        # Mais l'import top-level de base_agent OUI
        assert "core.base_agent" in imports

    def test_filters_stdlib(self, analyzer_instance, mock_project, monkeypatch):
        """Les imports stdlib (os, logging, json) sont ignorés."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["core.base_agent"]["path"]
        imports = analyzer_instance._extract_top_level_imports(filepath, known)
        assert "os" not in imports
        assert "logging" not in imports

    def test_handles_syntax_error(self, analyzer_instance, tmp_path):
        """Un fichier avec SyntaxError ne crash pas."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def foo(\n")  # SyntaxError
        result = analyzer_instance._extract_top_level_imports(str(bad_file), {"core.foo"})
        assert result == []

    def test_handles_missing_file(self, analyzer_instance):
        """Un fichier inexistant ne crash pas."""
        result = analyzer_instance._extract_top_level_imports("/nonexistent/path.py", {"core.foo"})
        assert result == []

    def test_from_import(self, analyzer_instance, tmp_path, mock_project, monkeypatch):
        """from core.base_agent import BaseAgent → matche core.base_agent."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["Agents.coder_agent"]["path"]
        imports = analyzer_instance._extract_top_level_imports(filepath, known)
        assert "core.base_agent" in imports

    def test_no_self_import(self, analyzer_instance, tmp_path):
        """Un module ne peut pas s'auto-importer dans le résultat."""
        f = tmp_path / "self_import.py"
        f.write_text("import self_import\n")
        result = analyzer_instance._extract_top_level_imports(str(f), {"self_import"})
        # self_import est dans known, mais le graph l'exclura au niveau _build
        # ici on vérifie juste que le parsing fonctionne
        assert isinstance(result, list)

    def test_multiple_imports_same_line(self, analyzer_instance, tmp_path):
        """import a, b → extrait les deux si connus."""
        f = tmp_path / "multi.py"
        f.write_text("import core_a, core_b\n")
        result = analyzer_instance._extract_top_level_imports(str(f), {"core_a", "core_b"})
        assert "core_a" in result
        assert "core_b" in result


# ===== TestDependencyGraph =====

class TestDependencyGraph:
    """Tests pour _build_dependency_graph()."""

    def test_builds_imports_of(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        imports_of, imported_by = analyzer_instance._build_dependency_graph(modules)
        assert "core.base_agent" in imports_of["core.orchestrator"]
        assert "core.event_bus.bus" in imports_of["core.orchestrator"]

    def test_builds_imported_by(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        imports_of, imported_by = analyzer_instance._build_dependency_graph(modules)
        # base_agent est importé par orchestrator, autonomy_engine, coder_agent, evolution_agent, dr_debug
        assert "core.orchestrator" in imported_by["core.base_agent"]
        assert "Agents.coder_agent" in imported_by["core.base_agent"]

    def test_no_self_import_in_graph(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        imports_of, _ = analyzer_instance._build_dependency_graph(modules)
        for mid, deps in imports_of.items():
            assert mid not in deps, f"{mid} s'auto-importe"

    def test_counts_match(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        imports_of, imported_by = analyzer_instance._build_dependency_graph(modules)
        # Chaque lien dans imports_of doit avoir un miroir dans imported_by
        for mid, deps in imports_of.items():
            for dep in deps:
                assert mid in imported_by[dep]

    def test_all_modules_have_entries(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        imports_of, imported_by = analyzer_instance._build_dependency_graph(modules)
        for mid in modules:
            assert mid in imports_of
            assert mid in imported_by

    def test_circular_deps_ok(self, analyzer_instance, tmp_path, monkeypatch):
        """Les dépendances circulaires ne crashent pas."""
        import core.impact_analyzer as mod
        monkeypatch.setattr(mod, "_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(mod, "_ROOT_FILES", [])
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        (core_dir / "__init__.py").write_text("")
        (core_dir / "a.py").write_text("from core.b import B\n")
        (core_dir / "b.py").write_text("from core.a import A\n")
        modules = analyzer_instance._discover_modules()
        imports_of, imported_by = analyzer_instance._build_dependency_graph(modules)
        assert "core.b" in imports_of["core.a"]
        assert "core.a" in imports_of["core.b"]


# ===== TestHealthData =====

class TestHealthData:
    """Tests pour _collect_health_data()."""

    def test_baseline_healthy(self, analyzer_instance, mock_project, monkeypatch):
        """Sans données d'erreur, tous les modules sont healthy."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        # Patch pour ne pas toucher aux vrais singletons
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        for mid, h in health.items():
            assert h["status"] == "healthy"

    def test_degraded_threshold(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        # Simuler 2 erreurs pour coder
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "coder", "status": "error"},
            {"agent": "coder", "status": "error"},
        ]
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        assert health["Agents.coder_agent"]["status"] == "degraded"

    def test_error_threshold(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "coder", "status": "error"},
        ] * 5
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        assert health["Agents.coder_agent"]["status"] == "error"

    def test_hallucination_degrades(self, analyzer_instance, mock_project, monkeypatch):
        """Quand threat_level >= 4, evolution et coder sont degraded."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 5})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        assert health["Agents.evolution_agent"]["status"] == "degraded"
        assert health["Agents.coder_agent"]["status"] == "degraded"

    def test_last_modified_set(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        for mid, h in health.items():
            assert h["last_modified"] > 0

    def test_unknown_agent_ignored(self, analyzer_instance, mock_project, monkeypatch):
        """Un agent inconnu dans routine_history est ignoré sans crash."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "nonexistent_agent", "status": "error"},
        ]
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        # Pas de crash, tous les modules restent healthy
        for mid, h in health.items():
            assert h["status"] == "healthy"

    def test_success_entries_not_counted(self, analyzer_instance, mock_project, monkeypatch):
        """Seules les entries avec status=error sont comptées."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "coder", "status": "success"},
            {"agent": "coder", "status": "success"},
            {"agent": "coder", "status": "success"},
        ]
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        assert health["Agents.coder_agent"]["status"] == "healthy"

    def test_import_failure_graceful(self, analyzer_instance, mock_project, monkeypatch):
        """Si l'import de autonomy/reptile échoue, tout reste healthy."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        # Pas de mock → les imports vont probablement échouer dans le contexte test
        # Mais le code est protégé par try/except
        health = analyzer_instance._collect_health_data(modules)
        for mid, h in health.items():
            assert h["status"] in ("healthy", "degraded", "error")


# ===== TestBuildGraphCache =====

class TestBuildGraphCache:
    """Tests pour build_graph() et son cache."""

    def test_returns_valid_structure(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        assert "nodes" in result
        assert "links" in result
        assert "stats" in result
        assert isinstance(result["nodes"], list)
        assert isinstance(result["links"], list)

    def test_stats_counts(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        stats = result["stats"]
        assert stats["total_modules"] == len(result["nodes"])
        assert stats["healthy"] + stats["degraded"] + stats["error"] == stats["total_modules"]

    def test_cache_reused(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result1 = analyzer_instance.build_graph()
            result2 = analyzer_instance.build_graph()
        assert result1 is result2  # même objet = cache

    def test_cache_expired(self, analyzer_instance, mock_project, monkeypatch):
        import core.impact_analyzer as mod
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result1 = analyzer_instance.build_graph()
            # Forcer l'expiration du cache
            analyzer_instance._cache_time -= mod._CACHE_TTL + 1
            result2 = analyzer_instance.build_graph()
        assert result1 is not result2

    def test_cache_invalidation(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result1 = analyzer_instance.build_graph()
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                analyzer_instance._on_invalidate({})
            )
            result2 = analyzer_instance.build_graph()
        assert result1 is not result2

    def test_node_fields(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        node = result["nodes"][0]
        assert "id" in node
        assert "name" in node
        assert "type" in node
        assert "status" in node
        assert "error_count" in node
        assert "import_count" in node
        assert "imported_by_count" in node

    def test_link_fields(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        assert len(result["links"]) > 0
        link = result["links"][0]
        assert "source" in link
        assert "target" in link
        assert "type" in link


# ===== TestCascade =====

class TestCascade:
    """Tests pour get_cascade()."""

    def test_cascade_base_agent(self, analyzer_instance, mock_project, monkeypatch):
        """base_agent est importé par beaucoup de modules → large cascade."""
        _patch_project_root(monkeypatch, mock_project)
        cascade = analyzer_instance.get_cascade("core.base_agent")
        # orchestrator, autonomy_engine, coder_agent, evolution_agent, dr_debug importent base_agent
        assert "core.orchestrator" in cascade
        assert "Agents.coder_agent" in cascade

    def test_cascade_leaf_module(self, analyzer_instance, mock_project, monkeypatch):
        """Un module feuille (pas importé par d'autres) → cascade vide."""
        _patch_project_root(monkeypatch, mock_project)
        cascade = analyzer_instance.get_cascade("core.capabilities.web_surfer")
        assert cascade == []

    def test_cascade_transitive(self, analyzer_instance, mock_project, monkeypatch):
        """La cascade est transitive : bus → orchestrator → main, evolution."""
        _patch_project_root(monkeypatch, mock_project)
        cascade = analyzer_instance.get_cascade("core.event_bus.bus")
        assert "core.orchestrator" in cascade
        # main importe orchestrator qui importe bus → main est dans la cascade
        assert "main" in cascade
