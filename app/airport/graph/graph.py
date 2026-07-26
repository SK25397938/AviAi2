class AirportGraph:

    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.adjacency = {}
        self.gates = {}
        self.parking_positions = {}
        self.holding_points = {}
        self.runway_entries = {}

    def add_node(self, node):
        self.nodes[node.id] = node
        self.adjacency.setdefault(
            node.id,
            []
        )

    def add_edge(self, edge):
        self.edges[edge.id] = edge
        self.adjacency.setdefault(
            edge.start,
            []
        ).append(edge.end)
        self.adjacency.setdefault(
            edge.end,
            []
        ).append(edge.start)

    def neighbors(self, node):
        return self.adjacency.get(
            node,
            []
        )

    def add_gate(self, name, node):
        self.gates[name] = node

    def add_parking_position(self, name, node):
        self.parking_positions[name] = node

    def add_holding_point(self, name, node):
        self.holding_points[name] = node

    def add_runway_entry(self, name, node):
        self.runway_entries[name] = node