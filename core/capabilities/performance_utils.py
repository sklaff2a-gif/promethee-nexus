import asyncio
from concurrent.futures import ThreadPoolExecutor


class AsyncTaskManager:
    def __init__(self, max_workers=10):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.loop = asyncio.get_event_loop()

    async def run(self, func, *args, **kwargs):
        return await self.loop.run_in_executor(
            self.executor,
            func,
            *args,
            **kwargs
        )