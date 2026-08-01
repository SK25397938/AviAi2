import math
from app.weather.models import RunwayDecision

class RunwaySelector:

    def __init__(self, weather, runways):
        self.weather = weather
        self.runways = runways

    def select(self):
        best = None
        best_headwind = float("-inf")
        best_crosswind = float("inf")
        for runway in self.runways:
            refs = runway["ref"].split("/")
            for ref in refs:
                ref = ref.strip()
                digits = "".join(c for c in ref if c.isdigit())
                if len(digits) != 2:
                    continue
                heading = int(digits) * 10
                angle = math.radians(
                    self.weather.wind_direction - heading
                )
                headwind = self.weather.wind_speed * math.cos(angle)
                crosswind = abs(
                    self.weather.wind_speed * math.sin(angle)
                )
                if (
                    headwind > best_headwind
                    or (
                        abs(headwind - best_headwind) < 0.1
                        and crosswind < best_crosswind
                    )
                ):
                    best = ref
                    best_headwind = headwind
                    best_crosswind = crosswind
        if best is None:
            best = self.runways[0]["ref"].split("/")[0]
            best_headwind = 0
            best_crosswind = 0
        return RunwayDecision(
            active_runway=best,
            arrival_runway=best,
            departure_runway=best,
            headwind=round(best_headwind, 1),
            crosswind=round(best_crosswind, 1),
            confidence=100,
            reason=f"Selected Runway {best} due to maximum headwind."
        )