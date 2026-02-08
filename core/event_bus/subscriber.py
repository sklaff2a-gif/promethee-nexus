from .bus import bus

class EventBusSubscriber:
    async def start_listening(self):
        # Pour le debug console
        bus.subscribe("*", self.log_event)
        
    async def log_event(self, event):
        # On ne print pas tout pour ne pas spammer, juste les infos critiques
        if event.get("type") in ["SYSTEM_OVERRIDE", "AGENT_ERROR"]:
            print(f"   📡 BUS: {event}")

    async def stop_listening(self):
        pass