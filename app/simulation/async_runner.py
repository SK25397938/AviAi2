"""Run blocking simulation work off the ASGI event loop, serially."""

import asyncio
from functools import partial


class SimulationRunner:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def run(self, function, *args, **kwargs):
        async with self._lock:
            return await asyncio.to_thread(partial(function, *args, **kwargs))

    async def update_and_snapshot(self, update, get_state, dt):
        async with self._lock:
            await asyncio.to_thread(update, dt)
            return await asyncio.to_thread(get_state)
