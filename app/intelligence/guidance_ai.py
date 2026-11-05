import math
import logging

from app.intelligence.graph_navigator import GraphNavigator
from app.intelligence.fallback_selection import (evaluate_fallback_candidates,
                                                  route_end_fallback_candidates)
from app.navigation.arrival_profile import arrival_schedule_groundspeed
from app.navigation.speed_conversion import indicated_to_true_airspeed
from app.simulation.physics import TURN_RATE_DEG_PER_SEC


class GuidanceAI:

    WAYPOINT_CAPTURE_DISTANCE = 0.002
    # Arrival aircraft move farther than the taxi capture radius each update.
    # Physical capture radius in nautical miles (about 600 ft).
    ARRIVAL_WAYPOINT_CAPTURE_DISTANCE = 0.1
    TAXI_SPEED = 15
    PROGRESS_STALL_SECONDS = 60.0

    @staticmethod
    def _distance_nm(lat1, lon1, lat2, lon2):
        """Great-circle distance in nautical miles."""
        from math import asin, cos, radians, sin, sqrt

        lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        return 2 * 3440.065 * asin(sqrt(min(1.0, a)))

    TAXI_PREFERRED_CONNECTIONS = {
        "N10": ["N9"],
        "N9": ["N8"]
    }

    def __init__(
        self,
        graph,
        holding_manager=None,
        arrival_routes=None,
        holding_release_validator=None,
        holding_release_callback=None,
        arrival_profiles=None,
        decision_event_callback=None,
        approach_handoff_validator=None,
        holding_level_decider=None,
        fallback_transition_validator=None,
    ):

        self.graph = graph
        self.holding_manager = holding_manager
        self.arrival_routes = arrival_routes or {}
        self.holding_release_validator = holding_release_validator
        self.holding_release_callback = holding_release_callback
        self.arrival_profiles = arrival_profiles or {}
        self.decision_event_callback = decision_event_callback
        self.approach_handoff_validator = approach_handoff_validator
        self.holding_level_decider = holding_level_decider
        self.fallback_transition_validator = fallback_transition_validator
        self._progress = {}

        self.navigator = GraphNavigator(
            graph
        )

    def update(self, aircraft, dt=1.0):

        if aircraft.state == "TAXI":

            self._taxi(
                aircraft
            )

            return

        if not aircraft.route:

            return

        previous_position = getattr(aircraft, "_guidance_previous_position", None)
        previous_target = getattr(aircraft, "_guidance_previous_target", None)
        current_position = (float(aircraft.lat), float(aircraft.lon))
        aircraft._guidance_previous_position = current_position

        if getattr(aircraft, "_approach_ai_waiting", False):
            safety = (self.approach_handoff_validator(aircraft)
                      if self.approach_handoff_validator is not None else {"safe": False})
            if safety.get("safe") and not self._valid_approach_selection(aircraft, safety):
                safety = {**safety, "safe": False,
                          "reason": "Approach validator did not provide a committed entry reservation"}
            if safety.get("safe"):
                trigger = str(self.arrival_routes.get(
                    getattr(aircraft, "arrival_route_id", None), {}
                ).get("approach_trigger", "")).upper()
                trigger_index = next((index for index, name in enumerate(aircraft.route)
                                      if str(name).upper() == trigger), None)
                entry_index = safety.get("approach_entry_index")
                entry_node = safety.get("approach_entry_node")
                if (trigger_index is not None and isinstance(entry_index, int)
                        and isinstance(entry_node, str)):
                    aircraft.assigned_node = aircraft.route[trigger_index]
                    aircraft.route_index = trigger_index + 1
                    aircraft.approach_clearance = True
                    aircraft._approach_entry_index = entry_index
                    aircraft._approach_entry_node = entry_node
                    aircraft.phase = "FINAL"
                    aircraft.state = "APPROACH"
                    aircraft.approach_phase = "INTERCEPT"
                    aircraft.target_node = entry_node
                    aircraft.clearance.direct_node = entry_node
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(
                            aircraft, "APPROACH CLEARED", trigger,
                            f"AI selected safe entry {entry_node}; pending trigger handoff completed",
                        )
                        self.decision_event_callback(
                            aircraft, "ROUTE TRANSITION", trigger,
                            f"Arrival route transitioned to approach entry {entry_node}",
                        )
                    for name in ("_approach_ai_waiting", "_approach_clearance_waiting"):
                        if hasattr(aircraft, name):
                            delattr(aircraft, name)
                    self._record_progress(aircraft, dt)
                    return
            if safety.get("ai_pending"):
                if self._enter_pending_approach_hold(aircraft, safety, dt):
                    for name in ("_approach_ai_waiting", "_approach_clearance_waiting"):
                        if hasattr(aircraft, name):
                            delattr(aircraft, name)
                    return
                # Do not pin guidance to the approach trigger while the model
                # is pending. The normal trigger capture path below applies
                # the configured route-extension fallback (or a compatible
                # hold) and keeps the aircraft moving on an executable route.
                delattr(aircraft, "_approach_ai_waiting")
            # A completed WAIT/HOLD recommendation returns to the normal
            # trigger-resolution path below, where the selected safe action
            # is validated and executed.
            if hasattr(aircraft, "_approach_ai_waiting"):
                delattr(aircraft, "_approach_ai_waiting")

        # A denied temporary sequencing fix is a real waiting state. Recheck
        # it before capture handling; otherwise a zero-distance target would
        # be captured and denied on every tick without advancing the cursor.
        resumed_wait = False
        if (getattr(aircraft, "_approach_clearance_waiting", False)
                and getattr(aircraft, "_approach_extension_used", False)):
            validator = self.approach_handoff_validator
            safety = validator(aircraft, from_fallback=True) if validator is not None else {"safe": False}
            if not safety.get("safe", False):
                aircraft.approach_clearance = False
                aircraft.target_node = getattr(aircraft, "_approach_extension_fix", aircraft.target_node)
                aircraft.clearance.direct_node = aircraft.target_node
                self._record_progress(aircraft, dt, legitimate_wait=True)
                return
            wait_index = getattr(aircraft, "_approach_extension_index", None)
            if not (isinstance(wait_index, int) and 0 <= wait_index < len(aircraft.route)):
                # Corrupt temporary state has no deterministic safe recovery;
                # remain stopped at the configured fix and keep reassessing.
                if self.decision_event_callback is not None:
                    self.decision_event_callback(
                        aircraft, "NAVIGATION WAIT STATE INVALID", aircraft.target_node,
                        f"Missing valid temporary route index: {wait_index!r}; preserving clearance wait"
                    )
                self._record_progress(aircraft, dt, legitimate_wait=True)
                return
            aircraft.assigned_node = aircraft.route[wait_index]
            aircraft.route_index = wait_index + 1
            aircraft.target_node = (aircraft.route[aircraft.route_index]
                                    if aircraft.route_index < len(aircraft.route) else None)
            resumed_wait = True
            for name in ("_approach_clearance_waiting", "_approach_extension_used",
                         "_approach_extension_fix", "_approach_extension_index"):
                if hasattr(aircraft, name):
                    delattr(aircraft, name)
            if resumed_wait:
                if aircraft.target_node is not None:
                    aircraft.assign_heading(self.navigator.heading_to_node(aircraft, aircraft.target_node))
                    aircraft.clearance.direct_node = aircraft.target_node
                self._record_progress(aircraft, dt)
                return

        active_holding_state = (
            self.holding_manager.state_for(aircraft)
            if self.holding_manager is not None else None
        )
        if active_holding_state is not None:
            # HoldingManager keeps route_index on the last captured fix and
            # target_node on the next waypoint. Reassert that pairing before
            # steering so stale targets cannot send an aircraft back to its
            # holding fix while the cursor has already advanced.
            next_index = aircraft.route_index + 1
            if 0 <= aircraft.route_index < len(aircraft.route) and next_index < len(aircraft.route):
                aircraft.target_node = aircraft.route[next_index]
                aircraft.clearance.direct_node = aircraft.target_node

        if aircraft.phase == "FINAL":

            return

        strict_arrival_navigation = (
            aircraft.phase == "ARRIVAL"
            and not (self.holding_manager is not None
                     and self.holding_manager.has_active_holding_route(aircraft))
        )
        route_waypoint_navigation = strict_arrival_navigation or active_holding_state is not None
        if strict_arrival_navigation:
            if aircraft.route_index < 0 or aircraft.route_index > len(aircraft.route):
                return
            if aircraft.route_index == len(aircraft.route):
                route_definition = self.arrival_routes.get(getattr(aircraft, "arrival_route_id", None), {})
                trigger = str(route_definition.get("approach_trigger", "")).upper()
                if str(getattr(aircraft, "assigned_node", "")).upper() == trigger and trigger:
                    existing_entry = {
                        "safe": bool(getattr(aircraft, "approach_clearance", False)),
                        "approach_entry_index": getattr(aircraft, "_approach_entry_index", None),
                        "approach_entry_node": getattr(aircraft, "_approach_entry_node", None),
                    }
                    handoff_validator = getattr(self, "approach_handoff_validator", None)
                    if self._valid_approach_selection(aircraft, existing_entry):
                        safety = existing_entry
                    else:
                        safety = (handoff_validator(aircraft)
                                  if handoff_validator is not None
                                  else {"safe": False,
                                        "reason": "No approach handoff validator is configured"})
                    if safety.get("safe") and self._valid_approach_selection(aircraft, safety):
                        entry_node = safety["approach_entry_node"]
                        aircraft.approach_clearance = True
                        aircraft._approach_entry_index = safety["approach_entry_index"]
                        aircraft._approach_entry_node = entry_node
                        aircraft.phase = "FINAL"
                        aircraft.state = "APPROACH"
                        aircraft.approach_phase = "INTERCEPT"
                        aircraft.target_node = entry_node
                        aircraft.clearance.direct_node = entry_node
                        navigator = getattr(self, "navigator", None)
                        if navigator is not None:
                            aircraft.assign_heading(navigator.heading_to_node(aircraft, entry_node))
                        if self.decision_event_callback is not None:
                            self.decision_event_callback(
                                aircraft, "APPROACH HANDOFF", trigger,
                                f"Validated route-end allocation committed for {entry_node}",
                            )
                        self._record_progress(aircraft, dt)
                        return

                    graph = getattr(self, "graph", None)
                    graph_nodes = getattr(graph, "nodes", {})
                    get_graph_node = getattr(graph, "get_node", lambda _name: None)
                    fallback_nodes = route_end_fallback_candidates(
                        route_definition.get("approach_fallback_preferences", []),
                        aircraft.route, aircraft.route_index, trigger, graph_nodes,
                    )
                    trigger_node = get_graph_node(trigger)
                    reachable = []
                    if trigger_node is not None:
                        for fallback_name in fallback_nodes:
                            fallback_node = get_graph_node(fallback_name)
                            if fallback_node is None:
                                continue
                            values = (
                                trigger_node.get("lat", trigger_node.get("latitude")),
                                trigger_node.get("lon", trigger_node.get("longitude")),
                                fallback_node.get("lat", fallback_node.get("latitude")),
                                fallback_node.get("lon", fallback_node.get("longitude")),
                            )
                            if any(value is None for value in values):
                                continue
                            distance_nm = self._distance_nm(*map(float, values))
                            if distance_nm > 35.0:
                                continue
                            validator = getattr(self, "fallback_transition_validator", None)
                            validation = (validator(aircraft, fallback_name)
                                          if callable(validator) else {"safe": True})
                            if isinstance(validation, bool):
                                validation = {"safe": validation}
                            if validation.get("safe"):
                                reachable.append((float(validation.get("distance_nm", distance_nm)),
                                                  fallback_name))
                    if reachable and not getattr(aircraft, "_approach_extension_used", False):
                        _, fallback_name = min(reachable)
                        fallback_index = len(aircraft.route)
                        aircraft.route.extend([fallback_name, trigger])
                        aircraft._approach_extension_used = True
                        aircraft._approach_extension_fix = fallback_name
                        aircraft._approach_extension_index = fallback_index
                        aircraft.approach_clearance = False
                        aircraft.target_node = fallback_name
                        aircraft.clearance.direct_node = fallback_name
                        navigator = getattr(self, "navigator", None)
                        if navigator is not None:
                            aircraft.assign_heading(navigator.heading_to_node(aircraft, fallback_name))
                        if self.decision_event_callback is not None:
                            self.decision_event_callback(
                                aircraft, "APPROACH ROUTE-END FALLBACK", fallback_name,
                                f"No approach entry passed safety validation; configured fallback from {trigger}",
                            )
                        self._record_progress(aircraft, dt)
                        return
                    manager = getattr(self, "holding_manager", None)
                    if manager is not None and aircraft.route:
                        previous_cursor = aircraft.route_index
                        aircraft.route_index = len(aircraft.route) - 1
                        try:
                            options = manager.compatible_holding_options(aircraft, trigger)
                            for option in options:
                                result = manager.apply_holding_route(
                                    aircraft, option["holding_route_id"], circuits=1,
                                    required_delay_seconds=safety.get("required_delay_seconds"),
                                )
                                if result.get("success"):
                                    navigator = getattr(self, "navigator", None)
                                    if navigator is not None and aircraft.target_node:
                                        aircraft.assign_heading(
                                            navigator.heading_to_node(aircraft, aircraft.target_node)
                                        )
                                    self._record_progress(aircraft, dt)
                                    return
                        finally:
                            if not manager.has_active_holding_route(aircraft):
                                aircraft.route_index = previous_cursor
                    aircraft.approach_clearance = False
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(
                            aircraft, "APPROACH HANDOFF BLOCKED", trigger,
                            safety.get("reason", "No safe entry or reachable configured fallback"),
                        )
                return
            # Arrival steering is always to the indexed waypoint. Keeping the
            # index and target synchronized here prevents stale targets and
            # makes each frame independent of earlier guidance calculations.
            aircraft.target_node = aircraft.route[aircraft.route_index]
        elif aircraft.target_node is None:

            return

        # A route splice or recovery changes the objective. Do not use a
        # segment sampled while pursuing the previous target as passage
        # evidence for the new fix.
        if previous_target != aircraft.target_node:
            previous_position = None
        aircraft._guidance_previous_target = aircraft.target_node

        self.apply_route_constraints(aircraft)

        node = self.graph.get_node(
            aircraft.target_node
        )

        distance_nm = self._distance_nm(aircraft.lat, aircraft.lon, node["lat"], node["lon"])
        distance = (distance_nm if route_waypoint_navigation else self.navigator.distance(
            aircraft.lat, aircraft.lon, node["lat"], node["lon"]
        ))

        aircraft.current_distance = distance

        capture_distance = (self.ARRIVAL_WAYPOINT_CAPTURE_DISTANCE
                            if route_waypoint_navigation else self.WAYPOINT_CAPTURE_DISTANCE)
        if active_holding_state is not None:
            holding_names = {point["name"] for point in active_holding_state.get("holding_waypoints", [])}
            if (aircraft.target_node in holding_names
                    and str(aircraft.target_node).upper()
                    != str(active_holding_state.get("rejoin_waypoint", "")).upper()):
                turn_rate = math.radians(TURN_RATE_DEG_PER_SEC)
                speed = max(0.0, float(getattr(aircraft, "speed_kts", 0.0) or 0.0))
                capture_distance = max(
                    capture_distance,
                    speed / (3600.0 * turn_rate) + speed * max(0.0, float(dt or 0.0)) / 7200.0,
                )
        captured = (self._valid_waypoint_capture(
            aircraft, node, distance, capture_distance, previous_position
        ) if route_waypoint_navigation else distance <= capture_distance)
        if not captured and active_holding_state is not None:
            reason, details = self._waypoint_capture_rejection(
                aircraft, node, distance, capture_distance, previous_position
            )
            self.holding_manager.report_boundary_capture_rejection(
                aircraft, reason, **details
            )
        if captured:

            reached_node = aircraft.target_node
            fallback_fix = getattr(aircraft, "_approach_extension_fix", None)
            fallback_index = getattr(aircraft, "_approach_extension_index", None)
            is_delay_fallback = (
                strict_arrival_navigation
                and getattr(aircraft, "_approach_extension_used", False)
                and isinstance(fallback_index, int)
                and aircraft.route_index == fallback_index
                and str(reached_node).upper() == str(fallback_fix).upper()
            )
            if is_delay_fallback and self.approach_handoff_validator is not None:
                safety = self.approach_handoff_validator(aircraft, from_fallback=True)
                if safety.get("safe"):
                    # A delay fix is only a place to reassess sequencing. Keep
                    # flying the permanent route to its approach trigger; only
                    # that trigger capture may assign APP32/APP38.
                    for name in ("_approach_extension_used", "_approach_extension_fix",
                                 "_approach_extension_index"):
                        if hasattr(aircraft, name):
                            delattr(aircraft, name)
                else:
                    selected_hold = safety.get("holding_route_id")
                    if (safety.get("ai_action") == "HOLD" and selected_hold
                            and self.holding_manager is not None):
                        options = self.holding_manager.compatible_holding_options(aircraft, reached_node)
                        selected_option = next((option for option in options
                                                if str(option.get("holding_route_id")) == str(selected_hold)), None)
                        if selected_option is not None:
                            applied = self.holding_manager.apply_holding_route(
                                aircraft, selected_hold, circuits=1,
                                required_delay_seconds=safety.get("required_delay_seconds"),
                            )
                            if applied.get("success"):
                                for name in ("_approach_clearance_waiting", "_approach_ai_waiting",
                                             "_approach_extension_used", "_approach_extension_fix",
                                             "_approach_extension_index"):
                                    if hasattr(aircraft, name):
                                        delattr(aircraft, name)
                                if self.decision_event_callback is not None:
                                    self.decision_event_callback(
                                        aircraft, "AI HOLD ASSIGNED", reached_node,
                                        f"Hold {selected_hold} assigned after no approach entry passed safety validation",
                                    )
                                return
                        if self.decision_event_callback is not None:
                            self.decision_event_callback(
                                aircraft, "AI HOLD REJECTED", reached_node,
                                f"Selected hold {selected_hold} is no longer compatible or available; remaining at safe reassessment fix",
                            )
                    # The delay fix is the configured waiting point for this
                    # sequencing extension. Keep the route cursor on it so
                    # the aircraft is reassessed here as traffic changes,
                    # instead of flying another trigger/delay loop.
                    aircraft.approach_clearance = False
                    aircraft.assigned_node = reached_node
                    aircraft.target_node = reached_node
                    aircraft.clearance.direct_node = reached_node
                    aircraft._approach_clearance_waiting = True
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(
                            aircraft, "APPROACH CLEARANCE WITHHELD", reached_node,
                            safety.get("reason", "Approach handoff validator withheld clearance")
                        )
                    return

            route_definition = self.arrival_routes.get(
                getattr(aircraft, "arrival_route_id", None), {}
            )
            trigger = str(route_definition.get("approach_trigger", "")).upper()
            if strict_arrival_navigation:
                if self.decision_event_callback is not None:
                    self.decision_event_callback(
                        aircraft, "WAYPOINT CAPTURED", reached_node,
                        f"Captured at {distance_nm:.3f} NM"
                    )
            is_approach_trigger = (
                strict_arrival_navigation
                and trigger
                and str(reached_node).upper() == trigger
            )
            if is_approach_trigger and self.decision_event_callback is not None:
                self.decision_event_callback(
                    aircraft, "APPROACH_TRIGGER_REACHED", reached_node,
                    f"cursor={aircraft.route_index}; position=({aircraft.lat:.6f},{aircraft.lon:.6f}); altitude_ft={aircraft.altitude_ft}; speed_kts={aircraft.speed_kts}",
                )
            if is_approach_trigger:
                aircraft.approach_clearance = False
                safety = (self.approach_handoff_validator(aircraft)
                          if self.approach_handoff_validator is not None else {
                              "safe": False,
                              "reason": "No approach handoff validator is configured",
                          })
                if safety.get("safe") and not self._valid_approach_selection(aircraft, safety):
                    safety = {**safety, "safe": False,
                              "reason": "Approach validator did not provide a committed entry reservation"}
                if safety.get("ai_pending"):
                    if self._enter_pending_approach_hold(aircraft, safety, dt):
                        return
                    aircraft._approach_ai_waiting = True
                    aircraft._approach_clearance_waiting = True
                    # Continue through the same validated delay-route logic
                    # used for a completed WAIT decision. Returning here would
                    # leave the trigger as both current fix and guidance target
                    # until the asynchronous decision completed.
                speed_reduction = safety.get("speed_reduction_kts")
                if isinstance(speed_reduction, (int, float)):
                    aircraft.assign_speed(float(speed_reduction))
                    aircraft._sequence_speed_ceiling = float(speed_reduction)
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(
                            aircraft, "SPEED REDUCTION", reached_node,
                            safety.get("reason", "Predicted spacing requires a speed adjustment")
                        )
                if safety.get("safe") and hasattr(aircraft, "separation_status"):
                    del aircraft.separation_status
                if safety.get("safe"):
                    for name in ("_approach_extension_used", "_approach_extension_fix",
                                 "_approach_extension_index"):
                        if hasattr(aircraft, name):
                            delattr(aircraft, name)
                if not safety.get("safe", False):
                    aircraft.approach_clearance = False
                    aircraft.assigned_node = reached_node
                    options = (self.holding_manager.compatible_holding_options(aircraft, reached_node)
                               if self.holding_manager is not None else [])
                    selected_hold = safety.get("holding_route_id")
                    if selected_hold:
                        if not any(str(option.get("holding_route_id")) == str(selected_hold)
                                   for option in options) and self.decision_event_callback is not None:
                            self.decision_event_callback(
                                aircraft, "AI HOLD REJECTED", reached_node,
                                f"Selected hold {selected_hold} is no longer compatible or available; evaluating safe fallback holds",
                            )
                        options = sorted(options, key=lambda option: (
                            str(option.get("holding_route_id")) != str(selected_hold),
                            option.get("occupancy", 0), option.get("distance_in_route", 0),
                        ))
                    held = False
                    for option in options:
                        result = self.holding_manager.apply_holding_route(
                            aircraft, option["holding_route_id"], circuits=1,
                            required_delay_seconds=safety.get("required_delay_seconds"),
                        )
                        if result.get("success"):
                            held = True
                            if self.decision_event_callback is not None:
                                self.decision_event_callback(
                                    aircraft, "HOLD_ENTERED", reached_node,
                                    f"Downstream approach separation unavailable; holding on {option['holding_route_id']}"
                                )
                            aircraft.assign_heading(self.navigator.heading_to_node(aircraft, aircraft.target_node))
                            break
                    if not held:
                        # Keep the route cursor moving. Speed reduction was
                        # already evaluated by the handoff predictor; this leg
                        # provides path extension when a hold is unavailable.
                        aircraft.separation_status = "APPROACH DELAYED"
                        trigger_node = self.graph.get_node(trigger)
                        route_index = getattr(aircraft, "route_index", -1)
                        fallback_result = evaluate_fallback_candidates(
                            route_definition.get("approach_fallback_preferences", []),
                            aircraft.route, route_index, getattr(self.graph, "nodes", {}),
                        )
                        fallback_candidates = []
                        validator = getattr(self, "fallback_transition_validator", None)
                        for fallback_name in fallback_result["candidates"]:
                            validation = (validator(aircraft, fallback_name)
                                          if callable(validator) else {"safe": True})
                            if isinstance(validation, bool):
                                validation = {"safe": validation}
                            if validation.get("safe"):
                                fallback_point = self.graph.get_node(fallback_name)
                                if fallback_point is not None:
                                    distance_nm = self._distance_nm(
                                        float(aircraft.lat), float(aircraft.lon),
                                        float(fallback_point["lat"]), float(fallback_point["lon"]),
                                    )
                                    fallback_candidates.append((
                                        float(validation.get("distance_nm", distance_nm)), fallback_name
                                    ))
                        if not fallback_candidates:
                            aircraft._approach_fallback_result = fallback_result
                            if self.decision_event_callback is not None:
                                self.decision_event_callback(
                                    aircraft, "NO SAFE GLOBAL FALLBACK", reached_node,
                                    "MB398 and OLGUS are unavailable or their validated transitions conflict with traffic; retain safe holding/wait behavior",
                                )
                            return fallback_result
                        extension_fix = min(fallback_candidates)[1]
                        if not getattr(aircraft, "_approach_extension_used", False):
                            # Insert one reusable delay loop in the active route.
                            aircraft.route[aircraft.route_index + 1:aircraft.route_index + 1] = [
                                extension_fix, reached_node
                            ]
                            aircraft._approach_extension_used = True
                            aircraft._approach_extension_fix = extension_fix
                            aircraft._approach_extension_index = aircraft.route_index + 1
                            aircraft.clearance.direct_node = extension_fix
                            if self.decision_event_callback is not None:
                                self.decision_event_callback(
                                    aircraft, f"{extension_fix} SEQUENCING", extension_fix,
                                    f"Downstream approach spacing insufficient; temporary path extension via {extension_fix}"
                                )
                        else:
                            delay_index = getattr(aircraft, "_approach_extension_index", None)
                            if (isinstance(delay_index, int)
                                    and delay_index < len(aircraft.route)
                                    and str(aircraft.route[delay_index]).upper() == extension_fix):
                                # Reuse the same route-compatible fallback loop until cleared.
                                aircraft.route_index = delay_index - 1
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(
                            aircraft, "APPROACH DELAYED", reached_node,
                            safety.get("reason", "Downstream approach separation is unavailable")
                        )
                    if held:
                        return
            else:
                safety = {"safe": True}

            if strict_arrival_navigation:
                aircraft.assigned_node = reached_node
                aircraft.route_index += 1
                if self.decision_event_callback is not None:
                    self.decision_event_callback(
                        aircraft, "ROUTE_CURSOR_ADVANCED", reached_node,
                        f"cursor={aircraft.route_index}; next={aircraft.route[aircraft.route_index] if aircraft.route_index < len(aircraft.route) else None}",
                    )
                at_holding_boundary = (
                    active_holding_state is not None
                    and self.holding_manager.is_holding_circuit_boundary(aircraft)
                )
                if at_holding_boundary:
                    # Let HoldingManager atomically release or splice the next
                    # circuit before publishing a new target. In particular,
                    # do not briefly commit the permanent-route rejoin target.
                    aircraft.target_node = reached_node
                elif aircraft.route_index < len(aircraft.route):
                    aircraft.target_node = aircraft.route[aircraft.route_index]
                else:
                    aircraft.target_node = None
                if (is_approach_trigger and safety.get("safe", True)
                        and not (self.holding_manager is not None
                                 and self.holding_manager.has_active_holding_route(aircraft))):
                    entry_index = safety.get("approach_entry_index")
                    entry_node = safety.get("approach_entry_node")
                    if isinstance(entry_index, int) and isinstance(entry_node, str):
                        aircraft.approach_clearance = True
                        aircraft._approach_entry_index = entry_index
                        aircraft._approach_entry_node = entry_node
                        aircraft.phase = "FINAL"
                        aircraft.state = "APPROACH"
                        aircraft.approach_phase = "INTERCEPT"
                        aircraft.target_node = entry_node
                        aircraft.clearance.direct_node = entry_node
                        if self.decision_event_callback is not None:
                            self.decision_event_callback(
                                aircraft, "APPROACH CLEARED", reached_node,
                                "Configured entry reservation and clearance committed",
                                decision_source=safety.get("decision_source"),
                            )
                            self.decision_event_callback(
                                aircraft, "APPROACH_CLEARANCE_COMMITTED", entry_node,
                                f"Trigger={reached_node}; route transition validated and committed",
                                decision_source=safety.get("decision_source"),
                            )
            else:
                aircraft.assigned_node = reached_node
                aircraft.advance_route()

            if (
                self.holding_manager is not None
                and self.holding_manager.has_active_holding_route(aircraft)
                and self.holding_manager.is_holding_circuit_boundary(aircraft)
            ):
                if self.decision_event_callback is not None:
                    self.decision_event_callback(
                        aircraft, "HOLD_CIRCUIT_COMPLETED", aircraft.target_node,
                        f"Completed circuit at cursor={aircraft.route_index}; requesting one safety reassessment",
                    )
                    self.decision_event_callback(
                        aircraft, "HOLD_RELEASE_REQUESTED", aircraft.target_node,
                        "One genuine circuit completed; requesting AI decision and deterministic safety validation",
                    )
                release = self.holding_manager.complete_holding_circuit(
                    aircraft,
                    self.holding_release_validator,
                    self.holding_level_decider,
                )
                if release.get("released") and self.holding_release_callback is not None:
                    self.holding_release_callback(aircraft, release)
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(
                            aircraft, "HOLD_RELEASED", release.get("rejoin_waypoint"),
                            "Release passed safety validation and the permanent arrival route was restored",
                        )
                        self.decision_event_callback(
                            aircraft, "ROUTE_RESTORED", release.get("rejoin_waypoint"),
                            f"cursor={aircraft.route_index}; target={aircraft.target_node}; hold guidance released",
                        )
                elif (release.get("success") or release.get("release_attempted")) \
                        and self.decision_event_callback is not None:
                    safety = release.get("safety", {})
                    decision_source = safety.get("decision_source") or "SAFETY_REJECTION"
                    self.decision_event_callback(
                        aircraft,
                        "HOLD RELEASE FAILED" if release.get("release_attempted") else "HOLD EXTENDED",
                        getattr(aircraft, "holding_fix", None),
                        release.get("message", "Additional holding circuit scheduled"),
                        decision_source=decision_source,
                    )

            if aircraft.target_node is None:

                if (self.holding_manager is not None
                        and self.holding_manager.has_active_holding_route(aircraft)):
                    return

                is_departure = aircraft.phase == "DEPARTURE"
                if is_departure:
                    aircraft.phase = "FINAL"
                else:
                    route_definition = self.arrival_routes.get(
                        getattr(aircraft, "arrival_route_id", None), {}
                    )
                    trigger = str(route_definition.get("approach_trigger", "")).upper()
                    final_waypoint = str(aircraft.assigned_node or "").upper()
                    if not trigger or final_waypoint != trigger:
                        print(f"ARRIVAL ROUTE END WITHOUT APPROACH TRIGGER: "
                              f"{aircraft.callsign} final={final_waypoint} trigger={trigger}")
                        return
                    aircraft.phase = "FINAL"
                    aircraft.state = "APPROACH"
                    aircraft.approach_phase = "INTERCEPT"
                aircraft.pending_event = ""

                if not is_departure:
                    print(f"APPROACH HANDOFF: {aircraft.callsign} at {aircraft.assigned_node}")
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(aircraft, "APPROACH HANDOFF", aircraft.assigned_node,
                                                     "Approach trigger was captured")

                return

        if aircraft.target_node is None:

            return

        heading = self.navigator.heading_to_node(
            aircraft,
            aircraft.target_node
        )

        aircraft.assign_heading(
            heading
        )

        aircraft.clearance.direct_node = (
            aircraft.target_node
        )
        self._record_progress(aircraft, dt)

    def _record_progress(self, aircraft, dt=1.0, legitimate_wait=False):
        """Diagnose stale route targets without inventing motion or clearance."""
        if getattr(aircraft, "phase", "") == "FINAL":
            return
        target = getattr(aircraft, "target_node", None)
        route = getattr(aircraft, "route", None)
        if not target or not route:
            return
        key = id(aircraft)
        distance = getattr(aircraft, "current_distance", None)
        progress = getattr(self, "_progress", None)
        if progress is None:
            progress = self._progress = {}
        state = progress.get(key)
        signature = (getattr(aircraft, "route_index", None), target,
                     getattr(aircraft, "clearance", None).direct_node
                     if getattr(aircraft, "clearance", None) is not None else None)
        if state is None or state["signature"] != signature:
            progress[key] = {"signature": signature, "elapsed": 0.0,
                             "last_distance": distance}
            return
        if distance is not None and state["last_distance"] is not None and distance < state["last_distance"] - 0.01:
            state["elapsed"] = 0.0
        else:
            state["elapsed"] += max(0.0, float(dt or 0.0))
        state["last_distance"] = distance
        if legitimate_wait or state["elapsed"] < self.PROGRESS_STALL_SECONDS:
            return
        last_diagnostic = state.get("last_diagnostic_elapsed", -self.PROGRESS_STALL_SECONDS)
        if state["elapsed"] - last_diagnostic >= self.PROGRESS_STALL_SECONDS:
            state["last_diagnostic_elapsed"] = state["elapsed"]
            logging.getLogger(__name__).warning(
                "Arrival route progression stalled",
                extra={
                    "event": "ROUTE_PROGRESSION_BLOCKED",
                    "aircraft_callsign": getattr(aircraft, "callsign", None),
                    "route_id": getattr(aircraft, "arrival_route_id", None),
                    "route_cursor": getattr(aircraft, "route_index", None),
                    "assigned_waypoint": getattr(aircraft, "assigned_node", None),
                    "target_waypoint": target,
                    "clearance_direct_node": signature[2],
                    "distance_nm": distance,
                    "heading_deg": getattr(aircraft, "heading_deg", None),
                    "target_heading_deg": getattr(aircraft, "target_heading_deg", None),
                    "guidance_mode": getattr(aircraft, "phase", None),
                    "hold_active": bool(self.holding_manager is not None
                                        and self.holding_manager.has_active_holding_route(aircraft)),
                    "reason": "target and route cursor remained unchanged without decreasing range",
                },
            )
        # Repair only an impossible self-target. Holds and clearance waits
        # have explicit reassessment paths and must remain untouched.
        if (target == getattr(aircraft, "assigned_node", None)
                and not (self.holding_manager is not None
                         and self.holding_manager.has_active_holding_route(aircraft))
                and not getattr(aircraft, "_approach_clearance_waiting", False)):
            index = getattr(aircraft, "route_index", -1)
            if isinstance(index, int) and index + 1 < len(route):
                next_target = route[index + 1]
                aircraft.target_node = next_target
                aircraft.clearance.direct_node = next_target
                state["signature"] = (index, next_target, next_target)
                state["elapsed"] = 0.0
                if self.decision_event_callback is not None:
                    reason = (
                        "Active target matched assigned fix for the stall threshold; "
                        f"cursor={index} assigned={getattr(aircraft, 'assigned_node', None)} "
                        f"target={target} clearance={getattr(aircraft.clearance, 'direct_node', None)} "
                        f"approach_clearance={getattr(aircraft, 'approach_clearance', False)} "
                        f"reservation={getattr(aircraft, '_approach_entry_node', None)} "
                        f"hold={getattr(aircraft, 'holding_route_id', None)} "
                        f"position=({getattr(aircraft, 'lat', None)},{getattr(aircraft, 'lon', None)}) "
                        f"distance={getattr(aircraft, 'current_distance', None)}"
                    )
                    self.decision_event_callback(
                        aircraft, "NAVIGATION STALL RECOVERED", next_target, reason
                    )

    def _enter_pending_approach_hold(self, aircraft, safety, dt):
        """Keep an arrival flying a configured circuit while no entry is safe."""
        manager = self.holding_manager
        selected_id = safety.get("holding_route_id")
        if manager is None or not selected_id:
            return False
        options = manager.compatible_holding_options(aircraft, aircraft.target_node)
        selected = next((option for option in options
                         if str(option.get("holding_route_id")) == str(selected_id)), None)
        if selected is None:
            return False
        result = manager.apply_holding_route(aircraft, selected_id, circuits=1)
        if not result.get("success"):
            return False
        if self.decision_event_callback is not None:
            self.decision_event_callback(
                    aircraft, "HOLD_ENTERED", aircraft.target_node,
                f"No approach entry passed safety validation; one circuit assigned while AI reassessment is pending ({selected_id})",
                decision_source="DETERMINISTIC_SAFETY_FALLBACK",
            )
        return True

    def _valid_approach_selection(self, aircraft, safety):
        """Require a selected entry and, in the engine, its live reservation."""
        entry_index = safety.get("approach_entry_index")
        entry_node = safety.get("approach_entry_node")
        if (not safety.get("safe") or not isinstance(entry_index, int)
                or isinstance(entry_index, bool) or entry_index < 0
                or not isinstance(entry_node, str) or not entry_node.strip()):
            return False
        owner = getattr(getattr(self, "approach_handoff_validator", None), "__self__", None)
        reservations = getattr(owner, "approach_reservations", None)
        if isinstance(reservations, dict) and reservations.get(entry_node.upper()) is not aircraft:
            return False
        return True

    @classmethod
    def _distance_to_segment_nm(cls, latitude, longitude, start, end):
        """Distance from a fix to a sampled ground track segment in NM."""
        scale_x = 60.0 * math.cos(math.radians(latitude))
        scale_y = 60.0

        def project(position):
            return ((float(position[1]) - longitude) * scale_x,
                    (float(position[0]) - latitude) * scale_y)

        start_x, start_y = project(start)
        end_x, end_y = project(end)
        segment_x, segment_y = end_x - start_x, end_y - start_y
        length_squared = segment_x * segment_x + segment_y * segment_y
        if length_squared <= 1e-12:
            return math.hypot(start_x, start_y)
        fraction = max(0.0, min(1.0,
            -(start_x * segment_x + start_y * segment_y) / length_squared))
        return math.hypot(start_x + fraction * segment_x,
                          start_y + fraction * segment_y)

    def _valid_waypoint_capture(self, aircraft, node, distance, capture_distance,
                                previous_position):
        """Require approach or measured passage before advancing a route fix."""
        if distance > capture_distance:
            if previous_position is None:
                return False
            return self._distance_to_segment_nm(
                node["lat"], node["lon"], previous_position,
                (float(aircraft.lat), float(aircraft.lon))) <= capture_distance

        if previous_position is None:
            return True

        previous_distance = self._distance_nm(
            previous_position[0], previous_position[1], node["lat"], node["lon"])
        # A fix that is already behind the aircraft is captured only when the
        # sampled path proves that the aircraft passed through its capture tube.
        if distance > previous_distance + 0.01:
            return self._distance_to_segment_nm(
                node["lat"], node["lon"], previous_position,
                (float(aircraft.lat), float(aircraft.lon))) <= capture_distance
        return True

    def _waypoint_capture_rejection(self, aircraft, node, distance, capture_distance,
                                    previous_position):
        """Describe why the route capture predicate rejected this sample."""
        details = {
            "distance_nm": round(float(distance), 4),
            "capture_radius_nm": round(float(capture_distance), 4),
        }
        if distance > capture_distance:
            if previous_position is None:
                return "outside_capture_radius_without_previous_sample", details
            segment_distance = self._distance_to_segment_nm(
                node["lat"], node["lon"], previous_position,
                (float(aircraft.lat), float(aircraft.lon)),
            )
            details["sampled_segment_distance_nm"] = round(segment_distance, 4)
            return "sampled_path_outside_capture_tube", details

        if previous_position is not None:
            previous_distance = self._distance_nm(
                previous_position[0], previous_position[1], node["lat"], node["lon"]
            )
            details["previous_distance_nm"] = round(previous_distance, 4)
            if distance > previous_distance + 0.01:
                segment_distance = self._distance_to_segment_nm(
                    node["lat"], node["lon"], previous_position,
                    (float(aircraft.lat), float(aircraft.lon)),
                )
                details["sampled_segment_distance_nm"] = round(segment_distance, 4)
                return "receding_from_fix_without_capture_tube_passage", details
        return "capture_validation_rejected", details

    def apply_route_constraints(self, aircraft):
        if not aircraft.route or aircraft.target_node is None or aircraft.phase == "FINAL":
            return
        active_holding = (self.holding_manager is not None
                          and self.holding_manager.has_active_holding_route(aircraft))
        if not active_holding:
            if aircraft.phase == "ARRIVAL":
                scheduled_speed = arrival_schedule_groundspeed(
                    aircraft.aircraft_type, getattr(aircraft, "altitude_ft", 0)
                )
                sequence_ceiling = getattr(aircraft, "_sequence_speed_ceiling", None)
                if isinstance(sequence_ceiling, (int, float)):
                    scheduled_speed = min(scheduled_speed, float(sequence_ceiling))
                aircraft.assign_speed(scheduled_speed)
            route_id = str(getattr(aircraft, "arrival_route_id", ""))
            waypoint_name = str(aircraft.target_node).upper()
            route_profiles = self.arrival_profiles.get(route_id, {})
            if not isinstance(route_profiles, dict):
                return
            point_profile = route_profiles.get(waypoint_name, {})
            if not isinstance(point_profile, dict):
                aircraft.target_vertical_speed_fpm = None
                return

            altitude_constraint = point_profile.get("altitude")
            if isinstance(altitude_constraint, dict):
                altitude = altitude_constraint.get("feet")
                constraint_type = altitude_constraint.get("type", "AT")
            elif isinstance(altitude_constraint, (int, float)) and not isinstance(altitude_constraint, bool):
                altitude = altitude_constraint
                constraint_type = "AT"
            else:
                # Non-published STAR profile altitudes guide descent between
                # mandatory constraints. Without this fallback a waypoint
                # with no published altitude leaves the previous target active.
                altitude = point_profile.get("profile_altitude_ft")
                constraint_type = "AT"
            speed_constraint = point_profile.get("speed")
            speed = speed_constraint.get("knots") if isinstance(speed_constraint, dict) else speed_constraint
            vertical_profile = point_profile.get("vertical_profile") or {}
            descent_rate = vertical_profile.get("descent_ft_per_nm") if isinstance(vertical_profile, dict) else None

            if (getattr(aircraft, "separation_status", None) != "CRITICAL CONFLICT"
                    and isinstance(altitude, (int, float)) and not isinstance(altitude, bool) and altitude > 0):
                current_altitude = float(getattr(aircraft, "altitude_ft", 0))
                if constraint_type == "AT_OR_BELOW":
                    commanded_altitude = min(current_altitude, float(altitude))
                elif constraint_type == "AT_OR_ABOVE":
                    commanded_altitude = max(current_altitude, float(altitude))
                else:
                    commanded_altitude = float(altitude)
                aircraft.assign_altitude(commanded_altitude)
                try:
                    fix = self.graph.get_node(aircraft.target_node)
                    distance_nm = self._distance_nm(
                        aircraft.lat, aircraft.lon, fix["lat"], fix["lon"]
                    )
                except (KeyError, TypeError, ValueError):
                    distance_nm = 0.0
                target_speed = float(getattr(aircraft, "target_speed_kts", 0) or
                                     getattr(aircraft, "speed_kts", 0) or 0)
                altitude_delta = commanded_altitude - current_altitude
                required_rate = (altitude_delta * max(target_speed, 1.0)
                                 / max(distance_nm * 60.0, 1.0))
                aircraft.target_vertical_speed_fpm = max(-1500.0, min(1500.0, required_rate))
            if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0:
                speed = indicated_to_true_airspeed(speed, getattr(aircraft, "altitude_ft", 0))
                if isinstance(speed_constraint, dict) and speed_constraint.get("type") == "MAX":
                    current_target = getattr(aircraft, "target_speed_kts", None)
                    if current_target is None:
                        current_target = aircraft.speed_kts
                    if current_target > speed:
                        aircraft.assign_speed(speed)
                else:
                    aircraft.assign_speed(speed)
            if getattr(aircraft, "separation_status", None) == "CRITICAL CONFLICT":
                aircraft.target_vertical_speed_fpm = None

    def _taxi(
        self,
        aircraft
    ):

        if aircraft.target_node is None:

            self._select_taxi_target(
                aircraft
            )

            if aircraft.target_node is None:

                aircraft.assign_speed(
                    0
                )

                return

        node = self.graph.get_node(
            aircraft.target_node
        )

        distance = self.navigator.distance(
            aircraft.lat,
            aircraft.lon,
            node["lat"],
            node["lon"]
        )

        aircraft.current_distance = distance

        if distance <= self.WAYPOINT_CAPTURE_DISTANCE:

            previous_node = aircraft.assigned_node

            aircraft.lat = node["lat"]
            aircraft.lon = node["lon"]

            aircraft.assigned_node = (
                aircraft.target_node
            )

            aircraft.previous_taxi_node = (
                previous_node
            )

            print(
                f"TAXI NODE REACHED: "
                f"{aircraft.callsign} -> "
                f"{aircraft.assigned_node}"
            )

            aircraft.target_node = None

            self._select_taxi_target(
                aircraft
            )

            if aircraft.target_node is None:

                aircraft.assign_speed(
                    0
                )

                return

        heading = self.navigator.heading_to_node(
            aircraft,
            aircraft.target_node
        )

        aircraft.assign_heading(
            heading
        )

        aircraft.assign_speed(
            self.TAXI_SPEED
        )

        aircraft.clearance.direct_node = (
            aircraft.target_node
        )

    def _select_taxi_target(
        self,
        aircraft
    ):

        current_node = aircraft.assigned_node

        if current_node is None:

            current_node = self.navigator.nearest_node(
                aircraft
            )

            aircraft.assigned_node = current_node

        if current_node is None:

            aircraft.target_node = None

            return

        preferred = self.TAXI_PREFERRED_CONNECTIONS.get(
            current_node,
            []
        )

        previous_node = getattr(
            aircraft,
            "previous_taxi_node",
            None
        )

        for node_id in preferred:

            if (
                node_id in self.graph.nodes
                and node_id != previous_node
            ):

                aircraft.target_node = node_id

                print(
                    f"TAXI TARGET: "
                    f"{aircraft.callsign} -> "
                    f"{node_id}"
                )

                return

        neighbors = self.graph.neighbors(
            current_node
        )

        available = [
            node_id
            for node_id in neighbors
            if node_id != previous_node
        ]

        if available:

            aircraft.target_node = available[0]

            print(
                f"TAXI TARGET: "
                f"{aircraft.callsign} -> "
                f"{aircraft.target_node}"
            )

        else:

            aircraft.target_node = None
