import math


def distance(lat1, lon1, lat2, lon2):
    return math.sqrt(
        (lat2 - lat1) ** 2 +
        (lon2 - lon1) ** 2
    )


def nearest_node(graph, lat, lon):

    nearest = None
    minimum = float("inf")

    for node_id, node in graph.nodes.items():

        d = distance(
            lat,
            lon,
            node["lat"],
            node["lon"]
        )

        if d < minimum:

            minimum = d

            nearest = node_id

    return nearest