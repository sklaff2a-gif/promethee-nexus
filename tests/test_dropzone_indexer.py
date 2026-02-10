import os
import pytest
from core.capabilities.dropzone_indexer import DropzoneIndexer


@pytest.fixture
def indexer():
    return DropzoneIndexer()


class TestFileClassification:

    def test_python_is_code(self, indexer):
        assert indexer._classify_file("main.py") == "code"

    def test_js_is_code(self, indexer):
        assert indexer._classify_file("app.js") == "code"

    def test_json_is_config(self, indexer):
        assert indexer._classify_file("package.json") == "config"

    def test_yaml_is_config(self, indexer):
        assert indexer._classify_file("config.yaml") == "config"

    def test_md_is_docs(self, indexer):
        assert indexer._classify_file("README.md") == "docs"

    def test_txt_is_docs(self, indexer):
        assert indexer._classify_file("notes.txt") == "docs"

    def test_csv_is_data(self, indexer):
        assert indexer._classify_file("data.csv") == "data"

    def test_unknown_is_other(self, indexer):
        assert indexer._classify_file("file.xyz") == "other"


class TestProjectDetection:

    def test_detect_by_requirements(self, indexer, tmp_path):
        project = tmp_path / "MonProjet"
        project.mkdir()
        (project / "requirements.txt").write_text("flask\n")
        (project / "main.py").write_text("print('hello')")

        projects = indexer.detect_projects(str(tmp_path))
        assert len(projects) == 1
        assert projects[0]["name"] == "MonProjet"
        assert "requirements.txt" in projects[0]["markers"]

    def test_detect_by_package_json(self, indexer, tmp_path):
        project = tmp_path / "FrontApp"
        project.mkdir()
        (project / "package.json").write_text('{"name": "app"}')

        projects = indexer.detect_projects(str(tmp_path))
        assert len(projects) == 1
        assert "package.json" in projects[0]["markers"]

    def test_no_marker_not_project(self, indexer, tmp_path):
        folder = tmp_path / "random"
        folder.mkdir()
        (folder / "notes.txt").write_text("random notes")

        projects = indexer.detect_projects(str(tmp_path))
        # Aucun marqueur de projet trouvé, mais des fichiers existent → fallback racine
        assert len(projects) >= 1

    def test_multiple_projects(self, indexer, tmp_path):
        p1 = tmp_path / "ProjetA"
        p1.mkdir()
        (p1 / "requirements.txt").write_text("requests\n")

        p2 = tmp_path / "ProjetB"
        p2.mkdir()
        (p2 / "package.json").write_text("{}")

        projects = indexer.detect_projects(str(tmp_path))
        names = [p["name"] for p in projects]
        assert "ProjetA" in names
        assert "ProjetB" in names


class TestHashDedup:

    def test_identical_files_same_hash(self, indexer, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        content = "def hello(): pass\n"
        f1.write_text(content)
        f2.write_text(content)

        assert indexer._hash_file(str(f1)) == indexer._hash_file(str(f2))

    def test_different_files_different_hash(self, indexer, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("def hello(): pass\n")
        f2.write_text("def world(): pass\n")

        assert indexer._hash_file(str(f1)) != indexer._hash_file(str(f2))


class TestCatalog:

    def test_catalog_returns_manifest(self, indexer, tmp_path):
        project = tmp_path / "TestProject"
        project.mkdir()
        (project / "requirements.txt").write_text("flask\n")
        (project / "main.py").write_text("print('hi')")
        (project / "README.md").write_text("# Readme")

        manifest = indexer.catalog(str(tmp_path))

        assert "projects" in manifest
        assert "duplicates" in manifest
        assert "summary" in manifest
        assert manifest["summary"]["total_projects"] >= 1
        assert manifest["summary"]["total_files"] >= 3

    def test_catalog_empty_dir(self, indexer, tmp_path):
        empty = tmp_path / "empty_dropzone"
        empty.mkdir()

        manifest = indexer.catalog(str(empty))

        assert manifest["summary"]["total_projects"] == 0
        assert manifest["summary"]["total_files"] == 0
        assert manifest["duplicates"] == []

    def test_catalog_nonexistent_dir(self, indexer, tmp_path):
        manifest = indexer.catalog(str(tmp_path / "inexistant"))

        assert manifest["summary"]["total_projects"] == 0
        assert manifest["summary"]["total_files"] == 0

    def test_duplicates_detected(self, indexer, tmp_path):
        p1 = tmp_path / "ProjetA"
        p1.mkdir()
        (p1 / "requirements.txt").write_text("x\n")
        (p1 / "utils.py").write_text("SAME_CONTENT = True\n")

        p2 = tmp_path / "ProjetB"
        p2.mkdir()
        (p2 / "requirements.txt").write_text("y\n")
        (p2 / "utils.py").write_text("SAME_CONTENT = True\n")

        manifest = indexer.catalog(str(tmp_path))

        assert manifest["summary"]["duplicates"] >= 1
        dup_files = [d["files"] for d in manifest["duplicates"]]
        # Au moins un groupe de doublons doit contenir utils.py des deux projets
        flat = [f for group in dup_files for f in group]
        utils_paths = [f for f in flat if "utils.py" in f]
        assert len(utils_paths) == 2

    def test_processed_dir_ignored(self, indexer, tmp_path):
        project = tmp_path / "Projet"
        project.mkdir()
        (project / "requirements.txt").write_text("x\n")
        (project / "main.py").write_text("code")

        processed = tmp_path / "processed"
        processed.mkdir()
        (processed / "old.py").write_text("archived")

        manifest = indexer.catalog(str(tmp_path))

        all_paths = [f["rel_path"] for p in manifest["projects"] for f in p["files"]]
        assert not any("processed" in p for p in all_paths)


class TestQuickCount:

    def test_quick_count(self, indexer, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.js").write_text("z")

        assert indexer.quick_count(str(tmp_path)) == 3

    def test_quick_count_excludes_processed(self, indexer, tmp_path):
        (tmp_path / "a.py").write_text("x")
        proc = tmp_path / "processed"
        proc.mkdir()
        (proc / "old.py").write_text("archived")

        assert indexer.quick_count(str(tmp_path)) == 1

    def test_quick_count_nonexistent(self, indexer, tmp_path):
        assert indexer.quick_count(str(tmp_path / "nope")) == 0
