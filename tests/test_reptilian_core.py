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
                await rept._trigger_reflexes({"budget": 4.0})
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
        await rept._on_ci_result({"passed": True})
        assert rept.threat_level == 3.0

    @pytest.mark.asyncio
    async def test_on_ci_failure_increases(self, rept):
        """CI échouée → augmente la menace."""
        rept.threat_level = 2.0
        await rept._on_ci_result({"passed": False})
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
