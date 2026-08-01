from app.simulation.clock import simulation_clock
from app.simulation.engine import SimulationEngine
from app.intelligence.instruction_manager import instruction_manager


class SimulationWorld:

    def __init__(self):
        self.airport = "VABB"
        self.engine = SimulationEngine()

    def spawn(
        self,
        aircraft_type,
        callsign
    ):
        self.engine.spawn(
            aircraft_type,
            callsign
        )

    def update(
        self,
        dt=1.0
    ):
        self.engine.update(dt)

    def get_all_aircraft(self):
        return self.engine.manager.all()

    def get_state(self):

        return {

            "airport": self.airport,

            "clock": simulation_clock.get_state(),

            "aircraft_count": len(
                self.engine.manager.all()
            ),

            "instruction_log": instruction_manager.latest(),

            "aircraft": [

                {

                    "callsign": ac.callsign,

                    "type": ac.aircraft_type,

                    "lat": ac.lat,

                    "lon": ac.lon,

                    "heading": ac.heading_deg,

                    "target_heading": ac.target_heading_deg,

                    "speed": ac.speed_kts,

                    "target_speed": ac.target_speed_kts,

                    "altitude": ac.altitude_ft,

                    "target_altitude": ac.target_altitude_ft,

                    "vertical_speed": ac.vertical_speed_fpm,

                    "phase": ac.phase,

                    "approach_phase": ac.approach_phase,

                    "state": ac.state,

                    "controller": ac.controller,

                    "last_instruction": ac.last_instruction,

                    "assigned_node": ac.assigned_node,

                    "target_node": ac.target_node,

                    "destination": ac.destination,

                    "runway": ac.clearance.runway,

                    "on_ground": ac.on_ground,

                    "landing_complete": ac.landing_complete,

                    "clearance": {

                        "heading": ac.clearance.heading,

                        "altitude": ac.clearance.altitude_ft,

                        "speed": ac.clearance.speed_kts,

                        "direct_node": ac.clearance.direct_node

                    }

                }

                for ac in self.engine.manager.all()

            ]

        }


simulation_world = SimulationWorld()