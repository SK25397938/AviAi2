import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.intelligence.guidance_ai import GuidanceAI
from app.intelligence.approach_ai import ApproachAI
from app.simulation.engine import SimulationEngine
from app.simulation.holding_manager import HoldingManager


def aircraft(callsign, route_id="r1", phase="ARRIVAL"):
    return SimpleNamespace(
        callsign=callsign,
        arrival_route_id=route_id,
        phase=phase,
        route=["IN", "TRIG"],
        route_index=1,
        assigned_node="TRIG",
        target_node=None,
        state="ENROUTE",
        lat=19.1,
        lon=73.4,
        altitude_ft=7000.0,
        vertical_speed_fpm=0.0,
        speed_kts=220.0,
        target_speed_kts=220.0,
        clearance=SimpleNamespace(direct_node=None),
        assign_heading=lambda heading: None,
    )


def sequencing_engine(traffic=(), entry_etas=None):
    engine = object.__new__(SimulationEngine)
    for item in list(traffic)[1:]:
        item.lat, item.lon = 20.0, 74.0
    points = [
        {"id": "APP38", "latitude": 19.098, "longitude": 73.447, "altitude_ft": 7000},
        {"id": "APP32", "latitude": 19.096, "longitude": 73.352, "altitude_ft": 6000},
        {"id": "APP25", "latitude": 19.095, "longitude": 73.257, "altitude_ft": 5000},
    ]
    engine.approach_ai = SimpleNamespace(approach_points=points)
    engine.arrival_routes = {
        "r1": {"waypoints": [
            {"name": "IN", "latitude": 19.08, "longitude": 73.4},
            {"name": "TRIG", "latitude": 19.1, "longitude": 73.4},
        ]}
    }
    engine.manager = SimpleNamespace(all=lambda: list(traffic))
    engine.holding_manager = SimpleNamespace(has_active_holding_route=lambda item: False)
    engine.approach_reservations = {}
    engine._route_progress_nm = lambda item, route: 0.0
    engine._approach_point_etas = lambda item, route, progress, speed: dict(enumerate(
        (entry_etas or {}).get(item.callsign, (10000.0, 10000.0, 10000.0))
    ))
    values = {"APP38": 120.0, "APP32": 500.0}
    if entry_etas and "own_entry" in entry_etas:
        values.update(entry_etas["own_entry"])
    engine._approach_entry_eta_seconds = lambda item, route, progress, speed, point, from_fallback=False: values[
        point["id"]
    ]
    return engine


class ApproachCoordinationTests(unittest.TestCase):
    def test_app32_occupied_app38_safe_selects_app38(self):
        own = aircraft("OWN")
        other = aircraft("OTHER")
        engine = sequencing_engine([own, other], {"OTHER": [10000.0] * 3})
        engine.approach_reservations["APP32"] = other

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"])
        self.assertEqual(result["approach_entry_node"], "APP38")
        self.assertIs(engine.approach_reservations["APP38"], own)
        self.assertIs(engine.approach_reservations["APP32"], other)

    def test_unsafe_preferred_and_alternative_keep_reservations(self):
        own = aircraft("OWN")
        other = aircraft("OTHER")
        other.altitude_ft = 6500.0
        engine = sequencing_engine(
            [own, other],
            {"OTHER": [120.0, 200.0, 300.0],
             "own_entry": {"APP38": 120.0, "APP32": 190.0}},
        )
        engine.approach_reservations["APP38"] = other

        result = engine._can_approach_handoff(own)

        self.assertFalse(result["safe"])
        self.assertNotIn("APP32", engine.approach_reservations)
        self.assertIs(engine.approach_reservations["APP38"], other)

    def test_app32_occupied_app38_unsafe_retains_safe_delay_state(self):
        own = aircraft("OWN")
        holder = aircraft("HOLDER")
        conflict = aircraft("CONFLICT")
        conflict.altitude_ft = 6500.0
        engine = sequencing_engine(
            [own, holder, conflict],
            {"CONFLICT": [120.0, 10000.0, 10000.0],
             "own_entry": {"APP38": 120.0, "APP32": 500.0}},
        )
        engine.approach_reservations["APP32"] = holder

        result = engine._can_approach_handoff(own)

        self.assertFalse(result["safe"])
        self.assertIs(engine.approach_reservations["APP32"], holder)
        self.assertNotIn("APP38", engine.approach_reservations)

    def test_both_available_selects_larger_downstream_margin(self):
        own = aircraft("OWN")
        other = aircraft("OTHER")
        other.altitude_ft = 6500.0
        engine = sequencing_engine(
            [own, other],
            {"OTHER": [450.0, 1200.0, 1600.0], "own_entry": {"APP38": 180.0, "APP32": 500.0}},
        )

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"])
        self.assertEqual(result["approach_entry_node"], "APP32")

    def test_same_aircraft_reassessment_does_not_duplicate_reservations(self):
        own = aircraft("OWN")
        engine = sequencing_engine([own])
        first = engine._can_approach_handoff(own)
        second = engine._can_approach_handoff(own)

        self.assertTrue(first["safe"] and second["safe"])
        self.assertEqual(sum(owner is own for owner in engine.approach_reservations.values()), 1)

    def test_selected_entry_becomes_approach_cursor_and_stale_reservation_clears(self):
        own = aircraft("OWN", phase="FINAL")
        own.aircraft_type = "A320"
        own.lat, own.lon = 19.15, 73.6
        own.controller = "Arrival"
        own.last_instruction = ""
        own._approach_entry_index = 1
        own.approach_complete = False
        own.assign_altitude = lambda altitude: None
        own.assign_speed = lambda speed: None
        approach = ApproachAI(None)

        approach.update(own)

        self.assertEqual(own.approach_index, 1)
        self.assertEqual(own.target_node, "APP32")

        engine = sequencing_engine([own])
        engine.approach_reservations["APP32"] = own
        own.approach_index = 2
        engine._prune_approach_reservations()
        self.assertNotIn("APP32", engine.approach_reservations)


class FallbackReleaseTests(unittest.TestCase):
    def guidance(self, safety):
        graph = SimpleNamespace(get_node=lambda name: {"lat": 19.1, "lon": 73.4})
        guidance = object.__new__(GuidanceAI)
        guidance.graph = graph
        guidance.navigator = SimpleNamespace(heading_to_node=lambda item, node: 90.0)
        guidance.holding_manager = None
        guidance.arrival_routes = {"r1": {"approach_trigger": "TRIG"}}
        guidance.arrival_profiles = {}
        guidance.decision_event_callback = None
        guidance.approach_handoff_validator = lambda item, **kwargs: safety
        guidance.apply_route_constraints = lambda item: None
        return guidance

    def test_safe_fallback_release_synchronizes_approach_state(self):
        item = aircraft("FALLBACK")
        item.route = ["TRIG", "DOGAP", "TRIG"]
        item.route_index = 1
        item.assigned_node = "TRIG"
        item.target_node = "DOGAP"
        item._approach_extension_used = True
        item._approach_extension_fix = "DOGAP"
        item._approach_extension_index = 1
        guidance = self.guidance({
            "safe": True, "approach_entry_index": 0, "approach_entry_node": "APP38"
        })

        guidance.update(item)

        self.assertEqual(item.phase, "FINAL")
        self.assertEqual(item.assigned_node, "DOGAP")
        self.assertEqual(item.route, ["TRIG", "DOGAP"])
        self.assertEqual(item.route_index, 1)
        self.assertEqual(item.target_node, "APP38")
        self.assertEqual(item.clearance.direct_node, "APP38")
        self.assertTrue(item.approach_clearance)
        self.assertFalse(hasattr(item, "_approach_extension_used"))

    def test_unsafe_fallback_remains_on_existing_loop(self):
        item = aircraft("FALLBACK")
        item.route = ["TRIG", "DOGAP", "TRIG"]
        item.route_index = 1
        item.assigned_node = "TRIG"
        item.target_node = "DOGAP"
        item._approach_extension_used = True
        item._approach_extension_fix = "DOGAP"
        item._approach_extension_index = 1
        guidance = self.guidance({"safe": False})

        guidance.update(item)

        self.assertEqual(item.phase, "ARRIVAL")
        self.assertEqual(item.route, ["TRIG", "DOGAP", "TRIG"])
        self.assertEqual(item.target_node, "TRIG")
        self.assertTrue(item._approach_extension_used)


class HoldingFairnessTests(unittest.TestCase):
    @staticmethod
    def release_engine(own_started, other_started, shared_route=True):
        own = aircraft("OWN")
        other = aircraft("OTHER", "r1" if shared_route else "r2")
        own_state = {
            "aircraft": own, "priority_callsign": None, "protected_merge_point": None,
            "original_route": ["IN", "MERGE", "OUT"], "rejoin_waypoint": "MERGE",
            "holding_route_id": "H1", "completed_circuits": 1, "requested_circuits": 1,
            "holding_since": own_started,
        }
        other_state = {
            "aircraft": other, "priority_callsign": None, "protected_merge_point": None,
            "original_route": ["IN", "MERGE", "OUT"] if shared_route else ["X", "Y", "Z"],
            "rejoin_waypoint": "MERGE" if shared_route else "Y",
            "holding_route_id": "H2", "completed_circuits": 1, "requested_circuits": 1,
            "holding_since": other_started,
        }
        route_one = {"waypoints": [
            {"name": "IN", "latitude": 19.0, "longitude": 73.0},
            {"name": "MERGE", "latitude": 19.0, "longitude": 73.1},
            {"name": "OUT", "latitude": 19.0, "longitude": 73.2},
        ]}
        route_two = route_one if shared_route else {"waypoints": [
            {"name": "X", "latitude": 18.0, "longitude": 72.0},
            {"name": "Y", "latitude": 18.0, "longitude": 72.1},
            {"name": "Z", "latitude": 18.0, "longitude": 72.2},
        ]}
        engine = object.__new__(SimulationEngine)
        engine.manager = SimpleNamespace(all=lambda: [own, other])
        engine.arrival_routes = {"r1": route_one, "r2": route_two}
        states = {id(own): own_state, id(other): other_state}
        engine.holding_manager = SimpleNamespace(
            state_for=lambda item: states.get(id(item)), _states=states
        )
        engine._route_progress_nm = lambda item, route: 0.0
        engine._approach_merge_eta_seconds = lambda *args: None
        return engine, own, other

    def test_holding_release_still_requires_circuit_boundary_and_safety(self):
        manager = HoldingManager([])
        item = aircraft("HOLD")
        state = {
            "aircraft": item, "next_circuit_end_index": 4,
            "completed_circuits": 0, "requested_circuits": 1,
            "holding_route_id": "H1", "rejoin_waypoint": "TRIG",
            "original_route": ["TRIG", "OUT"], "rejoin_index": 0,
            "holding_node_ids": [], "original_target_altitude": None,
            "original_target_speed": None,
        }
        manager._active_holds[id(item)] = state
        item.route_index = 3
        self.assertFalse(manager.complete_holding_circuit(item, lambda _: {"safe": True})["success"])

        item.route_index = 4
        manager._schedule_holding_circuit = Mock()
        manager.restore_original_route = Mock(return_value={"success": True})
        result = manager.complete_holding_circuit(item, lambda _: {"safe": True})

        self.assertTrue(result["released"])
        manager.restore_original_route.assert_called_once_with(item, circuit_boundary=True)

    def test_active_hold_target_is_synchronized_to_route_cursor(self):
        manager = HoldingManager([])
        item = aircraft("HOLD")
        item.route = ["TRIG", "H1_P01", "H1_P02", "H1_P03", "TRIG"]
        item.route_index = 2
        item.assigned_node = "H1_P02"
        item.target_node = "TRIG"  # stale target from the prior circuit leg
        item.lat, item.lon = 19.2, 73.5
        manager._active_holds[id(item)] = {"aircraft": item, "holding_route_id": "H1"}
        guidance = object.__new__(GuidanceAI)
        guidance.graph = SimpleNamespace(get_node=lambda name: {"lat": 20.0, "lon": 74.0})
        guidance.navigator = SimpleNamespace(
            heading_to_node=lambda ac, node: 90.0,
            distance=lambda *args: 1.0,
        )
        guidance.holding_manager = manager
        guidance.arrival_routes = {}
        guidance.arrival_profiles = {}
        guidance.decision_event_callback = None
        guidance.apply_route_constraints = lambda ac: None

        guidance.update(item)

        self.assertEqual(item.target_node, item.route[item.route_index + 1])
        self.assertEqual(item.target_node, "H1_P03")
        self.assertEqual(item.clearance.direct_node, "H1_P03")

    def test_longer_waiting_aircraft_wins_same_merge_tie(self):
        engine, own, _ = self.release_engine(20.0, 10.0)

        result = engine._can_release_holding(own)

        self.assertFalse(result["safe"])
        self.assertIn("waited longer", result["reason"])

    def test_older_candidate_can_release_before_newer_same_merge_hold(self):
        engine, own, _ = self.release_engine(10.0, 20.0)

        result = engine._can_release_holding(own)

        self.assertTrue(result["safe"])

    def test_older_unsafe_aircraft_does_not_block_independent_safe_release(self):
        engine, own, older = self.release_engine(20.0, 10.0, shared_route=False)
        leader = aircraft("LEADER", "r3")
        leader.lat, leader.lon = 18.0, 72.15
        leader.altitude_ft = 7000.0
        engine.manager.all = lambda: [own, older, leader]
        engine.arrival_routes["r3"] = {"waypoints": [
            {"name": "L", "latitude": 18.0, "longitude": 72.15},
            {"name": "Z", "latitude": 18.0, "longitude": 72.2},
        ]}
        engine.holding_manager._states[id(leader)] = None

        older_result = engine._can_release_holding(older)
        self.assertFalse(older_result["safe"])

        result = engine._can_release_holding(own)

        self.assertTrue(result["safe"])


if __name__ == "__main__":
    unittest.main()
