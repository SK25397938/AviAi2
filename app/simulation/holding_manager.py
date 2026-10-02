class HoldingManager:

    def __init__(self, holding_routes, graph=None):
        if isinstance(holding_routes, dict):
            holding_routes = holding_routes.get("holding_routes", [])

        self.holding_routes = (
            holding_routes if isinstance(holding_routes, list) else []
        )
        self.graph = graph
        self._active_holds = {}
        self._temporary_node_references = {}

    @staticmethod
    def _result(success, message):
        return {"success": success, "message": message}

    def _holding_route_by_id(self, holding_route_id):
        for route in self.holding_routes:
            if (
                isinstance(route, dict)
                and str(route.get("id", "")) == str(holding_route_id)
            ):
                return route
        return None

    def _activate_holding_nodes(self, waypoints):
        active_node_ids = []
        graph_nodes = getattr(self.graph, "nodes", None)
        if not isinstance(graph_nodes, dict):
            return active_node_ids

        for waypoint in waypoints:
            name = waypoint["name"]
            if name in self._temporary_node_references:
                self._temporary_node_references[name] += 1
                active_node_ids.append(name)
            elif name not in graph_nodes:
                self.graph.add_node(
                    name,
                    float(waypoint["latitude"]),
                    float(waypoint["longitude"])
                )
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
        if (
            not isinstance(route, list)
            or not isinstance(route_index, int)
            or isinstance(route_index, bool)
            or route_index < 0
            or route_index >= len(route)
        ):
            return []

        compatible = []
        for holding_route in self.holding_routes:
            if not isinstance(holding_route, dict):
                continue

            holding_route_id = holding_route.get("id")
            trigger = holding_route.get("trigger_waypoint")
            source_routes = holding_route.get("source_routes", [])
            waypoints = holding_route.get("waypoints", [])
            if (
                holding_route_id is None
                or not isinstance(trigger, str)
                or trigger.strip().upper() != str(rejoin_waypoint).strip().upper()
                or not isinstance(source_routes, list)
                or (source_routes and str(route_id) not in {str(item) for item in source_routes})
                or not isinstance(waypoints, list)
                or not waypoints
                or not isinstance(waypoints[-1], dict)
                or str(waypoints[-1].get("name", "")).strip().upper() != trigger.strip().upper()
            ):
                continue

            if any(
                str(waypoint).strip().upper() == trigger.strip().upper()
                for waypoint in route[route_index + 1:]
            ):
                compatible.append(holding_route_id)

        return compatible

    def apply_holding_route(self, aircraft, holding_route_id):
        route = self._holding_route_by_id(holding_route_id)
        if route is None:
            return self._result(False, f"Unknown holding route: {holding_route_id}")

        aircraft_key = id(aircraft)
        if aircraft_key in self._active_holds:
            return self._result(False, "Aircraft already has an active holding route")

        original_route = getattr(aircraft, "route", None)
        route_index = getattr(aircraft, "route_index", None)
        if (
            not isinstance(original_route, list)
            or not original_route
            or not isinstance(route_index, int)
            or isinstance(route_index, bool)
            or route_index < 0
            or route_index >= len(original_route)
        ):
            return self._result(False, "Aircraft has no valid active route position")

        source_routes = route.get("source_routes", [])
        aircraft_route_id = getattr(aircraft, "arrival_route_id", None)
        if source_routes and str(aircraft_route_id) not in {
            str(source_route) for source_route in source_routes
        }:
            return self._result(False, "Holding route is not configured for this arrival route")

        trigger = route.get("trigger_waypoint")
        if not isinstance(trigger, str) or not trigger:
            return self._result(False, "Holding route has no valid rejoin waypoint")

        rejoin_index = next(
            (
                index
                for index in range(route_index + 1, len(original_route))
                if str(original_route[index]).upper() == trigger.upper()
            ),
            None
        )
        if rejoin_index is None:
            return self._result(False, "Rejoin waypoint is not ahead on the aircraft route")

        holding_waypoints = route.get("waypoints")
        if not isinstance(holding_waypoints, list) or not holding_waypoints:
            return self._result(False, "Holding route has no waypoints")

        holding_names = []
        for waypoint in holding_waypoints:
            if not isinstance(waypoint, dict):
                return self._result(False, "Holding route contains an invalid waypoint")
            name = waypoint.get("name")
            if not isinstance(name, str) or not name:
                return self._result(False, "Holding route contains a waypoint without a name")
            holding_names.append(name)

        if holding_names[-1].upper() != trigger.upper():
            return self._result(False, "Holding route does not end at its configured rejoin waypoint")

        holding_node_ids = self._activate_holding_nodes(holding_waypoints)

        # Preserve the original path to the holding fix, then loop the hold
        # before continuing with the original route after that fix.
        active_route = (
            list(original_route[:rejoin_index])
            + holding_names
            + list(original_route[rejoin_index + 1:])
        )
        self._active_holds[aircraft_key] = {
            "aircraft": aircraft,
            "original_route": list(original_route),
            "original_target_altitude": getattr(aircraft, "target_altitude_ft", None),
            "original_target_speed": getattr(aircraft, "target_speed_kts", None),
            "rejoin_index": rejoin_index,
            "holding_rejoin_index": rejoin_index + len(holding_names) - 1,
            "rejoin_waypoint": trigger,
            "holding_route_id": route.get("id"),
            "holding_node_ids": holding_node_ids
        }

        aircraft.route = active_route
        aircraft.target_node = active_route[route_index + 1]

        return self._result(
            True,
            f"Holding route {route.get('id')} applied; rejoins at {trigger}"
        )

    def has_active_holding_route(self, aircraft):
        state = self._active_holds.get(id(aircraft))
        return state is not None and state.get("aircraft") is aircraft

    def restore_original_route(self, aircraft):
        state = self._active_holds.get(id(aircraft))
        if state is None or state.get("aircraft") is not aircraft:
            return self._result(False, "Aircraft has no active holding route")

        rejoin_waypoint = state["rejoin_waypoint"]
        if (
            str(getattr(aircraft, "assigned_node", "")).upper() != rejoin_waypoint.upper()
            or getattr(aircraft, "route_index", None) != state["holding_rejoin_index"]
        ):
            return self._result(False, f"Aircraft has not reached rejoin waypoint {rejoin_waypoint}")

        original_route = list(state["original_route"])
        rejoin_index = state["rejoin_index"]
        aircraft.route = original_route
        aircraft.route_index = rejoin_index
        aircraft.assigned_node = original_route[rejoin_index]
        aircraft.target_node = (
            original_route[rejoin_index + 1]
            if rejoin_index + 1 < len(original_route)
            else None
        )
        aircraft.assign_altitude(state.get("original_target_altitude"))
        aircraft.assign_speed(state.get("original_target_speed"))
        self._release_holding_nodes(state.get("holding_node_ids", []))
        del self._active_holds[id(aircraft)]

        return self._result(
            True,
            f"Original route restored at {rejoin_waypoint}"
        )

    def active_holding_routes(self):
        active = []
        for state in self._active_holds.values():
            route = self._holding_route_by_id(state.get("holding_route_id"))
            aircraft = state.get("aircraft")
            if route is None or aircraft is None:
                continue
            active.append({
                "type": "holding_route",
                "callsign": aircraft.callsign,
                "holding_route_id": route["id"],
                "fix": route.get("fix", route.get("trigger_waypoint")),
                "active": True,
                "waypoints": [
                    {"name": point["name"], "latitude": point["latitude"], "longitude": point["longitude"]}
                    for point in route.get("waypoints", [])
                    if isinstance(point, dict)
                    and all(key in point for key in ("name", "latitude", "longitude"))
                ],
            })
        return active
