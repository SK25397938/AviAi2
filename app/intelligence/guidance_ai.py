from app.intelligence.graph_navigator import GraphNavigator


class GuidanceAI:

    WAYPOINT_CAPTURE_DISTANCE = 0.02

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

        if not aircraft.route:

            return

        clearance = aircraft.active_clearance

        if clearance.heading is not None:

            aircraft.assign_heading(
                clearance.heading
            )

        if clearance.altitude_ft is not None:

            aircraft.assign_altitude(
                clearance.altitude_ft
            )

        if clearance.speed_kts is not None:

            aircraft.assign_speed(
                clearance.speed_kts
            )

        if clearance.next_node is not None:

            aircraft.target_node = (
                clearance.next_node
            )

        if aircraft.target_node is None:

            aircraft.phase = "FINAL"

            aircraft.state = "APPROACH"

            return

        remaining = (

            len(aircraft.route)

            -

            aircraft.route_index

            -

            1

        )

        aircraft.phase = (

            "FINAL"

            if remaining <= 2

            else "ARRIVAL"

        )

        node = self.graph.get_node(

            aircraft.target_node

        )

        distance = self.navigator.distance(

            aircraft.lat,

            aircraft.lon,

            node["lat"],

            node["lon"]

        )

        if distance <= self.WAYPOINT_CAPTURE_DISTANCE:

            aircraft.advance_route()

            if aircraft.target_node is None:

                aircraft.phase = "FINAL"

                aircraft.state = "APPROACH"

                aircraft.complete_clearance()

                return

        if aircraft.target_node is None:

            return

        heading = self.navigator.heading_to_node(

            aircraft,

            aircraft.target_node

        )

        if clearance.heading is None:

            aircraft.assign_heading(

                heading

            )

        aircraft.clearance.direct_node = (

            aircraft.target_node

        )

        aircraft.current_distance = distance