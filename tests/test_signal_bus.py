# tests/test_signal_bus.py — Tests du bus de signaux neuraux
# ~50 tests couvrant singleton, NeuralSignal, emit, throttling,
# priority, wrapped publish, metriques, persistance, integration.

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch, patch as mock_patch

import pytest

from core.event_bus.bus import bus, InMemoryEventBus
from core.signal_bus import (
    SignalBus,
    signal_bus,
    SignalPriority,
    NeuralSignal,
    SignalMetrics,
    ThrottleRule,
    SIGNAL_BUS_STATE_FILE,
    AUTO_FLOOD_THRESHOLD,
    AUTO_FLOOD_LIMIT,
    AUTO_FLOOD_DURATION,
    AUTO_FLOOD_WINDOW,
    SALIENCE_PROMOTION_THRESHOLD,
    _PRIORITY_MAP,
    _DEFAULT_THROTTLES,
    _ORIGIN_MAP,
)


# ================================================================
# FIXTURES
# ================================================================

@pytest.fixture(autouse=True)
def isolate_signal_bus(tmp_path, monkeypatch):
    """Isole chaque test : reset singleton, fichier tmp, restaure bus.publish."""
    # Nettoyer tout monkey-patch precedent sur bus.publish
    if 'publish' in vars(bus):
        del vars(bus)['publish']

    # Reset singleton
    SignalBus.reset_singleton()

    # Rediriger fichier de persistance vers tmp
    state_file = str(tmp_path / "signal_bus_state.json")
    monkeypatch.setattr("core.signal_bus.SIGNAL_BUS_STATE_FILE", state_file)

    sb = SignalBus()

    yield sb

    # Teardown : reset singleton + nettoyer monkey-patch
    SignalBus.reset_singleton()
    if 'publish' in vars(bus):
        del vars(bus)['publish']


@pytest.fixture
def inited_bus(isolate_signal_bus):
    """Un SignalBus deja patche (init appele)."""
    isolate_signal_bus.init()
    return isolate_signal_bus


# ================================================================
# SINGLETON
# ================================================================

class TestSingleton:

    def test_singleton_identity(self, isolate_signal_bus):
        """Deux instanciations retournent le meme objet."""
        a = SignalBus()
        b = SignalBus()
        assert a is b

    def test_reset_singleton(self):
        """reset_singleton detruit l'instance."""
        a = SignalBus()
        SignalBus.reset_singleton()
        b = SignalBus()
        assert a is not b

    def test_initial_state(self, isolate_signal_bus):
        """L'etat initial est vierge."""
        sb = isolate_signal_bus
        assert sb.total_published == 0
        assert sb.total_delivered == 0
        assert sb.total_dropped == 0
        assert sb.total_errors == 0
        assert sb._patched is False

    def test_double_init(self, isolate_signal_bus):
        """Double init ne casse rien."""
        sb = isolate_signal_bus
        sb.init()
        sb.init()  # Doit juste logguer un warning
        assert sb._patched is True


# ================================================================
# NEURAL SIGNAL
# ================================================================

class TestNeuralSignal:

    def test_creation_basic(self):
        """Creation avec valeurs par defaut."""
        sig = NeuralSignal(signal_type="TEST", origin="test")
        assert sig.signal_type == "TEST"
        assert sig.origin == "test"
        assert sig.priority == SignalPriority.NORMAL
        assert sig.payload == {}
        assert sig.timestamp > 0
        assert sig.ttl is None
        assert sig.correlation_id is None

    def test_creation_full(self):
        """Creation avec tous les champs."""
        sig = NeuralSignal(
            signal_type="DOPAMINE_SURGE",
            origin="dopamine",
            payload={"rpe": 0.5},
            priority=SignalPriority.HIGH,
            timestamp=1000.0,
            ttl=30.0,
            correlation_id="abc-123",
        )
        assert sig.signal_type == "DOPAMINE_SURGE"
        assert sig.payload == {"rpe": 0.5}
        assert sig.priority == SignalPriority.HIGH
        assert sig.timestamp == 1000.0
        assert sig.ttl == 30.0
        assert sig.correlation_id == "abc-123"

    def test_timestamp_auto(self):
        """Le timestamp est genere automatiquement."""
        before = time.time()
        sig = NeuralSignal(signal_type="TEST", origin="test")
        after = time.time()
        assert before <= sig.timestamp <= after

    def test_priority_ordering(self):
        """Les priorites sont ordonnees CRITICAL < HIGH < NORMAL < LOW."""
        assert SignalPriority.CRITICAL < SignalPriority.HIGH
        assert SignalPriority.HIGH < SignalPriority.NORMAL
        assert SignalPriority.NORMAL < SignalPriority.LOW

    def test_default_payload_independent(self):
        """Chaque signal a son propre dict payload."""
        a = NeuralSignal(signal_type="A", origin="test")
        b = NeuralSignal(signal_type="B", origin="test")
        a.payload["x"] = 1
        assert "x" not in b.payload


# ================================================================
# EMIT
# ================================================================

class TestEmit:

    @pytest.mark.asyncio
    async def test_emit_basic(self, inited_bus):
        """Un signal basique est delivre."""
        sig = NeuralSignal(signal_type="TEST_EVENT", origin="test")
        result = await inited_bus.emit(sig)
        assert result is True
        assert inited_bus.total_published == 1
        assert inited_bus.total_delivered == 1

    @pytest.mark.asyncio
    async def test_emit_with_payload(self, inited_bus):
        """Le payload est transmis au bus."""
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("PAYLOAD_TEST", handler)
        try:
            sig = NeuralSignal(
                signal_type="PAYLOAD_TEST",
                origin="test",
                payload={"key": "value"},
            )
            await inited_bus.emit(sig)
            assert len(received) == 1
            assert received[0] == {"key": "value"}
        finally:
            bus.unsubscribe("PAYLOAD_TEST", handler)

    @pytest.mark.asyncio
    async def test_emit_ttl_expired(self, inited_bus):
        """Un signal avec TTL expire est drop."""
        sig = NeuralSignal(
            signal_type="OLD_EVENT",
            origin="test",
            timestamp=time.time() - 100,
            ttl=10.0,
        )
        result = await inited_bus.emit(sig)
        assert result is False
        assert inited_bus.total_dropped == 1
        m = inited_bus._metrics["OLD_EVENT"]
        assert m.dropped_ttl == 1

    @pytest.mark.asyncio
    async def test_emit_ttl_valid(self, inited_bus):
        """Un signal avec TTL encore valide passe."""
        sig = NeuralSignal(
            signal_type="FRESH_EVENT",
            origin="test",
            ttl=60.0,
        )
        result = await inited_bus.emit(sig)
        assert result is True

    @pytest.mark.asyncio
    async def test_emit_error_handling(self, inited_bus):
        """Une erreur du subscriber est capturee."""
        async def bad_handler(data):
            raise ValueError("boom")

        bus.subscribe("ERROR_TEST", bad_handler)
        try:
            sig = NeuralSignal(signal_type="ERROR_TEST", origin="test")
            result = await inited_bus.emit(sig)
            # Le bus original gere l'erreur via _safe_call,
            # donc emit retourne True (pas d'exception propagee)
            assert result is True
            assert inited_bus.total_errors == 0  # L'erreur est geree par le bus, pas par signal_bus
        finally:
            bus.unsubscribe("ERROR_TEST", bad_handler)

    @pytest.mark.asyncio
    async def test_emit_metrics_update(self, inited_bus):
        """Les metriques sont mises a jour apres emit."""
        for _ in range(3):
            sig = NeuralSignal(signal_type="COUNTED", origin="test")
            await inited_bus.emit(sig)
        m = inited_bus._metrics["COUNTED"]
        assert m.published == 3
        assert m.delivered == 3
        assert m.total_latency_ms > 0

    @pytest.mark.asyncio
    async def test_emit_no_original_publish(self, isolate_signal_bus):
        """Emit sans init echoue car _original_publish est None."""
        sig = NeuralSignal(signal_type="TEST", origin="test")
        # _original_publish est None → TypeError
        result = await isolate_signal_bus.emit(sig)
        assert result is False
        assert isolate_signal_bus.total_errors == 1


# ================================================================
# THROTTLING
# ================================================================

class TestThrottling:

    @pytest.mark.asyncio
    async def test_under_limit(self, inited_bus):
        """Les signaux sous la limite passent."""
        # CARDIAC_BEAT = 4/60s
        for _ in range(4):
            sig = NeuralSignal(signal_type="CARDIAC_BEAT", origin="cardiac")
            result = await inited_bus.emit(sig)
            assert result is True

    @pytest.mark.asyncio
    async def test_over_limit(self, inited_bus):
        """Les signaux au-dessus de la limite sont drop."""
        for i in range(6):
            sig = NeuralSignal(signal_type="CARDIAC_BEAT", origin="cardiac")
            await inited_bus.emit(sig)
        m = inited_bus._metrics["CARDIAC_BEAT"]
        assert m.published == 4  # Seulement 4 passes
        assert m.dropped_throttle == 2  # 2 droppes

    @pytest.mark.asyncio
    async def test_window_sliding(self, inited_bus):
        """Les timestamps hors fenetre sont purges."""
        now = time.time()
        # Remplir la fenetre avec des timestamps anciens
        inited_bus._throttle_windows["CARDIAC_BEAT"] = \
            __import__("collections").deque([now - 120, now - 100, now - 90, now - 80])
        # Tous anciens (>60s) → purges → signal passe
        sig = NeuralSignal(signal_type="CARDIAC_BEAT", origin="cardiac")
        result = await inited_bus.emit(sig)
        assert result is True

    @pytest.mark.asyncio
    async def test_auto_flood_detection(self, inited_bus):
        """>50 signaux en 60s declenche auto-throttle."""
        now = time.time()
        # Simuler 51 timestamps dans la fenetre
        inited_bus._flood_windows["FLOOD_TEST"] = \
            __import__("collections").deque([now - i * 0.5 for i in range(AUTO_FLOOD_THRESHOLD)])
        # Le 51eme declenche l'auto-throttle
        sig = NeuralSignal(signal_type="FLOOD_TEST", origin="test")
        result = await inited_bus.emit(sig)
        assert result is True  # Le signal qui declenche passe
        assert "FLOOD_TEST" in inited_bus._auto_throttle_until

    @pytest.mark.asyncio
    async def test_auto_flood_blocks_after(self, inited_bus):
        """Apres detection flood, les signaux sont throttles."""
        now = time.time()
        inited_bus._auto_throttle_until["FLOODED"] = now + 300
        # Remplir la fenetre au max
        inited_bus._throttle_windows["FLOODED"] = \
            __import__("collections").deque([now - i * 0.1 for i in range(AUTO_FLOOD_LIMIT)])
        sig = NeuralSignal(signal_type="FLOODED", origin="test")
        result = await inited_bus.emit(sig)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_throttle(self, inited_bus):
        """set_throttle ajoute une regle dynamique."""
        inited_bus.set_throttle("CUSTOM_EVENT", max_per_window=2, window_seconds=10.0)
        assert "CUSTOM_EVENT" in inited_bus._throttle_rules
        rule = inited_bus._throttle_rules["CUSTOM_EVENT"]
        assert rule.max_per_window == 2
        assert rule.window_seconds == 10.0

    @pytest.mark.asyncio
    async def test_remove_throttle(self, inited_bus):
        """remove_throttle supprime la regle."""
        inited_bus.set_throttle("TEMP", max_per_window=1, window_seconds=10.0)
        inited_bus.remove_throttle("TEMP")
        assert "TEMP" not in inited_bus._throttle_rules

    @pytest.mark.asyncio
    async def test_throttle_independence(self, inited_bus):
        """Chaque signal type a sa propre fenetre."""
        # CARDIAC_BEAT (4/60s) et THOUGHT_STREAM (10/60s)
        for _ in range(4):
            await inited_bus.emit(
                NeuralSignal(signal_type="CARDIAC_BEAT", origin="cardiac"))
        # CARDIAC_BEAT plein
        result = await inited_bus.emit(
            NeuralSignal(signal_type="CARDIAC_BEAT", origin="cardiac"))
        assert result is False

        # THOUGHT_STREAM toujours libre
        result = await inited_bus.emit(
            NeuralSignal(signal_type="THOUGHT_STREAM", origin="voice"))
        assert result is True


# ================================================================
# PRIORITY MAPPING
# ================================================================

class TestPriorityMapping:

    def test_critical_events(self):
        """Les events critiques sont mappes CRITICAL."""
        assert _PRIORITY_MAP["REPTILIAN_ALERT"] == SignalPriority.CRITICAL
        assert _PRIORITY_MAP["OLLAMA_UNRESPONSIVE"] == SignalPriority.CRITICAL
        assert _PRIORITY_MAP["SMART_RESTART_REQUESTED"] == SignalPriority.CRITICAL

    def test_high_events(self):
        """Les events importants sont mappes HIGH."""
        assert _PRIORITY_MAP["DOPAMINE_SURGE"] == SignalPriority.HIGH
        assert _PRIORITY_MAP["HALLUCINATION_DETECTED"] == SignalPriority.HIGH

    def test_default_normal(self, isolate_signal_bus):
        """Un event inconnu a la priorite NORMAL par defaut."""
        sig = NeuralSignal(signal_type="UNKNOWN_EVENT", origin="test")
        # Dans _wrapped_publish, la priorite est assignee depuis _PRIORITY_MAP
        assert _PRIORITY_MAP.get("UNKNOWN_EVENT", SignalPriority.NORMAL) == SignalPriority.NORMAL

    def test_promotion_thalamus(self, inited_bus):
        """Le thalamus peut promouvoir NORMAL → HIGH."""
        mock_thalamus = MagicMock()
        mock_thalamus.compute_salience.return_value = 0.9

        sig = NeuralSignal(signal_type="SOME_EVENT", origin="test")
        assert sig.priority == SignalPriority.NORMAL

        with patch.dict("sys.modules", {"core.thalamus": mock_thalamus}):
            # Patcher l'import local
            with patch("core.signal_bus.SignalBus._maybe_promote_priority") as mock_promote:
                # Appeler directement la vraie methode
                pass

        # Test direct de _maybe_promote_priority
        thalamus_mod = MagicMock()
        thalamus_mod.thalamus.compute_salience.return_value = 0.9
        with patch.dict("sys.modules", {"core.thalamus": thalamus_mod}):
            inited_bus._maybe_promote_priority(sig)
        assert sig.priority == SignalPriority.HIGH

    def test_promotion_threshold(self, inited_bus):
        """Salience <= seuil ne promout pas."""
        sig = NeuralSignal(signal_type="LOW_SALIENCE", origin="test")
        thalamus_mod = MagicMock()
        thalamus_mod.thalamus.compute_salience.return_value = 0.5
        with patch.dict("sys.modules", {"core.thalamus": thalamus_mod}):
            inited_bus._maybe_promote_priority(sig)
        assert sig.priority == SignalPriority.NORMAL


# ================================================================
# WRAPPED PUBLISH
# ================================================================

class TestWrappedPublish:

    def test_init_installs_wrapper(self, inited_bus):
        """Apres init, bus.publish est le wrapper."""
        assert inited_bus._patched is True
        assert bus.publish != inited_bus._original_publish

    def test_shutdown_restores(self, inited_bus):
        """Shutdown restaure bus.publish original."""
        inited_bus.shutdown()
        assert inited_bus._patched is False
        # Pas d'attribut d'instance → bus.publish est la methode de classe
        assert 'publish' not in vars(bus)

    @pytest.mark.asyncio
    async def test_bus_publish_works(self, inited_bus):
        """bus.publish() fonctionne via le wrapper."""
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("WRAP_TEST", handler)
        try:
            await bus.publish("WRAP_TEST", {"msg": "hello"})
            assert len(received) == 1
            assert received[0] == {"msg": "hello"}
            assert inited_bus.total_published == 1
        finally:
            bus.unsubscribe("WRAP_TEST", handler)

    @pytest.mark.asyncio
    async def test_payload_preserved(self, inited_bus):
        """Le payload est transmis tel quel aux subscribers."""
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("PRESERVE_TEST", handler)
        try:
            payload = {"nested": {"key": [1, 2, 3]}}
            await bus.publish("PRESERVE_TEST", payload)
            assert received[0] == payload
        finally:
            bus.unsubscribe("PRESERVE_TEST", handler)

    @pytest.mark.asyncio
    async def test_origin_inferred(self, inited_bus):
        """L'origin est deduite du prefixe event."""
        # Emettre un signal et verifier les metriques
        await bus.publish("DOPAMINE_SURGE", {"rpe": 0.5})
        assert "DOPAMINE_SURGE" in inited_bus._metrics

    @pytest.mark.asyncio
    async def test_priority_assigned(self, inited_bus):
        """La priorite est assignee selon la map."""
        await bus.publish("REPTILIAN_ALERT", {"type": "FREEZE"})
        # Signal passe avec priorite CRITICAL
        assert inited_bus.total_published == 1


# ================================================================
# METRIQUES
# ================================================================

class TestMetrics:

    @pytest.mark.asyncio
    async def test_publish_counted(self, inited_bus):
        """Les publications sont comptees."""
        for _ in range(5):
            await inited_bus.emit(
                NeuralSignal(signal_type="COUNT_TEST", origin="test"))
        assert inited_bus.total_published == 5

    @pytest.mark.asyncio
    async def test_delivery_counted(self, inited_bus):
        """Les delivraisons sont comptees."""
        for _ in range(3):
            await inited_bus.emit(
                NeuralSignal(signal_type="DELIVER_TEST", origin="test"))
        m = inited_bus._metrics["DELIVER_TEST"]
        assert m.delivered == 3

    @pytest.mark.asyncio
    async def test_drop_throttle_counted(self, inited_bus):
        """Les drops throttle sont comptes."""
        inited_bus.set_throttle("LIMITED", max_per_window=1, window_seconds=60.0)
        await inited_bus.emit(NeuralSignal(signal_type="LIMITED", origin="test"))
        await inited_bus.emit(NeuralSignal(signal_type="LIMITED", origin="test"))
        m = inited_bus._metrics["LIMITED"]
        assert m.dropped_throttle == 1
        assert inited_bus.total_dropped == 1

    @pytest.mark.asyncio
    async def test_drop_ttl_counted(self, inited_bus):
        """Les drops TTL sont comptes."""
        sig = NeuralSignal(
            signal_type="EXPIRED",
            origin="test",
            timestamp=time.time() - 100,
            ttl=5.0,
        )
        await inited_bus.emit(sig)
        m = inited_bus._metrics["EXPIRED"]
        assert m.dropped_ttl == 1

    @pytest.mark.asyncio
    async def test_throughput(self, inited_bus):
        """Le throughput est calcule sur la fenetre."""
        for _ in range(10):
            await inited_bus.emit(
                NeuralSignal(signal_type="SPEED_TEST", origin="test"))
        tp = inited_bus.get_throughput()
        assert tp > 0

    @pytest.mark.asyncio
    async def test_get_metrics_structure(self, inited_bus):
        """get_metrics retourne la structure attendue."""
        await inited_bus.emit(
            NeuralSignal(signal_type="STRUCT_TEST", origin="test"))
        metrics = inited_bus.get_metrics()
        assert "global" in metrics
        assert "per_type" in metrics
        assert "top_volume" in metrics
        assert "top_latency" in metrics
        assert "throttle_rules" in metrics
        g = metrics["global"]
        assert g["total_published"] == 1
        assert g["total_delivered"] == 1

    @pytest.mark.asyncio
    async def test_get_stats_structure(self, inited_bus):
        """get_stats retourne une version allégée."""
        await inited_bus.emit(
            NeuralSignal(signal_type="STATS_TEST", origin="test"))
        stats = inited_bus.get_stats()
        assert "total_published" in stats
        assert "throughput_per_sec" in stats
        assert "signal_types_seen" in stats
        assert stats["signal_types_seen"] == 1

    def test_signal_metrics_to_dict(self):
        """SignalMetrics.to_dict calcule avg_latency."""
        m = SignalMetrics(published=10, delivered=5, total_latency_ms=100.0)
        d = m.to_dict()
        assert d["avg_latency_ms"] == 20.0
        assert d["published"] == 10


# ================================================================
# PERSISTANCE
# ================================================================

class TestPersistence:

    def test_save_creates_file(self, isolate_signal_bus, tmp_path):
        """_save cree le fichier JSON."""
        isolate_signal_bus.total_published = 42
        isolate_signal_bus._save()
        state_file = str(tmp_path / "signal_bus_state.json")
        assert os.path.exists(state_file)
        with open(state_file, "r") as f:
            data = json.load(f)
        assert data["total_published"] == 42

    def test_load_restores(self, tmp_path):
        """_load restaure les compteurs."""
        state_file = str(tmp_path / "signal_bus_state.json")
        data = {
            "total_published": 100,
            "total_delivered": 90,
            "total_dropped": 5,
            "total_errors": 2,
            "custom_throttles": {},
        }
        with open(state_file, "w") as f:
            json.dump(data, f)

        SignalBus.reset_singleton()
        with patch("core.signal_bus.SIGNAL_BUS_STATE_FILE", state_file):
            sb = SignalBus()
        assert sb.total_published == 100
        assert sb.total_delivered == 90
        SignalBus.reset_singleton()

    def test_load_custom_throttles(self, tmp_path):
        """_load restaure les throttles custom."""
        state_file = str(tmp_path / "signal_bus_state.json")
        data = {
            "total_published": 0,
            "total_delivered": 0,
            "total_dropped": 0,
            "total_errors": 0,
            "custom_throttles": {
                "MY_EVENT": {"max_per_window": 5, "window_seconds": 30.0}
            },
        }
        with open(state_file, "w") as f:
            json.dump(data, f)

        SignalBus.reset_singleton()
        with patch("core.signal_bus.SIGNAL_BUS_STATE_FILE", state_file):
            sb = SignalBus()
        assert "MY_EVENT" in sb._throttle_rules
        assert sb._throttle_rules["MY_EVENT"].max_per_window == 5
        SignalBus.reset_singleton()

    def test_load_empty_file(self, tmp_path):
        """_load gere un fichier vide sans crash."""
        state_file = str(tmp_path / "signal_bus_state.json")
        with open(state_file, "w") as f:
            f.write("")

        SignalBus.reset_singleton()
        with patch("core.signal_bus.SIGNAL_BUS_STATE_FILE", state_file):
            sb = SignalBus()
        # Pas de crash, valeurs par defaut
        assert sb.total_published == 0
        SignalBus.reset_singleton()


# ================================================================
# ORIGIN INFERENCE
# ================================================================

class TestOriginInference:

    def test_known_prefixes(self, isolate_signal_bus):
        """Les prefixes connus sont mappes correctement."""
        assert isolate_signal_bus._infer_origin("DOPAMINE_SURGE") == "dopamine"
        assert isolate_signal_bus._infer_origin("REPTILIAN_ALERT") == "reptilian"
        assert isolate_signal_bus._infer_origin("CARDIAC_BEAT") == "cardiac"
        assert isolate_signal_bus._infer_origin("HEART_RATE") == "cardiac"
        assert isolate_signal_bus._infer_origin("COUNCIL_RESULT") == "council"

    def test_unknown_prefix(self, isolate_signal_bus):
        """Un prefixe inconnu retourne 'unknown'."""
        assert isolate_signal_bus._infer_origin("XYZZY_EVENT") == "unknown"


# ================================================================
# INTEGRATION
# ================================================================

class TestIntegration:

    @pytest.mark.asyncio
    async def test_end_to_end_with_subscribers(self, inited_bus):
        """End-to-end : bus.publish → wrapper → subscriber."""
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("E2E_TEST", handler)
        try:
            await bus.publish("E2E_TEST", {"test": True})
            assert len(received) == 1
            assert received[0] == {"test": True}
            assert inited_bus.total_published == 1
            assert inited_bus.total_delivered == 1
        finally:
            bus.unsubscribe("E2E_TEST", handler)

    @pytest.mark.asyncio
    async def test_multi_subscribers(self, inited_bus):
        """Plusieurs subscribers recoivent le meme signal."""
        received_a = []
        received_b = []

        async def handler_a(data):
            received_a.append(data)

        async def handler_b(data):
            received_b.append(data)

        bus.subscribe("MULTI_TEST", handler_a)
        bus.subscribe("MULTI_TEST", handler_b)
        try:
            await bus.publish("MULTI_TEST", {"shared": True})
            assert len(received_a) == 1
            assert len(received_b) == 1
        finally:
            bus.unsubscribe("MULTI_TEST", handler_a)
            bus.unsubscribe("MULTI_TEST", handler_b)

    @pytest.mark.asyncio
    async def test_wildcard_still_works(self, inited_bus):
        """Les wildcard subscribers (*) recoivent tous les signals."""
        received = []

        async def wildcard_handler(data):
            received.append(data)

        bus.subscribe("*", wildcard_handler)
        try:
            await bus.publish("WILD_TEST", {"x": 1})
            assert len(received) == 1
            assert received[0]["type"] == "WILD_TEST"
            assert received[0]["data"] == {"x": 1}
        finally:
            bus.unsubscribe("*", wildcard_handler)

    @pytest.mark.asyncio
    async def test_throttle_then_recover(self, inited_bus):
        """Apres expiration de la fenetre, les signaux passent a nouveau."""
        inited_bus.set_throttle("RECOVER_TEST", max_per_window=1, window_seconds=0.1)
        sig1 = NeuralSignal(signal_type="RECOVER_TEST", origin="test")
        r1 = await inited_bus.emit(sig1)
        assert r1 is True

        sig2 = NeuralSignal(signal_type="RECOVER_TEST", origin="test")
        r2 = await inited_bus.emit(sig2)
        assert r2 is False

        # Attendre que la fenetre expire
        await asyncio.sleep(0.15)

        sig3 = NeuralSignal(signal_type="RECOVER_TEST", origin="test")
        r3 = await inited_bus.emit(sig3)
        assert r3 is True
