import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.navigation.loader import load_arrival_profiles, load_arrival_routes
from app.simulation.holding_manager import HoldingManager


ROOT = Path(__file__).resolve().parents[1]


class VabbStar3KRouteDataTests(unittest.TestCase):
    def test_runtime_activates_requested_route_ids_and_preserves_sequences(self):
        route_payload = json.loads(
            (ROOT / "app/navigation/routes/vabb_routes.json").read_text(encoding="utf-8")
        )
        configured = {str(route["id"]): route for route in route_payload["routes"]}
        loaded = load_arrival_routes()
        requested_ids = {"001", "002", "003", "004", *(f"{route_id:03d}" for route_id in range(6, 15))}

        self.assertTrue(requested_ids.issubset(loaded))
        self.assertEqual(len(configured), len(route_payload["routes"]))
        self.assertTrue(requested_ids.issubset(configured))
        expected_sequences = {
            "001": ["AKTIV", "DOBVU", "BB255", "BB256", "BB257", "BB266"],
            "002": ["IPNIB", "NM625", "NM515", "NM626", "GUTMU", "NM665", "NM667", "NM668", "BB264", "BB257", "BB266"],
            "003": ["EXOLU", "NM624", "GUTMU", "NM665", "NM667", "NM668", "BB264", "BB257", "BB266"],
            "004": ["OPAKA", "VELKO", "BB258", "BB259", "BB257", "BB266"],
        }
        for route_id in requested_ids:
            route = configured[route_id]
            self.assertEqual(loaded[route_id], route)
            if route_id in expected_sequences:
                self.assertEqual([point["name"] for point in route["waypoints"]], expected_sequences[route_id])

    def test_configured_hold_source_routes_and_runtime_filtering(self):
        holds = json.loads((ROOT / "holding_routes.json").read_text(encoding="utf-8"))
        route_data = load_arrival_routes()
        expected_sources = {
            "H-GUTMU": ["002", "003"],
            "H-DOBVU": ["001"],
            "H-VELKO": ["004"],
            "H-AKTOD": ["009", "010", "011", "012", "013", "014"],
            "H-UKETU": ["006", "007", "008"],
        }
        by_hold = {hold["id"]: hold for hold in holds}
        self.assertEqual({key: by_hold[key]["source_routes"] for key in expected_sources}, expected_sources)

        # Give the isolated selection fixture a valid true course; production holds
        # retain their unresolved magnetic-only course and remain geometrically inactive.
        for hold_id, source_ids in expected_sources.items():
            hold = dict(by_hold[hold_id], inbound_course_true=0)
            for route_id in source_ids:
                route = route_data[route_id]
                names = [point["name"] for point in route["waypoints"]]
                aircraft = SimpleNamespace(
                    route=names,
                    route_index=0,
                    arrival_route_id=route_id,
                )
                conflict = names[-1]
                options = HoldingManager([hold]).compatible_holding_options(aircraft, conflict)
                self.assertEqual([option["holding_route_id"] for option in options], [hold_id])

            hold["source_routes"] = ["ROUTE-NOT-ASSIGNED"]
            route_id = source_ids[0]
            route = route_data[route_id]
            aircraft = SimpleNamespace(
                route=[point["name"] for point in route["waypoints"]],
                route_index=0,
                arrival_route_id=route_id,
            )
            self.assertEqual(
                HoldingManager([hold]).compatible_holding_options(aircraft, aircraft.route[-1]),
                [],
            )

    def test_added_star_routes_match_published_sequences_and_profiles(self):
        route_payload = json.loads(
            (ROOT / "app/navigation/routes/vabb_routes.json").read_text(encoding="utf-8")
        )
        routes = route_payload["routes"]
        route_ids = [route["id"] for route in routes]
        self.assertEqual(len(route_ids), len(set(route_ids)))

        expected = {
            "AGELA": ["AGELA", "NM601", "BEDOL", "UKETU", "BB251", "BB252", "BB253", "BB254"],
            "BEDOL": ["BEDOL", "UKETU", "BB251", "BB252", "BB253", "BB254"],
            "EPKOS": ["EPKOS", "NM602", "BEDOL", "UKETU", "BB251", "BB252", "BB253", "BB254"],
            "BISET": ["BISET", "NM614", "AKTOD", "BB260", "BB261", "BB262", "BB263", "BB253", "BB254"],
            "DARMI": ["DARMI", "NM615", "NM616", "AKTOD", "BB260", "BB261", "BB262", "BB263", "BB253", "BB254"],
            "ERVIS": ["ERVIS", "NM617", "NM508", "NM618", "AKTOD", "BB260", "BB261", "BB262", "BB263", "BB253", "BB254"],
            "GUNDI": ["GUNDI", "NM619", "NM616", "AKTOD", "BB260", "BB261", "BB262", "BB263", "BB253", "BB254"],
            "KABSO": ["KABSO", "NM617", "NM508", "NM618", "AKTOD", "BB260", "BB261", "BB262", "BB263", "BB253", "BB254"],
            "SUGID": ["SUGID", "NM620", "NM621", "AKTOD", "BB260", "BB261", "BB262", "BB263", "BB253", "BB254"],
        }
        by_name = {route["name"].split()[0]: route for route in routes}
        self.assertTrue(set(expected).issubset(by_name))

        loaded_routes = load_arrival_routes()
        loaded_profiles = load_arrival_profiles()
        for name, sequence in expected.items():
            route = by_name[name]
            route_id = route["id"]
            names = [point["name"] for point in route["waypoints"]]
            self.assertEqual(names, sequence)
            self.assertEqual(list(loaded_routes[route_id]["waypoints"]), route["waypoints"])
            self.assertEqual(list(loaded_profiles[route_id]), sequence)
            self.assertEqual(route["approach_trigger"], "BB254")
            self.assertEqual(names[-1], route["approach_trigger"])
            self.assertNotIn("BB257", names)
            self.assertEqual(
                route["published_termination"],
                {
                    "type": "VECTOR",
                    "identifier": "VM",
                    "course_magnetic_deg": 350.0,
                    "course_true_deg": 349.25,
                },
            )
            for point in route["waypoints"]:
                self.assertIsInstance(point["latitude"], (int, float))
                self.assertIsInstance(point["longitude"], (int, float))
                self.assertGreaterEqual(point["latitude"], -90)
                self.assertLessEqual(point["latitude"], 90)
                self.assertGreaterEqual(point["longitude"], -180)
                self.assertLessEqual(point["longitude"], 180)

    def test_published_restrictions_are_loaded_for_route_waypoints(self):
        profiles = load_arrival_profiles()
        expected = {
            "006": {"NM601": ("AT", 24000), "BEDOL": ("AT", 24000), "UKETU": ("AT_OR_ABOVE", 12000), "BB251": ("AT", 11000)},
            "007": {"UKETU": ("AT_OR_ABOVE", 12000), "BB251": ("AT", 11000)},
            "008": {"NM602": ("AT", 25000), "BEDOL": ("AT", 25000), "UKETU": ("AT_OR_ABOVE", 12000)},
            "009": {"NM614": ("AT", 20000), "AKTOD": ("AT_OR_ABOVE", 16000), "BB263": ("AT_OR_BELOW", 11000), "BB253": ("AT", 10000)},
            "010": {"NM615": ("AT", 21000), "NM616": ("AT", 21000), "AKTOD": ("AT_OR_ABOVE", 16000)},
            "011": {"NM617": ("AT", 28000), "NM508": ("AT_OR_ABOVE", 25000), "NM618": ("AT", 24000)},
            "012": {"NM619": ("AT", 25000), "NM616": ("AT", 25000)},
            "013": {"NM617": ("AT", 29000), "NM508": ("AT_OR_ABOVE", 25000)},
            "014": {"NM620": ("AT", 29000), "NM621": ("AT", 25000)},
        }
        for route_id, waypoint_constraints in expected.items():
            for waypoint, (constraint_type, feet) in waypoint_constraints.items():
                self.assertEqual(
                    profiles[route_id][waypoint]["altitude"],
                    {"type": constraint_type, "feet": feet},
                )
            for waypoint in ("BB254",):
                speed = profiles[route_id][waypoint]["speed"]
                if route_id in {"006", "007", "008"}:
                    self.assertEqual(speed, {"type": "MAX", "knots": 210.0, "basis": "published"})
                else:
                    self.assertEqual(speed, {"type": "AT", "knots": 190.0, "basis": "published"})


if __name__ == "__main__":
    unittest.main()
