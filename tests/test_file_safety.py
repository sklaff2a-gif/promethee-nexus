"""Tests pour core/file_safety.py — validation canonique anti path traversal."""
import pytest
from core.file_safety import is_safe_target_path


class TestIsSafeTargetPath:
    """Tests de validation canonique des chemins.

    Couverture des vecteurs d'attaque identifiés par l'audit security 17/05 06:49 :
    - path traversal direct et obfusqué
    - paths absolus Unix et Windows
    - paths UNC Windows
    - null-byte injection
    - types invalides
    """

    def test_legitimate_paths_accepted(self):
        """Les paths relatifs vers fichiers du projet sont acceptés."""
        assert is_safe_target_path("core/body_schema.py") is True
        assert is_safe_target_path("Agents/architect_agent.py") is True
        assert is_safe_target_path("tests/test_factory.py") is True
        assert is_safe_target_path("config.py") is True

    def test_path_traversal_rejected(self):
        """Les tentatives de remontée d'arborescence sont rejetées."""
        assert is_safe_target_path("../../etc/passwd") is False
        assert is_safe_target_path("core/../../etc/passwd") is False
        assert is_safe_target_path("..\\..\\etc\\passwd") is False
        assert is_safe_target_path("../../../sensitive.txt") is False

    def test_absolute_unix_rejected(self):
        """Les paths absolus Unix sont rejetés (hors sandbox)."""
        assert is_safe_target_path("/etc/passwd") is False
        assert is_safe_target_path("/tmp/malicious.py") is False
        assert is_safe_target_path("/root/.ssh/id_rsa") is False

    def test_absolute_windows_rejected(self):
        """Les paths absolus Windows (drive letter) sont rejetés."""
        assert is_safe_target_path("C:\\Windows\\System32\\config\\SAM") is False
        assert is_safe_target_path("D:\\sensitive\\data.txt") is False
        assert is_safe_target_path("C:/Windows/win.ini") is False

    def test_unc_path_rejected(self):
        """Les paths UNC Windows sont rejetés (hors sandbox)."""
        assert is_safe_target_path("\\\\?\\C:\\sensitive\\data.txt") is False
        assert is_safe_target_path("\\\\server\\share\\file.py") is False

    def test_null_byte_injection_rejected(self):
        """Les paths avec null-byte (truncation attack) sont rejetés (ValueError capturée)."""
        assert is_safe_target_path("core/safe.py\x00../../etc/passwd") is False
        assert is_safe_target_path("\x00") is False

    def test_empty_and_invalid_types_rejected(self):
        """Les inputs vides ou de mauvais type sont rejetés."""
        assert is_safe_target_path("") is False
        assert is_safe_target_path(None) is False
        assert is_safe_target_path(123) is False
        assert is_safe_target_path([]) is False
        assert is_safe_target_path({"path": "core/x.py"}) is False

    def test_custom_base_path(self):
        """Le paramètre base permet de définir une sandbox custom."""
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "allowed.py").touch()
            assert is_safe_target_path("allowed.py", base=base) is True
            assert is_safe_target_path("../escape.py", base=base) is False
            assert is_safe_target_path("/etc/passwd", base=base) is False
