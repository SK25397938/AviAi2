import json
import math
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from app.simulation.holding_manager import HoldingManager


HOLDING_CONFIG = Path(__file__).resolve().parents[1] / "holding_routes.json"


class TestGraph:
    def __init__(self, nodes):
        self.nodes = nodes

    def add_node(self, name, lat, lon):
        self.nodes[name] = {"lat": lat, "lon": lon}


def load_holds():
    with HOLDING_CONFIG.open(encoding="utf-8") as source:
        return json.load(source)["holding_routes"]


def aircraft_for(holding):
    trigger = holding["trigger_waypoint"]
    trigger_point = next(point for point in holding["waypoints"]
                         if point["name"].upper() == trigger.upper())
    previous = f"{trigger}_PREV"
    following = f"{trigger}_NEXT"
    route_id = str(holding.get("source_routes", ["001"])[0])
    route = [previous, trigger, following]
    nodes = {
        previous: {"lat": trigger_point["latitude"] - 0.1,
                   "lon": trigger_point["longitude"]},
        trigger: {"lat": trigger_point["latitude"],
                  "lon": trigger_point["longitude"]},
        following: {"lat": trigger_point["latitude"] + 0.1,
                    "lon": trigger_point["longitude"]},
    }
    aircraft = SimpleNamespace(
        callsign="TEST", arrival_route_id=route_id, route=route, route_index=0,
        assigned_node=previous, target_node=trigger, lat=nodes[previous]["lat"],
        lon=nodes[previous]["lon"], altitude_ft=9000.0, target_altitude_ft=9000.0,
        speed_kts=220.0, target_speed_kts=220.0, state="ENROUTE", phase="ARRIVAL",
        controller="Arrival", last_instruction="", clearance=SimpleNamespace(direct_node=trigger),
        assign_altitude=lambda value: setattr(aircraft, "target_altitude_ft", value),
        assign_speed=lambda value: setattr(aircraft, "target_speed_kts", value),
    )
    return aircraft, route_id, TestGraph(nodes)


def read_haversine_nm(first, second):
    lat1, lat2 = math.radians(first["latitude"]), math.radians(second["latitude"])
    dlat = lat2 - lat1
    dlon = math.radians(second["longitude"] - first["longitude"])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3440.065 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))


class AdaptiveHoldingTests(unittest.TestCase):
    def make_manager(self, routes=None, holding_id=None):
        routes = deepcopy(routes if routes is not None else load_holds())
        if holding_id:
            routes = [route for route in routes if route["id"] == holding_id]
        return HoldingManager(routes)

    def apply_for_delay(self, holding, delay):
        aircraft, _, graph = aircraft_for(holding)
        manager = HoldingManager([deepcopy(holding)], graph)
        result = manager.apply_holding_route(
            aircraft, holding["id"], required_delay_seconds=delay
        )
        self.assertTrue(result["success"], result["message"])
        return manager, aircraft, manager.state_for(aircraft)

    def circuit_time_seconds(self, manager, holding, leg_nm, speed_kts=220.0):
        route = dict(holding, leg_length_nm=leg_nm)
        points = manager.racetrack_waypoints(route)
        length = sum(read_haversine_nm(first, second)
                     for first, second in zip(points, points[1:]))
        return length * 3600.0 / speed_kts

    def test_short_and_standard_geometry_keep_configured_orientation_and_order(self):
        for holding in load_holds():
            adaptive = holding.get("adaptive_leg_length_nm", {})
            if adaptive.get("enabled") is not True:
                continue
            manager = self.make_manager(holding_id=holding["id"])
            geometries = []
            for leg_nm in (holding["leg_length_nm"], adaptive["short_nm"]):
                route = dict(holding, leg_length_nm=leg_nm)
                points = manager.racetrack_waypoints(route)
                self.assertTrue(manager._valid_racetrack_geometry(route, points), holding["id"])
                self.assertEqual(points[0]["name"].upper(), holding["fix"].upper())
                self.assertEqual(points[-1]["name"].upper(), holding["fix"].upper())
                self.assertEqual(
                    [point["name"] for point in points[1:-1]],
                    [f"{holding['id']}_P{index:02d}" for index in range(1, len(points) - 1)],
                )

                course = float(holding["inbound_course_true"]) % 360.0
                outbound_start = manager.ARC_SEGMENTS
                outbound_end = outbound_start + 1
                inbound_start = manager.ARC_SEGMENTS * 2 + 1
                outbound = manager._bearing(points[outbound_start], points[outbound_end])
                inbound = manager._bearing(points[inbound_start], points[inbound_start + 1])
                self.assertLessEqual(abs((outbound - (course + 180.0) + 180.0) % 360.0 - 180.0), 1.0)
                self.assertLessEqual(abs((inbound - course + 180.0) % 360.0 - 180.0), 1.0)

                turn_sign = -1.0 if holding["turn_direction"] == "L" else 1.0
                first_arc_heading = manager._bearing(points[0], points[1])
                first_turn = (first_arc_heading - course + 180.0) % 360.0 - 180.0
                second_arc_heading = manager._bearing(points[outbound_end], points[outbound_end + 1])
                second_turn = (second_arc_heading - outbound + 180.0) % 360.0 - 180.0
                self.assertGreater(first_turn * turn_sign, 0.0, holding["id"])
                self.assertGreater(second_turn * turn_sign, 0.0, holding["id"])

                expected_chord = 2.0 * float(holding["turn_radius_nm"]) * math.sin(
                    math.pi / (2.0 * manager.ARC_SEGMENTS)
                )
                for first, second in ((points[0], points[1]),
                                      (points[outbound_end], points[outbound_end + 1])):
                    self.assertAlmostEqual(
                        read_haversine_nm(first, second), expected_chord, delta=0.1,
                        msg=holding["id"],
                    )
                geometries.append(points)

            standard, short = geometries
            self.assertEqual(len(standard), len(short))
            self.assertEqual([point["name"] for point in standard],
                             [point["name"] for point in short])
            self.assertEqual(standard[0]["latitude"], short[0]["latitude"])
            self.assertEqual(standard[0]["longitude"], short[0]["longitude"])
            self.assertNotEqual(standard[manager.ARC_SEGMENTS + 1],
                                short[manager.ARC_SEGMENTS + 1])
            configured = holding["waypoints"]
            generated_anchor_indices = (0, manager.ARC_SEGMENTS,
                                        manager.ARC_SEGMENTS + 1,
                                        manager.ARC_SEGMENTS * 2 + 1, -1)
            for configured_point, generated_index in zip(configured, generated_anchor_indices):
                self.assertLessEqual(
                    read_haversine_nm(configured_point, standard[generated_index]), 0.5,
                    msg=f"{holding['id']} configured waypoint {configured_point['name']} order/position",
                )

    def test_configured_short_and_standard_lengths_select_matching_geometry(self):
        for holding in load_holds():
            with self.subTest(route=holding["id"]):
                manager = self.make_manager(holding_id=holding["id"])
                adaptive = holding["adaptive_leg_length_nm"]
                short_time = self.circuit_time_seconds(
                    manager, holding, adaptive["short_nm"]
                )
                _, _, short_state = self.apply_for_delay(holding, short_time * 0.8)
                _, _, standard_state = self.apply_for_delay(holding, short_time + 1.0)

                outbound_start = manager.ARC_SEGMENTS
                inbound_start = manager.ARC_SEGMENTS * 2 + 1
                short_distance = read_haversine_nm(
                    short_state["holding_waypoints"][outbound_start],
                    short_state["holding_waypoints"][outbound_start + 1]
                )
                standard_distance = read_haversine_nm(
                    standard_state["holding_waypoints"][outbound_start],
                    standard_state["holding_waypoints"][outbound_start + 1],
                )
                self.assertAlmostEqual(short_distance, adaptive["short_nm"], delta=0.15)
                self.assertAlmostEqual(standard_distance, holding["leg_length_nm"], delta=0.15)
                self.assertAlmostEqual(
                    read_haversine_nm(short_state["holding_waypoints"][inbound_start],
                                      short_state["holding_waypoints"][inbound_start + 1]),
                    adaptive["short_nm"], delta=0.15,
                )
                self.assertAlmostEqual(adaptive["standard_nm"], holding["leg_length_nm"])

    def test_adaptive_choice_preserves_procedure_limits_turn_and_entry_configuration(self):
        for holding in load_holds():
            with self.subTest(route=holding["id"]):
                original = deepcopy(holding)
                manager, aircraft, state = self.apply_for_delay(holding, 0.0)
                self.assertEqual(holding, original)
                self.assertEqual(aircraft.target_speed_kts, min(220.0, holding["max_speed_kt"]))
                lower = manager._flight_level(holding["lower_limit"])
                upper = manager._flight_level(holding["upper_limit"])
                self.assertGreaterEqual(aircraft.target_altitude_ft, lower + 1000)
                self.assertLessEqual(aircraft.target_altitude_ft, upper)
                entry_directions = holding.get("entry_turn_directions", {})
                expected_direction = entry_directions.get(
                    aircraft.arrival_route_id, holding.get("entry_turn_direction")
                )
                entry_nodes = [node for node in aircraft.route if "_ENTRY_" in node]
                if expected_direction:
                    self.assertEqual(len(entry_nodes), 1)
                    self.assertTrue(entry_nodes[0].endswith(f"_{expected_direction}"))
                self.assertAlmostEqual(
                    float(holding["turn_radius_nm"]),
                    float(manager._holding_route_by_id(holding["id"])["turn_radius_nm"]),
                )
                self.assertEqual(state["holding_route_id"], holding["id"])
                points = state["holding_waypoints"]
                inbound_start = manager.ARC_SEGMENTS * 2 + 1
                inbound_heading = manager._bearing(points[inbound_start], points[inbound_start + 1])
                course_error = abs((inbound_heading - float(holding["inbound_course_true"])
                                    + 180.0) % 360.0 - 180.0)
                self.assertLessEqual(course_error, 1.0)
                outbound_start = manager.ARC_SEGMENTS
                outbound_heading = manager._bearing(points[outbound_start], points[outbound_start + 1])
                arc_heading = manager._bearing(points[0], points[1])
                signed_turn = (arc_heading - float(holding["inbound_course_true"]) + 180.0) % 360.0 - 180.0
                if holding["turn_direction"] == "L":
                    self.assertLess(signed_turn, 0.0)
                else:
                    self.assertGreater(signed_turn, 0.0)
                expected_chord = (2.0 * holding["turn_radius_nm"]
                                  * math.sin(math.pi / (2.0 * manager.ARC_SEGMENTS)))
                actual_chord = read_haversine_nm(points[0], points[1])
                self.assertAlmostEqual(actual_chord, expected_chord, delta=0.1)

    def test_standard_is_used_without_adaptive_configuration(self):
        for holding in load_holds():
            with self.subTest(route=holding["id"]):
                legacy = deepcopy(holding)
                legacy.pop("adaptive_leg_length_nm")
                manager, _, state = self.apply_for_delay(legacy, 0.0)
                distance = read_haversine_nm(
                    state["holding_waypoints"][manager.ARC_SEGMENTS],
                    state["holding_waypoints"][manager.ARC_SEGMENTS + 1]
                )
                self.assertAlmostEqual(distance, holding["leg_length_nm"], delta=0.15)

    def test_geometry_invalidity_prevents_activation_without_changing_route(self):
        holding = deepcopy(load_holds()[0])
        holding["turn_radius_nm"] = 0
        aircraft, _, graph = aircraft_for(holding)
        original_route = list(aircraft.route)
        manager = HoldingManager([holding], graph)

        result = manager.apply_holding_route(
            aircraft, holding["id"], required_delay_seconds=0.0
        )

        self.assertFalse(result["success"])
        self.assertEqual(aircraft.route, original_route)
        self.assertFalse(manager.has_active_holding_route(aircraft))

    def test_route_end_hold_requires_configured_continuation(self):
        holding = deepcopy(load_holds()[0])
        aircraft, _, graph = aircraft_for(holding)
        aircraft.route = ["EMROS_PREV", "EMROS"]
        manager = HoldingManager([holding], graph)
        rejected = manager.apply_holding_route(aircraft, holding["id"])
        self.assertFalse(rejected["success"])
        self.assertIn("No configured approach continuation", rejected["message"])
        self.assertEqual(aircraft.route, ["EMROS_PREV", "EMROS"])

        manager.route_continuation_validator = lambda item, fix: fix == "EMROS"
        accepted = manager.apply_holding_route(aircraft, holding["id"])
        self.assertTrue(accepted["success"], accepted["message"])
        self.assertIsNone(manager.state_for(aircraft)["rejoin_node"])

    def test_traffic_reassessment_does_not_resize_active_hold_and_denial_keeps_it(self):
        holding = load_holds()[0]
        adaptive = holding["adaptive_leg_length_nm"]
        manager, aircraft, state = self.apply_for_delay(holding, 0.0)
        geometry_before = deepcopy(state["holding_waypoints"])
        route_before = list(aircraft.route)
        selected_leg = read_haversine_nm(
            geometry_before[manager.ARC_SEGMENTS], geometry_before[manager.ARC_SEGMENTS + 1]
        )
        self.assertAlmostEqual(selected_leg, adaptive["short_nm"], delta=0.15)

        second = manager.apply_holding_route(
            aircraft, holding["id"], required_delay_seconds=999999.0
        )
        self.assertFalse(second["success"])
        self.assertEqual(state["holding_waypoints"], geometry_before)
        self.assertEqual(aircraft.route, route_before)

        aircraft.route_index = state["next_circuit_end_index"]
        aircraft.assigned_node = state["rejoin_waypoint"]
        release = manager.complete_holding_circuit(aircraft, lambda _: {"safe": False})
        self.assertFalse(release["released"])
        self.assertTrue(manager.has_active_holding_route(aircraft))
        self.assertEqual(state["holding_waypoints"], geometry_before)

