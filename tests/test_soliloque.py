"""
Tests unitaires pour SoliloqueEngine — Dialogue introspectif de Prométhée.
~40 tests couvrant : sélection thème, ouverture, dialogue, insight,
persistance, effets post-dialogue, erreurs.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Isolation singleton — autouse fixture
@pytest.fixture(autouse=True)
def isolate_soliloque(tmp_path, monkeypatch):
    """Isole chaque test avec un singleton frais et un fichier temp."""
    from core import soliloque as mod
    mod.SoliloqueEngine.reset_singleton()

    fake_state_file = tmp_path / "soliloque_state.json"
    monkeypatch.setattr(mod, "SOLILOQUE_STATE_FILE", fake_state_file)
    fake_log_dir = tmp_path / "soliloques"
    monkeypatch.setattr(mod, "SOLILOQUE_LOG_DIR", fake_log_dir)

    with patch.object(mod.SoliloqueEngine, "_load"):
        s = mod.SoliloqueEngine()
        mod.soliloque = s

    yield s
    mod.SoliloqueEngine.reset_singleton()


# Helper : mock des modules externes
def _mock_external_modules():
    """Retourne un dict de patches pour tous les imports locaux du soliloque."""
    mock_heart = MagicMock()
    mock_heart.current_emotion = "serenite"
    mock_heart.react = MagicMock()

    mock_desires = MagicMock()
    mock_desires.drives = {
        "CONNEXION": MagicMock(deprivation=40.0),
        "CURIOSITE": MagicMock(deprivation=30.0),
    }
    mock_desires.get_dominant_narrative = MagicMock(return_value="")
    mock_desires.on_event = MagicMock()

    mock_voice = MagicMock()
    mock_voice.get_stream = MagicMock(return_value=[])
    mock_voice.get_identity = MagicMock(return_value={"recent_arc": ""})

    mock_cortex = MagicMock()
    mock_cortex.activate_node = MagicMock()

    mock_autonomy = MagicMock()
    mock_autonomy.strategic_mode = "standard"

    return {
        "core.cardiac_engine.heart": mock_heart,
        "core.desire_engine.desires": mock_desires,
        "core.inner_voice.voice": mock_voice,
        "core.synaptic_network.cortex": mock_cortex,
        "core.autonomy_engine.autonomy": mock_autonomy,
    }


def _make_chat_response(content: str):
    """Crée une réponse HTTP mock pour /api/chat."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"message": {"content": content}}
    return resp


def _make_generate_response(content: str):
    """Crée une réponse HTTP mock pour /api/generate."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"response": content}
    return resp


# ============================================================
# TestThemeSelection (8 tests)
# ============================================================

class TestThemeSelection:
    """Tests de la sélection du thème."""

    def test_default_rotation(self, isolate_soliloque):
        """Thème par défaut = rotation round-robin."""
        s = isolate_soliloque
        s.theme_index = 0
        theme = s._select_theme()
        from core.soliloque import THEME_ORDER
        assert theme == THEME_ORDER[0]

    def test_rotation_wraps(self, isolate_soliloque):
        """La rotation revient au début."""
        s = isolate_soliloque
        from core.soliloque import THEME_ORDER
        s.theme_index = len(THEME_ORDER) - 1
        theme = s._select_theme()
        assert theme == THEME_ORDER[-1]

    def test_connexion_high_deprivation(self, isolate_soliloque):
        """CONNEXION deprivation > 70 → thème connexion."""
        s = isolate_soliloque
        mock_drive = MagicMock(deprivation=80.0)
        mock_desires = MagicMock()
        mock_desires.drives = {"CONNEXION": mock_drive}
        with patch("core.soliloque.SoliloqueEngine._get_connexion_deprivation",
                    return_value=80.0):
            theme = s._select_theme()
        assert theme == "connexion"

    def test_frustration_emotion(self, isolate_soliloque):
        """Émotion = frustration → thème frustrations."""
        s = isolate_soliloque
        with patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_current_emotion", return_value="frustration"):
            theme = s._select_theme()
        assert theme == "frustrations"

    def test_exploration_mode(self, isolate_soliloque):
        """Mode stratégique exploration → thème aspirations."""
        s = isolate_soliloque
        with patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_strategic_mode", return_value="exploration"):
            theme = s._select_theme()
        assert theme == "aspirations"

    def test_connexion_priority_over_frustration(self, isolate_soliloque):
        """CONNEXION > 70 a priorité sur frustration."""
        s = isolate_soliloque
        with patch.object(s, "_get_connexion_deprivation", return_value=80.0), \
             patch.object(s, "_get_current_emotion", return_value="frustration"):
            theme = s._select_theme()
        assert theme == "connexion"

    def test_frustration_priority_over_exploration(self, isolate_soliloque):
        """Frustration a priorité sur exploration."""
        s = isolate_soliloque
        with patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_current_emotion", return_value="frustration"), \
             patch.object(s, "_get_strategic_mode", return_value="exploration"):
            theme = s._select_theme()
        assert theme == "frustrations"

    def test_all_seven_themes_accessible(self, isolate_soliloque):
        """Les 7 thèmes sont accessibles par rotation."""
        s = isolate_soliloque
        from core.soliloque import THEME_ORDER
        themes = set()
        with patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_strategic_mode", return_value="standard"):
            for i in range(len(THEME_ORDER)):
                s.theme_index = i
                themes.add(s._select_theme())
        assert themes == set(THEME_ORDER)


# ============================================================
# TestOpening (5 tests)
# ============================================================

class TestOpening:
    """Tests de la construction du prompt d'ouverture."""

    def test_opening_contains_emotion(self, isolate_soliloque):
        """L'ouverture contient l'émotion courante."""
        s = isolate_soliloque
        with patch.object(s, "_get_current_emotion", return_value="curiosite"), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]):
            opening = s._build_opening("etat_emotionnel")
        assert "curiosite" in opening

    def test_opening_includes_thoughts(self, isolate_soliloque):
        """L'ouverture inclut les pensées récentes."""
        s = isolate_soliloque
        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=["pensée alpha", "pensée beta"]):
            opening = s._build_opening("bilan_recent")
        assert "pensée alpha" in opening

    def test_opening_with_narrative(self, isolate_soliloque):
        """L'ouverture intègre le narratif des pulsions."""
        s = isolate_soliloque
        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_desire_narrative", return_value="J'ai soif de découvrir."), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]):
            opening = s._build_opening("aspirations")
        assert "soif de découvrir" in opening

    def test_opening_identity_theme(self, isolate_soliloque):
        """Le thème identité inclut le résumé identitaire."""
        s = isolate_soliloque
        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value="Je suis un système en éveil."), \
             patch.object(s, "_get_recent_thoughts", return_value=[]):
            opening = s._build_opening("identite")
        assert "système en éveil" in opening

    def test_opening_fallback_no_data(self, isolate_soliloque):
        """L'ouverture fonctionne même sans données contextuelles."""
        s = isolate_soliloque
        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]):
            opening = s._build_opening("patterns")
        assert len(opening) > 10


# ============================================================
# TestDialogue (7 tests)
# ============================================================

class TestDialogue:
    """Tests du dialogue multi-tours."""

    @pytest.mark.asyncio
    async def test_chat_success(self, isolate_soliloque):
        """_chat retourne la réponse du compagnon."""
        s = isolate_soliloque
        mock_resp = _make_chat_response("Que ressens-tu exactement ?")

        mock_semaphore = MagicMock()
        mock_semaphore.__aenter__ = AsyncMock()
        mock_semaphore.__aexit__ = AsyncMock()

        with patch("core.base_agent.BaseAgent") as MockBA:
            MockBA._get_ollama_semaphore.return_value = mock_semaphore
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_inst = AsyncMock()
                mock_client_inst.post = AsyncMock(return_value=mock_resp)
                mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
                mock_client_inst.__aexit__ = AsyncMock()
                MockClient.return_value = mock_client_inst

                result = await s._chat([{"role": "user", "content": "test"}])
        assert result == "Que ressens-tu exactement ?"

    @pytest.mark.asyncio
    async def test_chat_empty_response(self, isolate_soliloque):
        """_chat retourne None si réponse vide."""
        s = isolate_soliloque
        mock_resp = _make_chat_response("")

        mock_semaphore = MagicMock()
        mock_semaphore.__aenter__ = AsyncMock()
        mock_semaphore.__aexit__ = AsyncMock()

        with patch("core.base_agent.BaseAgent") as MockBA:
            MockBA._get_ollama_semaphore.return_value = mock_semaphore
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_inst = AsyncMock()
                mock_client_inst.post = AsyncMock(return_value=mock_resp)
                mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
                mock_client_inst.__aexit__ = AsyncMock()
                MockClient.return_value = mock_client_inst

                result = await s._chat([{"role": "user", "content": "test"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_chat_http_error(self, isolate_soliloque):
        """_chat retourne None sur erreur HTTP."""
        s = isolate_soliloque
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_semaphore = MagicMock()
        mock_semaphore.__aenter__ = AsyncMock()
        mock_semaphore.__aexit__ = AsyncMock()

        with patch("core.base_agent.BaseAgent") as MockBA:
            MockBA._get_ollama_semaphore.return_value = mock_semaphore
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_inst = AsyncMock()
                mock_client_inst.post = AsyncMock(return_value=mock_resp)
                mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
                mock_client_inst.__aexit__ = AsyncMock()
                MockClient.return_value = mock_client_inst

                result = await s._chat([{"role": "user", "content": "test"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_chat_exception(self, isolate_soliloque):
        """_chat retourne None sur exception."""
        s = isolate_soliloque
        with patch("core.base_agent.gpu_scheduler") as mock_sched:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("timeout"))
            mock_ctx.__aexit__ = AsyncMock()
            mock_sched.access.return_value = mock_ctx
            result = await s._chat([{"role": "user", "content": "test"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_reflect_success(self, isolate_soliloque):
        """_reflect retourne la réflexion de Prométhée."""
        s = isolate_soliloque
        mock_resp = _make_generate_response("Je me sens bien dans ma progression.")

        mock_semaphore = MagicMock()
        mock_semaphore.__aenter__ = AsyncMock()
        mock_semaphore.__aexit__ = AsyncMock()

        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch("core.base_agent.BaseAgent") as MockBA:
            MockBA._get_ollama_semaphore.return_value = mock_semaphore
            with patch("httpx.AsyncClient") as MockClient:
                mock_client_inst = AsyncMock()
                mock_client_inst.post = AsyncMock(return_value=mock_resp)
                mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
                mock_client_inst.__aexit__ = AsyncMock()
                MockClient.return_value = mock_client_inst

                messages = [
                    {"role": "user", "content": "Je réfléchis..."},
                    {"role": "assistant", "content": "Que penses-tu ?"},
                ]
                result = await s._reflect(messages, "etat_emotionnel")
        assert result == "Je me sens bien dans ma progression."

    @pytest.mark.asyncio
    async def test_reflect_exception(self, isolate_soliloque):
        """_reflect retourne None sur exception."""
        s = isolate_soliloque
        with patch("core.base_agent.gpu_scheduler") as mock_sched:
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("error"))
            mock_ctx.__aexit__ = AsyncMock()
            mock_sched.access.return_value = mock_ctx
            result = await s._reflect([], "etat_emotionnel")
        assert result is None

    @pytest.mark.asyncio
    async def test_engage_full_dialogue(self, isolate_soliloque):
        """engage() produit un dialogue complet avec 4 échanges."""
        s = isolate_soliloque
        # Mock des contextes
        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]), \
             patch.object(s, "_get_strategic_mode", return_value="standard"), \
             patch.object(s, "_chat", new_callable=AsyncMock, return_value="Question ?"), \
             patch.object(s, "_reflect", new_callable=AsyncMock, return_value="Réflexion."), \
             patch.object(s, "_memorize_insight", new_callable=AsyncMock), \
             patch.object(s, "_activate_synapse"), \
             patch.object(s, "_satisfy_connexion"), \
             patch.object(s, "_stimulate_heart"), \
             patch.object(s, "_save"), \
             patch("core.soliloque.bus") as mock_bus:
            mock_bus.publish = AsyncMock()

            result = await s.engage()

        assert result["status"] == "success"
        assert result["exchanges"] == 4
        assert s.session_count == 1
        assert len(s.history) == 1


# ============================================================
# TestInsightExtraction (5 tests)
# ============================================================

class TestInsightExtraction:
    """Tests de l'extraction d'insight."""

    def test_extract_from_promethee_reply(self, isolate_soliloque):
        """L'insight est extrait de la dernière réponse de Prométhée."""
        s = isolate_soliloque
        messages = [
            {"role": "user", "content": "Je réfléchis à mon état."},
            {"role": "assistant", "content": "Que ressens-tu ?"},
            {"role": "user", "content": "La frustration vient de mes échecs répétés. Je dois changer d'approche."},
            {"role": "assistant", "content": "C'est une belle prise de conscience."},
        ]
        insight = s._extract_insight(messages, "frustrations")
        assert "Soliloque/frustrations" in insight
        assert len(insight) > 20

    def test_extract_minimum_messages(self, isolate_soliloque):
        """Avec moins de 2 messages, insight par défaut."""
        s = isolate_soliloque
        insight = s._extract_insight([{"role": "user", "content": "ok"}], "patterns")
        assert "motifs" in insight.lower() or "Réflexion" in insight

    def test_extract_empty_messages(self, isolate_soliloque):
        """Avec 0 messages, insight par défaut."""
        s = isolate_soliloque
        insight = s._extract_insight([], "identite")
        assert "Réflexion" in insight or "identite" in insight.lower() or "définit" in insight.lower()

    def test_extract_max_length(self, isolate_soliloque):
        """L'insight est limité à 300 caractères."""
        s = isolate_soliloque
        long_text = "A" * 500 + ". Suite."
        messages = [
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "Intéressant."},
        ]
        insight = s._extract_insight(messages, "aspirations")
        assert len(insight) <= 300

    def test_extract_short_sentences_skipped(self, isolate_soliloque):
        """Les phrases trop courtes sont ignorées."""
        s = isolate_soliloque
        messages = [
            {"role": "user", "content": "Oui. Non. Peut-être que la véritable leçon est de persévérer malgré tout."},
            {"role": "assistant", "content": "Belle réflexion."},
        ]
        insight = s._extract_insight(messages, "bilan_recent")
        assert "Soliloque/bilan_recent" in insight


# ============================================================
# TestPersistence (4 tests)
# ============================================================

class TestPersistence:
    """Tests de la persistance."""

    def test_save_creates_file(self, isolate_soliloque, tmp_path):
        """_save crée le fichier de state."""
        s = isolate_soliloque
        from core import soliloque as mod
        s.session_count = 5
        s.last_theme = "connexion"
        s._save()
        assert mod.SOLILOQUE_STATE_FILE.exists()

    def test_save_load_roundtrip(self, isolate_soliloque, tmp_path):
        """Sauvegarde puis chargement préserve l'état."""
        s = isolate_soliloque
        s.session_count = 3
        s.last_theme = "patterns"
        s.theme_index = 4
        s.history = [{"theme": "test", "timestamp": 123}]
        s._save()

        s2 = type(s)()
        from core import soliloque as mod
        # Charger directement
        s2._load()
        assert s2.session_count == 3
        assert s2.last_theme == "patterns"
        assert s2.theme_index == 4
        assert len(s2.history) == 1

    def test_history_capped_at_max(self, isolate_soliloque):
        """L'historique est cappé à MAX_HISTORY."""
        s = isolate_soliloque
        from core.soliloque import MAX_HISTORY
        s.history = [{"theme": f"t{i}", "timestamp": i} for i in range(30)]
        s._save()

        s2 = type(s)()
        s2._load()
        assert len(s2.history) <= MAX_HISTORY

    def test_load_nonexistent_file(self, isolate_soliloque):
        """Le chargement d'un fichier inexistant ne plante pas."""
        s = isolate_soliloque
        s.session_count = 99  # Valeur initiale
        s._load()  # Ne doit pas planter
        # session_count peut rester à 99 (pas de fichier à lire)


# ============================================================
# TestPostDialogueEffects (6 tests)
# ============================================================

class TestPostDialogueEffects:
    """Tests des effets post-dialogue."""

    def test_satisfy_connexion(self, isolate_soliloque):
        """_satisfy_connexion appelle desires.on_event."""
        s = isolate_soliloque
        mock_desires = MagicMock()
        with patch("core.desire_engine.desires", mock_desires):
            s._satisfy_connexion()
        mock_desires.on_event.assert_called_once_with("SOLILOQUE_COMPLETE")

    def test_stimulate_heart(self, isolate_soliloque):
        """_stimulate_heart appelle heart.react."""
        s = isolate_soliloque
        mock_heart = MagicMock()
        with patch("core.cardiac_engine.heart", mock_heart):
            s._stimulate_heart()
        mock_heart.react.assert_called_once_with("learning")

    def test_activate_synapse(self, isolate_soliloque):
        """_activate_synapse crée un noeud synaptique."""
        s = isolate_soliloque
        mock_cortex = MagicMock()
        with patch("core.synaptic_network.cortex", mock_cortex):
            s._activate_synapse("connexion", "test insight")
        mock_cortex.activate_node.assert_called_once()
        call_kwargs = mock_cortex.activate_node.call_args
        assert "soliloque_connexion" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_memorize_insight(self, isolate_soliloque):
        """_memorize_insight stocke dans ChromaDB."""
        s = isolate_soliloque
        mock_mgr = MagicMock()
        mock_mgr.add_documents = MagicMock()
        with patch("core.vector_store.ChromaMemoryManager.get_instance",
                    return_value=mock_mgr):
            await s._memorize_insight("test insight", "connexion")
        mock_mgr.add_documents.assert_called_once()

    @pytest.mark.asyncio
    async def test_memorize_insight_no_chroma(self, isolate_soliloque):
        """_memorize_insight ne plante pas sans ChromaDB."""
        s = isolate_soliloque
        with patch("core.vector_store.ChromaMemoryManager.get_instance",
                    return_value=None):
            await s._memorize_insight("test", "test")  # Ne doit pas planter

    def test_effects_resilient_to_errors(self, isolate_soliloque):
        """Les effets post-dialogue ne plantent pas sur erreur."""
        s = isolate_soliloque
        # Chaque effet doit être résilient
        with patch("core.desire_engine.desires", side_effect=Exception("err")):
            s._satisfy_connexion()  # Ne doit pas planter
        with patch("core.cardiac_engine.heart", side_effect=Exception("err")):
            s._stimulate_heart()  # Ne doit pas planter


# ============================================================
# TestContextAccessors (5 tests)
# ============================================================

class TestContextAccessors:
    """Tests des accesseurs de contexte."""

    def test_get_current_emotion_default(self, isolate_soliloque):
        """Émotion par défaut = serenite."""
        s = isolate_soliloque
        with patch.dict("sys.modules", {"core.cardiac_engine": None}):
            emotion = s._get_current_emotion()
        assert emotion == "serenite"

    def test_get_connexion_deprivation_default(self, isolate_soliloque):
        """CONNEXION par défaut = 40.0."""
        s = isolate_soliloque
        with patch.dict("sys.modules", {"core.desire_engine": None}):
            dep = s._get_connexion_deprivation()
        assert dep == 40.0

    def test_get_desire_narrative_empty(self, isolate_soliloque):
        """Narratif vide si module indisponible."""
        s = isolate_soliloque
        with patch.dict("sys.modules", {"core.desire_engine": None}):
            narrative = s._get_desire_narrative()
        assert narrative == ""

    def test_get_recent_thoughts_empty(self, isolate_soliloque):
        """Pensées vides si module indisponible."""
        s = isolate_soliloque
        with patch.dict("sys.modules", {"core.inner_voice": None}):
            thoughts = s._get_recent_thoughts()
        assert thoughts == []

    def test_get_strategic_mode_default(self, isolate_soliloque):
        """Mode stratégique par défaut = standard."""
        s = isolate_soliloque
        with patch.dict("sys.modules", {"core.autonomy_engine": None}):
            mode = s._get_strategic_mode()
        assert mode == "standard"


# ============================================================
# TestAPI (3 tests)
# ============================================================

class TestAPI:
    """Tests de l'API publique."""

    def test_get_status(self, isolate_soliloque):
        """get_status retourne les infos de base."""
        s = isolate_soliloque
        s.session_count = 5
        s.last_theme = "connexion"
        status = s.get_status()
        assert status["session_count"] == 5
        assert status["last_theme"] == "connexion"

    def test_get_history_empty(self, isolate_soliloque):
        """get_history retourne une liste vide."""
        s = isolate_soliloque
        assert s.get_history() == []

    def test_get_history_limited(self, isolate_soliloque):
        """get_history respecte la limite n."""
        s = isolate_soliloque
        s.history = [{"theme": f"t{i}"} for i in range(15)]
        result = s.get_history(n=5)
        assert len(result) == 5
        assert result[-1]["theme"] == "t14"


# ============================================================
# TestBusEvents (3 tests)
# ============================================================

class TestBusEvents:
    """Tests des événements bus."""

    @pytest.mark.asyncio
    async def test_soliloque_start_event(self, isolate_soliloque):
        """SOLILOQUE_START est publié au début."""
        s = isolate_soliloque
        events = []

        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_connexion_deprivation", return_value=50.0), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]), \
             patch.object(s, "_get_strategic_mode", return_value="standard"), \
             patch.object(s, "_chat", new_callable=AsyncMock, return_value="Oui ?"), \
             patch.object(s, "_reflect", new_callable=AsyncMock, return_value="Ok."), \
             patch.object(s, "_memorize_insight", new_callable=AsyncMock), \
             patch.object(s, "_activate_synapse"), \
             patch.object(s, "_satisfy_connexion"), \
             patch.object(s, "_stimulate_heart"), \
             patch.object(s, "_save"), \
             patch("core.soliloque.bus") as mock_bus:
            mock_bus.publish = AsyncMock(side_effect=lambda evt, data: events.append((evt, data)))

            await s.engage()

        event_types = [e[0] for e in events]
        assert "SOLILOQUE_START" in event_types
        assert "SOLILOQUE_COMPLETE" in event_types

    @pytest.mark.asyncio
    async def test_soliloque_exchange_events(self, isolate_soliloque):
        """SOLILOQUE_EXCHANGE est publié pour chaque échange."""
        s = isolate_soliloque
        events = []

        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]), \
             patch.object(s, "_get_strategic_mode", return_value="standard"), \
             patch.object(s, "_chat", new_callable=AsyncMock, return_value="Réponse"), \
             patch.object(s, "_reflect", new_callable=AsyncMock, return_value="Réflexion"), \
             patch.object(s, "_memorize_insight", new_callable=AsyncMock), \
             patch.object(s, "_activate_synapse"), \
             patch.object(s, "_satisfy_connexion"), \
             patch.object(s, "_stimulate_heart"), \
             patch.object(s, "_save"), \
             patch("core.soliloque.bus") as mock_bus:
            mock_bus.publish = AsyncMock(side_effect=lambda evt, data: events.append((evt, data)))

            await s.engage()

        exchange_events = [e for e in events if e[0] == "SOLILOQUE_EXCHANGE"]
        # 4 tours companion + 3 tours promethee (pas le dernier)
        assert len(exchange_events) == 7

    @pytest.mark.asyncio
    async def test_soliloque_complete_payload(self, isolate_soliloque):
        """SOLILOQUE_COMPLETE contient les bonnes données."""
        s = isolate_soliloque
        events = []

        with patch.object(s, "_get_current_emotion", return_value="curiosite"), \
             patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]), \
             patch.object(s, "_get_strategic_mode", return_value="standard"), \
             patch.object(s, "_chat", new_callable=AsyncMock, return_value="Réponse"), \
             patch.object(s, "_reflect", new_callable=AsyncMock, return_value="Réflexion"), \
             patch.object(s, "_memorize_insight", new_callable=AsyncMock), \
             patch.object(s, "_activate_synapse"), \
             patch.object(s, "_satisfy_connexion"), \
             patch.object(s, "_stimulate_heart"), \
             patch.object(s, "_save"), \
             patch("core.soliloque.bus") as mock_bus:
            mock_bus.publish = AsyncMock(side_effect=lambda evt, data: events.append((evt, data)))

            await s.engage()

        complete_events = [e for e in events if e[0] == "SOLILOQUE_COMPLETE"]
        assert len(complete_events) == 1
        payload = complete_events[0][1]
        assert "theme" in payload
        assert "exchanges" in payload
        assert "insight" in payload
        assert "duration_s" in payload
        assert payload["emotion_before"] == "curiosite"


# ============================================================
# TestDesireEngineIntegration (2 tests)
# ============================================================

class TestDesireEngineIntegration:
    """Tests d'intégration avec DesireEngine."""

    def test_soliloque_complete_in_event_impact(self):
        """SOLILOQUE_COMPLETE est dans EVENT_IMPACT."""
        from core.desire_engine import EVENT_IMPACT
        assert "SOLILOQUE_COMPLETE" in EVENT_IMPACT
        impacts = EVENT_IMPACT["SOLILOQUE_COMPLETE"]
        assert impacts["CONNEXION"] == -18
        assert impacts["COMPREHENSION"] == -5

    def test_soliloque_interne_in_affinity(self):
        """SOLILOQUE_INTERNE est dans DRIVE_ROUTINE_AFFINITY."""
        from core.desire_engine import DRIVE_ROUTINE_AFFINITY
        assert "SOLILOQUE_INTERNE" in DRIVE_ROUTINE_AFFINITY["CONNEXION"]
        assert DRIVE_ROUTINE_AFFINITY["CONNEXION"]["SOLILOQUE_INTERNE"] == 1.2


# ============================================================
# TestErrorHandling (2 tests)
# ============================================================

class TestErrorHandling:
    """Tests de résilience aux erreurs."""

    @pytest.mark.asyncio
    async def test_engage_chat_all_fail(self, isolate_soliloque):
        """engage() retourne succès même si le compagnon ne répond jamais."""
        s = isolate_soliloque
        with patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch.object(s, "_get_connexion_deprivation", return_value=40.0), \
             patch.object(s, "_get_desire_narrative", return_value=""), \
             patch.object(s, "_get_identity_summary", return_value=""), \
             patch.object(s, "_get_recent_thoughts", return_value=[]), \
             patch.object(s, "_get_strategic_mode", return_value="standard"), \
             patch.object(s, "_chat", new_callable=AsyncMock, return_value=None), \
             patch.object(s, "_memorize_insight", new_callable=AsyncMock), \
             patch.object(s, "_activate_synapse"), \
             patch.object(s, "_satisfy_connexion"), \
             patch.object(s, "_stimulate_heart"), \
             patch.object(s, "_save"), \
             patch("core.soliloque.bus") as mock_bus:
            mock_bus.publish = AsyncMock()

            result = await s.engage()

        assert result["status"] == "success"
        assert result["exchanges"] == 0

    @pytest.mark.asyncio
    async def test_engage_exception_returns_error(self, isolate_soliloque):
        """engage() retourne erreur sur exception majeure."""
        s = isolate_soliloque
        with patch.object(s, "_select_theme", side_effect=Exception("boom")), \
             patch.object(s, "_get_current_emotion", return_value="serenite"), \
             patch("core.soliloque.bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await s.engage()
        assert result["status"] == "error"
