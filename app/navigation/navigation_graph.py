from collections import deque
from math import atan2
from math import degrees
from math import radians
from math import sin
from math import cos
from math import sqrt


class NavigationGraph:

    def __init__(self):

        self.nodes = {}

    def add_node(
        self,
        node_id,
        lat,
        lon
    ):

        self.nodes[node_id] = {
            "lat": lat,
            "lon": lon,
            "neighbors": set()
        }

    def connect(
        self,
        a,
        b
    ):

        self.nodes[a]["neighbors"].add(b)
        self.nodes[b]["neighbors"].add(a)

    def get_node(
        self,
        node_id
    ):

        return self.nodes[node_id]

    def neighbors(
        self,
        node_id
    ):

        return list(self.nodes[node_id]["neighbors"])

    def nearest_node(
        self,
        lat,
        lon
    ):

        nearest = None
        best = float("inf")

        for node_id, node in self.nodes.items():

            d = sqrt(
                (lat - node["lat"]) ** 2 +
                (lon - node["lon"]) ** 2
            )

            if d < best:

                best = d
                nearest = node_id

        return nearest

    def heading(
        self,
        from_node,
        to_node
    ):

        a = self.nodes[from_node]
        b = self.nodes[to_node]

        lat1 = radians(a["lat"])
        lon1 = radians(a["lon"])

        lat2 = radians(b["lat"])
        lon2 = radians(b["lon"])

        dlon = lon2 - lon1

        x = sin(dlon) * cos(lat2)

        y = (
            cos(lat1) * sin(lat2)
            -
            sin(lat1) * cos(lat2) * cos(dlon)
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
        node_a,
        node_b
    ):

        a = self.nodes[node_a]
        b = self.nodes[node_b]

        return sqrt(
            (a["lat"] - b["lat"]) ** 2 +
            (a["lon"] - b["lon"]) ** 2
        )

    def shortest_path(
        self,
        start,
        goal
    ):

        if start == goal:
            return [start]

        queue = deque([[start]])

        visited = {start}

        while queue:

            path = queue.popleft()

            node = path[-1]

            for neighbor in self.neighbors(node):

                if neighbor in visited:
                    continue

                new_path = path + [neighbor]

                if neighbor == goal:
                    return new_path

                visited.add(neighbor)

                queue.append(new_path)

        return []