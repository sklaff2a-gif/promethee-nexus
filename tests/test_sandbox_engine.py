# tests/test_sandbox_engine.py — Tests pour le SandboxEngine
import asyncio
import os
import time

import pytest

import core.sandbox_engine as mod
from core.sandbox_engine import SandboxEngine, SandboxResult


@pytest.fixture(autouse=True)
def isolate_sandbox(tmp_path, monkeypatch):
    """Isole le sandbox dans un repertoire temporaire."""
    SandboxEngine.reset_singleton()

    prod_dir = str(tmp_path / "production")
    sandbox_dir = str(tmp_path / "sandbox")
    monkeypatch.setattr(mod, "SANDBOX_DIR", sandbox_dir)
    monkeypatch.setattr(mod, "PROJECT_ROOT", prod_dir)

    # Creer un faux projet production minimal
    os.makedirs(os.path.join(prod_dir, "core"), exist_ok=True)
    os.makedirs(os.path.join(prod_dir, "tests"), exist_ok=True)
    os.makedirs(os.path.join(prod_dir, "memory"), exist_ok=True)
    os.makedirs(os.path.join(prod_dir, "__pycache__"), exist_ok=True)

    # Fichiers sources
    with open(os.path.join(prod_dir, "core", "base_agent.py"), "w") as f:
        f.write("# base_agent\nclass BaseAgent:\n    pass\n")
    with open(os.path.join(prod_dir, "tests", "test_base.py"), "w") as f:
        f.write("def test_ok():\n    assert True\n")
    with open(os.path.join(prod_dir, "config.py"), "w") as f:
        f.write("# config\nVERSION = '1.0'\n")

    # Fichiers exclus
    with open(os.path.join(prod_dir, "memory", "state.json"), "w") as f:
        f.write("{}")
    with open(os.path.join(prod_dir, "__pycache__", "cache.pyc"), "w") as f:
        f.write("")
    with open(os.path.join(prod_dir, "backup.bak"), "w") as f:
        f.write("old")

    yield {"prod_dir": prod_dir, "sandbox_dir": sandbox_dir}


# =============================================================
# SINGLETON
# =============================================================

class TestSingleton:
    def test_identity(self):
        a = SandboxEngine()
        b = SandboxEngine()
        assert a is b

    def test_reset(self):
        a = SandboxEngine()
        SandboxEngine.reset_singleton()
        b = SandboxEngine()
        assert a is not b

    def test_initial_state(self):
        engine = SandboxEngine()
        stats = engine.get_stats()
        assert stats["sandbox_exists"] is False
        assert stats["is_fresh"] is False
        assert stats["total_tests_run"] == 0
        assert stats["total_promotions"] == 0


# =============================================================
# CREATE OR REFRESH
# =============================================================

class TestCreateOrRefresh:
    def test_creates_sandbox(self, isolate_sandbox):
        engine = SandboxEngine()
        result = engine.create_or_refresh()
        assert result is True
        assert os.path.isdir(isolate_sandbox["sandbox_dir"])
        # Le fichier source doit etre present
        assert os.path.isfile(
            os.path.join(isolate_sandbox["sandbox_dir"], "core", "base_agent.py")
        )

    def test_excludes_dirs(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        sandbox_dir = isolate_sandbox["sandbox_dir"]
        # memory et __pycache__ doivent etre exclus
        assert not os.path.exists(os.path.join(sandbox_dir, "memory"))
        assert not os.path.exists(os.path.join(sandbox_dir, "__pycache__"))

    def test_excludes_extensions(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        sandbox_dir = isolate_sandbox["sandbox_dir"]
        # .bak doit etre exclu
        assert not os.path.isfile(os.path.join(sandbox_dir, "backup.bak"))

    def test_updates_timestamp(self, isolate_sandbox):
        engine = SandboxEngine()
        assert engine._last_refresh == 0.0
        engine.create_or_refresh()
        assert engine._last_refresh > 0.0

    def test_skip_if_fresh(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        # Deuxieme appel doit etre skippe (sandbox frais)
        result = engine.create_or_refresh()
        assert result is False


# =============================================================
# APPLY CHANGE
# =============================================================

class TestApplyChange:
    def test_writes_file(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        result = engine.apply_change("core/new_module.py", "# nouveau module\n")
        assert result is True
        target = os.path.join(isolate_sandbox["sandbox_dir"], "core", "new_module.py")
        assert os.path.isfile(target)
        with open(target) as f:
            assert "nouveau module" in f.read()

    def test_creates_parents(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        result = engine.apply_change("deep/nested/dir/file.py", "content")
        assert result is True
        target = os.path.join(isolate_sandbox["sandbox_dir"], "deep", "nested", "dir", "file.py")
        assert os.path.isfile(target)

    def test_fails_if_no_sandbox(self, isolate_sandbox):
        engine = SandboxEngine()
        # Pas de create_or_refresh → sandbox inexistant
        result = engine.apply_change("core/foo.py", "content")
        assert result is False


# =============================================================
# RUN TESTS
# =============================================================

class TestRunTests:
    @pytest.mark.asyncio
    async def test_success(self, isolate_sandbox, monkeypatch):
        engine = SandboxEngine()
        engine.create_or_refresh()

        # Mock subprocess pour simuler un succes pytest
        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (b"2 passed in 0.5s", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        result = await engine.run_tests()
        assert result.success is True
        assert result.tests_passed == 2

    @pytest.mark.asyncio
    async def test_failure(self, isolate_sandbox, monkeypatch):
        engine = SandboxEngine()
        engine.create_or_refresh()

        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 1
                async def communicate(self):
                    return (b"1 passed, 2 failed in 1.0s", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        result = await engine.run_tests()
        assert result.success is False
        assert result.tests_passed == 1
        assert result.tests_failed == 2

    @pytest.mark.asyncio
    async def test_timeout(self, isolate_sandbox, monkeypatch):
        engine = SandboxEngine()
        engine.create_or_refresh()
        monkeypatch.setattr(mod, "TEST_TIMEOUT", 0.1)

        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = None
                async def communicate(self):
                    await asyncio.sleep(10)  # bloque longtemps
                    return (b"", None)
                def kill(self):
                    self.returncode = -9
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        result = await engine.run_tests()
        assert result.success is False
        assert "TIMEOUT" in result.output


# =============================================================
# PROMOTE
# =============================================================

class TestPromote:
    def test_copies_to_production(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        # Modifier un fichier dans le sandbox
        engine.apply_change("core/base_agent.py", "# modified\nclass BaseAgent:\n    pass\n")
        # Promouvoir
        result = engine.promote("core/base_agent.py")
        assert result is True
        # Verifier en production
        prod_file = os.path.join(isolate_sandbox["prod_dir"], "core", "base_agent.py")
        with open(prod_file) as f:
            assert "modified" in f.read()
        # Backup doit exister
        assert os.path.isfile(prod_file + ".bak")
        assert engine._stats["total_promotions"] == 1

    def test_fails_if_missing(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        result = engine.promote("core/nonexistent.py")
        assert result is False


# =============================================================
# DISCARD
# =============================================================

class TestDiscard:
    def test_restores_from_production(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        # Modifier dans le sandbox
        engine.apply_change("core/base_agent.py", "# modified version")
        # Restaurer
        engine.discard("core/base_agent.py")
        # Le sandbox doit avoir la version originale
        sandbox_file = os.path.join(isolate_sandbox["sandbox_dir"], "core", "base_agent.py")
        with open(sandbox_file) as f:
            content = f.read()
        assert "modified" not in content
        assert "BaseAgent" in content
        assert engine._stats["total_discards"] == 1


# =============================================================
# FRESHNESS
# =============================================================

class TestFreshness:
    def test_fresh_after_create(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        assert engine.is_fresh() is True

    def test_stale_after_threshold(self, isolate_sandbox, monkeypatch):
        engine = SandboxEngine()
        engine.create_or_refresh()
        # Simuler le temps ecoule
        engine._last_refresh = time.time() - mod.FRESHNESS_THRESHOLD - 1
        assert engine.is_fresh() is False


# =============================================================
# STATS
# =============================================================

class TestStats:
    def test_structure(self, isolate_sandbox):
        engine = SandboxEngine()
        stats = engine.get_stats()
        expected_keys = {
            "sandbox_exists", "is_fresh", "last_refresh",
            "total_tests_run", "total_passed", "total_failed",
            "total_promotions", "total_discards",
        }
        assert set(stats.keys()) == expected_keys


# =============================================================
# DERIVE TEST FILE
# =============================================================

class TestDeriveTestFile:
    def test_core_module(self):
        engine = SandboxEngine()
        assert engine._derive_test_file("core/foo.py") == "tests/test_foo.py"

    def test_agent_module(self):
        engine = SandboxEngine()
        assert engine._derive_test_file("Agents/bar_agent.py") == "tests/test_bar_agent.py"

    def test_backslash_path(self):
        engine = SandboxEngine()
        assert engine._derive_test_file("core\\foo.py") == "tests/test_foo.py"


# =============================================================
# PARSE PYTEST OUTPUT
# =============================================================

class TestParsePytestOutput:
    def test_all_passed(self):
        engine = SandboxEngine()
        p, f, e = engine._parse_pytest_output("10 passed in 2.5s")
        assert p == 10
        assert f == 0
        assert e == 0

    def test_mixed(self):
        engine = SandboxEngine()
        p, f, e = engine._parse_pytest_output("5 passed, 3 failed, 1 error in 4.0s")
        assert p == 5
        assert f == 3
        assert e == 1

    def test_empty(self):
        engine = SandboxEngine()
        p, f, e = engine._parse_pytest_output("")
        assert p == 0
        assert f == 0
        assert e == 0
