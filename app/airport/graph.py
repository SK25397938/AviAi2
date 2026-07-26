from .node import AirportNode
from .edge import AirportEdge


class AirportGraph:

    def __init__(self):

        self.nodes = {}

        self.edges = []

    def add_node(self, node: AirportNode):

        self.nodes[node.id] = node

    def add_edge(self, edge: AirportEdge):

        self.edges.append(edge)

        self.nodes[edge.start].neighbors.append(edge.end)

        self.nodes[edge.end].neighbors.append(edge.start)

    def get_node(self, node_id):

        return self.nodes[node_id]