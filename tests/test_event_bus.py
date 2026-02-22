import pytest
import asyncio
from core.event_bus.bus import InMemoryEventBus, DeadLetter, bus


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


class TestUnsubscribe:

    def test_unsubscribe_removes_callback(self):
        cb = lambda x: None
        bus.subscribe("UNSUB_TEST", cb)
        assert cb in bus.subscribers["UNSUB_TEST"]
        bus.unsubscribe("UNSUB_TEST", cb)
        assert cb not in bus.subscribers["UNSUB_TEST"]

    def test_unsubscribe_nonexistent_callback(self):
        """Desabonner un callback inexistant ne doit pas planter."""
        bus.subscribe("UNSUB2", lambda x: None)
        bus.unsubscribe("UNSUB2", lambda x: None)  # Callback different

    def test_unsubscribe_nonexistent_event(self):
        """Desabonner d'un event inexistant ne doit pas planter."""
        bus.unsubscribe("NEVER_EXISTED", lambda x: None)

    @pytest.mark.asyncio
    async def test_unsubscribed_callback_not_called(self):
        received = []
        cb = lambda data: received.append(data)
        bus.subscribe("LIFECYCLE", cb)
        await bus.publish("LIFECYCLE", {"msg": "before"})
        assert len(received) == 1

        bus.unsubscribe("LIFECYCLE", cb)
        await bus.publish("LIFECYCLE", {"msg": "after"})
        assert len(received) == 1  # Pas d'appel supplementaire


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

        # Callbacks async sont désormais awaités directement (pas de fire-and-forget)
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


class TestDeadLetterQueue:

    def setup_method(self):
        bus.reset()

    @pytest.mark.asyncio
    async def test_failed_sync_callback_creates_dead_letter(self):
        """Un callback sync qui raise crée une entrée DLQ."""
        def bad_cb(data):
            raise ValueError("boom")

        bus.subscribe("DLQ_TEST", bad_cb)
        await bus.publish("DLQ_TEST", {"key": "val"})

        assert bus.dead_letter_count == 1

    @pytest.mark.asyncio
    async def test_failed_async_callback_creates_dead_letter(self):
        """Un callback async qui raise crée aussi une entrée DLQ (fix C1)."""
        async def bad_async_cb(data):
            raise RuntimeError("async boom")

        bus.subscribe("DLQ_ASYNC", bad_async_cb)
        await bus.publish("DLQ_ASYNC", {"key": "val"})

        assert bus.dead_letter_count == 1
        dl = bus.get_dead_letters()[0]
        assert dl.event_type == "DLQ_ASYNC"
        assert dl.error == "async boom"
        assert dl.error_type == "RuntimeError"

    @pytest.mark.asyncio
    async def test_failed_async_does_not_block_others(self):
        """Un callback async en erreur ne bloque pas les suivants."""
        received = []

        async def bad_async_cb(data):
            raise ValueError("fail")

        async def good_async_cb(data):
            received.append(data)

        bus.subscribe("ASYNC_MIX", bad_async_cb)
        bus.subscribe("ASYNC_MIX", good_async_cb)
        await bus.publish("ASYNC_MIX", {"ok": True})

        assert len(received) == 1
        assert bus.dead_letter_count == 1

    @pytest.mark.asyncio
    async def test_dead_letter_contains_metadata(self):
        """Le dead letter contient toutes les métadonnées attendues."""
        def bad_cb(data):
            raise TypeError("type error")

        bus.subscribe("META_TEST", bad_cb)
        await bus.publish("META_TEST", {"data": 42})

        letters = bus.get_dead_letters()
        assert len(letters) == 1
        dl = letters[0]
        assert dl.event_type == "META_TEST"
        assert dl.payload == {"data": 42}
        assert "bad_cb" in dl.callback_name
        assert dl.error == "type error"
        assert dl.error_type == "TypeError"
        assert dl.timestamp > 0

    @pytest.mark.asyncio
    async def test_failed_callback_does_not_block_others(self):
        """Un callback en erreur ne bloque pas les autres ET crée un dead letter."""
        received = []

        def bad_cb(data):
            raise RuntimeError("fail")

        def good_cb(data):
            received.append(data)

        bus.subscribe("MIXED_TEST", bad_cb)
        bus.subscribe("MIXED_TEST", good_cb)
        await bus.publish("MIXED_TEST", {"ok": True})

        assert len(received) == 1
        assert bus.dead_letter_count == 1

    @pytest.mark.asyncio
    async def test_max_dead_letters_fifo(self):
        """Après MAX_DEAD_LETTERS erreurs, seules les plus récentes sont conservées."""
        def bad_cb(data):
            raise ValueError(f"error-{data['i']}")

        bus.subscribe("FIFO_TEST", bad_cb)

        for i in range(bus.MAX_DEAD_LETTERS + 20):
            await bus.publish("FIFO_TEST", {"i": i})

        assert bus.dead_letter_count == bus.MAX_DEAD_LETTERS
        # Le plus ancien conservé est le 20e (index 20)
        first = bus.get_dead_letters()[0]
        assert first.error == "error-20"

    def test_get_dead_letters_returns_copy(self):
        """Modifier la liste retournée ne modifie pas l'original."""
        # Injecter manuellement un dead letter pour le test
        dl = DeadLetter("TEST", {}, "cb", "err", "Error", 0.0)
        bus._dead_letters.append(dl)

        copy = bus.get_dead_letters()
        copy.clear()
        assert bus.dead_letter_count == 1

    def test_clear_dead_letters(self):
        """clear_dead_letters() vide la DLQ et retourne le count."""
        for i in range(5):
            bus._dead_letters.append(
                DeadLetter("T", {}, "cb", "e", "E", 0.0)
            )
        count = bus.clear_dead_letters()
        assert count == 5
        assert bus.dead_letter_count == 0

    @pytest.mark.asyncio
    async def test_retry_dead_letter_success(self):
        """Retry un dead letter dont le callback est maintenant corrigé."""
        call_count = 0

        def flaky_cb(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("first call fails")
            # Deuxième appel réussit

        bus.subscribe("RETRY_TEST", flaky_cb)
        await bus.publish("RETRY_TEST", {"val": 1})

        assert bus.dead_letter_count == 1
        assert call_count == 1

        result = await bus.retry_dead_letter(0)
        assert result is True
        assert bus.dead_letter_count == 0
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_dead_letter_invalid_index(self):
        """Index hors bornes retourne False."""
        assert await bus.retry_dead_letter(-1) is False
        assert await bus.retry_dead_letter(0) is False
        assert await bus.retry_dead_letter(999) is False

    def test_reset_clears_dead_letters(self):
        """reset() vide aussi la DLQ."""
        bus._dead_letters.append(
            DeadLetter("T", {}, "cb", "e", "E", 0.0)
        )
        assert bus.dead_letter_count == 1
        bus.reset()
        assert bus.dead_letter_count == 0

    def test_dead_letter_count_property(self):
        """.dead_letter_count retourne le bon nombre."""
        assert bus.dead_letter_count == 0
        for i in range(3):
            bus._dead_letters.append(
                DeadLetter("T", {}, "cb", "e", "E", 0.0)
            )
        assert bus.dead_letter_count == 3
