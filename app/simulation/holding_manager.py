import math
import time


class HoldingManager:
    ARC_SEGMENTS = 12

    def __init__(self, holding_routes, graph=None):
        if isinstance(holding_routes, dict):
            holding_routes = holding_routes.get("holding_routes", [])
        self.holding_routes = holding_routes if isinstance(holding_routes, list) else []
        self.graph = graph
        self.route_continuation_validator = None
        self._active_holds = {}
        self._temporary_node_references = {}

    @staticmethod
    def _result(success, message, **values):
        return {"success": success, "message": message, **values}

    @staticmethod
    def _flight_level(value):
        if not isinstance(value, str):
            return None
        text = value.strip().upper()
        return int(text[2:]) * 100 if text.startswith("FL") and text[2:].isdigit() else None

    def _holding_route_by_id(self, holding_route_id):
        return next((route for route in self.holding_routes
                     if isinstance(route, dict) and str(route.get("id")) == str(holding_route_id)), None)

    def holding_occupancy(self, holding_route_id):
        return sum(1 for state in self._active_holds.values()
                   if str(state.get("holding_route_id")) == str(holding_route_id))

    def has_capacity(self, holding_route_id):
        route = self._holding_route_by_id(holding_route_id)
        if route is None:
            return False
        capacity = route.get("capacity", 1)
        try:
            capacity = max(1, int(capacity))
        except (TypeError, ValueError):
            capacity = 1
        return self.holding_occupancy(holding_route_id) < capacity

    @staticmethod
    def _fix_point(route):
        fix = str(route.get("fix", route.get("trigger_waypoint", ""))).upper()
        for point in route.get("waypoints", []):
            if isinstance(point, dict) and str(point.get("name", "")).upper() == fix:
                try:
                    lat, lon = float(point["latitude"]), float(point["longitude"])
                except (KeyError, TypeError, ValueError):
                    return None
                if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                    return point.get("name"), lat, lon
        return None

    def racetrack_waypoints(self, route):
        """Create a closed, sampled racetrack from configured course and dimensions."""
        fix = self._fix_point(route)
        try:
            course = float(route["inbound_course_true"])
            leg = float(route["leg_length_nm"])
            radius = float(route["turn_radius_nm"])
            direction = str(route["turn_direction"]).upper()
        except (KeyError, TypeError, ValueError):
            return []
        if (fix is None or not math.isfinite(course) or not math.isfinite(leg)
                or not math.isfinite(radius) or leg <= 0 or radius <= 0
                or direction not in {"L", "R"}):
            return []

        fix_name, lat0, lon0 = fix
        # The configured fix is the end of the inbound leg. Begin at the fix,
        # turn onto the outbound leg, then complete the outbound and inbound legs.
        # Coordinates use east as +x and north as +y, so positive rotation is left.
        angular_turn_sign = 1.0 if direction == "L" else -1.0
        inbound = math.radians(course % 360.0)
        ux, uy = math.sin(inbound), math.cos(inbound)
        lx, ly = -uy * angular_turn_sign, ux * angular_turn_sign

        def coordinate(x_nm, y_nm):
            lat = lat0 + y_nm / 60.0
            cosine = math.cos(math.radians(lat0))
            lon = lon0 + x_nm / max(60.0 * cosine, 1e-6)
            return lat, lon

        points_xy = [(0.0, 0.0)]
        c1x, c1y = lx * radius, ly * radius
        r0x, r0y = -lx * radius, -ly * radius
        for step in range(1, self.ARC_SEGMENTS + 1):
            angle = angular_turn_sign * math.pi * step / self.ARC_SEGMENTS
            points_xy.append((c1x + r0x * math.cos(angle) - r0y * math.sin(angle),
                              c1y + r0x * math.sin(angle) + r0y * math.cos(angle)))

        outbound_x, outbound_y = -ux, -uy
        outbound_end_x = points_xy[-1][0] + outbound_x * leg
        outbound_end_y = points_xy[-1][1] + outbound_y * leg
        points_xy.append((outbound_end_x, outbound_end_y))
        c2x, c2y = outbound_end_x - lx * radius, outbound_end_y - ly * radius
        r0x, r0y = lx * radius, ly * radius
        for step in range(1, self.ARC_SEGMENTS + 1):
            angle = angular_turn_sign * math.pi * step / self.ARC_SEGMENTS
            points_xy.append((c2x + r0x * math.cos(angle) - r0y * math.sin(angle),
                              c2y + r0x * math.sin(angle) + r0y * math.cos(angle)))
        points_xy.append((0.0, 0.0))

        route_id = str(route.get("id", "HOLD")).replace(" ", "_")
        result = []
        for index, (x_nm, y_nm) in enumerate(points_xy):
            lat, lon = coordinate(x_nm, y_nm)
            result.append({
                "name": fix_name if index in (0, len(points_xy) - 1) else f"{route_id}_P{index:02d}",
                "latitude": round(lat, 7),
                "longitude": round(lon, 7),
            })
        return result

    @staticmethod
    def _distance_nm(first, second):
        lat1, lat2 = math.radians(first["latitude"]), math.radians(second["latitude"])
        dlat = lat2 - lat1
        dlon = math.radians(second["longitude"] - first["longitude"])
        value = (math.sin(dlat / 2.0) ** 2
                 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2)
        return 3440.065 * 2.0 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1.0 - value)))

    def _valid_racetrack_geometry(self, route, points):
        """Validate configured dimensions; sampled-leg tolerance is 0.15 NM."""
        fix = self._fix_point(route)
        if fix is None or len(points) != self.ARC_SEGMENTS * 2 + 3:
            return False
        try:
            leg = float(route["leg_length_nm"])
            course = float(route["inbound_course_true"]) % 360.0
            radius = float(route["turn_radius_nm"])
        except (KeyError, TypeError, ValueError):
            return False
        if not all(math.isfinite(value) and value > 0 for value in (leg, radius)) or not math.isfinite(course):
            return False
        outbound_start = self.ARC_SEGMENTS
        outbound_end = outbound_start + 1
        inbound_start = self.ARC_SEGMENTS * 2 + 1
        if (self._distance_nm(points[0], points[-1]) > 0.05
                or abs(self._distance_nm(points[outbound_start], points[outbound_end]) - leg) > 0.15
                or abs(self._distance_nm(points[inbound_start], points[inbound_start + 1]) - leg) > 0.15):
            return False
        inbound = self._bearing(points[inbound_start], points[inbound_start + 1])
        course_error = abs((inbound - course + 180.0) % 360.0 - 180.0)
        outbound = self._bearing(points[outbound_start], points[outbound_end])
        outbound_error = abs((outbound - (course + 180.0) + 180.0) % 360.0 - 180.0)
        first_arc_heading = self._bearing(points[0], points[1])
        first_turn = (first_arc_heading - course + 180.0) % 360.0 - 180.0
        second_arc_heading = self._bearing(points[outbound_end], points[outbound_end + 1])
        second_turn = (second_arc_heading - outbound + 180.0) % 360.0 - 180.0
        turn_direction = str(route.get("turn_direction", "")).upper()
        turn_matches = ((first_turn < 0 and second_turn < 0) if turn_direction == "L"
                        else (first_turn > 0 and second_turn > 0))
        expected_arc_chord = 2.0 * radius * math.sin(math.pi / (2.0 * self.ARC_SEGMENTS))
        radius_matches = all(
            abs(self._distance_nm(points[first], points[first + 1]) - expected_arc_chord) <= 0.1
            for first in (0, outbound_end)
        )
        return (course_error <= 1.0 and outbound_error <= 1.0
                and turn_matches and radius_matches)

    def _selected_holding_geometry(self, holding, aircraft, required_delay_seconds):
        """Choose short only when its generated one-circuit time covers the estimate."""
        standard_route = dict(holding)
        standard_route["leg_length_nm"] = holding.get("leg_length_nm")
        standard_points = self.racetrack_waypoints(standard_route)
        if not self._valid_racetrack_geometry(standard_route, standard_points):
            return []

        adaptive = holding.get("adaptive_leg_length_nm")
        if not isinstance(adaptive, dict) or adaptive.get("enabled") is not True:
            return standard_points
        try:
            standard_leg = float(holding["leg_length_nm"])
            configured_standard = float(adaptive.get("standard_nm", standard_leg))
            short_leg = float(adaptive["short_nm"])
        except (KeyError, TypeError, ValueError):
            return standard_points
        if (not math.isfinite(short_leg) or short_leg <= 0 or short_leg >= standard_leg
                or not math.isfinite(configured_standard)
                or abs(configured_standard - standard_leg) > 1e-6):
            return standard_points

        try:
            delay = float(required_delay_seconds)
        except (TypeError, ValueError):
            return standard_points
        if not math.isfinite(delay) or delay < 0:
            return standard_points

        short_route = dict(holding)
        short_route["leg_length_nm"] = short_leg
        short_points = self.racetrack_waypoints(short_route)
        if not self._valid_racetrack_geometry(short_route, short_points):
            return standard_points
        speed = min(float(getattr(aircraft, "speed_kts", holding.get("max_speed_kt", 230)) or 0),
                    float(holding.get("max_speed_kt", 230)))
        if not math.isfinite(speed) or speed <= 0:
            return standard_points
        short_circuit_nm = sum(self._distance_nm(a, b) for a, b in zip(short_points, short_points[1:]))
        if short_circuit_nm * 3600.0 / speed >= delay:
            return short_points
        return standard_points

    @staticmethod
    def _bearing(start, end):
        lat1, lat2 = math.radians(start["latitude"]), math.radians(end["latitude"])
        delta_lon = math.radians(end["longitude"] - start["longitude"])
        y = math.sin(delta_lon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    def _entry_transition(self, aircraft, route, rejoin_index, holding):
        route_id = str(getattr(aircraft, "arrival_route_id", ""))
        route_directions = holding.get("entry_turn_directions", {})
        direction = str(route_directions.get(route_id, holding.get("entry_turn_direction", ""))).upper()
        if direction not in {"L", "R"} or rejoin_index <= 0:
            return None
        graph_nodes = getattr(self.graph, "nodes", {})
        previous = graph_nodes.get(route[rejoin_index - 1]) if isinstance(graph_nodes, dict) else None
        trigger = graph_nodes.get(route[rejoin_index]) if isinstance(graph_nodes, dict) else None
        if not isinstance(previous, dict) or not isinstance(trigger, dict):
            return None
        try:
            inbound = self._bearing(
                {"latitude": float(previous["lat"]), "longitude": float(previous["lon"])},
                {"latitude": float(trigger["lat"]), "longitude": float(trigger["lon"])},
            )
            radius = float(holding["turn_radius_nm"])
            latitude, longitude = float(trigger["lat"]), float(trigger["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(radius) or radius <= 0:
            return None

        bearing = math.radians((inbound + (-90.0 if direction == "L" else 90.0)) % 360.0)
        angular_distance = radius / 3440.065
        lat1 = math.radians(latitude)
        lon1 = math.radians(longitude)
        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular_distance)
            + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
            math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
        )
        fix_name = str(holding.get("trigger_waypoint", "HOLD"))
        return {
            "name": f"{fix_name}_{route_id}_ENTRY_{direction}",
            "latitude": math.degrees(lat2),
            "longitude": (math.degrees(lon2) + 540.0) % 360.0 - 180.0,
        }

    def configured_holding_points(self):
        points = []
        for route in self.holding_routes:
            if not isinstance(route, dict):
                continue
            fix = self._fix_point(route)
            if fix is None or not self.racetrack_waypoints(route):
                continue
            points.append({
                "id": route.get("id"), "fix": fix[0], "latitude": fix[1], "longitude": fix[2],
                "holding_route_id": route.get("id"),
                "active": any(state.get("holding_route_id") == route.get("id") for state in self._active_holds.values()),
            })
        return points

    def _activate_holding_nodes(self, waypoints):
        active_node_ids = []
        graph_nodes = getattr(self.graph, "nodes", None)
        if not isinstance(graph_nodes, dict):
            return active_node_ids
        for point in waypoints:
            name = point["name"]
            if name in self._temporary_node_references:
                self._temporary_node_references[name] += 1
                active_node_ids.append(name)
            elif name not in graph_nodes:
                self.graph.add_node(name, float(point["latitude"]), float(point["longitude"]))
                self._temporary_node_references[name] = 1
                active_node_ids.append(name)
        return active_node_ids

    def _release_holding_nodes(self, node_ids):
        graph_nodes = getattr(self.graph, "nodes", None)
        if not isinstance(graph_nodes, dict):
            return
        for name in node_ids:
            references = self._temporary_node_references.get(name, 0) - 1
            if references <= 0:
                self._temporary_node_references.pop(name, None)
                graph_nodes.pop(name, None)
            else:
                self._temporary_node_references[name] = references

    def compatible_holding_route_ids(self, aircraft, rejoin_waypoint):
        route = getattr(aircraft, "route", None)
        route_index = getattr(aircraft, "route_index", None)
        if (not isinstance(route, list) or not isinstance(route_index, int) or isinstance(route_index, bool)
                or route_index < 0 or route_index >= len(route)):
            return []
        compatible = []
        for holding in self.holding_routes:
            if not isinstance(holding, dict):
                continue
            trigger = holding.get("trigger_waypoint")
            points = self.racetrack_waypoints(holding)
            if (not isinstance(trigger, str) or trigger.strip().upper() != str(rejoin_waypoint).strip().upper()
                    or not points):
                continue
            if any(str(node).strip().upper() == trigger.strip().upper() for node in route[route_index:]):
                compatible.append(holding.get("id"))
        return compatible

    def expected_rejoin_node(self, aircraft, holding_route_id):
        holding = self._holding_route_by_id(holding_route_id)
        if holding is None:
            return None
        route = getattr(aircraft, "route", [])
        index = getattr(aircraft, "route_index", 0)
        trigger = str(holding.get("trigger_waypoint", "")).upper()
        rejoin_index = next((i for i in range(index, len(route))
                             if str(route[i]).upper() == trigger), None)
        return route[rejoin_index + 1] if rejoin_index is not None and rejoin_index + 1 < len(route) else None

    def compatible_holding_options(self, aircraft, conflict_waypoint):
        route = getattr(aircraft, "route", None)
        route_index = getattr(aircraft, "route_index", None)
        if (not isinstance(route, list) or not isinstance(route_index, int)
                or isinstance(route_index, bool) or route_index < 0 or route_index >= len(route)):
            return []
        conflict_index = next((i for i in range(route_index, len(route))
                               if str(route[i]).upper() == str(conflict_waypoint).upper()), None)
        if conflict_index is None:
            return []
        options = []
        for holding in self.holding_routes:
            if not isinstance(holding, dict):
                continue
            trigger = holding.get("trigger_waypoint")
            if (not isinstance(trigger, str) or not self.racetrack_waypoints(holding)):
                continue
            trigger_index = next((i for i in range(route_index, len(route))
                                  if str(route[i]).upper() == trigger.upper()), None)
            if trigger_index is None or trigger_index > conflict_index:
                continue
            route_id = holding.get("id")
            if not self.has_capacity(route_id):
                continue
            source_routes = holding.get("source_routes", [])
            aircraft_route_id = str(getattr(aircraft, "arrival_route_id", ""))
            if source_routes and aircraft_route_id not in {str(item) for item in source_routes}:
                continue
            options.append({
                "holding_route_id": route_id,
                "holding_fix": trigger,
                "rejoin_node": route[trigger_index + 1] if trigger_index + 1 < len(route) else None,
                "occupancy": self.holding_occupancy(route_id),
                "distance_in_route": trigger_index - route_index,
            })
        return sorted(options, key=lambda option: (option["occupancy"], option["distance_in_route"],
                                                    str(option["holding_route_id"])))

    def apply_holding_route(self, aircraft, holding_route_id, circuits=1,
                            required_delay_seconds=None):
        holding = self._holding_route_by_id(holding_route_id)
        if holding is None:
            return self._result(False, f"Unknown holding route: {holding_route_id}")
        if not self.has_capacity(holding_route_id):
            return self._result(False, f"Holding route {holding_route_id} is at capacity")
        if isinstance(circuits, bool) or not isinstance(circuits, int) or not 1 <= circuits <= 10:
            return self._result(False, "Holding circuits must be an integer from 1 to 10")
        aircraft_key = id(aircraft)
        if aircraft_key in self._active_holds:
            return self._result(False, "Aircraft already has an active holding route")
        original_route = getattr(aircraft, "route", None)
        route_index = getattr(aircraft, "route_index", None)
        if (not isinstance(original_route, list) or not original_route or not isinstance(route_index, int)
                or isinstance(route_index, bool) or route_index < 0 or route_index >= len(original_route)):
            return self._result(False, "Aircraft has no valid active route position")
        trigger = holding.get("trigger_waypoint")
        if not isinstance(trigger, str) or not trigger:
            return self._result(False, "Holding route has no valid rejoin waypoint")
        rejoin_index = next((i for i in range(route_index, len(original_route))
                             if str(original_route[i]).upper() == trigger.upper()), None)
        if rejoin_index is None:
            return self._result(False, "Rejoin waypoint is not ahead on the aircraft route")
        if (rejoin_index + 1 >= len(original_route)
                and (self.route_continuation_validator is None
                     or not self.route_continuation_validator(aircraft, trigger))):
            return self._result(False, f"No configured approach continuation is available at {trigger}")
        hold_points = self._selected_holding_geometry(
            holding, aircraft, required_delay_seconds
        )
        if len(hold_points) < 5 or hold_points[0]["name"].upper() != trigger.upper() or hold_points[-1]["name"].upper() != trigger.upper():
            return self._result(False, "Configured holding pattern geometry is invalid")
        lower = self._flight_level(holding.get("lower_limit"))
        upper = self._flight_level(holding.get("upper_limit"))
        if lower is None or upper is None or upper <= lower:
            return self._result(False, "Holding altitude limits are missing or invalid")
        occupied_altitudes = {
            getattr(state["aircraft"], "target_altitude_ft", None)
            for state in self._active_holds.values()
            if state.get("holding_route_id") == holding.get("id")
        }
        altitude = next((level for level in range(lower + 1000, upper + 1, 1000)
                         if level not in occupied_altitudes), None)
        if altitude is None:
            return self._result(False, "No separated altitude slot is available in this holding pattern")
        hold_names = [point["name"] for point in hold_points]
        entry_point = self._entry_transition(aircraft, original_route, rejoin_index, holding)
        entry_names = [entry_point["name"]] if entry_point else []
        already_at_trigger = str(getattr(aircraft, "assigned_node", "")).upper() == trigger.upper()
        if already_at_trigger:
            prefix = list(original_route[:rejoin_index + 1])
        else:
            prefix = list(original_route[:rejoin_index])
        # Enter the racetrack from its outbound leg so the aircraft does not
        # fly through the protected merge fix before it starts holding.
        circuit_names = hold_names[1:]
        circuit_points = hold_points[1:]
        holding_nodes = self._activate_holding_nodes(hold_points + ([entry_point] if entry_point else []))
        active_route = (prefix + entry_names + circuit_names
                        + list(original_route[rejoin_index + 1:]))
        hold_start = len(prefix) + len(entry_names)
        suffix_start = hold_start + len(circuit_names)
        rejoin_node = original_route[rejoin_index + 1] if rejoin_index + 1 < len(original_route) else None
        self._active_holds[aircraft_key] = {
            "aircraft": aircraft, "original_route": list(original_route),
            "original_route_id": getattr(aircraft, "arrival_route_id", None),
            "original_route_index": route_index,
            "original_target_node": getattr(aircraft, "target_node", None),
            "original_next_waypoint": (original_route[route_index + 1]
                                        if route_index + 1 < len(original_route) else None),
            "merge_point": trigger,
            "original_target_altitude": getattr(aircraft, "target_altitude_ft", None),
            "original_target_speed": getattr(aircraft, "target_speed_kts", None),
            "original_state": getattr(aircraft, "state", "ENROUTE"),
            "original_controller": getattr(aircraft, "controller", ""),
            "original_instruction": getattr(aircraft, "last_instruction", ""),
            "rejoin_index": rejoin_index, "holding_start_index": hold_start,
            "holding_rejoin_index": suffix_start - 1, "rejoin_waypoint": trigger,
            "rejoin_node": rejoin_node, "holding_route_id": holding.get("id"),
            "holding_node_ids": holding_nodes, "holding_waypoints": hold_points,
            "holding_circuit_waypoints": circuit_points,
            "repeat_circuit_waypoints": hold_points[1:],
            "circuit_length": len(circuit_names), "requested_circuits": circuits,
            "scheduled_circuits": 1, "completed_circuits": 0,
            "next_circuit_end_index": hold_start + len(circuit_names) - 1,
            "holding_since": time.monotonic(),
        }
        aircraft.route = active_route
        # Keep the navigation cursor anchored to the aircraft's current fix,
        # then point guidance at the next node in the spliced route.
        assigned_index = next(
            (i for i, node in enumerate(active_route[:hold_start])
             if str(node).upper() == str(getattr(aircraft, "assigned_node", "")).upper()),
            max(0, min(route_index - 1, hold_start - 1)),
        )
        aircraft.route_index = assigned_index
        aircraft.assigned_node = active_route[assigned_index]
        aircraft.target_node = active_route[assigned_index + 1]
        if getattr(aircraft, "clearance", None) is not None:
            aircraft.clearance.direct_node = aircraft.target_node
        aircraft.state = "HOLDING"
        aircraft.controller = "Arrival"
        aircraft.holding_fix = trigger
        aircraft.holding_route_id = holding.get("id")
        aircraft.holding_rejoin_node = rejoin_node
        aircraft.holding_circuits_completed = 0
        aircraft.approach_clearance = False
        aircraft.assign_altitude(altitude)
        speed = min(float(getattr(aircraft, "speed_kts", holding.get("max_speed_kt", 230))),
                    float(holding.get("max_speed_kt", 230)))
        aircraft.assign_speed(speed)
        aircraft.holding_altitude_ft = altitude
        aircraft.holding_speed_kts = speed
        aircraft.last_instruction = f"Hold at {trigger}, maintain {altitude} feet and {speed:g} knots"
        print(f"HOLD ENTRY | callsign={aircraft.callsign} fix={trigger} route={holding.get('id')}")
        print(f"HOLD | callsign={aircraft.callsign} fix={trigger} altitude={altitude}ft speed={speed:g}kt")
        print(f"HOLD CIRCUIT START | callsign={aircraft.callsign} circuit=1/{circuits}")
        print(f"HOLD ACTIVATION | callsign={aircraft.callsign} "
              f"holding_route={holding.get('id')} hold_fix={trigger} hold_active=true")
        print(f"AIRCRAFT ROUTE | callsign={aircraft.callsign} "
              f"old_route={original_route} new_route={active_route}")
        return self._result(True, f"Holding route {holding.get('id')} applied", rejoin_node=rejoin_node)

    def has_active_holding_route(self, aircraft):
        state = self._active_holds.get(id(aircraft))
        return state is not None and state.get("aircraft") is aircraft

    def state_for(self, aircraft):
        state = self._active_holds.get(id(aircraft))
        return state if state is not None and state.get("aircraft") is aircraft else None

    def holding_status(self, aircraft):
        state = self.state_for(aircraft)
        if state is None:
            return None
        return {
            "state": "HOLDING",
            "fix": state["rejoin_waypoint"],
            "route_id": state["holding_route_id"],
            "rejoin_node": state["rejoin_node"],
            "circuits_completed": state["completed_circuits"],
            "requested_circuits": state["requested_circuits"],
        }

    def complete_holding_circuit(self, aircraft, can_release):
        state = self.state_for(aircraft)
        if state is None:
            return self._result(False, "Aircraft has no active holding route")
        index = getattr(aircraft, "route_index", -1)
        if index != state.get("next_circuit_end_index"):
            return self._result(False, "Aircraft has not completed a holding circuit")
        state["completed_circuits"] += 1
        aircraft.holding_circuits_completed = state["completed_circuits"]
        print(f"HOLD CIRCUIT COMPLETE | callsign={aircraft.callsign} "
              f"circuit={state['completed_circuits']}/{state['requested_circuits']} fix={state['rejoin_waypoint']}")
        safety = can_release(aircraft) if can_release is not None else {"safe": True, "reason": "No release validator configured"}
        if isinstance(safety, bool):
            safety = {"safe": safety, "reason": "Downstream route check"}
        print(f"APPROACH CLEARANCE CHECK | callsign={aircraft.callsign} "
              f"result={'GRANTED' if safety.get('safe') else 'DENIED'} reason={safety.get('reason', '')}")
        if safety.get("safe"):
            aircraft.approach_clearance = True
            if isinstance(safety.get("approach_entry_index"), int):
                aircraft._approach_entry_index = safety["approach_entry_index"]
                aircraft._approach_entry_node = safety.get("approach_entry_node")
            print(f"APPROACH CLEARANCE GRANTED | callsign={aircraft.callsign}")
            released = self.restore_original_route(aircraft, circuit_boundary=True)
            released.update({"released": released.get("success", False), "safety": safety,
                             "completed_circuits": state["completed_circuits"],
                             "holding_route_id": state["holding_route_id"]})
            return released

        print(f"APPROACH CLEARANCE DENIED | callsign={aircraft.callsign} reason={safety.get('reason', '')}")
        if state["completed_circuits"] >= state["requested_circuits"]:
            state["requested_circuits"] += 1
        state["scheduled_circuits"] += 1
        state["next_circuit_end_index"] = index + len(state["repeat_circuit_waypoints"])
        print(f"ADDITIONAL HOLD CIRCUIT | callsign={aircraft.callsign} next={state['scheduled_circuits']}")
        self._schedule_holding_circuit(aircraft, state, index)
        state["holding_rejoin_index"] += state["circuit_length"]
        return self._result(True, "Downstream traffic unsafe; continue holding", released=False,
                            safety=safety, completed_circuits=state["completed_circuits"])

    @staticmethod
    def _schedule_holding_circuit(aircraft, state, index):
        points = state.get("repeat_circuit_waypoints", state["holding_circuit_waypoints"])
        circuit_names = [point["name"] for point in points]
        aircraft.route[index + 1:index + 1] = circuit_names
        aircraft.target_node = aircraft.route[index + 1]
        if getattr(aircraft, "clearance", None) is not None:
            aircraft.clearance.direct_node = aircraft.target_node
        print(f"HOLD CIRCUIT START | callsign={aircraft.callsign} "
              f"circuit={state['scheduled_circuits']}/{state['requested_circuits']}")

    def is_holding_circuit_boundary(self, aircraft):
        state = self.state_for(aircraft)
        if state is None:
            return False
        index = getattr(aircraft, "route_index", -1)
        return index == state.get("next_circuit_end_index")

    def restore_original_route(self, aircraft, circuit_boundary=False):
        state = self.state_for(aircraft)
        if state is None:
            return self._result(False, "Aircraft has no active holding route")
        if (str(getattr(aircraft, "assigned_node", "")).upper() != str(state["rejoin_waypoint"]).upper()
                or (not circuit_boundary and getattr(aircraft, "route_index", None) != state["holding_rejoin_index"])):
            return self._result(False, f"Aircraft has not reached rejoin waypoint {state['rejoin_waypoint']}")
        original = list(state["original_route"])
        rejoin_index = state["rejoin_index"]
        aircraft.route = original
        aircraft.route_index = rejoin_index + 1
        aircraft.assigned_node = original[rejoin_index]
        aircraft.target_node = original[aircraft.route_index] if aircraft.route_index < len(original) else None
        if getattr(aircraft, "clearance", None) is not None:
            aircraft.clearance.direct_node = aircraft.target_node
        altitude, speed = state.get("original_target_altitude"), state.get("original_target_speed")
        if altitude is not None:
            aircraft.assign_altitude(altitude)
        if speed is not None:
            aircraft.assign_speed(speed)
        aircraft.state = state.get("original_state", "ENROUTE")
        aircraft.controller = state.get("original_controller", "")
        aircraft.last_instruction = state.get("original_instruction", "")
        for name in ("holding_fix", "holding_route_id", "holding_rejoin_node", "holding_circuits_completed"):
            if hasattr(aircraft, name):
                delattr(aircraft, name)
        self._release_holding_nodes(state.get("holding_node_ids", []))
        del self._active_holds[id(aircraft)]
        print(f"HOLD EXIT | callsign={aircraft.callsign} fix={state['rejoin_waypoint']}")
        if getattr(aircraft, "approach_clearance", False):
            print(f"APPROACH ENTRY | callsign={aircraft.callsign} trigger={state['rejoin_waypoint']} "
                  f"next={aircraft.target_node}")
        return self._result(True, f"Original route restored at {state['rejoin_waypoint']}",
                            rejoin_waypoint=state["rejoin_waypoint"], rejoin_node=state["rejoin_node"])

    def active_holding_routes(self):
        result = []
        for state in self._active_holds.values():
            aircraft = state["aircraft"]
            result.append({
                "type": "holding_route", "callsign": aircraft.callsign,
                "holding_route_id": state["holding_route_id"], "fix": state["rejoin_waypoint"],
                "active": True, "waypoints": list(state["holding_waypoints"]),
                "circuits_completed": state["completed_circuits"],
                "requested_circuits": state["requested_circuits"],
                "rejoin_node": state["rejoin_node"],
            })
        return result
