"""
Tests OutreachEngine — 21 tests couvrant les 5 categories, filtres en cascade,
file d'attente, composition LLM, mode silencieux, anti-harcelement, persistance.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch, patch as mock_patch

import pytest

from core.event_bus.bus import bus


# ─── Fixture autouse ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_outreach(tmp_path, monkeypatch):
    """Reset singleton + state file isolé pour chaque test."""
    import core.outreach as mod
    mod.OutreachEngine.reset_singleton()
    monkeypatch.setattr(mod, "OUTREACH_STATE_FILE", tmp_path / "outreach_state.json")
    bus.reset()
    engine = mod.OutreachEngine()
    engine.init()
    yield engine
    bus.reset()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _patch_now_hour(hour: int):
    """Patch _now_hour pour simuler une heure donnée."""
    return patch("core.outreach._now_hour", return_value=hour)


def _patch_thalamus_salient(salient: bool = True):
    """Patch le thalamus pour contrôler la saillance."""
    mock_thalamus = MagicMock()
    mock_thalamus.get_state.return_value = {"salience": 0.8 if salient else 0.2}
    return patch.dict("sys.modules", {"core.thalamus": MagicMock(thalamus=mock_thalamus)})


def _patch_connexion(deprivation: float):
    """Patch le DesireEngine pour contrôler la deprivation CONNEXION."""
    mock_drive = MagicMock()
    mock_drive.deprivation = deprivation
    mock_desires = MagicMock()
    mock_desires.drives = {"CONNEXION": mock_drive}
    return patch.dict("sys.modules", {"core.desire_engine": MagicMock(desires=mock_desires)})


def _patch_compose(return_value="Message LLM compose"):
    """Patch chat_engine.compose_outreach."""
    mock_ce = MagicMock()
    mock_ce.compose_outreach = AsyncMock(return_value=return_value)
    return patch.dict("sys.modules", {"core.chat_engine": MagicMock(chat_engine=mock_ce)})


# ═══════════════════════════════════════════════════════════════════════════════
#  TestCategoryMapping (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCategoryMapping:
    """Vérifie que chaque event bus arrive dans la bonne catégorie."""

    @pytest.mark.asyncio
    async def test_ollama_unresponsive_is_critical(self, reset_outreach):
        """OLLAMA_UNRESPONSIVE → critical, bypass tous les filtres."""
        engine = reset_outreach
        await bus.publish("OLLAMA_UNRESPONSIVE", {"consecutive_timeouts": 3})
        assert len(engine._delivered) == 1
        assert engine._delivered[0]["category"] == "critical"

    @pytest.mark.asyncio
    async def test_hypothalamus_alarm_high_severity_is_critical(self, reset_outreach):
        """HYPOTHALAMUS_ALARM severity 0.9 → critical."""
        engine = reset_outreach
        await bus.publish("HYPOTHALAMUS_ALARM", {"severity": 0.9, "reason": "overload"})
        assert len(engine._delivered) == 1
        assert engine._delivered[0]["category"] == "critical"

    @pytest.mark.asyncio
    async def test_hypothalamus_alarm_low_severity_dropped(self, reset_outreach):
        """HYPOTHALAMUS_ALARM severity 0.5 → ignoré (drop)."""
        engine = reset_outreach
        await bus.publish("HYPOTHALAMUS_ALARM", {"severity": 0.5, "reason": "minor"})
        assert len(engine._delivered) == 0

    @pytest.mark.asyncio
    async def test_eureka_moment_is_eureka(self, reset_outreach):
        """EUREKA_MOMENT → eureka (si filtres passent)."""
        engine = reset_outreach
        with _patch_now_hour(10), _patch_thalamus_salient(True), \
             _patch_connexion(60.0), _patch_compose("Eureka!"):
            await bus.publish("EUREKA_MOMENT", {"intent": "EXPANSION_CODE"})
            # Laisser le create_task s'executer
            await asyncio.sleep(0.1)
        assert len(engine._delivered) == 1
        assert engine._delivered[0]["category"] == "eureka"

    @pytest.mark.asyncio
    async def test_cingulate_conflict_high_severity_is_input_needed(self, reset_outreach):
        """CINGULATE_CONFLICT severity 0.7 → input_needed."""
        engine = reset_outreach
        with _patch_now_hour(10), _patch_thalamus_salient(True), \
             _patch_connexion(60.0), _patch_compose("Besoin d'avis"):
            await bus.publish("CINGULATE_CONFLICT", {"severity": 0.7, "subject": "refactor"})
            await asyncio.sleep(0.1)
        assert len(engine._delivered) == 1
        assert engine._delivered[0]["category"] == "input_needed"


# ═══════════════════════════════════════════════════════════════════════════════
#  TestFilterCascade (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilterCascade:
    """Vérifie chaque filtre de la cascade."""

    @pytest.mark.asyncio
    async def test_silent_mode_queues_non_critical(self, reset_outreach):
        """Silent mode ON → non-critical en queue."""
        engine = reset_outreach
        engine.set_silent_mode(True)
        with _patch_now_hour(10):
            await bus.publish("EUREKA_MOMENT", {"intent": "test"})
        assert len(engine._delivered) == 0
        assert len(engine._pending_queue) == 1

    @pytest.mark.asyncio
    async def test_silent_mode_passes_critical(self, reset_outreach):
        """Silent mode ON → critical passe quand même."""
        engine = reset_outreach
        engine.set_silent_mode(True)
        await bus.publish("OLLAMA_UNRESPONSIVE", {})
        assert len(engine._delivered) == 1
        assert engine._delivered[0]["category"] == "critical"

    @pytest.mark.asyncio
    async def test_silent_hours_queue_non_critical(self, reset_outreach):
        """Heure 3h → non-critical en queue."""
        engine = reset_outreach
        with _patch_now_hour(3):
            await bus.publish("EUREKA_MOMENT", {"intent": "test"})
        assert len(engine._delivered) == 0
        assert len(engine._pending_queue) == 1

    @pytest.mark.asyncio
    async def test_daily_max_queues(self, reset_outreach):
        """Daily max atteint → queue."""
        engine = reset_outreach
        import core.outreach as mod
        engine._daily_count = mod.MAX_PER_DAY
        engine._daily_date = time.strftime("%Y-%m-%d")
        with _patch_now_hour(10):
            await bus.publish("EUREKA_MOMENT", {"intent": "test"})
        assert len(engine._delivered) == 0
        assert len(engine._pending_queue) == 1

    @pytest.mark.asyncio
    async def test_cooldown_same_category_drops(self, reset_outreach):
        """Cooldown même catégorie → DROP (pas queue)."""
        engine = reset_outreach
        engine._last_per_category["eureka"] = time.time()  # Cooldown actif
        with _patch_now_hour(10):
            await bus.publish("EUREKA_MOMENT", {"intent": "test"})
        assert len(engine._delivered) == 0
        assert len(engine._pending_queue) == 0  # DROP, pas queue

    @pytest.mark.asyncio
    async def test_connexion_satisfied_queues_curiosity_but_passes_eureka(self, reset_outreach):
        """CONNEXION deprivation 20 → curiosity en queue, eureka passe."""
        engine = reset_outreach
        with _patch_now_hour(10), _patch_thalamus_salient(True), \
             _patch_connexion(20.0), _patch_compose("Message"):
            # Curiosity doit etre queue (CONNEXION trop satisfaite)
            await bus.publish("DMN_INSIGHT", {"insight": "test"})
            await asyncio.sleep(0.05)
            assert len(engine._delivered) == 0
            assert len(engine._pending_queue) == 1

            # Eureka bypass le filtre CONNEXION
            await bus.publish("EUREKA_MOMENT", {"intent": "test"})
            await asyncio.sleep(0.1)
            assert len(engine._delivered) == 1
            assert engine._delivered[0]["category"] == "eureka"


# ═══════════════════════════════════════════════════════════════════════════════
#  TestThalamus (1 test)
# ═══════════════════════════════════════════════════════════════════════════════

class TestThalamus:
    """Filtre thalamus."""

    @pytest.mark.asyncio
    async def test_thalamus_not_salient_queues(self, reset_outreach):
        """Thalamus pas saillant → en queue."""
        engine = reset_outreach
        with _patch_now_hour(10), _patch_thalamus_salient(False), _patch_connexion(60.0):
            await bus.publish("EUREKA_MOMENT", {"intent": "test"})
        assert len(engine._delivered) == 0
        assert len(engine._pending_queue) == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  TestPendingQueue (3 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPendingQueue:
    """File d'attente."""

    @pytest.mark.asyncio
    async def test_max_pending_fifo_overflow(self, reset_outreach):
        """Max 20, FIFO overflow (le plus ancien est jeté)."""
        engine = reset_outreach
        import core.outreach as mod
        for i in range(25):
            engine._enqueue("curiosity", {"sujet": f"sujet_{i}"})
        assert len(engine._pending_queue) == mod.MAX_PENDING
        # Le premier element doit etre le 5e (index 5 apres overflow)
        assert engine._pending_queue[0]["context"]["sujet"] == "sujet_5"

    @pytest.mark.asyncio
    async def test_user_chat_drains_queue(self, reset_outreach):
        """USER_CHAT déclenche drain de la queue en digest."""
        engine = reset_outreach
        engine._enqueue("curiosity", {"sujet": "test1"})
        engine._enqueue("eureka", {"intent": "test2"})

        with _patch_compose("Digest de 2 events"):
            await bus.publish("USER_CHAT", {"message": "hello"})
            await asyncio.sleep(0.15)

        # Queue doit etre videe
        assert len(engine._pending_queue) == 0
        # Un digest doit avoir ete livre
        assert any(m["category"] == "digest" for m in engine._delivered)

    @pytest.mark.asyncio
    async def test_critical_delivered_in_silent_mode(self, reset_outreach):
        """Critical livré même en silent mode (sauf si burst-limité)."""
        engine = reset_outreach
        engine.set_silent_mode(True)
        # En silent mode, un seul critical est livré (pas de burst)
        await bus.publish("OLLAMA_UNRESPONSIVE", {})
        assert len(engine._delivered) == 1
        assert len(engine._pending_queue) == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  TestBurstLimiter (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBurstLimiter:
    """Anti-burst : max BURST_MAX messages par BURST_WINDOW secondes."""

    @pytest.mark.asyncio
    async def test_burst_queues_after_max(self, reset_outreach):
        """Au-dela de BURST_MAX deliveries, tout est queue (via _deliver direct)."""
        engine = reset_outreach
        import core.outreach as mod

        # Simuler BURST_MAX livraisons recentes via _deliver directement
        for i in range(mod.BURST_MAX):
            engine._deliver("digest", f"message {i}")
        assert len(engine._delivered) == mod.BURST_MAX

        # Un nouveau critical doit etre queue (burst atteint)
        await bus.publish("OLLAMA_UNRESPONSIVE", {})
        assert len(engine._delivered) == mod.BURST_MAX  # pas de nouveau
        assert len(engine._pending_queue) == 1

    @pytest.mark.asyncio
    async def test_burst_resets_after_window(self, reset_outreach):
        """Apres expiration burst + cooldown, un nouveau message passe."""
        engine = reset_outreach
        import core.outreach as mod

        # Remplir le burst
        engine._deliver("critical", "alerte 1")
        assert len(engine._delivered) == 1

        # Simuler le passage du temps (> BURST_WINDOW et > cooldown critical 120s)
        max_cd = max(mod.BURST_WINDOW, mod.COOLDOWN_PER_CATEGORY.get("critical", 120))
        old = time.time() - max_cd - 1
        engine._hourly_timestamps = [old] * mod.BURST_MAX
        engine._last_per_category["critical"] = old

        # Un nouveau message doit passer
        await bus.publish("OLLAMA_UNRESPONSIVE", {})
        assert len(engine._delivered) == 2

    @pytest.mark.asyncio
    async def test_burst_affects_all_categories(self, reset_outreach):
        """Le burst limiter s'applique a toutes les categories."""
        engine = reset_outreach
        import core.outreach as mod

        # Remplir le burst via _deliver (categories mixtes)
        for i in range(mod.BURST_MAX):
            engine._deliver("digest", f"msg {i}")

        # Un eureka doit etre queue
        with _patch_now_hour(10), _patch_thalamus_salient(True), _patch_connexion(60.0):
            await bus.publish("EUREKA_MOMENT", {"intent": "test"})
        assert len(engine._pending_queue) == 1

        # Un critical aussi doit etre queue
        await bus.publish("OLLAMA_UNRESPONSIVE", {})
        assert len(engine._pending_queue) == 2

    @pytest.mark.asyncio
    async def test_critical_cooldown_prevents_spam(self, reset_outreach):
        """2 critical rapides : le 2e est queue (cooldown 120s)."""
        engine = reset_outreach

        await bus.publish("HYPOTHALAMUS_ALARM", {"severity": 0.9, "reason": "test1"})
        assert len(engine._delivered) == 1

        # 2e immediatement apres : cooldown bloque
        await bus.publish("HYPOTHALAMUS_ALARM", {"severity": 0.95, "reason": "test2"})
        assert len(engine._delivered) == 1
        assert len(engine._pending_queue) == 1
        assert engine._pending_queue[0]["context"]["detail"].startswith("Alarme hypothalamus")

    @pytest.mark.asyncio
    async def test_race_condition_prevented(self, reset_outreach):
        """Plusieurs events input_needed concurrents : un seul delivre grace au cooldown eager."""
        engine = reset_outreach

        with _patch_now_hour(10), _patch_thalamus_salient(True), \
             _patch_connexion(60.0), _patch_compose("Need input"):
            # Publier 5 CINGULATE_CONFLICT en rafale
            for i in range(5):
                await bus.publish("CINGULATE_CONFLICT", {"severity": 0.8, "subject": f"issue_{i}"})
            await asyncio.sleep(0.2)

        # Un seul delivre, les autres drop (cooldown eagerly reserved)
        assert len(engine._delivered) == 1
        assert engine._delivered[0]["category"] == "input_needed"


# ═══════════════════════════════════════════════════════════════════════════════
#  TestCompose (2 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompose:
    """Composition via LLM et fallback."""

    @pytest.mark.asyncio
    async def test_compose_success_delivers_llm_message(self, reset_outreach):
        """compose_outreach succes → message LLM livré."""
        engine = reset_outreach
        with _patch_now_hour(10), _patch_thalamus_salient(True), \
             _patch_connexion(60.0), _patch_compose("Decouverte fascinante !"):
            await bus.publish("EUREKA_MOMENT", {"intent": "EXPANSION_CODE"})
            await asyncio.sleep(0.1)
        assert len(engine._delivered) == 1
        assert engine._delivered[0]["text"] == "Decouverte fascinante !"

    @pytest.mark.asyncio
    async def test_compose_failure_uses_fallback(self, reset_outreach):
        """compose_outreach echec → fallback template utilisé."""
        engine = reset_outreach
        with _patch_now_hour(10), _patch_thalamus_salient(True), \
             _patch_connexion(60.0), _patch_compose(None):
            await bus.publish("EUREKA_MOMENT", {"intent": "EXPANSION_CODE"})
            await asyncio.sleep(0.1)
        assert len(engine._delivered) == 1
        # Doit contenir le texte du fallback template
        assert "EXPANSION_CODE" in engine._delivered[0]["text"]


# ═══════════════════════════════════════════════════════════════════════════════
#  TestSilentMode (1 test)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSilentMode:
    """Mode silencieux."""

    def test_auto_reset_after_4h(self, reset_outreach):
        """Auto-reset après 4h."""
        engine = reset_outreach
        import core.outreach as mod
        engine.set_silent_mode(True)
        assert engine._is_silent_mode() is True
        # Simuler 4h passees
        engine._silent_mode_since = time.time() - mod.SILENT_MODE_AUTO_RESET - 1
        assert engine._is_silent_mode() is False
        assert engine._silent_mode is False  # Auto-reset effectif


# ═══════════════════════════════════════════════════════════════════════════════
#  TestAntiHarassment (1 test)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAntiHarassment:
    """Anti-harcèlement : mode passif après 24h."""

    @pytest.mark.asyncio
    async def test_passive_mode_only_critical_for_telegram(self, reset_outreach):
        """Passif apres 24h → get_pending(telegram=True) = critical seulement."""
        engine = reset_outreach
        import core.outreach as mod

        # Simuler 24h+ sans USER_CHAT
        engine._last_user_chat = time.time() - mod.PASSIVE_MODE_TIMEOUT - 1

        # Livrer un critical et un eureka
        engine._deliver("critical", "Alerte critique !")
        engine._deliver("eureka", "Decouverte !")

        # Telegram ne recoit que le critical
        result = engine.get_pending(telegram=True)
        assert len(result["messages"]) == 1
        assert result["messages"][0]["category"] == "critical"

        # Sans telegram flag, on recoit tout
        result_all = engine.get_pending(telegram=False)
        assert len(result_all["messages"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  TestStats + Persistence (2 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatsPersistence:
    """Stats et persistance."""

    def test_get_stats_structure(self, reset_outreach):
        """get_stats retourne une structure complete."""
        engine = reset_outreach
        stats = engine.get_stats()
        assert "daily_count" in stats
        assert "daily_max" in stats
        assert "total_sent" in stats
        assert "pending_queue" in stats
        assert "delivered" in stats
        assert "silent_mode" in stats
        assert "passive_mode" in stats
        assert "last_user_chat" in stats
        assert "active_cooldowns" in stats

    def test_save_load_roundtrip(self, reset_outreach, tmp_path):
        """Save/load preservent l'état."""
        engine = reset_outreach
        # Modifier l'etat
        engine._total_sent = 42
        engine._daily_count = 5
        engine._silent_mode = True
        engine._silent_mode_since = 12345.0
        engine._last_per_category = {"eureka": time.time()}
        engine._pending_queue = [{"category": "curiosity", "context": {"sujet": "x"}, "timestamp": time.time()}]
        engine._save()

        # Recharger dans une nouvelle instance
        import core.outreach as mod
        mod.OutreachEngine.reset_singleton()
        engine2 = mod.OutreachEngine()
        engine2._initialized = True
        engine2._pending_queue = []
        engine2._delivered = []
        engine2._message_log = []
        engine2._last_per_category = {}
        engine2._silent_mode = False
        engine2._silent_mode_since = 0.0
        engine2._last_user_chat = time.time()
        engine2._daily_count = 0
        engine2._daily_date = ""
        engine2._hourly_timestamps = []
        engine2._total_sent = 0
        engine2._load()

        assert engine2._total_sent == 42
        assert engine2._daily_count == 5
        assert engine2._silent_mode is True
        assert engine2._silent_mode_since == 12345.0
        assert len(engine2._pending_queue) == 1
        assert "eureka" in engine2._last_per_category
