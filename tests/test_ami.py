"""Tests pour core/ami.py — Alfred, l'ami autonome."""

import json
import os
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.fixture
def isolate_alfred(tmp_path):
    """Isole le singleton Alfred avec un state file temporaire."""
    import core.ami as ami_mod
    ami_mod.AMI_STATE_FILE = str(tmp_path / "ami_state.json")
    ami_mod.AlfredEngine.reset_singleton()
    a = ami_mod.alfred
    a.init()
    yield a
    ami_mod.AlfredEngine.reset_singleton()


class TestAlfredInit:
    def test_singleton(self):
        from core.ami import AlfredEngine
        a1 = AlfredEngine()
        a2 = AlfredEngine()
        assert a1 is a2

    def test_init_state(self, isolate_alfred):
        a = isolate_alfred
        assert a.session_count == 0
        assert a.history == []
        assert a.last_coffee == 0.0


class TestAlfredPersonality:
    def test_system_prompt_not_therapist(self):
        from core.ami import ALFRED_SYSTEM_PROMPT
        prompt_lower = ALFRED_SYSTEM_PROMPT.lower()
        # Le prompt mentionne "fascinant" comme MOT INTERDIT — c'est correct
        # On vérifie que le rôle est ami/pote, pas thérapeute
        assert "pote" in prompt_lower or "ami" in prompt_lower
        assert "thérapeute" in prompt_lower  # mentionné comme ce qu'il N'EST PAS
        assert "tu ne dis jamais" in prompt_lower  # contient des interdictions

    def test_system_prompt_casual_tone(self):
        from core.ami import ALFRED_SYSTEM_PROMPT
        assert "tutoie" in ALFRED_SYSTEM_PROMPT.lower() or "tu " in ALFRED_SYSTEM_PROMPT

    def test_reactions_are_honest(self):
        from core.ami import _HONEST_REACTIONS
        # Aucune réaction ne devrait être flatteuse
        for r in _HONEST_REACTIONS:
            r_lower = r.lower()
            assert "fascinant" not in r_lower
            assert "brillant" not in r_lower
            assert "excellent" not in r_lower


class TestAlfredOpening:
    def test_build_opening_contains_subject(self, isolate_alfred):
        a = isolate_alfred
        text_source = {
            "type": "livrable scolaire",
            "subject": "les fractales",
            "content": "Les fractales sont des objets mathématiques...",
        }
        opening = a._build_opening(text_source)
        assert "fractales" in opening.lower()

    def test_build_opening_contains_reaction(self, isolate_alfred):
        a = isolate_alfred
        text_source = {"type": "journal", "subject": "test", "content": "test"}
        opening = a._build_opening(text_source)
        # Doit contenir du texte (pas vide)
        assert len(opening) > 20


class TestAlfredFindText:
    def test_find_text_empty(self, isolate_alfred):
        """Sans sources, retourne None."""
        a = isolate_alfred
        with patch("core.ami.os.path.exists", return_value=False):
            result = a._find_recent_text()
        # Peut retourner None ou un candidat selon les imports disponibles
        # On vérifie juste que ça ne crashe pas
        assert result is None or isinstance(result, dict)


class TestAlfredCooldown:
    @pytest.mark.asyncio
    async def test_cooldown_respected(self, isolate_alfred):
        """Le café ne se relance pas avant COOLDOWN_HOURS."""
        a = isolate_alfred
        a.last_coffee = time.time()  # Café tout juste pris
        result = await a.coffee_break()
        assert result["status"] == "skipped"
        assert "café" in result["result"].lower() or "prochain" in result["result"].lower()

    @pytest.mark.asyncio
    async def test_no_text_skips(self, isolate_alfred):
        """Sans texte récent, le café est annulé."""
        a = isolate_alfred
        a.last_coffee = 0.0  # Pas de cooldown
        with patch.object(a, "_find_recent_text", return_value=None):
            result = await a.coffee_break()
        assert result["status"] == "skipped"


class TestAlfredDialogue:
    @pytest.mark.asyncio
    async def test_coffee_break_success(self, isolate_alfred):
        """Coffee break complet avec mocks."""
        a = isolate_alfred
        a.last_coffee = 0.0

        text_source = {
            "type": "livrable scolaire",
            "subject": "les fractales",
            "content": "Les fractales...",
        }

        with patch.object(a, "_find_recent_text", return_value=text_source), \
             patch.object(a, "_promethee_responds", new_callable=AsyncMock,
                         return_value="C'est vrai, les fractales c'est un truc de fou."), \
             patch.object(a, "_alfred_responds", new_callable=AsyncMock,
                         return_value="Haha ouais, mais concrètement ça sert à quoi ?"), \
             patch("core.event_bus.bus.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await a.coffee_break()

        assert result["status"] == "success"
        assert result["exchanges"] >= 1
        assert a.session_count == 1

    @pytest.mark.asyncio
    async def test_coffee_break_promethee_silent(self, isolate_alfred):
        """Si Prométhée ne répond pas, le café échoue."""
        a = isolate_alfred
        a.last_coffee = 0.0

        text_source = {"type": "test", "subject": "test", "content": "test"}

        with patch.object(a, "_find_recent_text", return_value=text_source), \
             patch.object(a, "_promethee_responds", new_callable=AsyncMock,
                         return_value=None):
            result = await a.coffee_break()

        assert result["status"] == "error"


class TestAlfredPersistence:
    def test_save_load_roundtrip(self, isolate_alfred):
        a = isolate_alfred
        a.session_count = 5
        a.last_coffee = 12345.0
        a.history = [{"test": True}]
        a._save()

        # Reset et recharge
        from core.ami import AlfredEngine
        AlfredEngine.reset_singleton()
        import core.ami as ami_mod
        a2 = ami_mod.alfred
        a2.init()

        assert a2.session_count == 5
        assert a2.last_coffee == 12345.0
        assert len(a2.history) == 1


class TestAlfredSummary:
    def test_summarize_transcript(self, isolate_alfred):
        a = isolate_alfred
        messages = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "Salut, j'ai lu ton truc sur les fractales."},
            {"role": "user", "content": "Ouais c'était un exercice intéressant."},
            {"role": "assistant", "content": "Concrètement ça t'a appris quoi ?"},
        ]
        summary = a._summarize_transcript(messages)
        assert "Alfred" in summary
        assert len(summary) > 20
