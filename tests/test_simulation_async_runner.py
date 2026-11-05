import asyncio
import threading
import time
import unittest

from app.simulation.async_runner import SimulationRunner


class SimulationAsyncRunnerTests(unittest.TestCase):
    def test_slow_simulation_tick_does_not_block_the_event_loop(self):
        runner = SimulationRunner()
        heartbeat = []
        started = threading.Event()
        finished = threading.Event()

        def slow_update(_dt):
            started.set()
            time.sleep(0.08)
            finished.set()

        async def exercise():
            tick = asyncio.create_task(runner.update_and_snapshot(
                slow_update, lambda: {"ok": True}, 1
            ))
            await asyncio.to_thread(started.wait)
            await asyncio.sleep(0.005)
            heartbeat.append(not finished.is_set())
            return await tick

        result = asyncio.run(exercise())
        self.assertEqual(result, {"ok": True})
        self.assertEqual(heartbeat, [True], "the event loop must run before the slow update completes")

    def test_simulation_updates_are_serialized(self):
        runner = SimulationRunner()
        active = 0
        maximum = 0

        def update(_dt):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            time.sleep(0.02)
            active -= 1

        async def exercise():
            await asyncio.gather(*[
                runner.update_and_snapshot(update, lambda: {}, 1) for _ in range(4)
            ])

        asyncio.run(exercise())
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
