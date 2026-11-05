import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.intelligence.mistral import client as mistral_client
from app.intelligence.mistral.controller import MistralController
from app.simulation.holding_manager import HoldingManager
from tests.test_holding_adaptive import aircraft_for, load_holds


def model_response(decision, callsign="TEST", hold_id="H-GUTMU", fix="GUTMU",
                   altitude=12000, circuits=1, rejoin="NM665"):
    data = {
        "controller": "Arrival", "decision": decision,
        "callsign": callsign if decision == "HOLD" else None,
        "instruction": "Hold at GUTMU" if decision == "HOLD" else "",
        "holding_route_id": hold_id if decision == "HOLD" else None,
        "holding_fix": fix if decision == "HOLD" else None,
        "altitude": altitude if decision == "HOLD" else None,
        "speed": None, "hold_circuits": circuits if decision == "HOLD" else None,
        "rejoin_node": rejoin if decision == "HOLD" else None,
        "reason": "Predicted spacing conflict" if decision == "HOLD" else "",
    }
    return json.dumps(data)


class MistralControllerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.routes = deepcopy(load_holds())
        self.hold = next(item for item in self.routes if item["id"] == "H-GUTMU")
        self.manager = HoldingManager(self.routes)
        self.aircraft, self.graph, _ = aircraft_for(self.hold)
        self.aircraft.callsign = "TEST"
        self.aircraft.aircraft_type = "A320"
        self.aircraft.heading_deg = 90.0
        self.aircraft.route_index = 0
        self.aircraft.route.append("BB266")
        self.aircraft.target_node = self.aircraft.route[1]
        self.other = deepcopy(self.aircraft)
        self.other.callsign = "OTHER"
        self.other.aircraft_type = "A320"
        self.other.heading_deg = 90.0
        self.other.route = list(self.aircraft.route)
        self.other.route_index = 0
        self.other.arrival_route_id = self.aircraft.arrival_route_id
        self.other.lat += 0.2
        self.other.target_node = self.other.route[1]
        self.conflict = {
            "waypoint": "BB266", "aircraft": [self.aircraft, self.other],
            "aircraft_timing": {"TEST": {"eta_to_fix_seconds": 100},
                                "OTHER": {"eta_to_fix_seconds": 200}},
            "routes": {self.aircraft.arrival_route_id: {"waypoints": []}},
            "holding_routes": self.routes, "holding_geometries": {},
        }
        self.controller = MistralController()
        self.addCleanup(self.controller._executor.shutdown, wait=True)

    def _run_async_decision(self, response):
        callbacks = []
        with patch.object(mistral_client, "client", object()), \
             patch.object(mistral_client, "generate", return_value=response):
            self.assertTrue(self.controller.request_conflict(
                self.conflict, self.manager, callbacks.append
            ))
            key = self.controller.conflict_key(self.conflict)
            if key not in self.controller._pending:
                self.fail(callbacks[-1].get("validation_reason", "request used immediate fallback"))
            self.controller._pending[key]["future"].result(timeout=2)
            self.controller.process_pending([self.conflict], self.manager, callbacks.append)
        self.assertEqual(len(callbacks), 1)
        return callbacks[0]

    def test_real_conflict_request_uses_valid_model_hold_and_level(self):
        result = self._run_async_decision(model_response(
            "HOLD", callsign="TEST", hold_id="H-NM668-SYN", fix="NM668",
            altitude=14000, rejoin="BB264"
        ))
        self.assertEqual(result["decision_source"], "Mistral")
        self.assertEqual(result["decision"]["callsign"], "TEST")
        self.assertEqual(result["decision"]["holding_route_id"], "H-NM668-SYN")
        self.assertEqual(result["selected_aircraft"].holding_altitude_ft, 14000)
        self.assertEqual(self.manager.state_for(self.aircraft)["priority_callsign"], "OTHER")
        self.assertEqual(result["validation_result"], "accepted")

    def test_model_none_cannot_clear_conflict_and_falls_back_deterministically(self):
        result = self._run_async_decision(model_response("NONE"))
        self.assertEqual(result["decision_source"], "deterministic_fallback")
        self.assertEqual(result["decision"]["decision"], "HOLD")
        self.assertTrue(result["holding"])

    def test_stale_position_snapshot_is_rejected(self):
        snapshot = self.controller._snapshot(self.conflict)
        self.other.lat += 0.1
        self.assertFalse(self.controller._snapshot_is_current(snapshot, self.conflict, self.manager))

    def test_hold_release_operational_choice_rejects_response_from_previous_circuit(self):
        decision_id = ("hold-release", "TEST", "H-GUTMU", self.aircraft.arrival_route_id)
        first_snapshot = {"completed_circuits": 1, "route_index": 5,
                          "target_node": "H-GUTMU-OUT", "safe_nodes": ("APP32", "APP38")}
        with patch.object(mistral_client, "client", object()), \
             patch.object(mistral_client, "generate", return_value=json.dumps({
                 "choice": "RELEASE", "reason": "Previous circuit assessment"
             })):
            initial = self.controller.operational_choice(
                decision_id, "choose release", {"RELEASE", "CONTINUE"},
                first_snapshot, "RELEASE",
            )
            self.assertEqual(initial["status"], "pending")
            self.controller._operational_pending[decision_id]["future"].result(timeout=2)
            next_circuit = dict(first_snapshot, completed_circuits=2, route_index=10)
            result = self.controller.operational_choice(
                decision_id, "choose release again", {"RELEASE", "CONTINUE"},
                next_circuit, "RELEASE",
            )

        self.assertEqual(result["source"], "deterministic_fallback")
        self.assertEqual(result["reason"], "AI response rejected as stale")

    def test_unresolved_conflict_without_compatible_hold_is_retried(self):
        no_holds = HoldingManager([])
        callbacks = []
        with patch.object(mistral_client, "client", None):
            self.controller.request_conflict(self.conflict, no_holds, callbacks.append)
        key = self.controller.conflict_key(self.conflict)
        self.assertFalse(callbacks[-1]["holding"])
        self.assertEqual(self.controller._conflict_state[key]["status"], "unresolved")

        # Bypass the bounded operational retry interval: the active unsafe
        # conflict may be replanned; it was not treated as resolved.
        self.controller._conflict_state[key]["retry_after"] = 0.0
        with patch.object(mistral_client, "client", object()), \
             patch.object(mistral_client, "generate", return_value=model_response("NONE")):
            self.assertTrue(self.controller.request_conflict(
                self.conflict, no_holds, callbacks.append
            ))
            pending = self.controller._pending[key]
            pending["future"].result(timeout=2)
            self.controller.process_pending([self.conflict], no_holds, callbacks.append)

        self.assertEqual(len(callbacks), 2)
        self.assertFalse(callbacks[-1]["holding"])
        self.assertEqual(self.controller._conflict_state[key]["status"], "unresolved")

    def test_conflict_state_reopens_after_held_aircraft_leaves_the_hold(self):
        key = self.controller.conflict_key(self.conflict)
        self.controller._conflict_state[key] = {"status": "holding", "held_callsign": "TEST"}

        self.controller.clear_conflicts({key}, active_holding_callsigns=())

        self.assertNotIn(key, self.controller._conflict_state)


if __name__ == "__main__":
    unittest.main()
