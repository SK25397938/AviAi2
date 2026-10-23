import unittest
from types import SimpleNamespace

from app.intelligence.guidance_ai import GuidanceAI


WAYPOINTS = {
    "PREV": {"lat": 19.0, "lon": 73.0},
    "DOGAP": {"lat": 19.0, "lon": 73.1},
    "MB398": {"lat": 19.0, "lon": 73.3},
    "NEXT": {"lat": 19.0, "lon": 73.5},
}


def make_aircraft(fix):
    return SimpleNamespace(
        callsign="TEST-" + fix,
        arrival_route_id="r1",
        phase="ARRIVAL",
        route=["PREV", fix, "NEXT"],
        route_index=1,
        assigned_node="PREV",
        target_node=fix,
        state="ENROUTE",
        lat=19.0,
        lon=73.0,
        altitude_ft=7000.0,
        speed_kts=210.0,
        target_speed_kts=210.0,
        clearance=SimpleNamespace(direct_node=fix),
        assign_heading=lambda heading: None,
    )


class WaypointCaptureTests(unittest.TestCase):
    def setUp(self):
        graph = SimpleNamespace(
            get_node=lambda name: WAYPOINTS[name],
            nodes=WAYPOINTS,
        )
        self.guidance = GuidanceAI(
            graph,
            arrival_routes={"r1": {"approach_trigger": "NEXT"}},
        )
        self.guidance.apply_route_constraints = lambda aircraft: None

    def test_dogap_and_mb398_normal_inbound_capture_syncs_route_state(self):
        for fix in ("DOGAP", "MB398"):
            with self.subTest(fix=fix):
                aircraft = make_aircraft(fix)
                aircraft.lat, aircraft.lon = WAYPOINTS[fix]["lat"], WAYPOINTS[fix]["lon"] - 0.003
                self.guidance.update(aircraft)
                self.assertEqual(aircraft.target_node, fix)
                aircraft.lat, aircraft.lon = WAYPOINTS[fix]["lat"], WAYPOINTS[fix]["lon"]
                self.guidance.update(aircraft)
                self.assertEqual(aircraft.route_index, 2)
                self.assertEqual(aircraft.assigned_node, fix)
                self.assertEqual(aircraft.target_node, "NEXT")
                self.assertEqual(aircraft.clearance.direct_node, "NEXT")

    def test_fly_by_crossing_captures_both_fixes_once(self):
        for fix in ("DOGAP", "MB398"):
            with self.subTest(fix=fix):
                aircraft = make_aircraft(fix)
                target_lon = WAYPOINTS[fix]["lon"]
                aircraft.lat, aircraft.lon = 19.0, target_lon - 0.02
                self.guidance.update(aircraft)
                aircraft.lon = target_lon + 0.02
                self.guidance.update(aircraft)
                self.assertEqual((aircraft.route_index, aircraft.assigned_node, aircraft.target_node),
                                 (2, fix, "NEXT"))
                self.guidance.update(aircraft)
                self.assertEqual(aircraft.route_index, 2)

    def test_fix_behind_without_passage_evidence_is_not_captured(self):
        aircraft = make_aircraft("DOGAP")
        aircraft.lat, aircraft.lon = 19.0, WAYPOINTS["DOGAP"]["lon"] + 0.003
        self.guidance.update(aircraft)
        aircraft.lon += 0.003
        self.guidance.update(aircraft)
        self.assertEqual(aircraft.route_index, 1)
        self.assertEqual(aircraft.target_node, "DOGAP")

    def test_repeated_boundary_updates_do_not_advance_twice(self):
        aircraft = make_aircraft("MB398")
        aircraft.lat, aircraft.lon = 19.0, WAYPOINTS["MB398"]["lon"] - 0.0008
        self.guidance.update(aircraft)
        aircraft.lon = WAYPOINTS["MB398"]["lon"]
        self.guidance.update(aircraft)
        for _ in range(5):
            self.guidance.update(aircraft)
        self.assertEqual(aircraft.route_index, 2)
        self.assertEqual(aircraft.assigned_node, "MB398")
        self.assertEqual(aircraft.clearance.direct_node, "NEXT")

