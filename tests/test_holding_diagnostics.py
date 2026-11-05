import ast
import logging
import time
import unittest
from types import SimpleNamespace
from pathlib import Path

from app.simulation.holding_manager import HoldingManager
from tests.holding_config import normalize_holding_routes


def release_harness_class():
    engine_path = Path(__file__).resolve().parents[1] / "app/simulation/engine.py"
    tree = ast.parse(engine_path.read_text(encoding="utf-8"))
    engine_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == "SimulationEngine")
    wanted = {"_can_release_holding", "_holding_release_denial"}
    methods = [node for node in engine_class.body
               if isinstance(node, ast.FunctionDef) and node.name in wanted]
    harness = ast.ClassDef(
        name="ReleaseHarness", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    module = ast.fix_missing_locations(ast.Module(body=[harness], type_ignores=[]))
    namespace = {
        "logging": logging,
        "time": time,
        "__name__": "app.simulation.engine",
    }
    exec(compile(module, str(engine_path), "exec"), namespace)
    return namespace["ReleaseHarness"]


class HoldingDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ReleaseHarness = release_harness_class()

    def test_config_normalizer_accepts_array_and_wrapped_object(self):
        routes = [{"id": "H1"}]
        self.assertEqual(normalize_holding_routes(routes), routes)
        self.assertEqual(normalize_holding_routes({"holding_routes": routes}), routes)
        self.assertEqual(normalize_holding_routes({}), [])

    def test_config_normalizer_rejects_other_top_level_shapes(self):
        with self.assertRaises(ValueError):
            normalize_holding_routes("H1")
        with self.assertRaises(ValueError):
            normalize_holding_routes({"holding_routes": "H1"})

    def test_boundary_capture_diagnostic_is_structured_and_rate_limited(self):
        aircraft = SimpleNamespace(
            callsign="TEST123", route=["A", "B", "FIX", "AFTER"],
            route_index=1, target_node="FIX", assigned_node="B",
        )
        manager = HoldingManager([])
        manager._active_holds[id(aircraft)] = {
            "aircraft": aircraft,
            "holding_route_id": "H-FIX",
            "next_circuit_end_index": 2,
        }
        with self.assertLogs("app.simulation.holding_manager", level="WARNING") as captured:
            manager.report_boundary_capture_rejection(
                aircraft, "outside_capture_radius_without_previous_sample",
                distance_nm=0.2, capture_radius_nm=0.1,
            )
            manager.report_boundary_capture_rejection(
                aircraft, "outside_capture_radius_without_previous_sample",
                distance_nm=0.2, capture_radius_nm=0.1,
            )
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.event, "holding_boundary_capture_rejected")
        self.assertEqual(record.aircraft_callsign, "TEST123")
        self.assertEqual(record.hold_id, "H-FIX")
        self.assertEqual(record.route_cursor, 1)
        self.assertEqual(record.target_waypoint, "FIX")
        self.assertEqual(record.assigned_node, "B")
        self.assertEqual(record.boundary_index, 2)
        self.assertEqual(record.capture_rejection_reason,
                         "outside_capture_radius_without_previous_sample")

    def make_engine_for_release(self, hold, route_definition=None, traffic=()):
        engine = self.ReleaseHarness()
        engine.holding_manager = SimpleNamespace(
            state_for=lambda _aircraft: hold,
            has_active_holding_route=lambda _aircraft: False,
        )
        engine.manager = SimpleNamespace(all=lambda: list(traffic))
        engine.arrival_routes = {"R1": route_definition or {"waypoints": []}}
        engine.MIN_MERGE_SEPARATION_NM = 6.0
        engine.MIN_VERTICAL_SEPARATION_FT = 1000
        engine._approach_merge_eta_seconds = lambda *_args: None
        return engine

    def test_missing_holding_state_has_a_specific_release_denial(self):
        engine = self.make_engine_for_release(None)
        aircraft = SimpleNamespace(callsign="TEST123", holding_route_id="H1")
        with self.assertLogs("app.simulation.engine", level="WARNING") as captured:
            result = engine._can_release_holding(aircraft)
        self.assertEqual(result, {"safe": False, "reason": "Active holding state is missing"})
        record = captured.records[0]
        self.assertEqual(record.event, "holding_release_denied")
        self.assertEqual(record.aircraft_callsign, "TEST123")
        self.assertEqual(record.hold_id, "H1")
        self.assertEqual(record.blocking_condition, "active_holding_state_missing")

    def test_priority_aircraft_block_is_logged_without_changing_result(self):
        hold = {
            "holding_route_id": "H1", "completed_circuits": 1,
            "requested_circuits": 1, "priority_callsign": "LEADER",
            "protected_merge_point": "MERGE",
        }
        priority = SimpleNamespace(
            callsign="LEADER", arrival_route_id="R2", route_index=0,
        )
        engine = self.make_engine_for_release(
            hold, traffic=(priority,),
        )
        engine.arrival_routes["R2"] = {"waypoints": [{"name": "MERGE"}]}
        aircraft = SimpleNamespace(callsign="TEST123")
        with self.assertLogs("app.simulation.engine", level="WARNING") as captured:
            result = engine._can_release_holding(aircraft)
        self.assertEqual(result, {
            "safe": False,
            "reason": "Waiting for priority aircraft LEADER to clear MERGE",
            "other_aircraft": "LEADER",
            "conflict_waypoint": "MERGE",
        })
        record = captured.records[0]
        self.assertEqual(record.circuits_completed, 1)
        self.assertEqual(record.circuits_requested, 1)
        self.assertEqual(record.blocking_aircraft, "LEADER")
        self.assertEqual(record.blocking_condition,
                         "priority_aircraft_not_clear_of_protected_merge")

    def test_approach_handoff_rejection_is_reported_as_release_denial(self):
        hold = {
            "holding_route_id": "H1", "completed_circuits": 2,
            "requested_circuits": 1, "original_route": ["FIX"],
            "rejoin_waypoint": "FIX",
        }
        route = {"waypoints": [{"name": "FIX", "latitude": 19.0, "longitude": 72.0}]}
        engine = self.make_engine_for_release(hold, route)
        engine._can_approach_handoff = lambda _aircraft: {
            "safe": False, "reason": "No configured arrival-trigger approach transition is available",
        }
        aircraft = SimpleNamespace(
            callsign="TEST123", arrival_route_id="R1", target_speed_kts=200,
            speed_kts=200, altitude_ft=10000,
        )
        with self.assertLogs("app.simulation.engine", level="WARNING") as captured:
            result = engine._can_release_holding(aircraft)
        self.assertEqual(result, {
            "safe": False,
            "reason": "No configured arrival-trigger approach transition is available",
        })
        record = captured.records[0]
        self.assertEqual(record.blocking_condition, "approach_handoff_rejected")
        self.assertEqual(record.release_details["handoff_details"]["reason"], result["reason"])


if __name__ == "__main__":
    unittest.main()
