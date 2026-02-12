"""Tests pour le Formatter Agent - extraction déterministe fallback."""
import pytest
from Agents.formatter_agent import DivineFormatter


class TestIsValidFilename:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_valid_python_file(self):
        assert self.f._is_valid_filename("core/test.py") is True

    def test_valid_simple_file(self):
        assert self.f._is_valid_filename("agent_generator.py") is True

    def test_rejects_python_keyword(self):
        assert self.f._is_valid_filename("shutil.copy") is False
        assert self.f._is_valid_filename("import.py") is False

    def test_rejects_no_extension(self):
        assert self.f._is_valid_filename("randomword") is False

    def test_accepts_special_files(self):
        assert self.f._is_valid_filename("makefile") is True
        assert self.f._is_valid_filename("dockerfile") is True

    def test_rejects_empty(self):
        assert self.f._is_valid_filename("") is False
        assert self.f._is_valid_filename(None) is False


class TestExtractFromContext:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_extract_fichier_marker(self):
        text = "Voici le code :\nFICHIER: core/utils.py\n```python\nprint('hello')\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "core/utils.py"
        assert "print('hello')" in code

    def test_extract_path_pattern(self):
        text = "Le fichier core/new_module.py contient :\n```python\nclass Foo:\n    pass\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "core/new_module.py"
        assert "class Foo:" in code

    def test_extract_agents_path(self):
        text = "Modification dans Agents/custom_agent.py\n```python\nimport os\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "Agents/custom_agent.py"
        assert "import os" in code

    def test_extract_simple_py_file(self):
        text = "Le module agent_code_generator.py devrait avoir :\n```python\ndef generate():\n    return 42\n```"
        target, code = self.f._extract_from_context(text)
        assert target == "agent_code_generator.py"
        assert "def generate():" in code

    def test_no_code_block(self):
        text = "Il faudrait modifier core/base.py mais je n'ai pas de code."
        target, code = self.f._extract_from_context(text)
        assert target == "core/base.py"
        assert code is None

    def test_no_filename_no_code(self):
        text = "Voici une explication générale sans code ni fichier."
        target, code = self.f._extract_from_context(text)
        assert target is None
        assert code is None

    def test_code_but_no_filename(self):
        text = "Voici le code :\n```python\nprint('hello')\n```"
        target, code = self.f._extract_from_context(text)
        assert target is None
        assert "print('hello')" in code

    def test_picks_longest_code_block(self):
        text = (
            "```python\nx=1\n```\n"
            "Et aussi :\n"
            "```python\nclass BigClass:\n    def method(self):\n        return 42\n```"
        )
        _, code = self.f._extract_from_context(text)
        assert "class BigClass:" in code

    def test_rejects_import_py_as_filename(self):
        """import.py n'est pas un nom de fichier valide (blacklist)."""
        text = "Le fichier import.py\n```python\nx=1\n```"
        target, code = self.f._extract_from_context(text)
        # import.py est blacklisté, donc target devrait être None ou un autre match
        assert target != "import.py"

    def test_backslash_paths(self):
        text = "Le fichier core\\router.py a changé\n```python\npass\n```"
        target, code = self.f._extract_from_context(text)
        assert target is not None
        assert "router" in target


class TestHasFormattableCode:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_code_block_detected(self):
        text = "Voici le code :\n```python\ndef hello():\n    return 42\n```"
        assert self.f._has_formattable_code(text) is True

    def test_python_lines_detected(self):
        text = "import os\nfrom pathlib import Path\ndef process():\n    return None\n"
        assert self.f._has_formattable_code(text) is True

    def test_analysis_text_rejected(self):
        """Un texte d'analyse sans code est rejeté."""
        text = (
            "L'architecture actuelle utilise un pattern observer. "
            "Il serait judicieux d'ajouter un cache LRU pour optimiser les appels. "
            "Le module devrait être refactorisé en trois composants distincts."
        )
        assert self.f._has_formattable_code(text) is False

    def test_single_import_not_enough(self):
        text = "Il faudrait faire import os dans le fichier."
        assert self.f._has_formattable_code(text) is False

    def test_class_definition_code(self):
        text = "class MyAgent:\n    def process(self):\n        return None\n    def cleanup(self):\n        pass"
        assert self.f._has_formattable_code(text) is True


class TestRebuildFormattedResponse:
    def setup_method(self):
        self.f = DivineFormatter()

    def test_rebuild(self):
        result = self.f._rebuild_formatted_response("core/test.py", "print('hello')")
        assert "FICHIER: core/test.py" in result
        assert "```python" in result
        assert "print('hello')" in result
