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
    ADRENALINE_DECAY,
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
