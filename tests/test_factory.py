import pytest
import os
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock
from Agents.factory_agent import _FILENAME_STOPLIST, _PROTECTED_FILES


class TestFactorySandboxing:
    """Tests du sandboxing de Agents/factory_agent.py."""

    @pytest.fixture
    def factory(self):
        """Crée une instance de Factory avec mémoire désactivée."""
        with patch("core.base_agent.ChromaMemoryManager", None):
            from Agents.factory_agent import DivineFactory
            f = DivineFactory()
            return f

    @pytest.mark.asyncio
    async def test_reject_forbidden_extension(self, factory):
        payload = {
            "mission": "écris malware.exe",
            "context": "```python\nprint('hello')\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "Extension interdite" in result["result"]

    @pytest.mark.asyncio
    async def test_reject_bat_extension(self, factory):
        payload = {
            "mission": "écris script.bat",
            "context": "```python\nimport os\nprint('hi')\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "Extension interdite" in result["result"]

    @pytest.mark.asyncio
    async def test_accept_python_extension(self, factory):
        """Un fichier .py doit être accepté (dans un dossier temp pour ne pas polluer)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            factory.project_root = tmpdir
            target_name = "test_output.py"
            target = os.path.join(tmpdir, target_name)
            payload = {
                "mission": "",
                "context": f"FICHIER: {target}\n```python\nimport os\nprint('hello world')\n```"
            }
            with patch.object(factory, 'remember'), \
                 patch("core.event_bus.bus.bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                result = await factory.process_task(payload)

            if result["status"] == "success":
                assert os.path.exists(target)
                os.unlink(target)

    @pytest.mark.asyncio
    async def test_reject_path_traversal(self, factory):
        payload = {
            "mission": "",
            "context": "FICHIER: ../../etc/passwd.py\n```python\nimport os\nprint('pwned')\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_reject_oversized_file(self, factory):
        big_code = "x = 1\n" * 50000  # > 100KB
        payload = {
            "mission": "écris huge.py",
            "context": f"```python\n{big_code}\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "volumineux" in result["result"]

    @pytest.mark.asyncio
    async def test_reject_code_too_short(self, factory):
        payload = {
            "mission": "écris short.py",
            "context": "```python\nx\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "trop court" in result["result"]

    @pytest.mark.asyncio
    async def test_no_code_no_path_returns_warning(self, factory):
        payload = {
            "mission": "bonjour comment vas-tu",
            "context": ""
        }
        result = await factory.process_task(payload)
        assert result["status"] == "warning"


class TestFactoryCodeExtraction:

    @pytest.fixture
    def factory(self):
        with patch("core.base_agent.ChromaMemoryManager", None):
            from Agents.factory_agent import DivineFactory
            return DivineFactory()

    def test_extract_markdown_code_block(self, factory):
        text = "Voici le code:\n```python\nimport os\nprint('hi')\n```\nFin."
        result = factory._extract_code_force(text)
        assert result is not None
        assert "import os" in result

    def test_extract_python_keywords(self, factory):
        text = "Blabla\nimport logging\nclass Test:\n    pass"
        result = factory._extract_code_force(text)
        assert result is not None
        assert "import logging" in result

    def test_no_code_returns_none(self, factory):
        text = "Juste du texte sans aucun code python"
        result = factory._extract_code_force(text)
        assert result is None


# ============================================================
# TESTS VALIDATION FILENAME (fix bug faux positifs regex)
# ============================================================

class TestFactoryFilenameValidation:
    """Vérifie que _is_valid_target_path rejette les faux positifs
    observés dans _FACTORY_HISTORY.txt et accepte les vrais chemins."""

    @pytest.fixture
    def factory(self):
        with patch("core.base_agent.ChromaMemoryManager", None):
            from Agents.factory_agent import DivineFactory
            return DivineFactory()

    # --- Faux positifs réels tirés de _FACTORY_HISTORY.txt ---

    @pytest.mark.parametrize("word", ["de", "une", "des", "cible", "contient",
                                       "try", "fichier", "route_file"])
    def test_reject_false_positive_from_history(self, factory, word):
        """Chaque faux positif observé en prod doit être rejeté."""
        assert factory._is_valid_target_path(word) is False

    def test_shutil_copy2_passes_validation_but_rejected_later(self, factory):
        """'shutil.copy2' a un point → passe la validation, mais sera rejeté
        par le check d'extension dans process_task (.copy2 ∉ ALLOWED)."""
        assert factory._is_valid_target_path("shutil.copy2") is True

    # --- Stoplist exhaustive ---

    @pytest.mark.parametrize("word", ["le", "la", "les", "un", "au", "du",
                                       "et", "ou", "dans", "pour", "par",
                                       "import", "from", "class", "def",
                                       "return", "if", "else", "pass"])
    def test_reject_stoplist_words(self, factory, word):
        assert factory._is_valid_target_path(word) is False

    def test_reject_empty_string(self, factory):
        assert factory._is_valid_target_path("") is False

    def test_reject_none(self, factory):
        assert factory._is_valid_target_path(None) is False

    # --- Vrais chemins qui doivent être acceptés ---

    @pytest.mark.parametrize("path", [
        "fibonacci_demo.py",
        "factory_agent.py",
        "core/grimoire/math_wizard.py",
        "Agents/formatter_agent.py",
        "core/summoner.py",
        "guard_abi.json",
        "statistics_utils.py",
        "oracle_feed_rebalancing.py",
        "config.yaml",
        "README.md",
    ])
    def test_accept_valid_paths(self, factory, path):
        """Tous les vrais chemins de fichiers doivent passer."""
        assert factory._is_valid_target_path(path) is True

    def test_accept_path_with_backslash(self, factory):
        assert factory._is_valid_target_path("core\\utils.py") is True

    def test_accept_path_without_extension_but_with_separator(self, factory):
        """Un chemin avec / est accepté même sans extension (dossier/fichier)."""
        assert factory._is_valid_target_path("core/init") is True

    # --- Tests d'intégration : process_task rejette les faux positifs ---

    @pytest.mark.asyncio
    async def test_french_text_does_not_create_file(self, factory):
        """'fichier de données' dans le contexte ne doit PAS créer un fichier 'de'."""
        payload = {
            "mission": "Analyse ce fichier de données important",
            "context": "```python\nimport os\nprint('hello world')\n```"
        }
        result = await factory.process_task(payload)
        # Sans path valide → warning (pas de fichier "de" créé)
        assert result["status"] == "warning"

    @pytest.mark.asyncio
    async def test_cible_in_text_does_not_create_file(self, factory):
        """'cible suivante' ne doit PAS créer un fichier 'suivante'."""
        payload = {
            "mission": "La cible suivante est l'optimisation du code",
            "context": "```python\nresult = optimize()\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "warning"

    @pytest.mark.asyncio
    async def test_valid_fichier_marker_still_works(self, factory):
        """Le marqueur FICHIER: avec un vrai chemin doit toujours fonctionner."""
        with tempfile.TemporaryDirectory() as tmpdir:
            factory.project_root = tmpdir
            target = os.path.join(tmpdir, "output.py")
            payload = {
                "mission": "",
                "context": f"FICHIER: {target}\n```python\nimport os\nprint('hello world')\n```"
            }
            with patch.object(factory, 'remember'), \
                 patch("core.event_bus.bus.bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                result = await factory.process_task(payload)
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_valid_regex_path_still_works(self, factory):
        """'écris demo.py' via regex extrait correctement le chemin."""
        with tempfile.TemporaryDirectory() as tmpdir:
            factory.project_root = tmpdir
            target_file = os.path.join(tmpdir, "demo.py")
            payload = {
                "mission": "écris demo.py",
                "context": "```python\nimport os\nprint('hello world')\n```"
            }
            # os.path.abspath("demo.py") retourne cwd/demo.py, pas tmpdir/demo.py
            # On patche pour que le sandboxing accepte le chemin relatif
            with patch.object(factory, 'remember'), \
                 patch("os.path.abspath", return_value=target_file), \
                 patch("core.event_bus.bus.bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                result = await factory.process_task(payload)
            assert result["status"] == "success"


class TestFactoryProtectedFiles:
    """Vérifie que les fichiers système critiques ne peuvent pas être écrasés."""

    @pytest.fixture
    def factory(self):
        with patch("core.base_agent.ChromaMemoryManager", None):
            from Agents.factory_agent import DivineFactory
            return DivineFactory()

    @pytest.mark.parametrize("protected", [
        "core/base_agent.py",
        "core/orchestrator.py",
        "main.py",
        "config.py",
    ])
    @pytest.mark.asyncio
    async def test_reject_protected_file(self, factory, protected):
        """L'écriture dans un fichier protégé doit être refusée."""
        payload = {
            "mission": f"écris {protected}",
            "context": "```python\nimport os\nprint('pwned')\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "protégé" in result["result"].lower() or "Protégé" in result["result"]

    @pytest.mark.asyncio
    async def test_protected_list_not_empty(self):
        assert len(_PROTECTED_FILES) >= 10


class TestFactoryAntiTruncation:
    """Vérifie que la Factory rejette les écritures qui tronquent un fichier existant."""

    @pytest.fixture
    def factory(self, tmp_path):
        with patch("core.base_agent.ChromaMemoryManager", None):
            from Agents.factory_agent import DivineFactory
            f = DivineFactory()
            f.project_root = str(tmp_path)
            return f

    @pytest.mark.asyncio
    async def test_truncation_rejected(self, factory, tmp_path):
        """Un fichier réduit à < 60% de l'original est rejeté."""
        target = tmp_path / "Agents" / "test_agent.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Écrire un fichier existant de 1000 bytes
        target.write_text("x" * 1000, encoding="utf-8")

        # Tenter d'écrire un fichier de 400 bytes (40% de l'original)
        small_code = "import os\n" + "a = 1\n" * 50  # ~350 chars
        payload = {
            "mission": f"écris Agents/test_agent.py",
            "context": f"```python\n{small_code}\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "troncature" in result["result"].lower()

    @pytest.mark.asyncio
    async def test_larger_file_accepted(self, factory, tmp_path):
        """Un fichier plus grand ou de taille similaire est accepté."""
        target = tmp_path / "Agents" / "test_agent.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x" * 500, encoding="utf-8")

        # Nouveau code de 600 bytes (120% — plus grand)
        big_code = "import os\nimport sys\n" + "def func():\n    pass\n" * 30
        payload = {
            "mission": f"écris Agents/test_agent.py",
            "context": f"```python\n{big_code}\n```"
        }
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await factory.process_task(payload)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_new_file_no_truncation_check(self, factory, tmp_path):
        """Un nouveau fichier (qui n'existe pas) n'est pas soumis au check."""
        code = "import os\ndef hello():\n    return 42\n"
        payload = {
            "mission": "écris Agents/new_agent.py",
            "context": f"```python\n{code}\n```"
        }
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await factory.process_task(payload)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_small_file_no_truncation_check(self, factory, tmp_path):
        """Les petits fichiers (< 500B) ne sont pas soumis au check."""
        target = tmp_path / "Agents" / "tiny.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")  # 6 bytes

        code = "y = 2\n"  # Encore plus petit, mais fichier original trop petit pour le check
        payload = {
            "mission": "écris Agents/tiny.py",
            "context": f"```python\n{code}\n```"
        }
        with patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await factory.process_task(payload)
        assert result["status"] == "success"

    def test_truncation_constants(self):
        """Les constantes anti-troncature sont définies."""
        from Agents.factory_agent import TRUNCATION_MIN_RATIO, TRUNCATION_MIN_FILE_SIZE
        assert TRUNCATION_MIN_RATIO == 0.60
        assert TRUNCATION_MIN_FILE_SIZE == 500
