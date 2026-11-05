import json
import math
import unittest
from pathlib import Path

from app.simulation.holding_manager import HoldingManager


ROOT = Path(__file__).resolve().parents[1]


def holding(**values):
    route = {
        "id": "H-TEST",
        "fix": "FIX",
        "trigger_waypoint": "FIX",
        "waypoints": [{"name": "FIX", "latitude": 19.5, "longitude": 72.5}],
        "leg_length_nm": 10,
        "turn_radius_nm": 3.66,
        "turn_direction": "R",
    }
    route.update(values)
    return route


class HoldingCourseResolutionTests(unittest.TestCase):
    def test_aip_holds_generate_valid_geometry_and_preserve_configuration(self):
        expected = {
            "H-GUTMU": ("GUTMU", 19.588292, 72.269156, 130, 129.25, "R", ["002", "003"]),
            "H-DOBVU": ("DOBVU", 19.806797, 73.114522, 198, 197.25, "L", ["001"]),
            "H-VELKO": ("VELKO", 19.402567, 73.588061, 255, 254.25, "L", ["004"]),
            "H-AKTOD": ("AKTOD", 18.59205, 72.273436, 40, 39.25, "L",
                        ["009", "010", "011", "012", "013", "014"]),
            "H-UKETU": ("UKETU", 18.424089, 73.300225, 335, 334.25, "R",
                        ["006", "007", "008"]),
            "H-BB263-SYN": ("BB263", 18.882406, 73.030869, 83, 82.25, "R",
                            ["009", "010", "011", "012", "013", "014"]),
            "H-NM668-SYN": ("NM668", 19.438456, 72.779683, 94, 93.25, "R", ["002", "003"]),
            "H-BB256-SYN": ("BB256", 19.359756, 73.134664, 178, 177.25, "R", ["001"]),
            "H-BB252-SYN": ("BB252", 18.827858, 73.377053, 3, 2.25, "R",
                            ["006", "007", "008"]),
        }
        with (ROOT / "holding_routes.json").open(encoding="utf-8") as source:
            routes = json.load(source)
        self.assertEqual({route["id"] for route in routes}, set(expected))
        manager = HoldingManager(routes)

        for route in routes:
            with self.subTest(hold=route["id"]):
                fix, latitude, longitude, magnetic, true, turn, source_routes = expected[route["id"]]
                self.assertEqual(route["fix"], fix)
                self.assertEqual(route["trigger_waypoint"], fix)
                self.assertEqual(route["waypoints"], [{
                    "name": fix, "latitude": latitude, "longitude": longitude,
                }])
                self.assertEqual(route["inbound_course_mag"], magnetic)
                self.assertEqual(route["inbound_course_true"], true)
                self.assertEqual(route["source_routes"], source_routes)
                self.assertEqual(route["turn_direction"], turn)
                self.assertEqual(route["leg_length_nm"], 5 if route.get("synthetic") else 10)
                self.assertEqual((route["lower_limit"], route["upper_limit"]), ("FL100", "FL180"))
                self.assertEqual(route["max_speed_kt"], 230)
                self.assertEqual(route["capacity"], 2 if route.get("synthetic") else 3)
                self.assertEqual(route.get("synthetic", False), route["id"].endswith("-SYN"))

                points = manager.racetrack_waypoints(route)
                self.assertEqual(len(points), HoldingManager.ARC_SEGMENTS * 2 + 3)
                self.assertTrue(manager._valid_racetrack_geometry(route, points))
                self.assertEqual(points[0]["name"], fix)
                self.assertEqual(points[-1]["name"], fix)

    def test_existing_true_course_configuration_remains_supported(self):
        route = holding(inbound_course_true=95)
        points = HoldingManager([route]).racetrack_waypoints(route)
        self.assertEqual(len(points), HoldingManager.ARC_SEGMENTS * 2 + 3)
        course = HoldingManager._bearing(points[-2], points[-1])
        self.assertLessEqual(abs((course - 95 + 180) % 360 - 180), 1.0)

    def test_magnetic_course_converts_only_with_verified_per_hold_variation(self):
        route = holding(
            inbound_course_mag=100,
            magnetic_variation_deg=-5,
            magnetic_variation_source="verified test fixture reference",
            magnetic_variation_verified=True,
        )
        points = HoldingManager([route]).racetrack_waypoints(route)
        self.assertEqual(len(points), HoldingManager.ARC_SEGMENTS * 2 + 3)
        course = HoldingManager._bearing(points[-2], points[-1])
        self.assertLessEqual(abs((course - 95 + 180) % 360 - 180), 1.0)

    def test_magnetic_course_without_verified_variation_fails_with_hold_diagnostic(self):
        route = holding(inbound_course_mag=100)
        manager = HoldingManager([route])
        with self.assertLogs("app.simulation.holding_manager", level="WARNING") as captured:
            self.assertEqual(manager.racetrack_waypoints(route), [])
        self.assertIn("H-TEST", " ".join(captured.output))
        self.assertIn("verified magnetic_variation", " ".join(captured.output))

    def test_unverified_variation_is_not_used(self):
        route = holding(
            inbound_course_mag=100,
            magnetic_variation_deg=-5,
            magnetic_variation_source="unverified test reference",
            magnetic_variation_verified=False,
        )
        manager = HoldingManager([route])
        with self.assertLogs("app.simulation.holding_manager", level="WARNING") as captured:
            self.assertEqual(manager.racetrack_waypoints(route), [])
        self.assertIn("H-TEST", " ".join(captured.output))

    def test_true_course_takes_precedence_over_magnetic_fields(self):
        route = holding(inbound_course_true=25, inbound_course_mag=100)
        points = HoldingManager([route]).racetrack_waypoints(route)
        course = HoldingManager._bearing(points[-2], points[-1])
        self.assertLessEqual(abs((course - 25 + 180) % 360 - 180), 1.0)

    def test_missing_fix_coordinates_fails_with_hold_diagnostic(self):
        route = holding(inbound_course_true=90, waypoints=[])
        manager = HoldingManager([route])
        with self.assertLogs("app.simulation.holding_manager", level="WARNING") as captured:
            self.assertEqual(manager.racetrack_waypoints(route), [])
        self.assertIn("H-TEST", " ".join(captured.output))
        self.assertIn("coordinate anchor", " ".join(captured.output))

    def test_invalid_true_and_magnetic_course_values_fail(self):
        invalid_values = ("east", math.nan, math.inf, -1, 361, True)
        for field in ("inbound_course_true", "inbound_course_mag"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    options = {field: value}
                    if field == "inbound_course_mag":
                        options.update(
                            magnetic_variation_deg=0,
                            magnetic_variation_source="test reference",
                            magnetic_variation_verified=True,
                        )
                    route = holding(**options)
                    manager = HoldingManager([route])
                    with self.assertLogs("app.simulation.holding_manager", level="WARNING"):
                        self.assertEqual(manager.racetrack_waypoints(route), [])

    def test_every_configured_hold_has_a_fix_coordinate_anchor(self):
        with (ROOT / "holding_routes.json").open(encoding="utf-8") as source:
            routes = json.load(source)
        manager = HoldingManager(routes)
        self.assertEqual(len(routes), 9)
        for route in routes:
            with self.subTest(hold=route["id"]):
                anchor = manager._fix_point(route)
                self.assertIsNotNone(anchor)
                self.assertEqual(anchor[0].upper(), route["trigger_waypoint"].upper())


if __name__ == "__main__":
    unittest.main()
