from app.intelligence.graph_navigator import GraphNavigator


class GuidanceAI:

    WAYPOINT_CAPTURE_DISTANCE = 0.002
    # Arrival aircraft move farther than the taxi capture radius each update.
    # Physical capture radius in nautical miles (about 600 ft).
    ARRIVAL_WAYPOINT_CAPTURE_DISTANCE = 0.1
    TAXI_SPEED = 15

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

        self.navigator = GraphNavigator(
            graph
        )

    def update(
        self,
        aircraft
    ):

        if aircraft.state == "TAXI":

            self._taxi(
                aircraft
            )

            return

        if not aircraft.route:

            return

        if aircraft.phase == "FINAL":

            return

        strict_arrival_navigation = (
            aircraft.phase == "ARRIVAL"
            and not (self.holding_manager is not None
                     and self.holding_manager.has_active_holding_route(aircraft))
        )
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

        self.apply_route_constraints(aircraft)

        node = self.graph.get_node(
            aircraft.target_node
        )

        distance_nm = self._distance_nm(aircraft.lat, aircraft.lon, node["lat"], node["lon"])
        distance = (distance_nm if strict_arrival_navigation else self.navigator.distance(
            aircraft.lat, aircraft.lon, node["lat"], node["lon"]
        ))

        aircraft.current_distance = distance

        capture_distance = self.ARRIVAL_WAYPOINT_CAPTURE_DISTANCE if strict_arrival_navigation else self.WAYPOINT_CAPTURE_DISTANCE
        if distance <= capture_distance:

            reached_node = aircraft.target_node
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
                if safety.get("safe") and hasattr(aircraft, "separation_status"):
                    del aircraft.separation_status
                if not safety.get("safe", False):
                    aircraft.approach_clearance = False
                    aircraft.assigned_node = reached_node
                    options = (self.holding_manager.compatible_holding_options(aircraft, reached_node)
                               if self.holding_manager is not None else [])
                    held = False
                    for option in options:
                        result = self.holding_manager.apply_holding_route(
                            aircraft, option["holding_route_id"], circuits=1
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
                        aircraft.target_node = reached_node
                        aircraft.assign_speed(min(float(getattr(aircraft, "target_speed_kts", 140) or 140), 140.0))
                        aircraft.separation_status = "APPROACH DELAYED"
                    if self.decision_event_callback is not None:
                        self.decision_event_callback(
                            aircraft, "APPROACH DELAYED", reached_node,
                            safety.get("reason", "Downstream approach separation is unavailable")
                        )
                    return
            if (is_approach_trigger
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
                if constraint_type == "AT_OR_BELOW":
                    if aircraft.altitude_ft > altitude:
                        aircraft.assign_altitude(altitude)
                elif constraint_type == "AT_OR_ABOVE":
                    if aircraft.altitude_ft < altitude:
                        aircraft.assign_altitude(altitude)
                else:
                    aircraft.assign_altitude(altitude)
            if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0:
                if isinstance(speed_constraint, dict) and speed_constraint.get("type") == "MAX":
                    current_target = getattr(aircraft, "target_speed_kts", None)
                    if aircraft.speed_kts > speed and (current_target is None or current_target > speed):
                        aircraft.assign_speed(speed)
                else:
                    aircraft.assign_speed(speed)
            if (getattr(aircraft, "separation_status", None) != "CRITICAL CONFLICT"
                    and isinstance(descent_rate, (int, float)) and descent_rate > 0):
                aircraft.target_vertical_speed_fpm = -min(
                    1500.0, float(descent_rate) * max(float(aircraft.speed_kts), 1.0) / 60.0
                )
            else:
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
