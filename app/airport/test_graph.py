from app.airport.graph.builder import AirportGraphBuilder


builder = AirportGraphBuilder(
    "data/airports/VABB.json"
)

graph = builder.build()

print()

print("=" * 60)
print("AIRPORT GRAPH")
print("=" * 60)

print()

print("Nodes             :", len(graph.nodes))
print("Edges             :", len(graph.edges))
print("Connected Gates   :", len(graph.gates))
print("Holding Points    :", len(graph.holding_points))
print("Runway Entries    :", len(graph.runway_entries))

print()

print("Sample Gates")
print("-" * 60)

for name, node in list(graph.gates.items())[:10]:
    print(f"{name:<12} -> Node {node}")

print()

print("Sample Holding Points")
print("-" * 60)

for name, node in list(graph.holding_points.items())[:10]:
    print(f"{name:<12} -> Node {node}")

print()

print("Runway Entry Nodes")
print("-" * 60)

for name, node in graph.runway_entries.items():
    print(f"{name:<12} -> Node {node}")

print()

print("=" * 60)