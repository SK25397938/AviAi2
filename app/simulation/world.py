import uuid

from app.simulation.clock import simulation_clock
from app.simulation.engine import SimulationEngine
from app.intelligence.instruction_manager import instruction_manager


class SimulationWorld:

    def __init__(self):
        self.airport = "VABB"
        self.engine = SimulationEngine()
        self.session_mode = "NORMAL"
        self.session_id = None
        self._normal_engine = None
        normal_engine = self.engine
        normal_engine.normal_generation_allowed = lambda: (
            self.engine is normal_engine and self.session_mode == "NORMAL"
        )

    def enter_test_session(self):
        if self.session_mode == "TEST":
            return self.get_session_state()
        session_id = uuid.uuid4().hex
        # Construct successfully before swapping so a constructor failure leaves
        # the live normal simulation and all its resources untouched.
        test_engine = SimulationEngine(session_mode="TEST", session_id=session_id)
        self._normal_engine = self.engine
        self.engine = test_engine
        self.session_mode = "TEST"
        self.session_id = session_id
        return self.get_session_state()

    def exit_test_session(self, session_id=None):
        if self.session_mode != "TEST":
            return self.get_session_state()
        if not session_id or session_id != self.session_id:
            raise ValueError("Test session token does not match the active session")
        test_engine = self.engine
        normal_engine = self._normal_engine
        if normal_engine is None:
            raise RuntimeError("Normal simulation session is unavailable")
        # Swapping under SimulationRunner's serialization lock makes this
        # teardown atomic with respect to updates and spawn requests.
        self.engine = normal_engine
        self._normal_engine = None
        self.session_mode = "NORMAL"
        self.session_id = None
        test_engine.manager.aircraft.clear()
        test_engine.approach_reservations.clear()
        test_engine.approach_reservation_status.clear()
        test_engine.holding_manager._active_holds.clear()
        test_engine._last_separation_event_at.clear()
        test_engine._last_holding_release_diagnostic_at.clear()
        traffic_manager = getattr(test_engine, "traffic_manager", None)
        scheduler = getattr(test_engine, "scheduler", None)
        for resource in (
                getattr(test_engine, "aircraft_db", None),
                getattr(traffic_manager, "db", traffic_manager),
                getattr(scheduler, "db", scheduler)):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass
        return self.get_session_state()

    def get_session_state(self):
        return {"session_mode": self.session_mode, "session_id": self.session_id,
                "suspended_normal_aircraft_count": (
                    len(self._normal_engine.manager.all())
                    if self.session_mode == "TEST" and self._normal_engine is not None else 0
                ),
                "test_aircraft_count": sum(
                    1 for ac in self.engine.manager.all()
                    if getattr(ac, "test_session_id", None) == self.session_id
                ) if self.session_mode == "TEST" else 0}

    def spawn_test_aircraft(self, session_id, callsign, aircraft_type, node, route_id):
        if self.session_mode != "TEST" or not session_id or session_id != self.session_id:
            raise ValueError("Test session is not active")
        return self.engine.spawn_test_aircraft(callsign, aircraft_type, node, route_id)

    def spawn_random_test_aircraft(self, session_id, node, route_id):
        if self.session_mode != "TEST" or not session_id or session_id != self.session_id:
            raise ValueError("Test session is not active")
        return self.engine.spawn_random_test_aircraft(node, route_id)

    def remove_test_aircraft(self, session_id, callsign):
        if self.session_mode != "TEST" or not session_id or session_id != self.session_id:
            raise ValueError("Test session is not active")
        return self.engine.remove_test_aircraft(callsign)

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

    def _guidance_status(self, aircraft):
        if self.engine.holding_manager.has_active_holding_route(aircraft):
            return "HOLDING"
        if getattr(aircraft, "separation_status", None):
            return aircraft.separation_status
        if any(hasattr(aircraft, name) for name in (
            "_engine_shared_waypoint_applied_target_speed",
            "_engine_predictive_applied_target_speed",
            "_separation_target_speed",
            "_shared_waypoint_target_speed",
        )):
            return "SEQUENCING"
        return "APPROACH" if aircraft.phase == "FINAL" else "ENROUTE"

    def get_state(self):

        return {

            "session": self.get_session_state(),

            "airport": self.airport,

            "clock": simulation_clock.get_state(),

            "aircraft_count": len(
                self.engine.manager.all()
            ),

            "instruction_log": (instruction_manager.latest()
                                if self.session_mode == "NORMAL" else []),

            "ai_decision_log": list(self.engine.ai_decision_log),

            "decision_audit_summary": {
                decision_type: dict(sources)
                for decision_type, sources in getattr(
                    self.engine, "decision_audit_counts", {}
                ).items()
            },

            "departure_diagnostics": list(getattr(
                self.engine, "departure_diagnostics", []
            )),

            "arrival_guidance": [
                {
                    "callsign": ac.callsign,
                    "route_id": getattr(ac, "arrival_route_id", None),
                    "route_index": ac.route_index,
                    "waypoint": ac.assigned_node,
                    "target": ac.target_node,
                    "distance_nm": ac.current_distance if ac.phase == "ARRIVAL" else None,
                    "altitude_ft": ac.altitude_ft,
                    "target_altitude_ft": ac.target_altitude_ft,
                    "speed_kts": ac.speed_kts,
                    "target_speed_kts": ac.target_speed_kts,
                    "vertical_speed_fpm": ac.vertical_speed_fpm,
                    "phase": ac.phase,
                    "status": self._guidance_status(ac),
                }
                for ac in self.engine.manager.all()
                if getattr(ac, "phase", "") in ("ARRIVAL", "FINAL")
            ],

            "holding_routes": self.engine.holding_manager.active_holding_routes(),

            "holding_points": self.engine.holding_manager.configured_holding_points(),

            "aircraft": [

                {

                    "callsign": ac.callsign,

                    "test_session_id": getattr(ac, "test_session_id", None),

                    "test_spawned": getattr(ac, "test_spawned", False),

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

                    "route": list(ac.route),

                    "route_index": ac.route_index,

                    "holding": self.engine.holding_manager.holding_status(ac),

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
