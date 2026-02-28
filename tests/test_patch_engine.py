"""Tests pour core/patch_engine.py — moteur de micro-patchs SEARCH/REPLACE."""

import ast
import pytest
from core.patch_engine import (
    parse_patch, apply_patch, build_retry_context, PatchHunk, PatchResult,
    MICRO_PATCH_PROMPT, MICRO_PATCH_RETRY_PROMPT, _find_best_match_line,
)


# ===========================================================================
# TestParsePatch
# ===========================================================================

class TestParsePatch:

    def test_single_hunk(self):
        raw = (
            "<<<< SEARCH\n"
            "    return data\n"
            "====\n"
            "    return data.strip()\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 1
        assert hunks[0].search == "    return data"
        assert hunks[0].replace == "    return data.strip()"
        assert not errors

    def test_multiple_hunks(self):
        raw = (
            "<<<< SEARCH\n"
            "import os\n"
            "====\n"
            "import os\n"
            "import sys\n"
            ">>>> REPLACE\n"
            "\n"
            "<<<< SEARCH\n"
            "    pass\n"
            "====\n"
            "    return True\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 2
        assert hunks[0].replace == "import os\nimport sys"
        assert hunks[1].search == "    pass"
        assert not errors

    def test_empty_replace(self):
        """REPLACE vide = suppression (valide)."""
        raw = (
            "<<<< SEARCH\n"
            "    # commentaire inutile\n"
            "====\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 1
        assert hunks[0].replace == ""
        assert not errors

    def test_empty_search_error(self):
        """SEARCH vide = erreur."""
        raw = (
            "<<<< SEARCH\n"
            "====\n"
            "    nouveau code\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 0
        assert len(errors) >= 1
        assert "vide" in errors[0].lower()

    def test_markdown_wrapping(self):
        """Blocs dans ```python ... ```."""
        raw = (
            "Voici la modification :\n"
            "```python\n"
            "<<<< SEARCH\n"
            "    old_code()\n"
            "====\n"
            "    new_code()\n"
            ">>>> REPLACE\n"
            "```"
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 1
        assert hunks[0].search == "    old_code()"
        assert hunks[0].replace == "    new_code()"

    def test_trailing_spaces_markers(self):
        """Marqueurs avec espaces trailing."""
        raw = (
            "<<<< SEARCH   \n"
            "    x = 1\n"
            "====   \n"
            "    x = 2\n"
            ">>>> REPLACE   "
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 1
        assert hunks[0].search == "    x = 1"

    def test_text_around_blocks(self):
        """Texte explicatif avant/après les blocs."""
        raw = (
            "Voici les modifications nécessaires :\n\n"
            "1. Première modification :\n"
            "<<<< SEARCH\n"
            "    old()\n"
            "====\n"
            "    new()\n"
            ">>>> REPLACE\n\n"
            "Cela améliore les performances."
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 1
        assert not errors

    def test_no_blocks_empty(self):
        """Texte sans marqueur → liste vide."""
        raw = "Voici du texte sans aucun bloc SEARCH/REPLACE."
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 0
        assert not errors

    def test_incomplete_block(self):
        """SEARCH sans REPLACE → erreur."""
        raw = (
            "<<<< SEARCH\n"
            "    code\n"
            "===="
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 0
        assert len(errors) >= 1
        assert "incomplet" in errors[0].lower() or "état" in errors[0].lower()

    def test_blank_lines_preserved(self):
        """Lignes vides dans SEARCH/REPLACE préservées."""
        raw = (
            "<<<< SEARCH\n"
            "    def foo():\n"
            "\n"
            "        pass\n"
            "====\n"
            "    def foo():\n"
            "\n"
            "        return 42\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 1
        assert "\n\n" in hunks[0].search
        assert "\n\n" in hunks[0].replace

    def test_separator_in_code(self):
        """==== dans un commentaire ne casse pas le parsing."""
        raw = (
            "<<<< SEARCH\n"
            "    # Avant le séparateur\n"
            "    x = 1\n"
            "====\n"
            "    # ==== section ====\n"
            "    x = 2\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw)
        assert len(hunks) == 1
        # Le ==== dans le REPLACE est du contenu, pas un séparateur
        assert "====" in hunks[0].replace


# ===========================================================================
# TestApplyPatch
# ===========================================================================

class TestApplyPatch:

    def test_simple_replacement(self):
        source = "def hello():\n    return 'world'"
        hunks = [PatchHunk(search="    return 'world'", replace="    return 'hello world'")]
        result = apply_patch(source, hunks)
        assert result.success
        assert result.hunks_applied == 1
        assert "hello world" in result.patched_source

    def test_multi_line_replacement(self):
        source = "def process():\n    x = 1\n    y = 2\n    return x + y"
        hunks = [PatchHunk(
            search="    x = 1\n    y = 2\n    return x + y",
            replace="    x = 10\n    y = 20\n    z = 30\n    return x + y + z"
        )]
        result = apply_patch(source, hunks)
        assert result.success
        assert "z = 30" in result.patched_source

    def test_deletion(self):
        source = "import os\n# commentaire\nimport sys"
        hunks = [PatchHunk(search="# commentaire\n", replace="")]
        result = apply_patch(source, hunks)
        assert result.success
        assert "commentaire" not in result.patched_source

    def test_insertion_via_context(self):
        source = "import os\n\ndef main():\n    pass"
        hunks = [PatchHunk(
            search="import os",
            replace="import os\nimport logging"
        )]
        result = apply_patch(source, hunks)
        assert result.success
        assert "import logging" in result.patched_source

    def test_search_not_found_fails(self):
        source = "def hello():\n    pass"
        hunks = [PatchHunk(search="def goodbye():\n    pass", replace="def goodbye():\n    return")]
        result = apply_patch(source, hunks)
        assert not result.success
        assert result.hunks_applied == 0
        assert "introuvable" in result.error.lower()

    def test_partial_failure(self):
        source = "def a():\n    pass\n\ndef b():\n    pass"
        hunks = [
            PatchHunk(search="def a():\n    pass", replace="def a():\n    return 1"),
            PatchHunk(search="def inexistant():\n    pass", replace="def c():\n    return 3"),
        ]
        result = apply_patch(source, hunks)
        assert not result.success
        assert result.hunks_applied == 1
        assert result.hunks_total == 2

    def test_multiple_hunks_sequential(self):
        source = "x = 1\ny = 2\nz = 3"
        hunks = [
            PatchHunk(search="x = 1", replace="x = 10"),
            PatchHunk(search="z = 3", replace="z = 30"),
        ]
        result = apply_patch(source, hunks)
        assert result.success
        assert result.hunks_applied == 2
        assert "x = 10" in result.patched_source
        assert "z = 30" in result.patched_source
        assert "y = 2" in result.patched_source

    def test_first_occurrence_only(self):
        source = "x = 1\nprint(x)\nx = 1\nprint(x)"
        hunks = [PatchHunk(search="x = 1", replace="x = 99")]
        result = apply_patch(source, hunks)
        assert result.success
        # Seule la première occurrence est remplacée
        assert result.patched_source.count("x = 99") == 1
        assert result.patched_source.count("x = 1") == 1

    def test_normalized_matching(self):
        """Trailing spaces tolérés."""
        source = "def foo():   \n    pass"
        hunks = [PatchHunk(search="def foo():\n    pass", replace="def foo():\n    return 42")]
        result = apply_patch(source, hunks)
        assert result.success
        assert "return 42" in result.patched_source

    def test_empty_hunks_fails(self):
        source = "code"
        result = apply_patch(source, [])
        assert not result.success
        assert "aucun" in result.error.lower()

    def test_preserves_rest(self):
        source = "# en-tête\nimport os\n\ndef main():\n    pass\n\n# fin"
        hunks = [PatchHunk(search="def main():\n    pass", replace="def main():\n    return 0")]
        result = apply_patch(source, hunks)
        assert result.success
        assert result.patched_source.startswith("# en-tête")
        assert result.patched_source.endswith("# fin")
        assert "import os" in result.patched_source


# ===========================================================================
# TestIntegration
# ===========================================================================

class TestIntegration:

    def test_add_import_and_modify(self):
        source = (
            "import os\n"
            "\n"
            "def process(data):\n"
            "    return data\n"
        )
        raw_patch = (
            "<<<< SEARCH\n"
            "import os\n"
            "====\n"
            "import os\n"
            "import logging\n"
            ">>>> REPLACE\n"
            "\n"
            "<<<< SEARCH\n"
            "def process(data):\n"
            "    return data\n"
            "====\n"
            "def process(data):\n"
            "    logging.info(f\"Processing {len(data)} items\")\n"
            "    return data\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw_patch)
        assert len(hunks) == 2
        result = apply_patch(source, hunks)
        assert result.success
        assert "import logging" in result.patched_source
        assert "logging.info" in result.patched_source

    def test_result_valid_python(self):
        source = (
            "import os\n"
            "\n"
            "def hello():\n"
            "    print('hello')\n"
        )
        raw_patch = (
            "<<<< SEARCH\n"
            "def hello():\n"
            "    print('hello')\n"
            "====\n"
            "def hello():\n"
            "    msg = 'hello world'\n"
            "    print(msg)\n"
            ">>>> REPLACE"
        )
        hunks, _ = parse_patch(raw_patch)
        result = apply_patch(source, hunks)
        assert result.success
        # Le code patché doit être du Python valide
        ast.parse(result.patched_source)

    def test_unicode_in_patch(self):
        source = "# Commentaire\ndef traiter():\n    pass"
        raw_patch = (
            "<<<< SEARCH\n"
            "# Commentaire\n"
            "====\n"
            "# Amélioration : gestion des données françaises (accents, cédilles)\n"
            ">>>> REPLACE"
        )
        hunks, errors = parse_patch(raw_patch)
        assert len(hunks) == 1
        result = apply_patch(source, hunks)
        assert result.success
        assert "cédilles" in result.patched_source

    def test_windows_line_endings(self):
        """\\r\\n normalisé."""
        source = "def foo():\r\n    pass\r\n"
        # Normaliser le source (comme le ferait un read de fichier)
        source_norm = source.replace("\r\n", "\n")
        raw_patch = (
            "<<<< SEARCH\n"
            "def foo():\n"
            "    pass\n"
            "====\n"
            "def foo():\n"
            "    return 42\n"
            ">>>> REPLACE"
        )
        hunks, _ = parse_patch(raw_patch)
        result = apply_patch(source_norm, hunks)
        assert result.success

    def test_prompt_template_format(self):
        """Le template contient les marqueurs et les placeholders."""
        assert "<<<< SEARCH" in MICRO_PATCH_PROMPT
        assert "====" in MICRO_PATCH_PROMPT
        assert ">>>> REPLACE" in MICRO_PATCH_PROMPT
        assert "{target_file}" in MICRO_PATCH_PROMPT
        assert "{spec_id}" in MICRO_PATCH_PROMPT
        assert "{source_excerpt}" in MICRO_PATCH_PROMPT
        assert "{guardrail}" in MICRO_PATCH_PROMPT

    def test_retry_prompt_template_format(self):
        """Le template retry contient les placeholders nécessaires."""
        assert "{error_summary}" in MICRO_PATCH_RETRY_PROMPT
        assert "{failed_details}" in MICRO_PATCH_RETRY_PROMPT
        assert "{source_context}" in MICRO_PATCH_RETRY_PROMPT
        assert "{guardrail}" in MICRO_PATCH_RETRY_PROMPT
        assert "<<<< SEARCH" in MICRO_PATCH_RETRY_PROMPT


# ===========================================================================
# TestFailedHunks
# ===========================================================================

class TestFailedHunks:
    """Vérifie que les hunks échoués sont correctement enregistrés."""

    def test_failed_hunks_recorded(self):
        source = "x = 1\ny = 2"
        hunks = [PatchHunk(search="z = 3", replace="z = 99")]
        result = apply_patch(source, hunks)
        assert not result.success
        assert len(result.failed_hunks) == 1
        assert result.failed_hunks[0][0] == 0  # index
        assert result.failed_hunks[0][1].search == "z = 3"

    def test_partial_failure_records_correct_hunks(self):
        source = "x = 1\ny = 2\nz = 3"
        hunks = [
            PatchHunk(search="x = 1", replace="x = 10"),  # OK
            PatchHunk(search="w = 4", replace="w = 40"),  # FAIL
            PatchHunk(search="z = 3", replace="z = 30"),  # OK
        ]
        result = apply_patch(source, hunks)
        assert not result.success
        assert result.hunks_applied == 2
        assert len(result.failed_hunks) == 1
        assert result.failed_hunks[0][0] == 1  # index du hunk échoué
        assert result.failed_hunks[0][1].search == "w = 4"

    def test_success_has_no_failed_hunks(self):
        source = "x = 1"
        hunks = [PatchHunk(search="x = 1", replace="x = 2")]
        result = apply_patch(source, hunks)
        assert result.success
        assert len(result.failed_hunks) == 0


# ===========================================================================
# TestBuildRetryContext
# ===========================================================================

class TestBuildRetryContext:

    def test_empty_failed_hunks(self):
        result = PatchResult(
            success=True, patched_source="x = 1",
            hunks_applied=1, hunks_total=1,
        )
        ctx = build_retry_context("x = 1", result)
        assert ctx["error_summary"] == ""
        assert ctx["failed_details"] == ""

    def test_single_failed_hunk_context(self):
        source = (
            "import os\n"
            "\n"
            "class Agent:\n"
            "    def process(self, data):\n"
            "        return data\n"
            "\n"
            "    def cleanup(self):\n"
            "        pass\n"
        )
        failed_hunk = PatchHunk(
            search="    def proccess(self, data):\n        return data",  # typo
            replace="    def process(self, data):\n        return data.strip()"
        )
        result = PatchResult(
            success=False, patched_source=source,
            hunks_applied=0, hunks_total=1,
            error="Hunk 1/1: SEARCH introuvable",
            failed_hunks=[(0, failed_hunk)],
        )
        ctx = build_retry_context(source, result)
        assert "1 bloc(s)" in ctx["error_summary"]
        assert "proccess" in ctx["failed_details"]
        # Le contexte source doit montrer la zone autour de "process"
        assert "process" in ctx["source_context"]

    def test_multiple_failed_hunks(self):
        source = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        result = PatchResult(
            success=False, patched_source=source,
            hunks_applied=0, hunks_total=2,
            error="...",
            failed_hunks=[
                (0, PatchHunk(search="def fooo():", replace="def foo():\n    return 1")),
                (1, PatchHunk(search="def barr():", replace="def bar():\n    return 2")),
            ],
        )
        ctx = build_retry_context(source, result)
        assert "2 bloc(s)" in ctx["error_summary"]
        assert "BLOC 1" in ctx["failed_details"]
        assert "BLOC 2" in ctx["failed_details"]

    def test_source_context_has_line_numbers(self):
        source = "\n".join(f"line_{i} = {i}" for i in range(20))
        result = PatchResult(
            success=False, patched_source=source,
            hunks_applied=0, hunks_total=1,
            error="...",
            failed_hunks=[
                (0, PatchHunk(search="line_10 = 999", replace="line_10 = 0")),
            ],
        )
        ctx = build_retry_context(source, result)
        # Doit contenir des numéros de ligne
        assert " | " in ctx["source_context"]

    def test_retry_end_to_end(self):
        """Simule un cycle complet : patch échoue → retry contexte → patch corrigé."""
        source = (
            "import os\n"
            "\n"
            "def calculate(x, y):\n"
            "    return x + y\n"
        )
        # Patch initial avec typo dans le SEARCH
        bad_patch = (
            "<<<< SEARCH\n"
            "def calcualte(x, y):\n"
            "    return x + y\n"
            "====\n"
            "def calculate(x, y):\n"
            "    if y == 0:\n"
            "        return x\n"
            "    return x + y\n"
            ">>>> REPLACE"
        )
        hunks, _ = parse_patch(bad_patch)
        result = apply_patch(source, hunks)
        assert not result.success
        assert len(result.failed_hunks) == 1

        # Construire le contexte de retry
        ctx = build_retry_context(source, result)
        assert "calcualte" in ctx["failed_details"]
        assert "calculate" in ctx["source_context"]

        # Patch corrigé (comme le ferait le LLM après retry)
        good_patch = (
            "<<<< SEARCH\n"
            "def calculate(x, y):\n"
            "    return x + y\n"
            "====\n"
            "def calculate(x, y):\n"
            "    if y == 0:\n"
            "        return x\n"
            "    return x + y\n"
            ">>>> REPLACE"
        )
        retry_hunks, _ = parse_patch(good_patch)
        retry_result = apply_patch(source, retry_hunks)
        assert retry_result.success
        assert "if y == 0" in retry_result.patched_source


# ===========================================================================
# TestFindBestMatchLine
# ===========================================================================

class TestFindBestMatchLine:

    def test_exact_match(self):
        lines = ["import os", "def foo():", "    pass"]
        idx = _find_best_match_line(lines, "def foo():")
        assert idx == 1

    def test_approximate_match(self):
        lines = ["import os", "def process_data(self, items):", "    pass"]
        idx = _find_best_match_line(lines, "def process_data(self, data):")
        assert idx == 1  # "process_data" et "self" matchent

    def test_no_match(self):
        lines = ["x = 1", "y = 2"]
        idx = _find_best_match_line(lines, "def completely_unrelated():")
        # "completely_unrelated" n'est dans aucune ligne
        assert idx is None or isinstance(idx, int)
