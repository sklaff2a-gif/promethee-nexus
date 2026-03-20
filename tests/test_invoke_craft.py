"""Tests pour !invoke et !craft — invocation et creation d'outils Grimoire."""

import json
import os
import shutil
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.chat_engine import ChatEngine


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def chat():
    """ChatEngine frais pour chaque test."""
    engine = ChatEngine.__new__(ChatEngine)
    engine._init_done = True
    engine.messages = []
    engine._cached_organ_parts = []
    engine._cache_timestamp = 0.0
    engine._last_subfolder_hint = None
    ChatEngine._last_dispatch_time = 0.0
    return engine


@pytest.fixture
def tmp_grimoire(tmp_path):
    """Grimoire temporaire pour tester !craft sans toucher le vrai."""
    grimoire_dir = tmp_path / "grimoire"
    grimoire_dir.mkdir()
    index_path = grimoire_dir / "grimoire_index.json"
    index_path.write_text(json.dumps([
        {
            "slug": "math_wizard",
            "name": "MathWizard",
            "description": "Calculs mathematiques",
            "keywords": ["calcul", "equation"],
            "file": "math_wizard.py",
        },
        {
            "slug": "dr_debug",
            "name": "DrDebug",
            "description": "Diagnostic de bugs Python",
            "keywords": ["debug", "traceback"],
            "file": "dr_debug.py",
        },
    ]), encoding="utf-8")
    return grimoire_dir, str(index_path)


# ============================================================
# Tests !invoke
# ============================================================

class TestInvokeCommand:
    """Tests pour _execute_invoke_command."""

    @pytest.mark.asyncio
    async def test_invoke_no_args_lists_specialists(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        result = await chat._execute_invoke_command([])
        assert "GRIMOIRE" in result
        assert "math_wizard" in result
        assert "dr_debug" in result
        assert "2 specialistes" in result

    @pytest.mark.asyncio
    async def test_invoke_slug_only_shows_description(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        result = await chat._execute_invoke_command(["math_wizard"])
        assert "MathWizard" in result
        assert "Calculs mathematiques" in result
        assert "Usage" in result

    @pytest.mark.asyncio
    async def test_invoke_unknown_slug(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        result = await chat._execute_invoke_command(["unknown_agent"])
        assert "introuvable" in result
        assert "math_wizard" in result  # Suggestion

    @pytest.mark.asyncio
    async def test_invoke_with_mission_dispatches(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        with patch.object(chat, "_execute_dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = "[DISPATCH] OK"
            result = await chat._execute_invoke_command(["math_wizard", "calcule", "2+2"])
        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args
        assert call_args[0][0] == "math_wizard"
        assert "calcule 2+2" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_invoke_normalizes_slug(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        result = await chat._execute_invoke_command(["MATH-WIZARD"])
        assert "MathWizard" in result  # Trouve malgre les majuscules et tiret

    @pytest.mark.asyncio
    async def test_invoke_empty_index(self, chat, tmp_path):
        index_path = tmp_path / "empty.json"
        index_path.write_text("[]", encoding="utf-8")
        chat._GRIMOIRE_INDEX_PATH = str(index_path)
        result = await chat._execute_invoke_command([])
        assert "Aucun specialiste" in result


# ============================================================
# Tests !craft
# ============================================================

class TestCraftCommand:
    """Tests pour _execute_craft_command."""

    @pytest.mark.asyncio
    async def test_craft_no_args(self, chat):
        result = await chat._execute_craft_command([])
        assert "Usage" in result

    @pytest.mark.asyncio
    async def test_craft_one_arg(self, chat):
        result = await chat._execute_craft_command(["myagent"])
        assert "Usage" in result

    @pytest.mark.asyncio
    async def test_craft_invalid_name(self, chat):
        result = await chat._execute_craft_command(["my agent!", "desc"])
        assert "invalide" in result

    @pytest.mark.asyncio
    async def test_craft_short_name(self, chat):
        result = await chat._execute_craft_command(["ab", "description"])
        assert "trop court" in result

    @pytest.mark.asyncio
    async def test_craft_existing_slug(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        chat._GRIMOIRE_DIR = str(grimoire_dir)
        result = await chat._execute_craft_command(["math_wizard", "description"])
        assert "existe deja" in result

    @pytest.mark.asyncio
    async def test_craft_creates_file_and_index(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        chat._GRIMOIRE_DIR = str(grimoire_dir)
        result = await chat._execute_craft_command([
            "csv_parser", "Analyse", "de", "fichiers", "CSV", "et", "statistiques",
        ])
        assert "OUTIL CREE" in result
        assert "CsvParser" in result
        assert "csv_parser" in result

        # Verifier le fichier
        agent_path = grimoire_dir / "csv_parser.py"
        assert agent_path.exists()
        content = agent_path.read_text(encoding="utf-8")
        assert "class CsvParser" in content
        assert "BaseAgent" in content
        assert "process_task" in content

        # Verifier l'index
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
        slugs = [e["slug"] for e in index]
        assert "csv_parser" in slugs

    @pytest.mark.asyncio
    async def test_craft_generates_valid_python(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        chat._GRIMOIRE_DIR = str(grimoire_dir)
        await chat._execute_craft_command([
            "test_tool", "Outil", "de", "test", "temporaire",
        ])
        agent_path = grimoire_dir / "test_tool.py"
        content = agent_path.read_text(encoding="utf-8")
        # Valider que c'est du Python valide
        import ast
        ast.parse(content)  # Ne doit pas lever d'exception

    @pytest.mark.asyncio
    async def test_craft_extracts_keywords(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        chat._GRIMOIRE_DIR = str(grimoire_dir)
        result = await chat._execute_craft_command([
            "data_tool", "Analyse", "statistique", "avancee", "avec", "graphiques",
        ])
        assert "Keywords" in result
        assert "analyse" in result.lower()

    @pytest.mark.asyncio
    async def test_craft_normalizes_name(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        chat._GRIMOIRE_DIR = str(grimoire_dir)
        result = await chat._execute_craft_command([
            "My-Tool", "Un", "outil", "pratique",
        ])
        assert "OUTIL CREE" in result
        assert "MyTool" in result
        # Fichier avec underscore
        assert (grimoire_dir / "my_tool.py").exists()

    @pytest.mark.asyncio
    async def test_craft_then_invoke_works(self, chat, tmp_grimoire):
        """Test d'integration : craft puis invoke sur le meme agent."""
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        chat._GRIMOIRE_DIR = str(grimoire_dir)

        # Creer l'outil
        result = await chat._execute_craft_command([
            "helper", "Assistant", "general", "pour", "questions",
        ])
        assert "OUTIL CREE" in result

        # Invoquer (description seule)
        result = await chat._execute_invoke_command(["helper"])
        assert "Helper" in result
        assert "Usage" in result


# ============================================================
# Tests integration dans _execute_command
# ============================================================

class TestCommandRouting:
    """Verifie que !invoke et !craft sont bien routes."""

    @pytest.mark.asyncio
    async def test_invoke_routed(self, chat, tmp_grimoire):
        grimoire_dir, index_path = tmp_grimoire
        chat._GRIMOIRE_INDEX_PATH = index_path
        result = await chat._execute_command("invoke", [])
        assert "GRIMOIRE" in result

    @pytest.mark.asyncio
    async def test_craft_routed(self, chat):
        result = await chat._execute_command("craft", [])
        assert "Usage" in result

    def test_whitelist_includes_invoke_craft(self):
        assert "invoke" in ChatEngine._AUTO_ACTION_WHITELIST
        assert "craft" in ChatEngine._AUTO_ACTION_WHITELIST

    def test_help_includes_invoke_craft(self):
        assert "invoke" in ChatEngine._COMMAND_HELP
        assert "craft" in ChatEngine._COMMAND_HELP
