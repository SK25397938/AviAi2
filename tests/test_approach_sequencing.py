import unittest
from threading import Barrier, RLock, Thread
from types import SimpleNamespace
from unittest.mock import Mock

from app.intelligence.guidance_ai import GuidanceAI
from app.intelligence.approach_ai import ApproachAI
from app.intelligence.mistral.controller import MistralController
from app.simulation.engine import SimulationEngine
from app.simulation.holding_manager import HoldingManager
from app.navigation.loader import load_arrival_routes


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


def sequencing_engine(traffic=(), entry_etas=None, route_preferences=None):
    engine = object.__new__(SimulationEngine)
    for item in list(traffic)[1:]:
        item.lat, item.lon = 20.0, 74.0
    points = [
        {"id": "APP38", "latitude": 19.098, "longitude": 73.447, "altitude_ft": 7000,
         "arrival_entry_transition": {"type": "DIRECT_FROM_ARRIVAL_TRIGGER"}},
        {"id": "APP32", "latitude": 19.096, "longitude": 73.352, "altitude_ft": 6000,
         "arrival_entry_transition": {"type": "DIRECT_FROM_ARRIVAL_TRIGGER"}},
        {"id": "APP25", "latitude": 19.095, "longitude": 73.257, "altitude_ft": 5000},
    ]
    engine.approach_ai = SimpleNamespace(approach_points=points)
    engine.arrival_routes = {
        "r1": {"approach_trigger": "TRIG", "approach_entry_preferences": route_preferences or [], "waypoints": [
            {"name": "IN", "latitude": 19.08, "longitude": 73.4},
            {"name": "TRIG", "latitude": 19.1, "longitude": 73.4},
        ]}
    }
    engine.manager = SimpleNamespace(all=lambda: list(traffic))
    engine.holding_manager = SimpleNamespace(has_active_holding_route=lambda item: False)
    engine.approach_reservations = {}
    engine._approach_allocation_lock = RLock()
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
    def test_vabb_route_preferences_reference_configured_direct_entries(self):
        routes = load_arrival_routes()
        approach_points = ApproachAI(None).approach_points
        point_ids = {point["id"] for point in approach_points
                     if point.get("arrival_entry_transition", {}).get("type") == "DIRECT_FROM_ARRIVAL_TRIGGER"}

        for route in routes.values():
            trigger = route["approach_trigger"]
            preferences = route.get("approach_entry_preferences", [])
            if trigger == "OLGUS":
                self.assertEqual(preferences, ["APP32", "APP38"])
            elif trigger == "MB395":
                self.assertEqual(preferences, ["APP32", "APP38"])
            self.assertTrue(set(preferences).issubset(point_ids))

    def test_olgus_route_prefers_app32_when_both_entries_are_safe(self):
        own = aircraft("OLGUS")
        engine = sequencing_engine([own], route_preferences=["APP32", "APP38"])

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"], result["reason"])
        self.assertEqual(result["approach_entry_node"], "APP32")
        self.assertEqual(result["approach_entry_preference_rank"], 0)

    def test_mb395_route_prefers_app32_when_both_entries_are_safe(self):
        own = aircraft("MB395")
        engine = sequencing_engine([own], route_preferences=["APP32", "APP38"])

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"], result["reason"])
        self.assertEqual(result["approach_entry_node"], "APP32")
        self.assertEqual(result["approach_entry_preference_rank"], 0)

    def test_app32_altitude_unreachable_falls_back_to_safe_app38(self):
        own = aircraft("OWN")
        own.altitude_ft = 7500.0
        engine = sequencing_engine(
            [own], {"own_entry": {"APP32": 30.0, "APP38": 120.0}},
            route_preferences=["APP32", "APP38"],
        )

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"], result["reason"])
        self.assertEqual(result["approach_entry_node"], "APP38")
        self.assertIs(engine.approach_reservations["APP38"], own)

    def test_olgus_and_mb395_arrivals_use_preferred_entries_with_separation(self):
        olgus = aircraft("OLGUS")
        mb395 = aircraft("MB395")
        mb395.arrival_route_id = "r2"
        olgus.lat, olgus.lon = 19.215, 73.473333
        mb395.lat, mb395.lon = 18.936667, 73.38
        engine = sequencing_engine([olgus, mb395], route_preferences=["APP32", "APP38"])
        engine.arrival_routes["r1"]["waypoints"][-1].update(
            latitude=19.215, longitude=73.473333
        )
        engine.arrival_routes["r2"] = {
            "approach_trigger": "TRIG2", "approach_entry_preferences": ["APP32", "APP38"],
            "waypoints": [
                {"name": "IN2", "latitude": 18.8, "longitude": 73.3},
                {"name": "TRIG2", "latitude": 18.9, "longitude": 73.38},
            ],
        }
        engine._approach_point_etas = lambda item, route, progress, speed: {0: 5000.0, 1: 6000.0, 2: 7000.0}

        first = engine._can_approach_handoff(olgus)
        second = engine._can_approach_handoff(mb395)

        self.assertTrue(first["safe"], first["reason"])
        self.assertTrue(second["safe"], second["reason"])
        self.assertEqual(first["approach_entry_node"], "APP32")
        self.assertEqual(second["approach_entry_node"], "APP38")
        self.assertIs(engine.approach_reservations["APP32"], olgus)
        self.assertIs(engine.approach_reservations["APP38"], mb395)

    def test_eta_reassessment_switches_only_after_preferred_entry_becomes_unsafe(self):
        own = aircraft("OLGUS")
        other = aircraft("OTHER")
        other.altitude_ft = 6500.0
        etas = {"APP38": 120.0, "APP32": 500.0}
        downstream = {2: 5000.0}
        engine = sequencing_engine([own, other], route_preferences=["APP32", "APP38"])
        engine._approach_entry_eta_seconds = lambda item, route, progress, speed, point, from_fallback=False: etas[point["id"]]
        engine._approach_point_etas = lambda item, route, progress, speed: dict(downstream)

        first = engine._can_approach_handoff(own)
        self.assertTrue(first["safe"], first["reason"])
        self.assertEqual(first["approach_entry_node"], "APP32")

        downstream[2] = 590.0
        reassessed = engine._can_approach_handoff(own)
        self.assertTrue(reassessed["safe"], reassessed["reason"])
        self.assertEqual(reassessed["approach_entry_node"], "APP38")
        self.assertNotIn("APP32", engine.approach_reservations)
        self.assertIs(engine.approach_reservations["APP38"], own)

    def test_currently_occupied_entry_is_not_rejected_when_eta_separation_is_safe(self):
        own = aircraft("OLGUS")
        other = aircraft("AT-APP32")
        other.lat, other.lon = 19.096, 73.352
        other.altitude_ft = 6000.0
        engine = sequencing_engine([own, other], route_preferences=["APP32", "APP38"])
        engine._approach_entry_eta_seconds = lambda item, route, progress, speed, point, from_fallback=False: {
            "APP32": 500.0, "APP38": 120.0
        }[point["id"]]
        engine._approach_point_etas = lambda item, route, progress, speed: {1: 0.0, 2: 100.0}

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"], result["reason"])
        self.assertEqual(result["approach_entry_node"], "APP32")

    def test_unconfigured_intermediate_points_cannot_be_selected(self):
        own = aircraft("OWN")
        engine = sequencing_engine([own])
        engine.approach_ai.approach_points.insert(0, {
            "id": "APP18", "latitude": 19.09, "longitude": 73.16, "altitude_ft": 4400,
        })

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"])
        self.assertIn(result["approach_entry_node"], {"APP32", "APP38"})

    def test_aircraft_beyond_entry_is_checked_at_a_shared_downstream_fix(self):
        own = aircraft("OWN")
        leader = aircraft("LEADER", phase="FINAL")
        leader.approach_index = 2  # Already beyond APP32, approaching APP25.
        leader.altitude_ft = 6500.0
        engine = sequencing_engine(
            [own, leader],
            {"LEADER": [250.0, 250.0, 250.0],
             "own_entry": {"APP38": 90.0, "APP32": 120.0}},
        )
        engine._approach_point_etas = lambda item, route, progress, speed: (
            {2: 250.0} if item is leader else {0: 0.0, 1: 0.0, 2: 0.0}
        )

        result = engine._can_approach_handoff(own)

        self.assertFalse(result["safe"])
        self.assertIn("predicted inside 5 NM at APP25", result["reason"])

    def test_waiting_aircraft_can_reassess_after_entry_reservation_clears(self):
        own = aircraft("OWN")
        holder = aircraft("HOLDER")
        engine = sequencing_engine([own, holder])
        engine.approach_reservations["APP38"] = holder
        holder._approach_entry_index = 0
        holder._approach_entry_node = "APP38"
        engine.approach_reservations["APP32"] = holder
        holder._approach_entry_index = 1
        holder._approach_entry_node = "APP32"

        blocked = engine._can_approach_handoff(own)
        self.assertFalse(blocked["safe"])

        engine.approach_reservations.clear()
        del holder._approach_entry_index
        del holder._approach_entry_node
        available = engine._can_approach_handoff(own)
        self.assertTrue(available["safe"])
        self.assertIs(engine.approach_reservations[available["approach_entry_node"]], own)

    def test_missing_entry_transition_keeps_existing_flow_unavailable_safely(self):
        own = aircraft("OWN")
        engine = sequencing_engine([own])
        for point in engine.approach_ai.approach_points:
            point.pop("arrival_entry_transition", None)

        result = engine._can_approach_handoff(own)

        self.assertFalse(result["safe"])
        self.assertIn("No configured arrival-trigger approach transition", result["reason"])
        self.assertEqual(engine.approach_reservations, {})

    def test_direct_entry_requires_route_to_end_at_approach_trigger(self):
        own = aircraft("OWN")
        engine = sequencing_engine([own])
        engine.arrival_routes["r1"]["waypoints"][-1]["name"] = "NOT_TRIGGER"

        result = engine._can_approach_handoff(own)

        self.assertFalse(result["safe"])
        self.assertIn("does not start at the configured arrival trigger", result["reason"])
        self.assertEqual(engine.approach_reservations, {})

    def test_entry_is_rejected_when_altitude_cannot_be_reached(self):
        own = aircraft("OWN")
        own.altitude_ft = 20000.0
        engine = sequencing_engine(
            [own], {"own_entry": {"APP38": 1.0, "APP32": 1.0}}
        )

        result = engine._can_approach_handoff(own)

        self.assertFalse(result["safe"])
        self.assertIn("altitude is not reachable", result["reason"])
        self.assertEqual(engine.approach_reservations, {})

    def test_approach_fix_passed_between_ticks_advances_once(self):
        own = aircraft("PASS", phase="FINAL")
        own.aircraft_type = "A320"
        own._approach_entry_index = 1
        own.approach_complete = False
        own.controller = "Arrival"
        own.last_instruction = ""
        own.assign_altitude = lambda altitude: None
        own.assign_speed = lambda speed: None
        approach = ApproachAI(None)
        app32 = approach.approach_points[1]

        # Both sampled positions are outside the existing capture radius, but
        # the aircraft's path passes directly over APP32 between updates.
        own.lat = app32["latitude"]
        own.lon = app32["longitude"] + 0.004
        approach.update(own)
        self.assertEqual(own.approach_index, 1)

        own.lon = app32["longitude"] - 0.004
        approach.update(own)

        self.assertEqual(own.approach_index, 2)
        self.assertEqual(own.assigned_node, "APP32")
        self.assertEqual(own.target_node, approach.approach_points[2]["id"])
        self.assertEqual(own.clearance.direct_node, own.target_node)

        # A subsequent update continues toward the next fix without consuming
        # it or restoring the completed APP32 target.
        approach.update(own)
        self.assertEqual(own.approach_index, 2)
        self.assertEqual(own.target_node, approach.approach_points[2]["id"])

    def test_app32_occupied_app38_safe_selects_app38(self):
        own = aircraft("OWN")
        other = aircraft("OTHER")
        engine = sequencing_engine([own, other], {"OTHER": [10000.0] * 3},
                                   route_preferences=["APP32", "APP38"])
        engine.approach_reservations["APP32"] = other

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"])
        self.assertEqual(result["approach_entry_node"], "APP38")
        self.assertIs(engine.approach_reservations["APP38"], own)
        self.assertIs(engine.approach_reservations["APP32"], other)

    def test_app38_occupied_app32_safe_selects_app32(self):
        own = aircraft("OWN")
        holder = aircraft("HOLDER")
        engine = sequencing_engine([own, holder], {"HOLDER": [10000.0] * 3},
                                   route_preferences=["APP38", "APP32"])
        engine.approach_reservations["APP38"] = holder
        holder._approach_entry_index = 0
        holder._approach_entry_node = "APP38"

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"], result["reason"])
        self.assertEqual(result["approach_entry_node"], "APP32")
        self.assertEqual(own._approach_entry_node, "APP32")
        self.assertEqual(own._approach_entry_index, 1)
        self.assertIs(engine.approach_reservations["APP32"], own)
        self.assertIs(engine.approach_reservations["APP38"], holder)

    def test_established_approach_aircraft_without_map_entry_reclaims_reservation(self):
        own = aircraft("OWN")
        established = aircraft("ESTABLISHED", phase="FINAL")
        established.approach_index = 1
        established._approach_entry_index = 1
        established._approach_entry_node = "APP32"
        engine = sequencing_engine([own, established], route_preferences=["APP32", "APP38"])

        result = engine._can_approach_handoff(own)

        self.assertTrue(result["safe"], result["reason"])
        self.assertEqual(result["approach_entry_node"], "APP38")
        self.assertIs(engine.approach_reservations["APP32"], established)

    def test_concurrent_pending_clearances_cannot_both_claim_app32(self):
        arrivals = [aircraft("FIRST"), aircraft("SECOND")]
        engine = sequencing_engine(arrivals, route_preferences=["APP32", "APP38"])
        start = Barrier(2)
        results = []

        def allocate(item):
            start.wait()
            results.append(engine._can_approach_handoff(item))

        workers = [Thread(target=allocate, args=(item,)) for item in arrivals]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=2.0)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(result.get("approach_entry_node") == "APP32" for result in results), 1)
        self.assertEqual(sum(result.get("approach_entry_node") == "APP38" for result in results), 1)
        self.assertIsNot(engine.approach_reservations["APP32"], engine.approach_reservations["APP38"])

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
        self.assertIn("APP38 reserved for OTHER", result["reason"])
        self.assertIn("APP32:", result["reason"])
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


class ArrivalSequencingBoundsTests(unittest.TestCase):
    def test_merge_speed_adjustment_is_limited_to_ten_knots(self):
        item = aircraft("SPEED")
        assigned = []
        item.assign_speed = assigned.append
        item.target_speed_kts = 220.0
        engine = object.__new__(SimulationEngine)
        engine._minimum_sequencing_speed = lambda _: 170.0

        # This ETA calls for a 210 kt target, a 10 kt reduction from baseline.
        leader_eta = 20.0 * 3600.0 / 210.0 - 72.0
        applied = engine._apply_merge_speed_delay(item, 20.0, leader_eta, 250.0)

        self.assertTrue(applied)
        self.assertAlmostEqual(assigned[-1], 210.0)
        self.assertLessEqual(abs(assigned[-1] - 220.0), 10.0)

    def test_active_route_eta_uses_route_changes_and_fallback_waypoints(self):
        item = aircraft("ROUTE")
        item.route = ["IN", "DOGAP", "TRIG"]
        item.route_index = 1
        item.target_node = "DOGAP"
        item.lat, item.lon = 19.0, 73.0
        route = {"approach_trigger": "TRIG", "waypoints": [
            {"name": "IN", "latitude": 19.0, "longitude": 72.9},
            {"name": "TRIG", "latitude": 19.0, "longitude": 73.2},
        ]}
        engine = object.__new__(SimulationEngine)
        engine.graph = SimpleNamespace(nodes={
            "DOGAP": {"lat": 19.0, "lon": 73.1},
            "TRIG": {"lat": 19.0, "lon": 73.2},
        })
        engine.holding_manager = SimpleNamespace(has_active_holding_route=lambda _: False)

        distance = engine._active_route_distance_to_trigger_nm(item, route)
        expected_distance = engine._route_segment_length_nm(
            {"latitude": 19.0, "longitude": 73.0},
            {"latitude": 19.0, "longitude": 73.2},
        )
        self.assertAlmostEqual(distance, expected_distance, delta=0.01)

        item.route = ["IN", "HOLD_FIX", "TRIG"]
        item.target_node = "HOLD_FIX"
        engine.graph.nodes["HOLD_FIX"] = {"lat": 19.0, "lon": 73.05}
        updated_distance = engine._active_route_distance_to_trigger_nm(item, route)
        self.assertAlmostEqual(updated_distance, expected_distance, delta=0.01)


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

    def test_safe_fallback_reassessment_continues_to_route_trigger(self):
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

        self.assertEqual(item.phase, "ARRIVAL")
        self.assertEqual(item.assigned_node, "DOGAP")
        self.assertEqual(item.route, ["TRIG", "DOGAP", "TRIG"])
        self.assertEqual(item.route_index, 2)
        self.assertEqual(item.target_node, "TRIG")
        self.assertEqual(item.clearance.direct_node, "TRIG")
        self.assertFalse(getattr(item, "approach_clearance", False))
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
        guidance.decision_event_callback = Mock()

        guidance.update(item)

        self.assertEqual(item.phase, "ARRIVAL")
        self.assertEqual(item.route, ["TRIG", "DOGAP", "TRIG"])
        self.assertEqual(item.route_index, 1)
        self.assertEqual(item.assigned_node, "DOGAP")
        self.assertEqual(item.target_node, "DOGAP")
        self.assertEqual(item.clearance.direct_node, "DOGAP")
        self.assertTrue(item._approach_extension_used)
        guidance.decision_event_callback.assert_called_once_with(
            item, "APPROACH CLEARANCE WITHHELD", "DOGAP",
            "Approach handoff validator withheld clearance"
        )

    def test_waiting_fallback_is_reassessed_and_resumes_when_safe(self):
        item = aircraft("FALLBACK")
        item.route = ["TRIG", "DOGAP", "TRIG"]
        item.route_index = 1
        item.assigned_node = "TRIG"
        item.target_node = "DOGAP"
        item._approach_extension_used = True
        item._approach_extension_fix = "DOGAP"
        item._approach_extension_index = 1
        state = {"safe": False, "reason": "APP38 reserved for LEADER"}
        guidance = self.guidance(state)

        guidance.update(item)
        self.assertEqual(item.route_index, 1)
        self.assertEqual(item.target_node, "DOGAP")

        state.update(safe=True, approach_entry_index=0, approach_entry_node="APP38")
        guidance.update(item)
        self.assertEqual(item.route_index, 2)
        self.assertEqual(item.target_node, "TRIG")
        self.assertEqual(item.clearance.direct_node, "TRIG")
        self.assertFalse(hasattr(item, "_approach_extension_used"))

    def test_denied_fallback_is_reassessed_without_repeated_capture(self):
        item = aircraft("WAIT")
        item.route = ["TRIG", "DOGAP", "TRIG"]
        item.route_index = 1
        item.assigned_node = "DOGAP"
        item.target_node = "DOGAP"
        item._approach_extension_used = True
        item._approach_extension_fix = "DOGAP"
        item._approach_extension_index = 1
        item._approach_clearance_waiting = True
        checks = []
        guidance = self.guidance({"safe": False, "reason": "entry occupied"})
        guidance.approach_handoff_validator = lambda item, **kwargs: checks.append(item.target_node) or {"safe": False}
        guidance.update(item)
        guidance.update(item)
        self.assertEqual(checks, ["DOGAP", "DOGAP"])
        self.assertEqual(item.route_index, 1)
        self.assertEqual(item.target_node, "DOGAP")

    def test_waiting_fallback_advances_only_after_validator_allows_it(self):
        item = aircraft("RESUME")
        item.route = ["TRIG", "DOGAP", "TRIG"]
        item.route_index = 1
        item.assigned_node = "DOGAP"
        item.target_node = "DOGAP"
        item._approach_extension_used = True
        item._approach_extension_fix = "DOGAP"
        item._approach_extension_index = 1
        item._approach_clearance_waiting = True
        state = {"safe": False}
        guidance = self.guidance(state)
        guidance.update(item)
        state["safe"] = True
        guidance.update(item)
        self.assertEqual(item.route_index, 2)
        self.assertEqual(item.assigned_node, "DOGAP")
        self.assertEqual(item.target_node, "TRIG")

    def test_navigation_watchdog_repairs_only_stale_self_target(self):
        item = aircraft("STALL")
        item.route = ["IN", "DOGAP", "TRIG"]
        item.route_index = 1
        item.assigned_node = "DOGAP"
        item.target_node = "DOGAP"
        item.current_distance = 1.0
        events = []
        guidance = self.guidance({"safe": True})
        guidance.decision_event_callback = lambda *args: events.append(args)
        guidance._record_progress(item, dt=1.0)
        for _ in range(int(guidance.PROGRESS_STALL_SECONDS)):
            guidance._record_progress(item, dt=1.0)
        self.assertEqual(item.target_node, "TRIG")
        self.assertTrue(any(event[1] == "NAVIGATION STALL RECOVERED" for event in events))


class ReservationRetentionTests(unittest.TestCase):
    def test_temporarily_unsafe_owner_keeps_its_valid_reservation(self):
        own = aircraft("OWN")
        conflict = aircraft("CONFLICT")
        conflict.altitude_ft = 6500.0
        engine = sequencing_engine(
            [own, conflict],
            {"CONFLICT": [120.0, 200.0, 300.0],
             "own_entry": {"APP38": 120.0, "APP32": 190.0}},
        )
        engine.approach_reservations["APP38"] = own
        own._approach_entry_index = 0
        own._approach_entry_node = "APP38"

        result = engine._can_approach_handoff(own)

        self.assertFalse(result["safe"])
        self.assertIs(engine.approach_reservations["APP38"], own)
        self.assertEqual(own._approach_entry_node, "APP38")


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
        engine._can_approach_handoff = lambda item: {
            "safe": True, "approach_entry_index": 1, "approach_entry_node": "APP32"
        }
        return engine, own, other

    def test_holding_release_still_requires_circuit_boundary_and_safety(self):
        manager = HoldingManager([])
        item = aircraft("HOLD")
        state = {
            "aircraft": item, "next_circuit_end_index": 4,
            "completed_circuits": 0, "requested_circuits": 3,
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

    def test_active_hold_waypoint_uses_passage_capture_tube(self):
        manager = HoldingManager([])
        item = aircraft("HOLD")
        item.route = ["TRIG", "H1_P01", "H1_P02"]
        item.route_index = 0
        item.assigned_node = "TRIG"
        item.target_node = "H1_P01"
        item.lat, item.lon = 19.0, 73.0003
        item._guidance_previous_position = (19.0, 72.9997)
        item.advance_route = lambda: (
            setattr(item, "route_index", item.route_index + 1),
            setattr(item, "target_node", item.route[item.route_index + 1]),
        )
        manager._active_holds[id(item)] = {"aircraft": item, "holding_route_id": "H1"}
        nodes = {"H1_P01": {"lat": 19.0, "lon": 73.0},
                 "H1_P02": {"lat": 19.0, "lon": 73.1}}
        guidance = object.__new__(GuidanceAI)
        guidance.graph = SimpleNamespace(get_node=lambda name: nodes[name], nodes=nodes)
        guidance.navigator = SimpleNamespace(
            heading_to_node=lambda ac, node: 90.0,
            distance=lambda *args: 0.02,
        )
        guidance.holding_manager = manager
        guidance.arrival_routes = {}
        guidance.arrival_profiles = {}
        guidance.decision_event_callback = None
        guidance.apply_route_constraints = lambda ac: None

        guidance.update(item)

        self.assertEqual(item.assigned_node, "H1_P01")
        self.assertEqual(item.route_index, 1)
        self.assertEqual(item.target_node, "H1_P02")
        self.assertEqual(item.clearance.direct_node, "H1_P02")

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

    def test_pending_model_decision_does_not_block_deterministic_release(self):
        engine, own, _ = self.release_engine(10.0, 20.0)
        own_state = engine.holding_manager.state_for(own)
        own_state["original_route"] = ["IN", "MERGE", "OUT"]
        own_state["rejoin_waypoint"] = "MERGE"
        from app.intelligence.mistral.controller import mistral_controller
        pending = mistral_controller.has_pending_conflict_for
        mistral_controller.has_pending_conflict_for = lambda callsign: True
        try:
            result = engine._can_release_holding(own)
        finally:
            mistral_controller.has_pending_conflict_for = pending
        self.assertTrue(result["safe"])


class StaleConflictDecisionTests(unittest.TestCase):
    def test_pending_model_decision_does_not_pause_aircraft_guidance(self):
        from unittest.mock import patch
        import app.simulation.engine as engine_module

        item = aircraft("MOVING")
        item.approach_complete = False
        engine = object.__new__(SimulationEngine)
        engine.spawn_due_arrivals = lambda dt: None
        engine.process_due_departures = lambda: None
        engine.graph = None
        engine.manager = SimpleNamespace(all=lambda: [item])
        engine._restore_shared_waypoint_speeds = lambda traffic: None
        engine._apply_shared_waypoint_separation = lambda traffic: None
        engine._detect_shared_waypoint_conflicts = lambda traffic: None
        engine._enforce_predictive_separation = lambda traffic: None
        engine._record_route_following_actions = lambda traffic: None
        engine.arrival_ai = Mock()
        engine.guidance = Mock()
        engine.landing_ai = Mock()
        engine._prune_approach_reservations = lambda: None

        with patch.object(engine_module, "apply_arrival_separation"), \
                patch.object(engine_module, "move_aircraft"), \
                patch("app.intelligence.mistral.controller.mistral_controller.has_pending_conflict_for",
                      return_value=True):
            engine.update(1.0)

        engine.guidance.update.assert_called_once_with(item, 1.0)

    def test_conflict_uses_deterministic_hold_before_returning(self):
        controller = MistralController()
        aircraft_item = aircraft("TRAILING")
        leader = aircraft("LEADER")
        aircraft_item.holding_altitude_ft = 9000
        aircraft_item.holding_speed_kts = 210
        option = {"holding_route_id": "H1", "holding_fix": "TRIG", "rejoin_node": None}
        hold_state = {}
        manager = Mock()
        manager.compatible_holding_options.return_value = [option]
        manager.apply_holding_route.return_value = {"success": True}
        manager.state_for.return_value = hold_state
        for item in (aircraft_item, leader):
            item.aircraft_type = "A320"
            item.heading_deg = 180.0
        conflict = {"waypoint": "TRIG", "aircraft": (aircraft_item, leader),
                    "routes": {"r1": {"waypoints": []}}, "holding_routes": [],
                    "aircraft_timing": {"TRAILING": {"eta_to_fix_seconds": 200},
                                        "LEADER": {"eta_to_fix_seconds": 100}}}
        key = controller.conflict_key(conflict)
        callback = Mock()

        requested = controller.request_conflict(conflict, manager, callback)

        self.assertTrue(requested)
        self.assertEqual(controller._conflict_state[key]["status"], "holding")
        manager.apply_holding_route.assert_called_once_with(aircraft_item, "H1", circuits=1)
        self.assertEqual(hold_state["priority_callsign"], "LEADER")
        callback.assert_called_once()
        self.assertEqual(callback.call_args.args[0]["decision_source"], "deterministic_fallback")

    def test_removed_pending_conflict_is_cleared(self):
        controller = MistralController()
        key = ("TRIG", "LEADER", "TRAILING")
        controller._conflict_state[key] = {"status": "pending", "callsigns": {"TRAILING"}}
        controller.clear_conflicts(set())
        self.assertEqual(controller._conflict_state, {})


class HoldingRouteEndTests(unittest.TestCase):
    def test_route_end_continuation_uses_published_arrival_trigger_and_approach(self):
        from app.navigation.loader import load_arrival_routes

        engine = object.__new__(SimulationEngine)
        engine.arrival_routes = load_arrival_routes()
        engine.approach_ai = ApproachAI(None)
        item = aircraft("OLGUS-ARRIVAL", route_id="001")

        self.assertTrue(engine._has_configured_holding_continuation(item, "OLGUS"))
        self.assertFalse(engine._has_configured_holding_continuation(item, "EMROS"))

    def test_route_end_hold_release_restores_trigger_and_selected_approach(self):
        manager = HoldingManager([])
        item = aircraft("ROUTE-END")
        item.route = ["PREV", "TRIG", "H1_P01", "H1_P02", "H1_P03", "TRIG"]
        item.route_index = 5
        item.assigned_node = "TRIG"
        item.target_node = None
        state = {
            "aircraft": item, "next_circuit_end_index": 5,
            "completed_circuits": 0, "requested_circuits": 1,
            "holding_route_id": "H1", "rejoin_waypoint": "TRIG",
            "rejoin_index": 1, "rejoin_node": None,
            "original_route": ["PREV", "TRIG"], "holding_rejoin_index": 5,
            "holding_node_ids": [], "original_target_altitude": None,
            "original_target_speed": None, "original_state": "ENROUTE",
            "original_controller": "Arrival", "original_instruction": "",
        }
        manager._active_holds[id(item)] = state

        result = manager.complete_holding_circuit(item, lambda _: {
            "safe": True, "approach_entry_index": 1, "approach_entry_node": "APP32"
        })

        self.assertTrue(result["released"])
        self.assertFalse(manager.has_active_holding_route(item))
        self.assertEqual((item.route, item.route_index, item.assigned_node, item.target_node),
                         (["PREV", "TRIG"], 2, "TRIG", None))
        self.assertEqual(item.clearance.direct_node, None)
        self.assertEqual(item._approach_entry_node, "APP32")

        guidance = object.__new__(GuidanceAI)
        guidance.holding_manager = manager
        guidance.arrival_routes = {"r1": {"approach_trigger": "TRIG"}}
        guidance.decision_event_callback = None
        guidance.update(item)
        self.assertEqual(item.phase, "FINAL")

        approach = object.__new__(ApproachAI)
        approach.approach_points = [
            {"id": "APP38", "latitude": 19.1, "longitude": 73.1, "altitude_ft": 7000},
            {"id": "APP32", "latitude": 19.0, "longitude": 73.0, "altitude_ft": 6000},
        ]
        approach.speed_profiles = {}
        approach._closest_waypoint_index = lambda *_: 0
        approach._bearing = lambda *_: 90.0
        approach._distance = lambda *_: 10.0
        approach._get_speed = lambda *_: 180
        item.assign_altitude = lambda altitude: None
        item.assign_speed = lambda speed: None
        approach.update(item)
        self.assertEqual(item.target_node, "APP32")


if __name__ == "__main__":
    unittest.main()
