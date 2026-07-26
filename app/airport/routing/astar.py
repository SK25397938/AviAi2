import heapq


class AStar:

    def __init__(self, graph):

        self.graph = graph

    def heuristic(self, a, b):

        na = self.graph.nodes[a]
        nb = self.graph.nodes[b]

        dx = na.latitude - nb.latitude
        dy = na.longitude - nb.longitude

        return (dx * dx + dy * dy) ** 0.5

    def edge_cost(self, a, b):

        for edge in self.graph.edges.values():

            if (
                edge.start == a and edge.end == b
            ) or (
                edge.start == b and edge.end == a
            ):

                return edge.length

        return float("inf")

    def search(self, start, goal):

        frontier = []

        heapq.heappush(
            frontier,
            (
                0,
                start
            )
        )

        came_from = {
            start: None
        }

        cost = {
            start: 0
        }

        while frontier:

            _, current = heapq.heappop(frontier)

            if current == goal:
                break

            for nxt in self.graph.neighbors(current):

                new_cost = (
                    cost[current]
                    +
                    self.edge_cost(
                        current,
                        nxt
                    )
                )

                if (
                    nxt not in cost
                    or
                    new_cost < cost[nxt]
                ):

                    cost[nxt] = new_cost

                    priority = (
                        new_cost
                        +
                        self.heuristic(
                            nxt,
                            goal
                        )
                    )

                    heapq.heappush(
                        frontier,
                        (
                            priority,
                            nxt
                        )
                    )

                    came_from[nxt] = current

        if goal not in came_from:
            return []

        path = []

        node = goal

        while node is not None:

            path.append(node)

            node = came_from[node]

        path.reverse()

        return path
    import heapq


class AStar:

    def __init__(self, graph):

        self.graph = graph

    def heuristic(self, a, b):

        na = self.graph.nodes[a]
        nb = self.graph.nodes[b]

        dx = na.latitude - nb.latitude
        dy = na.longitude - nb.longitude

        return (dx * dx + dy * dy) ** 0.5

    def edge_cost(self, a, b):

        for edge in self.graph.edges.values():

            if (
                edge.start == a and edge.end == b
            ) or (
                edge.start == b and edge.end == a
            ):

                return edge.length

        return float("inf")

    def search(self, start, goal):

        frontier = []

        heapq.heappush(
            frontier,
            (
                0,
                start
            )
        )

        came_from = {
            start: None
        }

        cost = {
            start: 0
        }

        while frontier:

            _, current = heapq.heappop(frontier)

            if current == goal:
                break

            for nxt in self.graph.neighbors(current):

                new_cost = (
                    cost[current]
                    +
                    self.edge_cost(
                        current,
                        nxt
                    )
                )

                if (
                    nxt not in cost
                    or
                    new_cost < cost[nxt]
                ):

                    cost[nxt] = new_cost

                    priority = (
                        new_cost
                        +
                        self.heuristic(
                            nxt,
                            goal
                        )
                    )

                    heapq.heappush(
                        frontier,
                        (
                            priority,
                            nxt
                        )
                    )

                    came_from[nxt] = current

        if goal not in came_from:
            return []

        path = []

        node = goal

        while node is not None:

            path.append(node)

            node = came_from[node]

        path.reverse()

        return path

    def edge_path(self, node_path):

        edges = []

        for i in range(len(node_path) - 1):

            a = node_path[i]
            b = node_path[i + 1]

            for edge in self.graph.edges.values():

                if (
                    edge.start == a and edge.end == b
                ) or (
                    edge.start == b and edge.end == a
                ):

                    edges.append(edge.id)

                    break

        return edges

    def route(self, start, goal):

        nodes = self.search(
            start,
            goal
        )

        edges = self.edge_path(
            nodes
        )

        return {

            "nodes": nodes,

            "edges": edges

        }
    