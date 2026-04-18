"""Tests pour core/factuality_verifier.py (Veto epistemique V3.2).

Couvre l'extraction des references (lignes, fonctions), la verification
contre le fichier reel, et le calcul du ratio factuality.
"""
import os

os.environ.setdefault("PROMETHEE_TEST_MODE", "1")

import pytest

from core.factuality_verifier import (
    compute_factuality_score,
    extract_references,
    verify_against_file,
)


class TestExtractReferences:
    def test_line_number_basic(self):
        refs = extract_references("La fonction est a la ligne 42.")
        assert 42 in refs["line_numbers"]

    def test_line_number_with_L_prefix(self):
        refs = extract_references("Voir L26: def foo() et L70.")
        assert 26 in refs["line_numbers"]
        assert 70 in refs["line_numbers"]

    def test_line_number_in_parentheses(self):
        refs = extract_references("La fonction `verify_code_review` (ligne L26) fait X.")
        assert 26 in refs["line_numbers"]

    def test_function_in_backticks(self):
        refs = extract_references("La fonction `verify_code_review` est utilisee.")
        assert "verify_code_review" in refs["function_names"]

    def test_function_in_def(self):
        refs = extract_references("```python\ndef my_helper(x):\n    pass\n```")
        assert "my_helper" in refs["function_names"]

    def test_ignore_builtins(self):
        refs = extract_references("J utilise `str`, `list`, `True` et `None`.")
        assert "str" not in refs["function_names"]
        assert "True" not in refs["function_names"]
        assert "None" not in refs["function_names"]

    def test_no_duplicates(self):
        refs = extract_references("`foo` est dans foo, et `foo` est aussi la.")
        assert refs["function_names"].count("foo") <= 1

    def test_empty_content(self):
        refs = extract_references("")
        assert refs["line_numbers"] == []
        assert refs["function_names"] == []


class TestVerifyAgainstFile:
    @pytest.fixture
    def sample_file(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text(
            "def real_function():\n"
            "    pass\n"
            "\n"
            "def another_real():\n"
            "    return 42\n"
            "\n"
            "async def async_real():\n"
            "    pass\n"
        )
        return str(f)

    def test_all_refs_valid(self, sample_file):
        refs = {
            "line_numbers": [1, 4, 7],
            "function_names": ["real_function", "another_real"],
        }
        true_refs, total, details = verify_against_file(refs, sample_file)
        assert true_refs == 5
        assert total == 5

    def test_hallucinated_function(self, sample_file):
        refs = {
            "line_numbers": [1],
            "function_names": ["real_function", "hallucinated_func"],
        }
        true_refs, total, details = verify_against_file(refs, sample_file)
        # 1 ligne + 1 func vraie = 2 / 3
        assert true_refs == 2
        assert total == 3

    def test_hallucinated_line(self, sample_file):
        refs = {
            "line_numbers": [999],  # le fichier n'a que ~8 lignes
            "function_names": ["real_function"],
        }
        true_refs, total, details = verify_against_file(refs, sample_file)
        assert true_refs == 1
        assert total == 2

    def test_async_function_detected(self, sample_file):
        refs = {"line_numbers": [], "function_names": ["async_real"]}
        true_refs, total, details = verify_against_file(refs, sample_file)
        assert true_refs == 1

    def test_target_not_found(self):
        refs = {"line_numbers": [1], "function_names": ["foo"]}
        true_refs, total, details = verify_against_file(refs, "/nonexistent/path.py")
        assert true_refs == 0
        assert total == 0
        assert "error" in details


class TestComputeFactualityScore:
    @pytest.fixture
    def project_tree(self, tmp_path):
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "module.py").write_text(
            "def verify_code_review(result, target):\n"
            "    return True\n"
            "\n"
            "def _extract_real_names(filepath):\n"
            "    return []\n"
        )
        return str(tmp_path)

    def test_full_truth(self, project_tree):
        """Livrable entierement factuel -> ratio 1.0."""
        content = (
            "La fonction `verify_code_review` (ligne L1) fait X.\n"
            "Et `_extract_real_names` est ligne 4.\n"
        )
        ratio, total, details = compute_factuality_score(
            content, "core/module.py", project_tree
        )
        assert ratio == 1.0
        assert total == 4

    def test_full_hallucination(self, project_tree):
        """Livrable halluciné -> ratio 0.0 -> veto."""
        content = (
            "La fonction `fake_function` (ligne L999) fait X.\n"
            "Et `invented_helper` est ligne 500.\n"
        )
        ratio, total, details = compute_factuality_score(
            content, "core/module.py", project_tree
        )
        assert ratio == 0.0
        assert total == 4

    def test_mixed_below_threshold(self, project_tree):
        """2 vraies refs, 3 hallucinees = 0.4 (< 0.6 veto)."""
        content = (
            "La fonction `verify_code_review` (ligne L1) fait X.\n"
            "Et `hallucinated1`, `hallucinated2`, `hallucinated3` sont ailleurs.\n"
        )
        ratio, total, details = compute_factuality_score(
            content, "core/module.py", project_tree
        )
        assert total == 5
        assert ratio < 0.6
        assert ratio == 2 / 5

    def test_mixed_above_threshold(self, project_tree):
        """3 vraies refs, 1 hallucinee = 0.75 (>= 0.6 valide)."""
        content = (
            "`verify_code_review` (L1), `_extract_real_names` (L4) et `hallucine`.\n"
        )
        ratio, total, details = compute_factuality_score(
            content, "core/module.py", project_tree
        )
        assert total == 5
        assert ratio >= 0.6

    def test_no_refs_returns_minus_one(self, project_tree):
        """Livrable sans reference parsable -> -1 (bypass)."""
        content = "Ceci est un texte verbeux sans aucune fonction ni numero de ligne."
        ratio, total, details = compute_factuality_score(
            content, "core/module.py", project_tree
        )
        assert ratio == -1.0
        assert total == 0

    def test_no_target_file(self, project_tree):
        ratio, total, details = compute_factuality_score("content", "", project_tree)
        assert ratio == -1.0
        assert total == 0
