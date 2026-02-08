from .bus import bus

class EventPublisher:
    async def publish(self, event_type, payload):
        await bus.publish(event_type, payload)