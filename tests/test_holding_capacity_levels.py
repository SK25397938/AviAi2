from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import unittest
import json
from unittest.mock import patch

from app.intelligence.mistral import client as mistral_client
from app.intelligence.mistral.controller import MistralController
from app.simulation.engine import SimulationEngine
from app.simulation.holding_manager import HoldingManager
from tests.test_holding_adaptive import aircraft_for, load_holds


class HoldingCapacityLevelTests(unittest.TestCase):
    def setUp(self):
        self.routes = load_holds()

    def _manager_and_hold(self, hold_id):
        route = deepcopy(next(route for route in self.routes if route["id"] == hold_id))
        return HoldingManager([route]), route

    def test_primary_and_synthetic_capacities_and_levels(self):
        primary = {"H-GUTMU", "H-DOBVU", "H-AKTOD", "H-VELKO", "H-UKETU"}
        synthetic = {"H-NM668-SYN", "H-BB256-SYN", "H-BB252-SYN", "H-BB263-SYN"}
        for route in self.routes:
            if route["id"] in primary:
                self.assertEqual(route["capacity"], 3)
                self.assertEqual(route["holding_levels_ft"], [12000, 14000, 16000, 18000])
            if route["id"] in synthetic:
                self.assertEqual(route["capacity"], 2)
                self.assertEqual(route["holding_levels_ft"], [12000, 14000])

    def test_synthetic_candidates_are_only_configured_levels_and_each_can_be_assigned(self):
        for hold_id in ("H-BB263-SYN", "H-NM668-SYN", "H-BB256-SYN", "H-BB252-SYN"):
            manager, hold = self._manager_and_hold(hold_id)
            self.assertEqual(manager._holding_levels(hold, 10000, 18000), [12000.0, 14000.0])
            for level in hold["holding_levels_ft"]:
                with self.subTest(hold=hold_id, level=level):
                    manager, hold = self._manager_and_hold(hold_id)
                    aircraft, _, _ = aircraft_for(hold)
                    aircraft.altitude_ft = aircraft.target_altitude_ft = level
                    result = manager.apply_holding_route(aircraft, hold_id, preferred_level_ft=level)
                    self.assertTrue(result["success"], result["message"])
                    self.assertEqual(manager.state_for(aircraft)["assigned_holding_level_ft"], level)

    def test_synthetic_assignment_rejects_unsafe_levels_individually_and_together(self):
        for unsafe in ({12000.0}, {14000.0}, {12000.0, 14000.0}):
            with self.subTest(unsafe=unsafe):
                manager, hold = self._manager_and_hold("H-BB256-SYN")
                aircraft, _, _ = aircraft_for(hold)
                with patch.object(manager, "_level_transition_is_safe",
                                  side_effect=lambda _aircraft, level, _others: level not in unsafe):
                    preferred = next(iter(unsafe)) if len(unsafe) == 1 else None
                    result = manager.apply_holding_route(
                        aircraft, hold["id"], preferred_level_ft=preferred
                    )
                    self.assertFalse(result["success"])
                    self.assertIsNone(manager.state_for(aircraft))

    def test_primary_capacity_counts_slots_across_levels(self):
        manager, hold = self._manager_and_hold("H-GUTMU")
        occupants = []
        for level in (12000, 14000, 16000):
            aircraft, _, _ = aircraft_for(hold)
            aircraft.altitude_ft = level
            aircraft.target_altitude_ft = level
            result = manager.apply_holding_route(aircraft, hold["id"])
            self.assertTrue(result["success"], result["message"])
            occupants.append(aircraft)
        extra, _, _ = aircraft_for(hold)
        extra.altitude_ft = extra.target_altitude_ft = 18000
        self.assertFalse(manager.apply_holding_route(extra, hold["id"])["success"])
        self.assertEqual(manager.holding_occupancy(hold["id"]), 3)
        self.assertEqual([manager.state_for(a)["assigned_holding_level_ft"] for a in occupants],
                         [12000, 14000, 16000])

    def test_allocator_can_select_each_configured_primary_level(self):
        _, hold = self._manager_and_hold("H-GUTMU")
        for level in hold["holding_levels_ft"]:
            with self.subTest(level=level):
                manager = HoldingManager([deepcopy(hold)])
                aircraft, _, _ = aircraft_for(hold)
                aircraft.altitude_ft = aircraft.target_altitude_ft = level
                result = manager.apply_holding_route(aircraft, hold["id"])
                self.assertTrue(result["success"], result["message"])
                self.assertEqual(manager.state_for(aircraft)["assigned_holding_level_ft"], level)

    def test_allocator_rejects_unsafe_climb_through_occupied_level(self):
        manager, hold = self._manager_and_hold("H-GUTMU")
        first, _, _ = aircraft_for(hold)
        first.altitude_ft = first.target_altitude_ft = 12000
        self.assertTrue(manager.apply_holding_route(first, hold["id"])["success"])
        second, _, _ = aircraft_for(hold)
        second.altitude_ft = second.target_altitude_ft = 12000
        result = manager.apply_holding_route(second, hold["id"])
        self.assertFalse(result["success"])
        self.assertIn("1,000 ft", result["message"])

    def test_synthetic_capacity_is_two(self):
        manager, hold = self._manager_and_hold("H-BB256-SYN")
        for altitude in (9000, 13000):
            aircraft, _, _ = aircraft_for(hold)
            aircraft.altitude_ft = aircraft.target_altitude_ft = altitude
            self.assertTrue(manager.apply_holding_route(aircraft, hold["id"])["success"])
        aircraft, _, _ = aircraft_for(hold)
        self.assertFalse(manager.apply_holding_route(aircraft, hold["id"])["success"])
        self.assertEqual(manager.holding_occupancy(hold["id"]), 2)

    def test_concurrent_reservations_cannot_exceed_capacity(self):
        manager, hold = self._manager_and_hold("H-GUTMU")
        aircraft = []
        for index in range(12):
            item, _, _ = aircraft_for(hold)
            item.callsign = f"T{index}"
            item.altitude_ft = item.target_altitude_ft = 12000 + index * 2000
            aircraft.append(item)
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(lambda item: manager.apply_holding_route(item, hold["id"]), aircraft))
        self.assertEqual(sum(result["success"] for result in results), 3)
        self.assertEqual(manager.holding_occupancy(hold["id"]), 3)

    def test_level_change_occurs_only_at_circuit_boundary_after_denied_release(self):
        manager, hold = self._manager_and_hold("H-GUTMU")
        aircraft, _, _ = aircraft_for(hold)
        aircraft.altitude_ft = aircraft.target_altitude_ft = 12000
        self.assertTrue(manager.apply_holding_route(aircraft, hold["id"])["success"])
        state = manager.state_for(aircraft)
        state["next_circuit_end_index"] = 0
        aircraft.route_index = 0
        self.assertTrue(manager.complete_holding_circuit(aircraft, lambda _: {"safe": False})["success"])
        self.assertEqual(state["target_holding_level_ft"], 12000)
        state["next_circuit_end_index"] = 0
        self.assertTrue(manager.complete_holding_circuit(aircraft, lambda _: {"safe": False})["success"])
        self.assertNotEqual(state["target_holding_level_ft"], 12000)
        self.assertEqual(aircraft.target_altitude_ft, state["target_holding_level_ft"])
        self.assertEqual(state["last_level_change_circuit"], 2)

    def test_ai_can_choose_a_safe_level_at_the_eligible_boundary(self):
        manager, hold = self._manager_and_hold("H-GUTMU")
        aircraft, _, _ = aircraft_for(hold)
        aircraft.callsign = "AI-LEVEL"
        aircraft.aircraft_type = "A320"
        aircraft.heading_deg = 130
        aircraft.altitude_ft = aircraft.target_altitude_ft = 12000
        self.assertTrue(manager.apply_holding_route(aircraft, hold["id"])["success"])
        state = manager.state_for(aircraft)
        state["completed_circuits"] = 2
        engine = object.__new__(SimulationEngine)
        engine.holding_manager = manager
        engine.ai_arrival_decisions_enabled = True
        engine.ai_decision_log = []
        engine._last_separation_event_at = {}
        controller = MistralController()
        self.addCleanup(controller._executor.shutdown, wait=True)

        with patch("app.simulation.engine.mistral_controller", controller), \
             patch.object(mistral_client, "client", object()), \
             patch.object(mistral_client, "generate", return_value=json.dumps({
                 "choice": "14000", "reason": "Maintain 2,000 ft vertical spacing"
             })):
            pending = engine._choose_holding_level(aircraft, state)
            self.assertEqual(pending["choice"], "PENDING")
            next(iter(controller._operational_pending.values()))["future"].result(timeout=2)
            decision = engine._choose_holding_level(aircraft, state)

        self.assertEqual(decision["level_ft"], 14000)
        self.assertEqual(decision["source"], "Mistral")
        self.assertEqual(manager._select_holding_level(
            aircraft, hold, 10000, 18000, exclude_aircraft=aircraft,
            exclude_level=12000, preferred_level=decision["level_ft"],
        ), 14000)

    def test_restoring_route_releases_capacity(self):
        manager, hold = self._manager_and_hold("H-GUTMU")
        aircraft, _, _ = aircraft_for(hold)
        self.assertTrue(manager.apply_holding_route(aircraft, hold["id"])["success"])
        state = manager.state_for(aircraft)
        aircraft.assigned_node = state["rejoin_waypoint"]
        aircraft.route_index = state["holding_rejoin_index"]
        self.assertTrue(manager.restore_original_route(aircraft)["success"])
        self.assertEqual(manager.holding_occupancy(hold["id"]), 0)

    def test_inactive_aircraft_reservation_is_released(self):
        manager, hold = self._manager_and_hold("H-GUTMU")
        aircraft, _, _ = aircraft_for(hold)
        self.assertTrue(manager.apply_holding_route(aircraft, hold["id"])["success"])
        self.assertEqual(manager.prune_inactive_holds([]), 1)
        self.assertEqual(manager.holding_occupancy(hold["id"]), 0)
        self.assertIsNone(manager.state_for(aircraft))


if __name__ == "__main__":
    unittest.main()
