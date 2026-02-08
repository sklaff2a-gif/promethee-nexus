import pytest
import asyncio
from core.event_bus.bus import InMemoryEventBus, bus


class TestSingleton:

    def test_bus_is_singleton(self):
        bus1 = InMemoryEventBus()
        bus2 = InMemoryEventBus()
        assert bus1 is bus2

    def test_reset_clears_subscribers(self):
        bus.subscribe("TEST", lambda x: None)
        assert len(bus.subscribers) > 0
        bus.reset()
        assert len(bus.subscribers) == 0


class TestSubscribe:

    def test_subscribe_adds_callback(self):
        cb = lambda x: None
        bus.subscribe("EVENT_A", cb)
        assert cb in bus.subscribers["EVENT_A"]

    def test_subscribe_multiple_callbacks(self):
        cb1 = lambda x: None
        cb2 = lambda x: None
        bus.subscribe("EVENT_B", cb1)
        bus.subscribe("EVENT_B", cb2)
        assert len(bus.subscribers["EVENT_B"]) == 2

    def test_subscribe_different_events(self):
        bus.subscribe("EV1", lambda x: None)
        bus.subscribe("EV2", lambda x: None)
        assert "EV1" in bus.subscribers
        assert "EV2" in bus.subscribers


class TestPublish:

    @pytest.mark.asyncio
    async def test_publish_calls_sync_callback(self):
        received = []
        bus.subscribe("SYNC_TEST", lambda data: received.append(data))

        await bus.publish("SYNC_TEST", {"msg": "hello"})
        assert len(received) == 1
        assert received[0]["msg"] == "hello"

    @pytest.mark.asyncio
    async def test_publish_calls_async_callback(self):
        received = []

        async def async_cb(data):
            received.append(data)

        bus.subscribe("ASYNC_TEST", async_cb)
        await bus.publish("ASYNC_TEST", {"msg": "async"})

        # Laisser le temps au create_task de s'exécuter
        await asyncio.sleep(0.05)
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self):
        """Publier sans abonnés ne doit pas planter."""
        await bus.publish("NO_SUB_EVENT", {"test": True})

    @pytest.mark.asyncio
    async def test_wildcard_receives_all_events(self):
        received = []
        bus.subscribe("*", lambda data: received.append(data))

        await bus.publish("EVENT_X", {"val": 1})
        await bus.publish("EVENT_Y", {"val": 2})

        assert len(received) == 2
        # Les wildcards reçoivent un payload enveloppé
        assert received[0]["type"] == "EVENT_X"
        assert received[0]["data"]["val"] == 1
        assert received[1]["type"] == "EVENT_Y"

    @pytest.mark.asyncio
    async def test_direct_and_wildcard_both_called(self):
        direct = []
        wildcard = []

        bus.subscribe("DUAL_TEST", lambda d: direct.append(d))
        bus.subscribe("*", lambda d: wildcard.append(d))

        await bus.publish("DUAL_TEST", {"key": "value"})

        assert len(direct) == 1
        assert len(wildcard) == 1
        # Direct reçoit le payload brut
        assert direct[0]["key"] == "value"
        # Wildcard reçoit le payload enveloppé
        assert wildcard[0]["data"]["key"] == "value"


class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash_bus(self):
        """Un callback qui plante ne doit pas empêcher les autres."""
        received = []

        def bad_cb(data):
            raise ValueError("boom")

        def good_cb(data):
            received.append(data)

        bus.subscribe("ERR_TEST", bad_cb)
        bus.subscribe("ERR_TEST", good_cb)

        await bus.publish("ERR_TEST", {"test": True})
        assert len(received) == 1
