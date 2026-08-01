from .navigation_graph import NavigationGraph


def build_graph(grid):

    graph = NavigationGraph()

    previous_center = None

    for row in grid:

        center = row["center"]
        left = row["left"]
        right = row["right"]

        graph.add_node(center["id"], center["lat"], center["lon"])
        graph.add_node(left["id"], left["lat"], left["lon"])
        graph.add_node(right["id"], right["lat"], right["lon"])

        graph.connect(left["id"], center["id"])
        graph.connect(center["id"], right["id"])

        if previous_center:

            graph.connect(previous_center, center["id"])

        previous_center = center["id"]

    return graph