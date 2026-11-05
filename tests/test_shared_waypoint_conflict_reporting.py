from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.simulation.engine import SimulationEngine


class SharedWaypointConflictReportingTests(unittest.TestCase):
    def test_conflict_record_reports_predicted_vertical_separation_in_feet(self):
        first = SimpleNamespace(
            callsign="ARR001", arrival_route_id="route_a", phase="ARRIVAL",
            speed_kts=200.0, target_speed_kts=200.0, altitude_ft=10000.0,
            vertical_speed_fpm=0.0, target_node="SHARED", route_index=1,
        )
        second = SimpleNamespace(
            callsign="ARR002", arrival_route_id="route_b", phase="ARRIVAL",
            speed_kts=200.0, target_speed_kts=200.0, altitude_ft=9000.0,
            vertical_speed_fpm=1000.0, target_node="SHARED", route_index=1,
        )
        first_occurrence = ("route_a", {"name": "SHARED"}, 5.0, (19.1, 73.4))
        second_occurrence = ("route_b", {"name": "SHARED"}, 5.5, (19.1, 73.4))
        engine = object.__new__(SimulationEngine)
        engine.shared_arrival_waypoint_groups = [[first_occurrence, second_occurrence]]
        engine.arrival_routes = {"route_a": {}, "route_b": {}}
        engine.holding_routes = []
        engine.runway = SimpleNamespace(ident="27")
        engine.holding_manager = SimpleNamespace(has_active_holding_route=lambda aircraft: False)
        engine._route_progress_nm = lambda aircraft, route: 0.0
        engine._active_route_distance_to_trigger_nm = lambda aircraft, route, waypoint: None
        engine._route_segment_length_nm = lambda start, end: 0.0
        engine._apply_merge_speed_delay = lambda *args: False
        engine._record_separation_event = lambda *args: None
        engine._detect_approach_merge_conflicts = lambda aircraft_list: []

        with patch("app.simulation.engine.mistral_controller") as controller:
            controller.conflict_key.side_effect = lambda conflict: conflict["waypoint"]
            controller.request_conflict.return_value = False

            engine._detect_shared_waypoint_conflicts([first, second])

        conflict = controller.request_conflict.call_args.args[0]
        self.assertEqual(conflict["required_separation_nm"], 6.0)
        self.assertAlmostEqual(conflict["predicted_timing"]["predicted_separation_nm"], 0.5)
        self.assertEqual(
            conflict["aircraft_timing"]["ARR001"]["predicted_vertical_separation_ft"], 650
        )
        self.assertEqual(
            conflict["aircraft_timing"]["ARR002"]["predicted_vertical_separation_ft"], 650
        )


if __name__ == "__main__":
    unittest.main()
