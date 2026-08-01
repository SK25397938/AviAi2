from app.navigation.runway import Runway
from app.navigation.centerline import build_centerline
from app.navigation.grid_builder import build_grid
from app.navigation.graph_builder import build_graph
from app.navigation.pathfinder import Pathfinder


runway = Runway(
    ident="27",
    threshold_lat=19.0887,
    threshold_lon=72.8679,
    heading=270,
    length_nm=25
)

centerline = build_centerline(runway)

grid = build_grid(
    centerline,
    runway
)

graph = build_graph(grid)

pathfinder = Pathfinder(graph)

route = pathfinder.route(
    "N25",
    "N1"
)

print("Route:\n")

for node in route:

    print(node)