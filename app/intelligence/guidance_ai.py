import math

from app.intelligence.graph_navigator import GraphNavigator
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
    ):

        self.graph = graph
        self.holding_manager = holding_manager
        self.arrival_routes = arrival_routes or {}
        self.holding_release_validator = holding_release_validator
        self.holding_release_callback = holding_release_callback
        self.arrival_profiles = arrival_profiles or {}
        self.decision_event_callback = decision_event_callback
        self.approach_handoff_validator = approach_handoff_validator
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
                    aircraft.phase = "FINAL"
                    aircraft.state = "APPROACH"
                    aircraft.approach_phase = "INTERCEPT"
                    print(f"APPROACH HANDOFF: {aircraft.callsign} at {aircraft.assigned_node}")
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(aircraft, "APPROACH HANDOFF", aircraft.assigned_node,
                                                     "Approach trigger was captured")
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
            if is_approach_trigger and self.approach_handoff_validator is not None:
                safety = self.approach_handoff_validator(aircraft)
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
                                    aircraft, "HOLD ASSIGNED", reached_node,
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
                        extension_fix = min(
                            ("DOGAP", "MB398"),
                            key=lambda fix: self._distance_nm(
                                trigger_node["lat"], trigger_node["lon"],
                                self.graph.get_node(fix)["lat"], self.graph.get_node(fix)["lon"],
                            ),
                        )
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
                                # Reuse the same DOGAP/entry pair until cleared.
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
            if (is_approach_trigger and safety.get("safe", True)
                    and not (self.holding_manager is not None
                             and self.holding_manager.has_active_holding_route(aircraft))):
                aircraft.approach_clearance = True
                if self.decision_event_callback is not None:
                    self.decision_event_callback(aircraft, "APPROACH CLEARED", reached_node,
                                                 "Configured approach trigger captured")

            if strict_arrival_navigation:
                aircraft.assigned_node = reached_node
                aircraft.route_index += 1
                if aircraft.route_index < len(aircraft.route):
                    aircraft.target_node = aircraft.route[aircraft.route_index]
                else:
                    aircraft.target_node = None
            else:
                aircraft.assigned_node = reached_node
                aircraft.advance_route()

            if (
                self.holding_manager is not None
                and self.holding_manager.has_active_holding_route(aircraft)
                and self.holding_manager.is_holding_circuit_boundary(aircraft)
            ):
                release = self.holding_manager.complete_holding_circuit(
                    aircraft,
                    self.holding_release_validator,
                )
                if release.get("released") and self.holding_release_callback is not None:
                    self.holding_release_callback(aircraft, release)
                elif release.get("success") and self.decision_event_callback is not None:
                    self.decision_event_callback(
                        aircraft, "HOLD EXTENDED", getattr(aircraft, "holding_fix", None),
                        release.get("message", "Additional holding circuit scheduled"),
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

    def apply_route_constraints(self, aircraft):
        if not aircraft.route or aircraft.target_node is None or aircraft.phase == "FINAL":
            return
        if not (
            self.holding_manager is not None
            and self.holding_manager.has_active_holding_route(aircraft)
        ):
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
            altitude = altitude_constraint.get("feet") if isinstance(altitude_constraint, dict) else altitude_constraint
            constraint_type = altitude_constraint.get("type", "AT") if isinstance(altitude_constraint, dict) else "AT"
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
                if isinstance(speed_constraint, dict) and speed_constraint.get("type") == "MAX":
                    current_target = getattr(aircraft, "target_speed_kts", None)
                    if aircraft.speed_kts > speed and (current_target is None or current_target > speed):
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
