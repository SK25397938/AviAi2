from app.navigation.runway import Runway
from app.navigation.centerline import build_centerline
from app.navigation.grid_builder import build_grid
from app.navigation.graph_builder import build_graph


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

graph = build_graph(
    grid
)

print(f"Generated {len(graph.nodes)} nodes\n")

for node_id, node in sorted(graph.nodes.items()):

    print(
        node_id,
        node["lat"],
        node["lon"],
        list(node["neighbors"])
    )