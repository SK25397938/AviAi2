import unittest
from types import SimpleNamespace

from app.simulation.engine import SimulationEngine


def aircraft(callsign, longitude, speed, heading):
    return SimpleNamespace(
        callsign=callsign,
        lat=0.0,
        lon=longitude,
        speed_kts=speed,
        heading_deg=heading,
        altitude_ft=10000.0,
        vertical_speed_fpm=0.0,
        target_speed_kts=0.0,
        target_node=None,
        arrival_route_id=None,
        phase="ARRIVAL",
        route=[],
        route_index=0,
    )


class PredictiveSeparationPerformanceTests(unittest.TestCase):
    def test_far_aircraft_pair_is_skipped_when_it_cannot_reach_threshold(self):
        engine = SimulationEngine.__new__(SimulationEngine)
        events = []
        engine._record_separation_event = lambda *args: events.append(args)
        first = aircraft("A", 0.0, 220.0, 90.0)
        second = aircraft("B", 1.0, 220.0, 270.0)

        engine._enforce_predictive_separation([first, second])

        self.assertEqual(events, [])

    def test_pair_outside_six_nm_is_still_checked_if_it_can_converge_in_horizon(self):
        engine = SimulationEngine.__new__(SimulationEngine)
        events = []
        engine._record_separation_event = lambda *args: events.append(args)
        engine.arrival_routes = {}
        engine.holding_manager = SimpleNamespace(
            compatible_holding_options=lambda *_: [],
            has_active_holding_route=lambda _aircraft: True,
        )
        first = aircraft("A", 0.0, 450.0, 90.0)
        second = aircraft("B", 0.5, 450.0, 270.0)

        engine._enforce_predictive_separation([first, second])

        self.assertGreater(len(events), 0)
        self.assertEqual(events[0][3], "SEPARATION WARNING")


if __name__ == "__main__":
    unittest.main()
