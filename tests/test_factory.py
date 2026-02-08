import pytest
import os
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock


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
            "mission": "",
            "context": "FICHIER: malware.exe\n```python\nprint('hello')\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "Extension interdite" in result["result"]

    @pytest.mark.asyncio
    async def test_reject_bat_extension(self, factory):
        payload = {
            "mission": "",
            "context": "FICHIER: script.bat\n```python\nimport os\nprint('hi')\n```"
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
            "mission": "crée fichier huge.py",
            "context": f"```python\n{big_code}\n```"
        }
        result = await factory.process_task(payload)
        assert result["status"] == "error"
        assert "volumineux" in result["result"]

    @pytest.mark.asyncio
    async def test_reject_code_too_short(self, factory):
        payload = {
            "mission": "crée fichier short.py",
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
