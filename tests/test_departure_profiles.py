import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.navigation.departure_profile import (
    departure_speed_limit_gs,
    match_departure_profile,
)
from app.navigation.arrival_profile import arrival_schedule_groundspeed
from app.navigation.loader import (load_departure_profiles, load_departure_routes,
                                   load_departure_destination_map)
from app.simulation.engine import SimulationEngine


ROOT = Path(__file__).resolve().parents[1]


class DepartureProfilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_departure_profiles()
        cls.routes = load_departure_routes()

    def test_all_supplied_profile_rows_are_loaded(self):
        self.assertEqual(sum(len(rows) for rows in self.profiles.values()), 1095)
        self.assertEqual(len(self.profiles), 180)

    def test_route_aircraft_and_ordered_waypoints_must_all_match(self):
        route = self.routes["001"]
        profile = match_departure_profile(route, "A320", self.profiles)
        self.assertIsNotNone(profile)
        self.assertEqual([row["waypoint"] for row in profile],
                         [point["name"] for point in route["waypoints"]])
        self.assertIsNone(match_departure_profile(route, "UNLISTED", self.profiles))
        changed = dict(route, waypoints=list(route["waypoints"]) + [dict(route["waypoints"][-1])])
        self.assertIsNone(match_departure_profile(changed, "A320", self.profiles))

    def test_aircraft_aliases_match_csv_profiles_without_changing_route(self):
        self.assertIsNotNone(match_departure_profile(
            self.routes["001"], "B788", self.profiles
        ))

    def test_runway_and_bb154_climbout_values_remain_distinct(self):
        rows = match_departure_profile(self.routes["001"], "A320", self.profiles)
        runway, bb154 = rows[:2]
        self.assertEqual(runway["alt_ft"], 20)
        self.assertEqual(runway["gs_kt"], 0)
        self.assertEqual(runway["pub_alt_constraint"], {
            "operator": ">=", "value": 540.0, "unit": "ft"
        })
        self.assertEqual(bb154["gs_kt"], 267)
        self.assertIsNone(bb154["pub_alt_constraint"])
        nm561 = next(row for row in rows if row["waypoint"] == "NM561")
        self.assertEqual(nm561["pub_alt_constraint"], {
            "operator": "<=", "value": 10000.0, "unit": "ft"
        })

    def test_speed_units_remain_distinct_and_published_cap_is_converted(self):
        row = match_departure_profile(self.routes["001"], "A320", self.profiles)[1]
        self.assertEqual(row["ias_kt"], 250)
        self.assertEqual(row["gs_kt"], 267)
        cap = departure_speed_limit_gs(row["pub_speed_constraint"], row["alt_ft"])
        self.assertGreater(cap, 250)
        self.assertLess(cap, row["gs_kt"] + 1)
        self.assertGreater(arrival_schedule_groundspeed("A320", 29000), 400)

    def test_arrival_schedule_uses_high_altitude_mach_and_decelerates(self):
        at_fl290 = arrival_schedule_groundspeed("A320", 29000)
        at_fl250 = arrival_schedule_groundspeed("A320", 25000)
        at_fl200 = arrival_schedule_groundspeed("A320", 20000)
        at_18000 = arrival_schedule_groundspeed("A320", 18000)
        at_12000 = arrival_schedule_groundspeed("A320", 12000)
        at_7000 = arrival_schedule_groundspeed("A320", 7000)
        self.assertGreater(at_fl290, 400)
        self.assertGreater(at_fl200, 400)
        self.assertGreater(at_fl290, at_fl250)
        self.assertGreater(at_fl250, at_fl200)
        self.assertGreater(at_fl200, at_18000)
        self.assertGreater(at_fl200, at_12000)
        self.assertGreater(at_12000, at_7000)
        self.assertGreaterEqual(at_12000, 260)
        self.assertLessEqual(at_12000, 270)
        self.assertGreater(arrival_schedule_groundspeed("B777-300ER", 29000), at_fl290)

    def test_departure_starts_on_runway_in_takeoff_roll_state(self):
        class Manager:
            def __init__(self):
                self.aircraft = []

            def all(self):
                return list(self.aircraft)

            def add(self, aircraft):
                self.aircraft.append(aircraft)

        class Scheduler:
            db = SimpleNamespace(mark_departing=lambda _callsign: None)

            @staticmethod
            def get_due_departures(**_kwargs):
                return [{"callsign": "TEST001", "airline": "TEST", "destination": "BETKU",
                         "aircraft_type": "A320"}]

        engine = object.__new__(SimulationEngine)
        engine.manager = Manager()
        engine.scheduler = Scheduler()
        engine.traffic_manager = SimpleNamespace(active_wave=("LOW",), active_wave_capacity=5)
        engine.departure_routes = self.routes
        engine.departure_profiles = self.profiles
        engine.departure_waypoints = {}

        engine.process_due_departures()

        aircraft = engine.manager.all()[0]
        self.assertEqual(aircraft.phase, "DEPARTURE")
        self.assertEqual(aircraft.altitude_ft, 20)
        self.assertEqual(aircraft.speed_kts, 0)
        self.assertEqual(aircraft.target_speed_kts, 150)
        self.assertTrue(aircraft.on_ground)
        self.assertEqual(aircraft.target_node, "BB154")
        self.assertTrue(aircraft.departure_profile_available)

        aircraft.speed_kts = 140.0
        engine.spawn_due_arrivals = lambda _dt: None
        engine.process_due_departures = lambda: None
        engine.graph = SimpleNamespace()
        engine.guidance = SimpleNamespace(update=lambda _aircraft, _dt: None)
        engine._restore_shared_waypoint_speeds = lambda _traffic: None
        engine._apply_shared_waypoint_separation = lambda _traffic: None
        engine._detect_shared_waypoint_conflicts = lambda _traffic: None
        engine._enforce_predictive_separation = lambda _traffic: None
        engine._record_route_following_actions = lambda _traffic: None
        engine._prune_approach_reservations = lambda: None
        with patch("app.simulation.engine.apply_arrival_separation"):
            engine.update(1.0)
        self.assertEqual(aircraft.departure_climbout_minimum_ft, 540)
        self.assertGreaterEqual(aircraft.target_altitude_ft, 540)
        self.assertEqual(aircraft.target_vertical_speed_fpm, 2400)
        self.assertLess(aircraft.altitude_ft, 540)
        self.assertGreater(aircraft.altitude_ft, 20)
        self.assertLess(aircraft.speed_kts, aircraft.target_speed_kts)
        aircraft.assigned_node = "BB154"
        aircraft.altitude_ft = 1000.0
        aircraft.speed_kts = 250.0
        engine._validate_departure_fix_constraints(aircraft)
        self.assertTrue(aircraft.departure_constraint_checks["altitude:RWY27 climb-out"]["passed"])
        self.assertTrue(aircraft.departure_constraint_checks["speed:BB154"]["passed"])

    def test_unmatched_destination_does_not_get_an_unrelated_route(self):
        engine = object.__new__(SimulationEngine)
        engine.departure_routes = self.routes
        self.assertIsNone(engine._departure_route_for("NOT-IN-ROUTE-NETWORK"))

    def test_scheduled_destination_uses_explicit_configured_sid_mapping(self):
        engine = object.__new__(SimulationEngine)
        engine.departure_routes = self.routes
        engine.departure_destination_map = load_departure_destination_map()
        engine.runway = SimpleNamespace(ident="27")

        self.assertEqual(engine._departure_route_for("DXB")["route_id"], "004")
        self.assertEqual(engine._departure_route_for("FRA")["route_id"], "004")
        self.assertIsNone(engine._departure_route_for("NOT-IN-ROUTE-NETWORK"))

    def test_due_dxb_departure_spawns_on_runway_with_matching_profile(self):
        class Manager:
            def __init__(self):
                self.aircraft = []

            def all(self):
                return list(self.aircraft)

            def add(self, aircraft):
                self.aircraft.append(aircraft)

        class Scheduler:
            MAX_ACTIVE_AIRCRAFT = 13
            db = SimpleNamespace(mark_departing=lambda _callsign: None)

            @staticmethod
            def get_due_departures(**_kwargs):
                return [{"callsign": "TEST-DXB", "airline": "TEST", "destination": "DXB",
                         "aircraft_type": "A320"}]

        engine = object.__new__(SimulationEngine)
        engine.manager = Manager()
        engine.scheduler = Scheduler()
        engine.traffic_manager = SimpleNamespace(active_wave=("LOW",), active_wave_capacity=5)
        engine.departure_routes = self.routes
        engine.departure_destination_map = load_departure_destination_map()
        engine.departure_profiles = self.profiles
        engine.departure_waypoints = {}
        engine.runway = SimpleNamespace(ident="27")

        engine.process_due_departures()

        aircraft = engine.manager.all()[0]
        self.assertEqual(aircraft.departure_route_id, "004")
        self.assertEqual(aircraft.assigned_node, "RWY27")
        self.assertEqual(aircraft.target_node, "BB154")
        self.assertEqual(aircraft.state, "TAKEOFF_ROLL")
        self.assertTrue(aircraft.on_ground)
        self.assertTrue(aircraft.departure_profile_available)
        self.assertEqual(engine.departure_diagnostics[0]["status"], "spawned_on_runway")


if __name__ == "__main__":
    unittest.main()
