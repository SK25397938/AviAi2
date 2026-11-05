import logging
import math
import time
from threading import RLock


class HoldingManager:
    MAX_PLANNED_CIRCUITS = 4
    ARC_SEGMENTS = 12
    BOUNDARY_DIAGNOSTIC_INTERVAL_SECONDS = 30.0
    MIN_HOLD_VERTICAL_SEPARATION_FT = 1000.0
    PREFERRED_HOLD_VERTICAL_SEPARATION_FT = 2000.0

    def __init__(self, holding_routes, graph=None):
        if isinstance(holding_routes, dict):
            holding_routes = holding_routes.get("holding_routes", [])
        self.holding_routes = holding_routes if isinstance(holding_routes, list) else []
        self.graph = graph
        self.route_continuation_validator = None
        self._active_holds = {}
        self._capacity_lock = RLock()
        self._temporary_node_references = {}
        self._reported_geometry_diagnostics = set()
        self._boundary_diagnostic_last = {}

    def _geometry_diagnostic(self, route, message):
        route_id = route.get("id", "<unknown>") if isinstance(route, dict) else "<unknown>"
        key = (str(route_id), message)
        if key not in self._reported_geometry_diagnostics:
            logging.getLogger(__name__).warning(
                "Holding geometry unavailable for %s: %s", route_id, message
            )
            self._reported_geometry_diagnostics.add(key)

    def _inbound_course_true(self, route):
        """Resolve true degrees, requiring explicit provenance for magnetic input.

        Signed magnetic variation is east-positive, so true = magnetic + variation.
        Magnetic conversion is accepted only when the per-hold variation is marked
        verified and has a non-empty source reference. No airport-wide variation is
        inferred or applied to a hold.
        """
        raw_course = route.get("inbound_course_true")
        if raw_course is None:
            raw_course = route.get("inbound_course_mag")
            if raw_course is None:
                self._geometry_diagnostic(route, "missing inbound_course_true (or convertible inbound_course_mag)")
                return None
            raw_variation = route.get("magnetic_variation_deg")
            source = route.get("magnetic_variation_source")
            if (route.get("magnetic_variation_verified") is not True
                    or not isinstance(source, str) or not source.strip()
                    or isinstance(raw_variation, bool)):
                self._geometry_diagnostic(
                    route,
                    "inbound_course_mag requires verified magnetic_variation_deg and magnetic_variation_source",
                )
                return None
            try:
                variation = float(raw_variation)
            except (TypeError, ValueError):
                self._geometry_diagnostic(route, "magnetic_variation_deg is not numeric")
                return None
            if not math.isfinite(variation) or not -180.0 <= variation <= 180.0:
                self._geometry_diagnostic(route, "magnetic_variation_deg must be finite and within [-180, 180]")
                return None
        else:
            # Existing true-course configurations retain precedence and behavior.
            variation = 0.0

        if isinstance(raw_course, bool):
            self._geometry_diagnostic(route, "inbound course must be numeric degrees")
            return None
        try:
            course = float(raw_course)
        except (TypeError, ValueError):
            self._geometry_diagnostic(route, "inbound course must be numeric degrees")
            return None
        if not math.isfinite(course) or not 0.0 <= course <= 360.0:
            self._geometry_diagnostic(route, "inbound course must be finite and within [0, 360]")
            return None
        return (course + variation) % 360.0

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
        with self._capacity_lock:
            return sum(1 for state in self._active_holds.values()
                       if str(state.get("holding_route_id")) == str(holding_route_id))

    def has_capacity(self, holding_route_id):
        with self._capacity_lock:
            route = self._holding_route_by_id(holding_route_id)
            if route is None:
                return False
            capacity = route.get("capacity", 1)
            try:
                capacity = max(1, int(capacity))
            except (TypeError, ValueError):
                capacity = 1
            return self.holding_occupancy(holding_route_id) < capacity

    def _holding_levels(self, holding, lower, upper):
        configured = holding.get("holding_levels_ft")
        if isinstance(configured, list):
            levels = sorted({float(level) for level in configured
                             if isinstance(level, (int, float)) and not isinstance(level, bool)
                             and lower <= float(level) <= upper})
            return levels
        return [float(level) for level in range(lower + 1000, upper + 1, 1000)]

    @staticmethod
    def _vertical_profile(aircraft, target_altitude_ft):
        current = float(getattr(aircraft, "altitude_ft", target_altitude_ft) or 0)
        target = float(target_altitude_ft)
        current_rate = float(getattr(aircraft, "vertical_speed_fpm", 0) or 0)
        raw_target_rate = getattr(aircraft, "target_vertical_speed_fpm", None)
        has_target_rate = (isinstance(raw_target_rate, (int, float))
                           and not isinstance(raw_target_rate, bool))
        target_rate = (max(-1500.0, min(1500.0, float(raw_target_rate)))
                       if has_target_rate else math.copysign(1500.0, target - current))
        if target_rate * (target - current) <= 0:
            target_rate = math.copysign(1500.0, target - current)
        return current, current_rate, target, target_rate, has_target_rate

    @classmethod
    def _predicted_altitude(cls, aircraft, target_altitude_ft, seconds):
        current, current_rate, target, target_rate, has_target_rate = cls._vertical_profile(
            aircraft, target_altitude_ft
        )
        delta = target - current
        if abs(delta) < 1.0:
            return target
        duration = max(0.0, float(seconds))
        if not has_target_rate:
            change = math.copysign(min(abs(delta), 1500.0 * duration / 60.0), delta)
        else:
            ramp = min(duration, abs(target_rate - current_rate) / 180.0)
            ramp_end = current_rate + math.copysign(180.0 * ramp, target_rate - current_rate)
            change = (current_rate + ramp_end) * 0.5 * ramp / 60.0
            change += target_rate * max(0.0, duration - ramp) / 60.0
            change = math.copysign(min(abs(delta), abs(change)), delta)
        return min(max(current + change, min(current, target)), max(current, target))

    def _level_transition_is_safe(self, aircraft, target_level, other_states):
        duration = 0.0
        own_current = float(getattr(aircraft, "altitude_ft", target_level) or 0)
        own_duration = abs(float(target_level) - own_current) / 1500.0 * 60.0
        duration = max(duration, own_duration)
        profiles = []
        for state in other_states:
            other = state["aircraft"]
            other_target = float(state.get("target_holding_level_ft")
                                 or state.get("assigned_holding_level_ft")
                                 or getattr(other, "target_altitude_ft", getattr(other, "altitude_ft", 0)))
            profiles.append((other, other_target))
            other_current = float(getattr(other, "altitude_ft", other_target) or 0)
            duration = max(duration, abs(other_target - other_current) / 1500.0 * 60.0)
        horizon = int(math.ceil(min(1800.0, duration + 5.0)))
        for other, other_target in profiles:
            previous = (self._predicted_altitude(aircraft, target_level, 0.0)
                        - self._predicted_altitude(other, other_target, 0.0))
            for second in range(1, horizon + 1):
                difference = (self._predicted_altitude(aircraft, target_level, second)
                              - self._predicted_altitude(other, other_target, second))
                if previous == 0 or difference == 0 or previous * difference < 0:
                    return False
                if min(abs(previous), abs(difference)) < self.MIN_HOLD_VERTICAL_SEPARATION_FT:
                    return False
                previous = difference
        return True

    def _select_holding_level(self, aircraft, holding, lower, upper,
                              exclude_aircraft=None, exclude_level=None,
                              preferred_level=None):
        levels = self._holding_levels(holding, lower, upper)
        others = [state for state in self._active_holds.values()
                  if state.get("holding_route_id") == holding.get("id")
                  and state.get("aircraft") is not exclude_aircraft]
        current_altitude = float(getattr(aircraft, "altitude_ft", lower) or lower)
        current_target = float(getattr(aircraft, "target_altitude_ft", current_altitude)
                               or current_altitude)
        direction = (current_target - current_altitude
                     or float(getattr(aircraft, "vertical_speed_fpm", 0) or 0))
        assigned = [float(state.get("target_holding_level_ft")
                           or state.get("assigned_holding_level_ft")
                           or getattr(state["aircraft"], "target_altitude_ft", 0))
                    for state in others]
        ranked = []
        for level in levels:
            if exclude_level is not None and abs(level - float(exclude_level)) < 1.0:
                continue
            if not self._level_transition_is_safe(aircraft, level, others):
                continue
            spacing = min((abs(level - occupied) for occupied in assigned), default=float("inf"))
            spacing_penalty = int(spacing < self.PREFERRED_HOLD_VERTICAL_SEPARATION_FT)
            direction_penalty = int((direction > 0 and level < current_altitude)
                                    or (direction < 0 and level > current_altitude))
            ranked.append((spacing_penalty, abs(level - current_altitude),
                           direction_penalty, level))
        if preferred_level is not None:
            preferred = next((item for item in ranked
                              if abs(item[-1] - float(preferred_level)) < 1.0), None)
            return preferred[-1] if preferred is not None else None
        return min(ranked)[-1] if ranked else None

    def safe_holding_levels(self, aircraft, holding_route_id, exclude_current=True):
        """List currently safe primary-hold levels; callers still revalidate before applying."""
        with self._capacity_lock:
            holding = self._holding_route_by_id(holding_route_id)
            if not isinstance(holding, dict) or not isinstance(holding.get("holding_levels_ft"), list):
                return []
            lower = self._flight_level(holding.get("lower_limit"))
            upper = self._flight_level(holding.get("upper_limit"))
            if lower is None or upper is None or upper <= lower:
                return []
            state = self.state_for(aircraft)
            current = (state.get("target_holding_level_ft") if state else
                       getattr(aircraft, "holding_altitude_ft", None))
            return [level for level in self._holding_levels(holding, lower, upper)
                    if (not exclude_current or current is None or abs(level - float(current)) >= 1.0)
                    and self._level_transition_is_safe(
                        aircraft, level,
                        [item for item in self._active_holds.values()
                         if item.get("holding_route_id") == holding_route_id
                         and item.get("aircraft") is not aircraft],
                    )]

    @staticmethod
    def _fix_point(route):
        names = [str(route.get(key, "")).strip().upper()
                 for key in ("fix", "trigger_waypoint") if route.get(key)]
        for point in route.get("waypoints", []):
            if isinstance(point, dict) and str(point.get("name", "")).strip().upper() in names:
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
        course = self._inbound_course_true(route)
        if fix is None:
            fix_names = "/".join(
                str(route.get(key, ""))
                for key in ("fix", "trigger_waypoint") if route.get(key)
            )
            self._geometry_diagnostic(
                route,
                "waypoints has no valid coordinate anchor matching fix/trigger "
                f"{fix_names or '<missing>'}",
            )
            return []
        if course is None:
            return []
        try:
            leg = float(route["leg_length_nm"])
            radius = float(route["turn_radius_nm"])
            direction = str(route["turn_direction"]).upper()
        except (KeyError, TypeError, ValueError):
            self._geometry_diagnostic(route, "missing or invalid leg_length_nm, turn_radius_nm, or turn_direction")
            return []
        if (not math.isfinite(course) or not math.isfinite(leg)
                or not math.isfinite(radius) or leg <= 0 or radius <= 0
                or direction not in {"L", "R"}):
            self._geometry_diagnostic(route, "course or racetrack dimensions are outside valid ranges")
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
            course = self._inbound_course_true(route)
            if course is None:
                return False
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
        # The route transition must join the generated racetrack itself. The
        # former independently calculated radius point used the arrival leg's
        # bearing, which did not generally coincide with the first sampled
        # turn waypoint and created a visible dogleg beside the intended hold.
        geometry = self.racetrack_waypoints(holding)
        return dict(geometry[1]) if len(geometry) > 1 else None

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
            source_routes = holding.get("source_routes", [])
            aircraft_route_id = str(getattr(aircraft, "arrival_route_id", ""))
            if source_routes and aircraft_route_id not in {str(item) for item in source_routes}:
                continue
            if any(str(node).strip().upper() == trigger.strip().upper() for node in route[route_index:]):
                trigger_index = next(i for i in range(route_index, len(route))
                                     if str(route[i]).strip().upper() == trigger.strip().upper())
                entry_fixes = {str(name).strip().upper() for name in holding.get("entry_fixes", [])}
                route_prefix = {str(node).strip().upper() for node in route[:trigger_index + 1]}
                if not entry_fixes or route_prefix.intersection(entry_fixes):
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
            entry_fixes = {str(name).strip().upper() for name in holding.get("entry_fixes", [])}
            route_prefix = {str(node).strip().upper() for node in route[:trigger_index + 1]}
            if entry_fixes and not route_prefix.intersection(entry_fixes):
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
                            required_delay_seconds=None, preferred_level_ft=None):
        with self._capacity_lock:
            return self._apply_holding_route(
                aircraft, holding_route_id, circuits, required_delay_seconds,
                preferred_level_ft
            )

    def _apply_holding_route(self, aircraft, holding_route_id, circuits=1,
                             required_delay_seconds=None, preferred_level_ft=None):
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
        source_routes = holding.get("source_routes", [])
        active_route_id = str(getattr(aircraft, "arrival_route_id", ""))
        if source_routes and active_route_id not in {str(item) for item in source_routes}:
            return self._result(False, f"Holding route {holding_route_id} is not configured for arrival route {active_route_id or '<missing>'}")
        entry_fixes = {str(name).strip().upper() for name in holding.get("entry_fixes", [])}
        route_prefix = {str(node).strip().upper() for node in original_route[:rejoin_index + 1]}
        if entry_fixes and rejoin_index + 1 < len(original_route) and not route_prefix.intersection(entry_fixes):
            return self._result(False, f"No configured entry fix for holding route {holding_route_id} exists on the active route")
        expected_after = holding.get("route_after_hold")
        if isinstance(expected_after, list) and expected_after:
            actual_after = original_route[rejoin_index + 1:rejoin_index + 1 + len(expected_after)]
            if actual_after and [str(node).upper() for node in actual_after] != [str(node).upper() for node in expected_after]:
                return self._result(False, f"Route after {trigger} is incompatible with holding route {holding_route_id}")
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
        altitude = self._select_holding_level(
            aircraft, holding, lower, upper, preferred_level=preferred_level_ft
        )
        if altitude is None:
            return self._result(False, "No safe holding level is available with 1,000 ft vertical separation")
        hold_names = [point["name"] for point in hold_points]
        entry_point = self._entry_transition(aircraft, original_route, rejoin_index, holding)
        entry_names = [entry_point["name"]] if entry_point else []
        # Keep the configured trigger fix in the route. Skipping it and aiming
        # directly at the first racetrack point creates a chord across the
        # pattern and makes the flown track look translated from its geometry.
        prefix = list(original_route[:rejoin_index + 1])
        circuit_names = hold_names[2:] if entry_point else hold_names[1:]
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
            "original_target_vertical_speed": getattr(aircraft, "target_vertical_speed_fpm", None),
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
            # A hold instruction authorizes one circuit at a time. Future
            # circuits are only added after the circuit-boundary assessment.
            "circuit_length": len(circuit_names), "requested_circuits": 1,
            "scheduled_circuits": 1, "completed_circuits": 0,
            "next_circuit_end_index": hold_start + len(circuit_names) - 1,
            "assigned_holding_level_ft": altitude,
            "target_holding_level_ft": altitude,
            "required_delay_seconds": required_delay_seconds,
            "last_level_change_circuit": 0,
            "holding_since": time.monotonic(),
            "hold_episode_id": f"{getattr(aircraft, 'callsign', '<unknown>')}:{time.monotonic_ns()}",
            "last_release_evaluation_at": None,
            "last_release_rejection_reason": None,
            "safety_exception_circuits": 0,
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
        aircraft.target_vertical_speed_fpm = None
        speed = min(float(getattr(aircraft, "speed_kts", holding.get("max_speed_kt", 230))),
                    float(holding.get("max_speed_kt", 230)))
        aircraft.assign_speed(speed)
        aircraft.holding_altitude_ft = altitude
        aircraft.holding_speed_kts = speed
        aircraft.last_instruction = f"Hold at {trigger}, maintain {altitude} feet and {speed:g} knots"
        print(f"HOLD ENTRY | callsign={aircraft.callsign} fix={trigger} route={holding.get('id')} "
              f"level={altitude}ft occupancy={self.holding_occupancy(holding_route_id)}/{holding.get('capacity', 1)}")
        print(f"HOLD | callsign={aircraft.callsign} fix={trigger} altitude={altitude}ft speed={speed:g}kt")
        print(f"HOLD CIRCUIT START | callsign={aircraft.callsign} circuit=1/1")
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
            "holding_age_seconds": max(0.0, time.monotonic() - state["holding_since"]),
            "priority_score": self.holding_priority_score(state),
        }

    @staticmethod
    def holding_priority_score(state, now=None):
        """Deterministic queue rank: each completed circuit outweighs age drift."""
        if not isinstance(state, dict):
            return 0.0
        now = time.monotonic() if now is None else now
        age = max(0.0, now - state.get("holding_since", now))
        return int(state.get("completed_circuits", 0)) * 1_000_000.0 + age

    def complete_holding_circuit(self, aircraft, can_release, level_decider=None):
        state = self.state_for(aircraft)
        if state is None:
            return self._result(False, "Aircraft has no active holding route")
        index = getattr(aircraft, "route_index", -1)
        if index != state.get("next_circuit_end_index"):
            return self._result(False, "Aircraft has not completed a holding circuit")
        state["completed_circuits"] += 1
        aircraft.holding_circuits_completed = state["completed_circuits"]
        state["last_release_evaluation_at"] = time.monotonic()
        state["priority_score"] = self.holding_priority_score(state)
        logging.getLogger(__name__).info(
            "HOLD_CIRCUIT_COMPLETED",
            extra={"event": "HOLD_CIRCUIT_COMPLETED", "aircraft_callsign": aircraft.callsign,
                   "hold_id": state["holding_route_id"], "holding_fix": state["rejoin_waypoint"],
                   "completed_circuits": state["completed_circuits"],
                   "holding_age_seconds": max(0.0, time.monotonic() - state.get("holding_since", time.monotonic())),
                   "holding_level_ft": state.get("assigned_holding_level_ft")})
        print(f"HOLD CIRCUIT COMPLETE | callsign={aircraft.callsign} "
              f"circuit={state['completed_circuits']}/{state['requested_circuits']} fix={state['rejoin_waypoint']}")
        safety = can_release(aircraft) if can_release is not None else {"safe": True, "reason": "No release validator configured"}
        if isinstance(safety, bool):
            safety = {"safe": safety, "reason": "Downstream route check"}
        entry_index = safety.get("approach_entry_index")
        entry_node = safety.get("approach_entry_node")
        if (safety.get("safe")
                and (not isinstance(entry_index, int) or isinstance(entry_index, bool)
                     or entry_index < 0 or not isinstance(entry_node, str)
                     or not entry_node.strip())):
            safety = {**safety, "safe": False,
                      "reason": "No committed safe approach entry is available for holding release"}
        print(f"APPROACH CLEARANCE CHECK | callsign={aircraft.callsign} "
              f"result={'GRANTED' if safety.get('safe') else 'DENIED'} reason={safety.get('reason', '')}")
        if not safety.get("safe"):
            state["last_release_rejection_reason"] = safety.get("reason", "release unsafe")
        if safety.get("safe"):
            previous_route = list(getattr(aircraft, "route", []) or [])
            previous_cursor = getattr(aircraft, "route_index", None)
            previous_target = getattr(aircraft, "target_node", None)
            previous_state = getattr(aircraft, "state", None)
            previous_hold = state.get("holding_route_id")
            released = self.restore_original_route(aircraft, circuit_boundary=True)
            if not released.get("success"):
                # A safe approach decision is not a committed clearance until
                # the permanent route has actually replaced the hold route.
                aircraft.approach_clearance = False
                for name in ("_approach_entry_index", "_approach_entry_node"):
                    if hasattr(aircraft, name):
                        delattr(aircraft, name)
                logging.getLogger(__name__).error(
                    "Holding release rejected after route restoration failed",
                    extra={
                        "event": "holding_release_route_restore_failed",
                        "aircraft_callsign": aircraft.callsign,
                        "holding_route_id": previous_hold,
                        "route_restore_reason": released.get("message"),
                        "route_cursor": getattr(aircraft, "route_index", None),
                        "assigned_waypoint": getattr(aircraft, "assigned_node", None),
                        "target_waypoint": getattr(aircraft, "target_node", None),
                    },
                )
                released.update({"released": False, "release_attempted": True,
                                 "safety": safety, "holding_route_id": previous_hold,
                                 "old_route": previous_route, "old_route_index": previous_cursor,
                                 "old_target_node": previous_target,
                                 "old_state": previous_state,
                                 "new_state": getattr(aircraft, "state", None),
                                 "hold_active": self.has_active_holding_route(aircraft)})
                return released

            aircraft.approach_clearance = True
            aircraft._approach_entry_index = entry_index
            aircraft._approach_entry_node = entry_node
            print(f"APPROACH CLEARANCE GRANTED | callsign={aircraft.callsign}")
            released.update({"released": True, "safety": safety,
                             "completed_circuits": state["completed_circuits"],
                             "holding_route_id": previous_hold,
                             "old_route": previous_route, "old_route_index": previous_cursor,
                             "old_target_node": previous_target, "old_state": previous_state,
                             "new_route": list(getattr(aircraft, "route", []) or []),
                             "new_route_index": getattr(aircraft, "route_index", None),
                             "new_target_node": getattr(aircraft, "target_node", None),
                             "new_state": getattr(aircraft, "state", None),
                             "hold_active": self.has_active_holding_route(aircraft),
                             "approach_entry_node": safety.get("approach_entry_node")})
            return released

        print(f"APPROACH CLEARANCE DENIED | callsign={aircraft.callsign} reason={safety.get('reason', '')}")
        aircraft.approach_clearance = False
        if state["completed_circuits"] >= state["requested_circuits"]:
            if state["completed_circuits"] < self.MAX_PLANNED_CIRCUITS:
                state["requested_circuits"] = state["completed_circuits"] + 1
            else:
                state["safety_exception_circuits"] += 1
                logging.getLogger(__name__).critical(
                    "HOLD_SAFETY_EXCEPTION",
                    extra={"event": "HOLD_SAFETY_EXCEPTION", "aircraft_callsign": aircraft.callsign,
                           "hold_id": state["holding_route_id"], "holding_fix": state["rejoin_waypoint"],
                           "completed_circuits": state["completed_circuits"],
                           "reason": safety.get("reason", "release unsafe")})
        if state["completed_circuits"] == self.MAX_PLANNED_CIRCUITS:
            logging.getLogger(__name__).error(
                "HOLD_LIMIT_REACHED",
                extra={"event": "HOLD_LIMIT_REACHED", "aircraft_callsign": aircraft.callsign,
                       "hold_id": state["holding_route_id"], "holding_fix": state["rejoin_waypoint"],
                       "completed_circuits": state["completed_circuits"],
                       "decision": "release unsafe; safety exception reassessment"})
        current_level = state.get("target_holding_level_ft")
        holding = self._holding_route_by_id(state.get("holding_route_id")) or {}
        level_change_due = (
            isinstance(current_level, (int, float))
            and state["completed_circuits"] >= 2
            and state["completed_circuits"] - state.get("last_level_change_circuit", 0) >= 2
            and isinstance(holding.get("holding_levels_ft"), list)
        )
        if level_change_due:
            lower = self._flight_level(holding.get("lower_limit"))
            upper = self._flight_level(holding.get("upper_limit"))
            if level_decider is not None:
                recommendation = level_decider(aircraft, state)
                next_level = recommendation.get("level_ft") if isinstance(recommendation, dict) else None
                if next_level is not None:
                    with self._capacity_lock:
                        next_level = self._select_holding_level(
                            aircraft, holding, lower, upper,
                            exclude_aircraft=aircraft, exclude_level=current_level,
                            preferred_level=next_level,
                        )
                if (isinstance(recommendation, dict)
                        and recommendation.get("choice") == "REMAIN"):
                    next_level = None
            else:
                with self._capacity_lock:
                    next_level = (self._select_holding_level(
                        aircraft, holding, lower, upper,
                        exclude_aircraft=aircraft, exclude_level=current_level,
                    ) if lower is not None and upper is not None else None)
            if next_level is not None:
                previous_level = current_level
                state["previous_holding_level_ft"] = previous_level
                state["assigned_holding_level_ft"] = next_level
                state["target_holding_level_ft"] = next_level
                state["last_level_change_circuit"] = state["completed_circuits"]
                aircraft.holding_altitude_ft = next_level
                aircraft.assign_altitude(next_level)
                aircraft.target_vertical_speed_fpm = None
                print(f"HOLD LEVEL CHANGE | callsign={aircraft.callsign} "
                      f"fix={state['rejoin_waypoint']} circuit={state['completed_circuits']} "
                      f"from={previous_level}ft to={next_level}ft reason=continued delay "
                      f"after {safety.get('reason', 'release remains unsafe')}")
            elif level_decider is not None:
                print(f"HOLD LEVEL DECISION | callsign={aircraft.callsign} "
                      f"level={current_level}ft action=REMAIN_OR_REJECTED")
        state["scheduled_circuits"] += 1
        state["next_circuit_end_index"] = index + len(state["repeat_circuit_waypoints"])
        print(f"ADDITIONAL HOLD CIRCUIT | callsign={aircraft.callsign} next={state['scheduled_circuits']} "
              f"planned={state['requested_circuits']} safety_exception={state['completed_circuits'] >= self.MAX_PLANNED_CIRCUITS}")
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

    def report_boundary_capture_rejection(self, aircraft, reason, **capture_details):
        """Emit a throttled diagnostic if the current target is the hold boundary."""
        state = self.state_for(aircraft)
        if state is None:
            return False
        boundary_index = state.get("next_circuit_end_index")
        route_index = getattr(aircraft, "route_index", -1)
        target_waypoint = getattr(aircraft, "target_node", None)
        target_index = route_index + 1
        route = getattr(aircraft, "route", [])
        if (not isinstance(boundary_index, int) or target_index != boundary_index
                or target_index < 0 or target_index >= len(route)
                or str(route[target_index]).upper() != str(target_waypoint).upper()):
            return False

        callsign = str(getattr(aircraft, "callsign", "<unknown>"))
        hold_id = str(state.get("holding_route_id", "<unknown>"))
        now = time.monotonic()
        key = (callsign, hold_id, boundary_index, str(reason))
        previous = self._boundary_diagnostic_last.get(key)
        if (previous is not None
                and now - previous < self.BOUNDARY_DIAGNOSTIC_INTERVAL_SECONDS):
            return True
        self._boundary_diagnostic_last[key] = now
        logging.getLogger(__name__).warning(
            "Holding circuit boundary waypoint was not captured",
            extra={
                "event": "holding_boundary_capture_rejected",
                "aircraft_callsign": callsign,
                "hold_id": hold_id,
                "route_cursor": route_index,
                "target_waypoint": target_waypoint,
                "assigned_node": getattr(aircraft, "assigned_node", None),
                "boundary_index": boundary_index,
                "capture_rejection_reason": reason,
                **capture_details,
            },
        )
        return True

    def restore_original_route(self, aircraft, circuit_boundary=False):
        with self._capacity_lock:
            return self._restore_original_route(aircraft, circuit_boundary)

    def _restore_original_route(self, aircraft, circuit_boundary=False):
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
        aircraft.target_vertical_speed_fpm = state.get("original_target_vertical_speed")
        if speed is not None:
            aircraft.assign_speed(speed)
        aircraft.state = state.get("original_state", "ENROUTE")
        aircraft.controller = state.get("original_controller", "")
        aircraft.last_instruction = state.get("original_instruction", "")
        for name in ("holding_fix", "holding_route_id", "holding_rejoin_node", "holding_circuits_completed",
                     "holding_altitude_ft", "holding_level_transition"):
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

    def cancel_holding_route(self, aircraft, reason="holding assignment cancelled"):
        with self._capacity_lock:
            state = self.state_for(aircraft)
            if state is None:
                return self._result(False, "Aircraft has no active holding route")
            self._release_holding_nodes(state.get("holding_node_ids", []))
            del self._active_holds[id(aircraft)]
            for name in ("holding_fix", "holding_route_id", "holding_rejoin_node",
                         "holding_circuits_completed", "holding_altitude_ft", "holding_level_transition"):
                if hasattr(aircraft, name):
                    delattr(aircraft, name)
            if getattr(aircraft, "state", None) == "HOLDING":
                aircraft.state = state.get("original_state", "ENROUTE")
            logging.getLogger(__name__).info(
                "Holding reservation released callsign=%s hold=%s reason=%s occupancy=%s/%s",
                aircraft.callsign, state.get("holding_route_id"), reason,
                self.holding_occupancy(state.get("holding_route_id")),
                (self._holding_route_by_id(state.get("holding_route_id")) or {}).get("capacity", 1),
            )
            return self._result(True, reason, holding_route_id=state.get("holding_route_id"))

    def prune_inactive_holds(self, active_aircraft):
        active = {id(aircraft): aircraft for aircraft in active_aircraft}
        with self._capacity_lock:
            inactive = [state["aircraft"] for key, state in self._active_holds.items()
                        if key not in active or active[key] is not state.get("aircraft")]
            for aircraft in inactive:
                self.cancel_holding_route(aircraft, "aircraft no longer active")
        return len(inactive)

    def active_holding_routes(self):
        result = []
        for state in self._active_holds.values():
            aircraft = state["aircraft"]
            result.append({
                "type": "holding_route", "callsign": aircraft.callsign,
                "aircraft_type": getattr(aircraft, "aircraft_type", None),
                "position": {"latitude": getattr(aircraft, "lat", None),
                             "longitude": getattr(aircraft, "lon", None)},
                "altitude_ft": getattr(aircraft, "altitude_ft", None),
                "target_altitude_ft": getattr(aircraft, "target_altitude_ft", None),
                "speed_kts": getattr(aircraft, "speed_kts", None),
                "heading_deg": getattr(aircraft, "heading_deg", None),
                "route": list(getattr(aircraft, "route", []) or []),
                "route_index": getattr(aircraft, "route_index", None),
                "target_waypoint": getattr(aircraft, "target_node", None),
                "holding_route_id": state["holding_route_id"], "fix": state["rejoin_waypoint"],
                "active": True, "waypoints": list(state["holding_waypoints"]),
                "circuits_completed": state["completed_circuits"],
                "requested_circuits": state["requested_circuits"],
                "holding_level_ft": getattr(aircraft, "holding_altitude_ft", None),
                "rejoin_node": state["rejoin_node"],
            })
        return result
