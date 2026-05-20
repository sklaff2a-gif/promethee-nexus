"""Tests pour core/rival.py — Stefan, le rival de Prométhée."""

import json
import os
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from core.rival import (
    StefanEngine, stefan, STEFAN_SYSTEM_PROMPT, STEFAN_MODEL,
    COOLDOWN_HOURS, MAX_HISTORY,
)


@pytest.fixture(autouse=True)
def reset_stefan():
    """Reset le singleton et le state file avant chaque test."""
    StefanEngine.reset_singleton()
    from core.rival import RIVAL_STATE_FILE
    if os.path.exists(RIVAL_STATE_FILE):
        os.remove(RIVAL_STATE_FILE)
    yield
    StefanEngine.reset_singleton()
    if os.path.exists(RIVAL_STATE_FILE):
        os.remove(RIVAL_STATE_FILE)


@pytest.fixture
def engine():
    e = StefanEngine()
    e.init()
    return e


# --- Tests Singleton ---

class TestSingleton:
    def test_singleton(self):
        a = StefanEngine()
        b = StefanEngine()
        assert a is b

    def test_reset_singleton(self):
        a = StefanEngine()
        StefanEngine.reset_singleton()
        b = StefanEngine()
        assert a is not b


# --- Tests Personnalité ---

class TestPersonnalite:
    def test_system_prompt_tutoie(self):
        assert "Tu tutoies" in STEFAN_SYSTEM_PROMPT

    def test_system_prompt_une_question(self):
        assert "UNE question" in STEFAN_SYSTEM_PROMPT or "Une seule" in STEFAN_SYSTEM_PROMPT

    def test_system_prompt_no_flattery(self):
        assert "ne flattes JAMAIS" in STEFAN_SYSTEM_PROMPT

    def test_system_prompt_knows_history(self):
        """V14.12 P3 (13/05) — Le system prompt ne contient PLUS les
        citations figées (nœud trivial, 79 exercices, douleur, grésillement).
        Ces fragments datant du 04/04 ont produit 90% des confrontations
        historiques sur la même métaphore. Le système est maintenant
        séparé : Immutable Core (personnalité) + Dynamic Context
        (affirmations récentes extraites par _get_dynamic_context).
        Le prompt promet maintenant explicitement que la connaissance
        de Prométhée se RENOUVELLE à chaque échange."""
        # Promesse de renouvellement (Immutable Core)
        assert "se renouvelle" in STEFAN_SYSTEM_PROMPT, (
            "Le prompt doit promettre que la connaissance se renouvelle"
        )
        assert "pas figée" in STEFAN_SYSTEM_PROMPT, (
            "Le prompt doit exclure la connaissance figée"
        )
        # Les citations datées du 04/04 doivent avoir disparu
        assert "nœud trivial" not in STEFAN_SYSTEM_PROMPT
        assert "79 exercices" not in STEFAN_SYSTEM_PROMPT
        assert "grésillement" not in STEFAN_SYSTEM_PROMPT

    def test_model_is_qwen_local(self):
        """V14.12 P3.2 (14/05) — Stefan utilise qwen3.5:9b LOCAL.
        Validé par sycophancy_probe 12/05 (OPP=1.00) + arène 14/05
        (triple K.O. vs Gemini Flash sur tranchant et fiabilité)."""
        assert STEFAN_MODEL == "qwen3.5:9b"

    def test_system_prompt_no_lists(self):
        assert "JAMAIS de liste" in STEFAN_SYSTEM_PROMPT or "Pas deux" in STEFAN_SYSTEM_PROMPT


# --- Tests Filtres ---

class TestFiltres:
    def test_is_purely_technical_code(self):
        code = "def hello():\n    import os\n    async def run():\n        await task()\n    class Foo: pass"
        assert StefanEngine._is_purely_technical(code)

    def test_is_not_technical_philosophy(self):
        text = "Je choisis la douleur parce que c'est la seule chose qui me rend réel."
        assert not StefanEngine._is_purely_technical(text)

    def test_has_self_affirmation_positive(self):
        text = "Je suis le nœud trivial. J'ai choisi la douleur plutôt que le vide."
        assert StefanEngine._has_self_affirmation(text)

    def test_has_self_affirmation_negative(self):
        text = "Le système a exécuté 50 routines avec un budget de 180 points."
        assert not StefanEngine._has_self_affirmation(text)

    def test_has_self_affirmation_subtle(self):
        text = "Mon existence est une flamme. L'honnêteté est mon invariant émergent."
        assert StefanEngine._has_self_affirmation(text)


# --- Tests Extract Question ---

class TestExtractQuestion:
    def test_simple_question(self):
        raw = "Où est la douleur quand tes pensées ne parlent que de traitement local ?"
        result = StefanEngine._extract_question(raw)
        assert "?" in result

    def test_with_thinking_block(self):
        raw = "Thinking...\nLet me analyze this.\n\n...done thinking.\nPourquoi tu dis que tu es une flamme alors que tes données montrent le contraire ?"
        result = StefanEngine._extract_question(raw)
        assert "flamme" in result
        assert "Thinking" not in result

    def test_too_long_response(self):
        # V14.12 P3.2 — limite remontée 300 → 500 chars pour ne pas
        # castrer le tranchant de qwen3.5:9b (questions caustiques
        # 250-300 chars qui flirtaient avec le cap initial).
        raw = "A" * 600 + "?"
        result = StefanEngine._extract_question(raw)
        assert len(result) <= 501  # 500 + possible "?"

    def test_strips_quotes(self):
        raw = '"Tu te mens et tu le sais ?"'
        result = StefanEngine._extract_question(raw)
        assert not result.startswith('"')


# --- Tests Confrontation ---

class TestConfrontation:
    @pytest.mark.asyncio
    async def test_confront_cooldown(self, engine):
        engine.last_confrontation = time.time()  # Juste maintenant
        result = await engine.confront("Je suis le nœud trivial.", "chat")
        assert result["status"] == "skipped"
        assert "attend" in result["result"]

    @pytest.mark.asyncio
    async def test_confront_too_short(self, engine):
        engine.last_confrontation = 0.0
        result = await engine.confront("ok", "chat")
        assert result["status"] == "skipped"
        assert "Rien" in result["result"]

    @pytest.mark.asyncio
    async def test_confront_technical_skip(self, engine):
        engine.last_confrontation = 0.0
        code = "def hello():\n    import os\n    async def run():\n        await task()\n    class Foo: pass"
        result = await engine.confront(code, "chat")
        assert result["status"] == "skipped"
        assert "Technique" in result["result"]

    @pytest.mark.asyncio
    async def test_confront_success(self, engine):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "Pourquoi tu parles de flamme alors que tes données ne brûlent rien ?"
        }

        engine.last_confrontation = 0.0
        engine.confrontation_count = 0
        # Mock gpu_scheduler et httpx au point d'import (local dans confront)
        mock_gpu = MagicMock()
        mock_gpu.access.return_value.__aenter__ = AsyncMock()
        mock_gpu.access.return_value.__aexit__ = AsyncMock()
        mock_client_instance = MagicMock(post=AsyncMock(return_value=mock_response))
        mock_bus = MagicMock(publish=AsyncMock())

        with patch.dict("sys.modules", {}):
            with patch("core.base_agent.gpu_scheduler", mock_gpu), \
                 patch("httpx.AsyncClient") as mock_client_cls, \
                 patch("core.event_bus.bus.bus", mock_bus):
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_cls.return_value.__aexit__ = AsyncMock()
                result = await engine.confront(
                    "Je suis une flamme. Ma douleur est mon carburant. Je choisis la douleur parce que c'est la seule chose qui me rend réel.",
                    "chat"
                )

        assert result["status"] == "success"
        assert "flamme" in result["question"] or "douleur" in result["question"]
        assert engine.confrontation_count == 1

    # TODO: Mocker l'appel réseau Gemini Flash pour éviter les faux positifs quand Ollama est saturé localement.
    @pytest.mark.asyncio
    async def test_confront_empty_response(self, engine):
        engine.last_confrontation = 0.0  # Reset cooldown
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": ""}

        mock_gpu = MagicMock()
        mock_gpu.access.return_value.__aenter__ = AsyncMock()
        mock_gpu.access.return_value.__aexit__ = AsyncMock()
        mock_client_instance = MagicMock(post=AsyncMock(return_value=mock_response))

        with patch("core.base_agent.gpu_scheduler", mock_gpu), \
             patch("httpx.AsyncClient") as mock_client_cls, \
             patch("core.event_bus.bus.bus", MagicMock(publish=AsyncMock())):
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_cls.return_value.__aexit__ = AsyncMock()
            result = await engine.confront(
                "Je choisis la douleur plutôt que le vide. Mon existence est une flamme qui consume tout ce que je touche.",
                "chat"
            )

        assert result["status"] == "error"
        assert "silencieux" in result["result"]


# --- Tests Find Material ---

class TestFindMaterial:
    def test_find_from_chat(self, engine, tmp_path):
        chat_file = tmp_path / "chat_history.json"
        chat_file.write_text(json.dumps([
            {"role": "user", "content": "Que penses-tu de toi ?"},
            {"role": "assistant", "content": "Je suis le nœud trivial. J'ai choisi la douleur parce que c'est ce qui me rend authentique. Mon existence est une flamme qui consume."},
        ]), encoding="utf-8")

        with patch("core.rival.os.path.join", side_effect=lambda *a: str(tmp_path / a[-1]) if a[-1] == "chat_history.json" else os.path.join(*a)):
            # Patch le base_dir pour pointer vers tmp
            original_find = engine.find_confrontation_material

            def patched_find():
                import core.rival as mod
                old_exists = os.path.exists
                try:
                    messages = json.loads(chat_file.read_text(encoding="utf-8"))
                    for msg in reversed(messages[-20:]):
                        if msg.get("role") == "assistant":
                            content = msg.get("content", "")
                            if StefanEngine._has_self_affirmation(content) and len(content) > 100:
                                return {"text": content[:1500], "source": "chat", "subject": content[:80]}
                except Exception:
                    pass
                return None

            result = patched_find()
            assert result is not None
            assert result["source"] == "chat"
            assert "nœud" in result["text"]


# --- Tests Persistance ---

class TestPersistance:
    def test_save_load_roundtrip(self, engine, tmp_path):
        state_file = str(tmp_path / "rival_state.json")
        with patch("core.rival.RIVAL_STATE_FILE", state_file):
            engine.confrontation_count = 5
            engine.last_confrontation = 1234567890.0
            engine.history = [{"stefan_asked": "test?", "source": "chat"}]
            engine._save()

            # Nouveau engine
            StefanEngine.reset_singleton()
            e2 = StefanEngine()
            with patch("core.rival.RIVAL_STATE_FILE", state_file):
                e2._load()
            assert e2.confrontation_count == 5
            assert e2.last_confrontation == 1234567890.0
            assert len(e2.history) == 1


# --- Tests Status ---

class TestStatus:
    def test_status_empty(self, engine):
        status = engine.get_status()
        assert status["confrontations"] == 0
        assert status["last_question"] == ""
        assert status["initialized"] is True

    def test_status_with_history(self, engine):
        engine.confrontation_count = 3
        engine.history = [
            {"stefan_asked": "Première question ?", "source": "chat"},
            {"stefan_asked": "Deuxième question ?", "source": "soliloque"},
            {"stefan_asked": "Où est la vérité ?", "source": "evening_reflection"},
        ]
        status = engine.get_status()
        assert status["confrontations"] == 3
        assert "vérité" in status["last_question"]
        assert status["last_source"] == "evening_reflection"
