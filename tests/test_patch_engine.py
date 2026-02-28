"""Tests pour core/patch_engine.py — moteur de micro-patchs SEARCH/REPLACE."""

import ast
import pytest
from core.patch_engine import parse_patch, apply_patch, PatchHunk, PatchResult, MICRO_PATCH_PROMPT


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
