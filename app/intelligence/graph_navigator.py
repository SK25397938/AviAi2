from math import atan2
from math import degrees
from math import radians
from math import sin
from math import cos
from math import sqrt


class GraphNavigator:

    def __init__(self, graph):

        self.graph = graph

    def nearest_node(self, aircraft):

        nearest = None
        best = float("inf")

        for node_id, node in self.graph.nodes.items():

            d = self.distance(

                aircraft.lat,
                aircraft.lon,

                node["lat"],
                node["lon"]

            )

            if d < best:

                best = d
                nearest = node_id

        return nearest

    def build_route(
        self,
        aircraft,
        destination="N1"
    ):

        start = self.nearest_node(
            aircraft
        )

        return self.graph.shortest_path(
            start,
            destination
        )

    def heading_to_node(
        self,
        aircraft,
        node_id
    ):

        node = self.graph.get_node(
            node_id
        )

        lat1 = radians(aircraft.lat)
        lon1 = radians(aircraft.lon)

        lat2 = radians(node["lat"])
        lon2 = radians(node["lon"])

        dlon = lon2 - lon1

        x = sin(dlon) * cos(lat2)

        y = (

            cos(lat1) * sin(lat2)

            -

            sin(lat1)
            * cos(lat2)
            * cos(dlon)

        )

        return (

            degrees(
                atan2(
                    x,
                    y
                )
            ) + 360

        ) % 360

    def distance(
        self,
        lat1,
        lon1,
        lat2,
        lon2
    ):

        return sqrt(

            (lat1 - lat2) ** 2 +

            (lon1 - lon2) ** 2

        )

    def distance_to_node(
        self,
        aircraft,
        node_id
    ):

        node = self.graph.get_node(
            node_id
        )

        return self.distance(

            aircraft.lat,
            aircraft.lon,

            node["lat"],
            node["lon"]

        )

    def reached_node(
        self,
        aircraft,
        node_id,
        capture_distance=0.02
    ):

        return (

            self.distance_to_node(
                aircraft,
                node_id
            )

            <=

            capture_distance

        )

    def remaining_nodes(
        self,
        aircraft
    ):

        if not aircraft.route:

            return []

        return aircraft.route[
            aircraft.route_index:
        ]

    def route_complete(
        self,
        aircraft
    ):

        return aircraft.target_node is None

    def turn_difference(
        self,
        current,
        target
    ):

        return (

            (target - current + 540)

            % 360

        ) - 180