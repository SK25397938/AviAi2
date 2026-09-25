from app.intelligence.graph_navigator import GraphNavigator


class GuidanceAI:

    WAYPOINT_CAPTURE_DISTANCE = 0.002
    TAXI_SPEED = 15

    TAXI_PREFERRED_CONNECTIONS = {
        "N10": ["N9"],
        "N9": ["N8"]
    }

    def __init__(
        self,
        graph
    ):

        self.graph = graph

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

        clearance = aircraft.active_clearance

        if clearance.altitude_ft is not None:

            aircraft.assign_altitude(
                clearance.altitude_ft
            )

        if clearance.speed_kts is not None:

            aircraft.assign_speed(
                clearance.speed_kts
            )

        if aircraft.target_node is None:

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

            aircraft.assigned_node = (
                aircraft.target_node
            )

            aircraft.advance_route()

            if aircraft.target_node is None:

                aircraft.phase = "FINAL"
                aircraft.state = "APPROACH"
                aircraft.approach_phase = "INTERCEPT"
                aircraft.pending_event = ""

                print(
                    f"APPROACH HANDOFF: "
                    f"{aircraft.callsign} "
                    f"at {aircraft.assigned_node}"
                )

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