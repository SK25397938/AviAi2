import time
from datetime import datetime, timedelta


class SimulationClock:
    def __init__(self):
        self.running = False
        self.speed = 1.0
        self.simulation_time = datetime.now()
        self._last_real_time = time.monotonic()

    def start(self):
        self.running = True
        self._last_real_time = time.monotonic()

    def pause(self):
        self.update()
        self.running = False

    def resume(self):
        self.running = True
        self._last_real_time = time.monotonic()

    def set_speed(self, speed: float):
        if speed not in (1.0, 2.0, 4.0):
            raise ValueError("Simulation speed must be 1x, 2x, or 4x")
        self.update()
        self.speed = speed

    def update(self):
        current_real_time = time.monotonic()

        if self.running:
            elapsed_real = current_real_time - self._last_real_time
            elapsed_simulation = elapsed_real * self.speed
            self.simulation_time += timedelta(seconds=elapsed_simulation)

        self._last_real_time = current_real_time

    def get_time(self):
        self.update()
        return self.simulation_time

    def get_state(self):
        self.update()

        return {
            "running": self.running,
            "speed": self.speed,
            "simulation_time": self.simulation_time.isoformat()
        }


simulation_clock = SimulationClock()