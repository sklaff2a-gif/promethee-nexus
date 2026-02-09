import os
import shutil
import tempfile
import pytest
from core.vector_store import ChromaMemoryManager


@pytest.fixture(autouse=True)
def isolate_chroma(monkeypatch, tmp_path):
    """Force tous les tests à utiliser un répertoire temporaire pour éviter de toucher la vraie mémoire."""
    monkeypatch.chdir(tmp_path)
    ChromaMemoryManager.reset_all()
    yield
    ChromaMemoryManager.reset_all()


class TestProjectIsolation:
    """2 instances avec project_id différents → mémoires isolées."""

    def test_two_projects_are_isolated(self):
        mgr_a = ChromaMemoryManager.get_instance("projet_a")
        mgr_b = ChromaMemoryManager.get_instance("projet_b")

        mgr_a.add_documents(["souvenir projet A"], [{"agent": "coder"}], ["id_a"], "collective_wisdom")
        mgr_b.add_documents(["souvenir projet B"], [{"agent": "coder"}], ["id_b"], "collective_wisdom")

        res_a = mgr_a.query_documents(["souvenir"], n_results=5, collection_name="collective_wisdom")
        res_b = mgr_b.query_documents(["souvenir"], n_results=5, collection_name="collective_wisdom")

        docs_a = [d for sublist in res_a["documents"] for d in sublist]
        docs_b = [d for sublist in res_b["documents"] for d in sublist]

        assert "souvenir projet A" in docs_a
        assert "souvenir projet B" not in docs_a
        assert "souvenir projet B" in docs_b
        assert "souvenir projet A" not in docs_b

    def test_different_db_paths(self):
        mgr_a = ChromaMemoryManager.get_instance("alpha")
        mgr_b = ChromaMemoryManager.get_instance("beta")

        assert "alpha" in mgr_a.db_path
        assert "beta" in mgr_b.db_path
        assert mgr_a.db_path != mgr_b.db_path


class TestDynamicCollections:
    """Collection inconnue créée à la demande (plus de fallback silencieux)."""

    def test_unknown_collection_created_on_add(self):
        mgr = ChromaMemoryManager.get_instance("test_dynamic")
        assert "ci_failures" not in mgr.collections

        result = mgr.add_documents(["erreur CI"], [{"type": "failure"}], ["ci_1"], "ci_failures")
        assert result is True
        assert "ci_failures" in mgr.collections

    def test_unknown_collection_created_on_query(self):
        mgr = ChromaMemoryManager.get_instance("test_dynamic_q")
        assert "ci_successes" not in mgr.collections

        result = mgr.query_documents(["test"], n_results=1, collection_name="ci_successes")
        assert result is not None
        assert "ci_successes" in mgr.collections

    def test_dynamic_collection_stores_and_retrieves(self):
        mgr = ChromaMemoryManager.get_instance("test_dynamic_sr")
        mgr.add_documents(["build OK"], [{"type": "success"}], ["ci_s1"], "ci_successes")

        res = mgr.query_documents(["build"], n_results=1, collection_name="ci_successes")
        docs = [d for sublist in res["documents"] for d in sublist]
        assert "build OK" in docs


class TestDefaultProject:
    """Sans PROJECT_ID → utilise 'default'."""

    def test_default_project_id(self):
        mgr = ChromaMemoryManager.get_instance()
        assert mgr.project_id == "default"
        assert "default" in mgr.db_path

    def test_explicit_default_same_instance(self):
        mgr1 = ChromaMemoryManager.get_instance()
        mgr2 = ChromaMemoryManager.get_instance("default")
        assert mgr1 is mgr2


class TestMigration:
    """Ancienne structure ./memory/chroma_db/ → migrée vers ./memory/default/chroma_db/."""

    def test_migration_moves_old_to_new(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ChromaMemoryManager.reset_all()

        # Créer l'ancienne structure
        old_dir = tmp_path / "memory" / "chroma_db"
        old_dir.mkdir(parents=True)
        marker = old_dir / "marker.txt"
        marker.write_text("old_data")

        mgr = ChromaMemoryManager.get_instance("default")

        new_marker = tmp_path / "memory" / "default" / "chroma_db" / "marker.txt"
        assert new_marker.exists()
        assert new_marker.read_text() == "old_data"
        assert not old_dir.exists()

    def test_no_migration_if_new_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ChromaMemoryManager.reset_all()

        old_dir = tmp_path / "memory" / "chroma_db"
        old_dir.mkdir(parents=True)
        (old_dir / "old.txt").write_text("old")

        new_dir = tmp_path / "memory" / "default" / "chroma_db"
        new_dir.mkdir(parents=True)
        (new_dir / "new.txt").write_text("new")

        mgr = ChromaMemoryManager.get_instance("default")

        # L'ancien répertoire n'a pas été touché (migration ignorée)
        assert (old_dir / "old.txt").exists()
        assert (new_dir / "new.txt").exists()


class TestResetAll:
    """reset_all() nettoie toutes les instances."""

    def test_reset_clears_all_instances(self):
        ChromaMemoryManager.get_instance("p1")
        ChromaMemoryManager.get_instance("p2")
        assert len(ChromaMemoryManager._instances) == 2

        ChromaMemoryManager.reset_all()
        assert len(ChromaMemoryManager._instances) == 0

    def test_new_instance_after_reset(self):
        mgr1 = ChromaMemoryManager.get_instance("proj")
        ChromaMemoryManager.reset_all()
        mgr2 = ChromaMemoryManager.get_instance("proj")
        assert mgr1 is not mgr2
