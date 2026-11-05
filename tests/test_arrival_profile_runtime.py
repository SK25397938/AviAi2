import unittest
from types import SimpleNamespace

from app.navigation.arrival_profile import resolve_spawn_altitude, resolve_spawn_speed
from app.navigation.loader import load_arrival_profiles, load_arrival_routes
from app.simulation.engine import SimulationEngine
from app.intelligence.guidance_ai import GuidanceAI


class ArrivalProfileRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = load_arrival_profiles()
        cls.routes = load_arrival_routes()

    def resolve(self, route_id, index, fraction=0.0):
        points = self.routes[route_id]["waypoints"]
        start = points[index]
        end = points[min(index + 1, len(points) - 1)]
        return resolve_spawn_altitude(
            route_id, points, self.profiles,
            start["latitude"] + (end["latitude"] - start["latitude"]) * fraction,
            start["longitude"] + (end["longitude"] - start["longitude"]) * fraction,
            9999,
        )

    def test_csv_profiles_cover_rwy27_routes_and_preserve_constraint_types(self):
        self.assertIn("001", self.profiles)
        self.assertEqual(self.profiles["001"]["DOBVU"]["altitude"],
                         {"type": "AT", "feet": 15000})
        self.assertEqual(self.profiles["001"]["BB255"]["altitude"]["type"], "AT_OR_BELOW")
        self.assertEqual(self.profiles["002"]["NM665"]["altitude"]["type"], "AT_OR_ABOVE")
        self.assertNotIn("altitude", self.profiles["001"]["BB257"],
                         "carried simulation altitude is not a published restriction")

    def test_start_altitude_uses_route_start_not_aircraft_default(self):
        result = self.resolve("001", 0)
        self.assertTrue(result["resolved"])
        self.assertEqual(result["altitude_ft"], 15000)

    def test_engine_initializes_altitude_and_speed_before_first_guidance(self):
        route = self.routes["001"]
        aircraft = SimpleNamespace(
            callsign="PROFILE", arrival_route_id="001", lat=0.0, lon=0.0,
            aircraft_type="A320",
            altitude_ft=10000, target_altitude_ft=10000,
            speed_kts=250, target_speed_kts=250,
        )
        engine = object.__new__(SimulationEngine)
        engine.arrival_profiles = self.profiles
        engine._initialize_arrival_route(aircraft, route)
        self.assertEqual(aircraft.altitude_ft, 15000)
        self.assertEqual(aircraft.target_altitude_ft, 15000)
        self.assertEqual(aircraft.speed_kts, 250)
        self.assertGreater(aircraft.target_speed_kts, 290)
        self.assertLess(aircraft.target_speed_kts, 350)

    def test_terminal_speed_limit_is_not_applied_at_upper_route_spawn(self):
        route = self.routes["002"]
        aircraft = SimpleNamespace(
            callsign="UPPER", arrival_route_id="002", aircraft_type="A320",
            lat=0.0, lon=0.0, altitude_ft=10000, target_altitude_ft=10000,
            speed_kts=250, target_speed_kts=250,
        )
        engine = object.__new__(SimulationEngine)
        engine.arrival_profiles = self.profiles

        engine._initialize_arrival_route(aircraft, route)

        self.assertEqual(aircraft.altitude_ft, 28000)
        self.assertGreater(aircraft.target_speed_kts, 400)

    def test_spawn_altitude_at_exact_at_or_above_and_at_or_below_fixes(self):
        cases = (("001", "DOBVU", 15000, "AT"),
                 ("002", "NM665", 16000, "AT_OR_ABOVE"),
                 ("001", "BB255", 9000, "AT_OR_BELOW"))
        for route_id, name, feet, rule in cases:
            points = self.routes[route_id]["waypoints"]
            waypoint = next(point for point in points if point["name"] == name)
            result = resolve_spawn_altitude(
                route_id, points, self.profiles, waypoint["latitude"], waypoint["longitude"], 9999
            )
            self.assertTrue(result["resolved"])
            self.assertEqual(result["altitude_ft"], feet)
            self.assertEqual(self.profiles[route_id][name]["altitude"]["type"], rule)

    def test_intermediate_altitude_uses_cumulative_route_segment(self):
        result = self.resolve("001", 1, 0.5)
        self.assertTrue(result["resolved"])
        self.assertEqual(result["segment"], (1, 2))
        self.assertGreater(result["altitude_ft"], 9000)
        self.assertLess(result["altitude_ft"], 15000)

    def test_downstream_altitude_uses_downstream_profile(self):
        last_index = len(self.routes["001"]["waypoints"]) - 1
        result = self.resolve("001", last_index)
        self.assertTrue(result["resolved"])
        self.assertEqual(result["altitude_ft"], 8000)

    def test_bb254_and_bb266_use_route_specific_descent_targets_without_overriding_published_fixes(self):
        for route_id in ("006", "007", "008", "009", "010", "011", "012", "013", "014"):
            self.assertEqual(self.profiles[route_id]["BB254"]["profile_altitude_ft"], 8000)
            self.assertNotIn("altitude", self.profiles[route_id]["BB254"])
        for route_id in ("001", "002", "003", "004"):
            self.assertEqual(self.profiles[route_id]["BB266"]["profile_altitude_ft"], 8000)
        self.assertEqual(self.profiles["004"]["BB257"]["altitude"],
                         {"type": "AT", "feet": 10000})

    def test_profile_altitude_changes_target_through_descent_guidance(self):
        route_id, fix = "009", "BB254"
        point = next(item for item in self.routes[route_id]["waypoints"] if item["name"] == fix)
        aircraft = SimpleNamespace(
            phase="ARRIVAL", arrival_route_id=route_id, aircraft_type="A320",
            target_node=fix, route=[fix], altitude_ft=10000.0, target_altitude_ft=11000.0,
            speed_kts=200.0, target_speed_kts=200.0, lat=point["latitude"], lon=point["longitude"],
            assign_altitude=lambda value: setattr(aircraft, "target_altitude_ft", value),
            assign_speed=lambda value: setattr(aircraft, "target_speed_kts", value),
        )
        guidance = object.__new__(GuidanceAI)
        guidance.arrival_profiles = self.profiles
        guidance.holding_manager = None
        guidance.graph = SimpleNamespace(get_node=lambda _name: {"lat": point["latitude"], "lon": point["longitude"]})
        guidance._distance_nm = lambda *_args: 5.0

        guidance.apply_route_constraints(aircraft)

        self.assertEqual(aircraft.target_altitude_ft, 8000)
        self.assertLess(aircraft.target_vertical_speed_fpm, 0)
        self.assertEqual(aircraft.altitude_ft, 10000.0,
                         "guidance sets a descent target without an instantaneous altitude jump")

    def test_speed_profile_keeps_published_and_carried_limits_distinct(self):
        self.assertEqual(self.profiles["001"]["BB256"]["speed"],
                         {"type": "MAX", "knots": 190.0, "basis": "published"})
        self.assertEqual(self.profiles["001"]["BB257"]["speed"]["basis"], "carried")
        self.assertEqual(self.profiles["009"]["BB254"]["speed"],
                         {"type": "AT", "knots": 190.0, "basis": "published"})

    def test_spawn_speed_resolves_by_downstream_route_position(self):
        points = self.routes["009"]["waypoints"]
        for name, expected in (("BB263", 220.0), ("BB253", 200.0), ("BB254", 190.0)):
            point = next(item for item in points if item["name"] == name)
            progress = self.profiles["009"][name]["cum_nm"]
            result = resolve_spawn_speed(points, self.profiles, "009", progress, 260.0)
            self.assertTrue(result["resolved"])
            self.assertEqual(result["waypoint"], name)
            self.assertEqual(result["speed_kts"], expected)

    def test_missing_route_profile_uses_explicit_fallback(self):
        points = self.routes["001"]["waypoints"]
        result = resolve_spawn_altitude("missing", points, self.profiles,
                                        points[0]["latitude"], points[0]["longitude"], 18000)
        self.assertFalse(result["resolved"])
        self.assertEqual(result["altitude_ft"], 18000)
        self.assertEqual(result["waypoint"], "AKTIV")


if __name__ == "__main__":
    unittest.main()
