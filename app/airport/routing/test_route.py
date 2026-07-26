from app.airport.graph.builder import AirportGraphBuilder
from app.airport.routing.astar import AStar


builder = AirportGraphBuilder(
    "data/airports/VABB.json"
)

graph = builder.build()

planner = AStar(graph)

start = next(iter(graph.gates.values()))

goal = next(iter(graph.runway_entries.values()))

path = planner.search(
    start,
    goal
)

print()

print("=" * 60)
print("A* ROUTE TEST")
print("=" * 60)

print()

print("Start Node :", start)

print("Goal Node  :", goal)

print()

print("Path Length :", len(path))

print()

print("First 20 Nodes")

for node in path[:20]:

    print(node)

print()

print("=" * 60)