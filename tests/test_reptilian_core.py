"""
Tests pour ReptilianCore — le tronc cérébral de Prométhée.

~50 tests couvrant :
- Singleton et persistance
- Capteurs de menaces
- Threat level composite
- Réflexes (FREEZE, SHED, FLINCH, ADRENALINE, FIGHT)
- Amygdale (conditionnement pavlovien)
- API publique
- Événements bus
- Edge cases
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.reptilian_core import (
    ADAPTIVE_THRESHOLDS,
    ADRENALINE_DECAY,
    HABITUATION_DECAY_PER_TICK,
    HABITUATION_FLOOR,
    HABITUATION_ONSET,
    HABITUATION_RECOVERY_RATE,
    REFLEX_COOLDOWNS,
    REPTILIAN_STATE_FILE,
    THREAT_ALERT,
    THREAT_DECAY_RATE,
    THRESHOLDS,
    ReptilianCore,
    ThreatMemory,
    reptile,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_reptilian(tmp_path, monkeypatch):
    """Reset singleton + isolation fichier persistance."""
    ReptilianCore.reset_singleton()
    monkeypatch.setattr("core.reptilian_core.REPTILIAN_STATE_FILE", str(tmp_path / "reptilian.json"))
    yield
    ReptilianCore.reset_singleton()


@pytest.fixture
def rept():
    """Instance fraîche du reptilien."""
    return ReptilianCore()


# ============================================================
# 1. Singleton et initialisation
# ============================================================

class TestSingleton:

    def test_singleton_returns_same_instance(self, rept):
        """Deux instanciations retournent le même objet."""
        r2 = ReptilianCore()
        assert r2 is rept

    def test_reset_singleton_creates_new(self, rept):
        """Après reset, une nouvelle instance est créée."""
        old_id = id(rept)
        ReptilianCore.reset_singleton()
        r2 = ReptilianCore()
        assert id(r2) != old_id

    def test_initial_state(self, rept):
        """État initial : calme, pas de menace, pas d'adrénaline."""
        assert rept.threat_level == 0.0
        assert rept.adrenaline == 0.0
        assert rept.reflexes_triggered == {}
        assert rept.threat_memories == {}
        assert rept._alive is False


# ============================================================
# 2. Persistance
# ============================================================

class TestPersistence:

    def test_save_and_load(self, rept, tmp_path, monkeypatch):
        """Sauvegarde et rechargement de l'état."""
        rept.threat_level = 5.0
        rept.adrenaline = 0.7
        rept.reflexes_triggered = {"FREEZE": 2}
        rept._beat_counter = 42
        rept.threat_memories["ollama"] = ThreatMemory(
            pattern="ollama", severity=8.0, occurrences=3,
            last_seen=time.time(), conditioned_reflex="FIGHT"
        )
        rept.save()

        # Reset et recharger
        ReptilianCore.reset_singleton()
        r2 = ReptilianCore()
        assert r2.threat_level == 5.0
        assert r2.adrenaline == 0.7
        assert r2.reflexes_triggered == {"FREEZE": 2}
        assert r2._beat_counter == 42
        assert "ollama" in r2.threat_memories
        assert r2.threat_memories["ollama"].severity == 8.0

    def test_load_missing_file(self, rept, tmp_path, monkeypatch):
        """Pas de fichier → état par défaut."""
        monkeypatch.setattr("core.reptilian_core.REPTILIAN_STATE_FILE",
                            str(tmp_path / "nonexistent.json"))
        ReptilianCore.reset_singleton()
        r2 = ReptilianCore()
        assert r2.threat_level == 0.0

    def test_load_corrupt_file(self, rept, tmp_path, monkeypatch):
        """Fichier corrompu → état par défaut."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json at all")
        monkeypatch.setattr("core.reptilian_core.REPTILIAN_STATE_FILE", str(bad_file))
        ReptilianCore.reset_singleton()
        r2 = ReptilianCore()
        assert r2.threat_level == 0.0


# ============================================================
# 3. Capteurs de menaces
# ============================================================

class TestSensors:

    @pytest.mark.asyncio
    async def test_sense_no_threats(self, rept):
        """Aucune menace quand tout va bien."""
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50)), \
             patch("psutil.Process") as mock_proc, \
             patch("httpx.AsyncClient") as mock_client:
            mock_proc.return_value.memory_info.return_value = MagicMock(rss=500 * 1024 * 1024)
            resp = MagicMock(status_code=200)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=resp)
            ))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            threats = await rept._sense_threats()
            assert "cpu" not in threats
            assert "ram" not in threats
            assert "ollama" not in threats

    @pytest.mark.asyncio
    async def test_sense_cpu_critical(self, rept):
        """CPU critique → menace 8.0."""
        with patch("psutil.cpu_percent", return_value=96), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50)), \
             patch("psutil.Process") as mock_proc, \
             patch("httpx.AsyncClient") as mock_client:
            mock_proc.return_value.memory_info.return_value = MagicMock(rss=500 * 1024 * 1024)
            resp = MagicMock(status_code=200)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=resp)
            ))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            threats = await rept._sense_threats()
            assert threats.get("cpu") == 8.0

    @pytest.mark.asyncio
    async def test_sense_ram_warning(self, rept):
        """RAM warning → menace 4.0."""
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=78)), \
             patch("psutil.Process") as mock_proc, \
             patch("httpx.AsyncClient") as mock_client:
            mock_proc.return_value.memory_info.return_value = MagicMock(rss=500 * 1024 * 1024)
            resp = MagicMock(status_code=200)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=resp)
            ))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            threats = await rept._sense_threats()
            assert threats.get("ram") == 4.0

    @pytest.mark.asyncio
    async def test_sense_ollama_down(self, rept):
        """Ollama injoignable → menace 9.0."""
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50)), \
             patch("psutil.Process") as mock_proc, \
             patch("httpx.AsyncClient") as mock_client:
            mock_proc.return_value.memory_info.return_value = MagicMock(rss=500 * 1024 * 1024)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(side_effect=Exception("Connection refused"))
            ))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            threats = await rept._sense_threats()
            assert threats.get("ollama") == 9.0

    @pytest.mark.asyncio
    async def test_sense_error_streak(self, rept):
        """Error streak élevée → menace proportionnelle (test via compute_threat_level)."""
        # L'import autonomy est local dans _sense_threats — on teste le calcul direct
        level = rept._compute_threat_level({"error_streak": 6.0})
        assert level == 6.0

        level2 = rept._compute_threat_level({"error_streak": 9.0})
        assert level2 == 9.0

    @pytest.mark.asyncio
    async def test_sense_hallucination_storm(self, rept):
        """3+ hallucinations en 10 min → storm détecté."""
        now = time.time()
        rept._hallucination_timestamps = [now - 300, now - 200, now - 100]
        # Simuler _sense_threats sans les vrais capteurs système
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50)), \
             patch("psutil.Process") as mock_proc, \
             patch("httpx.AsyncClient") as mock_client:
            mock_proc.return_value.memory_info.return_value = MagicMock(rss=500 * 1024 * 1024)
            resp = MagicMock(status_code=200)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=resp)
            ))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch.dict("sys.modules", {"core.autonomy_engine": MagicMock()}):
                threats = await rept._sense_threats()
        assert threats.get("hallucination_storm") == 6.0

    @pytest.mark.asyncio
    async def test_sense_budget_warning(self, rept):
        """Budget à 87% → alerte."""
        mock_mod = MagicMock()
        mock_autonomy = MagicMock(
            error_streak=0,
            daily_count=70,  # 70/80 = 87.5%
            daily_budget_used=50,
            last_health_check=None,
        )
        mock_mod.autonomy = mock_autonomy
        mock_mod.MAX_DAILY_ROUTINES = 80
        mock_mod.DAILY_BUDGET_POINTS = 200

        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50)), \
             patch("psutil.Process") as mock_proc, \
             patch("httpx.AsyncClient") as mock_client:
            mock_proc.return_value.memory_info.return_value = MagicMock(rss=500 * 1024 * 1024)
            resp = MagicMock(status_code=200)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=resp)
            ))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch.dict("sys.modules", {"core.autonomy_engine": mock_mod}):
                threats = await rept._sense_threats()
        assert threats.get("budget") == 4.0

    @pytest.mark.asyncio
    async def test_sense_process_memory_high(self, rept):
        """Process Python > 2Go → alerte."""
        with patch("psutil.cpu_percent", return_value=30), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50)), \
             patch("psutil.Process") as mock_proc, \
             patch("httpx.AsyncClient") as mock_client:
            mock_proc.return_value.memory_info.return_value = MagicMock(
                rss=2500 * 1024 * 1024  # 2.5 Go
            )
            resp = MagicMock(status_code=200)
            mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                get=AsyncMock(return_value=resp)
            ))
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch.dict("sys.modules", {"core.autonomy_engine": MagicMock()}):
                threats = await rept._sense_threats()
        assert threats.get("process_memory") == 5.0


# ============================================================
# 4. Threat Level Composite
# ============================================================

class TestThreatLevel:

    def test_no_threats(self, rept):
        """Pas de menaces → niveau 0."""
        assert rept._compute_threat_level({}) == 0.0

    def test_single_threat(self, rept):
        """Une seule menace → son niveau."""
        assert rept._compute_threat_level({"cpu": 5.0}) == 5.0

    def test_multiple_threats_cascade(self, rept):
        """Menaces multiples → max + bonus cascade."""
        # cpu=5.0 (>=3=ALERT), ram=4.0 (>=3=ALERT), ollama=9.0 (>=3=ALERT)
        # max=9.0, active_count=3, cascade_bonus=(3-1)*0.5=1.0
        level = rept._compute_threat_level({"cpu": 5.0, "ram": 4.0, "ollama": 9.0})
        assert level == 10.0  # min(10, 9+1)

    def test_cascade_bonus_two_threats(self, rept):
        """Deux menaces actives → +0.5 bonus."""
        level = rept._compute_threat_level({"cpu": 4.0, "ram": 5.0})
        assert level == 5.5  # max=5 + (2-1)*0.5

    def test_sub_alert_not_counted(self, rept):
        """Menaces sous ALERT ne comptent pas pour le cascade bonus."""
        level = rept._compute_threat_level({"idle": 2.0, "cpu": 5.0})
        # active_count=1 (seul cpu>=3), cascade_bonus=0
        assert level == 5.0

    def test_decay(self, rept):
        """Le threat level décroît avec THREAT_DECAY_RATE."""
        rept.threat_level = 8.0
        # Pas de nouvelles menaces → new_level = 0
        # max(0, 8.0 * 0.85) = 6.8
        decayed = max(0, rept.threat_level * THREAT_DECAY_RATE)
        assert abs(decayed - 6.8) < 0.01


# ============================================================
# 5. Réflexes
# ============================================================

class TestReflexes:

    def test_freeze_activation(self, rept):
        """FREEZE s'active quand threat >= 7."""
        now = time.time()
        rept._activate_freeze(now)
        assert rept.should_freeze()
        assert rept.reflexes_triggered.get("FREEZE") == 1

    def test_freeze_expires(self, rept):
        """FREEZE expire après 3 minutes."""
        rept._freeze_until = time.time() - 1  # Expiré
        assert not rept.should_freeze()

    def test_shed_activation(self, rept):
        """SHED s'active et retourne max_cost=2."""
        now = time.time()
        rept._activate_shed(now)
        active, max_cost = rept.should_shed()
        assert active is True
        assert max_cost == 2

    def test_shed_expires(self, rept):
        """SHED expire après 2 minutes."""
        rept._shed_until = time.time() - 1  # Expiré
        active, max_cost = rept.should_shed()
        assert active is False

    def test_flinch_activation(self, rept):
        """FLINCH génère une raison de veto consommable une fois."""
        rept._activate_flinch({"cpu": 8.0, "ollama": 9.0})
        reason = rept.should_flinch()
        assert "FLINCH" in reason
        assert "ollama" in reason  # Le plus grave
        # Deuxième appel → vide (consommé)
        assert rept.should_flinch() == ""

    def test_adrenaline_injection(self, rept):
        """Injection d'adrénaline augmente le niveau."""
        with patch("core.reptilian_core.heart", create=True) as mock_heart:
            # Patcher l'import local
            with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock(heart=mock_heart)}):
                rept._inject_adrenaline()
        assert rept.adrenaline == 0.4
        assert rept.reflexes_triggered.get("ADRENALINE") == 1

    def test_adrenaline_caps_at_one(self, rept):
        """L'adrénaline est plafonnée à 1.0."""
        rept.adrenaline = 0.8
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            rept._inject_adrenaline()
        assert rept.adrenaline == 1.0

    def test_cooldown_prevents_spam(self, rept):
        """Un réflexe ne peut pas se déclencher pendant son cooldown."""
        now = time.time()
        rept._reflex_cooldowns["FREEZE"] = now
        assert not rept._can_trigger("FREEZE", now + 10)  # 10s < 180s cooldown
        assert rept._can_trigger("FREEZE", now + 200)     # 200s > 180s cooldown

    @pytest.mark.asyncio
    async def test_trigger_freeze_at_7(self, rept):
        """Le réflexe FREEZE se déclenche à threat >= 7."""
        rept.threat_level = 7.5
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock):
            await rept._trigger_reflexes({"ollama": 9.0})
        assert rept.should_freeze()

    @pytest.mark.asyncio
    async def test_trigger_shed_at_5(self, rept):
        """Le réflexe SHED se déclenche à threat >= 5 (et < 7)."""
        rept.threat_level = 5.5
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock):
            await rept._trigger_reflexes({"error_streak": 6.0})
        active, _ = rept.should_shed()
        assert active

    @pytest.mark.asyncio
    async def test_trigger_adrenaline_at_3(self, rept):
        """L'adrénaline est injectée à threat >= 3."""
        rept.threat_level = 3.5
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            with patch.object(rept, "_publish_alert", new_callable=AsyncMock):
                await rept._trigger_reflexes({"error_streak": 3.0})
        assert rept.adrenaline > 0

    @pytest.mark.asyncio
    async def test_fight_when_ollama_down(self, rept):
        """FIGHT se déclenche quand threat >= 6 et ollama down."""
        rept.threat_level = 6.5
        mock_resp = MagicMock(status_code=200)
        mock_client_instance = MagicMock(
            get=AsyncMock(return_value=mock_resp)
        )
        with patch("httpx.AsyncClient") as mock_client, \
             patch.object(rept, "_publish_alert", new_callable=AsyncMock):
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            await rept._trigger_reflexes({"ollama": 9.0})
        assert rept.reflexes_triggered.get("FIGHT", 0) >= 1


# ============================================================
# 6. Amygdale — Conditionnement pavlovien
# ============================================================

class TestAmygdala:

    def test_new_threat_memory(self, rept):
        """Premier conditionnement crée une nouvelle mémoire."""
        rept._condition_threat("ollama_down", 9.0, "FIGHT")
        assert "ollama_down" in rept.threat_memories
        mem = rept.threat_memories["ollama_down"]
        assert mem.severity == 9.0
        assert mem.occurrences == 1
        assert mem.conditioned_reflex == "FIGHT"

    def test_reinforce_threat_memory(self, rept):
        """Conditionnement répété renforce la mémoire (moyenne mobile)."""
        rept._condition_threat("ollama_down", 9.0, "FIGHT")
        rept._condition_threat("ollama_down", 7.0, "FIGHT")
        mem = rept.threat_memories["ollama_down"]
        # severity = 9.0 * 0.7 + 7.0 * 0.3 = 6.3 + 2.1 = 8.4
        assert abs(mem.severity - 8.4) < 0.01
        assert mem.occurrences == 2

    def test_escalate_reflex(self, rept):
        """Le réflexe le plus grave l'emporte."""
        rept._condition_threat("error_streak", 4.0, "ADRENALINE")
        assert rept.threat_memories["error_streak"].conditioned_reflex == "ADRENALINE"
        rept._condition_threat("error_streak", 8.0, "FREEZE")
        assert rept.threat_memories["error_streak"].conditioned_reflex == "FREEZE"

    def test_no_downgrade_reflex(self, rept):
        """Un réflexe ne peut pas être dégradé."""
        rept._condition_threat("error_streak", 8.0, "FREEZE")
        rept._condition_threat("error_streak", 3.0, "ADRENALINE")
        # FREEZE reste (index 3 > index 0)
        assert rept.threat_memories["error_streak"].conditioned_reflex == "FREEZE"

    def test_get_conditioned_response(self, rept):
        """Consultation de l'amygdale."""
        rept._condition_threat("cpu_high", 5.0, "SHED")
        mem = rept.get_conditioned_response("cpu_high")
        assert mem is not None
        assert mem.conditioned_reflex == "SHED"

    def test_get_conditioned_unknown(self, rept):
        """Pattern inconnu → None."""
        assert rept.get_conditioned_response("unknown_pattern") is None


# ============================================================
# 7. API Publique
# ============================================================

class TestPublicAPI:

    def test_should_freeze_false_by_default(self, rept):
        assert not rept.should_freeze()

    def test_should_shed_false_by_default(self, rept):
        active, _ = rept.should_shed()
        assert not active

    def test_should_flinch_empty_by_default(self, rept):
        assert rept.should_flinch() == ""

    def test_get_stats_structure(self, rept):
        """get_stats retourne un dict avec les bonnes clés."""
        stats = rept.get_stats()
        assert "threat_level" in stats
        assert "adrenaline" in stats
        assert "freeze_active" in stats
        assert "shed_active" in stats
        assert "last_threats" in stats
        assert "reflexes_triggered" in stats
        assert "threat_memories_count" in stats
        assert "alive" in stats

    def test_get_threat_level(self, rept):
        rept.threat_level = 4.5
        assert rept.get_threat_level() == 4.5

    def test_get_adrenaline(self, rept):
        rept.adrenaline = 0.6
        assert rept.get_adrenaline() == 0.6


# ============================================================
# 8. Événements Bus
# ============================================================

class TestBusEvents:

    @pytest.mark.asyncio
    async def test_on_routine_success_calms(self, rept):
        """Routine réussie → apaisement."""
        rept.threat_level = 5.0
        rept.adrenaline = 0.5
        await rept._on_routine_complete({"status": "success", "intent": "EXPANSION_CODE"})
        assert rept.threat_level == 4.0
        assert rept.adrenaline == 0.4

    @pytest.mark.asyncio
    async def test_on_routine_failure_no_effect(self, rept):
        """Routine échouée → pas d'apaisement."""
        rept.threat_level = 5.0
        await rept._on_routine_complete({"status": "error"})
        assert rept.threat_level == 5.0

    @pytest.mark.asyncio
    async def test_on_ci_success_calms(self, rept):
        """CI réussie → réduit la menace."""
        rept.threat_level = 4.0
        await rept._on_ci_result({"success": True})
        assert rept.threat_level == 3.0

    @pytest.mark.asyncio
    async def test_on_ci_failure_increases(self, rept):
        """CI échouée → augmente la menace."""
        rept.threat_level = 2.0
        await rept._on_ci_result({"success": False})
        assert rept.threat_level == 4.0

    @pytest.mark.asyncio
    async def test_on_hallucination_adds_timestamp(self, rept):
        """Hallucination détectée → timestamp ajouté."""
        assert len(rept._hallucination_timestamps) == 0
        await rept._on_hallucination({"agent": "coder", "type": "offtopic"})
        assert len(rept._hallucination_timestamps) == 1

    @pytest.mark.asyncio
    async def test_on_autonomy_heartbeat_updates_activity(self, rept):
        """Heartbeat autonomy → met à jour l'horodatage d'activité."""
        rept._last_activity = 0
        await rept._on_autonomy_heartbeat({})
        assert rept._last_activity > 0

    def test_on_routine_success_method(self, rept):
        """Méthode directe on_routine_success."""
        rept.threat_level = 3.0
        rept._flinch_reason = "test"
        rept.on_routine_success("EXPANSION_CODE")
        assert rept.threat_level == 2.0
        assert rept._flinch_reason == ""


# ============================================================
# 9. Edge Cases
# ============================================================

class TestEdgeCases:

    def test_threat_level_clamped_to_10(self, rept):
        """Le threat level ne dépasse pas 10."""
        level = rept._compute_threat_level({
            "cpu": 9.0, "ram": 9.0, "ollama": 9.0, "error_streak": 9.0
        })
        assert level == 10.0

    def test_adrenaline_decay(self, rept):
        """L'adrénaline décroît correctement."""
        rept.adrenaline = 1.0
        rept.adrenaline = max(0.0, rept.adrenaline * ADRENALINE_DECAY)
        assert abs(rept.adrenaline - 0.92) < 0.01

    def test_threat_memories_serializable(self, rept):
        """Les ThreatMemory sont sérialisables en JSON."""
        rept._condition_threat("test_pattern", 5.0, "SHED")
        from dataclasses import asdict
        data = asdict(rept.threat_memories["test_pattern"])
        json_str = json.dumps(data)
        assert "test_pattern" in json_str

    def test_hallucination_window_cleanup(self, rept):
        """Les vieilles hallucinations sont nettoyées."""
        old = time.time() - THRESHOLDS["hallucination_window"] - 100
        recent = time.time() - 10
        rept._hallucination_timestamps = [old, old, recent]
        # Simuler le nettoyage qui se fait dans _sense_threats
        now = time.time()
        window = THRESHOLDS["hallucination_window"]
        cleaned = [t for t in rept._hallucination_timestamps if now - t < window]
        assert len(cleaned) == 1

    @pytest.mark.asyncio
    async def test_watchdog_single_tick(self, rept):
        """Un seul tick du watchdog ne crash pas."""
        rept._alive = True

        with patch.object(rept, "_sense_threats", new_callable=AsyncMock, return_value={}), \
             patch.object(rept, "save"):
            # Simuler un tick manuellement
            threats = await rept._sense_threats()
            new_level = rept._compute_threat_level(threats)
            rept.threat_level = max(new_level, rept.threat_level * THREAT_DECAY_RATE)
            rept.adrenaline = max(0.0, rept.adrenaline * ADRENALINE_DECAY)
            assert rept.threat_level == 0.0

    def test_subscribe_events_idempotent(self, rept):
        """Souscription double ne duplique pas les handlers."""
        with patch.dict("sys.modules", {"core.event_bus.bus": MagicMock(), "core.event_bus": MagicMock()}):
            rept._subscribed = False
            rept._subscribe_events()
            assert rept._subscribed is True
            # Deuxième appel → pas d'erreur
            rept._subscribe_events()
            assert rept._subscribed is True

    def test_init_method(self, rept):
        """init() ne crash pas même sans bus."""
        with patch.object(rept, "_subscribe_events"):
            rept.init()


# ============================================================
# 10. Intégration mocks
# ============================================================

class TestIntegration:

    def test_cardiac_integration_on_adrenaline(self, rept):
        """L'adrénaline déclenche heart.react('threat')."""
        mock_heart = MagicMock()
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock(heart=mock_heart)}):
            rept._inject_adrenaline()
        mock_heart.react.assert_called_once_with("threat")

    @pytest.mark.asyncio
    async def test_full_cycle_threat_to_calm(self, rept):
        """Cycle complet : menace → réflexe → apaisement."""
        # 1. Menace détectée
        rept.threat_level = 7.0
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._trigger_reflexes({"ollama": 9.0})

        # 2. FREEZE actif
        assert rept.should_freeze()

        # 3. Ollama revient, routines réussissent
        rept.on_routine_success("HEALTH_CHECK")
        rept.on_routine_success("AUDIT_STRUCTURE")
        rept.on_routine_success("EXPANSION_CODE")
        assert rept.threat_level == 4.0  # 7 - 3

    def test_shed_blocks_expensive_routines(self, rept):
        """SHED actif → should_shed retourne True + max_cost."""
        rept._shed_until = time.time() + 120
        rept._shed_max_cost = 2
        active, max_cost = rept.should_shed()
        assert active
        assert max_cost == 2

    @pytest.mark.asyncio
    async def test_hallucination_storm_triggers_shed(self, rept):
        """3 hallucinations → storm → menace 6.0 → SHED."""
        now = time.time()
        rept._hallucination_timestamps = [now - 100, now - 50, now - 10]

        # Le capteur détecte le storm
        threats = {"hallucination_storm": 6.0}
        level = rept._compute_threat_level(threats)
        assert level == 6.0

        # Le réflexe SHED devrait se déclencher
        rept.threat_level = level
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._trigger_reflexes(threats)
        active, _ = rept.should_shed()
        assert active


# ============================================================
# 11. Budget-only supprime les réflexes
# ============================================================

class TestBudgetOnlyReflexes:

    @pytest.mark.asyncio
    async def test_budget_only_suppresses_reflexes(self, rept):
        """Budget seule menace → pas de SHED/FLINCH/ADRENALINE."""
        rept.threat_level = 5.0
        threats = {"budget": 5.0}
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._trigger_reflexes(threats)
        # Aucun réflexe ne doit être déclenché
        assert rept.reflexes_triggered.get("SHED", 0) == 0
        assert rept.reflexes_triggered.get("FLINCH", 0) == 0
        assert rept.reflexes_triggered.get("ADRENALINE", 0) == 0
        assert rept.adrenaline == 0.0

    @pytest.mark.asyncio
    async def test_budget_plus_other_threat_still_triggers(self, rept):
        """Budget + autre menace → SHED se déclenche normalement."""
        rept.threat_level = 5.5
        threats = {"budget": 5.0, "error_streak": 3.0}
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._trigger_reflexes(threats)
        active, _ = rept.should_shed()
        assert active

    @pytest.mark.asyncio
    async def test_budget_only_logs_once(self, rept):
        """Le message INFO n'est émis qu'une fois par cooldown SHED."""
        rept.threat_level = 5.0
        threats = {"budget": 5.0}
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}), \
             patch("core.reptilian_core.logger") as mock_logger:
            # Premier appel → log
            await rept._trigger_reflexes(threats)
            assert mock_logger.info.call_count == 1
            # Deuxième appel immédiat → cooldown SHED empêche le re-log
            await rept._trigger_reflexes(threats)
            assert mock_logger.info.call_count == 1  # Toujours 1


# ============================================================
# 12. SHED graduée selon la cause
# ============================================================

class TestShedGraduated:

    def test_shed_budget_allows_cost_4(self, rept):
        """Budget SHED → max_cost=4 (permet GRIMOIRE/REFACTOR)."""
        now = time.time()
        rept._activate_shed(now, cause="budget")
        active, max_cost = rept.should_shed()
        assert active is True
        assert max_cost == 4

    def test_shed_error_strict_cost_2(self, rept):
        """Error SHED → max_cost=2 (strict)."""
        now = time.time()
        rept._activate_shed(now, cause="error_streak")
        active, max_cost = rept.should_shed()
        assert active is True
        assert max_cost == 2

    def test_shed_default_no_cause_cost_2(self, rept):
        """SHED sans cause → max_cost=2 (défaut strict)."""
        now = time.time()
        rept._activate_shed(now)
        active, max_cost = rept.should_shed()
        assert active is True
        assert max_cost == 2

    def test_shed_grimoire_allowed_budget(self, rept):
        """GRIMOIRE (coût 3) passe en SHED budget (max_cost=4)."""
        now = time.time()
        rept._activate_shed(now, cause="budget")
        active, max_cost = rept.should_shed()
        assert active and max_cost >= 3  # GRIMOIRE coût 3 passe

    def test_shed_expansion_blocked_always(self, rept):
        """EXPANSION (coût 5) bloqué dans tous les cas de SHED."""
        now = time.time()
        # Budget SHED (max=4)
        rept._activate_shed(now, cause="budget")
        _, max_cost_budget = rept.should_shed()
        assert max_cost_budget < 5
        # Error SHED (max=2)
        rept._activate_shed(now, cause="error_streak")
        _, max_cost_error = rept.should_shed()
        assert max_cost_error < 5

    @pytest.mark.asyncio
    async def test_shed_cause_propagated_from_trigger(self, rept):
        """La cause de la menace est transmise à _activate_shed via _trigger_reflexes."""
        rept.threat_level = 5.5
        threats = {"budget": 5.0}
        # Budget + autre menace pour éviter le short-circuit budget_only
        threats["error_streak"] = 3.0
        with patch.object(rept, "_publish_alert", new_callable=AsyncMock), \
             patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._trigger_reflexes(threats)
        active, max_cost = rept.should_shed()
        assert active
        # La cause principale est "budget" (5.0 > 3.0)
        assert max_cost == 4


class TestTissueHandlersReptilian:
    """Sprint 5 — Grand Câblage : handlers tissue dans reptilian_core."""

    @pytest.mark.asyncio
    async def test_tissue_overload_sets_floor(self, rept):
        """Zone surchargée → plancher TISSUE_OVERLOAD_THREAT (max, pas +=)."""
        rept.threat_level = 1.0
        await rept._on_tissue_overload({"zone": "emotion", "activity": 3.0})
        assert rept.threat_level == 3.0  # max(1.0, 3.0)

    @pytest.mark.asyncio
    async def test_tissue_overload_no_accumulation(self, rept):
        """Appels répétés d'overload ne s'accumulent PAS (fix boucle feedback)."""
        rept.threat_level = 1.0
        for _ in range(10):
            await rept._on_tissue_overload({"zone": "goals", "activity": 3.0})
        # max() idempotent : 10 appels = même résultat qu'un seul
        assert rept.threat_level == 3.0

    @pytest.mark.asyncio
    async def test_tissue_overload_does_not_lower(self, rept):
        """Si threat déjà au-dessus du plancher, overload ne la baisse pas."""
        rept.threat_level = 9.5
        await rept._on_tissue_overload({"zone": "threat", "activity": 5.0})
        assert rept.threat_level == 9.5  # max(9.5, 3.0) = 9.5

    @pytest.mark.asyncio
    async def test_tissue_extinction_sets_floor(self, rept):
        """Population en danger → plancher TISSUE_EXTINCTION_THREAT (max, pas +=)."""
        rept.threat_level = 2.0
        await rept._on_tissue_extinction({"population": 5})
        assert rept.threat_level == 5.0  # max(2.0, 5.0)

    @pytest.mark.asyncio
    async def test_tissue_extinction_no_accumulation(self, rept):
        """Appels répétés d'extinction ne s'accumulent PAS (fix boucle feedback)."""
        rept.threat_level = 0.0
        for _ in range(10):
            await rept._on_tissue_extinction({"population": 5})
        # max() idempotent : reste à 5.0, pas 40.0
        assert rept.threat_level == 5.0

    @pytest.mark.asyncio
    async def test_tissue_extinction_does_not_lower(self, rept):
        """Si threat déjà au-dessus du plancher, extinction ne la baisse pas."""
        rept.threat_level = 8.0
        await rept._on_tissue_extinction({"population": 3})
        assert rept.threat_level == 8.0  # max(8.0, 5.0) = 8.0

    @pytest.mark.asyncio
    async def test_tissue_threat_subsided_reduces_threat(self, rept):
        """Zone threat calme → threat -1.5 (boucle rétroaction)."""
        rept.threat_level = 5.0
        await rept._on_tissue_threat_subsided({"activity": 0.3})
        assert rept.threat_level == 3.5

    @pytest.mark.asyncio
    async def test_tissue_threat_subsided_clamped_at_zero(self, rept):
        """Threat ne descend pas sous 0."""
        rept.threat_level = 1.0
        await rept._on_tissue_threat_subsided({"activity": 0.1})
        assert rept.threat_level == 0.0

    @pytest.mark.asyncio
    async def test_tissue_threat_subsided_reduces_adrenaline(self, rept):
        """Zone calme → adrenaline réduite aussi."""
        rept.threat_level = 3.0
        rept.adrenaline = 0.5
        await rept._on_tissue_threat_subsided({})
        assert rept.adrenaline == pytest.approx(0.4)


# ============================================================
# Watchdog Ollama adaptatif (Fix post-run 2026-03-03)
# ============================================================

class TestOllamaAdaptiveCheck:
    """Le check /api/tags est skippé quand Ollama est stable depuis >=12 cycles."""

    @pytest.mark.asyncio
    async def test_streak_increments_on_success(self, rept):
        """Chaque check OK incrémente le streak."""
        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("psutil.cpu_percent", return_value=30.0), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50.0)), \
             patch("psutil.Process", return_value=MagicMock(memory_info=MagicMock(return_value=MagicMock(rss=500*1024*1024)))), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch.dict("sys.modules", {"core.base_agent": MagicMock(BaseAgent=MagicMock(get_ollama_health=MagicMock(return_value={"circuit_open": False, "consecutive_timeouts": 0})))}), \
             patch.dict("sys.modules", {"core.autonomy_engine": None}), \
             patch.dict("sys.modules", {"core.circadian_rhythm": None}):
            rept._ollama_ok = False
            rept._ollama_ok_streak = 0
            await rept._sense_threats()
            assert rept._ollama_ok is True
            assert rept._ollama_ok_streak == 1

    @pytest.mark.asyncio
    async def test_skip_check_when_streak_high(self, rept):
        """Quand streak >= 12, le check HTTP est skippé."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 12

        with patch("psutil.cpu_percent", return_value=30.0), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50.0)), \
             patch("psutil.Process", return_value=MagicMock(memory_info=MagicMock(return_value=MagicMock(rss=500*1024*1024)))), \
             patch("httpx.AsyncClient") as mock_httpx, \
             patch.dict("sys.modules", {"core.base_agent": MagicMock(BaseAgent=MagicMock(get_ollama_health=MagicMock(return_value={"circuit_open": False, "consecutive_timeouts": 0})))}), \
             patch.dict("sys.modules", {"core.autonomy_engine": None}), \
             patch.dict("sys.modules", {"core.circadian_rhythm": None}):
            threats = await rept._sense_threats()
            # httpx ne doit PAS avoir été appelé
            mock_httpx.assert_not_called()
            assert "ollama" not in threats
            assert rept._ollama_ok_streak == 13

    @pytest.mark.asyncio
    async def test_streak_resets_on_error(self, rept):
        """Une erreur Ollama reset le streak à 0."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 15

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        # Force un check en mettant _ollama_ok à False
        rept._ollama_ok = False

        with patch("psutil.cpu_percent", return_value=30.0), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50.0)), \
             patch("psutil.Process", return_value=MagicMock(memory_info=MagicMock(return_value=MagicMock(rss=500*1024*1024)))), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch.dict("sys.modules", {"core.base_agent": MagicMock(BaseAgent=MagicMock(get_ollama_health=MagicMock(return_value={"circuit_open": False, "consecutive_timeouts": 0})))}), \
             patch.dict("sys.modules", {"core.autonomy_engine": None}), \
             patch.dict("sys.modules", {"core.circadian_rhythm": None}):
            threats = await rept._sense_threats()
            assert rept._ollama_ok_streak == 0
            assert rept._ollama_ok is False
            assert "ollama" in threats

    @pytest.mark.asyncio
    async def test_check_performed_when_not_ok(self, rept):
        """Quand _ollama_ok=False, le check est toujours effectué."""
        rept._ollama_ok = False
        rept._ollama_ok_streak = 0  # Après erreur, streak est à 0

        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("psutil.cpu_percent", return_value=30.0), \
             patch("psutil.virtual_memory", return_value=MagicMock(percent=50.0)), \
             patch("psutil.Process", return_value=MagicMock(memory_info=MagicMock(return_value=MagicMock(rss=500*1024*1024)))), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch.dict("sys.modules", {"core.base_agent": MagicMock(BaseAgent=MagicMock(get_ollama_health=MagicMock(return_value={"circuit_open": False, "consecutive_timeouts": 0})))}), \
             patch.dict("sys.modules", {"core.autonomy_engine": None}), \
             patch.dict("sys.modules", {"core.circadian_rhythm": None}):
            await rept._sense_threats()
            # Le check a été effectué (httpx appelé)
            mock_client.get.assert_called_once()
            assert rept._ollama_ok is True
            assert rept._ollama_ok_streak == 1

    def test_streak_below_threshold_checks(self, rept):
        """Streak < 12 → le check doit être effectué (pas de skip)."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 11
        # La condition de skip est: _ollama_ok AND streak >= 12
        # Avec streak=11 et _ollama_ok=True → pas de skip
        assert not (rept._ollama_ok and rept._ollama_ok_streak >= 12)


# ============================================================
# Log RAM critique (Fix post-run 2026-03-03)
# ============================================================

class TestRamLogging:
    """Log warning quand RAM critique, avec throttle."""

    @pytest.mark.asyncio
    async def test_ram_critical_logs_warning(self, rept):
        """RAM >= 90% → log warning."""
        mock_mem = MagicMock(percent=92.0)
        with patch("psutil.cpu_percent", return_value=30.0), \
             patch("psutil.virtual_memory", return_value=mock_mem), \
             patch("psutil.Process", return_value=MagicMock(memory_info=MagicMock(return_value=MagicMock(rss=500*1024*1024)))), \
             patch.dict("sys.modules", {"httpx": MagicMock()}), \
             patch.dict("sys.modules", {"core.base_agent": MagicMock(BaseAgent=MagicMock(get_ollama_health=MagicMock(return_value={"circuit_open": False, "consecutive_timeouts": 0})))}), \
             patch.dict("sys.modules", {"core.autonomy_engine": None}), \
             patch.dict("sys.modules", {"core.circadian_rhythm": None}), \
             patch("core.reptilian_core.logger") as mock_logger:
            # Forcer le check Ollama à skip pour simplifier
            rept._ollama_ok = True
            rept._ollama_ok_streak = 20
            rept._ram_critical_logged = 0.0
            await rept._sense_threats()
            mock_logger.warning.assert_any_call(
                f"REPTILIEN: RAM CRITIQUE 92% (seuil {THRESHOLDS['ram_crit']}%)"
            )

    @pytest.mark.asyncio
    async def test_ram_critical_throttled(self, rept):
        """Pas de log répété si < 2 min depuis le dernier log critique."""
        rept._ram_critical_logged = time.time() - 60  # Il y a 60s (< 120s)
        mock_mem = MagicMock(percent=92.0)
        with patch("psutil.cpu_percent", return_value=30.0), \
             patch("psutil.virtual_memory", return_value=mock_mem), \
             patch("psutil.Process", return_value=MagicMock(memory_info=MagicMock(return_value=MagicMock(rss=500*1024*1024)))), \
             patch.dict("sys.modules", {"httpx": MagicMock()}), \
             patch.dict("sys.modules", {"core.base_agent": MagicMock(BaseAgent=MagicMock(get_ollama_health=MagicMock(return_value={"circuit_open": False, "consecutive_timeouts": 0})))}), \
             patch.dict("sys.modules", {"core.autonomy_engine": None}), \
             patch.dict("sys.modules", {"core.circadian_rhythm": None}), \
             patch("core.reptilian_core.logger") as mock_logger:
            rept._ollama_ok = True
            rept._ollama_ok_streak = 20
            await rept._sense_threats()
            # Pas de log warning pour RAM (throttled)
            for call in mock_logger.warning.call_args_list:
                assert "RAM CRITIQUE" not in str(call)


# ============================================================
# Seuils adaptatifs (PercentileTracker)
# ============================================================

class TestAdaptiveThresholds:
    """Tests des seuils CPU/RAM adaptatifs basés sur les percentiles glissants."""

    # Helper pour créer les patches communs (skip ollama, autonomy, circadian)
    def _base_patches(self, cpu=30.0, ram=50.0, proc_mem_mb=500):
        """Retourne un context manager avec les patches de base."""
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            with patch("psutil.cpu_percent", return_value=cpu), \
                 patch("psutil.virtual_memory", return_value=MagicMock(percent=ram)), \
                 patch("psutil.Process", return_value=MagicMock(
                     memory_info=MagicMock(return_value=MagicMock(rss=proc_mem_mb * 1024 * 1024))
                 )), \
                 patch.dict("sys.modules", {
                     "httpx": MagicMock(),
                     "core.base_agent": MagicMock(BaseAgent=MagicMock(
                         get_ollama_health=MagicMock(return_value={"circuit_open": False, "consecutive_timeouts": 0})
                     )),
                     "core.autonomy_engine": None,
                     "core.circadian_rhythm": None,
                 }):
                yield
        return ctx()

    def test_trackers_initialized(self, rept):
        """Les trackers CPU et RAM existent à l'init."""
        from core.percentile_tracker import PercentileTracker
        assert isinstance(rept._cpu_tracker, PercentileTracker)
        assert isinstance(rept._ram_tracker, PercentileTracker)
        assert rept._cpu_tracker.count() == 0
        assert rept._ram_tracker.count() == 0

    @pytest.mark.asyncio
    async def test_cpu_warn_cold_start(self, rept):
        """Sans historique, CPU >= 80 → threat 4.0 (seuil hardcodé)."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        with self._base_patches(cpu=85.0):
            threats = await rept._sense_threats()
        assert threats.get("cpu") == 4.0

    @pytest.mark.asyncio
    async def test_cpu_crit_cold_start(self, rept):
        """Sans historique, CPU >= 95 → threat 8.0 (seuil hardcodé)."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        with self._base_patches(cpu=96.0):
            threats = await rept._sense_threats()
        assert threats.get("cpu") == 8.0

    @pytest.mark.asyncio
    async def test_ram_warn_cold_start(self, rept):
        """Sans historique, RAM >= 75 → threat 4.0 (seuil hardcodé)."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        with self._base_patches(ram=78.0):
            threats = await rept._sense_threats()
        assert threats.get("ram") == 4.0

    @pytest.mark.asyncio
    async def test_ram_crit_cold_start(self, rept):
        """Sans historique, RAM >= 90 → threat 8.0 (seuil hardcodé)."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        with self._base_patches(ram=92.0):
            threats = await rept._sense_threats()
        assert threats.get("ram") == 8.0

    @pytest.mark.asyncio
    async def test_cpu_adaptive_warn(self, rept):
        """Tracker rempli, CPU au ~P90 → threat 4.0 (warning adaptatif)."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        # Distribution large 0-99% (360 échantillons)
        for i in range(360):
            rept._cpu_tracker.record(float(i % 100))
        assert rept._cpu_tracker.is_ready()

        # CPU à 90% → ~P92 de la distribution 0-99 → entre P85 (warn) et P97 (crit)
        with self._base_patches(cpu=90.0):
            threats = await rept._sense_threats()
        assert threats.get("cpu") == 4.0

    @pytest.mark.asyncio
    async def test_cpu_adaptive_no_threat(self, rept):
        """Tracker rempli, CPU normal → pas de menace."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        # Remplir avec distribution large
        for i in range(360):
            rept._cpu_tracker.record(float(i % 100))
        assert rept._cpu_tracker.is_ready()

        # CPU à 50% → P50 environ, bien sous P85
        with self._base_patches(cpu=50.0):
            threats = await rept._sense_threats()
        assert "cpu" not in threats

    @pytest.mark.asyncio
    async def test_ram_adaptive_crit(self, rept):
        """Tracker rempli, RAM au P96 → threat 8.0 (critique adaptatif)."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        # Remplir avec des valeurs basses
        for i in range(360):
            rept._ram_tracker.record(40.0 + (i % 5))  # 40-44%
        assert rept._ram_tracker.is_ready()

        # RAM à 80% → devrait être au-dessus du P95 de la distribution 40-44%
        with self._base_patches(ram=80.0):
            threats = await rept._sense_threats()
        assert threats.get("ram") == 8.0

    @pytest.mark.asyncio
    async def test_cpu_absolute_override(self, rept):
        """CPU >= 98% → TOUJOURS critique, même si les percentiles disent non."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        # Remplir le tracker avec du CPU haut (simule un serveur toujours chaud)
        for i in range(360):
            rept._cpu_tracker.record(95.0 + (i % 4))  # 95-98%
        assert rept._cpu_tracker.is_ready()

        # CPU à 98% → sécurité absolue = critique
        with self._base_patches(cpu=98.0):
            threats = await rept._sense_threats()
        assert threats.get("cpu") == 8.0

    @pytest.mark.asyncio
    async def test_ram_absolute_override(self, rept):
        """RAM >= 97% → TOUJOURS critique, même si les percentiles disent non."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        for i in range(360):
            rept._ram_tracker.record(90.0 + (i % 8))  # 90-97%
        assert rept._ram_tracker.is_ready()

        # RAM à 97% → sécurité absolue
        with self._base_patches(ram=97.0):
            threats = await rept._sense_threats()
        assert threats.get("ram") == 8.0

    def test_trackers_persisted(self, rept, tmp_path, monkeypatch):
        """save → load conserve les trackers et is_ready()."""
        state_file = str(tmp_path / "reptilian_persist.json")
        monkeypatch.setattr("core.reptilian_core.REPTILIAN_STATE_FILE", state_file)

        # Remplir les trackers
        for i in range(400):
            rept._cpu_tracker.record(float(i % 100))
            rept._ram_tracker.record(50.0 + (i % 20))
        assert rept._cpu_tracker.is_ready()

        rept.save()

        # Nouveau reptilien qui charge l'état
        ReptilianCore.reset_singleton()
        r2 = ReptilianCore()
        assert r2._cpu_tracker.is_ready()
        assert r2._cpu_tracker.count() == 400
        assert r2._ram_tracker.count() == 400

    @pytest.mark.asyncio
    async def test_record_on_each_sense(self, rept):
        """Chaque appel à _sense_threats enregistre CPU et RAM dans les trackers."""
        rept._ollama_ok = True
        rept._ollama_ok_streak = 20
        assert rept._cpu_tracker.count() == 0
        assert rept._ram_tracker.count() == 0

        with self._base_patches(cpu=45.0, ram=60.0):
            await rept._sense_threats()
        assert rept._cpu_tracker.count() == 1
        assert rept._ram_tracker.count() == 1

        with self._base_patches(cpu=50.0, ram=65.0):
            await rept._sense_threats()
        assert rept._cpu_tracker.count() == 2
        assert rept._ram_tracker.count() == 2

    def test_get_stats_includes_tracker_info(self, rept):
        """get_stats() contient les infos des trackers."""
        stats = rept.get_stats()
        assert "cpu_tracker_ready" in stats
        assert "cpu_tracker_samples" in stats
        assert "ram_tracker_ready" in stats
        assert "ram_tracker_samples" in stats
        assert stats["cpu_tracker_ready"] is False
        assert stats["cpu_tracker_samples"] == 0


# ============================================================
# Habituation — Atténuation des menaces chroniques
# ============================================================

class TestHabituation:
    """Tests du mécanisme d'habituation aux menaces persistantes."""

    def test_no_habituation_before_onset(self, rept):
        """Pas d'atténuation avant HABITUATION_ONSET ticks."""
        threats = {"ram": 4.0}
        for _ in range(HABITUATION_ONSET - 1):
            rept._update_habituation(threats)
        # Le facteur doit rester à 1.0 (ou non défini)
        hab = rept._threat_habituation.get("ram", 1.0)
        assert hab == 1.0
        assert threats["ram"] == 4.0

    def test_habituation_starts_after_onset(self, rept):
        """L'atténuation commence après HABITUATION_ONSET ticks."""
        threats_template = {"ram": 4.0}
        for _ in range(HABITUATION_ONSET + 2):
            threats = dict(threats_template)
            rept._update_habituation(threats)
        hab = rept._threat_habituation.get("ram", 1.0)
        assert hab < 1.0
        assert hab >= HABITUATION_FLOOR

    def test_habituation_floor(self, rept):
        """Le facteur ne descend pas en dessous de HABITUATION_FLOOR."""
        for _ in range(200):
            threats = {"ram": 4.0}
            rept._update_habituation(threats)
        hab = rept._threat_habituation.get("ram", 1.0)
        assert hab == pytest.approx(HABITUATION_FLOOR, abs=0.01)

    def test_habituation_recovery(self, rept):
        """Quand la menace disparaît, le facteur remonte vers 1.0."""
        # Phase 1 : habituation
        for _ in range(50):
            threats = {"ram": 4.0}
            rept._update_habituation(threats)
        hab_before = rept._threat_habituation["ram"]
        assert hab_before < 1.0

        # Phase 2 : menace disparue → récupération
        for _ in range(30):
            threats = {}
            rept._update_habituation(threats)
        hab_after = rept._threat_habituation.get("ram", 1.0)
        assert hab_after > hab_before

    def test_habituation_per_source(self, rept):
        """RAM et CPU sont habitués indépendamment."""
        for _ in range(30):
            threats = {"ram": 4.0, "cpu": 8.0}
            rept._update_habituation(threats)
        # Les deux sources ont leurs propres compteurs
        assert rept._threat_persistence["ram"] == 30
        assert rept._threat_persistence["cpu"] == 30
        # Mais si une disparaît...
        for _ in range(5):
            threats = {"cpu": 8.0}
            rept._update_habituation(threats)
        assert rept._threat_persistence["cpu"] == 35
        assert rept._threat_persistence["ram"] == 0

    def test_habituation_attenuates_threat(self, rept):
        """La menace effective est réduite par l'habituation."""
        for _ in range(50):
            threats = {"ram": 4.0}
            rept._update_habituation(threats)
        # Après 50 ticks, le facteur a baissé → la menace RAM devrait être < 4.0
        threats = {"ram": 4.0}
        rept._update_habituation(threats)
        assert threats["ram"] < 4.0
        assert threats["ram"] >= 4.0 * HABITUATION_FLOOR

    def test_habituation_full_recovery_cleans_up(self, rept):
        """Après récupération complète (facteur=1.0), les entrées sont nettoyées."""
        for _ in range(30):
            threats = {"ram": 4.0}
            rept._update_habituation(threats)
        assert "ram" in rept._threat_habituation

        # Récupération complète (assez de ticks sans la menace)
        for _ in range(50):
            threats = {}
            rept._update_habituation(threats)
        # Nettoyé une fois le facteur revenu à 1.0
        assert "ram" not in rept._threat_habituation
        assert "ram" not in rept._threat_persistence

    def test_habituation_persisted(self, rept, tmp_path, monkeypatch):
        """save → load conserve l'habituation."""
        state_file = str(tmp_path / "reptilian_hab.json")
        monkeypatch.setattr("core.reptilian_core.REPTILIAN_STATE_FILE", state_file)

        for _ in range(30):
            threats = {"ram": 4.0}
            rept._update_habituation(threats)
        rept.save()

        ReptilianCore.reset_singleton()
        r2 = ReptilianCore()
        assert r2._threat_persistence.get("ram", 0) == 30
        assert r2._threat_habituation.get("ram", 1.0) < 1.0


# ============================================================
# Parasympathique — Nerf vague numérique
# ============================================================

class TestParasympathetic:
    """Tests du signal parasympathique (DOPAMINE_SURGE, EUREKA_MOMENT, etc.)."""

    @pytest.mark.asyncio
    async def test_dopamine_surge_calms(self, rept):
        """DOPAMINE_SURGE → réduction threat_level."""
        rept.threat_level = 6.0
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        assert rept.threat_level < 6.0

    @pytest.mark.asyncio
    async def test_relief_proportional_to_stress(self, rept):
        """Plus stressé → plus de soulagement (25% du threat actuel)."""
        # Cas 1 : stress élevé
        rept.threat_level = 8.0
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        after_high = rept.threat_level
        expected_high = 8.0 - min(2.0, 8.0 * 0.25)  # 8.0 - 2.0 = 6.0
        assert after_high == pytest.approx(expected_high)

        # Cas 2 : stress faible
        rept.threat_level = 2.0
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        after_low = rept.threat_level
        expected_low = 2.0 - min(2.0, 2.0 * 0.25)  # 2.0 - 0.5 = 1.5
        assert after_low == pytest.approx(expected_low)

    @pytest.mark.asyncio
    async def test_relief_capped_at_2(self, rept):
        """Le soulagement est plafonné à 2.0 même avec un threat très élevé."""
        rept.threat_level = 10.0
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        # 10.0 * 0.25 = 2.5 → cap à 2.0 → threat = 8.0
        assert rept.threat_level == pytest.approx(8.0)

    @pytest.mark.asyncio
    async def test_calm_when_already_calm(self, rept):
        """Quand threat est bas, quasi pas d'effet."""
        rept.threat_level = 0.5
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        # 0.5 * 0.25 = 0.125 → threat = 0.375
        assert rept.threat_level == pytest.approx(0.375)

    @pytest.mark.asyncio
    async def test_no_effect_at_zero(self, rept):
        """Aucun effet quand threat = 0."""
        rept.threat_level = 0.0
        rept.adrenaline = 0.0
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        assert rept.threat_level == 0.0
        assert rept.adrenaline == 0.0

    @pytest.mark.asyncio
    async def test_adrenaline_reduced(self, rept):
        """L'adrénaline est aussi réduite par le signal parasympathique."""
        rept.threat_level = 5.0
        rept.adrenaline = 0.8
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        assert rept.adrenaline == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_cardiac_soothe_called(self, rept):
        """heart.react('soothe') est appelé lors du signal parasympathique."""
        rept.threat_level = 5.0
        mock_heart = MagicMock()
        mock_cardiac = MagicMock(heart=mock_heart)
        with patch.dict("sys.modules", {"core.cardiac_engine": mock_cardiac}):
            await rept._on_parasympathetic_signal({})
        mock_heart.react.assert_called_once_with("soothe")

    @pytest.mark.asyncio
    async def test_threat_never_below_zero(self, rept):
        """Threat ne descend jamais sous 0 après signal parasympathique."""
        rept.threat_level = 0.1
        with patch.dict("sys.modules", {"core.cardiac_engine": MagicMock()}):
            await rept._on_parasympathetic_signal({})
        assert rept.threat_level >= 0.0


# ============================================================
# Decay Formula — Formule de décroissance améliorée
# ============================================================

class TestDecayFormula:
    """Tests de la formule decay corrigée (menace persistante descend lentement)."""

    def test_persistent_threat_slowly_decays(self, rept):
        """Même menace persistante → le threat_level descend lentement via blending."""
        rept.threat_level = 4.0
        new_level = 4.0
        # Appliquer la formule du watchdog_loop
        decayed = rept.threat_level * THREAT_DECAY_RATE
        if new_level >= decayed:
            result = new_level * 0.98 + decayed * 0.02
        else:
            result = decayed
        # 4.0 * 0.98 + (4.0 * 0.85) * 0.02 = 3.92 + 0.068 = 3.988
        assert result < 4.0
        assert result > 3.9

    def test_no_threat_normal_decay(self, rept):
        """Quand la menace disparaît, decay normal (×0.85)."""
        rept.threat_level = 5.0
        new_level = 0.0
        decayed = rept.threat_level * THREAT_DECAY_RATE
        if new_level >= decayed:
            result = new_level * 0.98 + decayed * 0.02
        else:
            result = decayed
        # decayed = 5.0 * 0.85 = 4.25, new_level=0 < 4.25 → result = 4.25
        assert result == pytest.approx(4.25)

    def test_decay_faster_with_habituation(self, rept):
        """Habituation + decay = descente rapide du threat_level."""
        # Simuler 50 ticks d'habituation sur la RAM
        for _ in range(50):
            threats = {"ram": 4.0}
            rept._update_habituation(threats)

        # Le facteur d'habituation est < 1.0
        hab = rept._threat_habituation.get("ram", 1.0)
        attenuated = 4.0 * hab  # Ex: 4.0 * 0.54 = 2.16

        # Compute threat → max(values) → attenuated
        new_level = attenuated
        rept.threat_level = 4.0  # Avant habituation

        decayed = rept.threat_level * THREAT_DECAY_RATE
        if new_level >= decayed:
            result = new_level * 0.98 + decayed * 0.02
        else:
            result = decayed

        # L'habituation réduit new_level → descent plus rapide
        assert result < 3.5  # Bien en dessous de l'ancien 4.0 bloqué

    def test_spike_overrides_decay(self, rept):
        """Un nouveau spike remonte le threat immédiatement."""
        rept.threat_level = 2.0
        new_level = 8.0  # Spike !
        decayed = rept.threat_level * THREAT_DECAY_RATE
        if new_level >= decayed:
            result = new_level * 0.98 + decayed * 0.02
        else:
            result = decayed
        # 8.0 * 0.98 + (2.0 * 0.85) * 0.02 = 7.84 + 0.034 = 7.874
        assert result > 7.5
        assert result < 8.0

    def test_combined_parasympathetic_and_decay(self, rept):
        """Les 3 effets cumulés (habituation + decay + parasympathique) descendent vite."""
        rept.threat_level = 4.0

        # 1. Habituation : 30 ticks
        for _ in range(30):
            threats = {"ram": 4.0}
            rept._update_habituation(threats)

        # 2. Menace atténuée
        threats = {"ram": 4.0}
        rept._update_habituation(threats)
        attenuated = threats["ram"]
        assert attenuated < 4.0

        # 3. Decay formula
        new_level = attenuated
        decayed = rept.threat_level * THREAT_DECAY_RATE
        if new_level >= decayed:
            rept.threat_level = new_level * 0.98 + decayed * 0.02
        else:
            rept.threat_level = decayed

        # 4. Signal parasympathique
        relief = min(2.0, rept.threat_level * 0.25)
        rept.threat_level = max(0.0, rept.threat_level - relief)

        # Résultat : bien en dessous de l'ancien 4.0 bloqué
        assert rept.threat_level < 3.0


# ============================================================
# TestHypothalamusAlarmPlancher
# ============================================================

class TestHypothalamusAlarmPlancher:
    """Tests du handler plancher (max au lieu de += additif)."""

    @pytest.mark.asyncio
    async def test_alarm_uses_floor_not_additive(self, rept):
        """max() au lieu de += : une alarme fixe un plancher."""
        rept.threat_level = 0.0
        await rept._on_hypothalamus_alarm({"variable": "stress", "severity": 1.0})
        # floor = min(3.0, 1.0 * 2.0) = 2.0
        assert rept.threat_level == 2.0

    @pytest.mark.asyncio
    async def test_multiple_alarms_no_accumulation(self, rept):
        """5 alarmes severity 1.0 = plancher 2.0, pas +2.5."""
        rept.threat_level = 0.0
        for var in ["energy", "stress", "dopamine", "cardiac_bpm", "sleep_pressure"]:
            await rept._on_hypothalamus_alarm({"variable": var, "severity": 1.0})
        # Ancien: 0 + 5*0.5 = 2.5 ; Nouveau: max(2.0, 2.0, ...) = 2.0
        assert rept.threat_level == 2.0

    @pytest.mark.asyncio
    async def test_alarm_severity_scales_floor(self, rept):
        """severity 0.5 → plancher 1.0."""
        rept.threat_level = 0.0
        await rept._on_hypothalamus_alarm({"variable": "energy", "severity": 0.5})
        assert rept.threat_level == 1.0

    @pytest.mark.asyncio
    async def test_alarm_floor_capped(self, rept):
        """Plancher max 3.0 même avec severity très élevée."""
        rept.threat_level = 0.0
        await rept._on_hypothalamus_alarm({"variable": "energy", "severity": 5.0})
        # floor = min(3.0, 5.0 * 2.0) = 3.0
        assert rept.threat_level == 3.0


# ============================================================
# Planchers absolus adaptatifs (anti faux-positifs RAM/CPU)
# ============================================================

class TestAdaptiveFloors:
    """Les seuils adaptatifs ne déclenchent que si la valeur est aussi au-dessus du plancher absolu."""

    def _make_mock_psutil(self, cpu_val: float, ram_val: float) -> MagicMock:
        """Crée un mock psutil complet pour _sense_threats."""
        mock = MagicMock()
        mock.cpu_percent.return_value = cpu_val
        mock.virtual_memory.return_value = MagicMock(percent=ram_val)
        mock.sensors_temperatures = MagicMock(side_effect=AttributeError)
        mock.Process.return_value.memory_info.return_value.rss = 500 * 1024 * 1024  # 500MB
        return mock

    @pytest.mark.asyncio
    async def test_ram_below_floor_no_threat(self, rept):
        """RAM à P95 mais valeur 65% (< floor 70%) → pas de threat."""
        rept._ram_tracker.is_ready = MagicMock(return_value=True)
        rept._ram_tracker.get_percentile_of = MagicMock(return_value=0.96)
        rept._ram_tracker.record = MagicMock()
        rept._cpu_tracker.is_ready = MagicMock(return_value=False)
        rept._cpu_tracker.record = MagicMock()

        mock_psutil = self._make_mock_psutil(30.0, 65.0)
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            threats = await rept._sense_threats()

        assert threats.get("ram", 0) == 0, "RAM 65% sous le floor 70% ne doit PAS déclencher de threat"

    @pytest.mark.asyncio
    async def test_ram_above_floor_triggers(self, rept):
        """RAM à P95 et valeur 82% (> floor 80%) → threat 8.0."""
        rept._ram_tracker.is_ready = MagicMock(return_value=True)
        rept._ram_tracker.get_percentile_of = MagicMock(return_value=0.96)
        rept._ram_tracker.record = MagicMock()
        rept._cpu_tracker.is_ready = MagicMock(return_value=False)
        rept._cpu_tracker.record = MagicMock()

        mock_psutil = self._make_mock_psutil(30.0, 82.0)
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            threats = await rept._sense_threats()

        assert threats.get("ram", 0) == 8.0, "RAM 82% au-dessus du floor 80% DOIT déclencher threat crit"

    @pytest.mark.asyncio
    async def test_cpu_below_floor_no_threat(self, rept):
        """CPU à P97 mais valeur 50% (< floor 60%) → pas de threat."""
        rept._cpu_tracker.is_ready = MagicMock(return_value=True)
        rept._cpu_tracker.get_percentile_of = MagicMock(return_value=0.98)
        rept._cpu_tracker.record = MagicMock()
        rept._ram_tracker.is_ready = MagicMock(return_value=False)
        rept._ram_tracker.record = MagicMock()

        mock_psutil = self._make_mock_psutil(50.0, 40.0)
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            threats = await rept._sense_threats()

        assert threats.get("cpu", 0) == 0, "CPU 50% sous le floor 60% ne doit PAS déclencher de threat"

    @pytest.mark.asyncio
    async def test_cpu_above_floor_triggers(self, rept):
        """CPU à P97 et valeur 90% (> floor 85%) → threat 8.0."""
        rept._cpu_tracker.is_ready = MagicMock(return_value=True)
        rept._cpu_tracker.get_percentile_of = MagicMock(return_value=0.98)
        rept._cpu_tracker.record = MagicMock()
        rept._ram_tracker.is_ready = MagicMock(return_value=False)
        rept._ram_tracker.record = MagicMock()

        mock_psutil = self._make_mock_psutil(90.0, 40.0)
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            threats = await rept._sense_threats()

        assert threats.get("cpu", 0) == 8.0, "CPU 90% au-dessus du floor 85% DOIT déclencher threat crit"


# ============================================================
# V14.3 Pilier 2 nocicepteurs — menace stale_dream
# ============================================================

import time as _time

class TestStaleDreamThreat:
    """Tests du couplage SYNAPTIC_DEBT_PRESSURE → reptilien.threat_memories['stale_dream']."""

    def _build_event(self, zscore: float, dette_h: float = 24.0):
        return {
            "dream_dette_h": dette_h,
            "zscore": zscore,
            "bump": 0.05,
            "pressure_after": 0.5,
        }

    @pytest.mark.asyncio
    async def test_premier_event_cree_threat_memory(self, rept):
        from core.reptilian_core import STALE_DREAM_PATTERN, STALE_DREAM_REFLEX
        assert STALE_DREAM_PATTERN not in rept.threat_memories
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=2.0))
        mem = rept.threat_memories.get(STALE_DREAM_PATTERN)
        assert mem is not None
        assert mem.severity == 4.0  # min(10, 2.0 * 2.0)
        assert mem.occurrences == 1
        assert mem.conditioned_reflex == STALE_DREAM_REFLEX

    @pytest.mark.asyncio
    async def test_severity_mise_a_jour_temps_reel_pas_moyenne(self, rept):
        """Bombardement : 5 events à z=2 puis z=4. Severity finale = 8 (pas une moyenne)."""
        from core.reptilian_core import STALE_DREAM_PATTERN
        for _ in range(5):
            await rept._on_synaptic_debt_pressure(self._build_event(zscore=2.0))
        mem = rept.threat_memories[STALE_DREAM_PATTERN]
        assert mem.severity == 4.0
        assert mem.occurrences == 1, "occurrences NE DOIT PAS exploser sur le bombardement"
        # Switch z=4
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=4.0))
        assert rept.threat_memories[STALE_DREAM_PATTERN].severity == 8.0

    @pytest.mark.asyncio
    async def test_severity_plafonnee_a_10(self, rept):
        from core.reptilian_core import STALE_DREAM_PATTERN
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=10.0))
        assert rept.threat_memories[STALE_DREAM_PATTERN].severity == 10.0

    @pytest.mark.asyncio
    async def test_zscore_negatif_no_op(self, rept):
        """Event reçu mais zscore <= 0 (dette sous baseline) → no-op."""
        from core.reptilian_core import STALE_DREAM_PATTERN
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=-1.0))
        assert STALE_DREAM_PATTERN not in rept.threat_memories
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=0.0))
        assert STALE_DREAM_PATTERN not in rept.threat_memories

    @pytest.mark.asyncio
    async def test_zscore_invalide_no_crash(self, rept):
        """Event malformé : pas de zscore, ou type non float."""
        await rept._on_synaptic_debt_pressure({})
        await rept._on_synaptic_debt_pressure({"zscore": "pas_un_float"})
        await rept._on_synaptic_debt_pressure({"zscore": None})
        # Ne doit PAS crasher

    @pytest.mark.asyncio
    async def test_alert_publie_si_severity_au_seuil(self, rept):
        """severity >= 5.0 (z=2.5) → REPTILIAN_ALERT publié."""
        from core.event_bus.bus import bus
        from core.reptilian_core import STALE_DREAM_PATTERN
        captured = []
        async def cap(ev): captured.append(dict(ev))
        bus.subscribe("REPTILIAN_ALERT", cap)
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=3.0))  # sev=6
        # Laisse le temps au bus
        await asyncio.sleep(0.05)
        assert len(captured) >= 1
        alert = captured[-1]
        assert alert["pattern"] == STALE_DREAM_PATTERN
        assert alert["severity"] >= 5.0

    @pytest.mark.asyncio
    async def test_alert_pas_publie_si_sous_seuil(self, rept):
        """severity < 5.0 (z=1.5..2.4) → pas d'alerte."""
        from core.event_bus.bus import bus
        captured = []
        async def cap(ev): captured.append(dict(ev))
        bus.subscribe("REPTILIAN_ALERT", cap)
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=2.0))  # sev=4
        await asyncio.sleep(0.05)
        assert len(captured) == 0

    @pytest.mark.asyncio
    async def test_alert_cooldown_respecte(self, rept):
        """2 events successifs au seuil → 1 seule alerte (cooldown)."""
        from core.event_bus.bus import bus
        captured = []
        async def cap(ev): captured.append(dict(ev))
        bus.subscribe("REPTILIAN_ALERT", cap)
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=3.0))
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=3.0))
        await rept._on_synaptic_debt_pressure(self._build_event(zscore=3.0))
        await asyncio.sleep(0.05)
        assert len(captured) == 1, "Cooldown de 120s doit empêcher les alertes répétées"

    def test_decay_pas_actif_si_recent(self, rept):
        """severity NE bouge PAS si refresh récent (dans la grâce)."""
        from core.reptilian_core import STALE_DREAM_PATTERN, STALE_DREAM_REFRESH_GRACE_S
        rept.threat_memories[STALE_DREAM_PATTERN] = ThreatMemory(
            pattern=STALE_DREAM_PATTERN, severity=6.0,
            occurrences=1, last_seen=_time.time() - 60,  # 1 min ago, dans la grâce
            conditioned_reflex="ADRENALINE",
        )
        rept._decay_stale_dream_threat()
        assert rept.threat_memories[STALE_DREAM_PATTERN].severity == 6.0

    def test_decay_actif_si_grace_depassee(self, rept):
        """severity diminue si non rafraîchie depuis grace."""
        from core.reptilian_core import (
            STALE_DREAM_PATTERN, STALE_DREAM_REFRESH_GRACE_S,
            STALE_DREAM_DECAY_PER_TICK,
        )
        rept.threat_memories[STALE_DREAM_PATTERN] = ThreatMemory(
            pattern=STALE_DREAM_PATTERN, severity=6.0,
            occurrences=1, last_seen=_time.time() - STALE_DREAM_REFRESH_GRACE_S - 10,
            conditioned_reflex="ADRENALINE",
        )
        rept._decay_stale_dream_threat()
        assert rept.threat_memories[STALE_DREAM_PATTERN].severity == 6.0 - STALE_DREAM_DECAY_PER_TICK

    def test_decay_supprime_si_sous_seuil(self, rept):
        """Si severity descend sous REMOVE_BELOW, threat retirée + cooldown nettoyé."""
        from core.reptilian_core import (
            STALE_DREAM_PATTERN, STALE_DREAM_REFRESH_GRACE_S,
        )
        rept.threat_memories[STALE_DREAM_PATTERN] = ThreatMemory(
            pattern=STALE_DREAM_PATTERN, severity=0.05,  # déjà très bas
            occurrences=1, last_seen=_time.time() - STALE_DREAM_REFRESH_GRACE_S - 10,
            conditioned_reflex="ADRENALINE",
        )
        rept._alert_cooldowns[STALE_DREAM_PATTERN] = _time.time()
        rept._decay_stale_dream_threat()
        assert STALE_DREAM_PATTERN not in rept.threat_memories
        assert STALE_DREAM_PATTERN not in rept._alert_cooldowns

    def test_decay_no_op_si_pas_de_menace(self, rept):
        """Si stale_dream absente, no-op safe."""
        # Pas de stale_dream dans threat_memories
        rept._decay_stale_dream_threat()  # Doit pas crasher

    @pytest.mark.asyncio
    async def test_subscribe_dans_subscribe_events(self, rept):
        """SYNAPTIC_DEBT_PRESSURE doit être dans les souscriptions du bus."""
        # Vérifier que le subscribe a bien lieu en appelant _subscribe_events
        from core.event_bus.bus import bus
        rept._subscribed = False
        rept._subscribe_events()
        # On vérifie indirectement : envoyer event via bus, le handler doit être appelé
        await bus.publish("SYNAPTIC_DEBT_PRESSURE", {"zscore": 3.0, "dream_dette_h": 24.0})
        await asyncio.sleep(0.05)
        from core.reptilian_core import STALE_DREAM_PATTERN
        assert STALE_DREAM_PATTERN in rept.threat_memories


# ============================================================
# Cicatrisation des fossiles de RESSOURCE (diagnostic « Fantôme du 14 juin »)
# ============================================================

class TestResourceFossilHealing:
    """Les threat_memories de ressource (cpu/ram/ollama/...) doivent décroître
    quand elles sont DORMANTES (condition disparue depuis > grâce), mais JAMAIS
    quand elles sont fraîches (détecteur actif rafraîchissant last_seen)."""

    def _make_mem(self, rept, pattern, severity, age_s):
        from core.reptilian_core import ThreatMemory
        rept.threat_memories[pattern] = ThreatMemory(
            pattern=pattern, severity=severity, occurrences=10,
            last_seen=time.time() - age_s, conditioned_reflex="FREEZE",
        )

    def test_fossile_dormant_s_erode(self, rept):
        """Un fossile dormant (vieux last_seen) perd de la severity à chaque tick."""
        from core.reptilian_core import RESOURCE_THREAT_GRACE_S, RESOURCE_THREAT_DECAY_PER_TICK
        self._make_mem(rept, "error_streak", severity=3.0, age_s=RESOURCE_THREAT_GRACE_S + 100)
        rept._decay_stale_dream_threat()
        assert rept.threat_memories["error_streak"].severity == pytest.approx(
            3.0 - RESOURCE_THREAT_DECAY_PER_TICK, abs=1e-6
        )

    def test_fossile_dormant_finit_supprime(self, rept):
        """Sous le seuil remove_below, le fossile disparaît de threat_memories."""
        from core.reptilian_core import RESOURCE_THREAT_GRACE_S
        self._make_mem(rept, "ollama", severity=0.15, age_s=RESOURCE_THREAT_GRACE_S + 100)
        rept._decay_stale_dream_threat()  # 0.15 - 0.1 = 0.05 < 0.1 remove_below
        assert "ollama" not in rept.threat_memories

    def test_memoire_fraiche_jamais_touchee(self, rept):
        """Grâce : une mémoire ressource récente (détecteur actif) ne décroît PAS."""
        self._make_mem(rept, "ram", severity=4.0, age_s=10)  # vue il y a 10s
        rept._decay_stale_dream_threat()
        assert "ram" in rept.threat_memories
        assert rept.threat_memories["ram"].severity == 4.0  # intacte

    def test_threat_level_inchange_par_erosion(self, rept):
        """L'érosion des fossiles ne touche PAS threat_level (= MAX détecteurs actifs)."""
        from core.reptilian_core import RESOURCE_THREAT_GRACE_S
        rept.threat_level = 1.5
        self._make_mem(rept, "cpu", severity=8.0, age_s=RESOURCE_THREAT_GRACE_S + 100)
        rept._decay_stale_dream_threat()
        assert rept.threat_level == 1.5

    def test_tous_les_patterns_ressource_couverts(self, rept):
        """Les 6 patterns ressource sont éligibles à l'érosion."""
        from core.reptilian_core import RESOURCE_THREAT_PATTERNS, RESOURCE_THREAT_GRACE_S
        for p in RESOURCE_THREAT_PATTERNS:
            self._make_mem(rept, p, severity=0.15, age_s=RESOURCE_THREAT_GRACE_S + 100)
        rept._decay_stale_dream_threat()
        for p in RESOURCE_THREAT_PATTERNS:
            assert p not in rept.threat_memories, f"{p} aurait du s'eroder"
