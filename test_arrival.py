from app.simulation.spawn import spawn_aircraft
from app.navigation.graph_builder import NavigationGraph
from app.intelligence.arrival_manager import ArrivalManager


graph = NavigationGraph()

arrival = ArrivalManager(graph)

for i in range(10):

    ac = spawn_aircraft(
        f"AIQ{i}",
        "A320",
        19.0887,
        72.8679
    )

    node = arrival.assign_entry(ac)

    print()

    print(ac.callsign)

    print(round(ac.lat,4))

    print(round(ac.lon,4))

    print(node)