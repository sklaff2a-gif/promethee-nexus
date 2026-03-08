"""Tests pour core/proactive_chat.py — Notifications proactives."""

import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from core.proactive_chat import ProactiveChat, GLOBAL_COOLDOWN, DAILY_MAX, TYPE_COOLDOWNS


@pytest.fixture(autouse=True)
def reset_proactive():
    """Reset le singleton entre chaque test."""
    ProactiveChat.reset_singleton()
    pc = ProactiveChat()
    pc.init()
    yield pc
    ProactiveChat.reset_singleton()


# ═══════════════════════════════════════════════════════════════════
#  TestCooldowns
# ═══════════════════════════════════════════════════════════════════

class TestCooldowns:
    """Tests des mécanismes anti-spam."""

    def test_global_cooldown_blocks(self, reset_proactive):
        """2ème message dans les 30min → bloqué."""
        pc = reset_proactive
        # Simuler un message récent
        pc._last_send_time = time.time() - 60  # Il y a 1 min
        assert pc._should_send("insight") is False

    def test_global_cooldown_expired_allows(self, reset_proactive):
        """Message après expiration du cooldown global → OK."""
        pc = reset_proactive
        pc._last_send_time = time.time() - GLOBAL_COOLDOWN - 1
        with patch("core.proactive_chat._now_hour", return_value=14):
            assert pc._should_send("insight") is True

    def test_per_type_cooldown(self, reset_proactive):
        """Même type dans le cooldown spécifique → bloqué."""
        pc = reset_proactive
        pc._last_send_time = time.time() - GLOBAL_COOLDOWN - 1  # Global OK
        pc._type_last_send["insight"] = time.time() - 60  # Type: récent
        with patch("core.proactive_chat._now_hour", return_value=14):
            assert pc._should_send("insight") is False

    def test_different_type_allowed(self, reset_proactive):
        """Type différent, cooldown global passé → OK."""
        pc = reset_proactive
        pc._last_send_time = time.time() - GLOBAL_COOLDOWN - 1
        pc._type_last_send["insight"] = time.time() - 60  # Insight récent
        with patch("core.proactive_chat._now_hour", return_value=14):
            # "alerte" n'a pas de cooldown actif
            assert pc._should_send("alerte") is True

    def test_daily_max_reached(self, reset_proactive):
        """7ème message du jour → bloqué."""
        pc = reset_proactive
        pc._daily_date = time.strftime("%Y-%m-%d")
        pc._daily_count = DAILY_MAX
        pc._last_send_time = time.time() - GLOBAL_COOLDOWN - 1
        with patch("core.proactive_chat._now_hour", return_value=14):
            assert pc._should_send("insight") is False

    def test_night_mode_blocks(self, reset_proactive):
        """Message entre 23h-7h → bloqué."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=2):
            assert pc._should_send("insight") is False

    def test_night_23h_blocks(self, reset_proactive):
        """23h exactement → bloqué."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=23):
            assert pc._should_send("insight") is False

    def test_day_mode_allows(self, reset_proactive):
        """Message à 14h → OK."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14):
            assert pc._should_send("insight") is True


# ═══════════════════════════════════════════════════════════════════
#  TestTriggers
# ═══════════════════════════════════════════════════════════════════

class TestTriggers:
    """Tests des handlers bus."""

    @pytest.mark.asyncio
    async def test_dmn_insight_queues_message(self, reset_proactive):
        """Event DMN_INSIGHT → message en queue."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_dmn_insight({"insight": "Le pattern X relie Y et Z"})
        assert len(pc._queue) == 1
        assert "INSIGHT" in pc._queue[0]["text"]
        assert "Le pattern X relie Y et Z" in pc._queue[0]["text"]

    @pytest.mark.asyncio
    async def test_reptilian_fight_queues_alert(self, reset_proactive):
        """REPTILIAN_ALERT mode FIGHT → message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_reptilian_alert({
                "mode": "FIGHT", "threat_level": 8.5, "adrenaline": 0.9
            })
        assert len(pc._queue) == 1
        assert "ALERTE" in pc._queue[0]["text"]
        assert "FIGHT" in pc._queue[0]["text"]

    @pytest.mark.asyncio
    async def test_reptilian_low_mode_ignored(self, reset_proactive):
        """REPTILIAN_ALERT mode VIGILANCE → pas de message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14):
            await pc._on_reptilian_alert({"mode": "VIGILANCE", "threat_level": 3.0})
        assert len(pc._queue) == 0

    @pytest.mark.asyncio
    async def test_ollama_unresponsive_queues(self, reset_proactive):
        """OLLAMA_UNRESPONSIVE → message alerte."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_ollama_unresponsive({})
        assert len(pc._queue) == 1
        assert "Ollama" in pc._queue[0]["text"]

    @pytest.mark.asyncio
    async def test_routine_success_queues(self, reset_proactive):
        """EXPANSION_CODE success → message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_routine_complete({
                "status": "success", "intent": "EXPANSION_CODE", "quality": 85
            })
        assert len(pc._queue) == 1
        assert "RÉSULTAT" in pc._queue[0]["text"]
        assert "EXPANSION_CODE" in pc._queue[0]["text"]

    @pytest.mark.asyncio
    async def test_routine_failure_ignored(self, reset_proactive):
        """Status error → pas de message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14):
            await pc._on_routine_complete({
                "status": "error", "intent": "EXPANSION_CODE"
            })
        assert len(pc._queue) == 0

    @pytest.mark.asyncio
    async def test_routine_irrelevant_intent_ignored(self, reset_proactive):
        """Intent non significatif (MEMORY_CLEANUP) → pas de message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14):
            await pc._on_routine_complete({
                "status": "success", "intent": "MEMORY_CLEANUP"
            })
        assert len(pc._queue) == 0

    @pytest.mark.asyncio
    async def test_inner_voice_high_salience(self, reset_proactive):
        """Saillance 0.8 → message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_inner_voice_broadcast({
                "salience": 0.8, "content": "Je sens un pattern émergent", "source": "prediction"
            })
        assert len(pc._queue) == 1
        assert "PENSÉE" in pc._queue[0]["text"]
        assert "pattern émergent" in pc._queue[0]["text"]

    @pytest.mark.asyncio
    async def test_inner_voice_low_salience_ignored(self, reset_proactive):
        """Saillance 0.3 → pas de message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14):
            await pc._on_inner_voice_broadcast({
                "salience": 0.3, "content": "bruit de fond"
            })
        assert len(pc._queue) == 0

    @pytest.mark.asyncio
    async def test_connexion_high_deprivation(self, reset_proactive):
        """Deprivation CONNEXION 90 → message."""
        pc = reset_proactive
        mock_drive = MagicMock()
        mock_drive.deprivation = 90.0

        mock_desires = MagicMock()
        mock_desires.get_drive.return_value = mock_drive

        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"), \
             patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}), \
             patch.dict("sys.modules", {"core.cardiac_engine": MagicMock(heart=MagicMock(get_stats=MagicMock(return_value={"dominant_emotion": "sérénité"})))}), \
             patch.dict("sys.modules", {"core.self_awareness": MagicMock(awareness=MagicMock(get_latest_snapshot=MagicMock(return_value={"mood": "calme"})))}):
            await pc._on_heartbeat({})
        assert len(pc._queue) == 1
        assert "CONNEXION" in pc._queue[0]["text"]

    @pytest.mark.asyncio
    async def test_connexion_low_deprivation_ignored(self, reset_proactive):
        """Deprivation CONNEXION 50 → pas de message."""
        pc = reset_proactive
        mock_drive = MagicMock()
        mock_drive.deprivation = 50.0

        mock_desires = MagicMock()
        mock_desires.get_drive.return_value = mock_drive

        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)}):
            await pc._on_heartbeat({})
        assert len(pc._queue) == 0


# ═══════════════════════════════════════════════════════════════════
#  TestMessageFormat
# ═══════════════════════════════════════════════════════════════════

class TestMessageFormat:
    """Tests du format des messages générés."""

    @pytest.mark.asyncio
    async def test_insight_message_contains_text(self, reset_proactive):
        """Le contenu de l'insight est dans le message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (sérénité, dopamine 5.2)"):
            await pc._on_dmn_insight({"insight": "connexion entre mémoire et apprentissage"})
        text = pc._queue[0]["text"]
        assert "connexion entre mémoire et apprentissage" in text

    @pytest.mark.asyncio
    async def test_alert_message_contains_threat_level(self, reset_proactive):
        """Le niveau de menace est dans le message d'alerte."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_reptilian_alert({
                "mode": "FREEZE", "threat_level": 7.3, "adrenaline": 0.6
            })
        text = pc._queue[0]["text"]
        assert "7.3" in text
        assert "0.6" in text

    @pytest.mark.asyncio
    async def test_message_has_signature(self, reset_proactive):
        """La signature émotionnelle est en fin de message."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (sérénité, dopamine 5.2)"):
            await pc._on_dmn_insight({"insight": "test"})
        text = pc._queue[0]["text"]
        assert "— Promethee" in text


# ═══════════════════════════════════════════════════════════════════
#  TestAPI
# ═══════════════════════════════════════════════════════════════════

class TestAPI:
    """Tests des méthodes get_pending/acknowledge/get_stats."""

    def test_get_pending_empty(self, reset_proactive):
        """Pas de messages → liste vide."""
        pc = reset_proactive
        assert pc.get_pending() == []

    @pytest.mark.asyncio
    async def test_get_pending_returns_queued(self, reset_proactive):
        """Message queueé → retourné par get_pending."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_dmn_insight({"insight": "test insight"})
        pending = pc.get_pending()
        assert len(pending) == 1
        assert pending[0]["trigger_type"] == "insight"

    @pytest.mark.asyncio
    async def test_ack_clears_queue(self, reset_proactive):
        """Acknowledge → queue vide."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            await pc._on_dmn_insight({"insight": "test"})
        assert len(pc.get_pending()) == 1
        pc.acknowledge()
        assert len(pc.get_pending()) == 0

    def test_get_stats(self, reset_proactive):
        """get_stats retourne les métriques attendues."""
        pc = reset_proactive
        stats = pc.get_stats()
        assert "daily_count" in stats
        assert "daily_max" in stats
        assert stats["daily_max"] == DAILY_MAX
        assert "pending" in stats
        assert "total_sent" in stats

    @pytest.mark.asyncio
    async def test_priority_ordering(self, reset_proactive):
        """Les messages sont triés par priorité décroissante."""
        pc = reset_proactive
        with patch("core.proactive_chat._now_hour", return_value=14), \
             patch("core.proactive_chat._get_emotional_signature", return_value="— Promethee (test)"):
            # D'abord un résultat (priorité 3)
            await pc._on_routine_complete({
                "status": "success", "intent": "EXPANSION_CODE", "quality": 80
            })
            # Reset cooldowns pour permettre un 2e message
            pc._last_send_time = 0
            pc._type_last_send.clear()
            # Puis une alerte (priorité 10)
            await pc._on_reptilian_alert({
                "mode": "FIGHT", "threat_level": 9.0, "adrenaline": 1.0
            })
        pending = pc.get_pending()
        assert len(pending) == 2
        # Alerte (10) doit être avant résultat (3)
        assert pending[0]["trigger_type"] == "alerte"
        assert pending[1]["trigger_type"] == "resultat"
