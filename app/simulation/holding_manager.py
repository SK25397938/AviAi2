import math


class HoldingManager:
    ARC_SEGMENTS = 12

    def __init__(self, holding_routes, graph=None):
        if isinstance(holding_routes, dict):
            holding_routes = holding_routes.get("holding_routes", [])
        self.holding_routes = holding_routes if isinstance(holding_routes, list) else []
        self.graph = graph
        self._active_holds = {}
        self._temporary_node_references = {}

    @staticmethod
    def _result(success, message, **values):
        return {"success": success, "message": message, **values}

    def _holding_route_by_id(self, holding_route_id):
        return next((route for route in self.holding_routes
                     if isinstance(route, dict) and str(route.get("id")) == str(holding_route_id)), None)

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
        sign = -1.0 if direction == "L" else 1.0
        outbound = math.radians((course + 180.0) % 360.0)
        ux, uy = math.sin(outbound), math.cos(outbound)
        lx, ly = -uy * sign, ux * sign

        def coordinate(x_nm, y_nm):
            lat = lat0 + y_nm / 60.0
            cosine = math.cos(math.radians(lat0))
            lon = lon0 + x_nm / max(60.0 * cosine, 1e-6)
            return lat, lon

        points_xy = [(0.0, 0.0), (ux * leg, uy * leg)]
        p1x, p1y = points_xy[-1]
        c1x, c1y = p1x + lx * radius, p1y + ly * radius
        r0x, r0y = p1x - c1x, p1y - c1y
        for step in range(1, self.ARC_SEGMENTS + 1):
            angle = sign * math.pi * step / self.ARC_SEGMENTS
            points_xy.append((c1x + r0x * math.cos(angle) - r0y * math.sin(angle),
                              c1y + r0x * math.sin(angle) + r0y * math.cos(angle)))

        p2x, p2y = points_xy[-1]
        inbound_x, inbound_y = -ux, -uy
        p3x, p3y = p2x + inbound_x * leg, p2y + inbound_y * leg
        points_xy.append((p3x, p3y))
        c2x, c2y = p3x - lx * radius, p3y - ly * radius
        r0x, r0y = p3x - c2x, p3y - c2y
        for step in range(1, self.ARC_SEGMENTS + 1):
            angle = sign * math.pi * step / self.ARC_SEGMENTS
            points_xy.append((c2x + r0x * math.cos(angle) - r0y * math.sin(angle),
                              c2y + r0x * math.sin(angle) + r0y * math.cos(angle)))

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
        route_id = getattr(aircraft, "arrival_route_id", None)
        if (not isinstance(route, list) or not isinstance(route_index, int) or isinstance(route_index, bool)
                or route_index < 0 or route_index >= len(route)):
            return []
        compatible = []
        for holding in self.holding_routes:
            if not isinstance(holding, dict):
                continue
            trigger = holding.get("trigger_waypoint")
            sources = holding.get("source_routes", [])
            points = self.racetrack_waypoints(holding)
            if (not isinstance(trigger, str) or trigger.strip().upper() != str(rejoin_waypoint).strip().upper()
                    or not isinstance(sources, list) or (sources and str(route_id) not in {str(item) for item in sources})
                    or not points):
                continue
            if any(str(node).strip().upper() == trigger.strip().upper() for node in route[route_index + 1:]):
                compatible.append(holding.get("id"))
        return compatible

    def expected_rejoin_node(self, aircraft, holding_route_id):
        holding = self._holding_route_by_id(holding_route_id)
        if holding is None:
            return None
        route = getattr(aircraft, "route", [])
        index = getattr(aircraft, "route_index", 0)
        trigger = str(holding.get("trigger_waypoint", "")).upper()
        rejoin_index = next((i for i in range(index + 1, len(route))
                             if str(route[i]).upper() == trigger), None)
        return route[rejoin_index + 1] if rejoin_index is not None and rejoin_index + 1 < len(route) else None

    def apply_holding_route(self, aircraft, holding_route_id, circuits=1):
        holding = self._holding_route_by_id(holding_route_id)
        if holding is None:
            return self._result(False, f"Unknown holding route: {holding_route_id}")
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
        sources = holding.get("source_routes", [])
        if sources and str(getattr(aircraft, "arrival_route_id", None)) not in {str(item) for item in sources}:
            return self._result(False, "Holding route is not configured for this arrival route")
        trigger = holding.get("trigger_waypoint")
        if not isinstance(trigger, str) or not trigger:
            return self._result(False, "Holding route has no valid rejoin waypoint")
        rejoin_index = next((i for i in range(route_index + 1, len(original_route))
                             if str(original_route[i]).upper() == trigger.upper()), None)
        if rejoin_index is None:
            return self._result(False, "Rejoin waypoint is not ahead on the aircraft route")
        hold_points = self.racetrack_waypoints(holding)
        if len(hold_points) < 5 or hold_points[0]["name"].upper() != trigger.upper() or hold_points[-1]["name"].upper() != trigger.upper():
            return self._result(False, "Configured holding pattern geometry is invalid")
        hold_names = [point["name"] for point in hold_points]
        holding_nodes = self._activate_holding_nodes(hold_points)
        active_route = list(original_route[:rejoin_index]) + hold_names * circuits + list(original_route[rejoin_index + 1:])
        hold_start = rejoin_index
        suffix_start = hold_start + len(hold_names) * circuits
        rejoin_node = original_route[rejoin_index + 1] if rejoin_index + 1 < len(original_route) else None
        self._active_holds[aircraft_key] = {
            "aircraft": aircraft, "original_route": list(original_route),
            "original_target_altitude": getattr(aircraft, "target_altitude_ft", None),
            "original_target_speed": getattr(aircraft, "target_speed_kts", None),
            "original_state": getattr(aircraft, "state", "ENROUTE"),
            "original_controller": getattr(aircraft, "controller", ""),
            "original_instruction": getattr(aircraft, "last_instruction", ""),
            "rejoin_index": rejoin_index, "holding_start_index": hold_start,
            "holding_rejoin_index": suffix_start - 1, "rejoin_waypoint": trigger,
            "rejoin_node": rejoin_node, "holding_route_id": holding.get("id"),
            "holding_node_ids": holding_nodes, "holding_waypoints": hold_points,
            "circuit_length": len(hold_names), "requested_circuits": circuits,
            "scheduled_circuits": circuits, "completed_circuits": 0,
        }
        aircraft.route = active_route
        # Keep the navigation cursor anchored to the aircraft's current fix,
        # then point guidance at the next node in the spliced route.
        assigned_index = next(
            (i for i, node in enumerate(active_route[:rejoin_index + 1])
             if str(node).upper() == str(getattr(aircraft, "assigned_node", "")).upper()),
            route_index,
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
        first_end = state["holding_start_index"] + state["circuit_length"] - 1
        if index < first_end or (index - first_end) % state["circuit_length"]:
            return self._result(False, "Aircraft has not completed a holding circuit")
        state["completed_circuits"] += 1
        aircraft.holding_circuits_completed = state["completed_circuits"]
        if state["completed_circuits"] < state["requested_circuits"]:
            return self._result(True, "Continue configured holding circuits", released=False,
                                completed_circuits=state["completed_circuits"])
        safety = can_release(aircraft) if can_release is not None else {"safe": True, "reason": "No release validator configured"}
        if isinstance(safety, bool):
            safety = {"safe": safety, "reason": "Downstream route check"}
        if safety.get("safe"):
            released = self.restore_original_route(aircraft, circuit_boundary=True)
            released.update({"released": released.get("success", False), "safety": safety,
                             "completed_circuits": state["completed_circuits"],
                             "holding_route_id": state["holding_route_id"]})
            return released

        hold_points = state["holding_waypoints"]
        suffix_start = state["holding_start_index"] + state["circuit_length"] * state["scheduled_circuits"]
        aircraft.route[suffix_start:suffix_start] = [point["name"] for point in hold_points]
        state["scheduled_circuits"] += 1
        state["holding_rejoin_index"] += state["circuit_length"]
        aircraft.target_node = aircraft.route[index + 1]
        return self._result(True, "Downstream traffic unsafe; continue holding", released=False,
                            safety=safety, completed_circuits=state["completed_circuits"])

    def is_holding_circuit_boundary(self, aircraft):
        state = self.state_for(aircraft)
        if state is None:
            return False
        index = getattr(aircraft, "route_index", -1)
        first_end = state["holding_start_index"] + state["circuit_length"] - 1
        return index >= first_end and (index - first_end) % state["circuit_length"] == 0

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
        aircraft.route_index = rejoin_index
        aircraft.assigned_node = original[rejoin_index]
        aircraft.target_node = original[rejoin_index + 1] if rejoin_index + 1 < len(original) else None
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
        print(f"HOLD RELEASE | callsign={aircraft.callsign} "
              f"holding_route={state['holding_route_id']} hold_fix={state['rejoin_waypoint']} "
              f"rejoin_node={state['rejoin_node']}")
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
