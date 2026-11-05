import unittest

from app.intelligence.fallback_selection import (compatible_fallbacks,
                                                 evaluate_fallback_candidates,
                                                 route_end_fallback_candidates)
from app.navigation.loader import load_arrival_routes


class FallbackSelectionTests(unittest.TestCase):
    def test_dogap_is_never_an_eligible_candidate(self):
        self.assertEqual(compatible_fallbacks(["DOGAP"], ["BB266", "DOGAP"], 0,
                                              {"BB266", "DOGAP"}), [])

    def test_olgus_is_global_and_requires_only_loaded_navigation_data(self):
        self.assertEqual(compatible_fallbacks(["OLGUS"], ["BB266", "OLGUS"], 0,
                                              {"BB266", "OLGUS"}), ["OLGUS"])
        self.assertEqual(compatible_fallbacks(["OLGUS"], ["BB266"], 1,
                                              {"BB266", "OLGUS"}), ["OLGUS"])

    def test_mb398_is_global_and_does_not_need_star_membership(self):
        self.assertEqual(compatible_fallbacks([], ["BB254", "MB398"], 0,
                                              {"BB254", "MB398"}), ["MB398"])
        self.assertEqual(compatible_fallbacks([], ["BB254"], 1, {"BB254", "MB398"}), ["MB398"])

    def test_no_compatible_fallback_fails_closed(self):
        self.assertEqual(compatible_fallbacks(["OLGUS"], ["BB266"], 1, {"BB266"}), [])
        self.assertEqual(evaluate_fallback_candidates(["OLGUS"], ["BB266"], 0, {"BB266"}),
                         {"status": "NO_COMPATIBLE_FALLBACK", "candidates": []})

    def test_route_end_fallbacks_require_configured_reachable_navigation_nodes(self):
        self.assertEqual(route_end_fallback_candidates(
            ["DOGAP", "MB398", "OLGUS"], ["BB266"], 1, "BB266",
            {"BB266", "DOGAP", "MB398", "OLGUS"}), ["MB398", "OLGUS"])
        self.assertEqual(route_end_fallback_candidates(
            ["OLGUS"], ["BB266", "NEXT"], 1, "BB266", {"BB266", "OLGUS"}), [])
        self.assertEqual(route_end_fallback_candidates(
            ["OLGUS"], ["BB266"], 1, "BB266", {"BB266"}), [])

    def test_loaded_routes_006_and_009_can_use_global_fallbacks(self):
        routes = load_arrival_routes()
        for route_id in ("006", "009"):
            route = routes[route_id]
            names = [point["name"] for point in route["waypoints"]]
            candidates = route_end_fallback_candidates(
                route.get("approach_fallback_preferences", []), names, len(names),
                route["approach_trigger"], {"MB398", "OLGUS"},
            )
            self.assertEqual(set(candidates), {"MB398", "OLGUS"})


if __name__ == "__main__":
    unittest.main()
