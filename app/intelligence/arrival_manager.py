from app.intelligence.graph_navigator import GraphNavigator


class ArrivalManager:

    def __init__(self, graph):

        self.graph = graph

        self.navigator = GraphNavigator(graph)

    def assign_entry(self, aircraft):

        route = self.navigator.build_route(
            aircraft,
            aircraft.destination
        )
        print(
    aircraft.callsign,
    route
)

        aircraft.assign_route(route)

        return route