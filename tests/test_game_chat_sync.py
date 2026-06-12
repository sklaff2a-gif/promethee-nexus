# tests/test_game_chat_sync.py — Sync chat salle de jeux <-> chat principal (12/06)
#
# Demande JM : la conversation pendant une partie doit faire partie de la
# memoire vive du chat principal (pas seulement de l'archive de fin de
# partie), et le chat principal doit savoir qu'une partie est en cours.
# Choix assume : seul le DIALOGUE est synchronise — la telemetrie des coups
# (intentions echecs, commentaires automatiques) reste dans le panneau jeu.
import pytest
from unittest.mock import patch, AsyncMock

from core.games.game_hub import GameHub
from core.chat_engine import ChatEngine


@pytest.fixture(autouse=True)
def reset_all(tmp_path, monkeypatch):
    import core.games.game_hub as hub_mod
    monkeypatch.setattr(hub_mod, "STATE_FILE", str(tmp_path / "game_state.json"))
    GameHub.reset_singleton()
    ChatEngine.reset_singleton()
    yield
    GameHub.reset_singleton()
    ChatEngine.reset_singleton()


@pytest.fixture
def engine():
    with patch.object(ChatEngine, "_load"):
        e = ChatEngine()
    e.messages = []
    return e


@pytest.fixture
def hub_en_partie():
    hub = GameHub()
    hub.new_game("morpion", opponent="human", promethee_starts=False)
    return hub


class TestSyncDialogue:

    @pytest.mark.asyncio
    async def test_message_humain_persiste_dans_chat_principal(self, engine, hub_en_partie):
        with patch.object(ChatEngine, "_trim_and_save"), \
             patch.object(GameHub, "_promethee_chat_llm", new_callable=AsyncMock,
                          return_value="Bien tente !"):
            await hub_en_partie.game_say("human", "Tu vas perdre cette fois.")
        users = [m for m in engine.messages if m.get("role") == "user"]
        assert len(users) == 1
        assert users[0]["badge"] == "game_chat"
        assert "[salle de jeux — morpion]" in users[0]["content"]
        assert "Tu vas perdre cette fois." in users[0]["content"]

    @pytest.mark.asyncio
    async def test_reponse_promethee_persiste_avec_source_emergente(self, engine, hub_en_partie):
        with patch.object(ChatEngine, "_trim_and_save"), \
             patch.object(GameHub, "_promethee_chat_llm", new_callable=AsyncMock,
                          return_value="On verra bien."):
            await hub_en_partie.game_say("human", "Pret ?")
        bots = [m for m in engine.messages if m.get("role") == "assistant"]
        assert len(bots) == 1
        assert bots[0]["content"] == "On verra bien."
        assert bots[0]["emergent_sources"] == ["salle_de_jeux"]

    @pytest.mark.asyncio
    async def test_live_events_publies(self, engine, hub_en_partie):
        published = []

        async def spy(event_type, payload):
            published.append((event_type, payload))

        with patch.object(ChatEngine, "_trim_and_save"), \
             patch.object(GameHub, "_promethee_chat_llm", new_callable=AsyncMock,
                          return_value="Je suis pret."), \
             patch("core.event_bus.bus.bus.publish", side_effect=spy):
            await hub_en_partie.game_say("human", "On joue ?")
        types = [t for t, _ in published]
        assert "GAME_CHAT_USER" in types
        assert types.count("CHAT_STREAM") == 3  # start + chunk + done
        chunk = next(p for t, p in published if t == "CHAT_STREAM" and "chunk" in p)
        assert chunk["chunk"] == "Je suis pret."

    def test_telemetrie_des_coups_non_synchronisee(self, engine, hub_en_partie):
        """Un coup j. avec commentaire automatique ne doit RIEN ecrire dans
        le chat principal (seul le dialogue est synchronise)."""
        hub_en_partie.play_move([0, 0], player="human")  # riposte morpion + comment auto
        assert engine.messages == []

    @pytest.mark.asyncio
    async def test_sync_robuste_si_chat_engine_indisponible(self, hub_en_partie):
        """Si le chat principal est inaccessible, le chat de jeu survit."""
        with patch.object(GameHub, "_promethee_chat_llm", new_callable=AsyncMock,
                          return_value="ok"), \
             patch("core.chat_engine.ChatEngine", side_effect=RuntimeError("down")):
            out = await hub_en_partie.game_say("human", "hello")
        assert out["status"] == "ok"


class TestPayloadLLMJeu:

    @pytest.mark.asyncio
    async def test_think_false_obligatoire(self, hub_en_partie):
        """Regression : sans think=False, gemma4 consomme num_predict en
        thinking -> response vide (chat de jeu MUET du 08 au 12/06)."""
        captured = {}

        class FakeResp:
            status_code = 200
            def json(self):
                return {"response": "Bien recu."}

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, timeout=None):
                captured.update(json or {})
                return FakeResp()

        import httpx
        with patch.object(httpx, "AsyncClient", FakeClient):
            reply = await hub_en_partie._promethee_chat_llm("salut")
        assert reply == "Bien recu."
        assert captured.get("think") is False


class TestConscienceDePartie:

    def test_system_prompt_mentionne_la_partie(self, engine, hub_en_partie):
        prompt = engine._build_system_prompt()
        assert "PARTIE EN COURS" in prompt
        assert "morpion" in prompt

    def test_system_prompt_sans_partie(self, engine):
        GameHub.reset_singleton()
        import core.games.game_hub as hub_mod
        hub_mod.game_hub = GameHub()  # singleton frais sans session
        prompt = engine._build_system_prompt()
        assert "PARTIE EN COURS" not in prompt
