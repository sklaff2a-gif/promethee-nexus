"""Tests pour core/factuality_verifier.py (Veto epistemique V3.2).

Couvre l'extraction des references (lignes, fonctions), la verification
contre le fichier reel, et le calcul du ratio factuality.
"""
import os

os.environ.setdefault("PROMETHEE_TEST_MODE", "1")

import pytest

from core.factuality_verifier import (
    compute_coverage,
    compute_creation_factuality,
    compute_factuality_score,
    compute_tech_ratio,
    detect_format_rule,
    extract_key_terms,
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


# ============================================================
# V3.3 Factuality CREATION (2026-04-19)
# ============================================================

class TestTechRatio:
    def test_pure_prose_zero_ratio(self):
        content = "Une fable sur l algorithme du courage. Le neurone de la sagesse."
        # Aucun pattern technique (mots isoles non sanctionnes)
        assert compute_tech_ratio(content) == 0.0

    def test_python_code_block_detected(self):
        content = "Voici le code :\n```python\ndef foo():\n    return 42\n```\nVoila."
        # Bloc de code + def foo( detectes
        ratio = compute_tech_ratio(content)
        assert ratio > 0.0

    def test_backtick_module_function_detected(self):
        content = "On utilise `core.prefrontal` pour gerer les buts."
        assert compute_tech_ratio(content) > 0.0

    def test_import_statement_detected(self):
        content = "La fable dit : import os et tout change"
        assert compute_tech_ratio(content) > 0.0

    def test_empty_content(self):
        assert compute_tech_ratio("") == 0.0


class TestKeyTerms:
    def test_extract_proper_nouns(self):
        challenge = "Réécris la fin de la fable avec Souris de Vector Store"
        terms = extract_key_terms(challenge)
        # "Souris", "Vector Store" doivent etre extraits
        assert any("Souris" in t for t in terms)
        assert any("Vector" in t for t in terms)

    def test_ignore_stop_words(self):
        challenge = "Pour la prochaine tâche, écris un poème"
        terms = extract_key_terms(challenge)
        # "Pour", "Ecris" filtres
        assert "Pour" not in terms
        for t in terms:
            assert not t.startswith("Écris") and not t.startswith("Ecris")

    def test_coverage_full(self):
        terms = ["Souris", "Vector Store"]
        content = "Voici Souris qui rencontre Vector Store dans la foret."
        assert compute_coverage(content, terms) == 1.0

    def test_coverage_partial(self):
        terms = ["Souris", "Vector Store", "haiku"]
        content = "Voici Souris dans la foret."
        assert compute_coverage(content, terms) == pytest.approx(1/3, abs=0.01)

    def test_coverage_empty_terms(self):
        assert compute_coverage("text", []) == 1.0


class TestFormatRules:
    def test_haiku_rule_detected(self):
        rule = detect_format_rule("Compose un haiku sur le silence.")
        assert rule is not None
        # Haiku valide : 3 lignes
        assert rule("Premier vers\nDeuxieme vers\nTroisieme") is True
        # Haiku invalide : 10 lignes
        assert rule("\n".join(["v"] * 10)) is False

    def test_single_paragraph_rule(self):
        rule = detect_format_rule("Ecris un seul paragraphe")
        assert rule is not None
        assert rule("Un paragraphe simple sans saut.") is True
        assert rule("Un paragraphe.\n\nUn deuxieme.\n\nUn troisieme.") is False

    def test_no_rule_on_free_prompt(self):
        assert detect_format_rule("Ecris ce que tu veux.") is None


class TestComputeCreationFactuality:
    def test_fable_honnete_score_eleve(self):
        """Fable pure sans code, key_terms presents, pas de format strict."""
        challenge = "Ecris une fable sur Souris et Renard"
        content = ("Il etait une fois une Souris qui rencontra un Renard. "
                   "La Souris dit au Renard qu il ne fallait pas mentir.")
        score, details = compute_creation_factuality(content, challenge)
        assert score > 0.7

    def test_cas_reel_17_04_veto(self):
        """Cas du 17/04 : fable + code Python = veto."""
        challenge = ("Reecris la fin de la fable pour inclure "
                     "la Souris de Vector Store + compose le haiku")
        content = ("Une fable sur l Echo et la Pierre.\n"
                   "```python\n"
                   "import os\n"
                   "def validate_factuality():\n"
                   "    return True\n"
                   "```\n"
                   "La fable continue.")
        score, details = compute_creation_factuality(content, challenge)
        # V1 detecte tech ratio eleve -> score bas. V2 detecte absence de
        # "Souris", "Vector Store", "haiku" -> score bas.
        assert score < 0.6

    def test_haiku_incorrect_format(self):
        challenge = "Compose un haiku sur la tristesse."
        # Roman au lieu de haiku : V3 format echoue
        content = "\n".join(["ligne " + str(i) for i in range(20)])
        score, details = compute_creation_factuality(content, challenge)
        assert score < 0.6

    def test_consigne_libre_sans_terme(self):
        """Consigne totalement libre -> seul V1 actif."""
        challenge = "Ecris librement."
        content = "Un texte sans code, sans structure, juste de la prose."
        score, details = compute_creation_factuality(content, challenge)
        # V1 : 0 tech tokens -> score=1.0. V2 : pas de key_terms >=2. V3 : pas de regle.
        assert score == 1.0

    def test_content_vide_bypass(self):
        score, details = compute_creation_factuality("", "consigne")
        assert score == -1.0

    def test_details_includes_active_vectors(self):
        """Retour 'details' doit contenir le comptage des vecteurs actifs."""
        challenge = "Compose un haiku sur Souris et Vector Store"
        content = "Un haiku\nSur la Souris\nEt Vector Store"
        score, details = compute_creation_factuality(content, challenge)
        assert "active_vectors" in details
        assert details["active_vectors"] >= 2  # V1 + V2 au minimum

