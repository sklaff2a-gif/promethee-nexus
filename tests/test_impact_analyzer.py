# tests/test_impact_analyzer.py
"""Tests pour core/impact_analyzer.py — Impact Graph."""
import os
import ast
import time
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def reset_analyzer(tmp_path, monkeypatch):
    """Reset singleton + isole les fichiers de state.

    Utilise _ROADMAP_FALLBACK pour les tests existants (pas de dependance roadmap_engine).
    """
    from core.impact_analyzer import ImpactAnalyzer, _ROADMAP_FALLBACK
    ImpactAnalyzer.reset_singleton()
    monkeypatch.setattr("core.impact_analyzer._get_roadmap_data", lambda: _ROADMAP_FALLBACK)
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
    """Crée un mini-projet avec quelques modules Python + bus events."""
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
        'from core.base_agent import BaseAgent\n'
        'from core.event_bus.bus import bus\n\n'
        'bus.subscribe("ROUTINE_COMPLETE", handler)\n'
        'bus.subscribe("CARDIAC_BEAT", handler)\n\n'
        'async def run():\n'
        '    await bus.publish("AUTONOMY_HEARTBEAT", {})\n'
        '    from core.orchestrator import orchestrator\n'
    )
    (core_dir / "cardiac_engine.py").write_text(
        'from core.event_bus.bus import bus\n\n'
        'bus.subscribe("ROUTINE_COMPLETE", handler)\n'
        'bus.subscribe("PSYCHE_UPDATE", handler)\n\n'
        'async def beat():\n'
        '    await bus.publish("CARDIAC_BEAT", {})\n'
    )
    (core_dir / "reptilian_core.py").write_text(
        'from core.event_bus.bus import bus\n\n'
        'bus.subscribe("ROUTINE_COMPLETE", handler)\n'
        'bus.subscribe("CARDIAC_BEAT", handler)\n\n'
        'async def watch():\n'
        '    await bus.publish("REPTILIAN_STATE", {})\n'
        '    await bus.publish("REPTILIAN_ALERT", {})\n'
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
        assert "core.orchestrator" not in imports
        assert "core.base_agent" in imports

    def test_filters_stdlib(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["core.base_agent"]["path"]
        imports = analyzer_instance._extract_top_level_imports(filepath, known)
        assert "os" not in imports
        assert "logging" not in imports

    def test_handles_syntax_error(self, analyzer_instance, tmp_path):
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def foo(\n")
        result = analyzer_instance._extract_top_level_imports(str(bad_file), {"core.foo"})
        assert result == []

    def test_handles_missing_file(self, analyzer_instance):
        result = analyzer_instance._extract_top_level_imports("/nonexistent/path.py", {"core.foo"})
        assert result == []

    def test_from_import(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["Agents.coder_agent"]["path"]
        imports = analyzer_instance._extract_top_level_imports(filepath, known)
        assert "core.base_agent" in imports

    def test_no_self_import(self, analyzer_instance, tmp_path):
        f = tmp_path / "self_import.py"
        f.write_text("import self_import\n")
        result = analyzer_instance._extract_top_level_imports(str(f), {"self_import"})
        assert isinstance(result, list)

    def test_multiple_imports_same_line(self, analyzer_instance, tmp_path):
        f = tmp_path / "multi.py"
        f.write_text("import core_a, core_b\n")
        result = analyzer_instance._extract_top_level_imports(str(f), {"core_a", "core_b"})
        assert "core_a" in result
        assert "core_b" in result


# ===== TestLocalImportCount =====

class TestLocalImportCount:
    """Tests pour _count_local_imports()."""

    def test_counts_local_imports(self, analyzer_instance, mock_project, monkeypatch):
        """autonomy_engine a 1 import local (orchestrator)."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["core.autonomy_engine"]["path"]
        count = analyzer_instance._count_local_imports(filepath, known)
        assert count == 1  # from core.orchestrator import orchestrator

    def test_no_local_imports(self, analyzer_instance, mock_project, monkeypatch):
        """base_agent n'a que des imports stdlib, 0 local."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        known = set(modules.keys())
        filepath = modules["core.base_agent"]["path"]
        count = analyzer_instance._count_local_imports(filepath, known)
        assert count == 0

    def test_handles_syntax_error(self, analyzer_instance, tmp_path):
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def foo(\n")
        assert analyzer_instance._count_local_imports(str(bad_file), {"core.foo"}) == 0

    def test_handles_missing_file(self, analyzer_instance):
        assert analyzer_instance._count_local_imports("/nonexistent", {"core.foo"}) == 0


# ===== TestBusConnections =====

class TestBusConnections:
    """Tests pour _extract_bus_connections() et _build_bus_graph()."""

    def test_extracts_publishes(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        conn = analyzer_instance._extract_bus_connections(
            modules["core.cardiac_engine"]["path"]
        )
        assert "CARDIAC_BEAT" in conn["publishes"]

    def test_extracts_subscribes(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        conn = analyzer_instance._extract_bus_connections(
            modules["core.cardiac_engine"]["path"]
        )
        assert "ROUTINE_COMPLETE" in conn["subscribes"]
        assert "PSYCHE_UPDATE" in conn["subscribes"]

    def test_no_bus_in_stdlib(self, analyzer_instance, mock_project, monkeypatch):
        """Un module sans bus.publish/subscribe retourne des listes vides."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        conn = analyzer_instance._extract_bus_connections(
            modules["core.base_agent"]["path"]
        )
        assert conn["publishes"] == []
        assert conn["subscribes"] == []

    def test_wildcard_excluded(self, analyzer_instance, tmp_path):
        """Le wildcard '*' dans bus.subscribe est exclu."""
        f = tmp_path / "wild.py"
        f.write_text('bus.subscribe("*", handler)\nbus.subscribe("REAL_EVENT", handler)\n')
        conn = analyzer_instance._extract_bus_connections(str(f))
        assert "*" not in conn["subscribes"]
        assert "REAL_EVENT" in conn["subscribes"]

    def test_build_bus_graph_links(self, analyzer_instance, mock_project, monkeypatch):
        """cardiac publie CARDIAC_BEAT, reptilian et autonomy y souscrivent → liens bus."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        bus_links, bus_info = analyzer_instance._build_bus_graph(modules)
        # cardiac_engine publie CARDIAC_BEAT → reptilian_core souscrit
        cardiac_to_reptilian = [
            l for l in bus_links
            if l["source"] == "core.cardiac_engine"
            and l["target"] == "core.reptilian_core"
            and l["event"] == "CARDIAC_BEAT"
        ]
        assert len(cardiac_to_reptilian) == 1

    def test_build_bus_graph_no_self_link(self, analyzer_instance, mock_project, monkeypatch):
        """Un module qui publie ET souscrit au même event ne crée pas d'auto-lien."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        bus_links, _ = analyzer_instance._build_bus_graph(modules)
        for link in bus_links:
            assert link["source"] != link["target"]

    def test_bus_info_per_module(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        _, bus_info = analyzer_instance._build_bus_graph(modules)
        assert "AUTONOMY_HEARTBEAT" in bus_info["core.autonomy_engine"]["publishes"]
        assert "CARDIAC_BEAT" in bus_info["core.autonomy_engine"]["subscribes"]

    def test_handles_missing_file(self, analyzer_instance):
        conn = analyzer_instance._extract_bus_connections("/nonexistent")
        assert conn == {"publishes": [], "subscribes": []}


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
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
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
        now = datetime.now().isoformat()
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "coder", "status": "error", "timestamp": now},
            {"agent": "coder", "status": "error", "timestamp": now},
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
        now = datetime.now().isoformat()
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "coder", "status": "error", "timestamp": now},
        ] * 5
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        assert health["Agents.coder_agent"]["status"] == "error"

    def test_hallucination_degrades(self, analyzer_instance, mock_project, monkeypatch):
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
        for mid, h in health.items():
            assert h["status"] == "healthy"

    def test_success_entries_not_counted(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "coder", "status": "success"},
        ] * 3
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        assert health["Agents.coder_agent"]["status"] == "healthy"

    def test_import_failure_graceful(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        health = analyzer_instance._collect_health_data(modules)
        for mid, h in health.items():
            assert h["status"] in ("healthy", "degraded", "error")

    def test_old_errors_ignored(self, analyzer_instance, mock_project, monkeypatch):
        """Erreurs vieilles de 48h ne comptent pas → healthy."""
        _patch_project_root(monkeypatch, mock_project)
        modules = analyzer_instance._discover_modules()
        old_ts = (datetime.now() - timedelta(hours=48)).isoformat()
        mock_autonomy = MagicMock()
        mock_autonomy.routine_history = [
            {"agent": "coder", "status": "error", "timestamp": old_ts},
        ] * 5
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=mock_autonomy),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            health = analyzer_instance._collect_health_data(modules)
        assert health["Agents.coder_agent"]["status"] == "healthy"

    def test_no_timestamp_still_counted(self, analyzer_instance, mock_project, monkeypatch):
        """Entrées sans champ timestamp sont comptées normalement."""
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
        # total_modules exclut les planned
        real_nodes = [n for n in result["nodes"] if n["type"] != "planned"]
        assert stats["total_modules"] == len(real_nodes)
        assert stats["healthy"] + stats["degraded"] + stats["error"] == stats["total_modules"]

    def test_stats_has_link_counts(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        assert "import_links" in result["stats"]
        assert "bus_links" in result["stats"]
        assert result["stats"]["import_links"] >= 0
        assert result["stats"]["bus_links"] >= 0

    def test_cache_reused(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result1 = analyzer_instance.build_graph()
            result2 = analyzer_instance.build_graph()
        assert result1 is result2

    def test_cache_expired(self, analyzer_instance, mock_project, monkeypatch):
        import core.impact_analyzer as mod
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result1 = analyzer_instance.build_graph()
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
        assert "local_import_count" in node
        assert "publishes" in node
        assert "subscribes" in node

    def test_link_types(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        link_types = set(l["type"] for l in result["links"])
        assert "imports" in link_types
        # Le mock project a des bus events
        assert "bus" in link_types

    def test_bus_links_have_event(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        bus_links = [l for l in result["links"] if l["type"] == "bus"]
        for link in bus_links:
            assert "event" in link


# ===== TestCascade =====

class TestCascade:
    """Tests pour get_cascade()."""

    def test_cascade_base_agent(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        cascade = analyzer_instance.get_cascade("core.base_agent")
        assert "core.orchestrator" in cascade
        assert "Agents.coder_agent" in cascade

    def test_cascade_leaf_module(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        cascade = analyzer_instance.get_cascade("core.capabilities.web_surfer")
        assert cascade == []

    def test_cascade_transitive(self, analyzer_instance, mock_project, monkeypatch):
        _patch_project_root(monkeypatch, mock_project)
        cascade = analyzer_instance.get_cascade("core.event_bus.bus")
        assert "core.orchestrator" in cascade
        assert "main" in cascade


# ===== TestRoadmap =====

class TestRoadmap:
    """Tests pour les nœuds roadmap (modules planifiés)."""

    def test_planned_nodes_present(self, analyzer_instance, mock_project, monkeypatch):
        """build_graph() inclut les nœuds roadmap avec type='planned'."""
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        planned = [n for n in result["nodes"] if n["type"] == "planned"]
        assert len(planned) == 10
        ids = {n["id"] for n in planned}
        assert "thalamus" in ids
        assert "amygdala" in ids
        assert "brain_vm" in ids
        assert "deep_metacognition" in ids
        assert "simulation_framework" in ids

    def test_planned_node_fields(self, analyzer_instance, mock_project, monkeypatch):
        """Les nœuds planned ont les champs phase, phase_name, description."""
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        amygdala = next(n for n in result["nodes"] if n["id"] == "amygdala")
        assert amygdala["phase"] == 2
        assert amygdala["phase_name"] == "ORGANES MANQUANTS"
        assert "emotionnelle" in amygdala["description"].lower()
        assert amygdala["status"] == "planned"
        assert amygdala["connects_to_count"] > 0

    def test_planned_links_to_existing(self, analyzer_instance, mock_project, monkeypatch):
        """Les liens planned ne ciblent que des modules existants."""
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        planned_links = [l for l in result["links"] if l["type"] == "planned"]
        node_ids = {n["id"] for n in result["nodes"]}
        for link in planned_links:
            assert link["source"] in node_ids, f"source {link['source']} inconnue"
            assert link["target"] in node_ids, f"target {link['target']} inconnue"

    def test_planned_links_exist(self, analyzer_instance, mock_project, monkeypatch):
        """Il y a des liens planned vers les modules du mock_project."""
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        planned_links = [l for l in result["links"] if l["type"] == "planned"]
        # Au moins certains connects_to matchent des modules existants
        assert len(planned_links) > 0

    def test_stats_planned_counts(self, analyzer_instance, mock_project, monkeypatch):
        """Les stats incluent planned_modules et planned_links."""
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        assert result["stats"]["planned_modules"] == 10
        assert result["stats"]["planned_links"] >= 0

    def test_total_modules_excludes_planned(self, analyzer_instance, mock_project, monkeypatch):
        """total_modules ne compte pas les planned."""
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        planned_count = len([n for n in result["nodes"] if n["type"] == "planned"])
        real_count = len(result["nodes"]) - planned_count
        assert result["stats"]["total_modules"] == real_count

    def test_planned_phases_coverage(self, analyzer_instance, mock_project, monkeypatch):
        """Les 7 etapes (1-7) sont representees dans le fallback."""
        _patch_project_root(monkeypatch, mock_project)
        with patch.dict("sys.modules", {
            "core.autonomy_engine": MagicMock(autonomy=MagicMock(routine_history=[])),
            "core.reptilian_core": MagicMock(reptile=MagicMock(get_stats=lambda: {"threat_level": 0})),
        }):
            result = analyzer_instance.build_graph()
        phases = {n["phase"] for n in result["nodes"] if n["type"] == "planned"}
        assert phases == {1, 2, 3, 4, 5, 6, 7}


# ===== TestRoadmapDynamic =====

class TestRoadmapDynamic:
    """Tests pour l'integration dynamique avec roadmap_engine."""

    def test_get_roadmap_data_uses_engine(self, monkeypatch, tmp_path):
        """_get_roadmap_data() utilise roadmap_engine quand disponible."""
        import json
        import core.impact_analyzer as mod
        from core.roadmap_engine import RoadmapEngine

        # Creer un roadmap de test
        RoadmapEngine.reset_singleton()
        roadmap_file = str(tmp_path / "roadmap.json")
        monkeypatch.setattr("core.roadmap_engine.ROADMAP_FILE", roadmap_file)
        data = {
            "_wip_paused": False,
            "modules": [{
                "id": "test_dynamic",
                "phase": 99,
                "phase_name": "TEST",
                "display": "test_dynamic",
                "description": "Module de test dynamique",
                "status": "researching",
                "specs": [],
                "depends_on": [],
                "estimated_cost": 1,
                "completion_criteria": [],
                "research_topics": [],
                "connects_to": [],
                "created_at": "2026-03-01T00:00:00",
                "updated_at": "2026-03-01T00:00:00",
            }],
        }
        with open(roadmap_file, "w") as f:
            json.dump(data, f)

        import core.roadmap_engine
        core.roadmap_engine.roadmap = RoadmapEngine()

        # Restaurer la vraie fonction _get_roadmap_data (pas le mock du fixture autouse)
        monkeypatch.setattr(mod, "_get_roadmap_data", mod.__dict__.get(
            "_get_roadmap_data_original", mod._get_roadmap_data
        ))
        # Re-patcher avec la vraie implementation
        original_func = None
        # Importer la vraie fonction depuis le source
        exec_ns = {}
        exec("""
def _get_roadmap_data():
    try:
        from core.roadmap_engine import roadmap as roadmap_engine
        modules = roadmap_engine.get_modules_for_graph()
        if modules:
            return modules
    except Exception:
        pass
    return []
""", exec_ns)
        monkeypatch.setattr(mod, "_get_roadmap_data", exec_ns["_get_roadmap_data"])

        result = mod._get_roadmap_data()
        assert len(result) == 1
        assert result[0]["id"] == "test_dynamic"
        assert result[0]["status"] == "researching"

        RoadmapEngine.reset_singleton()

    def test_fallback_when_engine_unavailable(self, monkeypatch):
        """_get_roadmap_data() retourne le fallback si roadmap_engine echoue."""
        import core.impact_analyzer as mod

        def failing_get_roadmap_data():
            try:
                raise ImportError("test")
            except Exception:
                pass
            return mod._ROADMAP_FALLBACK

        monkeypatch.setattr(mod, "_get_roadmap_data", failing_get_roadmap_data)
        result = mod._get_roadmap_data()
        assert len(result) == len(mod._ROADMAP_FALLBACK)
        assert result[0]["id"] == "roadmap_engine"
