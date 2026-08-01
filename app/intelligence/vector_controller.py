from app.intelligence.graph_navigator import GraphNavigator


class VectorController:

    def __init__(self, graph):

        self.graph = graph

        self.navigator = GraphNavigator(graph)

    def update(self, aircraft):

        if not aircraft.route:
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

        aircraft.heading_deg = heading

        node = self.graph.get_node(
            aircraft.target_node
        )

        distance = self.navigator.distance(

            aircraft.lat,
            aircraft.lon,

            node["lat"],
            node["lon"]

        )

        if distance < 0.01:

            aircraft.advance_route()

            if aircraft.target_node is None:

                aircraft.state = "FINAL"