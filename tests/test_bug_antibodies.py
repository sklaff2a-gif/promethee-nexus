"""Tests pour le systeme immunitaire — bug antibodies."""

import json
import os
import tempfile
import pytest

from core.bug_antibodies import (
    BugAntibodyRegistry, Antibody, Infection,
    antibody_registry, INITIAL_ANTIBODIES,
)


@pytest.fixture(autouse=True)
def reset(tmp_path):
    # Isoler le fichier persistant pour eviter les interferences
    import core.bug_antibodies as mod
    old_file = mod.ANTIBODIES_FILE
    mod.ANTIBODIES_FILE = str(tmp_path / "test_antibodies.json")
    BugAntibodyRegistry.reset_singleton()
    registry = BugAntibodyRegistry()
    yield registry
    BugAntibodyRegistry.reset_singleton()
    mod.ANTIBODIES_FILE = old_file


class TestAntibodyRegistry:

    def test_initial_antibodies_loaded(self, reset):
        reg = reset
        assert len(reg.antibodies) >= 5
        assert "AB-001" in reg.antibodies
        assert "AB-005" in reg.antibodies

    def test_add_antibody(self, reset):
        reg = reset
        ab = Antibody(
            id="AB-099", name="test_pattern",
            description="Test pattern", pattern=r"TODO",
            antibody_type="regex", severity="low",
        )
        reg.add(ab)
        assert "AB-099" in reg.antibodies

    def test_remove_antibody(self, reset):
        reg = reset
        reg.remove("AB-001")
        assert "AB-001" not in reg.antibodies

    def test_disable_antibody(self, reset):
        reg = reset
        reg.disable("AB-001")
        assert reg.antibodies["AB-001"].enabled is False

    def test_get_next_id(self, reset):
        reg = reset
        next_id = reg.get_next_id()
        assert next_id.startswith("AB-")
        num = int(next_id.split("-")[1])
        assert num > 5


class TestScanner:

    def test_scan_file_no_infections(self, reset, tmp_path):
        reg = reset
        test_file = tmp_path / "clean.py"
        test_file.write_text("def hello():\n    return 42\n")
        infections = reg.scan_file(str(test_file))
        assert infections == []

    def test_scan_file_detects_mutable_default(self, reset, tmp_path):
        reg = reset
        test_file = tmp_path / "buggy.py"
        test_file.write_text("def process(items=[]):\n    items.append(1)\n")
        infections = reg.scan_file(str(test_file))
        mutable = [i for i in infections if i.antibody_name == "mutable_default_arg"]
        assert len(mutable) >= 1
        assert mutable[0].line == 1

    def test_scan_file_detects_bare_except(self, reset, tmp_path):
        reg = reset
        test_file = tmp_path / "bare.py"
        test_file.write_text("try:\n    x = 1\nexcept:\n    pass\n")
        infections = reg.scan_file(str(test_file))
        bare = [i for i in infections if i.antibody_name == "bare_except"]
        assert len(bare) >= 1

    def test_scan_file_respects_exclude(self, reset, tmp_path):
        reg = reset
        # AB-003 exclut les fichiers test_
        test_file = tmp_path / "test_something.py"
        test_file.write_text("def process(items=[]):\n    pass\n")
        infections = reg.scan_file(str(test_file))
        mutable = [i for i in infections if i.antibody_name == "mutable_default_arg"]
        assert len(mutable) == 0  # Exclu par le filtre

    def test_scan_file_disabled_antibody_skipped(self, reset, tmp_path):
        reg = reset
        reg.disable("AB-003")
        test_file = tmp_path / "buggy2.py"
        test_file.write_text("def process(items=[]):\n    pass\n")
        infections = reg.scan_file(str(test_file))
        mutable = [i for i in infections if i.antibody_name == "mutable_default_arg"]
        assert len(mutable) == 0

    def test_scan_report_format(self, reset, tmp_path):
        reg = reset
        test_file = tmp_path / "multi.py"
        test_file.write_text("def bad(x=[]):\n    pass\ntry:\n    y=1\nexcept:\n    pass\n")
        # Override scan dirs pour tester
        import core.bug_antibodies as mod
        old_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = str(tmp_path)
        old_dirs = mod.SCAN_DIRS
        mod.SCAN_DIRS = ["."]
        try:
            report = reg.scan_report()
            assert "infection" in report.lower()
        finally:
            mod.PROJECT_ROOT = old_root
            mod.SCAN_DIRS = old_dirs

    def test_scan_report_clean(self, reset, tmp_path):
        reg = reset
        import core.bug_antibodies as mod
        old_root = mod.PROJECT_ROOT
        mod.PROJECT_ROOT = str(tmp_path)
        old_dirs = mod.SCAN_DIRS
        mod.SCAN_DIRS = ["."]
        # Creer un fichier propre
        (tmp_path / "clean.py").write_text("x = 1\n")
        try:
            report = reg.scan_report()
            assert "sain" in report.lower() or "aucune" in report.lower()
        finally:
            mod.PROJECT_ROOT = old_root
            mod.SCAN_DIRS = old_dirs


class TestGetStatus:

    def test_status_format(self, reset):
        reg = reset
        s = reg.get_status()
        assert "total_antibodies" in s
        assert s["total_antibodies"] >= 5
        assert "antibodies" in s

    def test_list_antibodies(self, reset):
        reg = reset
        text = reg.list_antibodies()
        assert "AB-001" in text
        assert "feedback_accumulation" in text


class TestAntibodyDataclass:

    def test_to_dict_roundtrip(self):
        ab = Antibody(
            id="AB-TEST", name="test", description="Test",
            pattern=r"TODO", antibody_type="regex", severity="low",
        )
        d = ab.to_dict()
        ab2 = Antibody.from_dict(d)
        assert ab2.id == ab.id
        assert ab2.pattern == ab.pattern
        assert ab2.severity == ab.severity
