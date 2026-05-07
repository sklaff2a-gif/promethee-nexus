import asyncio
from concurrent.futures import ThreadPoolExecutor


class AsyncTaskManager:
    def __init__(self, max_workers=10):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    async def run(self, func, *args, **kwargs):
        # asyncio.get_event_loop() est deprecated en Python 3.10+ et leve
        # RuntimeError si pas de loop courant (pytest-asyncio strict ferme
        # le loop entre tests). get_running_loop() fonctionne car run()
        # est lui-meme async, donc forcement dans un loop actif.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.executor,
            func,
            *args,
            **kwargs
        )