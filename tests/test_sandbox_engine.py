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
    monkeypatch.setattr(mod, "SANDBOX_CACHE_FILE", str(tmp_path / "sandbox_cache.json"))

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
            "pre_validation_catches", "graph_targeted_runs",
            "cache_hits",
            "prediction_hits",
        }
        assert set(stats.keys()) == expected_keys


# =============================================================
# PRE-VALIDATION AST
# =============================================================

class TestPreValidation:
    def test_syntax_error_caught(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine.apply_change("core/bad.py", "def broken(\n")
        result = engine._pre_validate("core/bad.py")
        assert result is not None
        assert result.success is False
        assert "SyntaxError" in result.output

    def test_missing_import_caught(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine.apply_change(
            "core/bad.py",
            "from core.nonexistent_module_xyz import foo\n"
        )
        result = engine._pre_validate("core/bad.py")
        assert result is not None
        assert result.success is False
        assert "introuvable" in result.output

    def test_valid_code_passes(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine.apply_change("core/good.py", "import os\nx = 1\n")
        result = engine._pre_validate("core/good.py")
        assert result is None  # None = validation OK

    def test_no_file_returns_none(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        result = engine._pre_validate("core/nonexistent.py")
        assert result is None

    def test_existing_project_import_passes(self, isolate_sandbox):
        """Import d'un module projet existant ne declenche pas d'erreur."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        # base_agent.py existe dans le fake prod
        engine.apply_change(
            "core/uses_base.py",
            "from core.base_agent import BaseAgent\n"
        )
        result = engine._pre_validate("core/uses_base.py")
        assert result is None

    def test_stdlib_import_not_checked(self, isolate_sandbox):
        """Les imports stdlib (collections, json) ne sont pas verifies."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine.apply_change(
            "core/stdlib.py",
            "from collections import deque\nimport json\n"
        )
        result = engine._pre_validate("core/stdlib.py")
        assert result is None

    def test_pre_validation_stats(self, isolate_sandbox):
        engine = SandboxEngine()
        engine.create_or_refresh()
        assert engine._stats["pre_validation_catches"] == 0


# =============================================================
# SELECT TESTS (graphe de dependances)
# =============================================================

class TestSelectTests:
    def test_fallback_to_derive(self, isolate_sandbox):
        """Sans graphe, fallback sur _derive_test_file."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        # Mock le graphe pour retourner liste vide
        from unittest.mock import MagicMock
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = []
        engine._test_graph = mock_graph
        # test_base_agent.py n'existe pas dans le sandbox → liste vide
        result = engine._select_tests("core/some_module.py")
        assert result is not None

    def test_hub_returns_none(self, isolate_sandbox):
        """Module hub retourne None (run complet)."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        from unittest.mock import MagicMock
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = None
        engine._test_graph = mock_graph
        result = engine._select_tests("core/base_agent.py")
        assert result is None

    def test_graph_results_used(self, isolate_sandbox):
        """Tests trouves par le graphe sont utilises s'ils existent."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        from unittest.mock import MagicMock
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = ["tests/test_base.py"]
        engine._test_graph = mock_graph
        result = engine._select_tests("core/base_agent.py")
        # test_base.py existe dans le sandbox
        assert result == ["tests/test_base.py"]


# =============================================================
# COPYTREE CIBLE
# =============================================================

class TestCopytreeCible:
    def test_excludes_promethee_sandbox(self, isolate_sandbox):
        """Le dossier PROMETHEE_sandbox est exclu du copytree."""
        prod_dir = isolate_sandbox["prod_dir"]
        os.makedirs(os.path.join(prod_dir, "PROMETHEE_sandbox"), exist_ok=True)
        with open(os.path.join(prod_dir, "PROMETHEE_sandbox", "dummy.py"), "w") as f:
            f.write("# dummy")
        engine = SandboxEngine()
        engine._last_refresh = 0.0
        engine.create_or_refresh()
        sandbox_dir = isolate_sandbox["sandbox_dir"]
        assert not os.path.exists(os.path.join(sandbox_dir, "PROMETHEE_sandbox"))

    def test_excludes_datasets(self, isolate_sandbox):
        prod_dir = isolate_sandbox["prod_dir"]
        os.makedirs(os.path.join(prod_dir, "datasets"), exist_ok=True)
        engine = SandboxEngine()
        engine._last_refresh = 0.0
        engine.create_or_refresh()
        sandbox_dir = isolate_sandbox["sandbox_dir"]
        assert not os.path.exists(os.path.join(sandbox_dir, "datasets"))

    def test_excludes_pyc(self, isolate_sandbox):
        """Les fichiers .pyc sont exclus."""
        prod_dir = isolate_sandbox["prod_dir"]
        with open(os.path.join(prod_dir, "core", "compiled.pyc"), "w") as f:
            f.write("")
        engine = SandboxEngine()
        engine.create_or_refresh()
        sandbox_dir = isolate_sandbox["sandbox_dir"]
        assert not os.path.isfile(os.path.join(sandbox_dir, "core", "compiled.pyc"))


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


# =============================================================
# CACHE MD5 (Niveau 3)
# =============================================================

class TestCacheMD5:
    def test_compute_pair_hash(self, isolate_sandbox):
        """Le hash combine source + tests."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        h = engine._compute_pair_hash("core/base_agent.py", ["tests/test_base.py"])
        assert h is not None
        # Format: md5_source:md5_test
        parts = h.split(":")
        assert len(parts) == 2
        assert len(parts[0]) == 32  # MD5 hex
        assert len(parts[1]) == 32

    def test_hash_changes_on_content_change(self, isolate_sandbox):
        """Si le fichier source change, le hash change."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        h1 = engine._compute_pair_hash("core/base_agent.py", ["tests/test_base.py"])
        # Modifier le source
        engine.apply_change("core/base_agent.py", "# v2\nclass BaseAgent:\n    x = 1\n")
        h2 = engine._compute_pair_hash("core/base_agent.py", ["tests/test_base.py"])
        assert h1 != h2

    def test_hash_none_if_missing_file(self, isolate_sandbox):
        """Hash None si un fichier n'existe pas."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        h = engine._compute_pair_hash("core/nonexistent.py", ["tests/test_base.py"])
        assert h is None

    def test_hash_deterministic(self, isolate_sandbox):
        """Meme fichiers → meme hash."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        h1 = engine._compute_pair_hash("core/base_agent.py", ["tests/test_base.py"])
        h2 = engine._compute_pair_hash("core/base_agent.py", ["tests/test_base.py"])
        assert h1 == h2

    def test_cache_lookup_miss(self, isolate_sandbox):
        """Cache vide → None."""
        engine = SandboxEngine()
        result = engine._cache_lookup("abc123:def456")
        assert result is None

    def test_cache_lookup_hit_success(self, isolate_sandbox):
        """Cache hit succes → retourne SandboxResult."""
        engine = SandboxEngine()
        engine._last_full_run = time.time()  # filet de securite OK
        engine._cache["hash1"] = {
            "success": True,
            "tests_passed": 5,
            "timestamp": time.time(),
        }
        result = engine._cache_lookup("hash1")
        assert result is not None
        assert result.success is True
        assert result.tests_passed == 5
        assert "CACHE HIT" in result.output
        assert engine._stats["cache_hits"] == 1

    def test_cache_lookup_ignores_failures(self, isolate_sandbox):
        """Cache hit echec → None (relancer le test)."""
        engine = SandboxEngine()
        engine._last_full_run = time.time()
        engine._cache["hash_fail"] = {
            "success": False,
            "tests_passed": 0,
            "timestamp": time.time(),
        }
        result = engine._cache_lookup("hash_fail")
        assert result is None

    def test_cache_bypass_after_6h(self, isolate_sandbox):
        """Cache bypasse si aucun run complet depuis 6h."""
        engine = SandboxEngine()
        engine._last_full_run = time.time() - mod.CACHE_FULL_RUN_INTERVAL - 1
        engine._cache["hash1"] = {
            "success": True,
            "tests_passed": 5,
            "timestamp": time.time(),
        }
        result = engine._cache_lookup("hash1")
        assert result is None  # bypasse malgre le hit

    def test_cache_update_stores_result(self, isolate_sandbox):
        """_cache_update stocke le resultat et persiste."""
        engine = SandboxEngine()
        result = SandboxResult(
            success=True, tests_passed=10, tests_failed=0,
            tests_errors=0, output="ok", duration_seconds=1.0,
        )
        engine._cache_update("hash_new", result)
        assert "hash_new" in engine._cache
        assert engine._cache["hash_new"]["success"] is True
        assert engine._cache["hash_new"]["tests_passed"] == 10
        # Fichier persiste
        import json
        with open(mod.SANDBOX_CACHE_FILE) as f:
            data = json.load(f)
        assert "hash_new" in data

    def test_cache_load_evicts_old(self, isolate_sandbox, tmp_path):
        """Le chargement evince les entrees > 24h."""
        import json
        cache_file = str(tmp_path / "sandbox_cache.json")
        old_data = {
            "old_hash": {
                "success": True,
                "tests_passed": 1,
                "timestamp": time.time() - mod.CACHE_ENTRY_TTL - 100,
            },
            "fresh_hash": {
                "success": True,
                "tests_passed": 2,
                "timestamp": time.time(),
            },
            "_last_full_run": time.time(),
        }
        with open(cache_file, "w") as f:
            json.dump(old_data, f)
        # Recharger
        engine = SandboxEngine()
        engine._load_cache()
        assert "old_hash" not in engine._cache
        assert "fresh_hash" in engine._cache

    def test_cache_load_missing_file(self, isolate_sandbox):
        """Fichier cache manquant → cache vide, pas de crash."""
        engine = SandboxEngine()
        engine._cache = {"should_be_cleared": True}
        engine._load_cache()
        assert engine._cache == {}

    @pytest.mark.asyncio
    async def test_run_tests_cache_hit_skips_pytest(self, isolate_sandbox, monkeypatch):
        """Un cache hit succes skip completement pytest."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine._last_full_run = time.time()

        # Injecter un hash connu dans le cache
        h = engine._compute_pair_hash("core/base_agent.py", ["tests/test_base.py"])
        assert h is not None
        engine._cache[h] = {
            "success": True,
            "tests_passed": 42,
            "timestamp": time.time(),
        }

        # Mock le graphe pour retourner test_base.py
        from unittest.mock import MagicMock
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = ["tests/test_base.py"]
        engine._test_graph = mock_graph

        # Mock subprocess — NE DOIT PAS etre appele
        call_count = 0
        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (b"1 passed", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        result = await engine.run_tests(target_file="core/base_agent.py")
        assert result.success is True
        assert "CACHE HIT" in result.output
        assert call_count == 0  # pytest PAS appele
        assert engine._stats["cache_hits"] == 1

    @pytest.mark.asyncio
    async def test_run_tests_updates_cache_after_run(self, isolate_sandbox, monkeypatch):
        """Apres un run reel, le cache est mis a jour."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine._last_full_run = time.time()

        # Mock le graphe
        from unittest.mock import MagicMock
        mock_graph = MagicMock()
        mock_graph.get_relevant_tests.return_value = ["tests/test_base.py"]
        engine._test_graph = mock_graph

        # Mock subprocess
        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (b"3 passed in 0.5s", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        result = await engine.run_tests(target_file="core/base_agent.py")
        assert result.success is True
        # Le cache doit contenir une entree
        h = engine._compute_pair_hash("core/base_agent.py", ["tests/test_base.py"])
        assert h in engine._cache
        assert engine._cache[h]["success"] is True
        assert engine._cache[h]["tests_passed"] == 3

    @pytest.mark.asyncio
    async def test_full_run_updates_last_full_run(self, isolate_sandbox, monkeypatch):
        """Un run complet (sans target_file) met a jour _last_full_run."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        assert engine._last_full_run == 0.0

        async def mock_exec(*args, **kwargs):
            class FakeProc:
                returncode = 0
                async def communicate(self):
                    return (b"100 passed in 5.0s", None)
                def kill(self):
                    pass
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
        await engine.run_tests()  # pas de target_file = run complet
        assert engine._last_full_run > 0.0

    def test_multiple_test_files_hash(self, isolate_sandbox):
        """Hash avec plusieurs fichiers tests est deterministe et ordonne."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        # Creer un second fichier test
        engine.apply_change("tests/test_extra.py", "def test_extra():\n    assert True\n")
        h1 = engine._compute_pair_hash(
            "core/base_agent.py",
            ["tests/test_base.py", "tests/test_extra.py"]
        )
        # Ordre inverse → meme hash (tri interne)
        h2 = engine._compute_pair_hash(
            "core/base_agent.py",
            ["tests/test_extra.py", "tests/test_base.py"]
        )
        assert h1 == h2
        # Format: md5_source:md5_test1,md5_test2
        parts = h1.split(":")
        assert len(parts) == 2
        assert "," in parts[1]  # 2 hashes separes par virgule


# =============================================================
# PREDICTION SANDBOX (Niveau 5) — 16 tests
# =============================================================

class TestSandboxPrediction:
    """Tests pour la prediction compilee dans le sandbox."""

    def test_sandbox_result_predicted_default_false(self):
        """Par defaut, predicted=False."""
        r = SandboxResult(True, 5, 0, 0, "ok", 1.0)
        assert r.predicted is False

    def test_sandbox_result_predicted_true(self):
        """On peut creer un SandboxResult avec predicted=True."""
        r = SandboxResult(True, 0, 0, 0, "predicted", 0.0, predicted=True)
        assert r.predicted is True

    def test_prediction_hits_stat(self, isolate_sandbox):
        """Le stat prediction_hits est initialise a 0."""
        engine = SandboxEngine()
        assert engine._stats["prediction_hits"] == 0

    def test_last_was_predicted_init(self, isolate_sandbox):
        """_last_was_predicted est False au demarrage."""
        engine = SandboxEngine()
        assert engine._last_was_predicted is False

    def test_promote_blocks_after_prediction(self, isolate_sandbox):
        """promote() refuse si le dernier run etait une prediction."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine._last_was_predicted = True
        result = engine.promote("core/base_agent.py")
        assert result is False

    def test_promote_allows_after_real_run(self, isolate_sandbox):
        """promote() autorise si le dernier run n'etait pas une prediction."""
        engine = SandboxEngine()
        engine.create_or_refresh()
        engine._last_was_predicted = False
        result = engine.promote("core/base_agent.py")
        assert result is True

    def test_extract_change_metadata_agent(self, isolate_sandbox):
        """Extraction agent depuis le chemin Agents/."""
        engine = SandboxEngine()
        agent, _ = engine._extract_change_metadata("Agents/coder_agent.py")
        assert agent == "coder"

    def test_extract_change_metadata_core(self, isolate_sandbox):
        """Les fichiers core/ sont attribues a evolution."""
        engine = SandboxEngine()
        agent, _ = engine._extract_change_metadata("core/base_agent.py")
        assert agent == "evolution"

    def test_extract_change_metadata_type_test(self, isolate_sandbox):
        """Les fichiers test sont detectes."""
        engine = SandboxEngine()
        _, change_type = engine._extract_change_metadata("tests/test_foo.py")
        assert change_type == "test"

    def test_extract_change_metadata_type_config(self, isolate_sandbox):
        """Les fichiers config sont detectes."""
        engine = SandboxEngine()
        _, change_type = engine._extract_change_metadata("config/settings.json")
        assert change_type == "config"

    def test_extract_change_metadata_type_default(self, isolate_sandbox):
        """Le type par defaut est evolution."""
        engine = SandboxEngine()
        _, change_type = engine._extract_change_metadata("core/base_agent.py")
        assert change_type == "evolution"

    def test_try_prediction_no_history(self, isolate_sandbox):
        """Pas de prediction sans historique."""
        engine = SandboxEngine()
        from unittest.mock import patch, MagicMock
        mock_compiler = MagicMock()
        mock_compiler.predict_sandbox_outcome.return_value = None
        with patch("core.sandbox_engine.compiler", mock_compiler, create=True), \
             patch.dict("sys.modules", {"core.neural_compiler": MagicMock(compiler=mock_compiler)}):
            result = engine._try_prediction("core/base_agent.py")
        assert result is None

    def test_try_prediction_success(self, isolate_sandbox):
        """Prediction succes retourne SandboxResult predicted=True."""
        engine = SandboxEngine()
        from unittest.mock import patch, MagicMock

        mock_compiler = MagicMock()
        mock_compiler.predict_sandbox_outcome.return_value = True

        # Patcher l'import dans le module
        import sys
        mock_nc_module = MagicMock()
        mock_nc_module.compiler = mock_compiler
        with patch.dict(sys.modules, {"core.neural_compiler": mock_nc_module}):
            result = engine._try_prediction("core/base_agent.py")

        assert result is not None
        assert result.predicted is True
        assert result.success is True
        assert engine._stats["prediction_hits"] == 1

    def test_record_sandbox_feedback(self, isolate_sandbox):
        """_record_sandbox_feedback appelle le compiler."""
        engine = SandboxEngine()
        from unittest.mock import patch, MagicMock
        import sys

        mock_compiler = MagicMock()
        mock_nc_module = MagicMock()
        mock_nc_module.compiler = mock_compiler
        with patch.dict(sys.modules, {"core.neural_compiler": mock_nc_module}):
            engine._record_sandbox_feedback("core/base_agent.py", True)

        mock_compiler.record_sandbox_outcome.assert_called_once()

    def test_verify_prediction(self, isolate_sandbox):
        """_verify_prediction appelle le compiler."""
        engine = SandboxEngine()
        from unittest.mock import patch, MagicMock
        import sys

        mock_compiler = MagicMock()
        mock_nc_module = MagicMock()
        mock_nc_module.compiler = mock_compiler
        with patch.dict(sys.modules, {"core.neural_compiler": mock_nc_module}):
            engine._verify_prediction("core/base_agent.py", True)

        mock_compiler.verify_sandbox_prediction.assert_called_once()

    def test_extract_grimoire_type(self, isolate_sandbox):
        """Les fichiers grimoire sont detectes."""
        engine = SandboxEngine()
        _, change_type = engine._extract_change_metadata("core/grimoire/recipe.py")
        assert change_type == "grimoire"
