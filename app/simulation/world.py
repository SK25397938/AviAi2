from typing import Dict, Optional
from app.simulation.aircraft.models import SimulatedAircraft
from app.simulation.clock import simulation_clock


class SimulationWorld:
    def __init__(self):
        self.airport = "VABB"
        self.aircraft: Dict[str, SimulatedAircraft] = {}

    def add_aircraft(self, aircraft: SimulatedAircraft):
        self.aircraft[aircraft.id] = aircraft

    def remove_aircraft(self, aircraft_id: str):
        self.aircraft.pop(aircraft_id, None)

    def get_aircraft(self, aircraft_id: str) -> Optional[SimulatedAircraft]:
        return self.aircraft.get(aircraft_id)

    def get_all_aircraft(self):
        return list(self.aircraft.values())

    def get_state(self):
        return {
            "airport": self.airport,
            "aircraft_count": len(self.aircraft),
            "clock": simulation_clock.get_state(),
            "aircraft": [
                aircraft.to_dict()
                for aircraft in self.aircraft.values()
            ]
        }


simulation_world = SimulationWorld()