import json
import unittest
from pathlib import Path

from app.intelligence.aircraft_state import AircraftState
from app.intelligence.guidance_ai import GuidanceAI
from app.simulation.holding_manager import HoldingManager
from app.simulation.physics import move_aircraft


ROOT = Path(__file__).resolve().parents[1]


class NavigationGraph:
    def __init__(self):
        self.nodes = {}

    def add_node(self, name, latitude, longitude):
        self.nodes[name] = {"lat": latitude, "lon": longitude}

    def get_node(self, name):
        return self.nodes[name]


class HoldingNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "holding_routes.json").open(encoding="utf-8") as source:
            cls.holds = json.load(source)["holding_routes"]
        with (ROOT / "app/navigation/routes/vabb_routes.json").open(encoding="utf-8") as source:
            cls.routes = json.load(source)["routes"]

    def simulate_one_circuit(self, hold_id, leg_length):
        hold = next(route for route in self.holds if route["id"] == hold_id)
        route = next(route for route in self.routes
                     if str(route["id"]) in hold["source_routes"])
        graph = NavigationGraph()
        for configured_route in self.routes:
            for point in configured_route["waypoints"]:
                graph.add_node(point["name"], point["latitude"], point["longitude"])
        for configured_hold in self.holds:
            for point in configured_hold["waypoints"]:
                graph.add_node(point["name"], point["latitude"], point["longitude"])

        fix = next(point for point in hold["waypoints"]
                   if point["name"].upper() == hold["trigger_waypoint"].upper())
        aircraft = AircraftState(
            callsign="TEST-HOLD", aircraft_type="A320",
            lat=fix["latitude"], lon=fix["longitude"], altitude_ft=9000,
            speed_kts=210, heading_deg=86, vertical_speed_fpm=0,
        )
        aircraft.route = [point["name"] for point in route["waypoints"]]
        trigger_index = next(index for index, point in enumerate(route["waypoints"])
                             if point["name"].upper() == hold["trigger_waypoint"].upper())
        aircraft.route_index = trigger_index
        aircraft.assigned_node = aircraft.route[trigger_index]
        aircraft.target_node = aircraft.route[trigger_index]
        aircraft.arrival_route_id = str(route["id"])
        aircraft.target_speed_kts = 210
        aircraft.clearance.direct_node = aircraft.target_node

        manager = HoldingManager(self.holds, graph)
        manager.route_continuation_validator = lambda _aircraft, _trigger: True
        guidance = GuidanceAI(graph, holding_manager=manager,
                              holding_release_validator=lambda _aircraft: {"safe": False})
        guidance.apply_route_constraints = lambda _aircraft: None
        delay = 0.0 if leg_length == "short" else 1_000_000_000.0
        result = manager.apply_holding_route(aircraft, hold_id, required_delay_seconds=delay)
        self.assertTrue(result["success"], result["message"])
        state = manager.state_for(aircraft)
        points = state["holding_waypoints"]
        if leg_length == "short":
            configured_leg = hold["adaptive_leg_length_nm"]["short_nm"]
        else:
            configured_leg = hold["leg_length_nm"]
        self.assertAlmostEqual(
            manager._distance_nm(points[manager.ARC_SEGMENTS],
                                 points[manager.ARC_SEGMENTS + 1]),
            configured_leg, delta=0.15,
        )

        expected_targets = [point["name"] for point in points[1:]]
        seen_targets = []
        previous_target = None
        for _ in range(3000):
            guidance.update(aircraft)
            target = aircraft.target_node
            if target is not None and manager.has_active_holding_route(aircraft):
                self.assertEqual(target, aircraft.route[aircraft.route_index + 1])
                self.assertEqual(aircraft.clearance.direct_node, target)
                self.assertAlmostEqual(
                    aircraft.target_heading_deg,
                    guidance.navigator.heading_to_node(aircraft, target),
                    delta=1e-6,
                )
            if target != previous_target and target in expected_targets:
                seen_targets.append(target)
                previous_target = target
            move_aircraft(aircraft, 1.0)
            if state["completed_circuits"] == 1:
                break
        self.assertEqual(seen_targets[:len(expected_targets)], expected_targets)
        self.assertEqual(state["completed_circuits"], 1)
        self.assertTrue(manager.has_active_holding_route(aircraft))
        self.assertEqual(aircraft.target_node, points[1]["name"])
        self.assertEqual(aircraft.clearance.direct_node, aircraft.target_node)

    def test_simulated_short_and_standard_left_and_right_holds_complete_in_order(self):
        for hold_id in ("H-OLGUS", "H-LIKTA"):
            for leg_length in ("short", "standard"):
                with self.subTest(hold=hold_id, leg=leg_length):
                    self.simulate_one_circuit(hold_id, leg_length)


if __name__ == "__main__":
    unittest.main()
