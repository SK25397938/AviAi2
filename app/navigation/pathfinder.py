class Pathfinder:

    def __init__(self, graph):

        self.graph = graph

    def route(
        self,
        start,
        destination
    ):

        return self.graph.shortest_path(

            start,

            destination

        )

    def next_node(
        self,
        route,
        index
    ):

        if index + 1 >= len(route):

            return None

        return route[index + 1]