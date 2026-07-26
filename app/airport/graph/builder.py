import json
from math import radians
from math import sin
from math import cos
from math import sqrt
from math import atan2

from app.airport.graph.node import Node
from app.airport.graph.edge import Edge
from app.airport.graph.graph import AirportGraph
from app.airport.graph.semantics import apply_semantics


class AirportGraphBuilder:

    def __init__(self, airport_file):
        self.airport_file = airport_file

    def distance(self, a, b):
        lat1, lon1 = a
        lat2, lon2 = b

        r = 6371000

        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)

        aa = (
            sin(dlat / 2) ** 2
            +
            cos(radians(lat1))
            *
            cos(radians(lat2))
            *
            sin(dlon / 2) ** 2
        )

        c = 2 * atan2(
            sqrt(aa),
            sqrt(1 - aa)
        )

        return r * c

    def nearest_node(self, graph, lat, lon):

        best = None
        best_distance = float("inf")

        for node in graph.nodes.values():

            d = self.distance(
                (lat, lon),
                (node.latitude, node.longitude)
            )

            if d < best_distance:
                best_distance = d
                best = node.id

        return best

    def build(self):

        with open(
            self.airport_file,
            "r",
            encoding="utf-8"
        ) as f:

            airport = json.load(f)

        airport = apply_semantics(airport)

        graph = AirportGraph()

        node_lookup = {}

        node_id = 0
        edge_id = 0

        for taxiway in airport["taxiways"]:

            coords = taxiway["geometry"]

            previous = None

            for lon, lat in coords:

                key = (
                    round(lat, 7),
                    round(lon, 7)
                )

                if key not in node_lookup:

                    node_lookup[key] = node_id

                    graph.add_node(

                        Node(
                            id=node_id,
                            latitude=lat,
                            longitude=lon,
                            kind="taxi",
                            name=taxiway.get("name"),
                            taxiway=taxiway.get("name")
                        )

                    )

                    node_id += 1

                current = node_lookup[key]

                if previous is not None:

                    graph.add_edge(

                        Edge(
                            id=edge_id,
                            start=previous,
                            end=current,
                            length=self.distance(
                                (
                                    graph.nodes[previous].latitude,
                                    graph.nodes[previous].longitude
                                ),
                                (
                                    lat,
                                    lon
                                )
                            ),
                            name=taxiway.get("name"),
                            taxiway=taxiway.get("name"),
                            edge_type="taxiway"
                        )

                    )

                    edge_id += 1

                previous = current

        for gate in airport["gates"]:

            if not gate["position"]:
                continue

            lon, lat = gate["position"]

            graph.add_gate(
                gate["name"],
                self.nearest_node(
                    graph,
                    lat,
                    lon
                )
            )

        for stand in airport["parking_positions"]:

            if not stand["position"]:
                continue

            lon, lat = stand["position"]

            graph.add_parking_position(
                stand["name"],
                self.nearest_node(
                    graph,
                    lat,
                    lon
                )
            )

        for hp in airport["holding_positions"]:

            if not hp["position"]:
                continue

            lon, lat = hp["position"]

            graph.add_holding_point(
                f"H{len(graph.holding_points)+1}",
                self.nearest_node(
                    graph,
                    lat,
                    lon
                )
            )

        for runway in airport["runways"]:

            coords = runway["geometry"]

            if not coords:
                continue

            start = coords[0]
            end = coords[-1]

            graph.add_runway_entry(
                f"{runway['ref']}_A",
                self.nearest_node(
                    graph,
                    start[1],
                    start[0]
                )
            )

            graph.add_runway_entry(
                f"{runway['ref']}_B",
                self.nearest_node(
                    graph,
                    end[1],
                    end[0]
                )
            )

        return graph