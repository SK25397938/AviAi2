import json
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.simulation.traffic_manager import TrafficManager
from tests.holding_config import load_holding_routes


ROOT = Path(__file__).resolve().parents[1]


class Rwy27UpdateTests(unittest.TestCase):
    def test_approach_preferences_and_olgu_fallback_are_configured_by_trigger(self):
        with (ROOT / "app/navigation/routes/vabb_routes.json").open(encoding="utf-8") as source:
            routes = json.load(source)["routes"]
        for route in routes:
            trigger = route["approach_trigger"]
            if trigger == "BB254":
                self.assertEqual(route["approach_entry_preferences"], ["APP25", "APP32", "APP38"])
            else:
                self.assertEqual(trigger, "BB266")
                self.assertEqual(route["approach_entry_preferences"], ["APP32", "APP38"])
            self.assertIn("OLGUS", route["approach_fallback_preferences"])
        with (ROOT / "data/semantics/vabb_approach.json").open(encoding="utf-8") as source:
            approach = json.load(source)["approach"]
        app25 = next(point for point in approach if point["id"] == "APP25")
        self.assertEqual(app25["arrival_entry_transition"]["type"], "DIRECT_FROM_ARRIVAL_TRIGGER")

    def test_downstream_capacity_uses_route_cursor_and_excludes_holds(self):
        area = "GUTMU"
        route = {"waypoints": [
            {"name": name} for name in ("IN", area, "NM665", "NM667", "NM668", "BB266")
        ], "approach_trigger": "BB266"}
        aircraft = [SimpleNamespace(
            phase="ARRIVAL", arrival_route_id="002", route_index=2,
        ) for _ in range(6)]
        aircraft.append(SimpleNamespace(phase="ARRIVAL", arrival_route_id="002", route_index=0))
        aircraft.append(SimpleNamespace(phase="FINAL", arrival_route_id="002", route_index=2))
        aircraft[-3].holding = True
        engine = SimpleNamespace(
            arrival_routes={"002": route}, manager=SimpleNamespace(all=lambda: aircraft),
            holding_manager=SimpleNamespace(
                has_active_holding_route=lambda item: bool(getattr(item, "holding", False))
            ),
        )
        method = self.downstream_occupancy_method()
        self.assertEqual(method(engine, None, area), 6)
        aircraft[-3].holding = False
        self.assertEqual(method(engine, None, area), 7)

    @staticmethod
    def downstream_occupancy_method():
        tree = ast.parse((ROOT / "app/simulation/engine.py").read_text(encoding="utf-8"))
        engine_class = next(node for node in tree.body
                            if isinstance(node, ast.ClassDef) and node.name == "SimulationEngine")
        method = next(node for node in engine_class.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "_downstream_segment_occupancy")
        method.name = "count"
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        namespace = {}
        exec(compile(module, "engine.py", "exec"), namespace)
        return namespace["count"]

    def test_traffic_wave_intervals_scale_modestly_and_keep_wave_caps(self):
        manager = TrafficManager.__new__(TrafficManager)
        manager.wave_index = 0
        manager.random = SimpleNamespace(uniform=lambda _low, _high: 80.0)
        self.assertEqual(manager._next_arrival_interval(), 72.0)
        self.assertEqual(manager.active_wave_capacity, 5)
        manager.wave_index = 2
        self.assertEqual(manager.active_wave_capacity, 13)

    def test_wave_scheduler_advances_using_simulation_time(self):
        manager = TrafficManager.__new__(TrafficManager)
        manager.wave_index = 0
        manager.wave_timer = 0.0
        manager.random = SimpleNamespace(uniform=lambda _low, _high: 80.0)
        manager._advance_wave(210.0)
        self.assertEqual(manager.wave_index, 1)
        self.assertAlmostEqual(manager.next_arrival_interval, 72.0)

    def test_all_supplied_published_and_synthetic_holds_are_configured(self):
        holds = load_holding_routes(ROOT / "holding_routes.json")
        self.assertEqual(len(holds), 9)
        by_id = {hold["id"]: hold for hold in holds}
        self.assertEqual({name for name, hold in by_id.items() if hold.get("synthetic")}, {
            "H-BB263-SYN", "H-NM668-SYN", "H-BB256-SYN", "H-BB252-SYN",
        })
        self.assertEqual({hold["trigger_waypoint"] for hold in holds}, {
            "UKETU", "DOBVU", "GUTMU", "VELKO", "AKTOD", "BB263", "NM668", "BB256", "BB252",
        })
        self.assertEqual({hold["downstream_capacity"] for hold in holds if hold.get("published")}, {6})


if __name__ == "__main__":
    unittest.main()
